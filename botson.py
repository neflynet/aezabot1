import asyncio
import logging
import os
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import LabeledPrice, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
import aiosqlite
from aiocryptopay import AioCryptoPay, Networks
from aiocryptopay.const import Asset

# ================= НАСТРОЙКИ =================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001987654321"))
PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID", "-1004272303448"))
PRIVATE_PRICE_STARS = int(os.getenv("PRIVATE_PRICE_STARS", "800"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "8882474847"))
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")

# 📸 ФОТО
PHOTOS = {
    "welcome": "photos/welcome.jpg",
    "private": "photos/private.jpg",
    "referral": "photos/referral.jpg",
    "success": "photos/success.jpg",
    "progress": "photos/progress.jpg",
}

# Рефералка
TIKTOK_COMMENT = "sonya_dasha_bot лучшие девочки"
SCREENSHOTS_REQUIRED = 10
REFERRAL_REWARDS = {
    5: {"discount": 0.10, "text": "🎁 Скидка 10% на приват"},
    10: {"discount": 0.20, "text": "🔥 Скидка 20% на приват"},
    30: {"discount": 0.0, "text": "💎 Личное сообщение от нас"},
}

DB_PATH = "club_bot.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ================= КРИПТО-КЛИЕНТ =================
crypto_client = None
if CRYPTO_BOT_TOKEN:
    crypto_client = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)
    logger.info("✅ CryptoBot подключён")

# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                ref_code TEXT UNIQUE,
                screenshots_sent INTEGER DEFAULT 0,
                screenshots_verified INTEGER DEFAULT 0,
                referral_links_sent INTEGER DEFAULT 0,
                discount REAL DEFAULT 0.0,
                sub_until TEXT,
                last_screenshot_time TEXT
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                currency TEXT,
                status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

async def create_user(user_id: int, username: str, ref_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, ref_code) VALUES (?, ?, ?)",
            (user_id, username, ref_code)
        )
        await db.commit()

async def add_screenshot(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET screenshots_sent = screenshots_sent + 1, last_screenshot_time = ? WHERE user_id = ?",
            (datetime.utcnow().isoformat(), user_id)
        )
        await db.commit()
        res = await db.execute("SELECT screenshots_sent FROM users WHERE user_id = ?", (user_id,))
        row = await res.fetchone()
        return row and row[0] >= SCREENSHOTS_REQUIRED

async def add_referral_invite(referrer_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET referral_links_sent = referral_links_sent + 1 WHERE user_id = ?", (referrer_id,))
        res = await db.execute("SELECT referral_links_sent FROM users WHERE user_id = ?", (referrer_id,))
        row = await res.fetchone()
        invites = row[0] if row else 0
        new_discount = 0.0
        for threshold, reward in sorted(REFERRAL_REWARDS.items()):
            if invites >= threshold:
                new_discount = reward["discount"]
        await db.execute("UPDATE users SET discount = ? WHERE user_id = ?", (new_discount, referrer_id))
        await db.commit()
        return new_discount, invites

async def activate_subscription(user_id: int, days: int = 30):
    until = (datetime.utcnow() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (until, user_id))
        await db.commit()

# ================= УТИЛИТЫ =================
def gen_ref_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def calc_price(base_price: int, discount: float) -> int:
    return max(0, int(base_price * (1 - discount)))

def get_photo(key: str):
    path = PHOTOS.get(key)
    if not path:
        return None
    if path.startswith("http"):
        return path
    if Path(path).exists():
        return FSInputFile(path)
    return None

# ================= БОТ =================
def create_bot():
    if PROXY_URL and PROXY_URL.strip():
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(PROXY_URL)
        session = AiohttpSession(connector=connector)
        logger.info(f"🔗 Запуск с прокси: {PROXY_URL}")
    else:
        session = AiohttpSession()
        logger.info("🔗 Прямое подключение к Telegram")
    return Bot(token=BOT_TOKEN, session=session)

bot = create_bot()
dp = Dispatcher()

# ================= ТЕКСТЫ =================
WELCOME_TEXT = """🔥 Привет, красавчик! 💋

Ты попал в закрытый клуб Сони и Даши 🌹

✨ Эксклюзивные фото
🎬 Личные видео
💬 Голосовые
🎁 Розыгрыши

👇 Выбирай:"""

PRIVATE_TEXT = """💎 Приватный клуб

🔥 Личные видео
💋 Голосовые
📸 Фото для своих

💰 Цена: {price}⭐{discount_text}"""

REFERRAL_INSTRUCTION = f"""🎁 Хочешь скидку?

1. Открой ТикТок
2. Оставь 10 комментов: «{TIKTOK_COMMENT}»
3. Пришли скриншоты сюда

📊 Бонусы:
• 5 друзей → -10%
• 10 друзей → -20%
• 30 друзей → личное сообщение 💋"""

REFERRAL_UNLOCKED = """🎉 Ты справился!

🔗 Твоя ссылка:
`{link}`

Скидывай друзьям и получай бонусы 📈"""

PAYMENT_SUCCESS = """💋 Оплата прошла! 🔥

Ты в клубе. Заходи, там ждёт эксклюзив 😘"""

# ================= ХЕНДЛЕРЫ =================

@dp.chat_join_request()
async def auto_approve(request: types.ChatJoinRequest):
    if request.chat.id == CHANNEL_ID:
        try:
            await bot.approve_chat_join_request(chat_id=request.chat.id, user_id=request.from_user.id)
            logger.info(f"✅ Заявка одобрена: {request.from_user.id}")
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        ref_code = gen_ref_code()
        await create_user(user_id, message.from_user.username, ref_code)
        user = await get_user(user_id)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔥 Мой канал", url="https://t.me/+pKvsVnMkruZhYjcy"))
    kb.row(InlineKeyboardButton(text="💎 Приват", callback_data="buy_private"))
    kb.row(InlineKeyboardButton(text="🎁 Скидка", callback_data="ref_start"))
    
    photo = get_photo("welcome")
    if photo:
        await message.answer_photo(photo=photo, caption=WELCOME_TEXT, reply_markup=kb.as_markup())
    else:
        await message.answer(WELCOME_TEXT, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "buy_private")
async def buy_private(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    discount = user["discount"]
    final_price = calc_price(PRIVATE_PRICE_STARS, discount)
    discount_text = f"\n🎁 Скидка: {int(discount*100)}%" if discount > 0 else ""
    
    text = PRIVATE_TEXT.format(price=final_price, discount_text=discount_text)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"💳 Оплатить {final_price}⭐", callback_data="pay_stars"))
    if crypto_client:
        kb.row(
            InlineKeyboardButton(text="💰 USDT (TRC20)", callback_data="pay_crypto_usdt"),
            InlineKeyboardButton(text="💎 TON", callback_data="pay_crypto_ton")
        )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    photo = get_photo("private")
    if photo:
        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb.as_markup())
    else:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "pay_stars")
