"""
WA Business Login — Telegram Bot /login Handler
=================================================
Handles /login command in the DikZz Telegram bot.
Runs the 3-layer WA Business registration flow per number:
  Layer 1: Telegram SendCodeRequest (trigger call/OTP)
  Wait:    ~60 seconds
  Layer 2: WA Business ADB → SMS verification

Usage in Telegram:
  /login 50935842111
  /login 50935842111 50935842288 50935842543
  /login 50935842111
  50935842288
  50935842543

Author: ENI x LO
"""

import asyncio
import json
import logging
import re
import time
import html as html_mod
from concurrent.futures import ThreadPoolExecutor, as_completed

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

log = logging.getLogger(__name__)

# ── Lazy import wa_biz_adb (avoid circular at module level) ──
_wa_mod = None

def _get_wa():
    global _wa_mod
    if _wa_mod is None:
        import wa_biz_adb
        _wa_mod = wa_biz_adb
    return _wa_mod


# ── Import styling from dik.py ───────────────────────────────
# Lazy-loaded to avoid circular imports at module scope
_dik = None

def _get_dik():
    global _dik
    if _dik is None:
        import dik as _d
        _dik = _d
    return _dik


def _em(emoji_id, fallback="⭐"):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


# ═══════════════════════════════════════════════════════════════
# CUSTOM EMOJI IDS (same as dik.py)
# ═══════════════════════════════════════════════════════════════
E1 = "5796205953913196373"           # ✅ success
E2 = "5420323339723881652"           # ❌ failed / warning
E4 = "5438467424770867483"           # 🎧 powered by
E5 = "5438436750114439411"           # 🏴‍☠️ header icon
E6 = "6003769830564434518"           # 🚀 rocket / fire

CE_LOADING  = "5256024382337205926"  # 🟠 loading
CE_NOMOR    = "5422696450888842691"  # 📞 phone
CE_PROFILE  = "5870994129244131212"  # 👤 profile
CE_WAKTU    = "5872756762347573066"  # ⏲ time
CE_NEGARA   = "5240097896279326170"  # 🌏 country
CE_BACK     = "5449847653586188540"  # ◀️ back
CE_WHATSAPP = "5345943173401175849"  # ❤️ whatsapp
CE_TELEGRAM = "5285350148451344065"  # 💬 telegram
CE_LOGIN    = "5355034377921244938"  # 🔑 login
CE_DETAIL_GEM = "5330237710655306682"  # 💎 gem

# ═══════════════════════════════════════════════════════════════
# STYLING — Matches dik.py _screen / _header / _footer / _quote
# ═══════════════════════════════════════════════════════════════
_BAR = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"


def _quote(body: str, expandable: bool = False) -> str:
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{body}</blockquote>"


def _header(title: str, breadcrumb: str = "") -> str:
    bc = f"\n<i>{breadcrumb}</i>\n" if breadcrumb else "\n"
    return f"{_em(E5, '🏴‍☠️')} <b>{title}</b>{bc}{_BAR}"


def _footer() -> str:
    return f"{_BAR}\n{_em(E4, '🎧')} <i>Powered by </i><b>DikZz</b>"


def _row(icon: str, label: str, value: str) -> str:
    return f"  {icon} <b>{label}:</b>  {value}"


def _screen(title: str, body: str, breadcrumb: str = "") -> str:
    inner = f"{_header(title, breadcrumb)}\n{body}\n\n{_footer()}"
    return _quote(inner)


# ═══════════════════════════════════════════════════════════════
# PHONE PARSER
# ═══════════════════════════════════════════════════════════════

def _clean_phones(raw_text: str) -> list[str]:
    """Parse phone numbers from text. Space/newline-separated, strips non-digits."""
    tokens = raw_text.strip().split()
    phones = []
    for token in tokens:
        cleaned = re.sub(r'[^0-9+]', '', token)
        cleaned = cleaned.lstrip('+')
        if len(cleaned) >= 7:
            phones.append(cleaned)
    return phones


# ═══════════════════════════════════════════════════════════════
# RESULT FORMATTING
# ═══════════════════════════════════════════════════════════════

def _fmt_layer1(result: dict) -> str:
    status = result.get("status", "unknown")
    detail = result.get("detail", "")
    if status == "sent":
        return f"{_em(E1, '✅')} Sent — {html_mod.escape(detail)}"
    elif status == "flood":
        return f"{_em(E2, '⚠️')} Flood — {html_mod.escape(detail)}"
    elif status == "banned":
        return f"{_em(E2, '❌')} Banned"
    elif status == "invalid":
        return f"{_em(E2, '❌')} Invalid"
    elif status == "timeout":
        return f"{_em(CE_LOADING, '⏳')} Timeout"
    else:
        return f"{_em(E2, '❌')} {html_mod.escape(status)}"


