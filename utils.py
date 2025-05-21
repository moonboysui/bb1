def shorten_address(address):
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"

def format_alert(buy_data, token_info, group_settings):
    token_symbol = token_info.get('symbol', 'TOKEN')
    token_price = token_info.get('price', 0)
    market_cap = token_info.get('market_cap', 0)
    liquidity = token_info.get('liquidity', 0)
    emoji = group_settings.get('emoji', '🔥')
    buystep = group_settings.get('buystep', 5)
    emoji_count = max(1, min(20, int(buy_data['usd_value'] / buystep)))
    emojis = emoji * emoji_count
    usd_value_str = f"${buy_data['usd_value']:.2f}"
    token_amount_str = f"{buy_data['amount']:.4f}"
    market_cap_str = f"${market_cap/1000000:.2f}M" if market_cap > 1e6 else f"${market_cap/1000:.2f}K"
    liquidity_str = f"${liquidity/1000000:.2f}M" if liquidity > 1e6 else f"${liquidity/1000:.2f}K"
    alert_text = (
        f"{emojis} NEW BUY {emojis}\n\n"
        f"💰 {token_amount_str} ${token_symbol} (≈{usd_value_str})\n"
        f"👤 Buyer: [{shorten_address(buy_data['buyer_address'])}](https://suivision.xyz/address/{buy_data['buyer_address']}) | "
        f"[Txn](https://suivision.xyz/txblock/{buy_data['tx_hash']})\n\n"
        f"📊 ${token_symbol} Stats:\n"
        f"💲 Price: ${token_price:.8f}\n"
        f"💹 Market Cap: {market_cap_str}\n"
        f"💧 Liquidity: {liquidity_str}"
    )
    return alert_text
