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

import psycopg2
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
def upgrade_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS stories (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, filename TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, message TEXT NOT NULL, link TEXT, is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS followers (id SERIAL PRIMARY KEY, follower VARCHAR(100) NOT NULL, following VARCHAR(100) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(follower, following))")
    c.execute("CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, sender VARCHAR(100) NOT NULL, receiver VARCHAR(100) NOT NULL, message TEXT NOT NULL, is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS bookmarks (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, media_id INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(username, media_id))")
    c.execute("CREATE TABLE IF NOT EXISTS global_chat (id SERIAL PRIMARY KEY, sender VARCHAR(100) NOT NULL, message TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()

    try:
        c.execute("ALTER TABLE media ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        c.execute("ALTER TABLE comments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.commit()
    except: conn.rollback()

    queries = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(120) UNIQUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_code VARCHAR(10) UNIQUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS otp VARCHAR(6)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'ACTIVE'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS country VARCHAR(100)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_pic TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS theme VARCHAR(20) DEFAULT 'cyan'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_pic TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS coins INTEGER DEFAULT 50",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_reward_claim TIMESTAMP"
    ]
    for q in queries:
        try:
            c.execute(q)
            conn.commit()
        except: conn.rollback()
            
    try:
        c.execute("UPDATE users SET role = 'user' WHERE role IS NULL")
        c.execute("UPDATE users SET status = 'ACTIVE' WHERE status IS NULL")
        c.execute("UPDATE users SET theme = 'cyan' WHERE theme IS NULL")
        c.execute("UPDATE users SET coins = 50 WHERE coins IS NULL")
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
# AUTO-CLEANUP
# =========================================================
def cleanup_database():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM users WHERE deletion_requested IS NOT NULL AND deletion_requested < NOW() - INTERVAL '7 days'")
        c.execute("DELETE FROM users WHERE username LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '30 days'")
        c.execute("DELETE FROM users WHERE role = 'user' AND last_active < NOW() - INTERVAL '90 days'")
        c.execute("DELETE FROM stories WHERE created_at < NOW() - INTERVAL '12 hours'")
        c.execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT 500)")
        c.execute("DELETE FROM messages WHERE created_at < NOW() - INTERVAL '30 days'")
        c.execute("DELETE FROM global_chat WHERE id NOT IN (SELECT id FROM global_chat ORDER BY id DESC LIMIT 200)")
        conn.commit()
    except: pass
    finally: conn.close()

# =========================================================
# SECURITY & GLOBAL CONTEXT
# =========================================================
def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

@app.before_request
def update_activity():
    if random.random() < 0.05: cleanup_database()
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
    vars_dict = {"csrf_token": get_csrf_token, "unread_notifications": 0, "unread_messages": 0, "my_theme": "cyan", "my_coins": 0}
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
            
            c.execute("SELECT theme, coins FROM users WHERE username = %s", (session.get("username"),))
            u_data = c.fetchone()
            if u_data:
                vars_dict["my_theme"] = u_data["theme"]
                vars_dict["my_coins"] = u_data["coins"]
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

def send_otp_email(to_email, otp):
    sender = os.environ.get("SMTP_EMAIL")
    password = os.environ.get("SMTP_PASSWORD")
    if not sender or not password: return True 
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        msg = f"Subject: Apna Gallery Verification\n\nYour OTP is: {otp}\nDo not share this with anyone."
        server.sendmail(sender, to_email, msg)
        server.quit()
        return True
    except: return False

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
# ROUTES: AUTHENTICATION
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
            c.execute("INSERT INTO users (username, password, user_code, role, coins) VALUES (%s, %s, %s, 'user', 50)", (guest_name, "guest", code))
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
                c.execute("INSERT INTO users (username, email, password, user_code, role, coins) VALUES (%s, %s, %s, %s, 'user', 50)", 
                          (username, email, generate_password_hash(password), code))
                conn.commit()
                session.update({"username": username, "is_registered": True, "is_admin": False, "role": "user"})
                flash(f"Account created! Welcome to Apna Gallery!", "success")
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
                    
                session.update({"username": user["username"], "is_registered": True, "is_admin": (user["role"] == "admin"), "role": user["role"]})
                conn.close()
                return redirect(url_for("feed"))
            conn.close()
            flash("Invalid credentials.", "error")

        elif action == "forgot":
            email = request.form.get("email", "").strip()
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = c.fetchone()
            if user:
                otp = ''.join(secrets.choice("0123456789") for _ in range(6))
                c.execute("UPDATE users SET otp = %s WHERE email = %s", (otp, email))
                conn.commit()
                send_otp_email(email, otp)
                session['reset_email'] = email
                flash("OTP sent to email.", "success")
                return redirect(url_for("verify_otp"))
            else:
                flash("Email not found.", "error")
            conn.close()

    return render_template("login.html")

@app.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():
    if 'reset_email' not in session: return redirect(url_for("login"))
    if request.method == "POST":
        otp = request.form.get("otp")
        new_pass = request.form.get("new_password")
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE email = %s AND otp = %s", (session['reset_email'], otp))
        if c.fetchone():
            c.execute("UPDATE users SET password = %s, otp = NULL WHERE email = %s", (generate_password_hash(new_pass), session['reset_email']))
            conn.commit()
            session.pop('reset_email', None)
            flash("Password updated successfully!", "success")
            return redirect(url_for("login"))
        flash("Invalid OTP.", "error")
        conn.close()
    return render_template("verify_otp.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/request_delete", methods=["POST"])
def request_delete():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET deletion_requested = CURRENT_TIMESTAMP WHERE username = %s", (session["username"],))
    conn.commit()
    conn.close()
    session.clear()
    flash("Account scheduled for deletion in 7 days.", "warning")
    return redirect(url_for("login"))

# =========================================================
# UNIVERSAL UPLOAD ROUTE WITH MENTIONS
# =========================================================
@app.route("/upload_asset", methods=["POST"])
def upload_asset():
    if "username" not in session: return redirect(url_for("login"))
    category = request.form.get("category", "Photo")
    title = request.form.get("title", "Untitled")
    file = request.files.get("media")
    filename = "SHAYARI_TEXT"

    if category != 'Shayari':
        if file and file.filename != "":
            filename = save_uploaded_file(file, category)
            if not filename:
                flash("Upload Failed! Check API keys.", "error")
                return redirect(request.referrer or url_for("dashboard"))
    
    is_approved = 1 if current_user_is_admin() else 0
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (%s, %s, %s, %s, %s, %s)",
              (filename, title, category, "", session.get("username"), is_approved))
    
    mentions = set(re.findall(r'@(\w+)', title))
    for m in mentions:
        if m != session["username"]:
            c.execute("SELECT id FROM users WHERE username = %s", (m,))
            if c.fetchone():
                msg = f"📣 {session['username']} mentioned you in a post!"
                link = f"/agent/{session['username']}"
                c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, msg, link))
                
    conn.commit()
    conn.close()
    flash("Asset published successfully!" if is_approved else "Asset sent to Admin for approval.", "success")
    return redirect(request.referrer or url_for("feed"))

