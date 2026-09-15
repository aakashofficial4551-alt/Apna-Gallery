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
# BULLETPROOF DATABASE AUTO-HEALER 🛡️
# =========================================================
def safe_execute(c, conn, query):
    try:
        c.execute(query)
        conn.commit()
    except Exception as e:
        conn.rollback()

def upgrade_db():
    conn = get_db_connection()
    c = conn.cursor()
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS stories (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, filename TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, message TEXT NOT NULL, link TEXT, is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS followers (id SERIAL PRIMARY KEY, follower VARCHAR(100) NOT NULL, following VARCHAR(100) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(follower, following))")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, sender VARCHAR(100) NOT NULL, receiver VARCHAR(100) NOT NULL, message TEXT NOT NULL, is_read BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS bookmarks (id SERIAL PRIMARY KEY, username VARCHAR(100) NOT NULL, media_id INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(username, media_id))")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS global_chat (id SERIAL PRIMARY KEY, sender VARCHAR(100) NOT NULL, message TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS reports (id SERIAL PRIMARY KEY, media_id INTEGER NOT NULL, reported_by VARCHAR(100) NOT NULL, reason TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(media_id, reported_by))")
    safe_execute(c, conn, "CREATE TABLE IF NOT EXISTS comment_likes (id SERIAL PRIMARY KEY, comment_id INTEGER NOT NULL, username VARCHAR(100) NOT NULL, UNIQUE(comment_id, username))")

    # Safe Column Alterations
    alter_queries = [
        "ALTER TABLE media ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE media ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'public'",
        "ALTER TABLE media ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0",
        "ALTER TABLE media ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS likes INTEGER DEFAULT 0",
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
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_pic TEXT"
    ]
    for q in alter_queries: safe_execute(c, conn, q)
            
    # Default values population
    safe_execute(c, conn, "UPDATE users SET role = 'user' WHERE role IS NULL")
    safe_execute(c, conn, "UPDATE users SET status = 'ACTIVE' WHERE status IS NULL")
    safe_execute(c, conn, "UPDATE users SET theme = 'cyan' WHERE theme IS NULL")
    safe_execute(c, conn, "UPDATE media SET visibility = 'public' WHERE visibility IS NULL")
    safe_execute(c, conn, "UPDATE media SET views = 0 WHERE views IS NULL")
    safe_execute(c, conn, "UPDATE media SET is_pinned = FALSE WHERE is_pinned IS NULL")
    safe_execute(c, conn, "UPDATE comments SET likes = 0 WHERE likes IS NULL")

    try:
        c.execute("SELECT id FROM users WHERE user_code IS NULL")
        for row in c.fetchall():
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            c.execute("UPDATE users SET user_code = %s WHERE id = %s", (code, row['id']))
        conn.commit()
    except: conn.rollback()
    conn.close()

upgrade_db()

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
    def get_avatar(profile_pic, username):
        if profile_pic and str(profile_pic).strip() != "":
            return profile_pic
        encoded_name = urllib.parse.quote(str(username))
        return f"https://api.dicebear.com/7.x/avataaars/svg?seed={encoded_name}&backgroundColor=1e293b"

    vars_dict = {"csrf_token": get_csrf_token, "unread_notifications": 0, "unread_messages": 0, "my_theme": "cyan", "get_avatar": get_avatar}
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
            
            c.execute("SELECT theme, bio, profile_pic FROM users WHERE username = %s", (session.get("username"),))
            u_data = c.fetchone()
            if u_data:
                vars_dict["my_theme"] = u_data["theme"]
                vars_dict["my_bio"] = u_data["bio"]
                vars_dict["my_profile_pic"] = u_data["profile_pic"]
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
                    
                session.update({"username": user["username"], "is_registered": True, "is_admin": (user["role"] == "admin"), "role": user["role"]})
                conn.close()
                return redirect(url_for("feed"))
            conn.close()
            flash("Invalid credentials.", "error")
            
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

@app.route("/settings", methods=["POST"])
def settings():
    if "username" not in session: return redirect(url_for("login"))
    action = request.form.get("action")
    conn = get_db_connection()
    c = conn.cursor()
    
    if action == "update_profile":
        c.execute("UPDATE users SET bio = %s, theme = %s WHERE username = %s", 
                  (request.form.get("bio", ""), request.form.get("theme", "cyan"), session["username"]))
        flash("Profile Settings Updated!", "success")
        
    elif action == "change_password":
        new_pass = request.form.get("new_password")
        c.execute("UPDATE users SET password = %s WHERE username = %s", (generate_password_hash(new_pass), session["username"]))
        flash("Password Changed Successfully!", "success")
        
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
# CRASH-PROOF UPLOAD ROUTE (BUG FIXED 100%)
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
                    flash("Upload Failed! Format not supported or file too large.", "error")
                    return redirect(request.referrer or url_for("dashboard"))
        
        is_approved = 1 if current_user_is_admin() else 0
        conn = get_db_connection()
        c = conn.cursor()
        
        # FIX: Removed `views` and `is_pinned` from the initial insert query to rely purely on database defaults. 
        # This completely avoids the column missing crash if migrations are delayed.
        c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (filename, title, category, "", session.get("username"), is_approved, visibility))
        
        mentions = set(re.findall(r'@(\w+)', title))
        for m in mentions:
            if m != session["username"]:
                c.execute("SELECT id FROM users WHERE username = %s", (m,))
                if c.fetchone():
                    c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you in a post!", f"/agent/{session['username']}"))
                    
        conn.commit()
        conn.close()
        flash(f"Asset published as {visibility.upper()}!" if is_approved else "Asset sent to Admin for approval.", "success")
        
    except Exception as e:
        # We will now print exactly WHAT failed to the user!
        flash(f"Database Security Alert: {str(e)}", "error")
        
    return redirect(request.referrer or url_for("feed"))

