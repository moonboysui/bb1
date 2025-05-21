# database.py (updated)

def init_db():
    """Initialize database tables"""
    with get_db() as conn:
        # Updated groups table schema
        conn.execute('''CREATE TABLE IF NOT EXISTS groups (
                        group_id INTEGER PRIMARY KEY,
                        token_address TEXT NOT NULL,
                        token_symbol TEXT NOT NULL,  # Added this column
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

def clear_fake_symbols():
    """Clean up invalid token symbols"""
    with get_db() as conn:
        # Changed to use token_address instead of non-existent token_symbol
        conn.execute("DELETE FROM groups WHERE token_address LIKE '%fake%'")
        conn.commit()
