"""
Site.pro + emailqu + Roundcube Webmail - WhatsApp Fix Module
Modul ini berisi semua helper functions untuk /fix dan /create commands.

Register menggunakan DrissionPage untuk bypass reCAPTCHA v2 invisible (gratis).
Setelah register, semua step (website, mailbox, webmail, send) pakai requests.
"""
import requests
import re
import time
import json
import random
import string
import html as html_module
import threading
from urllib.parse import urlencode, quote


# ════════════════════════════════════════════════════════════════
#  EMAILQU.COM — Temporary Email Helpers
# ════════════════════════════════════════════════════════════════

def emailqu_get_temp_email(max_retries=3):
    """Buat email temporary dari emailqu.com. Returns (email, domain) or (None, None)."""
    for attempt in range(max_retries):
        try:
            s = requests.Session()
            # Get random username
            r = s.get(f"https://emailqu.com/api/random-username", timeout=10)
            r.raise_for_status()
            username = r.json().get("username")
            if not username:
                continue

            # Get random domain
            r = s.get(f"https://emailqu.com/api/domains/random", timeout=10)
            r.raise_for_status()
            domains = r.json().get("domains", [])
            # Pick a non-subdomain, non-hidden domain
            valid_domains = [d for d in domains if not d.get("is_subdomain") and not d.get("is_hidden")]
            if not valid_domains:
                valid_domains = domains
            if not valid_domains:
                continue
            domain = random.choice(valid_domains)["domain"]

            # Verify domain
            r = s.get(f"https://emailqu.com/api/domain/verify/{domain}", timeout=10)
            if r.status_code != 200:
                continue

            email = f"{username}@{domain}"
            return email, domain
        except Exception as e:
            print(f"[SITEPRO] emailqu_get_temp_email attempt {attempt+1} error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return None, None


def emailqu_poll_inbox(email, timeout=90, interval=3):
    """Poll emailqu inbox sampai dapat email dari Site.pro. Returns email body_text or None."""
    s = requests.Session()
    encoded = quote(email, safe="")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = s.get(f"https://emailqu.com/api/public/emails/{encoded}?limit=1", timeout=10)
            if r.status_code == 200:
                data = r.json()
                emails = data.get("emails", [])
                if emails:
                    body = emails[0].get("body_text", "")
                    return body
        except Exception as e:
            print(f"[SITEPRO] emailqu_poll_inbox error: {e}")
        time.sleep(interval)
    return None


def emailqu_extract_otp(body_text):
    """Extract 6-digit OTP code from Site.pro verification email."""
    if not body_text:
        return None
    # Pattern: "Kode verifikasi: \n779016" or "Verification code: \n123456"
    m = re.search(r'(?:Kode verifikasi|Verification code)\s*:\s*\n?(\d{6})', body_text)
    if m:
        return m.group(1)
    # Fallback: cari 6 digit standalone
    m = re.search(r'\b(\d{6})\b', body_text)
    if m:
        return m.group(1)
    return None


# ════════════════════════════════════════════════════════════════
#  CAPTCHA SOLVER — reCAPTCHA v2 Invisible (2captcha-compatible API)
#  Supports: 2captcha.com, rucaptcha.com, anti-captcha.com, capsolver.com
# ════════════════════════════════════════════════════════════════

def solve_recaptcha_2captcha(api_key, sitekey, url, api_base="https://2captcha.com", max_wait=180):
    """
    Solve reCAPTCHA v2 invisible via 2captcha-compatible API.
    api_base: "https://2captcha.com" or "https://rucaptcha.com" etc.
    Returns token string or None.
    """
    if not api_key:
        print("[SITEPRO] CAPTCHA_API_KEY belum diset!")
        return None
    try:
        # Step 1: Submit task
        r = requests.post(f"{api_base}/in.php", data={
            "key": api_key,
            "method": "userrecaptcha",
            "googlekey": sitekey,
            "pageurl": url,
            "invisible": "1",
            "json": "1",
        }, timeout=30)
        data = r.json()
        if data.get("status") != 1:
            print(f"[SITEPRO] Captcha submit error: {data}")
            return None
        task_id = data["request"]
        print(f"[SITEPRO] Captcha task created: {task_id}")

        # Step 2: Wait before first poll
        time.sleep(15)

        # Step 3: Poll for result
        deadline = time.time() + max_wait
        while time.time() < deadline:
            r2 = requests.get(f"{api_base}/res.php", params={
                "key": api_key,
                "action": "get",
                "id": task_id,
                "json": "1",
            }, timeout=15)
            d2 = r2.json()
            if d2.get("request") == "CAPCHA_NOT_READY":
                time.sleep(5)
                continue
            if d2.get("status") == 1:
                print(f"[SITEPRO] Captcha solved!")
                return d2["request"]
            print(f"[SITEPRO] Captcha poll error: {d2}")
            return None
        print(f"[SITEPRO] Captcha solve timeout ({max_wait}s)")
        return None
    except Exception as e:
        print(f"[SITEPRO] Captcha solver error: {e}")
        return None


def solve_recaptcha_capsolver(api_key, sitekey, url, max_wait=180):
    """
    Solve reCAPTCHA v2 invisible via CapSolver API.
    Returns token string or None.
    """
    if not api_key:
        print("[SITEPRO] CAPSOLVER_API_KEY belum diset!")
        return None
    try:
        # Create task
        r = requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": url,
                "websiteKey": sitekey,
                "isInvisible": True,
            }
        }, timeout=30)
        data = r.json()
        if data.get("errorId", 0) != 0:
            print(f"[SITEPRO] CapSolver create error: {data}")
            return None
        task_id = data.get("taskId")
        if not task_id:
            print(f"[SITEPRO] CapSolver no taskId: {data}")
            return None
        print(f"[SITEPRO] CapSolver task: {task_id}")

        time.sleep(10)
        deadline = time.time() + max_wait
        while time.time() < deadline:
            r2 = requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": api_key,
                "taskId": task_id,
            }, timeout=15)
            d2 = r2.json()
            status = d2.get("status")
            if status == "ready":
                token = d2.get("solution", {}).get("gRecaptchaResponse")
                if token:
                    print(f"[SITEPRO] CapSolver solved!")
                    return token
            if d2.get("errorId", 0) != 0:
                print(f"[SITEPRO] CapSolver error: {d2}")
                return None
            time.sleep(5)
        print(f"[SITEPRO] CapSolver timeout ({max_wait}s)")
        return None
    except Exception as e:
        print(f"[SITEPRO] CapSolver error: {e}")
        return None


def solve_recaptcha(api_key, sitekey, url, solver_type="2captcha"):
    """
    Universal captcha solver dispatcher.
    solver_type: "2captcha", "capsolver", "anticaptcha"
    """
    if solver_type in ("2captcha", "rucaptcha"):
        base = "https://2captcha.com" if solver_type == "2captcha" else "https://rucaptcha.com"
        return solve_recaptcha_2captcha(api_key, sitekey, url, api_base=base)
    elif solver_type == "capsolver":
        return solve_recaptcha_capsolver(api_key, sitekey, url)
    elif solver_type == "anticaptcha":
        # Anti-captcha uses same protocol as 2captcha but different base
        return solve_recaptcha_2captcha(api_key, sitekey, url, api_base="https://api.anti-captcha.com")
    else:
        print(f"[SITEPRO] Unknown solver type: {solver_type}")
        return None


# ════════════════════════════════════════════════════════════════
#  SITE.PRO — Registration & Account Helpers
# ════════════════════════════════════════════════════════════════

def _sitepro_session():
    """Create a requests.Session with random Android UA for Site.pro.
    Includes DNS override to bypass VPS DNS resolution issues."""
    from urllib3.util.retry import Retry
    from requests.adapters import HTTPAdapter
    
    _android_uas = [
        'Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 13; Redmi Note 12 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 14; POCO X5 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.71 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 13; V2237) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.99 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 14; CPH2565) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.113 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 15; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36',
    ]
    
    # Force DNS: site.pro -> 172.104.174.106 (bypass VPS DNS issues)
    from urllib3.util.connection import create_connection as _orig_create_connection
    import urllib3.util.connection
    
    _SITEPRO_IP = "172.104.174.106"
    _orig_cc = urllib3.util.connection.create_connection
    
    def _patched_create_connection(address, *args, **kwargs):
        host, port = address
        if host == "site.pro":
            address = (_SITEPRO_IP, port)
        return _orig_create_connection(address, *args, **kwargs)
    
    urllib3.util.connection.create_connection = _patched_create_connection
    
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        'User-Agent': random.choice(_android_uas),
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.6,en;q=0.5',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    return s


def sitepro_restore_session(phpsessid, email=None, password=None):
    """
    Restore session dari PHPSESSID yg tersimpan.
    Returns (session, ok) — kalau PHPSESSID expired tapi email+password ada,
    coba re-login. Kalau gagal total, return (None, False).
    Retry 3x dengan backoff jika timeout.
    """
    if not phpsessid:
        return None, False
    
    # Retry loop for session check
    s = _sitepro_session()
    s.cookies.set('PHPSESSID', phpsessid, domain='site.pro')
    session_ok = False
    for attempt in range(3):
        try:
            r = s.get(
                "https://site.pro/id/Website-Saya/load-init/",
                headers={'X-Requested-With': 'XMLHttpRequest'},
                timeout=45,
            )
            try:
                data = r.json()
                if data.get('error') != 'no auth':
                    return s, True
            except Exception:
                pass
            break  # Got response but session expired, don't retry
        except Exception as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"[SITEPRO] restore_session check error (3x failed): {e}")

    # PHPSESSID expired — coba re-login pakai email+password kalau ada
    if email and password:
        for attempt in range(3):
            try:
                s2 = _sitepro_session()
                r = s2.post(
                    "https://site.pro/id/ul/",
                    data={
                        'redir_url': '', 'form_type': '1', 'social_submit': '',
                        'loginBuyDomain': '', 'loginOrderService': '',
                        'plan_id': '', 'plan_cycle': '',
                        'user_login': email, 'user_password': password,
                    },
                    headers={
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://site.pro',
                        'Referer': 'https://site.pro/id/',
                    },
                    allow_redirects=True, timeout=60,
                )
                # verify
                r2 = s2.get(
                    "https://site.pro/id/Website-Saya/load-init/",
                    headers={'X-Requested-With': 'XMLHttpRequest'},
                    timeout=45,
                )
                try:
                    data = r2.json()
                    if data.get('error') != 'no auth':
                        return s2, True
                except Exception:
                    pass
                break  # Got response but auth failed
            except Exception as e:
                if attempt < 2:
                    time.sleep(10 * (attempt + 1))
                else:
                    print(f"[SITEPRO] re-login error (3x failed): {e}")

    return None, False



