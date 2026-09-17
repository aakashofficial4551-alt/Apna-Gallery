import os
import time
import datetime
import random
import smtplib
from email.mime.text import MIMEText
import urllib.parse
import secrets
import json
import psycopg2.extras
import cloudinary.uploader
from flask import session
from database import get_db_connection

ZERO_TOLERANCE_WORDS = ['nude', 'sex', 'porn', 'nsfw', 'naked', 'xxx', 'boobs', 'dick', 'pussy']

def get_ist_time():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)

def current_user_is_admin():
    return session.get("role") == "admin"

def sync_admin_session():
    if "username" in session:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        c.execute("SELECT role FROM users WHERE username = %s", (session["username"],))
        user = c.fetchone()
        conn.close()
        if user:
            session["role"] = user['role']
            session["is_admin"] = (user['role'] == "admin")

def send_otp_email(receiver_email, otp):
    print(f"==========================================")
    print(f"🔒 SECURE OTP FOR {receiver_email} : {otp}")
    print(f"==========================================")
    sender = os.environ.get("EMAIL_USER")
    password = os.environ.get("EMAIL_PASS")
    if sender and password:
        try:
            msg = MIMEText(f"Your Secure Login OTP is: {otp}\n\nDo not share this. Expires in 5 minutes.")
            msg['Subject'] = 'Login Verification'
            msg['From'] = sender
            msg['To'] = receiver_email
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(sender, password)
                server.send_message(msg)
        except Exception as e: print("SMTP Email failed:", e)

def save_uploaded_file(file, category="Photo"):
    if not file or not file.filename: return None
    try:
        if category == "Photo" or category == "Image":
            upload_result = cloudinary.uploader.upload(file, resource_type="image", quality="auto", fetch_format="auto")
        else:
            upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        return upload_result["secure_url"]
    except Exception as e:
        print(f"Cloudinary Error: {e}")
        return None

def hijack_bot_post(media_id, interactor_username):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        c.execute("SELECT role FROM users WHERE username = %s", (interactor_username,))
        interactor = c.fetchone()
        if interactor and interactor['role'] == 'bot': return
        c.execute("SELECT uploaded_by FROM media WHERE id = %s", (media_id,))
        media = c.fetchone()
        if media:
            c.execute("SELECT role FROM users WHERE username = %s", (media['uploaded_by'],))
            uploader = c.fetchone()
            if uploader and uploader['role'] == 'bot':
                c.execute("SELECT username FROM users WHERE role = 'admin' ORDER BY id ASC LIMIT 1")
                admin_user = c.fetchone()
                if admin_user:
                    c.execute("UPDATE media SET uploaded_by = %s WHERE id = %s", (admin_user['username'], media_id))
                    conn.commit()
    except Exception as e: print("Hijack Protocol Error:", e)
    finally: conn.close()

def create_database_backup():
    try:
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        backup_data = {"timestamp": str(get_ist_time()), "users": [], "media": [], "notifications": []}
        c.execute("SELECT username, email, role, is_verified, created_at, wallet_balance FROM users")
        backup_data["users"] = [dict(row) for row in c.fetchall()]
        c.execute("SELECT id, title, category, uploaded_by, likes, views, tips_received, visibility, created_at FROM media")
        backup_data["media"] = [dict(row) for row in c.fetchall()]
        backup_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        file_path = os.path.join(backup_dir, f"backup_{datetime.date.today()}.json")
        with open(file_path, "w", encoding="utf-8") as f: json.dump(backup_data, f, default=str, indent=4)
        conn.close()
    except Exception as e: print(f"Backup Error: {e}")

def cleanup_database():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        ist_now = get_ist_time()
        c.execute("DELETE FROM messages WHERE created_at < %s - INTERVAL '12 hours'", (ist_now,))
        c.execute("DELETE FROM global_chat WHERE created_at < %s - INTERVAL '12 hours'", (ist_now,))
        c.execute("DELETE FROM users WHERE role = 'bot' AND created_at < %s - INTERVAL '12 hours'", (ist_now,))
        c.execute("DELETE FROM users WHERE username LIKE 'Guest-%%' AND last_active < %s - INTERVAL '3 days'", (ist_now,))
        c.execute("DELETE FROM users WHERE role = 'user' AND username NOT LIKE 'Guest-%%' AND last_active < %s - INTERVAL '90 days'", (ist_now,))
        c.execute("DELETE FROM users WHERE deletion_requested IS NOT NULL AND deletion_requested < %s - INTERVAL '7 days'", (ist_now,))
        c.execute("""
            DELETE FROM media 
            WHERE uploaded_by IN (SELECT username FROM users WHERE role = 'bot') 
            AND created_at < %s - INTERVAL '1 hour' 
            AND likes = 0 AND tips_received = 0 
            AND id NOT IN (SELECT DISTINCT media_id FROM comments)
        """, (ist_now,))
        c.execute("DELETE FROM media WHERE uploaded_by NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM comments WHERE username NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM likes WHERE username NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM followers WHERE follower NOT IN (SELECT username FROM users) OR following NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM messages WHERE sender NOT IN (SELECT username FROM users) OR receiver NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM blocks WHERE blocker NOT IN (SELECT username FROM users) OR blocked NOT IN (SELECT username FROM users)")
        c.execute("DELETE FROM stories WHERE created_at < %s - INTERVAL '12 hours'", (ist_now,))
        c.execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT 500)")
        conn.commit()
    except Exception as e: print("Cleanup Error:", e)
    finally: conn.close()

