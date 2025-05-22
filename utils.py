import math

def shorten_address(address: str) -> str:
    """Shorten a blockchain address (e.g., 0xabc...1234) for display."""
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"

def format_alert(buy_data: dict, token_info: dict, group_settings: dict) -> str:
    """
    Format an alert message for a new buy event, given the buy data, token info, and group settings.
    This function returns a Markdown-formatted message string.
    """
    token_symbol = token_info.get('symbol', 'TOKEN')
    token_price = token_info.get('price', 0)
    market_cap = token_info.get('market_cap', 0)
    liquidity = token_info.get('liquidity', 0)
    # Dynamic emojis based on buy size ($5 per emoji)
    emoji = group_settings.get('emoji', '🔥')
    emoji_count = max(1, min(20, int(buy_data['usd_value'] / 5)))
    emojis = emoji * emoji_count
    # Format amounts
    usd_value_str = f"${buy_data['usd_value']:.2f}"
    token_amount_str = f"{buy_data['amount']:.4f}"
    # Market cap and liquidity strings
    if market_cap > 1000000:
        market_cap_str = f"${market_cap/1000000:.2f}M"
    else:
        market_cap_str = f"${market_cap/1000:.2f}K"
    if liquidity > 1000000:
        liquidity_str = f"${liquidity/1000000:.2f}M"
    else:
        liquidity_str = f"${liquidity/1000:.2f}K"
    # Build the alert message text
    alert_text = (
        f"{emojis} NEW BUY {emojis}\n\n"
        f"💰 {token_amount_str} ${token_symbol} (≈{usd_value_str})\n"
        f"🧠 Buyer: [{shorten_address(buy_data['buyer_address'])}](https://suivision.xyz/txblock/{buy_data['tx_hash']})\n\n"
        f"📊 ${token_symbol} Stats:\n"
        f"💲 Price: ${token_price:.8f}\n"
        f"💹 Market Cap: {market_cap_str}\n"
        f"💧 Liquidity: {liquidity_str}"
    )
    return alert_text