@app.route("/api/network/<action_type>/<username>")
def api_network(action_type, username):
    if "username" not in session: return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    if action_type == "followers":
        c.execute("SELECT u.username, u.profile_pic, u.role FROM users u JOIN followers f ON u.username = f.follower WHERE f.following = %s", (username,))
    else:
        c.execute("SELECT u.username, u.profile_pic, u.role FROM users u JOIN followers f ON u.username = f.following WHERE f.follower = %s", (username,))
    results = c.fetchall()
    conn.close()
    return jsonify(results)

# =========================================================
# NEW: TIPPING SYSTEM (PHASE 38)
# =========================================================
@app.route("/tip/<int:media_id>", methods=["POST"])
def tip_creator(media_id):
    if "username" not in session: return jsonify({"error": "Login required", "success": False}), 401
    sender = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (media_id,))
    media = c.fetchone()
    if not media: 
        conn.close()
        return jsonify({"error": "Post not found", "success": False}), 404
        
    owner = media['uploaded_by']
    if sender == owner: 
        conn.close()
        return jsonify({"error": "You cannot tip your own post!", "success": False}), 400
        
    # Check sender balance
    c.execute("SELECT coins FROM users WHERE username = %s", (sender,))
    sender_coins = c.fetchone()['coins']
    
    if sender_coins < 10:
        conn.close()
        return jsonify({"error": "Not enough Apna Coins. Claim daily rewards!", "success": False}), 400
        
    # Process Transaction
    c.execute("UPDATE users SET coins = coins - 10 WHERE username = %s", (sender,))
    c.execute("UPDATE users SET coins = coins + 10 WHERE username = %s", (owner,))
    
    # Notify Receiver
    msg = f"💸 {sender} tipped you 10 Coins for '{media['title'][:15]}...'!"
    link = f"/gallery/{media['category']}"
    c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (owner, msg, link))
    conn.commit()
    
    # Return new balance
    c.execute("SELECT coins FROM users WHERE username = %s", (sender,))
    new_coins = c.fetchone()['coins']
    conn.close()
    
    return jsonify({"success": True, "new_coins": new_coins, "msg": "Tipped 10 Coins successfully!"})

