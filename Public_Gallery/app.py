import os
import secrets
import time
import urllib.parse
from collections import defaultdict, deque
import hmac
from uuid import uuid4
import datetime
import random
import smtplib
import re
import json
import html

import psycopg2
import psycopg2.extras
import cloudinary
import cloudinary.uploader
import requests
from dotenv import load_dotenv

from flask import (
    Flask, request, render_template, redirect, url_for, flash, session, jsonify, render_template_string
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import init_db, get_db_connection

# =========================================================
# BASE SETUP & CLOUDINARY
# =========================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)

app = Flask(__name__)
init_db()

# 💥 MILITARY GRADE COOKIE SECURITY 💥
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "development-only-secret-change-me"),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=True, 
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30)
)

# =========================================================
# 🛡️ THE TITANIUM FIREWALL & SESSION KILLER 🛡️
# =========================================================
request_tracker = defaultdict(list)
BANNED_IPS = set()

@app.before_request
def security_firewall_and_session_check():
    ip = request.remote_addr or "127.0.0.1"
    now = time.time()
    
    # Anti-DDoS
    request_tracker[ip] = [t for t in request_tracker[ip] if now - t < 60]
    if len(request_tracker[ip]) > 200:
        BANNED_IPS.add(ip)
    if ip in BANNED_IPS:
        return "Your IP has been permanently blocked for malicious activity. (Error 429)", 429
    request_tracker[ip].append(now)

    if request.endpoint in ['index', 'login', 'verify_otp', 'logout', 'static', 'api_search_suggest', 'privacy_policy', 'terms', 'about', 'dynamic_manifest'] or (request.path and request.path.startswith('/static/')):
        return

    # Strict CSRF Verification
    if request.method in ["POST", "PUT", "DELETE"]:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        session_token = session.get("csrf_token")
        if not token or not session_token or not hmac.compare_digest(token, session_token):
            if request.path.startswith('/api/'): return jsonify({"error": "Security Firewall: Invalid CSRF Token"}), 403
            else:
                flash("Security Firewall Blocked Your Request: Invalid Validation Token.", "error")
                return redirect(request.referrer or url_for('feed'))

    # GUILLOTINE ENGINE: SESSION TERMINATION
    if "username" in session:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT status FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        
        if user:
            c.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE username = %s", (session["username"],))
            conn.commit()
        conn.close()

        if not user:
            session.clear()
            if request.path.startswith('/api/'): return jsonify({"error": "Account deleted", "redirect": True}), 401
            flash("Your account has been deleted.", "error")
            return redirect(url_for('login'))
            
        if user['status'] == 'BANNED':
            session.clear()
            if request.path.startswith('/api/'): return jsonify({"error": "Account suspended", "redirect": True}), 401
            flash("Your account has been suspended by the Admin.", "error")
            return redirect(url_for('login'))
            
    if random.random() < 0.05: cleanup_database()
    if random.random() < 0.40: run_bot_engine() 

@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# =========================================================
# 👑 THE ADMIN HIJACK PROTOCOL
# =========================================================
def hijack_bot_post(media_id, interactor_username):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        c.execute("SELECT role FROM users WHERE username = %s", (interactor_username,))
        interactor = c.fetchone()
        if interactor and interactor['role'] == 'bot':
            return
            
        c.execute("SELECT uploaded_by FROM media WHERE id = %s", (media_id,))
        media = c.fetchone()
        if media:
            c.execute("SELECT role FROM users WHERE username = %s", (media['uploaded_by'],))
            uploader = c.fetchone()
            if uploader and uploader['role'] == 'bot':
                c.execute("SELECT username FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")
                admin_user = c.fetchone()
                if admin_user:
                    c.execute("UPDATE media SET uploaded_by = %s WHERE id = %s", (admin_user['username'], media_id))
                    conn.commit()
    except Exception as e: print("Hijack Protocol Error:", e)
    finally: conn.close()

# =========================================================
# DATABASE, BACKUP & FILTERS
# =========================================================
def create_database_backup():
    try:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        backup_data = {"timestamp": str(datetime.datetime.now()), "users": [], "media": [], "notifications": []}
        c.execute("SELECT username, email, role, is_verified, created_at, wallet_balance FROM users")
        backup_data["users"] = [dict(row) for row in c.fetchall()]
        c.execute("SELECT id, title, category, uploaded_by, likes, views, tips_received, visibility, created_at FROM media")
        backup_data["media"] = [dict(row) for row in c.fetchall()]
        backup_dir = os.path.join(BASE_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        file_path = os.path.join(backup_dir, f"backup_{datetime.date.today()}.json")
        with open(file_path, "w", encoding="utf-8") as f: json.dump(backup_data, f, default=str, indent=4)
        conn.close()
    except Exception as e: print(f"Backup Error: {e}")

@app.template_filter('format_text')
def format_text(text):
    if not text: return ""
    text = html.escape(str(text))
    text = re.sub(r'#(\w+)', r'<a href="/search?q=\1" style="color: var(--accent); text-decoration: none; font-weight: bold;">#\1</a>', text)
    text = re.sub(r'@(\w+)', r'<a href="/agent/\1" style="color: var(--warning); text-decoration: none; font-weight: bold;">@\1</a>', text)
    return text

@app.template_filter('is_online')
def is_online(last_active):
    if not last_active: return False
    return (datetime.datetime.now() - last_active).total_seconds() < 300

@app.template_filter('timeago')
def timeago(dt):
    if not dt: return ""
    now = datetime.datetime.now()
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60: return "Just now"
    elif seconds < 3600: return f"{int(seconds/60)}m ago"
    elif seconds < 86400: return f"{int(seconds/3600)}h ago"
    else: return f"{int(seconds/86400)}d ago"

def safe_alter(c, conn, query):
    try: 
        c.execute(query)
        conn.commit()
    except Exception: 
        conn.rollback()

def upgrade_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS stories (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, filename TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, message TEXT NOT NULL, link TEXT, is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS followers (id SERIAL PRIMARY KEY, follower VARCHAR(100) NOT NULL, following VARCHAR(100) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(follower, following))")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, sender VARCHAR(100) NOT NULL, receiver VARCHAR(100) NOT NULL, message TEXT NOT NULL, is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS bookmarks (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, media_id INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(username, media_id))")
    c.execute("CREATE TABLE IF NOT EXISTS global_chat (id SERIAL PRIMARY KEY, sender VARCHAR(100) NOT NULL, message TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS reports (id SERIAL PRIMARY KEY, media_id INTEGER NOT NULL, reported_by VARCHAR(100) NOT NULL, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(media_id, reported_by))")
    c.execute("CREATE TABLE IF NOT EXISTS comment_likes (id SERIAL PRIMARY KEY, comment_id INTEGER NOT NULL, username VARCHAR(100) NOT NULL, UNIQUE(comment_id, username))")
    c.execute("CREATE TABLE IF NOT EXISTS blocks (id SERIAL PRIMARY KEY, blocker VARCHAR(100) NOT NULL, blocked VARCHAR(100) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(blocker, blocked))")
    conn.commit()

    safe_alter(c, conn, "ALTER TABLE media ALTER COLUMN title TYPE TEXT")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    safe_alter(c, conn, "ALTER TABLE media ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    safe_alter(c, conn, "ALTER TABLE comments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    safe_alter(c, conn, "ALTER TABLE media ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'public'")
    safe_alter(c, conn, "ALTER TABLE media ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0")
    safe_alter(c, conn, "ALTER TABLE media ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE")
    safe_alter(c, conn, "ALTER TABLE comments ADD COLUMN IF NOT EXISTS likes INTEGER DEFAULT 0")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(120) UNIQUE")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_code VARCHAR(10) UNIQUE")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested TIMESTAMP")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS otp VARCHAR(6)")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'ACTIVE'")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user'")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS country VARCHAR(100)")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_pic TEXT")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS theme VARCHAR(20) DEFAULT 'cyan'")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_pic TEXT")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS website TEXT")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE")
    safe_alter(c, conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance INTEGER DEFAULT 100")
    safe_alter(c, conn, "ALTER TABLE media ADD COLUMN IF NOT EXISTS tips_received INTEGER DEFAULT 0")

    try:
        c.execute("UPDATE users SET role = 'user' WHERE role IS NULL")
        c.execute("UPDATE users SET status = 'ACTIVE' WHERE status IS NULL")
        c.execute("UPDATE users SET theme = 'cyan' WHERE theme IS NULL")
        c.execute("UPDATE users SET is_verified = FALSE WHERE is_verified IS NULL")
        c.execute("UPDATE users SET wallet_balance = 100 WHERE wallet_balance IS NULL")
        c.execute("UPDATE media SET visibility = 'public' WHERE visibility IS NULL")
        c.execute("UPDATE media SET views = 0 WHERE views IS NULL")
        c.execute("UPDATE media SET is_pinned = FALSE WHERE is_pinned IS NULL")
        c.execute("UPDATE media SET tips_received = 0 WHERE tips_received IS NULL")
        c.execute("UPDATE comments SET likes = 0 WHERE likes IS NULL")
        conn.commit()
    except: conn.rollback()
    
    try:
        c.execute("SELECT id FROM users WHERE user_code IS NULL")
        for row in c.fetchall():
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            c.execute("UPDATE users SET user_code = %s WHERE id = %s", (code, row['id']))
        conn.commit()
    except: conn.rollback()
    conn.close()

upgrade_db()

# =========================================================
# THE GRIM REAPER (1-HOUR PURGE & 12-HOUR BOT REINCARNATION)
# =========================================================
def cleanup_database():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM users WHERE role = 'bot' AND created_at < NOW() - INTERVAL '12 hours'")
        c.execute("DELETE FROM users WHERE username LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '3 days'")
        c.execute("DELETE FROM users WHERE role = 'user' AND username NOT LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '90 days'")
        c.execute("DELETE FROM users WHERE deletion_requested IS NOT NULL AND deletion_requested < NOW() - INTERVAL '7 days'")
        
        c.execute("""
            DELETE FROM media 
            WHERE uploaded_by IN (SELECT username FROM users WHERE role = 'bot') 
            AND created_at < NOW() - INTERVAL '1 hour' 
            AND likes = 0 
            AND tips_received = 0 
            AND id NOT IN (SELECT DISTINCT media_id FROM comments)
        """)
        
        c.execute("DELETE FROM media WHERE uploaded_by NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM comments WHERE username NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM likes WHERE username NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM followers WHERE follower NOT IN (SELECT username FROM users) OR following NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM messages WHERE sender NOT IN (SELECT username FROM users) OR receiver NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM blocks WHERE blocker NOT IN (SELECT username FROM users) OR blocked NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM global_chat WHERE created_at < NOW() - INTERVAL '5 minutes'")
        c.execute("DELETE FROM stories WHERE created_at < NOW() - INTERVAL '12 hours'")
        c.execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT 500)")
        conn.commit()
    except Exception as e: print("Cleanup Error:", e)
    finally: conn.close()

