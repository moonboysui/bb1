import re

def shorten_address(address):
    # Sui Move address: 0x123...::Module::Type
    if '::' in address:
        parts = address.split('::')
        prefix = parts[0][:6] + '...' + parts[0][-4:]
        module = parts[1] if len(parts) > 1 else ''
        return f"{prefix}::{module}"
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"

def valid_token_address(token):
    # Sui Move tokens: 0x...::...::...
    pattern = r"^0x[0-9a-fA-F]+(::[A-Za-z0-9_]+){2,}$"
    return re.match(pattern, token) is not None

def format_alert(buy_data, token_info, group_settings):
    token_symbol = token_info.get('symbol', 'TOKEN')
    token_price = token_info.get('price', 0)
    market_cap = token_info.get('market_cap', 0)
    liquidity = token_info.get('liquidity', 0)
    emoji = group_settings.get('emoji', '🔥')
    buystep = group_settings.get('buystep', 5)
    emoji_count = max(1, min(20, int(float(buy_data['usd_value']) / buystep)))
    emojis = emoji * emoji_count
    usd_value_str = f"${buy_data['usd_value']:.2f}"
    token_amount_str = f"{buy_data['amount']:.4f}"
    market_cap_str = f"${market_cap/1000000:.2f}M" if market_cap > 1e6 else f"${market_cap/1000:.2f}K"
    liquidity_str = f"${liquidity/1000000:.2f}M" if liquidity > 1e6 else f"${liquidity/1000:.2f}K"
    alert_text = (
        f"{emojis}\n"
        f"⬅️ Size {usd_value_str} | {buy_data['amount']:.2f} SUI\n"
        f"➡️ Got {buy_data.get('token_amt_str', '')}\n\n"
        f"👤 Buyer [{shorten_address(buy_data['buyer_address'])}](https://suivision.xyz/address/{buy_data['buyer_address']}) | "
        f"[Txn](https://suivision.xyz/txblock/{buy_data['tx_hash']})\n"
        f"🔼 MCap {market_cap_str}\n"
        f"📊 TVL/Liq {liquidity_str}\n"
        f"📊 Price ${token_price:.6f}\n"
        f"💧 SUI Price: ${token_info.get('sui_price', 0):.2f}\n"
    )
    return alert_text
