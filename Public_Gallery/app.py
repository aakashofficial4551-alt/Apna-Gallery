import os
import sqlite3
import secrets
import time
import cloudinary
import cloudinary.uploader
from collections import defaultdict, deque
import hmac
from uuid import uuid4

from dotenv import load_dotenv

from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    render_template_string,
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)

from werkzeug.utils import secure_filename

from ai_service import get_ai_response
from database import init_db


# =========================================================
# BASE DIRECTORY
# =========================================================

BASE_DIR = os.path.abspath(
    os.path.dirname(__file__)
)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

load_dotenv(
    os.path.join(
        BASE_DIR,
        "..",
        ".env"
    )
)


# =========================================================
# FLASK APP & DATABASE INITIALIZATION
# =========================================================

app = Flask(__name__)
init_db()

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "development-only-secret-change-me"
)


# =========================================================
# UPLOAD SETTINGS
# =========================================================

app.config["UPLOAD_FOLDER"] = os.path.join(
    BASE_DIR,
    "static",
    "uploads"
)
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)

# 50 MB Maximum limit preserved
app.config["MAX_CONTENT_LENGTH"] = (
    50 * 1024 * 1024
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True


# =========================================================
# CSRF PROTECTION
# =========================================================

def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token(token):
    stored = session.get("csrf_token", "")
    return bool(token) and bool(stored) and hmac.compare_digest(
        str(token), str(stored)
    )


def _request_origin_matches():
    from urllib.parse import urlsplit

    expected = urlsplit(request.host_url.rstrip("/"))
    expected_origin = f"{expected.scheme}://{expected.netloc}"

    origin = request.headers.get("Origin", "").strip().rstrip("/")
    if origin:
        return hmac.compare_digest(origin, expected_origin)

    referer = request.headers.get("Referer", "").strip()
    if referer:
        parsed = urlsplit(referer)
        referer_origin = f"{parsed.scheme}://{parsed.netloc}"
        return hmac.compare_digest(referer_origin, expected_origin)

    return False


@app.before_request
def protect_state_changing_requests():
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None

    token = request.form.get("csrf_token", "")

    if not token:
        token = request.headers.get("X-CSRF-Token", "")

    if not token and request.is_json:
        data = request.get_json(silent=True) or {}
        token = data.get("csrf_token", "")

    if validate_csrf_token(token):
        return None

    if _request_origin_matches():
        return None

    if request.is_json or request.path.startswith("/api/") or request.path.startswith("/like/"):
        return jsonify({"error": "Security check failed. Please refresh and try again."}), 403

    flash("Security check failed. Please refresh and try again.", "error")
    return redirect(request.referrer or url_for("dashboard"))


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": get_csrf_token}
    

# =========================================================
# SECURITY HEADERS
# =========================================================

@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), bluetooth=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "script-src 'self' 'unsafe-inline' https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob: https:; "
        "font-src 'self' data: https:; "
        "connect-src 'self' https:; "
        "object-src 'none'; "
        "worker-src 'self' blob:;"
    )

    return response


os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# =========================================================
# SECRETS & ALLOWED CONFIGS
# =========================================================

ADMIN_PASSCODE = os.environ.get("ADMIN_PASSCODE", "")
MYSTERY_CODE = os.environ.get("MYSTERY_CODE", "SOCHO")

ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "webp", "gif",
    "mp4", "webm",
    "mp3", "wav", "ogg",
    "pdf", "txt", "docx",
}


# =========================================================
# DATABASE CONNECTION HELPER
# =========================================================

DATABASE_PATH = os.path.join(BASE_DIR, "database.db")

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================
# PHASE 7: UPLOAD SECURITY & MAGIC-BYTE VALIDATION PIPELINE
# =========================================================

SECURE_ALLOWED_CATEGORIES = {
    'Photo': {'png', 'jpg', 'jpeg', 'gif', 'webp'},
    'Video': {'mp4', 'webm', 'ogg'},
    'Music': {'mp3', 'wav', 'ogg'},
    'Document': {'pdf', 'txt', 'docx'},
    'Shayari': {'png', 'jpg', 'jpeg', 'gif', 'webp'}
}

