# ⚠️ большой код — это нормально

import os, uuid, re
from datetime import datetime, timedelta
import pytz, asyncpg

from aiogram import Bot, Dispatcher
from aiogram.types import *
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

API_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
TZ = pytz.timezone("Europe/Warsaw")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TZ)

db = None
user_lang = {}

# ===== TEXTS =====

TEXTS = {
"en":{"menu":"Menu","add":"➕ Add","today":"📋 Today","edit":"✏️ Edit","delete":"❌ Delete","taken":"✅ Mark as taken",
"name":"Enter medicine name:","dose":"Enter dose:","freq":"Choose frequency:",
"daily":"Every day","weekly":"Specific days","time":"Enter time (HH:MM):","days":"Select days:",
"saved":"Saved ✅","choose_med":"Select a medicine:","choose_field":"Choose field:",
"new_val":"Enter new value:","updated":"Updated ✅","deleted":"Deleted ❌",
"today_empty":"No medicines today","invalid":"Invalid input","bad_time":"Invalid time format",
"error":"Something went wrong. Please try again."},

"ru":{"menu":"Меню","add":"➕ Добавить","today":"📋 Сегодня","edit":"✏️ Редактировать","delete":"❌ Удалить","taken":"✅ Отметить как принятое",
"name":"Введите название лекарства:","dose":"Введите дозировку:","freq":"Выберите частоту:",
"daily":"Каждый день","weekly":"Выбрать дни","time":"Введите время (ЧЧ:ММ):","days":"Выберите дни:",
"saved":"Сохранено ✅","choose_med":"Выберите лекарство:","choose_field":"Выберите поле:",
"new_val":"Введите новое значение:","updated":"Обновлено ✅","deleted":"Удалено ❌",
"today_empty":"Сегодня ничего нет","invalid":"Неверный ввод","bad_time":"Неверный формат времени",
"error":"Что-то пошло не так. Попробуйте снова."},

"pl":{"menu":"Menu","add":"➕ Dodaj","today":"📋 Dzisiaj","edit":"✏️ Edytuj","delete":"❌ Usuń","taken":"✅ Oznacz jako przyjęte",
"name":"Podaj nazwę leku:","dose":"Podaj dawkę:","freq":"Wybierz częstotliwość:",
"daily":"Codziennie","weekly":"Wybrane dni","time":"Podaj godzinę (HH:MM):","days":"Wybierz dni:",
"saved":"Zapisano ✅","choose_med":"Wybierz lek:","choose_field":"Wybierz pole:",
"new_val":"Podaj nową wartość:","updated":"Zaktualizowano ✅","deleted":"Usunięto ❌",
"today_empty":"Brak leków na dziś","invalid":"Nieprawidłowe dane","bad_time":"Zły format godziny",
"error":"Coś poszło nie tak. Spróbuj ponownie."}
}

def t(uid,k): return TEXTS[user_lang.get(uid,"en")][k]

WEEK=["mon","tue","wed","thu","fri","sat","sun"]

def valid_text(x): return bool(re.match(r"^[\w\s.,%+\-/()]+$", x))

def valid_time(x):
    try: datetime.strptime(x,"%H:%M"); return True
    except: return False

def menu(uid):
    kb=ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(t(uid,"add"),t(uid,"today"))
    kb.add(t(uid,"edit"),t(uid,"delete"))
    kb.add(t(uid,"taken"))
    return kb

def days_kb(selected=[]):
    kb=InlineKeyboardMarkup(row_width=3)
    for i,d in enumerate(WEEK):
        label=f"✔ {d}" if i in selected else d
        kb.insert(InlineKeyboardButton(label,callback_data=f"d_{i}"))
    kb.add(InlineKeyboardButton("Done",callback_data="done"))
    return kb

async def send_reminder(uid,med):
    kb=InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅",callback_data=f"take_{med['id']}"),
        InlineKeyboardButton("⏳",callback_data=f"snooze_{med['id']}_15"),
        InlineKeyboardButton("❌",callback_data=f"skip_{med['id']}")
    )
    await bot.send_message(uid,f"💊 {med['name']} ({med['dose']})",reply_markup=kb)

def schedule(m):
    h,mn=map(int,m["time"].split(":"))
    try: scheduler.remove_job(m["id"])
    except: pass

    if m["freq"]=="daily":
        scheduler.add_job(send_reminder,"cron",args=[m["user_id"],m],hour=h,minute=mn,id=m["id"])
    else:
        scheduler.add_job(send_reminder,"cron",
            args=[m["user_id"],m],
            day_of_week=",".join([WEEK[d] for d in m["days"]]),
            hour=h,minute=mn,id=m["id"])

async def reload_jobs():
    scheduler.remove_all_jobs()
    rows=await db.fetch("SELECT * FROM meds")
    for r in rows:
        schedule(dict(r))