def _fmt_final(session) -> str:
    if not session.steps:
        return f"{_em(E2, '❌')} No steps"

    last = session.steps[-1]
    texts = last.ui_texts[:5] if last.ui_texts else []

    success_words = ["business profile", "create your", "choose", "contacts",
                     "chats", "welcome", "finish"]
    is_blocked = any("login not available" in t.lower() or "can\u2019t log you in" in t.lower() for t in last.ui_texts)
    is_success = any(any(sw in t.lower() for sw in success_words) for t in last.ui_texts)
    is_rate = any("too many" in t.lower() for t in last.ui_texts)
    is_verify = any("verify" in t.lower() for t in last.ui_texts)

    if is_success:
        return f"{_em(E1, '✅')} AUTO-VERIFIED"
    elif is_blocked:
        return f"{_em(E2, '❌')} BLOCKED — Login not available"
    elif is_rate:
        return f"{_em(E2, '⚠️')} Rate limited"
    elif is_verify:
        return f"{_em(CE_WHATSAPP, '💬')} Verification screen"
    elif last.error:
        return f"{_em(E2, '❌')} {html_mod.escape(last.error[:50])}"
    else:
        preview = ", ".join(t[:30] for t in texts[:3]) if texts else "—"
        return f"{_em(CE_NOMOR, '📱')} {html_mod.escape(preview)}"