# =========================================================
# THE SMART PHANTOM ENGINE (AGGRESSIVE MODE & FIXED AI SEED) 🤖
# =========================================================
def run_bot_engine():
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        bot_names = ["Aria_Cyber", "Neo_Vibes", "Luna_Arts", "Zenith_Pro", "Kai_X", "Nova_King", "Echo_World", "Sage_Pixel", "Atlas_Lens", "Orion_Sky", "Lyra_Mood", "Rohan_Tech", "Kabir_Vibes", "Priya_Art"]
        
        bot_chats = [
            "Bhai yeh app sach me lit hai 🔥",
            "Koi online hai? Let's vibe ✨",
            "Just uploaded a new pic, check it out guys!",
            "Admin ne kya mast features banaye hain 👏",
            "Need some coins yaar, tip kardo koi 😂🪙",
            "Mausam kaisa hai tum logo ki taraf? 🌧️",
            "Anyone into cyberpunk aesthetics here? 🏙️",
            "Can't stop scrolling this feed ngl 🚀",
            "Hello everyone! Naya hu yahan, support kardo 🙌"
        ]
        
        c.execute("SELECT COUNT(id) as count FROM users WHERE role = 'bot'")
        bot_count = c.fetchone()['count']
        
        if bot_count < 50:
            name = f"{random.choice(bot_names)}_{random.randint(100, 9999)}"
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            c.execute("INSERT INTO users (username, password, user_code, role, is_verified, bio, wallet_balance) VALUES (%s, %s, %s, 'bot', TRUE, 'Creating vibes inside the matrix ✨', 5000)", (name, "botpass123", code))
            conn.commit()
            bot_username = name
        else:
            c.execute("SELECT username FROM users WHERE role = 'bot' ORDER BY RANDOM() LIMIT 1")
            bot_username = c.fetchone()['username']
            
        action = random.randint(1, 100)
        
        # 50% chance to create a new post to keep feed ALIVE
        if action <= 50: 
            cat = random.choice(["Photo", "Photo", "Photo", "Shayari"])
            if cat == "Photo":
                high_end_prompts = [
                    "A hyper-realistic cinematic portrait of a cyberpunk hacker in neon lights, 8k resolution, highly detailed, Unreal Engine 5 render",
                    "Breathtaking beautiful fantasy landscape with floating islands and glowing waterfalls, ethereal lighting, concept art",
                    "A sleek futuristic sports car driving through a neon-lit Tokyo street at night, rain reflections, ray tracing, 4k",
                    "Minimalist vaporwave aesthetic room with neon pink and blue lights, retro 80s arcade machine, 3d render",
                    "Close up photography of a mystical glowing crystal in a dark forest, magical atmosphere, macro lens",
                    "Anime style beautiful scenery of a quiet train station at sunset with pastel clouds, Makoto Shinkai style",
                    "Dark moody aesthetic photo of a cup of coffee on a wooden table with rain on the window, cinematic color grading"
                ]
                
                # 50-50 split between AI art and Aesthetic real photos
                if random.random() > 0.5:
                    prompt = random.choice(high_end_prompts)
                    encoded_prompt = urllib.parse.quote(prompt)
                    seed_val = random.randint(1, 999999) 
                    img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true&seed={seed_val}&width=800&height=1000"
                    
                    caption = random.choice([
                        f"Just generated this masterpiece! Kaisa laga? ✨ #AIArt #Aesthetics",
                        f"Generated this cool aesthetic 🔥 What do you think? #{bot_username}",
                        f"Speechless... AI art is the future 🔥 #DigitalArt"
                    ])
                else:
                    seed_val = random.randint(1, 999999)
                    img_url = f"https://picsum.photos/seed/{seed_val}/800/1000"
                    caption = random.choice([
                        "Nature ki vibe hi alag hai 🌲 #peace #sukoon", 
                        "Current mood ✨ #chill #aesthetic", 
                        "Aaj ka din was super productive! 😎 #grind", 
                        "Captured this moment. 📸 Kaisi hai pic? #photography #india"
                    ])
            else:
                img_url = "SHAYARI_TEXT"
                caption = random.choice([
                    "Words hit deeper when you're silent... 💔 #sad #shayari #deep",
                    "Zindagi ek safar hai suhana... ✨ #quotes #life",
                    "Akele chalne ka apna hi maza hai 🚶‍♂️ #motivation #hindi"
                ])
            
            c.execute("INSERT INTO media (filename, title, category, uploaded_by, approved, visibility, views, tips_received) VALUES (%s, %s, %s, %s, 1, 'public', %s, %s)",
                      (img_url, caption, cat, bot_username, random.randint(5, 50), 0))
                      
        elif 50 < action <= 70:
            c.execute("INSERT INTO global_chat (sender, message) VALUES (%s, %s)", (bot_username, random.choice(bot_chats)))
        elif 70 < action <= 80:
            c.execute("SELECT username FROM users WHERE role != 'bot' AND username != %s ORDER BY RANDOM() LIMIT 1", (bot_username,))
            target = c.fetchone()
            if target:
                try: c.execute("INSERT INTO followers (follower, following) VALUES (%s, %s)", (bot_username, target['username']))
                except: pass
        elif action > 80: 
            c.execute("SELECT id, uploaded_by, title, category FROM media WHERE uploaded_by != %s ORDER BY RANDOM() LIMIT 1", (bot_username,))
            media = c.fetchone()
            if media:
                if random.random() > 0.5: 
                    c.execute("UPDATE users SET wallet_balance = wallet_balance - 10 WHERE username = %s AND wallet_balance >= 10", (bot_username,))
                    if c.rowcount == 1:
                        c.execute("UPDATE users SET wallet_balance = wallet_balance + 10 WHERE username = %s", (media['uploaded_by'],))
                        c.execute("UPDATE media SET tips_received = COALESCE(tips_received, 0) + 10 WHERE id = %s", (media['id'],))
                        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", 
                                  (media['uploaded_by'], f"💰 You received 10 Coins from {bot_username} for '{media['title'][:15]}...'", f"/post/{media['id']}"))
                else: 
                    try:
                        c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT DO NOTHING", (media['id'], bot_username))
                        if c.rowcount == 1: 
                            c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media['id'],))
                            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", 
                                      (media['uploaded_by'], f"❤️ {bot_username} liked your post!", f"/post/{media['id']}"))
                    except: pass
        conn.commit()
    except Exception as e: print("Bot Engine Error:", e)
    finally: conn.close()

