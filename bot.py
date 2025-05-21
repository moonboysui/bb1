import os
import logging
import asyncio
import sqlite3
import time
import re
from datetime import datetime, timedelta
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaAnimation
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from aiohttp import web
from database import init_db, get_db, save_group_settings, clear_fake_symbols
from utils import validate_sui_address, shorten_address, format_alert
from sui_api import fetch_token_info, fetch_recent_buys, verify_payment

# Initialize database and logging
init_db()
clear_fake_symbols()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "7551845767:AAF3UOQ4E0o33Bsd-0PBAlOLcifZU-1gT00")
    BOOST_WALLET = os.getenv("BOOST_WALLET", "0x7338ef163ee710923803cb0dd60b5b02cddc5fbafef417342e1bbf1fba20e702")
    TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
    PORT = int(os.getenv("PORT", 8080))
    SUI_EXPLORER = "https://suiscan.xyz/mainnet/tx"

# Conversation states
(
    SETUP_GROUP, SET_TOKEN, SET_EMOJI, SET_MIN_BUY,
    SET_WEBSITE, SET_TELEGRAM, SET_TWITTER, SET_CHART,
    SET_MEDIA, BOOST_SELECT, BOOST_CONFIRM
) = range(11)

BOOST_OPTIONS = {
    "4h": {"duration": 14400, "cost": 15},
    "8h": {"duration": 28800, "cost": 20},
    "12h": {"duration": 43200, "cost": 27},
    "24h": {"duration": 86400, "cost": 45},
    "48h": {"duration": 172800, "cost": 80},
    "72h": {"duration": 259200, "cost": 110},
    "1week": {"duration": 604800, "cost": 180}
}

# Web server for Render health checks
async def health_check(request):
    return web.Response(text="OK")

async def run_web_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    logger.info(f"Health check server running on port {Config.PORT}")

# ======================
# CORE BOT FUNCTIONALITY
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type in ['group', 'supergroup']:
        user = update.effective_user
        admins = await context.bot.get_chat_administrators(update.message.chat.id)
        if not any(admin.user.id == user.id for admin in admins):
            await update.message.reply_text("❌ Only admins can configure!")
            return ConversationHandler.END

        context.user_data['group'] = {
            'id': update.message.chat.id,
            'title': update.message.chat.title
        }

        await update.message.reply_text(
            "⚙️ Continue setup in private chat:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Start Setup", url=f"https://t.me/{context.bot.username}")
            ]])
        )
        return SETUP_GROUP
    else:
        await show_setup_menu(update, context)
        return SET_TOKEN

async def show_setup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_data = context.user_data.get('group', {})
    text = (
        f"⚙️ Configuring {group_data.get('title', 'your group')}\n\n"
        f"Token: {shorten_address(group_data.get('token_address', 'Not set'))}\n"
        f"Min Buy: ${group_data.get('min_buy', 0):.2f}\n"
        f"Emoji: {group_data.get('emoji', '🔥')} "
        f"(per ${group_data.get('emoji_step', 5):.2f})\n"
        f"Media: {'✅ Set' if group_data.get('media_id') else '❌ Not set'}"
    )
    
    keyboard = [
        [InlineKeyboardButton("Set Token", callback_data="set_token"),
         InlineKeyboardButton("Set Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Min Buy", callback_data="set_min"),
         InlineKeyboardButton("Set Links", callback_data="set_links")],
        [InlineKeyboardButton("Set Media", callback_data="set_media"),
         InlineKeyboardButton("Finish", callback_data="finish_setup")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "set_token":
        await query.edit_message_text("🔗 Enter SUI token address (0x...::module::type):")
        return SET_TOKEN
        
    elif query.data == "set_emoji":
        await query.edit_message_text("🎨 Send emoji and $ step (e.g., '🔥 5'):")
        return SET_EMOJI
        
    elif query.data == "set_min":
        await query.edit_message_text("💰 Enter minimum USD value for alerts:")
        return SET_MIN_BUY
        
    elif query.data == "set_links":
        await query.edit_message_text("🔗 Choose link to set:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Website", callback_data="set_website"),
             InlineKeyboardButton("Telegram", callback_data="set_telegram")],
            [InlineKeyboardButton("Twitter", callback_data="set_twitter"),
             InlineKeyboardButton("Chart", callback_data="set_chart")],
            [InlineKeyboardButton("Back", callback_data="back")]
        ]))
        return SET_WEBSITE
        
    elif query.data == "set_media":
        await query.edit_message_text("📷 Send photo/GIF or type 'skip':")
        return SET_MEDIA
        
    elif query.data == "finish_setup":
        return await finish_setup(update, context)
        
    elif query.data == "back":
        return await show_setup_menu(update, context)
        
    return ConversationHandler.END

