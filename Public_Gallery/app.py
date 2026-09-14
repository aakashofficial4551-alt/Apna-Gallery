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

from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)

from werkzeug.utils import secure_filename

from ai_service import get_ai_response


# =========================================================
# BASE DIRECTORY
# =========================================================

BASE_DIR = os.path.abspath(
    os.path.dirname(__file__)
)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

# Local development ke liye .env load karega
load_dotenv(
    os.path.join(
        BASE_DIR,
        "..",
        ".env"
    )
)


# =========================================================
# FLASK APP
# =========================================================

app = Flask(__name__)


# Secret key environment se
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


# Maximum upload size = 50 MB
app.config["MAX_CONTENT_LENGTH"] = (
    50 * 1024 * 1024
)


# Session security
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# Render production HTTPS ke liye
app.config["SESSION_COOKIE_SECURE"] = True


# Upload directory create
os.makedirs(
    app.config["UPLOAD_FOLDER"],
    exist_ok=True
)


# =========================================================
# SECRET SETTINGS
# =========================================================

ADMIN_PASSCODE = os.environ.get(
    "ADMIN_PASSCODE",
    ""
)


MYSTERY_CODE = os.environ.get(
    "MYSTERY_CODE",
    "SOCHO"
)


# =========================================================
# ALLOWED FILE TYPES
# =========================================================

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


# =========================================================
# DATABASE
# =========================================================

DATABASE_PATH = os.path.join(
    BASE_DIR,
    "database.db"
)


