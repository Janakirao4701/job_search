import os
import json
import logging
from flask import Flask, request, jsonify, render_template, send_from_directory
import pandas as pd
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates', static_folder='templates')

PORT = 5000
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_PATH = os.path.join(DATA_DIR, 'tracker.json')

def load_tracker():
    if os.path.exists(TRACKER_PATH):
        try:
            with open(TRACKER_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading tracker: {e}")
            return {}
    return {}

def save_tracker(tracker_data):
    try:
        with open(TRACKER_PATH, 'w', encoding='utf-8') as f:
            json.dump(tracker_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving tracker: {e}")
        return False

def discover_clients():
    """Scan the parent directory for client JSON profiles."""
    clients = {}
    parent_dir = os.path.dirname(DATA_DIR)
    
    try:
        for file in os.listdir(parent_dir):
            if file.endswith('.json') and file != 'tracker.json' and file != 'package.json':
                full_path = os.path.join(parent_dir, file)
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        # Expecting format: { "client_id": { "profile": {...}, "text": "..." } }
                        for client_key, client_data in data.items():
                            if isinstance(client_data, dict) and 'profile' in client_data:
                                clients[client_key] = {
                                    'profile': client_data.get('profile', {}),
                                    'text': client_data.get('text', ''),
                                    'source_file': file
                                }
                except Exception as e:
                    logger.error(f"Error parsing profile file {file}: {e}")
    except Exception as e:
        logger.error(f"Error scanning for profiles: {e}")
    
    # Fallback to look inside the active directory if none found in parent
    if not clients:
        try:
            for file in os.listdir(DATA_DIR):
                if file.endswith('.json') and file != 'tracker.json':
                    full_path = os.path.join(DATA_DIR, file)
                    with open(full_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        for client_key, client_data in data.items():
                            if isinstance(client_data, dict) and 'profile' in client_data:
                                clients[client_key] = {
                                    'profile': client_data.get('profile', {}),
                                    'text': client_data.get('text', ''),
                                    'source_file': file
                                }
        except Exception as e:
            logger.error(f"Error scanning local dir for profiles: {e}")
            
    return clients

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/clients', methods=['GET'])
def get_clients():
    clients = discover_clients()
    return jsonify(clients)

@app.route('/api/tracker', methods=['GET', 'POST'])
def tracker_api():
    tracker = load_tracker()
    
    if request.method == 'POST':
        data = request.json
        job_url = data.get('job_url')
        client_id = data.get('client_id')
        client_name = data.get('client_name')
        status = data.get('status', 'Not Applied')
        job_title = data.get('job_title')
        company = data.get('company')
        
        if not job_url:
            return jsonify({'error': 'Missing job_url'}), 400
            
        tracker[job_url] = {
            'client_id': client_id,
            'client_name': client_name,
            'status': status,
            'job_title': job_title,
            'company': company,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        if save_tracker(tracker):
            return jsonify({'success': True, 'tracker': tracker})
        else:
            return jsonify({'error': 'Failed to save status'}), 500
            
    return jsonify(tracker)

@app.route('/api/search', methods=['POST'])
def search_jobs():
    data = request.json or {}
    search_term = data.get('search_term', 'Test Engineer')
    location = data.get('location', '')
    site_names = data.get('site_names', ['indeed', 'linkedin', 'zip_recruiter', 'google'])
    results_wanted = int(data.get('results_wanted', 20))
    hours_old = int(data.get('hours_old', 72))
    
    logger.info(f"Starting job scrape for term='{search_term}' loc='{location}' sites={site_names}")
    
    try:
        from jobspy import scrape_jobs
        
        # Scrape jobs using python-jobspy
        jobs = scrape_jobs(
            site_name=site_names,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
            country_indeed="USA"
        )
        
        if jobs is None or jobs.empty:
            return jsonify([])
            
        # Clean data for JSON serialization (replace NaN/NaT)
        jobs = jobs.fillna("")
        
        # Format dates nicely
        if 'date_posted' in jobs.columns:
            # If date_posted is timestamp or datetime, convert to string
            jobs['date_posted'] = jobs['date_posted'].apply(
                lambda x: x.strftime('%Y-%m-%d') if hasattr(x, 'strftime') else str(x)
            )
            
        jobs_list = jobs.to_dict(orient='records')
        return jsonify(jobs_list)
        
    except Exception as e:
        logger.error(f"Error during job search: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print(f"Starting JobStaffer Dashboard on http://localhost:{PORT}")
    app.run(debug=True, port=PORT)
