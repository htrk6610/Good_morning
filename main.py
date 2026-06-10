from aiohttp import web
import asyncio
import logging
import os
import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
if TOKEN:
    TOKEN = TOKEN.strip().replace('"', '').replace("'", "")
    if "=" in TOKEN:
        TOKEN = TOKEN.split("=")[-1].strip()
    TOKEN = TOKEN.replace(" ", "")

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.strip().replace('"', '').replace("'", "")
    if "=" in DATABASE_URL and not DATABASE_URL.startswith("postgres"):
        DATABASE_URL = DATABASE_URL.split("=")[-1].strip()
    DATABASE_URL = DATABASE_URL.replace(" ", "")

# Хак для библиотеки asyncpg (требуется схема postgres://)
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgres://", 1)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(level=logging.INFO)

# Чистая и стандартная инициализация
bot = None
dp = Dispatcher()
scheduler = AsyncIOScheduler()
db_pool = None

# --- БАЗА ДАННЫХ (POSTGRESQL) ---
async def init_db():
    global db_pool
    # Отключаем кэш запросов для корректной работы пулера Neon
    db_pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)

    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language_code TEXT,
                cat_requests INTEGER DEFAULT 0,
                sub_morning BOOLEAN DEFAULT FALSE,
                sub_night BOOLEAN DEFAULT FALSE,
                sub_breakfast BOOLEAN DEFAULT FALSE,
                sub_lunch BOOLEAN DEFAULT FALSE,
                sub_obed BOOLEAN DEFAULT FALSE,
                sub_snack BOOLEAN DEFAULT FALSE,
                sub_supper BOOLEAN DEFAULT FALSE,
                sub_second_supper BOOLEAN DEFAULT FALSE
            )
        ''')

async def upsert_user(user: types.User):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (user_id, username, first_name, language_code) 
            VALUES ($1, $2, $3, $4)
            ON CONFLICT(user_id) DO UPDATE SET 
            username=EXCLUDED.username, 
            first_name=EXCLUDED.first_name, 
            language_code=EXCLUDED.language_code
        ''', user.id, user.username, user.first_name, user.language_code)

