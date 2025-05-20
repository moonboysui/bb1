import os
import time
import logging
import urllib.parse
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters, CallbackContext
import requests
from database import init_db, get_db
from utils import shorten_address, format_alert
from sui import verify_payment

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOOST_RECEIVER = os.getenv('BOOST_RECEIVER', '0xYourSUIWalletAddress')
TRENDING_CHANNEL = os.getenv('TRENDING_CHANNEL', '@moonbagstrending')

# Initialize database
init_db()

# Logging setup (fixed typo)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA, CONFIRM = range(9)

# Menu keyboard for user configuration
def get_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Track Token", callback_data='track_token')],
        [InlineKeyboardButton("📉 Set Minimum Buy Size", callback_data='set_min_buy')],
        [InlineKeyboardButton("🎯 Choose Emoji Style", callback_data='choose_emoji')],
        [InlineKeyboardButton("🌐 Add Website", callback_data='add_website')],
        [InlineKeyboardButton("💬 Add Telegram Link", callback_data='add_telegram')],
        [InlineKeyboardButton("❌ Add X (Twitter) Link", callback_data='add_twitter')],
        [InlineKeyboardButton("📷 Upload Media", callback_data='upload_media')],
        [InlineKeyboardButton("✅ Finish Setup", callback_data='finish_setup')],
    ])

# Start command (handles group and private chat)
async def start(update: Update, context: CallbackContext) -> None:
    if update.message.chat.type in ['group', 'supergroup']:
        group_id = update.message.chat.id
        button = InlineKeyboardButton("➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}?start=group{group_id}")
        await update.message.reply_text(
            "Thanks for inviting me! Please continue setup in private chat.",
            reply_markup=InlineKeyboardMarkup([[button]])
        )
    else:
        param = context.args[0] if context.args else None
        if param and param.startswith('group'):
            group_id = int(param[5:])
            context.user_data['group_id'] = group_id
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
                group = cursor.fetchone()
            if group:
                settings_text = (
                    f"Current settings for group {group_id}:\n"
                    f"Token: {group['token_address']}\n"
                    f"Min Buy: ${group['min_buy_usd']}\n"
                    f"Emoji: {group['emoji']}\n"
                    f"Website: {group['website'] or 'N/A'}\n"
                    f"Telegram: {group['telegram_link'] or 'N/A'}\n"
                    f"Twitter: {group['twitter_link'] or 'N/A'}\n"
                    f"Media: {'Set' if group['media_file_id'] else 'Not Set'}"
                )
                button = InlineKeyboardButton("✏️ Edit Settings", callback_data='edit_settings')
                await update.message.reply_text(settings_text, reply_markup=InlineKeyboardMarkup([[button]]))
            else:
                await update.message.reply_text(
                    "Let’s configure the bot for your group. Use the buttons below.",
                    reply_markup=get_menu_keyboard()
                )
            return CHOOSING
        else:
            await update.message.reply_text("Please start the configuration from your group using /start.")

