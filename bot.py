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
    ContextTypes,
)
from database import get_db, init_db
from utils import shorten_address, format_alert
from sui import verify_payment

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOOST_RECEIVER = os.getenv("BOOST_RECEIVER", "0x0000000000000000000000000000000000000000000000000000000000000000")
TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
PORT = int(os.getenv("PORT", 8080))

# Conversation states
CHOOSING, INPUT_TOKEN, INPUT_MIN_BUY, INPUT_EMOJI, INPUT_WEBSITE, INPUT_TELEGRAM, INPUT_TWITTER, INPUT_MEDIA = range(8)

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the /start command in groups and private chats."""
    logger.info(f"Received /start in chat {update.message.chat.id}, type: {update.message.chat.type}")
    try:
        if update.message.chat.type in ["group", "supergroup"]:
            group_id = update.message.chat.id
            button = InlineKeyboardButton(
                "➡️ Continue in Private Chat", url=f"https://t.me/{context.bot.username}?start=group{group_id}"
            )
            await update.message.reply_text(
                "Thanks for inviting me! Please continue setup in private chat.",
                reply_markup=InlineKeyboardMarkup([[button]])
            )
            return ConversationHandler.END
        else:
            param = context.args[0] if context.args else None
            if param and param.startswith("group"):
                group_id = int(param[5:])
                context.user_data["group_id"] = group_id
                try:
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
                except Exception as e:
                    logger.error(f"Database error in start: {e}")
                    await update.message.reply_text("Error accessing settings. Please try again.")
                    return ConversationHandler.END
            else:
                await update.message.reply_text("Please start the configuration from your group using /start.")
                return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in start: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
        return ConversationHandler.END

async def boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show boost pricing and instructions."""
    pricing = (
        "Boost your token in the trending channel!\n"
        "Pricing:\n"
        "- 1 SUI: 1 hour\n"
        "- 5 SUI: 6 hours\n"
        "- 10 SUI: 24 hours\n\n"
        f"Please send SUI to: `{BOOST_RECEIVER}`\n"
        "Reply with the transaction hash: /confirm <hash>"
    )
    await update.message.reply_text(pricing, parse_mode="Markdown")