def sitepro_get_csrf(session):
    """Ambil CSRF token (lc) dari halaman Site.pro. Returns (csrf, session) or (None, session)."""
    try:
        # Try Website-Saya page first (for logged-in users)
        r = session.get("https://site.pro/id/Website-Saya/", timeout=15)
        if r.status_code == 200:
            # Pattern: input.val("e5ba40389bfbaa4362f24baa6a7bcb75")
            m = re.search(r'input\.val\(["\']([a-f0-9]{32})["\']\)', r.text)
            if m:
                return m.group(1), session
            # Fallback: name="lc" value="..."
            m = re.search(r'name=["\']lc["\'][\s>]*value=["\']([a-f0-9]{32})["\']', r.text)
            if m:
                return m.group(1), session
            # Fallback: csrfToken in JS
            m = re.search(r'["\']csrfToken["\']\s*[=:]\s*["\']([a-f0-9]{32})["\']', r.text)
            if m:
                return m.group(1), session
            # Fallback: token in JSON-like data
            m = re.search(r'"token"\s*:\s*"([a-f0-9]{32})"', r.text)
            if m:
                return m.group(1), session

        # Fallback: main page
        r2 = session.get("https://site.pro/id/", timeout=15)
        if r2.status_code == 200:
            m = re.search(r'input\.val\(["\']([a-f0-9]{32})["\']\)', r2.text)
            if m:
                return m.group(1), session
            m = re.search(r'name=["\']lc["\'][\s>]*value=["\']([a-f0-9]{32})["\']', r2.text)
            if m:
                return m.group(1), session
    except Exception as e:
        print(f"[SITEPRO] get_csrf error: {e}")
    return None, session


def _random_name():
    """Generate random full name for registration."""
    first_names = ["Alex", "Ryan", "Jordan", "Casey", "Taylor", "Morgan", "Riley", "Quinn", "Avery", "Blake"]
    last_names = ["Smith", "Lee", "Park", "Kim", "Chen", "Wang", "Silva", "Santos", "Lopez", "Garcia"]
    return f"{random.choice(first_names)} {random.choice(last_names)}"


def _random_password():
    """Generate random password meeting Site.pro requirements."""
    chars = string.ascii_letters + string.digits
    pw = ''.join(random.choices(chars, k=12))
    return pw + "@" + random.choice(string.digits)


def _random_email_user():
    """Generate email username: wa_fix_XXXXX."""
    return f"wa_fix_{random.randint(10000, 99999)}"


def sitepro_register(session, temp_email, name, password, csrf, captcha_token):
    """Register akun Site.pro via requests (requires valid captcha_token).
    Returns (success: bool, session, message: str)."""
    try:
        data = {
            'redir_url': '',
            'refId': '',
            'plan': '0',
            'social_submit': '',
            'forced_user_mtype': '',
            'user_mtype': '0',
            'plan_id': '',
            'plan_cycle': '',
            'regPageId': '700',
            'regUrl': 'https://site.pro/id/',
            'regBtn': 'create-new-website-C',
            'regType': '0',
            'regTag': '',
            'regBuyDomain': '',
            'coupon': '',
            'open_from_facebook': '0',
            'open_from_facebook_asia': '0',
            'g-recaptcha-response': captcha_token,
            'create_email': temp_email,
            'create_name': name,
            'create_pass': password,
            'lc': csrf,
        }
        r = session.post(
            "https://site.pro/id/ul/register/",
            data=data,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/',
            },
            allow_redirects=False,
            timeout=30,
        )
        if r.status_code == 302:
            loc = r.headers.get('Location', '')
            if loc:
                session.get(loc, timeout=15)
            return True, session, "Register berhasil, menunggu OTP..."
        else:
            return False, session, f"Register gagal (status {r.status_code})"
    except Exception as e:
        return False, session, f"Register error: {e}"


# Shared state for profile cloning (template = hasil clone profil utama 1x)
_cloned_profile_cache = {'path': None, 'lock': threading.Lock()}

# NopeCHA extension ID (untuk minimal profile clone)
NOPECHA_EXT_ID = "dknlfmjaanfblgfdfebhijalfmhmjjjo"


_port_lock = threading.Lock()
_port_next = [9333]  # counter port debug, dinaikkan berurutan (race-free)


def _free_port():
    """Alokasikan port debug UNIK & bebas untuk tiap browser.

    PENTING: pakai counter + lock, BUKAN bind(port 0). bind(0) bisa kasih port
    yang sama ke 2 thread (OS dipakai-ulang setelah close) -> 2 Chrome rebutan
    port yang sama -> DrissionPage error "the user folder does conflict".
    """
    import socket
    with _port_lock:
        for _ in range(5000):
            port = _port_next[0]
            _port_next[0] += 1
            if _port_next[0] > 60000:
                _port_next[0] = 9333
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(('127.0.0.1', port))
                s.close()
                return port  # port ini belum dipakai & belum dibagikan thread lain
            except OSError:
                s.close()
                continue
    # fallback terakhir
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_min_template():
    """Bangun template profil MINIMAL: hanya file yang dibutuhkan agar NopeCHA
    terdaftar & aktif. Sangat cepat (~0.1s, ~6MB) vs clone profil penuh (53s+169s).
    Dipakai bersama semua instance (di-cache di _cloned_profile_cache).
    """
    import os
    import shutil
    import tempfile
    main_profile = os.environ['LOCALAPPDATA'] + r'\Google\Chrome\User Data'

    def cp_file(src, dst):
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

    def cp_tree(src, dst):
        if os.path.isdir(src):
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True)
            except Exception:
                pass

    tmpl = os.path.join(tempfile.gettempdir(), 'sitepro_min_clone')
    if os.path.isdir(tmpl):
        shutil.rmtree(tmpl, ignore_errors=True)
    # Local State (top-level) + prefs Default -> registrasi & MAC extension
    cp_file(os.path.join(main_profile, 'Local State'),
            os.path.join(tmpl, 'Local State'))
    for f in ('Preferences', 'Secure Preferences'):
        cp_file(os.path.join(main_profile, 'Default', f),
                os.path.join(tmpl, 'Default', f))
    # File extension NopeCHA + setting tersimpan (API key/config)
    cp_tree(os.path.join(main_profile, 'Default', 'Extensions', NOPECHA_EXT_ID),
            os.path.join(tmpl, 'Default', 'Extensions', NOPECHA_EXT_ID))
    cp_tree(os.path.join(main_profile, 'Default', 'Local Extension Settings', NOPECHA_EXT_ID),
            os.path.join(tmpl, 'Default', 'Local Extension Settings', NOPECHA_EXT_ID))
    for d in ('Extension State', 'Extension Rules', 'Extension Scripts'):
        cp_tree(os.path.join(main_profile, 'Default', d),
                os.path.join(tmpl, 'Default', d))
    return tmpl

# ── Window tiling: tiap browser dapat slot grid (kiri→kanan, lalu turun) ──
WIN_W, WIN_H = 480, 380     # ukuran window kecil & rapi
WIN_COLS = 4                # 4 kolom per baris
WIN_X0, WIN_Y0 = 20, 20     # offset awal dari pojok kiri-atas
_window_slot = {'n': 0, 'lock': threading.Lock()}


def _next_window_pos():
    """Alokasikan posisi window berikutnya dalam grid rapi. Return (x, y)."""
    with _window_slot['lock']:
        slot = _window_slot['n']
        _window_slot['n'] = (_window_slot['n'] + 1) % (WIN_COLS * 3)  # 3 baris siklus
    col = slot % WIN_COLS
    row = slot // WIN_COLS
    x = WIN_X0 + col * (WIN_W + 10)
    y = WIN_Y0 + row * (WIN_H + 10)
    return x, y


def _cleanup_clone_profile():
    """Reset template clone cache so next attempt re-clones from main profile.

    Dipanggil setelah register fail/timeout — sebab template lama bisa kena
    state aneh (cookies, recaptcha cache) yang bikin NopeCHA stuck.
    """
    import os
    import shutil
    with _cloned_profile_cache['lock']:
        path = _cloned_profile_cache.get('path')
        _cloned_profile_cache['path'] = None
        if path and os.path.isdir(path):
            try:
                shutil.rmtree(path, ignore_errors=True)
                print("[SITEPRO] Cleared cloned profile template")
            except Exception as e:
                print(f"[SITEPRO] Cleanup warn: {e}")


