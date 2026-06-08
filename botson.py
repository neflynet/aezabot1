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

# ================= НАСТРОЙКИ =================
load_dotenv()

# ВАЖНО: внутри кавычек НЕТ пробелов!
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1001987654321"))
PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID", "-1004272303448"))
PRIVATE_PRICE_STARS = int(os.getenv("PRIVATE_PRICE_STARS", "800"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "8882474847"))
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")

PHOTOS = {
    "welcome": "https://files.catbox.moe/f44vzq.jpg",
    "private": "https://files.catbox.moe/azf68z.jpg",
    "referral": "https://files.catbox.moe/vi8745.jpg",
    "success": "https://files.catbox.moe/049tsn.jpg",
    "progress": "https://files.catbox.moe/tk798n.jpg",
}

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
        await db.execute("INSERT INTO users (user_id, username, ref_code) VALUES (?, ?, ?)", (user_id, username, ref_code))
        await db.commit()

async def add_screenshot(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET screenshots_sent = screenshots_sent + 1, last_screenshot_time = ? WHERE user_id = ?", (datetime.utcnow().isoformat(), user_id))
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

Ты попал в закрытый клуб двух самых горячих девчонок — Сони и Даши 🌹

Здесь нет скучных постов. Только:
✨ Эксклюзивные фото, которых нет в открытом доступе
🎬 Личные видео-приветы для своих
💬 Голосовые, в которых мы шепчем только для тебя
🎁 Розыгрыши и сюрпризы для самых активных

Мы не просто «модели». Мы — твои личные фантазии, которые стали реальностью 😏

👇 Выбирай, что хочешь прямо сейчас:"""

PRIVATE_TEXT = """💎 Приватный клуб Сони и Даши

Здесь то, чего ты не увидишь нигде:
🔥 Личные видео-приветы только для тебя
💋 Голосовые, в которых мы шепчем твоё имя
📸 Фото, которые мы делаем только для «своих»
🎁 Сюрпризы и розыгрыши для подписчиков

Это не просто контент. Это — наше внимание только к тебе 😏

💰 Цена для тебя: {price}⭐{discount_text}"""

REFERRAL_INSTRUCTION = f"""🎁 Хочешь скидку на приват? Всё просто!

Шаг 1: Открой ТикТок
✍️ Шаг 2: Оставь 10 комментариев под нашими видео:
   «{TIKTOK_COMMENT}»
📸 Шаг 3: Пришли скриншоты каждого комментария ПРЯМО В ЭТОТ БОТ

Как только мы увидим 10/10 скриншотов — ты получишь:
🔗 Свою реферальную ссылку
💰 Возможность приглашать друзей и получать скидки:

📊 Твои бонусы:
• 5 друзей по твоей ссылке → скидка 10% на приват
• 10 друзей → скидка 20% на приват  
• 30 друзей → мы напишем тебе ЛИЧНО 💋

Просто будь активным — и всё получится 😘"""

REFERRAL_UNLOCKED = """🎉 Поздравляю, ты справился! 💋

Ты отправил 10 скриншотов — мы всё проверили и видим твоё старание 😏

🔗 Твоя реферальная ссылка:
`{link}`

Просто скидывай её друзьям. Когда они зайдут в бота по твоей ссылке — ты начнёшь получать бонусы:

📈 Твои бонусы:
• 5 приглашённых → скидка 10% на приват
• 10 приглашённых → скидка 20% на приват
• 30 приглашённых → мы напишем тебе ЛИЧНО 💋

Просто делись — и получай удовольствие 😘"""

PAYMENT_SUCCESS = """✅ Оплата прошла, красавчик! 🔥

Ты теперь в нашем приватном клубе.
Заходи — там уже ждёт кое-что особенное только для тебя 😘

✨ Не забывай заглядывать — мы часто добавляем новое"""

# ================= ХЕНДЛЕРЫ =================

@dp.chat_join_request()
async def auto_approve(request: types.ChatJoinRequest):
    logger.info(f"📩 Получена заявка в чат {request.chat.id} от {request.from_user.id}")
    
    if request.chat.id == CHANNEL_ID:
        try:
            await bot.approve_chat_join_request(
                chat_id=request.chat.id,
                user_id=request.from_user.id
            )
            logger.info(f"✅ Заявка успешно одобрена для: {request.from_user.id}")
            
            try:
                user = await get_user(request.from_user.id)
                if not user:
                    ref_code = gen_ref_code()
                    await create_user(request.from_user.id, request.from_user.username, ref_code)
                    user = await get_user(request.from_user.id)

                discount = user["discount"] if user else 0.0
                price = calc_price(PRIVATE_PRICE_STARS, discount)
                discount_text = f"\n🎁 Твоя скидка: {int(discount*100)}%" if discount > 0 else ""

                kb = InlineKeyboardBuilder()
                kb.row(InlineKeyboardButton(text="💎 Войти в Приватный клуб", callback_data="buy_private"))
                kb.row(InlineKeyboardButton(text="🎁 Получить скидку", callback_data="ref_start"))

                text = (
                    f"🔥 {request.from_user.first_name}, привет! 💋\n\n"
                    f"Мы одобрили твою заявку в канал — добро пожаловать 😈\n\n"
                    f"Но то, что ты видишь там — это лишь верхушка айсберга 🌹\n\n"
                    f"В нашем Приватном клубе совсем другой уровень:\n"
                    f"🔞 Контент, которого нет в открытом канале\n"
                    f"💬 Голосовые и видео-приветы лично для тебя\n"
                    f"🎁 Сюрпризы и розыгрыши только для своих\n\n"
                    f"💰 Доступ: {price}⭐{discount_text}\n\n"
                    f"👇 Готов? Жми:"
                )

                photo = get_photo("welcome")
                if photo:
                    await bot.send_photo(chat_id=request.from_user.id, photo=photo, caption=text, reply_markup=kb.as_markup())
                else:
                    await bot.send_message(chat_id=request.from_user.id, text=text, reply_markup=kb.as_markup())
            except Exception as e:
                logger.warning(f"⚠️ Не удалось отправить ЛС: {e}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка одобрения заявки: {e}")
    else:
        logger.warning(f"⚠️ Заявка пришла в другой чат (ID: {request.chat.id}), игнорируем.")

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
    kb.row(InlineKeyboardButton(text="💎 Приватный клуб", callback_data="buy_private"))
    kb.row(InlineKeyboardButton(text="🎁 Получить скидку", callback_data="ref_start"))
    
    photo = get_photo("welcome")
    if photo:
        await message.answer_photo(photo=photo, caption=WELCOME_TEXT, reply_markup=kb.as_markup(), disable_web_page_preview=True)
    else:
        await message.answer(WELCOME_TEXT, reply_markup=kb.as_markup(), disable_web_page_preview=True)

@dp.callback_query(F.data == "buy_private")
async def buy_private(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка пользователя", show_alert=True)
        return
    
    discount = user["discount"]
    final_price = calc_price(PRIVATE_PRICE_STARS, discount)
    discount_text = f"\n🎁 Твоя персональная скидка: {int(discount*100)}%" if discount > 0 else ""
    
    text = PRIVATE_TEXT.format(price=final_price, discount_text=discount_text)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"💳 Оплатить {final_price}⭐", callback_data="pay_stars"))
    if crypto_client:
        kb.row(
            InlineKeyboardButton(text="💰 USDT (TRC20)", callback_data="pay_crypto_usdt"),
            InlineKeyboardButton(text="💎 TON", callback_data="pay_crypto_ton")
        )
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "pay_stars")
async def pay_stars(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    discount = user["discount"] if user else 0.0
    price = calc_price(PRIVATE_PRICE_STARS, discount)
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="💎 Приватный клуб",
        description="Личный контент от Сони и Даши на 30 дней",
        payload="private_access",
        currency="XTR",
        prices=[LabeledPrice(label="Доступ к привату", amount=price)],
        start_parameter="private_sub"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto(callback: types.CallbackQuery):
    if not crypto_client:
        await callback.answer("❌ Крипто не настроена", show_alert=True)
        return
    
    crypto_type = callback.data.split("_")[-1]
    user = await get_user(callback.from_user.id)
    stars_price = calc_price(PRIVATE_PRICE_STARS, user["discount"] if user else 0.0)
    
    if crypto_type == "usdt":
        amount = round(stars_price * 0.02, 2)
        asset = "USDT"
        title = "💰 Оплата USDT"
    else:
        amount = round(stars_price * 0.01, 2)
        asset = "TON"
        title = "💎 Оплата TON"
    
    try:
        invoice = await crypto_client.create_invoice(
            asset=asset,
            amount=amount,
            description="Доступ в приват на 30 дней",
            payload=f"user_{callback.from_user.id}_{crypto_type}"
        )
        
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="💳 Оплатить через CryptoBot", url=invoice.bot_invoice_url))
        kb.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_crypto_{invoice.invoice_id}"))
        kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="buy_private"))
        
        text = f"{title}\nСумма: {amount} {crypto_type.upper()}\nСтатус: {invoice.status}"
        
        await callback.message.answer(text, reply_markup=kb.as_markup())
        await callback.answer()
    except Exception as e:
        logger.error(f"❌ Ошибка создания инвойса: {e}")
        await callback.answer("⚠️ Ошибка оплаты", show_alert=True)

@dp.callback_query(F.data.startswith("check_crypto_"))
async def check_crypto_payment(callback: types.CallbackQuery):
    if not crypto_client:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    invoice_id = callback.data.split("_")[-1]
    
    try:
        invoices = await crypto_client.get_invoices(status="active")
        invoice = None
        for inv in invoices:
            if str(inv.invoice_id) == invoice_id:
                invoice = inv
                break
        
        if not invoice:
            await callback.answer("⚠️ Инвойс не найден", show_alert=True)
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
                text = PAYMENT_SUCCESS + f"\n\n🔗 Ссылка для входа:\n{link.invite_link}"
                await callback.message.answer(text)
            except Exception as e:
                logger.error(f"❌ Ошибка создания ссылки: {e}")
                await callback.message.answer("✅ Оплата подтверждена! Напиши админу для доступа 💌")
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
        await message.answer("⚠️ Оплата прошла, но произошла ошибка. Напиши админу 💌")

@dp.callback_query(F.data == "start")
async def go_start(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        ref_code = gen_ref_code()
        await create_user(callback.from_user.id, callback.from_user.username, ref_code)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🔥 Мой канал", url="https://t.me/+pKvsVnMkruZhYjcy"))
    kb.row(InlineKeyboardButton(text="💎 Приватный клуб", callback_data="buy_private"))
    kb.row(InlineKeyboardButton(text="🎁 Получить скидку", callback_data="ref_start"))
    
    await callback.message.answer(WELCOME_TEXT, reply_markup=kb.as_markup())
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
        ref_link = f"https://t.me/{my_bot.username}?start={user['ref_code']}"
        text = REFERRAL_UNLOCKED.format(link=ref_link)
        
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="📋 Скопировать", switch_inline_query=user['ref_code']))
        kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
        
        photo = get_photo("referral")
        if photo:
            await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb.as_markup(), disable_web_page_preview=True)
        else:
            await callback.message.answer(text, reply_markup=kb.as_markup())
        return
    
    progress = min(screenshots / SCREENSHOTS_REQUIRED, 1.0)
    bar = "█" * int(progress * 10) + "░" * (10 - int(progress * 10))
    text = f"{REFERRAL_INSTRUCTION}\n\n📊 Твой прогресс:\n[{bar}] {screenshots}/{SCREENSHOTS_REQUIRED} скриншотов"
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    photo = get_photo("progress")
    if photo:
        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb.as_markup())
    else:
        await callback.message.answer(text, reply_markup=kb.as_markup())

@dp.message(F.photo | F.document)
async def handle_screenshot(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Сначала нажми /start, красавчик 😘")
        return
    
    await add_screenshot(user_id)
    screenshots = user["screenshots_sent"] + 1
    
    if screenshots >= SCREENSHOTS_REQUIRED and user["screenshots_verified"] < SCREENSHOTS_REQUIRED:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET screenshots_verified = screenshots_sent WHERE user_id = ?", (user_id,))
            await db.commit()
        try:
            await bot.send_message(ADMIN_ID, f"🎯 Новый пользователь выполнил задание!\n👤 @{message.from_user.username} ({user_id})\n📸 Отправил {SCREENSHOTS_REQUIRED} скриншотов")
        except:
            pass
        my_bot = await bot.get_me()
        ref_link = f"https://t.me/{my_bot.username}?start={user['ref_code']}"
        text = f"🎉 Я всё проверила, красавчик! 💋\n\nТы отправил 10/10 скриншотов — я вижу твоё старание 😏\n\n🔗 Твоя реферальная ссылка:\n`{ref_link}`\n\nПросто скидывай её друзьям. Когда они зайдут по твоей ссылке — ты начнёшь получать бонусы:\n• 5 друзей → скидка 10%\n• 10 друзей → скидка 20%\n• 30 друзей → я напишу тебе ЛИЧНО 💋\n\nПросто делись — и получай удовольствие 😘"
        photo = get_photo("success")
        if photo:
            await message.answer_photo(photo=photo, caption=text, disable_web_page_preview=True)
        else:
            await message.answer(text, disable_web_page_preview=True)
    else:
        progress = min(screenshots / SCREENSHOTS_REQUIRED, 1.0)
        bar = "█" * int(progress * 10) + "░" * (10 - int(progress * 10))
        remaining = SCREENSHOTS_REQUIRED - screenshots
        text = f"💋 Скриншот принят, красавчик! 😘\n\n📊 Твой прогресс:\n[{bar}] {screenshots}/{SCREENSHOTS_REQUIRED}\n\n"
        if remaining > 0:
            text += f"Осталось всего {remaining} скриншот{'а' if remaining in [2,3,4] else 'ов'}!\nПродолжай в том же духе — и скоро получишь свою реферальную ссылку 🔥"
        else:
            text += "Ты справился! Мы проверим скриншоты и скоро напишем тебе 😘"
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
        target = message.text.split()[1]
        if target.startswith("@"):
            await message.answer("❌ Укажи ID пользователя (число), а не @юзернейм")
            return
        target_id = int(target)
        new_discount, invites = await add_referral_invite(target_id)
        reward_text = REFERRAL_REWARDS.get(invites, {}).get("text", "🎁 Бонус обновлён")
        await message.answer(f"✅ Добавлено приглашение для {target_id}\n📊 Всего приглашений: {invites}\n💰 Текущая скидка: {int(new_discount * 100)}%\n🎁 {reward_text}")
        try:
            await bot.send_message(target_id, f"🎉 Поздравляю! Ты получил бонус за приглашённого друга 😘\nТвоя скидка обновлена: {int(new_discount * 100)}%\n{reward_text if new_discount > 0 else ''}")
        except:
            pass
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nИспользование: /add_invite 123456789")

# ================= ЗАПУСК =================
async def main():
    await init_db()
    if not PHOTOS["welcome"].startswith("http"):
        Path("photos").mkdir(exist_ok=True)
    logger.info("🤖 Анонимный бот Сони и Даши запущен. Ожидаю красавчиков...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
