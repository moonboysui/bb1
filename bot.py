import os
import logging
import time
import threading
import asyncio
import json
from datetime import datetime, timedelta
import requests
from aiohttp import web
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
from database import init_db, get_db
from utils import shorten_address, format_alert
from sui_api import verify_payment, fetch_recent_buys, fetch_token_info, get_token_symbol

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "7551845767:AAF3UOQ4E0o33Bsd-0PBAlOLcifZU-1gT00")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0x7338ef163ee710923803cb0dd60b5b02cddc5fbafef417342e1bbf1fba20e702")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
PORT = int(os.getenv("PORT", 8080))

# Conversation states for setup
(
    CHOOSING, 
    INPUT_TOKEN, 
    INPUT_MIN_BUY, 
    INPUT_EMOJI, 
    INPUT_WEBSITE,
    INPUT_TELEGRAM, 
    INPUT_TWITTER, 
    INPUT_MEDIA,
    BOOST_CONFIRM
) = range(9)

# Boost options with duration (in seconds) and cost (in SUI)
BOOST_OPTIONS = {
    "4h": {"duration": 4 * 3600, "cost": 15},
    "8h": {"duration": 8 * 3600, "cost": 20},
    "12h": {"duration": 12 * 3600, "cost": 30},
    "24h": {"duration": 24 * 3600, "cost": 50},
    "48h": {"duration": 48 * 3600, "cost": 80},
    "72h": {"duration": 72 * 3600, "cost": 120},
    "1week": {"duration": 7 * 24 * 3600, "cost": 220},
    "2weeks": {"duration": 14 * 24 * 3600, "cost": 410},
    "1month": {"duration": 30 * 24 * 3600, "cost": 780},
}

# --- HTTP Server for Render.com ---

async def health_check(request):
    """Respond to Render.com health checks."""
    return web.Response(text="OK")

async def run_server():
    """Run an HTTP server to keep Render.com happy."""
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"HTTP server started on port {PORT}")

def start_http_server():
    """Start the HTTP server in a separate thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())
    loop.run_forever()

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command in a group."""
    if update.message.chat.type in ['group', 'supergroup']:
        # Record which group the setup is for
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
        # If they're already in private chat
        if 'setup_group_id' in context.user_data:
            await update.message.reply_text(
                f"🚀 Moonbags BuyBot Setup\n\n"
                f"You're configuring the bot for: {context.user_data['setup_group_name']}",
                reply_markup=get_menu_keyboard()
            )
            return CHOOSING
        else:
            # If they're starting in private chat without a group
            await update.message.reply_text(
                "Please add me to a group first, then use /start in that group to configure monitoring."
            )
            return ConversationHandler.END