def sitepro_register_browser(temp_email, name, password, headless=False, timeout=300):
    """
    Register akun Site.pro menggunakan DrissionPage (Chromium automation).
    Menggunakan CHROME PROFILE UTAMA (bukan temp profile).
    reCAPTCHA v2 invisible auto-solves via NopeCHA extension.
    Window kecil & rapi (800x600).

    Flow:
    1. Load site.pro/id/ di Chrome utama + NopeCHA
    2. Click "Mulai Gratis" -> "Daftar dengan Email"
    3. Fill form (email, name, password)
    4. Click "Daftar" submit -> reCAPTCHA invisible auto-executes
    5. Wait for redirect to confirm page (NopeCHA solves challenge if needed)
    6. Poll OTP dari emailqu -> confirm
    7. Login via requests for proper session

    Returns (success: bool, requests_session, message: str)
    - requests_session sudah logged-in via email/password login setelah register
    """
    from DrissionPage import Chromium, ChromiumOptions
    import os
    import glob
    import tempfile
    import shutil

    # NopeCHA extension base dir (auto-detect version folder)
    NOPECHA_BASE = r"C:\Users\USER\AppData\Local\Google\Chrome\User Data\Default\Extensions\dknlfmjaanfblgfdfebhijalfmhmjjjo"

    def _find_nopecha_ext():
        """Find the NopeCHA extension folder (any version)."""
        if not os.path.isdir(NOPECHA_BASE):
            return None
        candidates = sorted(glob.glob(os.path.join(NOPECHA_BASE, "*")), reverse=True)
        for c in candidates:
            if os.path.isfile(os.path.join(c, "manifest.json")):
                return c
        return None

    browser = None
    tmp_profile = None
    try:
        # PROFIL STRATEGY (Chrome 149+ blokir --load-extension, NopeCHA sudah
        # ter-install di profil utama; 2 Chromium TIDAK boleh share user-data-dir):
        #   1) Bangun TEMPLATE minimal (cuma NopeCHA + prefs) 1x -> ~0.1s, ~6MB.
        #   2) Tiap instance copy template -> dir unik (cepat, aman paralel).
        cache = _cloned_profile_cache
        with cache['lock']:
            if cache['path'] is None or not os.path.isdir(cache['path']):
                print(f"[SITEPRO] Membangun template profil minimal (NopeCHA)...")
                cache['path'] = _build_min_template()
                print(f"[SITEPRO] Template profil siap (NopeCHA included).")
            template_dir = cache['path']

        win_x, win_y = _next_window_pos()

        # Launch dengan RETRY: kalau bentrok ("user folder does conflict" / port
        # rebutan), coba lagi dengan port + folder BARU. ChromiumOptions dibuat
        # ulang tiap attempt biar state address-nya bersih.
        launch_err = None
        for attempt in range(1, 4):
            port = _free_port()
            instance_dir = tempfile.mkdtemp(prefix='sitepro_inst_')
            try:
                shutil.copytree(template_dir, instance_dir, dirs_exist_ok=True)
            except Exception as e:
                print(f"[SITEPRO] copy template warn: {e}")
            tmp_profile = instance_dir  # hapus saat finally

            co = ChromiumOptions()
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-dev-shm-usage')
            co.set_argument('--lang=id-ID')
            co.set_argument(f'--window-size={WIN_W},{WIN_H}')
            co.set_argument(f'--window-position={win_x},{win_y}')
            co.set_local_port(port)
            co.set_user_data_path(instance_dir)
            co.set_argument('--profile-directory=Default')
            if headless:
                co.set_argument('--headless=new')
                co.set_argument('--disable-blink-features=AutomationControlled')

            print(f"[SITEPRO] Starting browser @ {win_x},{win_y} port={port} (attempt {attempt})...")
            try:
                browser = Chromium(co)
                tab = browser.latest_tab
                launch_err = None
                break
            except Exception as e:
                launch_err = e
                print(f"[SITEPRO] Launch gagal (attempt {attempt}): {e}")
                browser = None
                try:
                    shutil.rmtree(instance_dir, ignore_errors=True)
                except Exception:
                    pass
                tmp_profile = None
                time.sleep(1.5)

        if browser is None:
            return False, None, f"Browser gagal start (3x coba): {launch_err}"

        # Step 1: Load page, then clear cookies & reload to ensure logged-out state
        # (main Chrome profile may already be logged in to Site.pro from prior runs)
        print(f"[SITEPRO] Loading site.pro/id/...")
        tab.get("https://site.pro/id/", timeout=30)
        try:
            tab.set.cookies.clear()
            tab.get("https://site.pro/id/", timeout=30)
        except Exception as e:
            print(f"[SITEPRO] clear cookies warn: {e}")
        # Tunggu lebih lama supaya NopeCHA extension sempat inject ke DOM
        time.sleep(6)

        # Step 2: Open registration modal via JS-click.
        # native .click() gagal "This element has no location or size" di window
        # kecil (viewport sempit). JS-click jalan di elemen apapun.
        tab.run_js(r"""
            document.querySelectorAll('a,button,div,span').forEach(function(e){
                if((e.textContent||'').trim().indexOf('Mulai Gratis')===0){ e.click(); }
            });
        """)
        time.sleep(2)
        tab.run_js(r"""
            document.querySelectorAll('a,button,div,span').forEach(function(e){
                if((e.textContent||'').trim().indexOf('Daftar dengan Email')>=0){ e.click(); }
            });
        """)
        time.sleep(2)

        # Step 3: Fill form via JS
        print(f"[SITEPRO] Filling form...")
        fill_ok = tab.run_js(f"""
            var e = document.querySelector('input[name="create_email"]');
            var n = document.querySelector('input[name="create_name"]');
            var p = document.querySelector('input[name="create_pass"]');
            if (e) {{ e.focus(); e.value = '{temp_email}'; e.dispatchEvent(new Event('input', {{bubbles:true}})); e.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            if (n) {{ n.focus(); n.value = '{name}'; n.dispatchEvent(new Event('input', {{bubbles:true}})); n.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            if (p) {{ p.focus(); p.value = '{password}'; p.dispatchEvent(new Event('input', {{bubbles:true}})); p.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            return e && n && p ? 'ok' : 'missing';
        """)
        if fill_ok != 'ok':
            return False, None, "Form fields not found"
        time.sleep(1)

        # Step 4: Click submit (triggers CaptchaExecCaptchaField0)
        print(f"[SITEPRO] Submitting form (captcha auto-solve via NopeCHA)...")
        tab.run_js("var b = document.querySelector('.btn-register-submit'); if(b) b.click();")

        # Step 4b: Beri NopeCHA waktu mulai deteksi reCAPTCHA.
        print(f"[SITEPRO] Menunggu NopeCHA mulai solve (5s)...")
        time.sleep(5)

        # Step 4c: Tunggu captcha SELESAI — deteksi aktif sampai tidak ada captcha
        # lagi (token g-recaptcha terisi & popup challenge hilang). Jendela 30-60s.
        print(f"[SITEPRO] Menunggu captcha selesai (deteksi sampai hilang, maks 60s)...")
        captcha_deadline = time.time() + 60
        while time.time() < captcha_deadline:
            try:
                url = tab.url
                html = tab.html
            except Exception:
                time.sleep(2)
                continue
            # Sudah pindah halaman = captcha pasti sudah lewat
            if ('confirmCode' in html or 'check-activated' in url
                    or 'Pilih-layanan' in url or 'Website-Saya' in url):
                break
            try:
                cap = tab.run_js("""
                    var token = '';
                    var ta = document.querySelector('textarea[name="g-recaptcha-response"]');
                    if (ta) token = ta.value || '';
                    var bf = document.querySelector('.b-frame');
                    var challenge = bf ? (bf.offsetHeight > 200) : false;
                    return { solved: token.length > 0, challenge: challenge };
                """) or {}
            except Exception:
                cap = {}
            if cap.get('challenge'):
                print("    [CAPTCHA] Challenge popup tampil, NopeCHA solving...")
                time.sleep(4)
                continue
            if cap.get('solved'):
                print("    [CAPTCHA] Token terisi & tidak ada captcha lagi -> lanjut.")
                break
            time.sleep(3)

        # Step 5: Wait for captcha + confirm page
        deadline = time.time() + timeout
        registered = False
        challenge_seen = False
        csrf_retries = 0
        MAX_CSRF_RETRIES = 3
        while time.time() < deadline:
            try:
                html = tab.html
                url = tab.url
            except Exception:
                time.sleep(3)
                continue

            if 'confirmCode' in html or 'check-activated' in url:
                print(f"[SITEPRO] Register berhasil! (confirm page)")
                registered = True
                break

            if 'Pilih-layanan' in url or 'Website-Saya' in url:
                print(f"[SITEPRO] Register berhasil! (redirected)")
                registered = True
                break

            # CSRF token mismatch: muncul kalau "Daftar" diklik SEBELUM captcha
            # selesai. Fix: tunggu ~20 detik (biar NopeCHA selesai), lalu klik
            # tombol submit lagi -> halaman redirect ke halaman captcha.
            html_low = html.lower()
            if ('csrf' in html_low or 'token mismatch' in html_low
                    or 'token tidak' in html_low or 'token tidak cocok' in html_low):
                if csrf_retries < MAX_CSRF_RETRIES:
                    csrf_retries += 1
                    print(f"    [CSRF] Token mismatch terdeteksi (captcha belum selesai). "
                          f"Tunggu 20s lalu klik ulang (retry {csrf_retries}/{MAX_CSRF_RETRIES})...")
                    time.sleep(20)
                    try:
                        tab.run_js("var b = document.querySelector('.btn-register-submit'); if(b) b.click();")
                    except Exception:
                        pass
                    time.sleep(8)
                    continue
                else:
                    print(f"    [CSRF] Token mismatch terus berulang, menyerah.")
                    break

            # Check captcha challenge popup (image grid)
            try:
                status = tab.run_js("""
                    var frame = document.querySelector('.b-frame');
                    return { frameH: frame ? frame.offsetHeight : 0, url: window.location.href };
                """)
                challenge_visible = bool(status and status.get('frameH', 0) > 500)
                if challenge_visible:
                    if not challenge_seen:
                        print(f"    [CAPTCHA] Challenge popup (h={status['frameH']}), NopeCHA sedang solve...")
                        challenge_seen = True
                    # Beri waktu lebih lama untuk image challenge
                    time.sleep(5)
                    continue
            except Exception:
                pass

            time.sleep(3)

        if not registered:
            # Cleanup browser dulu agar lock dilepas sebelum hapus profile cache
            try:
                if browser:
                    browser.quit()
            except Exception:
                pass
            browser = None
            _cleanup_clone_profile()
            return False, None, f"Register timeout ({timeout}s)"

        # Step 6: Poll OTP and confirm via browser
        print(f"[SITEPRO] Menunggu OTP email...")
        body = emailqu_poll_inbox(temp_email, timeout=90, interval=3)
        if not body:
            return False, None, "OTP email timeout (90s)"

        otp = emailqu_extract_otp(body)
        if not otp:
            return False, None, "Gagal extract OTP dari email"
        print(f"[SITEPRO] OTP: {otp}")

        # Submit OTP
        tab.run_js(f"""
            var forms = document.querySelectorAll('form');
            for (var i = 0; i < forms.length; i++) {{
                var cf = forms[i].querySelector('input[name="confirmCode"]');
                if (cf) {{
                    cf.value = '{otp}';
                    forms[i].submit();
                    break;
                }}
            }}
        """)
        time.sleep(5)
        print(f"[SITEPRO] OTP submitted! URL: {tab.url}")

    except Exception as e:
        return False, None, f"Browser register error: {e}"
    finally:
        if browser:
            try:
                browser.quit()
            except:
                pass
        if tmp_profile:
            import shutil
            try:
                shutil.rmtree(tmp_profile, ignore_errors=True)
            except:
                pass

    # Step 7: Login via requests using registered email/password
    print(f"[SITEPRO] Login via requests (email={temp_email})...")
    session = _sitepro_session()
    csrf, session = sitepro_get_csrf(session)
    if not csrf:
        return False, None, "Gagal ambil CSRF untuk login"

    try:
        r = session.post(
            "https://site.pro/id/ul/",
            data={
                'redir_url': '',
                'form_type': '1',
                'social_submit': '',
                'loginBuyDomain': '',
                'loginOrderService': '',
                'plan_id': '',
                'plan_cycle': '',
                'user_login': temp_email,
                'user_password': password,
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/',
            },
            allow_redirects=True,
            timeout=30,
        )
        phpsessid = session.cookies.get('PHPSESSID', 'none')
        print(f"[SITEPRO] Login! PHPSESSID={phpsessid[:16]}...")

        # Verify by hitting load-init
        r2 = session.get("https://site.pro/id/Website-Saya/load-init/", timeout=15,
                         headers={'X-Requested-With': 'XMLHttpRequest'})
        try:
            data = r2.json()
            if data.get("error") == "no auth":
                print(f"[SITEPRO] load-init failed, navigating to Website-Saya...")
                session.get("https://site.pro/id/Website-Saya/", timeout=15)
                r3 = session.get("https://site.pro/id/Website-Saya/load-init/", timeout=15,
                                 headers={'X-Requested-With': 'XMLHttpRequest'})
                print(f"[SITEPRO] load-init retry: {r3.text[:100]}")
            else:
                print(f"[SITEPRO] load-init OK!")
        except:
            pass

        return True, session, "Register + login berhasil"
    except Exception as e:
        return False, None, f"Login error: {e}"


