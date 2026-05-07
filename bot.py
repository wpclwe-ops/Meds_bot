import os
import re
import uuid
from datetime import datetime, timedelta

import asyncpg
import pytz

from aiogram import Bot, Dispatcher
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
    CallbackQuery
)
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# =========================================================
# CONFIG
# =========================================================

API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

TZ = pytz.timezone("Europe/Warsaw")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

scheduler = AsyncIOScheduler(timezone=TZ)

db = None
user_lang = {}

WEEK_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# =========================================================
# TEXTS
# =========================================================

TEXTS = {
    "en": {
        "choose_lang": "Choose language",

        "menu": "Menu",

        "add": "➕ Add",
        "today": "📋 Today",
        "edit": "✏️ Edit",
        "delete": "❌ Delete",
        "taken": "✅ Mark as taken",

        "name": "Enter medicine name:",
        "dose": "Enter dose:",
        "freq": "Choose frequency:",
        "daily": "Every day",
        "weekly": "Specific days",
        "time": "Enter time (HH:MM):",
        "days": "Select days:",
        "saved": "Saved ✅",

        "choose_med": "Select a medicine:",
        "choose_field": "Choose field:",
        "new_value": "Enter new value:",
        "updated": "Updated ✅",
        "deleted": "Deleted ❌",

        "today_empty": "No medicines today",

        "invalid": "Invalid input",
        "bad_time": "Invalid time format",

        "error": "Something went wrong. Please try again."
    },

    "ru": {
        "choose_lang": "Выберите язык",

        "menu": "Меню",

        "add": "➕ Добавить",
        "today": "📋 Сегодня",
        "edit": "✏️ Редактировать",
        "delete": "❌ Удалить",
        "taken": "✅ Отметить как принятое",

        "name": "Введите название лекарства:",
        "dose": "Введите дозировку:",
        "freq": "Выберите частоту:",
        "daily": "Каждый день",
        "weekly": "Выбрать дни",
        "time": "Введите время (ЧЧ:ММ):",
        "days": "Выберите дни:",
        "saved": "Сохранено ✅",

        "choose_med": "Выберите лекарство:",
        "choose_field": "Выберите поле:",
        "new_value": "Введите новое значение:",
        "updated": "Обновлено ✅",
        "deleted": "Удалено ❌",

        "today_empty": "Сегодня ничего нет",

        "invalid": "Неверный ввод",
        "bad_time": "Неверный формат времени",

        "error": "Что-то пошло не так. Попробуйте снова."
    },

    "pl": {
        "choose_lang": "Wybierz język",

        "menu": "Menu",

        "add": "➕ Dodaj",
        "today": "📋 Dzisiaj",
        "edit": "✏️ Edytuj",
        "delete": "❌ Usuń",
        "taken": "✅ Oznacz jako przyjęte",

        "name": "Podaj nazwę leku:",
        "dose": "Podaj dawkę:",
        "freq": "Wybierz częstotliwość:",
        "daily": "Codziennie",
        "weekly": "Wybrane dni",
        "time": "Podaj godzinę (HH:MM):",
        "days": "Wybierz dni:",
        "saved": "Zapisano ✅",

        "choose_med": "Wybierz lek:",
        "choose_field": "Wybierz pole:",
        "new_value": "Podaj nową wartość:",
        "updated": "Zaktualizowano ✅",
        "deleted": "Usunięto ❌",

        "today_empty": "Brak leków na dziś",

        "invalid": "Nieprawidłowe dane",
        "bad_time": "Zły format godziny",

        "error": "Coś poszło nie tak. Spróbuj ponownie."
    }
}


# =========================================================
# HELPERS
# =========================================================

def t(uid, key):
    lang = user_lang.get(uid, "en")
    return TEXTS.get(lang, TEXTS["en"]).get(key, key)


def valid_text(text):
    return bool(re.match(r"^[\w\s.,%+\-/()]+$", text))


def valid_time(text):
    try:
        datetime.strptime(text, "%H:%M")
        return True
    except:
        return False


def main_menu(uid):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    kb.add(
        KeyboardButton(t(uid, "add")),
        KeyboardButton(t(uid, "today"))
    )

    kb.add(
        KeyboardButton(t(uid, "edit")),
        KeyboardButton(t(uid, "delete"))
    )

    kb.add(
        KeyboardButton(t(uid, "taken"))
    )

    return kb


