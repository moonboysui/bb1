import os
import logging
import threading
import asyncio
from dotenv import load_dotenv
from datetime import datetime
import math
import aiohttp.web

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)

from database import init_db, clear_fake_symbols, get_db
from utils import shorten_address, format_alert
from sui_api import fetch_token_info, get_token_symbol, verify_payment
from buy_stream import start_buy_stream

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL")
PORT = int(os.getenv("PORT", 8080))

# Conversation states
(
    CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_BUYSTEP,
    INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA, BOOST_CONFIRM
) = range(10)

BOOST_OPTIONS = [
    ("4h",  4 * 3600,   15),
    ("8h",  8 * 3600,   20),
    ("12h", 12 * 3600,  27),
    ("24h", 24 * 3600,  45),
    ("48h", 48 * 3600,  80),
    ("72h", 72 * 3600, 110),
    ("1w",  7 * 24 * 3600, 180)
]

# --- HTTP server for Render health check ---
async def health_check(request):
    return aiohttp.web.Response(text="OK")

async def run_server():
    app = aiohttp.web.Application()
    app.add_routes([aiohttp.web.get('/', health_check)])
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"HTTP server started on port {PORT}")

def start_http_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())
    loop.run_forever()

# --- Setup Keyboard ---
def get_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔗 Track Token", callback_data="set_token"), InlineKeyboardButton("📉 Set Min Buy ($)", callback_data="set_min_buy")],
        [InlineKeyboardButton("🎯 Choose Emoji", callback_data="set_emoji"), InlineKeyboardButton("🪙 Emoji Step ($)", callback_data="set_buystep")],
        [InlineKeyboardButton("🌐 Add Website", callback_data="set_website"), InlineKeyboardButton("💬 Add Telegram Link", callback_data="set_telegram")],
        [InlineKeyboardButton("❌ Add X Link", callback_data="set_twitter"), InlineKeyboardButton("📷 Upload Media", callback_data="set_media")],
        [InlineKeyboardButton("✅ Finish Setup", callback_data="finish_setup")]
    ]
    return InlineKeyboardMarkup(keyboard)

def save_group_settings(group_id, settings):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO groups
                (group_id, token_address, token_symbol, min_buy_usd, buystep, emoji, website, telegram_link, twitter_link, media_file_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    settings.get('token_address'),
                    settings.get('token_symbol', 'TOKEN'),
                    settings.get('min_buy_usd', 0),
                    settings.get('buystep', 5),
                    settings.get('emoji', '🔥'),
                    settings.get('website'),
                    settings.get('telegram_link'),
                    settings.get('twitter_link'),
                    settings.get('media_file_id')
                )
            )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving group settings: {e}")
        return False

