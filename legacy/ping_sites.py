import requests
import random
import time
import csv
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

SITES_FILE = 'sites.txt'
SUMMARY_REPORT = 'report.csv'
PROBLEM_REPORT = 'detailed_status.csv'
MAX_WORKERS = 4 

SUB_PAGES = ['', '/about-us', '/contact-us', '/privacy-policy']

def is_valid_url(url):
    if '.' not in url or ' ' in url or '↑' in url:
        return False
    if url.startswith(('/', '.', '#')):
        return False
    return True

def ping_url(original_url):
    url = original_url.strip()
    if not url or not is_valid_url(url):
        return None

    if not url.startswith('http'):
        base_url = 'http://' + url
    else:
        base_url = url
        
    if base_url.endswith('/'):
        base_url = base_url[:-1]
    
    # 💥 Magic Trick: গুগল বট সেজে ইনফিনিটি ফ্রির সিকিউরিটি বাইপাস করা
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5'
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    suspend_urls = ["suspended-domain.net", "suspendedpage", "epizy.com/suspended"]
    
    strict_error_phrases = [
        "this domain has been suspended",
        "reaching server limits",
        "contact support in your hosting control panel",
        "account has been suspended", 
        "this site is under construction", 
        "default web site page", 
        "suspended-domain.net",
        "site is sleeping"
    ]

    # সিকিউরিটি দেওয়ালের সিগনেচার
    js_challenge_phrases = [
        "aes.js",
        "this site requires javascript to work",
        "checking your browser"
    ]
    
    try:
        time.sleep(random.uniform(1, 3))
        response1 = session.get(base_url, timeout=15, allow_redirects=True)
        
        final_url1 = response1.url.lower()
        page_content1 = response1.text.lower()
        
        if any(url_part in final_url1 for url_part in suspend_urls):
            return original_url, response1.status_code, "Suspended", response1.url
            
        if any(phrase in page_content1 for phrase in strict_error_phrases):
            return original_url, response1.status_code, "Suspended/Host_Error", response1.url
            
        # যদি গুগল বট হওয়ার পরও সে সিকিউরিটি পেজ দেয়
        if any(phrase in page_content1 for phrase in js_challenge_phrases):
            return original_url, response1.status_code, "Blocked_by_Security", response1.url
            
        chosen_sub = random.choice(SUB_PAGES)
        if not chosen_sub:
            response2 = response1
            final_url2 = final_url1
            page_content2 = page_content1
        else:
            target_url = f"{base_url}{chosen_sub}"
            time.sleep(random.uniform(2, 4))
            session.headers.update({'Referer': response1.url})
            response2 = session.get(target_url, timeout=15, allow_redirects=True)
            final_url2 = response2.url.lower()
            page_content2 = response2.text.lower()
        
        if any(url_part in final_url2 for url_part in suspend_urls):
            return original_url, response2.status_code, "Suspended", response2.url
            
        if any(phrase in page_content2 for phrase in strict_error_phrases):
            return original_url, response2.status_code, "Suspended/Host_Error", response2.url
            
        if any(phrase in page_content2 for phrase in js_challenge_phrases):
            return original_url, response2.status_code, "Blocked_by_Security", response2.url
            
        history = response2.history
        redirected = len(history) > 0
        
        if redirected and response2.status_code == 200:
            orig_clean = url.replace('http://', '').replace('https://', '').replace('www.', '').split('/')[0]
            final_clean = response2.url.replace('http://', '').replace('https://', '').replace('www.', '').split('/')[0]
            
            if orig_clean != final_clean:
                return original_url, response2.status_code, "Redirected", response2.url
            else:
                return original_url, response2.status_code, "Success", response2.url
        
        if response2.status_code == 200:
            return original_url, response2.status_code, "Success", response2.url
        else:
            return original_url, response2.status_code, f"Error_{response2.status_code}", response2.url
            
    except Exception:
        return original_url, 0, "Invalid/Down", "N/A"

def start_process():
    if not os.path.exists(SITES_FILE): return

    with open(SITES_FILE, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]
    
    random.shuffle(urls)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(ping_url, urls))

    results = [r for r in results if r is not None]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(PROBLEM_REPORT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Exact_URL_from_List", "Status_Code", "Issue_Type", "Final_URL", "Checked_At"])
        for url, code, status, final_u in results:
            if status != "Success":
                writer.writerow([url, code, status, final_u, now])

    success_count = sum(1 for _, _, s, _ in results if s == "Success")
    suspended_count = sum(1 for _, _, s, _ in results if "Suspended" in s)
    redirect_count = sum(1 for _, _, s, _ in results if s == "Redirected")
    failed_count = len(results) - (success_count + suspended_count + redirect_count)

    file_exists = os.path.exists(SUMMARY_REPORT)
    with open(SUMMARY_REPORT, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Date", "Total", "Success", "Suspended", "Redirected", "Failed"])
        writer.writerow([now, len(results), success_count, suspended_count, redirect_count, failed_count])

if __name__ == "__main__":
    start_process()
