import os
import logging
import time
import threading
import asyncio
import json
from datetime import datetime, timedelta
import requests

# Import web for aiohttp server
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

from database import init_db, get_db # Ensure get_db is also imported
from utils import shorten_address, format_alert, format_leaderboard_entry
from sui_api import (
    verify_payment,
    fetch_token_info,
    get_token_symbol,
    fetch_sui_price,
    fetch_recent_buys_from_queue, # New function to get buys from WebSocket
    _buy_event_queue, # Directly access the queue for internal logic if needed
    start_sui_event_listener # To start the WebSocket listener
)

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "7551845767:AAF3UOQ4E0o33Bsd-0PBAlOLcifZU-1gT00")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0x7338ef163ee710923803cb0dd60b5b02cddc5fbafef417342e1bbf1fba20e702")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "-1002008899889") # Example: use channel ID or username
PORT = int(os.getenv("PORT", 8080))

# Initialize database
init_db()

# Conversation states for setup
(
    INPUT_TOKEN_ADDRESS,
    INPUT_MIN_BUY,
    INPUT_EMOJI,
    INPUT_BUY_STEP,
    INPUT_WEBSITE,
    INPUT_TELEGRAM,
    INPUT_TWITTER,
    INPUT_MEDIA,
) = range(8)

# Conversation states for boost
(
    BOOST_TOKEN_ADDRESS,
    BOOST_AMOUNT,
    BOOST_CONFIRM,
) = range(8, 11) # Continue range from previous states

# --- Helper Functions ---
async def get_group_settings(group_id):
    """Retrieve group settings from the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        columns = [description[0] for description in cursor.description]
        return dict(zip(columns, row))
    return None

async def update_group_settings(group_id, settings):
    """Update group settings in the database."""
    conn = get_db()
    cursor = conn.cursor()
    # Construct SET clause dynamically
    set_clauses = [f"{key} = ?" for key in settings.keys()]
    query = f"UPDATE groups SET {', '.join(set_clauses)} WHERE group_id = ?"
    values = list(settings.values()) + [group_id]
    cursor.execute(query, values)
    conn.commit()
    conn.close()

async def add_group(group_id, token_address, token_symbol="TOKEN"):
    """Add a new group to the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO groups (group_id, token_address, token_symbol) VALUES (?, ?, ?)",
        (group_id, token_address, token_symbol),
    )
    conn.commit()
    conn.close()

async def get_all_configured_groups():
    """Retrieve all configured groups from the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT group_id, token_address, token_symbol, min_buy_usd, emoji, buy_step, website, telegram_link, twitter_link, media_file_id FROM groups")
    rows = cursor.fetchall()
    conn.close()
    groups = []
    columns = [description[0] for description in cursor.description]
    for row in rows:
        groups.append(dict(zip(columns, row)))
    return groups

async def add_buy_to_db(buy_data):
    """Add a detected buy to the database to prevent duplicate alerts."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO buys (transaction_id, token_address, buyer_address, amount, usd_value, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (
                buy_data["transaction_id"],
                buy_data["token_address"],
                buy_data["buyer_address"],
                buy_data["amount"],
                buy_data["usd_value"],
                buy_data["timestamp"],
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        logger.info(f"Duplicate buy transaction_id: {buy_data['transaction_id']}. Skipping.")
        return False
    finally:
        conn.close()

