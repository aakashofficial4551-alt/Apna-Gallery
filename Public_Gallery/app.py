import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

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
    # Users Table for optional accounts
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE, password TEXT)''')
    # Media Table
    conn.execute('''CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL, title TEXT, category TEXT, 
                        prompt TEXT, uploaded_by TEXT, approved INTEGER DEFAULT 0,
                        likes INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, media_id INTEGER, 
                        user_name TEXT, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- ROUTES ---

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
                flash('Account Created! You can now login.', 'success')
            except sqlite3.IntegrityError:
                flash('Username already exists!', 'error')
        elif action == 'login':
            user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
            if user:
                session['username'] = user['username']
                session['is_registered'] = True
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid Credentials!', 'error')
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

@app.route('/profile')
def profile():
    if 'username' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    # Fetch all uploads by this user, approved or not
    my_uploads = conn.execute('SELECT * FROM media WHERE uploaded_by = ? ORDER BY created_at DESC', (session['username'],)).fetchall()
    conn.close()
    return render_template('profile.html', my_uploads=my_uploads)

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
                flash('Invalid format!', 'error')
                return redirect(url_for('gallery', category=category))

        is_approved = 1 if session.get('is_admin') else 0
        conn = get_db_connection()
        conn.execute('INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (?, ?, ?, ?, ?, ?)',
                     (filename, request.form.get('title'), category, request.form.get('prompt', ''), session['username'], is_approved))
        conn.commit()
        conn.close()
        flash('Sent for Admin approval. You can view it in your Profile.', 'success')
        return redirect(url_for('gallery', category=category))

    conn = get_db_connection()
    media_files = conn.execute('SELECT * FROM media WHERE category = ? AND approved = 1 ORDER BY created_at DESC', (category,)).fetchall()
    conn.close()
    return render_template('gallery.html', media_files=media_files, category=category)

# Admin, Delete, Mystery routes remain the same as previous...
# (Paste the admin, delete, and mystery routes from the previous code here)

if __name__ == '__main__':
    app.run(debug=True)
