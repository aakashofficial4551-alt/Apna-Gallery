import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "socho_kya_hoga_secret_key_123"
app.config['UPLOAD_FOLDER'] = 'static/uploads'

# Added Audio and Documents extensions
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm', 'mp3', 'wav', 'pdf', 'txt', 'docx'}
ADMIN_PASSCODE = "12345"

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Media Table
    conn.execute('''CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        title TEXT,
                        category TEXT,
                        prompt TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
    # Comments Table
    conn.execute('''CREATE TABLE IF NOT EXISTS comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        media_id INTEGER,
                        user_name TEXT,
                        comment TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/login', methods=['POST'])
def login():
    session['username'] = request.form.get('username')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST' and 'media' in request.files:
        file = request.files['media']
        title = request.form.get('title', 'Untitled')
        category = request.form.get('category', 'Photo')
        prompt = request.form.get('prompt', '')

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            conn = get_db_connection()
            conn.execute('INSERT INTO media (filename, title, category, prompt) VALUES (?, ?, ?, ?)',
                         (filename, title, category, prompt))
            conn.commit()
            conn.close()
            flash('File added successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid format!', 'error')

    conn = get_db_connection()
    media_files = conn.execute('SELECT * FROM media ORDER BY created_at DESC').fetchall()
    comments_db = conn.execute('SELECT * FROM comments ORDER BY created_at ASC').fetchall()
    conn.close()

    # Group comments by media_id
    comments = {}
    for c in comments_db:
        if c['media_id'] not in comments:
            comments[c['media_id']] = []
        comments[c['media_id']].append(c)

    return render_template('index.html', media_files=media_files, comments=comments)

@app.route('/comment/<int:media_id>', methods=['POST'])
def add_comment(media_id):
    if 'username' not in session:
        flash('Enter your name first!', 'error')
        return redirect(url_for('index'))
        
    comment_text = request.form.get('comment')
    if comment_text:
        conn = get_db_connection()
        conn.execute('INSERT INTO comments (media_id, user_name, comment) VALUES (?, ?, ?)',
                     (media_id, session['username'], comment_text))
        conn.commit()
        conn.close()
    return redirect(url_for('index'))

@app.route('/delete/<int:id>', methods=['POST'])
def delete(id):
    passcode = request.form.get('passcode')
    if passcode == ADMIN_PASSCODE:
        conn = get_db_connection()
        item = conn.execute('SELECT * FROM media WHERE id = ?', (id,)).fetchone()
        if item:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], item['filename']))
            except:
                pass
            conn.execute('DELETE FROM media WHERE id = ?', (id,))
            conn.execute('DELETE FROM comments WHERE media_id = ?', (id,))
            conn.commit()
        conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
