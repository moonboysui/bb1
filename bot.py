import os
import logging
import threading
import asyncio
import time
import math
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from aiohttp import web
from dotenv import load_dotenv

from database import init_db, get_db, clear_fake_symbols
from utils import shorten_address, format_alert
from sui_api import verify_payment, fetch_recent_buys, fetch_token_info, get_token_symbol

load_dotenv()

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL")
PORT = int(os.getenv("PORT", 8080))

# --- Conversation States ---
(
    CHOOSING,
    INPUT_TOKEN,
    INPUT_MIN_BUY,
    INPUT_EMOJI,
    INPUT_BUYSTEP,
    INPUT_WEBSITE,
    INPUT_TELEGRAM,
    INPUT_TWITTER,
    INPUT_MEDIA,
    BOOST_CONFIRM
) = range(10)

# --- Boost Options ---
BOOST_OPTIONS = [
    ("4h",  4 * 3600,   15),
    ("8h",  8 * 3600,   20),
    ("12h", 12 * 3600,  27),
    ("24h", 24 * 3600,  45),
    ("48h", 48 * 3600,  80),
    ("72h", 72 * 3600, 110),
    ("1w",  7 * 24 * 3600, 180)
]

# --- HTTP server for Render health checks ---
async def health_check(request):
    return web.Response(text="OK")

async def run_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
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

