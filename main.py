# -*- coding: utf-8 -*-
"""
==========================================================================
  EARN & REFER BOT  -  single file Telegram bot (pyTelegramBotAPI)
==========================================================================
  User side : Account, Invite Link, Extra Rewards, Withdraw, My History,
              Premium, Buy Key (auto delivery), Deposit (UPI / PhonePe)
  Admin side: full admin panel (ON/OFF switches, channels, products+keys,
              redeem codes, premium plans, UPI, withdrawals, deposits,
              users, broadcast, welcome setup, multi-admin, export)

  INSTALL :  pip install pyTelegramBotAPI flask requests qrcode[pil]
  RUN     :  python main.py   (Railway uses the Procfile)

  SET THESE (env variables or edit the CONFIG block below):
      BOT_TOKEN, OWNER_ID
      PHONEPE_MERCHANT_ID, PHONEPE_SALT_KEY, PHONEPE_SALT_INDEX (auto pay)
      ZAPUPI_TOKEN, ZAPUPI_SECRET                               (auto pay)
      PUBLIC_URL (your hosting url, optional)
==========================================================================
"""

import os
import io
import csv
import sys
import json
import time
import html
import base64
import random
import string
import hashlib
import sqlite3
import logging
import re
import atexit
import threading
import types as types_ns
import subprocess
from urllib.parse import quote

import requests
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telebot.apihelper import ApiTelegramException
from flask import Flask

try:
    import qrcode
except Exception:  # qrcode is optional
    qrcode = None

