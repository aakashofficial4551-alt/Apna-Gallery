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

from ai_service import get_ai_response
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

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "development-only-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# =========================================================
# SECURITY & BACKUP ENGINE
# =========================================================
@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

def create_database_backup():
    try:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        backup_data = {"timestamp": str(datetime.datetime.now()), "users": [], "media": [], "notifications": []}
        
        c.execute("SELECT username, email, role, is_verified, created_at FROM users")
        backup_data["users"] = [dict(row) for row in c.fetchall()]
        
        c.execute("SELECT id, title, category, uploaded_by, likes, views, visibility, created_at FROM media")
        backup_data["media"] = [dict(row) for row in c.fetchall()]
        
        backup_dir = os.path.join(BASE_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        file_path = os.path.join(backup_dir, f"backup_{datetime.date.today()}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, default=str, indent=4)
        conn.close()
    except Exception as e: print(f"Backup Error: {e}")

# =========================================================
# TEXT & TIME FILTERS
# =========================================================
@app.template_filter('format_text')
def format_text(text):
    if not text: return ""
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

# =========================================================
# DATABASE AUTO-HEALER
# =========================================================
def safe_alter(c, conn, query):
    try: c.execute(query); conn.commit()
    except Exception: conn.rollback()

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

    try:
        c.execute("UPDATE users SET role = 'user' WHERE role IS NULL")
        c.execute("UPDATE users SET status = 'ACTIVE' WHERE status IS NULL")
        c.execute("UPDATE users SET theme = 'cyan' WHERE theme IS NULL")
        c.execute("UPDATE users SET is_verified = FALSE WHERE is_verified IS NULL")
        c.execute("UPDATE media SET visibility = 'public' WHERE visibility IS NULL")
        c.execute("UPDATE media SET views = 0 WHERE views IS NULL")
        c.execute("UPDATE media SET is_pinned = FALSE WHERE is_pinned IS NULL")
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
# THE GRIM REAPER (AUTO-CLEANUP)
# =========================================================
def cleanup_database():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM users WHERE role = 'bot' AND created_at < NOW() - INTERVAL '7 days'")
        c.execute("DELETE FROM users WHERE username LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '3 days'")
        c.execute("DELETE FROM users WHERE role = 'user' AND username NOT LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '90 days'")
        c.execute("DELETE FROM users WHERE deletion_requested IS NOT NULL AND deletion_requested < NOW() - INTERVAL '7 days'")
        c.execute("DELETE FROM media WHERE uploaded_by IN (SELECT username FROM users WHERE role = 'bot') AND created_at < NOW() - INTERVAL '24 hours'")
        
        c.execute("DELETE FROM media WHERE uploaded_by NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM comments WHERE username NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM likes WHERE username NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM followers WHERE follower NOT IN (SELECT username FROM users) OR following NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM messages WHERE sender NOT IN (SELECT username FROM users) OR receiver NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM blocks WHERE blocker NOT IN (SELECT username FROM users) OR blocked NOT IN (SELECT username FROM users)")
        
        c.execute("DELETE FROM global_chat WHERE created_at < NOW() - INTERVAL '5 minutes'")
        c.execute("DELETE FROM stories WHERE created_at < NOW() - INTERVAL '12 hours'")
        c.execute("DELETE FROM messages WHERE created_at < NOW() - INTERVAL '24 hours'")
        c.execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT 500)")
        conn.commit()
    except Exception as e: print("Cleanup Error:", e)
    finally: conn.close()

# =========================================================
# THE SMART PHANTOM ENGINE (AI BOTS)
# =========================================================
def run_bot_engine():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        bot_names = ["Rahul_Vibes", "Priya_007", "Aman_Cool", "Sneha_Arts", "Vikram_Pro", "Kabir_Singh", "Pooja_X", "Anjali_Cute", "Rohan_Tech", "Karan_King"]
        bot_chats = [
            "Hey everyone! Kya haal hain? 👋", "Koi online hai kya is waqt? 🤔", 
            "Bhai yeh app ekdum mast chal rahi hai! 🔥", "Good morning dosto! Have a great day ☀️", 
            "Hello world! Just joined this awesome gallery.", "Koi badhiya photo upload karo yaar! 😎",
            "Speed kaafi fast hai is website ki 🚀", "Mausam bohot badiya hai aaj 🌧️",
            "Are yaar, kya chal raha hai aajkal? 🧐"
        ]
        
        c.execute("SELECT COUNT(id) FROM users WHERE role = 'bot'")
        bot_count = c.fetchone()['count']
        
        if bot_count < 10:
            name = f"{random.choice(bot_names)}_{random.randint(100, 9999)}"
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            c.execute("INSERT INTO users (username, password, user_code, role, is_verified, bio) VALUES (%s, %s, %s, 'bot', TRUE, 'Just roaming around the digital gallery ✨')", (name, "botpass123", code))
            conn.commit()
            bot_username = name
        else:
            c.execute("SELECT username FROM users WHERE role = 'bot' ORDER BY RANDOM() LIMIT 1")
            bot_username = c.fetchone()['username']
            
        action = random.randint(1, 100)
        
        if action <= 35: 
            cat = random.choice(["Photo", "Photo", "Photo", "Shayari"])
            if cat == "Photo":
                img_url = f"https://picsum.photos/800/1000?random={random.randint(1, 100000)}"
                caption = random.choice(["Nature ki khoobsurti 🌲 #nature #peace", "Vibes ✨ #chill #mood", "Aaj ka din bohot badhiya tha! 😎", "Random click 📸 #photography #india", "Just a beautiful view today. ✈️"])
            else:
                img_url = "SHAYARI_TEXT"
                caption = "Waqt ne sikhaya hai chup rehna... 💔 #sad #shayari #hindi"
            
            c.execute("INSERT INTO media (filename, title, category, uploaded_by, approved, visibility, views) VALUES (%s, %s, %s, %s, 1, 'public', %s)",
                      (img_url, caption, cat, bot_username, random.randint(5, 50)))
        elif 35 < action <= 70:
            c.execute("INSERT INTO global_chat (sender, message) VALUES (%s, %s)", (bot_username, random.choice(bot_chats)))
        elif 70 < action <= 85:
            c.execute("SELECT username FROM users WHERE role = 'bot' AND username != %s ORDER BY RANDOM() LIMIT 1", (bot_username,))
            target = c.fetchone()
            if target:
                try: c.execute("INSERT INTO followers (follower, following) VALUES (%s, %s)", (bot_username, target['username']))
                except: pass
        elif action > 85: 
            c.execute("SELECT id FROM media ORDER BY RANDOM() LIMIT 1")
            media = c.fetchone()
            if media:
                try:
                    c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT DO NOTHING", (media['id'], bot_username))
                    if c.rowcount == 1: c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media['id'],))
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

