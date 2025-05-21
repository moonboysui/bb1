import os

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    BOOST_WALLET = os.getenv("BOOST_WALLET")
    TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL")
    PORT = int(os.getenv("PORT", 10000))  # Render requires port 10000
    WEB_SERVER_URL = os.getenv("WEB_SERVER_URL", "https://your-render-app-name.onrender.com")
  