async def init_db():
    global db
    db = await asyncpg.connect(DB_URL)

    await db.execute("""CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY, lang TEXT);""")
    await db.execute("""CREATE TABLE IF NOT EXISTS meds(id TEXT PRIMARY KEY,user_id TEXT,name TEXT,dose TEXT,time TEXT,freq TEXT,days INT[]);""")
    await db.execute("""CREATE TABLE IF NOT EXISTS logs(id SERIAL PRIMARY KEY,user_id TEXT,med_id TEXT,status TEXT,time TIMESTAMP);""")

# ===== START =====

@dp.message_handler(commands=["start"])
async def start(msg):
    kb=ReplyKeyboardMarkup(resize_keyboard=True).add("English","Русский","Polski")
    await msg.answer("Choose language",reply_markup=kb)

@dp.message_handler(lambda m: m.text in ["English","Русский","Polski"])
async def set_lang(msg):
    uid=str(msg.from_user.id)
    langs={"English":"en","Русский":"ru","Polski":"pl"}
    user_lang[uid]=langs[msg.text]

    await db.execute("""
    INSERT INTO users(user_id,lang) VALUES($1,$2)
    ON CONFLICT (user_id) DO UPDATE SET lang=$2
    """,uid,langs[msg.text])

    await msg.answer(t(uid,"menu"),reply_markup=menu(uid))

# ===== ADD =====

class Add(StatesGroup):
    name=State();dose=State();freq=State();time=State();days=State()

@dp.message_handler(lambda m: "Add" in m.text or "Добавить" in m.text or "Dodaj" in m.text)
async def add(msg):
    await msg.answer(t(str(msg.from_user.id),"name"))
    await Add.name.set()

@dp.message_handler(state=Add.name)
async def a1(msg,state):
    if not valid_text(msg.text):
        return await msg.answer(t(str(msg.from_user.id),"invalid"))
    await state.update_data(name=msg.text)
    await msg.answer(t(str(msg.from_user.id),"dose"))
    await Add.dose.set()

@dp.message_handler(state=Add.dose)
async def a2(msg,state):
    await state.update_data(dose=msg.text)
    uid=str(msg.from_user.id)
    kb=ReplyKeyboardMarkup(resize_keyboard=True).add(t(uid,"daily"),t(uid,"weekly"))
    await msg.answer(t(uid,"freq"),reply_markup=kb)
    await Add.freq.set()

@dp.message_handler(state=Add.freq)
async def a3(msg,state):
    txt=msg.text.lower()
    await state.update_data(freq="daily" if "day" in txt or "каж" in txt or "cod" in txt else "weekly")
    await msg.answer(t(str(msg.from_user.id),"time"),reply_markup=ReplyKeyboardRemove())
    await Add.time.set()

@dp.message_handler(state=Add.time)
async def a4(msg,state):
    if not valid_time(msg.text):
        return await msg.answer(t(str(msg.from_user.id),"bad_time"))
    await state.update_data(time=msg.text)
    data=await state.get_data()

    if data["freq"]=="weekly":
        await msg.answer(t(str(msg.from_user.id),"days"),reply_markup=days_kb())
        await Add.days.set()
    else:
        await finish_add(msg,state)

@dp.callback_query_handler(lambda c:c.data.startswith("d_"),state=Add.days)
async def a_days(call,state):
    if call.data=="done":
        data=await state.get_data()
        if not data.get("days"):
            await call.answer("Select at least one day")
            return
        await finish_add(call.message,state)
        return

    d=int(call.data.split("_")[1])
    data=await state.get_data()
    days=data.get("days") or []

    if d in days:
        days.remove(d)
    else:
        days.append(d)

    await state.update_data(days=days)
    await call.message.edit_reply_markup(days_kb(days))

async def finish_add(msg,state):
    data=await state.get_data()
    uid=str(msg.from_user.id)

    m={"id":str(uuid.uuid4()),"user_id":uid,**data,"days":data.get("days") or []}

    await db.execute("""INSERT INTO meds VALUES($1,$2,$3,$4,$5,$6,$7)""",
        m["id"],m["user_id"],m["name"],m["dose"],m["time"],m["freq"],m["days"])

    schedule(m)
    await msg.answer(t(uid,"saved"),reply_markup=menu(uid))
    await state.finish()

# ===== STARTUP =====

async def on_startup(dp):
    await init_db()

    # 🔥 КРИТИЧНЫЙ ФИКС
    await bot.delete_webhook(drop_pending_updates=True)

    rows=await db.fetch("SELECT * FROM users")
    for r in rows:
        user_lang[r["user_id"]] = r["lang"]

    await reload_jobs()
    scheduler.start()

if __name__=="__main__":
    executor.start_polling(dp,on_startup=on_startup)