# Configuration handlers (menu selection)
async def start_config(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    option = query.data
    prompts = {
        'track_token': "Please paste the token address to track (e.g., 0xabc...::module::SYMBOL).",
        'set_min_buy': "Enter the minimum buy size in USD (e.g., 10).",
        'choose_emoji': "Send a single emoji to use for buy alerts.",
        'add_website': "Enter your website URL (or 'skip' to omit).",
        'add_telegram': "Enter your Telegram link (or 'skip' to omit).",
        'add_twitter': "Enter your Twitter URL (or 'skip' to omit).",
        'upload_media': "Upload an image or GIF (or send 'skip' to omit).",
    }
    state_map = {
        'track_token': INPUT_TOKEN,
        'set_min_buy': INPUT_MIN_BUY,
        'choose_emoji': INPUT_EMOJI,
        'add_website': INPUT_WEBSITE,
        'add_telegram': INPUT_TELEGRAM,
        'add_twitter': INPUT_TWITTER,
        'upload_media': INPUT_MEDIA,
    }
    if option == 'finish_setup':
        await finish_setup(update, context)
        return ConversationHandler.END
    if option == 'edit_settings':
        await query.edit_message_text("Select an option to edit.", reply_markup=get_menu_keyboard())
        return CHOOSING
    await query.edit_message_text(prompts[option])
    return state_map[option]

# Generic input handler
async def receive_input(update: Update, context: CallbackContext, key: str, state: int, validate=None) -> int:
    text = update.message.text.strip()
    if validate and not validate(text):
        await update.message.reply_text("Invalid input. Try again.")
        return state
    if text.lower() == 'skip' and key != 'token_address':
        context.user_data[key] = None
    else:
        context.user_data[key] = text
    await update.message.reply_text(f"{key.replace('_', ' ').title()} set.", reply_markup=get_menu_keyboard())
    return CHOOSING

# Input handlers for specific fields
async def receive_token(update: Update, context: CallbackContext) -> int:
    def is_valid_sui_address(addr):
        # Validate Sui address format (e.g., 0xabc...::module::SYMBOL)
        return addr.startswith('0x') and '::' in addr
    return await receive_input(update, context, 'token_address', INPUT_TOKEN, is_valid_sui_address)

async def receive_min_buy(update: Update, context: CallbackContext) -> int:
    def is_number(s):
        try:
            float(s)
            return True
        except ValueError:
            return False
    return await receive_input(update, context, 'min_buy_usd', INPUT_MIN_BUY, is_number)

async def receive_emoji(update: Update, context: CallbackContext) -> int:
    def is_single_emoji(s):
        return len(s) == 1 and s.isprintable()  # Simplified
    return await receive_input(update, context, 'emoji', INPUT_EMOJI, is_single_emoji)

async def receive_website(update: Update, context: CallbackContext) -> int:
    return await receive_input(update, context, 'website', INPUT_WEBSITE)

async def receive_telegram(update: Update, context: CallbackContext) -> int:
    return await receive_input(update, context, 'telegram_link', INPUT_TELEGRAM)

async def receive_twitter(update: Update, context: CallbackContext) -> int:
    return await receive_input(update, context, 'twitter_link', INPUT_TWITTER)

async def receive_media(update: Update, context: CallbackContext) -> int:
    if update.message.text and update.message.text.lower() == 'skip':
        context.user_data['media_file_id'] = None
        await update.message.reply_text("Media skipped.", reply_markup=get_menu_keyboard())
        return CHOOSING
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.animation:
        file_id = update.message.animation.file_id
    else:
        await update.message.reply_text("Please upload an image or GIF, or send 'skip'.")
        return INPUT_MEDIA
    context.user_data['media_file_id'] = file_id
    await update.message.reply_text("Media set.", reply_markup=get_menu_keyboard())
    return CHOOSING

# Finish setup and save to database
async def finish_setup(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    if 'token_address' not in context.user_data:
        await query.edit_message_text("Token address is required. Please set it first.", reply_markup=get_menu_keyboard())
        return CHOOSING
    group_id = context.user_data['group_id']
    data = {
        'token_address': context.user_data.get('token_address'),
        'min_buy_usd': float(context.user_data.get('min_buy_usd', 5.0)),
        'emoji': context.user_data.get('emoji', '🔥'),
        'website': context.user_data.get('website'),
        'telegram_link': context.user_data.get('telegram_link'),
        'twitter_link': context.user_data.get('twitter_link'),
        'media_file_id': context.user_data.get('media_file_id'),
    }
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO groups (group_id, token_address, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (group_id, data['token_address'], data['min_buy_usd'], data['emoji'], data['website'], data['telegram_link'], data['twitter_link'], data['media_file_id'])
        )
    summary = (
        f"Setup complete for group {group_id}:\n"
        f"Token: {data['token_address']}\n"
        f"Min Buy: ${data['min_buy_usd']}\n"
        f"Emoji: {data['emoji']}\n"
        f"Website: {data['website'] or 'N/A'}\n"
        f"Telegram: {data['telegram_link'] or 'N/A'}\n"
        f"Twitter: {data['twitter_link'] or 'N/A'}\n"
        f"Media: {'Set' if data['media_file_id'] else 'Not Set'}"
    )
    await query.edit_message_text(summary)
    context.user_data.clear()
    return ConversationHandler.END

# Cancel setup
async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Setup cancelled.", reply_markup=get_menu_keyboard())
    return CHOOSING

# Fetch recent trades using Raiden X API
def fetch_recent_buys(since: int) -> list:
    # Get all configured token addresses from the database
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT token_address FROM groups")
        token_addresses = [row['token_address'] for row in cursor.fetchall()]
    
    trades = []
    base_url = "https://api-public.raidenx.io/sui/defi/txs/token"
    for token_address in token_addresses:
        # URL-encode the token address (e.g., 0xabc...::module::SYMBOL -> 0xabc...%3A%3Amodule%3A%3ASYMBOL)
        encoded_address = urllib.parse.quote(token_address, safe='')
        url = f"{base_url}?address={encoded_address}&txType=ALL&sortType=desc"
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                txs = response.json().get("data", [])
                for tx in txs:
                    # Assume timestamp is in seconds; adjust if API uses milliseconds
                    tx_time = tx.get("timestamp", 0)
                    if tx_time <= since:
                        continue
                    # Construct trade data (adjust based on actual API response structure)
                    trade = {
                        "token_address": token_address,
                        "sui_amount": float(tx.get("amount", 0)) / 1_000_000_000,  # SUI decimals
                        "usd_value": float(tx.get("amount", 0)) / 1_000_000_000 * fetch_current_price(token_address),
                        "tokens_purchased": float(tx.get("amount", 0)),
                        "buyer": tx.get("sender", ""),
                        "price": fetch_current_price(token_address),
                        "market_cap": 0,  # Updated in fetch_token_stats
                        "liquidity": 0,   # Updated in fetch_token_stats
                        "token_symbol": tx.get("symbol", "UNKNOWN")  # Adjust if API provides symbol
                    }
                    trades.append(trade)
            else:
                logger.error(f"Raiden X API error for {token_address}: {response.status_code}")
        except requests.RequestException as e:
            logger.error(f"Error fetching trades for {token_address}: {e}")
        time.sleep(0.6)  # 100 requests/minute = ~0.6s delay
    return trades

# Fetch current token price using Raiden X API
def fetch_current_price(token_address: str) -> float:
    base_url = "https://api-public.raidenx.io/sui/defi/price"
    encoded_address = urllib.parse.quote(token_address, safe='')
    url = f"{base_url}?address={encoded_address}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return float(response.json().get("price", 0.0))
        return 0.0
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Error fetching price for {token_address}: {e}")
        return 0.0

# Fetch token stats (market cap, liquidity) using Raiden X API
def fetch_token_stats(token_address: str) -> dict:
    base_url = "https://api-public.raidenx.io/sui/defi/v3/token/market-data"
    encoded_address = urllib.parse.quote(token_address, safe='')
    url = f"{base_url}?address={encoded_address}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", {})
            return {
                "symbol": data.get("symbol", "UNKNOWN"),
                "market_cap": float(data.get("marketCap", 0)),
                "liquidity": float(data.get("liquidity", 0))
            }
        return {"symbol": "UNKNOWN", "market_cap": 0, "liquidity": 0}
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Error fetching stats for {token_address}: {e}")
        return {"symbol": "UNKNOWN", "market_cap": 0, "liquidity": 0}

# Poll for new buys and process them
async def poll_buys(context: CallbackContext) -> None:
    last_check = context.bot_data.get('last_check', 0)
    now = int(datetime.utcnow().timestamp())
    buys = fetch_recent_buys(last_check)
    for buy in buys:
        await process_buy(context, buy)
    context.bot_data['last_check'] = now

# Process each buy and send alerts
async def process_buy(context: CallbackContext, buy: dict) -> None:
    token_address = buy['token_address']
    now = int(datetime.utcnow().timestamp())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT group_id, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id FROM groups WHERE token_address = ?",
            (token_address,)
        )
        groups = cursor.fetchall()
        for group in groups:
            if buy['usd_value'] >= group['min_buy_usd']:
                message, reply_markup, media_file_id = format_alert(
                    {**buy, 'website': group['website'], 'telegram_link': group['telegram_link'], 'twitter_link': group['twitter_link']},
                    group['emoji'], buy['token_symbol'], group['media_file_id']
                )
                if media_file_id:
                    await context.bot.send_photo(group['group_id'], media_file_id, caption=message, reply_markup=reply_markup, parse_mode='Markdown')
                else:
                    await context.bot.send_message(group['group_id'], message, reply_markup=reply_markup, parse_mode='Markdown')
        cursor.execute("SELECT * FROM boosts WHERE token_address = ? AND expiration_timestamp > ?", (token_address, now))
        boosted = cursor.fetchone() is not None
        if buy['usd_value'] >= 200 or boosted:
            message, reply_markup, _ = format_alert(buy, '🔥', buy['token_symbol'], include_links=False)
            await context.bot.send_message(TRENDING_CHANNEL, message, reply_markup=reply_markup, parse_mode='Markdown')
        cursor.execute(
            "INSERT INTO buys (token_address, timestamp, usd_value) VALUES (?, ?, ?)",
            (token_address, now, buy['usd_value'])
        )