@app.route("/claim_reward", methods=["POST"])
def claim_reward():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT last_reward_claim FROM users WHERE username = %s", (session["username"],))
    user = c.fetchone()
    now = datetime.datetime.now()
    last_claim = user['last_reward_claim'] if user and user['last_reward_claim'] else None
    
    if not last_claim or (now - last_claim).total_seconds() > 86400:
        c.execute("UPDATE users SET coins = coins + 20, last_reward_claim = CURRENT_TIMESTAMP WHERE username = %s", (session["username"],))
        conn.commit()
        flash("🎁 Daily Reward Claimed! You got +20 Apna Coins.", "success")
    else:
        flash("⏳ You already claimed your reward today. Come back tomorrow!", "warning")
        
    conn.close()
    return redirect(request.referrer or url_for("profile"))

# =========================================================
# CORE ROUTES (DUAL FEED)
# =========================================================
@app.route("/")
def index():
    if "username" not in session: return redirect(url_for("login"))
    return redirect(url_for("feed"))

@app.route("/feed")
def feed():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    
    tab = request.args.get("tab", "foryou")
    
    if tab == "global":
        c.execute("SELECT m.*, u.profile_pic, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC LIMIT 50")
        feed_posts = c.fetchall()
    else:
        c.execute("""
            SELECT m.*, u.profile_pic, u.role 
            FROM media m 
            JOIN users u ON m.uploaded_by = u.username 
            JOIN followers f ON f.following = m.uploaded_by 
            WHERE f.follower = %s AND m.approved = 1 AND m.filename != 'SHAYARI_TEXT' 
            ORDER BY m.id DESC LIMIT 50
        """, (session["username"],))
        feed_posts = c.fetchall()
        if not feed_posts:
            c.execute("SELECT m.*, u.profile_pic, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC LIMIT 10")
            feed_posts = c.fetchall()

    c.execute("SELECT c.*, u.role FROM comments c JOIN users u ON c.username = u.username ORDER BY c.id ASC")
    comments_db = c.fetchall()
    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    
    c.execute("SELECT media_id FROM bookmarks WHERE username = %s", (session["username"],))
    saved_ids = [row['media_id'] for row in c.fetchall()]
    conn.close()
    
    return render_template("feed.html", posts=feed_posts, comments=comments, saved_ids=saved_ids, current_tab=tab)

@app.route("/explore")
def explore():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.filename != 'SHAYARI_TEXT' ORDER BY RANDOM() LIMIT 40")
    explore_posts = c.fetchall()
    conn.close()
    return render_template("explore.html", posts=explore_posts)

