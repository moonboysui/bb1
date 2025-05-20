import os
import logging
import time
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
    CallbackContext,
)
from database import get_db, init_db
from utils import shorten_address, format_alert
from sui import verify_payment

# Set up logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0x0000000000000000000000000000000000000000000000000000000000000000")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
PORT = int(os.getenv("PORT", 8080))

# Conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA = range(8)

async def start(update: Update, context: CallbackContext) -> None:
    """Handle the /start command in groups and private chats."""
    logger.info(f"Received /start command in chat {update.message.chat.id}, type: {update.message.chat.type}")
    try:
        if update.message.chat.type in ["group", "supergroup"]:
            group_id = update.message.chat.id
            button = InlineKeyboardButton("➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}?start=group{group_id}")
            await update.message.reply_text(
                "Thanks for inviting me! Please continue setup in private chat.",
                reply_markup=InlineKeyboardMarkup([[button]])
            )
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
                        f"Token: {group['token_address']}\n"
                        f"Min Buy: ${group['min_buy_usd']}\n"
                        f"Emoji: {group['emoji']}\n"
                        f"Website: {group['website'] or 'N/A'}\n"
                        f"Telegram: {group['telegram_link'] or 'N/A'}\n"
                        f"Twitter: {group['twitter_link'] or 'N/A'}\n"
                        f"Media: {'Set' if group['media_file_id'] else 'Not Set'}"
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
    except Exception as e:
        logger.error(f"Error in start: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

def get_menu_keyboard():
    """Return the configuration menu keyboard."""
    buttons = [
        [InlineKeyboardButton("Set Token Address", callback_data="set_token")],
        [InlineKeyboardButton("Set Min Buy", callback_data="set_min_buy")],
        [InlineKeyboardButton("Set Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Set Website", callback_data="set_website")],
        [InlineKeyboardButton("Set Telegram", callback_data="set_telegram")],
        [InlineKeyboardButton("Set Twitter", callback_data="set_twitter")],
        [InlineKeyboardButton("Set Media", callback_data="set_media")]
    ]
    return InlineKeyboardMarkup(buttons)

async def start_config(update: Update, context: CallbackContext) -> int:
    """Handle configuration menu selections."""
    query = update.callback_query
    await query.answer()
    if query.data == "edit_settings":
        await query.edit_message_text("Choose an option to edit:", reply_markup=get_menu_keyboard())
        return CHOOSING
    return CHOOSING  # Placeholder; expand as needed

async def error_handler(update: Update, context: CallbackContext) -> None:
    """Log errors raised by the bot."""
    logger.error(f"Exception while handling an update: {context.error}")

def main():
    """Start the bot."""
    init_db()  # Initialize the database
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CallbackQueryHandler(start_config)],
        states={
            CHOOSING: [CallbackQueryHandler(start_config)],
            # Add other states as needed
        },
        fallbacks=[],
        per_message=True,
    )

    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
