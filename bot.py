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
from sui_sdk import SuiClient, SuiTransaction  # Hypothetical Sui SDK

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration using environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUI_RPC_URL = os.getenv("SUI_RPC_URL", "https://fullnode.mainnet.sui.io")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0xYourSuiAddressHere")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
RAIDEN_X_API = os.getenv("RAIDEN_X_API", "https://api.raidenx.io/v1")
PORT = int(os.getenv("PORT", 8080))

# Conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA = range(8)

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
                telegram_link TEXT,
                twitter_link TEXT,
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
            INSERT OR REPLACE INTO groups (group_id, token_address, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group_id,
            settings["token_address"],
            settings["min_buy_usd"],
            settings["emoji"],
            settings.get("website"),
            settings.get("telegram_link"),
            settings.get("twitter_link"),
            settings.get("media_file_id")
        ))
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
    if settings.get("website"):
        message += f"\nWebsite: {settings['website']}"
    if settings.get("telegram_link"):
        message += f"\nTelegram: {settings['telegram_link']}"
    if settings.get("twitter_link"):
        message += f"\nTwitter: {settings['twitter_link']}"
    return message, settings.get("media_file_id")

# --- Sui Payment Verification ---

async def verify_payment(txn_hash, expected_amount):
    """Verify a Sui payment transaction."""
    try:
        sui_client = SuiClient(SUI_RPC_URL)
        txn = sui_client.get_transaction(txn_hash)
        if not txn:
            return False
        amount = float(txn["effects"]["balance_changes"]["amount"]) / 10**9  # Convert from MIST to SUI
        receiver = txn["effects"]["balance_changes"]["recipient"]
        return amount >= expected_amount and receiver == BOOST_RECEIVER
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
        return False

# --- Raiden X API Polling ---

