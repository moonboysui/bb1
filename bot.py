import os
import logging
import threading
import time
import asyncio
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
from database import init_db, get_db, clear_fake_symbols
from utils import shorten_address, format_alert
from sui_api import verify_payment, fetch_recent_buys, fetch_token_info, get_token_symbol
import buy_stream  # WebSocket event handling for buy tracking

# Initialize database and clear any placeholder symbols
init_db()
clear_fake_symbols()

# Logging setup
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
PORT = int(os.getenv("PORT", "8080"))

# Conversation states for /start setup
(CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI,
 INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER,
 INPUT_MEDIA, BOOST_CONFIRM) = range(9)

# Boost duration options (duration in seconds, cost in SUI)
BOOST_OPTIONS = {
    "4h":    {"duration": 4 * 3600,    "cost": 15},
    "8h":    {"duration": 8 * 3600,    "cost": 20},
    "12h":   {"duration": 12 * 3600,   "cost": 30},
    "24h":   {"duration": 24 * 3600,   "cost": 50},
    "48h":   {"duration": 48 * 3600,   "cost": 80},
    "72h":   {"duration": 72 * 3600,   "cost": 120},
    "1week": {"duration": 7 * 24 * 3600,  "cost": 220},
    "2weeks":{"duration": 14 * 24 * 3600, "cost": 410},
    "1month":{"duration": 30 * 24 * 3600, "cost": 780},
}

# --- HTTP health-check server (for Render) ---
from aiohttp import web

async def health_check(request):
    return web.Response(text="OK")

async def run_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"HTTP healthcheck server started on port {PORT}")

