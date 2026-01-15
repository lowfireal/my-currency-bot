import asyncio
import logging
import os
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")  # Сюда вставим ссылку из Neon

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ FSM ---
class AdminState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()
    waiting_for_action = State()
    waiting_for_broadcast_amount = State()

# --- БАЗА ДАННЫХ (POSTGRESQL) ---
async def create_pool():
    return await asyncpg.create_pool(dsn=DATABASE_URL)

async def init_db(pool):
    async with pool.acquire() as connection:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                nickname TEXT,
                balance BIGINT DEFAULT 0
            )
        """)

async def add_user(pool, user_id: int, username: str):
    async with pool.acquire() as connection:
        # В Postgres синтаксис отличается от SQLite
        await connection.execute("""
            INSERT INTO users (user_id, username, nickname, balance)
            VALUES ($1, $2, $3, 0)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username, username)

async def get_user(pool, user_id: int):
    async with pool.acquire() as connection:
        return await connection.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def update_balance(pool, user_id: int, amount: int):
    async with pool.acquire() as connection:
        await connection.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, user_id)

async def set_nickname(pool, user_id: int, new_nick: str):
    async with pool.acquire() as connection:
        await connection.execute("UPDATE users SET nickname = $1 WHERE user_id = $2", new_nick, user_id)

async def get_all_users(pool):
    async with pool.acquire() as connection:
        return await connection.fetch("SELECT user_id FROM users")

# --- ЛОГИКА БОТА ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await add_user(bot.db_pool, message.from_user.id, message.from_user.username or "NoName")
    await message.answer("Привет! Ты в базе. Данные теперь надежно сохранены.")

@dp.message(Command("me"))
async def cmd_me(message: types.Message):
    await add_user(bot.db_pool, message.from_user.id, message.from_user.username or "NoName")
    user = await get_user(bot.db_pool, message.from_user.id)
    if user:
        # В asyncpg доступ по именам колонок или индексам
        text = (
            f"👤 <b>Профиль:</b> {user['nickname']}\n"
            f"💰 <b>Баланс:</b> {user['balance']} монет\n"
            f"🆔 <b>ID:</b> <code>{user['user_id']}</code>"
        )
        await message.answer(text, parse_mode="HTML")

# --- АДМИНСКИЕ ФУНКЦИИ ---
def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать", callback_data="admin_add"),
         InlineKeyboardButton(text="➖ Отнять", callback_data="admin_remove")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])
    await message.answer("Админка:", reply_markup=kb)

@dp.callback_query(F.data.in_({"admin_add", "admin_remove"}))
async def process_money_action(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(action=callback.data.split("_")[1])
    await callback.message.answer("Введи ID юзера:")
    await state.set_state(AdminState.waiting_for_user_id)
    await callback.answer()

@dp.message(AdminState.waiting_for_user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Нужны цифры.")
    await state.update_data(user_id=int(message.text))
    await message.answer("Введи сумму:")
    await state.set_state(AdminState.waiting_for_amount)

@dp.message(AdminState.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Нужны цифры.")
    data = await state.get_data()
    amount = int(message.text)
    final = amount if data['action'] == "add" else -amount
    await update_balance(bot.db_pool, data['user_id'], final)
    await message.answer(f"Готово! Баланс изменен на {final}.")
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Сумма для раздачи всем:")
    await state.set_state(AdminState.waiting_for_broadcast_amount)

@dp.message(AdminState.waiting_for_broadcast_amount)
async def broadcast_finish(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return
    amount = int(message.text)
    users = await get_all_users(bot.db_pool)
    for u in users:
        await update_balance(bot.db_pool, u['user_id'], amount)
    await message.answer(f"Выдано по {amount} монет {len(users)} юзерам.")
    await state.clear()

# --- СЛУШАТЕЛЬ ВСЕХ СООБЩЕНИЙ ---
@dp.message()
async def auto_register(message: types.Message):
    if message.from_user:
        await add_user(bot.db_pool, message.from_user.id, message.from_user.username or "NoName")

async def main():
    # Создаем пул соединений и сохраняем в бота, чтобы было удобно доставать
    pool = await create_pool()
    bot.db_pool = pool
    await init_db(pool)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
  
