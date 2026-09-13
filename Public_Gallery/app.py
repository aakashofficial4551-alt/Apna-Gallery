import os
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "socho_kya_hoga_secret_key"
app.config['UPLOAD_FOLDER'] = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm'}

# Yahan apna secret password dalein jisse sirf aap delete kar sakein
ADMIN_PASSCODE = "12345" 

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# Create database table
def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        title TEXT,
                        category TEXT,
                        prompt TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'media' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(request.url)
        
        file = request.files['media']
        title = request.form.get('title', 'Untitled Project')
        category = request.form.get('category', 'Cinematic')
        prompt = request.form.get('prompt', '')

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            conn = get_db_connection()
            conn.execute('INSERT INTO media (filename, title, category, prompt) VALUES (?, ?, ?, ?)',
                         (filename, title, category, prompt))
            conn.commit()
            conn.close()
            
            flash('Visual added to the Vault!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid format! Use images or videos.', 'error')

    conn = get_db_connection()
    media_files = conn.execute('SELECT * FROM media ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template('index.html', media_files=media_files)

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
            conn.commit()
            flash('Item deleted securely.', 'success')
        conn.close()
    else:
        flash('Access Denied: Wrong Passcode', 'error')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