# Generate leaderboard for trending tokens
async def generate_leaderboard(context: CallbackContext) -> None:
    now = datetime.utcnow()
    thirty_min_ago = now - timedelta(minutes=30)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT token_address FROM groups")
        tokens = [row['token_address'] for row in cursor.fetchall()]
        for token in tokens:
            price = fetch_current_price(token)
            cursor.execute(
                "INSERT INTO price_snapshots (token_address, timestamp, price) VALUES (?, ?, ?)",
                (token, int(now.timestamp()), price)
            )
        cursor.execute("SELECT token_address FROM boosts WHERE expiration_timestamp > ?", (int(now.timestamp()),))
        boosted_tokens = set(row['token_address'] for row in cursor.fetchall())
        volumes = {}
        price_changes = {}
        for token in tokens:
            cursor.execute(
                "SELECT SUM(usd_value) FROM buys WHERE token_address = ? AND timestamp > ?",
                (token, int(thirty_min_ago.timestamp()))
            )
            volumes[token] = cursor.fetchone()[0] or 0
            cursor.execute(
                "SELECT price FROM price_snapshots WHERE token_address = ? ORDER BY timestamp DESC LIMIT 2",
                (token,)
            )
            snapshots = cursor.fetchall()
            if len(snapshots) == 2:
                current, previous = snapshots[0]['price'], snapshots[1]['price']
                price_changes[token] = (current - previous) / previous * 100 if previous else 0
            else:
                price_changes[token] = None
        boosted = sorted([(t, volumes[t]) for t in tokens if t in boosted_tokens], key=lambda x: x[1], reverse=True)
        others = sorted([(t, volumes[t]) for t in tokens if t not in boosted_tokens], key=lambda x: x[1], reverse=True)
        top_10 = (boosted + others)[:10]
        message = "🌟 Top 10 Trending Tokens 🌟\n\n"
        for i, (token, volume) in enumerate(top_10, 1):
            stats = fetch_token_stats(token)
            change = price_changes.get(token)
            change_str = f"{'📈' if change > 0 else '📉'} {abs(change):.2f}%" if change is not None else "N/A"
            message += (
                f"{i}️⃣ {stats['symbol']} ({shorten_address(token)})\n"
                f"💰 Market Cap: ${stats['market_cap']:.2f}\n"
                f"{change_str}\n\n"
            )
        msg = await context.bot.send_message(TRENDING_CHANNEL, message)
        await context.bot.pin_chat_message(TRENDING_CHANNEL, msg.message_id, disable_notification=True)

