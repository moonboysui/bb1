import math
from config import Config

def shorten_address(address, length=6):
    return f"{address[:length]}...{address[-length:]}" if address else ""

def format_alert(buy_data, token_info, group_settings):
    emoji_count = max(1, min(20, int(buy_data['usd_value'] / group_settings.get('emoji_step', 5)))
    emojis = group_settings.get('emoji', '🔥') * emoji_count
    
    return (
        f"{emojis} {token_info['symbol']} Buy! {emojis}\n\n"
        f"💰 Size ${buy_data['usd_value']:.2f} | {buy_data['amount']:.2f SUI\n"
        f"👤 Buyer [{shorten_address(buy_data['buyer'])}]({Config.SUI_EXPLORER}/tx/{buy_data['tx_hash']}) | "
        f"[Txn]({Config.SUI_EXPLORER}/tx/{buy_data['tx_hash']})\n"
        f"🔼 MCap ${token_info['market_cap']/1000:.2f}K\n"
        f"📊 TVL/Liq ${token_info['liquidity']/1000:.2f}K\n"
        f"📈 Price ${token_info['price']:.6f}\n"
        f"💧 SUI Price: ${token_info['sui_price']:.2f}\n\n"
        f"{format_links(group_settings)}"
        f"\n\n[Chart]({group_settings.get('chart_link', '')}) | "
        f"[Vol Bot]({Config.VOL_BOT_LINK}) | "
        f"[Trending](https://t.me/{Config.TRENDING_CHANNEL})"
    )

def format_links(settings):
    links = []
    if settings.get('website'): links.append(f"[Website]({settings['website']})")
    if settings.get('telegram_link'): links.append(f"[Telegram]({settings['telegram_link']})")
    if settings.get('twitter_link'): links.append(f"[X]({settings['twitter_link']})")
    return " | ".join(links) + "\n" if links else ""