async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when user clicks on the "Continue in Private Chat" button."""
    if not context.user_data.get('setup_group_id'):
        await update.message.reply_text(
            "Please add me to a group first, then use /start in that group to begin setup."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"🚀 Moonbags BuyBot Setup\n\n"
        f"You're configuring the bot for: {context.user_data['setup_group_name']}",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle selection from the configuration menu."""
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
        await query.message.reply_text("🎯 Send the emoji you'd like to represent buys (e.g. 🔥). One per $5.")
        return INPUT_EMOJI
    
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
        
        # Check for required fields
        required_fields = ['token_address', 'min_buy_usd', 'emoji']
        missing = [field for field in required_fields if field not in settings]
        
        if missing:
            missing_text = ", ".join(missing).replace("_", " ")
            await query.message.reply_text(
                f"⚠️ Setup incomplete! You still need to set: {missing_text}",
                reply_markup=get_menu_keyboard()
            )
            return CHOOSING
        
        # Get token symbol for display
        try:
            token_symbol = await get_token_symbol(settings['token_address'])
            settings['token_symbol'] = token_symbol
        except Exception as e:
            logger.error(f"Error fetching token symbol: {e}")
            settings['token_symbol'] = "TOKEN"
        
        # Update database
        group_id = context.user_data['setup_group_id']
        save_group_settings(group_id, settings)
        
        summary = (
            f"✅ Setup Complete!\n\n"
            f"Token: {shorten_address(settings['token_address'])} (${settings['token_symbol']})\n"
            f"Min Buy: ${settings['min_buy_usd']}\n"
            f"Emoji: {settings['emoji']}\n"
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
        
        # Send to private chat
        await query.message.reply_text(summary)
        
        # Also send a confirmation to the group
        await context.bot.send_message(
            group_id,
            f"✅ Setup complete! Now tracking ${settings['token_symbol']} buys above ${settings['min_buy_usd']}."
        )
        
        # Clear user data
        context.user_data.clear()
        
        return ConversationHandler.END

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process token address input."""
    token = update.message.text.strip()
    
    # Basic validation
    if not token.startswith("0x") or len(token) < 10:
        await update.message.reply_text("⚠️ That doesn't look like a valid token address. Please enter an address starting with 0x.")
        return INPUT_TOKEN
    
    # Initialize settings if not present
    if 'settings' not in context.user_data:
        context.user_data['settings'] = {}
    
    context.user_data['settings']['token_address'] = token
    
    await update.message.reply_text(
        f"✅ Token address saved: {shorten_address(token)}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process minimum buy amount input."""
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
    """Process emoji input."""
    emoji = update.message.text.strip()
    
    # Basic validation (could be improved)
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

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process website URL input."""
    website = update.message.text.strip()
    
    if website.lower() == 'skip':
        website = None
    else:
        # Basic URL validation
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
    """Process Telegram link input."""
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
    """Process Twitter/X link input."""
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
    """Process media (photo/GIF) input."""
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
        # Get the largest photo size
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
    """Cancel the conversation."""
    context.user_data.clear()
    await update.message.reply_text("✅ Setup cancelled.")
    return ConversationHandler.END

async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /boost command to boost token visibility."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "⚠️ Please provide a token address.\n"
            "Usage: /boost 0x1234...abcd"
        )
        return
    
    token_address = context.args[0]
    
    # Basic validation
    if not token_address.startswith("0x"):
        await update.message.reply_text("⚠️ Please provide a valid token address starting with 0x.")
        return
    
    # Store token address for later
    context.user_data['boost_token'] = token_address
    
    # Get token symbol if possible
    try:
        token_symbol = await get_token_symbol(token_address)
        context.user_data['boost_token_symbol'] = token_symbol
    except Exception as e:
        logger.error(f"Error fetching token symbol: {e}")
        context.user_data['boost_token_symbol'] = "TOKEN"
    
    # Create keyboard with boost options
    keyboard = []
    for duration, details in BOOST_OPTIONS.items():
        human_duration = duration.replace("h", " Hours").replace("week", " Week").replace("month", " Month")
        if duration in ["1week", "2weeks", "1month"]:
            human_duration = human_duration.replace("s", "s")
        button_text = f"{human_duration} - {details['cost']} SUI"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"boost_{duration}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🚀 Boost ${context.user_data['boost_token_symbol']} ({shorten_address(token_address)})\n\n"
        f"Boosting will:\n"
        f"• Place your token at the top of the trending leaderboard\n"
        f"• Show ALL buys in the trending channel\n\n"
        f"Select a boost duration:",
        reply_markup=reply_markup
    )

async def boost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle boost duration selection."""
    query = update.callback_query
    await query.answer()
    
    duration_key = query.data.split("_")[1]
    
    if duration_key not in BOOST_OPTIONS:
        await query.message.reply_text("⚠️ Invalid boost option selected.")
        return
    
    boost_details = BOOST_OPTIONS[duration_key]
    token_address = context.user_data.get('boost_token')
    token_symbol = context.user_data.get('boost_token_symbol', "TOKEN")
    
    if not token_address:
        await query.message.reply_text("⚠️ No token selected for boosting. Please use /boost first.")
        return
    
    # Store boost info
    context.user_data['boost_duration'] = duration_key
    context.user_data['boost_cost'] = boost_details['cost']
    context.user_data['boost_seconds'] = boost_details['duration']
    
    human_duration = duration_key.replace("h", " Hours").replace("week", " Week").replace("month", " Month")
    if duration_key in ["1week", "2weeks", "1month"]:
        human_duration = human_duration.replace("s", "s")
    
    await query.message.reply_text(
        f"💰 Boost Payment Required\n\n"
        f"Token: ${token_symbol} ({shorten_address(token_address)})\n"
        f"Duration: {human_duration}\n"
        f"Cost: {boost_details['cost']} SUI\n\n"
        f"Please send exactly {boost_details['cost']} SUI to:\n"
        f"`{BOOST_RECEIVER}`\n\n"
        f"After sending, reply with the transaction hash using:\n"
        f"/confirm TXHASH"
    )
    
    return BOOST_CONFIRM

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm a boost payment from provided transaction hash."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "⚠️ Please provide the transaction hash.\n"
            "Usage: /confirm 0x1234...abcd"
        )
        return
    
    txn_hash = context.args[0].strip()
    
    # Check if we have pending boost data
    token_address = context.user_data.get('boost_token')
    token_symbol = context.user_data.get('boost_token_symbol', "TOKEN")
    boost_cost = context.user_data.get('boost_cost')
    boost_duration = context.user_data.get('boost_duration')
    boost_seconds = context.user_data.get('boost_seconds')
    
    if not all([token_address, boost_cost, boost_duration, boost_seconds]):
        await update.message.reply_text(
            "⚠️ No pending boost to confirm. Please use /boost first to select a token and duration."
        )
        return
    
    # Show processing message
    processing_msg = await update.message.reply_text("⏳ Verifying transaction...")
    
    # Verify the payment
    try:
        payment_verified = await verify_payment(txn_hash, boost_cost, BOOST_RECEIVER)
        
        if not payment_verified:
            await processing_msg.edit_text(
                "❌ Payment verification failed. Please check:\n"
                "- You sent exactly the correct amount\n"
                "- You sent to the correct address\n"
                "- The transaction is confirmed\n\n"
                "Try again with /confirm TXHASH"
            )
            return
        
        # Payment verified, activate the boost
        expiration = int(time.time()) + boost_seconds
        
        # Save to database
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp) VALUES (?, ?)",
                (token_address, expiration)
            )
            conn.commit()
        
        # Format expiration time
        expiration_dt = datetime.fromtimestamp(expiration)
        expiration_str = expiration_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        
        human_duration = boost_duration.replace("h", " Hours").replace("week", " Week").replace("month", " Month")
        if boost_duration in ["1week", "2weeks", "1month"]:
            human_duration = human_duration.replace("s", "s")
        
        # Send confirmation
        await processing_msg.edit_text(
            f"✅ Boost activated!\n\n"
            f"Token: ${token_symbol} ({shorten_address(token_address)})\n"
            f"Duration: {human_duration}\n"
            f"Expires: {expiration_str}\n\n"
            f"Your token will now:\n"
            f"• Be featured at the top of the trending leaderboard\n"
            f"• Have ALL buys shown in the trending channel\n\n"
            f"Thank you for your support! 🚀"
        )
        
        # Clear the boost data
        for key in ['boost_token', 'boost_token_symbol', 'boost_duration', 'boost_cost', 'boost_seconds']:
            if key in context.user_data:
                del context.user_data[key]
        
        # Also post to trending channel
        await context.bot.send_message(
            TRENDING_CHANNEL,
            f"🔥 NEW BOOST! 🔥\n\n"
            f"${token_symbol} has been boosted for {human_duration}!\n"
            f"All buys will be featured in this channel."
        )
        
    except Exception as e:
        logger.error(f"Error confirming boost: {e}")
        await processing_msg.edit_text(
            "❌ An error occurred while verifying your payment. Please try again or contact support."
        )

