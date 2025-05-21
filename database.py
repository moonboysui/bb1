import sqlite3
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def init_db():
    """Initialize database tables"""
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
                        group_id INTEGER PRIMARY KEY,
                        token_address TEXT NOT NULL,
                        min_buy REAL NOT NULL,
                        emoji TEXT NOT NULL,
                        emoji_step REAL NOT NULL,
                        media_id TEXT,
                        website TEXT,
                        telegram TEXT,
                        twitter TEXT,
                        chart_link TEXT)''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS boosts (
                        token_address TEXT PRIMARY KEY,
                        expires INTEGER NOT NULL,
                        active INTEGER DEFAULT 1)''')
        
        conn.commit()

def get_db():
    """Get database connection"""
    return sqlite3.connect('bot.db', check_same_thread=False)

def save_group_settings(group_id: int, settings: Dict[str, Any]):
    """Save group configuration to database"""
    with get_db() as conn:
        conn.execute('''INSERT OR REPLACE INTO groups VALUES (
                        :id, :token_address, :min_buy, :emoji, 
                        :emoji_step, :media_id, :website, 
                        :telegram, :twitter, :chart_link)''',
                    {
                        'id': group_id,
                        'token_address': settings.get('token_address'),
                        'min_buy': settings.get('min_buy', 0),
                        'emoji': settings.get('emoji', '🔥'),
                        'emoji_step': settings.get('emoji_step', 5),
                        'media_id': settings.get('media_id'),
                        'website': settings.get('website'),
                        'telegram': settings.get('telegram'),
                        'twitter': settings.get('twitter'),
                        'chart_link': settings.get('chart_link')
                    })
        conn.commit()

def clear_fake_symbols():
    """Clean up invalid token symbols"""
    with get_db() as conn:
        conn.execute("DELETE FROM groups WHERE token_symbol = 'TOKEN'")
        conn.commit()