MAGIC_NUMBERS = {
    'png': [b'\x89PNG\r\n\x1a\n'],
    'jpg': [b'\xff\xd8\xff'],
    'jpeg': [b'\xff\xd8\xff'],
    'gif': [b'GIF87a', b'GIF89a'],
    'pdf': [b'%PDF-'],
    'docx': [b'PK\x03\x04'],
    'mp4': [b'ftyp'],
    'webm': [b'\x1aE\xdf\xa3'],
    'ogg': [b'OggS'],
    'mp3': [b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xfa', b'\xff\xf2'],
    'wav': [b'RIFF'],
    'webp': [b'RIFF'],
}

def validate_file_security(file, category="Photo"):
    if not file or not file.filename:
        return False, "No file provided."

    filename = file.filename
    if '.' not in filename:
        return False, "Invalid file structure."

    ext = filename.rsplit('.', 1)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        return False, f"File extension .{ext} is not permitted."

    if category and category in SECURE_ALLOWED_CATEGORIES:
        if ext not in SECURE_ALLOWED_CATEGORIES[category]:
            return False, f"Extension .{ext} not allowed in category {category}."

    header = file.read(2048)
    file.seek(0)

    if ext == 'txt':
        try:
            header.decode('utf-8')
            return True, ""
        except UnicodeDecodeError:
            return False, "Invalid text encoding."

    if ext in MAGIC_NUMBERS:
        valid_sig = False
        for sig in MAGIC_NUMBERS[ext]:
            if ext in ['mp4', 'webp', 'wav']:
                if sig in header[:16]:
                    valid_sig = True
                    break
            elif header.startswith(sig):
                valid_sig = True
                break

        if not valid_sig:
            return False, f"Security alert: Content signature mismatch for (.{ext})."

    return True, ""


def allowed_file(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def make_unique_filename(filename):
    safe_name = secure_filename(filename)
    if not safe_name or "." not in safe_name:
        return uuid4().hex
    extension = safe_name.rsplit(".", 1)[1].lower()
    return f"{uuid4().hex}.{extension}"


def save_uploaded_file(file, category="Photo"):
    if not file or not file.filename:
        return None

    is_valid, err_msg = validate_file_security(file, category)
    if not is_valid:
        print(f"UPLOAD REJECTED: {err_msg}")
        return None

    try:
        # File seedha Cloudinary par jayegi, Render par nahi
        upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        return upload_result["secure_url"]  # Ab database mein filename ki jagah URL save hoga
    except Exception as e:
        print("CLOUDINARY UPLOAD ERROR:", repr(e))
        return None


# =========================================================
# BRUTE-FORCE PROTECTION & RATE LIMITING
# =========================================================

RATE_LIMIT_WINDOW = 10 * 60
RATE_LIMIT_MAX_FAILURES = 5
RATE_LIMIT_BLOCK_TIME = 10 * 60
_failed_attempts = defaultdict(deque)
_blocked_until = {}

def _rate_limit_key(scope):
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.remote_addr
    return f"{scope}:{ip or 'unknown'}"

def _rate_limited(scope):
    key = _rate_limit_key(scope)
    now = time.monotonic()
    blocked_until = _blocked_until.get(key, 0)
    if blocked_until > now:
        return True, int(blocked_until - now) + 1
    _blocked_until.pop(key, None)
    attempts = _failed_attempts[key]
    while attempts and now - attempts[0] > RATE_LIMIT_WINDOW:
        attempts.popleft()
    return False, 0

def _record_failed_attempt(scope):
    key = _rate_limit_key(scope)
    now = time.monotonic()
    attempts = _failed_attempts[key]
    while attempts and now - attempts[0] > RATE_LIMIT_WINDOW:
        attempts.popleft()
    attempts.append(now)
    if len(attempts) >= RATE_LIMIT_MAX_FAILURES:
        _blocked_until[key] = now + RATE_LIMIT_BLOCK_TIME
        attempts.clear()

def _clear_failed_attempts(scope):
    key = _rate_limit_key(scope)
    _failed_attempts.pop(key, None)
    _blocked_until.pop(key, None)


# =========================================================
# AUTHORIZATION & AUDIT LOGGING HELPERS (PHASE 4)
# =========================================================

def log_admin_action(actor_username, action, target_type, target_id, metadata=""):
    """
    Records sensitive admin actions into the audit_logs table.
    """
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO audit_logs (actor_username, action, target_type, target_id, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (actor_username, action, target_type, target_id, metadata)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("AUDIT LOG ERROR:", repr(e))


def get_current_user():
    username = session.get("username")
    if not username or not session.get("is_registered"):
        return None
    conn = get_db_connection()
    try:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()


def current_user_is_admin():
    user = get_current_user()
    return bool(user and user["role"] == "admin")


def sync_admin_session():
    is_admin = current_user_is_admin()
    session["is_admin"] = is_admin
    return is_admin


# =========================================================
# LOGIN / REGISTRATION ROUTE
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        limited, retry_after = _rate_limited("login")
        if limited:
            flash(f"Too many failed attempts. Try again in {retry_after // 60 + 1} minutes.", "error")
            return redirect(url_for("login"))

        action = request.form.get("action", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if action == "guest":
            session.clear()
            session["username"] = f"Guest-{uuid4().hex[:8]}"
            session["is_registered"] = False
            session["is_admin"] = False
            return redirect(url_for("dashboard"))

        if not username or len(username) > 40:
            flash("Invalid username length or input.", "error")
            return redirect(url_for("login"))

        allowed_username_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        if not all(char in allowed_username_chars for char in username):
            flash("Username contains restricted characters.", "error")
            return redirect(url_for("login"))

        if not password:
            flash("Please enter your password.", "error")
            return redirect(url_for("login"))

        if action == "register":
            if len(password) < 6:
                flash("Password must contain at least 6 characters.", "error")
                return redirect(url_for("login"))
            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return redirect(url_for("login"))

            conn = None
            try:
                conn = get_db_connection()
                existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                if existing:
                    flash("Username already exists.", "error")
                    return redirect(url_for("login"))

                password_hash = generate_password_hash(password)
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password_hash))
                conn.commit()

                session.clear()
                session["username"] = username
                session["is_registered"] = True
                session["is_admin"] = False
                flash("Account created successfully!", "success")
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                if conn: conn.rollback()
                flash("Username already exists.", "error")
                return redirect(url_for("login"))
            finally:
                if conn: conn.close()

        if action == "login":
            conn = None
            try:
                conn = get_db_connection()
                user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

                if not user:
                    _record_failed_attempt("login")
                    flash("Invalid username or password.", "error")
                    return redirect(url_for("login"))

                if "status" in user.keys() and user["status"] != "ACTIVE":
                    _record_failed_attempt("login")
                    flash("Account is suspended.", "error")
                    return redirect(url_for("login"))

                stored_password = user["password"] or ""
                password_valid = False
                legacy_password = False

                if stored_password.startswith(("pbkdf2:", "scrypt:")):
                    try:
                        password_valid = check_password_hash(stored_password, password)
                    except Exception:
                        password_valid = False
                else:
                    if stored_password == password:
                        password_valid = True
                        legacy_password = True

                if password_valid:
                    if legacy_password:
                        new_hash = generate_password_hash(password)
                        conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_hash, user["id"]))
                        conn.commit()

                    session.clear()
                    session["username"] = user["username"]
                    session["is_registered"] = True
                    session["is_admin"] = (user["role"] == "admin")
                    _clear_failed_attempts("login")
                    return redirect(url_for("dashboard"))

                _record_failed_attempt("login")
                flash("Invalid username or password.", "error")
                return redirect(url_for("login"))
            finally:
                if conn: conn.close()

    return render_template("login.html")


# =========================================================
# LOGOUT & CORE PAGES
# =========================================================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))
    sync_admin_session()
    return render_template("dashboard.html")


@app.route("/games")
def games():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("games.html")


# =========================================================
# PROFILE ROUTE
# =========================================================

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()

    if request.method == "POST":
        if "bio" in request.form:
            bio = request.form.get("bio", "").strip()
            country = request.form.get("country", "India").strip()
            conn.execute("UPDATE users SET bio = ?, country = ? WHERE username = ?", (bio, country, session["username"]))
            conn.commit()
            flash("Profile updated successfully!", "success")

        elif "profile_pic" in request.files:
            file = request.files["profile_pic"]
            filename = save_uploaded_file(file, "Photo")
            if filename:
                conn.execute("UPDATE users SET profile_pic = ? WHERE username = ?", (filename, session["username"]))
                conn.commit()
                flash("Profile picture updated!", "success")
            else:
                flash("Invalid profile picture format or failed validation.", "error")

        elif "story" in request.files:
            file = request.files["story"]
            filename = save_uploaded_file(file, "Photo")
            if filename:
                conn.execute("INSERT INTO stories (username, filename) VALUES (?, ?)", (session["username"], filename))
                conn.commit()
                flash("Story uploaded successfully!", "success")
            else:
                flash("Invalid story format or failed validation.", "error")

    user = conn.execute("SELECT * FROM users WHERE username = ?", (session["username"],)).fetchone()
    my_uploads = conn.execute("SELECT * FROM media WHERE uploaded_by = ? ORDER BY id DESC", (session["username"],)).fetchall()
    stories = conn.execute("SELECT * FROM stories WHERE username = ? ORDER BY id DESC", (session["username"],)).fetchall()
    post_count = len(my_uploads)
    conn.close()

    return render_template("profile.html", user=user, my_uploads=my_uploads, post_count=post_count, stories=stories)


# =========================================================
# GALLERY ROUTE
# =========================================================

@app.route("/gallery/<category>", methods=["GET", "POST"])
def gallery(category):
    if "username" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("media")
        filename = "SHAYARI_TEXT"

        if file and file.filename != "":
            saved_filename = save_uploaded_file(file, category)
            if saved_filename:
                filename = saved_filename
            else:
                flash("Invalid file format or validation error for this category!", "error")
                return redirect(url_for("gallery", category=category))

        is_approved = 1 if current_user_is_admin() else 0
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (?, ?, ?, ?, ?, ?)",
            (filename, request.form.get("title", "Untitled"), category, request.form.get("prompt", ""), session.get("username", "Anonymous"), is_approved)
        )
        conn.commit()
        conn.close()

        if is_approved:
            flash("File uploaded directly by Admin!", "success")
        else:
            flash("Uploaded! Sent to Admin for approval.", "success")

        return redirect(url_for("gallery", category=category))

    conn = get_db_connection()
    media_files = conn.execute("SELECT * FROM media WHERE category = ? AND approved = 1 ORDER BY id DESC", (category,)).fetchall()
    comments_db = conn.execute("SELECT * FROM comments ORDER BY id ASC").fetchall()
    conn.close()

    comments = {}
    for comment in comments_db:
        media_id = comment["media_id"]
        if media_id not in comments:
            comments[media_id] = []
        comments[media_id].append(comment)

    return render_template("gallery.html", media_files=media_files, category=category, comments=comments)


# =========================================================
# AI ENDPOINT & LIKES
# =========================================================

@app.route("/api/ai", methods=["POST"])
def ai_endpoint():
    if "username" not in session:
        return jsonify({"reply": "Please login first."}), 401

    data = request.get_json(silent=True) or {}
    user_query = data.get("query", "").strip()[:1000]

    if not user_query:
        return jsonify({"reply": "Please type something."})

    try:
        bot_reply = get_ai_response(user_query)
    except Exception:
        bot_reply = "Sorry, assistant is temporarily unavailable."

    return jsonify({"reply": bot_reply})


