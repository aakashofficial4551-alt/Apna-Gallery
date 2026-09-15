import os
import secrets
import time
from collections import defaultdict, deque
import hmac
from uuid import uuid4

import psycopg2
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

from flask import (
    Flask, request, render_template, redirect, url_for, flash, session, jsonify, render_template_string
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from ai_service import get_ai_response
from database import init_db, get_db_connection


# =========================================================
# BASE DIRECTORY & ENV VARIABLES
# =========================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))


# =========================================================
# CLOUDINARY CONFIG (PERMANENT STORAGE)
# =========================================================
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)

# =========================================================
# FLASK APP INITIALIZATION
# =========================================================
app = Flask(__name__)
init_db()

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "development-only-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024 # 50MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True


# =========================================================
# CSRF PROTECTION & SECURITY HEADERS
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

def _request_origin_matches():
    from urllib.parse import urlsplit
    expected = urlsplit(request.host_url.rstrip("/"))
    expected_origin = f"{expected.scheme}://{expected.netloc}"
    origin = request.headers.get("Origin", "").strip().rstrip("/")
    if origin: return hmac.compare_digest(origin, expected_origin)
    referer = request.headers.get("Referer", "").strip()
    if referer:
        parsed = urlsplit(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}"
        return hmac.compare_digest(referer_origin, expected_origin)
    return False

@app.before_request
def protect_state_changing_requests():
    if request.method in {"GET", "HEAD", "OPTIONS"}: return None
    token = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not token and request.is_json:
        data = request.get_json(silent=True) or {}
        token = data.get("csrf_token", "")
    if validate_csrf_token(token) or _request_origin_matches(): return None
    
    if request.is_json or request.path.startswith("/api/") or request.path.startswith("/like/"):
        return jsonify({"error": "Security check failed."}), 403
    flash("Security check failed. Please refresh.", "error")
    return redirect(request.referrer or url_for("dashboard"))

@app.context_processor
def inject_security_helpers():
    return {"csrf_token": get_csrf_token}

@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https:; "
        "style-src 'self' 'unsafe-inline' https:; img-src 'self' data: res.cloudinary.com https:; "
        "media-src 'self' res.cloudinary.com https:; connect-src 'self' https:;"
    )
    return response


# =========================================================
# SECRETS & FILE VALIDATION
# =========================================================
ADMIN_PASSCODE = os.environ.get("ADMIN_PASSCODE", "")
MYSTERY_CODE = os.environ.get("MYSTERY_CODE", "SOCHO")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "mp4", "webm", "mp3", "wav", "ogg", "pdf", "txt", "docx"}
SECURE_ALLOWED_CATEGORIES = {
    'Photo': {'png', 'jpg', 'jpeg', 'gif', 'webp'},
    'Video': {'mp4', 'webm', 'ogg'},
    'Music': {'mp3', 'wav', 'ogg'},
    'Document': {'pdf', 'txt', 'docx'},
    'Shayari': {'png', 'jpg', 'jpeg', 'gif', 'webp'}
}

def validate_file_security(file, category="Photo"):
    if not file or not file.filename or '.' not in file.filename:
        return False, "Invalid file."
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS or (category in SECURE_ALLOWED_CATEGORIES and ext not in SECURE_ALLOWED_CATEGORIES[category]):
        return False, f"Extension .{ext} not allowed."
    return True, ""

def save_uploaded_file(file, category="Photo"):
    if not file or not file.filename: return None
    is_valid, err_msg = validate_file_security(file, category)
    if not is_valid:
        print(f"UPLOAD REJECTED: {err_msg}")
        return None
    try:
        # Upload directly to Cloudinary
        upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        return upload_result["secure_url"]
    except Exception as e:
        print("CLOUDINARY ERROR:", repr(e))
        return None


# =========================================================
# RATE LIMITING & AUDIT LOGS
# =========================================================
RATE_LIMIT_WINDOW, RATE_LIMIT_MAX_FAILURES, RATE_LIMIT_BLOCK_TIME = 600, 5, 600
_failed_attempts, _blocked_until = defaultdict(deque), {}

def _rate_limited(scope):
    key = f"{scope}:{request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()}"
    now = time.monotonic()
    if _blocked_until.get(key, 0) > now: return True, int(_blocked_until[key] - now) + 1
    _blocked_until.pop(key, None)
    while _failed_attempts[key] and now - _failed_attempts[key][0] > RATE_LIMIT_WINDOW:
        _failed_attempts[key].popleft()
    return False, 0

def _record_failed_attempt(scope):
    key = f"{scope}:{request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()}"
    now = time.monotonic()
    _failed_attempts[key].append(now)
    if len(_failed_attempts[key]) >= RATE_LIMIT_MAX_FAILURES:
        _blocked_until[key] = now + RATE_LIMIT_BLOCK_TIME
        _failed_attempts[key].clear()

def log_admin_action(actor_username, action, target_type, target_id, metadata=""):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO audit_logs (actor_username, action, target_type, target_id, metadata) VALUES (%s, %s, %s, %s, %s)",
            (actor_username, action, target_type, target_id, metadata)
        )
        conn.commit()
        conn.close()
    except Exception as e: print("AUDIT LOG ERROR:", repr(e))

def get_current_user():
    username = session.get("username")
    if not username or not session.get("is_registered"): return None
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = c.fetchone()
    conn.close()
    return user

def current_user_is_admin():
    user = get_current_user()
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
        limited, retry = _rate_limited("login")
        if limited:
            flash(f"Too many attempts. Try in {retry // 60 + 1} mins.", "error")
            return redirect(url_for("login"))

        action, username, password, confirm = request.form.get("action"), request.form.get("username", "").strip(), request.form.get("password", ""), request.form.get("confirm_password", "")
        
        if action == "guest":
            session.clear()
            session["username"] = f"Guest-{uuid4().hex[:8]}"
            session["is_registered"], session["is_admin"] = False, False
            return redirect(url_for("dashboard"))

        if not username or len(username) > 40:
            flash("Invalid username.", "error")
            return redirect(url_for("login"))

        if action == "register":
            if len(password) < 6 or password != confirm:
                flash("Invalid password or mismatch.", "error")
                return redirect(url_for("login"))
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, generate_password_hash(password)))
                conn.commit()
                session.clear()
                session.update({"username": username, "is_registered": True, "is_admin": False})
                flash("Account created!", "success")
                return redirect(url_for("dashboard"))
            except psycopg2.IntegrityError:
                flash("Username exists.", "error")
                return redirect(url_for("login"))
            finally: conn.close()

        if action == "login":
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = c.fetchone()
            conn.close()

            if user and user.get("status") == "ACTIVE" and check_password_hash(user["password"], password):
                session.clear()
                session.update({"username": user["username"], "is_registered": True, "is_admin": (user["role"] == "admin")})
                _failed_attempts.pop(f"login:{request.remote_addr}", None)
                return redirect(url_for("dashboard"))
            
            _record_failed_attempt("login")
            flash("Invalid credentials or suspended.", "error")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# ROUTES: CORE
# =========================================================
@app.route("/")
@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for("login"))
    sync_admin_session()
    
    # Naya Code: Fetching Trending Assets
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE approved = 1 ORDER BY likes DESC LIMIT 3")
    trending = c.fetchall()
    conn.close()
    
    return render_template("dashboard.html", trending=trending)

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
        if "bio" in request.form:
            c.execute("UPDATE users SET bio = %s, country = %s WHERE username = %s", 
                      (request.form.get("bio", ""), request.form.get("country", "India"), session["username"]))
            conn.commit()
            flash("Profile updated!", "success")
        elif "profile_pic" in request.files:
            url = save_uploaded_file(request.files["profile_pic"], "Photo")
            if url:
                c.execute("UPDATE users SET profile_pic = %s WHERE username = %s", (url, session["username"]))
                conn.commit()
                flash("Avatar updated!", "success")

    c.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
    user = c.fetchone()
    c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY id DESC", (session["username"],))
    my_uploads = c.fetchall()
    conn.close()
    return render_template("profile.html", user=user, my_uploads=my_uploads, post_count=len(my_uploads))


