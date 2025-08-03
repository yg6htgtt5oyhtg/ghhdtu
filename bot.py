import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import requests
from telethon import TelegramClient, events, Button
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonFake,
    InputReportReasonOther,
)

# ==== НАСТРОЙКИ ====
SEMD = '440506:AAk6tpDL2MmNdUDA2vHHNxjjnIRohXp5z2n'
BOT_TOKEN = '7568569882:AAEyfdy7c8Q1U3XsoU2sYNvmT1bjXHDuJRU'
API_ID = 27633578
API_HASH = '98a87b483bf61af442c21ab1fd8c06e1'
CRYPTOBOT_API_TOKEN = SEMD  # Same as bot token for CryptoBot API
SESSIONS_DIR = 'sessions'
OWNER_ID = 6971683917  # Your user_id
SUBS_FILE = 'subs.json'
LOG_ID = 6971683917
CRYPTOBOT_API_URL = 'https://pay.crypt.bot/api'

session_clients = []
user_states = {}  # user_id: 'awaiting_username'
subscriptions = {}  # user_id: expire_iso
pending_payments = {}  # user_id: {'invoice_id': str, 'amount': float, 'duration': int}

# === ЗАГРУЖАЕМ ВСЕ .session ===
def load_sessions():
    for file in os.listdir(SESSIONS_DIR):
        if file.endswith('.session'):
            path = os.path.join(SESSIONS_DIR, file)
            client = TelegramClient(path, API_ID, API_HASH)
            session_clients.append(client)

# === РАБОТА С ПОДПИСКАМИ ===
def load_subs():
    global subscriptions
    if os.path.exists(SUBS_FILE):
        with open(SUBS_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content:
                subscriptions = json.loads(content)
            else:
                subscriptions = {}
    else:
        subscriptions = {}

def save_subs():
    with open(SUBS_FILE, 'w', encoding='utf-8') as f:
        json.dump(subscriptions, f, indent=2, ensure_ascii=False)

def is_subscribed(user_id):
    if str(user_id) not in subscriptions:
        return False
    expire_time = datetime.fromisoformat(subscriptions[str(user_id)])
    if expire_time.tzinfo is None:
        expire_time = expire_time.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expire_time

def set_subscription(user_id, minutes):
    expire_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    subscriptions[str(user_id)] = expire_time.isoformat()
    save_subs()

# === CRYPTOBOT API FUNCTIONS ===
def create_invoice(amount, currency='USD', description='Subscription', asset='USDT'):
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_API_TOKEN}
    payload = {
        'amount': amount,
        'currency': currency,
        'description': description,
        'currency_type': 'crypto',  # Explicitly set to crypto
        'asset': asset  # Required for crypto invoices
    }
    response = requests.post(f'{CRYPTOBOT_API_URL}/createInvoice', json=payload, headers=headers)
    if response.status_code == 200:
        return response.json()['result']
    else:
        print(f"Error creating invoice: {response.text}")
        return None

def check_invoice_status(invoice_id):
    headers = {'Crypto-Pay-API-Token': CRYPTOBOT_API_TOKEN}
    response = requests.get(f'{CRYPTOBOT_API_URL}/getInvoices?invoice_ids={invoice_id}', headers=headers)
    if response.status_code == 200:
        invoice = response.json()['result']['items'][0]
        return invoice['status']
    return None

# === ИНИЦИАЛИЗАЦИЯ БОТА ===
bot = TelegramClient('bot_session15', API_ID, API_HASH)

# === ОБРАБОТКА КОМАНД ===
@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.respond(
        "Привет! Нажми кнопку, чтобы заказать вареники или посмотреть цены:",
        buttons=[
            [Button.inline("🥟 Заказать вареники", b"vareniki")],
            [Button.inline("👤 Мой профиль", b"profile")],
            [Button.inline("📜 Цены", b"price")]
        ]
    )

@bot.on(events.CallbackQuery(data=b'price'))
async def price(event):
    await event.respond(
        "🥟 Ассортимент",
        buttons=[
            [Button.inline("🥟 1 день - 1$", b"day")],
            [Button.inline("🥟 7 дней - 4$", b"week")],
            [Button.inline("🥟 30 дней - 10$", b"month")]
        ]
    )

