import os
import logging
import time
import threading
import asyncio
import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
import sqlite3
from pysui import SuiClient, SuiConfig  # For Sui blockchain interaction

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Replace with your token
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0x7338ef163ee710923803cb0dd60b5b02cddc5fbafef417342e1bbf1fba20e702")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@yourtrendingchannel")
PORT = int(os.getenv("PORT", 8080))
SUI_RPC_URL = "https://fullnode.mainnet.sui.io"  # Sui mainnet RPC

# Conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TWITTER, INPUT_TELEGRAM, INPUT_MEDIA = range(8)

# Boost options with your specified durations and costs
BOOST_OPTIONS = {
    "1_hour": {"duration": 3600, "cost": 1.0},
    "6_hours": {"duration": 21600, "cost": 5.0},
    "12_hours": {"duration": 43200, "cost": 10.0},
    "24_hours": {"duration": 86400, "cost": 20.0},
}

# --- Database Functions ---

def init_db():
    """Initialize the SQLite database."""
    with sqlite3.connect("bot_settings.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                token_address TEXT NOT NULL,
                min_buy_usd REAL NOT NULL,
                emoji TEXT NOT NULL,
                website TEXT,
                twitter TEXT,
                telegram TEXT,
                media_file_id TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                usd_value REAL NOT NULL,
                buyer TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS boosts (
                token_address TEXT PRIMARY KEY,
                expiration_timestamp INTEGER NOT NULL
            )
        """)
        conn.commit()
    logger.info("Database initialized")

def get_db():
    """Get a database connection."""
    return sqlite3.connect("bot_settings.db")

def save_group_settings(group_id, settings):
    """Save group settings to the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO groups (group_id, token_address, min_buy_usd, emoji, website, twitter, telegram, media_file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (group_id, settings["token_address"], settings["min_buy_usd"], settings["emoji"],
              settings.get("website"), settings.get("twitter"), settings.get("telegram"), settings.get("media_file_id")))
        conn.commit()
    logger.info(f"Settings saved for group {group_id}")

def log_buy(token_address, timestamp, usd_value, buyer):
    """Log a buy transaction."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO buys (token_address, timestamp, usd_value, buyer)
            VALUES (?, ?, ?, ?)
        """, (token_address, timestamp, usd_value, buyer))
        conn.commit()

def save_boost(token_address, expiration):
    """Save a boost expiration."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp)
            VALUES (?, ?)
        """, (token_address, expiration))
        conn.commit()

# --- HTTP Server for Render.com ---

async def health_check(request):
    """Respond to Render.com health checks."""
    return web.Response(text="OK")

async def run_server():
    """Run the HTTP server for Render.com."""
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"HTTP server started on port {PORT}")
    await asyncio.Event().wait()

def start_http_server():
    """Start the HTTP server in a separate thread."""
    asyncio.run(run_server())

# --- Utility Functions ---

def shorten_address(address):
    """Shorten a blockchain address."""
    if len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"

def format_alert(buy, settings):
    """Format a buy alert message."""
    message = (
        f"{settings['emoji']} Buy Alert\n"
        f"Amount: ${buy['usd_value']:.2f}\n"
        f"Buyer: {shorten_address(buy['buyer'])}\n"
        f"Token: {shorten_address(buy['token_address'])}\n"
    )
    if settings.get("website"):
        message += f"Website: {settings['website']}\n"
    if settings.get("twitter"):
        message += f"Twitter: {settings['twitter']}\n"
    if settings.get("telegram"):
        message += f"Telegram: {settings['telegram']}\n"
    return message

# --- Sui Payment Verification ---

async def verify_payment(txn_hash, expected_amount):
    """Verify a Sui payment transaction using pysui."""
    try:
        sui_client = SuiClient(config=SuiConfig.from_config_file(None, SUI_RPC_URL))
        txn_response = await sui_client.get_transaction_block(txn_hash)
        if not txn_response:
            logger.error(f"No transaction found for hash: {txn_hash}")
            return False
        balance_changes = txn_response.effects.balance_changes
        for change in balance_changes:
            owner = change.owner.get("AddressOwner")
            amount = int(change.amount)
            if owner == BOOST_RECEIVER and amount >= expected_amount * 10**9:  # SUI uses 9 decimals
                logger.info(f"Payment verified: {txn_hash}")
                return True
        logger.info(f"Payment conditions not met for: {txn_hash}")
        return False
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
        return False

# --- Live Buy Tracking with Sui RPC ---

