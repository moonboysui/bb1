from math import floor
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def shorten_address(address):
    """Shorten a blockchain address for display."""
    if not isinstance(address, str) or len(address) < 10:
        return "N/A"
    return f"{address[:6]}...{address[-4:]}"

def format_alert(buy, emoji, token_symbol, media_file_id=None, include_links=True):
    """Format a buy alert message."""
    required_keys = [
        'usd_value', 'sui_amount', 'tokens_purchased', 'buyer',
        'price', 'market_cap', 'liquidity', 'token_address'
    ]
    missing_keys = [key for key in required_keys if key not in buy]
    if missing_keys:
        raise ValueError(f"Missing required keys in buy dictionary: {', '.join(missing_keys)}")

    num_emojis = min(floor(buy['usd_value'] / 5), 20)
    emojis = emoji * num_emojis
    buyer_link = f"https://suivision.xyz/account/{buy['buyer']}"
    message = (
        f"{emojis}\n"
        f"💰 {buy['sui_amount']} SUI (${buy['usd_value']:.2f})\n"
        f"🎯 {buy['tokens_purchased']} ${token_symbol}\n"
        f"🧠 Buyer: [{shorten_address(buy['buyer'])}]({buyer_link})\n"
        f"📉 Price: ${buy['price']:.4f} | MC: ${buy['market_cap']:.2f} | Liq: ${buy['liquidity']:.2f}"
    )
    buttons = []
    if include_links:
        buttons.append([InlineKeyboardButton(f"BUY ${token_symbol}", url=f"https://moonbags.finance/buy?token={buy['token_address']}")])
        buttons.append([InlineKeyboardButton("🌕 Moonbags Trending", url="https://t.me/moonbagstrending")])
        if buy.get('website'):
            buttons.append([InlineKeyboardButton("Website", url=buy['website'])])
        if buy.get('telegram_link'):
            buttons.append([InlineKeyboardButton("Telegram", url=buy['telegram_link'])])
        if buy.get('twitter_link'):
            buttons.append([InlineKeyboardButton("Twitter", url=buy['twitter_link'])])
    return message, InlineKeyboardMarkup(buttons) if buttons else None, media_file_id
