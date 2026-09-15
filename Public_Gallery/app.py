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
    Flask, request, render_template, redirect, url_for, flash, session, jsonify
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
# DATABASE AUTO-UPGRADER (No need to drop tables)
# =========================================================
def upgrade_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Safely add new columns if they don't exist
    queries = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(120) UNIQUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_code VARCHAR(10) UNIQUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS otp VARCHAR(6)"
    ]
    for q in queries:
        try:
            c.execute(q)
            conn.commit()
        except:
            conn.rollback()
            
    # Generate 10-Digit codes for existing users
    c.execute("SELECT id FROM users WHERE user_code IS NULL")
    for row in c.fetchall():
        code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
        c.execute("UPDATE users SET user_code = %s WHERE id = %s", (code, row['id']))
    conn.commit()
    conn.close()

upgrade_db()

# =========================================================
# AUTO-CLEANUP (Runs randomly on requests to save server)
# =========================================================
def cleanup_database():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        # Delete 7-Day permanent deletion requests
        c.execute("DELETE FROM users WHERE deletion_requested IS NOT NULL AND deletion_requested < NOW() - INTERVAL '7 days'")
        # Delete 30-Day inactive Guests
        c.execute("DELETE FROM users WHERE username LIKE 'Guest-%%' AND last_active < NOW() - INTERVAL '30 days'")
        # Delete 3-Month (90-Day) inactive normal users
        c.execute("DELETE FROM users WHERE role = 'user' AND last_active < NOW() - INTERVAL '90 days'")
        # Delete 12-Hour Stories
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

def validate_csrf_token(token):
    stored = session.get("csrf_token", "")
    return bool(token) and bool(stored) and hmac.compare_digest(str(token), str(stored))

@app.before_request
def update_activity():
    if random.random() < 0.05: cleanup_database() # 5% chance to run cleanup on any request
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
        return True # Fake success for testing
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

# =========================================================
# AUTHENTICATION & RECOVERY
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
                
                # Cancel deletion if they log back in
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
# CORE ROUTES (Profile, Gallery, etc.)
# =========================================================
@app.route("/")
@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE approved = 1 AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
    trending = c.fetchall()
    
    # Fetch active stories globally
    c.execute("""
        SELECT s.*, u.profile_pic FROM stories s 
        JOIN users u ON s.username = u.username 
        WHERE s.created_at >= NOW() - INTERVAL '12 hours' ORDER BY s.id DESC
    """)
    stories = c.fetchall()
    conn.close()
    return render_template("dashboard.html", trending=trending, stories=stories)

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
    # Profile shows BOTH approved and pending uploads
    c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY id DESC", (session["username"],))
    my_uploads = c.fetchall()
    conn.close()
    return render_template("profile.html", user=user, my_uploads=my_uploads, post_count=len(my_uploads))

# ... (Gallery, Search, Leaderboard, Likes remain the same as previous) ...

# =========================================================
# UNIFIED AI (Chat + Image in One)
# =========================================================
@app.route("/ai-studio", methods=["GET", "POST"])
def ai_studio():
    if "username" not in session: return redirect(url_for("login"))
    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        if not prompt: return redirect(url_for("ai_studio"))
        
        # Detect if user wants an image
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
            # Normal AI Chat
            try: reply = get_ai_response(prompt)
            except: reply = "I am currently offline."
            return render_template("ai_studio.html", chat_reply=reply, user_prompt=prompt)
            
    return render_template("ai_studio.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
