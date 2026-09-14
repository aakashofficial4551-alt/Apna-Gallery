import os
import sqlite3
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
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from ai_service import get_ai_response


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

load_dotenv(os.path.join(BASE_DIR, "..", ".env"))

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "development-only-secret-change-me"
)

app.config["UPLOAD_FOLDER"] = os.path.join(
    BASE_DIR,
    "static",
    "uploads"
)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


ADMIN_PASSCODE = os.environ.get("ADMIN_PASSCODE", "")
MYSTERY_CODE = os.environ.get("MYSTERY_CODE", "SOCHO")


ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
    "mp4",
    "webm",
    "mp3",
    "wav",
    "ogg",
    "pdf",
    "txt",
    "docx",
}


os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DATABASE_PATH = os.path.join(BASE_DIR, "database.db")


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# AUTO-HEAL DATABASE SCHEMA (Purani database ko naye system me auto-update karega)
def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT,
                        bio TEXT DEFAULT 'Classified Agent of SOCHO KYA HOGA.',
                        country TEXT DEFAULT 'India', friends_count INTEGER DEFAULT 0,
                        profile_pic TEXT DEFAULT '')''')
    conn.execute('''CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, title TEXT, 
                        category TEXT, prompt TEXT, uploaded_by TEXT, approved INTEGER DEFAULT 0,
                        likes INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, media_id INTEGER, 
                        user_name TEXT, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS stories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, 
                        filename TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()

    # Column Missing check - Fixes Admin approval not saving issue!
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(media)")
    cols = [col[1] for col in cursor.fetchall()]
    if 'approved' not in cols:
        try: cursor.execute("ALTER TABLE media ADD COLUMN approved INTEGER DEFAULT 0")
        except: pass
    if 'likes' not in cols:
        try: cursor.execute("ALTER TABLE media ADD COLUMN likes INTEGER DEFAULT 0")
        except: pass
    if 'uploaded_by' not in cols:
        try: cursor.execute("ALTER TABLE media ADD COLUMN uploaded_by TEXT DEFAULT 'Anonymous'")
        except: pass
    if 'prompt' not in cols:
        try: cursor.execute("ALTER TABLE media ADD COLUMN prompt TEXT DEFAULT ''")
        except: pass
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    DATABASE_PATH = os.path.join(BASE_DIR, "database.db")


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/login", methods=["GET", "POST"])
def login():

    if session.get("username"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        action = request.form.get("action", "").strip()

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        # -----------------------
        # Basic validation
        # -----------------------

        if not username:
            flash("Please enter a username.", "error")
            return redirect(url_for("login"))

        if len(username) > 40:
            flash("Username is too long.", "error")
            return redirect(url_for("login"))

        # -----------------------
        # Guest Login
        # -----------------------

        if action == "guest":

            session.clear()

            session["username"] = f"{username} (Guest)"
            session["is_registered"] = False

            return redirect(url_for("dashboard"))

        if not password:
            flash("Please enter your password.", "error")
            return redirect(url_for("login"))

        conn = get_db_connection()

        # -----------------------
        # Register
        # -----------------------

        if action == "register":

            if len(password) < 6:
                conn.close()

                flash(
                    "Password must contain at least 6 characters.",
                    "error"
                )

                return redirect(url_for("login"))

            password_hash = generate_password_hash(password)

            try:

                conn.execute(
                    """
                    INSERT INTO users
                    (username, password)
                    VALUES (?, ?)
                    """,
                    (
                        username,
                        password_hash,
                    ),
                )

                conn.commit()

                flash(
                    "Account created successfully. You can now sign in.",
                    "success"
                )

            except sqlite3.IntegrityError:

                flash(
                    "That username is already registered.",
                    "error"
                )

            finally:
                conn.close()

            return redirect(url_for("login"))

        # -----------------------
        # Login
        # -----------------------

        if action == "login":

            user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()

            if not user:

                conn.close()

                flash(
                    "Invalid username or password.",
                    "error"
                )

                return redirect(url_for("login"))

            stored_password = user["password"] or ""

            password_valid = False
            legacy_password = False

            # Modern hashed password
            if stored_password.startswith(
                ("pbkdf2:", "scrypt:")
            ):

                try:
                    password_valid = check_password_hash(
                        stored_password,
                        password
                    )

                except ValueError:
                    password_valid = False

            # Old Apna Gallery plaintext password
            else:

                if stored_password == password:
                    password_valid = True
                    legacy_password = True

            if password_valid:

                # Automatically secure old account
                if legacy_password:

                    new_hash = generate_password_hash(password)

                    conn.execute(
                        """
                        UPDATE users
                        SET password = ?
                        WHERE id = ?
                        """,
                        (
                            new_hash,
                            user["id"],
                        ),
                    )

                    conn.commit()

                conn.close()

                session.clear()

                session["username"] = user["username"]
                session["is_registered"] = True

                return redirect(url_for("dashboard"))

            conn.close()

            flash(
                "Invalid username or password.",
                "error"
            )

            return redirect(url_for("login"))

        conn.close()

        flash(
            "Invalid request.",
            "error"
        )

    return render_template("login.html")