# ==========================================================================
#  CONFIG
# ==========================================================================
# Railway Variables me BOT_TOKEN ho to wahi use hoga, warna neeche wala default token.
DEFAULT_BOT_TOKEN = "9043344483:AAEbrEqdXkGoT5gecdsMHdL8F8iVWTRlDKw"
BOT_TOKEN = (os.environ.get("BOT_TOKEN", "") or DEFAULT_BOT_TOKEN).strip().strip("\"'").strip()
if not re.match(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$", BOT_TOKEN):
    print(
        "\n❌ BOT_TOKEN is missing or wrong.\n"
        "   Railway -> your service -> Variables -> add:  BOT_TOKEN = 123456789:AAxxxxxxxx...\n"
        "   (full token from @BotFather, with the number, the colon and the letters; no quotes, no spaces)\n"
        f"   Value received: {'(empty - variable not found)' if not BOT_TOKEN else 'length ' + str(len(BOT_TOKEN)) + ', has colon: ' + str(':' in BOT_TOKEN)}\n",
        flush=True,
    )
    sys.exit(3 if os.environ.get("CLONE_MODE") == "1" else 1)
try:
    OWNER_ID = int(os.environ.get("OWNER_ID", "0").strip() or 0)
except ValueError:
    OWNER_ID = 0
# On Railway: add a Volume mounted at /data so the database survives redeploys.
DB_PATH = os.environ.get("DB_PATH") or (
    "/data/earn_refer_bot.db" if os.path.isdir("/data") else "earn_refer_bot.db"
)


def _usable_db_path(path):
    """Return a DB path whose folder exists and is writable; else fall back safely."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [path, os.path.join(here, "earn_refer_bot.db"), "/tmp/earn_refer_bot.db"]
    for c in candidates:
        try:
            d = os.path.dirname(os.path.abspath(c))
            os.makedirs(d, exist_ok=True)
            if not os.access(d, os.W_OK):
                continue
            t = sqlite3.connect(c)
            t.execute("CREATE TABLE IF NOT EXISTS _probe(x)")
            t.commit()
            t.close()
            return c
        except Exception:
            continue
    return ":memory:"


DB_PATH = _usable_db_path(DB_PATH)
PORT = int(os.environ.get("PORT", "8080"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
if not PUBLIC_URL and os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
    PUBLIC_URL = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
CURRENCY = "₹"

# Clone mode: this same file is started by the main bot for every "Add Bot" customer.
CLONE_MODE = os.environ.get("CLONE_MODE") == "1"
CLONE_ID = os.environ.get("CLONE_ID", "")

# ---- PhonePe PG (standard checksum API) ----
PHONEPE_MERCHANT_ID = os.environ.get("PHONEPE_MERCHANT_ID", "")
PHONEPE_SALT_KEY = os.environ.get("PHONEPE_SALT_KEY", "")
PHONEPE_SALT_INDEX = os.environ.get("PHONEPE_SALT_INDEX", "1")
PHONEPE_ENV = os.environ.get("PHONEPE_ENV", "prod")  # prod | uat
PHONEPE_HOST = {
    "prod": "https://api.phonepe.com/apis/hermes",
    "uat": "https://api-preprod.phonepe.com/apis/pg-sandbox",
}

# ---- ZapUPI (check endpoint names with your ZapUPI docs) ----
ZAPUPI_TOKEN = os.environ.get("ZAPUPI_TOKEN", "")
ZAPUPI_SECRET = os.environ.get("ZAPUPI_SECRET", "")
ZAPUPI_BASE = os.environ.get("ZAPUPI_BASE", "https://api.zapupi.com/api")

AUTO_PAY_EXPIRY = 30 * 60  # seconds a gateway order stays valid
POLL_EVERY = 15            # seconds between payment status checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("earnbot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True, num_threads=8)
ME_USERNAME = ""  # filled in main()

# ==========================================================================
#  DEFAULT SETTINGS  (admin can change all of these from the panel)
# ==========================================================================
DEFAULTS = {
    # switches
    "bot_on": "1",
    "video_on": "1",
    "daily_on": "1",
    "target_on": "1",
    "withdraw_on": "1",
    "upi_on": "1",
    "auto_pay_on": "0",
    "premium_on": "1",
    "shop_on": "1",
    "redeem_on": "1",
    "force_join_on": "1",
    "clone_on": "1",
    # numbers
    "ref_reward": "5",
    "daily_bonus": "2",
    "min_withdraw": "50",
    "min_deposit": "10",
    "target_refs": "10",
    "target_reward": "50",
    "premium_multiplier": "2",
    "clone_price": "50",
    "max_clones": "20",
    # texts
    "upi_id": "",
    "upi_name": "Store",
    "gateway": "phonepe",
    "support": "",
    "payout_channel": "",
    "updates_channel": "",
    "help_text": "1) Invite friends with your link and earn per refer.\n2) Claim the daily bonus every 24 hours.\n3) Withdraw when you reach the minimum amount.\n4) Any problem? Tap Report a Problem or contact support.",
    "welcome_text": "Invite friends, claim your daily bonus and withdraw your earnings!",
    "welcome_video": "",
    "welcome_photo": "",
}

TOGGLES = [
    ("bot_on", "🤖 Bot"),
    ("video_on", "🎬 Welcome Video"),
    ("force_join_on", "📢 Force Join"),
    ("daily_on", "🎁 Daily Bonus"),
    ("target_on", "🎯 Target Reward"),
    ("withdraw_on", "💸 Withdraw"),
    ("redeem_on", "🎟 Redeem Code"),
    ("shop_on", "🛒 Buy Key Shop"),
    ("premium_on", "⭐ Premium"),
    ("upi_on", "🏦 Manual UPI"),
    ("auto_pay_on", "⚡ Auto Payment"),
    ("clone_on", "🤖 Add Bot (Clone)"),
]

NUMERICS = [
    ("ref_reward", "👥 Refer Reward"),
    ("daily_bonus", "🎁 Daily Bonus"),
    ("min_withdraw", "💸 Min Withdraw"),
    ("min_deposit", "💳 Min Deposit"),
    ("target_refs", "🎯 Target Referrals"),
    ("target_reward", "🏆 Target Reward"),
    ("premium_multiplier", "⭐ Premium Multiplier"),
    ("clone_price", "🤖 Clone Price"),
    ("max_clones", "🤖 Max Clones"),
]

TEXT_KEYS = {
    "upi_id": "UPI ID (example: name@upi)",
    "upi_name": "UPI / Store Name",
    "support": "Support username (example: @mysupport)",
    "welcome_text": "Welcome message text",
    "updates_channel": "Daily updates channel (@username or link)",
    "help_text": "Help / FAQ text shown in Support",
    "payout_channel": "Payout channel id or @username (withdraw proofs will be posted)",
}

# ==========================================================================
#  DATABASE
# ==========================================================================
_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def q(sql, args=(), fetch=None):
    """Run one SQL statement. fetch: 'one' | 'all' | 'count' | None(last id)."""
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        if fetch == "count":
            return cur.rowcount
        return cur.lastrowid


def init_db():
    with _lock:
        _conn.executescript(
            """
        CREATE TABLE IF NOT EXISTS users(
            uid INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance REAL DEFAULT 0,
            referred_by INTEGER DEFAULT 0,
            ref_rewarded INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            ref_earned REAL DEFAULT 0,
            target_claimed INTEGER DEFAULT 0,
            last_daily INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            premium_until INTEGER DEFAULT 0,
            total_deposit REAL DEFAULT 0,
            total_withdraw REAL DEFAULT 0,
            codes_used INTEGER DEFAULT 0,
            upi TEXT DEFAULT '',
            joined INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS admins(uid INTEGER PRIMARY KEY, added INTEGER);
        CREATE TABLE IF NOT EXISTS channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT, title TEXT, link TEXT
        );
        CREATE TABLE IF NOT EXISTS txns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, kind TEXT, amount REAL, note TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, amount REAL, upi TEXT,
            status TEXT DEFAULT 'pending', ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, amount REAL, method TEXT, order_id TEXT,
            status TEXT DEFAULT 'pending', ts INTEGER, proof TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, price REAL, descr TEXT DEFAULT '', active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS keys(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER, key_text TEXT,
            sold INTEGER DEFAULT 0, buyer INTEGER DEFAULT 0, sold_ts INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, product_id INTEGER, price REAL, key_text TEXT, ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS codes(
            code TEXT PRIMARY KEY, amount REAL, max_uses INTEGER,
            used INTEGER DEFAULT 0, created INTEGER
        );
        CREATE TABLE IF NOT EXISTS code_uses(code TEXT, uid INTEGER);
        CREATE TABLE IF NOT EXISTS plans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, price REAL, days INTEGER
        );
        CREATE TABLE IF NOT EXISTS clones(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_uid INTEGER, token TEXT, username TEXT,
            status TEXT DEFAULT 'active', created INTEGER, paid REAL DEFAULT 0,
            restarts INTEGER DEFAULT 0, last_start INTEGER DEFAULT 0, last_error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS kb_sig(uid INTEGER PRIMARY KEY, sig TEXT);
        CREATE TABLE IF NOT EXISTS clone_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER, token TEXT, username TEXT, amount REAL,
            method TEXT, order_id TEXT, status TEXT DEFAULT 'pending', ts INTEGER
        );
        """
        )
        try:
            _conn.execute("ALTER TABLE clones ADD COLUMN admin_uid INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        _conn.commit()


# ==========================================================================
#  SETTINGS HELPERS
# ==========================================================================
def S(key):
    row = q("SELECT v FROM settings WHERE k=?", (key,), "one")
    return row["v"] if row else DEFAULTS.get(key, "")


def SF(key):
    try:
        return float(S(key))
    except Exception:
        return 0.0


def SI(key):
    return int(SF(key))


def set_S(key, value):
    q("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (key, str(value)))


def on(key):
    if CLONE_MODE and key in ("auto_pay_on", "clone_on"):
        return False  # clones use their own UPI only; no resell of clones
    return S(key) == "1"


def flip(key):
    set_S(key, "0" if on(key) else "1")


# ==========================================================================
#  SMALL UTILITIES
# ==========================================================================
def now():
    return int(time.time())


def fdate(ts):
    if not ts:
        return "-"
    return time.strftime("%d-%m-%Y %H:%M", time.localtime(ts))


def esc(x):
    return html.escape(str(x if x is not None else ""))


def money(x):
    try:
        x = float(x)
    except Exception:
        x = 0.0
    if abs(x - int(x)) < 0.005:
        return f"{CURRENCY}{int(x):,}"
    return f"{CURRENCY}{x:,.2f}"


def gen_code(n=8):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def onoff(flag):
    return "🟢 ON" if flag else "🔴 OFF"


def parse_float(text):
    try:
        v = float(str(text).strip().replace(",", ""))
        return v
    except Exception:
        return None


# ==========================================================================
#  COLORFUL BUTTONS  (Telegram Bot API "style": primary / success / danger)
# ==========================================================================
class CBtn(InlineKeyboardButton):
    """InlineKeyboardButton that can carry a colour style."""

    def __init__(self, text, style=None, **kwargs):
        super().__init__(text, **kwargs)
        self._cstyle = style

    def to_dict(self):
        d = super().to_dict()
        if self._cstyle:
            d["style"] = self._cstyle
        return d


class CKey(KeyboardButton):
    """Bottom-keyboard button that can carry a colour style."""

    def __init__(self, text, style=None):
        super().__init__(text)
        self._cstyle = style

    def to_dict(self):
        d = super().to_dict()
        if self._cstyle:
            d["style"] = self._cstyle
        return d


BLUE, GREEN, RED = "primary", "success", "danger"


def B(text, cb=None, url=None, style=None):
    if url:
        return CBtn(text, style=style, url=url)
    return CBtn(text, style=style, callback_data=cb)


def KB(*rows):
    m = InlineKeyboardMarkup()
    for r in rows:
        if r:
            m.row(*r)
    return m


def back_btn(to="menu", text="⬅️ Back"):
    return B(text, to, style=RED)


# ==========================================================================
#  SEND / EDIT HELPERS
# ==========================================================================
def send(chat_id, text, markup=None, **kw):
    try:
        return bot.send_message(
            chat_id, text, reply_markup=markup, disable_web_page_preview=True, **kw
        )
    except Exception as e:
        log.warning("send fail %s: %s", chat_id, e)
        return None


def notify(uid, text, markup=None):
    return send(uid, text, markup)


def notify_admins(text, markup=None):
    ids = {OWNER_ID}
    for r in q("SELECT uid FROM admins", fetch="all"):
        ids.add(r["uid"])
    for a in ids:
        send(a, text, markup)


def ans(c, text="", alert=False):
    try:
        bot.answer_callback_query(c.id, text, show_alert=alert)
    except Exception:
        pass


def render(c, text, markup=None):
    """Edit the callback message in place; fall back to delete+send."""
    chat_id = c.message.chat.id
    mid = c.message.message_id
    try:
        bot.edit_message_text(
            text, chat_id, mid, reply_markup=markup, disable_web_page_preview=True
        )
        return
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            return
    except Exception:
        pass
    try:
        bot.delete_message(chat_id, mid)
    except Exception:
        pass
    send(chat_id, text, markup)


# ==========================================================================
#  STATE (what the bot is waiting for from a user)
# ==========================================================================
STATE = {}


def set_state(uid, name, **data):
    STATE[uid] = {"name": name, **data}


def get_state(uid):
    return STATE.get(uid)


def clear_state(uid):
    STATE.pop(uid, None)


# ==========================================================================
#  USERS / ADMINS / BALANCE
# ==========================================================================
def get_user(uid):
    return q("SELECT * FROM users WHERE uid=?", (uid,), "one")


def ensure_user(tg_user, ref=None):
    u = get_user(tg_user.id)
    if u:
        q(
            "UPDATE users SET username=?, first_name=? WHERE uid=?",
            (tg_user.username or "", tg_user.first_name or "", tg_user.id),
        )
        return u
    referrer = 0
    if ref and ref != tg_user.id and get_user(ref):
        referrer = ref
    q(
        "INSERT INTO users(uid,username,first_name,referred_by,joined) VALUES(?,?,?,?,?)",
        (tg_user.id, tg_user.username or "", tg_user.first_name or "", referrer, now()),
    )
    return get_user(tg_user.id)


def is_admin(uid):
    if uid == OWNER_ID:
        return True
    return q("SELECT uid FROM admins WHERE uid=?", (uid,), "one") is not None


def is_premium(uid):
    u = get_user(uid)
    return bool(u and u["premium_until"] > now())


def mult(uid):
    return SF("premium_multiplier") if is_premium(uid) else 1.0


def add_balance(uid, amount, kind, note=""):
    with _lock:
        q("UPDATE users SET balance=balance+? WHERE uid=?", (amount, uid))
        q(
            "INSERT INTO txns(uid,kind,amount,note,ts) VALUES(?,?,?,?,?)",
            (uid, kind, amount, note, now()),
        )


def deduct_balance(uid, amount, kind, note=""):
    """Atomic: returns True only when the user had enough balance."""
    with _lock:
        n = q(
            "UPDATE users SET balance=balance-? WHERE uid=? AND balance>=?",
            (amount, uid, amount),
            "count",
        )
        if not n:
            return False
        q(
            "INSERT INTO txns(uid,kind,amount,note,ts) VALUES(?,?,?,?,?)",
            (uid, kind, -amount, note, now()),
        )
        return True


def uname(u):
    if u["username"]:
        return "@" + u["username"]
    return u["first_name"] or str(u["uid"])


# ==========================================================================
#  FORCE JOIN CHANNELS
# ==========================================================================
def get_channels():
    return q("SELECT * FROM channels ORDER BY id", fetch="all")


def not_joined(uid):
    missing = []
    for ch in get_channels():
        try:
            mem = bot.get_chat_member(ch["chat_id"], uid)
            if mem.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:
            log.warning("get_chat_member %s: %s (bot must be admin there)", ch["chat_id"], e)
    return missing


def send_join_prompt(chat_id, missing):
    rows = []
    for ch in missing:
        rows.append([B("📢 Join " + (ch["title"] or "Channel"), url=ch["link"], style=BLUE)])
    rows.append([B("✅ I have joined", "chk_join", style=GREEN)])
    send(
        chat_id,
        "🔒 <b>Join our channels first</b>\n\n"
        "To use this bot please join all channels below, then tap "
        "<b>I have joined</b>.",
        KB(*rows),
    )


def gate(uid, chat_id):
    """True when the user may use the bot right now."""
    u = get_user(uid)
    if u and u["banned"]:
        send(chat_id, "🚫 <b>You are banned from using this bot.</b>")
        return False
    if is_admin(uid):
        return True
    if not on("bot_on"):
        send(chat_id, "🛠 <b>Bot is temporarily OFF.</b>\nPlease try again later.")
        return False
    if on("force_join_on"):
        missing = not_joined(uid)
        if missing:
            send_join_prompt(chat_id, missing)
            return False
    return True


# ==========================================================================
#  CALLBACK REGISTRY
# ==========================================================================
CB = {}


def cb(name, admin=False, owner=False, nogate=False):
    def deco(fn):
        CB[name] = (fn, admin, owner, nogate)
        return fn

    return deco


STATE_HANDLERS = {}
CB_FROM_KEYBOARD = {}


def on_state(name):
    def deco(fn):
        STATE_HANDLERS[name] = fn
        return fn

    return deco


# ==========================================================================
#  REFERRAL / TARGET REWARDS
# ==========================================================================
def check_target(uid):
    if not on("target_on"):
        return
    t = SI("target_refs")
    if t <= 0:
        return
    u = get_user(uid)
    while u and u["ref_count"] >= (u["target_claimed"] + 1) * t:
        reward = SF("target_reward")
        add_balance(uid, reward, "target", f"Target reached: {(u['target_claimed'] + 1) * t} refers")
        q("UPDATE users SET target_claimed=target_claimed+1 WHERE uid=?", (uid,))
        notify(
            uid,
            f"🏆 <b>Target completed!</b>\n"
            f"You reached {(u['target_claimed'] + 1) * t} referrals and got "
            f"<b>{money(reward)}</b> bonus.",
        )
        u = get_user(uid)


def reward_referrer(uid):
    """Pay the referrer once, after the new user passed force-join."""
    u = get_user(uid)
    if not u or not u["referred_by"] or u["ref_rewarded"]:
        return
    n = q("UPDATE users SET ref_rewarded=1 WHERE uid=? AND ref_rewarded=0", (uid,), "count")
    if not n:
        return
    rid = u["referred_by"]
    amt = SF("ref_reward") * mult(rid)
    add_balance(rid, amt, "referral", f"Referral: {uid}")
    q(
        "UPDATE users SET ref_count=ref_count+1, ref_earned=ref_earned+? WHERE uid=?",
        (amt, rid),
    )
    notify(
        rid,
        f"👥 <b>New referral!</b>\n{esc(u['first_name'])} joined with your link.\n"
        f"💰 You earned <b>{money(amt)}</b>",
    )
    check_target(rid)


# ==========================================================================
#  MAIN MENU  (layout copied from the screenshot)
# ==========================================================================
def pay_available():
    """True when at least one deposit method is ON."""
    return bool(on("auto_pay_on") or (on("upi_on") and S("upi_id")))


ALL_MENU_ACTIONS = {
    "👤 Account": "account", "🔗 Invite Link": "invite", "🎁 Extra Rewards": "extra",
    "💸 Withdraw": "withdraw", "📜 My History": "history", "👥 My Refers": "myrefs",
    "🛒 Buy Key": "shop", "💳 Deposit": "deposit", "⭐ Premium": "premium",
    "🎟 Redeem Code": "redeem", "📊 Dashboard": "dashboard", "📡 Status": "status",
    "📱 My Bots": "clone_my", "🆘 Support & Help": "support",
    "📢 Daily Updates": "updates", "🛡 Admin Panel": "adm",
}


def menu_action(text):
    """Which action does a bottom-keyboard button text stand for?"""
    t = (text or "").strip()
    if t in ALL_MENU_ACTIONS:
        return ALL_MENU_ACTIONS[t]
    if t.startswith("🤖 Add Bot"):
        return "clone_add"
    return None


def menu_layout(uid):
    """Rows of (label, action, colour). Features the admin turned OFF are left out."""
    rows = [[("👤 Account", "account", BLUE)]]
    r = [("🔗 Invite Link", "invite", GREEN)]
    if on("daily_on") or on("target_on"):
        r.append(("🎁 Extra Rewards", "extra", BLUE))
    rows.append(r)
    if on("withdraw_on"):
        rows.append([("💸 Withdraw", "withdraw", RED)])
    rows.append([("📜 My History", "history", BLUE), ("👥 My Refers", "myrefs", GREEN)])

    def pair(*items):
        items = [i for i in items if i]
        if items:
            rows.append(items)

    pair(("🛒 Buy Key", "shop", GREEN) if on("shop_on") else None,
         ("💳 Deposit", "deposit", BLUE) if pay_available() else None)
    pair(("⭐ Premium", "premium", BLUE) if on("premium_on") else None,
         ("🎟 Redeem Code", "redeem", GREEN) if on("redeem_on") else None)
    pair(("📊 Dashboard", "dashboard", BLUE), ("📡 Status", "status", GREEN))
    if not CLONE_MODE and on("clone_on"):
        rows.append([(f"🤖 Add Bot ({money(SF('clone_price'))})", "clone_add", GREEN),
                     ("📱 My Bots", "clone_my", BLUE)])
    last = [("🆘 Support & Help", "support", RED)]
    if S("updates_channel"):
        last.append(("📢 Daily Updates", "updates", BLUE))
    rows.append(last)
    if is_admin(uid):
        rows.append([("🛡 Admin Panel", "adm", RED)])
    return rows


def main_markup(uid):
    """The menu is a keyboard at the bottom of the chat (like a reply keyboard)."""
    layout = menu_layout(uid)
    mk = ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True, row_width=2)
    for row in layout:
        mk.row(*[CKey(label, style=col) for label, _a, col in row])
    try:
        sig = hashlib.md5(json.dumps(layout, ensure_ascii=False).encode()).hexdigest()
        q("INSERT OR REPLACE INTO kb_sig(uid,sig) VALUES(?,?)", (uid, sig))
    except Exception:
        pass
    return mk


def kb_sync(uid, chat_id):
    """If the admin changed ON/OFF switches, silently push the new keyboard to this user."""
    try:
        layout = menu_layout(uid)
        sig = hashlib.md5(json.dumps(layout, ensure_ascii=False).encode()).hexdigest()
        row = q("SELECT sig FROM kb_sig WHERE uid=?", (uid,), "one")
        if row and row["sig"] == sig:
            return
        send(chat_id, "🔄 Menu updated", main_markup(uid), disable_notification=True)
    except Exception as e:
        log.warning("kb_sync fail: %s", e)


def menu_text(first_name):
    return (
        f"👋 <b>Welcome {esc(first_name)}!</b>\n\n"
        f"{esc(S('welcome_text'))}\n\n"
        f"👇 Choose an option below"
    )


def send_main(uid, chat_id, first_name, fresh=True):
    """Welcome screen. Uses the welcome video/photo when admin enabled it."""
    text = menu_text(first_name)
    mk = main_markup(uid)
    try:
        if fresh and on("video_on") and S("welcome_video"):
            bot.send_video(chat_id, S("welcome_video"), caption=text, reply_markup=mk)
            return
        if fresh and on("video_on") and S("welcome_photo"):
            bot.send_photo(chat_id, S("welcome_photo"), caption=text, reply_markup=mk)
            return
    except Exception as e:
        log.warning("welcome media failed: %s", e)
    send(chat_id, text, mk)


@bot.message_handler(commands=["start"])
def h_start(m):
    uid = m.from_user.id
    clear_state(uid)
    parts = (m.text or "").split(maxsplit=1)
    ref = None
    if len(parts) > 1 and parts[1].strip().isdigit():
        ref = int(parts[1].strip())
    ensure_user(m.from_user, ref)
    if not gate(uid, m.chat.id):
        return
    reward_referrer(uid)
    send_main(uid, m.chat.id, m.from_user.first_name)


@bot.message_handler(commands=["menu"])
def h_menu_cmd(m):
    uid = m.from_user.id
    clear_state(uid)
    ensure_user(m.from_user)
    if gate(uid, m.chat.id):
        send_main(uid, m.chat.id, m.from_user.first_name, fresh=False)


@bot.message_handler(commands=["cancel"])
def h_cancel(m):
    clear_state(m.from_user.id)
    send(m.chat.id, "❌ Cancelled.", KB([B("🏠 Menu", "menu", style=BLUE)]))


@bot.message_handler(commands=["setupi"])
def h_setupi(m):
    """Admin quick-setup:  /setupi name@bank Store Name   (turns on QR payments)."""
    if not is_admin(m.from_user.id):
        return
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 2 or "@" not in parts[1]:
        send(m.chat.id, "Usage: <code>/setupi yourname@bank Store Name</code>\n"
                        f"Current UPI: <code>{esc(S('upi_id')) or '-'}</code>")
        return
    set_S("upi_id", parts[1].strip())
    if len(parts) > 2 and parts[2].strip():
        set_S("upi_name", parts[2].strip()[:40])
    set_S("upi_on", "1")
    send(m.chat.id, f"✅ UPI saved: <code>{esc(S('upi_id'))}</code> ({esc(S('upi_name'))})\n"
                    "QR codes will now be generated for deposits and Add Bot payments.")


@bot.message_handler(commands=["admin"])
def h_admin_cmd(m):
    if is_admin(m.from_user.id):
        clear_state(m.from_user.id)
        send(m.chat.id, admin_home_text(), admin_home_markup())


@cb("chk_join", nogate=True)
def cb_chk_join(c):
    uid = c.from_user.id
    missing = not_joined(uid)
    if missing:
        ans(c, "❌ You have not joined all channels yet!", True)
        return
    ans(c, "✅ Verified")
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except Exception:
        pass
    reward_referrer(uid)
    send_main(uid, c.message.chat.id, c.from_user.first_name)


@cb("menu")
def cb_menu(c):
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except Exception:
        pass
    send_main(c.from_user.id, c.message.chat.id, c.from_user.first_name, fresh=False)


@cb("updates")
def cb_updates(c):
    if not S("updates_channel"):
        ans(c, "No updates channel set.", True)
        return
    render(
        c,
        "📢 <b>Daily Updates</b>\n\nJoin our channel for daily updates and offers 👇",
        KB([B("📢 Open Channel", url=tme(S("updates_channel")), style=BLUE)], [back_btn()]),
    )


# ==========================================================================
#  ACCOUNT
# ==========================================================================
@cb("account")
def cb_account(c):
    uid = c.from_user.id
    u = get_user(uid)
    prem = "Not active"
    if u["premium_until"] > now():
        prem = "Active till " + fdate(u["premium_until"])
    text = (
        "👤 <b>My Account</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"👤 Name: {esc(u['first_name'])}\n"
        f"💰 <b>Balance: {money(u['balance'])}</b>\n"
        f"👥 Total Refer: <b>{u['ref_count']}</b>\n"
        f"🎁 Refer Earnings: {money(u['ref_earned'])}\n"
        f"🎟 Redeem Codes Used: {u['codes_used']}\n"
        f"💳 Total Deposit: {money(u['total_deposit'])}\n"
        f"💸 Total Withdrawn: {money(u['total_withdraw'])}\n"
        f"⭐ Premium: {prem}\n"
        f"📅 Joined: {fdate(u['joined'])}"
    )
    render(
        c,
        text,
        KB(
            [B("💸 Withdraw", "withdraw", style=RED), B("💳 Deposit", "deposit", style=BLUE)],
            [B("📜 My History", "history", style=BLUE)],
            [back_btn()],
        ),
    )


# ==========================================================================
#  INVITE LINK
# ==========================================================================
@cb("invite")
def cb_invite(c):
    uid = c.from_user.id
    u = get_user(uid)
    link = f"https://t.me/{ME_USERNAME}?start={uid}"
    per = SF("ref_reward") * mult(uid)
    text = (
        "🔗 <b>Your Invite Link</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"<code>{link}</code>\n\n"
        f"💰 Per Refer: <b>{money(per)}</b>\n"
        f"👥 Total Refer: <b>{u['ref_count']}</b>\n"
        f"🎁 Earned from refer: <b>{money(u['ref_earned'])}</b>\n"
    )
    t = SI("target_refs")
    if on("target_on") and t > 0:
        nxt = (u["target_claimed"] + 1) * t
        text += (
            f"\n🎯 Target: {u['ref_count']}/{nxt} refers → "
            f"<b>{money(SF('target_reward'))}</b> bonus"
        )
    share = "https://t.me/share/url?url=" + quote(link) + "&text=" + quote(
        "Join this bot and earn money daily! 💰"
    )
    render(
        c,
        text,
        KB(
            [B("📤 Share Link", url=share, style=GREEN)],
            [B("🏆 Top Referrers", "top", style=BLUE)],
            [back_btn()],
        ),
    )


@cb("top")
def cb_top(c):
    rows = q(
        "SELECT first_name, username, ref_count FROM users WHERE ref_count>0 "
        "ORDER BY ref_count DESC LIMIT 10",
        fetch="all",
    )
    text = "🏆 <b>Top 10 Referrers</b>\n━━━━━━━━━━━━━━━\n"
    if not rows:
        text += "No referrals yet."
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        nm = r["first_name"] or r["username"] or "User"
        text += f"{mark} {esc(nm)} — <b>{r['ref_count']}</b>\n"
    render(c, text, KB([back_btn("invite")]))


# ==========================================================================
#  EXTRA REWARDS  (daily bonus, redeem code, target)
# ==========================================================================
@cb("extra")
def cb_extra(c):
    uid = c.from_user.id
    u = get_user(uid)
    daily = f"{money(SF('daily_bonus') * mult(uid))}" if on("daily_on") else "OFF"
    t = SI("target_refs")
    tgt = "OFF"
    if on("target_on") and t > 0:
        tgt = f"{u['ref_count']}/{(u['target_claimed'] + 1) * t}"
    text = (
        "🎁 <b>Extra Rewards</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎁 Daily Bonus: <b>{daily}</b>\n"
        f"🎟 Redeem Code: <b>{'ON' if on('redeem_on') else 'OFF'}</b>\n"
        f"🎯 Target Progress: <b>{tgt}</b>"
    )
    render(
        c,
        text,
        KB(
            [B("🎁 Claim Daily Bonus", "daily", style=GREEN)],
            [B("🎟 Redeem Code", "redeem", style=BLUE)],
            [B("🎯 Target Reward", "target", style=BLUE)],
            [back_btn()],
        ),
    )


@cb("daily")
def cb_daily(c):
    uid = c.from_user.id
    if not on("daily_on"):
        ans(c, "❌ Daily bonus is OFF right now.", True)
        return
    u = get_user(uid)
    wait = u["last_daily"] + 86400 - now()
    if wait > 0:
        h, mnt = wait // 3600, (wait % 3600) // 60
        ans(c, f"⏳ Come back after {h}h {mnt}m", True)
        return
    amt = SF("daily_bonus") * mult(uid)
    q("UPDATE users SET last_daily=? WHERE uid=?", (now(), uid))
    add_balance(uid, amt, "daily", "Daily bonus")
    ans(c, f"🎉 You got {money(amt)} daily bonus!", True)
    cb_extra(c)


@cb("target")
def cb_target(c):
    uid = c.from_user.id
    if not on("target_on") or SI("target_refs") <= 0:
        ans(c, "❌ Target reward is OFF right now.", True)
        return
    u = get_user(uid)
    t = SI("target_refs")
    nxt = (u["target_claimed"] + 1) * t
    left = max(0, nxt - u["ref_count"])
    ans(
        c,
        f"🎯 Refer {t} users to get {money(SF('target_reward'))}.\n"
        f"Progress: {u['ref_count']}/{nxt} (need {left} more). "
        f"Reward is added automatically.",
        True,
    )


@cb("redeem")
def cb_redeem(c):
    if not on("redeem_on"):
        ans(c, "❌ Redeem code is OFF right now.", True)
        return
    set_state(c.from_user.id, "redeem")
    render(
        c,
        "🎟 <b>Redeem Code</b>\n\nSend your redeem code now.",
        KB([back_btn("menu", "❌ Cancel")]),
    )


@on_state("redeem")
def st_redeem(m, st):
    uid = m.from_user.id
    code = (m.text or "").strip().upper()
    clear_state(uid)
    row = q("SELECT * FROM codes WHERE code=?", (code,), "one")
    back = KB([B("🎁 Extra Rewards", "extra", style=BLUE)])
    if not row:
        send(m.chat.id, "❌ Invalid code.", back)
        return
    if row["used"] >= row["max_uses"]:
        send(m.chat.id, "❌ This code has reached its usage limit.", back)
        return
    if q("SELECT 1 FROM code_uses WHERE code=? AND uid=?", (code, uid), "one"):
        send(m.chat.id, "❌ You already used this code.", back)
        return
    with _lock:
        n = q(
            "UPDATE codes SET used=used+1 WHERE code=? AND used<max_uses", (code,), "count"
        )
        if not n:
            send(m.chat.id, "❌ This code has reached its usage limit.", back)
            return
        q("INSERT INTO code_uses(code,uid) VALUES(?,?)", (code, uid))
    q("UPDATE users SET codes_used=codes_used+1 WHERE uid=?", (uid,))
    add_balance(uid, row["amount"], "redeem", f"Code {code}")
    send(m.chat.id, f"✅ Code applied! <b>{money(row['amount'])}</b> added to balance.", back)


# ==========================================================================
#  WITHDRAW
# ==========================================================================
@cb("withdraw")
def cb_withdraw(c):
    uid = c.from_user.id
    if not on("withdraw_on"):
        ans(c, "❌ Withdraw is OFF right now. Try later.", True)
        return
    u = get_user(uid)
    minw = SF("min_withdraw")
    if u["balance"] < minw:
        ans(c, f"❌ Minimum withdraw is {money(minw)}.\nYour balance: {money(u['balance'])}", True)
        return
    set_state(uid, "w_upi")
    saved = ""
    if u["upi"]:
        saved = f"\n💾 Last used: <code>{esc(u['upi'])}</code> (send it again to reuse)"
    render(
        c,
        f"💸 <b>Withdraw</b>\n\n💰 Balance: <b>{money(u['balance'])}</b>\n"
        f"📉 Minimum: {money(minw)}\n\n"
        f"Send your <b>UPI ID</b> now.{saved}",
        KB([back_btn("menu", "❌ Cancel")]),
    )


@on_state("w_upi")
def st_w_upi(m, st):
    uid = m.from_user.id
    upi = (m.text or "").strip()
    if "@" not in upi or len(upi) < 5 or " " in upi:
        send(m.chat.id, "❌ Invalid UPI ID. Send again (example: name@upi) or /cancel")
        return
    q("UPDATE users SET upi=? WHERE uid=?", (upi, uid))
    set_state(uid, "w_amt", upi=upi)
    u = get_user(uid)
    send(
        m.chat.id,
        f"✅ UPI saved: <code>{esc(upi)}</code>\n\n"
        f"Now send the <b>amount</b> to withdraw (max {money(u['balance'])}).",
    )


@on_state("w_amt")
def st_w_amt(m, st):
    uid = m.from_user.id
    amt = parse_float(m.text)
    u = get_user(uid)
    minw = SF("min_withdraw")
    if amt is None or amt < minw or amt > u["balance"]:
        send(
            m.chat.id,
            f"❌ Send a number between {money(minw)} and {money(u['balance'])}, or /cancel",
        )
        return
    if not on("withdraw_on"):
        clear_state(uid)
        send(m.chat.id, "❌ Withdraw is OFF right now.")
        return
    if not deduct_balance(uid, amt, "withdraw", "Withdraw request (hold)"):
        send(m.chat.id, "❌ Insufficient balance.")
        return
    wid = q(
        "INSERT INTO withdrawals(uid,amount,upi,status,ts) VALUES(?,?,?,?,?)",
        (uid, amt, st["upi"], "pending", now()),
    )
    clear_state(uid)
    send(
        m.chat.id,
        f"✅ <b>Withdraw request #{wid} sent</b>\n\n💰 Amount: {money(amt)}\n"
        f"🏦 UPI: <code>{esc(st['upi'])}</code>\n\nAdmin will review it soon.",
        KB([B("🏠 Menu", "menu", style=BLUE)]),
    )
    notify_admins(
        f"💸 <b>New Withdraw #{wid}</b>\n👤 {esc(u['first_name'])} (<code>{uid}</code>)\n"
        f"💰 {money(amt)}\n🏦 <code>{esc(st['upi'])}</code>",
        KB(
            [
                B("✅ Approve", f"a_wd:ok:{wid}", style=GREEN),
                B("❌ Reject", f"a_wd:no:{wid}", style=RED),
            ]
        ),
    )


# ==========================================================================
#  MY HISTORY
# ==========================================================================
@cb("history")
def cb_history(c):
    uid = c.from_user.id
    rows = q("SELECT * FROM txns WHERE uid=? ORDER BY id DESC LIMIT 12", (uid,), "all")
    wd = q(
        "SELECT status, COUNT(*) n, SUM(amount) s FROM withdrawals WHERE uid=? GROUP BY status",
        (uid,),
        "all",
    )
    text = "📜 <b>My History</b>\n━━━━━━━━━━━━━━━\n"
    if wd:
        text += "💸 <b>Withdrawals</b>\n"
        for r in wd:
            text += f"• {r['status'].title()}: {r['n']} ({money(r['s'] or 0)})\n"
        text += "\n"
    text += "🧾 <b>Last transactions</b>\n"
    if not rows:
        text += "No transactions yet."
    for r in rows:
        sign = "➕" if r["amount"] >= 0 else "➖"
        text += f"{sign} {money(abs(r['amount']))} • {esc(r['kind'])} • {fdate(r['ts'])}\n"
    render(c, text, KB([B("🛒 My Keys", "mykeys", style=GREEN)], [back_btn()]))


@cb("mykeys")
def cb_mykeys(c):
    rows = q(
        "SELECT o.*, p.name FROM orders o LEFT JOIN products p ON p.id=o.product_id "
        "WHERE o.uid=? ORDER BY o.id DESC LIMIT 10",
        (c.from_user.id,),
        "all",
    )
    text = "🔑 <b>My Purchased Keys</b>\n━━━━━━━━━━━━━━━\n"
    if not rows:
        text += "You have not bought anything yet."
    for r in rows:
        text += f"📦 {esc(r['name'])} • {money(r['price'])}\n<code>{esc(r['key_text'])}</code>\n\n"
    render(c, text, KB([back_btn("history")]))


# ==========================================================================
#  SHOP  (buy key -> key is delivered automatically)
# ==========================================================================
def stock_of(pid):
    r = q("SELECT COUNT(*) n FROM keys WHERE product_id=? AND sold=0", (pid,), "one")
    return r["n"]


@cb("shop")
def cb_shop(c):
    if not on("shop_on"):
        ans(c, "❌ Shop is OFF right now.", True)
        return
    prods = q("SELECT * FROM products WHERE active=1 ORDER BY id", fetch="all")
    u = get_user(c.from_user.id)
    text = f"🛒 <b>Buy Key</b>\n💰 Balance: <b>{money(u['balance'])}</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not prods:
        text += "No products available right now."
    for p in prods:
        st = stock_of(p["id"])
        label = f"{p['name']} • {money(p['price'])} • {st} left"
        rows.append([B(label, f"buy:{p['id']}", style=GREEN if st else RED)])
    rows.append([back_btn()])
    render(c, text, KB(*rows))


@cb("buy")
def cb_buy(c, args=None):
    pid = int(args[0])
    p = q("SELECT * FROM products WHERE id=? AND active=1", (pid,), "one")
    if not p:
        ans(c, "Product not found", True)
        return
    st = stock_of(pid)
    text = (
        f"📦 <b>{esc(p['name'])}</b>\n\n{esc(p['descr'])}\n\n"
        f"💰 Price: <b>{money(p['price'])}</b>\n📊 Stock: {st}"
    )
    rows = []
    if st:
        rows.append([B("✅ Confirm Purchase", f"buyok:{pid}", style=GREEN)])
    rows.append([back_btn("shop")])
    render(c, text, KB(*rows))


def buy_product(uid, pid):
    """Returns (ok, message_or_key). Everything happens inside one lock."""
    with _lock:
        p = q("SELECT * FROM products WHERE id=? AND active=1", (pid,), "one")
        if not p:
            return False, "Product not found."
        k = q("SELECT * FROM keys WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1", (pid,), "one")
        if not k:
            return False, "Out of stock."
        if not deduct_balance(uid, p["price"], "purchase", p["name"]):
            return False, "NOBAL"
        q("UPDATE keys SET sold=1, buyer=?, sold_ts=? WHERE id=?", (uid, now(), k["id"]))
        q(
            "INSERT INTO orders(uid,product_id,price,key_text,ts) VALUES(?,?,?,?,?)",
            (uid, pid, p["price"], k["key_text"], now()),
        )
        return True, (p["name"], k["key_text"], p["price"])


@cb("buyok")
def cb_buyok(c, args=None):
    uid = c.from_user.id
    ok, res = buy_product(uid, int(args[0]))
    if not ok:
        if res == "NOBAL":
            ans(c, "❌ Not enough balance. Deposit first.", True)
            render(
                c,
                "❌ <b>Insufficient balance</b>\nPlease add balance and try again.",
                KB([B("💳 Deposit", "deposit", style=GREEN)], [back_btn("shop")]),
            )
        else:
            ans(c, "❌ " + res, True)
        return
    name, key, price = res
    ans(c, "✅ Purchase successful!")
    render(
        c,
        f"✅ <b>Purchase successful!</b>\n\n📦 {esc(name)}\n💰 Paid: {money(price)}\n\n"
        f"🔑 <b>Your key:</b>\n<code>{esc(key)}</code>\n\nSaved in My History → My Keys.",
        KB([B("🛒 Buy More", "shop", style=GREEN)], [back_btn()]),
    )
    notify_admins(f"🛒 <b>Sale</b>: {esc(name)} • {money(price)} • user <code>{uid}</code>")


# ==========================================================================
#  PREMIUM
# ==========================================================================
@cb("premium")
def cb_premium(c):
    if not on("premium_on"):
        ans(c, "❌ Premium is OFF right now.", True)
        return
    uid = c.from_user.id
    u = get_user(uid)
    plans = q("SELECT * FROM plans ORDER BY price", fetch="all")
    status = "Not active"
    if u["premium_until"] > now():
        status = "Active till " + fdate(u["premium_until"])
    text = (
        "⭐ <b>Premium Plans</b>\n━━━━━━━━━━━━━━━\n"
        f"Status: <b>{status}</b>\n"
        f"💰 Balance: {money(u['balance'])}\n\n"
        f"Premium gives <b>x{SF('premium_multiplier'):g}</b> on refer reward "
        f"and daily bonus!\n"
    )
    rows = []
    if not plans:
        text += "\nNo plans available yet."
    for p in plans:
        rows.append([B(f"⭐ {p['name']} • {p['days']} days • {money(p['price'])}", f"plan:{p['id']}", style=GREEN)])
    rows.append([back_btn()])
    render(c, text, KB(*rows))


@cb("plan")
def cb_plan(c, args=None):
    uid = c.from_user.id
    p = q("SELECT * FROM plans WHERE id=?", (int(args[0]),), "one")
    if not p:
        ans(c, "Plan not found", True)
        return
    if not deduct_balance(uid, p["price"], "premium", p["name"]):
        ans(c, "❌ Not enough balance. Deposit first.", True)
        return
    u = get_user(uid)
    base = max(now(), u["premium_until"])
    until = base + p["days"] * 86400
    q("UPDATE users SET premium_until=? WHERE uid=?", (until, uid))
    ans(c, "⭐ Premium activated!", True)
    cb_premium(c)


# ==========================================================================
#  DEPOSIT  (auto gateway + manual UPI with admin approval)
# ==========================================================================
@cb("deposit")
def cb_deposit(c):
    rows = []
    if on("auto_pay_on"):
        rows.append([B("⚡ Auto Pay (instant)", "dep:auto", style=GREEN)])
    if on("upi_on") and S("upi_id"):
        rows.append([B("🏦 Manual UPI (QR)", "dep:manual", style=BLUE)])
    rows.append([back_btn()])
    if len(rows) == 1:
        ans(c, "❌ Deposit is OFF right now.", True)
        return
    render(
        c,
        f"💳 <b>Deposit</b>\n\nMinimum: {money(SF('min_deposit'))}\nChoose payment method 👇",
        KB(*rows),
    )


@cb("dep")
def cb_dep(c, args=None):
    method = args[0]
    set_state(c.from_user.id, "dep_amt", method=method)
    render(
        c,
        f"💳 Send the <b>amount</b> you want to add (min {money(SF('min_deposit'))}).",
        KB([back_btn("deposit", "❌ Cancel")]),
    )


def upi_uri(amount, order_id):
    return (
        f"upi://pay?pa={quote(S('upi_id'))}&pn={quote(S('upi_name'))}"
        f"&am={amount:.2f}&cu=INR&tn={quote(order_id)}"
    )


def make_qr(data):
    if not qrcode:
        return None
    try:
        img = qrcode.make(data)
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        bio.name = "qr.png"
        return bio
    except Exception as e:
        log.warning("QR generate failed: %s", e)
        return None


@on_state("dep_amt")
def st_dep_amt(m, st):
    uid = m.from_user.id
    amt = parse_float(m.text)
    if amt is None or amt < SF("min_deposit"):
        send(m.chat.id, f"❌ Send a valid amount (min {money(SF('min_deposit'))}) or /cancel")
        return
    method = st["method"]
    clear_state(uid)
    order_id = f"DEP{uid}{now()}"
    if method == "auto":
        if not on("auto_pay_on"):
            send(m.chat.id, "❌ Auto payment is OFF right now.")
            return
        gw = S("gateway")
        url = phonepe_create(uid, amt, order_id) if gw == "phonepe" else zapupi_create(uid, amt, order_id)
        if not url:
            send(m.chat.id, "❌ Payment gateway error. Try manual UPI or contact admin.")
            return
        did = q(
            "INSERT INTO deposits(uid,amount,method,order_id,status,ts) VALUES(?,?,?,?,?,?)",
            (uid, amt, gw, order_id, "pending", now()),
        )
        send(
            m.chat.id,
            f"⚡ <b>Auto Payment #{did}</b>\n💰 Amount: <b>{money(amt)}</b>\n\n"
            f"Tap <b>Pay Now</b>, complete the payment — balance is added "
            f"<b>automatically</b> within a minute.",
            KB(
                [B("💳 Pay Now", url=url, style=GREEN)],
                [B("🔄 Check Status", f"dep_chk:{did}", style=BLUE)],
                [B("🏠 Menu", "menu", style=RED)],
            ),
        )
    else:
        if not (on("upi_on") and S("upi_id")):
            send(m.chat.id, "❌ Manual UPI is OFF right now.")
            return
        did = q(
            "INSERT INTO deposits(uid,amount,method,order_id,status,ts) VALUES(?,?,?,?,?,?)",
            (uid, amt, "manual", order_id, "pending", now()),
        )
        caption = (
            f"🏦 <b>Manual UPI Deposit #{did}</b>\n\n💰 Amount: <b>{money(amt)}</b>\n"
            f"UPI ID: <code>{esc(S('upi_id'))}</code>\nName: {esc(S('upi_name'))}\n\n"
            f"Pay the exact amount, then send the <b>payment screenshot</b> here."
        )
        set_state(uid, "dep_ss", did=did)
        qr = make_qr(upi_uri(amt, order_id))
        try:
            if qr:
                bot.send_photo(m.chat.id, qr, caption=caption)
            else:
                send(m.chat.id, caption)
        except Exception:
            send(m.chat.id, caption)


@on_state("dep_ss")
def st_dep_ss(m, st):
    uid = m.from_user.id
    if m.content_type not in ("photo", "document"):
        send(m.chat.id, "❌ Please send the payment screenshot as a photo, or /cancel")
        return
    did = st["did"]
    d = q("SELECT * FROM deposits WHERE id=?", (did,), "one")
    if not d or d["status"] != "pending":
        clear_state(uid)
        return
    q("UPDATE deposits SET proof='sent' WHERE id=?", (did,))
    clear_state(uid)
    send(m.chat.id, "✅ Screenshot sent. Admin will verify and add your balance.",
         KB([B("🏠 Menu", "menu", style=BLUE)]))
    u = get_user(uid)
    for a in set([OWNER_ID] + [r["uid"] for r in q("SELECT uid FROM admins", fetch="all")]):
        try:
            bot.copy_message(
                a,
                m.chat.id,
                m.message_id,
                parse_mode="HTML",
                caption=f"💳 <b>Deposit #{did}</b>\n👤 {esc(u['first_name'])} (<code>{uid}</code>)\n"
                f"💰 {money(d['amount'])}",
                reply_markup=KB(
                    [
                        B("✅ Approve", f"a_dp:ok:{did}", style=GREEN),
                        B("❌ Reject", f"a_dp:no:{did}", style=RED),
                    ]
                ),
            )
        except Exception as e:
            log.warning("deposit forward fail: %s", e)


@cb("dep_chk")
def cb_dep_chk(c, args=None):
    did = int(args[0])
    d = q("SELECT * FROM deposits WHERE id=? AND uid=?", (did, c.from_user.id), "one")
    if not d:
        ans(c, "Not found", True)
        return
    if d["status"] == "pending":
        check_deposit(d)
        d = q("SELECT * FROM deposits WHERE id=?", (did,), "one")
    msg = {
        "paid": "✅ Payment received! Balance added.",
        "pending": "⏳ Payment not received yet. Try again in a minute.",
        "expired": "⌛ This payment order expired.",
        "rejected": "❌ Payment rejected.",
    }.get(d["status"], d["status"])
    ans(c, msg, True)


# ==========================================================================
#  SUPPORT / HELP / DASHBOARD / STATUS / MY REFERS / REPORT PROBLEM
# ==========================================================================
START_TIME = time.time()


def tme(val):
    """Turn @name / t.me/name / full link into a clickable https link."""
    val = (val or "").strip()
    if not val:
        return ""
    if val.startswith("http"):
        return val
    if val.startswith("t.me/"):
        return "https://" + val
    return "https://t.me/" + val.lstrip("@")


def fmt_uptime(sec):
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    mnt = r // 60
    return f"{d}d {h}h {mnt}m"


@cb("myrefs")
def cb_myrefs(c):
    uid = c.from_user.id
    u = get_user(uid)
    rows = q(
        "SELECT first_name, username, joined, ref_rewarded FROM users "
        "WHERE referred_by=? ORDER BY joined DESC LIMIT 15",
        (uid,),
        "all",
    )
    text = (
        "👥 <b>My Refers</b>\n━━━━━━━━━━━━━━━\n"
        f"Total refers: <b>{u['ref_count']}</b>\n"
        f"Earned: <b>{money(u['ref_earned'])}</b>\n\n"
    )
    if not rows:
        text += "You have not invited anyone yet.\nShare your invite link to start earning!"
    else:
        text += "<b>Latest invited users</b>\n"
        for r in rows:
            nm = r["first_name"] or r["username"] or "User"
            mark = "✅" if r["ref_rewarded"] else "⏳"
            text += f"{mark} {esc(nm)} • {fdate(r['joined'])}\n"
        text += "\n✅ = reward paid   ⏳ = waiting to join channels"
    render(
        c,
        text,
        KB([B("🔗 Invite Link", "invite", style=GREEN)], [back_btn()]),
    )


@cb("dashboard")
def cb_dashboard(c):
    uid = c.from_user.id
    u = get_user(uid)
    day = now() - 86400
    today = q(
        "SELECT COALESCE(SUM(amount),0) s FROM txns WHERE uid=? AND ts>? AND amount>0 "
        "AND kind IN ('referral','daily','target','redeem')",
        (uid, day),
        "one",
    )["s"]
    refs24 = q("SELECT COUNT(*) n FROM users WHERE referred_by=? AND joined>?", (uid, day), "one")["n"]
    rank = q("SELECT COUNT(*) n FROM users WHERE ref_count>?", (u["ref_count"],), "one")["n"] + 1
    wp = q(
        "SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM withdrawals WHERE uid=? AND status='pending'",
        (uid,),
        "one",
    )
    bought = q("SELECT COUNT(*) n FROM orders WHERE uid=?", (uid,), "one")["n"]
    wait = u["last_daily"] + 86400 - now()
    if not on("daily_on"):
        daily = "OFF"
    elif wait > 0:
        daily = f"in {wait // 3600}h {(wait % 3600) // 60}m"
    else:
        daily = "Ready to claim ✅"
    text = (
        "📊 <b>My Dashboard</b>\n━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>{money(u['balance'])}</b>\n"
        f"📅 Earned (last 24h): {money(today)}\n"
        f"👥 Refers: {u['ref_count']} (+{refs24} today)\n"
        f"🏆 Your refer rank: <b>#{rank}</b>\n"
        f"🎁 Next daily bonus: {daily}\n"
        f"💸 Pending withdraw: {wp['n']} ({money(wp['s'])})\n"
        f"🔑 Keys bought: {bought}\n"
        f"⭐ Premium: {'Active' if u['premium_until'] > now() else 'No'}"
    )
    render(
        c,
        text,
        KB(
            [B("🎁 Extra Rewards", "extra", style=GREEN), B("💸 Withdraw", "withdraw", style=RED)],
            [B("🔄 Refresh", "dashboard", style=BLUE)],
            [back_btn()],
        ),
    )


@cb("status")
def cb_status(c):
    uid = c.from_user.id
    total = q("SELECT COUNT(*) n FROM users", fetch="one")["n"]
    paid = q("SELECT COALESCE(SUM(amount),0) s FROM withdrawals WHERE status='paid'", fetch="one")["s"]
    last = q("SELECT * FROM withdrawals WHERE uid=? ORDER BY id DESC LIMIT 1", (uid,), "one")
    mine = "No withdraw yet"
    if last:
        icon = {"pending": "⏳", "paid": "✅", "rejected": "❌"}.get(last["status"], "•")
        mine = f"{icon} #{last['id']} {money(last['amount'])} — {last['status'].title()}"
    pay = []
    if on("auto_pay_on"):
        pay.append("Auto")
    if on("upi_on") and S("upi_id"):
        pay.append("UPI")
    text = (
        "📡 <b>Bot Status</b>\n━━━━━━━━━━━━━━━\n"
        f"🤖 Bot: {onoff(on('bot_on'))}\n"
        f"⏱ Uptime: {fmt_uptime(time.time() - START_TIME)}\n"
        f"👥 Total users: <b>{total}</b>\n"
        f"💸 Total paid out: <b>{money(paid)}</b>\n\n"
        f"💸 Withdraw: {onoff(on('withdraw_on'))}\n"
        f"🎁 Daily Bonus: {onoff(on('daily_on'))}\n"
        f"🎯 Target: {onoff(on('target_on'))}\n"
        f"🎟 Redeem: {onoff(on('redeem_on'))}\n"
        f"🛒 Shop: {onoff(on('shop_on'))}\n"
        f"⭐ Premium: {onoff(on('premium_on'))}\n"
        f"💳 Deposit: {onoff(bool(pay))} {('(' + ' + '.join(pay) + ')') if pay else ''}\n\n"
        f"📌 <b>My last withdraw:</b> {mine}"
    )
    render(c, text, KB([B("🔄 Refresh", "status", style=BLUE)], [back_btn()]))


def owner_contact():
    """Return (text_line, url) for the owner / support contact."""
    url = ""
    label = ""
    if S("support"):
        url = tme(S("support"))
        label = S("support") if S("support").startswith("@") else url
    elif OWNER_ID:
        ou = get_user(OWNER_ID)
        uname = (ou["username"] if ou and ou["username"] else "") or ""
        if uname:
            url = "https://t.me/" + uname.lstrip("@")
            label = "@" + uname.lstrip("@")
        else:
            url = f"tg://user?id={OWNER_ID}"
            label = "Owner"
    return label, url


@cb("support")
def cb_support(c):
    rows = []
    label, url = owner_contact()
    if url:
        rows.append([B("👑 Contact Owner", url=url, style=GREEN)])
    if S("updates_channel"):
        rows.append([B("📢 Daily Updates Channel", url=tme(S("updates_channel")), style=BLUE)])
    rows.append([B("🐞 Report a Problem", "report", style=RED)])
    rows.append([back_btn()])
    text = "🆘 <b>Support & Help</b>\n━━━━━━━━━━━━━━━\n" + esc(S("help_text"))
    if label:
        if url.startswith("tg://"):
            text += f"\n\n👑 <b>Owner Contact:</b> <a href=\"{url}\">Tap to message owner</a>"
        else:
            text += f"\n\n👑 <b>Owner Contact:</b> {esc(label)}"
    render(c, text, KB(*rows))


@cb("report")
def cb_report(c):
    set_state(c.from_user.id, "report")
    render(
        c,
        "🐞 <b>Report a Problem</b>\n\nDescribe your problem / error in one message "
        "(payment issue, key not working, bug...).\nAdmin will contact you.",
        KB([back_btn("support", "❌ Cancel")]),
    )


@on_state("report")
def st_report(m, st):
    uid = m.from_user.id
    if m.content_type != "text" or len(m.text.strip()) < 5:
        send(m.chat.id, "❌ Please send your problem as a text message (min 5 letters), or /cancel")
        return
    clear_state(uid)
    u = get_user(uid)
    notify_admins(
        f"🐞 <b>Problem Report</b>\n👤 {esc(u['first_name'])} {('@' + u['username']) if u['username'] else ''} "
        f"(<code>{uid}</code>)\n💰 Balance: {money(u['balance'])}\n\n{esc(m.text.strip()[:1500])}",
        KB([B("✉️ Reply to user", f"a_u:msg:{uid}", style=GREEN)]),
    )
    send(m.chat.id, "✅ Your report was sent to admin. We will reply soon.", KB([B("🏠 Menu", "menu", style=BLUE)]))


# ==========================================================================
#  PAYMENT GATEWAYS  (PhonePe + ZapUPI)  and  AUTO CHECKER
# ==========================================================================
def _pp_sign(path):
    raw = (path + PHONEPE_SALT_KEY).encode()
    return hashlib.sha256(raw).hexdigest() + "###" + PHONEPE_SALT_INDEX


def phonepe_create(uid, amount, order_id):
    """Create a PhonePe pay page. Returns payment url or None."""
    if not (PHONEPE_MERCHANT_ID and PHONEPE_SALT_KEY):
        log.warning("PhonePe keys are not set")
        return None
    redirect = PUBLIC_URL or f"https://t.me/{ME_USERNAME}"
    payload = {
        "merchantId": PHONEPE_MERCHANT_ID,
        "merchantTransactionId": order_id,
        "merchantUserId": f"U{uid}",
        "amount": int(round(amount * 100)),
        "redirectUrl": redirect,
        "redirectMode": "REDIRECT",
        "callbackUrl": (PUBLIC_URL + "/phonepe_callback") if PUBLIC_URL else redirect,
        "paymentInstrument": {"type": "PAY_PAGE"},
    }
    b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    path = "/pg/v1/pay"
    headers = {
        "Content-Type": "application/json",
        "X-VERIFY": hashlib.sha256((b64 + path + PHONEPE_SALT_KEY).encode()).hexdigest()
        + "###"
        + PHONEPE_SALT_INDEX,
    }
    try:
        host = PHONEPE_HOST.get(PHONEPE_ENV, PHONEPE_HOST["prod"])
        r = requests.post(host + path, json={"request": b64}, headers=headers, timeout=25)
        j = r.json()
        if j.get("success"):
            return j["data"]["instrumentResponse"]["redirectInfo"]["url"]
        log.warning("PhonePe create failed: %s", j)
    except Exception as e:
        log.warning("PhonePe create error: %s", e)
    return None


def phonepe_paid(order_id):
    """True / False / None(unknown)."""
    try:
        path = f"/pg/v1/status/{PHONEPE_MERCHANT_ID}/{order_id}"
        headers = {
            "Content-Type": "application/json",
            "X-VERIFY": _pp_sign(path),
            "X-MERCHANT-ID": PHONEPE_MERCHANT_ID,
        }
        host = PHONEPE_HOST.get(PHONEPE_ENV, PHONEPE_HOST["prod"])
        j = requests.get(host + path, headers=headers, timeout=20).json()
        code = j.get("code", "")
        if code == "PAYMENT_SUCCESS":
            return True
        if code in ("PAYMENT_ERROR", "PAYMENT_DECLINED", "TIMED_OUT"):
            return False
    except Exception as e:
        log.warning("PhonePe status error: %s", e)
    return None


def zapupi_create(uid, amount, order_id):
    if not (ZAPUPI_TOKEN and ZAPUPI_SECRET):
        log.warning("ZapUPI keys are not set")
        return None
    data = {
        "token_key": ZAPUPI_TOKEN,
        "secret_key": ZAPUPI_SECRET,
        "amount": str(int(amount) if amount == int(amount) else amount),
        "order_id": order_id,
        "custom_mobile": "9999999999",
        "redirect_url": PUBLIC_URL or f"https://t.me/{ME_USERNAME}",
        "remark": f"deposit {uid}",
    }
    try:
        j = requests.post(ZAPUPI_BASE + "/create-order", data=data, timeout=25).json()
        if str(j.get("status", "")).lower() == "success":
            return j.get("payment_url") or (j.get("data") or {}).get("payment_url")
        log.warning("ZapUPI create failed: %s", j)
    except Exception as e:
        log.warning("ZapUPI create error: %s", e)
    return None


def zapupi_paid(order_id):
    try:
        data = {"token_key": ZAPUPI_TOKEN, "secret_key": ZAPUPI_SECRET, "order_id": order_id}
        j = requests.post(ZAPUPI_BASE + "/order-status", data=data, timeout=20).json()
        d = j.get("data") or {}
        st = str(d.get("status", j.get("status", ""))).lower()
        if st in ("success", "paid", "completed"):
            return True
        if st in ("failed", "cancelled", "expired"):
            return False
    except Exception as e:
        log.warning("ZapUPI status error: %s", e)
    return None


def credit_deposit(d):
    """Credit a deposit exactly once."""
    n = q(
        "UPDATE deposits SET status='paid' WHERE id=? AND status='pending'",
        (d["id"],),
        "count",
    )
    if not n:
        return False
    add_balance(d["uid"], d["amount"], "deposit", f"Deposit #{d['id']} ({d['method']})")
    q("UPDATE users SET total_deposit=total_deposit+? WHERE uid=?", (d["amount"], d["uid"]))
    notify(
        d["uid"],
        f"✅ <b>Deposit successful</b>\n💰 {money(d['amount'])} added to your balance.",
        KB([B("🏠 Menu", "menu", style=BLUE)]),
    )
    return True


def check_deposit(d):
    if d["method"] == "phonepe":
        res = phonepe_paid(d["order_id"])
    elif d["method"] == "zapupi":
        res = zapupi_paid(d["order_id"])
    else:
        return
    if res is True:
        credit_deposit(d)
    elif res is False:
        q("UPDATE deposits SET status='rejected' WHERE id=? AND status='pending'", (d["id"],))
    elif now() - d["ts"] > AUTO_PAY_EXPIRY:
        q("UPDATE deposits SET status='expired' WHERE id=? AND status='pending'", (d["id"],))


def poller():
    """Background thread: auto-credit gateway payments."""
    while True:
        try:
            rows = q(
                "SELECT * FROM deposits WHERE status='pending' AND method IN ('phonepe','zapupi')",
                fetch="all",
            )
            for d in rows:
                check_deposit(d)
            for o in q("SELECT * FROM clone_orders WHERE status='pending' AND method IN ('phonepe','zapupi')", fetch="all"):
                check_clone_order(o)
        except Exception as e:
            log.warning("poller error: %s", e)
        time.sleep(POLL_EVERY)


# ==========================================================================
#  CLONE BOTS  ("Add Bot")
#  A user sends his own bot token + pays -> an exact copy of this bot runs
#  for him in its OWN process with its OWN database. He is the admin of it.
#  A supervisor thread keeps every clone alive (auto restart + auto start
#  after every redeploy). Owner checks status from "My Bots".
# ==========================================================================
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
CLONE_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "clones")
CLONE_PROCS = {}   # clone_id -> {"proc": Popen, "started": ts, "log": file}
CLONE_FAILS = {}   # clone_id -> consecutive quick crashes
CLONE_NEXT = {}    # clone_id -> earliest time for next restart


def clone_db_path(cid):
    return os.path.join(CLONE_DIR, f"clone_{cid}.db")


def clone_log_path(cid):
    return os.path.join(CLONE_DIR, f"clone_{cid}.log")


def tg_get_me(token):
    """Validate a bot token with Telegram. Returns (username, error)."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
        if r.get("ok"):
            return r["result"]["username"], None
        return None, r.get("description", "Invalid token")
    except Exception as e:
        return None, str(e)


def clone_env(cl):
    """Environment for the clone process. Payment-gateway keys are NOT passed."""
    skip = ("PHONEPE_", "ZAPUPI_", "BOT_TOKEN", "OWNER_ID", "DB_PATH", "CLONE_", "PUBLIC_URL", "PORT")
    env = {k: v for k, v in os.environ.items() if not k.startswith(skip)}
    env.update(
        {
            "CLONE_MODE": "1",
            "CLONE_ID": str(cl["id"]),
            "BOT_TOKEN": cl["token"],
            "OWNER_ID": str((cl["admin_uid"] if "admin_uid" in cl.keys() and cl["admin_uid"] else cl["owner_uid"])),
            "DB_PATH": clone_db_path(cl["id"]),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def stop_clone_proc(cid):
    info = CLONE_PROCS.pop(cid, None)
    if not info:
        return
    try:
        if info["proc"].poll() is None:
            info["proc"].terminate()
            try:
                info["proc"].wait(timeout=8)
            except Exception:
                info["proc"].kill()
    except Exception:
        pass
    try:
        info["log"].close()
    except Exception:
        pass


def start_clone(cl):
    stop_clone_proc(cl["id"])
    os.makedirs(CLONE_DIR, exist_ok=True)
    lp = clone_log_path(cl["id"])
    try:
        if os.path.exists(lp) and os.path.getsize(lp) > 2_000_000:
            os.remove(lp)
    except Exception:
        pass
    logf = open(lp, "ab")
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__)],
        env=clone_env(cl),
        stdout=logf,
        stderr=subprocess.STDOUT,
        cwd=os.path.dirname(os.path.abspath(__file__)) or None,
    )
    CLONE_PROCS[cl["id"]] = {"proc": proc, "started": time.time(), "log": logf}
    q("UPDATE clones SET last_start=? WHERE id=?", (now(), cl["id"]))
    log.info("Clone #%s (@%s) started, pid %s", cl["id"], cl["username"], proc.pid)