def _run_single_blocking(phone: str, device_id: str, emu_index: int) -> dict:
    """Run full 3-layer flow. Blocking (runs in thread)."""
    wa = _get_wa()
    t0 = time.time()
    try:
        session = wa.run_3layer_single(phone, device_id, emu_index)
        elapsed = int(time.time() - t0)
        try:
            l1 = json.loads(session.layer1_result) if session.layer1_result else {}
        except (json.JSONDecodeError, TypeError):
            l1 = {"status": "unknown", "detail": str(session.layer1_result)}
        return {
            "phone": phone, "country": session.country,
            "local": session.local_number, "device": device_id,
            "layer1": _fmt_layer1(l1), "final": _fmt_final(session),
            "steps": len(session.steps), "elapsed": elapsed, "error": None,
        }
    except Exception as e:
        return {
            "phone": phone, "country": "?", "local": phone, "device": device_id,
            "layer1": f"{_em(E2, '❌')} Error",
            "final": f"{_em(E2, '❌')} {html_mod.escape(str(e)[:60])}",
            "steps": 0, "elapsed": int(time.time() - t0), "error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
# MESSAGE BUILDERS
# ═══════════════════════════════════════════════════════════════

def _build_help_msg() -> str:
    body = (
        f"\nKirim nomor (kode negara, tanpa +):\n\n"
        f"<code>/login 50935842111</code>\n"
        f"<code>/login 509xxx 509yyy 509zzz</code>\n\n"
        f"Atau multi-line:\n"
        f"<code>/login 50935842111\n50935842288\n50935842543</code>\n\n"
        f"{_em(CE_DETAIL_GEM, '💎')} <b>3-Layer Flow:</b>\n"
        f"{_row(_em(CE_TELEGRAM, '💬'), 'Layer 1', 'SendCodeRequest (trigger call)')}\n"
        f"{_row(_em(CE_WAKTU, '⏲'), 'Wait', '~60 detik')}\n"
        f"{_row(_em(CE_WHATSAPP, '❤️'), 'Layer 2', 'WA Business ADB → SMS')}\n"
    )
    return _screen("WA BUSINESS LOGIN", body, breadcrumb="/login")


def _build_start_msg(phones: list[str]) -> str:
    phone_rows = "\n".join(
        f"  {_em(CE_NOMOR, '📞')} <code>+{p}</code>" for p in phones
    )
    body = (
        f"\n{_em(CE_DETAIL_GEM, '💎')} <b>Phones ({len(phones)}):</b>\n"
        f"{phone_rows}\n\n"
        f"{_em(CE_LOADING, '🟠')} Preparing emulators..."
    )
    return _screen("WA LOGIN — STARTING", body, breadcrumb="/login")


def _build_running_msg(phones: list[str]) -> str:
    phone_rows = "\n".join(
        f"  {_em(CE_NOMOR, '📞')} <code>+{p}</code>" for p in phones
    )
    body = (
        f"\n{_em(CE_DETAIL_GEM, '💎')} <b>Phones ({len(phones)}):</b>\n"
        f"{phone_rows}\n\n"
        f"{_em(CE_LOADING, '🟠')} Layer 1 (Telegram API) + Layer 2 (WA ADB)...\n"
        f"{_em(CE_WAKTU, '⏲')} ~3-5 menit per nomor"
    )
    return _screen("WA LOGIN — RUNNING", body, breadcrumb="/login")


def _build_report(results: list[dict]) -> str:
    total = len(results)
    success = sum(1 for r in results if "✅" in r.get("final", "") or "AUTO" in r.get("final", ""))
    failed = total - success

    summary = (
        f"\n{_em(E1, '✅')} <b>{success}</b> success   "
        f"{_em(E2, '❌')} <b>{failed}</b> failed   "
        f"{_em(CE_NOMOR, '📞')} <b>{total}</b> total\n"
    )

    rows = []
    for i, r in enumerate(results, 1):
        country_esc = html_mod.escape(r.get("country", "?"))
        elapsed_s = f"{r['elapsed']}s"
        dev_name = html_mod.escape(r.get('device', '?'))
        rows.append(
            f"\n{_em(E6, '🔥')} <b>#{i}</b>  <code>+{r['phone']}</code>  ({country_esc})\n"
            f"{_row(_em(CE_TELEGRAM, '💬'), 'Layer 1', r['layer1'])}\n"
            f"{_row(_em(CE_WHATSAPP, '❤️'), 'Layer 2', r['final'])}\n"
            f"{_row(_em(CE_WAKTU, '⏲'), 'Time', elapsed_s)}"
            f"  •  {r['steps']} steps  •  {dev_name}"
        )

    body = summary + "\n".join(rows)
    return _screen("WA LOGIN — REPORT", body, breadcrumb="/login")


def _build_error_msg(error: str) -> str:
    body = f"\n{_em(E2, '❌')} <b>Error:</b>\n<code>{html_mod.escape(error[:300])}</code>"
    return _screen("WA LOGIN — FAILED", body, breadcrumb="/login")


# ═══════════════════════════════════════════════════════════════
# TELEGRAM /login COMMAND
# ═══════════════════════════════════════════════════════════════

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /login <phone1> [phone2] [phone3] ...
    Runs 3-layer WA Business registration flow for each number.
    """
    user_id = update.effective_user.id

    # Parse phone numbers
    raw_text = ""
    if update.message and update.message.text:
        full_text = update.message.text
        # Everything after /login
        parts = full_text.split(maxsplit=1)
        if len(parts) > 1:
            raw_text = parts[1]
        elif "\n" in full_text:
            raw_text = full_text.split("\n", 1)[1]

    phones = _clean_phones(raw_text)

    if not phones:
        await update.message.reply_text(
            _build_help_msg(),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "KEMBALI", callback_data=f"menu_start_{user_id}",
                    icon_custom_emoji_id=CE_BACK, style="danger",
                )],
            ]),
        )
        return

    # Limit 6
    if len(phones) > 6:
        phones = phones[:6]
        await update.message.reply_text(
            _quote(f"{_em(E2, '⚠️')} Maksimal 6 nomor per batch. Pakai 6 pertama."),
            parse_mode=ParseMode.HTML,
        )

    # Send initial status
    status_msg = await update.message.reply_text(
        _build_start_msg(phones),
        parse_mode=ParseMode.HTML,
    )

    loop = asyncio.get_event_loop()

    def _run_all():
        wa = _get_wa()
        count = len(phones)

        try:
            devices = wa.ensure_emulators(count)
        except Exception as e:
            log.error("ensure_emulators failed: %s", e)
            devices = wa.get_adb_devices()

        if not devices:
            return [{
                "phone": p, "country": "?", "local": p, "device": "none",
                "layer1": f"{_em(E2, '❌')} No device",
                "final": f"{_em(E2, '❌')} No ADB devices",
                "steps": 0, "elapsed": 0, "error": "No devices",
            } for p in phones]

        # Dedup using normalize
        seen, unique = set(), []
        for dev in devices:
            port = wa._normalize_port(dev)
            if port not in seen:
                seen.add(port)
                unique.append(dev)

        results = []
        if len(unique) >= count:
            with ThreadPoolExecutor(max_workers=min(count, 6), thread_name_prefix="WA-Login") as pool:
                futs = {}
                for i, ph in enumerate(phones):
                    futs[pool.submit(_run_single_blocking, ph, unique[i], i)] = ph
                for f in as_completed(futs):
                    try:
                        results.append(f.result())
                    except Exception as e:
                        results.append({
                            "phone": futs[f], "country": "?", "local": futs[f],
                            "device": "?",
                            "layer1": f"{_em(E2, '❌')} Thread error",
                            "final": f"{_em(E2, '❌')} {html_mod.escape(str(e)[:60])}",
                            "steps": 0, "elapsed": 0, "error": str(e),
                        })
        else:
            dev = unique[0]
            for i, ph in enumerate(phones):
                results.append(_run_single_blocking(ph, dev, i))

        return results

    try:
        await status_msg.edit_text(
            _build_running_msg(phones),
            parse_mode=ParseMode.HTML,
        )

        results = await loop.run_in_executor(None, _run_all)

        report = _build_report(results)
        try:
            await status_msg.edit_text(
                report,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "KEMBALI", callback_data=f"menu_start_{user_id}",
                        icon_custom_emoji_id=CE_BACK, style="danger",
                    )],
                ]),
            )
        except Exception:
            await update.message.reply_text(
                report,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "KEMBALI", callback_data=f"menu_start_{user_id}",
                        icon_custom_emoji_id=CE_BACK, style="danger",
                    )],
                ]),
            )

    except Exception as e:
        log.error("login_command fatal: %s", e)
        try:
            await status_msg.edit_text(
                _build_error_msg(str(e)),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "KEMBALI", callback_data=f"menu_start_{user_id}",
                        icon_custom_emoji_id=CE_BACK, style="danger",
                    )],
                ]),
            )
        except Exception:
            await update.message.reply_text(
                _build_error_msg(str(e)),
                parse_mode=ParseMode.HTML,
            )
