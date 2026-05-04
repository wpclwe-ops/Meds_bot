import os
import uuid
from datetime import datetime, timedelta
import pytz

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.types import *
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===== CONFIG =====
API_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
TZ = pytz.timezone("Europe/Warsaw")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TZ)

db = None

# ===== DB =====

async def init_db():
    global db
    db = await asyncpg.connect(DB_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS meds (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        name TEXT,
        dose TEXT,
        time TEXT,
        freq TEXT,
        days INT[]
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        med_id TEXT,
        status TEXT,
        time TIMESTAMP
    );
    """)

# ===== FSM =====

class AddMed(StatesGroup):
    name = State()
    dose = State()
    freq = State()
    time = State()
    days = State()

class EditMed(StatesGroup):
    field = State()
    value = State()

# ===== HELPERS =====

WEEK = ["mon","tue","wed","thu","fri","sat","sun"]

def validate_time(t):
    try:
        datetime.strptime(t, "%H:%M")
        return True
    except:
        return False

def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Add", "📋 Today")
    kb.add("✏️ Edit", "❌ Delete")
    return kb

def reminder_kb(mid):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅", callback_data=f"take_{mid}"),
        InlineKeyboardButton("⏳", callback_data=f"snooze_{mid}_15"),
        InlineKeyboardButton("❌", callback_data=f"skip_{mid}")
    )
    return kb

async def send_reminder(uid, med):
    await bot.send_message(uid, f"💊 {med['name']} ({med['dose']})", reply_markup=reminder_kb(med["id"]))

def schedule_med(med):
    h, m = map(int, med["time"].split(":"))
    job_id = med["id"]

    try:
        scheduler.remove_job(job_id)
    except:
        pass

    if med["freq"] == "daily":
        scheduler.add_job(send_reminder,"cron",
            args=[med["user_id"], med],
            hour=h, minute=m,
            id=job_id
        )
    else:
        days = ",".join([WEEK[d] for d in med["days"]])
        scheduler.add_job(send_reminder,"cron",
            args=[med["user_id"], med],
            day_of_week=days,
            hour=h, minute=m,
            id=job_id
        )

async def reload_jobs():
    scheduler.remove_all_jobs()
    rows = await db.fetch("SELECT * FROM meds")
    for r in rows:
        schedule_med(dict(r))

# ===== START =====

@dp.message_handler(commands=["start"])
async def start(msg):
    await msg.answer("Menu", reply_markup=menu())

# ===== TODAY =====

@dp.message_handler(lambda m: m.text == "📋 Today")
async def today(msg):
    uid = str(msg.from_user.id)
    now = datetime.now(TZ)

    meds = await db.fetch("SELECT * FROM meds WHERE user_id=$1", uid)
    logs = await db.fetch("""
        SELECT * FROM logs 
        WHERE user_id=$1 
        AND DATE(time AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Warsaw') = CURRENT_DATE
    """, uid)

    log_map = {l["med_id"]: l["status"] for l in logs}

    result = []

    for m in meds:
        if m["freq"] == "daily":
            show = True
        else:
            show = now.weekday() in m["days"]

        if not show:
            continue

        status = log_map.get(m["id"], "pending")

        icon = "⏺"
        if status == "taken":
            icon = "✅"
        elif status == "skipped":
            icon = "❌"
        elif status == "snoozed":
            icon = "⏳"

        result.append(f"{m['time']} {m['name']} {icon}")

    if not result:
        await msg.answer("Empty")
        return

    await msg.answer("\n".join(result))

# ===== ADD =====

@dp.message_handler(lambda m: m.text == "➕ Add")
async def add_start(msg):
    await msg.answer("Name:")
    await AddMed.name.set()

@dp.message_handler(state=AddMed.name)
async def add_name(msg,state):
    await state.update_data(name=msg.text)
    await msg.answer("Dose:")
    await AddMed.dose.set()

@dp.message_handler(state=AddMed.dose)
async def add_dose(msg,state):
    await state.update_data(dose=msg.text)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📅 Every day","📆 Specific days")
    await msg.answer("Frequency:", reply_markup=kb)
    await AddMed.freq.set()

@dp.message_handler(state=AddMed.freq)
async def add_freq(msg,state):
    if "Every" in msg.text:
        await state.update_data(freq="daily", days=[])
    else:
        await state.update_data(freq="weekly")

    await msg.answer("Time HH:MM", reply_markup=ReplyKeyboardRemove())
    await AddMed.time.set()

@dp.message_handler(state=AddMed.time)
async def add_time(msg,state):
    if not validate_time(msg.text):
        return await msg.answer("Invalid time")

    await state.update_data(time=msg.text)
    data = await state.get_data()

    if data["freq"] == "weekly":
        kb = InlineKeyboardMarkup()
        for i,d in enumerate(WEEK):
            kb.insert(InlineKeyboardButton(d, callback_data=f"d_{i}"))
        kb.add(InlineKeyboardButton("Done", callback_data="done"))
        await msg.answer("Select days:", reply_markup=kb)
        await AddMed.days.set()
    else:
        await finish_add(msg,state)

@dp.callback_query_handler(lambda c: c.data.startswith("d_"), state=AddMed.days)
async def add_days(call,state):
    if call.data == "done":
        await finish_add(call.message,state)
        return

    d = int(call.data.split("_")[1])
    data = await state.get_data()
    days = data.get("days", [])

    if d in days:
        days.remove(d)
    else:
        days.append(d)

    await state.update_data(days=days)
    await call.answer("ok")

async def finish_add(msg,state):
    data = await state.get_data()
    uid = str(msg.from_user.id)

    med = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "name": data["name"],
        "dose": data["dose"],
        "time": data["time"],
        "freq": data["freq"],
        "days": data.get("days", [])
    }

    await db.execute("""
        INSERT INTO meds(id,user_id,name,dose,time,freq,days)
        VALUES($1,$2,$3,$4,$5,$6,$7)
    """, *med.values())

    schedule_med(med)

    await msg.answer("Saved ✅", reply_markup=menu())
    await state.finish()

# ===== DELETE =====

@dp.message_handler(lambda m: m.text == "❌ Delete")
async def delete(msg):
    uid = str(msg.from_user.id)
    meds = await db.fetch("SELECT id,name FROM meds WHERE user_id=$1", uid)

    kb = InlineKeyboardMarkup()
    for m in meds:
        kb.add(InlineKeyboardButton(m["name"], callback_data=f"del_{m['id']}"))

    await msg.answer("Delete:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_"))
async def delete_cb(call):
    mid = call.data.split("_")[1]

    await db.execute("DELETE FROM meds WHERE id=$1", mid)
    await db.execute("DELETE FROM logs WHERE med_id=$1", mid)

    try:
        scheduler.remove_job(mid)
    except:
        pass

    await call.message.answer("Deleted ❌", reply_markup=menu())

# ===== EDIT =====

@dp.message_handler(lambda m: m.text == "✏️ Edit")
async def edit(msg):
    uid = str(msg.from_user.id)
    meds = await db.fetch("SELECT id,name FROM meds WHERE user_id=$1", uid)

    kb = InlineKeyboardMarkup()
    for m in meds:
        kb.add(InlineKeyboardButton(m["name"], callback_data=f"edit_{m['id']}"))

    await msg.answer("Choose:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_choose(call, state: FSMContext):
    await state.update_data(mid=call.data.split("_")[1])

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Name", callback_data="field_name"))
    kb.add(InlineKeyboardButton("Dose", callback_data="field_dose"))
    kb.add(InlineKeyboardButton("Time", callback_data="field_time"))

    await call.message.answer("Field:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("field_"))
async def edit_field(call, state: FSMContext):
    await state.update_data(field=call.data.split("_")[1])
    await call.message.answer("Send new value:")

@dp.message_handler(state="*")
async def edit_save(msg, state: FSMContext):
    data = await state.get_data()

    if "mid" not in data:
        return

    field = data["field"]
    mid = data["mid"]

    if field == "time" and not validate_time(msg.text):
        await msg.answer("Invalid time")
        return

    if field == "name":
        await db.execute("UPDATE meds SET name=$1 WHERE id=$2", msg.text, mid)
    elif field == "dose":
        await db.execute("UPDATE meds SET dose=$1 WHERE id=$2", msg.text, mid)
    elif field == "time":
        await db.execute("UPDATE meds SET time=$1 WHERE id=$2", msg.text, mid)

    await reload_jobs()

    await msg.answer("Updated ✅", reply_markup=menu())
    await state.finish()

# ===== CALLBACKS =====

@dp.callback_query_handler(lambda c: c.data.startswith("take_"))
async def take(call):
    uid = str(call.from_user.id)
    mid = call.data.split("_")[1]

    await db.execute("""
        INSERT INTO logs(user_id, med_id, status, time)
        VALUES($1,$2,'taken',NOW())
    """, uid, mid)

    await call.message.answer("✅")

@dp.callback_query_handler(lambda c: c.data.startswith("skip_"))
async def skip(call):
    uid = str(call.from_user.id)
    mid = call.data.split("_")[1]

    await db.execute("""
        INSERT INTO logs(user_id, med_id, status, time)
        VALUES($1,$2,'skipped',NOW())
    """, uid, mid)

    await call.message.answer("❌")

@dp.callback_query_handler(lambda c: c.data.startswith("snooze_"))
async def snooze(call):
    uid = str(call.from_user.id)
    _, mid, mins = call.data.split("_")

    med = await db.fetchrow("SELECT * FROM meds WHERE id=$1", mid)

    await db.execute("""
        INSERT INTO logs(user_id, med_id, status, time)
        VALUES($1,$2,'snoozed',NOW())
    """, uid, mid)

    scheduler.add_job(
        send_reminder,
        "date",
        run_date=datetime.now(TZ) + timedelta(minutes=int(mins)),
        args=[uid, dict(med)]
    )

    await call.message.answer(f"+{mins}m")

# ===== RUN =====

async def on_startup(dp):
    await init_db()
    await reload_jobs()
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
