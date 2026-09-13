import os
from flask import Flask, request, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename

app = Flask(__name__)
# Security key for flash messages
app.secret_key = "socho_kya_hoga_secret_key" 
app.config['UPLOAD_FOLDER'] = 'static/uploads'
# Sirf images aur videos allow karne ke liye
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'media' not in request.files:
            flash('No file part', 'error')
            return redirect(request.url)
        file = request.files['media']
        if file.filename == '':
            flash('No selected file', 'error')
            return redirect(request.url)
        if file and allowed_file(file.filename):
            # secure_filename safe naam banata hai (e.g., spaces hatata hai)
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            flash('Media Uploaded Successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid file format. Only images and videos are allowed.', 'error')
    
    # Files ko get karna aur naye files ko sabse upar dikhana (sorting)
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    files_with_paths = [os.path.join(app.config['UPLOAD_FOLDER'], f) for f in files]
    files_with_paths.sort(key=os.path.getmtime, reverse=True)
    sorted_files = [os.path.basename(f) for f in files_with_paths]
    
    return render_template('index.html', media_files=sorted_files)

if __name__ == '__main__':
    app.run(debug=True)