# --- Setup Flow Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type in ['group', 'supergroup']:
        # Only allow group admins to start setup
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
    if choice == "set_token":
        await query.message.reply_text("🔗 Please paste the token address you want to track (starting with 0x):")
        return INPUT_TOKEN
    elif choice == "set_min_buy":
        await query.message.reply_text("📉 Only alert for buys above what USD value?")
        return INPUT_MIN_BUY
    elif choice == "set_emoji":
        await query.message.reply_text("🎯 Send the emoji you'd like to represent buys (e.g. 🔥).")
        return INPUT_EMOJI
    elif choice == "set_buystep":
        await query.message.reply_text("🪙 Enter how many dollars per emoji (e.g. 5 means 1 emoji per $5).")
        return INPUT_BUYSTEP
    elif choice == "set_website":
        await query.message.reply_text("🌐 Please enter your website URL or type 'skip' to skip this step.")
        return INPUT_WEBSITE
    elif choice == "set_telegram":
        await query.message.reply_text("💬 Please enter your Telegram link or type 'skip' to skip this step.")
        return INPUT_TELEGRAM
    elif choice == "set_twitter":
        await query.message.reply_text("❌ Please enter your X (Twitter) link or type 'skip' to skip this step.")
        return INPUT_TWITTER
    elif choice == "set_media":
        await query.message.reply_text("📷 Upload a photo or GIF for your alerts, or type 'skip' to skip this step.")
        return INPUT_MEDIA
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
    if website:
        await update.message.reply_text(
            f"✅ Website set to: {website}\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "✅ Website setting skipped.\n\nWhat would you like to configure next?",
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
    if telegram_link:
        await update.message.reply_text(
            f"✅ Telegram link set to: {telegram_link}\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "✅ Telegram link skipped.\n\nWhat would you like to configure next?",
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
    if twitter_link:
        await update.message.reply_text(
            f"✅ X (Twitter) link set to: {twitter_link}\n\nWhat would you like to configure next?",
            reply_markup=get_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "✅ X (Twitter) link skipped.\n\nWhat would you like to configure next?",
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

# --- Boost Command ---

async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please enter the token address you want to boost (starting with 0x):"
    )
    context.user_data["boost_step"] = "awaiting_token"
    return

async def boost_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("boost_step")
    if step == "awaiting_token":
        token_address = update.message.text.strip()
        if not token_address.startswith("0x") or len(token_address) < 10:
            await update.message.reply_text("⚠️ That doesn't look like a valid token address. Please enter an address starting with 0x.")
            return
        context.user_data['boost_token'] = token_address
        try:
            token_symbol = get_token_symbol(token_address)
            context.user_data['boost_token_symbol'] = token_symbol
        except Exception:
            context.user_data['boost_token_symbol'] = "TOKEN"
        keyboard = []
        for duration, seconds, cost in BOOST_OPTIONS:
            human = duration.replace("h", " Hours").replace("w", " Week")
            button_text = f"{human} - {cost} SUI"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"boost_{duration}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🚀 Boost ${context.user_data['boost_token_symbol']} ({shorten_address(token_address)})\n"
            f"Boosting will:\n"
            f"• Place your token at the top of the trending leaderboard\n"
            f"• Show ALL buys in the trending channel\n\n"
            f"Select a boost duration:",
            reply_markup=reply_markup
        )
        context.user_data["boost_step"] = "awaiting_option"
        return
    return

async def boost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    duration_key = query.data.split("_")[1]
    opt = next((o for o in BOOST_OPTIONS if o[0] == duration_key), None)
    if not opt:
        await query.message.reply_text("⚠️ Invalid boost option selected.")
        return
    _, boost_seconds, boost_cost = opt
    token_address = context.user_data.get('boost_token')
    token_symbol = context.user_data.get('boost_token_symbol', "TOKEN")
    context.user_data['boost_cost'] = boost_cost
    context.user_data['boost_seconds'] = boost_seconds
    await query.message.reply_text(
        f"💰 Boost Payment Required\n\n"
        f"Token: ${token_symbol} ({shorten_address(token_address)})\n"
        f"Duration: {duration_key}\n"
        f"Cost: {boost_cost} SUI\n\n"
        f"Please send exactly {boost_cost} SUI to:\n"
        f"`{BOOST_RECEIVER}`\n\n"
        f"After sending, reply with the transaction hash using:\n"
        f"/confirm TXHASH"
    )
    context.user_data["boost_step"] = "awaiting_confirm"
    return

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("⚠️ Please provide the transaction hash.\nUsage: /confirm 0x1234...abcd")
        return
    txn_hash = update.message.text.split(" ")[-1].strip()
    token_address = context.user_data.get('boost_token')
    token_symbol = context.user_data.get('boost_token_symbol', "TOKEN")
    boost_cost = context.user_data.get('boost_cost')
    boost_seconds = context.user_data.get('boost_seconds')
    if not all([token_address, boost_cost, boost_seconds]):
        await update.message.reply_text("⚠️ No pending boost to confirm. Please use /boost first to select a token and duration.")
        return
    processing_msg = await update.message.reply_text("⏳ Verifying transaction...")
    try:
        payment_verified = verify_payment(txn_hash, boost_cost, BOOST_RECEIVER)
        if not payment_verified:
            await processing_msg.edit_text(
                "❌ Payment verification failed. Please check:\n"
                "- You sent exactly the correct amount\n"
                "- You sent to the correct address\n"
                "- The transaction is confirmed\n\n"
                "Try again with /confirm TXHASH"
            )
            return
        expiration = int(time.time()) + boost_seconds
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp) VALUES (?, ?)",
                (token_address, expiration)
            )
            conn.commit()
        expiration_dt = datetime.fromtimestamp(expiration)
        expiration_str = expiration_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        await processing_msg.edit_text(
            f"✅ Boost activated!\n\n"
            f"Token: ${token_symbol} ({shorten_address(token_address)})\n"
            f"Duration: {boost_seconds // 3600} Hours\n"
            f"Expires: {expiration_str}\n\n"
            f"Your token will now:\n"
            f"• Be featured at the top of the trending leaderboard\n"
            f"• Have ALL buys shown in the trending channel\n\n"
            f"Thank you for your support! 🚀"
        )
        await context.bot.send_message(
            TRENDING_CHANNEL,
            f"🔥 NEW BOOST! 🔥\n\n${token_symbol} has been boosted for {boost_seconds // 3600} Hours!\nAll buys will be featured in this channel."
        )
        for key in ['boost_token', 'boost_token_symbol', 'boost_cost', 'boost_seconds', 'boost_step']:
            if key in context.user_data:
                del context.user_data[key]
    except Exception as e:
        logger.error(f"Error confirming boost: {e}")
        await processing_msg.edit_text(
            "❌ An error occurred while verifying your payment. Please try again or contact support."
        )

# --- Leaderboard + Buy Alerts ---