@app.route("/like/<int:media_id>", methods=["POST"])
def like(media_id):
    if "username" not in session:
        return jsonify({"error": "Login required."}), 401

    username = str(session["username"])
    conn = get_db_connection()
    try:
        media = conn.execute("SELECT id, likes FROM media WHERE id = ? AND approved = 1", (media_id,)).fetchone()
        if not media:
            return jsonify({"error": "Media not found."}), 404

        cursor = conn.execute("INSERT OR IGNORE INTO likes (media_id, username) VALUES (?, ?)", (media_id, username))
        if cursor.rowcount == 1:
            conn.execute("UPDATE media SET likes = likes + 1 WHERE id = ?", (media_id,))
            liked_now = True
        else:
            liked_now = False

        conn.commit()
        result = conn.execute("SELECT likes FROM media WHERE id = ?", (media_id,)).fetchone()
        return jsonify({"likes": result["likes"], "liked": liked_now, "already_liked": not liked_now})
    finally:
        conn.close()


# =========================================================
# ADMIN & MODERATION ROUTES
# =========================================================

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        limited, retry_after = _rate_limited("admin")
        if limited:
            flash(f"Too many failed attempts. Try again in {retry_after // 60 + 1} minutes.", "error")
            return render_template("admin.html", auth_required=True)

        if not session.get("username") or not session.get("is_registered"):
            flash("Please login first.", "error")
            return redirect(url_for("login"))

        entered_pass = request.form.get("passcode", "")
        if not ADMIN_PASSCODE or not hmac.compare_digest(entered_pass, ADMIN_PASSCODE):
            _record_failed_attempt("admin")
            flash("Access Denied: Incorrect Passcode!", "error")
            return render_template("admin.html", auth_required=True)

        conn = get_db_connection()
        conn.execute("UPDATE users SET role = 'admin' WHERE username = ?", (session["username"],))
        conn.commit()
        conn.close()

        session["is_admin"] = True
        _clear_failed_attempts("admin")
        flash("Admin role activated!", "success")

    if not current_user_is_admin():
        session["is_admin"] = False
        return render_template("admin.html", auth_required=True)

    session["is_admin"] = True
    conn = get_db_connection()
    pending_media = conn.execute("SELECT * FROM media WHERE approved = 0 ORDER BY id DESC").fetchall()
    conn.close()

    return render_template("admin.html", pending_media=pending_media, auth_required=False)


@app.route("/admin/audit-logs")
def admin_audit_logs():
    if not current_user_is_admin():
        session["is_admin"] = False
        flash("Admin access required.", "error")
        return redirect(url_for("admin"))

    conn = get_db_connection()
    logs = conn.execute(
        """
        SELECT * FROM audit_logs
        ORDER BY id DESC
        LIMIT 100
        """
    ).fetchall()
    conn.close()

    return render_template_string(
        """
        <!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Audit Logs | Apna Gallery Admin</title>
            <style>
                body { font-family: system-ui, sans-serif; background: #08111f; color: #fff; padding: 30px; margin: 0; }
                .container { max-width: 900px; margin: auto; background: #101b2d; padding: 24px; border-radius: 16px; border: 1px solid #2b405c; }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 0.9rem; }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #1e293b; }
                th { color: #38bdf8; }
                a { color: #38bdf8; text-decoration: none; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Security Audit Logs</h1>
                <p><a href="{{ url_for('admin') }}">&larr; Back to Admin Dashboard</a></p>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Actor</th>
                            <th>Action</th>
                            <th>Target</th>
                            <th>Timestamp</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in logs %}
                        <tr>
                            <td>{{ log.id }}</td>
                            <td>{{ log.actor_username }}</td>
                            <td>{{ log.action }}</td>
                            <td>{{ log.target_type }} #{{ log.target_id }}</td>
                            <td>{{ log.created_at }}</td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="5" style="text-align:center; color:#94a3b8;">No audit logs recorded yet.</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """,
        logs=logs
    )