@app.before_request
def update_activity():
    if random.random() < 0.05: 
        cleanup_database()
        create_database_backup()
    if random.random() < 0.20: run_bot_engine() 
        
    if "username" in session:
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE username = %s", (session["username"],))
            conn.commit()
            conn.close()
        except: pass

@app.context_processor
def inject_global_vars():
    def get_avatar(profile_pic, username):
        if profile_pic and str(profile_pic).strip() != "": return profile_pic
        encoded_name = urllib.parse.quote(str(username))
        return f"https://api.dicebear.com/7.x/avataaars/svg?seed={encoded_name}&backgroundColor=1e293b"

    vars_dict = {"csrf_token": get_csrf_token, "unread_notifications": 0, "unread_messages": 0, "my_theme": "cyan", "get_avatar": get_avatar, "my_website": "", "my_blocked_users": []}
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
            
            c.execute("SELECT theme, bio, profile_pic, website, is_verified FROM users WHERE username = %s", (session.get("username"),))
            u_data = c.fetchone()
            if u_data:
                vars_dict["my_theme"] = u_data["theme"]
                vars_dict["my_bio"] = u_data["bio"]
                vars_dict["my_profile_pic"] = u_data["profile_pic"]
                vars_dict["my_website"] = u_data.get("website", "")
                vars_dict["my_is_verified"] = u_data.get("is_verified", False)
                
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
# ROUTES: AUTHENTICATION & SETTINGS
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"): return redirect(url_for("feed"))
    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if action == "guest":
            session.clear()
            guest_name = f"Guest-{uuid4().hex[:8]}"
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO users (username, password, user_code, role) VALUES (%s, %s, %s, 'user')", (guest_name, "guest", code))
            conn.commit()
            conn.close()
            session.update({"username": guest_name, "is_registered": False, "is_admin": False, "role": "user"})
            return redirect(url_for("feed"))

        if action == "register":
            email = request.form.get("email", "").strip()
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO users (username, email, password, user_code, role) VALUES (%s, %s, %s, %s, 'user')", 
                          (username, email, generate_password_hash(password), code))
                conn.commit()
                session.update({"username": username, "is_registered": True, "is_admin": False, "role": "user"})
                flash("Account created! Welcome to Apna Gallery!", "success")
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
                session.update({"username": user["username"], "is_registered": not is_guest, "is_admin": (user["role"] == "admin"), "role": user["role"]})
                conn.close()
                return redirect(url_for("feed"))
            conn.close()
            flash("Invalid credentials.", "error")
            
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/settings", methods=["POST"])
def settings():
    if "username" not in session: return redirect(url_for("login"))
    action = request.form.get("action")
    conn = get_db_connection()
    c = conn.cursor()
    
    if action == "update_profile":
        c.execute("UPDATE users SET bio = %s, theme = %s, website = %s WHERE username = %s", 
                  (request.form.get("bio", ""), request.form.get("theme", "cyan"), request.form.get("website", ""), session["username"]))
        flash("Profile Settings Updated!", "success")
        
    elif action == "change_password":
        new_pass = request.form.get("new_password")
        c.execute("UPDATE users SET password = %s WHERE username = %s", (generate_password_hash(new_pass), session["username"]))
        flash("Password Changed Successfully!", "success")
        
    elif action == "upgrade_guest":
        new_username = request.form.get("new_username", "").strip()
        email = request.form.get("email", "").strip()
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
        return redirect(url_for("login"))
        
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("dashboard"))