async def confirm_boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verify a boost payment."""
    if not context.args:
        await update.message.reply_text("Please provide the transaction hash: /confirm <hash>")
        return
    txn_hash = context.args[0]
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token_address FROM groups WHERE group_id = ?", (update.message.chat.id,))
            group = cursor.fetchone()
        if not group:
            await update.message.reply_text("No token configured. Set up with /start.")
            return
        token_address = group["token_address"]
        amounts = {1: 3600, 5: 21600, 10: 86400}  # SUI to seconds
        for amount, duration in amounts.items():
            if verify_payment(txn_hash, amount, BOOST_RECEIVER):
                expiration = int(time.time()) + duration
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR REPLACE INTO boosts (token_address, expiration_timestamp) VALUES (?, ?)",
                        (token_address, expiration),
                    )
                    conn.commit()
                await update.message.reply_text(f"Boost confirmed! Token boosted for {duration//3600} hours.")
                return
        await update.message.reply_text("Payment not verified. Check the hash or amount.")
    except Exception as e:
        logger.error(f"Error in confirm_boost: {e}")
        await update.message.reply_text("Error verifying payment. Try again.")

async def poll_buys(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for new buys and post alerts."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT group_id, token_address, min_buy_usd, emoji, media_file_id FROM groups")
            groups = cursor.fetchall()
        for group in groups:
            buys = fetch_recent_buys(group["token_address"], group["min_buy_usd"])
            for buy in buys:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT id FROM buys WHERE token_address = ? AND timestamp = ?",
                        (group["token_address"], buy["timestamp"]),
                    )
                    if cursor.fetchone():
                        continue
                    cursor.execute(
                        "INSERT INTO buys (token_address, timestamp, usd_value) VALUES (?, ?, ?)",
                        (group["token_address"], buy["timestamp"], buy["usd_value"]),
                    )
                    conn.commit()
                message, reply_markup, media_file_id = format_alert(buy, group["emoji"], "Token", group["media_file_id"])
                if buy["usd_value"] >= 200:
                    await context.bot.send_message(TRENDING_CHANNEL, message, reply_markup=reply_markup)
                else:
                    if media_file_id:
                        if media_file_id.startswith("Ag"):
                            await context.bot.send_animation(group["group_id"], media_file_id, caption=message)
                        else:
                            await context.bot.send_photo(group["group_id"], media_file_id, caption=message)
                    else:
                        await context.bot.send_message(group["group_id"], message, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in poll_buys: {e}")

def fetch_recent_buys(token_address: str, min_usd: float) -> list:
    """Get recent buys from Raiden X API."""
    try:
        encoded_address = token_address.replace("::", "%3A%3A")
        url = f"https://api-public.raidenx.io/sui/defi/txs/token?address={encoded_address}&txType=ALL&sortType=desc"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        txs = response.json().get("data", [])
        buys = []
        for tx in txs:
            usd_value = float(tx.get("usd_value", 0))
            if usd_value >= min_usd:
                buys.append({
                    "timestamp": int(tx.get("timestamp", 0)),
                    "usd_value": usd_value,
                    "buyer": tx.get("sender", ""),
                    "symbol": tx.get("symbol", "Unknown"),
                    "website": tx.get("website", ""),
                    "sui_amount": float(tx.get("sui_amount", 0)),
                    "tokens_purchased": float(tx.get("tokens_purchased", 0)),
                    "price": float(tx.get("price", 0)),
                    "market_cap": float(tx.get("market_cap", 0)),
                    "liquidity": float(tx.get("liquidity", 0)),
                    "token_address": token_address,
                })
        return buys
    except Exception as e:
        logger.error(f"Error fetching buys: {e}")
        return []

async def generate_leaderboard(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post a leaderboard of top tokens."""
    try:
        cutoff = int(time.time()) - 24 * 3600
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT token_address, COUNT(*) as buy_count, SUM(usd_value) as total_usd
                FROM buys
                WHERE timestamp >= ?
                GROUP BY token_address
                ORDER BY total_usd DESC
                LIMIT 5
                """,
                (cutoff,),
            )
            leaders = cursor.fetchall()
        if not leaders:
            return
        message = "🏆 Top Tokens (Last 24 Hours) 🏆\n\n"
        for i, leader in enumerate(leaders, 1):
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT emoji FROM groups WHERE token_address = ?", (leader["token_address"],))
                group = cursor.fetchone()
            emoji = group["emoji"] if group and group["emoji"] else "🔥"
            message += (
                f"{i}. {shorten_address(leader['token_address'])} {emoji}\n"
                f"   Buys: {leader['buy_count']}\n"
                f"   Total: ${leader['total_usd']:.2f}\n\n"
            )
        await context.bot.send_message(TRENDING_CHANNEL, message)
    except Exception as e:
        logger.error(f"Error in generate_leaderboard: {e}")

# --- Configuration Handlers ---

async def start_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle configuration menu selections."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "edit_settings":
        context.user_data.clear()
        await query.message.reply_text("Let’s edit the settings. Use the buttons below.", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif data == "set_token":
        await query.message.reply_text("Please enter the token address.")
        return INPUT_TOKEN
    elif data == "set_min_buy":
        await query.message.reply_text("Please enter the minimum buy amount in USD.")
        return INPUT_MIN_BUY
    elif data == "set_emoji":
        await query.message.reply_text("Please enter the emoji for alerts.")
        return INPUT_EMOJI
    elif data == "set_website":
        await query.message.reply_text("Please enter the website URL (or 'skip' to skip).")
        return INPUT_WEBSITE
    elif data == "set_telegram":
        await query.message.reply_text("Please enter the Telegram link (or 'skip' to skip).")
        return INPUT_TELEGRAM
    elif data == "set_twitter":
        await query.message.reply_text("Please enter the Twitter link (or 'skip' to skip).")
        return INPUT_TWITTER
    elif data == "set_media":
        await query.message.reply_text("Please send a photo or GIF for alerts (or 'skip' to skip).")
        return INPUT_MEDIA
    elif data == "finish_setup":
        group_id = context.user_data.get("group_id")
        if not group_id:
            await query.message.reply_text("Error: Group ID not found. Start over with /start in your group.")
            return ConversationHandler.END
        settings = context.user_data.get("settings", {})
        token_address = settings.get("token_address")
        min_buy_usd = settings.get("min_buy_usd")
        if not token_address or not min_buy_usd:
            await query.message.reply_text("Token address and minimum buy are required. Start over with /start.")
            return ConversationHandler.END
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO groups (group_id, token_address, min_buy_usd, emoji, website, telegram_link, twitter_link, media_file_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        token_address,
                        min_buy_usd,
                        settings.get("emoji"),
                        settings.get("website"),
                        settings.get("telegram_link"),
                        settings.get("twitter_link"),
                        settings.get("media_file_id"),
                    ),
                )
                conn.commit()
            summary = (
                f"Setup complete for group {group_id}:\n"
                f"Token: {token_address}\n"
                f"Min Buy: ${min_buy_usd}\n"
                f"Emoji: {settings.get('emoji', 'N/A')}\n"
                f"Website: {settings.get('website', 'N/A')}\n"
                f"Telegram: {settings.get('telegram_link', 'N/A')}\n"
                f"Twitter: {settings.get('twitter_link', 'N/A')}\n"
                f"Media: {'Set' if settings.get('media_file_id') else 'Not Set'}"
            )
            await query.message.reply_text(summary)
            context.user_data.clear()
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"Database error in finish_setup: {e}")
            await query.message.reply_text("Error saving settings. Please try again.")
            return ConversationHandler.END

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the token address."""
    context.user_data.setdefault("settings", {})["token_address"] = update.message.text.strip()
    await update.message.reply_text("Token address saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_min_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the minimum buy amount."""
    try:
        min_buy = float(update.message.text.strip())
        context.user_data.setdefault("settings", {})["min_buy_usd"] = min_buy
        await update.message.reply_text("Minimum buy saved. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    except ValueError:
        await update.message.reply_text("Please enter a valid number for the minimum buy amount.")
        return INPUT_MIN_BUY

async def receive_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the emoji."""
    context.user_data.setdefault("settings", {})["emoji"] = update.message.text.strip()
    await update.message.reply_text("Emoji saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the website URL."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["website"] = text
    await update.message.reply_text("Website saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Telegram link."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["telegram_link"] = text
    await update.message.reply_text("Telegram link saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the Twitter link."""
    text = update.message.text.strip()
    if text.lower() != "skip":
        context.user_data.setdefault("settings", {})["twitter_link"] = text
    await update.message.reply_text("Twitter link saved. What's next?", reply_markup=get_menu_keyboard())
    return CHOOSING

async def receive_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the media file ID."""
    text = update.message.text.strip() if update.message.text else None
    if text and text.lower() == "skip":
        await update.message.reply_text("Media skipped. What's next?", reply_markup=get_menu_keyboard())
        return CHOOSING
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data.setdefault("settings", {})["media_file_id"] = file_id
        await update.message.reply_text("Photo saved. What's next?", reply_markup=get_menu_keyboard())
    elif update.message.animation:
        file_id = update.message.animation.file_id
        context.user_data.setdefault("settings", {})["media_file_id"] = file_id
        await update.message.reply_text("GIF saved. What's next?", reply_markup=get_menu_keyboard())
    else:
        await update.message.reply_text("Please send a photo or GIF, or type 'skip' to skip.")
        return INPUT_MEDIA
    return CHOOSING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the setup process."""
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END

# --- Utility Functions ---

def get_menu_keyboard() -> InlineKeyboardMarkup:
    """Create the configuration menu."""
    keyboard = [
        [InlineKeyboardButton("Token Address", callback_data="set_token")],
        [InlineKeyboardButton("Minimum Buy", callback_data="set_min_buy")],
        [InlineKeyboardButton("Emoji", callback_data="set_emoji")],
        [InlineKeyboardButton("Website", callback_data="set_website")],
        [InlineKeyboardButton("Telegram Link", callback_data="set_telegram")],
        [InlineKeyboardButton("Twitter Link", callback_data="set_twitter")],
        [InlineKeyboardButton("Media", callback_data="set_media")],
        [InlineKeyboardButton("Finish Setup", callback_data="finish_setup")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- HTTP Server for Render.com ---

async def health_check(request):
    """Respond to Render.com health checks."""
    return web.Response(text="OK")

async def run_server():
    """Start an HTTP server for Render.com."""
    try:
        app = web.Application()
        app.add_routes([web.get("/", health_check)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info(f"Health check server running on port {PORT}")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise

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
            INPUT_MEDIA: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.TEXT & ~filters.COMMAND, receive_media)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=True,  # Fix PTBUserWarning
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("boost", boost))
    application.add_handler(CommandHandler("confirm", confirm_boost))
    application.add_error_handler(lambda update, context: logger.error(f"Update {update} caused error {context.error}"))

    # Schedule recurring tasks
    application.job_queue.run_repeating(poll_buys, interval=10, first=0)
    application.job_queue.run_repeating(generate_leaderboard, interval=1800, first=1800)

    # Start the HTTP server as a background task
    asyncio.create_task(run_server())

    # Run the bot's polling
    await application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
