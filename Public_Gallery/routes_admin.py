import os
import html
import hmac
import psycopg2.extras
from flask import request, render_template, redirect, url_for, flash, session
from database import get_db_connection
from core_utils import get_ist_time, run_bot_engine

def init_admin_routes(app):
    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        if "username" not in session: return redirect(url_for("login"))
        if request.method == "POST":
            passcode = request.form.get("passcode", "")
            if hmac.compare_digest(passcode, os.environ.get("ADMIN_PASSCODE", "SUPREME123")):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE users SET role = 'user' WHERE role = 'admin'")
                c.execute("UPDATE users SET role = 'admin' WHERE username = %s", (session["username"],))
                conn.commit()
                conn.close()
                session["role"] = "admin"
                session["is_admin"] = True
                flash("Supreme Admin Access Granted. With great power comes great responsibility.", "success")
            else: flash("Access Denied.", "error")
            return redirect(url_for("admin"))

        if not session.get("is_admin"): return render_template("admin.html", auth_required=True)
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT * FROM media WHERE approved = 0 ORDER BY id DESC")
        pending = c.fetchall()
        c.execute("SELECT COUNT(*) as cnt FROM users")
        users_cnt = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'bot'")
        bot_cnt = c.fetchone()['cnt']
        c.execute("SELECT * FROM users ORDER BY id DESC")
        all_users = c.fetchall()
        c.execute("SELECT r.id as report_id, r.media_id, r.reported_by, r.reason, r.created_at, m.title, m.filename, m.category, m.uploaded_by FROM reports r JOIN media m ON r.media_id = m.id ORDER BY r.id DESC")
        reports = c.fetchall()
        conn.close()
        return render_template("admin.html", auth_required=False, pending_media=pending, user_count=users_cnt, bot_count=bot_cnt, all_users=all_users, reports=reports)

    @app.route("/admin/trigger_bots", methods=["POST"])
    def trigger_bots():
        if not session.get("is_admin"): return redirect(url_for("admin"))
        run_bot_engine()
        flash("🤖 Bot Engine Triggered Successfully!", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/broadcast", methods=["POST"])
    def admin_broadcast():
        if not session.get("is_admin"): return redirect(url_for("admin"))
        msg = html.escape(request.form.get("broadcast_message", "").strip())
        if msg:
            conn = get_db_connection()
            c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            c.execute("SELECT username FROM users WHERE role != 'bot'")
            users = c.fetchall()
            for u in users:
                c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)",
                          (u['username'], f"📣 <b>SYSTEM BROADCAST:</b> {msg}", "/dashboard", get_ist_time()))
            conn.commit()
            conn.close()
            flash("Broadcast sent to all agents! 📢", "success")
        return redirect(url_for("admin"))

    @app.route("/admin/user_action/<int:user_id>/<action>", methods=["POST"])
    def admin_user_action(user_id, action):
        if not session.get("is_admin"): return redirect(url_for("admin"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if action == "ban": 
            c.execute("UPDATE users SET status = 'BANNED' WHERE id = %s", (user_id,))
            flash("Agent Suspended!", "success")
        elif action == "unban": 
            c.execute("UPDATE users SET status = 'ACTIVE' WHERE id = %s", (user_id,))
            flash("Agent Reactivated!", "success")
        elif action == "verify": 
            c.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (user_id,))
            flash("Agent Verified ✅!", "success")
        elif action == "unverify": 
            c.execute("UPDATE users SET is_verified = FALSE WHERE id = %s", (user_id,))
            flash("Verification Removed.", "success")
        elif action == "delete_user":
            c.execute("SELECT username FROM users WHERE id = %s", (user_id,))
            u = c.fetchone()
            if u:
                uname = u['username']
                tables = ["media", "comments", "likes", "stories", "global_chat", "notifications"]
                for table in tables:
                    c.execute(f"DELETE FROM {table} WHERE {'uploaded_by' if table=='media' else 'sender' if table=='global_chat' else 'username'} = %s", (uname,))
                c.execute("DELETE FROM followers WHERE follower = %s OR following = %s", (uname, uname))
                c.execute("DELETE FROM messages WHERE sender = %s OR receiver = %s", (uname, uname))
                c.execute("DELETE FROM blocks WHERE blocker = %s OR blocked = %s", (uname, uname))
                c.execute("DELETE FROM users WHERE id = %s", (user_id,))
                flash(f"Agent '{uname}' permanently deleted. 🗑️", "success")
        conn.commit()
        conn.close()
        return redirect(url_for("admin"))

    @app.route("/approve/<int:id>", methods=["POST"])
    def approve(id):
        if not session.get("is_admin"): return redirect(url_for("admin"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("UPDATE media SET approved = 1 WHERE id = %s", (id,))
        c.execute("SELECT uploaded_by, title FROM media WHERE id = %s", (id,))
        media_info = c.fetchone()
        if media_info: c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (media_info['uploaded_by'], f"✅ Approved! '{media_info['title'][:15]}' is now live.", f"/post/{id}", get_ist_time()))
        conn.commit()
        conn.close()
        flash("Approved!", "success")
        return redirect(url_for("admin"))

    @app.route("/delete/<int:id>", methods=["POST"])
    def delete(id):
        if not session.get("is_admin"): return redirect(url_for("admin"))
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
        if not session.get("is_admin"): return redirect(url_for("admin"))
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM reports WHERE id = %s", (report_id,))
        conn.commit()
        conn.close()
        flash("Report dismissed.", "success")
        return redirect(url_for("admin"))
