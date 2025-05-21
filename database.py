import sqlite3
import os
import logging
from config import Config

logger = logging.getLogger(__name__)

def ensure_db_directory():
    db_dir = os.path.dirname(Config.DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

def init_db():
    ensure_db_directory()
    with sqlite3.connect(Config.DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                token_address TEXT NOT NULL,
                token_symbol TEXT DEFAULT 'TOKEN',
                min_buy_usd REAL DEFAULT 0,
                emoji TEXT DEFAULT '🔥',
                website TEXT,
                telegram_link TEXT,
                twitter_link TEXT,
                chart_link TEXT,
                media_file_id TEXT,
                emoji_step REAL DEFAULT 5
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS boosts (
                token_address TEXT PRIMARY KEY,
                expiration INTEGER INTEGER NOT NULL,
                boost_level INTEGER DEFAULT 1
            )
        """)
        conn.commit()

def get_db():
    ensure_db_directory()
    return sqlite3.connect(Config.DATABASE_PATH)