async def handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token_address = update.message.text.strip()
    if not validate_sui_address(token_address):
        await update.message.reply_text("❌ Invalid SUI token format!")
        return SET_TOKEN
    
    try:
        token_info = fetch_token_info(token_address)
        context.user_data['group'].update({
            'token_address': token_address,
            'token_symbol': token_info.get('symbol', 'TOKEN')
        })
        await update.message.reply_text(
            f"✅ Token set: {token_info['symbol']}\n"
            f"Price: ${token_info['price']:.6f}\n"
            f"Market Cap: ${token_info['market_cap']/1000:.2f}K"
        )
    except Exception as e:
        logger.error(f"Token error: {e}")
        await update.message.reply_text("❌ Failed to fetch token info!")
        return SET_TOKEN
    
    return await show_setup_menu(update, context)

async def handle_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split()
        emoji = parts[0]
        step = float(parts[1])
        context.user_data['group'].update({
            'emoji': emoji,
            'emoji_step': step
        })
        await update.message.reply_text(f"✅ Set {emoji} per ${step}")
    except:
        await update.message.reply_text("❌ Invalid format! Use: 'EMOJI AMOUNT'")
        return SET_EMOJI
    return await show_setup_menu(update, context)

async def handle_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        context.user_data['group']['min_buy'] = amount
        await update.message.reply_text(f"✅ Min buy set to ${amount:.2f}")
    except:
        await update.message.reply_text("❌ Invalid amount!")
        return SET_MIN_BUY
    return await show_setup_menu(update, context)

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.lower() == 'skip':
        context.user_data['group']['media_id'] = None
        await update.message.reply_text("✅ Media skipped")
    else:
        if update.message.photo:
            media_id = update.message.photo[-1].file_id
        elif update.message.animation:
            media_id = update.message.animation.file_id
        else:
            await update.message.reply_text("❌ Unsupported media type!")
            return SET_MEDIA
        context.user_data['group']['media_id'] = media_id
        await update.message.reply_text("✅ Media saved!")
    return await show_setup_menu(update, context)

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group = context.user_data['group']
    required = ['token_address', 'min_buy', 'emoji']
    missing = [field for field in required if field not in group]
    
    if missing:
        await update.message.reply_text(f"❌ Missing: {', '.join(missing)}")
        return await show_setup_menu(update, context)
    
    try:
        save_group_settings(group['id'], group)
        await context.bot.send_message(
            group['id'],
            f"✅ Setup complete! Tracking {group['token_symbol']} "
            f"with min ${group['min_buy']:.2f} buys"
        )
        context.user_data.clear()
    except Exception as e:
        logger.error(f"Setup error: {e}")
        await update.message.reply_text("❌ Setup failed!")
    return ConversationHandler.END

# ======================
# BOOST SYSTEM
# ======================

async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /boost <token_address>")
        return
    
    token_address = ' '.join(context.args)
    if not validate_sui_address(token_address):
        await update.message.reply_text("❌ Invalid token address!")
        return
    
    context.user_data['boost'] = {'token_address': token_address}
    
    keyboard = [
        [InlineKeyboardButton(f"{dur} - {details['cost']} SUI", 
         callback_data=f"boost_{dur}")] 
        for dur, details in BOOST_OPTIONS.items()
    ]
    
    await update.message.reply_text(
        "💰 Select boost duration:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return BOOST_SELECT

async def handle_boost_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    duration = query.data.split('_')[1]
    
    boost_data = BOOST_OPTIONS[duration]
    context.user_data['boost'].update({
        'duration': boost_data['duration'],
        'cost': boost_data['cost'],
        'expires': int(time.time()) + boost_data['duration']
    })
    
    await query.edit_message_text(
        f"Send {boost_data['cost']} SUI to:\n"
        f"`{Config.BOOST_WALLET}`\n\n"
        "After payment, reply with:\n"
        "/confirm <transaction_hash>"
    )
    return BOOST_CONFIRM

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Missing transaction hash!")
        return
    
    tx_hash = context.args[0]
    boost_data = context.user_data.get('boost', {})
    
    try:
        if await verify_payment(tx_hash, boost_data['cost'], Config.BOOST_WALLET):
            with get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO boosts VALUES (?, ?, ?)",
                    (boost_data['token_address'], boost_data['expires'], 1)
                )
                conn.commit()
            
            await update.message.reply_text(
                f"✅ Boost active until: "
                f"{datetime.fromtimestamp(boost_data['expires']).strftime('%Y-%m-%d %H:%M UTC')}"
            )
            
            token_info = fetch_token_info(boost_data['token_address'])
            await context.bot.send_message(
                Config.TRENDING_CHANNEL,
                f"🚀 BOOST ACTIVATED!\n{token_info['symbol']} "
                f"({shorten_address(boost_data['token_address'])}) "
                f"for {human_duration(boost_data['duration'])}"
            )
        else:
            await update.message.reply_text("❌ Payment verification failed!")
    except Exception as e:
        logger.error(f"Boost error: {e}")
        await update.message.reply_text("❌ Boost failed!")
    
    context.user_data.clear()
    return ConversationHandler.END