def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

@app.context_processor
def inject_global_vars():
    def get_avatar(profile_pic, username):
        if profile_pic and str(profile_pic).strip() != "": return profile_pic
        encoded_name = urllib.parse.quote(str(username))
        return f"https://api.dicebear.com/7.x/avataaars/svg?seed={encoded_name}&backgroundColor=1e293b"

    vars_dict = {"csrf_token": get_csrf_token, "unread_notifications": 0, "unread_messages": 0, "my_theme": "cyan", "get_avatar": get_avatar, "my_website": "", "my_blocked_users": [], "my_wallet": 0}
    if session.get("username"):
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(id) as cnt FROM notifications WHERE username = %s AND is_read = FALSE", (session.get("username"),))
            res1 = c.fetchone()
            if res1: vars_dict["unread_notifications"] = res1['cnt']
            
            c.execute("SELECT COUNT(id) as cnt FROM messages WHERE receiver = %s AND is_read = FALSE", (session.get("username"),))
            res2 = c.fetchone()
            if res2: vars_dict["unread_messages"] = res2['cnt']
            
            c.execute("SELECT theme, bio, profile_pic, website, is_verified, wallet_balance FROM users WHERE username = %s", (session.get("username"),))
            u_data = c.fetchone()
            if u_data:
                vars_dict["my_theme"] = u_data["theme"]
                vars_dict["my_bio"] = u_data["bio"]
                vars_dict["my_profile_pic"] = u_data["profile_pic"]
                vars_dict["my_website"] = u_data.get("website", "")
                vars_dict["my_is_verified"] = u_data.get("is_verified", False)
                vars_dict["my_wallet"] = u_data.get("wallet_balance", 0)
                
            c.execute("SELECT blocked FROM blocks WHERE blocker = %s", (session.get("username"),))
            vars_dict["my_blocked_users"] = [r['blocked'] for r in c.fetchall()]
            conn.close()
        except: pass
    return vars_dict

def save_uploaded_file(file, category="Photo"):
    if not file or not file.filename: return None
    try:
        if category == "Photo" or category == "Image":
            upload_result = cloudinary.uploader.upload(file, resource_type="image", quality="auto", fetch_format="auto")
        else:
            upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        return upload_result["secure_url"]
    except Exception as e:
        print(f"Cloudinary Error: {e}")
        return None

def current_user_is_admin():
    return session.get("role") == "admin"

def sync_admin_session():
    if "username" in session:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT role FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        conn.close()
        if user:
            session["role"] = user["role"]
            session["is_admin"] = (user["role"] == "admin")

# =========================================================
# PUBLIC ROUTES
# =========================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/terms")
def terms():
    return render_template("terms.html", hide_navbar=True)

@app.route("/about")
def about():
    return render_template("about.html", hide_navbar=True)

@app.route("/privacy")
def privacy_policy():
    return render_template("privacy.html", hide_navbar=True)

@app.route("/manifest.json")
def dynamic_manifest():
    app_name = "VibeX"
    manifest_data = {
        "name": app_name,
        "short_name": app_name,
        "description": "The ultimate creator ecosystem and social vault.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]
    }
    return jsonify(manifest_data)

