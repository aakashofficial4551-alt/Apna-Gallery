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
# DATABASE AUTO-UPGRADER & AUTO-HEAL (FIXED)
# =========================================================
def upgrade_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Create Stories Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            filename TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # 2. Safely add ALL possible missing columns to avoid 500 Errors
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
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_pic TEXT"
    ]
    for q in queries:
        try:
            c.execute(q)
            conn.commit()
        except:
            conn.rollback()
            
    # 3. Generate 10-Digit codes for existing users
    try:
        c.execute("SELECT id FROM users WHERE user_code IS NULL")
        for row in c.fetchall():
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            c.execute("UPDATE users SET user_code = %s WHERE id = %s", (code, row['id']))
        conn.commit()
    except:
        conn.rollback()
        
    conn.close()

upgrade_db()

# =========================================================
# AUTO-CLEANUP (Runs randomly to save server)
# =========================================================
def cleanup_database():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM users WHERE deletion_requested IS NOT NULL AND deletion_requested < NOW() - INTERVAL '7 days'")
        c.execute("DELETE FROM users WHERE username LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '30 days'")
        c.execute("DELETE FROM users WHERE role = 'user' AND last_active < NOW() - INTERVAL '90 days'")
        c.execute("DELETE FROM stories WHERE created_at < NOW() - INTERVAL '12 hours'")
        conn.commit()
    except Exception as e:
        print("Cleanup Error:", e)
    finally:
        conn.close()

# =========================================================
# SECURITY & HELPERS
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
    if "username" in session and request.method == "GET":
        try:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE username = %s", (session["username"],))
            conn.commit()
            conn.close()
        except: pass

@app.context_processor
def inject_security_helpers(): return {"csrf_token": get_csrf_token}

def save_uploaded_file(file, category="Photo"):
    if not file or not file.filename: return None
    try:
        upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        return upload_result["secure_url"]
    except Exception as e: return None

def send_otp_email(to_email, otp):
    sender = os.environ.get("SMTP_EMAIL")
    password = os.environ.get("SMTP_PASSWORD")
    if not sender or not password:
        print(f"⚠️ SMTP NOT CONFIGURED. OTP for {to_email} is: {otp}")
        return True 
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        msg = f"Subject: Apna Gallery Verification\n\nYour OTP is: {otp}\nDo not share this with anyone."
        server.sendmail(sender, to_email, msg)
        server.quit()
        return True
    except Exception as e:
        print("EMAIL ERROR:", e)
        return False

def current_user_is_admin():
    username = session.get("username")
    if not username: return False
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE username = %s", (username,))
    user = c.fetchone()
    conn.close()
    return bool(user and user.get("role") == "admin")

def sync_admin_session():
    is_admin = current_user_is_admin()
    session["is_admin"] = is_admin
    return is_admin

# =========================================================
# ROUTES: AUTHENTICATION
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"): return redirect(url_for("dashboard"))
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
            c.execute("INSERT INTO users (username, password, user_code) VALUES (%s, %s, %s)", (guest_name, "guest", code))
            conn.commit()
            conn.close()
            session.update({"username": guest_name, "is_registered": False, "is_admin": False})
            return redirect(url_for("dashboard"))

        if action == "register":
            email = request.form.get("email", "").strip()
            if len(password) < 6:
                flash("Password too short.", "error")
                return redirect(url_for("login"))
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO users (username, email, password, user_code) VALUES (%s, %s, %s, %s)", 
                          (username, email, generate_password_hash(password), code))
                conn.commit()
                session.update({"username": username, "is_registered": True, "is_admin": False})
                flash(f"Account created! Your unique ID is {code}", "success")
                return redirect(url_for("dashboard"))
            except psycopg2.IntegrityError:
                flash("Username or Email already exists.", "error")
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
                    flash("Welcome back! Account deletion cancelled.", "success")
                    
                session.update({"username": user["username"], "is_registered": True, "is_admin": (user["role"] == "admin")})
                conn.close()
                return redirect(url_for("dashboard"))
            
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
            flash("Password updated successfully! You can now login.", "success")
            return redirect(url_for("login"))
        flash("Invalid OTP.", "error")
        conn.close()
    return render_template_string("""
    <div style="max-width:400px; margin: 100px auto; background:#101b2d; padding:30px; border-radius:12px; color:white; text-align:center;">
        <h2 style="color:#38bdf8;">Enter OTP</h2>
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <input type="text" name="otp" placeholder="6-Digit OTP" required style="width:100%; padding:10px; margin-bottom:15px; border-radius:6px;"><br>
            <input type="password" name="new_password" placeholder="New Password" required style="width:100%; padding:10px; margin-bottom:15px; border-radius:6px;"><br>
            <button type="submit" style="background:#38bdf8; color:black; padding:10px 20px; border:none; border-radius:6px; cursor:pointer;">Reset Password</button>
        </form>
    </div>
    """)

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
    flash("Account scheduled for deletion in 7 days. Login before that to cancel.", "warning")
    return redirect(url_for("login"))

