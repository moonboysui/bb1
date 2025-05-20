import os
import logging
import asyncio
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

# Set up logging for debugging and tracking
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables or use hardcoded values
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Replace with your bot token
PORT = int(os.getenv("PORT", 8080))

# Define conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA = range(8)

# --- Database Functions ---

def init_db():
    """Initialize the SQLite database."""
    with sqlite3.connect("bot_settings.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                token_address TEXT,
                min_buy_usd REAL,
                emoji TEXT,
                website TEXT,
                telegram_link TEXT,
                twitter_link TEXT,
                media_file_id TEXT
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
            settings.get("token_address"),
            settings.get("min_buy_usd"),
            settings.get("emoji"),
            settings.get("website"),
            settings.get("telegram_link"),
            settings.get("twitter_link"),
            settings.get("media_file_id")
        ))
        conn.commit()
    logger.info(f"Settings saved for group {group_id}")

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the /start command in groups and private chats."""
    logger.info(f"Received /start in chat {update.message.chat.id}, type: {update.message.chat.type}")
    try:
        if update.message.chat.type in ["group", "supergroup"]:
            group_id = update.message.chat.id
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
                try:
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
                except Exception as e:
                    logger.error(f"Database error in start: {e}")
                    await update.message.reply_text("Error accessing settings. Please try again.")
                    return ConversationHandler.END
            else:
                await update.message.reply_text("Please start the configuration from your group using /start.")
                return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in start: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
        return ConversationHandler.END

# --- Configuration Handlers ---

async def start_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle selections from the configuration menu."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "edit_settings":
        await query.message.reply_text("Choose an option to set:", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif data == "set_token":
        await query.message.reply_text("Please enter the token address.")
        return INPUT_TOKEN
    elif data == "set_min_buy":
        await query.message.reply_text("Please enter the minimum buy amount in USD.")
        return INPUT_MIN_BUY
    elif data == "set_emoji":
        await query.message.reply_text("Please enter the emoji for alerts.")
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
        if "token_address" not in settings or "min_buy_usd" not in settings:
            await query.message.reply_text("You must set the token address and minimum buy first!")
            return CHOOSING
        group_id = context.user_data["group_id"]
        save_group_settings(group_id, settings)
        await query.message.reply_text("Setup complete! Your settings are saved.")
        return ConversationHandler.END
    return CHOOSING

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the token address."""
    user_input = update.message.text.strip()
    context.user_data.setdefault("settings", {})["token_address"] = user_input
    await update.message.reply_text("Token address saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the minimum buy amount."""
    try:
        min_buy = float(update.message.text.strip())
        context.user_data.setdefault("settings", {})["min_buy_usd"] = min_buy
        await update.message.reply_text("Minimum buy saved. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    except ValueError:
        await update.message.reply_text("Please enter a valid number (e.g., 10.50).")
        return INPUT_MIN_BUY

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the emoji."""
    user_input = update.message.text.strip()
    context.user_data.setdefault("settings", {})["emoji"] = user_input
    await update.message.reply_text("Emoji saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the website URL."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["website"] = text
    await update.message.reply_text("Website saved (or skipped). What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Telegram link."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["telegram_link"] = text
    await update.message.reply_text("Telegram link saved (or skipped). What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Twitter link."""
    text = update.message.text.strip()
    if text.lower() != "skip":
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

# --- HTTP Server for Render.com ---

async def health_check(request):
    """Respond to Render.com health checks."""
    return web.Response(text="OK")

async def run_server():
    """Run a simple HTTP server for Render.com."""
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"HTTP server started on port {PORT}")

# --- Placeholder Handlers (Minimal Implementation) ---

async def boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for the /boost command."""
    await update.message.reply_text("Boost functionality not implemented yet.")
    logger.info("Boost command called")

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for the /confirm command."""
    await update.message.reply_text("Confirm boost functionality not implemented yet.")
    logger.info("Confirm command called")

async def poll_buys(context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for polling buy transactions."""
    logger.info("Polling buys (placeholder)")

async def generate_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """Placeholder for generating leaderboards."""
    logger.info("Generating leaderboard (placeholder)")

# --- Main Application ---

async def main():
    """Initialize the database, set up the bot, and start polling."""
    init_db()
    logger.info("Database initialized")

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
            INPUT_TELEGRAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_telegram)],
            INPUT_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_twitter)],
            INPUT_MEDIA: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.TEXT & ~filters.COMMAND, receive_media)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=True,
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost))
    application.add_handler(CommandHandler("confirm", confirm_boost))
    application.add_error_handler(lambda update, context: logger.error(f"Update {update} caused error {context.error}"))

    # Schedule recurring tasks
    application.job_queue.run_repeating(poll_buys, interval=10, first=0)
    application.job_queue.run_repeating(generate_leaderboard, interval=1800, first=1800)

    # Start the HTTP server as a background task
    asyncio.create_task(run_server())

    # Start polling
    await application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