# =========================================================
# ASSET MANAGEMENT
# =========================================================
@app.route("/upload_asset", methods=["POST"])
def upload_asset():
    if "username" not in session: return redirect(url_for("login"))
    try:
        category = request.form.get("category", "Photo")
        title = request.form.get("title", "Untitled")
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
        c = conn.cursor()
        c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, FALSE)",
                  (filename, title, category, "", session.get("username"), is_approved, visibility))
        mentions = set(re.findall(r'@(\w+)', title))
        for m in mentions:
            if m != session["username"]:
                c.execute("SELECT id FROM users WHERE username = %s", (m,))
                if c.fetchone(): c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you!", f"/agent/{session['username']}"))
        conn.commit()
        conn.close()
        flash(f"Asset published as {visibility.upper()}!" if is_approved else "Asset sent to Admin for approval.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Server Alert: {str(e)[:150]}", "error")
    return redirect(request.referrer or url_for("feed"))

@app.route("/api/view/<int:media_id>", methods=["POST"])
def add_view(media_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE media SET views = views + 1 WHERE id = %s", (media_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# =========================================================
# CORE ROUTES
# =========================================================
@app.route("/")
def index():
    if "username" not in session: return redirect(url_for("login"))
    return redirect(url_for("feed"))

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
    
    if tab == "global":
        c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.id DESC LIMIT %s OFFSET %s", (session["username"], session["username"], limit, offset))
        feed_posts = [dict(row) for row in c.fetchall()]
    else:
        c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username JOIN followers f ON f.following = m.uploaded_by WHERE f.follower = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.id DESC LIMIT %s OFFSET %s", (session["username"], session["username"], session["username"], limit, offset))
        feed_posts = [dict(row) for row in c.fetchall()]
        if not feed_posts and offset == 0:
            c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.id DESC LIMIT 10", (session["username"], session["username"]))
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
    c = conn.cursor()
    c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, m.views, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY RANDOM() LIMIT 40", (session["username"],))
    explore_posts = c.fetchall()
    conn.close()
    return render_template("explore.html", posts=explore_posts)

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    sync_admin_session()
    return render_template("dashboard.html")

# 💥 NATIVE APP DEDICATED ROUTES (hide_navbar=True) 💥
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
        msg = request.form.get("message", "").strip()
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

@app.route("/global_chat", methods=["GET", "POST"])
def global_chat():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == "POST":
        msg = request.form.get("message", "").strip()
        if msg:
            c.execute("INSERT INTO global_chat (sender, message) VALUES (%s, %s)", (session["username"], msg[:500]))
            conn.commit()
        return redirect(url_for("global_chat"))
    conn.close()
    return render_template("global_chat.html", hide_navbar=True)

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

@app.route("/api/global_chat_history")
def api_global_chat_history():
    if "username" not in session: return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"""
        SELECT gc.id, gc.sender, gc.message, TO_CHAR(gc.created_at, 'HH12:MI AM') as time, u.role, u.is_verified 
        FROM global_chat gc JOIN users u ON gc.sender = u.username 
        WHERE gc.sender NOT IN (SELECT blocked FROM blocks WHERE blocker = %s)
        ORDER BY gc.created_at ASC
    """, (session["username"],))
    history = c.fetchall()
    conn.close()
    formatted = [{"id": r['id'], "sender": r['sender'], "message": r['message'], "time": r['time'], "role": r['role'], "is_verified": r['is_verified']} for r in history]
    return jsonify(formatted)

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
    user = c.fetchone()
    c.execute("SELECT m.* FROM media m JOIN bookmarks b ON m.id = b.media_id WHERE b.username = %s ORDER BY b.id DESC", (session["username"],))
    saved_uploads = c.fetchall()
    conn.close()
    return render_template("profile.html", user=user, saved_uploads=saved_uploads)

@app.route("/agent/<username>")
def agent_profile(username):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    agent = c.fetchone()
    conn.close()
    return render_template("agent.html", agent=agent)

@app.route("/api/ai", methods=["POST"])
def ai_endpoint():
    query = request.get_json(silent=True).get("query", "")[:1000]
    try: reply = get_ai_response(query).replace('"', '').replace("'", "")
    except: reply = "Beautiful day! #vibes #nature"
    return jsonify({"reply": reply})

@app.route("/ai-studio", methods=["GET", "POST"])
def ai_studio():
    if "username" not in session: return redirect(url_for("login"))
    return render_template("ai_studio.html", hide_navbar=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