@bot.on(events.CallbackQuery(data=b'profile'))
async def profile(event):
    user_id = event.sender_id
    sub = is_subscribed(user_id)
    text = f"<b>👤 Профиль пользователя\n\n🆔 ID: {user_id}\n</b>"
    if sub:
        expire = subscriptions[str(user_id)]
        dt = datetime.fromisoformat(expire)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        left = dt - datetime.now(timezone.utc)
        minutes_left = int(left.total_seconds() // 60)
        text += f"<b>✨ Статус подписки: ✅ Подписка активна до: <code>{dt}</code> UTC\n⏱ Осталось: <code>{minutes_left}</code> мин.</b>"
    else:
        text += "<b>✨ Статус подписки: ❌ Подписка неактивна</b>"
    await event.respond(text, parse_mode='html')

@bot.on(events.NewMessage(pattern=r'/sub'))
async def give_sub(event):
    if event.sender_id != OWNER_ID:
        return await event.respond("<b>❌ У тебя нет прав для этой команды.</b>", parse_mode='html')

    args = event.raw_text.split()
    if len(args) != 3 or not args[2].isdigit():
        return await event.respond("⚠ Используй: /sub @username 30")

    username = args[1].lstrip('@')
    minutes = int(args[2])

    try:
        user = await bot.get_entity(username)
        set_subscription(user.id, minutes)
        await event.respond(f"✅ Подписка выдана для @{username} на {minutes} минут.", parse_mode='html')
    except Exception as e:
        await event.respond(f"⚠ Ошибка: {str(e)}", parse_mode='html')

# === ОБРАБОТКА ПЛАТЕЖЕЙ ===
@bot.on(events.CallbackQuery(data=b'day'))
async def handle_day_subscription(event):
    user_id = event.sender_id
    amount = 1.0  # $1 for 1 day
    duration = 1440  # 1 day in minutes
    invoice = create_invoice(amount, description='1 Day Subscription')
    if invoice:
        pending_payments[user_id] = {'invoice_id': invoice['invoice_id'], 'amount': amount, 'duration': duration}
        await event.respond(
            f"<b>💳 Оплатите подписку на 1 день (1$):</b>\n"
            f"👉 <a href='{invoice['pay_url']}'>Оплатить через CryptoBot</a>\n\n",
            "<b>📱 Проверить оплату - /check_payment.</b>",
            parse_mode='html',
            link_preview=False
        )
    else:
        await event.respond("<b>⚠ Ошибка при создании счета. Попробуйте позже.</b>", parse_mode='html')

@bot.on(events.CallbackQuery(data=b'week'))
async def handle_week_subscription(event):
    user_id = event.sender_id
    amount = 4.0  # $4 for 7 days
    duration = 10080  # 7 days in minutes
    invoice = create_invoice(amount, description='7 Days Subscription')
    if invoice:
        pending_payments[user_id] = {'invoice_id': invoice['invoice_id'], 'amount': amount, 'duration': duration}
        await event.respond(
            f"<b>💳 Оплатите подписку на 7 дней (4$):</b>\n"
            f"👉 <a href='{invoice['pay_url']}'>Оплатить через CryptoBot</a>\n\n",
            "<b>📱 Проверить оплату - /check_payment.</b>",
            parse_mode='html',
            link_preview=False
        )
    else:
        await event.respond("<b>⚠ Ошибка при создании счета. Попробуйте позже.</b>", parse_mode='html')

@bot.on(events.CallbackQuery(data=b'month'))
async def handle_month_subscription(event):
    user_id = event.sender_id
    amount = 10.0  # $10 for 30 days
    duration = 43200  # 30 days in minutes
    invoice = create_invoice(amount, description='30 Days Subscription')
    if invoice:
        pending_payments[user_id] = {'invoice_id': invoice['invoice_id'], 'amount': amount, 'duration': duration}
        await event.respond(
            f"<b>💳 Оплатите подписку на 30 дней (10$):</b>\n"
            f"👉 <a href='{invoice['pay_url']}'>Оплатить через CryptoBot</a>\n\n",
            "<b>📱 Проверить оплату - /check_payment.</b>",
            parse_mode='html',
            link_preview=False
        )
    else:
        await event.respond("<b>⚠ Ошибка при создании счета. Попробуйте позже.</b>", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'/check_payment'))
