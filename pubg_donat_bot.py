"""
pubg_donat_bot.py
Simple Telegram donation (PUBG UC) bot (long-polling).
Configure with environment variables:
  - BOT_TOKEN: Telegram bot token
  - ADMIN_IDS: comma-separated admin telegram IDs, e.g. "123456789,987654321"
Notes:
  - This repository does NOT contain any real token. Set your token on the server.
  - Use a process manager (systemd, Docker, or Render background worker) to keep it running.
Dependencies: aiogram, aiosqlite
"""
import logging
import os
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.types import LabeledPrice
from aiogram.utils import executor

# Load configuration from environment
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x) for x in ADMIN_IDS_ENV.split(",") if x.strip().isdigit()]

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN environment variable. Set it and restart the bot.")

DB_PATH = os.getenv("DB_PATH", "orders.db")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")  # Optional: Telegram Payments provider token
CURRENCY = os.getenv("CURRENCY", "USD")  # Currency for Telegram Payments (if used)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# --- DB init ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                option TEXT,
                amount INTEGER,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT
            )
        \"\"\")
        await db.commit()

# --- Helper: add order ---
async def add_order(user_id, username, option, amount, status="pending", note=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (user_id, username, option, amount, status, note) VALUES (?,?,?,?,?,?)",
            (user_id, username, option, amount, status, note)
        )
        await db.commit()
        return cur.lastrowid

# --- Start ---
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    text = ("Assalomu alaykum! 🎮\\n"
            "PUBG donat qilish botiga xush kelibsiz.\\n\\n"
            "Buyurtma berish uchun quyidagilardan birini tanlang:")
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Donat qilish", "Buyurtmalarim")
    await message.answer(text, reply_markup=keyboard)

# --- Show options ---
@dp.message_handler(lambda m: m.text == "Donat qilish")
async def show_options(message: types.Message):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("100 UC — $1", callback_data="opt_100_100"),
           types.InlineKeyboardButton("500 UC — $4", callback_data="opt_500_400"))
    kb.add(types.InlineKeyboardButton("Qo'lda/karta orqali to'lash", callback_data="manual_pay"))
    await message.answer("Qaysi paketni xohlaysiz?\n(US Dollar ko'rsatilgan: misol uchun)", reply_markup=kb)

# --- Handle callbacks ---
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("opt_"))
async def process_option(call: types.CallbackQuery):
    await call.answer()
    parts = call.data.split("_")
    uc = parts[1]
    amount_cents = int(parts[2])
    amount = amount_cents
    user = call.from_user

    if PROVIDER_TOKEN:
        prices = [LabeledPrice(label=f"{uc} UC", amount=amount)]
        await bot.send_invoice(chat_id=user.id,
                               title=f"{uc} UC",
                               description=f"Buyurtma: {uc} UC",
                               payload=f"pay_{uc}_{user.id}",
                               provider_token=PROVIDER_TOKEN,
                               currency=CURRENCY,
                               prices=prices)
    else:
        order_id = await add_order(user.id, user.username or "", f"{uc} UC", amount, status="pending")
        await call.message.answer(
            f"Buyurtma qabul qilindi (id: {order_id}).\\n"
            "Iltimos, quyidagi ma'lumotlar bo'yicha to'lovni amalga oshiring:\\n"
            "- Bank karta: [SIZNING REKVIZIT]\\n"
            "- Muxbir: [SIZNING ISM]\\n\\n"
            "To'lovni amalga oshirganingizdan so'ng, to'lov kvitansiyasini yuboring yoki /paid {id} komandasini yuboring.".format(id=order_id)
        )
        for admin in ADMIN_IDS:
            try:
                await bot.send_message(admin, f"Yangi buyurtma #{order_id} by @{user.username}\\nPaket: {uc} UC\\nSumma: {amount}")
            except Exception:
                pass

# --- Manual pay command ---
@dp.message_handler(commands=["paid"])
async def cmd_paid(message: types.Message):
    args = message.get_args().strip()
    if not args.isdigit():
        await message.reply("Foydalanish: /paid <buyurtma_id>")
        return
    order_id = int(args)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, username, option, amount, status FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await message.reply("Buyurtma topilmadi.")
            return
        if row[5] != "pending":
            await message.reply(f"Buyurtma holati: {row[5]}. Agar muammo bo'lsa admin bilan bog'laning.")
            return
        await db.execute("UPDATE orders SET status=? WHERE id=?", ("paid_waiting", order_id))
        await db.commit()
    await message.reply("To'lov qabul qilindi. Admin tasdiqlaydi — bir oz kuting.")
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, f"Buyurtma #{order_id} uchun to'lov bildirildi. Tekshiring va /fulfill {order_id} bilan bajarilsin.")
        except Exception:
            pass

# --- Admin: fulfill order ---
@dp.message_handler(commands=["fulfill"])
async def cmd_fulfill(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Foydalanuvchi emas.")
        return
    args = message.get_args().strip()
    if not args.isdigit():
        await message.reply("Foydalanish: /fulfill <order_id>")
        return
    order_id = int(args)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, user_id, option, status FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await message.reply("Buyurtma topilmadi.")
            return
        if row[3] == "done":
            await message.reply("Buyurtma allaqachon bajarilgan.")
            return
        # TODO: Integrate with official provider API here
        await db.execute("UPDATE orders SET status=? WHERE id=?", ("done", order_id))
        await db.commit()
    await message.reply(f"Buyurtma #{order_id} bajarildi.")
    try:
        await bot.send_message(row[1], f"Sizning buyurtmangiz #{order_id} bajarildi — rahmat!")
    except Exception:
        pass

# --- View my orders ---
@dp.message_handler(lambda m: m.text == "Buyurtmalarim")
async def my_orders(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, option, amount, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,))
        rows = await cur.fetchall()
    if not rows:
        await message.reply("Sizda buyurtma yo'q.")
        return
    text = "Sizning buyurtmalaringiz:\\n\\n"
    for r in rows:
        text += f"#{r[0]} — {r[1]} — {r[2]} — {r[3]} — {r[4]}\\n"
    await message.reply(text)

# --- Payments pre-checkout (Telegram Payments) ---
@dp.pre_checkout_query_handler(lambda q: True)
async def pre_checkout(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def got_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    await add_order(message.from_user.id, message.from_user.username or "", payload, int(message.successful_payment.total_amount), status="done")
    await message.answer("To'lov qabul qilindi. Buyurtmangiz bajariladi. Rahmat!")
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, f"Yangi to'lov qabul qilindi by @{message.from_user.username}: {payload}")
        except Exception:
            pass

# --- Fallback handler ---
@dp.message_handler()
async def fallback(message: types.Message):
    await message.reply("Noma'lum buyruq. /start bilan boshlang yoki 'Donat qilish' tugmasini bosing.")

# --- main ---
if __name__ == "__main__":
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    executor.start_polling(dp, skip_updates=True)