def clone_running(cid):
    info = CLONE_PROCS.get(cid)
    return bool(info and info["proc"].poll() is None)


def clone_last_error(cid):
    try:
        with open(clone_log_path(cid), "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
        bad = [x for x in lines if any(w in x for w in ("ERROR", "Error", "Traceback", "Exception", "Unauthorized"))]
        return (bad[-1] if bad else "")[:200]
    except Exception:
        return ""


def clone_user_count(cid):
    try:
        con = sqlite3.connect(f"file:{clone_db_path(cid)}?mode=ro", uri=True, timeout=3)
        n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0


def clone_supervisor():
    """Keeps all active clones running. Runs only in the main bot."""
    time.sleep(3)
    while True:
        try:
            for cl in q("SELECT * FROM clones WHERE status='active'", fetch="all"):
                cid = cl["id"]
                info = CLONE_PROCS.get(cid)
                if info and info["proc"].poll() is None:
                    if time.time() - info["started"] > 120:
                        CLONE_FAILS[cid] = 0
                    continue
                if info:  # process exited
                    code = info["proc"].poll()
                    CLONE_PROCS.pop(cid, None)
                    try:
                        info["log"].close()
                    except Exception:
                        pass
                    if code == 3:  # token invalid / revoked
                        q("UPDATE clones SET status='invalid' WHERE id=?", (cid,))
                        notify(
                            cl["owner_uid"],
                            f"⚠️ <b>Your bot @{esc(cl['username'])} stopped</b>\n"
                            f"Its token is invalid or revoked. Open <b>My Bots → Change Token</b> "
                            f"and send the new token.",
                        )
                        continue
                    CLONE_FAILS[cid] = CLONE_FAILS.get(cid, 0) + 1
                    q(
                        "UPDATE clones SET restarts=restarts+1, last_error=? WHERE id=?",
                        (clone_last_error(cid), cid),
                    )
                    CLONE_NEXT[cid] = time.time() + min(300, 10 * 2 ** CLONE_FAILS[cid])
                if time.time() < CLONE_NEXT.get(cid, 0):
                    continue
                start_clone(cl)
        except Exception as e:
            log.warning("clone supervisor error: %s", e)
        time.sleep(10)


def active_clone_count():
    return q("SELECT COUNT(*) n FROM clones WHERE status!='deleted'", fetch="one")["n"]


def clone_icon(cl):
    if cl["status"] == "stopped":
        return "🔴"
    if cl["status"] == "invalid":
        return "⚠️"
    return "🟢" if clone_running(cl["id"]) else "🟡"


# --------------------------------------------------------------------------
#  ORDERS + PAYMENT
# --------------------------------------------------------------------------
def oname(o):
    return ("@" + o["username"]) if o["username"] else "new bot (token pending)"


def clone_token_prompt():
    return (
        "🔑 <b>Step 1 of 2 — Bot Token</b>\n━━━━━━━━━━━━━━━\n"
        "1️⃣ Open @BotFather → /newbot and create your bot\n"
        "2️⃣ Copy the <b>bot token</b> (looks like <code>123456789:AAxxxxxxxx</code>)\n"
        "3️⃣ Send the token here now\n\n"
        "🔐 We delete your message right after reading it.\n"
        "Send /cancel to stop (you can continue later from <b>My Bots</b>)."
    )


def deploy_order(oid):
    """Payment confirmed (only once). Next the buyer sends bot token + admin id.
    Returns the order id, or None when it was already processed."""
    with _lock:
        o = q("SELECT * FROM clone_orders WHERE id=?", (oid,), "one")
        if not o:
            return None
        n = q("UPDATE clone_orders SET status='paid' WHERE id=? AND status='pending'", (oid,), "count")
        if not n:
            return None
    set_state(o["uid"], "clone_token", oid=oid)
    notify(
        o["uid"],
        f"✅ <b>Payment approved!</b> ({money(o['amount'])})\n\n" + clone_token_prompt(),
        KB([B("🔑 Send Bot Token", f"clone_setup:{oid}", style=GREEN)]),
    )
    notify_admins(f"💰 <b>Clone payment approved</b> • order #{oid} • user <code>{o['uid']}</code> • {money(o['amount'])}\nWaiting for the user's bot token + admin id.")
    return oid


def finish_clone_setup(uid, chat_id, oid, admin_uid):
    """Token + admin id are ready: create the clone and start it."""
    with _lock:
        o = q("SELECT * FROM clone_orders WHERE id=? AND uid=?", (oid, uid), "one")
        if not o or o["status"] != "paid" or not o["token"]:
            send(chat_id, "❌ This order is not ready. Open <b>My Bots</b> and try again.")
            return None
        if q("SELECT 1 FROM clones WHERE token=? AND status!='deleted'", (o["token"],), "one"):
            send(chat_id, "❌ This bot token is already in use. Send a different token.")
            set_state(uid, "clone_token", oid=oid)
            q("UPDATE clone_orders SET token='', username='' WHERE id=?", (oid,))
            return None
        n = q("UPDATE clone_orders SET status='done' WHERE id=? AND status='paid'", (oid,), "count")
        if not n:
            return None
        cid = q(
            "INSERT INTO clones(owner_uid,admin_uid,token,username,status,created,paid) VALUES(?,?,?,?,?,?,?)",
            (uid, admin_uid, o["token"], o["username"], "active", now(), o["amount"]),
        )
    cl = q("SELECT * FROM clones WHERE id=?", (cid,), "one")
    try:
        start_clone(cl)
    except Exception as e:
        log.error("clone start failed: %s", e)
    send(
        chat_id,
        f"🎉 <b>Your bot is live!</b>\n\n🤖 @{esc(o['username'])}\n👑 Admin ID: <code>{admin_uid}</code>\n\n"
        f"1️⃣ Open @{esc(o['username'])} and send /start\n"
        f"2️⃣ Send /admin there — the admin ID above is the owner of that bot\n"
        f"3️⃣ Set your UPI, channels, products and prices from your admin panel\n\n"
        f"📱 Check status anytime: <b>My Bots</b>.",
        KB([B("🔗 Open Bot", url=f"https://t.me/{o['username']}", style=GREEN)], [B("📱 My Bots", "clone_my", style=BLUE)]),
    )
    notify_admins(f"🤖 <b>New clone #{cid}</b> @{esc(o['username'])} • buyer <code>{uid}</code> • admin <code>{admin_uid}</code> • {money(o['amount'])}")
    return cid


def check_clone_order(o):
    if o["method"] == "phonepe":
        res = phonepe_paid(o["order_id"])
    elif o["method"] == "zapupi":
        res = zapupi_paid(o["order_id"])
    else:
        return
    if res is True:
        deploy_order(o["id"])
    elif res is False:
        q("UPDATE clone_orders SET status='rejected' WHERE id=? AND status='pending'", (o["id"],))
    elif now() - o["ts"] > AUTO_PAY_EXPIRY and o["method"] in ("phonepe", "zapupi"):
        q("UPDATE clone_orders SET status='expired' WHERE id=? AND status='pending'", (o["id"],))


def send_clone_payment(chat_id, oid):
    o = q("SELECT * FROM clone_orders WHERE id=?", (oid,), "one")
    amt = o["amount"]
    rows = []
    manual = bool(S("upi_id"))
    if on("auto_pay_on"):
        rows.append([B("⚡ Auto Pay (PhonePe / UPI)", f"clone_pay:auto:{oid}", style=GREEN)])
    rows.append([B(f"💰 Pay {money(amt)} from Wallet", f"clone_pay:wallet:{oid}", style=BLUE)])
    if manual:
        rows.append([B("📸 I Paid — Send Screenshot", f"clone_pay:ss:{oid}", style=GREEN)])
    rows.append([B("❌ Cancel", f"clone_pay:cancel:{oid}", style=RED)])
    caption = f"🤖 <b>Add Bot — Order #{oid}</b>\n━━━━━━━━━━━━━━━\n💰 Amount: <b>{money(amt)}</b>\n\n"
    if manual:
        caption += (
            f"📲 Scan the QR with any UPI app and pay <b>exactly {money(amt)}</b>\n"
            f"UPI ID: <code>{esc(S('upi_id'))}</code>\n\n"
            "After paying tap <b>I Paid</b> and send the screenshot.\n"
            "✅ Once payment is approved, the bot will ask for your <b>bot token</b> and <b>admin ID</b> "
            "and then start your bot."
        )
    else:
        caption += "Choose how you want to pay 👇"
        notify_admins("⚠️ Someone tried to buy a clone but <b>UPI ID is not set</b>. Set it in Admin → UPI & Payment so the QR can be generated.")
    mk = KB(*rows)
    if manual:
        qr = make_qr(upi_uri(amt, o["order_id"]))
        if qr:
            try:
                bot.send_photo(chat_id, qr, caption=caption, reply_markup=mk)
                return
            except Exception as e:
                log.warning("qr send failed: %s", e)
        else:
            caption += f"\n\n(QR could not be generated — pay to the UPI ID above.)"
    send(chat_id, caption, mk)


@cb("clone_add")
def cb_clone_add(c):
    if CLONE_MODE or not on("clone_on"):
        ans(c, "❌ Add Bot is OFF right now.", True)
        return
    uid = c.from_user.id
    waiting = q("SELECT id FROM clone_orders WHERE uid=? AND status='paid' ORDER BY id DESC LIMIT 1", (uid,), "one")
    if waiting:
        cb_clone_setup(c, [str(waiting["id"])])
        return
    if active_clone_count() >= SI("max_clones"):
        ans(c, "❌ Server is full right now. Please try later.", True)
        return
    if not S("upi_id") and not on("auto_pay_on"):
        notify_admins("⚠️ A user tapped <b>Add Bot</b> but <b>UPI ID is not set</b>, so no QR can be made.\n"
                      "Set it now: <code>/setupi yourname@bank Store Name</code>")
    render(
        c,
        f"🤖 <b>Add Your Own Bot — {money(SF('clone_price'))}</b>\n━━━━━━━━━━━━━━━\n"
        "You get an exact copy of this bot running 24/7 with your own users, "
        "admin panel, channels, UPI and products.\n\n"
        "<b>Steps</b>\n"
        f"1️⃣ Pay {money(SF('clone_price'))} using the QR we generate\n"
        "2️⃣ After payment is approved, send your <b>bot token</b> (from @BotFather)\n"
        "3️⃣ Send your <b>admin ID</b> (Telegram numeric ID)\n"
        "4️⃣ Your bot starts automatically 🚀",
        KB(
            [B(f"💳 Pay {money(SF('clone_price'))} & Continue", "clone_buy", style=GREEN)],
            [back_btn("menu", "❌ Cancel")],
        ),
    )


@cb("clone_buy")
def cb_clone_buy(c):
    if CLONE_MODE or not on("clone_on"):
        ans(c, "❌ Add Bot is OFF right now.", True)
        return
    uid = c.from_user.id
    if active_clone_count() >= SI("max_clones"):
        ans(c, "❌ Server is full right now. Please try later.", True)
        return
    q("UPDATE clone_orders SET status='cancelled' WHERE uid=? AND status='pending'", (uid,))
    oid = q(
        "INSERT INTO clone_orders(uid,token,username,amount,method,order_id,status,ts) VALUES(?,?,?,?,?,?,?,?)",
        (uid, "", "", SF("clone_price"), "manual", f"CLN{uid}{now()}", "pending", now()),
    )
    ans(c)
    send_clone_payment(c.message.chat.id, oid)


@cb("clone_setup")
def cb_clone_setup(c, args=None):
    oid = int(args[0])
    uid = c.from_user.id
    o = q("SELECT * FROM clone_orders WHERE id=? AND uid=?", (oid, uid), "one")
    if not o or o["status"] != "paid":
        ans(c, "This order is not waiting for setup.", True)
        return
    set_state(uid, "clone_token", oid=oid)
    ans(c)
    send(c.message.chat.id, clone_token_prompt(), KB([back_btn("clone_my", "❌ Cancel")]))


@on_state("clone_token")
def st_clone_token(m, st):
    uid = m.from_user.id
    token = (m.text or "").strip()
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:
        pass
    o = q("SELECT * FROM clone_orders WHERE id=? AND uid=?", (st.get("oid"), uid), "one")
    if not o or o["status"] != "paid":
        clear_state(uid)
        send(m.chat.id, "❌ No paid order is waiting. Open <b>Add Bot</b> to start.")
        return
    if not TOKEN_RE.match(token):
        send(m.chat.id, "❌ This does not look like a bot token. Send the token from @BotFather, or /cancel")
        return
    if token == BOT_TOKEN:
        send(m.chat.id, "❌ You cannot use this bot's own token.")
        return
    if q("SELECT 1 FROM clones WHERE token=? AND status!='deleted'", (token,), "one"):
        send(m.chat.id, "❌ This bot token is already added. Send another token.")
        return
    username, err = tg_get_me(token)
    if not username:
        send(m.chat.id, f"❌ Telegram rejected this token: {esc(err)}\nSend the correct token or /cancel")
        return
    q("UPDATE clone_orders SET token=?, username=? WHERE id=?", (token, username, o["id"]))
    set_state(uid, "clone_admin", oid=o["id"])
    send(
        m.chat.id,
        f"✅ Token verified: <b>@{esc(username)}</b>\n\n"
        "👑 <b>Step 2 of 2 — Admin ID</b>\n━━━━━━━━━━━━━━━\n"
        "Send the <b>numeric Telegram ID</b> of the person who will be the admin/owner of this bot.\n"
        "Get it from @userinfobot (send /start there).\n\n"
        "Or tap the button to use your own ID.",
        KB(
            [B(f"👤 Use my ID ({uid})", f"clone_selfid:{o['id']}", style=GREEN)],
            [back_btn("clone_my", "❌ Cancel")],
        ),
    )


@on_state("clone_admin")
def st_clone_admin(m, st):
    uid = m.from_user.id
    txt = (m.text or "").strip()
    if not re.fullmatch(r"\d{5,15}", txt):
        send(m.chat.id, "❌ Admin ID must be numbers only (example: 123456789). Get it from @userinfobot, or /cancel")
        return
    clear_state(uid)
    finish_clone_setup(uid, m.chat.id, st.get("oid"), int(txt))


@cb("clone_selfid")
def cb_clone_selfid(c, args=None):
    uid = c.from_user.id
    clear_state(uid)
    ans(c)
    finish_clone_setup(uid, c.message.chat.id, int(args[0]), uid)


@cb("clone_pay")
def cb_clone_pay(c, args=None):
    method, oid = args[0], int(args[1])
    uid = c.from_user.id
    o = q("SELECT * FROM clone_orders WHERE id=? AND uid=?", (oid, uid), "one")
    if not o or o["status"] != "pending":
        ans(c, "This order is not active anymore.", True)
        return
    chat = c.message.chat.id
    if method == "cancel":
        q("UPDATE clone_orders SET status='cancelled' WHERE id=?", (oid,))
        render(c, "❌ Order cancelled.", KB([B("🏠 Menu", "menu", style=BLUE)]))
        return
    if method == "wallet":
        if not deduct_balance(uid, o["amount"], "clone", f"Add Bot @{o['username']}"):
            ans(c, f"❌ Not enough balance. You need {money(o['amount'])}.", True)
            return
        cid = deploy_order(oid)
        if not cid:
            add_balance(uid, o["amount"], "refund", f"Add Bot order #{oid} failed")
            ans(c, "❌ Order already processed. Balance refunded.", True)
            return
        render(c, "✅ Paid from wallet. Now send your bot token and admin ID to start your bot 👇", KB([B("🔑 Send Bot Token", f"clone_setup:{oid}", style=GREEN)]))
        return
    if method == "auto":
        if not on("auto_pay_on"):
            ans(c, "❌ Auto payment is OFF.", True)
            return
        gw = S("gateway")
        new_oid = f"{o['order_id']}A{str(now())[-5:]}"
        url = phonepe_create(uid, o["amount"], new_oid) if gw == "phonepe" else zapupi_create(uid, o["amount"], new_oid)
        if not url:
            ans(c, "❌ Gateway error. Use QR / wallet instead.", True)
            return
        q("UPDATE clone_orders SET method=?, order_id=?, ts=? WHERE id=?", (gw, new_oid, now(), oid))
        send(
            chat,
            f"⚡ <b>Auto Payment</b> — Order #{oid}\n💰 {money(o['amount'])}\n\n"
            f"Tap <b>Pay Now</b>. Your bot starts <b>automatically</b> after payment.",
            KB(
                [B("💳 Pay Now", url=url, style=GREEN)],
                [B("🔄 Check Status", f"clone_chk:{oid}", style=BLUE)],
            ),
        )
        return
    if method == "ss":
        set_state(uid, "clone_ss", oid=oid)
        send(chat, "📸 Send the <b>payment screenshot</b> now (photo).")


@on_state("clone_ss")
def st_clone_ss(m, st):
    uid = m.from_user.id
    if m.content_type not in ("photo", "document"):
        send(m.chat.id, "❌ Please send the payment screenshot as a photo, or /cancel")
        return
    o = q("SELECT * FROM clone_orders WHERE id=?", (st["oid"],), "one")
    clear_state(uid)
    if not o or o["status"] != "pending":
        return
    send(m.chat.id, "✅ Screenshot sent. After admin approves, I will ask for your bot token and admin ID.",
         KB([B("📱 My Bots", "clone_my", style=GREEN)]))
    u = get_user(uid)
    for a in set([OWNER_ID] + [r["uid"] for r in q("SELECT uid FROM admins", fetch="all")]):
        try:
            bot.copy_message(
                a,
                m.chat.id,
                m.message_id,
                parse_mode="HTML",
                caption=f"🤖 <b>Clone order #{o['id']}</b>\n👤 {esc(u['first_name'])} (<code>{uid}</code>)\n"
                f"Add Bot payment\n💰 {money(o['amount'])}",
                reply_markup=KB(
                    [
                        B("✅ Approve", f"a_cl:ok:{o['id']}", style=GREEN),
                        B("❌ Reject", f"a_cl:no:{o['id']}", style=RED),
                    ]
                ),
            )
        except Exception as e:
            log.warning("clone proof forward fail: %s", e)


@cb("clone_chk")
def cb_clone_chk(c, args=None):
    oid = int(args[0])
    o = q("SELECT * FROM clone_orders WHERE id=? AND uid=?", (oid, c.from_user.id), "one")
    if not o:
        ans(c, "Not found", True)
        return
    if o["status"] == "pending":
        check_clone_order(o)
        o = q("SELECT * FROM clone_orders WHERE id=?", (oid,), "one")
    msg = {
        "paid": "✅ Payment received! Open My Bots to send your bot token.",
        "pending": "⏳ Payment not received yet. Try again in a minute.",
        "expired": "⌛ This payment expired. Create a new order.",
        "rejected": "❌ Payment failed.",
    }.get(o["status"], o["status"])
    ans(c, msg, True)


# --------------------------------------------------------------------------
#  MY BOTS (status / start / stop / restart / change token / delete)
# --------------------------------------------------------------------------
def get_clone_for(c, cid):
    cl = q("SELECT * FROM clones WHERE id=? AND status!='deleted'", (cid,), "one")
    if not cl or (cl["owner_uid"] != c.from_user.id and not is_admin(c.from_user.id)):
        ans(c, "Bot not found", True)
        return None
    return cl


@cb("clone_my")
def cb_clone_my(c):
    uid = c.from_user.id
    rows_db = q("SELECT * FROM clones WHERE owner_uid=? AND status!='deleted' ORDER BY id", (uid,), "all")
    text = "📱 <b>My Bots</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    waiting = q("SELECT id FROM clone_orders WHERE uid=? AND status='paid' ORDER BY id", (uid,), "all")
    for w in waiting:
        text += f"⏳ Order #{w['id']} is paid — send your bot token + admin ID to start it.\n\n"
        rows.append([B(f"🔑 Complete Setup (order #{w['id']})", f"clone_setup:{w['id']}", style=GREEN)])
    if not rows_db and not waiting:
        text += "You have no cloned bots yet.\nTap <b>Add Bot</b> to create yours."
    for cl in rows_db:
        rows.append([B(f"{clone_icon(cl)} @{cl['username']}", f"clone_view:{cl['id']}", style=BLUE)])
    if on("clone_on") and not CLONE_MODE:
        rows.append([B(f"➕ Add Bot ({money(SF('clone_price'))})", "clone_add", style=GREEN)])
    rows.append([back_btn()])
    render(c, text, KB(*rows))


@cb("clone_view")
def cb_clone_view(c, args=None):
    cid = int(args[0])
    cl = get_clone_for(c, cid)
    if not cl:
        return
    running = clone_running(cid)
    if cl["status"] == "stopped":
        st = "🔴 Stopped"
    elif cl["status"] == "invalid":
        st = "⚠️ Token invalid — change token"
    elif running:
        up = fmt_uptime(time.time() - CLONE_PROCS[cid]["started"])
        st = f"🟢 Running (uptime {up})"
    else:
        st = "🟡 Starting..."
    text = (
        f"🤖 <b>@{esc(cl['username'])}</b>\n━━━━━━━━━━━━━━━\n"
        f"Status: <b>{st}</b>\n"
        f"👥 Users in this bot: <b>{clone_user_count(cid)}</b>\n"
        f"🔁 Auto restarts: {cl['restarts']}\n"
        f"📅 Created: {fdate(cl['created'])}\n"
        f"💰 Paid: {money(cl['paid'])}\n"
    )
    if is_admin(c.from_user.id):
        text += f"👤 Buyer: <code>{cl['owner_uid']}</code>\n"
        if "admin_uid" in cl.keys() and cl["admin_uid"]:
            text += f"👑 Bot admin: <code>{cl['admin_uid']}</code>\n"
    if cl["last_error"]:
        text += f"\n⚠️ Last error: <code>{esc(cl['last_error'])}</code>"
    toggle = (
        B("▶️ Start", f"clone_act:start:{cid}", style=GREEN)
        if cl["status"] == "stopped"
        else B("⏹ Stop", f"clone_act:stop:{cid}", style=RED)
    )
    back = "clone_my" if cl["owner_uid"] == c.from_user.id else "a_cl_menu"
    render(
        c,
        text,
        KB(
            [B("🔗 Open Bot", url=f"https://t.me/{cl['username']}", style=GREEN), B("🔄 Refresh", f"clone_view:{cid}", style=BLUE)],
            [B("♻️ Restart", f"clone_act:restart:{cid}", style=BLUE), toggle],
            [B("🔑 Change Token", f"clone_tok:{cid}", style=BLUE), B("🗑 Delete", f"clone_del:{cid}", style=RED)],
            [back_btn(back)],
        ),
    )


@cb("clone_act")
def cb_clone_act(c, args=None):
    action, cid = args[0], int(args[1])
    cl = get_clone_for(c, cid)
    if not cl:
        return
    if action == "stop":
        q("UPDATE clones SET status='stopped' WHERE id=?", (cid,))
        stop_clone_proc(cid)
        ans(c, "⏹ Bot stopped")
    else:
        q("UPDATE clones SET status='active' WHERE id=?", (cid,))
        CLONE_FAILS[cid] = 0
        CLONE_NEXT[cid] = 0
        start_clone(q("SELECT * FROM clones WHERE id=?", (cid,), "one"))
        ans(c, "▶️ Bot started" if action == "start" else "♻️ Restarting...")
        time.sleep(2)
    cb_clone_view(c, [str(cid)])


@cb("clone_tok")
def cb_clone_tok(c, args=None):
    cid = int(args[0])
    if not get_clone_for(c, cid):
        return
    set_state(c.from_user.id, "clone_newtok", cid=cid)
    render(
        c,
        "🔑 Send the <b>new bot token</b> for this bot (free). We delete your message after reading it.",
        KB([back_btn(f"clone_view:{cid}", "❌ Cancel")]),
    )


@on_state("clone_newtok")
def st_clone_newtok(m, st):
    uid = m.from_user.id
    token = (m.text or "").strip()
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:
        pass
    cl = q("SELECT * FROM clones WHERE id=? AND status!='deleted'", (st["cid"],), "one")
    if not cl or (cl["owner_uid"] != uid and not is_admin(uid)):
        clear_state(uid)
        return
    if not TOKEN_RE.match(token):
        send(m.chat.id, "❌ Invalid token format. Send again or /cancel")
        return
    if q("SELECT 1 FROM clones WHERE token=? AND status!='deleted' AND id!=?", (token, cl["id"]), "one"):
        send(m.chat.id, "❌ This token is already used by another bot.")
        return
    username, err = tg_get_me(token)
    if not username:
        send(m.chat.id, f"❌ Telegram rejected this token: {esc(err)}")
        return
    clear_state(uid)
    q("UPDATE clones SET token=?, username=?, status='active', last_error='' WHERE id=?", (token, username, cl["id"]))
    CLONE_FAILS[cl["id"]] = 0
    CLONE_NEXT[cl["id"]] = 0
    start_clone(q("SELECT * FROM clones WHERE id=?", (cl["id"],), "one"))
    send(m.chat.id, f"✅ Token updated. @{esc(username)} is restarting.",
         KB([B("📱 My Bots", "clone_my", style=GREEN)]))


@cb("clone_del")
def cb_clone_del(c, args=None):
    cid = int(args[0])
    cl = get_clone_for(c, cid)
    if not cl:
        return
    render(
        c,
        f"🗑 <b>Delete @{esc(cl['username'])}?</b>\nThe bot will stop and <b>no refund</b> is given.",
        KB(
            [B("✅ Yes, delete", f"clone_delok:{cid}", style=RED)],
            [back_btn(f"clone_view:{cid}", "⬅️ No, go back")],
        ),
    )


@cb("clone_delok")
def cb_clone_delok(c, args=None):
    cid = int(args[0])
    cl = get_clone_for(c, cid)
    if not cl:
        return
    stop_clone_proc(cid)
    q("UPDATE clones SET status='deleted', token='' WHERE id=?", (cid,))
    ans(c, "🗑 Deleted")
    if cl["owner_uid"] == c.from_user.id:
        cb_clone_my(c)
    else:
        cb_a_cl_menu(c)


# --------------------------------------------------------------------------
#  ADMIN: CLONES
# --------------------------------------------------------------------------
@cb("a_cl_menu", admin=True)
def cb_a_cl_menu(c):
    rows_db = q("SELECT * FROM clones WHERE status!='deleted' ORDER BY id DESC LIMIT 25", fetch="all")
    pend = q("SELECT * FROM clone_orders WHERE status='pending' ORDER BY id DESC LIMIT 8", fetch="all")
    run = sum(1 for cl in rows_db if clone_running(cl["id"]))
    income = q("SELECT COALESCE(SUM(paid),0) s FROM clones", fetch="one")["s"]
    text = (
        "🤖 <b>Clone Bots</b>\n━━━━━━━━━━━━━━━\n"
        f"Total: {len(rows_db)}   🟢 Running: {run}\n"
        f"Max allowed: {SI('max_clones')}   Price: {money(SF('clone_price'))}\n"
        f"💰 Clone income: {money(income)}\n"
    )
    if not S("upi_id"):
        text += "\n⚠️ <b>UPI ID not set</b> — QR cannot be generated; buyers can only pay from wallet. Set it in UPI & Payment.\n"
    rows = []
    for cl in rows_db:
        rows.append([B(f"{clone_icon(cl)} @{cl['username']} • {cl['owner_uid']}", f"clone_view:{cl['id']}", style=BLUE)])
    if pend:
        text += "\n⏳ <b>Pending orders</b>\n"
        for o in pend:
            text += f"#{o['id']} • <code>{o['uid']}</code> • {o['method']}\n"
            rows.append(
                [
                    B(f"✅ #{o['id']}", f"a_cl:ok:{o['id']}", style=GREEN),
                    B(f"❌ #{o['id']}", f"a_cl:no:{o['id']}", style=RED),
                ]
            )
    rows.append([B("🔄 Refresh", "a_cl_menu", style=BLUE)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_cl", admin=True)
def cb_a_cl(c, args=None):
    action, oid = args[0], int(args[1])
    o = q("SELECT * FROM clone_orders WHERE id=?", (oid,), "one")
    if not o or o["status"] != "pending":
        ans(c, "Already processed", True)
        return
    if action == "ok":
        cid = deploy_order(oid)
        ans(c, "✅ Approved — user will now send token + admin ID" if cid else "❌ Already processed", True)
    else:
        q("UPDATE clone_orders SET status='rejected' WHERE id=?", (oid,))
        notify(o["uid"], f"❌ <b>Add Bot order #{oid} rejected</b>\nContact support if you already paid.")
        ans(c, "❌ Rejected")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:
        pass


# ==========================================================================
#  ADMIN PANEL  -  HOME + DASHBOARD
# ==========================================================================
def admin_home_text():
    return (
        "🛡 <b>Admin Panel</b>\n━━━━━━━━━━━━━━━\n"
        f"🤖 Bot: {onoff(on('bot_on'))}\n"
        f"🎬 Video: {onoff(on('video_on'))}\n"
        f"🏦 UPI: {onoff(on('upi_on'))}   ⚡ Auto: {onoff(on('auto_pay_on'))}\n\n"
        "Choose a section 👇"
    )


def admin_home_markup():
    return KB(
        [B("📊 Dashboard", "a_dash", style=BLUE)],
        [B("🔘 ON/OFF Switches", "a_tg_menu", style=GREEN), B("🔢 Amounts", "a_num_menu", style=BLUE)],
        [B("📢 Channels", "a_ch_menu", style=BLUE), B("🎬 Welcome Setup", "a_wel_menu", style=GREEN)],
        [B("🛒 Products & Keys", "a_pr_menu", style=GREEN), B("🎟 Redeem Codes", "a_cd_menu", style=BLUE)],
        [B("⭐ Premium Plans", "a_pl_menu", style=BLUE), B("💳 UPI & Payment", "a_pay_menu", style=GREEN)],
        [B("💸 Withdrawals", "a_wd_menu", style=RED), B("💵 Deposits", "a_dp_menu", style=RED)],
        [B("👥 Users", "a_us_menu", style=BLUE), B("📤 Broadcast", "a_bc", style=GREEN)],
        [B("👑 Admins", "a_adm_menu", style=BLUE), B("📁 Export", "a_exp_menu", style=GREEN)],
        [B("🆘 Support & Links", "a_sup_menu", style=BLUE)]
        + ([] if CLONE_MODE else [B("🤖 Clones", "a_cl_menu", style=GREEN)]),
        [B("🏠 User Menu", "menu", style=RED)],
    )


def to_admin(label="⬅️ Admin Panel"):
    return B(label, "adm", style=RED)


@cb("adm", admin=True)
def cb_adm(c):
    render(c, admin_home_text(), admin_home_markup())


@cb("a_dash", admin=True)
def cb_dash(c):
    day = now() - 86400
    r = lambda sql, a=(): q(sql, a, "one")
    users = r("SELECT COUNT(*) n, COALESCE(SUM(balance),0) b FROM users")
    new24 = r("SELECT COUNT(*) n FROM users WHERE joined>?", (day,))["n"]
    prem = r("SELECT COUNT(*) n FROM users WHERE premium_until>?", (now(),))["n"]
    banned = r("SELECT COUNT(*) n FROM users WHERE banned=1")["n"]
    refs = r("SELECT COALESCE(SUM(ref_count),0) n, COALESCE(SUM(ref_earned),0) e FROM users")
    wp = r("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM withdrawals WHERE status='pending'")
    wd = r("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM withdrawals WHERE status='paid'")
    dp = r("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM deposits WHERE status='paid'")
    od = r("SELECT COUNT(*) n, COALESCE(SUM(price),0) s FROM orders")
    keys = r("SELECT COUNT(*) n FROM keys WHERE sold=0")["n"]
    text = (
        "📊 <b>Dashboard</b>\n━━━━━━━━━━━━━━━\n"
        f"👥 Users: <b>{users['n']}</b> (+{new24} in 24h)\n"
        f"⭐ Premium: {prem}   🚫 Banned: {banned}\n"
        f"💰 Users' total balance: {money(users['b'])}\n\n"
        f"🔗 Total refers: {refs['n']} (paid {money(refs['e'])})\n"
        f"💸 Pending withdraw: {wp['n']} ({money(wp['s'])})\n"
        f"✅ Paid withdraw: {wd['n']} ({money(wd['s'])})\n"
        f"💳 Deposits: {dp['n']} ({money(dp['s'])})\n"
        f"🛒 Sales: {od['n']} ({money(od['s'])})\n"
        f"🔑 Keys in stock: {keys}\n"
        f"📢 Force channels: {len(get_channels())}"
    )
    render(c, text, KB([B("🔄 Refresh", "a_dash", style=GREEN)], [to_admin()]))


# ==========================================================================
#  ADMIN: ON/OFF SWITCHES
# ==========================================================================
def toggles_markup():
    rows = []
    pair = []
    for key, label in TOGGLES:
        if CLONE_MODE and key in ("auto_pay_on", "clone_on"):
            continue
        pair.append(B(f"{label}: {'ON' if on(key) else 'OFF'}", f"a_tg:{key}", style=GREEN if on(key) else RED))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([to_admin()])
    return KB(*rows)


@cb("a_tg_menu", admin=True)
def cb_tg_menu(c):
    render(c, "🔘 <b>ON / OFF Switches</b>\nTap a button to switch.", toggles_markup())


@cb("a_tg", admin=True)
def cb_tg(c, args=None):
    key = args[0]
    if key not in dict(TOGGLES):
        return
    flip(key)
    ans(c, f"{dict(TOGGLES)[key]} → {'ON' if on(key) else 'OFF'}")
    cb_tg_menu(c)


# ==========================================================================
#  ADMIN: NUMBERS / TEXTS
# ==========================================================================
@cb("a_num_menu", admin=True)
def cb_num_menu(c):
    rows = []
    text = "🔢 <b>Amounts & Limits</b>\n━━━━━━━━━━━━━━━\n"
    for key, label in NUMERICS:
        if CLONE_MODE and key in ("clone_price", "max_clones"):
            continue
        text += f"{label}: <b>{S(key)}</b>\n"
        rows.append([B(f"✏️ {label}", f"a_num:{key}", style=BLUE)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_num", admin=True)
def cb_num(c, args=None):
    key = args[0]
    set_state(c.from_user.id, "a_num", key=key)
    render(
        c,
        f"✏️ Send new value for <b>{dict(NUMERICS)[key]}</b>\nCurrent: <b>{S(key)}</b>",
        KB([back_btn("a_num_menu", "❌ Cancel")]),
    )


@on_state("a_num")
def st_a_num(m, st):
    v = parse_float(m.text)
    if v is None or v < 0:
        send(m.chat.id, "❌ Send a valid number or /cancel")
        return
    set_S(st["key"], f"{v:g}")
    clear_state(m.from_user.id)
    send(m.chat.id, f"✅ Updated: <b>{dict(NUMERICS)[st['key']]}</b> = {v:g}",
         KB([B("🔢 Amounts", "a_num_menu", style=BLUE)]))


@cb("a_txt", admin=True)
def cb_txt(c, args=None):
    key = args[0]
    set_state(c.from_user.id, "a_txt", key=key)
    render(
        c,
        f"✏️ Send new value:\n<b>{TEXT_KEYS[key]}</b>\n\nCurrent: <code>{esc(S(key)) or '-'}</code>\n"
        f"Send <code>-</code> to clear.",
        KB([back_btn(TXT_BACK.get(key, "a_pay_menu"), "❌ Cancel")]),
    )


@on_state("a_txt")
def st_a_txt(m, st):
    val = (m.text or "").strip()
    if val == "-":
        val = ""
    if st["key"] == "upi_id" and val and not re.match(r"^[A-Za-z0-9._\-]{2,}@[A-Za-z0-9]{2,}$", val):
        send(m.chat.id, "❌ Invalid UPI ID. Example: <code>name@upi</code> or <code>9876543210@ybl</code>\nSend again or /cancel")
        return
    set_S(st["key"], val)
    clear_state(m.from_user.id)
    back = TXT_BACK.get(st["key"], "a_pay_menu")
    if st["key"] in ("upi_id", "upi_name") and S("upi_id"):
        send(m.chat.id, "✅ Saved. Tap <b>Preview QR</b> to see the payment QR users will get.",
             KB([B("👁 Preview QR", "a_upi_qr", style=GREEN)], [B("⬅️ Back to settings", back, style=BLUE)]))
        return
    send(m.chat.id, "✅ Saved.", KB([B("⬅️ Back to settings", back, style=GREEN)]))


# ==========================================================================
#  ADMIN: UPI & PAYMENT
# ==========================================================================
@cb("a_pay_menu", admin=True)
def cb_pay_menu(c):
    gw = S("gateway")
    text = (
        "💳 <b>UPI & Payment</b>\n━━━━━━━━━━━━━━━\n"
        f"🏦 Manual UPI: {onoff(on('upi_on'))}\n"
        f"UPI ID: <code>{esc(S('upi_id')) or '-'}</code>\n"
        f"Name: {esc(S('upi_name'))}\n"
        f"⚡ Auto Pay: {onoff(on('auto_pay_on'))}\n"
        f"Gateway: <b>{gw}</b>\n"
        f"Support: {esc(S('support')) or '-'}\n"
        f"Payout channel: {esc(S('payout_channel')) or '-'}"
    )
    if not S("upi_id"):
        text += "\n\n⚠️ <b>UPI ID is not set.</b> Users cannot pay by QR until you set it (Add Bot orders too)."
    render(
        c,
        text,
        KB(
            [B(f"🏦 UPI: {'ON' if on('upi_on') else 'OFF'}", "a_tg:upi_on", style=GREEN if on("upi_on") else RED),
             B(f"⚡ Auto: {'ON' if on('auto_pay_on') else 'OFF'}", "a_tg:auto_pay_on", style=GREEN if on("auto_pay_on") else RED)],
            [B("✏️ Set UPI ID", "a_txt:upi_id", style=BLUE), B("✏️ UPI Name", "a_txt:upi_name", style=BLUE)],
            [B("👁 Preview QR", "a_upi_qr", style=GREEN)],
            [B(("✅ " if gw == "phonepe" else "") + "PhonePe", "a_gw:phonepe", style=GREEN),
             B(("✅ " if gw == "zapupi" else "") + "ZapUPI", "a_gw:zapupi", style=GREEN)],
            [B("✏️ Support", "a_txt:support", style=BLUE), B("✏️ Payout Channel", "a_txt:payout_channel", style=BLUE)],
            [to_admin()],
        ),
    )


@cb("a_gw", admin=True)
def cb_gw(c, args=None):
    set_S("gateway", args[0])
    ans(c, f"Gateway: {args[0]}")
    cb_pay_menu(c)


# ==========================================================================
#  ADMIN: SUPPORT & LINKS
# ==========================================================================
TXT_BACK = {
    "support": "a_sup_menu",
    "updates_channel": "a_sup_menu",
    "help_text": "a_sup_menu",
    "welcome_text": "a_wel_menu",
}


@cb("a_sup_menu", admin=True)
def cb_sup_menu(c):
    text = (
        "🆘 <b>Support & Links</b>\n━━━━━━━━━━━━━━━\n"
        f"💬 Support: <code>{esc(S('support')) or '-'}</code>\n"
        f"📢 Daily updates channel: <code>{esc(S('updates_channel')) or '-'}</code>\n\n"
        f"❓ Help text:\n{esc(S('help_text'))}"
    )
    render(
        c,
        text,
        KB(
            [B("✏️ Support Username", "a_txt:support", style=BLUE)],
            [B("✏️ Updates Channel", "a_txt:updates_channel", style=BLUE)],
            [B("✏️ Help Text", "a_txt:help_text", style=BLUE)],
            [to_admin()],
        ),
    )


# ==========================================================================
#  ADMIN: FORCE JOIN CHANNELS
# ==========================================================================
@cb("a_ch_menu", admin=True)
def cb_ch_menu(c):
    chs = get_channels()
    text = "📢 <b>Force Join Channels</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not chs:
        text += "No channels added."
    for ch in chs:
        text += f"• {esc(ch['title'])} — <code>{esc(ch['chat_id'])}</code>\n"
        rows.append([B(f"🗑 Remove {ch['title']}", f"a_ch_del:{ch['id']}", style=RED)])
    text += "\n⚠️ Add the bot as <b>admin</b> in each channel."
    rows.append([B("➕ Add Channel", "a_ch_add", style=GREEN)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_ch_add", admin=True)
def cb_ch_add(c):
    set_state(c.from_user.id, "a_ch_add")
    render(
        c,
        "➕ Send channel <b>@username</b> or <b>-100xxxxxxxxxx</b> id.\n"
        "Bot must be admin in that channel.",
        KB([back_btn("a_ch_menu", "❌ Cancel")]),
    )


@on_state("a_ch_add")
def st_ch_add(m, st):
    ref = (m.text or "").strip()
    try:
        chat = bot.get_chat(ref)
    except Exception as e:
        send(m.chat.id, f"❌ Cannot find channel: {esc(e)}\nSend again or /cancel")
        return
    link = f"https://t.me/{chat.username}" if chat.username else None
    if not link:
        try:
            link = bot.export_chat_invite_link(chat.id)
        except Exception:
            link = getattr(chat, "invite_link", None)
    if not link:
        send(m.chat.id, "❌ Could not create an invite link. Make the bot admin and retry.")
        return
    q(
        "INSERT INTO channels(chat_id,title,link) VALUES(?,?,?)",
        (str(chat.id), chat.title or ref, link),
    )
    clear_state(m.from_user.id)
    send(m.chat.id, f"✅ Channel added: <b>{esc(chat.title)}</b>", KB([B("📢 Channels", "a_ch_menu", style=BLUE)]))


@cb("a_ch_del", admin=True)
def cb_ch_del(c, args=None):
    q("DELETE FROM channels WHERE id=?", (int(args[0]),))
    ans(c, "🗑 Removed")
    cb_ch_menu(c)


# ==========================================================================
#  ADMIN: WELCOME SETUP (text / video / photo)
# ==========================================================================
@cb("a_wel_menu", admin=True)
def cb_wel_menu(c):
    text = (
        "🎬 <b>Welcome Setup</b>\n━━━━━━━━━━━━━━━\n"
        f"Video shown on /start: {onoff(on('video_on'))}\n"
        f"Video saved: {'✅' if S('welcome_video') else '❌'}   "
        f"Photo saved: {'✅' if S('welcome_photo') else '❌'}\n\n"
        f"Text:\n{esc(S('welcome_text'))}"
    )
    render(
        c,
        text,
        KB(
            [B(f"🎬 Video: {'ON' if on('video_on') else 'OFF'}", "a_tg:video_on", style=GREEN if on("video_on") else RED)],
            [B("✏️ Welcome Text", "a_txt:welcome_text", style=BLUE)],
            [B("🎥 Set Video", "a_wel_set:video", style=GREEN), B("🖼 Set Photo", "a_wel_set:photo", style=GREEN)],
            [B("🗑 Clear Media", "a_wel_clear", style=RED)],
            [to_admin()],
        ),
    )


@cb("a_wel_set", admin=True)
def cb_wel_set(c, args=None):
    kind = args[0]
    set_state(c.from_user.id, "a_wel", kind=kind)
    render(c, f"Send the welcome <b>{kind}</b> now.", KB([back_btn("a_wel_menu", "❌ Cancel")]))


@on_state("a_wel")
def st_wel(m, st):
    kind = st["kind"]
    fid = None
    if kind == "video" and m.content_type in ("video", "animation"):
        fid = (m.video or m.animation).file_id
    elif kind == "photo" and m.content_type == "photo":
        fid = m.photo[-1].file_id
    if not fid:
        send(m.chat.id, f"❌ Please send a {kind}, or /cancel")
        return
    set_S("welcome_video" if kind == "video" else "welcome_photo", fid)
    clear_state(m.from_user.id)
    send(m.chat.id, "✅ Saved.", KB([B("🎬 Welcome Setup", "a_wel_menu", style=BLUE)]))


@cb("a_wel_clear", admin=True)
def cb_wel_clear(c):
    set_S("welcome_video", "")
    set_S("welcome_photo", "")
    ans(c, "🗑 Cleared")
    cb_wel_menu(c)


# ==========================================================================
#  ADMIN: PRODUCTS & KEYS
# ==========================================================================
@cb("a_pr_menu", admin=True)
def cb_pr_menu(c):
    prods = q("SELECT * FROM products ORDER BY id", fetch="all")
    text = "🛒 <b>Products & Keys</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not prods:
        text += "No products yet."
    for p in prods:
        flag = "🟢" if p["active"] else "🔴"
        rows.append([B(f"{flag} {p['name']} • {money(p['price'])} • stock {stock_of(p['id'])}", f"a_pr_view:{p['id']}", style=BLUE)])
    rows.append([B("➕ Add Product", "a_pr_add", style=GREEN)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_pr_add", admin=True)
def cb_pr_add(c):
    set_state(c.from_user.id, "a_pr_add")
    render(
        c,
        "➕ Send product like this:\n\n<code>Name | price | description</code>\n\n"
        "Example:\n<code>Premium Key 1 Day | 50 | Works on all servers</code>",
        KB([back_btn("a_pr_menu", "❌ Cancel")]),
    )


@on_state("a_pr_add")
def st_pr_add(m, st):
    parts = [x.strip() for x in (m.text or "").split("|")]
    if len(parts) < 2 or parse_float(parts[1]) is None:
        send(m.chat.id, "❌ Wrong format. Use: Name | price | description")
        return
    pid = q(
        "INSERT INTO products(name,price,descr) VALUES(?,?,?)",
        (parts[0], parse_float(parts[1]), parts[2] if len(parts) > 2 else ""),
    )
    clear_state(m.from_user.id)
    send(m.chat.id, f"✅ Product #{pid} added. Now add keys.",
         KB([B("🔑 Add Keys", f"a_pr_keys:{pid}", style=GREEN)], [B("🛒 Products", "a_pr_menu", style=BLUE)]))


@cb("a_pr_view", admin=True)
def cb_pr_view(c, args=None):
    pid = int(args[0])
    p = q("SELECT * FROM products WHERE id=?", (pid,), "one")
    if not p:
        return
    sold = q("SELECT COUNT(*) n FROM keys WHERE product_id=? AND sold=1", (pid,), "one")["n"]
    text = (
        f"📦 <b>{esc(p['name'])}</b>\n💰 {money(p['price'])}\n{esc(p['descr'])}\n\n"
        f"🔑 In stock: <b>{stock_of(pid)}</b>   ✅ Sold: {sold}\n"
        f"Status: {onoff(p['active'])}"
    )
    render(
        c,
        text,
        KB(
            [B("🔑 Add Keys", f"a_pr_keys:{pid}", style=GREEN)],
            [B("🔁 ON/OFF", f"a_pr_tg:{pid}", style=BLUE), B("🗑 Delete", f"a_pr_del:{pid}", style=RED)],
            [back_btn("a_pr_menu")],
        ),
    )


@cb("a_pr_keys", admin=True)
def cb_pr_keys(c, args=None):
    set_state(c.from_user.id, "a_pr_keys", pid=int(args[0]))
    render(
        c,
        "🔑 Send keys — <b>one key per line</b> (you can paste hundreds at once).",
        KB([back_btn(f"a_pr_view:{args[0]}", "❌ Cancel")]),
    )


@on_state("a_pr_keys")
def st_pr_keys(m, st):
    lines = [x.strip() for x in (m.text or "").splitlines() if x.strip()]
    if not lines:
        send(m.chat.id, "❌ Send at least one key, or /cancel")
        return
    with _lock:
        for ln in lines:
            q("INSERT INTO keys(product_id,key_text) VALUES(?,?)", (st["pid"], ln))
    clear_state(m.from_user.id)
    send(m.chat.id, f"✅ {len(lines)} keys added. Stock: {stock_of(st['pid'])}",
         KB([B("📦 Product", f"a_pr_view:{st['pid']}", style=BLUE)]))


@cb("a_pr_tg", admin=True)
def cb_pr_tg(c, args=None):
    q("UPDATE products SET active=1-active WHERE id=?", (int(args[0]),))
    cb_pr_view(c, args)


@cb("a_pr_del", admin=True)
def cb_pr_del(c, args=None):
    pid = int(args[0])
    q("DELETE FROM keys WHERE product_id=? AND sold=0", (pid,))
    q("DELETE FROM products WHERE id=?", (pid,))
    ans(c, "🗑 Deleted")
    cb_pr_menu(c)


# ==========================================================================
#  ADMIN: REDEEM CODES
# ==========================================================================
@cb("a_cd_menu", admin=True)
def cb_cd_menu(c):
    rows_db = q("SELECT * FROM codes ORDER BY created DESC LIMIT 15", fetch="all")
    text = "🎟 <b>Redeem Codes</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not rows_db:
        text += "No codes yet."
    for r in rows_db:
        text += f"<code>{r['code']}</code> • {money(r['amount'])} • {r['used']}/{r['max_uses']}\n"
        rows.append([B(f"🗑 {r['code']}", f"a_cd_del:{r['code']}", style=RED)])
    rows.append([B("➕ Create Code", "a_cd_new", style=GREEN)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_cd_new", admin=True)
def cb_cd_new(c):
    set_state(c.from_user.id, "a_cd_new")
    render(
        c,
        "➕ Send: <code>amount | max_uses | custom_code(optional)</code>\n\n"
        "Example: <code>10 | 100</code> or <code>25 | 50 | DIWALI25</code>",
        KB([back_btn("a_cd_menu", "❌ Cancel")]),
    )


@on_state("a_cd_new")
def st_cd_new(m, st):
    parts = [x.strip() for x in (m.text or "").split("|")]
    amt = parse_float(parts[0]) if parts else None
    uses = parse_float(parts[1]) if len(parts) > 1 else None
    if amt is None or uses is None:
        send(m.chat.id, "❌ Wrong format. Use: amount | max_uses | code(optional)")
        return
    code = parts[2].upper() if len(parts) > 2 and parts[2] else gen_code()
    if q("SELECT 1 FROM codes WHERE code=?", (code,), "one"):
        send(m.chat.id, "❌ This code already exists. Send another.")
        return
    q("INSERT INTO codes(code,amount,max_uses,created) VALUES(?,?,?,?)", (code, amt, int(uses), now()))
    clear_state(m.from_user.id)
    send(m.chat.id, f"✅ Code created: <code>{code}</code>\n💰 {money(amt)} • {int(uses)} uses",
         KB([B("🎟 Codes", "a_cd_menu", style=BLUE)]))


@cb("a_cd_del", admin=True)
def cb_cd_del(c, args=None):
    q("DELETE FROM codes WHERE code=?", (args[0],))
    ans(c, "🗑 Deleted")
    cb_cd_menu(c)


# ==========================================================================
#  ADMIN: PREMIUM PLANS
# ==========================================================================
@cb("a_pl_menu", admin=True)
def cb_pl_menu(c):
    plans = q("SELECT * FROM plans ORDER BY price", fetch="all")
    text = "⭐ <b>Premium Plans</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not plans:
        text += "No plans yet."
    for p in plans:
        text += f"• {esc(p['name'])} — {p['days']} days — {money(p['price'])}\n"
        rows.append([B(f"🗑 {p['name']}", f"a_pl_del:{p['id']}", style=RED)])
    rows.append([B("➕ Add Plan", "a_pl_add", style=GREEN)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_pl_add", admin=True)
def cb_pl_add(c):
    set_state(c.from_user.id, "a_pl_add")
    render(c, "➕ Send: <code>Name | price | days</code>\nExample: <code>Gold | 99 | 30</code>",
           KB([back_btn("a_pl_menu", "❌ Cancel")]))


@on_state("a_pl_add")
def st_pl_add(m, st):
    parts = [x.strip() for x in (m.text or "").split("|")]
    if len(parts) < 3 or parse_float(parts[1]) is None or parse_float(parts[2]) is None:
        send(m.chat.id, "❌ Wrong format. Use: Name | price | days")
        return
    q("INSERT INTO plans(name,price,days) VALUES(?,?,?)", (parts[0], parse_float(parts[1]), int(parse_float(parts[2]))))
    clear_state(m.from_user.id)
    send(m.chat.id, "✅ Plan added.", KB([B("⭐ Plans", "a_pl_menu", style=BLUE)]))


@cb("a_pl_del", admin=True)
def cb_pl_del(c, args=None):
    q("DELETE FROM plans WHERE id=?", (int(args[0]),))
    ans(c, "🗑 Deleted")
    cb_pl_menu(c)


# ==========================================================================
#  ADMIN: WITHDRAWALS
# ==========================================================================
@cb("a_wd_menu", admin=True)
def cb_wd_menu(c):
    rows_db = q("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id LIMIT 10", fetch="all")
    text = "💸 <b>Pending Withdrawals</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not rows_db:
        text += "No pending requests 🎉"
    for w in rows_db:
        text += f"#{w['id']} • <code>{w['uid']}</code> • {money(w['amount'])} • <code>{esc(w['upi'])}</code>\n"
        rows.append(
            [
                B(f"✅ #{w['id']}", f"a_wd:ok:{w['id']}", style=GREEN),
                B(f"❌ #{w['id']}", f"a_wd:no:{w['id']}", style=RED),
            ]
        )
    rows.append([B("🔄 Refresh", "a_wd_menu", style=BLUE)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_wd", admin=True)
def cb_wd(c, args=None):
    action, wid = args[0], int(args[1])
    w = q("SELECT * FROM withdrawals WHERE id=?", (wid,), "one")
    if not w or w["status"] != "pending":
        ans(c, "Already processed", True)
        return
    if action == "ok":
        q("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,))
        q("UPDATE users SET total_withdraw=total_withdraw+? WHERE uid=?", (w["amount"], w["uid"]))
        notify(
            w["uid"],
            f"✅ <b>Withdraw #{wid} approved</b>\n💰 {money(w['amount'])} sent to "
            f"<code>{esc(w['upi'])}</code>",
        )
        pc = S("payout_channel")
        if pc:
            send(pc, f"✅ <b>Payment Proof</b>\n💰 {money(w['amount'])} paid to user <code>{str(w['uid'])[:-3]}•••</code>")
        ans(c, "✅ Approved")
    else:
        q("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
        add_balance(w["uid"], w["amount"], "refund", f"Withdraw #{wid} rejected")
        notify(w["uid"], f"❌ <b>Withdraw #{wid} rejected</b>\n💰 {money(w['amount'])} refunded to your balance.")
        ans(c, "❌ Rejected & refunded")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:
        pass
    if c.message.text and c.message.text.startswith("💸 Pending"):
        cb_wd_menu(c)


# ==========================================================================
#  ADMIN: DEPOSITS
# ==========================================================================
@cb("a_dp_menu", admin=True)
def cb_dp_menu(c):
    rows_db = q(
        "SELECT * FROM deposits WHERE status='pending' ORDER BY id DESC LIMIT 10", fetch="all"
    )
    text = "💵 <b>Pending Deposits</b>\n━━━━━━━━━━━━━━━\n"
    rows = []
    if not rows_db:
        text += "No pending deposits."
    for d in rows_db:
        text += f"#{d['id']} • <code>{d['uid']}</code> • {money(d['amount'])} • {d['method']}\n"
        if d["method"] == "manual":
            rows.append(
                [
                    B(f"✅ #{d['id']}", f"a_dp:ok:{d['id']}", style=GREEN),
                    B(f"❌ #{d['id']}", f"a_dp:no:{d['id']}", style=RED),
                ]
            )
    rows.append([B("🔄 Refresh", "a_dp_menu", style=BLUE)])
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_dp", admin=True)
def cb_dp(c, args=None):
    action, did = args[0], int(args[1])
    d = q("SELECT * FROM deposits WHERE id=?", (did,), "one")
    if not d or d["status"] != "pending":
        ans(c, "Already processed", True)
        return
    if action == "ok":
        credit_deposit(d)
        ans(c, "✅ Approved & credited")
    else:
        q("UPDATE deposits SET status='rejected' WHERE id=?", (did,))
        notify(d["uid"], f"❌ <b>Deposit #{did} rejected</b>\nContact support if you already paid.")
        ans(c, "❌ Rejected")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:
        pass


# ==========================================================================
#  ADMIN: USERS
# ==========================================================================
@cb("a_us_menu", admin=True)
def cb_us_menu(c):
    set_state(c.from_user.id, "a_us_search")
    render(
        c,
        "👥 <b>Users</b>\n\nSend a user <b>ID</b> or <b>@username</b> to open the user card.",
        KB([to_admin()]),
    )


def find_user(text):
    text = (text or "").strip()
    if text.isdigit():
        return get_user(int(text))
    return q("SELECT * FROM users WHERE username=? COLLATE NOCASE", (text.lstrip("@"),), "one")


def user_card_text(u):
    prem = "Active till " + fdate(u["premium_until"]) if u["premium_until"] > now() else "No"
    refby = u["referred_by"] or "-"
    return (
        f"👤 <b>{esc(u['first_name'])}</b> {('@' + u['username']) if u['username'] else ''}\n"
        f"🆔 <code>{u['uid']}</code>\n"
        f"💰 Balance: <b>{money(u['balance'])}</b>\n"
        f"👥 Refers: {u['ref_count']} (earned {money(u['ref_earned'])})\n"
        f"🔗 Referred by: <code>{refby}</code>\n"
        f"💳 Deposit: {money(u['total_deposit'])}   💸 Withdrawn: {money(u['total_withdraw'])}\n"
        f"⭐ Premium: {prem}\n"
        f"🚫 Banned: {'Yes' if u['banned'] else 'No'}\n"
        f"📅 Joined: {fdate(u['joined'])}"
    )


def user_card_markup(u):
    uid = u["uid"]
    return KB(
        [B("➕ Add Balance", f"a_u:add:{uid}", style=GREEN), B("➖ Deduct", f"a_u:sub:{uid}", style=RED)],
        [
            B("✅ Unban" if u["banned"] else "🚫 Ban", f"a_u:ban:{uid}", style=GREEN if u["banned"] else RED),
            B("⭐ Give Premium", f"a_u:prem:{uid}", style=BLUE),
        ],
        [B("✉️ Message User", f"a_u:msg:{uid}", style=BLUE)],
        [B("🔍 Another User", "a_us_menu", style=BLUE)],
        [to_admin()],
    )


@on_state("a_us_search")
def st_us_search(m, st):
    u = find_user(m.text)
    if not u:
        send(m.chat.id, "❌ User not found. Send ID / @username again or /cancel")
        return
    clear_state(m.from_user.id)
    send(m.chat.id, user_card_text(u), user_card_markup(u))


@cb("a_u", admin=True)
def cb_u(c, args=None):
    action, uid = args[0], int(args[1])
    u = get_user(uid)
    if not u:
        ans(c, "User not found", True)
        return
    if action == "ban":
        if uid == OWNER_ID:
            ans(c, "Cannot ban the owner", True)
            return
        q("UPDATE users SET banned=1-banned WHERE uid=?", (uid,))
        ans(c, "Done")
        u = get_user(uid)
        render(c, user_card_text(u), user_card_markup(u))
        return
    prompts = {
        "add": "➕ Send amount to <b>add</b>",
        "sub": "➖ Send amount to <b>deduct</b>",
        "prem": "⭐ Send number of <b>premium days</b> to give",
        "msg": "✉️ Send the message (text / photo / video / file) for this user",
    }
    set_state(c.from_user.id, "a_u_act", mode=action, uid=uid)
    render(c, prompts[action] + f"\nUser: <code>{uid}</code>", KB([back_btn("a_us_menu", "❌ Cancel")]))


@on_state("a_u_act")
def st_u_act(m, st):
    mode, uid = st["mode"], st["uid"]
    if mode == "msg":
        try:
            bot.copy_message(uid, m.chat.id, m.message_id)
            send(m.chat.id, "✅ Message sent.")
        except Exception as e:
            send(m.chat.id, f"❌ Failed: {esc(e)}")
        clear_state(m.from_user.id)
        return
    v = parse_float(m.text)
    if v is None or v <= 0:
        send(m.chat.id, "❌ Send a positive number or /cancel")
        return
    clear_state(m.from_user.id)
    if mode == "add":
        add_balance(uid, v, "admin", "Added by admin")
        notify(uid, f"💰 Admin added <b>{money(v)}</b> to your balance.")
    elif mode == "sub":
        if not deduct_balance(uid, v, "admin", "Deducted by admin"):
            q("UPDATE users SET balance=0 WHERE uid=?", (uid,))
        notify(uid, f"➖ Admin deducted <b>{money(v)}</b> from your balance.")
    elif mode == "prem":
        u = get_user(uid)
        until = max(now(), u["premium_until"]) + int(v) * 86400
        q("UPDATE users SET premium_until=? WHERE uid=?", (until, uid))
        notify(uid, f"⭐ Admin gave you <b>{int(v)} days</b> of Premium!")
    u = get_user(uid)
    send(m.chat.id, "✅ Done.\n\n" + user_card_text(u), user_card_markup(u))


# ==========================================================================
#  ADMIN: BROADCAST  (text / photo / video / file / anything)
# ==========================================================================
BC_DRAFT = {}


@cb("a_bc", admin=True)
def cb_bc(c):
    set_state(c.from_user.id, "a_bc")
    render(
        c,
        "📤 <b>Broadcast</b>\n\nSend the message you want to deliver to <b>all users</b>.\n"
        "Text, photo, video, file, voice — anything works.",
        KB([to_admin("❌ Cancel")]),
    )


@on_state("a_bc")
def st_bc(m, st):
    BC_DRAFT[m.from_user.id] = (m.chat.id, m.message_id)
    clear_state(m.from_user.id)
    total = q("SELECT COUNT(*) n FROM users WHERE banned=0", fetch="one")["n"]
    send(
        m.chat.id,
        f"📤 Ready to send to <b>{total}</b> users.\nConfirm?",
        KB(
            [B("🚀 Send Now", "a_bc_go", style=GREEN), B("❌ Cancel", "adm", style=RED)],
        ),
    )


def do_broadcast(admin_id, src_chat, src_msg):
    users = q("SELECT uid FROM users WHERE banned=0", fetch="all")
    ok = fail = 0
    for r in users:
        try:
            bot.copy_message(r["uid"], src_chat, src_msg)
            ok += 1
        except ApiTelegramException as e:
            fail += 1
            if e.error_code == 429:
                time.sleep(3)
        except Exception:
            fail += 1
        time.sleep(0.05)
    send(admin_id, f"📤 <b>Broadcast finished</b>\n✅ Delivered: {ok}\n❌ Failed: {fail}")


@cb("a_bc_go", admin=True)
def cb_bc_go(c):
    draft = BC_DRAFT.pop(c.from_user.id, None)
    if not draft:
        ans(c, "No draft found", True)
        return
    ans(c, "🚀 Broadcast started")
    render(c, "🚀 Broadcast running in background...", KB([to_admin()]))
    threading.Thread(target=do_broadcast, args=(c.from_user.id, draft[0], draft[1]), daemon=True).start()


# ==========================================================================
#  ADMIN: MULTIPLE ADMINS (owner only)
# ==========================================================================
@cb("a_adm_menu", admin=True)
def cb_adm_menu(c):
    rows_db = q("SELECT uid FROM admins", fetch="all")
    text = f"👑 <b>Admins</b>\n━━━━━━━━━━━━━━━\nOwner: <code>{OWNER_ID}</code>\n"
    rows = []
    for r in rows_db:
        text += f"• <code>{r['uid']}</code>\n"
        if c.from_user.id == OWNER_ID:
            rows.append([B(f"🗑 Remove {r['uid']}", f"a_adm_del:{r['uid']}", style=RED)])
    if c.from_user.id == OWNER_ID:
        rows.append([B("➕ Add Admin", "a_adm_add", style=GREEN)])
    else:
        text += "\nOnly the owner can add or remove admins."
    rows.append([to_admin()])
    render(c, text, KB(*rows))


@cb("a_adm_add", owner=True)
def cb_adm_add(c):
    set_state(c.from_user.id, "a_adm_add")
    render(c, "➕ Send the Telegram <b>user ID</b> of the new admin.", KB([back_btn("a_adm_menu", "❌ Cancel")]))


@on_state("a_adm_add")
def st_adm_add(m, st):
    t = (m.text or "").strip()
    if not t.isdigit():
        send(m.chat.id, "❌ Send numeric user ID or /cancel")
        return
    q("INSERT OR IGNORE INTO admins(uid,added) VALUES(?,?)", (int(t), now()))
    clear_state(m.from_user.id)
    notify(int(t), "🛡 You are now an <b>admin</b> of this bot. Send /admin")
    send(m.chat.id, "✅ Admin added.", KB([B("👑 Admins", "a_adm_menu", style=BLUE)]))


@cb("a_adm_del", owner=True)
def cb_adm_del(c, args=None):
    q("DELETE FROM admins WHERE uid=?", (int(args[0]),))
    ans(c, "🗑 Removed")
    cb_adm_menu(c)


# ==========================================================================
#  ADMIN: EXPORT
# ==========================================================================
@cb("a_exp_menu", admin=True)
def cb_exp_menu(c):
    render(
        c,
        "📁 <b>Export CSV</b>\nWhich data do you want?",
        KB(
            [B("👥 Users + Refers", "a_exp:users", style=GREEN)],
            [B("💸 Withdrawals", "a_exp:wd", style=BLUE), B("💳 Deposits", "a_exp:dp", style=BLUE)],
            [B("🏆 Top Referrers (text)", "a_exp:top", style=GREEN)],
            [to_admin()],
        ),
    )


def csv_file(name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(list(r))
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    data.name = name
    return data


@cb("a_exp", admin=True)
def cb_exp(c, args=None):
    kind = args[0]
    chat = c.message.chat.id
    ans(c, "Preparing...")
    if kind == "users":
        rows = q("SELECT * FROM users ORDER BY joined", fetch="all")
        f = csv_file(
            "users.csv",
            ["uid", "username", "name", "balance", "refers", "ref_earned", "referred_by",
             "deposit", "withdrawn", "premium_until", "banned", "joined"],
            [[r["uid"], r["username"], r["first_name"], r["balance"], r["ref_count"], r["ref_earned"],
              r["referred_by"], r["total_deposit"], r["total_withdraw"], fdate(r["premium_until"]),
              r["banned"], fdate(r["joined"])] for r in rows],
        )
        bot.send_document(chat, f, caption=f"👥 {len(rows)} users")
    elif kind == "wd":
        rows = q("SELECT * FROM withdrawals ORDER BY id", fetch="all")
        f = csv_file("withdrawals.csv", ["id", "uid", "amount", "upi", "status", "time"],
                     [[r["id"], r["uid"], r["amount"], r["upi"], r["status"], fdate(r["ts"])] for r in rows])
        bot.send_document(chat, f, caption=f"💸 {len(rows)} withdrawals")
    elif kind == "dp":
        rows = q("SELECT * FROM deposits ORDER BY id", fetch="all")
        f = csv_file("deposits.csv", ["id", "uid", "amount", "method", "status", "time"],
                     [[r["id"], r["uid"], r["amount"], r["method"], r["status"], fdate(r["ts"])] for r in rows])
        bot.send_document(chat, f, caption=f"💳 {len(rows)} deposits")
    else:
        rows = q("SELECT uid, first_name, ref_count, ref_earned FROM users WHERE ref_count>0 "
                 "ORDER BY ref_count DESC LIMIT 25", fetch="all")
        text = "🏆 <b>Top 25 Referrers</b>\n━━━━━━━━━━━━━━━\n"
        for i, r in enumerate(rows, 1):
            text += f"{i}. {esc(r['first_name'])} (<code>{r['uid']}</code>) — {r['ref_count']} • {money(r['ref_earned'])}\n"
        send(chat, text if rows else "No referrals yet.")


# ----- inside a clone the payment menu is UPI-only -----
if CLONE_MODE:

    def cb_pay_menu_clone(c):
        text = (
            "💳 <b>UPI & Payment</b>\n━━━━━━━━━━━━━━━\n"
            f"🏦 Manual UPI: {onoff(on('upi_on'))}\n"
            f"UPI ID: <code>{esc(S('upi_id')) or '-'}</code>\n"
            f"Name: {esc(S('upi_name'))}\n"
            f"Support: {esc(S('support')) or '-'}\n"
            f"Payout channel: {esc(S('payout_channel')) or '-'}"
        )
        render(
            c,
            text,
            KB(
                [B(f"🏦 UPI: {'ON' if on('upi_on') else 'OFF'}", "a_tg:upi_on", style=GREEN if on("upi_on") else RED)],
                [B("✏️ Set UPI ID", "a_txt:upi_id", style=BLUE), B("✏️ UPI Name", "a_txt:upi_name", style=BLUE)],
                [B("👁 Preview QR", "a_upi_qr", style=GREEN)],
                [B("✏️ Support", "a_txt:support", style=BLUE), B("✏️ Payout Channel", "a_txt:payout_channel", style=BLUE)],
                [to_admin()],
            ),
        )

    CB["a_pay_menu"] = (cb_pay_menu_clone, True, False, False)


# ==========================================================================
#  ADMIN: PREVIEW PAYMENT QR
# ==========================================================================
@cb("a_upi_qr", admin=True)
def cb_upi_qr(c):
    if not S("upi_id"):
        ans(c, "❌ First set your UPI ID.", True)
        return
    amt = SF("clone_price") if (not CLONE_MODE and SF("clone_price") > 0) else 10.0
    qr = make_qr(upi_uri(amt, "PREVIEW"))
    if not qr:
        ans(c, "❌ QR library missing. Add qrcode[pil] to requirements.txt and redeploy.", True)
        return
    ans(c)
    try:
        bot.send_photo(
            c.message.chat.id,
            qr,
            caption=(
                f"👁 <b>QR Preview</b> ({money(amt)})\n"
                f"UPI ID: <code>{esc(S('upi_id'))}</code>\nName: {esc(S('upi_name'))}\n\n"
                "This is exactly what users see when they pay. Scan it with any UPI app to test "
                "(you can cancel before paying)."
            ),
            reply_markup=KB([B("⬅️ UPI Settings", "a_pay_menu", style=BLUE)]),
        )
    except Exception as e:
        log.warning("qr preview failed: %s", e)
        send(c.message.chat.id, "❌ Could not send QR image.")


# ==========================================================================
#  CALLBACK ROUTER
# ==========================================================================
@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    try:
        data = c.data or ""
        name, *args = data.split(":")
        entry = CB.get(name)
        if not entry:
            ans(c)
            return
        fn, admin_only, owner_only, nogate = entry
        uid = c.from_user.id
        ensure_user(c.from_user)
        if owner_only and uid != OWNER_ID:
            ans(c, "⛔ Owner only", True)
            return
        if admin_only and not is_admin(uid):
            ans(c, "⛔ Admins only", True)
            return
        if not nogate and not gate(uid, c.message.chat.id):
            ans(c)
            return
        clear_state(uid)
        if not CB_FROM_KEYBOARD.get(c.id):
            kb_sync(uid, c.message.chat.id)
        if fn.__code__.co_argcount >= 2:
            fn(c, args)
        else:
            fn(c)
        ans(c)
    except Exception as e:
        log.error("callback error: %s", e, exc_info=True)
        ans(c, "⚠️ Something went wrong, try again.", True)


# ==========================================================================
#  MESSAGE ROUTER  (answers for states + fallback)
# ==========================================================================
ALL_TYPES = ["text", "photo", "video", "document", "audio", "voice", "animation", "sticker", "video_note"]


@bot.message_handler(content_types=ALL_TYPES)
def on_message(m):
    try:
        if m.chat.type != "private":
            return
        uid = m.from_user.id
        ensure_user(m.from_user)
        act = menu_action(m.text) if m.content_type == "text" else None
        if act:
            # a button from the bottom keyboard was tapped
            clear_state(uid)
            if not gate(uid, m.chat.id):
                return
            kb_sync(uid, m.chat.id)
            if act == "adm" and not is_admin(uid):
                return
            fake = types_ns.SimpleNamespace(
                id=f"kb{uid}{m.message_id}", data=act, from_user=m.from_user, message=m
            )
            CB_FROM_KEYBOARD[fake.id] = True
            try:
                on_callback(fake)
            finally:
                CB_FROM_KEYBOARD.pop(fake.id, None)
            return
        st = get_state(uid)
        if st:
            fn = STATE_HANDLERS.get(st["name"])
            if fn:
                if not is_admin(uid) and not gate(uid, m.chat.id):
                    return
                fn(m, st)
                return
            clear_state(uid)
        if gate(uid, m.chat.id):
            send(m.chat.id, "👇 Choose an option from the menu", main_markup(uid))
    except Exception as e:
        log.error("message error: %s", e, exc_info=True)
        send(m.chat.id, "⚠️ Something went wrong, try again.")


# ==========================================================================
#  FLASK KEEP-ALIVE  (+ PhonePe callback endpoint)
# ==========================================================================
app = Flask(__name__)


@app.route("/")
def home():
    return "Earn & Refer bot is running ✅"


@app.route("/health")
def health():
    return "ok", 200


@app.route("/phonepe_callback", methods=["GET", "POST"])
def phonepe_callback():
    # Status is verified by the poller using the status API, so this
    # endpoint only needs to answer 200 to PhonePe.
    return "OK", 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


# ==========================================================================
#  MAIN
# ==========================================================================
def main():
    global ME_USERNAME
    if not OWNER_ID:
        print("❌ Set OWNER_ID (your numeric Telegram id) in Railway → Variables.")
        sys.exit(1)
    init_db()
    try:
        bot.remove_webhook()
    except Exception:
        pass
    log.info("Database: %s", DB_PATH)
    try:
        me = bot.get_me()
    except ApiTelegramException as e:
        if CLONE_MODE and e.error_code in (401, 404):
            print("TOKEN_INVALID", flush=True)
            sys.exit(3)
        raise
    ME_USERNAME = me.username
    log.info("Bot started as @%s", ME_USERNAME)
    if not CLONE_MODE:
        os.makedirs(CLONE_DIR, exist_ok=True)
        threading.Thread(target=run_flask, daemon=True).start()
        threading.Thread(target=poller, daemon=True).start()
        threading.Thread(target=clone_supervisor, daemon=True).start()
        atexit.register(lambda: [stop_clone_proc(i) for i in list(CLONE_PROCS)])
    else:
        log.info("CLONE MODE #%s (owner %s)", CLONE_ID, OWNER_ID)
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
        except Exception as e:
            log.error("polling crashed: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