async def trend_alert(context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT token_address FROM groups")
            tokens = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT token_address, expiration_timestamp FROM boosts WHERE expiration_timestamp > ?", (int(time.time()),))
            boosted_tokens = {row[0]: row[1] for row in cursor.fetchall()}
        if not tokens:
            logger.info("No tokens being tracked, skipping trending update")
            return
        token_data = []
        for token in tokens:
            try:
                info = fetch_token_info(token)
                symbol = info.get('symbol', 'TOKEN')
                market_cap = info.get('market_cap', 0)
                price_change = 0  # Optionally, implement 30min price change using price history
                thirty_min_ago = int(time.time()) - 1800
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT SUM(usd_value) FROM buys WHERE token_address = ? AND timestamp > ?",
                        (token, thirty_min_ago)
                    )
                    volume = cursor.fetchone()[0] or 0
                is_boosted = token in boosted_tokens
                boost_remaining = boosted_tokens[token] - int(time.time()) if is_boosted else 0
                token_data.append({
                    'address': token,
                    'symbol': symbol,
                    'market_cap': market_cap,
                    'price_change': price_change,
                    'volume_30m': volume,
                    'is_boosted': is_boosted,
                    'boost_remaining': boost_remaining
                })
            except Exception as e:
                logger.error(f"Error fetching data for token {token}: {e}")
        token_data.sort(key=lambda x: (-1 if x['is_boosted'] else 0, -x['boost_remaining'], -x['volume_30m']))
        top_tokens = token_data[:10]
        if not top_tokens:
            logger.warning("No token data available for trending leaderboard")
            return
        now = datetime.utcnow()
        header = f"🔥 MOONBAGS TRENDING: {now.strftime('%H:%M UTC')}\n\n"
        leaderboard = header
        for i, token in enumerate(top_tokens):
            position_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i]
            market_cap_str = f"${token['market_cap']/1000000:.1f}M" if token['market_cap'] > 1e6 else f"${token['market_cap']/1000:.1f}K"
            boost_label = f" 🚀 BOOSTED ({math.ceil(token['boost_remaining']/3600)}h)" if token['is_boosted'] else ""
            entry = (
                f"{position_emoji} <a href='{token.get('telegram_link','')}'>{token['symbol']}</a>{boost_label}\n"
                f"💰 Market Cap: {market_cap_str}\n"
                f"📊 Volume(30m): ${token['volume_30m']:.0f}\n"
            )
            leaderboard += entry
            if i < len(top_tokens) - 1:
                leaderboard += "\n"
        leaderboard += (
            f"\n\n💎 Want to trend? Boost with /boost! Top buys shown in @moonbagstrending"
        )
        message = await context.bot.send_message(
            TRENDING_CHANNEL,
            leaderboard, parse_mode="HTML"
        )
        try:
            await context.bot.pin_chat_message(
                TRENDING_CHANNEL,
                message.message_id
            )
        except Exception as e:
            logger.error(f"Failed to pin trending message: {e}")
    except Exception as e:
        logger.error(f"Error posting trending leaderboard: {e}")