# 💥 PHASE 53: REPOST ENGINE 💥
@app.route("/repost/<int:media_id>", methods=["POST"])
def repost(media_id):
    if "username" not in session: return jsonify({"error": "Login required"}), 401
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE id = %s", (media_id,))
    original = c.fetchone()
    
    if original:
        new_title = f"🔄 Reposted from @{original['uploaded_by']}: {original['title']}"
        c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility) VALUES (%s, %s, %s, %s, %s, 1, 'public')",
                  (original['filename'], new_title[:200], original['category'], "", session["username"]))
        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (original['uploaded_by'], f"🔄 {session['username']} reposted your asset!", f"/agent/{session['username']}"))
        conn.commit()
        flash("Successfully Reposted to your timeline!", "success")
    conn.close()
    return redirect(request.referrer or url_for("feed"))

@app.route("/edit_post/<int:media_id>", methods=["POST"])
def edit_post(media_id):
    if "username" not in session: return redirect(url_for("login"))
    new_title = request.form.get("title", "").strip()
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
    flash("Post Pinned to top of your profile!", "success")
    return redirect(request.referrer or url_for("profile"))

@app.route("/api/view/<int:media_id>", methods=["POST"])
def add_view(media_id):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("UPDATE media SET views = views + 1 WHERE id = %s", (media_id,))
        conn.commit()
    except: pass # Ignore if views column isn't ready
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
    conn = get_db_connection()
    c = conn.cursor()
    tab = request.args.get("tab", "foryou")
    
    if tab == "global":
        c.execute("SELECT m.*, u.profile_pic, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC LIMIT 50")
        feed_posts = c.fetchall()
    else:
        c.execute("""
            SELECT m.*, u.profile_pic, u.role 
            FROM media m JOIN users u ON m.uploaded_by = u.username 
            JOIN followers f ON f.following = m.uploaded_by 
            WHERE f.follower = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' 
            ORDER BY m.id DESC LIMIT 50
        """, (session["username"],))
        feed_posts = c.fetchall()
        if not feed_posts:
            c.execute("SELECT m.*, u.profile_pic, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC LIMIT 10")
            feed_posts = c.fetchall()

    c.execute("SELECT c.*, u.role FROM comments c JOIN users u ON c.username = u.username ORDER BY c.id ASC")
    comments_db = c.fetchall()
    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    
    c.execute("SELECT media_id FROM bookmarks WHERE username = %s", (session["username"],))
    saved_ids = [row['media_id'] for row in c.fetchall()]
    conn.close()
    
    return render_template("feed.html", posts=feed_posts, comments=comments, saved_ids=saved_ids, current_tab=tab)

