import html
import random
from uuid import uuid4
import datetime
import secrets
import psycopg2.extras
from flask import request, render_template, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from core_utils import get_ist_time, send_otp_email

def init_auth_routes(app):
    @app.route("/login", methods=["GET"])
    def login():
        if session.get("username"): return redirect(url_for("feed"))
        return render_template("login.html")

    @app.route("/request_otp", methods=["POST"])
    def request_otp():
        email = html.escape(request.form.get("email", "").strip().lower())
        if not email: return jsonify({"error": "Email is required"}), 400
        otp = str(random.randint(100000, 999999))
        expiry = get_ist_time() + datetime.timedelta(minutes=5)
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = c.fetchone()
        if not user:
            username = f"User_{uuid4().hex[:8]}"
            c.execute("INSERT INTO users (username, nickname, email, otp_code, otp_expiry, wallet_balance, role, created_at, last_active) VALUES (%s, %s, %s, %s, %s, 100, 'user', %s, %s)", 
                      (username, username, email, otp, expiry, get_ist_time(), get_ist_time()))
        else:
            c.execute("UPDATE users SET otp_code = %s, otp_expiry = %s WHERE email = %s", (otp, expiry, email))
        conn.commit()
        conn.close()
        send_otp_email(email, otp)
        return jsonify({"success": True, "message": "OTP sent to your email! (Check spam)"})

    @app.route("/verify_otp", methods=["POST"])
    def verify_otp():
        email = request.form.get("email", "").strip().lower()
        otp = request.form.get("otp", "").strip()
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT * FROM users WHERE email = %s AND otp_code = %s", (email, otp))
        user = c.fetchone()
        if user:
            if get_ist_time() > user['otp_expiry']:
                conn.close()
                return jsonify({"error": "OTP Expired! Request a new one."}), 400
            if user['status'] == 'BANNED':
                conn.close()
                return jsonify({"error": "Account Suspended for Policy Violation."}), 403
            c.execute("UPDATE users SET otp_code = NULL, otp_expiry = NULL, deletion_requested = NULL WHERE email = %s", (email,))
            ist_now = get_ist_time()
            last_reward = user.get('last_login_reward')
            reward_msg = "Welcome back!"
            if not last_reward or last_reward.date() < ist_now.date():
                c.execute("UPDATE users SET wallet_balance = wallet_balance + 5, last_login_reward = %s WHERE email = %s", (ist_now, email))
                reward_msg = "You received 5 Daily Login Coins! 🪙"
            conn.commit()
            conn.close()
            session.permanent = True
            session["username"] = user['username']
            session["role"] = user['role']
            session["is_admin"] = (user['role'] == 'admin')
            session["is_registered"] = True
            return jsonify({"success": True, "reward": reward_msg, "redirect": url_for("feed")})
        conn.close()
        return jsonify({"error": "Invalid OTP!"}), 400

    @app.route("/logout")
    def logout():
        if session.get("role") == "admin":
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE users SET role = 'user' WHERE username = %s", (session.get("username"),))
            conn.commit()
            conn.close()
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
            c.execute("UPDATE users SET bio = %s, theme = %s, website = %s WHERE username = %s", (bio, theme, website, session["username"]))
            flash("Profile Settings Updated!", "success")
        elif action == "unblock_user":
            target = request.form.get("blocked_username")
            c.execute("DELETE FROM blocks WHERE blocker = %s AND blocked = %s", (session["username"], target))
            flash(f"User unblocked.", "success")
        elif action == "delete_account":
            c.execute("UPDATE users SET deletion_requested = %s WHERE username = %s", (get_ist_time(), session["username"]))
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
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT wallet_balance, is_verified FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        is_admin = session.get('role') == 'admin'
        if user['is_verified']: flash("You are already verified! ✅", "success")
        elif is_admin or user['wallet_balance'] >= 1000:
            if not is_admin: c.execute("UPDATE users SET wallet_balance = wallet_balance - 1000, is_verified = TRUE WHERE username = %s", (session["username"],))
            else: c.execute("UPDATE users SET is_verified = TRUE WHERE username = %s", (session["username"],))
            conn.commit()
            flash("Congratulations! You bought the Verified Badge ✅", "success")
        else: flash("Not enough coins! You need 1000 🪙 to get verified.", "error")
        conn.close()
        return redirect(request.referrer or url_for("dashboard"))