async def check_buys(context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT token_address FROM groups")
            tokens = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT token_address FROM boosts WHERE expiration_timestamp > ?", (int(time.time()),))
            boosted_tokens = [row[0] for row in cursor.fetchall()]
        if not tokens:
            logger.info("No tokens being tracked, skipping buy check")
            return
        last_check = context.bot_data.get('last_check_timestamp', int(time.time()) - 60)
        current_time = int(time.time())
        context.bot_data['last_check_timestamp'] = current_time
        for token_address in tokens:
            try:
                buys = fetch_recent_buys(token_address, last_check)
                if not buys:
                    continue
                logger.info(f"Found {len(buys)} new buys for token {token_address}")
                token_info = fetch_token_info(token_address)
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT group_id, min_buy_usd, buystep, emoji, website, telegram_link, twitter_link, media_file_id
                        FROM groups WHERE token_address = ?
                        """,
                        (token_address,)
                    )
                    groups = cursor.fetchall()
                for buy in buys:
                    # Save to DB
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT OR IGNORE INTO buys
                            (transaction_id, token_address, buyer_address, amount, usd_value, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                buy['tx_hash'],
                                token_address,
                                buy['buyer_address'],
                                buy['amount'],
                                buy['usd_value'],
                                buy['timestamp']
                            )
                        )
                        conn.commit()
                    for group_data in groups:
                        group_id, min_buy_usd, buystep, emoji, website, telegram_link, twitter_link, media_file_id = group_data
                        if buy['usd_value'] < min_buy_usd:
                            continue
                        group_settings = {
                            'emoji': emoji,
                            'buystep': buystep
                        }
                        alert_text = format_alert(buy, token_info, group_settings)
                        # Buttons
                        keyboard = [
                            [InlineKeyboardButton(f"BUY ${token_info.get('symbol', 'TOKEN')}", url=f"https://moonbags.io/tokens/{token_address}")],
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
                        try:
                            if media_file_id:
                                await context.bot.send_photo(
                                    chat_id=group_id,
                                    photo=media_file_id,
                                    caption=alert_text,
                                    parse_mode="Markdown",
                                    reply_markup=reply_markup
                                )
                            else:
                                await context.bot.send_message(
                                    chat_id=group_id,
                                    text=alert_text,
                                    parse_mode="Markdown",
                                    reply_markup=reply_markup
                                )
                        except Exception as e:
                            logger.error(f"Failed to send alert to group {group_id}: {e}")
                    should_post_trending = (
                        buy['usd_value'] >= 200 or token_address in boosted_tokens
                    )
                    if should_post_trending:
                        trending_emoji = "🔥"
                        emoji_count = max(1, min(20, int(buy['usd_value'] / 5)))
                        emojis = trending_emoji * emoji_count
                        trending_text = (
                            f"{emojis} TRENDING BUY {emojis}\n\n"
                            f"💰 {buy['amount']:.4f} ${token_info.get('symbol', 'TOKEN')} (≈${buy['usd_value']:.2f})\n"
                            f"👤 Buyer: [{shorten_address(buy['buyer_address'])}](https://suivision.xyz/address/{buy['buyer_address']}) | "
                            f"[Txn](https://suivision.xyz/txblock/{buy['tx_hash']})\n\n"
                            f"📊 ${token_info.get('symbol', 'TOKEN')} Stats:\n"
                            f"💲 Price: ${token_info.get('price', 0):.8f}\n"
                            f"💹 Market Cap: {token_info.get('market_cap', 0)/1000:.2f}K\n"
                            f"💧 Liquidity: {token_info.get('liquidity', 0)/1000:.2f}K"
                        )
                        trending_keyboard = [
                            [InlineKeyboardButton(f"BUY ${token_info.get('symbol', 'TOKEN')}", url=f"https://moonbags.io/tokens/{token_address}"),
                             InlineKeyboardButton("🚀 Boost", url=f"https://t.me/MoonbagsBot?start=boost_{token_address}")]
                        ]
                        trending_markup = InlineKeyboardMarkup(trending_keyboard)
                        try:
                            await context.bot.send_message(
                                chat_id=TRENDING_CHANNEL,
                                text=trending_text,
                                parse_mode="Markdown",
                                reply_markup=trending_markup
                            )
                        except Exception as e:
                            logger.error(f"Failed to send buy to trending channel: {e}")
            except Exception as e:
                logger.error(f"Error processing buys for token {token_address}: {e}")
    except Exception as e:
        logger.error(f"Error checking for buys: {e}")

# --- Main ---

def main():
    init_db()
    clear_fake_symbols()
    server_thread = threading.Thread(target=start_http_server)
    server_thread.daemon = True
    server_thread.start()
    application = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, start_private)
        ],
        states={
            CHOOSING: [CallbackQueryHandler(menu_handler)],
            INPUT_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            INPUT_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_buy)],
            INPUT_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_emoji)],
            INPUT_BUYSTEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buystep)],
            INPUT_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_website)],
            INPUT_TELEGRAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_telegram)],
            INPUT_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_twitter)],
            INPUT_MEDIA: [
                MessageHandler(filters.PHOTO | filters.ANIMATION, receive_media),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_media)
            ],
            BOOST_CONFIRM: [CommandHandler("confirm", confirm_boost)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost_command))
    application.add_handler(CallbackQueryHandler(boost_callback, pattern=r"^boost_"))
    application.add_handler(CommandHandler("confirm", confirm_boost))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, boost_message_handler))
    job_queue = application.job_queue
    job_queue.run_repeating(check_buys, interval=30, first=5)
    job_queue.run_repeating(trend_alert, interval=1800, first=60)
    application.run_polling()

if __name__ == '__main__':
    main()