async def pay_stars(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    price = calc_price(PRIVATE_PRICE_STARS, user["discount"] if user else 0.0)
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="💎 Приват",
        description="Доступ на 30 дней",
        payload="private_access",
        currency="XTR",
        prices=[LabeledPrice(label="Доступ", amount=price)]
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(callback: types.CallbackQuery):
    if not crypto_client:
        await callback.answer("❌ Крипто не настроена", show_alert=True)
        return
    
    crypto_type = callback.data.split("_")[-1]  # usdt или ton
    user = await get_user(callback.from_user.id)
    stars_price = calc_price(PRIVATE_PRICE_STARS, user["discount"] if user else 0.0)
    
    # Конвертация: 1⭐ ≈ 0.02 USDT или 0.01 TON
    if crypto_type == "usdt":
        amount = round(stars_price * 0.02, 2)
        asset = Asset.USDT
        title = "💰 Оплата USDT"
    else:  # ton
        amount = round(stars_price * 0.01, 2)
        asset = Asset.TON
        title = "💎 Оплата TON"
    
    invoice = await crypto_client.create_invoice(
        asset=asset,
        amount=amount,
        description="Доступ в приват на 30 дней",
        payload=f"user_{callback.from_user.id}_{crypto_type}"
    )
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Оплатить", url=invoice.bot_invoice_url))
    kb.row(InlineKeyboardButton(text="🔄 Проверить", callback_data=f"check_crypto_{invoice.invoice_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_private"))
    
    await callback.message.edit_text(
        f"{title}\nСумма: {amount} {crypto_type.upper()}\nСтатус: {invoice.status}",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("check_crypto_"))
async def check_crypto_payment(callback: types.CallbackQuery):
    if not crypto_client:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    invoice_id = callback.data.split("_")[-1]
    
    try:
        # Получаем список инвойсов и ищем нужный
        invoices = await crypto_client.get_invoices(status="active")
        invoice = None
        for inv in invoices:
            if str(inv.invoice_id) == invoice_id:
                invoice = inv
                break
        
        if not invoice:
            await callback.answer("⏳ Инвойс не найден", show_alert=True)
            return
        
        if invoice.status == "paid":
            user_id = callback.from_user.id
            await activate_subscription(user_id, days=30)
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO payments (user_id, amount, currency, status) VALUES (?, ?, ?, 'crypto_success')",
                    (user_id, invoice.amount, invoice.asset)
                )
                await db.commit()
            
            try:
                link = await bot.create_chat_invite_link(
                    chat_id=PRIVATE_CHANNEL_ID,
                    member_limit=1,
                    name=f"pay_{user_id}"
                )
                photo = get_photo("success")
                text = PAYMENT_SUCCESS + f"\n\n🔗 Ссылка для входа:\n{link.invite_link}"
                if photo:
                    await callback.message.answer_photo(photo=photo, caption=text)
                else:
                    await callback.message.edit_text(text)
            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                await callback.message.edit_text("✅ Оплата подтверждена! Напиши админу для доступа 💌")
        else:
            await callback.answer("⏳ Оплата ещё не поступила", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Ошибка проверки: {e}")
        await callback.answer("⚠️ Ошибка проверки", show_alert=True)

@dp.pre_checkout_query()
async def pre_checkout(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def on_payment(message: types.Message):
    user_id = message.from_user.id
    await activate_subscription(user_id, days=30)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id, amount, currency, status) VALUES (?, ?, ?, 'success')",
            (user_id, message.successful_payment.total_amount, "XTR")
        )
        await db.commit()
    
    try:
        link = await bot.create_chat_invite_link(
            chat_id=PRIVATE_CHANNEL_ID,
            member_limit=1,
            name=f"pay_{user_id}"
        )
        photo = get_photo("success")
        text = PAYMENT_SUCCESS + f"\n\n🔗 Твоя ссылка:\n{link.invite_link}"
        if photo:
            await message.answer_photo(photo=photo, caption=text)
        else:
            await message.answer(text)
        logger.info(f"💰 Оплата от {user_id}: {message.successful_payment.total_amount}⭐")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await message.answer("⚠️ Оплата прошла, но ошибка. Напиши админу 💌")

