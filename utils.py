# utils.py
import re
from config import Config

def validate_sui_address(address: str) -> bool:
    """Validate SUI token address format (0x...::module::type)"""
    pattern = r"^0x[a-fA-F0-9]{64}::[a-zA-Z0-9_]+::[a-zA-Z0-9_]+$"
    return re.match(pattern, address) is not None

def shorten_address(address: str, chars: int = 6) -> str:
    """Shorten address while preserving type info"""
    if "::" in address:
        parts = address.split("::")
        return f"{parts[0][:chars]}...{parts[0][-chars:]}::{parts[1]}"
    return f"{address[:chars]}...{address[-chars:]}"

def format_alert(buy_data: dict, token_info: dict, group_settings: dict) -> str:
    """Generate formatted buy alert message"""
    # CORRECTED LINE WITH PROPER PARENTHESIS
    emoji_count = max(1, min(20, int(buy_data['usd_value'] / group_settings.get('emoji_step', 5))))
    
    emojis = group_settings.get('emoji', '🔥') * emoji_count
    
    return (
        f"{emojis} {token_info['symbol']} Buy! {emojis}\n\n"
        f"💰 Size ${buy_data['usd_value']:.2f} | {buy_data['amount']:.2f} SUI\n"
        f"👤 Buyer: [{shorten_address(buy_data['buyer'])}]({Config.SUI_EXPLORER}/tx/{buy_data['tx_hash']})\n"
        f"🔼 MCap ${token_info['market_cap']/1000:.2f}K\n"
        f"📊 TVL/Liq ${token_info['liquidity']/1000:.2f}K\n"
        f"📈 Price ${token_info['price']:.8f}\n"
        f"💧 SUI Price: ${token_info['sui_price']:.2f}\n\n"
        f"{format_links(group_settings)}\n"
        f"[Chart]({group_settings.get('chart_link', '')}) | "
        f"[Vol Bot]({Config.VOL_BOT_LINK}) | "
        f"[Trending](https://t.me/{Config.TRENDING_CHANNEL})"
    )

def format_links(settings: dict) -> str:
    """Format social media links"""
    links = []
    if settings.get('website'):
        links.append(f"[Website]({settings['website']})")
    if settings.get('telegram'):
        links.append(f"[Telegram]({settings['telegram']})")
    if settings.get('twitter'):
        links.append(f"[X]({settings['twitter']})")
    return " | ".join(links) + "\n" if links else ""