# =========================================================
# CORE ROUTES (Dashboard, Profile, Games)
# =========================================================
@app.route("/")
@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    sync_admin_session()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE approved = 1 AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
    trending = c.fetchall()
    
    # Fetch active stories
    c.execute("""
        SELECT s.*, u.profile_pic FROM stories s 
        JOIN users u ON s.username = u.username 
        WHERE s.created_at >= NOW() - INTERVAL '12 hours' ORDER BY s.id DESC
    """)
    stories = c.fetchall()
    conn.close()
    return render_template("dashboard.html", trending=trending, stories=stories)

@app.route("/games")
def games():
    if "username" not in session: return redirect(url_for("login"))
    return render_template("games.html")

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
        elif action == "avatar" and "profile_pic" in request.files:
            url = save_uploaded_file(request.files["profile_pic"], "Photo")
            if url: c.execute("UPDATE users SET profile_pic = %s WHERE username = %s", (url, session["username"]))
        elif action == "story" and "story_media" in request.files:
            url = save_uploaded_file(request.files["story_media"], "Photo")
            if url: c.execute("INSERT INTO stories (username, filename) VALUES (%s, %s)", (session["username"], url))
        conn.commit()
        return redirect(url_for("profile"))

    c.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
    user = c.fetchone()
    c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY id DESC", (session["username"],))
    my_uploads = c.fetchall()
    conn.close()
    return render_template("profile.html", user=user, my_uploads=my_uploads, post_count=len(my_uploads))

# =========================================================
# ROUTES: GALLERY, SEARCH, LEADERBOARD
# =========================================================
@app.route("/gallery/<category>", methods=["GET", "POST"])
def gallery(category):
    if "username" not in session: return redirect(url_for("login"))
    
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
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (%s, %s, %s, %s, %s, %s)",
                  (filename, request.form.get("title", "Untitled"), category, request.form.get("prompt", ""), session.get("username"), is_approved))
        conn.commit()
        conn.close()
        flash("File live!" if is_approved else "Sent to Admin for approval.", "success")
        return redirect(url_for("gallery", category=category))

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE category = %s AND approved = 1 AND filename != 'SHAYARI_TEXT' ORDER BY id DESC", (category,))
    if category == 'Shayari': c.execute("SELECT * FROM media WHERE category = %s AND approved = 1 ORDER BY id DESC", (category,))
    media_files = c.fetchall()
    
    c.execute("SELECT * FROM comments ORDER BY id ASC")
    comments_db = c.fetchall()
    conn.close()

    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    return render_template("gallery.html", media_files=media_files, category=category, comments=comments)

@app.route("/search")
def search():
    if "username" not in session: return redirect(url_for("login"))
    query = request.args.get("q", "").strip()
    if not query: return redirect(url_for("dashboard"))
    
    conn = get_db_connection()
    c = conn.cursor()
    search_term = f"%{query}%"
    c.execute("SELECT * FROM media WHERE approved = 1 AND filename != 'SHAYARI_TEXT' AND (title ILIKE %s OR prompt ILIKE %s) ORDER BY id DESC", (search_term, search_term))
    media_files = c.fetchall()
    
    c.execute("SELECT * FROM comments ORDER BY id ASC")
    comments_db = c.fetchall()
    conn.close()
    
    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    
    return render_template("search.html", media_files=media_files, query=query, comments=comments)

