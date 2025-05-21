# bot.py - Complete Implementation
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
from config import Config
from database import init_db, get_db, save_group
from utils import validate_sui_address, shorten_address, format_alert, format_links
from sui_api import fetch_token_info, fetch_recent_buys, verify_payment

# Initialize database and logging
init_db()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(
    SETUP_GROUP, SET_TOKEN, SET_EMOJI, SET_MIN_BUY,
    SET_WEBSITE, SET_TELEGRAM, SET_TWITTER, SET_CHART,
    SET_MEDIA, BOOST_SELECT, BOOST_CONFIRM
) = range(11)

BOOST_OPTIONS = {
    "4h": (14400, 15),
    "8h": (28800, 20),
    "12h": (43200, 27),
    "24h": (86400, 45),
    "48h": (172800, 80),
    "72h": (259200, 110),
    "1week": (604800, 180)
}

# ---------------------------
# Helper Functions
# ---------------------------

def get_setup_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Set Token", callback_data="set_token"),
         InlineKeyboardButton("Set Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Min Buy", callback_data="set_min"),
         InlineKeyboardButton("Set Links", callback_data="set_links")],
        [InlineKeyboardButton("Set Media", callback_data="set_media"),
         InlineKeyboardButton("Finish", callback_data="finish_setup")]
    ])

def get_links_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Website", callback_data="set_website"),
         InlineKeyboardButton("Telegram", callback_data="set_telegram")],
        [InlineKeyboardButton("Twitter/X", callback_data="set_twitter"),
         InlineKeyboardButton("Chart Link", callback_data="set_chart")],
        [InlineKeyboardButton("Back", callback_data="back_setup")]
    ])

def is_boosted(token_address):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT expiration FROM boosts WHERE token_address = ?", (token_address,))
        result = cursor.fetchone()
        return result and result[0] > time.time()

def human_duration(seconds):
    intervals = (
        ('week', 604800),
        ('day', 86400),
        ('hour', 3600),
        ('minute', 60)
    )
    for name, count in intervals:
        value = seconds // count
        if value > 0:
            return f"{int(value)} {name}{'s' if value > 1 else ''}"
    return "unknown"

# ---------------------------
# Command Handlers
# ---------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type in ['group', 'supergroup']:
        # Admin verification
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
        "Current Settings:\n"
        f"• Token: {group_data.get('token_symbol', 'Not set')} "
        f"({shorten_address(group_data.get('token_address', ''))})\n"
        f"• Min Buy: ${group_data.get('min_buy', 0):.2f}\n"
        f"• Emoji: {group_data.get('emoji', '🔥')} "
        f"(step: ${group_data.get('emoji_step', 5):.2f})\n"
        f"• Media: {'Set' if group_data.get('media_id') else 'Not set'}"
    )
    await update.message.reply_text(text, reply_markup=get_setup_keyboard())

async def set_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text(
        "🔗 Enter full SUI token address:\n"
        "Format: 0x...::module::type\n"
        "Example:\n"
        "0x7b888393d6a552819bb0a7f878183abaf04550bfb9546b20ea586d338210826f::moon::MOON"
    )
    return SET_TOKEN

