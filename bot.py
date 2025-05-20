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
from pysui.sui.sui_clients.sync_client import SuiClient
from pysui.sui.sui_config import SuiConfig

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

# Conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_MEDIA = range(5)

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
            INSERT OR REPLACE INTO groups (group_id, token_address, min_buy_usd, emoji, media_file_id)
            VALUES (?, ?, ?, ?, ?)
        """, (group_id, settings["token_address"], settings["min_buy_usd"], settings["emoji"], settings.get("media_file_id")))
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
        f"Token: {shorten_address(buy['token_address'])}"
    )
    return message

# --- Sui Payment Verification ---

def verify_payment(txn_hash, expected_amount):
    """Verify a Sui payment transaction using pysui."""
    try:
        # Initialize SuiClient with the mainnet RPC URL
        sui_client = SuiClient(config=SuiConfig.from_config_file(None, "https://rpc.mainnet.sui.io"))
        # Fetch transaction details (synchronous call for simplicity)
        txn_response = sui_client.get_transaction_block(txn_hash)
        if not txn_response:
            logger.error(f"No transaction found for hash: {txn_hash}")
            return False

        # Check balance changes
        for event in txn_response.events:
            if event.type == "coin::TransferObject" and event.parsed_json.get("recipient") == BOOST_RECEIVER:
                amount = int(event.parsed_json.get("amount", 0))
                if amount >= expected_amount * 10**9:  # SUI uses 9 decimals
                    logger.info(f"Payment verified: {txn_hash}")
                    return True
        logger.info(f"Payment conditions not met for: {txn_hash}")
        return False
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
        return False

# --- Simulated Raiden X API Polling ---

def fetch_recent_buys(token_address, min_usd, last_timestamp):
    """Simulate fetching recent buys (replace with real API if available)."""
    # This is a simulation since no real Raiden X API is provided
    try:
        # Simulated response
        simulated_buys = [
            {
                "token_address": token_address,
                "timestamp": int(time.time()),
                "usd_value": min_usd + 5.0,
                "buyer": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            }
        ] if time.time() % 300 > 150 else []  # Simulate buys every 5 minutes
        return [buy for buy in simulated_buys if buy["usd_value"] >= min_usd and buy["timestamp"] > last_timestamp]
    except Exception as e:
        logger.error(f"Failed to fetch buys: {e}")
        return []

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the /start command."""
    await update.message.reply_text(
        "Welcome! Configure the bot by setting the token address, minimum buy, emoji, and optional media.",
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
    elif data == "set_media":
        await query.message.reply_text("Please send a photo or GIF for alerts, or type 'skip' to skip.")
        return INPUT_MEDIA
    elif data == "finish_setup":
        settings = context.user_data.get("settings", {})
        if "token_address" not in settings or "min_buy_usd" not in settings or "emoji" not in settings:
            await query.message.reply_text("You must set the token address, minimum buy, and emoji first!")
            return CHOOSING
        group_id = update.effective_chat.id
        save_group_settings(group_id, settings)
        await query.message.reply_text("Setup complete! Your settings are saved.")
        return ConversationHandler.END
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
    if len(user_input) > 10:  # Reasonable limit for emoji
        await update.message.reply_text("Please enter a single emoji or short sequence.")
        return INPUT_EMOJI
    context.user_data.setdefault("settings", {})["emoji"] = user_input
    await update.message.reply_text("Emoji saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the media file ID or skip."""
    if update.message.text and update.message.text.lower() == "skip":
        context.user_data.setdefault("settings", {}).pop("media_file_id", None)  # Clear if skipped
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
    """Handle the /boost command."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /boost <token_address>")
        return
    token_address = context.args[0]
    context.user_data["last_boost_token"] = token_address
    cost = 10.0  # Example cost in SUI
    await update.message.reply_text(
        f"To boost {shorten_address(token_address)} for 24 hours, send {cost} SUI to {BOOST_RECEIVER}.\n"
        "After sending, use /confirm <transaction_hash>."
    )

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm a boost payment."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /confirm <transaction_hash>")
        return
    txn_hash = context.args[0]
    cost = 10.0  # Must match /boost
    if verify_payment(txn_hash, cost):  # Synchronous call wrapped in async handler
        token_address = context.user_data.get("last_boost_token")
        if not token_address:
            await update.message.reply_text("Please use /boost first to specify the token.")
            return
        expiration = int(time.time()) + 24 * 3600  # 24 hours
        save_boost(token_address, expiration)
        await update.message.reply_text(f"Boost confirmed for {shorten_address(token_address)}!")
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
            "media_file_id": group[4]
        }
        buys = fetch_recent_buys(settings["token_address"], settings["min_buy_usd"], last_timestamp)
        for buy in buys:
            log_buy(buy["token_address"], buy["timestamp"], buy["usd_value"], buy["buyer"])
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
            INPUT_MEDIA: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.TEXT & ~filters.COMMAND, receive_media)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost))
    application.add_handler(CommandHandler("confirm", confirm_boost))

    # Schedule jobs
    application.job_queue.run_repeating(poll_buys, interval=60, first=0)

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