def sitepro_confirm_code(session, otp_code):
    """Konfirmasi OTP code di Site.pro. Returns (success, session, message)."""
    try:
        r = session.post(
            "https://site.pro/id/",
            data={'confirmCode': otp_code},
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/',
            },
            allow_redirects=True,
            timeout=30,
        )
        if r.status_code == 200 and ('Pilih-layanan' in r.url or 'Website-Saya' in r.url or 'choose' in r.url.lower()):
            return True, session, "Akun berhasil diaktifkan!"
        # Check if redirected to a good page
        if r.status_code == 200:
            return True, session, "Konfirmasi dikirim"
        return False, session, f"Konfirmasi gagal (status {r.status_code})"
    except Exception as e:
        return False, session, f"Konfirmasi error: {e}"


def sitepro_create_website(session, csrf):
    """Buat website di Site.pro. Returns (website_id, domain_name, message)."""
    try:
        r = session.post(
            "https://site.pro/id/Website-Saya/create-website",
            json={"csrfToken": csrf},
            headers={
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/Website-Saya/',
                'X-Requested-With': 'XMLHttpRequest',
            },
            timeout=30,
        )
        data = r.json()
        if data.get("ok"):
            ws_data = data.get("data", {})
            ws_id = ws_data.get("id")
            domains = ws_data.get("domains", [])
            domain_name = domains[0]["name"] if domains else "unknown"
            return ws_id, domain_name, "Website dibuat"
        return None, None, f"Create website gagal: {data}"
    except Exception as e:
        return None, None, f"Create website error: {e}"


def sitepro_create_mailbox(session, csrf, email_user, email_password):
    """Buat mailbox di siteprofree.email. Returns (mailbox_id, email, message)."""
    try:
        r = session.post(
            "https://site.pro/id/mailbox/add-mailbox",
            json={
                "domainId": 3,
                "emailUser": email_user,
                "emailPassword": email_password,
                "token": csrf,
            },
            headers={
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/Website-Saya/',
                'X-Requested-With': 'XMLHttpRequest',
            },
            timeout=30,
        )
        data = r.json()
        if data.get("ok"):
            mb = data.get("data", {})
            return mb.get("id"), mb.get("email"), "Mailbox dibuat"
        return None, None, f"Create mailbox gagal: {data}"
    except Exception as e:
        return None, None, f"Create mailbox error: {e}"


def sitepro_create_mailboxes(session, csrf, password, count=5):
    """
    Buat `count` mailbox (sender) sekaligus untuk 1 akun Site.pro.
    Tiap mailbox = alamat pengirim terpisah di siteprofree.email.
    Returns list of dict: [{'mailbox_id': int, 'email': str}, ...]
    """
    out = []
    for i in range(count):
        email_user = _random_email_user()
        mb_id, sitepro_email, msg = sitepro_create_mailbox(session, csrf, email_user, password)
        if mb_id:
            out.append({'mailbox_id': mb_id, 'email': sitepro_email})
            print(f"[SITEPRO] Mailbox {i+1}/{count}: {sitepro_email}")
        else:
            print(f"[SITEPRO] Mailbox {i+1}/{count} gagal: {msg}")
        time.sleep(1)
    return out


# ════════════════════════════════════════════════════════════════
#  ROUNDCUBE WEBMAIL — Login & Send Email
# ════════════════════════════════════════════════════════════════

def sitepro_open_webmail(session, mailbox_id):
    """Get SSO credentials and login to Roundcube webmail. Returns (webmail_session, message) or (None, error)."""
    try:
        # Step 1: Get SSO token from Site.pro
        r = session.post(
            "https://site.pro/id/mailbox/open-webmail",
            json={"mailboxId": mailbox_id},
            headers={
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/Website-Saya/',
            },
            timeout=30,
        )
        data = r.json()
        if not data.get("ok"):
            return None, f"open-webmail gagal: {data}"

        sso = data["data"]
        action_url = sso["action"]
        fields = sso["fields"]

        # Step 2: POST to splogin
        ws = requests.Session()
        ws.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
            'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.6,en;q=0.5',
        })
        r2 = ws.post(
            action_url,
            data={
                'spkey': fields['spkey'],
                'spsign': fields['spsign'],
                'splocale': fields.get('splocale', 'en_US'),
            },
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://site.pro',
                'Referer': 'https://site.pro/id/Website-Saya/',
            },
            allow_redirects=False,
            timeout=30,
        )

        # Step 3: Follow redirect chain to get roundcube_sessid
        # splogin redirects to /webmail/ -> sso.php -> index.php
        for _ in range(5):
            loc = r2.headers.get('Location')
            if not loc:
                break
            if loc.startswith('/'):
                loc = f"https://siteprofree.email{loc}"
            r2 = ws.get(loc, allow_redirects=False, timeout=15)

        # Final load of inbox to establish session
        ws.get("https://siteprofree.email/webmail/?_task=mail&_mbox=INBOX", timeout=15)

        return ws, "Webmail login berhasil"
    except Exception as e:
        return None, f"Webmail login error: {e}"


def roundcube_get_compose_token(webmail_session):
    """Open compose page and extract _token and _from identity. Returns (token, from_id, compose_id) or (None,None,None)."""
    try:
        # Open compose
        r = webmail_session.get(
            "https://siteprofree.email/webmail/?_task=mail&_mbox=INBOX&_action=compose",
            allow_redirects=True,
            timeout=15,
        )
        text = r.text

        # Extract _token
        m = re.search(r'name=["\']_token["\']\s*value=["\']([^"\']+)["\']', text)
        if not m:
            m = re.search(r'"request_token"\s*:\s*"([^"]+)"', text)
        token = m.group(1) if m else None

        # Extract compose ID from URL
        m2 = re.search(r'_id=([a-f0-9]+)', r.url)
        compose_id = m2.group(1) if m2 else None

        # Extract _from identity ID
        m3 = re.search(r'<option[^>]*value=["\'](\d+)["\'][^>]*selected', text)
        if not m3:
            m3 = re.search(r'name=["\']_from["\']\s*value=["\'](\d+)["\']', text)
        if not m3:
            m3 = re.search(r'"identity"\s*:\s*"?(\d+)', text)
        from_id = m3.group(1) if m3 else None

        return token, from_id, compose_id
    except Exception as e:
        print(f"[SITEPRO] get_compose_token error: {e}")
        return None, None, None


def roundcube_send_email(webmail_session, token, from_id, compose_id, to_email, message):
    """Send email via Roundcube webmail. Returns (success, message)."""
    try:
        ts = int(time.time() * 1000)
        data = {
            '_token': token,
            '_task': 'mail',
            '_action': 'send',
            '_id': compose_id or '',
            '_attachments': '',
            '_from': from_id or '',
            '_to': f"{to_email},",
            '_cc': '',
            '_bcc': '',
            '_replyto': '',
            '_followupto': '',
            '_subject': '',
            '_draft_saveid': '',
            '_draft': '',
            '_is_html': '0',
            '_framed': '1',
            '_message': message,
            'editorSelector': 'plain',
            '_priority': '0',
            '_store_target': 'Sent',
        }
        r = webmail_session.post(
            f"https://siteprofree.email/webmail/?_task=mail&_unlock=loading{ts}&_framed=1&_lang=en",
            data=data,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://siteprofree.email',
                'Referer': f'https://siteprofree.email/webmail/?_task=mail&_action=compose&_id={compose_id}',
            },
            timeout=30,
        )
        if r.status_code == 200 and ('sent_successfully' in r.text or 'berhasil dikirim' in r.text.lower()):
            return True, "Email berhasil dikirim!"
        return False, f"Kirim email gagal (status {r.status_code})"
    except Exception as e:
        return False, f"Kirim email error: {e}"


