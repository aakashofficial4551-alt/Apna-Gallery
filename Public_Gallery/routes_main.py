import html
import urllib.parse
import re
import random
from collections import defaultdict
import requests
import psycopg2.extras
from flask import request, render_template, redirect, url_for, flash, session, jsonify, send_from_directory, make_response
from database import get_db_connection
from core_utils import get_ist_time, save_uploaded_file, hijack_bot_post, current_user_is_admin, sync_admin_session, ZERO_TOLERANCE_WORDS
import os

def init_main_routes(app):
    # 💥 STRICT PWA ROUTES FOR PWABUILDER 💥
    @app.route('/sw.js')
    def sw():
        response = make_response(send_from_directory('static', 'sw.js'))
        response.headers['Content-Type'] = 'application/javascript'
        response.headers['Service-Worker-Allowed'] = '/'
        return response

    @app.route('/manifest.json')
    def manifest():
        response = make_response(send_from_directory('static', 'manifest.json'))
        response.headers['Content-Type'] = 'application/manifest+json'
        return response

    @app.route("/")
    def index(): return render_template("index.html")

    @app.route("/terms")
    def terms(): return render_template("terms.html", hide_navbar=True)

    @app.route("/about")
    def about(): return render_template("about.html", hide_navbar=True)

    @app.route("/privacy")
    def privacy_policy(): return render_template("privacy.html", hide_navbar=True)

    @app.route("/upload_asset", methods=["POST"])
    def upload_asset():
        if "username" not in session: return redirect(url_for("login"))
        try:
            category = request.form.get("category", "Photo")
            title = html.escape(request.form.get("title", "Untitled"))
            visibility = request.form.get("visibility", "public")
            file = request.files.get("media")
            
            for word in ZERO_TOLERANCE_WORDS:
                if word in title.lower() or word in category.lower():
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("UPDATE users SET status = 'BANNED' WHERE username = %s", (session["username"],))
                    conn.commit()
                    conn.close()
                    session.clear()
                    flash("Zero Tolerance Policy Violated. Account Permanently Banned.", "error")
                    return redirect(url_for("index"))
                    
            filename = "SHAYARI_TEXT"
            if category != 'Shayari':
                if file and file.filename != "":
                    filename = save_uploaded_file(file, category)
                    if not filename:
                        flash("Upload Failed! Format not supported.", "error")
                        return redirect(request.referrer or url_for("dashboard"))
            
            is_approved = 1 if current_user_is_admin() else 0
            conn = get_db_connection()
            c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned, tips_received) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, FALSE, 0) RETURNING id",
                      (filename, title, category, "", session.get("username"), is_approved, visibility))
            new_media_id = c.fetchone()['id']
            
            for m in set(re.findall(r'@(\w+)', title)):
                if m != session["username"]:
                    c.execute("SELECT id FROM users WHERE username = %s", (m,))
                    if c.fetchone(): c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you!", f"/post/{new_media_id}"))
            conn.commit()
            conn.close()
            flash(f"Asset published as {visibility.upper()}!" if is_approved else "Asset sent to Admin for approval.", "success")
        except Exception as e:
            flash(f"Server Alert: {str(e)[:150]}", "error")
        return redirect(request.referrer or url_for("feed"))

    @app.route("/post/<int:media_id>")
    def view_post(media_id):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        block_filter = "m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND m.uploaded_by NOT IN (SELECT blocker FROM blocks WHERE blocked = %s)"
        c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.id = %s AND {block_filter}", (media_id, session["username"], session["username"]))
        post = c.fetchone()
        if not post:
            conn.close()
            return render_template("404.html")
        post_dict = dict(post)
        c.execute("SELECT c.*, u.role, u.is_verified FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = %s ORDER BY c.id ASC", (media_id,))
        post_dict['comments'] = [dict(row) for row in c.fetchall()]
        c.execute("SELECT id FROM bookmarks WHERE username = %s AND media_id = %s", (session["username"], media_id))
        post_dict['is_saved'] = bool(c.fetchone())
        conn.close()
        return render_template("single_post.html", post=post_dict, hide_navbar=True)

    @app.route("/feed")
    def feed():
        if "username" not in session: return redirect(url_for("login"))
        return render_template("feed.html", current_tab=request.args.get("tab", "foryou"))

    @app.route("/api/feed_data")
    def api_feed_data():
        if "username" not in session: return jsonify([])
        tab = request.args.get("tab", "foryou")
        offset = int(request.args.get("offset", 0))
        limit = 10 
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        block_filter = "m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND m.uploaded_by NOT IN (SELECT blocker FROM blocks WHERE blocked = %s)"
        if tab == "global":
            c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.is_pinned DESC, m.id DESC LIMIT %s OFFSET %s", (session["username"], session["username"], limit, offset))
            feed_posts = [dict(row) for row in c.fetchall()]
        else:
            c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username JOIN followers f ON f.following = m.uploaded_by WHERE f.follower = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.id DESC LIMIT %s OFFSET %s", (session["username"], session["username"], session["username"], limit, offset))
            feed_posts = [dict(row) for row in c.fetchall()]
            if not feed_posts and offset == 0:
                c.execute(f"SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.is_pinned DESC, m.id DESC LIMIT 10", (session["username"], session["username"]))
                feed_posts = [dict(row) for row in c.fetchall()]
        post_ids = [p['id'] for p in feed_posts]
        comments = defaultdict(list)
        if post_ids:
            c.execute("SELECT c.*, u.role, u.is_verified FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = ANY(%s) AND c.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND c.username NOT IN (SELECT blocker FROM blocks WHERE blocked = %s) ORDER BY c.id ASC", (post_ids, session["username"], session["username"]))
            for comment in c.fetchall(): comments[comment["media_id"]].append(dict(comment))
        c.execute("SELECT media_id FROM bookmarks WHERE username = %s", (session["username"],))
        saved_ids = [row['media_id'] for row in c.fetchall()]
        conn.close()
        for post in feed_posts:
            post['comments'] = comments[post['id']]
            post['is_saved'] = post['id'] in saved_ids
        return jsonify(feed_posts)

    @app.route("/tip/<int:media_id>", methods=["POST"])
    def tip_creator(media_id):
        if "username" not in session: return jsonify({"error": "Login required."}), 401
        tipper = str(session["username"])
        if tipper.startswith("Guest-"): return jsonify({"error": "Register a permanent account to use Coins! 🪙"}), 403
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        is_admin = session.get('role') == 'admin'
        c.execute("SELECT wallet_balance FROM users WHERE username = %s", (tipper,))
        user_data = c.fetchone()
        balance = user_data['wallet_balance'] if user_data else 0
        if not is_admin and balance < 10:
            conn.close()
            return jsonify({"error": "Insufficient Coins! You need at least 10 🪙."}), 400
        c.execute("SELECT uploaded_by, title FROM media WHERE id = %s", (media_id,))
        media = c.fetchone()
        if not media:
            conn.close()
            return jsonify({"error": "Media not found."}), 404
        creator = media['uploaded_by']
        if creator == tipper:
            conn.close()
            return jsonify({"error": "You cannot tip your own post!"}), 400
        try:
            if not is_admin: c.execute("UPDATE users SET wallet_balance = wallet_balance - 10 WHERE username = %s", (tipper,))
            c.execute("UPDATE users SET wallet_balance = wallet_balance + 10 WHERE username = %s", (creator,))
            c.execute("UPDATE media SET tips_received = COALESCE(tips_received, 0) + 10 WHERE id = %s", (media_id,))
            title_snippet = (media['title'][:15] + '...') if media['title'] else 'your post'
            badge = "👑 (Admin)" if is_admin else ""
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", 
                      (creator, f"💰 You received 10 Coins from {tipper} {badge} for '{title_snippet}'", f"/post/{media_id}"))
            conn.commit()
            hijack_bot_post(media_id, tipper)
            success = True
        except Exception:
            conn.rollback()
            success = False
        c.execute("SELECT tips_received FROM media WHERE id = %s", (media_id,))
        new_tips = c.fetchone()['tips_received']
        conn.close()
        return jsonify({"success": success, "new_tips": new_tips})

    @app.route("/api/view/<int:media_id>", methods=["POST"])
    def add_view(media_id):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE media SET views = views + 1 WHERE id = %s", (media_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    @app.route("/add_comment/<int:media_id>", methods=["POST"])
    def add_comment(media_id):
        if "username" not in session: return redirect(url_for("login"))
        text = html.escape(request.form.get("comment_text", "").strip())
        if text:
            conn = get_db_connection()
            c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            c.execute("INSERT INTO comments (media_id, username, comment_text) VALUES (%s, %s, %s)", (media_id, session["username"], text[:200]))
            c.execute("SELECT uploaded_by, title, category FROM media WHERE id = %s", (media_id,))
            media_info = c.fetchone()
            if media_info and media_info['uploaded_by'] != session["username"]:
                c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"💬 {session['username']} commented on {media_info['title'][:15]}...", f"/post/{media_id}"))
            for m in set(re.findall(r'@(\w+)', text)):
                if m != session["username"]:
                    c.execute("SELECT id FROM users WHERE username = %s", (m,))
                    if c.fetchone():
                        c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (m, f"📣 {session['username']} mentioned you in a comment!", f"/post/{media_id}"))
            conn.commit()
            conn.close()
            hijack_bot_post(media_id, session["username"])
            flash("Comment posted!", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/like/<int:media_id>", methods=["POST"])
    def like(media_id):
        if "username" not in session: return jsonify({"error": "Login required."}), 401
        username = str(session["username"])
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT (media_id, username) DO NOTHING", (media_id, username))
        if c.rowcount == 1:
            c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media_id,))
            c.execute("SELECT uploaded_by, title FROM media WHERE id = %s", (media_id,))
            media_info = c.fetchone()
            if media_info and media_info['uploaded_by'] != username:
                c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (media_info['uploaded_by'], f"❤️ {username} liked your asset: {media_info['title'][:15]}...", f"/post/{media_id}"))
            conn.commit()
            liked_now = True
            hijack_bot_post(media_id, username)
        else: liked_now = False
        c.execute("SELECT likes FROM media WHERE id = %s", (media_id,))
        likes = c.fetchone()["likes"]
        conn.close()
        return jsonify({"likes": likes, "liked": liked_now})

    @app.route("/like_comment/<int:comment_id>", methods=["POST"])
    def like_comment(comment_id):
        if "username" not in session: return jsonify({"error": "Login required."}), 401
        username = session["username"]
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("INSERT INTO comment_likes (comment_id, username) VALUES (%s, %s) ON CONFLICT (comment_id, username) DO NOTHING", (comment_id, username))
        if c.rowcount == 1:
            c.execute("UPDATE comments SET likes = COALESCE(likes, 0) + 1 WHERE id = %s", (comment_id,))
            c.execute("SELECT username, media_id FROM comments WHERE id = %s", (comment_id,))
            comment_info = c.fetchone()
            if comment_info and comment_info['username'] != username:
                c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (comment_info['username'], f"❤️ {username} liked your comment!", f"/post/{comment_info['media_id']}"))
            conn.commit()
        c.execute("SELECT likes FROM comments WHERE id = %s", (comment_id,))
        likes = c.fetchone()["likes"]
        conn.close()
        return jsonify({"likes": likes or 0})

    @app.route("/edit_post/<int:media_id>", methods=["POST"])
    def edit_post(media_id):
        if "username" not in session: return redirect(url_for("login"))
        new_title = html.escape(request.form.get("title", "").strip())
        if new_title:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE media SET title = %s WHERE id = %s AND uploaded_by = %s", (new_title, media_id, session["username"]))
            conn.commit()
            conn.close()
            flash("Post updated successfully!", "success")
        return redirect(request.referrer or url_for("profile"))

    @app.route("/pin_post/<int:media_id>", methods=["POST"])
    def pin_post(media_id):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE media SET is_pinned = FALSE WHERE uploaded_by = %s", (session["username"],))
        c.execute("UPDATE media SET is_pinned = TRUE WHERE id = %s AND uploaded_by = %s", (media_id, session["username"]))
        conn.commit()
        conn.close()
        flash("Post Pinned!", "success")
        return redirect(request.referrer or url_for("profile"))

    @app.route("/delete_own/<int:media_id>", methods=["POST"])
    def delete_own(media_id):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM media WHERE id = %s AND uploaded_by = %s", (media_id, session["username"]))
        conn.commit()
        conn.close()
        flash("Asset permanently deleted.", "success")
        return redirect(request.referrer or url_for("profile"))

    @app.route("/report/<int:media_id>", methods=["POST"])
    def report_asset(media_id):
        if "username" not in session: return jsonify({"error": "Login required"}), 401
        conn = get_db_connection()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO reports (media_id, reported_by, reason) VALUES (%s, %s, 'Inappropriate Content')", (media_id, session["username"]))
            conn.commit()
            return jsonify({"success": True})
        except:
            return jsonify({"error": "Already reported"}), 400
        finally: conn.close()

    @app.route("/notifications")
    def notifications():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT * FROM notifications WHERE username = %s ORDER BY id DESC LIMIT 50", (session["username"],))
        notifs = c.fetchall()
        c.execute("UPDATE notifications SET is_read = TRUE WHERE username = %s AND is_read = FALSE", (session["username"],))
        conn.commit()
        conn.close()
        return render_template("notifications.html", notifications=notifs, hide_navbar=True)

    @app.route("/api/unread_notifs")
    def api_unread_notifs():
        if "username" not in session: return jsonify({"unread": 0})
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT COUNT(id) as cnt FROM notifications WHERE username = %s AND is_read = FALSE", (session["username"],))
        res = c.fetchone()
        unread = res['cnt'] if res else 0
        conn.close()
        return jsonify({"unread": unread})

    @app.route("/clear_notifications", methods=["POST"])
    def clear_notifications():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM notifications WHERE username = %s", (session["username"],))
        conn.commit()
        conn.close()
        flash("Inbox cleared!", "success")
        return redirect(url_for("notifications"))

    @app.route("/leaderboard")
    def leaderboard():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT username, profile_pic, is_verified, wallet_balance, role FROM users WHERE role != 'bot' AND username NOT LIKE 'Guest-%%' ORDER BY wallet_balance DESC LIMIT 10")
        wealthy = [dict(row) for row in c.fetchall()]
        c.execute("""
            SELECT u.username, u.profile_pic, u.is_verified, COUNT(f.id) as follower_count 
            FROM users u LEFT JOIN followers f ON u.username = f.following 
            WHERE u.role != 'bot' AND u.username NOT LIKE 'Guest-%%'
            GROUP BY u.username, u.profile_pic, u.is_verified 
            ORDER BY follower_count DESC LIMIT 10
        """)
        famous = [dict(row) for row in c.fetchall()]
        conn.close()
        return render_template("leaderboard.html", wealthy=wealthy, famous=famous, hide_navbar=True)

    @app.route("/reels")
    def reels():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.category = 'Video' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY RANDOM() LIMIT 20", (session["username"],))
        videos = c.fetchall()
        conn.close()
        return render_template("reels.html", videos=videos)

    # 💥 AUTO-PILOT AI BOTS (Background Trigger) 💥
    @app.before_request
    def wake_up_bots():
        # Har request par 5% chance hai ki ek bot active hokar upload karega
        if request.endpoint not in ['static', 'sw', 'manifest'] and random.random() < 0.05:
            try:
                # Triggering bot logic silently
                from core_utils import execute_bot_routine
                execute_bot_routine()
            except:
                pass

    @app.route("/explore")
    def explore():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Spotlight user
        c.execute("SELECT username, profile_pic, bio, is_verified FROM users WHERE role = 'user' AND is_verified = TRUE AND username NOT LIKE 'Guest-%%' ORDER BY wallet_balance DESC LIMIT 1")
        spotlight = c.fetchone()
        
        # 💥 FIXED: Post ID linked properly for anchor scrolling
        c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, m.views, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY RANDOM() LIMIT 42", (session["username"],))
        explore_posts = [dict(row) for row in c.fetchall()]
        
        conn.close()
        return render_template("explore.html", posts=explore_posts, spotlight=spotlight)

    @app.route("/gallery/<category>", methods=["GET", "POST"])
    def gallery(category):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == "POST":
            try:
                filename = "SHAYARI_TEXT"
                if category != 'Shayari':
                    file = request.files.get("media")
                    if file and file.filename != "":
                        filename = save_uploaded_file(file, category)
                        if not filename:
                            flash("Upload Failed! Check API keys.", "error")
                            return redirect(url_for("gallery", category=category))
                is_approved = 1 if current_user_is_admin() else 0
                visibility = request.form.get("visibility", "public")
                c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, FALSE)",
                          (filename, html.escape(request.form.get("title", "Untitled")), category, "", session.get("username"), is_approved, visibility))
                conn.commit()
                flash("File live!" if is_approved else "Sent to Admin for approval.", "success")
            except Exception as e:
                conn.rollback()
                flash(f"Upload System Fault: {str(e)[:100]}", "error")
            return redirect(url_for("gallery", category=category))

        c.execute("SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.category = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY m.id DESC", (category, session["username"]))
        if category == 'Shayari': c.execute("SELECT m.*, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.category = %s AND m.approved = 1 AND m.visibility = 'public' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY m.id DESC", (category, session["username"]))
        media_files = c.fetchall()
        c.execute("SELECT c.*, u.role, u.is_verified FROM comments c JOIN users u ON c.username = u.username ORDER BY c.id ASC")
        comments_db = c.fetchall()
        conn.close()
        comments = defaultdict(list)
        for comment in comments_db: comments[comment["media_id"]].append(comment)
        return render_template("gallery.html", media_files=media_files, category=category, comments=comments)

    @app.route("/dashboard")
    def dashboard():
        if "username" not in session: return redirect(url_for("login"))
        sync_admin_session()
        try:
            conn = get_db_connection()
            c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            c.execute("SELECT id, title, filename, category, likes, views FROM media WHERE approved = 1 AND visibility = 'public' AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
            trending = c.fetchall()
            c.execute("SELECT s.id, s.username, s.filename, s.created_at, u.profile_pic, u.role, u.is_verified FROM stories s JOIN users u ON s.username = u.username WHERE s.created_at >= NOW() - INTERVAL '12 hours' AND s.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY s.id DESC", (session["username"],))
            stories = c.fetchall()
            conn.close()
            return render_template("dashboard.html", trending=trending, stories=stories)
        except Exception as e:
            return f"<div style='color:#f43f5e; padding:50px; text-align:center;'><h1>Dashboard Engine Error</h1><p>{str(e)}</p><a href='/feed'>Go back to Feed</a></div>"

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "update_profile":
                nickname = html.escape(request.form.get("nickname", session["username"]))
                bio = html.escape(request.form.get("bio", ""))
                country = html.escape(request.form.get("country", ""))
                if request.files.get("cover_pic"): c.execute("UPDATE users SET cover_pic = %s WHERE username = %s", (save_uploaded_file(request.files["cover_pic"], "Photo"), session["username"]))
                if request.files.get("profile_pic"): c.execute("UPDATE users SET profile_pic = %s WHERE username = %s", (save_uploaded_file(request.files["profile_pic"], "Photo"), session["username"]))
                c.execute("UPDATE users SET nickname = %s, bio = %s, country = %s WHERE username = %s", (nickname, bio, country, session["username"]))
                conn.commit()
                flash("Profile Updated!", "success")
            elif action == "story" and "story_media" in request.files:
                url = save_uploaded_file(request.files["story_media"], "Photo")
                if url: c.execute("INSERT INTO stories (username, filename) VALUES (%s, %s)", (session["username"], url))
            conn.commit()
            return redirect(url_for("profile"))

        c.execute("SELECT * FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        c.execute("SELECT * FROM media WHERE uploaded_by = %s ORDER BY is_pinned DESC, id DESC", (session["username"],))
        my_uploads = c.fetchall()
        c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (session["username"],))
        followers_count = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (session["username"],))
        following_count = c.fetchone()['cnt']
        c.execute("SELECT m.* FROM bookmarks b JOIN media m ON m.id = b.media_id WHERE b.username = %s ORDER BY b.id DESC", (session["username"],))
        saved_uploads = c.fetchall()
        conn.close()
        return render_template("profile.html", user=user, my_uploads=my_uploads, saved_uploads=saved_uploads, post_count=len(my_uploads), followers_count=followers_count, following_count=following_count)

    @app.route("/agent/<username>")
    def agent_profile(username):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT * FROM blocks WHERE (blocker = %s AND blocked = %s) OR (blocker = %s AND blocked = %s)", (session["username"], username, username, session["username"]))
        if c.fetchone():
            flash("You cannot view this profile.", "error")
            conn.close()
            return redirect(url_for("feed"))
        c.execute("SELECT * FROM users WHERE username = %s", (username,))
        agent = c.fetchone()
        if not agent:
            flash("Agent not found.", "error")
            conn.close()
            return redirect(url_for("feed"))
        c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (session["username"], username))
        is_following = bool(c.fetchone())
        if is_following:
            c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
        else:
            c.execute("SELECT m.*, u.role FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
        agent_uploads = c.fetchall()
        c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
        followers_count = c.fetchone()['cnt']
        c.execute("SELECT COUNT(*) as cnt FROM followers WHERE follower = %s", (username,))
        following_count = c.fetchone()['cnt']
        conn.close()
        return render_template("agent.html", agent=agent, uploads=agent_uploads, post_count=len(agent_uploads), followers_count=followers_count, following_count=following_count, is_following=is_following)

    @app.route("/follow/<username>", methods=["POST"])
    def follow(username):
        if "username" not in session: return jsonify({"error": "Login required"}), 401
        current_user = session["username"]
        if current_user == username: return jsonify({"error": "Cannot follow yourself"}), 400
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id FROM users WHERE username = %s", (username,))
        if not c.fetchone(): return jsonify({"error": "User not found"}), 404
        c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (current_user, username))
        is_following = c.fetchone()
        if is_following:
            c.execute("DELETE FROM followers WHERE follower = %s AND following = %s", (current_user, username))
            following_now = False
        else:
            c.execute("INSERT INTO followers (follower, following) VALUES (%s, %s)", (current_user, username))
            c.execute("INSERT INTO notifications (username, message, link) VALUES (%s, %s, %s)", (username, f"👤 {current_user} started following you!", f"/agent/{current_user}"))
            following_now = True
        conn.commit()
        c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
        followers_count = c.fetchone()['cnt']
        conn.close()
        return jsonify({"followers": followers_count, "following_now": following_now})

    @app.route("/api/network/<action_type>/<username>")
    def api_network(action_type, username):
        if "username" not in session: return jsonify([])
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if action_type == "followers":
            c.execute("SELECT u.username, u.profile_pic, u.role, u.is_verified FROM users u JOIN followers f ON u.username = f.follower WHERE f.following = %s", (username,))
        else:
            c.execute("SELECT u.username, u.profile_pic, u.role, u.is_verified FROM users u JOIN followers f ON u.username = f.following WHERE f.follower = %s", (username,))
        results = c.fetchall()
        conn.close()
        return jsonify([dict(r) for r in results])

    @app.route("/inbox")
    def inbox():
        if "username" not in session: return redirect(url_for("login"))
        me = session["username"]
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("""
            SELECT u.username, u.profile_pic, u.role, u.last_active, u.is_verified
            FROM users u 
            WHERE u.username IN (SELECT receiver FROM messages WHERE sender = %s UNION SELECT sender FROM messages WHERE receiver = %s)
            AND u.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s)
        """, (me, me, me))
        contacts = c.fetchall()
        final_contacts = []
        for contact in contacts:
            contact_dict = dict(contact)
            c.execute("SELECT COUNT(id) as cnt FROM messages WHERE sender = %s AND receiver = %s AND is_read = FALSE", (contact_dict['username'], me))
            contact_dict['unread'] = c.fetchone()['cnt']
            final_contacts.append(contact_dict)
        conn.close()
        return render_template("inbox.html", contacts=final_contacts, hide_navbar=True)

    @app.route("/chat/<username>", methods=["GET", "POST"])
    def chat(username):
        if "username" not in session: return redirect(url_for("login"))
        me = session["username"]
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT * FROM blocks WHERE (blocker = %s AND blocked = %s) OR (blocker = %s AND blocked = %s)", (me, username, username, me))
        if c.fetchone():
            flash("You cannot message this user.", "error")
            conn.close()
            return redirect(url_for("inbox"))
        if request.method == "POST":
            msg = html.escape(request.form.get("message", "").strip())
            if msg: c.execute("INSERT INTO messages (sender, receiver, message) VALUES (%s, %s, %s)", (me, username, msg))
            conn.commit()
            return redirect(url_for("chat", username=username))
        c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
        conn.commit()
        c.execute("SELECT username, profile_pic, role, last_active, is_verified FROM users WHERE username = %s", (username,))
        contact = c.fetchone()
        conn.close()
        if not contact: return redirect(url_for("inbox"))
        return render_template("chat.html", contact=contact, hide_navbar=True)

    @app.route("/api/chat_history/<username>")
    def api_chat_history(username):
        if "username" not in session: return jsonify([])
        me = session["username"]
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id, sender, message, TO_CHAR(created_at, 'HH24:MI') as time, is_read FROM messages WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s) ORDER BY created_at ASC", (me, username, username, me))
        history = c.fetchall()
        c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
        conn.commit()
        conn.close()
        return jsonify([dict(r) for r in history])

    @app.route("/unsend_message/<int:msg_id>", methods=["POST"])
    def unsend_message(msg_id):
        if "username" not in session: return jsonify({"success": False})
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM messages WHERE id = %s AND sender = %s", (msg_id, session["username"]))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    @app.route("/reply_story/<username>", methods=["POST"])
    def reply_story(username):
        if "username" not in session: return jsonify({"error": "Login required"}), 401
        msg = html.escape(request.form.get("message", "").strip())
        if msg:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO messages (sender, receiver, message) VALUES (%s, %s, %s)", (session["username"], username, f"Replying to your story: {msg}"))
            conn.commit()
            conn.close()
            return jsonify({"success": True})
        return jsonify({"error": "Empty message"}), 400

    @app.route("/global_chat", methods=["GET", "POST"])
    def global_chat():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor()
        if request.method == "POST":
            msg = html.escape(request.form.get("message", "").strip())
            if msg:
                c.execute("INSERT INTO global_chat (sender, message) VALUES (%s, %s)", (session["username"], msg[:500]))
                conn.commit()
            return redirect(url_for("global_chat"))
        conn.close()
        return render_template("global_chat.html", hide_navbar=True)

    @app.route("/api/global_chat_history")
    def api_global_chat_history():
        if "username" not in session: return jsonify([])
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute(f"SELECT gc.id, gc.sender, gc.message, TO_CHAR(gc.created_at, 'HH12:MI AM') as time, u.role, u.is_verified FROM global_chat gc JOIN users u ON gc.sender = u.username WHERE gc.sender NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY gc.created_at ASC", (session["username"],))
        history = c.fetchall()
        conn.close()
        return jsonify([dict(r) for r in history])

    @app.route("/search")
    def search():
        if "username" not in session: return redirect(url_for("login"))
        query = request.args.get("q", "").strip()
        if not query: return redirect(url_for("dashboard"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        clean_query = query.replace("#", "").replace("@", "")
        search_term = f"%{clean_query}%"
        c.execute("SELECT m.*, u.profile_pic, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND (m.title ILIKE %s OR m.prompt ILIKE %s) AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY m.id DESC", (search_term, search_term, session["username"]))
        media_files = [dict(row) for row in c.fetchall()]
        c.execute("SELECT username, profile_pic, role, bio, is_verified FROM users WHERE username ILIKE %s AND username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) LIMIT 20", (search_term, session["username"]))
        found_users = [dict(row) for row in c.fetchall()]
        conn.close()
        return render_template("search.html", media_files=media_files, found_users=found_users, query=query)

    @app.route("/api/search_suggest")
    def api_search_suggest():
        if "username" not in session: return jsonify([])
        q = request.args.get("q", "").strip()
        if not q: return jsonify([])
        clean_q = q.replace("#", "").replace("@", "").lower()
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT username, profile_pic, is_verified FROM users WHERE username ILIKE %s AND username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) LIMIT 5", (f"%{clean_q}%", session["username"]))
        users = [{"type": "user", "username": r['username'], "pic": r['profile_pic'], "verified": r['is_verified']} for r in c.fetchall()]
        c.execute("SELECT title FROM media WHERE title ILIKE %s LIMIT 10", (f"%#{clean_q}%",))
        tags = set()
        for r in c.fetchall():
            if r['title']:
                for t in re.findall(r'#(\w+)', r['title']):
                    if clean_q in t.lower(): tags.add(t)
        tags_list = [{"type": "tag", "tag": t} for t in list(tags)[:4]]
        conn.close()
        return jsonify(users + tags_list)

    @app.route("/ai-studio", methods=["GET", "POST"])
    def ai_studio():
        if "username" not in session: return redirect(url_for("login"))
        if request.method == "POST":
            prompt = request.form.get("prompt", "").strip()
            if not prompt: return redirect(url_for("ai_studio"))
            wants_image = any(word in prompt.lower() for word in ["create", "generate", "draw", "make an image", "paint", "banao"])
            if wants_image:
                encoded_prompt = urllib.parse.quote(prompt)
                seed_val = random.randint(1, 999999) 
                image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?nologo=true&seed={seed_val}&width=800&height=1000"
                try:
                    r = requests.get(image_url, timeout=15)
                    secure_url = cloudinary.uploader.upload(r.content, resource_type="image")["secure_url"] if r.status_code == 200 else image_url
                    conn = get_db_connection()
                    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                    c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned) VALUES (%s, %s, %s, %s, %s, 1, 'public', 0, FALSE) RETURNING id",
                              (secure_url, f"AI: {prompt[:20]}...", "Photo", prompt, session["username"]))
                    conn.commit()
                    conn.close()
                    flash("Image created successfully! (100% Free)", "success")
                    return redirect(url_for("gallery", category="Photo"))
                except: flash("Image generation failed.", "error")
            else:
                reply = "⚠️ AI Chatting feature has been temporarily disabled for maintenance. We will bring it back during the official launch! (You can still generate images by typing 'Create an image of...')"
                return render_template("ai_studio.html", chat_reply=reply, user_prompt=prompt, hide_navbar=True)
        return render_template("ai_studio.html", hide_navbar=True)

    @app.route("/analytics")
    def analytics():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT COUNT(id) as total_posts, SUM(views) as total_views, SUM(likes) as total_likes, SUM(tips_received) as total_tips FROM media WHERE uploaded_by = %s", (session["username"],))
        stats = c.fetchone()
        c.execute("SELECT title, views, likes, tips_received, category, filename FROM media WHERE uploaded_by = %s ORDER BY views DESC LIMIT 5", (session["username"],))
        top_posts = c.fetchall()
        conn.close()
        return render_template("analytics.html", stats=stats, top_posts=top_posts, hide_navbar=True)