# ======================
# BUY MONITORING & ALERTS
# ======================

async def check_buys(context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM groups")
            groups = cursor.fetchall()
            
            for group in groups:
                buys = fetch_recent_buys(group['token_address'])
                for buy in buys:
                    if buy['usd_value'] >= group['min_buy']:
                        await send_alert(context, group, buy)
                        cursor.execute(
                            "INSERT OR IGNORE INTO buys VALUES (?, ?, ?, ?, ?, ?)",
                            (buy['tx_hash'], group['token_address'], buy['buyer'],
                             buy['amount'], buy['usd_value'], buy['timestamp'])
                        )
                        conn.commit()
    except Exception as e:
        logger.error(f"Buy check error: {e}")

async def send_alert(context, group, buy):
    try:
        token_info = fetch_token_info(group['token_address'])
        alert_text = format_alert(buy, token_info, group)
        keyboard = [
            [InlineKeyboardButton("Buy Now", url=group['chart_link'])],
            [InlineKeyboardButton("Trending", url=f"t.me/{Config.TRENDING_CHANNEL}")]
        ]
        
        if group['media_id']:
            await context.bot.send_photo(
                chat_id=group['group_id'],
                photo=group['media_id'],
                caption=alert_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=group['group_id'],
                text=alert_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        
        if buy['usd_value'] >= 200 or is_boosted(group['token_address']):
            await post_trending(context, group, buy, token_info)
    except Exception as e:
        logger.error(f"Alert error: {e}")


# ======================
# TRENDING SYSTEM
# ======================

def is_boosted(token_address: str) -> bool:
    """Check if a token is currently boosted"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT expires FROM boosts WHERE token_address = ? AND expires > ?",
            (token_address, int(time.time()))
        )
        return cursor.fetchone() is not None

def human_duration(seconds: int) -> str:
    """Convert seconds to human-readable duration"""
    periods = [
        ('week', 604800),
        ('day', 86400),
        ('hour', 3600),
        ('minute', 60)
    ]
    result = []
    for name, period in periods:
        value = seconds // period
        if value:
            seconds -= value * period
            result.append(f"{value} {name}{'s' if value != 1 else ''}")
    return " ".join(result[:2]) if result else "less than a minute"

async def post_trending(context: ContextTypes.DEFAULT_TYPE, group, buy, token_info):
    """Post boosted buy to trending channel"""
    try:
        boosted = is_boosted(group['token_address'])
        price_change = token_info.get('price_change_24h', 0)
        
        message = (
            f"🚀 **{token_info['symbol']}** ({shorten_address(group['token_address'])})\n"
            f"💵 Price: ${token_info['price']:.6f} ({price_change:+.2f}%)\n"
            f"📈 MC: ${token_info['market_cap']/1000:.2f}K\n"
            f"🔥 Recent Buy: ${buy['usd_value']:.2f}\n"
            f"🕒 {human_duration(time.time() - buy['timestamp'])} ago\n"
        )
        
        if boosted:
            message += "\n⭐ BOOSTED LISTING ⭐\n"
            
        buttons = []
        if group.get('chart_link'):
            buttons.append(InlineKeyboardButton("📊 Chart", url=group['chart_link']))
        if group.get('website'):
            buttons.append(InlineKeyboardButton("🌐 Website", url=group['website']))
        if group.get('telegram'):
            buttons.append(InlineKeyboardButton("💬 Telegram", url=group['telegram']))
        if group.get('twitter'):
            buttons.append(InlineKeyboardButton("🐦 Twitter", url=group['twitter']))
            
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        
        if group.get('media_id'):
            await context.bot.send_photo(
                Config.TRENDING_CHANNEL,
                photo=group['media_id'],
                caption=message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                Config.TRENDING_CHANNEL,
                text=message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Trending post error: {e}")

# ======================
# HANDLER SETUP & MAIN
# ======================

def main() -> None:
    """Start the bot"""
    application = Application.builder().token(Config.BOT_TOKEN).build()

    # Setup conversation handler for group configuration
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_token)],
            SET_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_emoji)],
            SET_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_min_buy)],
            SET_MEDIA: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.TEXT, handle_media)],
            SET_WEBSITE: [CallbackQueryHandler(handle_link_setup)],
            BOOST_SELECT: [CallbackQueryHandler(handle_boost_selection)],
            BOOST_CONFIRM: [CommandHandler("confirm", confirm_boost)]
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        allow_reentry=True
    )

    # Register handlers
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_button_click))
    application.add_handler(CommandHandler("boost", boost_command))
    
    # Setup periodic tasks
    job_queue = application.job_queue
    job_queue.run_repeating(check_buys, interval=60, first=10)
    
    # Start services
    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())
    
    # Run bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