def roundcube_read_inbox(webmail_session, timeout=120, fetch_body=True, mbox='INBOX'):
    """
    Baca inbox webmail Roundcube.

    Roundcube `_remote=1` list response menaruh data pesan di field `exec`
    sebagai JavaScript `this.add_message_row(UID, header_obj, info_obj, flagged)`.
    Kita parse panggilan-panggilan itu lewat regex.

    Returns (list_of_messages, error_str_or_None).
    Tiap message: {uid, subject, from, date, size, body (opsional)}.
    """
    try:
        r = webmail_session.get(
            f"https://siteprofree.email/webmail/?_task=mail&_action=list&_mbox={mbox}&_remote=1&_unlock=0&_page=1",
            timeout=15,
        )
        if r.status_code != 200:
            return [], f"List gagal: {r.status_code}"

        try:
            data = r.json()
        except Exception:
            return [], "Response bukan JSON"

        exec_js = data.get('exec', '') or ''
        msg_count = data.get('env', {}).get('messagecount', 0)
        if not exec_js or msg_count == 0:
            return [], "No messages"

        # Pattern: this.add_message_row(UID, {header_json}, {info_json}, flagged);
        # Iterasi semua panggilan, extract JSON pakai counter brace.
        import re as _re
        results = []
        for m in _re.finditer(r'this\.add_message_row\(\s*(\d+)\s*,\s*', exec_js):
            uid = int(m.group(1))
            j = m.end()
            header_obj = _extract_json_obj(exec_js, j)
            if not header_obj:
                continue
            j_after = _find_obj_end(exec_js, j) + 1
            while j_after < len(exec_js) and exec_js[j_after] in ', \t\n':
                j_after += 1
            info_obj = _extract_json_obj(exec_js, j_after) or {}

            fromto_html = header_obj.get('fromto', '') or ''
            ma = _re.search(r'title="([^"]+)"', fromto_html)
            from_addr = ma.group(1) if ma else ''
            from_name = _re.sub(r'<[^>]+>', '', fromto_html).strip()

            results.append({
                'uid': uid,
                'subject': header_obj.get('subject', ''),
                'from': from_addr or from_name,
                'from_name': from_name,
                'date': header_obj.get('date', ''),
                'size': header_obj.get('size', ''),
                'mbox': (info_obj or {}).get('mbox', mbox),
                'body': '',
            })

        # Optional: fetch body untuk tiap message
        if fetch_body and results:
            for msg in results:
                body = _fetch_message_body(webmail_session, msg['uid'], msg['mbox'])
                msg['body'] = body

        return results, None
    except Exception as e:
        return [], f"Read inbox error: {e}"


def _extract_json_obj(s, start):
    """Extract JSON object starting at position `start` in string `s`. Returns dict or None."""
    if start >= len(s) or s[start] != '{':
        return None
    end = _find_obj_end(s, start)
    if end < 0:
        return None
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return None


def _find_obj_end(s, start):
    """Find position of closing brace matching opening at `start`. Returns index or -1."""
    if start >= len(s) or s[start] != '{':
        return -1
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
    return -1


def _fetch_message_body(webmail_session, uid, mbox='INBOX'):
    """Fetch full HTML body of a message via _action=show, then strip to text."""
    try:
        r = webmail_session.get(
            f"https://siteprofree.email/webmail/?_task=mail&_action=show&_mbox={mbox}&_uid={uid}&_extwin=0",
            timeout=15,
        )
        if r.status_code != 200:
            return ""
        html = r.text
        import re as _re
        # Cari <div id="message-objects"> atau body content
        # Roundcube taruh body di <div class="rcmBody"> atau langsung di iframe
        m = _re.search(r'<div[^>]*id="message-htmlpart\d+"[^>]*>(.*?)</div>\s*</div>', html, _re.DOTALL)
        body_html = m.group(1) if m else html

        # Fallback: cari iframe body src dan fetch itu
        if not m:
            mif = _re.search(r'<iframe[^>]+src="([^"]+_action=get[^"]+)"', html)
            if mif:
                src = mif.group(1).replace('&amp;', '&')
                if src.startswith('/'):
                    src = 'https://siteprofree.email' + src
                r2 = webmail_session.get(src, timeout=15)
                if r2.status_code == 200:
                    body_html = r2.text

        # Strip HTML
        text = _re.sub(r'<style[^>]*>.*?</style>', '', body_html, flags=_re.DOTALL)
        text = _re.sub(r'<script[^>]*>.*?</script>', '', text, flags=_re.DOTALL)
        text = _re.sub(r'<br\s*/?>', '\n', text)
        text = _re.sub(r'</p>', '\n\n', text)
        text = _re.sub(r'<[^>]+>', ' ', text)
        text = html_module.unescape(text)
        text = _re.sub(r'[ \t]+', ' ', text)
        text = _re.sub(r'\n{3,}', '\n\n', text).strip()
        return text[:3000]
    except Exception as e:
        print(f"[SITEPRO] _fetch_message_body error: {e}")
        return ""


# ════════════════════════════════════════════════════════════════
#  PIPELINE — Full /fix flow orchestrator
# ════════════════════════════════════════════════════════════════

def run_fix_pipeline(nomor, captcha_key="", db_cur=None, db_conn=None, solver_type="browser"):
    """
    Jalankan full pipeline untuk 1 nomor:
    1. Buat temp email (emailqu)
    2. Register akun Site.pro (browser = undetected-chromedriver, atau captcha API)
    3. Poll OTP dan konfirmasi
    4. Buat website
    5. Buat mailbox siteprofree.email
    6. Login webmail
    7. Kirim email ke WhatsApp support

    solver_type: "browser" (default, gratis), "2captcha", "capsolver", "anticaptcha"

    Returns dict dengan hasil setiap step.
    """
    result = {
        'nomor': nomor,
        'success': False,
        'steps': [],
        'temp_email': None,
        'sitepro_email': None,
        'error': None,
    }

    def log(step, ok, msg):
        status = "\u2705" if ok else "\u274c"
        result['steps'].append(f"{status} {step}: {msg}")

    # Step 1: Buat temp email
    temp_email, domain = emailqu_get_temp_email()
    if not temp_email:
        log("Temp Email", False, "Gagal buat email temporary")
        result['error'] = "Gagal buat email temporary"
        return result
    result['temp_email'] = temp_email
    log("Temp Email", True, temp_email)

    # Step 2: Register + OTP (browser or captcha API)
    name = _random_name()
    password = _random_password()

    if solver_type == "browser":
        # DrissionPage: register + auto-solve captcha + OTP in one step (GRATIS)
        ok, session, msg = sitepro_register_browser(temp_email, name, password, headless=False, timeout=300)
        if not ok:
            log("Register (Browser)", False, msg)
            result['error'] = msg
            return result
        log("Register + OTP (Browser)", True, msg)
    else:
        # Use captcha API (paid)
        session = _sitepro_session()
        csrf, session = sitepro_get_csrf(session)
        if not csrf:
            log("CSRF Token", False, "Gagal ambil CSRF token")
            result['error'] = "Gagal ambil CSRF token"
            return result
        log("CSRF Token", True, f"{csrf[:8]}...")

        captcha_token = solve_recaptcha(
            captcha_key,
            "6LeKkToUAAAAAHd9EiB6BaSXazFQ5CFIxmyLFm1Z",
            "https://site.pro/id/",
            solver_type=solver_type,
        )
        if not captcha_token:
            log("reCAPTCHA", False, "Gagal solve captcha")
            result['error'] = "Gagal solve captcha"
            return result
        log("reCAPTCHA", True, "Solved")

        ok, session, msg = sitepro_register(session, temp_email, name, password, csrf, captcha_token)
        if not ok:
            log("Register", False, msg)
            result['error'] = msg
            return result
        log("Register", True, msg)

        # Poll OTP (only needed for API solver mode, browser mode does it internally)
        body = emailqu_poll_inbox(temp_email, timeout=90, interval=3)
        if not body:
            log("OTP Email", False, "Timeout menunggu email OTP (90s)")
            result['error'] = "Timeout OTP"
            return result
        otp = emailqu_extract_otp(body)
        if not otp:
            log("OTP Extract", False, "Gagal extract kode OTP dari email")
            result['error'] = "Gagal extract OTP"
            return result
        log("OTP", True, f"Kode: {otp}")

        ok, session, msg = sitepro_confirm_code(session, otp)
        if not ok:
            log("Konfirmasi", False, msg)
            result['error'] = msg
            return result
        log("Konfirmasi", True, msg)

    # Refresh CSRF after login
    csrf, _ = sitepro_get_csrf(session)

    # Step 6: Buat website (diperlukan sebelum mailbox)
    ws_id, domain_name, msg = sitepro_create_website(session, csrf)
    if not ws_id:
        log("Website", False, msg)
        result['error'] = msg
        return result
    log("Website", True, f"{domain_name}")

    # Step 7: Buat mailbox
    email_user = _random_email_user()
    mb_id, sitepro_email, msg = sitepro_create_mailbox(session, csrf, email_user, password)
    if not mb_id:
        log("Mailbox", False, msg)
        result['error'] = msg
        return result
    result['sitepro_email'] = sitepro_email
    log("Mailbox", True, sitepro_email)

    # Step 8: Login webmail
    ws_session, msg = sitepro_open_webmail(session, mb_id)
    if not ws_session:
        log("Webmail Login", False, msg)
        result['error'] = msg
        return result
    log("Webmail Login", True, msg)

    # Step 9: Compose & kirim email
    token, from_id, compose_id = roundcube_get_compose_token(ws_session)
    if not token:
        log("Compose", False, "Gagal ambil token compose")
        result['error'] = "Gagal ambil token compose"
        return result
    log("Compose", True, f"Token didapat")

    # Format nomor (pastikan format +62xxx)
    nomor_clean = nomor.strip().replace("+", "").replace("-", "").replace(" ", "")
    if not nomor_clean.startswith("62") and nomor_clean.startswith("0"):
        nomor_clean = "62" + nomor_clean[1:]

    message = (
        "Olá, Equipe de Suporte do WhatsApp,\n\n"
        "Estou entrando em contato porque não consigo fazer login na minha conta do WhatsApp. "
        "Toda vez que tento, recebo a seguinte mensagem: \"Login Not Available Right Now.\"\n\n"
        "Já tentei reiniciar o aparelho, verificar minha conexão com a internet e tentar novamente "
        "várias vezes, mas o problema continua sem solução.\n\n"
        "Essa conta é muito importante para mim, pois contém meus grupos de estudo da universidade, "
        "materiais de aula e comunicações acadêmicas importantes. A perda de acesso está afetando meus estudos.\n\n"
        "Número de telefone : "
        f"+{nomor_clean}\n\n"
        "Agradeceria muito se pudessem me ajudar a restaurar o acesso o mais rápido possível. "
        "Obrigado pela atenção e pelo suporte.\n\n"
        "Atenciosamente,\n"
        "DikZz"
    )

    ok, msg = roundcube_send_email(ws_session, token, from_id, compose_id, "support@support.whatsapp.com", message)
    if not ok:
        log("Kirim Email", False, msg)
        result['error'] = msg
        return result
    log("Kirim Email", True, f"Terkirim ke support@support.whatsapp.com")

    # Save to DB if available
    if db_cur and db_conn:
        try:
            phpsessid = session.cookies.get('PHPSESSID', '')
            db_cur.execute(
                """INSERT INTO sitepro_accounts 
                   (user_id, temp_email, sitepro_email, sitepro_password, phpsessid, csrf_token, mailbox_id, website_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent')""",
                (0, temp_email, sitepro_email, password, phpsessid, csrf or '', mb_id, ws_id)
            )
            db_conn.commit()
        except Exception as e:
            print(f"[SITEPRO] DB save error: {e}")

    result['success'] = True
    return result