# =========================================================
# ROUTES: AUTHENTICATION & SETTINGS
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"): return redirect(url_for("feed"))
    if request.method == "POST":
        action = request.form.get("action")
        username = html.escape(request.form.get("username", "").strip())
        password = request.form.get("password", "")
        
        if action == "guest":
            session.clear()
            guest_name = f"Guest-{uuid4().hex[:8]}"
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO users (username, password, user_code, role, wallet_balance) VALUES (%s, %s, %s, 'user', 100)", (guest_name, "guest", code))
            conn.commit()
            conn.close()
            session.permanent = True
            session.update({"username": guest_name, "is_registered": False, "is_admin": False, "role": "user"})
            flash("Welcome! You've received 100 Free Coins 🪙", "success")
            return redirect(url_for("feed"))

        if action == "register":
            email = html.escape(request.form.get("email", "").strip())
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO users (username, email, password, user_code, role, wallet_balance) VALUES (%s, %s, %s, %s, 'user', 100)", 
                          (username, email, generate_password_hash(password), code))
                conn.commit()
                session.permanent = True
                session.update({"username": username, "is_registered": True, "is_admin": False, "role": "user"})
                flash("Account created! You got 100 Welcome Coins 🪙", "success")
                return redirect(url_for("feed"))
            except:
                flash("Username or Email exists.", "error")
            finally: conn.close()
            return redirect(url_for("login"))

        if action == "login":
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE username = %s OR email = %s", (username, username))
            user = c.fetchone()
            
            if user and check_password_hash(user["password"], password):
                if user.get("status") == "BANNED":
                    flash("Account suspended.", "error")
                    return redirect(url_for("login"))
                if user.get("deletion_requested"):
                    c.execute("UPDATE users SET deletion_requested = NULL WHERE id = %s", (user['id'],))
                    conn.commit()
                    flash("Account deletion cancelled.", "success")
                    
                is_guest = user["username"].startswith("Guest-")
                session.permanent = True
                session.update({"username": user["username"], "is_registered": not is_guest, "is_admin": (user["role"] == "admin"), "role": user["role"]})
                conn.close()
                return redirect(url_for("feed"))
            conn.close()
            flash("Invalid credentials.", "error")
            
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/settings", methods=["POST"])
def settings():
    if "username" not in session: return redirect(url_for("login"))
    action = request.form.get("action")
    conn = get_db_connection()
    c = conn.cursor()
    
    if action == "update_profile":
        bio = html.escape(request.form.get("bio", ""))
        website = html.escape(request.form.get("website", ""))
        theme = html.escape(request.form.get("theme", "cyan"))
        c.execute("UPDATE users SET bio = %s, theme = %s, website = %s WHERE username = %s", 
                  (bio, theme, website, session["username"]))
        flash("Profile Settings Updated!", "success")
        
    elif action == "change_password":
        new_pass = request.form.get("new_password")
        c.execute("UPDATE users SET password = %s WHERE username = %s", (generate_password_hash(new_pass), session["username"]))
        flash("Password Changed Successfully!", "success")
        
    elif action == "upgrade_guest":
        new_username = html.escape(request.form.get("new_username", "").strip())
        email = html.escape(request.form.get("email", "").strip())
        password = request.form.get("password", "")
        old_username = session["username"]
        c.execute("SELECT id FROM users WHERE username = %s OR email = %s", (new_username, email))
        if c.fetchone():
            flash("Username or Email already exists!", "error")
        else:
            try:
                c.execute("UPDATE users SET username = %s, email = %s, password = %s WHERE username = %s", (new_username, email, generate_password_hash(password), old_username))
                tables_to_update = [
                    ("media", "uploaded_by"), ("comments", "username"), ("likes", "username"),
                    ("followers", "follower"), ("followers", "following"), ("messages", "sender"),
                    ("messages", "receiver"), ("bookmarks", "username"), ("global_chat", "sender"),
                    ("reports", "reported_by"), ("comment_likes", "username"), ("blocks", "blocker"),
                    ("blocks", "blocked"), ("notifications", "username"), ("stories", "username")
                ]
                for table, col in tables_to_update:
                    c.execute(f"UPDATE {table} SET {col} = %s WHERE {col} = %s", (new_username, old_username))
                session.update({"username": new_username, "is_registered": True})
                flash("Account Upgraded Permanently! 🎉", "success")
            except Exception: flash("Error upgrading account.", "error")

    elif action == "unblock_user":
        target = request.form.get("blocked_username")
        c.execute("DELETE FROM blocks WHERE blocker = %s AND blocked = %s", (session["username"], target))
        flash(f"User unblocked.", "success")
        
    elif action == "delete_account":
        c.execute("UPDATE users SET deletion_requested = CURRENT_TIMESTAMP WHERE username = %s", (session["username"],))
        session.clear()
        conn.commit()
        conn.close()
        flash("Account scheduled for deletion in 7 days.", "warning")
        return redirect(url_for("index"))
        
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/buy_verification", methods=["POST"])
def buy_verification():
    if "username" not in session: return redirect(url_for("login"))
    if str(session["username"]).startswith("Guest-"):
        flash("Guest accounts cannot be verified. Please upgrade your account first! ⭐", "error")
        return redirect(request.referrer or url_for("dashboard"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT wallet_balance, is_verified FROM users WHERE username = %s", (session["username"],))
    user = c.fetchone()
    is_admin = session.get('role') == 'admin'
    
    if user['is_verified']:
        flash("You are already verified! ✅", "success")
    elif is_admin or user['wallet_balance'] >= 1000:
        if not is_admin: c.execute("UPDATE users SET wallet_balance = wallet_balance - 1000, is_verified = TRUE WHERE username = %s", (session["username"],))
        else: c.execute("UPDATE users SET is_verified = TRUE WHERE username = %s", (session["username"],))
        conn.commit()
        flash("Congratulations! You bought the Verified Badge ✅", "success")
    else:
        flash("Not enough coins! You need 1000 🪙 to get verified.", "error")
    conn.close()
    return redirect(request.referrer or url_for("dashboard"))

# =========================================================
# ASSET MANAGEMENT, POST VIEW & TIPPING 
# =========================================================
@app.route("/upload_asset", methods=["POST"])
def upload_asset():
    if "username" not in session: return redirect(url_for("login"))
    try:
        category = request.form.get("category", "Photo")
        title = html.escape(request.form.get("title", "Untitled"))
        visibility = request.form.get("visibility", "public")
        file = request.files.get("media")
        filename = "SHAYARI_TEXT"

        if category != 'Shayari':
            if file and file.filename != "":
                filename = save_uploaded_file(file, category)
                if not filename:
                    flash("Upload Failed! Format not supported.", "error")
                    return redirect(request.referrer or url_for("dashboard"))
        
        is_approved = 1 if current_user_is_admin() else 0
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned, tips_received) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, FALSE, 0) RETURNING id",
                  (filename, title, category, "", session.get("username"), is_approved, visibility))
        new_media_id = c.fetchone()['id']
        
        mentions = set(re.findall(r'@(\w+)', title))
        for m in mentions:
            if m != session["username"]:
                c.execute("SELECT id FROM users WHERE username = %s", (m,))
                if c.fetchone(): c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you!", f"/post/{new_media_id}"))
        conn.commit()
        conn.close()
        flash(f"Asset published as {visibility.upper()}!" if is_approved else "Asset sent to Admin for approval.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Server Alert: {str(e)[:150]}", "error")
    return redirect(request.referrer or url_for("feed"))

@app.route("/post/<int:media_id>")
def view_post(media_id):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    block_filter = "m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND m.uploaded_by NOT IN (SELECT blocker FROM blocks WHERE blocked = %s)"
    c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.id = %s AND {block_filter}", (media_id, session["username"], session["username"]))
    post = c.fetchone()
    
    if not post:
        conn.close()
        return render_template("404.html")
        
    post_dict = dict(post)
    c.execute("SELECT c.*, u.role, u.is_verified FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = %s ORDER BY c.id ASC", (media_id,))
    post_dict['comments'] = [dict(row) for row in c.fetchall()]
    
    c.execute("SELECT id FROM bookmarks WHERE username = %s AND media_id = %s", (session["username"], media_id))
    post_dict['is_saved'] = bool(c.fetchone())
    
    post_dict['created_at'] = timeago(post_dict['created_at'])
    for comm in post_dict['comments']: comm['created_at'] = timeago(comm['created_at'])
    
    conn.close()
    return render_template("single_post.html", post=post_dict, hide_navbar=True)

@app.route("/tip/<int:media_id>", methods=["POST"])
def tip_creator(media_id):
    if "username" not in session: return jsonify({"error": "Login required."}), 401
    tipper = str(session["username"])
    if tipper.startswith("Guest-"): return jsonify({"error": "Register a permanent account to use Coins! 🪙"}), 403
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    is_admin = session.get('role') == 'admin'
    c.execute("SELECT wallet_balance FROM users WHERE username = %s", (tipper,))
    user_data = c.fetchone()
    balance = user_data['wallet_balance'] if user_data else 0
    if not is_admin and balance < 10:
        conn.close()
        return jsonify({"error": "Insufficient Coins! You need at least 10 🪙."}), 400
    c.execute("SELECT uploaded_by, title FROM media WHERE id = %s", (media_id,))
    media = c.fetchone()
    if not media:
        conn.close()
        return jsonify({"error": "Media not found."}), 404
    creator = media['uploaded_by']
    if creator == tipper:
        conn.close()
        return jsonify({"error": "You cannot tip your own post!"}), 400
    try:
        if not is_admin: c.execute("UPDATE users SET wallet_balance = wallet_balance - 10 WHERE username = %s", (tipper,))
        c.execute("UPDATE users SET wallet_balance = wallet_balance + 10 WHERE username = %s", (creator,))
        c.execute("UPDATE media SET tips_received = COALESCE(tips_received, 0) + 10 WHERE id = %s", (media_id,))
        title_snippet = (media['title'][:15] + '...') if media['title'] else 'your post'
        badge = "👑 (Admin)" if is_admin else ""
        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", 
                  (creator, f"💰 You received 10 Coins from {tipper} {badge} for '{title_snippet}'", f"/post/{media_id}"))
        conn.commit()
        hijack_bot_post(media_id, tipper)
        success = True
    except Exception as e:
        conn.rollback()
        success = False
    c.execute("SELECT tips_received FROM media WHERE id = %s", (media_id,))
    new_tips = c.fetchone()['tips_received']
    conn.close()
    return jsonify({"success": success, "new_tips": new_tips})

