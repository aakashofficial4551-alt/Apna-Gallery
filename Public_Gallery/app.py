import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "socho_kya_hoga_secret_key_123"
app.config['UPLOAD_FOLDER'] = 'static/uploads'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mp3', 'wav', 'pdf', 'txt', 'docx'}
ADMIN_PASSCODE = "12345" # Admin banne ka password
MYSTERY_CODE = "SOCHO"

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Naya Database Schema (approved column add kiya gaya hai)
    conn.execute('''CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL, title TEXT, category TEXT, 
                        prompt TEXT, uploaded_by TEXT, approved INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, media_id INTEGER, 
                        user_name TEXT, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# PAGE 1: Login & Welcome
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session['username'] = request.form.get('username')
        session['age'] = request.form.get('age')
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# PAGE 2: Dashboard (Boxes)
@app.route('/')
@app.route('/dashboard')
def dashboard():
    if 'username' not in session: return redirect(url_for('login'))
    return render_template('dashboard.html')

# PAGE 3: Specific Gallery & Upload
@app.route('/gallery/<category>', methods=['GET', 'POST'])
def gallery(category):
    if 'username' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        file = request.files['media']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            # Agar admin hai toh direct approve (1), warna pending (0)
            is_approved = 1 if session.get('is_admin') else 0
            
            conn = get_db_connection()
            conn.execute('INSERT INTO media (filename, title, category, prompt, uploaded_by, approved) VALUES (?, ?, ?, ?, ?, ?)',
                         (filename, request.form.get('title'), category, request.form.get('prompt', ''), session['username'], is_approved))
            conn.commit()
            conn.close()
            
            if is_approved:
                flash('File uploaded successfully!', 'success')
            else:
                flash('File uploaded! It will be visible after Admin approval.', 'success')
            return redirect(url_for('gallery', category=category))
        else:
            flash('Invalid format!', 'error')

    conn = get_db_connection()
    # Sirf approved media dikhao
    media_files = conn.execute('SELECT * FROM media WHERE category = ? AND approved = 1 ORDER BY created_at DESC', (category,)).fetchall()
    comments_db = conn.execute('SELECT * FROM comments ORDER BY created_at ASC').fetchall()
    conn.close()

    comments = {}
    for c in comments_db:
        if c['media_id'] not in comments: comments[c['media_id']] = []
        comments[c['media_id']].append(c)

    return render_template('gallery.html', media_files=media_files, category=category, comments=comments)

# ADMIN PANEL
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('passcode') == ADMIN_PASSCODE:
            session['is_admin'] = True
            flash('Admin Access Granted.', 'success')
        else:
            flash('Invalid Passcode', 'error')
            
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
        flash('Media Approved!', 'success')
    return redirect(url_for('admin'))

@app.route('/delete/<int:id>')
def delete(id):
    if session.get('is_admin'):
        conn = get_db_connection()
        item = conn.execute('SELECT * FROM media WHERE id = ?', (id,)).fetchone()
        if item:
            try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], item['filename']))
            except: pass
            conn.execute('DELETE FROM media WHERE id = ?', (id,))
            conn.execute('DELETE FROM comments WHERE media_id = ?', (id,))
            conn.commit()
        conn.close()
        flash('Item deleted.', 'success')
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
    flash('ACCESS DENIED', 'error')
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
