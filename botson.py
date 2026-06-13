import asyncio
import logging
import os
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import LabeledPrice, InlineKeyboardButton, FSInputFile, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
import aiosqlite
from aiocryptopay import AioCryptoPay, Networks

# ================= НАСТРОЙКИ =================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PROXY_URL = os.getenv("PROXY_URL")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003863080862"))
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

# ================= FSM =================
class PromoState(StatesGroup):
    waiting_for_code = State()

class BroadcastState(StatesGroup):
    waiting_message = State()

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
                last_screenshot_time TEXT,
                active_promo TEXT DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                currency TEXT,
                status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                discount REAL,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            );
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()

async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
            return [row[0] for row in rows]

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

async def apply_promo_to_user(user_id: int, promo_code: str, discount: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET active_promo = ?, discount = ? WHERE user_id = ?", (promo_code, discount, user_id))
        await db.commit()

async def increment_promo_use(promo_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?", (promo_code,))
        await db.commit()

async def check_promo_code(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promo_codes WHERE code = ?", (code.upper(),)) as cur:
            promo = await cur.fetchone()
            if promo and promo["is_active"] == 1 and promo["current_uses"] < promo["max_uses"]:
                return promo
            return None

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

def stars_to_rubles(stars: int) -> int:
    return round(stars * 1.3)

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

Здесь нет скучных постов. Только:
✨ Эксклюзивные фото, которых нет в открытом доступе
🎬 Личные видео-приветы для своих
💬 Голосовые, в которых мы шепчем только для тебя
🎁 Розыгрыши и сюрпризы для самых активных

👇 Выбирай, что хочешь прямо сейчас:"""

PRIVATE_TEXT = """💋 Привет, красавчик! Рада, что ты здесь 😏

В привате у нас совсем другая атмосфера:
🔥 Видео и фото, которые стыдно показывать в обычном канале
💬 Голосовые, где мы шепчем лично для тебя
🎁 И иногда даже выполняем твои маленькие фантазии...

💰 Обычная цена: 800⭐ (≈ 1040₽)
{promo_text}
{discount_text}

⚠️ Не переживай, оплата полностью безопасна — это официальная кнопка Telegram.

Нажимай «Оплатить» и сразу заходи к нам 🤫"""

STARS_GUIDE_TEXT = """📖 Не знаешь, как оплатить звёздами? Без паники! 😘

⭐ Звёзды — это просто способ оплаты внутри Telegram, как карта в App Store.

💳 Как оплатить за 1 минуту:
1️⃣ Нажми «Оплатить» ниже 👇
2️⃣ В окне нажми «Купить звёзды»
3️⃣ Выбери количество и оплати картой
4️⃣ Всё! Доступ откроется сразу 🔥

🆘 Не получается? Напиши нам 💌"""

REFERRAL_INSTRUCTION = f"""🎁 Хочешь скидку? Всё просто!

📱 Открой ТикТок
✍️ Оставь 10 комментариев: «{TIKTOK_COMMENT}»
📸 Пришли скриншоты в этот бот

Как увидим 10/10 — получишь реферальную ссылку и скидки:
• 5 друзей → скидка 10%
• 10 друзей → скидка 20%
• 30 друзей → личное сообщение 💋"""

REFERRAL_UNLOCKED = """🎉 Поздравляю! 💋

Ты отправил 10 скриншотов — мы всё проверили 😏

🔗 Твоя реферальная ссылка:
`{link}`

Скидывай её друзьям и получай бонусы:
• 5 приглашённых → скидка 10%
• 10 приглашённых → скидка 20%
• 30 приглашённых → личное сообщение 💋"""

PAYMENT_SUCCESS = """✅ Оплата прошла, красавчик! 🔥

Ты теперь в нашем приватном клубе.
Заходи — там ждёт кое-что особенное 😘"""

HELP_TEXT = """📋 Команды:
/start — главное меню
/stars_guide — как оплатить звёздами
/help — список команд

💎 В меню:
• Приватный клуб
• Программа скидок

Пиши нам 💌"""

# ================= ХЕНДЛЕРЫ =================

@dp.chat_join_request()
async def auto_approve(request: types.ChatJoinRequest):
    if request.chat.id == CHANNEL_ID:
        try:
            await bot.approve_chat_join_request(
                chat_id=request.chat.id,
                user_id=request.from_user.id
            )
            logger.info(f"✅ Заявка одобрена: {request.from_user.id}")
            
            try:
                user = await get_user(request.from_user.id)
                if not user:
                    ref_code = gen_ref_code()
                    await create_user(request.from_user.id, request.from_user.username, ref_code)
                    user = await get_user(request.from_user.id)

                discount = user["discount"] if user else 0.0
                price = calc_price(PRIVATE_PRICE_STARS, discount)
                rubles = stars_to_rubles(price)
                discount_text = f"\n🎁 Твоя скидка: {int(discount*100)}%" if discount > 0 else ""

                kb = InlineKeyboardBuilder()
                kb.row(InlineKeyboardButton(text="💎 Войти в Приватный клуб", callback_data="buy_private"))
                kb.row(InlineKeyboardButton(text="🎁 Получить скидку", callback_data="ref_start"))

                text = (
                    f"🔥 {request.from_user.first_name}, привет! 💋\n\n"
                    f"Мы одобрили твою заявку — добро пожаловать 😈\n\n"
                    f"💰 Доступ: {price}⭐ (≈ {rubles} руб.){discount_text}\n\n"
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
            logger.error(f"❌ Ошибка одобрения: {e}")

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
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

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(HELP_TEXT)

@dp.message(Command("stars_guide"))
async def cmd_stars_guide(message: types.Message):
    await message.answer(STARS_GUIDE_TEXT)

# ================= ПРОМОКОДЫ =================
@dp.message(Command("add_promo"))
async def cmd_add_promo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer("❌ Использование: /add_promo КОД СКИДКА ЛИМИТ\nПример: /add_promo HOT50 0.5 10")
        return
    
    code = parts[1].upper()
    try:
        discount = float(parts[2])
        max_uses = int(parts[3])
    except ValueError:
        await message.answer("❌ Скидка — число (0.5 = 50%), лимит — целое число.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO promo_codes (code, discount, max_uses) VALUES (?, ?, ?)",
                (code, discount, max_uses)
            )
            await db.commit()
            await message.answer(f"✅ Промокод {code} создан!\nСкидка: {int(discount*100)}%\nЛимит: {max_uses}")
        except aiosqlite.IntegrityError:
            await message.answer(f"❌ Промокод {code} уже существует.")

@dp.callback_query(F.data == "enter_promo")
async def ask_promo(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(PromoState.waiting_for_code)
    await callback.message.answer("🎟️ Введи промокод (одним сообщением):")
    await callback.answer()

@dp.message(PromoState.waiting_for_code)
async def process_promo(message: types.Message, state: FSMContext):
    await state.clear()
    code = message.text.strip().upper()
    
    promo = await check_promo_code(code)
    if not promo:
        await message.answer("❌ Промокод недействителен или достиг лимита.")
        return
    
    await apply_promo_to_user(message.from_user.id, code, promo["discount"])
    
    await message.answer(f"🎉 Промокод {code} активирован!\nСкидка {int(promo['discount']*100)}% применена!\n\nПеренаправляю к оплате 👇")
    
    callback_query = types.CallbackQuery(
        id="0",
        from_user=message.from_user,
        chat_instance="0",
        data="buy_private"
    )
    await buy_private(callback_query)

# ================= РАССЫЛКА =================
@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BroadcastState.waiting_message)
    await message.answer("📢 Напиши сообщение для рассылки. Для отмены: /cancel")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current is not None:
        await state.clear()
        await message.answer("✅ Отменено")

@dp.message(BroadcastState.waiting_message)
async def do_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    user_ids = await get_all_user_ids()
    sent = 0
    failed = 0

    await message.answer(f"⏳ Рассылка на {len(user_ids)} пользователей...")

    for uid in user_ids:
        try:
            await message.copy_to(uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"✅ Готово!\n📨 Отправлено: {sent}\n❌ Не доставлено: {failed}")

# ================= ОПЛАТА =================
@dp.callback_query(F.data == "buy_private")
async def buy_private(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    discount = user["discount"]
    final_price = calc_price(PRIVATE_PRICE_STARS, discount)
    rubles = stars_to_rubles(final_price)
    
    promo_text = ""
    discount_text = ""
    
    if user["active_promo"]:
        promo_text = f"🔥 ПРОМОКОД: {user['active_promo']}\nСкидка {int(discount*100)}% применена!"
    elif discount > 0:
        discount_text = f"🎁 Твоя скидка: {int(discount*100)}%"

    text = PRIVATE_TEXT.format(promo_text=promo_text, discount_text=discount_text)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"⭐ Оплатить {final_price} Stars (≈{rubles}₽)", callback_data="pay_stars"))
    if crypto_client:
        kb.row(
            InlineKeyboardButton(text="💰 USDT (TRC20)", callback_data="pay_crypto_usdt"),
            InlineKeyboardButton(text="💎 TON", callback_data="pay_crypto_ton")
        )
    
    if not user["active_promo"]:
        kb.row(InlineKeyboardButton(text="🎟️ У меня есть промокод", callback_data="enter_promo"))
        
    kb.row(InlineKeyboardButton(text="📖 Как купить Stars?", callback_data="stars_guide_inline"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "stars_guide_inline")
async def stars_guide_inline(callback: types.CallbackQuery):
    await callback.message.answer(STARS_GUIDE_TEXT)
    await callback.answer()

@dp.callback_query(F.data == "pay_stars")
async def pay_stars(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    discount = user["discount"] if user else 0.0
    price = calc_price(PRIVATE_PRICE_STARS, discount)
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="💎 Приватный клуб",
        description="Личный контент на 30 дней",
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
        logger.error(f"❌ Ошибка: {e}")
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
            await callback.answer("❌ Инвойс не найден", show_alert=True)
            return
        
        if invoice.status == "paid":
            user_id = callback.from_user.id
            user = await get_user(user_id)
            
            await activate_subscription(user_id, days=30)
            
            if user and user["active_promo"]:
                await increment_promo_use(user["active_promo"])
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO payments (user_id, amount, currency, status) VALUES (?, ?, ?, 'crypto_success')",
                    (user_id, invoice.amount, invoice.asset)
                )
                await db.commit()
            
            try:
                link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHANNEL_ID, member_limit=1, name=f"pay_{user_id}")
                await callback.message.answer(PAYMENT_SUCCESS + f"\n\n🔗 Ссылка:\n{link.invite_link}")
            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                await callback.message.answer("✅ Оплата подтверждена! Напиши админу 💌")
        else:
            await callback.answer("⏳ Оплата ещё не поступила", show_alert=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await callback.answer("⚠️ Ошибка проверки", show_alert=True)

@dp.pre_checkout_query()
async def pre_checkout(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def on_payment(message: types.Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    await activate_subscription(user_id, days=30)
    
    if user and user["active_promo"]:
        await increment_promo_use(user["active_promo"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id, amount, currency, status) VALUES (?, ?, ?, 'success')",
            (user_id, message.successful_payment.total_amount, "XTR")
        )
        await db.commit()

    try:
        link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHANNEL_ID, member_limit=1, name=f"pay_{user_id}")
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
    text = f"{REFERRAL_INSTRUCTION}\n\n📊 Твой прогресс:\n[{bar}] {screenshots}/{SCREENSHOTS_REQUIRED}"
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))
    
    photo = get_photo("progress")
    if photo:
        await callback.message.answer_photo(photo=photo, caption=text, reply_markup=kb.as_markup())
    else:
        await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()

@dp.message(F.photo | F.document)
async def handle_screenshot(message: types.Message, state: FSMContext):
    current = await state.get_state()
    if current == BroadcastState.waiting_message:
        await do_broadcast(message, state)
        return
        
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("❌ Сначала нажми /start")
        return
    
    await add_screenshot(user_id)
    screenshots = user["screenshots_sent"] + 1
    
    if screenshots >= SCREENSHOTS_REQUIRED and user["screenshots_verified"] < SCREENSHOTS_REQUIRED:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET screenshots_verified = screenshots_sent WHERE user_id = ?", (user_id,))
            await db.commit()
        try:
            await bot.send_message(ADMIN_ID, f"🎯 Пользователь @{message.from_user.username} ({user_id}) выполнил задание!")
        except:
            pass
        my_bot = await bot.get_me()
        ref_link = f"https://t.me/{my_bot.username}?start={user['ref_code']}"
        text = f"🎉 Я всё проверила! 💋\n\nТы отправил 10/10 скриншотов 😏\n\n🔗 Твоя ссылка:\n`{ref_link}`"
        photo = get_photo("success")
        if photo:
            await message.answer_photo(photo=photo, caption=text, disable_web_page_preview=True)
        else:
            await message.answer(text, disable_web_page_preview=True)
    else:
        progress = min(screenshots / SCREENSHOTS_REQUIRED, 1.0)
        bar = "█" * int(progress * 10) + "░" * (10 - int(progress * 10))
        remaining = SCREENSHOTS_REQUIRED - screenshots
        text = f"💋 Скриншот принят! 😘\n\n📊 Прогресс:\n[{bar}] {screenshots}/{SCREENSHOTS_REQUIRED}\n\n"
        if remaining > 0:
            text += f"Осталось {remaining} скриншот{'а' if remaining in [2,3,4] else 'ов'}!"
        else:
            text += "✅ Ты справился!"
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
            await message.answer("❌ Укажи ID (число), а не @юзернейм")
            return
        target_id = int(target)
        new_discount, invites = await add_referral_invite(target_id)
        reward_text = REFERRAL_REWARDS.get(invites, {}).get("text", "🎁 Бонус обновлён")
        await message.answer(f"✅ Добавлено для {target_id}\n📊 Приглашений: {invites}\n💰 Скидка: {int(new_discount * 100)}%\n🎁 {reward_text}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ================= ЗАПУСК =================
async def main():
    await init_db()
    if not PHOTOS["welcome"].startswith("http"):
        Path("photos").mkdir(exist_ok=True)
    
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="stars_guide", description="📖 Как оплатить звёздами"),
        BotCommand(command="help", description="📋 Список команд"),
    ])

    logger.info("🤖 Бот запущен. Ожидаю красавчиков...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
