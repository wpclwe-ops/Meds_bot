# --- ВСЕ ИМПОРТЫ ---
import os
import uuid
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIG ---
API_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
scheduler = AsyncIOScheduler()

db = None

# ================= DB =================

async def init_db():
    global db
    db = await asyncpg.connect(DB_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        lang TEXT
    );
    """)

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

# ================= MENU =================

def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Add", "📋 Today")
    kb.add("✏️ Edit", "❌ Delete")
    return kb

# ================= FSM =================

class AddMed(StatesGroup):
    name = State()
    dose = State()
    freq = State()
    time = State()
    days = State()

class EditMed(StatesGroup):
    field = State()
    value = State()
    days = State()

# ================= HELPERS =================

WEEK = ["mon","tue","wed","thu","fri","sat","sun"]

def validate_time(t):
    try:
        datetime.strptime(t, "%H:%M")
        return True
    except:
        return False

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

    if med["freq"] == "daily":
        scheduler.add_job(send_reminder,"cron",
            args=[med["user_id"], med],
            hour=h, minute=m,
            id=f"{med['id']}_daily",
            replace_existing=True
        )
    else:
        days = ",".join([WEEK[d] for d in med["days"]])
        scheduler.add_job(send_reminder,"cron",
            args=[med["user_id"], med],
            day_of_week=days,
            hour=h, minute=m,
            id=f"{med['id']}_weekly",
            replace_existing=True
        )

async def reload_jobs():
    scheduler.remove_all_jobs()
    rows = await db.fetch("SELECT * FROM meds")
    for r in rows:
        schedule_med(dict(r))

# ================= START =================

@dp.message_handler(commands=["start"])
async def start(msg):
    await msg.answer("Menu", reply_markup=menu())

# ================= TODAY =================

@dp.message_handler(lambda m: m.text == "📋 Today")
async def today(msg):
    uid = str(msg.from_user.id)

    rows = await db.fetch("""
        SELECT m.name, m.dose, l.status, l.time
        FROM logs l
        JOIN meds m ON m.id = l.med_id
        WHERE l.user_id = $1
        AND DATE(l.time) = CURRENT_DATE
        ORDER BY l.time
    """, uid)

    if not rows:
        await msg.answer("Empty")
        return

    text = ""
    for r in rows:
        status = "✅" if r["status"] == "taken" else "❌"
        text += f"{r['time'].strftime('%H:%M')} {r['name']} {status}\n"

    await msg.answer(text)

# ================= ADD =================

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
        await msg.answer("Invalid time")
        return

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
    days = data.get("days",[])
    if d not in days:
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
        "days": data.get("days",[])
    }

    await db.execute("""
        INSERT INTO meds VALUES($1,$2,$3,$4,$5,$6,$7)
    """, *med.values())

    schedule_med(med)
    await msg.answer("Saved ✅", reply_markup=menu())
    await state.finish()

# ================= DELETE =================

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
    await reload_jobs()
    await call.message.answer("Deleted ❌", reply_markup=menu())

# ================= EDIT =================

@dp.message_handler(lambda m: m.text == "✏️ Edit")
async def edit(msg):
    uid = str(msg.from_user.id)
    meds = await db.fetch("SELECT id,name FROM meds WHERE user_id=$1", uid)

    kb = InlineKeyboardMarkup()
    for m in meds:
        kb.add(InlineKeyboardButton(m["name"], callback_data=f"edit_{m['id']}"))

    await msg.answer("Choose:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_choose(call,state):
    mid = call.data.split("_")[1]
    await state.update_data(mid=mid)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Name","field_name"))
    kb.add(InlineKeyboardButton("Dose","field_dose"))
    kb.add(InlineKeyboardButton("Time","field_time"))
    kb.add(InlineKeyboardButton("Days","field_days"))

    await call.message.answer("Field:", reply_markup=kb)
    await EditMed.field.set()

@dp.callback_query_handler(lambda c: c.data.startswith("field_"), state=EditMed.field)
async def edit_field(call,state):
    field = call.data.split("_")[1]
    await state.update_data(field=field)

    if field == "days":
        kb = InlineKeyboardMarkup()
        for i,d in enumerate(WEEK):
            kb.insert(InlineKeyboardButton(d, callback_data=f"ed_{i}"))
        kb.add(InlineKeyboardButton("Done", callback_data="ed_done"))
        await call.message.answer("Select days:", reply_markup=kb)
        await EditMed.days.set()
    else:
        await call.message.answer("New value:")
        await EditMed.value.set()

@dp.callback_query_handler(lambda c: c.data.startswith("ed_"), state=EditMed.days)
async def edit_days(call,state):
    if call.data == "ed_done":
        data = await state.get_data()
        await db.execute("UPDATE meds SET days=$1 WHERE id=$2", data["days"], data["mid"])
        await reload_jobs()
        await call.message.answer("Updated ✅", reply_markup=menu())
        await state.finish()
        return

    d = int(call.data.split("_")[1])
    data = await state.get_data()
    days = data.get("days",[])
    if d not in days:
        days.append(d)
    await state.update_data(days=days)
    await call.answer("ok")

@dp.message_handler(state=EditMed.value)
async def edit_save(msg,state):
    data = await state.get_data()
    mid = data["mid"]
    field = data["field"]

    if field == "time" and not validate_time(msg.text):
        await msg.answer("Invalid time")
        return

    await db.execute(f"UPDATE meds SET {field}=$1 WHERE id=$2", msg.text, mid)
    await reload_jobs()

    await msg.answer("Updated ✅", reply_markup=menu())
    await state.finish()

# ================= CALLBACKS =================

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

    scheduler.add_job(
        send_reminder,
        "date",
        run_date=datetime.now() + timedelta(minutes=int(mins)),
        args=[uid, dict(med)]
    )

    await call.message.answer(f"+{mins}m")

# ================= RUN =================

async def on_startup(dp):
    await init_db()
    await reload_jobs()
    scheduler.start()

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