# 💥 PHASE 52: SINGLE POST DEEP LINK 💥
@app.route("/post/<int:media_id>")
def view_post(media_id):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT m.*, u.profile_pic, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.id = %s", (media_id,))
    post = c.fetchone()
    if not post:
        flash("Post not found.", "error")
        return redirect(url_for("feed"))
        
    c.execute("SELECT c.*, u.role FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = %s ORDER BY c.id ASC", (media_id,))
    post_comments = c.fetchall()
    comments = {media_id: post_comments}
    
    c.execute("SELECT media_id FROM bookmarks WHERE username = %s", (session["username"],))
    saved_ids = [row['media_id'] for row in c.fetchall()]
    conn.close()
    return render_template("post.html", post=post, comments=comments, saved_ids=saved_ids)

@app.route("/reels")
def reels():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT m.*, u.profile_pic, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.category = 'Video' ORDER BY RANDOM() LIMIT 20")
    videos = c.fetchall()
    conn.close()
    return render_template("reels.html", videos=videos)

@app.route("/explore")
def explore():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, m.views, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY RANDOM() LIMIT 40")
    except:
        c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, 0 as views, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY RANDOM() LIMIT 40")
        
    explore_posts = c.fetchall()
    c.execute("SELECT title FROM media WHERE approved = 1 AND visibility = 'public'")
    all_titles = c.fetchall()
    conn.close()
    
    tag_counts = defaultdict(int)
    for row in all_titles:
        if row['title']:
            tags = re.findall(r'#(\w+)', row['title'])
            for t in tags: tag_counts[t.lower()] += 1
            
    trending_tags = [tag for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:6]]
    return render_template("explore.html", posts=explore_posts, trending_tags=trending_tags)

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    sync_admin_session()
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        try:
            c.execute("SELECT id, title, filename, category, likes, views FROM media WHERE approved = 1 AND visibility = 'public' AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
        except:
            c.rollback()
            c.execute("SELECT id, title, filename, category, likes, 0 as views FROM media WHERE approved = 1 AND visibility = 'public' AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
            
        trending = c.fetchall()
        c.execute("SELECT s.id, s.username, s.filename, s.created_at, u.profile_pic, u.role FROM stories s JOIN users u ON s.username = u.username WHERE s.created_at >= NOW() - INTERVAL '12 hours' ORDER BY s.id DESC")
        stories = c.fetchall()
        conn.close()
        return render_template("dashboard.html", trending=trending, stories=stories)
    except Exception as e:
        return f"<div style='color:#f43f5e; padding:50px; text-align:center; font-family:sans-serif;'><h1>Dashboard Engine Error</h1><p>{str(e)}</p><a href='/feed' style='color:#38bdf8;'>Go back to Feed</a></div>"

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
    
    try:
        c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY is_pinned DESC, id DESC", (session["username"],))
    except:
        c.rollback()
        c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY id DESC", (session["username"],))
        
    my_uploads = c.fetchall()
    
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (session["username"],))
    followers_count = c.fetchone()['cnt']
    c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (session["username"],))
    following_count = c.fetchone()['cnt']
    
    c.execute("SELECT m.* FROM media m JOIN bookmarks b ON m.id = b.media_id WHERE b.username = %s ORDER BY b.id DESC", (session["username"],))
    saved_uploads = c.fetchall()
    conn.close()
    
    return render_template("profile.html", user=user, my_uploads=my_uploads, saved_uploads=saved_uploads, post_count=len(my_uploads), followers_count=followers_count, following_count=following_count)