def days_keyboard(selected=None):
    if selected is None:
        selected = []

    kb = InlineKeyboardMarkup(row_width=3)

    for i, day in enumerate(WEEK_DAYS):
        text = f"✔ {day}" if i in selected else day

        kb.insert(
            InlineKeyboardButton(
                text=text,
                callback_data=f"day_{i}"
            )
        )

    kb.add(
        InlineKeyboardButton(
            text="Done",
            callback_data="days_done"
        )
    )

    return kb


# =========================================================
# DATABASE
# =========================================================

async def init_db():
    global db

    db = await asyncpg.connect(DATABASE_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id TEXT PRIMARY KEY,
        lang TEXT
    )
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS meds(
        id TEXT PRIMARY KEY,
        user_id TEXT,
        name TEXT,
        dose TEXT,
        med_time TEXT,
        freq TEXT,
        days INT[]
    )
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS logs(
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        med_id TEXT,
        status TEXT,
        created_at TIMESTAMP
    )
    """)


# =========================================================
# SCHEDULER
# =========================================================

async def send_reminder(user_id, med):
    kb = InlineKeyboardMarkup()

    kb.add(
        InlineKeyboardButton(
            "✅",
            callback_data=f"take_{med['id']}"
        ),

        InlineKeyboardButton(
            "⏳",
            callback_data=f"snooze_{med['id']}_15"
        ),

        InlineKeyboardButton(
            "❌",
            callback_data=f"skip_{med['id']}"
        )
    )

    await bot.send_message(
        user_id,
        f"💊 {med['name']} ({med['dose']})",
        reply_markup=kb
    )


def schedule_med(med):
    hour, minute = map(int, med["med_time"].split(":"))

    try:
        scheduler.remove_job(med["id"])
    except:
        pass

    if med["freq"] == "daily":

        scheduler.add_job(
            send_reminder,
            trigger="cron",
            id=med["id"],
            hour=hour,
            minute=minute,
            args=[med["user_id"], med]
        )

    else:

        scheduler.add_job(
            send_reminder,
            trigger="cron",
            id=med["id"],
            day_of_week=",".join(
                [WEEK_DAYS[d] for d in med["days"]]
            ),
            hour=hour,
            minute=minute,
            args=[med["user_id"], med]
        )


async def reload_jobs():
    scheduler.remove_all_jobs()

    meds = await db.fetch("SELECT * FROM meds")

    for med in meds:
        schedule_med(dict(med))


# =========================================================
# STATES
# =========================================================

class AddMed(StatesGroup):
    name = State()
    dose = State()
    freq = State()
    time = State()
    days = State()


# =========================================================
# START
# =========================================================

@dp.message_handler(commands=["start"])
async def start(message):

    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    kb.add(
        KeyboardButton("English"),
        KeyboardButton("Русский"),
        KeyboardButton("Polski")
    )

    await message.answer(
        "Choose language",
        reply_markup=kb
    )


@dp.message_handler(lambda m: m.text and m.text in ["English", "Русский", "Polski"])
async def set_language(message):

    uid = str(message.from_user.id)

    lang_map = {
        "English": "en",
        "Русский": "ru",
        "Polski": "pl"
    }

    lang = lang_map[message.text]

    user_lang[uid] = lang

    await db.execute("""
    INSERT INTO users(user_id, lang)
    VALUES($1, $2)
    ON CONFLICT (user_id)
    DO UPDATE SET lang=$2
    """, uid, lang)

    await message.answer(
        t(uid, "menu"),
        reply_markup=main_menu(uid)
    )


# =========================================================
# ADD
# =========================================================

@dp.message_handler(lambda m: m.text and (
    "Add" in m.text
    or "Добавить" in m.text
    or "Dodaj" in m.text
))
async def add_start(message):

    await message.answer(
        t(str(message.from_user.id), "name")
    )

    await AddMed.name.set()


@dp.message_handler(state=AddMed.name)
async def add_name(message, state):

    uid = str(message.from_user.id)

    if not valid_text(message.text):
        return await message.answer(t(uid, "invalid"))

    await state.update_data(name=message.text)

    await message.answer(t(uid, "dose"))

    await AddMed.dose.set()


@dp.message_handler(state=AddMed.dose)
async def add_dose(message, state):

    uid = str(message.from_user.id)

    await state.update_data(dose=message.text)

    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    kb.add(
        KeyboardButton(t(uid, "daily")),
        KeyboardButton(t(uid, "weekly"))
    )

    await message.answer(
        t(uid, "freq"),
        reply_markup=kb
    )

    await AddMed.freq.set()


@dp.message_handler(state=AddMed.freq)
async def add_freq(message, state):

    txt = message.text.lower()

    freq = "daily"

    if (
        "specific" in txt
        or "выб" in txt
        or "wybrane" in txt
    ):
        freq = "weekly"

    await state.update_data(freq=freq)

    await message.answer(
        t(str(message.from_user.id), "time"),
        reply_markup=ReplyKeyboardRemove()
    )

    await AddMed.time.set()


@dp.message_handler(state=AddMed.time)
async def add_time(message, state):

    uid = str(message.from_user.id)

    if not valid_time(message.text):
        return await message.answer(t(uid, "bad_time"))

    await state.update_data(med_time=message.text)

    data = await state.get_data()

    if data["freq"] == "weekly":

        await message.answer(
            t(uid, "days"),
            reply_markup=days_keyboard()
        )

        await AddMed.days.set()

    else:

        await finish_add(message, state)


@dp.callback_query_handler(lambda c: c.data.startswith("day_"), state=AddMed.days)
async def select_days(call, state):

    data = await state.get_data()

    selected = data.get("days", [])

    day = int(call.data.split("_")[1])

    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)

    await state.update_data(days=selected)

    await call.message.edit_reply_markup(
        days_keyboard(selected)
    )


@dp.callback_query_handler(lambda c: c.data == "days_done", state=AddMed.days)
async def finish_days(call, state):

    data = await state.get_data()

    if not data.get("days"):
        return await call.answer("Select at least one day")

    await finish_add(call.message, state)


async def finish_add(message, state):

    uid = str(message.chat.id)

    data = await state.get_data()

    med = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "name": data["name"],
        "dose": data["dose"],
        "med_time": data["med_time"],
        "freq": data["freq"],
        "days": data.get("days", [])
    }

    await db.execute("""
    INSERT INTO meds(
        id,
        user_id,
        name,
        dose,
        med_time,
        freq,
        days
    )
    VALUES($1,$2,$3,$4,$5,$6,$7)
    """,
        med["id"],
        med["user_id"],
        med["name"],
        med["dose"],
        med["med_time"],
        med["freq"],
        med["days"]
    )

    schedule_med(med)

    await message.answer(
        t(uid, "saved"),
        reply_markup=main_menu(uid)
    )

    await state.finish()


# =========================================================
# TODAY
# =========================================================

@dp.message_handler(lambda m: m.text and (
    "Today" in m.text
    or "Сегодня" in m.text
    or "Dzisiaj" in m.text
))
async def today(message):

    uid = str(message.from_user.id)

    now = datetime.now(TZ)

    meds = await db.fetch("""
    SELECT * FROM meds
    WHERE user_id=$1
    """, uid)

    logs = await db.fetch("""
    SELECT * FROM logs
    WHERE user_id=$1
    AND DATE(created_at AT TIME ZONE 'UTC'
    AT TIME ZONE 'Europe/Warsaw') = CURRENT_DATE
    """, uid)

    log_map = {
        log["med_id"]: log["status"]
        for log in logs
    }

    result = []

    for med in meds:

        days = med["days"] or []

        show = (
            med["freq"] == "daily"
            or now.weekday() in days
        )

        if not show:
            continue

        status = log_map.get(
            med["id"],
            "pending"
        )

        icon = {
            "taken": "✅",
            "skipped": "❌",
            "snoozed": "⏳"
        }.get(status, "⏺")

        result.append(
            f"{med['med_time']} {med['name']} {icon}"
        )

    result = sorted(result)

    if not result:
        return await message.answer(
            t(uid, "today_empty")
        )

    await message.answer("\n".join(result))


# =========================================================
# TAKEN MENU
# =========================================================

@dp.message_handler(lambda m: m.text and (
    "Mark as taken" in m.text
    or "принятое" in m.text.lower()
    or "przyjęte" in m.text.lower()
))
async def taken_menu(message):

    uid = str(message.from_user.id)

    now = datetime.now(TZ)

    meds = await db.fetch("""
    SELECT * FROM meds
    WHERE user_id=$1
    """, uid)

    kb = InlineKeyboardMarkup()

    found = False

    for med in meds:

        days = med["days"] or []

        show = (
            med["freq"] == "daily"
            or now.weekday() in days
        )

        if not show:
            continue

        found = True

        kb.add(
            InlineKeyboardButton(
                med["name"],
                callback_data=f"take_{med['id']}"
            )
        )

    if not found:
        return await message.answer(
            t(uid, "today_empty")
        )

    await message.answer(
        t(uid, "choose_med"),
        reply_markup=kb
    )


# =========================================================
# CALLBACKS
# =========================================================

@dp.callback_query_handler(lambda c: c.data.startswith("take_"))
async def take_callback(call: CallbackQuery):

    uid = str(call.from_user.id)

    med_id = call.data.split("_")[1]

    await db.execute("""
    INSERT INTO logs(
        user_id,
        med_id,
        status,
        created_at
    )
    VALUES($1,$2,'taken',NOW())
    """, uid, med_id)

    await call.answer("Taken ✅")

    try:
        await call.message.edit_reply_markup()
    except:
        pass


@dp.callback_query_handler(lambda c: c.data.startswith("skip_"))
async def skip_callback(call: CallbackQuery):

    uid = str(call.from_user.id)

    med_id = call.data.split("_")[1]

    await db.execute("""
    INSERT INTO logs(
        user_id,
        med_id,
        status,
        created_at
    )
    VALUES($1,$2,'skipped',NOW())
    """, uid, med_id)

    await call.answer("Skipped ❌")

    try:
        await call.message.edit_reply_markup()
    except:
        pass


@dp.callback_query_handler(lambda c: c.data.startswith("snooze_"))
async def snooze_callback(call: CallbackQuery):

    uid = str(call.from_user.id)

    _, med_id, mins = call.data.split("_")

    med = await db.fetchrow("""
    SELECT * FROM meds
    WHERE id=$1
    """, med_id)

    if not med:
        return await call.answer("Medicine not found")

    await db.execute("""
    INSERT INTO logs(
        user_id,
        med_id,
        status,
        created_at
    )
    VALUES($1,$2,'snoozed',NOW())
    """, uid, med_id)

    scheduler.add_job(
        send_reminder,
        trigger="date",
        run_date=datetime.now(TZ) + timedelta(minutes=int(mins)),
        args=[uid, dict(med)]
    )

    await call.answer(f"Snoozed for {mins} min ⏳")

    try:
        await call.message.edit_reply_markup()
    except:
        pass


# =========================================================
# DELETE
# =========================================================

@dp.message_handler(lambda m: m.text and (
    "Delete" in m.text
    or "Удалить" in m.text
    or "Usuń" in m.text
))
async def delete_menu(message):

    uid = str(message.from_user.id)

    meds = await db.fetch("""
    SELECT id, name FROM meds
    WHERE user_id=$1
    """, uid)

    kb = InlineKeyboardMarkup()

    for med in meds:

        kb.add(
            InlineKeyboardButton(
                med["name"],
                callback_data=f"delete_{med['id']}"
            )
        )

    await message.answer(
        t(uid, "choose_med"),
        reply_markup=kb
    )


@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def delete_callback(call: CallbackQuery):

    med_id = call.data.split("_")[1]

    try:
        scheduler.remove_job(med_id)
    except:
        pass

    await db.execute("""
    DELETE FROM meds
    WHERE id=$1
    """, med_id)

    await db.execute("""
    DELETE FROM logs
    WHERE med_id=$1
    """, med_id)

    await call.answer("Deleted")

    try:
        await call.message.edit_reply_markup()
    except:
        pass


# =========================================================
# FALLBACK
# =========================================================

@dp.message_handler()
async def fallback(message):

    uid = str(message.from_user.id)

    await message.answer(
        t(uid, "error"),
        reply_markup=main_menu(uid)
    )


# =========================================================
# STARTUP
# =========================================================

async def on_startup(_):

    await init_db()

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    rows = await db.fetch("""
    SELECT * FROM users
    """)

    for row in rows:
        user_lang[row["user_id"]] = row["lang"]

    await reload_jobs()

    scheduler.start()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    executor.start_polling(
        dp,
        on_startup=on_startup,
        skip_updates=True
    )