@app.route("/creator")
def creator_studio():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(id) as total_posts, COALESCE(SUM(likes), 0) as total_likes FROM media WHERE uploaded_by = %s", (session["username"],))
    stats = c.fetchone()
    c.execute("SELECT COUNT(*) as followers FROM followers WHERE following = %s", (session["username"],))
    followers = c.fetchone()['followers']
    c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY id DESC", (session["username"],))
    my_media = c.fetchall()
    conn.close()
    return render_template("creator.html", stats=stats, followers=followers, my_media=my_media)

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    sync_admin_session()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE approved = 1 AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
    trending = c.fetchall()
    c.execute("SELECT s.*, u.profile_pic, u.role FROM stories s JOIN users u ON s.username = u.username WHERE s.created_at >= NOW() - INTERVAL '12 hours' ORDER BY s.id DESC")
    stories = c.fetchall()
    
    c.execute("SELECT last_reward_claim FROM users WHERE username = %s", (session["username"],))
    user_data = c.fetchone()
    last_claim = user_data['last_reward_claim'] if user_data else None
    can_claim = not last_claim or (datetime.datetime.now() - last_claim).total_seconds() > 86400
    
    conn.close()
    return render_template("dashboard.html", trending=trending, stories=stories, can_claim=can_claim)

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
    return render_template("notifications.html", notifications=notifs)

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "bio":
            c.execute("UPDATE users SET bio = %s, country = %s WHERE username = %s", 
                      (request.form.get("bio", ""), request.form.get("country", "India"), session["username"]))
            flash("Profile updated!", "success")
        elif action == "theme":
            theme = request.form.get("theme", "cyan")
            if theme in ["cyan", "gold", "rose", "matrix"]:
                c.execute("UPDATE users SET theme = %s WHERE username = %s", (theme, session["username"]))
                flash("Theme applied successfully!", "success")
        elif action == "avatar" and "profile_pic" in request.files:
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
    c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY id DESC", (session["username"],))
    my_uploads = c.fetchall()
    
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (session["username"],))
    followers_count = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (session["username"],))
    following_count = c.fetchone()['cnt']
    
    c.execute("SELECT m.* FROM media m JOIN bookmarks b ON m.id = b.media_id WHERE b.username = %s ORDER BY b.id DESC", (session["username"],))
    saved_uploads = c.fetchall()
    
    last_claim = user['last_reward_claim']
    can_claim = not last_claim or (datetime.datetime.now() - last_claim).total_seconds() > 86400
    
    conn.close()
    
    return render_template("profile.html", user=user, my_uploads=my_uploads, saved_uploads=saved_uploads, post_count=len(my_uploads), followers_count=followers_count, following_count=following_count, can_claim=can_claim)

# =========================================================
# PUBLIC PORTFOLIO & FOLLOW SYSTEM
# =========================================================
@app.route("/agent/<username>")
def agent_profile(username):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    agent = c.fetchone()
    if not agent:
        flash("Agent not found.", "error")
        conn.close()
        return redirect(url_for("feed"))
        
    c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC", (username,))
    agent_uploads = c.fetchall()
    
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
    followers_count = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (username,))
    following_count = c.fetchone()['cnt']
    
    c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (session["username"], username))
    is_following = bool(c.fetchone())
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
        msg = f"👤 {current_user} started following you!"
        link = f"/agent/{current_user}"
        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (username, msg, link))
        following_now = True
        
    conn.commit()
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
    followers_count = c.fetchone()['cnt']
    conn.close()
    return jsonify({"followers": followers_count, "following_now": following_now})

