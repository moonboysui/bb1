import sqlite3
from contextlib import contextmanager

@contextmanager
def get_db():
    conn = sqlite3.connect('moonbags.db')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                token_address TEXT NOT NULL,
                min_buy_usd REAL DEFAULT 5.0,
                emoji TEXT DEFAULT '🔥',
                website TEXT,
                telegram_link TEXT,
                twitter_link TEXT,
                media_file_id TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS boosts (
                token_address TEXT PRIMARY KEY,
                expiration_timestamp INTEGER
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT,
                timestamp INTEGER,
                price REAL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS buys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT,
                timestamp INTEGER,
                usd_value REAL
            )
        ''')