# =========================================================
# OTHERS (Follow, Inbox, Admin etc.)
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
        
    c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (session["username"], username))
    is_following = bool(c.fetchone())
    
    try:
        if is_following:
            c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
        else:
            c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
    except:
        c.rollback()
        if is_following:
            c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC", (username,))
        else:
            c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC", (username,))
        
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
        c.execute("SELECT u.username, u.profile_pic, u.role FROM users u JOIN followers f ON u.username = f.follower WHERE f.following = %s", (username,))
    else:
        c.execute("SELECT u.username, u.profile_pic, u.role FROM users u JOIN followers f ON u.username = f.following WHERE f.follower = %s", (username,))
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

@app.route("/reply_story/<username>", methods=["POST"])
def reply_story(username):
    if "username" not in session: return jsonify({"error": "Login required"}), 401
    msg = request.form.get("message", "").strip()
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

@app.route("/search")
def search():
    if "username" not in session: return redirect(url_for("login"))
    query = request.args.get("q", "").strip()
    if not query: return redirect(url_for("dashboard"))
    conn = get_db_connection()
    c = conn.cursor()
    clean_query = query.replace("#", "").replace("@", "")
    search_term = f"%{clean_query}%"
    c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND (m.title ILIKE %s OR m.prompt ILIKE %s) ORDER BY m.id DESC", (search_term, search_term))
    media_files = c.fetchall()
    c.execute("SELECT username, profile_pic, role, bio FROM users WHERE username ILIKE %s LIMIT 20", (search_term,))
    found_users = c.fetchall()
    conn.close()
    return render_template("search.html", media_files=media_files, found_users=found_users, query=query)

@app.route("/gallery/<category>", methods=["GET", "POST"])
def gallery(category):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    if request.method == "POST":
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
        c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (filename, request.form.get("title", "Untitled"), category, "", session.get("username"), is_approved, visibility))
        conn.commit()
        flash("File live!" if is_approved else "Sent to Admin for approval.", "success")
        return redirect(url_for("gallery", category=category))

    c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.category = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.id DESC", (category,))
    if category == 'Shayari': c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.category = %s AND m.approved = 1 AND m.visibility = 'public' ORDER BY m.id DESC", (category,))
    media_files = c.fetchall()
    c.execute("SELECT c.*, u.role FROM comments c JOIN users u ON c.username = u.username ORDER BY c.id ASC")
    comments_db = c.fetchall()
    conn.close()
    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    return render_template("gallery.html", media_files=media_files, category=category, comments=comments)

@app.route("/bookmark/<int:media_id>", methods=["POST"])
def bookmark(media_id):
    if "username" not in session: return jsonify({"error": "Login required."}), 401
    username = session["username"]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM bookmarks WHERE username = %s AND media_id = %s", (username, media_id))
    if c.fetchone():
        c.execute("DELETE FROM bookmarks WHERE username = %s AND media_id = %s", (username, media_id))
        bookmarked = False
    else:
        c.execute("INSERT INTO bookmarks (username, media_id) VALUES (%s, %s)", (username, media_id))
        bookmarked = True
    conn.commit()
    conn.close()
    return jsonify({"bookmarked": bookmarked})

@app.route("/api/ai", methods=["POST"])
def ai_endpoint():
    if "username" not in session: return jsonify({"reply": "Login first."}), 401
    query = request.get_json(silent=True).get("query", "")[:1000]
    try: 
        reply = get_ai_response(query).replace('"', '').replace("'", "")
    except: 
        reply = "Beautiful day! #vibes #nature"
    return jsonify({"reply": reply})

