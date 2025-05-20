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

# Assuming these are your custom modules from earlier discussions
from database import get_db, init_db
from utils import shorten_address, format_alert
from sui import verify_payment

# Set up logging to track bot activity and debug issues
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0x000...000")  # Default Sui address
TRENDING_CHANNEL_ID = os.getenv("TRENDING_CHANNEL_ID", "-1001234567890")  # Replace with your channel ID
PORT = int(os.getenv("PORT", 8080))  # Default port for Render.com

# Conversation states for group configuration
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA = range(8)

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the /start command in both groups and private chats."""
    chat_id = update.message.chat.id
    chat_type = update.message.chat.type
    logger.info(f"Received /start in chat {chat_id}, type: {chat_type}")

    try:
        if chat_type in ["group", "supergroup"]:
            # In groups, redirect to private chat for configuration
            button = InlineKeyboardButton(
                "➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}?start=group{chat_id}"
            )
            await update.message.reply_text(
                "Thanks for adding me! Please configure me in private chat.",
                reply_markup=InlineKeyboardMarkup([[button]])
            )
            return ConversationHandler.END
        else:
            # In private chat
            param = context.args[0] if context.args else None
            if param and param.startswith("group"):
                group_id = int(param[5:])
                context.user_data["group_id"] = group_id
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
                    group = cursor.fetchone()

                if group:
                    # Display existing settings
                    settings_text = (
                        f"Settings for group {group_id}:\n"
                        f"Token: {shorten_address(group['token_address'])}\n"
                        f"Min Buy: ${group['min_buy_usd']}\n"
                        f"Emoji: {group['emoji']}\n"
                        f"Website: {group['website'] or 'N/A'}\n"
                        f"Telegram: {group['telegram_link'] or 'N/A'}\n"
                        f"Twitter: {group['twitter_link'] or 'N/A'}\n"
                        f"Media: {'Set' if group['media_file_id'] else 'Not Set'}"
                    )
                    button = InlineKeyboardButton("✏️ Edit Settings", callback_data="edit_settings")
                    await update.message.reply_text(
                        settings_text, reply_markup=InlineKeyboardMarkup([[button]])
                    )
                else:
                    # New group setup
                    context.user_data["settings"] = {}
                    await update.message.reply_text(
                        "Let’s set up your group. Choose an option:", reply_markup=get_menu_keyboard()
                    )
                return CHOOSING
            else:
                await update.message.reply_text("Please use /start in your group first.")
                return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await update.message.reply_text("Something went wrong. Please try again.")
        return ConversationHandler.END

async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post a message to the trending channel (placeholder)."""
    if str(update.message.chat.id) == TRENDING_CHANNEL_ID:
        await update.message.reply_text("This is a trending channel update!")
    else:
        await update.message.reply_text(f"Trending updates are posted to {TRENDING_CHANNEL_ID}.")

# --- Configuration Handlers ---

async def start_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle configuration menu selections."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "edit_settings":
        await query.edit_message_text("Choose an option to edit:", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif data == "set_token":
        await query.message.reply_text("Enter the token address:")
        return INPUT_TOKEN
    elif data == "set_min_buy":
        await query.message.reply_text("Enter the minimum buy amount in USD:")
        return INPUT_MIN_BUY
    elif data == "set_emoji":
        await query.message.reply_text("Enter the emoji for alerts:")
        return INPUT_EMOJI
    elif data == "set_website":
        await query.message.reply_text("Enter the website URL (or 'skip'):")
        return INPUT_WEBSITE
    elif data == "set_telegram":
        await query.message.reply_text("Enter the Telegram link (or 'skip'):")
        return INPUT_TELEGRAM
    elif data == "set_twitter":
        await query.message.reply_text("Enter the Twitter link (or 'skip'):")
        return INPUT_TWITTER
    elif data == "set_media":
        await query.message.reply_text("Send a photo or GIF for alerts (or 'skip'):")
        return INPUT_MEDIA
    return CHOOSING

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the token address."""
    context.user_data["settings"]["token_address"] = update.message.text.strip()
    await update.message.reply_text("Token address saved. Next option:", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the minimum buy amount."""
    try:
        min_buy = float(update.message.text.strip())
        context.user_data["settings"]["min_buy_usd"] = min_buy
        await update.message.reply_text("Min buy saved. Next option:", reply_markup=get_menu_keyboard())
        return CHOOSING
    except ValueError:
        await update.message.reply_text("Please enter a valid number.")
        return INPUT_MIN_BUY

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the emoji."""
    context.user_data["settings"]["emoji"] = update.message.text.strip()
    await update.message.reply_text("Emoji saved. Next option:", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the website URL."""
    text = update.message.text.strip().lower()
    context.user_data["settings"]["website"] = None if text == "skip" else text
    await update.message.reply_text("Website saved. Next option:", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Telegram link."""
    text = update.message.text.strip().lower()
    context.user_data["settings"]["telegram_link"] = None if text == "skip" else text
    await update.message.reply_text("Telegram link saved. Next option:", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Twitter link."""
    text = update.message.text.strip().lower()
    context.user_data["settings"]["twitter_link"] = None if text == "skip" else text
    await update.message.reply_text("Twitter link saved. Next option:", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the media file ID."""
    text = update.message.text.strip().lower() if update.message.text else None
    if text == "skip":
        context.user_data["settings"]["media_file_id"] = None
    elif update.message.photo:
        context.user_data["settings"]["media_file_id"] = update.message.photo[-1].file_id
    elif update.message.animation:
        context.user_data["settings"]["media_file_id"] = update.message.animation.file_id
    else:
        await update.message.reply_text("Please send a photo or GIF, or type 'skip'.")
        return INPUT_MEDIA
    await update.message.reply_text("Media saved. Next option:", reply_markup=get_menu_keyboard())
    return CHOOSING

# --- Utility Functions ---

def get_menu_keyboard() -> InlineKeyboardMarkup:
    """Generate the configuration menu."""
    buttons = [
        [InlineKeyboardButton("Set Token", callback_data="set_token")],
        [InlineKeyboardButton("Set Min Buy", callback_data="set_min_buy")],
        [InlineKeyboardButton("Set Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Set Website", callback_data="set_website")],
        [InlineKeyboardButton("Set Telegram", callback_data="set_telegram")],
        [InlineKeyboardButton("Set Twitter", callback_data="set_twitter")],
        [InlineKeyboardButton("Set Media", callback_data="set_media")],
    ]
    return InlineKeyboardMarkup(buttons)

# --- HTTP Server for Render.com ---

async def health_check(request):
    """Respond to Render.com health checks."""
    return web.Response(text="OK")

async def run_server():
    """Start an HTTP server for Render.com."""
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"HTTP server started on port {PORT}")

# --- Main Application ---

async def main():
    """Initialize and run the bot."""
    # Initialize the database
    init_db()
    logger.info("Database initialized")

    # Set up the Telegram application
    application = Application.builder().token(BOT_TOKEN).build()

    # Define the conversation handler
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
            INPUT_MEDIA: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_media)],
        },
        fallbacks=[],
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("trending", trending))

    # Start the HTTP server and bot polling concurrently
    asyncio.create_task(run_server())
    await application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
