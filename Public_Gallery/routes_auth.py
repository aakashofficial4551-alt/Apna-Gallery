import html
import random
from uuid import uuid4
import datetime
import secrets
import smtplib
from email.mime.text import MIMEText
import psycopg2.extras
from flask import request, render_template, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from core_utils import get_ist_time

# 💥 GMAIL OTP MAILER SCRIPT 💥
def send_otp_email(receiver_email, otp):
    # DHYAAN DO: Apna asli Gmail ID aur App Password yahan dalna
    sender_email = "your-email@gmail.com"
    password = "your-app-password-here" 
    
    msg = MIMEText(f"Welcome to PHANTX. Your Login OTP is: {otp}\n\nPlease do not share this code with anyone. It expires in 10 minutes.")
    msg['Subject'] = 'PHANTX Security OTP'
    msg['From'] = sender_email
    msg['To'] = receiver_email
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print("Mail Sending Failed:", e)
        return False

def init_auth_routes(app):

    @app.route("/upgrade_guest", methods=["POST"])
    def upgrade_guest():
        if "username" not in session or not session["username"].startswith("Guest-"):
            return redirect(url_for("profile"))
            
        new_email = request.form.get("email").strip()
        new_username = request.form.get("new_username").strip()
        password = request.form.get("password", "")
        
        if not new_username.isalnum() or len(new_username) < 3:
            flash("Username must be at least 3 alphanumeric characters.", "error")
            return redirect(url_for("profile"))
            
        conn = get_db_connection()
        c = conn.cursor()
        try:
            # 1. Update Username in Users table
            c.execute("UPDATE users SET username = %s, email = %s, password = %s WHERE username = %s", (new_username, new_email, generate_password_hash(password), session["username"]))
            # 2. Transfer all records to new username
            tables_to_update = [
                ("media", "uploaded_by"), ("comments", "username"), ("likes", "username"),
                ("followers", "follower"), ("followers", "following"), ("messages", "sender"),
                ("messages", "receiver"), ("bookmarks", "username"), ("global_chat", "sender"),
                ("reports", "reported_by"), ("comment_likes", "username"), ("blocks", "blocker"),
                ("blocks", "blocked"), ("notifications", "username"), ("stories", "username")
            ]
            for table, col in tables_to_update:
                c.execute(f"UPDATE {table} SET {col} = %s WHERE {col} = %s", (new_username, session["username"]))
            
            conn.commit()
            session["username"] = new_username
            session["is_registered"] = True
            flash("Account Upgraded Permanently! 🚀 Welcome to the Elite club.", "success")
        except Exception as e:
            conn.rollback()
            flash("Username or Email is already taken by someone else.", "error")
        finally:
            conn.close()
            
        return redirect(url_for("profile"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("username"): return redirect(url_for("feed"))
        if request.method == "POST":
            action = request.form.get("action")
            username = html.escape(request.form.get("username", "").strip())
            password = request.form.get("password", "")
            
            if action == "guest":
                session.clear()
                guest_name = f"Guest-{uuid4().hex[:8]}"
                code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("INSERT INTO users (username, password, user_code, role, wallet_balance) VALUES (%s, %s, %s, 'user', 100)", (guest_name, "guest", code))
                conn.commit()
                conn.close()
                session.permanent = True
                session.update({"username": guest_name, "is_registered": False, "is_admin": False, "role": "user"})
                flash("Welcome! You've received 100 Free Coins 🪙", "success")
                return redirect(url_for("feed"))

            if action == "register":
                email = html.escape(request.form.get("email", "").strip())
                code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
                try:
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("INSERT INTO users (username, email, password, user_code, role, wallet_balance) VALUES (%s, %s, %s, %s, 'user', 100)", 
                              (username, email, generate_password_hash(password), code))
                    conn.commit()
                    session.permanent = True
                    session.update({"username": username, "is_registered": True, "is_admin": False, "role": "user"})
                    flash("Account created! You got 100 Welcome Coins 🪙", "success")
                    return redirect(url_for("feed"))
                except:
                    flash("Username or Email exists.", "error")
                finally: conn.close()
                return redirect(url_for("login"))

            if action == "login":
                conn = get_db_connection()
                c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
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
                    session.permanent = True
                    session.update({"username": user["username"], "is_registered": not is_guest, "is_admin": (user["role"] == "admin"), "role": user["role"]})
                    conn.close()
                    return redirect(url_for("feed"))
                conn.close()
                flash("Invalid credentials.", "error")
                
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/settings", methods=["POST"])
    def settings():
        if "username" not in session: return redirect(url_for("login"))
        action = request.form.get("action")
        conn = get_db_connection()
        c = conn.cursor()
        
        if action == "update_profile":
            bio = html.escape(request.form.get("bio", ""))
            website = html.escape(request.form.get("website", ""))
            theme = html.escape(request.form.get("theme", "cyan"))
            c.execute("UPDATE users SET bio = %s, theme = %s, website = %s WHERE username = %s", 
                      (bio, theme, website, session["username"]))
            flash("Profile Settings Updated!", "success")
            
        elif action == "change_password":
            new_pass = request.form.get("new_password")
            c.execute("UPDATE users SET password = %s WHERE username = %s", (generate_password_hash(new_pass), session["username"]))
            flash("Password Changed Successfully!", "success")

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
            return redirect(url_for("index"))
            
        conn.commit()
        conn.close()
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/buy_verification", methods=["POST"])
    def buy_verification():
        if "username" not in session: return redirect(url_for("login"))
        if str(session["username"]).startswith("Guest-"):
            flash("Guest accounts cannot be verified. Please upgrade your account first! ⭐", "error")
            return redirect(request.referrer or url_for("dashboard"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT wallet_balance, is_verified FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        is_admin = session.get('role') == 'admin'
        
        if user['is_verified']:
            flash("You are already verified! ✅", "success")
        elif is_admin or user['wallet_balance'] >= 1000:
            if not is_admin: c.execute("UPDATE users SET wallet_balance = wallet_balance - 1000, is_verified = TRUE WHERE username = %s", (session["username"],))
            else: c.execute("UPDATE users SET is_verified = TRUE WHERE username = %s", (session["username"],))
            conn.commit()
            flash("Congratulations! You bought the Verified Badge ✅", "success")
        else:
            flash("Not enough coins! You need 1000 🪙 to get verified.", "error")
        conn.close()
        return redirect(request.referrer or url_for("dashboard"))