@app.route("/api/view/<int:media_id>", methods=["POST"])
def add_view(media_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE media SET views = views + 1 WHERE id = %s", (media_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/add_comment/<int:media_id>", methods=["POST"])
def add_comment(media_id):
    if "username" not in session: return redirect(url_for("login"))
    text = html.escape(request.form.get("comment_text", "").strip())
    if text:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("INSERT INTO comments (media_id, username, comment_text) VALUES (%s, %s, %s)", (media_id, session["username"], text[:200]))
        c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (media_id,))
        media_info = c.fetchone()
        
        if media_info and media_info['uploaded_by'] != session["username"]:
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"💬 {session['username']} commented on {media_info['title'][:15]}...", f"/post/{media_id}"))
            
        mentions = set(re.findall(r'@(\w+)', text))
        for m in mentions:
            if m != session["username"]:
                c.execute("SELECT id FROM users WHERE username = %s", (m,))
                if c.fetchone():
                    c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you in a comment!", f"/post/{media_id}"))
        conn.commit()
        conn.close()
        hijack_bot_post(media_id, session["username"])
        flash("Comment posted!", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/like/<int:media_id>", methods=["POST"])
def like(media_id):
    if "username" not in session: return jsonify({"error": "Login required."}), 401
    username = str(session["username"])
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT (media_id, username) DO NOTHING", (media_id, username))
    if c.rowcount == 1:
        c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media_id,))
        c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (media_id,))
        media_info = c.fetchone()
        if media_info and media_info['uploaded_by'] != username:
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"❤️ {username} liked your asset: {media_info['title'][:15]}...", f"/post/{media_id}"))
        conn.commit()
        liked_now = True
        hijack_bot_post(media_id, username)
    else: liked_now = False
    c.execute("SELECT likes FROM media WHERE id = %s", (media_id,))
    likes = c.fetchone()["likes"]
    conn.close()
    return jsonify({"likes": likes, "liked": liked_now})

@app.route("/like_comment/<int:comment_id>", methods=["POST"])
def like_comment(comment_id):
    if "username" not in session: return jsonify({"error": "Login required."}), 401
    username = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO comment_likes (comment_id, username) VALUES (%s, %s) ON CONFLICT (comment_id, username) DO NOTHING", (comment_id, username))
    if c.rowcount == 1:
        c.execute("UPDATE comments SET likes = COALESCE(likes, 0) + 1 WHERE id = %s", (comment_id,))
        c.execute("SELECT username, media_id FROM comments WHERE id = %s", (comment_id,))
        comment_info = c.fetchone()
        if comment_info and comment_info['username'] != username:
            c.execute("SELECT category FROM media WHERE id = %s", (comment_info['media_id'],))
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (comment_info['username'], f"❤️ {username} liked your comment!", f"/post/{comment_info['media_id']}"))
        conn.commit()
    c.execute("SELECT likes FROM comments WHERE id = %s", (comment_id,))
    likes = c.fetchone()["likes"]
    conn.close()
    return jsonify({"likes": likes or 0})

@app.route("/edit_post/<int:media_id>", methods=["POST"])
def edit_post(media_id):
    if "username" not in session: return redirect(url_for("login"))
    new_title = html.escape(request.form.get("title", "").strip())
    if new_title:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE media SET title = %s WHERE id = %s AND uploaded_by = %s", (new_title, media_id, session["username"]))
        conn.commit()
        conn.close()
        flash("Post updated successfully!", "success")
    return redirect(request.referrer or url_for("profile"))

@app.route("/pin_post/<int:media_id>", methods=["POST"])
def pin_post(media_id):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE media SET is_pinned = FALSE WHERE uploaded_by = %s", (session["username"],))
    c.execute("UPDATE media SET is_pinned = TRUE WHERE id = %s AND uploaded_by = %s", (media_id, session["username"]))
    conn.commit()
    conn.close()
    flash("Post Pinned!", "success")
    return redirect(request.referrer or url_for("profile"))

@app.route("/delete_own/<int:media_id>", methods=["POST"])
def delete_own(media_id):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM media WHERE id = %s AND uploaded_by = %s", (media_id, session["username"]))
    conn.commit()
    conn.close()
    flash("Asset permanently deleted.", "success")
    return redirect(request.referrer or url_for("profile"))

@app.route("/report/<int:media_id>", methods=["POST"])
def report_asset(media_id):
    if "username" not in session: return jsonify({"error": "Login required"}), 401
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO reports (media_id, reported_by, reason) VALUES (%s, %s, 'Inappropriate Content')", (media_id, session["username"]))
        conn.commit()
        return jsonify({"success": True})
    except:
        return jsonify({"error": "Already reported"}), 400
    finally: conn.close()

# =========================================================
# CORE ROUTES (FEED & LEADERBOARD)
# =========================================================
@app.route("/feed")
def feed():
    if "username" not in session: return redirect(url_for("login"))
    tab = request.args.get("tab", "foryou")
    return render_template("feed.html", current_tab=tab)

@app.route("/api/feed_data")
def api_feed_data():
    if "username" not in session: return jsonify([])
    tab = request.args.get("tab", "foryou")
    offset = int(request.args.get("offset", 0))
    limit = 10 
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    block_filter = "m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND m.uploaded_by NOT IN (SELECT blocker FROM blocks WHERE blocked = %s)"
    
    ai_order_logic = "m.is_pinned DESC, m.id DESC"
    
    if tab == "global":
        c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY {ai_order_logic} LIMIT %s OFFSET %s", (session["username"], session["username"], limit, offset))
        feed_posts = [dict(row) for row in c.fetchall()]
    else:
        c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username JOIN followers f ON f.following = m.uploaded_by WHERE f.follower = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.id DESC LIMIT %s OFFSET %s", (session["username"], session["username"], session["username"], limit, offset))
        feed_posts = [dict(row) for row in c.fetchall()]
        if not feed_posts and offset == 0:
            c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY {ai_order_logic} LIMIT 10", (session["username"], session["username"]))
            feed_posts = [dict(row) for row in c.fetchall()]

    post_ids = [p['id'] for p in feed_posts]
    comments = defaultdict(list)
    if post_ids:
        c.execute("SELECT c.*, u.role, u.is_verified FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = ANY(%s) AND c.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND c.username NOT IN (SELECT blocker FROM blocks WHERE blocked = %s) ORDER BY c.id ASC", (post_ids, session["username"], session["username"]))
        for comment in c.fetchall(): comments[comment["media_id"]].append(dict(comment))
    
    c.execute("SELECT media_id FROM bookmarks WHERE username = %s", (session["username"],))
    saved_ids = [row['media_id'] for row in c.fetchall()]
    conn.close()
    
    for post in feed_posts:
        post['created_at'] = timeago(post['created_at'])
        post['comments'] = comments[post['id']]
        for comm in post['comments']: comm['created_at'] = timeago(comm['created_at'])
        post['is_saved'] = post['id'] in saved_ids
    return jsonify(feed_posts)

