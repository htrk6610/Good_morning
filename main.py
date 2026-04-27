import asyncio
import logging
import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = '____'
DB_PATH = 'bot_users.db'

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        ''')
        await db.commit()

async def add_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        await db.commit()

async def remove_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
        await db.commit()

async def is_subscribed(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users') as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def get_cat_url():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://api.thecatapi.com/v1/images/search') as resp:
                data = await resp.json()
                return data[0]['url']
        except Exception:
            return "https://http.cat/500"

async def send_broadcast(text):
    users = await get_all_users()
    if not users:
        return
    
    cat_url = await get_cat_url()
    for user_id in users:
        try:
            await bot.send_photo(chat_id=user_id, photo=cat_url, caption=text)
        except Exception as e:
            logging.error(f"Ошибка доставки до {user_id}: {e}")

def main_menu(subscribed: bool):
    builder = InlineKeyboardBuilder()
    if subscribed:
        builder.button(text="🚫 Хватит кормить и слать котов", callback_data="unsubscribe")
    else:
        builder.button(text="✅ Подписаться на счастье", callback_data="subscribe")
    
    builder.button(text="🐈 Кот по требованию", callback_data="get_cat")
    builder.adjust(1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    sub = await is_subscribed(message.from_user.id)
    await message.answer(
        "Привет! Я — твой персональный диетолог-котофил.\n"
        "Буду слать тебе еду по расписанию и котов для душевного равновесия.",
        reply_markup=main_menu(sub)
    )

@dp.callback_query(F.data == "get_cat")
async def manual_cat(callback: types.CallbackQuery):
    url = await get_cat_url()
    await callback.message.answer_photo(url, caption="Твой внеочередной кусь-пакет доставлен!")
    await callback.answer()

@dp.callback_query(F.data == "subscribe")
async def sub_handler(callback: types.CallbackQuery):
    await add_user(callback.from_user.id)
    await callback.message.edit_text("Подписка оформлена! Готовь желудок и галерею.", 
        reply_markup=main_menu(True))

@dp.callback_query(F.data == "unsubscribe")
async def unsub_handler(callback: types.CallbackQuery):
    await remove_user(callback.from_user.id)
    await callback.message.edit_text("Вы отписались. Коты в печали, холодильник пуст.", 
        reply_markup=main_menu(False))

async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    jobs = [
        ("08:00", "Доброе утро! Время завтрака. Кот одобряет твою овсянку! 🥣"),
        ("11:00", "Время ланча! Перекуси, пока кот не съел твой кактус. ☕"),
        ("14:00", "ОБЕД! Пора подкрепиться серьезно. Приятного аппетита! 🍲"),
        ("16:30", "Полдник. Время чая и легких чесаний за ушком. 🥨"),
        ("19:00", "Ужин подан! Не забудь поделиться с... а, ладно, ешь сам. 🍗"),
        ("22:00", "Второй ужин (ночной дожор). Мы никому не скажем. 🍕"),
        ("23:30", "Спокойной ночи! Спи сладко, как котик на солнышке. 🌙")
    ]

    for time, msg in jobs:
        h, m = time.split(':')
        scheduler.add_job(send_broadcast, 'cron', hour=int(h), minute=int(m), args=[msg])

    scheduler.start()
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass