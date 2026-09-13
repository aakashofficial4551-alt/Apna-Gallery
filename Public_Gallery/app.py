import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "socho_kya_hoga_secret_key_123"
app.config['UPLOAD_FOLDER'] = 'static/uploads'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mp3', 'wav', 'pdf', 'txt', 'docx'}
ADMIN_PASSCODE = "12345"
# MYSTERY ROOM PASSWORD
MYSTERY_CODE = "SOCHO" 

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS media (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, title TEXT, category TEXT, prompt TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT, media_id INTEGER, user_name TEXT, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 1. Login Route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session['username'] = request.form.get('username')
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# 2. Main Gallery Route
@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    media_files = conn.execute('SELECT * FROM media ORDER BY created_at DESC').fetchall()
    comments_db = conn.execute('SELECT * FROM comments ORDER BY created_at ASC').fetchall()
    conn.close()

    comments = {}
    for c in comments_db:
        if c['media_id'] not in comments:
            comments[c['media_id']] = []
        comments[c['media_id']].append(c)

    return render_template('index.html', media_files=media_files, comments=comments)

# 3. Upload Route
@app.route('/upload', methods=['POST'])
def upload():
    if 'username' not in session: return redirect(url_for('login'))
    
    file = request.files['media']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        conn = get_db_connection()
        conn.execute('INSERT INTO media (filename, title, category) VALUES (?, ?, ?)',
                     (filename, request.form.get('title'), request.form.get('category')))
        conn.commit()
        conn.close()
        flash('File added successfully!', 'success')
    else:
        flash('Invalid format!', 'error')
    return redirect(url_for('index'))

# 4. Comments Route
@app.route('/comment/<int:media_id>', methods=['POST'])
def add_comment(media_id):
    if 'username' in session:
        conn = get_db_connection()
        conn.execute('INSERT INTO comments (media_id, user_name, comment) VALUES (?, ?, ?)',
                     (media_id, session['username'], request.form.get('comment')))
        conn.commit()
        conn.close()
    return redirect(url_for('index'))

# 5. Mystery Room Route
@app.route('/mystery', methods=['POST'])
def mystery():
    if request.form.get('passcode') == MYSTERY_CODE:
        return render_template('mystery.html')
    else:
        flash('ACCESS DENIED: Incorrect Mystery Code', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