# --- Utility Functions ---

def get_menu_keyboard():
    """Create the menu keyboard for bot configuration."""
    keyboard = [
        [InlineKeyboardButton("🔗 Track Token", callback_data="set_token")],
        [InlineKeyboardButton("📉 Set Minimum Buy Size", callback_data="set_min_buy")],
        [InlineKeyboardButton("🎯 Choose Emoji Style", callback_data="set_emoji")],
        [InlineKeyboardButton("🌐 Add Website", callback_data="set_website")],
        [InlineKeyboardButton("💬 Add Telegram Link", callback_data="set_telegram")],
        [InlineKeyboardButton("❌ Add X (Twitter) Link", callback_data="set_twitter")],
        [InlineKeyboardButton("📷 Upload Media", callback_data="set_media")],
        [InlineKeyboardButton("✅ Finish Setup", callback_data="finish_setup")],
    ]
    return InlineKeyboardMarkup(keyboard)

def save_group_settings(group_id, settings):
    """Save the group token tracking settings to the database."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO groups 
                (group_id, token_address, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    settings.get('token_address'),
                    settings.get('min_buy_usd', 0),
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

async def trend_alert(context: ContextTypes.DEFAULT_TYPE):
    """Send trending leaderboard to the trending channel."""
    try:
        # Get all tracked tokens
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT token_address FROM groups")
            tokens = [row[0] for row in cursor.fetchall()]
            
            # Also get boosted tokens
            cursor.execute("SELECT token_address, expiration_timestamp FROM boosts WHERE expiration_timestamp > ?", 
                          (int(time.time()),))
            boosted_tokens = {row[0]: row[1] for row in cursor.fetchall()}
        
        if not tokens:
            logger.info("No tokens being tracked, skipping trending update")
            return
        
        # Get volume data for each token
        token_data = []
        for token in tokens:
            try:
                # Get token info
                info = await fetch_token_info(token)
                symbol = info.get('symbol', 'TOKEN')
                market_cap = info.get('market_cap', 0)
                price_change = info.get('price_change_30m', 0)
                
                # Get 30-min volume
                thirty_min_ago = int(time.time()) - 1800
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT SUM(usd_value) FROM buys WHERE token_address = ? AND timestamp > ?",
                        (token, thirty_min_ago)
                    )
                    volume = cursor.fetchone()[0] or 0
                
                # Calculate boost status
                is_boosted = token in boosted_tokens
                boost_remaining = 0
                if is_boosted:
                    boost_remaining = boosted_tokens[token] - int(time.time())
                
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
        
        # Sort tokens: boosted first (by remaining time), then by volume
        token_data.sort(key=lambda x: (-1 if x['is_boosted'] else 0, -x['boost_remaining'], -x['volume_30m']))
        
        # Take top 10
        top_tokens = token_data[:10]
        
        if not top_tokens:
            logger.warning("No token data available for trending leaderboard")
            return
        
# Format the leaderboard
        now = datetime.now()
        header = f"🔥 MOONBAGS TRENDING: {now.strftime('%H:%M UTC')}\n\n"
        
        leaderboard = header
        for i, token in enumerate(top_tokens):
            position = i + 1
            position_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i]
            
            # Format price change
            if token['price_change'] > 0:
                price_change_str = f"📈 +{token['price_change']:.1f}%"
            elif token['price_change'] < 0:
                price_change_str = f"📉 {token['price_change']:.1f}%"
            else:
                price_change_str = "➖ 0%"
            
            # Format market cap
            if token['market_cap'] > 1000000:
                market_cap_str = f"${token['market_cap']/1000000:.1f}M"
            else:
                market_cap_str = f"${token['market_cap']/1000:.1f}K"
            
            # Boosted label
            boost_label = ""
            if token['is_boosted']:
                remaining_hours = math.ceil(token['boost_remaining'] / 3600)
                boost_label = f" 🚀 BOOSTED ({remaining_hours}h)"
            
            entry = (
                f"{position_emoji} ${token['symbol']}{boost_label}\n"
                f"💰 Market Cap: {market_cap_str}\n"
                f"{price_change_str}\n"
            )
            
            leaderboard += entry
            
            # Add separator except for last item
            if i < len(top_tokens) - 1:
                leaderboard += "\n"
        
        # Add footer with boost info
        leaderboard += (
            f"\n\n💎 Your token not on trending? Boost it with /boost! "
            f"Top buys shown in @moonbagstrending"
        )
        
        # Send to trending channel
        message = await context.bot.send_message(
            TRENDING_CHANNEL,
            leaderboard
        )
        
        # Pin the message
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
    """Check for new token buys and send alerts."""
    try:
        # Get all tracked tokens
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT token_address FROM groups")
            tokens = [row[0] for row in cursor.fetchall()]
            
            # Also get boosted tokens
            cursor.execute("SELECT token_address FROM boosts WHERE expiration_timestamp > ?", 
                          (int(time.time()),))
            boosted_tokens = [row[0] for row in cursor.fetchall()]
        
        if not tokens:
            logger.info("No tokens being tracked, skipping buy check")
            return
        
        # Get last check timestamp
        last_check = context.bot_data.get('last_check_timestamp', int(time.time()) - 60)
        current_time = int(time.time())
        context.bot_data['last_check_timestamp'] = current_time
        
        # Fetch recent buys for all tracked tokens
        for token_address in tokens:
            try:
                buys = await fetch_recent_buys(token_address, last_check)
                
                if not buys:
                    continue
                
                logger.info(f"Found {len(buys)} new buys for token {token_address}")
                
                # Process each buy
                for buy in buys:
                    # Store in database first
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
                    
                    # Get token info for the alert
                    token_info = await fetch_token_info(token_address)
                    token_symbol = token_info.get('symbol', 'TOKEN')
                    token_price = token_info.get('price', 0)
                    token_market_cap = token_info.get('market_cap', 0)
                    token_liquidity = token_info.get('liquidity', 0)
                    
                    # Get all groups tracking this token and their settings
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            SELECT group_id, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id
                            FROM groups
                            WHERE token_address = ?
                            """,
                            (token_address,)
                        )
                        groups = cursor.fetchall()
                    
                    # Format the alert
                    for group_data in groups:
                        group_id, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id = group_data
                        
                        # Skip if buy is smaller than minimum
                        if buy['usd_value'] < min_buy_usd:
                            continue
                        
                        # Generate dynamic emojis based on buy size
                        emoji_count = max(1, min(20, int(buy['usd_value'] / 5)))
                        emojis = emoji * emoji_count
                        
                        # Format amounts
                        usd_value_str = f"${buy['usd_value']:.2f}"
                        token_amount_str = f"{buy['amount']:.4f}"
                        
                        # Format market data
                        if token_market_cap > 1000000:
                            market_cap_str = f"${token_market_cap/1000000:.2f}M"
                        else:
                            market_cap_str = f"${token_market_cap/1000:.2f}K"
                        
                        if token_liquidity > 1000000:
                            liquidity_str = f"${token_liquidity/1000000:.2f}M"
                        else:
                            liquidity_str = f"${token_liquidity/1000:.2f}K"
                        
                        # Format alert text
                        alert_text = (
                            f"{emojis} NEW BUY {emojis}\n\n"
                            f"💰 {token_amount_str} ${token_symbol} (≈{usd_value_str})\n"
                            f"🧠 Buyer: [{shorten_address(buy['buyer_address'])}](https://suivision.xyz/txblock/{buy['tx_hash']})\n\n"
                            f"📊 ${token_symbol} Stats:\n"
                            f"💲 Price: ${token_price:.8f}\n"
                            f"💹 Market Cap: {market_cap_str}\n"
                            f"💧 Liquidity: {liquidity_str}"
                        )
                        
                        # Create buttons
                        keyboard = []
                        buy_button = InlineKeyboardButton(f"BUY ${token_symbol}", url=f"https://moonbags.io/tokens/{token_address}")
                        trending_button = InlineKeyboardButton("🌕 Moonbags Trending", url=f"https://t.me/moonbagstrending")
                        keyboard.append([buy_button, trending_button])
                        
                        # Add optional links if configured
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
                        
                        # Send to the group
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
                    
                    # Check if we should post to trending channel
                    should_post_trending = (
                        buy['usd_value'] >= 200 or  # Buy is $200 or more
                        token_address in boosted_tokens  # Token is boosted
                    )
                    
                    if should_post_trending:
                        # Use the same format as group alerts but with slightly modified text
                        emoji_count = max(1, min(20, int(buy['usd_value'] / 5)))
                        trending_emoji = "🔥"  # Standard emoji for trending channel
                        emojis = trending_emoji * emoji_count
                        
                        trending_text = (
                            f"{emojis} TRENDING BUY {emojis}\n\n"
                            f"💰 {token_amount_str} ${token_symbol} (≈{usd_value_str})\n"
                            f"🧠 Buyer: [{shorten_address(buy['buyer_address'])}](https://suivision.xyz/txblock/{buy['tx_hash']})\n\n"
                            f"📊 ${token_symbol} Stats:\n"
                            f"💲 Price: ${token_price:.8f}\n"
                            f"💹 Market Cap: {market_cap_str}\n"
                            f"💧 Liquidity: {liquidity_str}"
                        )
                        
                        # Create buttons for trending post
                        trending_keyboard = []
                        buy_button = InlineKeyboardButton(f"BUY ${token_symbol}", url=f"https://moonbags.io/tokens/{token_address}")
                        boost_button = InlineKeyboardButton("🚀 Boost This Token", url=f"https://t.me/MoonbagsBot?start=boost_{token_address}")
                        trending_keyboard.append([buy_button, boost_button])
                        
                        trending_markup = InlineKeyboardMarkup(trending_keyboard)
                        
                        # Send to trending channel
                        try:
                            # Use the media from the first group that has one
                            media_id = None
                            with get_db() as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "SELECT media_file_id FROM groups WHERE token_address = ? AND media_file_id IS NOT NULL LIMIT 1",
                                    (token_address,)
                                )
                                result = cursor.fetchone()
                                if result:
                                    media_id = result[0]
                            
                            if media_id:
                                await context.bot.send_photo(
                                    chat_id=TRENDING_CHANNEL,
                                    photo=media_id,
                                    caption=trending_text,
                                    parse_mode="Markdown",
                                    reply_markup=trending_markup
                                )
                            else:
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

# --- Main Function ---

def main():
    """Start the bot."""
    # Initialize the database
    init_db()
    
    # Start the HTTP server for Render.com
    server_thread = threading.Thread(target=start_http_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Register conversation handler for setup
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
    
    # Add standalone command handlers
    application.add_handler(CommandHandler("boost", boost_command))
    application.add_handler(CallbackQueryHandler(boost_callback, pattern=r"^boost_"))
    application.add_handler(CommandHandler("confirm", confirm_boost))
    
    # Set up scheduled jobs
    job_queue = application.job_queue
    
    # Check for buys every 30 seconds
    job_queue.run_repeating(check_buys, interval=30, first=5)
    
    # Post trending leaderboard every 30 minutes
    job_queue.run_repeating(trend_alert, interval=1800, first=60)
    
    # Start the Bot
    application.run_polling()

if __name__ == '__main__':
    main()
       