def run_bot_engine():
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        bot_names = ["Aria_Cyber", "Neo_Vibes", "Luna_Arts", "Zenith_Pro", "Kai_X", "Nova_King", "Echo_World", "Sage_Pixel", "Atlas_Lens", "Orion_Sky", "Lyra_Mood", "Rohan_Tech", "Kabir_Vibes", "Priya_Art"]
        bot_chats = ["Bhai yeh app sach me lit hai 🔥", "Koi online hai? Let's vibe ✨", "Just uploaded a new pic, check it out guys!", "Admin ne kya mast features banaye hain 👏", "Need some coins yaar, tip kardo koi 😂🪙", "Anyone into cyberpunk aesthetics here? 🏙️", "Can't stop scrolling this feed ngl 🚀"]
        
        c.execute("SELECT COUNT(id) as count FROM users WHERE role = 'bot'")
        if c.fetchone()['count'] < 50:
            name = f"{random.choice(bot_names)}_{random.randint(100, 9999)}"
            code = ''.join(secrets.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(10))
            c.execute("INSERT INTO users (username, nickname, password, user_code, role, is_verified, bio, wallet_balance, created_at, last_active) VALUES (%s, %s, %s, %s, 'bot', TRUE, 'Creating vibes inside the matrix ✨', 5000, %s, %s)", (name, name, "botpass123", code, get_ist_time(), get_ist_time()))
            conn.commit()
            bot_username = name
        else:
            c.execute("SELECT username FROM users WHERE role = 'bot' ORDER BY RANDOM() LIMIT 1")
            bot_username = c.fetchone()['username']
            
        action = random.randint(1, 100)
        
        if action <= 40: 
            cat = random.choice(["Photo", "Photo", "Shayari"])
            if cat == "Photo":
                if random.random() > 0.5:
                    prompt = "A hyper-realistic cinematic portrait of a cyberpunk hacker in neon lights, 8k resolution, highly detailed, Unreal Engine 5 render"
                    img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?nologo=true&seed={random.randint(1, 999999)}&width=800&height=1000"
                    caption = "Just generated this masterpiece! Kaisa laga? ✨ #AIArt #Aesthetics"
                else:
                    img_url = f"https://picsum.photos/seed/{random.randint(1, 999999)}/800/1000"
                    caption = "Current mood ✨ #chill #aesthetic"
            else:
                img_url = "SHAYARI_TEXT"
                caption = "Zindagi ek safar hai suhana... ✨ #quotes #life"
            c.execute("INSERT INTO media (filename, title, category, uploaded_by, approved, visibility, views, tips_received, created_at, allow_comments) VALUES (%s, %s, %s, %s, 1, 'public', %s, 0, %s, TRUE)", (img_url, caption, cat, bot_username, random.randint(5, 50), get_ist_time()))
        
        elif 40 < action <= 60:
            c.execute("DELETE FROM media WHERE id IN (SELECT id FROM media WHERE uploaded_by = %s AND likes = 0 AND tips_received = 0 ORDER BY RANDOM() LIMIT 1)", (bot_username,))
                      
        elif 60 < action <= 70:
            c.execute("INSERT INTO global_chat (sender, message, created_at) VALUES (%s, %s, %s)", (bot_username, random.choice(bot_chats), get_ist_time()))
        elif 70 < action <= 80:
            c.execute("SELECT username FROM users WHERE role != 'bot' AND username != %s ORDER BY RANDOM() LIMIT 1", (bot_username,))
            target = c.fetchone()
            if target:
                try: c.execute("INSERT INTO followers (follower, following, created_at) VALUES (%s, %s, %s)", (bot_username, target['username'], get_ist_time()))
                except: pass
        elif action > 80: 
            c.execute("SELECT id, uploaded_by, title, category FROM media WHERE uploaded_by != %s ORDER BY RANDOM() LIMIT 1", (bot_username,))
            media = c.fetchone()
            if media:
                if random.random() > 0.5: 
                    c.execute("UPDATE users SET wallet_balance = wallet_balance - 10 WHERE username = %s AND wallet_balance >= 10", (bot_username,))
                    if c.rowcount == 1:
                        c.execute("UPDATE users SET wallet_balance = wallet_balance + 10 WHERE username = %s", (media['uploaded_by'],))
                        c.execute("UPDATE media SET tips_received = COALESCE(tips_received, 0) + 10 WHERE id = %s", (media['id'],))
                        c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (media['uploaded_by'], f"💰 You received 10 Coins from {bot_username} for '{media['title'][:15]}...'", f"/post/{media['id']}", get_ist_time()))
                else: 
                    try:
                        c.execute("INSERT INTO likes (media_id, username) VALUES (%s, %s) ON CONFLICT DO NOTHING", (media['id'], bot_username))
                        if c.rowcount == 1: 
                            c.execute("UPDATE media SET likes = likes + 1 WHERE id = %s", (media['id'],))
                            c.execute("INSERT INTO notifications (username, message, link, created_at) VALUES (%s, %s, %s, %s)", (media['uploaded_by'], f"❤️ {bot_username} liked your post!", f"/post/{media['id']}", get_ist_time()))
                    except: pass
        conn.commit()
    except Exception as e: print("Bot Engine Error:", e)
    finally: conn.close()