def get_db_connection():

    conn = sqlite3.connect(
        DATABASE_PATH
    )

    conn.row_factory = sqlite3.Row

    return conn


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def init_db():

    conn = get_db_connection()

    # -----------------------------------------------------
    # USERS
    # -----------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            bio TEXT DEFAULT
                'Classified Agent of SOCHO KYA HOGA.',
            country TEXT DEFAULT 'India',
            friends_count INTEGER DEFAULT 0,
            profile_pic TEXT DEFAULT ''
        )
        """
    )


    # -----------------------------------------------------
    # MEDIA
    # -----------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            title TEXT,
            category TEXT,
            prompt TEXT,
            uploaded_by TEXT,
            approved INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


    # -----------------------------------------------------
    # COMMENTS
    # -----------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            user_name TEXT,
            comment TEXT,
            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


    # -----------------------------------------------------
    # STORIES
    # -----------------------------------------------------

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            filename TEXT,
            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


    conn.commit()


    # =====================================================
    # AUTO-HEAL OLD DATABASE
    # =====================================================

    cursor = conn.cursor()

    cursor.execute(
        "PRAGMA table_info(media)"
    )

    columns = [
        column[1]
        for column in cursor.fetchall()
    ]


    # approved column
    if "approved" not in columns:

        try:

            cursor.execute(
                """
                ALTER TABLE media
                ADD COLUMN approved
                INTEGER DEFAULT 0
                """
            )

        except sqlite3.OperationalError:
            pass


    # likes column
    if "likes" not in columns:

        try:

            cursor.execute(
                """
                ALTER TABLE media
                ADD COLUMN likes
                INTEGER DEFAULT 0
                """
            )

        except sqlite3.OperationalError:
            pass


    # uploaded_by column
    if "uploaded_by" not in columns:

        try:

            cursor.execute(
                """
                ALTER TABLE media
                ADD COLUMN uploaded_by
                TEXT DEFAULT 'Anonymous'
                """
            )

        except sqlite3.OperationalError:
            pass


    # prompt column
    if "prompt" not in columns:

        try:

            cursor.execute(
                """
                ALTER TABLE media
                ADD COLUMN prompt
                TEXT DEFAULT ''
                """
            )

        except sqlite3.OperationalError:
            pass


    conn.commit()

    conn.close()


# Database initialize
init_db()


# =========================================================
# FILE HELPERS
# =========================================================

def allowed_file(filename):

    if not filename:
        return False

    if "." not in filename:
        return False

    extension = (
        filename
        .rsplit(".", 1)[1]
        .lower()
    )

    return extension in ALLOWED_EXTENSIONS


def make_unique_filename(filename):

    safe_name = secure_filename(
        filename
    )

    if not safe_name:

        return uuid4().hex


    if "." not in safe_name:

        return uuid4().hex


    extension = (
        safe_name
        .rsplit(".", 1)[1]
        .lower()
    )


    return (
        f"{uuid4().hex}."
        f"{extension}"
    )


def save_uploaded_file(file):

    if not file:
        return None

    if not file.filename:
        return None

    if not allowed_file(
        file.filename
    ):
        return None


    filename = make_unique_filename(
        file.filename
    )


    filepath = os.path.join(
        app.config["UPLOAD_FOLDER"],
        filename
    )


    file.save(filepath)


    return filename


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if session.get("username"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        action = request.form.get("action", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Guest login does not require username/password
        if action == "guest":
            session.clear()
            session["username"] = f"Guest-{uuid4().hex[:8]}"
            session["is_registered"] = False
            session["is_admin"] = False
            return redirect(url_for("dashboard"))

        if not username:
            flash("Please enter a username.", "error")
            return redirect(url_for("login"))

        if len(username) > 40:
            flash("Username is too long.", "error")
            return redirect(url_for("login"))

        allowed_username_chars = (
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            "_-"
        )

        if not all(char in allowed_username_chars for char in username):
            flash(
                "Username can contain only letters, numbers, _ and -.",
                "error"
            )
            return redirect(url_for("login"))

        if not password:
            flash("Please enter your password.", "error")
            return redirect(url_for("login"))

        if action == "register":

            if len(password) < 6:
                flash(
                    "Password must contain at least 6 characters.",
                    "error"
                )
                return redirect(url_for("login"))

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return redirect(url_for("login"))

            conn = None

            try:
                conn = get_db_connection()

                existing_user = conn.execute(
                    "SELECT id FROM users WHERE username = ?",
                    (username,)
                ).fetchone()

                if existing_user:
                    flash(
                        "Username already exists. Please choose another.",
                        "error"
                    )
                    return redirect(url_for("login"))

                password_hash = generate_password_hash(password)

                conn.execute(
                    """
                    INSERT INTO users (username, password)
                    VALUES (?, ?)
                    """,
                    (username, password_hash)
                )
                conn.commit()

                session.clear()
                session["username"] = username
                session["is_registered"] = True
                session["is_admin"] = False

                flash(
                    "Account created successfully! Welcome to Apna Gallery.",
                    "success"
                )
                return redirect(url_for("dashboard"))

            except sqlite3.IntegrityError:
                if conn:
                    conn.rollback()
                flash(
                    "Username already exists. Please choose another.",
                    "error"
                )
                return redirect(url_for("login"))

            except Exception as e:
                print("REGISTER ERROR:", repr(e))
                if conn:
                    conn.rollback()
                flash(
                    "Account creation failed. Please try again.",
                    "error"
                )
                return redirect(url_for("login"))

            finally:
                if conn:
                    conn.close()

        if action == "login":

            conn = None

            try:
                conn = get_db_connection()

                user = conn.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE username = ?
                    """,
                    (username,)
                ).fetchone()

                if not user:
                    flash(
                        "Invalid username or password.",
                        "error"
                    )
                    return redirect(url_for("login"))

                stored_password = user["password"] or ""
                password_valid = False
                legacy_password = False

                if stored_password.startswith(("pbkdf2:", "scrypt:")):
                    try:
                        password_valid = check_password_hash(
                            stored_password,
                            password
                        )
                    except Exception as e:
                        print("PASSWORD HASH ERROR:", repr(e))
                        password_valid = False
                else:
                    if stored_password == password:
                        password_valid = True
                        legacy_password = True

                if password_valid:

                    if legacy_password:
                        new_hash = generate_password_hash(password)
                        conn.execute(
                            """
                            UPDATE users
                            SET password = ?
                            WHERE id = ?
                            """,
                            (new_hash, user["id"])
                        )
                        conn.commit()

                    session.clear()
                    session["username"] = user["username"]
                    session["is_registered"] = True
                    session["is_admin"] = False

                    return redirect(url_for("dashboard"))

                flash(
                    "Invalid username or password.",
                    "error"
                )
                return redirect(url_for("login"))

            except Exception as e:
                print("LOGIN ERROR:", repr(e))
                flash(
                    "Login failed. Please try again.",
                    "error"
                )
                return redirect(url_for("login"))

            finally:
                if conn:
                    conn.close()

        flash("Invalid request.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# =========================================================
# HOME / DASHBOARD
# =========================================================

@app.route("/")
@app.route("/dashboard")
def dashboard():

    if "username" not in session:

        return redirect(
            url_for("login")
        )


    return render_template(
        "dashboard.html"
    )


# =========================================================
# GAMES
# =========================================================

@app.route("/games")
def games():

    if "username" not in session:

        return redirect(
            url_for("login")
        )


    return render_template(
        "games.html"
    )


# =========================================================
# PROFILE
# =========================================================

@app.route(
    "/profile",
    methods=["GET", "POST"]
)
def profile():

    if "username" not in session:

        return redirect(
            url_for("login")
        )


    conn = get_db_connection()


    # =====================================================
    # PROFILE UPDATE
    # =====================================================

    if request.method == "POST":


        # -------------------------------------------------
        # BIO UPDATE
        # -------------------------------------------------

        if "bio" in request.form:

            bio = request.form.get(
                "bio",
                ""
            ).strip()


            country = request.form.get(
                "country",
                "India"
            ).strip()


            conn.execute(
                """
                UPDATE users
                SET
                    bio = ?,
                    country = ?
                WHERE username = ?
                """,
                (
                    bio,
                    country,
                    session["username"],
                ),
            )


            conn.commit()


            flash(
                "Profile updated successfully!",
                "success"
            )


        # -------------------------------------------------
        # PROFILE PICTURE
        # -------------------------------------------------

        elif "profile_pic" in request.files:

            file = request.files[
                "profile_pic"
            ]


            filename = save_uploaded_file(
                file
            )


            if filename:

                conn.execute(
                    """
                    UPDATE users
                    SET profile_pic = ?
                    WHERE username = ?
                    """,
                    (
                        filename,
                        session["username"],
                    ),
                )


                conn.commit()


                flash(
                    "Profile picture updated!",
                    "success"
                )


            else:

                flash(
                    "Invalid profile picture format.",
                    "error"
                )


        # -------------------------------------------------
        # STORY
        # -------------------------------------------------

        elif "story" in request.files:

            file = request.files[
                "story"
            ]


            filename = save_uploaded_file(
                file
            )


            if filename:

                conn.execute(
                    """
                    INSERT INTO stories
                    (
                        username,
                        filename
                    )
                    VALUES (?, ?)
                    """,
                    (
                        session["username"],
                        filename,
                    ),
                )


                conn.commit()


                flash(
                    "Story uploaded successfully!",
                    "success"
                )


            else:

                flash(
                    "Invalid story file format.",
                    "error"
                )


    # =====================================================
    # USER DATA
    # =====================================================

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE username = ?
        """,
        (
            session["username"],
        ),
    ).fetchone()


    # =====================================================
    # USER UPLOADS
    # =====================================================

    my_uploads = conn.execute(
        """
        SELECT *
        FROM media
        WHERE uploaded_by = ?
        ORDER BY id DESC
        """,
        (
            session["username"],
        ),
    ).fetchall()


    # =====================================================
    # STORIES
    # =====================================================

    stories = conn.execute(
        """
        SELECT *
        FROM stories
        WHERE username = ?
        ORDER BY id DESC
        """,
        (
            session["username"],
        ),
    ).fetchall()


    post_count = len(
        my_uploads
    )


    conn.close()


    return render_template(
        "profile.html",
        user=user,
        my_uploads=my_uploads,
        post_count=post_count,
        stories=stories,
    )


# =========================================================
# GALLERY
# =========================================================

@app.route(
    "/gallery/<category>",
    methods=["GET", "POST"]
)
def gallery(category):

    if "username" not in session:

        return redirect(
            url_for("login")
        )


    # =====================================================
    # UPLOAD
    # =====================================================

    if request.method == "POST":

        file = request.files.get(
            "media"
        )


        filename = "SHAYARI_TEXT"


        # -------------------------------------------------
        # REAL FILE
        # -------------------------------------------------

        if file and file.filename != "":

            saved_filename = (
                save_uploaded_file(
                    file
                )
            )


            if saved_filename:

                filename = saved_filename

            else:

                flash(
                    "Invalid file format!",
                    "error"
                )

                return redirect(
                    url_for(
                        "gallery",
                        category=category
                    )
                )


        # =================================================
        # ADMIN = DIRECT APPROVAL
        # NORMAL USER = PENDING
        # =================================================

        is_approved = (
            1
            if session.get("is_admin")
            else 0
        )


        conn = get_db_connection()


        conn.execute(
            """
            INSERT INTO media
            (
                filename,
                title,
                category,
                prompt,
                uploaded_by,
                approved
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                filename,

                request.form.get(
                    "title",
                    "Untitled"
                ),

                category,

                request.form.get(
                    "prompt",
                    ""
                ),

                session.get(
                    "username",
                    "Anonymous"
                ),

                is_approved,
            ),
        )


        conn.commit()

        conn.close()


        # =================================================
        # FLASH MESSAGE
        # =================================================

        if is_approved:

            flash(
                "File uploaded directly by Admin!",
                "success"
            )

        else:

            flash(
                "Uploaded! Sent to Admin for approval.",
                "success"
            )


        return redirect(
            url_for(
                "gallery",
                category=category
            )
        )


    # =====================================================
    # FETCH APPROVED MEDIA
    # =====================================================

    conn = get_db_connection()


    media_files = conn.execute(
        """
        SELECT *
        FROM media
        WHERE
            category = ?
            AND approved = 1
        ORDER BY id DESC
        """,
        (
            category,
        ),
    ).fetchall()


    # =====================================================
    # FETCH COMMENTS
    # =====================================================

    comments_db = conn.execute(
        """
        SELECT *
        FROM comments
        ORDER BY id ASC
        """
    ).fetchall()


    conn.close()


    # =====================================================
    # ORGANIZE COMMENTS
    # =====================================================

    comments = {}


    for comment in comments_db:

        media_id = comment[
            "media_id"
        ]


        if media_id not in comments:

            comments[media_id] = []


        comments[
            media_id
        ].append(
            comment
        )


    # =====================================================
    # RENDER
    # =====================================================

    return render_template(
        "gallery.html",

        media_files=media_files,

        category=category,

        comments=comments,
    )