@app.route("/leaderboard")
def leaderboard():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT username, profile_pic, is_verified, wallet_balance, role FROM users WHERE role != 'bot' AND username NOT LIKE 'Guest-%%' ORDER BY wallet_balance DESC LIMIT 10")
    wealthy = [dict(row) for row in c.fetchall()]
    c.execute("""
        SELECT u.username, u.profile_pic, u.is_verified, COUNT(f.id) as follower_count 
        FROM users u LEFT JOIN followers f ON u.username = f.following 
        WHERE u.role != 'bot' AND u.username NOT LIKE 'Guest-%%'
        GROUP BY u.username, u.profile_pic, u.is_verified 
        ORDER BY follower_count DESC LIMIT 10
    """)
    famous = [dict(row) for row in c.fetchall()]
    conn.close()
    return render_template("leaderboard.html", wealthy=wealthy, famous=famous, hide_navbar=True)

@app.route("/reels")
def reels():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.category = 'Video' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY RANDOM() LIMIT 20", (session["username"],))
    videos = c.fetchall()
    conn.close()
    return render_template("reels.html", videos=videos)

@app.route("/explore")
def explore():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    c.execute("SELECT username, profile_pic, bio, is_verified FROM users WHERE role = 'user' AND is_verified = TRUE AND username NOT LIKE 'Guest-%%' ORDER BY wallet_balance DESC LIMIT 1")
    spotlight = c.fetchone()
    
    c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, m.views, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY RANDOM() LIMIT 40", (session["username"],))
    explore_posts = [dict(row) for row in c.fetchall()]
    c.execute("SELECT title FROM media WHERE approved = 1 AND visibility = 'public'")
    all_titles = c.fetchall()
    
    tag_counts = defaultdict(int)
    for row in all_titles:
        if row['title']:
            tags = re.findall(r'#(\w+)', row['title'])
            for t in tags: tag_counts[t.lower()] += 1
    trending_tags = [tag for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:6]]
    conn.close()
    return render_template("explore.html", posts=explore_posts, trending_tags=trending_tags, spotlight=spotlight)

@app.route("/gallery/<category>", methods=["GET", "POST"])
def gallery(category):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == "POST":
        try:
            filename = "SHAYARI_TEXT"
            if category != 'Shayari':
                file = request.files.get("media")
                if file and file.filename != "":
                    filename = save_uploaded_file(file, category)
                    if not filename:
                        flash("Upload Failed! Check API keys.", "error")
                        return redirect(url_for("gallery", category=category))
            
            is_approved = 1 if current_user_is_admin() else 0
            visibility = request.form.get("visibility", "public")
            c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, FALSE)",
                      (filename, html.escape(request.form.get("title", "Untitled")), category, "", session.get("username"), is_approved, visibility))
            conn.commit()
            flash("File live!" if is_approved else "Sent to Admin for approval.", "success")
        except Exception as e:
            conn.rollback()
            flash(f"Upload System Fault: {str(e)[:100]}", "error")
        return redirect(url_for("gallery", category=category))

    c.execute("SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.category = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY m.id DESC", (category, session["username"]))
    if category == 'Shayari': c.execute("SELECT m.*, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.category = %s AND m.approved = 1 AND m.visibility = 'public' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY m.id DESC", (category, session["username"]))
    media_files = c.fetchall()
    c.execute("SELECT c.*, u.role, u.is_verified FROM comments c JOIN users u ON c.username = u.username ORDER BY c.id ASC")
    comments_db = c.fetchall()
    conn.close()
    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    return render_template("gallery.html", media_files=media_files, category=category, comments=comments)

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    sync_admin_session()
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, title, filename, category, likes, views FROM media WHERE approved = 1 AND visibility = 'public' AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
        trending = c.fetchall()
        c.execute("SELECT s.id, s.username, s.filename, s.created_at, u.profile_pic, u.role, u.is_verified FROM stories s JOIN users u ON s.username = u.username WHERE s.created_at >= NOW() - INTERVAL '12 hours' AND s.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY s.id DESC", (session["username"],))
        stories = c.fetchall()
        conn.close()
        return render_template("dashboard.html", trending=trending, stories=stories)
    except Exception as e:
        return f"<div style='color:#f43f5e; padding:50px; text-align:center;'><h1>Dashboard Engine Error</h1><p>{str(e)}</p><a href='/feed'>Go back to Feed</a></div>"

@app.route("/notifications")
def notifications():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM notifications WHERE username = %s ORDER BY id DESC LIMIT 50", (session["username"],))
    notifs = c.fetchall()
    c.execute("UPDATE notifications SET is_read = TRUE WHERE username = %s AND is_read = FALSE", (session["username"],))
    conn.commit()
    conn.close()
    return render_template("notifications.html", notifications=notifs, hide_navbar=True)

@app.route("/api/unread_notifs")
def api_unread_notifs():
    if "username" not in session: return jsonify({"unread": 0})
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(id) as cnt FROM notifications WHERE username = %s AND is_read = FALSE", (session["username"],))
    res = c.fetchone()
    unread = res['cnt'] if res else 0
    conn.close()
    return jsonify({"unread": unread})

@app.route("/clear_notifications", methods=["POST"])
def clear_notifications():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM notifications WHERE username = %s", (session["username"],))
    conn.commit()
    conn.close()
    flash("Inbox cleared!", "success")
    return redirect(url_for("notifications"))

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "avatar" and "profile_pic" in request.files:
            url = save_uploaded_file(request.files["profile_pic"], "Photo")
            if url: c.execute("UPDATE users SET profile_pic = %s WHERE username = %s", (url, session["username"]))
        elif action == "cover" and "cover_pic" in request.files:
            url = save_uploaded_file(request.files["cover_pic"], "Photo")
            if url: c.execute("UPDATE users SET cover_pic = %s WHERE username = %s", (url, session["username"]))
        elif action == "story" and "story_media" in request.files:
            url = save_uploaded_file(request.files["story_media"], "Photo")
            if url: c.execute("INSERT INTO stories (username, filename) VALUES (%s, %s)", (session["username"], url))
        conn.commit()
        return redirect(url_for("profile"))

    c.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
    user = c.fetchone()
    c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY is_pinned DESC, id DESC", (session["username"],))
    my_uploads = c.fetchall()
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (session["username"],))
    followers_count = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (session["username"],))
    following_count = c.fetchone()['cnt']
    c.execute("SELECT m.* FROM bookmarks b JOIN media m ON m.id = b.media_id WHERE b.username = %s ORDER BY b.id DESC", (session["username"],))
    saved_uploads = c.fetchall()
    conn.close()
    return render_template("profile.html", user=user, my_uploads=my_uploads, saved_uploads=saved_uploads, post_count=len(my_uploads), followers_count=followers_count, following_count=following_count)

@app.route("/agent/<username>")
def agent_profile(username):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM blocks WHERE (blocker = %s AND blocked = %s) OR (blocker = %s AND blocked = %s)", (session["username"], username, username, session["username"]))
    if c.fetchone():
        flash("You cannot view this profile.", "error")
        conn.close()
        return redirect(url_for("feed"))
        
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    agent = c.fetchone()
    if not agent:
        flash("Agent not found.", "error")
        conn.close()
        return redirect(url_for("feed"))
        
    c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (session["username"], username))
    is_following = bool(c.fetchone())
    if is_following:
        c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
    else:
        c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
    agent_uploads = c.fetchall()
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
    followers_count = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (username,))
    following_count = c.fetchone()['cnt']
    conn.close()
    return render_template("agent.html", agent=agent, uploads=agent_uploads, post_count=len(agent_uploads), followers_count=followers_count, following_count=following_count, is_following=is_following)