@app.route("/leaderboard")
def leaderboard():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT uploaded_by as username, COUNT(id) as total_uploads, COALESCE(SUM(likes), 0) as total_likes 
        FROM media WHERE approved = 1 GROUP BY uploaded_by ORDER BY total_likes DESC
    """)
    leaders = c.fetchall()
    conn.close()
    return render_template("leaderboard.html", leaders=leaders)

# =========================================================
# ROUTES: UNIFIED AI STUDIO
# =========================================================
@app.route("/ai-studio", methods=["GET", "POST"])
def ai_studio():
    if "username" not in session: return redirect(url_for("login"))
    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        if not prompt: return redirect(url_for("ai_studio"))
        
        trigger_words = ["create", "generate", "draw", "make an image", "image of", "picture of"]
        wants_image = any(word in prompt.lower() for word in trigger_words)
        
        if wants_image:
            encoded_prompt = urllib.parse.quote(prompt)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true"
            try:
                r = requests.get(image_url, timeout=15)
                secure_url = cloudinary.uploader.upload(r.content, resource_type="image")["secure_url"] if r.status_code == 200 else image_url
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (%s, %s, %s, %s, %s, 1)",
                          (secure_url, f"AI: {prompt[:20]}...", "Photo", prompt, session["username"]))
                conn.commit()
                conn.close()
                flash("Image created and saved to Photo Vault!", "success")
                return redirect(url_for("gallery", category="Photo"))
            except:
                flash("Image generation failed.", "error")
        else:
            try: reply = get_ai_response(prompt)
            except: reply = "I am currently offline."
            return render_template("ai_studio.html", chat_reply=reply, user_prompt=prompt)
            
    return render_template("ai_studio.html")

# =========================================================
# ROUTES: API, SOCIAL (LIKES & COMMENTS)
# =========================================================
@app.route("/api/ai", methods=["POST"])
def ai_endpoint():
    if "username" not in session: return jsonify({"reply": "Login first."}), 401
    try: reply = get_ai_response(request.get_json(silent=True).get("query", "")[:1000])
    except: reply = "Assistant unavailable."
    return jsonify({"reply": reply})

@app.route("/like/<int:media_id>", methods=["POST"])
def like(media_id):
    if "username" not in session: return jsonify({"error": "Login required."}), 401
    username = str(session["username"])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM media WHERE id = %s AND approved = 1", (media_id,))
    if not c.fetchone(): return jsonify({"error": "Media not found."}), 404
    
    c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT (media_id, username) DO NOTHING", (media_id, username))
    if c.rowcount == 1:
        c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media_id,))
        conn.commit()
        liked_now = True
    else: liked_now = False

    c.execute("SELECT likes FROM media WHERE id = %s", (media_id,))
    likes = c.fetchone()["likes"]
    conn.close()
    return jsonify({"likes": likes, "liked": liked_now})

@app.route("/add_comment/<int:media_id>", methods=["POST"])
def add_comment(media_id):
    if "username" not in session: return redirect(url_for("login"))
    text = request.form.get("comment_text", "").strip()
    if text:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO comments (media_id, username, comment_text) VALUES (%s, %s, %s)", (media_id, session["username"], text[:200]))
        conn.commit()
        conn.close()
        flash("Comment posted!", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/delete_own/<int:media_id>", methods=["POST"])
def delete_own(media_id):
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE id = %s AND uploaded_by = %s", (media_id, session["username"]))
    if c.fetchone():
        c.execute("DELETE FROM media WHERE id = %s", (media_id,))
        conn.commit()
        flash("Asset permanently deleted.", "success")
    else: flash("Unauthorized action.", "error")
    conn.close()
    return redirect(url_for("profile"))

# =========================================================
# ROUTES: ADMIN GOD MODE & MYSTERY (FIXED)
# =========================================================
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if not session.get("is_registered"): return redirect(url_for("login"))
        if hmac.compare_digest(request.form.get("passcode", ""), ADMIN_PASSCODE):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE users SET role = 'admin' WHERE username = %s", (session["username"],))
            conn.commit()
            conn.close()
            session["is_admin"] = True
            flash("Admin active!", "success")
        else: flash("Access Denied!", "error")

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
    
    # FIXED: Using SELECT * to avoid crash if any column is slightly misnamed in DB
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
        flash("Agent Suspended successfully!", "success")
    elif action == "unban":
        c.execute("UPDATE users SET status = 'ACTIVE' WHERE id = %s", (user_id,))
        flash("Agent Reactivated!", "success")
    elif action == "make_admin":
        c.execute("UPDATE users SET role = 'admin' WHERE id = %s", (user_id,))
        flash("Agent promoted to Admin HQ!", "success")
        
    conn.commit()
    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/audit-logs")
def admin_audit_logs():
    if not current_user_is_admin(): return redirect(url_for("admin"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100")
    logs = c.fetchall()
    conn.close()
    return render_template("audit_logs.html", logs=logs)

@app.route("/approve/<int:id>", methods=["GET", "POST"])
def approve(id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    if request.method == "GET": return f'<form method="post"><input type="hidden" name="csrf_token" value="{get_csrf_token()}"><button type="submit">Confirm</button></form>'
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE media SET approved = 1 WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    flash("Approved!", "success")
    return redirect(url_for("admin"))

@app.route("/delete/<int:id>", methods=["GET", "POST"])
def delete(id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    if request.method == "GET": return f'<form method="post"><input type="hidden" name="csrf_token" value="{get_csrf_token()}"><button type="submit">Confirm</button></form>'
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM media WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    flash("Deleted!", "success")
    return redirect(url_for("admin"))

@app.route("/mystery", methods=["POST"])
def mystery():
    if hmac.compare_digest(request.form.get("passcode", ""), MYSTERY_CODE): return render_template("mystery.html")
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