def fetch_recent_buys(token_address, min_usd, last_timestamp):
    """Fetch recent buys from Raiden X API."""
    try:
        response = requests.get(
            f"{RAIDEN_X_API}/transactions",
            params={"token": token_address, "min_usd": min_usd, "since": last_timestamp}
        )
        response.raise_for_status()
        return response.json()["transactions"]
    except Exception as e:
        logger.error(f"Failed to fetch buys: {e}")
        return []

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the /start command."""
    chat = update.message.chat
    if chat.type in ["group", "supergroup"]:
        group_id = chat.id
        button = InlineKeyboardButton(
            "➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}?start=group{group_id}"
        )
        await update.message.reply_text(
            "Thanks for inviting me! Please continue setup in private chat.",
            reply_markup=InlineKeyboardMarkup([[button]])
        )
        return ConversationHandler.END
    else:
        param = context.args[0] if context.args else None
        if param and param.startswith("group"):
            group_id = int(param[5:])
            context.user_data["group_id"] = group_id
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
                group = cursor.fetchone()
            if group:
                settings_text = (
                    f"Current settings for group {group_id}:\n"
                    f"Token: {group[1]}\n"
                    f"Min Buy: ${group[2]}\n"
                    f"Emoji: {group[3]}\n"
                    f"Website: {group[4] or 'N/A'}\n"
                    f"Telegram: {group[5] or 'N/A'}\n"
                    f"Twitter: {group[6] or 'N/A'}\n"
                    f"Media: {'Set' if group[7] else 'Not Set'}"
                )
                button = InlineKeyboardButton("✏️ Edit Settings", callback_data="edit_settings")
                await update.message.reply_text(settings_text, reply_markup=InlineKeyboardMarkup([[button]]))
            else:
                await update.message.reply_text(
                    "Let’s configure the bot for your group. Use the buttons below.",
                    reply_markup=get_menu_keyboard()
                )
            return CHOOSING
        else:
            await update.message.reply_text("Please start the configuration from your group using /start.")
            return ConversationHandler.END

async def start_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle configuration menu selections."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "edit_settings":
        await query.message.reply_text("Choose an option to set:", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif data == "set_token":
        await query.message.reply_text("Please enter the token address (e.g., 0x123...).")
        return INPUT_TOKEN
    elif data == "set_min_buy":
        await query.message.reply_text("Please enter the minimum buy amount in USD (e.g., 10.50).")
        return INPUT_MIN_BUY
    elif data == "set_emoji":
        await query.message.reply_text("Please enter the emoji for alerts (e.g., 🚀).")
        return INPUT_EMOJI
    elif data == "set_website":
        await query.message.reply_text("Please enter the website URL (or type 'skip' to skip).")
        return INPUT_WEBSITE
    elif data == "set_telegram":
        await query.message.reply_text("Please enter the Telegram link (or type 'skip' to skip).")
        return INPUT_TELEGRAM
    elif data == "set_twitter":
        await query.message.reply_text("Please enter the Twitter link (or type 'skip' to skip).")
        return INPUT_TWITTER
    elif data == "set_media":
        await query.message.reply_text("Please send a photo or GIF for alerts (or type 'skip' to skip).")
        return INPUT_MEDIA
    elif data == "finish_setup":
        settings = context.user_data.get("settings", {})
        if "token_address" not in settings or "min_buy_usd" not in settings or "emoji" not in settings:
            await query.message.reply_text("You must set the token address, minimum buy, and emoji first!")
            return CHOOSING
        group_id = context.user_data["group_id"]
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

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the website URL."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        if not text.startswith("http"):
            text = "https://" + text
        context.user_data.setdefault("settings", {})["website"] = text
    await update.message.reply_text("Website saved (or skipped). What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Telegram link."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        if not text.startswith("https://t.me/"):
            text = "https://t.me/" + text.lstrip("@")
        context.user_data.setdefault("settings", {})["telegram_link"] = text
    await update.message.reply_text("Telegram link saved (or skipped). What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Twitter link."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        if not text.startswith("https://twitter.com/"):
            text = "https://twitter.com/" + text.lstrip("@")
        context.user_data.setdefault("settings", {})["twitter_link"] = text
    await update.message.reply_text("Twitter link saved (or skipped). What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the media file ID."""
    text = update.message.text.strip() if update.message.text else None
    if text and text.lower() == "skip":
        await update.message.reply_text("Media skipped. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data.setdefault("settings", {})["media_file_id"] = file_id
        await update.message.reply_text("Photo saved. What's next?", reply_markup=get_menu_keyboard())
    elif update.message.animation:
        file_id = update.message.animation.file_id
        context.user_data.setdefault("settings", {})["media_file_id"] = file_id
        await update.message.reply_text("GIF saved. What's next?", reply_markup=get_menu_keyboard())
    else:
        await update.message.reply_text("Please send a photo or GIF, or type 'skip' to skip.")
        return INPUT_MEDIA
    return CHOOSING

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
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id FROM groups WHERE token_address = ?", (token_address,))
        group = cursor.fetchone()
    if not group:
        await update.message.reply_text("Token not found in any group.")
        return
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
    if await verify_payment(txn_hash, cost):
        # Assuming user provides token_address in context or fetch from message history
        token_address = context.user_data.get("last_boost_token")
        if not token_address:
            await update.message.reply_text("Please use /boost first to specify the token.")
            return
        expiration = int(time.time()) + 24 * 3600  # 24 hours
        save_boost(token_address, expiration)
        await update.message.reply_text(f"Boost confirmed for {shorten_address(token_address)} until <timestamp>!")
    else:
        await update.message.reply_text("Payment verification failed. Ensure you sent the correct amount.")

async def poll_buys(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Poll Raiden X API for buy transactions."""
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
            "telegram_link": group[5],
            "twitter_link": group[6],
            "media_file_id": group[7]
        }
        buys = fetch_recent_buys(settings["token_address"], settings["min_buy_usd"], last_timestamp)
        for buy in buys:
            log_buy(buy["token_address"], buy["timestamp"], buy["usd_value"], buy["buyer"])
            message, media_file_id = format_alert(buy, settings)
            if media_file_id:
                await context.bot.send_photo(group[0], media_file_id, caption=message)
                await context.bot.send_photo(TRENDING_CHANNEL, media_file_id, caption=message)
            else:
                await context.bot.send_message(group[0], message)
                await context.bot.send_message(TRENDING_CHANNEL, message)
    context.bot_data["last_poll"] = int(time.time())

async def generate_leaderboard(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and post the leaderboard."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT token_address, SUM(usd_value) as total_usd
            FROM buys
            WHERE timestamp > ?
            GROUP BY token_address
            ORDER BY total_usd DESC
            LIMIT 10
        """, (int(time.time()) - 24 * 3600,))
        leaders = cursor.fetchall()
    if not leaders:
        return
    leaderboard = "🏆 Top 10 Tokens (Last 24h)\n\n"
    for i, (token, total_usd) in enumerate(leaders, 1):
        leaderboard += f"{i}. {shorten_address(token)}: ${total_usd:.2f}\n"
    await context.bot.send_message(TRENDING_CHANNEL, leaderboard)

# --- Utility Functions ---

def get_menu_keyboard():
    """Generate the configuration menu keyboard."""
    keyboard = [
        [InlineKeyboardButton("Set Token Address", callback_data="set_token")],
        [InlineKeyboardButton("Set Minimum Buy", callback_data="set_min_buy")],
        [InlineKeyboardButton("Set Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Set Website", callback_data="set_website")],
        [InlineKeyboardButton("Set Telegram Link", callback_data="set_telegram")],
        [InlineKeyboardButton("Set Twitter Link", callback_data="set_twitter")],
        [InlineKeyboardButton("Set Media", callback_data="set_media")],
        [InlineKeyboardButton("Finish Setup", callback_data="finish_setup")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Main Function ---

def main():
    """Set up and run the bot."""
    init_db()
    logger.info("Database initialized")

    threading.Thread(target=start_http_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
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
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost))
    application.add_handler(CommandHandler("confirm", confirm_boost))

    application.job_queue.run_repeating(poll_buys, interval=10, first=0)
    application.job_queue.run_repeating(generate_leaderboard, interval=1800, first=1800)

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