# =========================================================
# AI CHAT API
# =========================================================

@app.route(
    "/api/ai",
    methods=["POST"]
)
def ai_endpoint():

    # Security: login required
    if "username" not in session:

        return jsonify(
            {
                "reply": "Please login first."
            }
        ), 401


    data = (
        request.get_json(
            silent=True
        )
        or {}
    )


    user_query = (
        data.get(
            "query",
            ""
        )
        or ""
    ).strip()


    # Limit query length
    user_query = user_query[
        :1000
    ]


    if not user_query:

        return jsonify(
            {
                "reply":
                    "Please type something."
            }
        )


    try:

        bot_reply = get_ai_response(
            user_query
        )

    except Exception:

        bot_reply = (
            "Sorry, assistant is "
            "temporarily unavailable."
        )


    return jsonify(
        {
            "reply": bot_reply
        }
    )


# =========================================================
# LIKE
# =========================================================

@app.route(
    "/like/<int:media_id>",
    methods=["POST"]
)
def like(media_id):

    if "username" not in session:

        return jsonify(
            {
                "error":
                    "Login required."
            }
        ), 401


    conn = get_db_connection()


    # Check media exists
    media = conn.execute(
        """
        SELECT id
        FROM media
        WHERE id = ?
        """,
        (
            media_id,
        ),
    ).fetchone()


    if not media:

        conn.close()

        return jsonify(
            {
                "error":
                    "Media not found."
            }
        ), 404


    # Existing like system preserved
    conn.execute(
        """
        UPDATE media
        SET likes = likes + 1
        WHERE id = ?
        """,
        (
            media_id,
        ),
    )


    conn.commit()


    result = conn.execute(
        """
        SELECT likes
        FROM media
        WHERE id = ?
        """,
        (
            media_id,
        ),
    ).fetchone()


    conn.close()


    return jsonify(
        {
            "likes":
                result["likes"]
        }
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route(
    "/admin",
    methods=["GET", "POST"]
)
def admin():

    # =====================================================
    # ADMIN LOGIN
    # =====================================================

    if request.method == "POST":

        entered_pass = (
            request.form
            .get("passcode", "")
        )


        # Environment variable required
        if (
            ADMIN_PASSCODE
            and entered_pass
            == ADMIN_PASSCODE
        ):

            session["is_admin"] = True


            flash(
                "Admin Access Granted!",
                "success"
            )


        else:

            flash(
                "Access Denied: Incorrect Passcode!",
                "error"
            )


    # =====================================================
    # ADMIN ACCESS CHECK
    # =====================================================

    if not session.get(
        "is_admin"
    ):

        return render_template(
            "admin.html",
            auth_required=True
        )


    # =====================================================
    # PENDING MEDIA
    # =====================================================

    conn = get_db_connection()


    pending_media = conn.execute(
        """
        SELECT *
        FROM media
        WHERE approved = 0
        ORDER BY id DESC
        """
    ).fetchall()


    conn.close()


    return render_template(
        "admin.html",
        pending_media=pending_media,
        auth_required=False
    )


# =========================================================
# APPROVE MEDIA
# =========================================================

@app.route(
    "/approve/<int:id>"
)
def approve(id):

    if not session.get(
        "is_admin"
    ):

        flash(
            "Admin access required.",
            "error"
        )

        return redirect(
            url_for("admin")
        )


    conn = get_db_connection()


    conn.execute(
        """
        UPDATE media
        SET approved = 1
        WHERE id = ?
        """,
        (
            id,
        ),
    )


    conn.commit()

    conn.close()


    flash(
        "Item successfully approved and published!",
        "success"
    )


    return redirect(
        url_for("admin")
    )


# =========================================================
# DELETE MEDIA
# =========================================================

@app.route(
    "/delete/<int:id>"
)
def delete(id):

    if not session.get(
        "is_admin"
    ):

        flash(
            "Admin access required.",
            "error"
        )

        return redirect(
            url_for("admin")
        )


    conn = get_db_connection()


    item = conn.execute(
        """
        SELECT *
        FROM media
        WHERE id = ?
        """,
        (
            id,
        ),
    ).fetchone()


    if item:

        filename = item[
            "filename"
        ]


        # Delete actual uploaded file
        if (
            filename
            and filename != "SHAYARI_TEXT"
        ):

            filepath = os.path.join(
                app.config[
                    "UPLOAD_FOLDER"
                ],
                filename
            )


            try:

                if os.path.exists(
                    filepath
                ):

                    os.remove(
                        filepath
                    )

            except OSError:

                pass


        # Delete database entry
        conn.execute(
            """
            DELETE FROM media
            WHERE id = ?
            """,
            (
                id,
            ),
        )


        conn.commit()


    conn.close()


    return redirect(
        request.referrer
        or url_for("dashboard")
    )


# =========================================================
# MYSTERY
# =========================================================

@app.route(
    "/mystery",
    methods=["POST"]
)
def mystery():

    entered_code = (
        request.form
        .get("passcode", "")
    )


    if (
        MYSTERY_CODE
        and entered_code
        == MYSTERY_CODE
    ):

        return render_template(
            "mystery.html"
        )


    flash(
        "Incorrect mystery code.",
        "error"
    )


    return redirect(
        url_for("dashboard")
    )


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(
    413
)
def request_entity_too_large(error):

    flash(
        "File is too large. Maximum allowed size is 50 MB.",
        "error"
    )


    return redirect(
        request.referrer
        or url_for("dashboard")
    )


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":

    debug_mode = (
        os.environ.get(
            "FLASK_DEBUG",
            "false"
        ).lower()
        == "true"
    )


    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        ),
        debug=debug_mode
    )