async def get_user_subs(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
        return dict(row) if row else None

async def toggle_sub(user_id: int, column: str):
    async with db_pool.acquire() as conn:
        await conn.execute(f'UPDATE users SET {column} = NOT {column} WHERE user_id = $1', user_id)

async def set_all_subs(user_id: int, state: bool):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            UPDATE users SET 
            sub_morning=$1, sub_night=$1, sub_breakfast=$1, sub_lunch=$1, 
            sub_obed=$1, sub_snack=$1, sub_supper=$1, sub_second_supper=$1 
            WHERE user_id = $2
        ''', state, user_id)

async def increment_cat_request(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET cat_requests = cat_requests + 1 WHERE user_id = $1', user_id)

# --- СЕРВИС КОТИКОВ ---
async def get_cat_url():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get('https://api.thecatapi.com/v1/images/search') as resp:
                data = await resp.json()
                return data[0]['url']
        except Exception:
            return "https://http.cat/500"

# --- РАССЫЛКИ ---
async def send_scheduled_cat(text: str, column_name: str):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(f'SELECT user_id FROM users WHERE {column_name} = TRUE')
        
    if not rows:
        return
    
    cat_url = await get_cat_url()
    for row in rows:
        user_id = row['user_id']
        try:
            await bot.send_photo(chat_id=user_id, photo=cat_url, caption=text)
        except Exception as e:
            logging.error(f"Ошибка доставки до {user_id}: {e}")

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🐈 Кіт на вимогу")
    builder.button(text="⚙️ Мої підписки")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_settings_keyboard(user_data: dict):
    def check(val): return "✅" if val else "❌"
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Ранок {check(user_data['sub_morning'])}", callback_data="tgl_sub_morning")
    builder.button(text=f"Нічь {check(user_data['sub_night'])}", callback_data="tgl_sub_night")
    builder.button(text=f"Сніданок {check(user_data['sub_breakfast'])}", callback_data="tgl_sub_breakfast")
    builder.button(text=f"Ланч {check(user_data['sub_lunch'])}", callback_data="tgl_sub_lunch")
    builder.button(text=f"Обід {check(user_data['sub_obed'])}", callback_data="tgl_sub_obed")
    builder.button(text=f"Полуденок {check(user_data['sub_snack'])}", callback_data="tgl_sub_snack")
    builder.button(text=f"Вечеря {check(user_data['sub_supper'])}", callback_data="tgl_sub_supper")
    builder.button(text=f"Нічний жор {check(user_data['sub_second_supper'])}", callback_data="tgl_sub_second_supper")
    
    builder.button(text="Вімкни ВСЕ", callback_data="all_on")
    builder.button(text="Вимкни ВСЕ", callback_data="all_off")
    
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await upsert_user(message.from_user)
    await message.answer(
        "Привіт! Я — твій персональний дієтолог- кіт.\n"
        "Скористайся меню знизу, щоб налаштувати наш графік кітов.",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "🐈 Кіт на вимогу")
async def manual_cat_text(message: types.Message):
    await upsert_user(message.from_user)
    await increment_cat_request(message.from_user.id)
    url = await get_cat_url()
    await message.answer_photo(url, caption="Твій позачерговий кіт!")

@dp.message(F.text == "⚙️ Мої підписки")
async def settings_menu(message: types.Message):
    await upsert_user(message.from_user)
    user_data = await get_user_subs(message.from_user.id)
    await message.answer("Налаштуй свій ідеальний графік отримання кітов:", reply_markup=get_settings_keyboard(user_data))

@dp.callback_query(F.data.startswith("tgl_"))
async def toggle_handler(callback: types.CallbackQuery):
    column = callback.data.replace("tgl_", "")
    await toggle_sub(callback.from_user.id, column)
    user_data = await get_user_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user_data))
    await callback.answer()

@dp.callback_query(F.data.in_(["all_on", "all_off"]))
async def toggle_all_handler(callback: types.CallbackQuery):
    state = True if callback.data == "all_on" else False
    await set_all_subs(callback.from_user.id, state)
    user_data = await get_user_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(user_data))
    await callback.answer("Настройки обновлены!")

# --- АДМИН ПАНЕЛЬ ---
@dp.message(Command("users"), F.from_user.id == ADMIN_ID)
async def admin_list_users(message: types.Message):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id, username, first_name FROM users LIMIT 100')
            
    if not rows:
        await message.answer("В базе пока никого нет.")
        return
        
    text = "📋 **Список пользователей (топ 100):**\n\n"
    for row in rows:
        username_str = f"@{row['username']}" if row['username'] else "Нет юзернейма"
        text += f"ID: `{row['user_id']}` | {username_str} | {row['first_name']}\n"
        
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("send"), F.from_user.id == ADMIN_ID)
async def admin_send_private(message: types.Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("⚠️ Использование: `/send <ID_пользователя> <Текст>`", parse_mode="Markdown")
        return
        
    try:
        target_user_id = int(parts[1])
        text = parts[2]
    except ValueError:
        await message.answer("❌ Ошибка: ID должен быть числом.")
        return

    cat_url = await get_cat_url()
    try:
        await bot.send_photo(chat_id=target_user_id, photo=cat_url, caption=f"💌 **Персональна доставка:**\n\n{text}", parse_mode="Markdown")
        await message.answer(f"✅ Успешно отправлено юзеру {target_user_id}!")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: {e}")

@dp.message(Command("broadcast"), F.from_user.id == ADMIN_ID)
async def admin_broadcast(message: types.Message):
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Использование: /broadcast Текст")
        return

    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id FROM users')
            
    cat_url = await get_cat_url()
    count = 0
    for row in rows:
        try:
            await bot.send_photo(chat_id=row['user_id'], photo=cat_url, caption=f"🎁 ПОЗАЧЕРГОВИЙ КІТ ДЛЯ НАСТРОЮ!\n\n{text}")
            count += 1
        except Exception:
            pass
    await message.answer(f"Рассылка завершена. Доставлено: {count} юзерам.")

@dp.message()
async def echo_all_unhandled(message: types.Message):
    await message.answer(
        f"🤖 Омнисія почув твій меседж: *'{message.text}'*, але я розумію тільки мову кітов!\n\n"
        f"Використовуй меню знизу або команду /start",
        parse_mode="Markdown"
    )

# --- ЗАПУСК ---
async def main():
    global bot
    if not TOKEN or not DATABASE_URL:
        logging.error("Переменные окружения BOT_TOKEN или DATABASE_URL не заданы!")
        return

    # Подключаем базу данных
    await init_db()
    
    # Инициализируем бота стандартным способом строго внутри запущенного event loop
    bot = Bot(token=TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    
    jobs = [
        ("07:30", "Доброго ранку! Прокидайся, котики вже не сплять! ☀️", "sub_morning"),
        ("07:45", "Час сніданку. Кіт схвалює твою вівсянку! 🥣", "sub_breakfast"),
        ("10:45", "Час ланчу! Перекуси, поки кіт не з'їв твій кактус. ☕", "sub_lunch"),
        ("14:00", "ОБІД! Пора підкріпитися серйозно. 🍲", "sub_obed"),
        ("16:00", "Полуденок. Час чаю та легких чухань за вушком. 🥨", "sub_snack"),
        ("17:30", "Вечеря подана! Смачного. 🍗", "sub_supper"),
        ("21:00", "Друга вечеря (нічний дожор). Ми нікому не скажемо. 🍕", "sub_second_supper"),
        ("23:00", "На добраніч! Солодких снів. 🌙", "sub_night")
    ]

    for time, msg, column in jobs:
        h, m = time.split(':')
        scheduler.add_job(send_scheduled_cat, 'cron', hour=int(h), minute=int(m), args=[msg, column])

    scheduler.start()
    
    logging.info("Бот успешно запускается, открываем соединение с Telegram...")
    
# --- ЗАГЛУШКА ДЛЯ RENDER (HEALTH CHECK) ---
    async def handle_health(request):
        return web.Response(text="Bot is alive and kicking!")

    app = web.Application()
    app.router.add_get('/', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render сам выдает порт в переменные окружения, по умолчанию берем 10000
    render_port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", render_port)
    await site.start()
    logging.info(f"Фоновый веб-сервер для Render запущен на порту {render_port}")
    
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass