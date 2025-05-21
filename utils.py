import math
import logging

logger = logging.getLogger(__name__)

def shorten_address(address):
    """Shorten a blockchain address for display."""
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"

def format_alert(buy_data, token_info, group_settings, sui_price):
    """
    Format an alert message for a group.
    Args:
        buy_data (dict): Dictionary containing buy transaction details.
        token_info (dict): Dictionary containing token market data (from RaidenX).
        group_settings (dict): Dictionary containing group specific settings from the database.
        sui_price (float): Current price of SUI in USD.
    """
    
    # Extract needed values with defaults
    token_symbol = token_info.get('symbol', 'TOKEN').upper()
    token_name = token_info.get('name', 'Unknown Token')
    token_price = token_info.get('price', 0)
    market_cap = token_info.get('market_cap', 0)
    liquidity = token_info.get('liquidity', 0)
    
    # Buy Data
    buyer_address = buy_data.get('buyer_address', 'N/A')
    transaction_id = buy_data.get('transaction_id', 'N/A')
    usd_value = buy_data.get('usd_value', 0)
    token_amount = buy_data.get('amount', 0)
    
    # Group Settings
    emoji = group_settings.get('emoji', '🔥')
    buy_step = group_settings.get('buy_step', 1)
    
    website_link = group_settings.get('website')
    telegram_link = group_settings.get('telegram_link')
    twitter_link = group_settings.get('twitter_link')
    
    # Links for the alert
    # The 'Chart' link needs the token_address, which we don't have directly in token_info.
    # It should come from group_settings or buy_data's token_address field.
    # Assuming token_address is available in buy_data for constructing the chart link.
    token_contract_address = buy_data.get('token_address', 'UNKNOWN_TOKEN_ADDRESS')

    # Format emojis based on buy size and buy step
    emoji_count = 0
    if buy_step > 0:
        emoji_count = max(1, int(usd_value / buy_step)) # At least 1 emoji
    emojis = emoji * min(emoji_count, 50) # Cap at 50 emojis to prevent overly long messages

    # Format amounts
    usd_value_str = f"${usd_value:.2f}"
    
    # Calculate SUI size if token price is available and not zero
    sui_size = 0
    if token_price > 0:
        sui_size = usd_value / sui_price if sui_price > 0 else 0 # Use SUI price for calculation
    sui_size_str = f"{sui_size:.2f} SUI"
    
    # Format token amount (e.g., K, M, B for large numbers)
    if token_amount >= 1_000_000_000:
        token_amount_str = f"{token_amount / 1_000_000_000:.2f}B"
    elif token_amount >= 1_000_000:
        token_amount_str = f"{token_amount / 1_000_000:.2f}M"
    elif token_amount >= 1_000:
        token_amount_str = f"{token_amount / 1_000:.2f}K"
    else:
        token_amount_str = f"{token_amount:.2f}"

    # Format market data (K, M, B)
    def format_market_value(value):
        if value >= 1_000_000_000:
            return f"${value/1_000_000_000:.2f}B"
        elif value >= 1_000_000:
            return f"${value/1_000_000:.2f}M"
        elif value >= 1_000:
            return f"${value/1_000:.2f}K"
        else:
            return f"${value:.2f}"

    market_cap_str = format_market_value(market_cap)
    liquidity_str = format_market_value(liquidity)

    # --- Construct the Message ---
    # The token symbol in the title links to the token's Telegram channel
    message_parts = []
    
    # Title: TOKEN Buy! with hyperlink
    if telegram_link:
        message_parts.append(f"[{token_symbol} Buy!]({telegram_link})\n")
    else:
        message_parts.append(f"{token_symbol} Buy!\n")

    message_parts.append(f"{emojis}\n")
    message_parts.append(f"⬅️ Size {usd_value_str} | {sui_size_str}\n")
    message_parts.append(f"➡️ Got {token_amount_str} {token_symbol}\n")
    
    # Buyer and Transaction links
    suivision_buyer_link = f"https://suivision.xyz/address/{buyer_address}"
    suiscan_tx_link = f"https://suiscan.xyz/mainnet/tx/{transaction_id}"
    
    message_parts.append(f"👤 Buyer [{shorten_address(buyer_address)}]({suivision_buyer_link}) | Txn ([Link]({suiscan_tx_link}))\n")
    
    message_parts.append(f"🔼 MCap {market_cap_str}\n")
    message_parts.append(f"📊 TVL/Liq {liquidity_str}\n")
    message_parts.append(f"📊 Price ${token_price:.6f}\n") # Using .6f for more precision on small prices
    message_parts.append(f"💧 SUI Price: ${sui_price:.2f}\n")

    # Optional social links
    social_links = []
    if website_link:
        social_links.append(f"[Website]({website_link})")
    if telegram_link:
        social_links.append(f"[Telegram]({telegram_link})")
    if twitter_link:
        social_links.append(f"[X]({twitter_link})")
    
    if social_links:
        message_parts.append(" ".join(social_links) + "\n")

    # Fixed links as per user request
    # Chart link needs dynamic token contract address for Dexscreener
    # Assumes the token_contract_address is the package ID or object ID needed by Dexscreener.
    dexscreener_chart_link = f"https://dexscreener.com/sui/{token_contract_address}"
    message_parts.append(f"[Chart]({dexscreener_chart_link}) | [Vol. Bot](https://t.me/suivolumebot) | [Sui Trending](https://t.me/moonbagstrending)\n")
    message_parts.append("———\n")
    message_parts.append("[Ad: Place your advertisement here](https://t.me/BullsharkTrendingBot?start=adBuyRequest)")

    alert_message = "".join(message_parts)

    # Prepare inline keyboard for the "Buy" button
    keyboard = [[InlineKeyboardButton(f"Buy {token_symbol}", url=f"https://dexscreener.com/sui/{token_contract_address}?trade")]
                # You might replace the above with a specific DEX swap URL if preferred:
                # InlineKeyboardButton(f"Buy {token_symbol}", url=f"https://yourdex.com/swap?token={token_contract_address}&base=SUI")
               ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Return the formatted message, reply markup, and media_file_id if present
    return {
        "text": alert_message,
        "reply_markup": reply_markup,
        "media_file_id": group_settings.get('media_file_id') # Pass custom media file ID
    }

# This is a basic example of how you might format a leaderboard entry
# It will be used in bot.py for the trending leaderboard
def format_leaderboard_entry(rank, token_symbol, telegram_link, market_cap, price_change_30m):
    market_cap_str = ""
    if market_cap >= 1_000_000_000:
        market_cap_str = f"${market_cap/1_000_000_000:.2f}B"
    elif market_cap >= 1_000_000:
        market_cap_str = f"${market_cap/1_000_000:.2f}M"
    elif market_cap >= 1_000:
        market_cap_str = f"${market_cap/1_000:.2f}K"
    else:
        market_cap_str = f"${market_cap:.2f}"

    price_change_str = f"{price_change_30m:+.2f}%" if price_change_30m is not None else "N/A"

    if telegram_link:
        return f"{rank}. [{token_symbol}]({telegram_link}) | MCap: {market_cap_str} | 30m: {price_change_str}"
    else:
        return f"{rank}. {token_symbol} | MCap: {market_cap_str} | 30m: {price_change_str}"