# Boost system
async def boost(update: Update, context: CallbackContext) -> None:
    pricing = "4h: 15 SUI\n8h: 20 SUI\n12h: 25 SUI\n24h: 40 SUI\n48h: 60 SUI\n72h: 80 SUI\n1week: 100 SUI"
    await update.message.reply_text(
        f"To boost your token, send SUI to: `{BOOST_RECEIVER}`\n\n"
        f"Pricing:\n{pricing}\n\n"
        f"After sending, use: /confirm <txn_hash> <duration>",
        parse_mode='Markdown'
    )

async def confirm_boost(update: Update, context: CallbackContext) -> None:
    try:
        txn_hash, duration_str = context.args
        duration_map = {'4h': (15, 4*3600), '8h': (20, 8*3600), '12h': (25, 12*3600), '24h': (40, 24*3600),
                        '48h': (60, 48*3600), '72h': (80, 72*3600), '1week': (100, 7*86400)}
        if duration_str not in duration_map:
            await update.message.reply_text("Invalid duration. Options: 4h, 8h, 12h, 24h, 48h, 72h, 1week")
            return
        expected_amount, seconds = duration_map[duration_str]
        group_id = context.user_data.get('group_id')
        if not group_id:
            await update.message.reply_text("Please configure a group first to select a token to boost.")
            return
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token_address FROM groups WHERE group_id = ?", (group_id,))
            token = cursor.fetchone()
            if not token:
                await update.message.reply_text("No token configured for this group.")
                return
            token_address = token['token_address']
        if verify_payment(txn_hash, expected_amount, BOOST_RECEIVER):
            expiration = int((datetime.utcnow() + timedelta(seconds=seconds)).timestamp())
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp) VALUES (?, ?)",
                    (token_address, expiration)
                )
            await update.message.reply_text(f"Boost activated for {token_address} for {duration_str}!")
        else:
            await update.message.reply_text("Payment verification failed. Check the transaction hash.")
    except Exception as e:
        logger.error(f"Boost confirmation error: {e}")
        await update.message.reply_text("Error processing your request. Format: /confirm <txn_hash> <duration>")

# Main setup
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), CallbackQueryHandler(start_config)],
        states={
            CHOOSING: [CallbackQueryHandler(start_config)],
            INPUT_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            INPUT_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_buy)],
            INPUT_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_emoji)],
            INPUT_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_website)],
            INPUT_TELEGRAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_telegram)],
            INPUT_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_twitter)],
            INPUT_MEDIA: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.TEXT & ~filters.COMMAND, receive_media)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=True  # Fix per_message warning
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost))
    application.add_handler(CommandHandler("confirm", confirm_boost))

    # Schedule jobs
    application.job_queue.run_repeating(poll_buys, interval=10, first=0)
    application.job_queue.run_repeating(generate_leaderboard, interval=1800, first=1800)

    application.run_polling()

if __name__ == '__main__':
    main()