@app.route("/follow/<username>", methods=["POST"])
def follow(username):
    if "username" not in session: return jsonify({"error": "Login required"}), 401
    current_user = session["username"]
    if current_user == username: return jsonify({"error": "Cannot follow yourself"}), 400
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = %s", (username,))
    if not c.fetchone(): return jsonify({"error": "User not found"}), 404
    c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (current_user, username))
    is_following = c.fetchone()
    if is_following:
        c.execute("DELETE FROM followers WHERE follower = %s AND following = %s", (current_user, username))
        following_now = False
    else:
        c.execute("INSERT INTO followers (follower, following) VALUES (%s, %s)", (current_user, username))
        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (username, f"👤 {current_user} started following you!", f"/agent/{current_user}"))
        following_now = True
    conn.commit()
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
    followers_count = c.fetchone()['cnt']
    conn.close()
    return jsonify({"followers": followers_count, "following_now": following_now})

@app.route("/api/network/<action_type>/<username>")
def api_network(action_type, username):
    if "username" not in session: return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    if action_type == "followers":
        c.execute("SELECT u.username, u.profile_pic, u.role, u.is_verified FROM users u JOIN followers f ON u.username = f.follower WHERE f.following = %s", (username,))
    else:
        c.execute("SELECT u.username, u.profile_pic, u.role, u.is_verified FROM users u JOIN followers f ON u.username = f.following WHERE f.follower = %s", (username,))
    results = c.fetchall()
    conn.close()
    return jsonify(results)

