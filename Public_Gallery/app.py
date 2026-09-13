import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
from werkzeug.utils import secure_filename
from ai_service import get_ai_response

app = Flask(__name__)
app.secret_key = "socho_kya_hoga_secret_key_123"
app.config['UPLOAD_FOLDER'] = 'static/uploads'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mp3', 'wav', 'pdf', 'txt', 'docx'}
ADMIN_PASSCODE = "12345"
MYSTERY_CODE = "SOCHO"

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Added profile_pic column
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
        # Update Bio & Country
        if 'bio' in request.form:
            conn.execute('UPDATE users SET bio = ?, country = ? WHERE username = ?',
                         (request.form['bio'], request.form['country'], session['username']))
            conn.commit()
        # Upload Profile Picture
        elif 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute('UPDATE users SET profile_pic = ? WHERE username = ?', (filename, session['username']))
                conn.commit()
        # Upload Story
        elif 'story' in request.files:
            file = request.files['story']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                conn.execute('INSERT INTO stories (username, filename) VALUES (?, ?)', (session['username'], filename))
                conn.commit()

    user = conn.execute('SELECT * FROM users WHERE username = ?', (session['username'],)).fetchone()
    my_uploads = conn.execute('SELECT * FROM media WHERE uploaded_by = ? ORDER BY created_at DESC', (session['username'],)).fetchall()
    stories = conn.execute('SELECT * FROM stories WHERE username = ? ORDER BY created_at DESC', (session['username'],)).fetchall()
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
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        is_approved = 1 if session.get('is_admin') else 0
        conn = get_db_connection()
        conn.execute('INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (?, ?, ?, ?, ?, ?)',
                     (filename, request.form.get('title'), category, request.form.get('prompt', ''), session['username'], is_approved))
        conn.commit()
        conn.close()
        flash('Submitted for Admin approval.', 'success')
        return redirect(url_for('gallery', category=category))

    conn = get_db_connection()
    media_files = conn.execute('SELECT * FROM media WHERE category = ? AND approved = 1 ORDER BY created_at DESC', (category,)).fetchall()
    comments_db = conn.execute('SELECT * FROM comments ORDER BY created_at ASC').fetchall()
    conn.close()

    comments = {}
    for c in comments_db:
        if c['media_id'] not in comments: comments[c['media_id']] = []
        comments[c['media_id']].append(c)

    return render_template('gallery.html', media_files=media_files, category=category, comments=comments)

# AI Chat API Endpoint
@app.route('/api/ai', methods=['POST'])
def ai_endpoint():
    data = request.get_json()
    user_query = data.get('query', '')
    bot_reply = get_ai_response(user_query)
    return jsonify({'reply': bot_reply})

# --- ADMIN ROUTES ---
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('passcode') == ADMIN_PASSCODE:
            session['is_admin'] = True
        else: flash('Invalid Admin Passcode!', 'error')
    
    if not session.get('is_admin'): 
        return render_template('admin.html', auth_required=True)
    
    conn = get_db_connection()
    pending_media = conn.execute('SELECT * FROM media WHERE approved = 0 ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template('admin.html', pending_media=pending_media, auth_required=False)

@app.route('/approve/<int:id>')
def approve(id):
    if session.get('is_admin'):
        conn = get_db_connection()
        conn.execute('UPDATE media SET approved = 1 WHERE id = ?', (id,))
        conn.commit()
        conn.close()
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

@app.route('/comment/<int:media_id>', methods=['POST'])
def add_comment(media_id):
    if 'username' in session:
        conn = get_db_connection()
        conn.execute('INSERT INTO comments (media_id, user_name, comment) VALUES (?, ?, ?)',
                     (media_id, session['username'], request.form.get('comment')))
        conn.commit()
        conn.close()
    return redirect(request.referrer)

@app.route('/mystery', methods=['POST'])
def mystery():
    if request.form.get('passcode') == MYSTERY_CODE:
        return render_template('mystery.html')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