@app.route("/approve/<int:id>", methods=["GET", "POST"])
def approve(id):
    if not current_user_is_admin():
        session["is_admin"] = False
        flash("Admin access required.", "error")
        return redirect(url_for("admin"))

    if request.method == "GET":
        token = get_csrf_token()
        return render_template_string(
            """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Confirm Approval</title></head>
            <body style="font-family:sans-serif;background:#08111f;color:#fff;display:grid;place-items:center;height:100vh;">
            <div style="background:#101b2d;padding:30px;border-radius:10px;text-align:center;">
            <h2>Approve this upload?</h2><form method="post">
            <input type="hidden" name="csrf_token" value="{{ token }}">
            <button type="submit" style="background:#22c55e;color:#fff;padding:10px 20px;border:none;border-radius:5px;cursor:pointer;">Confirm</button>
            <a href="{{ url_for('admin') }}" style="color:#aaa;margin-left:15px;">Cancel</a></form></div></body></html>""",
            token=token
        )

    if not validate_csrf_token(request.form.get("csrf_token", "")):
        flash("Security check failed.", "error")
        return redirect(url_for("admin"))

    conn = get_db_connection()
    conn.execute("UPDATE media SET approved = 1 WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    # PHASE 4: Audit Logging for Approval Action
    log_admin_action(session.get("username", "admin"), "APPROVE_MEDIA", "media", id)

    flash("Item successfully approved!", "success")
    return redirect(url_for("admin"))


@app.route("/delete/<int:id>", methods=["GET", "POST"])
def delete(id):
    if not current_user_is_admin():
        session["is_admin"] = False
        flash("Admin access required.", "error")
        return redirect(url_for("admin"))

    if request.method == "GET":
        token = get_csrf_token()
        return render_template_string(
            """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Confirm Delete</title></head>
            <body style="font-family:sans-serif;background:#08111f;color:#fff;display:grid;place-items:center;height:100vh;">
            <div style="background:#101b2d;padding:30px;border-radius:10px;text-align:center;">
            <h2>Delete this upload?</h2><form method="post">
            <input type="hidden" name="csrf_token" value="{{ token }}">
            <button type="submit" style="background:#ef4444;color:#fff;padding:10px 20px;border:none;border-radius:5px;cursor:pointer;">Delete</button>
            <a href="{{ url_for('admin') }}" style="color:#aaa;margin-left:15px;">Cancel</a></form></div></body></html>""",
            token=token
        )

    if not validate_csrf_token(request.form.get("csrf_token", "")):
        flash("Security check failed.", "error")
        return redirect(url_for("admin"))

    conn = get_db_connection()
    item = conn.execute("SELECT * FROM media WHERE id = ?", (id,)).fetchone()
    if item:
        filename = item["filename"]
        if filename and filename != "SHAYARI_TEXT":
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except OSError:
                pass
        conn.execute("DELETE FROM media WHERE id = ?", (id,))
        conn.commit()
    conn.close()

    # PHASE 4: Audit Logging for Delete Action
    log_admin_action(session.get("username", "admin"), "DELETE_MEDIA", "media", id)

    flash("Item deleted successfully.", "success")
    return redirect(url_for("admin"))


# =========================================================
# MYSTERY ROUTE & RATE LIMITING HOOK
# =========================================================

@app.before_request
def protect_mystery_code():
    if request.path != "/mystery" or request.method != "POST":
        return None

    limited, retry_after = _rate_limited("mystery")
    if limited:
        flash(f"Too many attempts. Try again in {retry_after // 60 + 1} minutes.", "error")
        return redirect(url_for("dashboard"))

    entered_code = request.form.get("passcode", "")
    if MYSTERY_CODE and entered_code and hmac.compare_digest(entered_code, MYSTERY_CODE):
        _clear_failed_attempts("mystery")
        return None

    _record_failed_attempt("mystery")
    return None


@app.route("/mystery", methods=["POST"])
def mystery():
    entered_code = request.form.get("passcode", "")
    if MYSTERY_CODE and entered_code == MYSTERY_CODE:
        return render_template("mystery.html")
    flash("Incorrect mystery code.", "error")
    return redirect(url_for("dashboard"))


# =========================================================
# ERROR HANDLERS & APP EXECUTION
# =========================================================

@app.errorhandler(404)
def not_found_error(error):
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template("500.html"), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    flash("File is too large. Maximum allowed size is 50 MB.", "error")
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=debug_mode)
