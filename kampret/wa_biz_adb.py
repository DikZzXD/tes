"""
WA Business ADB Registration Ã¢â‚¬â€ 3-Layer Multi-Emulator
======================================================
3-Layer flow:
  Layer 1: Telethon SendCodeRequest (trigger call/OTP via Telegram API)
  Wait:    ~60 seconds
  Layer 2: WA Business ADB Ã¢â€ â€™ SMS verification

Features:
  - Auto-detect country from phone number
  - Multi-emulator parallel (1 number per LDPlayer)
  - Smart SMS flow: Send SMS Ã¢â€ â€™ wait 1-3 min Ã¢â€ â€™ skip if rate-limited
  - Telegram bot /login integration

Author: ENI x LO
"""

import subprocess
import time
import json
import logging
import xml.etree.ElementTree as ET
import re
import sys
import os
import asyncio
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Ã¢â€â‚¬Ã¢â€â‚¬ Logging Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("wa_biz_adb.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# Ã¢â€â‚¬Ã¢â€â‚¬ Config Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
ADB_PATH = os.getenv("ADB_PATH", r"C:\LDPlayer\LDPlayer9\adb.exe")
LDCONSOLE = os.getenv("LDCONSOLE", r"C:\LDPlayer\LDPlayer9\ldconsole.exe")
WA_PACKAGE = "com.whatsapp.w4b"
STEP_DELAY = float(os.getenv("STEP_DELAY", "1"))
MAX_WAIT_UI = int(os.getenv("MAX_WAIT_UI", "10"))
LAYER1_WAIT = int(os.getenv("LAYER1_WAIT", "30"))  # wait after call API (was 60)
SMS_WAIT_MAX = int(os.getenv("SMS_WAIT_MAX", "120"))  # max wait for SMS (2 min)
OUTPUT_DIR = Path("wa_biz_output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Ã¢â€â‚¬Ã¢â€â‚¬ Telethon API Pool (same as dik.py) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
TG_API_POOL = [
    (2040,     "b18441a1ff607e10a989891a5462e627"),
    (37365889, "78969cf319a8f18260369f10d0ae053a"),
    (39191050, "2ee2a563b5e174e6c5f8009992722284"),
    (32251747, "1383994847f0c19770ecc2a7ac8220b5"),
]
_tg_api_idx = [0]

def _next_tg_api():
    idx = _tg_api_idx[0] % len(TG_API_POOL)
    _tg_api_idx[0] += 1
    return TG_API_POOL[idx]


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# COUNTRY AUTO-DETECTION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def detect_country(phone: str) -> tuple[str, str]:
    """
    Detect country name and dial code from phone number.
    Returns (country_name, local_number_without_code).
    """
    import phonenumbers

    # Normalize: strip +, spaces, dashes
    clean = re.sub(r'[^0-9]', '', phone)

    # Try parse with + prefix
    try:
        parsed = phonenumbers.parse(f"+{clean}", None)
        country_code = parsed.country_code
        national = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        )
        # Get country name from region code
        region = phonenumbers.region_code_for_number(parsed)

        # Map region code to WA Business country name
        REGION_TO_NAME = {
            "ID": "Indonesia", "HT": "Haiti", "TJ": "Tajikistan",
            "US": "United States", "GB": "United Kingdom",
            "DZ": "Algeria", "KH": "Cambodia", "IN": "India",
            "PH": "Philippines", "MY": "Malaysia", "SG": "Singapore",
            "TH": "Thailand", "VN": "Vietnam", "BR": "Brazil",
            "RU": "Russia", "DE": "Germany", "FR": "France",
            "IT": "Italy", "ES": "Spain", "NL": "Netherlands",
            "AU": "Australia", "JP": "Japan", "KR": "South Korea",
            "CN": "China", "MX": "Mexico", "AR": "Argentina",
            "CO": "Colombia", "CL": "Chile", "PE": "Peru",
            "EG": "Egypt", "SA": "Saudi Arabia", "AE": "United Arab Emirates",
            "TR": "Turkey", "PK": "Pakistan", "BD": "Bangladesh",
            "NG": "Nigeria", "KE": "Kenya", "ZA": "South Africa",
            "UA": "Ukraine", "PL": "Poland", "RO": "Romania",
            "CZ": "Czech Republic", "HU": "Hungary", "SE": "Sweden",
            "NO": "Norway", "DK": "Denmark", "FI": "Finland",
            "PT": "Portugal", "GR": "Greece", "IL": "Israel",
            "NZ": "New Zealand", "CA": "Canada", "IE": "Ireland",
            "AT": "Austria", "CH": "Switzerland", "BE": "Belgium",
            "TW": "Taiwan", "HK": "Hong Kong",
        }

        # Fallback: use pycountry-style name from phonenumbers
        if region and region in REGION_TO_NAME:
            country_name = REGION_TO_NAME[region]
        elif region:
            # Try geocoder
            try:
                from phonenumbers import geocoder
                country_name = geocoder.description_for_number(parsed, "en")
                if not country_name:
                    country_name = region
            except Exception:
                country_name = region
        else:
            country_name = "Unknown"

        # Strip country code from number for WA input
        national_digits = re.sub(r'[^0-9]', '', national)
        # Also try stripping leading 0 if present
        if national_digits.startswith("0"):
            national_digits = national_digits[1:]

        log.info("Phone %s Ã¢â€ â€™ Country: %s (+%d), Local: %s",
                 phone, country_name, country_code, national_digits)
        return country_name, national_digits

    except Exception as e:
        log.warning("Failed to parse phone %s: %s Ã¢â‚¬â€ fallback manual", phone, e)

    # Manual fallback for common prefixes
    MANUAL_MAP = [
        ("509", "Haiti"), ("62", "Indonesia"), ("992", "Tajikistan"),
        ("1", "United States"), ("44", "United Kingdom"),
        ("213", "Algeria"), ("855", "Cambodia"), ("91", "India"),
    ]
    for prefix, name in MANUAL_MAP:
        if clean.startswith(prefix):
            local = clean[len(prefix):]
            if local.startswith("0"):
                local = local[1:]
            return name, local

    return "Unknown", clean


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DATA CLASSES
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

@dataclass
class StepResult:
    step: str
    success: bool
    ui_texts: list[str] = field(default_factory=list)
    resource_ids: list[str] = field(default_factory=list)
    screenshot: str = ""
    xml_dump: str = ""
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class EmulatorSession:
    index: int
    name: str
    device_id: str
    phone_number: str
    country: str = ""
    local_number: str = ""
    steps: list[StepResult] = field(default_factory=list)
    layer1_result: str = ""


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# ADB WRAPPER
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def adb(device_id: str, args: list[str], timeout: int = 30) -> str:
    cmd = [ADB_PATH, "-s", device_id, *args]
    log.debug("RUN: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0 and result.stderr.strip():
            log.warning("ADB stderr [%s]: %s", device_id, result.stderr.strip()[:200])
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ADB timeout ({timeout}s) on {device_id}")
    except FileNotFoundError:
        raise RuntimeError(f"ADB not found at: {ADB_PATH}")


def tap(device_id: str, x: int, y: int):
    adb(device_id, ["shell", "input", "tap", str(x), str(y)])
    log.info("[%s] TAP (%d, %d)", device_id, x, y)


def input_text(device_id: str, value: str):
    """Input text via ADB. Try clipboard paste first (more reliable), fallback to input text."""
    # Method 1: ADB input text
    safe = value.replace(" ", "%s")
    adb(device_id, ["shell", "input", "text", safe])
    log.info("[%s] INPUT: %s", device_id, value)


def input_text_clipboard(device_id: str, value: str):
    """Input text via ADB clipboard broadcast (works on emulators where input text fails)."""
    try:
        # Set clipboard content
        adb(device_id, ["shell", "am", "broadcast", "-a", "clipper.set", "-e", "text", value])
        time.sleep(0.3)
        # Paste: Ctrl+V
        adb(device_id, ["shell", "input", "keyevent", "279"])  # KEYCODE_PASTE
        log.info("[%s] CLIPBOARD PASTE: %s", device_id, value)
    except Exception:
        # Fallback: type character by character
        for ch in value:
            if ch.isdigit():
                adb(device_id, ["shell", "input", "text", ch])
                time.sleep(0.05)
        log.info("[%s] CHAR-BY-CHAR INPUT: %s", device_id, value)


def keyevent(device_id: str, code: int):
    adb(device_id, ["shell", "input", "keyevent", str(code)])


def _clear_text_field(device_id: str):
    """Clear text in focused field. Works on LDPlayer."""
    # Method 1: Triple-tap to select all, then delete
    # (Triple-tap selects all text in Android EditText)
    adb(device_id, ["shell", "input", "tap", "439", "513"])
    time.sleep(0.05)
    adb(device_id, ["shell", "input", "tap", "439", "513"])
    time.sleep(0.05)
    adb(device_id, ["shell", "input", "tap", "439", "513"])
    time.sleep(0.1)
    adb(device_id, ["shell", "input", "keyevent", "67"])  # DEL selected text
    time.sleep(0.1)
    # Method 2: Move to end + batch backspace (backup)
    adb(device_id, ["shell", "input", "keyevent", "123"])  # MOVE_END
    time.sleep(0.1)
    # Send all DEL keys in one shell command (fast!)
    del_cmd = " && ".join(["input keyevent 67"] * 20)
    adb(device_id, ["shell", f"({del_cmd})"])
    time.sleep(0.2)

def _safe_fn(name: str) -> str:
    """Sanitize filename for Windows (replace : and other bad chars)."""
    return re.sub(r'[:<>|"?*]', '_', name)


def screenshot(device_id: str, filename: str) -> str:
    remote = "/sdcard/wa_screen.png"
    local = str(OUTPUT_DIR / _safe_fn(filename))
    adb(device_id, ["shell", "screencap", "-p", remote])
    adb(device_id, ["pull", remote, local])
    log.info("[%s] Screenshot: %s", device_id, local)
    return local


def dump_ui(device_id: str, filename: str = "window.xml") -> Path:
    remote = "/sdcard/window.xml"
    local = OUTPUT_DIR / _safe_fn(filename)
    adb(device_id, ["shell", "uiautomator", "dump", remote])
    adb(device_id, ["pull", remote, str(local)])
    return local


def parse_ui_nodes(xml_path: Path) -> list[dict]:
    tree = ET.parse(xml_path)
    nodes = []
    for node in tree.iter("node"):
        a = node.attrib
        nodes.append({
            "text": a.get("text", ""),
            "resource_id": a.get("resource-id", ""),
            "class": a.get("class", ""),
            "content_desc": a.get("content-desc", ""),
            "bounds": a.get("bounds", ""),
            "clickable": a.get("clickable", "false"),
            "enabled": a.get("enabled", "true"),
        })
    return nodes


def find_node_by_text(nodes: list[dict], keyword: str) -> Optional[dict]:
    """Case-insensitive partial match."""
    kw = keyword.lower()
    for n in nodes:
        if kw in n["text"].lower():
            return n
    return None


def find_node_by_exact_text(nodes: list[dict], text: str) -> Optional[dict]:
    """Exact text match (case-insensitive, stripped)."""
    for n in nodes:
        if n["text"].strip().lower() == text.strip().lower():
            return n
    return None


def find_node_by_id(nodes: list[dict], resource_id: str) -> Optional[dict]:
    for n in nodes:
        if n["resource_id"] == resource_id:
            return n
    return None


def get_bounds_center(bounds_str: str) -> tuple[int, int]:
    m = re.findall(r'\[(\d+),(\d+)\]', bounds_str)
    if len(m) < 2:
        raise ValueError(f"Invalid bounds: {bounds_str}")
    x1, y1 = int(m[0][0]), int(m[0][1])
    x2, y2 = int(m[1][0]), int(m[1][1])
    return (x1 + x2) // 2, (y1 + y2) // 2


def tap_node(device_id: str, node: dict):
    cx, cy = get_bounds_center(node["bounds"])
    tap(device_id, cx, cy)


def wait_for_text(device_id: str, keyword: str, timeout: int = None, dump_name: str = "wait.xml") -> Optional[dict]:
    timeout = timeout or MAX_WAIT_UI
    start = time.time()
    while time.time() - start < timeout:
        try:
            xml_path = dump_ui(device_id, dump_name)
            nodes = parse_ui_nodes(xml_path)
            node = find_node_by_text(nodes, keyword)
            if node:
                return node
        except Exception:
            pass
        time.sleep(1.5)
    return None


def wait_for_id(device_id: str, resource_id: str, timeout: int = None, dump_name: str = "wait.xml") -> Optional[dict]:
    timeout = timeout or MAX_WAIT_UI
    start = time.time()
    while time.time() - start < timeout:
        try:
            xml_path = dump_ui(device_id, dump_name)
            nodes = parse_ui_nodes(xml_path)
            node = find_node_by_id(nodes, resource_id)
            if node:
                return node
        except Exception:
            pass
        time.sleep(1.5)
    return None


def capture_full_state(device_id: str, step_name: str) -> StepResult:
    result = StepResult(step=step_name, success=True)
    try:
        safe_name = step_name.replace(" ", "_").lower()
        ts = datetime.now().strftime("%H%M%S")
        ss_file = f"{device_id}_{safe_name}_{ts}.png"
        result.screenshot = screenshot(device_id, ss_file)
        xml_file = f"{device_id}_{safe_name}_{ts}.xml"
        xml_path = dump_ui(device_id, xml_file)
        result.xml_dump = str(xml_path)
        nodes = parse_ui_nodes(xml_path)
        for n in nodes:
            if n["text"]:
                result.ui_texts.append(n["text"])
            if n["resource_id"]:
                result.resource_ids.append(n["resource_id"])
        log.info("[%s] STEP '%s' Ã¢â‚¬â€ texts: %s", device_id, step_name, result.ui_texts[:8])
    except Exception as e:
        result.error = str(e)
        result.success = False
        log.error("[%s] capture failed: %s", device_id, e)
    return result


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# LDPLAYER HELPERS
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def ldconsole_cmd(args: list[str], timeout: int = 30) -> str:
    cmd = [LDCONSOLE, *args]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return result.stdout.strip()


def get_running_emulators() -> list[dict]:
    raw = ldconsole_cmd(["list2"])
    emulators = []
    for line in raw.splitlines():
        parts = line.split(",")
        if len(parts) >= 6:
            idx = int(parts[0])
            name = parts[1]
            is_running = int(parts[4]) == 1
            if is_running:
                emulators.append({"index": idx, "name": name})
    return emulators


def get_adb_devices() -> list[str]:
    cmd = [ADB_PATH, "devices"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    devices = []
    for line in result.stdout.splitlines():
        if "\tdevice" in line:
            dev_id = line.split("\t")[0].strip()
            devices.append(dev_id)
    return devices


def _normalize_port(dev: str) -> str:
    """Normalize device ID to a canonical port for dedup.
    LDPlayer ADB: emulator-5554 uses ADB port 5555, emulator-5556 uses 5557, etc.
    So 127.0.0.1:5555 == emulator-5554 (console_port = adb_port - 1).
    """
    if dev.startswith("emulator-"):
        console_port = int(dev.split("-")[1])
        return str(console_port)
    elif ":" in dev:
        adb_port = int(dev.split(":")[1])
        # ADB port = console port + 1
        return str(adb_port - 1)
    return dev


def ensure_emulators(count: int) -> list[str]:
    """Ensure N emulators are running. Launch if needed. Return device IDs."""
    running = get_running_emulators()
    running_indices = {e["index"] for e in running}
    log.info("Currently running: %s, need %d", running_indices, count)

    for i in range(count):
        if i not in running_indices:
            log.info("Launching LDPlayer index %d...", i)
            try:
                ldconsole_cmd(["launch", "--index", str(i)], timeout=10)
                time.sleep(3)
            except Exception as e:
                log.warning("Failed to launch index %d: %s", i, e)

    if len(running_indices) < count:
        log.info("Waiting 40s for emulators to boot...")
        time.sleep(40)

    # Connect ADB to all instances
    for i in range(count):
        port = 5555 + i * 2
        try:
            subprocess.run(
                [ADB_PATH, "connect", f"127.0.0.1:{port}"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception:
            pass

    time.sleep(3)
    devices = get_adb_devices()
    log.info("ADB devices available: %s", devices)
    return devices[:count]


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# LAYER 1: TELEGRAM SENDCODEREQUEST
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

async def layer1_send_code(phone: str) -> dict:
    """
    Send Telegram verification code to phone via SendCodeRequest.
    This triggers a call/app notification to the number.
    Returns: {"status": "sent"|"error"|"flood", "detail": "..."}
    """
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.auth import SendCodeRequest
        from telethon.tl.types import CodeSettings
        from telethon.errors import (
            PhoneNumberBannedError, PhoneNumberInvalidError,
            PhoneNumberFloodError, FloodWaitError
        )
    except ImportError:
        log.error("Telethon not installed!")
        return {"status": "error", "detail": "Telethon not installed"}

    clean = re.sub(r'[^0-9]', '', phone)
    if not clean.startswith("+"):
        clean = "+" + clean

    api_id, api_hash = _next_tg_api()
    devices = [
        "Samsung SM-S918B", "Pixel 7 Pro", "Pixel 8",
        "Xiaomi Redmi Note 12", "OnePlus 11",
    ]
    sys_versions = ["Android 13", "Android 14", "Android 12"]

    client = TelegramClient(
        StringSession(), api_id, api_hash,
        device_model=random.choice(devices),
        system_version=random.choice(sys_versions),
        app_version=f"1.{random.randint(0,9)}.{random.randint(0,99)}",
    )
    client.flood_sleep_threshold = 0

    try:
        await asyncio.wait_for(client.connect(), timeout=10)
        result = await asyncio.wait_for(client(SendCodeRequest(
            phone_number=clean, api_id=api_id,
            api_hash=api_hash, settings=CodeSettings(),
        )), timeout=15)

        otp_type = type(result.type).__name__ if result.type else "Unknown"
        log.info("[LAYER1] SendCodeRequest OK for %s Ã¢â‚¬â€ type: %s", phone, otp_type)
        return {"status": "sent", "detail": f"Code sent via {otp_type}"}

    except PhoneNumberBannedError:
        return {"status": "banned", "detail": "Phone number banned on Telegram"}
    except PhoneNumberInvalidError:
        return {"status": "invalid", "detail": "Invalid phone number format"}
    except PhoneNumberFloodError:
        return {"status": "flood", "detail": "Phone number flood (too many requests)"}
    except FloodWaitError as e:
        return {"status": "flood", "detail": f"FloodWait {e.seconds}s"}
    except asyncio.TimeoutError:
        return {"status": "timeout", "detail": "Connection timeout"}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def run_layer1(phone: str) -> dict:
    """Sync wrapper for layer1_send_code."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(layer1_send_code(phone))
        loop.close()
        return result
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# LAYER 2: WA BUSINESS ADB FLOW (SMS FOCUS)
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def select_country_in_wa(device_id: str, country_name: str) -> bool:
    """Select country in WA Business registration screen. Returns True if successful."""
    log.info("[%s] Selecting country: %s", device_id, country_name)

    # Check if already correct
    xml_path = dump_ui(device_id, f"{device_id}_country_check.xml")
    nodes = parse_ui_nodes(xml_path)
    for n in nodes:
        if "country code for" in n.get("content_desc", "").lower():
            if country_name.lower() in n.get("content_desc", "").lower():
                log.info("[%s] Country already set to %s, skipping", device_id, country_name)
                return True
            break

    # Find and tap country spinner
    country_node = None
    for n in nodes:
        if n.get("resource_id") == "com.whatsapp.w4b:id/registration_country":
            country_node = n
            break
    if not country_node:
        for n in nodes:
            if "country code for" in n.get("content_desc", "").lower():
                country_node = n
                break
    if not country_node:
        for n in nodes:
            if n["text"] in ("Haiti", "United States", "Afghanistan", "Indonesia",
                             "Tajikistan", "Algeria", "Cambodia") or \
               n.get("resource_id", "").endswith("registration_country"):
                country_node = n
                break

    if not country_node:
        log.error("[%s] Country spinner not found!", device_id)
        return False

    tap_node(device_id, country_node)
    time.sleep(2)

    # Find search field
    search_field = wait_for_id(
        device_id, "com.whatsapp.w4b:id/search_src_text",
        timeout=5, dump_name=f"{device_id}_search.xml"
    )
    if not search_field:
        xml_path = dump_ui(device_id, f"{device_id}_search2.xml")
        nodes = parse_ui_nodes(xml_path)
        for n in nodes:
            if n["class"] == "android.widget.EditText" or "search" in n["resource_id"].lower():
                search_field = n
                break

    if search_field:
        tap_node(device_id, search_field)
        time.sleep(0.5)
        input_text(device_id, country_name)
        time.sleep(2)

        # Find the LIST ITEM (not search bar)
        xml_path = dump_ui(device_id, f"{device_id}_country_list.xml")
        nodes = parse_ui_nodes(xml_path)

        indo_node = None
        # Strategy 1: country_first_name resource-id
        for n in nodes:
            if n["resource_id"] == "com.whatsapp.w4b:id/country_first_name" and \
               country_name.lower() in n["text"].lower():
                indo_node = n
                break
        # Strategy 2: content-desc with "Double tap to select"
        if not indo_node:
            for n in nodes:
                if "double tap to select" in n.get("content_desc", "").lower() and \
                   country_name.lower() in n.get("content_desc", "").lower():
                    indo_node = n
                    break
        # Strategy 3: text match excluding search EditText
        if not indo_node:
            for n in nodes:
                if country_name.lower() in n["text"].lower() and \
                   "edit_text" not in n["resource_id"] and \
                   "search" not in n["resource_id"]:
                    indo_node = n
                    break

        if indo_node:
            log.info("[%s] Found country list item: '%s' at %s",
                     device_id, indo_node["text"], indo_node["bounds"])
            tap_node(device_id, indo_node)
            time.sleep(1.5)
            return True
        else:
            # Coordinate fallback
            log.warning("[%s] Country list item not found, tap fallback y=216", device_id)
            tap(device_id, 360, 216)
            time.sleep(1.5)
            return True
    else:
        # No search, scroll to find
        log.info("[%s] No search field, scrolling for %s", device_id, country_name)
        for scroll_attempt in range(25):
            xml_path = dump_ui(device_id, f"{device_id}_scroll_{scroll_attempt}.xml")
            nodes = parse_ui_nodes(xml_path)
            node = find_node_by_text(nodes, country_name)
            if node:
                tap_node(device_id, node)
                time.sleep(1.5)
                return True
            adb(device_id, ["shell", "input", "swipe", "360", "900", "360", "400", "300"])
            time.sleep(0.8)
        log.error("[%s] Country %s not found after scrolling", device_id, country_name)
        return False


def run_wa_registration(session: EmulatorSession) -> EmulatorSession:
    """Full WA Business ADB registration flow for 1 emulator."""
    dev = session.device_id
    phone = session.phone_number
    country = session.country
    local_num = session.local_number

    log.info("=" * 60)
    log.info("[%s] WA BIZ REGISTRATION Ã¢â‚¬â€ Phone: %s, Country: %s", dev, phone, country)
    log.info("=" * 60)

    try:
        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 0: Launch WA Business Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 0: Force-stop & launch WA Business", dev)
        adb(dev, ["shell", "am", "force-stop", WA_PACKAGE])
        time.sleep(0.5)
        adb(dev, ["shell", "monkey", "-p", WA_PACKAGE, "1"])
        time.sleep(STEP_DELAY + 1.5)
        step0 = capture_full_state(dev, "00_app_launch")
        session.steps.append(step0)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 1: AGREE AND CONTINUE Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 1: AGREE AND CONTINUE", dev)
        agree_node = wait_for_text(dev, "AGREE AND CONTINUE", timeout=10, dump_name=f"{dev}_s1.xml")
        if agree_node:
            tap_node(dev, agree_node)
            time.sleep(STEP_DELAY)
        step1 = capture_full_state(dev, "01_after_agree")
        session.steps.append(step1)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 2: Permissions Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 2: Handle permissions", dev)
        time.sleep(1)
        for _ in range(3):
            perm = wait_for_text(dev, "ALLOW", timeout=2, dump_name=f"{dev}_perm.xml")
            if perm:
                tap_node(dev, perm)
                time.sleep(0.5)
            else:
                break
        step2 = capture_full_state(dev, "02_after_permissions")
        session.steps.append(step2)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 3: Select country Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 3: Select country %s", dev, country)
        select_country_in_wa(dev, country)
        step3 = capture_full_state(dev, "03_country_selected")
        session.steps.append(step3)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 4: Input phone number Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 4: Input number %s", dev, local_num)
        phone_field = wait_for_id(
            dev, "com.whatsapp.w4b:id/registration_phone",
            timeout=5, dump_name=f"{dev}_s4.xml"
        )
        if not phone_field:
            phone_field = wait_for_text(dev, "phone number", timeout=3, dump_name=f"{dev}_s4b.xml")

        if phone_field:
            tap_node(dev, phone_field)
            time.sleep(0.5)
            _clear_text_field(dev)
            time.sleep(0.3)
            input_text(dev, local_num)
            time.sleep(1)
        else:
            log.error("[%s] Phone field NOT FOUND", dev)

        step4 = capture_full_state(dev, "04_phone_entered")
        session.steps.append(step4)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 5: Tap NEXT Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 5: Tap NEXT", dev)
        next_node = wait_for_text(dev, "NEXT", timeout=4, dump_name=f"{dev}_s5.xml")
        if not next_node:
            next_node = wait_for_id(dev, "com.whatsapp.w4b:id/registration_submit",
                                    timeout=3, dump_name=f"{dev}_s5b.xml")
        if next_node:
            tap_node(dev, next_node)
        else:
            keyevent(dev, 66)
        time.sleep(STEP_DELAY)
        step5 = capture_full_state(dev, "05_after_next")
        session.steps.append(step5)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 6: Handle confirmation dialog Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        log.info("[%s] Step 6: Confirmation dialog", dev)
        time.sleep(1)
        xml_path = dump_ui(dev, f"{dev}_s6.xml")
        nodes = parse_ui_nodes(xml_path)
        all_texts = [n["text"] for n in nodes if n["text"]]
        log.info("[%s] Dialog: %s", dev, [t[:60] for t in all_texts])

        is_error = any("too long" in t.lower() or "too short" in t.lower() or
                       "invalid" in t.lower() for t in all_texts)
        if is_error:
            log.error("[%s] NUMBER ERROR: %s", dev, all_texts)
            ok_node = find_node_by_exact_text(nodes, "OK")
            if ok_node:
                tap_node(dev, ok_node)
            step6 = capture_full_state(dev, "06_error")
            step6.error = f"Validation error: {all_texts}"
            session.steps.append(step6)
            return session  # bail out

        # Tap Continue/OK/YES
        confirm = find_node_by_exact_text(nodes, "Continue")
        if not confirm:
            confirm = find_node_by_exact_text(nodes, "OK")
        if not confirm:
            confirm = find_node_by_exact_text(nodes, "YES")
        if confirm:
            log.info("[%s] Tapping '%s'", dev, confirm["text"])
            tap_node(dev, confirm)
            time.sleep(STEP_DELAY)
        step6 = capture_full_state(dev, "06_after_confirm")
        session.steps.append(step6)

        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        # STEP 7+8: VERIFICATION MEGA-LOOP
        # Keep trying until we pass verification or timeout.
        # Handles: rate-limit, "choose how to verify", missed
        # call screen, "Didn't receive code", "Receive SMS",
        # "Send SMS", and auto-verified states.
        # Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
        log.info("[%s] Step 7: Entering verification mega-loop", dev)
        VERIFY_TIMEOUT = SMS_WAIT_MAX + 60  # total budget
        verify_start = time.time()
        sms_requested = False
        rate_limited = False

        while time.time() - verify_start < VERIFY_TIMEOUT:
            time.sleep(2)
            try:
                xml_path = dump_ui(dev, f"{dev}_verify_loop.xml")
                nodes = parse_ui_nodes(xml_path)
                all_texts = [n["text"] for n in nodes if n["text"]]
                elapsed_v = int(time.time() - verify_start)
                log.info("[%s] verify@%ds Ã¢â‚¬â€ %s", dev, elapsed_v, [t[:50] for t in all_texts])
            except Exception as ex:
                log.debug("[%s] verify poll error: %s", dev, ex)
                continue

            joined = " ".join(t.lower() for t in all_texts)

            # Ã¢â€â‚¬Ã¢â€â‚¬ SUCCESS: past verification Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            if any(kw in joined for kw in (
                "business profile", "create your", "contacts",
                "chats", "welcome", "finish", "choose a name",
                "add a profile", "backup",
            )):
                log.info("[%s] Ã¢Å“â€œ AUTO-VERIFIED Ã¢â‚¬â€ past OTP screen!", dev)
                break

            # Ã¢â€â‚¬Ã¢â€â‚¬ LOGIN BLOCKED / BANNED Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            if "login not available" in joined or "can\u2019t log you in" in joined or "cannot log you" in joined:
                log.warning("[%s] BLOCKED Ã¢â‚¬â€ 'Login not available right now'", dev)
                break

            # Ã¢â€â‚¬Ã¢â€â‚¬ RATE LIMIT dialog Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            if "too many" in joined:
                rate_limited = True
                log.warning("[%s] RATE LIMITED", dev)
                ok_node = find_node_by_exact_text(nodes, "OK")
                if ok_node:
                    tap_node(dev, ok_node)
                    time.sleep(1)
                continue

            # Ã¢â€â‚¬Ã¢â€â‚¬ "Is this the correct number?" confirmation dialog
            if "is this the correct number" in joined or "correct number" in joined:
                yes_node = find_node_by_exact_text(nodes, "Yes")
                if not yes_node:
                    yes_node = find_node_by_exact_text(nodes, "YES")
                if not yes_node:
                    yes_node = find_node_by_exact_text(nodes, "Continue")
                if not yes_node:
                    yes_node = find_node_by_exact_text(nodes, "OK")
                if yes_node:
                    log.info("[%s] Confirmation dialog Ã¢â‚¬â€ tapping '%s'", dev, yes_node["text"])
                    tap_node(dev, yes_node)
                    time.sleep(STEP_DELAY)
                continue

            # Ã¢â€â‚¬Ã¢â€â‚¬ "Choose how to verify" / verification method screen
            # It's a radio-button list: Missed call / Receive SMS / Voice call
            # Select "Receive SMS" then tap "CONTINUE"
            if "choose how to verify" in joined:
                sms_node = find_node_by_text(nodes, "Receive SMS")
                if not sms_node:
                    sms_node = find_node_by_text(nodes, "Send SMS")
                if not sms_node:
                    sms_node = find_node_by_text(nodes, "SMS")
                if sms_node:
                    log.info("[%s] Selecting '%s' radio button", dev, sms_node["text"])
                    tap_node(dev, sms_node)
                    time.sleep(0.5)
                # Now tap CONTINUE
                cont_node = find_node_by_exact_text(nodes, "CONTINUE")
                if not cont_node:
                    cont_node = find_node_by_exact_text(nodes, "Continue")
                if cont_node:
                    log.info("[%s] Tapping CONTINUE after selecting SMS", dev)
                    tap_node(dev, cont_node)
                    sms_requested = True
                    time.sleep(STEP_DELAY)
                continue

            # Ã¢â€â‚¬Ã¢â€â‚¬ "VERIFY ANOTHER WAY" button Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            vaw = find_node_by_text(nodes, "VERIFY ANOTHER WAY")
            if vaw:
                log.info("[%s] 'VERIFY ANOTHER WAY' Ã¢â€ â€™ tapping", dev)
                tap_node(dev, vaw)
                time.sleep(STEP_DELAY)
                continue

            # Ã¢â€â‚¬Ã¢â€â‚¬ "Didn't receive code?" link Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            drc = find_node_by_text(nodes, "Didn't receive code")
            if not drc:
                drc = find_node_by_text(nodes, "Resend")
            if drc:
                log.info("[%s] 'Didn't receive code' Ã¢â€ â€™ tapping", dev)
                tap_node(dev, drc)
                time.sleep(1.5)
                continue

            # Ã¢â€â‚¬Ã¢â€â‚¬ "Too long" / "Too short" error Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            if "too long" in joined or "too short" in joined:
                log.warning("[%s] Number validation error Ã¢â‚¬â€ tapping OK", dev)
                ok_node = find_node_by_exact_text(nodes, "OK")
                if ok_node:
                    tap_node(dev, ok_node)
                    time.sleep(1)
                    # Clear field and re-enter correct number
                    phone_field_fix = find_node_by_id(nodes, "com.whatsapp.w4b:id/registration_phone")
                    if not phone_field_fix:
                        # Re-dump after OK
                        fix_xml = dump_ui(dev, f"{dev}_fix_toolong.xml")
                        fix_nodes = parse_ui_nodes(fix_xml)
                        phone_field_fix = find_node_by_id(fix_nodes, "com.whatsapp.w4b:id/registration_phone")
                    if phone_field_fix:
                        tap_node(dev, phone_field_fix)
                        time.sleep(0.3)
                        _clear_text_field(dev)
                        time.sleep(0.2)
                        input_text(dev, local_num)
                        time.sleep(0.5)
                continue

            # Ã¢â€â‚¬Ã¢â€â‚¬ Verification screen with timer Ã¢â‚¬â€ just wait Ã¢â€â‚¬
            # But NOT the "Enter your phone number" screen
            if "enter your phone number" in joined:
                if not hasattr(session, '_stuck_retries'):
                    session._stuck_retries = 0
                session._stuck_retries += 1
                if session._stuck_retries > 3:
                    log.error("[%s] STUCK > 3 retries Ã¢â‚¬â€ giving up on phone entry", dev)
                    break
                log.warning("[%s] STUCK on phone entry (retry %d/3)", dev, session._stuck_retries)
                phone_field_retry = find_node_by_id(nodes, "com.whatsapp.w4b:id/registration_phone")
                if phone_field_retry:
                    tap_node(dev, phone_field_retry)
                    time.sleep(0.5)
                    _clear_text_field(dev)
                    time.sleep(0.2)
                    # Type char by char
                    for ch in local_num:
                        if ch.isdigit():
                            adb(dev, ["shell", "input", "text", ch])
                            time.sleep(0.08)
                    time.sleep(0.5)
                    # Tap NEXT
                    next_retry = find_node_by_text(nodes, "NEXT")
                    if next_retry:
                        tap_node(dev, next_retry)
                        time.sleep(1)
                continue
            if "verify" in joined or "requesting" in joined or "code" in joined:
                # Still on verification screen, waiting for OTP
                if sms_requested:
                    # Already requested SMS, just wait
                    time.sleep(5)
                else:
                    time.sleep(2)
                continue

            # Unknown state Ã¢â‚¬â€ capture and wait
            time.sleep(3)

        step7 = capture_full_state(dev, "07_verify_done")
        session.steps.append(step7)

        # Ã¢â€â‚¬Ã¢â€â‚¬ STEP 9: Final state Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
        step9 = capture_full_state(dev, "09_final_state")
        session.steps.append(step9)

        log.info("[%s] Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â FINAL STATE Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â", dev)
        for t in step9.ui_texts:
            log.info("[%s]   Ã¢â€ â€™ %s", dev, t[:100])
        log.info("[%s] FLOW COMPLETE Ã¢Å“â€œ", dev)

    except Exception as e:
        log.error("[%s] FATAL: %s", dev, e)
        import traceback
        traceback.print_exc()
        session.steps.append(StepResult(step="fatal", success=False, error=str(e)))

    return session


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 3-LAYER ORCHESTRATOR
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def run_3layer_single(phone: str, device_id: str, emu_index: int = 0) -> EmulatorSession:
    """Run complete 3-layer flow for 1 phone number on 1 emulator."""
    country, local_num = detect_country(phone)

    session = EmulatorSession(
        index=emu_index, name=f"emu_{emu_index}",
        device_id=device_id, phone_number=phone,
        country=country, local_number=local_num,
    )

    # Ã¢â€â‚¬Ã¢â€â‚¬ LAYER 1: Telegram SendCodeRequest Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    log.info("Ã¢â€¢â€Ã¢â€¢ÂÃ¢â€¢Â LAYER 1: Telegram SendCodeRequest for %s Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢â€”", phone)
    l1_result = run_layer1(phone)
    session.layer1_result = json.dumps(l1_result)
    log.info("[LAYER1] Result: %s", l1_result)

    if l1_result["status"] in ("sent", "flood"):
        log.info("Ã¢â€¢â€Ã¢â€¢ÂÃ¢â€¢Â WAIT %ds after Layer 1 Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢â€”", LAYER1_WAIT)
        time.sleep(LAYER1_WAIT)
    else:
        log.warning("[LAYER1] Status=%s, skipping wait, proceed to Layer 2", l1_result["status"])
        time.sleep(2)

    # Ã¢â€â‚¬Ã¢â€â‚¬ LAYER 2: WA Business ADB Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    log.info("Ã¢â€¢â€Ã¢â€¢ÂÃ¢â€¢Â LAYER 2: WA Business ADB for %s Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢â€”", phone)
    session = run_wa_registration(session)

    # Save report
    save_session_report(session)
    return session


def run_3layer_multi(phones: list[str]):
    """Run 3-layer flow for multiple phones. 
    If enough unique emulators: parallel (1 per device).
    If not: sequential on same device (force-stop between each).
    """
    count = len(phones)
    log.info("Multi-layer mode: %d phones", count)

    devices = ensure_emulators(count)
    
    # Dedup: emulator-5554 and 127.0.0.1:5555 can be the same physical device
    # Keep only unique devices Ã¢â‚¬â€ prefer emulator-XXXX format
    seen_ports = set()
    unique_devices = []
    for dev in devices:
        # Normalize: extract port number
        port = _normalize_port(dev)
        if port not in seen_ports:
            seen_ports.add(port)
            unique_devices.append(dev)
    
    log.info("Unique devices after dedup: %s (from %s)", unique_devices, devices)
    
    sessions = []
    
    if len(unique_devices) >= count:
        # Enough unique emulators Ã¢â‚¬â€ run parallel
        log.info("PARALLEL mode: %d devices for %d phones", len(unique_devices), count)
        with ThreadPoolExecutor(max_workers=min(count, 6), thread_name_prefix="WA") as pool:
            futures = {}
            for i, phone in enumerate(phones):
                dev = unique_devices[i]
                future = pool.submit(run_3layer_single, phone, dev, i)
                futures[future] = phone

            for future in as_completed(futures):
                phone = futures[future]
                try:
                    session = future.result()
                    sessions.append(session)
                    log.info("[%s] Complete. Layer1=%s", phone, session.layer1_result)
                except Exception as e:
                    log.error("[%s] Thread failed: %s", phone, e)
    else:
        # Not enough unique emulators Ã¢â‚¬â€ run SEQUENTIAL on same device
        dev = unique_devices[0]
        log.info("SEQUENTIAL mode: %d phones on device %s", count, dev)
        for i, phone in enumerate(phones):
            log.info("Ã¢â€ÂÃ¢â€ÂÃ¢â€Â Phone %d/%d: %s Ã¢â€ÂÃ¢â€ÂÃ¢â€Â", i + 1, count, phone)
            session = run_3layer_single(phone, dev, i)
            sessions.append(session)
            log.info("[%s] Done. Moving to next...", phone)
            time.sleep(2)

    save_combined_report(sessions)
    return sessions


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# REPORT
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def save_session_report(session: EmulatorSession):
    report = {
        "device_id": session.device_id,
        "phone_number": session.phone_number,
        "country": session.country,
        "local_number": session.local_number,
        "layer1_result": session.layer1_result,
        "total_steps": len(session.steps),
        "steps": [{
            "step": s.step, "success": s.success, "timestamp": s.timestamp,
            "ui_texts": s.ui_texts, "screenshot": s.screenshot,
            "error": s.error,
        } for s in session.steps],
    }
    safe_phone = re.sub(r'[^0-9]', '', session.phone_number)
    filename = OUTPUT_DIR / f"report_{safe_phone}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("Report saved: %s", filename)


def save_combined_report(sessions: list[EmulatorSession]):
    combined = {
        "total": len(sessions),
        "timestamp": datetime.now().isoformat(),
        "sessions": [{
            "phone": s.phone_number, "country": s.country,
            "device": s.device_id, "layer1": s.layer1_result,
            "steps": len(s.steps),
            "errors": [st.error for st in s.steps if st.error],
        } for s in sessions],
    }
    filename = OUTPUT_DIR / "combined_report.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    log.info("Combined report: %s", filename)


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# MAIN CLI
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="WA Business 3-Layer Registration Ã¢â‚¬â€ LDPlayer",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--phones", "-p", nargs="+", required=True,
        help="Phone numbers (with country code, e.g. 50935842111)",
    )
    parser.add_argument(
        "--skip-layer1", action="store_true",
        help="Skip Telegram SendCodeRequest (Layer 1)",
    )
    parser.add_argument(
        "--layer1-wait", type=int, default=LAYER1_WAIT,
        help=f"Seconds to wait after Layer 1 (default: {LAYER1_WAIT})",
    )
    parser.add_argument(
        "--sms-wait", type=int, default=SMS_WAIT_MAX,
        help=f"Max seconds to wait for SMS (default: {SMS_WAIT_MAX})",
    )
    parser.add_argument(
        "--delay", type=float, default=STEP_DELAY,
        help=f"Delay between steps (default: {STEP_DELAY})",
    )

    args = parser.parse_args()

    # Update globals
    import wa_biz_adb
    wa_biz_adb.STEP_DELAY = args.delay
    wa_biz_adb.LAYER1_WAIT = args.layer1_wait
    wa_biz_adb.SMS_WAIT_MAX = args.sms_wait

    phones = args.phones
    log.info("Ã¢â€¢â€Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢â€”")
    log.info("Ã¢â€¢â€˜  WA BUSINESS 3-LAYER Ã¢â‚¬â€ LDPlayer Multi-Instance  Ã¢â€¢â€˜")
    log.info("Ã¢â€¢Â Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â£")
    log.info("Ã¢â€¢â€˜ Phones: %-41s Ã¢â€¢â€˜", ", ".join(phones[:3]))
    log.info("Ã¢â€¢â€˜ Layer1: %-41s Ã¢â€¢â€˜", "SKIP" if args.skip_layer1 else f"ON (wait {args.layer1_wait}s)")
    log.info("Ã¢â€¢â€˜ SMS Wait: %-39s Ã¢â€¢â€˜", f"{args.sms_wait}s")
    log.info("Ã¢â€¢Å¡Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â")

    if args.skip_layer1:
        wa_biz_adb.LAYER1_WAIT = 0

    if len(phones) == 1:
        devices = get_adb_devices()
        if not devices:
            devices = ensure_emulators(1)
        run_3layer_single(phones[0], devices[0])
    else:
        run_3layer_multi(phones)


if __name__ == "__main__":
    main()
