import json
import uuid
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage

from apscheduler.schedulers.asyncio import AsyncIOScheduler

API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("BOT_TOKEN not set")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
scheduler = AsyncIOScheduler()

USERS_FILE = "users.json"
MEDS_FILE = "meds.json"
LOG_FILE = "logs.json"

# ================= STORAGE =================

def load(file):
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return {}

def save(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# ================= LANG =================

TEXTS = {
    "en": {
        "menu": "Menu:",
        "saved": "Saved ✅",
        "deleted": "Deleted ❌",
        "updated": "Updated ✅",
        "today_empty": "No entries today",
        "add_name": "Medicine name:",
        "add_dose": "Dose:",
        "add_freq": "Frequency?",
        "daily": "Daily",
        "weekly": "Weekly",
        "add_time": "Time HH:MM",
        "invalid_time": "Invalid time",
        "add_days": "Select days",
        "add_food": "With food?",
        "add_note": "Notes or -"
    },
    "ru": {
        "menu": "Меню:",
        "saved": "Сохранено ✅",
        "deleted": "Удалено ❌",
        "updated": "Обновлено ✅",
        "today_empty": "Сегодня пусто",
        "add_name": "Название:",
        "add_dose": "Дозировка:",
        "add_freq": "Частота?",
        "daily": "Каждый день",
        "weekly": "По дням недели",
        "add_time": "Время HH:MM",
        "invalid_time": "Неверный формат",
        "add_days": "Выбери дни",
        "add_food": "С едой?",
        "add_note": "Заметка или -"
    },
    "pl": {
        "menu": "Menu:",
        "saved": "Zapisano ✅",
        "deleted": "Usunięto ❌",
        "updated": "Zaktualizowano ✅",
        "today_empty": "Brak wpisów",
        "add_name": "Nazwa:",
        "add_dose": "Dawka:",
        "add_freq": "Częstotliwość?",
        "daily": "Codziennie",
        "weekly": "Dni tygodnia",
        "add_time": "Godzina HH:MM",
        "invalid_time": "Zły format",
        "add_days": "Wybierz dni",
        "add_food": "Z jedzeniem?",
        "add_note": "Uwagi lub -"
    }
}

def get_lang(uid):
    return load(USERS_FILE).get(uid, {}).get("lang", "en")

def t(uid, key):
    return TEXTS[get_lang(uid)][key]

# ================= MENU =================

def main_menu(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    lang = get_lang(uid)

    if lang == "ru":
        kb.add("➕ Добавить", "📋 Сегодня")
        kb.add("✏️ Редактировать", "❌ Удалить")
    elif lang == "pl":
        kb.add("➕ Dodaj", "📋 Dziś")
        kb.add("✏️ Edytuj", "❌ Usuń")
    else:
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
    food = State()
    note = State()

class EditMed(StatesGroup):
    field = State()
    value = State()

# ================= HELPERS =================

WEEK = ["mon","tue","wed","thu","fri","sat","sun"]

def validate_time(text):
    try:
        datetime.strptime(text, "%H:%M")
        return True
    except:
        return False

def reminder_kb(mid):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅", callback_data=f"take_{mid}"),
        InlineKeyboardButton("⏳", callback_data=f"snooze_menu_{mid}"),
        InlineKeyboardButton("❌", callback_data=f"skip_{mid}")
    )
    return kb

def snooze_kb(mid):
    kb = InlineKeyboardMarkup()
    for m in [10,30,60]:
        kb.add(InlineKeyboardButton(f"+{m}", callback_data=f"snooze_{mid}_{m}"))
    return kb

async def send_reminder(uid, med):
    text = f"💊 {med['name']} ({med['dose']})"
    if med["food"]:
        text += "\n🍽"
    if med["note"]:
        text += f"\n⚠️ {med['note']}"
    await bot.send_message(uid, text, reply_markup=reminder_kb(med["id"]))

def schedule_med(uid, med):
    h, m = map(int, med["time"].split(":"))

    if med["freq"] == "daily":
        scheduler.add_job(send_reminder,"cron",args=[uid,med],hour=h,minute=m,id=f"{uid}_{med['id']}")
    else:
        days = ",".join([WEEK[d] for d in med["days"]])
        scheduler.add_job(send_reminder,"cron",args=[uid,med],day_of_week=days,hour=h,minute=m,id=f"{uid}_{med['id']}")

def reload_jobs():
    scheduler.remove_all_jobs()
    meds = load(MEDS_FILE)
    for uid in meds:
        for med in meds[uid]:
            schedule_med(uid, med)

# ================= START =================

@dp.message_handler(commands=["start"])
async def start(msg: types.Message):
    uid = str(msg.from_user.id)

    if load(USERS_FILE).get(uid):
        await msg.answer(t(uid,"menu"), reply_markup=main_menu(uid))
        return

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("English", "Русский", "Polski")
    await msg.answer("Choose language", reply_markup=kb)

@dp.message_handler(lambda m: m.text in ["English","Русский","Polski"])
async def set_lang(msg: types.Message):
    langs = {"English":"en","Русский":"ru","Polski":"pl"}
    uid = str(msg.from_user.id)

    data = load(USERS_FILE)
    data[uid] = {"lang": langs[msg.text]}
    save(USERS_FILE, data)

    await msg.answer("OK", reply_markup=main_menu(uid))

# ================= BUTTONS =================

@dp.message_handler(lambda m: m.text in ["➕ Добавить","➕ Add","➕ Dodaj"])
async def btn_add(msg):
    await add(msg)

@dp.message_handler(lambda m: m.text in ["📋 Сегодня","📋 Today","📋 Dziś"])
async def btn_today(msg):
    await today(msg)

@dp.message_handler(lambda m: m.text in ["✏️ Редактировать","✏️ Edit","✏️ Edytuj"])
async def btn_edit(msg):
    await edit(msg)

@dp.message_handler(lambda m: m.text in ["❌ Удалить","❌ Delete","❌ Usuń"])
async def btn_delete(msg):
    await delete(msg)

# ================= ADD =================

@dp.message_handler(commands=["add"])
async def add(msg):
    await msg.answer(t(str(msg.from_user.id),"add_name"))
    await AddMed.name.set()

@dp.message_handler(state=AddMed.name)
async def add_name(msg,state):
    await state.update_data(name=msg.text)
    await msg.answer(t(str(msg.from_user.id),"add_dose"))
    await AddMed.dose.set()

@dp.message_handler(state=AddMed.dose)
async def add_dose(msg,state):
    await state.update_data(dose=msg.text)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(t(str(msg.from_user.id),"daily"), t(str(msg.from_user.id),"weekly"))
    await msg.answer(t(str(msg.from_user.id),"add_freq"), reply_markup=kb)
    await AddMed.freq.set()

@dp.message_handler(state=AddMed.freq)
async def add_freq(msg,state):
    txt = msg.text.lower()
    await state.update_data(freq="daily" if any(x in txt for x in ["day","каж","cod"]) else "weekly")
    await msg.answer(t(str(msg.from_user.id),"add_time"), reply_markup=ReplyKeyboardRemove())
    await AddMed.time.set()

@dp.message_handler(state=AddMed.time)
async def add_time(msg,state):
    if not validate_time(msg.text):
        await msg.answer(t(str(msg.from_user.id),"invalid_time"))
        return

    await state.update_data(time=msg.text)
    data = await state.get_data()

    if data["freq"] == "weekly":
        kb = InlineKeyboardMarkup()
        for i,d in enumerate(WEEK):
            kb.insert(InlineKeyboardButton(d, callback_data=f"d_{i}"))
        kb.add(InlineKeyboardButton("Done", callback_data="d_done"))
        await msg.answer(t(str(msg.from_user.id),"add_days"), reply_markup=kb)
        await AddMed.days.set()
    else:
        await state.update_data(days=[])
        await ask_food(msg, state)

@dp.callback_query_handler(lambda c: c.data.startswith("d_"), state=AddMed.days)
async def pick_days(call,state):
    if call.data == "d_done":
        await ask_food(call.message, state)
        return

    d = int(call.data.split("_")[1])
    data = await state.get_data()
    days = data.get("days", [])

    if d not in days:
        days.append(d)

    await state.update_data(days=days)
    await call.answer("ok")

async def ask_food(msg,state):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("Yes","No","Да","Нет","Tak","Nie")
    await msg.answer(t(str(msg.from_user.id),"add_food"), reply_markup=kb)
    await AddMed.food.set()

@dp.message_handler(state=AddMed.food)
async def add_food(msg,state):
    await state.update_data(food=msg.text.lower() in ["yes","да","tak"])
    await msg.answer(t(str(msg.from_user.id),"add_note"), reply_markup=ReplyKeyboardRemove())
    await AddMed.note.set()

@dp.message_handler(state=AddMed.note)
async def finish(msg,state):
    data = await state.get_data()
    uid = str(msg.from_user.id)

    meds = load(MEDS_FILE)
    meds.setdefault(uid, [])

    med = {
        "id": str(uuid.uuid4()),
        "name": data["name"],
        "dose": data["dose"],
        "time": data["time"],
        "freq": data["freq"],
        "days": data.get("days", []),
        "food": data["food"],
        "note": None if msg.text == "-" else msg.text
    }

    meds[uid].append(med)
    save(MEDS_FILE, meds)
    reload_jobs()

    await msg.answer(t(uid,"saved"), reply_markup=main_menu(uid))
    await state.finish()

# ================= DELETE =================

@dp.message_handler(commands=["delete"])
async def delete(msg):
    uid = str(msg.from_user.id)
    meds = load(MEDS_FILE).get(uid, [])

    kb = InlineKeyboardMarkup()
    for m in meds:
        kb.add(InlineKeyboardButton(m["name"], callback_data=f"del_{m['id']}"))

    await msg.answer("Delete:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("del_"))
async def delete_cb(call):
    uid = str(call.from_user.id)
    mid = call.data.split("_")[1]

    meds = load(MEDS_FILE)
    meds[uid] = [m for m in meds[uid] if m["id"] != mid]
    save(MEDS_FILE, meds)

    reload_jobs()
    await call.message.answer("Deleted ❌", reply_markup=main_menu(uid))

# ================= EDIT =================

@dp.message_handler(commands=["edit"])
async def edit(msg):
    uid = str(msg.from_user.id)
    meds = load(MEDS_FILE).get(uid, [])

    kb = InlineKeyboardMarkup()
    for m in meds:
        kb.add(InlineKeyboardButton(m["name"], callback_data=f"edit_{m['id']}"))

    await msg.answer("Edit:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_choose(call,state):
    await state.update_data(mid=call.data.split("_")[1])

    kb = InlineKeyboardMarkup()
    for f in ["name","dose","time","note"]:
        kb.add(InlineKeyboardButton(f, callback_data=f"f_{f}"))

    await call.message.answer("Field:", reply_markup=kb)
    await EditMed.field.set()

@dp.callback_query_handler(lambda c: c.data.startswith("f_"), state=EditMed.field)
async def edit_field(call,state):
    await state.update_data(field=call.data.split("_")[1])
    await call.message.answer("New value:")
    await EditMed.value.set()

@dp.message_handler(state=EditMed.value)
async def edit_save(msg,state):
    data = await state.get_data()
    uid = str(msg.from_user.id)

    meds = load(MEDS_FILE)

    for m in meds[uid]:
        if m["id"] == data["mid"]:
            if data["field"] == "time" and not validate_time(msg.text):
                await msg.answer("Invalid time")
                return
            m[data["field"]] = msg.text

    save(MEDS_FILE, meds)
    reload_jobs()

    await msg.answer("Updated ✅", reply_markup=main_menu(uid))
    await state.finish()

# ================= CALLBACKS =================

@dp.callback_query_handler(lambda c: c.data.startswith("take_"))
async def take(call):
    uid = str(call.from_user.id)
    mid = call.data.split("_")[1]

    logs = load(LOG_FILE)
    logs.setdefault(uid, []).append({
        "med_id": mid,
        "status": "taken",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

    save(LOG_FILE, logs)
    await call.message.answer("✅")

@dp.callback_query_handler(lambda c: c.data.startswith("skip_"))
async def skip(call):
    uid = str(call.from_user.id)
    mid = call.data.split("_")[1]

    logs = load(LOG_FILE)
    logs.setdefault(uid, []).append({
        "med_id": mid,
        "status": "skipped",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

    save(LOG_FILE, logs)
    await call.message.answer("❌")

@dp.callback_query_handler(lambda c: c.data.startswith("snooze_menu_"))
async def snooze_menu(call):
    mid = call.data.split("_")[2]
    await call.message.answer("⏳", reply_markup=snooze_kb(mid))

@dp.callback_query_handler(lambda c: c.data.startswith("snooze_") and not c.data.startswith("snooze_menu"))
async def snooze(call):
    uid = str(call.from_user.id)
    _, mid, mins = call.data.split("_")

    meds = load(MEDS_FILE)
    med = next(m for m in meds[uid] if m["id"] == mid)

    scheduler.add_job(
        send_reminder,
        "date",
        run_date=datetime.now() + timedelta(minutes=int(mins)),
        args=[uid, med]
    )

    await call.message.answer(f"+{mins}m")

# ================= LIST =================

@dp.message_handler(commands=["today"])
async def today(msg):
    uid = str(msg.from_user.id)
    logs = load(LOG_FILE).get(uid, [])

    today = datetime.now().strftime("%Y-%m-%d")
    entries = [e for e in logs if e["time"].startswith(today)]

    if not entries:
        await msg.answer(t(uid,"today_empty"), reply_markup=main_menu(uid))
        return

    await msg.answer("\n".join([f"{e['time'][11:]} {e['status']}" for e in entries]), reply_markup=main_menu(uid))

# ================= RUN =================

if __name__ == "__main__":
    scheduler.start()
    reload_jobs()
    executor.start_polling(dp, skip_updates=True)