async def save_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await show_setup_menu(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Token error: {e}")
        await update.message.reply_text("❌ Failed to verify token!")
        return SET_TOKEN

async def set_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("💰 Enter minimum USD value for alerts:")
    return SET_MIN_BUY

async def save_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        min_buy = float(update.message.text)
        context.user_data['group']['min_buy'] = min_buy
        await update.message.reply_text(f"✅ Min buy set: ${min_buy:.2f}")
        await show_setup_menu(update, context)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Invalid amount!")
        return SET_MIN_BUY

async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🎨 Send emoji and $ value per emoji (e.g.: '🔥 5')")
    return SET_EMOJI

async def save_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split()
        emoji = parts[0]
        step = float(parts[1])
        context.user_data['group'].update({
            'emoji': emoji,
            'emoji_step': step
        })
        await update.message.reply_text(f"✅ Set {emoji} per ${step}")
        await show_setup_menu(update, context)
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ Invalid format! Use: 'EMOJI AMOUNT'")
        return SET_EMOJI

async def set_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🔗 Configure links:", reply_markup=get_links_keyboard())
    return SET_WEBSITE

async def save_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    website = update.message.text.strip()
    if not website.startswith(('http://', 'https://')):
        website = f'https://{website}'
    context.user_data['group']['website'] = website
    await update.message.reply_text("✅ Website saved!")
    await show_setup_menu(update, context)
    return ConversationHandler.END

async def save_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['group']['telegram'] = update.message.text.strip()
    await update.message.reply_text("✅ Telegram saved!")
    await show_setup_menu(update, context)
    return ConversationHandler.END

async def save_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['group']['twitter'] = update.message.text.strip()
    await update.message.reply_text("✅ Twitter/X saved!")
    await show_setup_menu(update, context)
    return ConversationHandler.END

async def save_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chart_link = update.message.text.strip()
    if not chart_link.startswith(('http://', 'https://')):
        chart_link = f'https://{chart_link}'
    context.user_data['group']['chart_link'] = chart_link
    await update.message.reply_text("✅ Chart link saved!")
    await show_setup_menu(update, context)
    return ConversationHandler.END

async def set_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("📷 Send photo/GIF or type 'skip'")
    return SET_MEDIA

async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text and update.message.text.lower() == 'skip':
        context.user_data['group']['media_id'] = None
        await update.message.reply_text("✅ Media skipped")
    else:
        if update.message.photo:
            media_id = update.message.photo[-1].file_id
        elif update.message.animation:
            media_id = update.message.animation.file_id
        else:
            await update.message.reply_text("❌ Unsupported media!")
            return SET_MEDIA
        context.user_data['group']['media_id'] = media_id
        await update.message.reply_text("✅ Media saved!")
    await show_setup_menu(update, context)
    return ConversationHandler.END

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_data = context.user_data['group']
    try:
        save_group(group_data['id'], group_data)
        await context.bot.send_message(
            group_data['id'],
            f"✅ Setup complete! Tracking {group_data['token_symbol']} "
            f"(Buys > ${group_data['min_buy']:.2f})"
        )
        await update.callback_query.edit_message_text("✅ Setup done!")
        context.user_data.clear()
    except Exception as e:
        logger.error(f"Setup error: {e}")
        await update.callback_query.edit_message_text("❌ Setup failed!")
    return ConversationHandler.END

async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /boost 0x...::module::type")
        return
    
    token_address = ' '.join(context.args)
    if not validate_sui_address(token_address):
        await update.message.reply_text("❌ Invalid token address!")
        return
    
    context.user_data['boost'] = {'token_address': token_address}
    
    keyboard = [
        [InlineKeyboardButton(f"{dur} - {cost} SUI", callback_data=f"boost_{dur}")]
        for dur, (sec, cost) in BOOST_OPTIONS.items()
    ]
    
    await update.message.reply_text(
        "💰 Select boost duration:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return BOOST_SELECT

async def boost_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    duration = query.data.split('_')[1]
    sec, cost = BOOST_OPTIONS[duration]
    
    context.user_data['boost'].update({
        'duration': sec,
        'cost': cost,
        'expires': int(time.time()) + sec
    })
    
    await query.edit_message_text(
        f"Send {cost} SUI to:\n`{Config.BOOST_WALLET}`\n"
        "Then reply with: /confirm TX_HASH"
    )
    return BOOST_CONFIRM

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Missing TX hash!")
        return
    
    tx_hash = context.args[0]
    boost_data = context.user_data.get('boost')
    
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
            
            # Post to trending
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

async def check_buys(context: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM groups")
        groups = cursor.fetchall()
        
        for group in groups:
            try:
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
        alert = format_alert(buy, token_info, group)
        keyboard = [
            [InlineKeyboardButton("Buy Now", url=group['chart_link'])],
            [InlineKeyboardButton("Trending", url=f"t.me/{Config.TRENDING_CHANNEL}")]
        ]
        
        if group['media_id']:
            await context.bot.send_photo(
                chat_id=group['group_id'],
                photo=group['media_id'],
                caption=alert,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=group['group_id'],
                text=alert,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        
        # Post to trending if needed
        if buy['usd_value'] >= 200 or is_boosted(group['token_address']):
            await post_trending(context, group, buy, token_info)
    except Exception as e:
        logger.error(f"Alert error: {e}")

async def post_trending(context, group, buy, token_info):
    try:
        alert_text = (
            f"🚀 BIG BUY ALERT\n\n"
            f"{token_info['symbol']} ({shorten_address(group['token_address'])})\n"
            f"Amount: ${buy['usd_value']:.2f}\n"
            f"Buyer: {shorten_address(buy['buyer'])}\n"
            f"Tx: {Config.SUI_EXPLORER}/{buy['tx_hash']}"
        )
        await context.bot.send_message(
            Config.TRENDING_CHANNEL,
            alert_text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Trending post error: {e}")

async def update_trending_board(context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get 30min volumes
            cursor.execute("""
                SELECT token_address, SUM(usd_value) as volume 
                FROM buys 
                WHERE timestamp > ?
                GROUP BY token_address
            """, (int(time.time()) - 1800,))
            volumes = {row[0]: row[1] for row in cursor.fetchall()}
            
                 # Get active boosts
            cursor.execute("SELECT token_address, boost_level FROM boosts WHERE expiration > ?", 
                          (int(time.time()),))
            boosts = {row[0]: row[1] for row in cursor.fetchall()}

        # Calculate scores with boost multipliers
        ranked_tokens = []
        for token_address, volume in volumes.items():
            boost = boosts.get(token_address, 1)
            score = volume * boost
            ranked_tokens.append({
                'address': token_address,
                'volume': volume,
                'boost': boost,
                'score': score
            })

        # Sort by score descending
        ranked_tokens.sort(key=lambda x: x['score'], reverse=True)
        top_tokens = ranked_tokens[:10]

        # Build leaderboard message
        message = "🏆 MOONBAGS TRENDING LEADERBOARD 🏆\n\n"
        emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        for idx, token in enumerate(top_tokens):
            token_info = fetch_token_info(token['address'])
            boost_text = f" (🚀 Boost x{token['boost']})" if token['boost'] > 1 else ""
            message += (
                f"{emojis[idx]} ${token_info['symbol']}{boost_text}\n"
                f"   Volume (30m): ${token['volume']:.2f}\n"
                f"   Price: ${token_info['price']:.6f}\n"
                f"   Change: {token_info['price_change_30m']:.2f}%\n\n"
            )

        message += f"\nUpdated: {datetime.utcnow().strftime('%H:%M UTC')}"
        
        # Send and pin new leaderboard
        try:
            sent_message = await context.bot.send_message(
                chat_id=Config.TRENDING_CHANNEL,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            
            # Unpin old message and pin new
            await context.bot.unpin_all_chat_messages(Config.TRENDING_CHANNEL)
            await context.bot.pin_chat_message(
                chat_id=Config.TRENDING_CHANNEL,
                message_id=sent_message.message_id
            )
        except Exception as e:
            logger.error(f"Leaderboard posting failed: {e}")

    except Exception as e:
        logger.error(f"Trending board error: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Operation cancelled")
    return ConversationHandler.END

def main():
    application = Application.builder().token(Config.BOT_TOKEN).build()

    # Setup conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SETUP_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, start)],
            SET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_token)],
            SET_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_emoji)],
            SET_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_min_buy)],
            SET_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_website)],
            SET_TELEGRAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_telegram)],
            SET_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_twitter)],
            SET_CHART: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_chart)],
            SET_MEDIA: [
                MessageHandler(filters.PHOTO | filters.ANIMATION, save_media),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_media)
            ],
            BOOST_SELECT: [CallbackQueryHandler(boost_selected, pattern=r"^boost_")],
            BOOST_CONFIRM: [CommandHandler('confirm', confirm_boost)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('boost', boost_command))

    # Set up periodic tasks
    job_queue = application.job_queue
    job_queue.run_repeating(check_buys, interval=30, first=10)
    job_queue.run_repeating(update_trending_board, interval=1800, first=60)

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()
