import os
from flask import Flask, request, render_template, redirect, url_for

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'

# This ensures the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'photo' in request.files:
            file = request.files['photo']
            if file.filename != '':
                # Save the uploaded file
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        return redirect(url_for('index'))
    
    # Get a list of all uploaded photos to display
    photos = os.listdir(app.config['UPLOAD_FOLDER'])
    return render_template('index.html', photos=photos)

if __name__ == '__main__':
    app.run(debug=True)