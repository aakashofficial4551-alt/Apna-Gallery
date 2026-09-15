import os
import psycopg2
from psycopg2.extras import RealDictCursor

def get_db_connection():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set in environment variables")
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return conn

def init_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Skipping DB Init: No DATABASE_URL found.")
        return

    conn = get_db_connection()
    c = conn.cursor()

    # Users Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            role VARCHAR(20) DEFAULT 'user',
            bio TEXT DEFAULT '',
            country VARCHAR(50) DEFAULT 'India',
            profile_pic TEXT DEFAULT '',
            status VARCHAR(20) DEFAULT 'ACTIVE'
        );
    """)

    # Media Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            title VARCHAR(150) NOT NULL,
            category VARCHAR(50) NOT NULL,
            prompt TEXT DEFAULT '',
            uploaded_by VARCHAR(50) NOT NULL,
            approved INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0
        );
    """)

    # Likes Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id SERIAL PRIMARY KEY,
            media_id INTEGER NOT NULL,
            username VARCHAR(50) NOT NULL,
            UNIQUE(media_id, username)
        );
    """)

    # Stories Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) NOT NULL,
            filename TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Comments Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            media_id INTEGER NOT NULL,
            username VARCHAR(50) NOT NULL,
            comment_text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Audit Logs Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            actor_username VARCHAR(50),
            action VARCHAR(50),
            target_type VARCHAR(50),
            target_id INTEGER,
            metadata TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()