@dp.callback_query(F.data == "start")
async def go_start(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        ref_code = gen_ref_code()
        await create_user(callback.from_user.id, callback.from_user.username, ref_code)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔥 Мой канал", url="https://t.me/+pKvsVnMkruZhYjcy"))
    kb.row(InlineKeyboardButton(text="💎 Приват", callback_data="buy_private"))
    kb.row(InlineKeyboardButton(text="🎁 Скидка", callback_data="ref_start"))
    
    photo = get_photo("welcome")
    if photo:
        await callback.message.edit_text(WELCOME_TEXT, reply_markup=kb.as_markup())
    else:
        await callback.message.edit_text(WELCOME_TEXT, reply_markup=kb.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "ref_start")
async def ref_start(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    screenshots = user["screenshots_verified"]
    if screenshots >= SCREENSHOTS_REQUIRED:
        my_bot = await bot.get_me()
        # ✅ ПРАВИЛЬНАЯ РЕФЕРАЛЬНАЯ ССЫЛКА
        ref_link = f"https://t.me/{my_bot.username}?start={user['ref_code']}"
        text = REFERRAL_UNLOCKED.format(link=ref_link)
        
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📋 Копировать", switch_inline_query=user['ref_code']))
        kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
        
        photo = get_photo("referral")
        if photo:
            await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb.as_markup())
        else:
            await callback.message.edit_text(text, reply_markup=kb.as_markup())
        return
    
    progress = min(screenshots / SCREENSHOTS_REQUIRED, 1.0)
    bar = "█" * int(progress * 10) + "░" * (10 - int(progress * 10))
    text = f"{REFERRAL_INSTRUCTION}\n\n📊 Прогресс: [{bar}] {screenshots}/{SCREENSHOTS_REQUIRED}"
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    photo = get_photo("progress")
    if photo:
        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb.as_markup())
    else:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())

@dp.message(F.photo | F.document)
async def handle_screenshot(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Сначала /start")
        return
    
    await add_screenshot(user_id)
    screenshots = user["screenshots_sent"] + 1
    
    if screenshots >= SCREENSHOTS_REQUIRED and user["screenshots_verified"] < SCREENSHOTS_REQUIRED:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET screenshots_verified = screenshots_sent WHERE user_id = ?", (user_id,))
            await db.commit()
        
        my_bot = await bot.get_me()
        ref_link = f"https://t.me/{my_bot.username}?start={user['ref_code']}"
        text = f"🎉 Всё проверила! 💋\n\nТвоя ссылка:\n`{ref_link}`"
        
        photo = get_photo("success")
        if photo:
            await message.answer_photo(photo=photo, caption=text)
        else:
            await message.answer(text)
    else:
        bar = "█" * int((screenshots/SCREENSHOTS_REQUIRED)*10) + "░" * (10 - int((screenshots/SCREENSHOTS_REQUIRED)*10))
        rem = SCREENSHOTS_REQUIRED - screenshots
        text = f"💋 Принято!\n\n📊 [{bar}] {screenshots}/{SCREENSHOTS_REQUIRED}\n\nОсталось {rem}!"
        
        photo = get_photo("progress")
        if photo:
            await message.answer_photo(photo=photo, caption=text)
        else:
            await message.answer(text)

@dp.message(Command("add_invite"))
async def admin_add_invite(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        target_id = int(message.text.split()[1])
        disc, inv = await add_referral_invite(target_id)
        await message.answer(f"✅ {target_id}\n📊 {inv} друзей\n💰 Скидка: {int(disc*100)}%")
    except Exception as e:
        await message.answer(f"❌ {e}")

# ================= ЗАПУСК =================
async def main():
    await init_db()
    if not PHOTOS["welcome"].startswith("http"):
        Path("photos").mkdir(exist_ok=True)
    logger.info("🤖 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