@app.route("/ai-studio", methods=["GET", "POST"])
def ai_studio():
    if "username" not in session: return redirect(url_for("login"))
    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        if not prompt: return redirect(url_for("ai_studio"))
        trigger_words = ["create", "generate", "draw", "make an image", "paint"]
        wants_image = any(word in prompt.lower() for word in trigger_words)
        
        if wants_image:
            encoded_prompt = urllib.parse.quote(prompt)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true"
            try:
                r = requests.get(image_url, timeout=15)
                secure_url = cloudinary.uploader.upload(r.content, resource_type="image")["secure_url"] if r.status_code == 200 else image_url
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility) VALUES (%s, %s, %s, %s, %s, 1, 'public')",
                          (secure_url, f"AI: {prompt[:20]}...", "Photo", prompt, session["username"]))
                conn.commit()
                conn.close()
                flash("Image created successfully! (100% Free)", "success")
                return redirect(url_for("gallery", category="Photo"))
            except: flash("Image generation failed.", "error")
        else:
            try: reply = get_ai_response(prompt)
            except: reply = "I am currently offline."
            return render_template("ai_studio.html", chat_reply=reply, user_prompt=prompt)
    return render_template("ai_studio.html")

@app.route("/like/<int:media_id>", methods=["POST"])
def like(media_id):
    if "username" not in session: return jsonify({"error": "Login required."}), 401
    username = str(session["username"])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT (media_id, username) DO NOTHING", (media_id, username))
    if c.rowcount == 1:
        c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media_id,))
        c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (media_id,))
        media_info = c.fetchone()
        if media_info and media_info['uploaded_by'] != username:
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"❤️ {username} liked your asset: {media_info['title']}", f"/gallery/{media_info['category']}"))
        conn.commit()
        liked_now = True
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
        try:
            c.execute("UPDATE comments SET likes = COALESCE(likes, 0) + 1 WHERE id = %s", (comment_id,))
            c.execute("SELECT username, media_id FROM comments WHERE id = %s", (comment_id,))
            comment_info = c.fetchone()
            if comment_info and comment_info['username'] != username:
                c.execute("SELECT category FROM media WHERE id = %s", (comment_info['media_id'],))
                media_cat = c.fetchone()
                cat = media_cat['category'] if media_cat else 'Photo'
                c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (comment_info['username'], f"❤️ {username} liked your comment!", f"/gallery/{cat}"))
            conn.commit()
        except: pass
    
    try:
        c.execute("SELECT likes FROM comments WHERE id = %s", (comment_id,))
        likes = c.fetchone()["likes"]
    except: likes = 0
    conn.close()
    return jsonify({"likes": likes or 0})

@app.route("/add_comment/<int:media_id>", methods=["POST"])
def add_comment(media_id):
    if "username" not in session: return redirect(url_for("login"))
    text = request.form.get("comment_text", "").strip()
    if text:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO comments (media_id, username, comment_text) VALUES (%s, %s, %s)", (media_id, session["username"], text[:200]))
        c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (media_id,))
        media_info = c.fetchone()
        
        if media_info and media_info['uploaded_by'] != session["username"]:
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"💬 {session['username']} commented on {media_info['title']}", f"/gallery/{media_info['category']}"))
            
        mentions = set(re.findall(r'@(\w+)', text))
        for m in mentions:
            if m != session["username"]:
                c.execute("SELECT id FROM users WHERE username = %s", (m,))
                if c.fetchone():
                    c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you in a comment!", f"/gallery/{media_info['category']}"))
        conn.commit()
        conn.close()
        flash("Comment posted!", "success")
    return redirect(request.referrer or url_for("dashboard"))

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
    conn.close()
    return render_template("admin.html", pending_media=pending_media, user_count=user_count, media_count=media_count, likes_count=likes_count, all_users=all_users, reports=reports, auth_required=False)

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
        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"✅ Approved! '{media_info['title']}' is now live.", f"/gallery/{media_info['category']}"))
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
    if hmac.compare_digest(request.form.get("passcode", ""), os.environ.get("MYSTERY_CODE", "SOCHO")): 
        return render_template("mystery.html")
    flash("Incorrect code.", "error")
    return redirect(url_for("dashboard"))

@app.errorhandler(404)
def not_found_error(error): return render_template("404.html"), 404
@app.errorhandler(500)
def internal_error(error): return render_template("500.html"), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
