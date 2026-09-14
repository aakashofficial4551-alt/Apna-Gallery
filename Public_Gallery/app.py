import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
from werkzeug.utils import secure_filename
from ai_service import get_ai_response

app = Flask(__name__)
app.secret_key = "socho_kya_hoga_secret_key_123"
app.config['UPLOAD_FOLDER'] = 'static/uploads'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mp3', 'wav', 'pdf', 'txt', 'docx'}

# SECRET ADMIN PASSCODE
ADMIN_PASSCODE = "800123"
MYSTERY_CODE = "SOCHO"

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('database.db')
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
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username')
        password = request.form.get('password', '')
        
        if action == 'guest':
            session['username'] = username + " (Guest)"
            return redirect(url_for('dashboard'))
            
        conn = get_db_connection()
        if action == 'register':
            try:
                conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
                conn.commit()
                flash('Account Created! Please Login.', 'success')
            except sqlite3.IntegrityError:
                flash('Username already exists!', 'error')
        elif action == 'login':
            user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
            if user:
                session['username'] = user['username']
                session['is_registered'] = True
                return redirect(url_for('dashboard'))
            else: flash('Invalid Credentials!', 'error')
        conn.close()
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@app.route('/dashboard')
def dashboard():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/games')
def games():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template('games.html')

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'username' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    if request.method == 'POST':
        if 'bio' in request.form:
            conn.execute('UPDATE users SET bio = ?, country = ? WHERE username = ?',
                         (request.form['bio'], request.form['country'], session['username']))
            conn.commit()
        elif 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute('UPDATE users SET profile_pic = ? WHERE username = ?', (filename, session['username']))
                conn.commit()
        elif 'story' in request.files:
            file = request.files['story']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute('INSERT INTO stories (username, filename) VALUES (?, ?)', (session['username'], filename))
                conn.commit()

    user = conn.execute('SELECT * FROM users WHERE username = ?', (session['username'],)).fetchone()
    my_uploads = conn.execute('SELECT * FROM media WHERE uploaded_by = ? ORDER BY id DESC', (session['username'],)).fetchall()
    stories = conn.execute('SELECT * FROM stories WHERE username = ? ORDER BY id DESC', (session['username'],)).fetchall()
    post_count = len(my_uploads)
    conn.close()
    return render_template('profile.html', user=user, my_uploads=my_uploads, post_count=post_count, stories=stories)

@app.route('/gallery/<category>', methods=['GET', 'POST'])
def gallery(category):
    if 'username' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        file = request.files.get('media')
        filename = "SHAYARI_TEXT"
        if file and file.filename != '':
            if allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            else:
                flash('Invalid file format!', 'error')
                return redirect(url_for('gallery', category=category))
        
        # Admin upload karega toh direct approve, baaki logo ka pending (0)
        is_approved = 1 if session.get('is_admin') else 0
        
        conn = get_db_connection()
        conn.execute('INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (?, ?, ?, ?, ?, ?)',
                     (filename, request.form.get('title', 'Untitled'), category, request.form.get('prompt', ''), session.get('username', 'Anonymous'), is_approved))
        conn.commit()
        conn.close()
        
        if is_approved:
            flash('File uploaded directly by Admin!', 'success')
        else:
            flash('Uploaded! Sent to Admin for approval.', 'success')
        return redirect(url_for('gallery', category=category))

    conn = get_db_connection()
    # Sirf approved media gallery me dikhegi
    media_files = conn.execute('SELECT * FROM media WHERE category = ? AND approved = 1 ORDER BY id DESC', (category,)).fetchall()
    comments_db = conn.execute('SELECT * FROM comments ORDER BY id ASC').fetchall()
    conn.close()

    comments = {}
    for c in comments_db:
        if c['media_id'] not in comments: comments[c['media_id']] = []
        comments[c['media_id']].append(c)

    return render_template('gallery.html', media_files=media_files, category=category, comments=comments)

# AI Chat API
@app.route('/api/ai', methods=['POST'])
def ai_endpoint():
    data = request.get_json()
    user_query = data.get('query', '')
    bot_reply = get_ai_response(user_query)
    return jsonify({'reply': bot_reply})

# Like
@app.route('/like/<int:media_id>', methods=['POST'])
def like(media_id):
    conn = get_db_connection()
    conn.execute('UPDATE media SET likes = likes + 1 WHERE id = ?', (media_id,))
    conn.commit()
    likes = conn.execute('SELECT likes FROM media WHERE id = ?', (media_id,)).fetchone()['likes']
    conn.close()
    return jsonify({'likes': likes})

# ADMIN DASHBOARD
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        entered_pass = request.form.get('passcode')
        if entered_pass == ADMIN_PASSCODE:
            session['is_admin'] = True
            flash('Admin Access Granted!', 'success')
        else: 
            flash('Access Denied: Incorrect Passcode!', 'error')
    
    if not session.get('is_admin'): 
        return render_template('admin.html', auth_required=True)
    
    conn = get_db_connection()
    # Saare unapproved items layega
    pending_media = conn.execute('SELECT * FROM media WHERE approved = 0 ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('admin.html', pending_media=pending_media, auth_required=False)

@app.route('/approve/<int:id>')
def approve(id):
    if session.get('is_admin'):
        conn = get_db_connection()
        conn.execute('UPDATE media SET approved = 1 WHERE id = ?', (id,))
        conn.commit()
        conn.close()
        flash('Item successfully approved and published!', 'success')
    return redirect(url_for('admin'))

@app.route('/delete/<int:id>')
def delete(id):
    if session.get('is_admin'):
        conn = get_db_connection()
        item = conn.execute('SELECT * FROM media WHERE id = ?', (id,)).fetchone()
        if item:
            if item['filename'] != 'SHAYARI_TEXT':
                try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], item['filename']))
                except: pass
            conn.execute('DELETE FROM media WHERE id = ?', (id,))
            conn.commit()
        conn.close()
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/mystery', methods=['POST'])
def mystery():
    if request.form.get('passcode') == MYSTERY_CODE:
        return render_template('mystery.html')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
