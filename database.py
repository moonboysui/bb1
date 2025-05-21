import sqlite3
import os
import logging
from config import Config

logger = logging.getLogger(__name__)

def ensure_db():
    db_dir = os.path.dirname(Config.DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

def init_db():
    ensure_db()
    with sqlite3.connect(Config.DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                min_buy REAL DEFAULT 0,
                emoji TEXT DEFAULT '🔥',
                emoji_step REAL DEFAULT 5,
                website TEXT,
                telegram TEXT,
                twitter TEXT,
                chart_link TEXT,
                media_id TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS boosts (
                token_address TEXT PRIMARY KEY,
                expiration INTEGER,
                boost_level INTEGER DEFAULT 1
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                tx_hash TEXT PRIMARY KEY,
                token_address TEXT,
                buyer TEXT,
                amount REAL,
                usd_value REAL,
                timestamp INTEGER
            )
        """)
        conn.commit()

def get_db():
    ensure_db()
    return sqlite3.connect(Config.DATABASE_PATH)

def save_group(group_id, data):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO groups VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            group_id,
            data['token_address'],
            data.get('symbol'),
            data.get('min_buy', 0),
            data.get('emoji', '🔥'),
            data.get('emoji_step', 5),
            data.get('website'),
            data.get('telegram'),
            data.get('twitter'),
            data.get('chart_link'),
            data.get('media_id')
        ))
        conn.commit()