def fetch_recent_buys(token_address, min_usd, last_timestamp):
    """Fetch recent buys from the Sui blockchain using RPC."""
    try:
        url = f"{SUI_RPC_URL}/transactions"
        params = {
            "filter": {"token": token_address},
            "after_timestamp": last_timestamp
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        transactions = response.json().get("transactions", [])
        
        # Placeholder USD conversion (replace with real price feed)
        buys = []
        for tx in transactions:
            usd_value = float(tx.get("amount", 0)) * 0.1  # Placeholder
            if usd_value >= min_usd and tx.get("timestamp", 0) > last_timestamp:
                buys.append({
                    "usd_value": usd_value,
                    "buyer": tx.get("sender", "unknown"),
                    "token_address": token_address,
                    "timestamp": tx.get("timestamp")
                })
        return buys
    except Exception as e:
        logger.error(f"Failed to fetch buys: {e}")
        return []

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the /start command."""
    await update.message.reply_text(
        "Welcome! Configure the bot with these options:",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def start_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle configuration menu selections."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "set_token":
        await query.message.reply_text("Please enter the token address (e.g., 0x123...).")
        return INPUT_TOKEN
    elif data == "set_min_buy":
        await query.message.reply_text("Please enter the minimum buy amount in USD (e.g., 10.50).")
        return INPUT_MIN_BUY
    elif data == "set_emoji":
        await query.message.reply_text("Please enter the emoji for alerts (e.g., 🚀).")
        return INPUT_EMOJI
    elif data == "set_website":
        await query.message.reply_text("Please enter the website URL or type 'skip'.")
        return INPUT_WEBSITE
    elif data == "set_twitter":
        await query.message.reply_text("Please enter the Twitter handle or type 'skip'.")
        return INPUT_TWITTER
    elif data == "set_telegram":
        await query.message.reply_text("Please enter the Telegram link or type 'skip'.")
        return INPUT_TELEGRAM
    elif data == "set_media":
        await query.message.reply_text("Send a photo/GIF or type 'skip'.")
        return INPUT_MEDIA
    elif data == "finish_setup":
        settings = context.user_data.get("settings", {})
        required = ["token_address", "min_buy_usd", "emoji"]
        if all(k in settings for k in required):
            save_group_settings(update.effective_chat.id, settings)
            await query.message.reply_text("Setup complete!")
            return ConversationHandler.END
        else:
            await query.message.reply_text("Please set token, min buy, and emoji first.")
            return CHOOSING
    return CHOOSING

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the token address."""
    user_input = update.message.text.strip()
    if not user_input.startswith("0x") or len(user_input) < 10:
        await update.message.reply_text("Please enter a valid token address starting with '0x'.")
        return INPUT_TOKEN
    context.user_data.setdefault("settings", {})["token_address"] = user_input
    await update.message.reply_text("Token address saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the minimum buy amount."""
    try:
        min_buy = float(update.message.text.strip())
        if min_buy <= 0:
            raise ValueError("Amount must be positive")
        context.user_data.setdefault("settings", {})["min_buy_usd"] = min_buy
        await update.message.reply_text("Minimum buy saved. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    except ValueError:
        await update.message.reply_text("Please enter a valid positive number (e.g., 10.50).")
        return INPUT_MIN_BUY

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the emoji."""
    user_input = update.message.text.strip()
    if len(user_input) > 10:
        await update.message.reply_text("Please enter a single emoji or short sequence.")
        return INPUT_EMOJI
    context.user_data.setdefault("settings", {})["emoji"] = user_input
    await update.message.reply_text("Emoji saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the website URL or skip."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["website"] = text
    else:
        context.user_data.setdefault("settings", {})["website"] = None
    await update.message.reply_text("Website saved or skipped. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Twitter handle or skip."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["twitter"] = text
    else:
        context.user_data.setdefault("settings", {})["twitter"] = None
    await update.message.reply_text("Twitter saved or skipped. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Telegram link or skip."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["telegram"] = text
    else:
        context.user_data.setdefault("settings", {})["telegram"] = None
    await update.message.reply_text("Telegram saved or skipped. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the media file ID or skip."""
    if update.message.text and update.message.text.lower() == "skip":
        context.user_data.setdefault("settings", {})["media_file_id"] = None
        await update.message.reply_text("Media skipped. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data.setdefault("settings", {})["media_file_id"] = file_id
        await update.message.reply_text("Photo saved. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif update.message.animation:
        file_id = update.message.animation.file_id
        context.user_data.setdefault("settings", {})["media_file_id"] = file_id
        await update.message.reply_text("GIF saved. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    else:
        await update.message.reply_text("Please send a photo or GIF, or type 'skip' to skip.")
        return INPUT_MEDIA

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the configuration process."""
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END

async def boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display boost duration options with inline buttons."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /boost <token_address>")
        return
    token_address = context.args[0]
    context.user_data["boost_token"] = token_address
    keyboard = [
        [InlineKeyboardButton("1 Hour (1 SUI)", callback_data="boost_1_hour")],
        [InlineKeyboardButton("6 Hours (5 SUI)", callback_data="boost_6_hours")],
        [InlineKeyboardButton("12 Hours (10 SUI)", callback_data="boost_12_hours")],
        [InlineKeyboardButton("24 Hours (20 SUI)", callback_data="boost_24_hours")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Choose a boost duration for {shorten_address(token_address)}:",
        reply_markup=reply_markup
    )

async def boost_duration_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle boost duration selection."""
    query = update.callback_query
    await query.answer()
    duration_key = query.data.split("_")[1] + "_hours"
    if duration_key not in BOOST_OPTIONS:
        await query.message.reply_text("Invalid boost duration selected.")
        return
    duration_info = BOOST_OPTIONS[duration_key]
    cost = duration_info["cost"]
    token_address = context.user_data.get("boost_token")
    if not token_address:
        await query.message.reply_text("Please use /boost first to specify the token.")
        return
    context.user_data["boost_cost"] = cost
    context.user_data["boost_duration"] = duration_info["duration"]
    await query.message.reply_text(
        f"To boost {shorten_address(token_address)} for {duration_key.replace('_', ' ')}, send {cost} SUI to {BOOST_RECEIVER}.\n"
        "After sending, use /confirm <transaction_hash>."
    )

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm a boost payment."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /confirm <transaction_hash>")
        return
    txn_hash = context.args[0]
    expected_amount = context.user_data.get("boost_cost")
    if not expected_amount:
        await update.message.reply_text("Please select a boost duration first using /boost.")
        return
    if await verify_payment(txn_hash, expected_amount):
        token_address = context.user_data.get("boost_token")
        duration = context.user_data.get("boost_duration")
        if not token_address or not duration:
            await update.message.reply_text("Please use /boost first to specify the token and duration.")
            return
        expiration = int(time.time()) + duration
        save_boost(token_address, expiration)
        await update.message.reply_text(f"Boost confirmed for {shorten_address(token_address)} for {duration // 3600} hours!")
    else:
        await update.message.reply_text("Payment verification failed. Ensure you sent the correct amount.")

async def poll_buys(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Poll for buy transactions."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM groups")
        groups = cursor.fetchall()
    last_timestamp = context.bot_data.get("last_poll", int(time.time()) - 3600)
    for group in groups:
        settings = {
            "token_address": group[1],
            "min_buy_usd": group[2],
            "emoji": group[3],
            "website": group[4],
            "twitter": group[5],
            "telegram": group[6],
            "media_file_id": group[7]
        }
        buys = fetch_recent_buys(settings["token_address"], settings["min_buy_usd"], last_timestamp)
        for buy in buys:
            message = format_alert(buy, settings)
            if settings["media_file_id"]:
                await context.bot.send_photo(group[0], settings["media_file_id"], caption=message)
                await context.bot.send_photo(TRENDING_CHANNEL, settings["media_file_id"], caption=message)
            else:
                await context.bot.send_message(group[0], message)
                await context.bot.send_message(TRENDING_CHANNEL, message)
    context.bot_data["last_poll"] = int(time.time())

# --- Utility Functions ---

def get_menu_keyboard():
    """Generate the configuration menu keyboard."""
    keyboard = [
        [InlineKeyboardButton("Set Token Address", callback_data="set_token")],
        [InlineKeyboardButton("Set Minimum Buy", callback_data="set_min_buy")],
        [InlineKeyboardButton("Set Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Set Website", callback_data="set_website")],
        [InlineKeyboardButton("Set Twitter", callback_data="set_twitter")],
        [InlineKeyboardButton("Set Telegram", callback_data="set_telegram")],
        [InlineKeyboardButton("Set Media (Optional)", callback_data="set_media")],
        [InlineKeyboardButton("Finish Setup", callback_data="finish_setup")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Main Function ---

def main():
    """Set up and run the bot."""
    init_db()
    logger.info("Database initialized")

    # Start HTTP server for Render.com
    threading.Thread(target=start_http_server, daemon=True).start()

    # Initialize bot application
    application = Application.builder().token(BOT_TOKEN).build()

    # Set up conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [CallbackQueryHandler(start_config)],
            INPUT_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            INPUT_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_buy)],
            INPUT_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_emoji)],
            INPUT_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_website)],
            INPUT_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_twitter)],
            INPUT_TELEGRAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_telegram)],
            INPUT_MEDIA: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.TEXT & ~filters.COMMAND, receive_media)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost))
    application.add_handler(CommandHandler("confirm", confirm_boost))
    application.add_handler(CallbackQueryHandler(boost_duration_selected, pattern="^boost_"))

    # Schedule jobs
    application.job_queue.run_repeating(poll_buys, interval=60, first=0)

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
