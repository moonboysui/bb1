import re
import math
from config import Config

SUI_ADDRESS_REGEX = r"^0x[a-fA-F0-9]{64}::[a-zA-Z0-9_]+::[a-zA-Z0-9_]+$"

def validate_sui_address(address):
    return re.match(SUI_ADDRESS_REGEX, address)

def shorten_address(address, keep=6):
    if "::" in address:
        parts = address.split("::")
        return f"{parts[0][:keep]}...{parts[0][-keep:]}::{parts[1]}"
    return f"{address[:keep]}...{address[-keep:]}"

def format_alert(buy, token, group):
    emoji_count = max(1, min(20, int(buy['usd_value'] / group['emoji_step']))
    emojis = group['emoji'] * emoji_count
    
    return (
        f"{emojis} {token['symbol']} Buy! {emojis}\n\n"
        f"💰 Size ${buy['usd_value']:.2f} | {buy['amount']:.2f} SUI\n"
        f"👤 Buyer: [{shorten_address(buy['buyer'])}]({Config.SUI_EXPLORER}/{buy['tx_hash']})\n"
        f"🔼 MCap ${token['market_cap']/1000:.2f}K\n"
        f"📊 TVL/Liq ${token['liquidity']/1000:.2f}K\n"
        f"📈 Price ${token['price']:.8f}\n"
        f"💧 SUI Price: ${token['sui_price']:.2f}\n\n"
        f"{format_links(group)}\n"
        f"[Chart]({group['chart_link']}) | "
        f"[Vol Bot]({Config.VOL_BOT_LINK}) | "
        f"[Trending](https://t.me/{Config.TRENDING_CHANNEL})"
    )

def format_links(group):
    links = []
    if group.get('website'): links.append(f"[Website]({group['website']})")
    if group.get('telegram'): links.append(f"[Telegram]({group['telegram']})")
    if group.get('twitter'): links.append(f"[X]({group['twitter']})")
    return " | ".join(links)