def start_http_server():
    """Run the health-check HTTP server in a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())
    loop.run_forever()

# --- Command and conversation handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start in a group to initialize the setup process."""
    if update.message.chat.type in ('group', 'supergroup'):
        # Only allow group admins to configure
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("❌ Only a group admin can configure this bot.")
                return ConversationHandler.END
        except Exception as e:
            logger.error(f"Failed to check admin status: {e}")
        # Store the group info for configuration
        context.user_data['setup_group_id'] = update.effective_chat.id
        context.user_data['setup_group_name'] = update.effective_chat.title
        # Prompt user to continue setup in private chat
        keyboard = [[InlineKeyboardButton("➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}")]]
        await update.message.reply_text(
            "Thanks for adding me! Click below to continue setup in a private chat with me.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    else:
        # If /start is used in private without initiating from a group
        if 'setup_group_id' in context.user_data:
            await update.message.reply_text(
                f"🚀 Moonbags BuyBot Setup\n\nYou're configuring the bot for: {context.user_data['setup_group_name']}",
                reply_markup=get_menu_keyboard()
            )
            return CHOOSING
        else:
            await update.message.reply_text("Please use /start in a group to begin configuration.")
            return ConversationHandler.END

async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle when the user opens the private chat after using /start in a group."""
    if not context.user_data.get('setup_group_id'):
        await update.message.reply_text("Please send /start in your group first to initiate setup.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"🚀 Moonbags BuyBot Setup\n\nYou're configuring the bot for: {context.user_data['setup_group_name']}",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle selection from the setup menu keyboard."""
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == "set_token":
        await query.message.reply_text("🔗 Please paste the token address to track (must start with 0x):")
        return INPUT_TOKEN
    elif choice == "set_min_buy":
        await query.message.reply_text("📉 Set the minimum buy USD value for alerts:")
        return INPUT_MIN_BUY
    elif choice == "set_emoji":
        await query.message.reply_text("🎯 Send an emoji to represent buys (e.g. 🔥). It will repeat per $5 of buy size.")
        return INPUT_EMOJI
    elif choice == "set_website":
        await query.message.reply_text("🌐 Enter your project website URL (or type 'skip' to skip):")
        return INPUT_WEBSITE
    elif choice == "set_telegram":
        await query.message.reply_text("💬 Enter your Telegram invite link (or 'skip'):")
        return INPUT_TELEGRAM
    elif choice == "set_twitter":
        await query.message.reply_text("❌ Enter your Twitter (X) link (or 'skip'):")
        return INPUT_TWITTER
    elif choice == "set_media":
        await query.message.reply_text("📷 Send a photo or GIF for alerts (or type 'skip' to skip):")
        return INPUT_MEDIA
    elif choice == "finish_setup":
        if not context.user_data.get('setup_group_id'):
            await query.message.reply_text("Error: No group selected. Please restart setup from the group.")
            return ConversationHandler.END
        settings = context.user_data.get('settings', {})
        # Require token, min_buy, emoji to be set
        required_fields = ['token_address', 'min_buy_usd', 'emoji']
        missing = [f for f in required_fields if f not in settings]
        if missing:
            missing_text = ", ".join(missing).replace("_", " ")
            await query.message.reply_text(
                f"⚠️ Setup incomplete. Please configure: {missing_text}",
                reply_markup=get_menu_keyboard()
            )
            return CHOOSING
        # Fetch token symbol for display/storage
        try:
            token_symbol = get_token_symbol(settings['token_address'])
            settings['token_symbol'] = token_symbol
        except Exception as e:
            logger.error(f"Error fetching token symbol: {e}")
            settings['token_symbol'] = "TOKEN"
        # Save settings to database
        group_id = context.user_data['setup_group_id']
        save_group_settings(group_id, settings)
        # If WebSocket tracking is enabled, subscribe to this token's events
        if buy_stream.WS_URL:
            buy_stream.subscribe_queue.put(settings['token_address'])
        # Send summary to user and confirmation to group
        summary = (
            "✅ Setup Complete!\n\n"
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
            summary += "Media: ✅ Uploaded\n"
        summary += "\nThe bot will now track buys for this token in your group!"
        await query.message.reply_text(summary)
        await context.bot.send_message(
            group_id,
            f"✅ BuyBot setup complete! Now tracking ${settings['token_symbol']} buys above ${settings['min_buy_usd']}."
        )
        # Clear user data and end conversation
        context.user_data.clear()
        return ConversationHandler.END

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the token address input."""
    token = update.message.text.strip()
    if not token.startswith("0x") or len(token) < 10:
        await update.message.reply_text("⚠️ Invalid address. Make sure it starts with 0x...")
        return INPUT_TOKEN
    context.user_data.setdefault('settings', {})['token_address'] = token
    await update.message.reply_text(
        f"✅ Token address saved: {shorten_address(token)}\n\nWhat would you like to configure next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the minimum USD buy size input."""
    try:
        min_buy = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return INPUT_MIN_BUY
    if min_buy <= 0:
        await update.message.reply_text("⚠️ Minimum buy must be a positive number.")
        return INPUT_MIN_BUY
    context.user_data.setdefault('settings', {})['min_buy_usd'] = min_buy
    await update.message.reply_text(
        f"✅ Minimum buy set to: ${min_buy}\n\nWhat next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the emoji input for buy alerts."""
    emoji = update.message.text.strip()
    if len(emoji) == 0 or len(emoji) > 10:
        await update.message.reply_text("⚠️ Please send a valid emoji (up to 10 characters).")
        return INPUT_EMOJI
    context.user_data.setdefault('settings', {})['emoji'] = emoji
    await update.message.reply_text(
        f"✅ Emoji set to: {emoji}\n\nWhat next?",
        reply_markup=get_menu_keyboard()
    )
    return CHOOSING

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the website URL input."""
    website = update.message.text.strip()
    if website.lower() == 'skip':
        website = None
    else:
        if not website.startswith("http://") and not website.startswith("https://"):
            website = "https://" + website
    context.user_data.setdefault('settings', {})['website'] = website
    if website:
        await update.message.reply_text(
            f"✅ Website set to: {website}\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "✅ Website skipped.\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the Telegram link input."""
    tg_link = update.message.text.strip()
    if tg_link.lower() == 'skip':
        tg_link = None
    context.user_data.setdefault('settings', {})['telegram_link'] = tg_link
    if tg_link:
        await update.message.reply_text(
            f"✅ Telegram link set to: {tg_link}\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "✅ Telegram link skipped.\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the Twitter (X) link input."""
    tw_link = update.message.text.strip()
    if tw_link.lower() == 'skip':
        tw_link = None
    context.user_data.setdefault('settings', {})['twitter_link'] = tw_link
    if tw_link:
        await update.message.reply_text(
            f"✅ X (Twitter) link set to: {tw_link}\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "✅ X link skipped.\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the media (photo/GIF) input."""
    # Skip handling
    if update.message.text and update.message.text.strip().lower() == 'skip':
        context.user_data.setdefault('settings', {})['media_file_id'] = None
        await update.message.reply_text(
            "✅ Media skipped.\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    # Photo input
    if update.message.photo:
        file_id = update.message.photo[-1].file_id  # largest size photo
        context.user_data.setdefault('settings', {})['media_file_id'] = file_id
        await update.message.reply_text(
            "✅ Photo saved for alerts.\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    # GIF/animation input
    if update.message.animation:
        file_id = update.message.animation.file_id
        context.user_data.setdefault('settings', {})['media_file_id'] = file_id
        await update.message.reply_text(
            "✅ GIF saved for alerts.\n\nWhat next?",
            reply_markup=get_menu_keyboard()
        )
        return CHOOSING
    # Invalid input
    await update.message.reply_text("⚠️ Please send a photo or GIF, or type 'skip' to skip.")
    return INPUT_MEDIA

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the setup process."""
    context.user_data.clear()
    await update.message.reply_text("✅ Setup cancelled.")
    return ConversationHandler.END

async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /boost command to initiate token boosting."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("⚠️ Provide a token address.\nUsage: /boost 0x1234...abcd")
        return
    token_address = context.args[0]
    if not token_address.startswith("0x"):
        await update.message.reply_text("⚠️ Invalid token address. Must start with 0x.")
        return
    # Store token for boost flow
    context.user_data['boost_token'] = token_address
    try:
        token_symbol = get_token_symbol(token_address)
    except Exception as e:
        logger.error(f"Error fetching symbol for boost token: {e}")
        token_symbol = "TOKEN"
    context.user_data['boost_token_symbol'] = token_symbol
    # Build boost duration options keyboard
    buttons = []
    for dur, details in BOOST_OPTIONS.items():
        # Human-readable duration
        human = dur.replace("h", " Hours").replace("week", " Week").replace("month", " Month")
        if dur in ["1week", "2weeks", "1month"]:
            human = human.replace("s", "s")
        btn_text = f"{human} - {details['cost']} SUI"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"boost_{dur}")])
    await update.message.reply_text(
        f"🚀 Boost ${token_symbol} ({shorten_address(token_address)})\n\n"
        f"Boosting will:\n"
        f"• Put your token at the **top** of the trending leaderboard\n"
        f"• Show **all buys > $20** in @moonbagstrending during the boost\n\n"
        f"Select a boost duration:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    # No state transition here; next step handled by CallbackQueryHandler

async def boost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle selecting a boost duration from the inline keyboard."""
    query = update.callback_query
    await query.answer()
    if not context.user_data.get('boost_token'):
        await query.message.reply_text("⚠️ No token selected for boost. Use /boost <token> first.")
        return
    choice = query.data  # e.g., "boost_4h"
    duration_key = choice.split("_", 1)[1] if "_" in choice else choice
    if duration_key not in BOOST_OPTIONS:
        await query.message.reply_text("⚠️ Invalid boost option.")
        return
    details = BOOST_OPTIONS[duration_key]
    context.user_data['boost_duration'] = duration_key
    context.user_data['boost_cost'] = details['cost']
    context.user_data['boost_seconds'] = details['duration']
    # Prepare payment instruction message
    human = duration_key.replace("h", " Hours").replace("week", " Week").replace("month", " Month")
    if duration_key in ["1week", "2weeks", "1month"]:
        human = human.replace("s", "s")
    token_symbol = context.user_data.get('boost_token_symbol', "TOKEN")
    await query.message.reply_text(
        f"💰 **Boost Payment Required**\n\n"
        f"Token: ${token_symbol} ({shorten_address(context.user_data['boost_token'])})\n"
        f"Duration: {human}\n"
        f"Cost: {details['cost']} SUI\n\n"
        f"Send **{details['cost']} SUI** to the address below, then reply /confirm <TXHASH>:\n"
        f"`{BOOST_RECEIVER}`",
        parse_mode="Markdown"
    )
    return BOOST_CONFIRM

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm the SUI payment for a token boost."""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("⚠️ Provide the transaction hash.\nUsage: /confirm <TXHASH>")
        return
    tx_hash = context.args[0].strip()
    # Ensure we have pending boost data
    token_address = context.user_data.get('boost_token')
    token_symbol = context.user_data.get('boost_token_symbol', 'TOKEN')
    boost_cost = context.user_data.get('boost_cost')
    boost_duration = context.user_data.get('boost_duration')
    boost_seconds = context.user_data.get('boost_seconds')
    if not token_address or boost_cost is None or boost_duration is None or boost_seconds is None:
        await update.message.reply_text("⚠️ No pending boost. Use /boost <token> first.")
        return
    # Verify payment asynchronously
    processing_msg = await update.message.reply_text("⏳ Verifying transaction on-chain...")
    try:
        payment_ok = await verify_payment(tx_hash, boost_cost, BOOST_RECEIVER)
        if not payment_ok:
            await processing_msg.edit_text(
                "❌ Payment verification failed.\n"
                "- Check that you sent the exact amount\n"
                "- Check that you sent to the correct address\n"
                "- Ensure the transaction is confirmed on-chain\n\n"
                "Then try /confirm <TXHASH> again."
            )
            return
        # Payment verified: activate boost
        expiration = int(time.time()) + boost_seconds
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp) VALUES (?, ?)",
                        (token_address, expiration))
            conn.commit()
        expiration_time = datetime.utcfromtimestamp(expiration).strftime("%Y-%m-%d %H:%M:%S UTC")
        human = boost_duration.replace("h", " Hours").replace("week", " Week").replace("month", " Month")
        if boost_duration in ["1week", "2weeks", "1month"]:
            human = human.replace("s", "s")
        await processing_msg.edit_text(
            f"✅ Boost activated!\n\n"
            f"Token: ${token_symbol} ({shorten_address(token_address)})\n"
            f"Duration: {human}\n"
            f"Expires: {expiration_time}\n\n"
            f"Your token is now boosted. It will lead the trending board and all buys > $20 will show in @moonbagstrending. 🚀"
        )
        # Clear boost context data
        for key in ['boost_token', 'boost_token_symbol', 'boost_duration', 'boost_cost', 'boost_seconds']:
            context.user_data.pop(key, None)
        # Announce new boost in trending channel
        await context.bot.send_message(
            TRENDING_CHANNEL,
            f"🔥 **NEW BOOST!** 🔥\n\n${token_symbol} has been boosted for {human}!\nAll buys will be featured here.",
            parse_mode="Markdown"
        )
        # If using WebSocket tracking, subscribe to this token (if not already tracked)
        if buy_stream.WS_URL:
            buy_stream.subscribe_queue.put(token_address)
    except Exception as e:
        logger.error(f"Error confirming boost: {e}")
        await processing_msg.edit_text("❌ An error occurred during verification. Please try again or contact support.")

def get_menu_keyboard():
    """Construct the inline keyboard for configuration menu (2 buttons per row)."""
    keyboard = [
        [InlineKeyboardButton("🔗 Track Token", callback_data="set_token"),
         InlineKeyboardButton("📉 Set Minimum Buy Size", callback_data="set_min_buy")],
        [InlineKeyboardButton("🎯 Choose Emoji Style", callback_data="set_emoji"),
         InlineKeyboardButton("🌐 Add Website", callback_data="set_website")],
        [InlineKeyboardButton("💬 Add Telegram Link", callback_data="set_telegram"),
         InlineKeyboardButton("❌ Add X (Twitter) Link", callback_data="set_twitter")],
        [InlineKeyboardButton("📷 Upload Media", callback_data="set_media"),
         InlineKeyboardButton("✅ Finish Setup", callback_data="finish_setup")]
    ]
    return InlineKeyboardMarkup(keyboard)

def save_group_settings(group_id: int, settings: dict) -> bool:
    """Save group token tracking settings into the database."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO groups
                (group_id, token_address, token_symbol, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                group_id,
                settings.get('token_address'),
                settings.get('token_symbol', 'TOKEN'),
                settings.get('min_buy_usd', 0),
                settings.get('emoji', '🔥'),
                settings.get('website'),
                settings.get('telegram_link'),
                settings.get('twitter_link'),
                settings.get('media_file_id')
            ))
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving group settings: {e}")
        return False

# --- Background jobs for trending and buy alerts ---

async def trend_alert(context: ContextTypes.DEFAULT_TYPE):
    """Send the trending top-10 tokens leaderboard to the trending channel."""
    try:
        # Gather all tracked tokens and active boosts
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT token_address FROM groups")
            tokens = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT token_address, expiration_timestamp FROM boosts WHERE expiration_timestamp > ?", (int(time.time()),))
            active_boosts = {row[0]: row[1] for row in cur.fetchall()}
        # Include boosted tokens not in any group
        for token in active_boosts:
            if token not in tokens:
                tokens.append(token)
        if not tokens:
            logger.info("No tokens being tracked; skipping trending update.")
            return
        # Fetch data for each token
        token_data = []
        for token in tokens:
            try:
                info = fetch_token_info(token)
                symbol = info.get('symbol', 'TOKEN')
                market_cap = info.get('market_cap', 0.0)
                price_change = info.get('price_change_30m', 0.0)
                # Calculate 30-min volume from stored buys
                cutoff = int(time.time()) - 1800
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT SUM(usd_value) FROM buys WHERE token_address = ? AND timestamp > ?", (token, cutoff))
                    volume = cur.fetchone()[0] or 0.0
                is_boosted = token in active_boosts
                boost_remaining = active_boosts[token] - int(time.time()) if is_boosted else 0
                token_data.append({
                    "symbol": symbol,
                    "market_cap": market_cap,
                    "price_change": price_change,
                    "volume_30m": volume,
                    "is_boosted": is_boosted,
                    "boost_remaining": boost_remaining
                })
            except Exception as e:
                logger.error(f"Error fetching data for {token}: {e}")
        if not token_data:
            logger.warning("No token data available for trending leaderboard.")
            return
        # Sort tokens: boosted first (with longer boost first), then by volume
        token_data.sort(key=lambda x: (-1 if x['is_boosted'] else 0, -x['boost_remaining'], -x['volume_30m']))
        top_tokens = token_data[:10]
        now = datetime.utcnow()
        leaderboard_text = f"🔥 **MOONBAGS TRENDING**: {now.strftime('%H:%M UTC')}\n\n"
        for i, token in enumerate(top_tokens, start=1):
            rank_emoji = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"][i-1]
            # Price change arrow
            if token['price_change'] > 0:
                change_str = f"📈 +{token['price_change']:.1f}%"
            elif token['price_change'] < 0:
                change_str = f"📉 {token['price_change']:.1f}%"
            else:
                change_str = "➖ 0%"
            # Market cap formatting
            market_cap = token['market_cap']
            if market_cap > 1_000_000:
                market_cap_str = f"${market_cap/1_000_000:.1f}M"
            else:
                market_cap_str = f"${market_cap/1000:.1f}K"
            # Boost label if applicable
            boost_label = ""
            if token['is_boosted']:
                hours_left = math.ceil(token['boost_remaining'] / 3600)
                boost_label = f" 🚀 BOOSTED ({hours_left}h)"
            leaderboard_text += (
                f"{rank_emoji} ${token['symbol']}{boost_label}\n"
                f"💰 Market Cap: {market_cap_str}\n"
                f"{change_str}\n\n"
            )
        leaderboard_text += "💎 *Your token not trending?* Boost it with /boost!"
        # Send trending leaderboard message
        msg = await context.bot.send_message(TRENDING_CHANNEL, leaderboard_text, parse_mode="Markdown")
        # Auto-pin the message every 30 minutes
        last_pin = context.bot_data.get('last_pin_time', 0)
        if time.time() - last_pin >= 1800:
            try:
                await context.bot.pin_chat_message(TRENDING_CHANNEL, msg.message_id)
                context.bot_data['last_pin_time'] = time.time()
            except Exception as e:
                logger.error(f"Failed to pin trending message: {e}")
    except Exception as e:
        logger.error(f"Error in trend_alert job: {e}")

async def check_buys(context: ContextTypes.DEFAULT_TYPE):
    """Fallback polling job: check for new buys via API if WebSocket is not used."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT token_address FROM groups")
            tokens = [row[0] for row in cur.fetchall()]
        if not tokens:
            return
        last_check = context.bot_data.get('last_check_timestamp', int(time.time()) - 60)
        current_time = int(time.time())
        context.bot_data['last_check_timestamp'] = current_time
        for token in tokens:
            buys = fetch_recent_buys(token, last_check)
            if not buys:
                continue
            for buy in buys:
                # Store in DB (ignore duplicates by tx_id)
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT OR IGNORE INTO buys (transaction_id, token_address, buyer_address, amount, usd_value, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (buy['tx_hash'], token, buy['buyer_address'], buy['amount'], buy['usd_value'], buy['timestamp']))
                    conn.commit()
                # Send alerts for this buy
                await send_buy_alerts(token, buy, context)
    except Exception as e:
        logger.error(f"Error in check_buys: {e}")

async def process_ws_events(context: ContextTypes.DEFAULT_TYPE):
    """Job to process incoming buy events from the WebSocket listener."""
    try:
        while not buy_stream.event_queue.empty():
            event = buy_stream.event_queue.get()
            # Parse the WebSocket event data for coin balance changes
            coin_type = event.get("coinType") or event.get("type")
            if not coin_type:
                continue  # skip if no coin type
            token_address = coin_type  # the Move token address string
            amount = event.get("amount", 0)
            if amount is None or float(amount) <= 0:
                continue  # skip non-positive changes (sells/transfers out)
            # Determine buyer address (owner) if present
            owner = event.get("owner")
            if isinstance(owner, dict):
                buyer_address = owner.get("AddressOwner") or owner.get("address") or ""
            else:
                buyer_address = str(owner) if owner else ""
            if not buyer_address.startswith("0x"):
                continue  # skip if owner is not a user address
            # Compute token amount and USD value
            token_amount = float(amount) / 1_000_000_000  # assume 10^9 decimals for token quantity
            info = fetch_token_info(token_address)
            price = info.get('price', 0.0)
            usd_value = token_amount * price
            buy_data = {
                "tx_hash": event.get("txDigest") or event.get("tx_digest") or "",
                "buyer_address": buyer_address,
                "amount": token_amount,
                "usd_value": usd_value,
                "timestamp": int(event.get("timestamp", 0) / 1000) if event.get("timestamp") else int(time.time())
            }
            # Insert into buys database (ignore if already exists)
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT OR IGNORE INTO buys (transaction_id, token_address, buyer_address, amount, usd_value, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (buy_data['tx_hash'], token_address, buyer_address, token_amount, usd_value, buy_data['timestamp']))
                conn.commit()
            # Send alerts for this buy event
            await send_buy_alerts(token_address, buy_data, context)
    except Exception as e:
        logger.error(f"Error processing WS events: {e}")

async def send_buy_alerts(token_address: str, buy: dict, context: ContextTypes.DEFAULT_TYPE):
    """Send buy alert messages to all groups tracking the token and possibly to trending channel."""
    # Fetch latest token info for formatting
    token_info = fetch_token_info(token_address)
    token_symbol = token_info.get('symbol', 'TOKEN')
    token_price = token_info.get('price', 0.0)
    token_market_cap = token_info.get('market_cap', 0.0)
    token_liquidity = token_info.get('liquidity', 0.0)
    usd_str = f"${buy['usd_value']:.2f}"
    amount_str = f"{buy['amount']:.4f}"
    # Get all group configurations for this token
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT group_id, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id
            FROM groups WHERE token_address = ?
        """, (token_address,))
        group_rows = cur.fetchall()
    for (group_id, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id) in group_rows:
        if buy['usd_value'] < float(min_buy_usd or 0):
            continue  # below this group's alert threshold
        # Compose alert message text with dynamic emoji count
        emoji_count = max(1, min(20, int(buy['usd_value'] / 5)))
        emojis = (emoji or "🔥") * emoji_count
        # Format market cap and liquidity for display
        market_cap_str = f"${token_market_cap/1000000:.2f}M" if token_market_cap > 1_000_000 else f"${token_market_cap/1000:.2f}K"
        liquidity_str = f"${token_liquidity/1000000:.2f}M" if token_liquidity > 1_000_000 else f"${token_liquidity/1000:.2f}K"
        alert_text = (
            f"{emojis} NEW BUY {emojis}\n\n"
            f"💰 {amount_str} ${token_symbol} (≈{usd_str})\n"
            f"🧠 Buyer: [{shorten_address(buy['buyer_address'])}](https://suivision.xyz/txblock/{buy['tx_hash']})\n\n"
            f"📊 ${token_symbol} Stats:\n"
            f"💲 Price: ${token_price:.8f}\n"
            f"💹 Market Cap: {market_cap_str}\n"
            f"💧 Liquidity: {liquidity_str}"
        )
        # Inline buttons: BUY + Trending, and optional links
        keyboard = []
        keyboard.append([
            InlineKeyboardButton(f"BUY ${token_symbol}", url=f"https://moonbags.io/tokens/{token_address}"),
            InlineKeyboardButton("🌕 Moonbags Trending", url=f"https://t.me/moonbagstrending")
        ])
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
        # Send alert to the group (photo if media configured, otherwise text)
        try:
            if media_file_id:
                await context.bot.send_photo(chat_id=group_id, photo=media_file_id, caption=alert_text,
                                             parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await context.bot.send_message(chat_id=group_id, text=alert_text,
                                               parse_mode="Markdown", reply_markup=reply_markup)
            logger.info(f"Sent alert to group {group_id} for ${token_symbol}")
        except Exception as e:
            logger.error(f"Failed to send alert to group {group_id}: {e}")
    # Determine if a trending channel alert is needed
    is_boosted = False
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM boosts WHERE token_address = ? AND expiration_timestamp > ?", (token_address, int(time.time())))
        if cur.fetchone():
            is_boosted = True
    if buy['usd_value'] >= 200 or (is_boosted and buy['usd_value'] >= 20):
        # Trending alert text (standard 🔥 emoji style)
        emoji_count = max(1, min(20, int(buy['usd_value'] / 5)))
        trending_emojis = "🔥" * emoji_count
        trending_text = (
            f"{trending_emojis} TRENDING BUY {trending_emojis}\n\n"
            f"💰 {amount_str} ${token_symbol} (≈{usd_str})\n"
            f"🧠 Buyer: [{shorten_address(buy['buyer_address'])}](https://suivision.xyz/txblock/{buy['tx_hash']})\n\n"
            f"📊 ${token_symbol} Stats:\n"
            f"💲 Price: ${token_price:.8f}\n"
            f"💹 Market Cap: {market_cap_str}\n"
            f"💧 Liquidity: {liquidity_str}"
        )
        # Inline buttons: BUY + Boost (deep-link to bot /boost command)
        trend_buttons = [[
            InlineKeyboardButton(f"BUY ${token_symbol}", url=f"https://moonbags.io/tokens/{token_address}"),
            InlineKeyboardButton("🚀 Boost This Token", url=f"https://t.me/{context.bot.username}?start=boost_{token_address}")
        ]]
        trend_markup = InlineKeyboardMarkup(trend_buttons)
        # Use the first available media (if any) from group settings for trending post
        media_id = None
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT media_file_id FROM groups WHERE token_address = ? AND media_file_id IS NOT NULL LIMIT 1", (token_address,))
            result = cur.fetchone()
            if result:
                media_id = result[0]
        try:
            if media_id:
                await context.bot.send_photo(chat_id=TRENDING_CHANNEL, photo=media_id,
                                             caption=trending_text, parse_mode="Markdown", reply_markup=trend_markup)
            else:
                await context.bot.send_message(chat_id=TRENDING_CHANNEL, text=trending_text,
                                               parse_mode="Markdown", reply_markup=trend_markup)
        except Exception as e:
            logger.error(f"Failed to send trending buy alert: {e}")

def main():
    # Start the health-check HTTP server in background
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    # Prepare initial token list for WebSocket subscription
    initial_tokens = []
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT token_address FROM groups")
        initial_tokens = [row[0] for row in cur.fetchall()]
    # Launch WebSocket listener thread for real-time buy events (if configured)
    if buy_stream.WS_URL:
        ws_thread = threading.Thread(target=buy_stream.start_ws_thread, args=(initial_tokens,), daemon=True)
        ws_thread.start()
        logger.info("WebSocket listener thread started.")
    # Create and configure the Telegram bot application
    application = Application.builder().token(BOT_TOKEN).build()
    # Conversation handler for /start setup flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING:    [CallbackQueryHandler(menu_handler)],
            INPUT_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            INPUT_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_buy)],
            INPUT_EMOJI:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_emoji)],
            INPUT_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_website)],
            INPUT_TELEGRAM:[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_telegram)],
            INPUT_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_twitter)],
            INPUT_MEDIA: [
                MessageHandler(filters.PHOTO | filters.ANIMATION, receive_media),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_media)
            ],
            BOOST_CONFIRM: [CommandHandler("confirm", confirm_boost)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(menu_handler))  # Global menu handler fix
    # Standalone command handlers
    application.add_handler(CommandHandler("boost", boost_command))
    application.add_handler(CallbackQueryHandler(boost_callback, pattern=r"^boost_"))
    application.add_handler(CommandHandler("confirm", confirm_boost))
    # Schedule background jobs
    job_queue = application.job_queue
    if buy_stream.WS_URL:
        job_queue.run_repeating(process_ws_events, interval=1, first=5)
    else:
        job_queue.run_repeating(check_buys, interval=30, first=5)
    job_queue.run_repeating(trend_alert, interval=300, first=60)
    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()