def run_create_pipeline(captcha_key="", db_cur=None, db_conn=None, solver_type="browser"):
    """
    Buat 1 akun Site.pro (tanpa kirim email).
    solver_type: "browser" (default, gratis), "2captcha", "capsolver", "anticaptcha"
    Returns dict dengan hasil.
    """
    result = {
        'success': False,
        'steps': [],
        'temp_email': None,
        'sitepro_email': None,
        'error': None,
    }

    def log(step, ok, msg):
        status = "\u2705" if ok else "\u274c"
        result['steps'].append(f"{status} {step}: {msg}")

    # Step 1: Temp email
    temp_email, domain = emailqu_get_temp_email()
    if not temp_email:
        log("Temp Email", False, "Gagal")
        result['error'] = "Gagal buat email temporary"
        return result
    result['temp_email'] = temp_email
    log("Temp Email", True, temp_email)

    # Step 2: Register + OTP
    name = _random_name()
    password = _random_password()

    if solver_type == "browser":
        ok, session, msg = sitepro_register_browser(temp_email, name, password, headless=False, timeout=300)
        if not ok:
            log("Register (Browser)", False, msg)
            result['error'] = msg
            return result
        log("Register + OTP (Browser)", True, msg)
    else:
        session = _sitepro_session()
        csrf, session = sitepro_get_csrf(session)
        if not csrf:
            log("CSRF", False, "Gagal")
            result['error'] = "Gagal ambil CSRF"
            return result
        log("CSRF", True, "OK")

        captcha_token = solve_recaptcha(captcha_key, "6LeKkToUAAAAAHd9EiB6BaSXazFQ5CFIxmyLFm1Z", "https://site.pro/id/", solver_type=solver_type)
        if not captcha_token:
            log("reCAPTCHA", False, "Gagal")
            result['error'] = "Gagal solve captcha"
            return result
        log("reCAPTCHA", True, "Solved")

        ok, session, msg = sitepro_register(session, temp_email, name, password, csrf, captcha_token)
        if not ok:
            log("Register", False, msg)
            result['error'] = msg
            return result
        log("Register", True, msg)

        body = emailqu_poll_inbox(temp_email, timeout=150, interval=3)
        if not body:
            log("OTP", False, "Timeout")
            result['error'] = "Timeout OTP"
            return result
        otp = emailqu_extract_otp(body)
        if not otp:
            log("OTP", False, "Gagal extract")
            result['error'] = "Gagal extract OTP"
            return result
        log("OTP", True, f"Kode: {otp}")

        ok, session, msg = sitepro_confirm_code(session, otp)
        if not ok:
            log("Konfirmasi", False, msg)
            result['error'] = msg
            return result
        log("Konfirmasi", True, msg)

    # Refresh CSRF
    csrf, _ = sitepro_get_csrf(session)

    # Step 5: Create website
    ws_id, domain_name, msg = sitepro_create_website(session, csrf)
    log("Website", ws_id is not None, msg if ws_id else msg)

    # Step 6: Create 5 mailbox (sender) — retry sampai 3x kalau gagal.
    mboxes = None
    for _mb_try in range(3):
        mboxes = sitepro_create_mailboxes(session, csrf, password, count=MAX_MAILBOXES_PER_ACCOUNT)
        if mboxes:
            break
        # Refresh CSRF sebelum coba lagi (token bisa basi).
        try:
            csrf, session = sitepro_get_csrf(session)
        except Exception:
            pass
        time.sleep(2)
    if mboxes:
        result['sitepro_email'] = mboxes[0]['email']
        result['mailboxes'] = [m['email'] for m in mboxes]
        log("Mailbox", True, f"{len(mboxes)} sender dibuat")
    else:
        log("Mailbox", False, "Gagal buat mailbox (3x)")

    # Step 7: Health-check — pastikan webmail mailbox pertama bisa login.
    # Kalau gagal, akun tetap disimpan tapi ditandai 'unhealthy' biar /fix
    # tidak memilih sender yang sebenarnya mati.
    healthy = True
    if mboxes:
        try:
            ws_session, _hc_msg = sitepro_open_webmail(session, mboxes[0]['mailbox_id'])
            healthy = bool(ws_session)
            log("Health-check", healthy, "webmail OK" if healthy else "webmail gagal login")
        except Exception as _hc_e:
            healthy = False
            log("Health-check", False, f"error: {_hc_e}")
    result['healthy'] = healthy

    # Save to DB (akun + semua sender)
    if db_cur and db_conn and mboxes:
        try:
            phpsessid = session.cookies.get('PHPSESSID', '')
            acc_id = _save_account_with_mailboxes(
                db_cur, db_conn,
                {
                    'user_id': 0, 'temp_email': temp_email,
                    'sitepro_email': mboxes[0]['email'],
                    'sitepro_password': password,
                    'phpsessid': phpsessid, 'csrf_token': csrf or '',
                    'website_id': ws_id,
                },
                mboxes,
            )
            # Tandai status akun sesuai hasil health-check.
            if acc_id and not healthy:
                try:
                    db_cur.execute(
                        "UPDATE sitepro_accounts SET status='unhealthy' WHERE id=?",
                        (acc_id,),
                    )
                    db_conn.commit()
                except Exception:
                    pass
        except Exception as e:
            print(f"[SITEPRO] DB save error: {e}")
            result['error'] = f"DB save gagal: {e}"

    result['success'] = bool(mboxes)
    return result


# ════════════════════════════════════════════════════════════════
#  PIPELINE — Multi-send /fix (1 akun → kirim ke banyak nomor)
# ════════════════════════════════════════════════════════════════

MAX_SENDS_PER_ACCOUNT = 5  # Batas compose ke WhatsApp support per mailbox (legacy)
MAX_MAILBOXES_PER_ACCOUNT = 5  # 1 akun Site.pro = maksimal 5 mailbox (sender)
SENDER_COOLDOWN_SECS = 3600    # Sender bisa dipakai lagi setelah 1 jam
SENDER_REFRESH_SECS = 600      # Session di-refresh tiap 10 menit biar tidak mati


def _last_insert_rowid(db_cur):
    """Ambil ID baris terakhir (proxy DB tidak punya .lastrowid)."""
    try:
        db_cur.execute("SELECT last_insert_rowid()")
        return db_cur.fetchone()[0]
    except Exception:
        return None


def _save_account_with_mailboxes(db_cur, db_conn, account_fields, mailboxes):
    """
    Simpan akun baru + daftar mailbox-nya.
    account_fields = dict(user_id, temp_email, sitepro_email, sitepro_password,
                          phpsessid, csrf_token, website_id)
    mailboxes = [{'mailbox_id': int, 'email': str}, ...]
    Returns account_id (int) atau None.
    """
    if not db_cur or not db_conn:
        return None
    try:
        first_mb = mailboxes[0]['mailbox_id'] if mailboxes else 0
        db_cur.execute(
            """INSERT INTO sitepro_accounts
               (user_id, temp_email, sitepro_email, sitepro_password,
                phpsessid, csrf_token, mailbox_id, website_id, status, send_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', 0)""",
            (
                account_fields.get('user_id', 0),
                account_fields.get('temp_email', ''),
                account_fields.get('sitepro_email', ''),
                account_fields.get('sitepro_password', ''),
                account_fields.get('phpsessid', ''),
                account_fields.get('csrf_token', ''),
                first_mb,
                account_fields.get('website_id', 0),
            ),
        )
        account_id = _last_insert_rowid(db_cur)
        for mb in mailboxes:
            db_cur.execute(
                """INSERT INTO sitepro_mailboxes
                   (account_id, mailbox_id, email, used, compose_count)
                   VALUES (?, ?, ?, 0, 0)""",
                (account_id, mb['mailbox_id'], mb['email']),
            )
        db_conn.commit()
        return account_id
    except Exception as e:
        print(f"[SITEPRO] _save_account_with_mailboxes error: {e}")
        return None


def _pick_available_senders(db_cur, limit=5, cooldown_secs=SENDER_COOLDOWN_SECS, server=None):
    """
    Ambil sender dari SEMUA akun yang sudah lewat cooldown (1 jam).
    Sender belum pernah dipakai (last_used_at NULL) didahulukan, lalu yang
    paling lama tidak dipakai. Tiap item membawa kredensial akun-nya supaya
    session bisa di-restore.

    server: None = semua sender; 1 = m.id <= 150; 2 = m.id > 150.

    Return list of dict: {row_id, mailbox_id, email, account_id, phpsessid,
                          sitepro_email, temp_email, sitepro_password}
    """
    if not db_cur:
        return []
    out = []
    try:
        # Filter partition sesuai pilihan server (BERBASIS RANK, bukan id mentah).
        # Server 1 = 150 sender dengan id TERKECIL (paling lama dibuat).
        # Server 2 = sisanya. Tahan terhadap autoincrement yang naik tinggi
        # setelah penghapusan.
        _s1_ids = (
            "SELECT id FROM sitepro_mailboxes "
            "WHERE mailbox_id IS NOT NULL AND mailbox_id > 0 "
            "ORDER BY id ASC LIMIT 150"
        )
        if server == 1:
            partition_clause = f" AND m.id IN ({_s1_ids})"
        elif server == 2:
            partition_clause = f" AND m.id NOT IN ({_s1_ids})"
        else:
            partition_clause = ""
        db_cur.execute(
            f"""SELECT m.id, m.mailbox_id, m.email, m.account_id,
                       a.phpsessid, a.sitepro_email, a.temp_email, a.sitepro_password
                FROM sitepro_mailboxes m
                JOIN sitepro_accounts a ON a.id = m.account_id
                WHERE m.mailbox_id IS NOT NULL AND m.mailbox_id > 0
                  AND (m.last_used_at IS NULL
                       OR m.last_used_at <= datetime('now', '-{int(cooldown_secs)} seconds'))
                  {partition_clause}
                ORDER BY (m.last_used_at IS NULL) DESC, m.last_used_at ASC, m.id ASC
                LIMIT ?""",
            (limit,),
        )
        for r in db_cur.fetchall():
            out.append({
                'row_id': r[0], 'mailbox_id': r[1], 'email': r[2], 'account_id': r[3],
                'phpsessid': r[4], 'sitepro_email': r[5], 'temp_email': r[6],
                'sitepro_password': r[7],
            })
    except Exception as e:
        print(f"[SITEPRO] _pick_available_senders error: {e}")
    return out