async def get_boost_status(token_address):
    """Check if a token is currently boosted."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT expiration_timestamp FROM boosts WHERE token_address = ?", (token_address,))
    row = cursor.fetchone()
    conn.close()
    if row:
        expiration_timestamp = row[0]
        return expiration_timestamp > int(time.time())
    return False

async def add_boost(token_address, duration_seconds):
    """Add or update a token boost."""
    conn = get_db()
    cursor = conn.cursor()
    expiration_timestamp = int(time.time()) + duration_seconds
    cursor.execute(
        "INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp) VALUES (?, ?)",
        (token_address, expiration_timestamp),
    )
    conn.commit()
    conn.close()

# --- Conversation Handlers (Bot Setup) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation, redirects private for setup."""
    if update.message.chat.type != "private":
        # Store group_id for later setup
        context.user_data["group_id"] = update.effective_chat.id
        await update.message.reply_text(
            "Please configure the bot in a private chat by sending me /start. "
            "Make sure I'm an admin in this group first!"
        )
        return ConversationHandler.END
    
    # If in private chat
    await update.message.reply_text(
        "Welcome! Let's set up your bot for a group. "
        "First, what is the token address you want to track on Sui?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return INPUT_TOKEN_ADDRESS

async def receive_token_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the token address and fetches its symbol."""
    token_address = update.message.text.strip()
    
    # Basic validation (could be more robust)
    if not token_address.startswith("0x") or len(token_address) < 40: # Sui addresses are long
        await update.message.reply_text("That doesn't look like a valid Sui token address. Please try again.")
        return INPUT_TOKEN_ADDRESS
    
    context.user_data["temp_token_address"] = token_address
    token_info = sui_api.fetch_token_info(token_address)
    token_symbol = token_info.get("symbol", "TOKEN")
    context.user_data["temp_token_symbol"] = token_symbol

    await update.message.reply_text(
        f"Token symbol for {token_address} is: {token_symbol}. "
        "What is the minimum buy amount in USD you want the bot to alert for? (e.g., 5.00 for $5)",
    )
    return INPUT_MIN_BUY

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the minimum buy amount."""
    try:
        min_buy_usd = float(update.message.text.strip())
        if min_buy_usd < 0:
            raise ValueError
        context.user_data["temp_min_buy_usd"] = min_buy_usd
        await update.message.reply_text(
            "What emoji would you like to use for buy alerts? (e.g., 🔥)"
        )
        return INPUT_EMOJI
    except ValueError:
        await update.message.reply_text("Please enter a valid number for the minimum buy amount.")
        return INPUT_MIN_BUY

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the emoji for alerts."""
    emoji = update.message.text.strip()
    if not emoji:
        await update.message.reply_text("Please provide an emoji.")
        return INPUT_EMOJI
    context.user_data["temp_emoji"] = emoji
    await update.message.reply_text(
        "Set the buy step for emojis (emojis per $ buy). "
        "For example, '1' for one emoji per $1, or '5' for one emoji per $5 buy."
    )
    return INPUT_BUY_STEP

async def receive_buy_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the buy step for emoji calculation."""
    try:
        buy_step = float(update.message.text.strip())
        if buy_step <= 0:
            raise ValueError
        context.user_data["temp_buy_step"] = buy_step
        await update.message.reply_text(
            "Optional: Enter the token's website URL or type 'skip'."
        )
        return INPUT_WEBSITE
    except ValueError:
        await update.message.reply_text("Please enter a valid positive number for the buy step.")
        return INPUT_BUY_STEP

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the token's website URL."""
    website = update.message.text.strip()
    context.user_data["temp_website"] = website if website.lower() != "skip" else None
    await update.message.reply_text(
        "Optional: Enter the token's Telegram group URL or type 'skip'."
    )
    return INPUT_TELEGRAM

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the token's Telegram URL."""
    telegram = update.message.text.strip()
    context.user_data["temp_telegram"] = telegram if telegram.lower() != "skip" else None
    await update.message.reply_text(
        "Optional: Enter the token's X (Twitter) URL or type 'skip'."
    )
    return INPUT_TWITTER

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the token's X (Twitter) URL."""
    twitter = update.message.text.strip()
    context.user_data["temp_twitter"] = twitter if twitter.lower() != "skip" else None
    await update.message.reply_text(
        "Optional: Send a custom media (photo/GIF) for the alerts or type 'skip'. "
        "This will be used instead of the default emoji alerts."
    )
    return INPUT_MEDIA

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives custom media for alerts."""
    media_file_id = None
    if update.message.photo:
        media_file_id = update.message.photo[-1].file_id # Get the largest photo
    elif update.message.animation:
        media_file_id = update.message.animation.file_id
    elif update.message.text and update.message.text.lower() == "skip":
        media_file_id = None
    else:
        await update.message.reply_text("Please send a photo/GIF or type 'skip'.")
        return INPUT_MEDIA
    
    context.user_data["temp_media_file_id"] = media_file_id
    
    # Get the group_id from context.user_data (set during initial /start in group)
    group_id = context.user_data.get("group_id") or update.effective_chat.id # If private, use private chat ID for testing

    # Save all settings to the database
    await add_group(group_id, context.user_data["temp_token_address"], context.user_data["temp_token_symbol"])
    await update_group_settings(group_id, {
        "min_buy_usd": context.user_data["temp_min_buy_usd"],
        "emoji": context.user_data["temp_emoji"],
        "buy_step": context.user_data["temp_buy_step"],
        "website": context.user_data["temp_website"],
        "telegram_link": context.user_data["temp_telegram"],
        "twitter_link": context.user_data["temp_twitter"],
        "media_file_id": context.user_data["temp_media_file_id"]
    })

    await update.message.reply_text(
        f"Bot configured successfully for group ID: {group_id}! "
        "It will now track buys for "
        f"{context.user_data['temp_token_symbol']} (Address: {shorten_address(context.user_data['temp_token_address'])})."
    )
    context.user_data.clear() # Clear temporary data
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the conversation."""
    await update.message.reply_text("Setup cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Conversation Handlers (Boost Feature) ---
async def boost_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the boost conversation."""
    if update.message.chat.type != "private":
        await update.message.reply_text("Please use the /boost command in a private chat with the bot.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "To boost a token, please enter its token address (the one you configured for the bot)."
    )
    return BOOST_TOKEN_ADDRESS

async def receive_boost_token_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the token address for boosting."""
    token_address = update.message.text.strip()
    if not token_address.startswith("0x") or len(token_address) < 40:
        await update.message.reply_text("Invalid Sui token address. Please try again.")
        return BOOST_TOKEN_ADDRESS
    
    token_info = fetch_token_info(token_address)
    if token_info["symbol"] == "TOKEN": # Check if token info was successfully fetched
        await update.message.reply_text("Could not find information for this token. Please ensure it's a valid configured token address.")
        return BOOST_TOKEN_ADDRESS

    context.user_data["boost_token_address"] = token_address
    context.user_data["boost_token_symbol"] = token_info["symbol"]

    # Display boost options
    keyboard = [
        [InlineKeyboardButton("4 hours (15 SUI)", callback_data="boost_4h_15")],
        [InlineKeyboardButton("8 hours (20 SUI)", callback_data="boost_8h_20")],
        [InlineKeyboardButton("12 hours (27 SUI)", callback_data="boost_12h_27")],
        [InlineKeyboardButton("24 hours (45 SUI)", callback_data="boost_24h_45")],
        [InlineKeyboardButton("48 hours (80 SUI)", callback_data="boost_48h_80")],
        [InlineKeyboardButton("72 hours (110 SUI)", callback_data="boost_72h_110")],
        [InlineKeyboardButton("1 Week (180 SUI)", callback_data="boost_1w_180")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"You want to boost {context.user_data['boost_token_symbol']}. Choose a boost duration:",
        reply_markup=reply_markup,
    )
    return BOOST_AMOUNT # Move to next state to handle callback

async def boost_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles boost duration selection."""
    query = update.callback_query
    await query.answer()

    duration_code, sui_cost = query.data.replace("boost_", "").split("_")
    sui_cost = float(sui_cost)
    
    duration_seconds = 0
    if duration_code == "4h": duration_seconds = 4 * 3600
    elif duration_code == "8h": duration_seconds = 8 * 3600
    elif duration_code == "12h": duration_seconds = 12 * 3600
    elif duration_code == "24h": duration_seconds = 24 * 3600
    elif duration_code == "48h": duration_seconds = 48 * 3600
    elif duration_code == "72h": duration_seconds = 72 * 3600
    elif duration_code == "1w": duration_seconds = 7 * 24 * 3600
    
    context.user_data["boost_duration_seconds"] = duration_seconds
    context.user_data["boost_sui_cost"] = sui_cost

    await query.edit_message_text(
        f"To boost {context.user_data['boost_token_symbol']} for {duration_code.replace('h', ' hours').replace('w', ' week')} "
        f"it costs {sui_cost} SUI.\n\n"
        f"Please send {sui_cost} SUI to the following address: `{BOOST_RECEIVER}`\n\n"
        "After sending, reply with the **transaction hash** to confirm your payment."
    )
    return BOOST_CONFIRM

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirms the payment and applies the boost."""
    transaction_hash = update.message.text.strip()
    token_address = context.user_data.get("boost_token_address")
    sui_cost = context.user_data.get("boost_sui_cost")
    duration_seconds = context.user_data.get("boost_duration_seconds")
    token_symbol = context.user_data.get("boost_token_symbol")

    if not all([transaction_hash, token_address, sui_cost, duration_seconds]):
        await update.message.reply_text("Something went wrong. Please restart the /boost process.")
        context.user_data.clear()
        return ConversationHandler.END

    # --- IMPORTANT: Real payment verification needs to be implemented in sui_api.py ---
    # The current verify_payment is a placeholder.
    payment_verified = verify_payment(transaction_hash, BOOST_RECEIVER, sui_cost)

    if payment_verified:
        await add_boost(token_address, duration_seconds)
        await update.message.reply_text(
            f"Payment confirmed! {token_symbol} has been boosted for {duration_seconds / 3600:.0f} hours."
        )
        logger.info(f"Boost activated for {token_symbol} ({token_address}) for {duration_seconds} seconds.")

        # Announce boost in trending channel
        await context.bot.send_message(
            chat_id=TRENDING_CHANNEL,
            text=f"🚀 **{token_symbol}** has just activated a boost! All buys will now show in the trending channel for the next {duration_seconds / 3600:.0f} hours!",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Payment could not be verified. Please ensure you sent the correct amount and provided the correct transaction hash. If you believe this is an error, contact support."
        )
        logger.warning(f"Payment verification failed for {token_address}, hash: {transaction_hash}")
    
    context.user_data.clear()
    return ConversationHandler.END

# --- Scheduled Jobs ---
async def check_buys(context: ContextTypes.DEFAULT_TYPE):
    """
    Scheduled job to fetch recent buys from the Sui event queue and send alerts.
    """
    logger.info("Running check_buys job...")
    
    # Get current SUI price once for all calculations
    sui_price = fetch_sui_price()
    if sui_price == 0:
        logger.warning("Could not fetch SUI price. Skipping buy checks dependent on USD value.")
        return

    # Get all configured groups
    all_groups = await get_all_configured_groups()
    if not all_groups:
        logger.info("No groups configured. Skipping buy checks.")
        return

    # Get all currently boosted tokens
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT token_address, expiration_timestamp FROM boosts WHERE expiration_timestamp > ?", (int(time.time()),))
    boosted_tokens_data = cursor.fetchall()
    conn.close()
    boosted_token_addresses = {row[0] for row in boosted_tokens_data}

    # Fetch buys from the global queue (populated by WebSocket listener)
    new_buys = fetch_recent_buys_from_queue()
    if not new_buys:
        logger.info("No new buys detected from Sui events.")
        return

    logger.info(f"Processing {len(new_buys)} new buy events.")
    
    # Process each new buy
    for buy in new_buys:
        token_address = buy["token_address"]
        # Fetch up-to-date token info from RaidenX for each buy
        token_info = fetch_token_info(token_address)
        
        if token_info["price"] == 0:
            logger.warning(f"Skipping buy for {token_address} due to zero price.")
            continue
        
        # Recalculate USD value based on current token price and SUI price
        # This assumes `buy["amount"]` is in the token's smallest unit if price is per token.
        # If `buy["amount"]` is the USD equivalent, this might need adjustment.
        # For simplicity, assuming buy["amount"] is the actual token quantity.
        buy["usd_value"] = buy["amount"] * token_info["price"]
        
        # Add to DB to prevent duplicates, only proceed if not a duplicate
        if not await add_buy_to_db(buy):
            continue

        # Send alerts to relevant groups
        for group in all_groups:
            if group["token_address"] == token_address:
                # Group-specific alert
                if buy["usd_value"] >= group["min_buy_usd"]:
                    formatted_alert = format_alert(buy, token_info, group, sui_price)
                    try:
                        if formatted_alert.get("media_file_id"):
                            # Send with custom media
                            if formatted_alert["media_file_id"].startswith("CgAC"): # Check for GIF
                                await context.bot.send_animation(
                                    chat_id=group["group_id"],
                                    animation=formatted_alert["media_file_id"],
                                    caption=formatted_alert["text"],
                                    parse_mode="Markdown",
                                    reply_markup=formatted_alert["reply_markup"]
                                )
                            else: # Assume photo
                                await context.bot.send_photo(
                                    chat_id=group["group_id"],
                                    photo=formatted_alert["media_file_id"],
                                    caption=formatted_alert["text"],
                                    parse_mode="Markdown",
                                    reply_markup=formatted_alert["reply_markup"]
                                )
                        else:
                            await context.bot.send_message(
                                chat_id=group["group_id"],
                                text=formatted_alert["text"],
                                parse_mode="Markdown",
                                reply_markup=formatted_alert["reply_markup"]
                            )
                        logger.info(f"Sent alert to group {group['group_id']} for {token_info['symbol']} buy.")
                    except Exception as e:
                        logger.error(f"Error sending alert to group {group['group_id']}: {e}")
            
        # Send alerts to Trending Channel based on rules
        is_boosted = token_address in boosted_token_addresses
        if buy["usd_value"] >= 200 or is_boosted:
            trending_group_settings = { # Use a dummy group_settings for trending channel
                "emoji": "💎",
                "buy_step": 10, # Trending channel might have a different emoji step
                "website": token_info.get("website"), # Pass through token info from RaidenX
                "telegram_link": token_info.get("telegram_link"), # Assuming RaidenX provides this, or fetch from DB
                "twitter_link": token_info.get("twitter_link"),
                "media_file_id": None # No custom media for trending channel, use emojis
            }
            # For trending channel, try to get telegram link from configured groups if available
            for group in all_groups:
                if group["token_address"] == token_address and group["telegram_link"]:
                    trending_group_settings["telegram_link"] = group["telegram_link"]
                    break

            formatted_trending_alert = format_alert(buy, token_info, trending_group_settings, sui_price)
            try:
                await context.bot.send_message(
                    chat_id=TRENDING_CHANNEL,
                    text=f"🔥 TRENDING BUY! 🔥\n{formatted_trending_alert['text']}",
                    parse_mode="Markdown",
                    reply_markup=formatted_trending_alert['reply_markup']
                )
                logger.info(f"Sent trending alert for {token_info['symbol']} buy.")
            except Exception as e:
                logger.error(f"Error sending trending alert: {e}")

async def trend_alert(context: ContextTypes.DEFAULT_TYPE):
    """
    Scheduled job to generate and post the trending leaderboard.
    """
    logger.info("Running trend_alert (leaderboard) job...")
    
    sui_price = fetch_sui_price()
    if sui_price == 0:
        logger.warning("Could not fetch SUI price for leaderboard. Skipping.")
        return

    all_groups = await get_all_configured_groups()
    if not all_groups:
        logger.info("No groups configured. Skipping leaderboard generation.")
        return

    # Get active boosts
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT token_address, expiration_timestamp FROM boosts WHERE expiration_timestamp > ?", (int(time.time()),))
    active_boosts = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()

    # Calculate 30-minute volume and price change (conceptual)
    # WARNING: RaidenX's market-data endpoint typically provides 24h volume.
    # Obtaining accurate 30-min volume and price change requires a more granular
    # data source (e.g., a real-time data provider with historical data or a custom indexer).
    # For this example, we'll use a placeholder for 30-min volume/price change.
    # You will need to enhance `Workspace_token_info` or use another API if precise
    # 30-min data is critical for your leaderboard.
    
    token_metrics = {}
    for group in all_groups:
        token_address = group["token_address"]
        token_info = fetch_token_info(token_address)
        if token_info["symbol"] == "TOKEN" or token_info["price"] == 0:
            logger.warning(f"Skipping {token_address} for leaderboard due to missing info.")
            continue
        
        # Placeholder for 30-min volume and price change
        # In a real scenario, you'd fetch/calculate these.
        # For demonstration, use a fraction of 24h volume and a random price change.
        estimated_30m_volume = token_info.get("volume_24h", 0) / 48 # Roughly 30 mins of 24h
        
        # Placeholder price change (you need to get actual historical data for this)
        price_change_30m = (datetime.now().timestamp() % 10 - 5) * 2 # Random between -10% and +10%

        # Boost points calculation
        boost_score = 0
        if token_address in active_boosts:
            # Boosted tokens get a significant boost to their ranking
            # Adjust this value as needed for desired impact
            boost_score = 1000000000 # Example: Add 1 Billion to volume for boosted tokens
            logger.info(f"Token {token_info['symbol']} is boosted. Adding {boost_score} to ranking score.")

        ranking_score = estimated_30m_volume + boost_score
        
        token_metrics[token_address] = {
            "symbol": token_info["symbol"],
            "telegram_link": group.get("telegram_link"),
            "market_cap": token_info["market_cap"],
            "price_change_30m": price_change_30m,
            "ranking_score": ranking_score
        }

    # Sort tokens by ranking score (descending)
    sorted_tokens = sorted(token_metrics.items(), key=lambda item: item[1]["ranking_score"], reverse=True)

    leaderboard_text = ["📊 **Moonbags Trending Leaderboard (30 min Volume)** 📊\n"]
    for i, (token_address, metrics) in enumerate(sorted_tokens[:10]): # Top 10
        leaderboard_text.append(
            format_leaderboard_entry(
                i + 1, 
                metrics["symbol"], 
                metrics["telegram_link"], 
                metrics["market_cap"], 
                metrics["price_change_30m"]
            )
        )
    
    leaderboard_message = "\n".join(leaderboard_text)

    try:
        # Send the new leaderboard
        sent_message: Message = await context.bot.send_message(
            chat_id=TRENDING_CHANNEL,
            text=leaderboard_message,
            parse_mode="Markdown",
            disable_web_page_preview=True # To prevent link previews from being too large
        )
        # Pin the new leaderboard
        await context.bot.pin_chat_message(
            chat_id=TRENDING_CHANNEL,
            message_id=sent_message.message_id,
            disable_notification=True # Don't notify channel members about pin
        )
        logger.info("New leaderboard posted and pinned.")

        # Optionally, unpin the old leaderboard if you store its message_id
        # For simplicity, this example doesn't store/unpin old messages.
    except Exception as e:
        logger.error(f"Error posting/pinning leaderboard: {e}")

# --- Web Server for Health Check ---
async def health_check(request):
    return web.Response(text="Bot is running!")

def start_http_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    loop.run_until_complete(site.start())
    logger.info(f"HTTP server started on port {PORT}")
    loop.run_forever()

# --- Main Bot Application ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Start the HTTP server in a separate thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Start the Sui WebSocket listener in a separate thread
    # This is already handled by sui_api.py on import, but ensure it's not duplicated
    # logger.info("Sui WebSocket listener is handled by sui_api.py import.")

    # Setup ConversationHandler for group configuration
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start, filters=filters.ChatType.PRIVATE),
            CommandHandler("start", start, filters=filters.ChatType.GROUPS & filters.Regex("moonbagsbuybot"))
        ],
        states={
            INPUT_TOKEN_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token_address)],
            INPUT_MIN_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_buy)],
            INPUT_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_emoji)],
            INPUT_BUY_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_step)],
            INPUT_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_website)],
            INPUT_TELEGRAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_telegram)],
            INPUT_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_twitter)],
            INPUT_MEDIA: [
                MessageHandler(filters.PHOTO | filters.ANIMATION, receive_media),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_media) # Allow 'skip' text
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True # Allow restarting conversation if interrupted
    )
    application.add_handler(conv_handler)
    
    # Setup ConversationHandler for boost feature
    boost_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("boost", boost_command)],
        states={
            BOOST_TOKEN_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_boost_token_address)],
            BOOST_AMOUNT: [CallbackQueryHandler(boost_callback, pattern=r"^boost_")],
            BOOST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_boost)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    application.add_handler(boost_conv_handler)

    # Add standalone command handlers that might be outside conv flow
    application.add_handler(CommandHandler("cancel", cancel)) # Fallback if not in conversation

    # Set up scheduled jobs
    job_queue = application.job_queue
    
    # Check for buys every 30 seconds
    job_queue.run_repeating(check_buys, interval=30, first=5)
    
    # Post trending leaderboard every 30 minutes
    job_queue.run_repeating(trend_alert, interval=1800, first=60) # 1800 seconds = 30 minutes
    
    # Start the Bot
    application.run_polling()

if __name__ == "__main__":
    from telegram.ext import ReplyKeyboardRemove # Import for ReplyKeyboardRemove

    # Clear fake symbols on startup, if you want this for fresh database testing
    # database.clear_fake_symbols()

    main()