async def check_payment(event):
    user_id = event.sender_id
    if user_id not in pending_payments:
        await event.respond("<b>❌ Нет активных платежей.</b>", parse_mode='html')
        return

    payment = pending_payments[user_id]
    invoice_id = payment['invoice_id']
    status = check_invoice_status(invoice_id)

    if status == 'paid':
        set_subscription(user_id, payment['duration'])
        await event.respond(
            f"<b>✅ Оплата подтверждена!</b>\n"
            f"Подписка на {payment['duration']//1440} дней активирована.",
            parse_mode='html'
        )
        await bot.send_message(LOG_ID, f"👤 Пользователь {user_id} оплатил подписку на {payment['duration']//1440} дней")
        del pending_payments[user_id]
    elif status == 'active':
        await event.respond("<b>⏳ Оплата еще не подтверждена. Проверьте позже.</b>", parse_mode='html')
    else:
        await event.respond("<b>❌ Оплата не удалась или счет истек.</b>", parse_mode='html')
        del pending_payments[user_id]

# === ОБРАБОТКА ИНЛАЙН-КНОПКИ ===
@bot.on(events.CallbackQuery(data=b'vareniki'))
async def handle_order(event):
    user_id = event.sender_id

    if not is_subscribed(user_id):
        await event.respond("<b>❌ У тебя нет активной подписки.</b>", parse_mode='html')
        return

    user_states[user_id] = 'awaiting_username'
    await event.respond(
        "<b>🔗 Отлично! Введите имя пользователя, кому отправить вареники:</b>\n\n<code>Пример формата: username</code>",
        parse_mode='html'
    )

@bot.on(events.NewMessage)
async def message_handler(event):
    user_id = event.sender_id
    if user_states.get(user_id) != 'awaiting_username':
        return

    username = event.raw_text.strip().lstrip('@')
    user_states.pop(user_id, None)

    if not is_subscribed(user_id):
        return await event.respond("<b>❌ Подписка истекла.</b>", parse_mode='html')

    await event.respond(
        f"<b>⏳ Отправляю вареники к </b><code>@{username}</code><b>...</b>",
        parse_mode='html'
    )

    success, fail = 0, 0
    for client in session_clients:
        try:
            user = await client.get_entity(username)

            # Жалоба: Спам
            await client(ReportPeerRequest(
                peer=user,
                reason=InputReportReasonSpam(),
                message="Спам активность"
            ))
            await asyncio.sleep(1)

            # Жалоба: Фейк
            await client(ReportPeerRequest(
                peer=user,
                reason=InputReportReasonFake(),
                message="Поддельный аккаунт"
            ))
            await asyncio.sleep(1)

            # Жалоба: Нарушение правил
            await client(ReportPeerRequest(
                peer=user,
                reason=InputReportReasonOther(),
                message="Нарушение правил Telegram. Проверьте пожалуйста."
            ))
            await asyncio.sleep(1)

            success += 1
        except Exception as e:
            fail += 1
            print(f"Ошибка при отправке жалоб с {client.session.filename}: {e}")

    await event.respond(
        f"<b>✅ Вареники отправлены</b>\n\n👤 Получатель: <code>@{username}</code>\n📦 Вареников: <code>{success}</code>\n⚠ Ошибок: <code>{fail}</code>",
        parse_mode='html'
    )
    await event.client.send_message(LOG_ID, f"👤 Пользователь {user_id} отправил жалобы на @{username}")

# === ЗАПУСК ===
async def main():
    print("[*] Загружаем сессии...")
    load_sessions()
    load_subs()
    for client in session_clients:
        await client.connect()
    print(f"[+] Загружено аккаунтов: {len(session_clients)}")

    await bot.start(bot_token=BOT_TOKEN)
    print("[+] Бот запущен")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())