# Lock global: serialize fase pilih+reservasi sender supaya /fix paralel
# tidak menarik sender yang sama (anti-tabrakan).
_SENDER_RESERVE_LOCK = threading.Lock()


def _reserve_available_senders(db_cur, db_conn, limit=5,
                               cooldown_secs=SENDER_COOLDOWN_SECS, server=None):
    """Pilih DAN reservasi sender secara atomik (anti-tabrakan).

    Berbeda dari _pick_available_senders yang hanya membaca, fungsi ini:
      1. Mengunci _SENDER_RESERVE_LOCK (serialize antar thread di pool global).
      2. SELECT sender siap-pakai (cooldown lewat / belum pernah dipakai),
         dengan filter partisi server berbasis rank.
      3. LANGSUNG set last_used_at = now untuk sender terpilih (reservasi) +
         commit, sebelum lock dilepas.

    Efeknya: begitu sender terpilih, cooldown langsung aktif, jadi pemanggil
    lain (run /fix paralel) tidak akan memilih sender yang sama. Kalau
    pengiriman nanti gagal, sender tetap "terpakai" untuk window cooldown —
    trade-off aman demi mencegah tabrakan.

    Return list of dict sama seperti _pick_available_senders.
    """
    if not db_cur or not db_conn:
        return []
    out = []
    with _SENDER_RESERVE_LOCK:
        try:
            # Partisi berbasis rank (lihat _pick_available_senders).
            _s1_ids = (
                "SELECT id FROM sitepro_mailboxes "
                "WHERE mailbox_id IS NOT NULL AND mailbox_id > 0 "
                "ORDER BY id ASC LIMIT 150"
            )
            if server == 1:
                partition_clause = f" AND m.id IN ({_s1_ids})"
            elif server == 2:
                partition_clause = f" AND m.id NOT IN ({_s1_ids})"
            else:
                partition_clause = ""

            db_cur.execute(
                f"""SELECT m.id, m.mailbox_id, m.email, m.account_id,
                           a.phpsessid, a.sitepro_email, a.temp_email, a.sitepro_password
                    FROM sitepro_mailboxes m
                    JOIN sitepro_accounts a ON a.id = m.account_id
                    WHERE m.mailbox_id IS NOT NULL AND m.mailbox_id > 0
                      AND (m.last_used_at IS NULL
                           OR m.last_used_at <= datetime('now', '-{int(cooldown_secs)} seconds'))
                      {partition_clause}
                    ORDER BY (m.last_used_at IS NULL) DESC, m.last_used_at ASC, m.id ASC
                    LIMIT ?""",
                (limit,),
            )
            rows = db_cur.fetchall()
            picked_ids = [r[0] for r in rows]

            # Reservasi atomik: set cooldown sekarang juga.
            if picked_ids:
                placeholders = ",".join(["?"] * len(picked_ids))
                db_cur.execute(
                    f"""UPDATE sitepro_mailboxes
                        SET last_used_at = CURRENT_TIMESTAMP
                        WHERE id IN ({placeholders})""",
                    picked_ids,
                )
                db_conn.commit()

            for r in rows:
                out.append({
                    'row_id': r[0], 'mailbox_id': r[1], 'email': r[2], 'account_id': r[3],
                    'phpsessid': r[4], 'sitepro_email': r[5], 'temp_email': r[6],
                    'sitepro_password': r[7],
                })
        except Exception as e:
            print(f"[SITEPRO] _reserve_available_senders error: {e}")
    return out


