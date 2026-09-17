import html
import urllib.parse
import re
import random
from collections import defaultdict
import requests
import psycopg2.extras
from flask import request, render_template, redirect, url_for, flash, session, jsonify
from database import get_db_connection
from core_utils import get_ist_time, save_uploaded_file, hijack_bot_post, current_user_is_admin, sync_admin_session, ZERO_TOLERANCE_WORDS

def init_main_routes(app):
    @app.route("/")
    def index(): return render_template("index.html")

    @app.route("/terms")
    def terms(): return render_template("terms.html", hide_navbar=True)

    @app.route("/about")
    def about(): return render_template("about.html", hide_navbar=True)

    @app.route("/privacy")
    def privacy_policy(): return render_template("privacy.html", hide_navbar=True)

    @app.route("/manifest.json")
    def dynamic_manifest():
        return jsonify({"name": "PHANTX", "short_name": "PHANTX", "display": "standalone", "start_url": "/", "background_color": "#0f172a", "theme_color": "#0f172a", "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]})

    @app.route("/upload_asset", methods=["POST"])
    def upload_asset():
        if "username" not in session: return redirect(url_for("login"))
        try:
            category = request.form.get("category", "Photo")
            title = html.escape(request.form.get("title", "Untitled"))
            visibility = request.form.get("visibility", "public")
            allow_comments = request.form.get("allow_comments") == "true"
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
            c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned, tips_received, created_at, allow_comments) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, FALSE, 0, %s, %s) RETURNING id", (filename, title, category, "", session.get("username"), is_approved, visibility, get_ist_time(), allow_comments))
            new_media_id = c.fetchone()['id']
            for m in set(re.findall(r'@(\w+)', title)):
                if m != session["username"]:
                    c.execute("SELECT id FROM users WHERE username = %s", (m,))
                    if c.fetchone(): c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (m, f"📣 {session['username']} mentioned you!", f"/post/{new_media_id}", get_ist_time()))
            conn.commit()
            conn.close()
            flash(f"Asset published as {visibility.upper()}!" if is_approved else "Asset sent to Admin for approval.", "success")
        except Exception as e: flash(f"Server Alert: {str(e)[:150]}", "error")
        return redirect(request.referrer or url_for("feed"))

    @app.route("/post/<int:media_id>")
    def view_post(media_id):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        block_filter = "m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND m.uploaded_by NOT IN (SELECT blocker FROM blocks WHERE blocked = %s)"
        c.execute(f"SELECT m.*, u.profile_pic, u.nickname, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.id = %s AND {block_filter}", (media_id, session["username"], session["username"]))
        post = c.fetchone()
        if not post: return render_template("404.html")
        post_dict = dict(post)
        c.execute("SELECT c.*, u.role, u.is_verified, u.nickname FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = %s ORDER BY c.id ASC", (media_id,))
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
        ai_order_logic = "m.is_pinned DESC, m.id DESC"
        if tab == "global":
            c.execute(f"SELECT m.*, u.profile_pic, u.nickname, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY {ai_order_logic} LIMIT %s OFFSET %s", (session["username"], session["username"], limit, offset))
            feed_posts = [dict(row) for row in c.fetchall()]
        else:
            c.execute(f"SELECT m.*, u.profile_pic, u.nickname, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username JOIN followers f ON f.following = m.uploaded_by WHERE f.follower = %s AND m.approved = 1 AND m.visibility IN ('public', 'followers') AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY m.id DESC LIMIT %s OFFSET %s", (session["username"], session["username"], session["username"], limit, offset))
            feed_posts = [dict(row) for row in c.fetchall()]
            if not feed_posts and offset == 0:
                c.execute(f"SELECT m.*, u.profile_pic, u.nickname, u.role, u.is_verified FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND {block_filter} ORDER BY {ai_order_logic} LIMIT 10", (session["username"], session["username"]))
                feed_posts = [dict(row) for row in c.fetchall()]
        post_ids = [p['id'] for p in feed_posts]
        comments = defaultdict(list)
        if post_ids:
            c.execute("SELECT c.*, u.role, u.nickname, u.is_verified FROM comments c JOIN users u ON c.username = u.username WHERE c.media_id = ANY(%s) AND c.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) AND c.username NOT IN (SELECT blocker FROM blocks WHERE blocked = %s) ORDER BY c.id ASC", (post_ids, session["username"], session["username"]))
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
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        is_admin = session.get('role') == 'admin'
        c.execute("SELECT wallet_balance FROM users WHERE username = %s", (tipper,))
        user_data = c.fetchone()
        balance = user_data['wallet_balance'] if user_data else 0
        if not is_admin and balance < 10: return jsonify({"error": "Insufficient Coins! You need at least 10 🪙."}), 400
        c.execute("SELECT uploaded_by, title FROM media WHERE id = %s", (media_id,))
        media = c.fetchone()
        if not media: return jsonify({"error": "Media not found."}), 404
        creator = media['uploaded_by']
        if creator == tipper: return jsonify({"error": "You cannot tip your own post!"}), 400
        try:
            if not is_admin: c.execute("UPDATE users SET wallet_balance = wallet_balance - 10 WHERE username = %s", (tipper,))
            c.execute("UPDATE users SET wallet_balance = wallet_balance + 10 WHERE username = %s", (creator,))
            c.execute("UPDATE media SET tips_received = COALESCE(tips_received, 0) + 10 WHERE id = %s", (media_id,))
            c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (creator, f"💰 You received 10 Coins from {tipper} for '{media['title'][:15]}...'", f"/post/{media_id}", get_ist_time()))
            conn.commit()
            hijack_bot_post(media_id, tipper)
            success = True
        except: success = False
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
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT allow_comments, uploaded_by, title FROM media WHERE id = %s", (media_id,))
        media_info = c.fetchone()
        if media_info and media_info['allow_comments'] and text:
            c.execute("INSERT INTO comments (media_id, username, comment_text, created_at) VALUES (%s, %s, %s, %s)", (media_id, session["username"], text[:200], get_ist_time()))
            if media_info['uploaded_by'] != session["username"]:
                c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (media_info['uploaded_by'], f"💬 {session['username']} commented on {media_info['title'][:15]}...", f"/post/{media_id}", get_ist_time()))
            for m in set(re.findall(r'@(\w+)', text)):
                if m != session["username"]:
                    c.execute("SELECT id FROM users WHERE username = %s", (m,))
                    if c.fetchone(): c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (m, f"📣 {session['username']} mentioned you in a comment!", f"/post/{media_id}", get_ist_time()))
            conn.commit()
            hijack_bot_post(media_id, session["username"])
            flash("Comment posted!", "success")
        else: flash("Comments are disabled for this post.", "error")
        conn.close()
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
                c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (media_info['uploaded_by'], f"❤️ {username} liked your asset: {media_info['title'][:15]}...", f"/post/{media_id}", get_ist_time()))
            conn.commit()
            liked_now = True
            hijack_bot_post(media_id, username)
        else: liked_now = False
        c.execute("SELECT likes FROM media WHERE id = %s", (media_id,))
        likes = c.fetchone()["likes"]
        conn.close()
        return jsonify({"likes": likes, "liked": liked_now})

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
                if url: c.execute("INSERT INTO stories (username, filename, created_at) VALUES (%s, %s, %s)", (session["username"], url, get_ist_time()))
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
        conn.close()
        return render_template("profile.html", user=user, my_uploads=my_uploads, post_count=len(my_uploads), followers_count=followers_count, following_count=following_count)

    @app.route("/agent/<username>")
    def agent_profile(username):
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT * FROM blocks WHERE (blocker = %s AND blocked = %s) OR (blocker = %s AND blocked = %s)", (session["username"], username, username, session["username"]))
        if c.fetchone(): return redirect(url_for("feed"))
        c.execute("SELECT * FROM users WHERE username = %s", (username,))
        agent = c.fetchone()
        if not agent: return redirect(url_for("feed"))
        c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (session["username"], username))
        is_following = bool(c.fetchone())
        vis_filter = "('public', 'followers')" if is_following else "('public')"
        c.execute(f"SELECT m.*, u.role, u.nickname FROM media m JOIN users u ON m.uploaded_by = u.username WHERE m.uploaded_by = %s AND m.approved = 1 AND m.visibility IN {vis_filter} AND m.filename != 'SHAYARI_TEXT' ORDER BY m.is_pinned DESC, m.id DESC", (username,))
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
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id FROM followers WHERE follower = %s AND following = %s", (current_user, username))
        if c.fetchone():
            c.execute("DELETE FROM followers WHERE follower = %s AND following = %s", (current_user, username))
            following_now = False
        else:
            c.execute("INSERT INTO followers (follower, following, created_at) VALUES (%s, %s, %s)", (current_user, username, get_ist_time()))
            following_now = True
        conn.commit()
        c.execute("SELECT COUNT(*) as cnt FROM followers WHERE following = %s", (username,))
        followers_count = c.fetchone()['cnt']
        conn.close()
        return jsonify({"followers": followers_count, "following_now": following_now})

    @app.route("/dashboard")
    def dashboard():
        if "username" not in session: return redirect(url_for("login"))
        sync_admin_session()
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id, title, filename, category, likes, views FROM media WHERE approved = 1 AND visibility = 'public' AND filename != 'SHAYARI_TEXT' ORDER BY likes DESC LIMIT 3")
        trending = c.fetchall()
        c.execute("SELECT s.id, s.username, s.filename, s.created_at, u.profile_pic, u.role, u.is_verified, u.nickname FROM stories s JOIN users u ON s.username = u.username WHERE s.created_at >= %s - INTERVAL '12 hours' AND s.username NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY s.id DESC", (get_ist_time(), session["username"]))
        stories = c.fetchall()
        conn.close()
        return render_template("dashboard.html", trending=trending, stories=stories)

    @app.route("/explore")
    def explore():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT m.id, m.filename, m.title, m.category, m.likes, m.views, m.uploaded_by FROM media m WHERE m.approved = 1 AND m.visibility = 'public' AND m.filename != 'SHAYARI_TEXT' AND m.uploaded_by NOT IN (SELECT blocked FROM blocks WHERE blocker = %s) ORDER BY RANDOM() LIMIT 40", (session["username"],))
        explore_posts = [dict(row) for row in c.fetchall()]
        conn.close()
        return render_template("explore.html", posts=explore_posts)

    @app.route("/chat/<username>", methods=["GET", "POST"])
    def chat(username):
        if "username" not in session: return redirect(url_for("login"))
        me = session["username"]
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if request.method == "POST":
            msg = html.escape(request.form.get("message", "").strip())
            if msg: c.execute("INSERT INTO messages (sender, receiver, message, created_at) VALUES (%s, %s, %s, %s)", (me, username, msg, get_ist_time()))
            conn.commit()
            return redirect(url_for("chat", username=username))
        c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
        conn.commit()
        c.execute("SELECT username, nickname, profile_pic, role, last_active, is_verified FROM users WHERE username = %s", (username,))
        contact = c.fetchone()
        conn.close()
        return render_template("chat.html", contact=contact, hide_navbar=True)
        
    @app.route("/api/chat_history/<username>")
    def api_chat_history(username):
        if "username" not in session: return jsonify([])
        me = session["username"]
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT id, sender, message, TO_CHAR(created_at, 'HH12:MI AM') as time, is_read FROM messages WHERE (sender = %s AND receiver = %s) OR (sender = %s AND receiver = %s) ORDER BY created_at ASC", (me, username, username, me))
        history = c.fetchall()
        c.execute("UPDATE messages SET is_read = TRUE WHERE sender = %s AND receiver = %s AND is_read = FALSE", (username, me))
        conn.commit()
        conn.close()
        return jsonify([dict(r) for r in history])

    @app.route("/global_chat", methods=["GET", "POST"])
    def global_chat():
        if "username" not in session: return redirect(url_for("login"))
        conn = get_db_connection()
        c = conn.cursor()
        if request.method == "POST":
            msg = html.escape(request.form.get("message", "").strip())
            if msg:
                c.execute("INSERT INTO global_chat (sender, message, created_at) VALUES (%s, %s, %s)", (session["username"], msg[:500], get_ist_time()))
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
                    c.execute("INSERT INTO media (filename, title, category, prompt, uploaded_by, approved, visibility, views, is_pinned, created_at) VALUES (%s, %s, %s, %s, %s, 1, 'public', 0, FALSE, %s) RETURNING id",
                              (secure_url, f"AI: {prompt[:20]}...", "Photo", prompt, session["username"], get_ist_time()))
                    conn.commit()
                    conn.close()
                    flash("Image created successfully! (100% Free)", "success")
                    return redirect(url_for("dashboard"))
                except: flash("Image generation failed.", "error")
            else:
                reply = "⚠️ AI Chatting feature has been temporarily disabled for maintenance. We will bring it back during the official launch! (You can still generate images by typing 'Create an image of...')"
                return render_template("ai_studio.html", chat_reply=reply, user_prompt=prompt, hide_navbar=True)
        return render_template("ai_studio.html", hide_navbar=True)