@app.route("/inbox")
def inbox():
    if "username" not in session: return redirect(url_for("login"))
    me = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT u.username, u.profile_pic, u.role, u.last_active, u.is_verified
        FROM users u 
        WHERE u.username IN (SELECT receiver FROM messages WHERE sender = %s UNION SELECT sender FROM messages WHERE receiver = %s)
        AND u.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s)
    """, (me, me, me))
    contacts = c.fetchall()
    for contact in contacts:
        c.execute("SELECT COUNT(id) as cnt FROM messages WHERE sender = %s AND receiver = %s AND is_read = FALSE", (contact['username'], me))
        contact['unread'] = c.fetchone()['cnt']
    conn.close()
    return render_template("inbox.html", contacts=contacts, hide_navbar=True)

@app.route("/chat/<username>", methods=["GET", "POST"])
def chat(username):
    if "username" not in session: return redirect(url_for("login"))
    me = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM blocks WHERE (blocker = %s AND blocked = %s) OR (blocker = %s AND blocked = %s)", (me, username, username, me))
    if c.fetchone():
        flash("You cannot message this user.", "error")
        conn.close()
        return redirect(url_for("inbox"))
    if request.method == "POST":
        msg = html.escape(request.form.get("message", "").strip())
        if msg:
            c.execute("INSERT INTO messages (sender, receiver, message) VALUES (%s, %s, %s)", (me, username, msg))
            conn.commit()
        return redirect(url_for("chat", username=username))
    c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
    conn.commit()
    c.execute("SELECT username, profile_pic, role, last_active, is_verified FROM users WHERE username = %s", (username,))
    contact = c.fetchone()
    conn.close()
    if not contact: return redirect(url_for("inbox"))
    return render_template("chat.html", contact=contact, hide_navbar=True)

@app.route("/api/chat_history/<username>")
def api_chat_history(username):
    if "username" not in session: return jsonify([])
    me = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, sender, message, TO_CHAR(created_at, 'HH24:MI') as time, is_read FROM messages WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s) ORDER BY created_at ASC", (me, username, username, me))
    history = c.fetchall()
    c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
    conn.commit()
    conn.close()
    formatted_history = [{"id": r['id'], "sender": r['sender'], "message": r['message'], "time": r['time'], "is_read": r['is_read']} for r in history]
    return jsonify(formatted_history)

@app.route("/unsend_message/<int:msg_id>", methods=["POST"])
def unsend_message(msg_id):
    if "username" not in session: return jsonify({"success": False})
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE id = %s AND sender = %s", (msg_id, session["username"]))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/reply_story/<username>", methods=["POST"])
def reply_story(username):
    if "username" not in session: return jsonify({"error": "Login required"}), 401
    msg = html.escape(request.form.get("message", "").strip())
    if msg:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO messages (sender, receiver, message) VALUES (%s, %s, %s)", (session["username"], username, f"Replying to your story: {msg}"))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    return jsonify({"error": "Empty message"}), 400

@app.route("/global_chat", methods=["GET", "POST"])
def global_chat():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == "POST":
        msg = html.escape(request.form.get("message", "").strip())
        if msg:
            c.execute("INSERT INTO global_chat (sender, message) VALUES (%s, %s)", (session["username"], msg[:500]))
            conn.commit()
        return redirect(url_for("global_chat"))
    conn.close()
    return render_template("global_chat.html", hide_navbar=True)

@app.route("/api/global_chat_history")
def api_global_chat_history():
    if "username" not in session: return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"SELECT gc.id, gc.sender, gc.message, TO_CHAR(gc.created_at, 'HH12:MI AM') as time, u.role, u.is_verified FROM global_chat gc JOIN users u ON gc.sender = u.username WHERE gc.sender NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY gc.created_at ASC", (session["username"],))
    history = c.fetchall()
    conn.close()
    formatted = [{"id": r['id'], "sender": r['sender'], "message": r['message'], "time": r['time'], "role": r['role'], "is_verified": r['is_verified']} for r in history]
    return jsonify(formatted)

@app.route("/search")
def search():
    if "username" not in session: return redirect(url_for("login"))
    query = request.args.get("q", "").strip()
    if not query: return redirect(url_for("dashboard"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    clean_query = query.replace("#", "").replace("@", "")
    search_term = f"%{clean_query}%"
    c.execute("SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND (m.title ILIKE %s OR m.prompt ILIKE %s) AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY m.id DESC", (search_term, search_term, session["username"]))
    media_files = [dict(row) for row in c.fetchall()]
    c.execute("SELECT username, profile_pic, role, bio, is_verified FROM users WHERE username ILIKE %s AND username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) LIMIT 20", (search_term, session["username"]))
    found_users = [dict(row) for row in c.fetchall()]
    conn.close()
    return render_template("search.html", media_files=media_files, found_users=found_users, query=query)

@app.route("/api/search_suggest")
def api_search_suggest():
    if "username" not in session: return jsonify([])
    q = request.args.get("q", "").strip()
    if not q: return jsonify([])
    clean_q = q.replace("#", "").replace("@", "").lower()
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT username, profile_pic, is_verified FROM users WHERE username ILIKE %s AND username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) LIMIT 5", (f"%{clean_q}%", session["username"]))
    users = [{"type": "user", "username": r['username'], "pic": r['profile_pic'], "verified": r['is_verified']} for r in c.fetchall()]
    c.execute("SELECT title FROM media WHERE title ILIKE %s LIMIT 10", (f"%#{clean_q}%",))
    tags = set()
    for r in c.fetchall():
        if r['title']:
            found_tags = re.findall(r'#(\w+)', r['title'])
            for t in found_tags:
                if clean_q in t.lower(): tags.add(t)
    tags_list = [{"type": "tag", "tag": t} for t in list(tags)[:4]]
    conn.close()
    return jsonify(users + tags_list)

# 💥 AI STUDIO UPDATED: CHAT DISABLED, ONLY IMAGES ENABLED 💥
@app.route("/ai-studio", methods=["GET", "POST"])
def ai_studio():
    if "username" not in session: return redirect(url_for("login"))
    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        if not prompt: return redirect(url_for("ai_studio"))
        
        wants_image = any(word in prompt.lower() for word in ["create", "generate", "draw", "make an image", "paint", "banao"])
        
        if wants_image:
            encoded_prompt = urllib.parse.quote(prompt)
            seed_val = random.randint(1, 999999) 
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true&seed={seed_val}&width=800&height=1000"
            try:
                r = requests.get(image_url, timeout=15)
                secure_url = cloudinary.uploader.upload(r.content, resource_type="image")["secure_url"] if r.status_code == 200 else image_url
                conn = get_db_connection()
                c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned) VALUES (%s, %s, %s, %s, %s, 1, 'public', 0, FALSE) RETURNING id",
                          (secure_url, f"AI: {prompt[:20]}...", "Photo", prompt, session["username"]))
                conn.commit()
                conn.close()
                flash("Image created successfully! (100% Free)", "success")
                return redirect(url_for("gallery", category="Photo"))
            except: flash("Image generation failed.", "error")
        else:
            # 💥 Chat feature hard-disabled with a maintenance message 💥
            reply = "⚠️ AI Chatting feature has been temporarily disabled for maintenance. We will bring it back during the official launch! (You can still generate images by typing 'Create an image of...')"
            return render_template("ai_studio.html", chat_reply=reply, user_prompt=prompt, hide_navbar=True)
            
    return render_template("ai_studio.html", hide_navbar=True)

@app.route("/analytics")
def analytics():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    c.execute("SELECT COUNT(id) as total_posts, SUM(views) as total_views, SUM(likes) as total_likes, SUM(tips_received) as total_tips FROM media WHERE uploaded_by = %s", (session["username"],))
    stats = c.fetchone()
    c.execute("SELECT title, views, likes, tips_received, category, filename FROM media WHERE uploaded_by = %s ORDER BY views DESC LIMIT 5", (session["username"],))
    top_posts = c.fetchall()
    conn.close()
    return render_template("analytics.html", stats=stats, top_posts=top_posts, hide_navbar=True)

# =========================================================
# ADMIN CONTROLS
# =========================================================
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if not session.get("is_registered"): return redirect(url_for("login"))
        if hmac.compare_digest(request.form.get("passcode", ""), os.environ.get("ADMIN_PASSCODE", "")):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE users SET role = 'admin' WHERE username = %s", (session["username"],))
            conn.commit()
            conn.close()
            session["is_admin"] = True
            session["role"] = "admin"
            flash("Admin active!", "success")
        else: flash("Access Denied!", "error")
        return redirect(url_for("admin"))

    if not current_user_is_admin(): return render_template("admin.html", auth_required=True)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE approved = 0 ORDER BY id DESC")
    pending_media = c.fetchall()
    c.execute("SELECT COUNT(*) as count FROM users")
    user_count = c.fetchone()['count']
    c.execute("SELECT COUNT(*) as count FROM media WHERE approved = 1")
    media_count = c.fetchone()['count']
    c.execute("SELECT SUM(likes) as total FROM media")
    likes_count = c.fetchone()['total'] or 0
    c.execute("SELECT * FROM users ORDER BY id DESC")
    all_users = c.fetchall()
    c.execute("SELECT r.id as report_id, r.media_id, r.reported_by, r.reason, r.created_at, m.title, m.filename, m.category, m.uploaded_by FROM reports r JOIN media m ON r.media_id = m.id ORDER BY r.id DESC")
    reports = c.fetchall()
    c.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'bot'")
    bot_count = c.fetchone()['cnt']
    conn.close()
    return render_template("admin.html", pending_media=pending_media, user_count=user_count, media_count=media_count, likes_count=likes_count, all_users=all_users, reports=reports, bot_count=bot_count, auth_required=False)

@app.route("/admin/trigger_bots", methods=["POST"])
def trigger_bots():
    if not current_user_is_admin(): return redirect(url_for("admin"))
    run_bot_engine()
    flash("🤖 Bot Engine Triggered Successfully!", "success")
    return redirect(url_for("admin"))

@app.route("/admin/broadcast", methods=["POST"])
def admin_broadcast():
    if not current_user_is_admin(): return redirect(url_for("admin"))
    msg = html.escape(request.form.get("broadcast_message", "").strip())
    if msg:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT username FROM users WHERE role != 'bot'")
        users = c.fetchall()
        for u in users:
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)",
                      (u['username'], f"📣 <b>SYSTEM BROADCAST:</b> {msg}", "/dashboard"))
        conn.commit()
        conn.close()
        flash("Broadcast sent to all agents! 📢", "success")
    return redirect(url_for("admin"))

@app.route("/admin/user_action/<int:user_id>/<action>", methods=["POST"])
def admin_user_action(user_id, action):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if action == "ban": 
        c.execute("UPDATE users SET status = 'BANNED' WHERE id = %s", (user_id,))
        flash("Agent Suspended!", "success")
    elif action == "unban": 
        c.execute("UPDATE users SET status = 'ACTIVE' WHERE id = %s", (user_id,))
        flash("Agent Reactivated!", "success")
    elif action == "make_admin": 
        c.execute("UPDATE users SET role = 'admin', wallet_balance = 9999999 WHERE id = %s", (user_id,))
        flash("Promoted to Admin HQ with God Mode!", "success")
    elif action == "verify": 
        c.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (user_id,))
        flash("Agent Verified ✅!", "success")
    elif action == "unverify": 
        c.execute("UPDATE users SET is_verified = FALSE WHERE id = %s", (user_id,))
        flash("Verification Removed.", "success")
    elif action == "delete_user":
        c.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        u = c.fetchone()
        if u:
            uname = u['username']
            c.execute("DELETE FROM media WHERE uploaded_by = %s", (uname,))
            c.execute("DELETE FROM comments WHERE username = %s", (uname,))
            c.execute("DELETE FROM likes WHERE username = %s", (uname,))
            c.execute("DELETE FROM followers WHERE follower = %s OR following = %s", (uname, uname))
            c.execute("DELETE FROM messages WHERE sender = %s OR receiver = %s", (uname, uname))
            c.execute("DELETE FROM blocks WHERE blocker = %s OR blocked = %s", (uname, uname))
            c.execute("DELETE FROM stories WHERE username = %s", (uname,))
            c.execute("DELETE FROM global_chat WHERE sender = %s", (uname,))
            c.execute("DELETE FROM notifications WHERE username = %s", (uname,))
            c.execute("DELETE FROM users WHERE id = %s", (user_id,))
            flash(f"Agent '{uname}' permanently deleted. 🗑️", "success")
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/audit-logs")
def admin_audit_logs():
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    try: c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100"); logs = c.fetchall()
    except: logs = []
    conn.close()
    return render_template("audit_logs.html", logs=logs)

@app.route("/approve/<int:id>", methods=["POST"])
def approve(id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE media SET approved = 1 WHERE id = %s", (id,))
    c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (id,))
    media_info = c.fetchone()
    if media_info: c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"✅ Approved! '{media_info['title'][:15]}' is now live.", f"/post/{id}"))
    conn.commit()
    conn.close()
    flash("Approved!", "success")
    return redirect(url_for("admin"))

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM reports WHERE media_id = %s", (id,))
    c.execute("DELETE FROM media WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    flash("Deleted successfully!", "success")
    return redirect(request.referrer or url_for("admin"))

@app.route("/dismiss_report/<int:report_id>", methods=["POST"])
def dismiss_report(report_id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM reports WHERE id = %s", (report_id,))
    conn.commit()
    conn.close()
    flash("Report dismissed.", "success")
    return redirect(url_for("admin"))

@app.route("/mystery", methods=["POST"])
def mystery():
    if hmac.compare_digest(request.form.get("passcode", ""), os.environ.get("MYSTERY_CODE", "SOCHO")): return render_template("mystery.html")
    flash("Incorrect code.", "error")
    return redirect(url_for("dashboard"))

@app.errorhandler(404)
def not_found_error(error): return render_template("404.html"), 404
@app.errorhandler(500)
def internal_error(error): return render_template("500.html"), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
