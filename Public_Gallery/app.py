import os
import time
from collections import defaultdict
import hmac
import random
import datetime
import urllib.parse
import re
import html

import psycopg2.extras
import cloudinary
from dotenv import load_dotenv

from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify

from database import init_db, get_db_connection
from core_utils import get_ist_time, cleanup_database, run_bot_engine

from routes_auth import init_auth_routes
from routes_admin import init_admin_routes
from routes_main import init_main_routes

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

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "supreme-secret-key-change-me"),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=True, 
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30)
)

request_tracker = defaultdict(list)
BANNED_IPS = set()

@app.before_request
def security_firewall_and_session_check():
    ip = request.remote_addr or "127.0.0.1"
    now = time.time()
    request_tracker[ip] = [t for t in request_tracker[ip] if now - t < 60]
    if len(request_tracker[ip]) > 200: BANNED_IPS.add(ip)
    if ip in BANNED_IPS: return "Your IP has been permanently blocked for malicious activity. (Error 429)", 429
    request_tracker[ip].append(now)

    if request.endpoint in ['index', 'login', 'request_otp', 'verify_otp', 'logout', 'static', 'api_search_suggest', 'privacy_policy', 'terms', 'about', 'manifest', 'sw'] or (request.path and request.path.startswith('/static/')):
        return

    if request.method in ["POST", "PUT", "DELETE"]:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        session_token = session.get("csrf_token")
        if not token or not session_token or not hmac.compare_digest(token, session_token):
            if request.path.startswith('/api/'): return jsonify({"error": "Security Firewall: Invalid CSRF Token"}), 403
            else:
                flash("Security Firewall Blocked Your Request: Invalid Validation Token.", "error")
                return redirect(request.referrer or url_for('feed'))

    if "username" in session:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT status FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        if user:
            c.execute("UPDATE users SET last_active = %s WHERE username = %s", (get_ist_time(), session["username"]))
            conn.commit()
        conn.close()

        if not user or user['status'] == 'BANNED':
            session.clear()
            if request.path.startswith('/api/'): return jsonify({"error": "Account suspended", "redirect": True}), 401
            flash("Account deleted or suspended.", "error")
            return redirect(url_for('login'))
            
    if random.random() < 0.05: cleanup_database()
    if random.random() < 0.40: run_bot_engine() 

@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

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
    return (get_ist_time() - last_active).total_seconds() < 300

@app.template_filter('timeago')
def timeago(dt):
    if not dt: return ""
    now = get_ist_time()
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 60: return "Just now"
    elif seconds < 3600: return f"{int(seconds/60)}m ago"
    elif seconds < 86400: return f"{int(seconds/3600)}h ago"
    else: return f"{int(seconds/86400)}d ago"

def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        import secrets
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

@app.context_processor
def inject_global_vars():
    def get_avatar(profile_pic, username):
        if profile_pic and str(profile_pic).strip() != "": return profile_pic
        encoded_name = urllib.parse.quote(str(username))
        return f"https://api.dicebear.com/7.x/avataaars/svg?seed={encoded_name}&backgroundColor=1e293b"

    vars_dict = {"csrf_token": get_csrf_token, "unread_notifications": 0, "unread_messages": 0, "my_theme": "cyan", "get_avatar": get_avatar, "my_website": "", "my_blocked_users": [], "my_wallet": 0, "my_nickname": ""}
    if session.get("username"):
        try:
            conn = get_db_connection()
            c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            c.execute("SELECT COUNT(id) as cnt FROM notifications WHERE username = %s AND is_read = FALSE", (session.get("username"),))
            res1 = c.fetchone()
            if res1: vars_dict["unread_notifications"] = res1['cnt']
            c.execute("SELECT COUNT(id) as cnt FROM messages WHERE receiver = %s AND is_read = FALSE", (session.get("username"),))
            res2 = c.fetchone()
            if res2: vars_dict["unread_messages"] = res2['cnt']
            c.execute("SELECT theme, bio, profile_pic, website, is_verified, wallet_balance, nickname FROM users WHERE username = %s", (session.get("username"),))
            u_data = c.fetchone()
            if u_data:
                vars_dict["my_theme"] = u_data['theme']
                vars_dict["my_bio"] = u_data['bio']
                vars_dict["my_profile_pic"] = u_data['profile_pic']
                vars_dict["my_website"] = u_data['website'] or ""
                vars_dict["my_is_verified"] = u_data['is_verified'] or False
                vars_dict["my_wallet"] = u_data['wallet_balance'] or 0
                vars_dict["my_nickname"] = u_data['nickname'] or session.get("username")
            conn.close()
        except: pass
    return vars_dict

@app.errorhandler(404)
def not_found_error(error): return render_template("404.html"), 404
@app.errorhandler(500)
def internal_error(error): return render_template("500.html"), 500

# 🌟 MAGICAL ROUTE REGISTRATION 🌟
init_auth_routes(app)
init_admin_routes(app)
init_main_routes(app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
