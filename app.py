import os
import re
import json
import logging
import requests
import time
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, render_template, send_from_directory
import pandas as pd

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='.', static_folder='.')

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

COMPANIES_PATH = os.path.join(DATA_DIR, 'companies.json')

def load_companies():
    if os.path.exists(COMPANIES_PATH):
        try:
            with open(COMPANIES_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading companies: {e}")
    return {}

def normalize_string(s):
    if not s:
        return ""
    return re.sub(r'[^a-z0-9]', '', s.lower().strip())

def parse_date(date_str):
    if not date_str:
        return None
    date_str = re.sub(r'\.\d+Z$', 'Z', date_str)
    date_str = re.sub(r'\.\d+\+\d+:\d+$', '', date_str)
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

def parse_relative_date(rel_str):
    if not rel_str:
        return None
    rel_lower = rel_str.lower().strip()
    now = datetime.now(timezone.utc)
    
    if 'today' in rel_lower or 'just posted' in rel_lower or 'now' in rel_lower or 'hour' in rel_lower:
        return now
    if 'yesterday' in rel_lower:
        return now - timedelta(days=1)
    
    m = re.search(r'(\d+)\s+day', rel_lower)
    if m:
        return now - timedelta(days=int(m.group(1)))
    
    m = re.search(r'(\d+)\s+week', rel_lower)
    if m:
        return now - timedelta(days=int(m.group(1)) * 7)
        
    m = re.search(r'(\d+)\s+month', rel_lower)
    if m:
        return now - timedelta(days=int(m.group(1)) * 30)

    if '30+' in rel_lower or 'older' in rel_lower:
        return now - timedelta(days=35)
        
    return None

def scrape_greenhouse_company(company, keywords, timeout=8):
    jobs = []
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        res = requests.get(url, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            for job in data.get('jobs', []):
                title = job.get('title', '')
                if any(kw.lower() in title.lower() for kw in keywords.split()):
                    loc = job.get('location', {}).get('name', 'N/A')
                    apply_url = job.get('absolute_url', '')
                    updated_at = job.get('updated_at', '')
                    jobs.append({
                        'title': title,
                        'company': company.capitalize(),
                        'location': loc,
                        'date_posted': updated_at,
                        'source': f"Greenhouse — {company.capitalize()}",
                        'source_type': 'greenhouse',
                        'apply_url': apply_url,
                        'date_unknown': False if updated_at else True
                    })
    except Exception as e:
        logger.error(f"Error scraping Greenhouse for {company}: {e}")
    return jobs

def scrape_lever_company(company, keywords, timeout=8):
    jobs = []
    try:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        res = requests.get(url, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            for job in data:
                title = job.get('text', '')
                if any(kw.lower() in title.lower() for kw in keywords.split()):
                    loc = job.get('categories', {}).get('location', 'N/A')
                    apply_url = job.get('hostedUrl', '')
                    created_at = job.get('createdAt')
                    date_posted = ""
                    if created_at:
                        date_posted = datetime.fromtimestamp(created_at / 1000.0, timezone.utc).isoformat()
                    jobs.append({
                        'title': title,
                        'company': company.capitalize(),
                        'location': loc,
                        'date_posted': date_posted,
                        'source': f"Lever — {company.capitalize()}",
                        'source_type': 'lever',
                        'apply_url': apply_url,
                        'date_unknown': False if date_posted else True
                    })
    except Exception as e:
        logger.error(f"Error scraping Lever for {company}: {e}")
    return jobs

def scrape_workable_company(company, keywords, timeout=8):
    jobs = []
    try:
        url = f"https://apply.workable.com/api/v1/widget/accounts/{company}"
        res = requests.get(url, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            for job in data.get('jobs', []):
                title = job.get('title', '')
                if any(kw.lower() in title.lower() for kw in keywords.split()):
                    loc = job.get('location', {}).get('city', 'N/A')
                    shortcode = job.get('shortcode', '')
                    apply_url = f"https://apply.workable.com/{company}/j/{shortcode}/"
                    published = job.get('published', '')
                    date_posted = ""
                    date_unknown = True
                    if published:
                        parsed_d = parse_date(published)
                        if parsed_d:
                            date_posted = parsed_d.isoformat()
                            date_unknown = False
                        else:
                            date_posted = published
                    jobs.append({
                        'title': title,
                        'company': company.capitalize(),
                        'location': loc,
                        'date_posted': date_posted,
                        'source': f"Workable — {company.capitalize()}",
                        'source_type': 'workable',
                        'apply_url': apply_url,
                        'date_unknown': date_unknown
                    })
    except Exception as e:
        logger.error(f"Error scraping Workable for {company}: {e}")
    return jobs

def scrape_smartrecruiters_company(company, keywords, timeout=8):
    jobs = []
    try:
        url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        res = requests.get(url, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            for job in data.get('content', []):
                title = job.get('name', '')
                if any(kw.lower() in title.lower() for kw in keywords.split()):
                    loc_info = job.get('location', {})
                    loc = f"{loc_info.get('city', '')}, {loc_info.get('country', '')}".strip(', ')
                    job_id = job.get('id', '')
                    apply_url = f"https://jobs.smartrecruiters.com/{company}/{job_id}"
                    released_date = job.get('releasedDate', '')
                    jobs.append({
                        'title': title,
                        'company': company.capitalize(),
                        'location': loc or 'N/A',
                        'date_posted': released_date,
                        'source': f"SmartRecruiters — {company.capitalize()}",
                        'source_type': 'smartrecruiters',
                        'apply_url': apply_url,
                        'date_unknown': False if released_date else True
                    })
    except Exception as e:
        logger.error(f"Error scraping SmartRecruiters for {company}: {e}")
    return jobs

def scrape_workday_company(company_name, base_url, keywords, location_filter="", timeout=8):
    jobs = []
    try:
        parsed = urlparse(base_url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        parts = [p for p in parsed.path.split('/') if p]
        portal = parts[-1] if parts else ""
        company = parsed.netloc.split('.')[0]
        
        api_url = f"{domain}/wday/cxs/{company}/{portal}/jobs"
        payload = {
            "appliedFacets": {},
            "limit": 30,
            "offset": 0,
            "searchText": keywords
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            for posting in data.get('jobPostings', []):
                title = posting.get('title', '')
                loc = posting.get('locationsText', 'N/A')
                
                if location_filter and location_filter.lower() not in loc.lower():
                    continue
                    
                external_path = posting.get('externalPath', '')
                apply_url = f"{domain}{parsed.path}{external_path}"
                posted_on = posting.get('postedOn', '')
                
                date_posted = ""
                date_unknown = True
                parsed_rel = parse_relative_date(posted_on)
                if parsed_rel:
                    date_posted = parsed_rel.isoformat()
                    date_unknown = False
                    
                jobs.append({
                    'title': title,
                    'company': company_name,
                    'location': loc,
                    'date_posted': date_posted,
                    'source': f"Workday — {company_name}",
                    'source_type': 'workday',
                    'apply_url': apply_url,
                    'date_unknown': date_unknown
                })
    except Exception as e:
        logger.error(f"Error scraping Workday for {company_name}: {e}")
    return jobs

def scrape_jobspy(site_names, keywords, location, results_wanted, days_ago, timeout=10):
    jobs_list = []
    try:
        from jobspy import scrape_jobs
        jobs = scrape_jobs(
            site_name=site_names,
            search_term=keywords,
            location=location,
            results_wanted=results_wanted,
            hours_old=days_ago * 24,
            country_indeed="USA"
        )
        if jobs is not None and not jobs.empty:
            jobs = jobs.fillna("")
            for _, row in jobs.iterrows():
                site = row.get('site', 'Indeed')
                title = row.get('title', '')
                company = row.get('company', '')
                loc = row.get('location', '')
                date_val = row.get('date_posted', '')
                apply_url = row.get('job_url', '')
                
                date_posted = ""
                date_unknown = True
                if date_val:
                    if hasattr(date_val, 'strftime'):
                        date_posted = date_val.strftime('%Y-%m-%dT%H:%M:%SZ')
                        date_unknown = False
                    else:
                        parsed_d = parse_date(str(date_val)) or parse_relative_date(str(date_val))
                        if parsed_d:
                            date_posted = parsed_d.isoformat()
                            date_unknown = False
                        else:
                            date_posted = str(date_val)
                            
                jobs_list.append({
                    'title': title,
                    'company': company,
                    'location': loc,
                    'date_posted': date_posted,
                    'source': site,
                    'source_type': site.lower().replace(' ', '_'),
                    'apply_url': apply_url,
                    'date_unknown': date_unknown
                })
    except Exception as e:
        logger.error(f"Error scraping jobspy: {e}")
    return jobs_list

@app.route('/api/search', methods=['POST'])
def search_jobs():
    data = request.json or {}
    start_time = time.time()
    
    # Support both old and new PRD API schemas
    keywords = data.get('keywords') or data.get('search_term') or 'Test Engineer'
    location = data.get('location', '')
    sources = data.get('sources') or data.get('site_names') or ['indeed', 'linkedin']
    
    # Normalize days_ago/hours_old
    days_ago = data.get('days_ago')
    if days_ago is None:
        hours_old = data.get('hours_old')
        days_ago = int(hours_old / 24) if hours_old else 3
    else:
        days_ago = int(days_ago)
        
    results_wanted = int(data.get('results_wanted', 30))
    
    # Determine target companies
    override_companies = data.get('companies', [])
    companies_cfg = load_companies()
    
    # Determine lists of companies per portal
    greenhouse_cfg = companies_cfg.get('greenhouse', [])
    lever_cfg = companies_cfg.get('lever', [])
    smartrecruiters_cfg = companies_cfg.get('smartrecruiters', [])
    
    # Workable custom list discovery
    workable_cfg = list(companies_cfg.get('workable', []))
    for c in companies_cfg.get('custom', []):
        if c.get('ats', '').lower() == 'workable':
            parsed_url = urlparse(c.get('url', ''))
            parts = [p for p in parsed_url.path.split('/') if p]
            if parts:
                slug = parts[0]
                if slug not in workable_cfg:
                    workable_cfg.append(slug)

    # Route override companies to their respective portals
    if override_companies:
        greenhouse_list = [c for c in override_companies if c.lower() in [g.lower() for g in greenhouse_cfg]]
        lever_list = [c for c in override_companies if c.lower() in [l.lower() for l in lever_cfg]]
        smartrecruiters_list = [c for c in override_companies if c.lower() in [s.lower() for s in smartrecruiters_cfg]]
        workable_list = [c for c in override_companies if c.lower() in [w.lower() for w in workable_cfg]]
        
        # If a company doesn't match any standard list but is explicitly typed, let's keep it as fallback for Greenhouse/Lever
        all_known_slugs = set([g.lower() for g in greenhouse_cfg] + [l.lower() for l in lever_cfg] + [s.lower() for s in smartrecruiters_cfg] + [w.lower() for w in workable_cfg])
        for c in override_companies:
            if c.lower() not in all_known_slugs:
                if 'greenhouse' in sources:
                    greenhouse_list.append(c)
    else:
        greenhouse_list = greenhouse_cfg
        lever_list = lever_cfg
        smartrecruiters_list = smartrecruiters_cfg
        workable_list = workable_cfg
    
    workday_list = companies_cfg.get('workday', [])
    if 'workday' in sources and override_companies:
        workday_list = [w for w in workday_list if any(c.lower() in w['company'].lower() for c in override_companies)]
        
    logger.info(f"Search query: keywords='{keywords}' location='{location}' days_ago={days_ago} sources={sources}")
    
    all_jobs = []
    errors = []
    
    # Thread pool for parallel scraping with 10s timeout
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {}
        
        # 1. Jobspy broad boards
        jobspy_sites = [s for s in sources if s in ['indeed', 'linkedin', 'zip_recruiter', 'glassdoor']]
        if jobspy_sites:
            if override_companies:
                for company in override_companies:
                    comp_query = f"{keywords} company:\"{company}\""
                    futures[executor.submit(scrape_jobspy, jobspy_sites, comp_query, location, results_wanted, days_ago)] = f"jobspy:{company}"
            else:
                futures[executor.submit(scrape_jobspy, jobspy_sites, keywords, location, results_wanted, days_ago)] = "jobspy"
            
        # 2. Greenhouse portals
        if 'greenhouse' in sources:
            for company in greenhouse_list:
                futures[executor.submit(scrape_greenhouse_company, company, keywords)] = f"greenhouse:{company}"
                
        # 3. Lever portals
        if 'lever' in sources:
            for company in lever_list:
                futures[executor.submit(scrape_lever_company, company, keywords)] = f"lever:{company}"
                
        # 4. SmartRecruiters portals
        if 'smartrecruiters' in sources:
            for company in smartrecruiters_list:
                futures[executor.submit(scrape_smartrecruiters_company, company, keywords)] = f"smartrecruiters:{company}"
                
        # 5. Workable portals
        if 'workable' in sources:
            for company in workable_list:
                futures[executor.submit(scrape_workable_company, company, keywords)] = f"workable:{company}"
                
        # 6. Workday portals
        if 'workday' in sources:
            for w_item in workday_list:
                comp_name = w_item['company']
                base_url = w_item['url']
                futures[executor.submit(scrape_workday_company, comp_name, base_url, keywords, location)] = f"workday:{comp_name}"
                
        # Gather results with timeout
        for future in as_completed(futures):
            source_tag = futures[future]
            try:
                result = future.result(timeout=10)
                if result:
                    all_jobs.extend(result)
            except Exception as e:
                logger.error(f"Source {source_tag} timed out or failed: {e}")
                errors.append({'source': source_tag, 'message': str(e)})

    # Recency filtering
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
    filtered_jobs = []
    for job in all_jobs:
        if job.get('date_unknown'):
            if days_ago >= 30:
                filtered_jobs.append(job)
        else:
            p_date = parse_date(job.get('date_posted', ''))
            if p_date and p_date >= cutoff_date:
                filtered_jobs.append(job)
            elif not p_date:
                filtered_jobs.append(job)

    # Deduplication logic
    unique_jobs = {}
    duplicates_removed = 0
    
    def get_source_priority(job):
        st = job.get('source_type', '')
        if st in ['greenhouse', 'lever', 'workday', 'smartrecruiters', 'workable']:
            return 1
        return 2

    filtered_jobs.sort(key=get_source_priority)

    for job in filtered_jobs:
        title_norm = normalize_string(job.get('title', ''))
        company_norm = normalize_string(job.get('company', ''))
        loc_norm = normalize_string(job.get('location', ''))
        
        dup_key = f"{title_norm}_{company_norm}_{loc_norm}"
        
        if dup_key in unique_jobs:
            duplicates_removed += 1
            if get_source_priority(job) < get_source_priority(unique_jobs[dup_key]):
                unique_jobs[dup_key] = job
        else:
            unique_jobs[dup_key] = job

    # Format output dates to human readable labels
    final_results = list(unique_jobs.values())
    now = datetime.now(timezone.utc)
    for job in final_results:
        rel_label = "Posted date unknown"
        p_date = parse_date(job.get('date_posted', ''))
        if p_date:
            diff = now - p_date
            if diff.days == 0:
                hours = int(diff.seconds / 3600)
                if hours == 0:
                    rel_label = "Posted just now"
                elif hours == 1:
                    rel_label = "Posted 1 hour ago"
                else:
                    rel_label = f"Posted {hours} hours ago"
            elif diff.days == 1:
                rel_label = "Posted yesterday"
            else:
                rel_label = f"Posted {diff.days} days ago"
        job['date_label'] = rel_label

    # Sort final results by date (newest first)
    def get_sort_key(job):
        p_date = parse_date(job.get('date_posted', ''))
        return p_date.timestamp() if p_date else 0

    final_results.sort(key=get_sort_key, reverse=True)

    search_time_ms = int((time.time() - start_time) * 1000)
    
    response_payload = {
        "total": len(final_results),
        "duplicates_removed": duplicates_removed,
        "search_time_ms": search_time_ms,
        "results": final_results,
        "errors": errors
    }
    
    # If old frontend calls this, return direct list for compatibility
    if 'search_term' in data:
        old_format_list = []
        for r in final_results:
            old_format_list.append({
                'title': r['title'],
                'company': r['company'],
                'location': r['location'],
                'date_posted': r['date_label'],
                'site': r['source_type'].upper() if r['source_type'] in ['indeed', 'linkedin'] else r['source_type'].capitalize(),
                'job_url': r['apply_url']
            })
        return jsonify(old_format_list)
        
    return jsonify(response_payload)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting JobStaffer Dashboard on port {port}")
    app.run(host='0.0.0.0', port=port, debug=True)