@app.route("/gallery/<category>", methods=["GET", "POST"])
def gallery(category):
    if "username" not in session: return redirect(url_for("login"))
    
    if request.method == "POST":
        filename = "SHAYARI_TEXT"
        file = request.files.get("media")
        if file and file.filename != "":
            filename = save_uploaded_file(file, category) or "SHAYARI_TEXT"
        
        is_approved = 1 if current_user_is_admin() else 0
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (%s, %s, %s, %s, %s, %s)",
            (filename, request.form.get("title", "Untitled"), category, request.form.get("prompt", ""), session.get("username"), is_approved)
        )
        conn.commit()
        conn.close()
        flash("File live!" if is_approved else "Sent to Admin for approval.", "success")
        return redirect(url_for("gallery", category=category))

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE category = %s AND approved = 1 ORDER BY id DESC", (category,))
    media_files = c.fetchall()
    c.execute("SELECT * FROM comments ORDER BY id ASC")
    comments_db = c.fetchall()
    conn.close()

    comments = defaultdict(list)
    for comment in comments_db: comments[comment["media_id"]].append(comment)
    return render_template("gallery.html", media_files=media_files, category=category, comments=comments)


# =========================================================
# ROUTES: API & LIKES
# =========================================================
@app.route("/api/ai", methods=["POST"])
def ai_endpoint():
    if "username" not in session: return jsonify({"reply": "Login first."}), 401
    try:
        reply = get_ai_response(request.get_json(silent=True).get("query", "")[:1000])
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


# =========================================================
# ROUTES: ADMIN & MYSTERY
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
    
    # Pendings fetch karna
    c.execute("SELECT * FROM media WHERE approved = 0 ORDER BY id DESC")
    pending_media = c.fetchall()
    
    # Analytics / Stats fetch karna
    c.execute("SELECT COUNT(*) as count FROM users")
    user_count = c.fetchone()['count']
    
    c.execute("SELECT COUNT(*) as count FROM media WHERE approved = 1")
    media_count = c.fetchone()['count']
    
    c.execute("SELECT SUM(likes) as total FROM media")
    likes_count = c.fetchone()['total'] or 0
    
    conn.close()
    
    return render_template("admin.html", pending_media=pending_media, user_count=user_count, media_count=media_count, likes_count=likes_count, auth_required=False)


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
    if request.method == "GET":
        return f'<form method="post"><input type="hidden" name="csrf_token" value="{get_csrf_token()}"><button type="submit">Confirm Approve</button></form>'
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE media SET approved = 1 WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    log_admin_action(session.get("username"), "APPROVE_MEDIA", "media", id)
    flash("Approved!", "success")
    return redirect(url_for("admin"))


@app.route("/delete/<int:id>", methods=["GET", "POST"])
def delete(id):
    if not current_user_is_admin(): return redirect(url_for("admin"))
    if request.method == "GET":
        return f'<form method="post"><input type="hidden" name="csrf_token" value="{get_csrf_token()}"><button type="submit">Confirm Delete</button></form>'
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM media WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    log_admin_action(session.get("username"), "DELETE_MEDIA", "media", id)
    flash("Deleted!", "success")
    return redirect(url_for("admin"))


@app.route("/mystery", methods=["POST"])
def mystery():
    if hmac.compare_digest(request.form.get("passcode", ""), MYSTERY_CODE):
        return render_template("mystery.html")
    flash("Incorrect code.", "error")
    return redirect(url_for("dashboard"))


# =========================================================
# ERRORS & EXECUTION
# =========================================================
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
