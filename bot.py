import os
import logging
import time
import threading
import asyncio
import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
from sui import verify_payment  # Updated implementation
from utils import format_alert, shorten_address

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL")
RAIDEN_API = "https://api.raidenx.xyz/v1"
SUI_EXPLORER = "https://suiscan.xyz/mainnet/tx/"

# Conversation states
SETUP_GROUP, SETUP_TOKEN, SETUP_MIN, SETUP_EMOJI, SETUP_LINKS, SETUP_MEDIA = range(6)

# Boost options (duration in seconds)
BOOST_OPTIONS = {
    "4h": {"duration": 14400, "cost": 15},
    "8h": {"duration": 28800, "cost": 20},
    "12h": {"duration": 43200, "cost": 30},
    "24h": {"duration": 86400, "cost": 50},
    "48h": {"duration": 172800, "cost": 80},
    "72h": {"duration": 259200, "cost": 120},
    "1w": {"duration": 604800, "cost": 220},
    "2w": {"duration": 1209600, "cost": 410},
    "1m": {"duration": 2592000, "cost": 780}
}

def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (group_id INT PRIMARY KEY, token_address TEXT, min_buy REAL, 
                  emoji TEXT, website TEXT, telegram TEXT, twitter TEXT, media_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS boosts
                 (token_address TEXT PRIMARY KEY, expires INT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS leaderboard
                 (token_address TEXT, volume REAL, timestamp INT)''')
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == 'private':
        await update.message.reply_text("Please add me to a group first!")
        return ConversationHandler.END
        
    keyboard = [[InlineKeyboardButton("➡️ Continue in Private Chat", url=f"t.me/{context.bot.username}")]]
    await update.message.reply_text(
        "🚀 Welcome to Moonbags BuyBot!\n\n"
        "Please continue setup in private chat:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    context.user_data['setup_group'] = update.message.chat.id
    return SETUP_GROUP

async def setup_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Paste the token contract address:")
    return SETUP_TOKEN

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    if not token.startswith('0x'):
        await update.message.reply_text("❌ Invalid token address. Please try again:")
        return SETUP_TOKEN
    
    # Verify token exists on Raiden X
    try:
        response = requests.get(f"{RAIDEN_API}/token/{token}").json()
        if not response.get('exists'):
            raise ValueError("Token not found")
    except Exception as e:
        await update.message.reply_text("❌ Invalid token. Please verify and try again:")
        return SETUP_TOKEN
    
    context.user_data['token'] = token
    await update.message.reply_text("📉 Set minimum USD value for alerts:")
    return SETUP_MIN

async def receive_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        min_buy = float(update.message.text)
        context.user_data['min_buy'] = min_buy
        await update.message.reply_text("🎯 Choose alert emoji (e.g. 🔥):")
        return SETUP_EMOJI
    except:
        await update.message.reply_text("❌ Invalid amount. Please enter a number:")
        return SETUP_MIN

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['emoji'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("🌐 Add Website", callback_data='website'),
         InlineKeyboardButton("💬 Add Telegram", callback_data='telegram')],
        [InlineKeyboardButton("🐦 Add Twitter", callback_data='twitter'),
         InlineKeyboardButton("📷 Add Media", callback_data='media')],
        [InlineKeyboardButton("✅ Finish Setup", callback_data='finish')]
    ]
    await update.message.reply_text(
        "📝 Optional Links & Media:",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return SETUP_LINKS

async def save_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO groups VALUES (?,?,?,?,?,?,?,?)''',
              (data['setup_group'], data['token'], data['min_buy'], data['emoji'],
               data.get('website'), data.get('telegram'), data.get('twitter'), 
               data.get('media_id')))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Setup complete! The bot is now monitoring your token!")
    return ConversationHandler.END

async def check_buys(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT * FROM groups')
    groups = c.fetchall()
    
    for group in groups:
        response = requests.get(f"{RAIDEN_API}/transactions?token={group[1]}")
        if response.status_code == 200:
            for tx in response.json()['transactions']:
                if tx['valueUSD'] >= group[2]:
                    await send_alert(context, group, tx)
    
    # Update leaderboard
    update_leaderboard()
    await post_leaderboard(context)

async def send_alert(context, group, tx):
    message, markup, media = format_alert(tx, group[3], group[1], group[7])
    try:
        if media:
            await context.bot.send_photo(group[0], media, caption=message, reply_markup=markup)
        else:
            await context.bot.send_message(group[0], message, reply_markup=markup)
        
        if tx['valueUSD'] >= 200 or is_boosted(group[1]):
            await context.bot.send_message(TRENDING_CHANNEL, message, reply_markup=markup)
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")

async def boost_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for duration, info in BOOST_OPTIONS.items():
        keyboard.append([InlineKeyboardButton(
            f"{duration} - {info['cost']} SUI", 
            callback_data=f"boost_{duration}")])
    
    await update.message.reply_text(
        "💸 Buy Visibility Boost:\n\n"
        f"Receiver: {BOOST_RECEIVER}\n"
        "Select duration:",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def verify_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txn_hash = context.args[0]
    duration = context.args[1]
    if verify_payment(txn_hash, BOOST_OPTIONS[duration]['cost'], BOOST_RECEIVER):
        expires = int(time.time()) + BOOST_OPTIONS[duration]['duration']
        conn = sqlite3.connect('bot.db')
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO boosts VALUES (?,?)', 
                 (context.user_data['boost_token'], expires))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ Boost activated!")
    else:
        await update.message.reply_text("❌ Payment verification failed")

def update_leaderboard():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO leaderboard 
                 SELECT token_address, SUM(valueUSD), strftime('%s','now') 
                 FROM transactions 
                 WHERE timestamp > strftime('%s','now')-1800''')
    conn.commit()
    conn.close()

async def post_leaderboard(context):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''SELECT token_address, volume FROM leaderboard 
               ORDER BY volume DESC LIMIT 10''')
    top10 = c.fetchall()
    
    message = "🏆 Top Trending Tokens (Last 30 mins):\n\n"
    for idx, (token, vol) in enumerate(top10):
        message += f"{idx+1}. {shorten_address(token)} - ${vol:,.2f}\n"
    
    await context.bot.send_message(TRENDING_CHANNEL, message)
    await context.bot.pin_chat_message(TRENDING_CHANNEL, message.message_id)

def main():
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Setup conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SETUP_GROUP: [MessageHandler(filters.CHAT_PRIVATE, setup_token)],
            SETUP_TOKEN: [MessageHandler(filters.TEXT, receive_token)],
            SETUP_MIN: [MessageHandler(filters.TEXT, receive_min)],
            SETUP_EMOJI: [MessageHandler(filters.TEXT, receive_emoji)],
            SETUP_LINKS: [CallbackQueryHandler(handle_links)],
            SETUP_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, receive_media)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('boost', boost_menu))
    application.add_handler(CommandHandler('confirm', verify_boost))
    application.job_queue.run_repeating(check_buys, interval=300, first=10)
    application.job_queue.run_repeating(post_leaderboard, interval=1800)
    
    application.run_polling()

if __name__ == '__main__':
    main()
