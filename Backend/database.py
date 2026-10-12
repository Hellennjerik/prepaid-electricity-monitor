import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "kplc_meter.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT,
            meter_number TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meters (
            meter_number TEXT PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            kwh_used REAL DEFAULT 0.0,
            status TEXT DEFAULT 'CONNECTED',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_string TEXT UNIQUE NOT NULL,
            amount REAL NOT NULL,
            meter_number TEXT NOT NULL,
            status TEXT DEFAULT 'unused',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            amount_paid REAL NOT NULL,
            token_string TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("Database initialized at:", DB_PATH)

if __name__ == "__main__":
    init_db()