def _mark_sender_composed(db_cur, db_conn, mailbox_row_id, used_for=None):
    """Catat 1 compose: compose_count++, set last_used_at=now (mulai cooldown)."""
    if not db_cur or not db_conn or not mailbox_row_id:
        return
    try:
        db_cur.execute(
            """UPDATE sitepro_mailboxes
               SET compose_count = COALESCE(compose_count, 0) + 1,
                   used = 1, used_for = ?,
                   last_used_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (used_for or '', mailbox_row_id),
        )
        db_cur.execute(
            """UPDATE sitepro_accounts
               SET send_count = COALESCE(send_count, 0) + 1,
                   last_used_at = CURRENT_TIMESTAMP
               WHERE id = (SELECT account_id FROM sitepro_mailboxes WHERE id = ?)""",
            (mailbox_row_id,),
        )
        db_conn.commit()
    except Exception as e:
        print(f"[SITEPRO] _mark_sender_composed error: {e}")


def refresh_all_sessions(db_cur, db_conn):
    """
    Dipanggil periodik (tiap 10 menit) oleh daemon di bot.
    Restore session tiap akun + ping load-init biar PHPSESSID tidak mati.
    Update phpsessid di DB kalau berubah. Return (ok_count, total).
    """
    if not db_cur:
        return (0, 0)
    rows = []
    try:
        db_cur.execute(
            """SELECT id, phpsessid, sitepro_email, temp_email, sitepro_password
               FROM sitepro_accounts
               WHERE sitepro_email IS NOT NULL AND sitepro_email != ''"""
        )
        rows = db_cur.fetchall()
    except Exception as e:
        print(f"[SITEPRO] refresh_all_sessions query error: {e}")
        return (0, 0)

    ok = 0
    for r in rows:
        acct_id, phpsessid, sp_email, tmp_email, pw = r
        try:
            sess, valid = sitepro_restore_session(
                phpsessid, email=sp_email or tmp_email, password=pw,
            )
            if valid and sess:
                ok += 1
                new_sid = sess.cookies.get('PHPSESSID', '')
                if new_sid and new_sid != phpsessid and db_conn:
                    try:
                        db_cur.execute(
                            "UPDATE sitepro_accounts SET phpsessid = ? WHERE id = ?",
                            (new_sid, acct_id),
                        )
                        db_conn.commit()
                    except Exception:
                        pass
        except Exception as e:
            print(f"[SITEPRO] refresh akun #{acct_id} gagal: {e}")
        time.sleep(0.5)
    return (ok, len(rows))


def run_fix_multi_pipeline(
    numbers,
    captcha_key="",
    db_cur=None,
    db_conn=None,
    solver_type="browser",
    reply_wait=240,
    progress_cb=None,
    max_sends_per_account=MAX_SENDS_PER_ACCOUNT,
    message_template=None,
    server=None,
):
    """
    Pipeline /fix versi 3 (model 5-mailbox):
      - 1 akun Site.pro = maksimal 5 mailbox (sender). Tiap nomor dikirim dari
        mailbox/sender yang BERBEDA.
      - Coba reuse akun yang masih punya mailbox belum terpakai (used=0).
      - Kalau habis / tidak ada -> register akun baru + buat 5 mailbox sekaligus.
      - Kirim 1 email per nomor dari mailbox berbeda; tandai mailbox terpakai.
      - Polling inbox tiap mailbox -> deteksi balasan baru mengandung 'whatsapp',
        stop begitu balasan pertama masuk.

    Returns dict (+'account_reused', +'sends_left', +'newest_reply').
    """
    out = {
        'success': False,
        'account_ready': False,
        'account_reused': False,
        'sends_left': 0,
        'numbers': [],
        'total_sent': 0,
        'total_replied': 0,
        'replies': [],
        'newest_reply': None,
        'error': None,
        'steps': [],
    }

    def step(ok, label):
        out['steps'].append(("OK " if ok else "X  ") + label)

    def _cb(stage, data):
        if progress_cb:
            try:
                progress_cb(stage, data)
            except Exception:
                pass

    # Normalisasi nomor di awal
    def _norm(n):
        c = str(n).strip().replace("+", "").replace("-", "").replace(" ", "")
        if not c.startswith("62") and c.startswith("0"):
            c = "62" + c[1:]
        return c

    numbers = [_norm(n) for n in numbers]

    def _wa_message(nomor_clean):
        # Kalau user kasih template custom via /set, pakai itu (replace {nomor}).
        if message_template:
            try:
                tpl = str(message_template)
                if "{nomor}" in tpl:
                    return tpl.replace("{nomor}", nomor_clean)
                # Kalau placeholder hilang (harusnya tidak terjadi karena divalidasi di /set),
                # tetap append nomor di akhir biar pesan tetap nyambung.
                return tpl + f"\n\n+{nomor_clean}"
            except Exception:
                pass
        return (
            "Olá, Equipe de Suporte do WhatsApp,\n\n"
            "Estou entrando em contato porque não consigo fazer login na minha conta do WhatsApp. "
            "Toda vez que tento, recebo a seguinte mensagem: \"Login Not Available Right Now.\"\n\n"
            "Já tentei reiniciar o aparelho, verificar minha conexão com a internet e tentar novamente "
            "várias vezes, mas o problema continua sem solução.\n\n"
            "Essa conta é muito importante para mim, pois contém meus grupos de estudo da universidade, "
            "materiais de aula e comunicações acadêmicas importantes. A perda de acesso está afetando meus estudos.\n\n"
            "Número de telefone : "
            f"+{nomor_clean}\n\n"
            "Agradeceria muito se pudessem me ajudar a restaurar o acesso o mais rápido possível. "
            "Obrigado pela atenção e pelo suporte.\n\n"
            "Atenciosamente,\n"
            "DikZz"
        )

    # ── Session cache per akun (restore sekali, pakai untuk semua mailbox-nya) ─
    session_cache = {}

    def _session_for(sender):
        aid = sender['account_id']
        if aid in session_cache:
            return session_cache[aid]
        sess, ok = sitepro_restore_session(
            sender.get('phpsessid'),
            email=sender.get('sitepro_email') or sender.get('temp_email'),
            password=sender.get('sitepro_password'),
        )
        if ok and sess:
            session_cache[aid] = sess
            return sess
        return None

    # ── Fallback: register akun baru + 5 mailbox, kembalikan list sender ──
    def _register_new_account():
        temp_email, _d = emailqu_get_temp_email()
        if not temp_email:
            return None, "Gagal buat alamat sementara"
        name = _random_name()
        password = _random_password()

        if solver_type == "browser":
            ok, sess, msg = sitepro_register_browser(temp_email, name, password, headless=False, timeout=300)
            if not ok:
                return None, f"register: {msg}"
        else:
            sess = _sitepro_session()
            csrf, sess = sitepro_get_csrf(sess)
            if not csrf:
                return None, "Gagal ambil CSRF"
            captcha_token = solve_recaptcha(
                captcha_key, "6LeKkToUAAAAAHd9EiB6BaSXazFQ5CFIxmyLFm1Z",
                "https://site.pro/id/", solver_type=solver_type,
            )
            if not captcha_token:
                return None, "Gagal solve captcha"
            ok, sess, msg = sitepro_register(sess, temp_email, name, password, csrf, captcha_token)
            if not ok:
                return None, f"register: {msg}"
            body = emailqu_poll_inbox(temp_email, timeout=90, interval=3)
            otp = emailqu_extract_otp(body) if body else None
            if not otp:
                return None, "Timeout/Gagal OTP"
            ok, sess, msg = sitepro_confirm_code(sess, otp)
            if not ok:
                return None, f"konfirmasi: {msg}"

        csrf, _ = sitepro_get_csrf(sess)
        ws_id, _domain, msg = sitepro_create_website(sess, csrf)
        if not ws_id:
            return None, f"siapkan akun: {msg}"

        mboxes = sitepro_create_mailboxes(sess, csrf, password, count=MAX_MAILBOXES_PER_ACCOUNT)
        if not mboxes:
            return None, "Gagal buat mailbox"

        phpsessid = sess.cookies.get('PHPSESSID', '')
        acct_id = _save_account_with_mailboxes(
            db_cur, db_conn,
            {
                'user_id': 0, 'temp_email': temp_email,
                'sitepro_email': mboxes[0]['email'],
                'sitepro_password': password,
                'phpsessid': phpsessid, 'csrf_token': csrf or '',
                'website_id': ws_id,
            },
            mboxes,
        )
        if acct_id:
            session_cache[acct_id] = sess  # session sudah hidup, pakai ulang

        new_senders = []
        if acct_id and db_cur:
            db_cur.execute(
                "SELECT id, mailbox_id, email FROM sitepro_mailboxes WHERE account_id = ? ORDER BY id ASC",
                (acct_id,),
            )
            for m in db_cur.fetchall():
                new_senders.append({
                    'row_id': m[0], 'mailbox_id': m[1], 'email': m[2],
                    'account_id': acct_id, 'phpsessid': phpsessid,
                    'sitepro_email': mboxes[0]['email'], 'temp_email': temp_email,
                    'sitepro_password': password,
                })
        else:
            for m in mboxes:
                new_senders.append({
                    'row_id': None, 'mailbox_id': m['mailbox_id'], 'email': m['email'],
                    'account_id': acct_id, 'phpsessid': phpsessid,
                    'sitepro_email': mboxes[0]['email'], 'temp_email': temp_email,
                    'sitepro_password': password,
                })
        if not new_senders:
            return None, "Akun dibuat tapi tidak ada sender tersimpan"
        return new_senders, None

    # ── Kirim: 1 nomor = 1 sender berbeda (cooldown 1 jam) ────────────────
    nums_status = []
    used_sends = []  # {nomor, mailbox_id, row_id, ws_session, account_id}
    remaining = list(numbers)

    # Reservasi sender secara ATOMIK (lock + langsung set cooldown), supaya
    # /fix paralel tidak menarik sender yang sama. Filter partisi server 1/2.
    sender_queue = _reserve_available_senders(
        db_cur, db_conn, limit=len(remaining), server=server
    ) if (db_cur and db_conn) else []
    out['account_reused'] = len(sender_queue) > 0
    if sender_queue:
        print(f"[SITEPRO] Pakai {len(sender_queue)} sender dari pool (reuse, cooldown ok)")

    first_ready = False
    abort = False
    while remaining and not abort:
        nomor = remaining.pop(0)

        # Dapatkan 1 sender (dari pool, atau register akun baru sebagai fallback)
        sender = None
        while sender is None:
            if sender_queue:
                sender = sender_queue.pop(0)
            else:
                new_senders, err = _register_new_account()
                if not new_senders:
                    if not first_ready:
                        out['error'] = err
                        step(False, f"siapkan sender: {err}")
                    # Tandai nomor ini + sisanya gagal
                    nums_status.append({'nomor': nomor, 'sent': False,
                                        'sent_msg': err or "Tidak ada sender", 'replied': False})
                    for n in remaining:
                        nums_status.append({'nomor': n, 'sent': False,
                                            'sent_msg': err or "Tidak ada sender", 'replied': False})
                    remaining = []
                    abort = True
                    break
                sender_queue.extend(new_senders)
        if abort or sender is None:
            break

        if not first_ready:
            first_ready = True
            out['account_ready'] = True
            step(True, "pakai sender pool" if out['account_reused'] else "siapkan sender baru")
            _cb('account_ready', {'reused': out['account_reused']})

        sess = _session_for(sender)
        if not sess:
            nums_status.append({'nomor': nomor, 'sent': False,
                                'sent_msg': "Session sender tidak valid", 'replied': False})
            continue

        ws_session, msg = sitepro_open_webmail(sess, sender['mailbox_id'])
        if not ws_session:
            nums_status.append({'nomor': nomor, 'sent': False,
                                'sent_msg': f"login kotak masuk gagal: {msg}", 'replied': False})
            continue

        token, from_id, compose_id = roundcube_get_compose_token(ws_session)
        if not token:
            nums_status.append({'nomor': nomor, 'sent': False,
                                'sent_msg': "Gagal ambil token kirim", 'replied': False})
            continue

        ok, smsg = roundcube_send_email(
            ws_session, token, from_id, compose_id,
            "support@support.whatsapp.com", _wa_message(nomor),
        )
        nums_status.append({'nomor': nomor, 'sent': bool(ok),
                            'sent_msg': smsg or "", 'replied': False})
        if ok:
            out['total_sent'] += 1
            _mark_sender_composed(db_cur, db_conn, sender.get('row_id'), used_for=nomor)
            used_sends.append({
                'nomor': nomor, 'mailbox_id': sender['mailbox_id'],
                'row_id': sender.get('row_id'), 'ws_session': ws_session,
                'account_id': sender['account_id'],
            })
        _cb('sent', {'nomor': nomor, 'ok': ok})
        time.sleep(1)

    out['numbers'] = nums_status

    # ── Polling balasan dari tiap mailbox sender ─────────────────────────
    # Snapshot UID terbesar tiap mailbox SEBELUM cek balasan.
    pre_uid = {}
    for us in used_sends:
        try:
            pre_msgs, _e = roundcube_read_inbox(us['ws_session'], timeout=15, fetch_body=False)
            pre_uid[us['mailbox_id']] = max((int(m.get('uid', 0)) for m in pre_msgs), default=0)
        except Exception:
            pre_uid[us['mailbox_id']] = 0

    deadline = time.time() + reply_wait
    replies_seen = set()
    # Set mailbox yang sudah dapat balasan (1 reply per sender = cukup).
    matched_mailboxes = set()

    while used_sends and time.time() < deadline:
        for us in used_sends:
            # Sudah dapet balasan sebelumnya untuk sender ini? Skip.
            if us['mailbox_id'] in matched_mailboxes:
                continue
            msgs, _err = roundcube_read_inbox(us['ws_session'], timeout=15)
            if not msgs:
                continue
            base = pre_uid.get(us['mailbox_id'], 0)
            msgs_sorted = sorted(msgs, key=lambda x: int(x.get('uid', 0)))
            for m in msgs_sorted:
                try:
                    uid_int = int(m.get('uid', 0))
                except Exception:
                    continue
                if uid_int <= base:
                    continue
                key = f"{us['mailbox_id']}:{uid_int}"
                if key in replies_seen:
                    continue
                subj = (m.get('subject') or '').lower()
                body = (m.get('body') or '').lower()
                frm = (m.get('from') or '').lower()
                # Balasan WA datang DARI ...@whatsapp.com; subjek/isi (Portugis)
                # belum tentu memuat kata "whatsapp". Cek pengirim juga supaya
                # balasan tidak terlewat (bikin nunggu sampai timeout).
                if ('whatsapp' not in subj and 'whatsapp' not in body
                        and 'whatsapp' not in frm):
                    continue
                replies_seen.add(key)
                reply_obj = {
                    'uid': str(uid_int),
                    'subject': m.get('subject', ''),
                    'from': m.get('from', ''),
                    'body': m.get('body', ''),
                    'matched_nomor': us['nomor'],
                }
                out['replies'].append(reply_obj)
                out['newest_reply'] = reply_obj
                for ns in nums_status:
                    if ns['nomor'] == us['nomor'] and not ns['replied']:
                        ns['replied'] = True
                        break
                _cb('reply', {'uid': str(uid_int), 'matched': us['nomor']})
                # Hentikan polling untuk SENDER ini saja (1 reply = cukup),
                # lalu lanjut polling sender lain.
                matched_mailboxes.add(us['mailbox_id'])
                break
        # Kalau semua sender sudah dapet balasan, selesai lebih awal.
        if len(matched_mailboxes) >= len(used_sends):
            break
        time.sleep(5)

    out['total_replied'] = sum(1 for ns in nums_status if ns['replied'])

    # sends_left = jumlah sender di SELURUH pool yang siap pakai sekarang
    # (belum pernah dipakai atau sudah lewat cooldown 1 jam).
    out['sends_left'] = 0
    try:
        if db_cur:
            db_cur.execute(
                f"""SELECT COUNT(*) FROM sitepro_mailboxes
                    WHERE mailbox_id IS NOT NULL AND mailbox_id > 0
                      AND (last_used_at IS NULL
                           OR last_used_at <= datetime('now', '-{int(SENDER_COOLDOWN_SECS)} seconds'))"""
            )
            out['sends_left'] = db_cur.fetchone()[0]
    except Exception:
        pass

    out['success'] = out['total_sent'] > 0
    return out
