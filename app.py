from flask import Flask, render_template, request, jsonify, send_file, abort
import os
import tempfile
import shutil
import subprocess
import pathlib
import uuid
import threading
import time
from urllib.parse import urlparse
import re

# Import your existing script functions
from flatten_repo import (
    git_clone, git_head_commit, collect_files, build_html,
    derive_temp_output_path, MAX_DEFAULT_BYTES
)

app = Flask(__name__)

# Store processing status
processing_status = {}

def is_valid_github_url(url):
    """Validate if the URL is a valid GitHub repository URL."""
    if not url:
        return False
    
    # Basic URL validation
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return False
    except:
        return False
    
    # GitHub URL patterns
    github_patterns = [
        r'^https://github\.com/[\w\-\.]+/[\w\-\.]+/?$',
        r'^https://github\.com/[\w\-\.]+/[\w\-\.]+\.git/?$',
        r'^git@github\.com:[\w\-\.]+/[\w\-\.]+\.git$'
    ]
    
    return any(re.match(pattern, url.strip()) for pattern in github_patterns)

def process_repo(task_id, repo_url, max_bytes):
    """Process repository in background thread."""
    processing_status[task_id] = {
        'status': 'cloning',
        'message': 'Cloning repository...',
        'progress': 10
    }
    
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="flatten_repo_web_")
        repo_dir = pathlib.Path(tmpdir, "repo")
        
        # Clone repository
        git_clone(repo_url, str(repo_dir))
        
        processing_status[task_id] = {
            'status': 'scanning',
            'message': 'Scanning files...',
            'progress': 40
        }
        
        # Get commit info and scan files
        head = git_head_commit(str(repo_dir))
        infos = collect_files(repo_dir, max_bytes)
        
        processing_status[task_id] = {
            'status': 'generating',
            'message': 'Generating HTML...',
            'progress': 70
        }
        
        # Generate HTML
        html_out = build_html(repo_url, repo_dir, head, infos)
        
        # Save to output directory
        output_dir = pathlib.Path("output")
        output_dir.mkdir(exist_ok=True)
        output_file = output_dir / f"{task_id}.html"
        output_file.write_text(html_out, encoding="utf-8")
        
        processing_status[task_id] = {
            'status': 'complete',
            'message': 'HTML generated successfully!',
            'progress': 100,
            'file_path': str(output_file),
            'file_size': output_file.stat().st_size
        }
        
    except subprocess.CalledProcessError as e:
        error_msg = "Repository not found or access denied"
        if "Authentication failed" in str(e):
            error_msg = "Private repository - authentication required"
        elif "not found" in str(e).lower():
            error_msg = "Repository not found"
        
        processing_status[task_id] = {
            'status': 'error',
            'message': error_msg,
            'progress': 0
        }
    except Exception as e:
        processing_status[task_id] = {
            'status': 'error',
            'message': f'Error: {str(e)}',
            'progress': 0
        }
    finally:
        # Clean up temporary directory
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process():
    data = request.get_json()
    repo_url = data.get('repo_url', '').strip()
    max_bytes = data.get('max_bytes', MAX_DEFAULT_BYTES)
    
    if not repo_url:
        return jsonify({'error': 'Repository URL is required'}), 400
    
    if not is_valid_github_url(repo_url):
        return jsonify({'error': 'Please enter a valid GitHub repository URL'}), 400
    
    # Generate unique task ID
    task_id = str(uuid.uuid4())
    
    # Start background processing
    thread = threading.Thread(target=process_repo, args=(task_id, repo_url, max_bytes))
    thread.daemon = True
    thread.start()
    
    return jsonify({'task_id': task_id})

@app.route('/status/<task_id>')
def status(task_id):
    if task_id not in processing_status:
        return jsonify({'error': 'Task not found'}), 404
    
    return jsonify(processing_status[task_id])

@app.route('/download/<task_id>')
def download(task_id):
    if task_id not in processing_status:
        abort(404)
    
    status_info = processing_status[task_id]
    if status_info['status'] != 'complete':
        abort(404)
    
    file_path = status_info['file_path']
    if not os.path.exists(file_path):
        abort(404)
    
    # Extract repo name for filename
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Try to extract repo name from HTML title
            import re
            match = re.search(r'<title>Flattened repo – (.+?)</title>', content)
            if match:
                repo_url = match.group(1)
                repo_name = repo_url.split('/')[-1]
                if repo_name.endswith('.git'):
                    repo_name = repo_name[:-4]
                filename = f"{repo_name}_flattened.html"
            else:
                filename = "repo_flattened.html"
    except:
        filename = "repo_flattened.html"
    
    return send_file(file_path, as_attachment=True, download_name=filename)

@app.route('/view/<task_id>')
def view(task_id):
    if task_id not in processing_status:
        abort(404)
    
    status_info = processing_status[task_id]
    if status_info['status'] != 'complete':
        abort(404)
    
    file_path = status_info['file_path']
    if not os.path.exists(file_path):
        abort(404)
    
    return send_file(file_path)

# Clean up old files periodically
def cleanup_old_files():
    """Remove files older than 24 hours."""
    output_dir = pathlib.Path("output")
    if not output_dir.exists():
        return
    
    cutoff_time = time.time() - (24 * 60 * 60)  # 24 hours ago
    
    for file_path in output_dir.glob("*.html"):
        try:
            if file_path.stat().st_mtime < cutoff_time:
                file_path.unlink()
                # Also remove from processing_status
                task_id = file_path.stem
                if task_id in processing_status:
                    del processing_status[task_id]
        except:
            pass

# Run cleanup every hour
def start_cleanup_thread():
    def cleanup_loop():
        while True:
            time.sleep(3600)  # 1 hour
            cleanup_old_files()
    
    cleanup_thread = threading.Thread(target=cleanup_loop)
    cleanup_thread.daemon = True
    cleanup_thread.start()

if __name__ == '__main__':
    # Create output directory
    pathlib.Path("output").mkdir(exist_ok=True)
    
    # Start cleanup thread
    start_cleanup_thread()
    
    # Get port from environment (Render requirement)
    port = int(os.environ.get('PORT', 5000))
    
    # Run the app
    app.run(debug=False, host='0.0.0.0', port=port)
