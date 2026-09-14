import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')

def get_db_connection():
    """Returns a secure database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """
    PHASE 2: DATABASE ARCHITECTURE
    Safely initializes the production-grade schema.
    Uses IF NOT EXISTS to strictly preserve all existing data.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. USERS TABLE
    # Preserves legacy columns (password, friends_count) while adding Phase 2 columns (role, status, timestamps)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'USER',
            bio TEXT,
            country TEXT,
            profile_pic TEXT,
            friends_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ACTIVE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP
        )
    ''')

    # 2. MEDIA TABLE
    # Preserves legacy columns (prompt, uploaded_by, approved) while adding Phase 2 columns (mime_type, size, visibility)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT NOT NULL,
            original_filename TEXT,
            title TEXT NOT NULL,
            prompt TEXT,
            category TEXT NOT NULL,
            mime_type TEXT,
            size INTEGER,
            uploaded_by TEXT NOT NULL,
            approved INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ACTIVE',
            visibility TEXT DEFAULT 'PUBLIC',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')

    # 3. LIKES TABLE (One-User-One-Like enforcement)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            UNIQUE(media_id, user_id)
        )
    ''')

    # 4. STORIES TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')

    # 5. COMMENTS TABLE
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE
        )
    ''')
    
    # 6. AUDIT LOGS TABLE (Phase 2 Requirement for Admin actions)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_username TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

if __name__ == '__main__':
    # Allows manual execution to setup schema
    init_db()
    print("Phase 2 Database Architecture initialized successfully.")