# --- Conversation Handlers for Setup (same as in previous code, but compacted for clarity) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type in ['group', 'supergroup']:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if member.status not in ["administrator", "creator"]:
            await update.message.reply_text("Only group admins can configure the BuyBot.")
            return ConversationHandler.END
        context.user_data['setup_group_id'] = update.effective_chat.id
        context.user_data['setup_group_name'] = update.effective_chat.title
        keyboard = [[InlineKeyboardButton("➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Thanks for inviting me! To begin setup, please continue in private chat.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END
    else:
        if 'setup_group_id' in context.user_data:
            await update.message.reply_text(
                f"🚀 Moonbags BuyBot Setup\n\nYou're configuring the bot for: {context.user_data['setup_group_name']}",
                reply_markup=get_menu_keyboard()
            )
            return CHOOSING
        else:
            await update.message.reply_text(
                "Please add me to a group first, then use /start in that group to configure monitoring."
            )
            return ConversationHandler.END

async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('setup_group_id'):
        await update.message.reply_text(
            "Please add me to a group first, then use /start in that group to begin setup."
        )
        return ConversationHandler.END
    await update.message.reply_text(
        f"🚀 Moonbags BuyBot Setup\n\nYou're configuring the bot for: {context.user_data['setup_group_name']}",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    prompts = {
        "set_token": "🔗 Please paste the token address you want to track (starting with 0x):",
        "set_min_buy": "📉 Only alert for buys above what USD value?",
        "set_emoji": "🎯 Send the emoji you'd like to represent buys (e.g. 🔥).",
        "set_buystep": "🪙 Enter how many dollars per emoji (e.g. 5 means 1 emoji per $5).",
        "set_website": "🌐 Please enter your website URL or type 'skip' to skip this step.",
        "set_telegram": "💬 Please enter your Telegram link or type 'skip' to skip this step.",
        "set_twitter": "❌ Please enter your X (Twitter) link or type 'skip' to skip this step.",
        "set_media": "📷 Upload a photo or GIF for your alerts, or type 'skip' to skip this step."
    }
    if choice in prompts:
        await query.message.reply_text(prompts[choice])
        return {
            "set_token": INPUT_TOKEN, "set_min_buy": INPUT_MIN_BUY,
            "set_emoji": INPUT_EMOJI, "set_buystep": INPUT_BUYSTEP,
            "set_website": INPUT_WEBSITE, "set_telegram": INPUT_TELEGRAM,
            "set_twitter": INPUT_TWITTER, "set_media": INPUT_MEDIA
        }[choice]
    elif choice == "finish_setup":
        if not context.user_data.get('setup_group_id'):
            await query.message.reply_text("Error: No group selected for setup. Please restart from a group.")
            return ConversationHandler.END
        settings = context.user_data.get('settings', {})
        required_fields = ['token_address', 'min_buy_usd', 'emoji', 'buystep']
        missing = [field for field in required_fields if field not in settings]
        if missing:
            missing_text = ", ".join(missing).replace("_", " ")
            await query.message.reply_text(
                f"⚠️ Setup incomplete! You still need to set: {missing_text}",
                reply_markup=get_menu_keyboard()
            )
            return CHOOSING
        try:
            token_symbol = get_token_symbol(settings['token_address'])
            settings['token_symbol'] = token_symbol
        except Exception as e:
            logger.error(f"Error fetching token symbol: {e}")
            settings['token_symbol'] = "TOKEN"
        group_id = context.user_data['setup_group_id']
        save_group_settings(group_id, settings)
        summary = (
            f"✅ Setup Complete!\n\n"
            f"Token: {shorten_address(settings['token_address'])} (${settings['token_symbol']})\n"
            f"Min Buy: ${settings['min_buy_usd']}\n"
            f"Emoji: {settings['emoji']} (every ${settings['buystep']})\n"
        )
        if settings.get('website'):
            summary += f"Website: {settings['website']}\n"
        if settings.get('telegram_link'):
            summary += f"Telegram: {settings['telegram_link']}\n"
        if settings.get('twitter_link'):
            summary += f"Twitter: {settings['twitter_link']}\n"
        if settings.get('media_file_id'):
            summary += f"Media: ✅ Uploaded\n"
        summary += "\nThe bot will now track buys for this token and alert your group!"
        await query.message.reply_text(summary)
        await context.bot.send_message(
            group_id,
            f"✅ Setup complete! Now tracking ${settings['token_symbol']} buys above ${settings['min_buy_usd']}."
        )
        context.user_data.clear()
        return ConversationHandler.END

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    if not token.startswith("0x") or len(token) < 10:
        await update.message.reply_text("⚠️ That doesn't look like a valid token address. Please enter an address starting with 0x.")
        return INPUT_TOKEN
    if 'settings' not in context.user_data:
        context.user_data['settings'] = {}
    context.user_data['settings']['token_address'] = token
    await update.message.reply_text(
        f"✅ Token address saved: {shorten_address(token)}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        min_buy = float(update.message.text.strip())
        if min_buy <= 0:
            await update.message.reply_text("⚠️ Please enter a positive value.")
            return INPUT_MIN_BUY
        if 'settings' not in context.user_data:
            context.user_data['settings'] = {}
        context.user_data['settings']['min_buy_usd'] = min_buy
        await update.message.reply_text(
            f"✅ Minimum buy set to: ${min_buy}\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return INPUT_MIN_BUY

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    emoji = update.message.text.strip()
    if len(emoji) > 10:
        await update.message.reply_text("⚠️ That's too long for an emoji. Please enter a shorter option.")
        return INPUT_EMOJI
    if 'settings' not in context.user_data:
        context.user_data['settings'] = {}
    context.user_data['settings']['emoji'] = emoji
    await update.message.reply_text(
        f"✅ Emoji set to: {emoji}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_buystep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        buystep = float(update.message.text.strip())
        if buystep <= 0:
            await update.message.reply_text("⚠️ Please enter a positive value.")
            return INPUT_BUYSTEP
        if 'settings' not in context.user_data:
            context.user_data['settings'] = {}
        context.user_data['settings']['buystep'] = buystep
        await update.message.reply_text(
            f"✅ Emoji Step set to: ${buystep} per emoji\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return INPUT_BUYSTEP

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    website = update.message.text.strip()
    if website.lower() == 'skip':
        website = None
    else:
        if not website.startswith(('http://', 'https://')):
            website = f"https://{website}"
    if 'settings' not in context.user_data:
        context.user_data['settings'] = {}
    context.user_data['settings']['website'] = website
    await update.message.reply_text(
        f"✅ Website set to: {website if website else 'skipped'}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_link = update.message.text.strip()
    if telegram_link.lower() == 'skip':
        telegram_link = None
    if 'settings' not in context.user_data:
        context.user_data['settings'] = {}
    context.user_data['settings']['telegram_link'] = telegram_link
    await update.message.reply_text(
        f"✅ Telegram link set to: {telegram_link if telegram_link else 'skipped'}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    twitter_link = update.message.text.strip()
    if twitter_link.lower() == 'skip':
        twitter_link = None
    if 'settings' not in context.user_data:
        context.user_data['settings'] = {}
    context.user_data['settings']['twitter_link'] = twitter_link
    await update.message.reply_text(
        f"✅ X (Twitter) link set to: {twitter_link if twitter_link else 'skipped'}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.strip().lower() == 'skip':
        if 'settings' not in context.user_data:
            context.user_data['settings'] = {}
        context.user_data['settings']['media_file_id'] = None
        await update.message.reply_text(
            "✅ Media skipped.\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        if 'settings' not in context.user_data:
            context.user_data['settings'] = {}
        context.user_data['settings']['media_file_id'] = file_id
        await update.message.reply_text(
            "✅ Photo saved for alerts.\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    elif update.message.animation:
        file_id = update.message.animation.file_id
        if 'settings' not in context.user_data:
            context.user_data['settings'] = {}
        context.user_data['settings']['media_file_id'] = file_id
        await update.message.reply_text(
            "✅ GIF saved for alerts.\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    else:
        await update.message.reply_text(
            "⚠️ Please send a photo, GIF, or type 'skip' to skip this step."
        )
        return INPUT_MEDIA

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Setup cancelled.")
    return ConversationHandler.END

# --- Boost Handlers (can be copy-pasted from earlier for brevity, or implemented fully as above) ---

# --- LIVE BUY STREAM HANDLER ---

async def handle_live_buy(buy, group_data):
    # group_data: (group_id, min_buy_usd, buystep, emoji, website, telegram_link, twitter_link, media_file_id)
    group_id, min_buy_usd, buystep, emoji, website, telegram_link, twitter_link, media_file_id = group_data
    token_info = fetch_token_info(buy['token_out'])
    group_settings = {'emoji': emoji, 'buystep': buystep}
    # Fetch up-to-date USD value if needed
    price = token_info.get("price") or 0
    usd_value = float(buy['amount']) * price
    if usd_value < min_buy_usd:
        return
    buy['usd_value'] = usd_value
    alert_text = format_alert(buy, token_info, group_settings)
    # Buttons
    keyboard = [
        [InlineKeyboardButton(f"BUY ${token_info.get('symbol', 'TOKEN')}", url=f"https://moonbags.io/tokens/{buy['token_out']}")],
        [InlineKeyboardButton("🌕 Moonbags Trending", url="https://t.me/moonbagstrending")]
    ]
    link_buttons = []
    if website:
        link_buttons.append(InlineKeyboardButton("🌐 Website", url=website))
    if telegram_link:
        link_buttons.append(InlineKeyboardButton("💬 Telegram", url=telegram_link))
    if twitter_link:
        link_buttons.append(InlineKeyboardButton("❌ Twitter", url=twitter_link))
    if link_buttons:
        keyboard.append(link_buttons)
    reply_markup = InlineKeyboardMarkup(keyboard)
    app = Application.builder().token(BOT_TOKEN).build()
    try:
        if media_file_id:
            await app.bot.send_photo(
                chat_id=group_id,
                photo=media_file_id,
                caption=alert_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await app.bot.send_message(
                chat_id=group_id,
                text=alert_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Failed to send alert to group {group_id}: {e}")

# --- Trending Leaderboard and other jobs can be copied from earlier ---

def main():
    init_db()
    clear_fake_symbols()
    server_thread = threading.Thread(target=start_http_server)
    server_thread.daemon = True
    server_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    # -- Add all handlers here, as before (setup flow, boost, etc) --

    # Start buy stream
    loop = asyncio.get_event_loop()
    loop.create_task(start_buy_stream(handle_live_buy))

    application.run_polling()

if __name__ == "__main__":
    main()
