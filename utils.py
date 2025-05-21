from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def shorten_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address

def format_alert(tx: dict, emoji: str, token_address: str, media_id: str = None):
    num_emojis = min(int(tx['valueUSD'] // 5, 20)
    emojis = emoji * num_emojis
    explorer_link = f"{SUI_EXPLORER}{tx['txHash']}"
    
    message = (
        f"{emojis}\n"
        f"💰 {tx['amountSUI']} SUI (${tx['valueUSD']:.2f})\n"
        f"🧠 Buyer: {shorten_address(tx['buyer']} [View TX]({explorer_link})\n"
        f"📈 Price: ${tx['price']:.4f} | MC: ${tx['marketCap']:,.0f}"
    )
    
    buttons = [
        [InlineKeyboardButton("🛒 Buy Token", url=f"https://moonbags.finance/buy?token={token_address}")],
        [InlineKeyboardButton("🌍 Trending Channel", url=f"https://t.me/{TRENDING_CHANNEL}")]
    ]
    
    return message, InlineKeyboardMarkup(buttons), media_id