# =========================================================
# DIRECT MESSAGING (INBOX & CHAT) & GLOBAL CHAT
# =========================================================
@app.route("/inbox")
def inbox():
    if "username" not in session: return redirect(url_for("login"))
    me = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT u.username, u.profile_pic, u.role, u.last_active
        FROM users u 
        WHERE u.username IN (SELECT receiver FROM messages WHERE sender = %s UNION SELECT sender FROM messages WHERE receiver = %s)
    """, (me, me))
    contacts = c.fetchall()
    for contact in contacts:
        c.execute("SELECT COUNT(id) as cnt FROM messages WHERE sender = %s AND receiver = %s AND is_read = FALSE", (contact['username'], me))
        contact['unread'] = c.fetchone()['cnt']
    conn.close()
    return render_template("inbox.html", contacts=contacts)

@app.route("/chat/<username>", methods=["GET", "POST"])
def chat(username):
    if "username" not in session: return redirect(url_for("login"))
    me = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == "POST":
        msg = request.form.get("message", "").strip()
        if msg:
            c.execute("INSERT INTO messages (sender, receiver, message) VALUES (%s, %s, %s)", (me, username, msg))
            conn.commit()
        return redirect(url_for("chat", username=username))
    c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
    conn.commit()
    c.execute("SELECT * FROM messages WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s) ORDER BY created_at ASC", (me, username, username, me))
    chat_history = c.fetchall()
    c.execute("SELECT username, profile_pic, role, last_active FROM users WHERE username = %s", (username,))
    contact = c.fetchone()
    conn.close()
    if not contact: return redirect(url_for("inbox"))
    return render_template("chat.html", chat_history=chat_history, contact=contact)

@app.route("/api/chat_history/<username>")
def api_chat_history(username):
    if "username" not in session: return jsonify([])
    me = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, sender, message, TO_CHAR(created_at, 'HH24:MI') as time FROM messages WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s) ORDER BY created_at ASC", (me, username, username, me))
    history = c.fetchall()
    c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
    conn.commit()
    conn.close()
    formatted_history = [{"id": r['id'], "sender": r['sender'], "message": r['message'], "time": r['time']} for r in history]
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
    return render_template("global_chat.html")

@app.route("/api/global_chat_history")
def api_global_chat_history():
    if "username" not in session: return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT gc.id, gc.sender, gc.message, TO_CHAR(gc.created_at, 'HH24:MI') as time, u.role FROM global_chat gc JOIN users u ON gc.sender = u.username ORDER BY gc.created_at ASC")
    history = c.fetchall()
    conn.close()
    formatted = [{"id": r['id'], "sender": r['sender'], "message": r['message'], "time": r['time'], "role": r['role']} for r in history]
    return jsonify(formatted)

# =========================================================
# ADMIN ROUTE
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
    conn.close()
    return render_template("admin.html", pending_media=pending_media, user_count=user_count, media_count=media_count, likes_count=likes_count, all_users=all_users, auth_required=False)

@app.route("/admin/user_action/<int:user_id>/<action>", methods=["POST"])
def admin_user_action(user_id, action):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    if action == "ban":
        c.execute("UPDATE users SET status = 'BANNED' WHERE id = %s", (user_id,))
        flash("Agent Suspended!", "success")
    elif action == "unban":
        c.execute("UPDATE users SET status = 'ACTIVE' WHERE id = %s", (user_id,))
        flash("Agent Reactivated!", "success")
    elif action == "make_admin":
        c.execute("UPDATE users SET role = 'admin' WHERE id = %s", (user_id,))
        flash("Promoted to Admin HQ!", "success")
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/audit-logs")
def admin_audit_logs():
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100")
        logs = c.fetchall()
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
    if media_info:
        msg = f"✅ Approved! Your asset '{media_info['title']}' is now live."
        link = f"/gallery/{media_info['category']}"
        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], msg, link))
    conn.commit()
    conn.close()
    flash("Approved!", "success")
    return redirect(url_for("admin"))

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM media WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    flash("Deleted!", "success")
    return redirect(url_for("admin"))

@app.route("/mystery", methods=["POST"])
def mystery():
    if hmac.compare_digest(request.form.get("passcode", ""), os.environ.get("MYSTERY_CODE", "SOCHO")): 
        return render_template("mystery.html")
    flash("Incorrect code.", "error")
    return redirect(url_for("dashboard"))

@app.errorhandler(404)
def not_found_error(error): return render_template("404.html"), 404
@app.errorhandler(500)
def internal_error(error): return render_template("500.html"), 500
@app.errorhandler(413)
def request_entity_too_large(error):
    flash("File too large (Max 50MB).", "error")
    return redirect(request.referrer or url_for("dashboard"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
