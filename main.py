import os, json, time, threading, requests
from datetime import datetime
import pytz
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ---------- কনফিগ ----------
TOKEN = "8697329515:AAHroo2JKtn9Sitzq8F5uxxfte3ZtVoRslI"
ADMIN_USER_ID = "8538304896"  # <-- আপনার টেলিগ্রাম আইডি
API_KEYS_FILE = "api_keys.json"
DATA_FILE = "user_data.json"
TEAMS = ["Brazil", "Argentina", "Germany", "France", "England", "Spain",
         "Portugal", "Netherlands", "Italy", "Belgium", "Croatia", "Uruguay"]
BENGALI_MONTHS = {
    1: "জানুয়ারি", 2: "ফেব্রুয়ারি", 3: "মার্চ", 4: "এপ্রিল",
    5: "মে", 6: "জুন", 7: "জুলাই", 8: "আগস্ট",
    9: "সেপ্টেম্বর", 10: "অক্টোবর", 11: "নভেম্বর", 12: "ডিসেম্বর"
}

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

user_data = {}
predictions = {}       # {match_id: [prediction_entry, ...]}
lock = threading.Lock()
api_keys_lock = threading.Lock()

# ---------- API Keys Management ----------
def load_api_keys():
    try:
        with open(API_KEYS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"keys": [], "current_index": 0, "last_reset_day": None}

def save_api_keys(data):
    with api_keys_lock:
        with open(API_KEYS_FILE, "w") as f:
            json.dump(data, f, indent=2)

api_keys_data = load_api_keys()

def get_current_key():
    global api_keys_data
    with api_keys_lock:
        keys = api_keys_data.get("keys", [])
        if not keys:
            return None
        today = datetime.utcnow().day
        if api_keys_data.get("last_reset_day") != today:
            for k in keys:
                k["usage"] = 0
            api_keys_data["last_reset_day"] = today
            api_keys_data["current_index"] = 0
            save_api_keys(api_keys_data)
        for i in range(len(keys)):
            idx = (api_keys_data["current_index"] + i) % len(keys)
            if keys[idx]["usage"] < 100:
                api_keys_data["current_index"] = idx
                save_api_keys(api_keys_data)
                return keys[idx]["key"]
        return None

def mark_api_used(key):
    global api_keys_data
    with api_keys_lock:
        for k in api_keys_data.get("keys", []):
            if k["key"] == key:
                k["usage"] = k.get("usage", 0) + 1
                break
        save_api_keys(api_keys_data)

# ---------- ইউজার ডাটা ----------
def load_data():
    global user_data
    try:
        with open(DATA_FILE, "r") as f:
            user_data = json.load(f)
    except FileNotFoundError:
        user_data = {}

def save_data():
    with lock:
        with open(DATA_FILE, "w") as f:
            json.dump(user_data, f, indent=2)

load_data()

def get_user(uid):
    uid = str(uid)
    if uid not in user_data:
        user_data[uid] = {
            "followed": [],
            "timezone": "Asia/Dhaka",
            "mute_start": None,
            "mute_end": None,
            "username": None,
            "fav_team": None,
            "notifications_enabled": True,
            "auto_unfollow_times": {},
            "prev_status": {},
            "poll_interval": 90,
            "points": 0
        }
    return user_data[uid]

# ---------- Smart API Helper ----------
def api_get(endpoint):
    max_attempts = len(api_keys_data.get("keys", [])) + 1
    for _ in range(max_attempts):
        key = get_current_key()
        if not key:
            try:
                bot.send_message(ADMIN_USER_ID, "⚠️ সব API কী-র দৈনিক লিমিট শেষ! বট আপাতত বন্ধ।")
            except:
                pass
            return None
        url = "https://free-api-live-football-data.p.rapidapi.com/" + endpoint
        headers = {
            "x-rapidapi-key": key,
            "x-rapidapi-host": "free-api-live-football-data.p.rapidapi.com"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                mark_api_used(key)
                return resp.json()
            elif resp.status_code == 429:
                continue
            else:
                return None
        except:
            return None
    return None

def get_match(mid):
    data = api_get(f"fixtures?id={mid}")
    if not data or "response" not in data or not data["response"]:
        return None
    return data["response"][0]

def get_today_fixtures():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    data = api_get(f"fixtures?date={today}")
    if not data or "response" not in data:
        return []
    return data["response"]

# ---------- সময় কনভার্টার ----------
def utc_to_dhaka(utc_str):
    try:
        utc_time = datetime.strptime(utc_str[:19], "%Y-%m-%dT%H:%M:%S")
        dhaka_tz = pytz.timezone("Asia/Dhaka")
        dhaka_time = utc_time.replace(tzinfo=pytz.utc).astimezone(dhaka_tz)
        month_bn = BENGALI_MONTHS[dhaka_time.month]
        return f"{dhaka_time.day} {month_bn}, {dhaka_time.strftime('%I:%M %p')}"
    except:
        return utc_str[:16].replace("T", " ")

# ---------- কিবোর্ড ----------
def main_reply_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.row(KeyboardButton("⚽ ম্যাচ"), KeyboardButton("📅 আজকের ম্যাচ"))
    kb.row(KeyboardButton("📋 আমার ম্যাচ"), KeyboardButton("⭐ পছন্দের দল"))
    kb.row(KeyboardButton("🧠 প্রেডিকশন"), KeyboardButton("📊 পয়েন্ট"))
    kb.row(KeyboardButton("⚙️ সেটিংস"), KeyboardButton("❓ সাহায্য"))
    return kb

def can_notify(user):
    if not user.get("notifications_enabled", True):
        return False
    if user["mute_start"] is not None and user["mute_end"] is not None:
        tz = pytz.timezone(user["timezone"])
        now = datetime.now(tz).hour
        s, e = user["mute_start"], user["mute_end"]
        if s < e:
            if s <= now < e: return False
        else:
            if now >= s or now < e: return False
    return True

# ---------- প্রেডিকশন রেজলভ ----------
def resolve_predictions(match_id, event_type, event_data=None):
    global predictions, user_data
    resolved_to_notify = []
    with lock:
        if match_id not in predictions:
            return
        preds = predictions[match_id]
        for pred in preds[:]:
            if pred["resolved"]:
                continue
            if pred["type"] == "score_at_minute":
                resolved = False
                correct = None
                if event_type == "minute_reached":
                    current_min = event_data["minute"]
                    if current_min >= pred["minute"]:
                        # মিনিট পার হয়েছে, এখন স্কোর চেক
                        hg = event_data.get("home_goals", 0)
                        ag = event_data.get("away_goals", 0)
                        if pred["home_goals"] == hg and pred["away_goals"] == ag:
                            correct = True
                        else:
                            correct = False
                        resolved = True
                elif event_type == "match_end":
                    # ম্যাচ শেষে এখনও রেজলভ না হলে জোর করে করা
                    hg = event_data.get("home_goals", 0)
                    ag = event_data.get("away_goals", 0)
                    if pred["home_goals"] == hg and pred["away_goals"] == ag:
                        correct = True
                    else:
                        correct = False
                    resolved = True

                if resolved:
                    pred["resolved"] = True
                    pred["correct"] = correct
                    uid = pred["user_id"]
                    user = get_user(uid)
                    if correct:
                        user["points"] = user.get("points", 0) + 10
                        msg = "✅ সঠিক (+10)!"
                    else:
                        user["points"] = user.get("points", 0) - 5
                        msg = "❌ ভুল (-5)!"
                    detail = f"{pred['minute']}′ মিনিটের স্কোর {pred['home_goals']}-{pred['away_goals']} অনুমান"
                    resolved_to_notify.append((uid, f"🏁 {detail} প্রেডিকশন: {msg}"))
                    preds.remove(pred)
        if not predictions[match_id]:
            del predictions[match_id]
    for uid, message in resolved_to_notify:
        try:
            bot.send_message(uid, message)
        except:
            pass

# ---------- পোলিং ইন্টারভাল মিন ----------
def get_min_interval_for_match(mid):
    min_interval = 90
    for uid, u in user_data.items():
        if mid in u.get("followed", []):
            ui = u.get("poll_interval", 90)
            if ui < min_interval:
                min_interval = ui
    return min_interval

# ---------- ব্যাকগ্রাউন্ড চেকার ----------
def match_checker():
    def process_auto_unfollow():
        with lock:
            for uid, u in user_data.items():
                auto_times = u.get("auto_unfollow_times", {})
                for mid in list(auto_times.keys()):
                    if time.time() >= auto_times[mid]:
                        if mid in u.get("followed", []):
                            u["followed"].remove(mid)
                        del user_data[uid]["auto_unfollow_times"][mid]

    def process_events(match_data, mid, uid, last_event_count):
        events = match_data.get("events", [])
        if not events:
            return last_event_count
        new_events = events[last_event_count:]
        fixture = match_data.get("fixture", match_data)
        teams = match_data.get("teams", {})
        home_team = teams.get("home", {}).get("name", "?")
        away_team = teams.get("away", {}).get("name", "?")
        home_goals = fixture.get("score", {}).get("fulltime", {}).get("home", 0) or 0
        away_goals = fixture.get("score", {}).get("fulltime", {}).get("away", 0) or 0
        for ev in new_events:
            etype = ev.get("type")
            detail = ev.get("detail", "")
            minute_ev = ev.get("time", {}).get("elapsed", "?")
            player = ev.get("player", {}).get("name", "?")
            team_ev = ev.get("team", {}).get("name", "")
            if etype == "Goal":
                extra = ""
                if "Penalty" in detail: extra = " (পেনাল্টি)"
                elif "Own Goal" in detail: extra = " (আত্মঘাতী)"
                msg = f"⚽️ গোল! {player} {minute_ev}′{extra}\n{home_team} {home_goals} - {away_goals} {away_team}"
                bot.send_message(uid, msg)
            elif etype == "Card":
                card_type = "🟨 হলুদ" if "Yellow" in detail else "🟥 লাল"
                bot.send_message(uid, f"{card_type} কার্ড: {player} ({minute_ev}′)") 
            elif etype == "Subst":
                assist = ev.get("assist", {}).get("name", "?")
                bot.send_message(uid, f"🔄 বদল: {player} ↓ / {assist} ↑ ({minute_ev}′)") 
            elif etype == "Foul":
                bot.send_message(uid, f"🦵 ফাউল: {player} ({team_ev}) {minute_ev}′")
            elif etype == "Corner":
                bot.send_message(uid, f"🏁 কর্নার: {team_ev} {minute_ev}′")
            elif etype == "Penalty":
                if "missed" in detail.lower():
                    bot.send_message(uid, f"❌ পেনাল্টি মিস: {player} ({minute_ev}′)") 
                else:
                    bot.send_message(uid, f"🎯 পেনাল্টি অ্যাওয়ার্ড: {team_ev} {minute_ev}′")
            elif etype == "Free Kick":
                bot.send_message(uid, f"🦵 ফ্রি কিক: {player} ({minute_ev}′)") 
            elif etype == "Injury":
                bot.send_message(uid, f"🤕 ইনজুরি: {player} {minute_ev}′")
        return len(events)

    while True:
        process_auto_unfollow()
        with lock:
            all_followed = set()
            for u in user_data.values():
                all_followed.update(u.get("followed", []))
        if not all_followed:
            time.sleep(300)
            continue

        fixtures = get_today_fixtures()
        if not fixtures:
            time.sleep(120)
            continue
        live_matches = {}
        for f in fixtures:
            fid = str(f.get("fixture", {}).get("id", f.get("id")))
            status = f.get("fixture", {}).get("status", {}).get("short", f.get("status", {}).get("short", ""))
            if status in ("1H", "2H"):
                live_matches[fid] = f

        active_mid = None
        for mid in all_followed:
            if mid in live_matches:
                active_mid = mid
                break
        if not active_mid:
            # যেসব ফলো করা ম্যাচ লাইভ নেই, তাদের প্রেডিকশনও রেজলভ হবে না, তাই স্কিপ
            time.sleep(300)
            continue

        # পোলিং লুপ
        while True:
            match_data = get_match(int(active_mid))
            if not match_data:
                break
            fixture = match_data.get("fixture", match_data)
            status = fixture.get("status", {}).get("short", "")
            if status not in ("1H", "2H"):
                if status == "FT":
                    # শেষ নোটিফ ও প্রেডিকশন রেজলভ
                    for uid, u in user_data.items():
                        if active_mid in u.get("followed", []):
                            user = get_user(uid)
                            if can_notify(user):
                                home = fixture.get("teams", {}).get("home", {}).get("name", "?")
                                away = fixture.get("teams", {}).get("away", {}).get("name", "?")
                                home_goals = fixture.get("score", {}).get("fulltime", {}).get("home", 0) or 0
                                away_goals = fixture.get("score", {}).get("fulltime", {}).get("away", 0) or 0
                                bot.send_message(uid, f"🏁 খেলা শেষ!\n{home} {home_goals} - {away_goals} {away}")
                                user.setdefault("auto_unfollow_times", {})[active_mid] = time.time() + 60
                    # প্রেডিকশন রেজলভ (ম্যাচ শেষ)
                    resolve_predictions(active_mid, "match_end", {
                        "home_goals": fixture.get("score", {}).get("fulltime", {}).get("home", 0) or 0,
                        "away_goals": fixture.get("score", {}).get("fulltime", {}).get("away", 0) or 0
                    })
                break

            # শুরুর নোটিফিকেশন
            for uid, u in user_data.items():
                if active_mid in u.get("followed", []):
                    user = get_user(uid)
                    if can_notify(user):
                        prev = user.get("prev_status", {}).get(active_mid)
                        if prev != "live":
                            home = fixture.get("teams", {}).get("home", {}).get("name", "?")
                            away = fixture.get("teams", {}).get("away", {}).get("name", "?")
                            bot.send_message(uid, f"⚽ ম্যাচ শুরু!\n{home} 🆚 {away}")
                            user.setdefault("prev_status", {})[active_mid] = "live"

            # ইভেন্ট প্রসেসিং
            for uid, u in user_data.items():
                if active_mid in u.get("followed", []):
                    user = get_user(uid)
                    if can_notify(user):
                        last_key = f"last_{active_mid}"
                        last_event_count = user.get(last_key, 0)
                        new_count = process_events(match_data, active_mid, uid, last_event_count)
                        user[last_key] = new_count
            save_data()

            # প্রেডিকশন রেজলভ (মিনিট অনুযায়ী)
            current_min = fixture.get("status", {}).get("elapsed")
            if current_min is not None:
                current_hg = fixture.get("score", {}).get("fulltime", {}).get("home", 0) or 0
                current_ag = fixture.get("score", {}).get("fulltime", {}).get("away", 0) or 0
                resolve_predictions(active_mid, "minute_reached", {
                    "minute": int(current_min),
                    "home_goals": current_hg,
                    "away_goals": current_ag
                })

            sleep_seconds = max(30, get_min_interval_for_match(active_mid))
            time.sleep(sleep_seconds)

# ---------- কমান্ড ----------
@bot.message_handler(commands=['start'])
def start_cmd(msg):
    user = get_user(msg.from_user.id)
    user["username"] = msg.from_user.username or msg.from_user.first_name
    save_data()
    bot.send_message(msg.chat.id, "⚽ ফিফা বটে স্বাগতম!\nনিচের মেনু থেকে বেছে নিন 👇", reply_markup=main_reply_keyboard())

@bot.message_handler(commands=['points'])
def points_cmd(msg):
    uid = str(msg.from_user.id)
    user = get_user(uid)
    pts = user.get("points", 0)
    bot.send_message(msg.chat.id, f"📊 আপনার পয়েন্ট: {pts}")

@bot.message_handler(commands=['addapikey'])
def add_api_key_cmd(msg):
    if msg.from_user.id != ADMIN_USER_ID:
        bot.reply_to(msg, "❌ আপনি অ্যাডমিন নন।")
        return
    try:
        key = msg.text.split(maxsplit=1)[1].strip()
        if not key:
            bot.reply_to(msg, "ব্যবহার: /addapikey <API_KEY>")
            return
    except IndexError:
        bot.reply_to(msg, "ব্যবহার: /addapikey <API_KEY>")
        return
    with api_keys_lock:
        keys = api_keys_data.setdefault("keys", [])
        if any(k["key"] == key for k in keys):
            bot.reply_to(msg, "⚠️ এই API কী ইতিমধ্যে আছে।")
            return
        keys.append({"key": key, "usage": 0})
        save_api_keys(api_keys_data)
    bot.reply_to(msg, "✅ API কী সফলভাবে যোগ করা হয়েছে।")

@bot.message_handler(commands=['setinterval'])
def set_interval_cmd(msg):
    uid = str(msg.from_user.id)
    user = get_user(uid)
    try:
        sec = int(msg.text.split()[1])
        if sec < 30 or sec > 600:
            bot.reply_to(msg, "⚠️ ইন্টারভাল ৩০ থেকে ৬০০ সেকেন্ডের মধ্যে হতে হবে।")
            return
    except (IndexError, ValueError):
        bot.reply_to(msg, "ব্যবহার: /setinterval <seconds> (30-600)")
        return
    user["poll_interval"] = sec
    save_data()
    bot.reply_to(msg, f"⏱ আপনার পোলিং ইন্টারভাল {sec} সেকেন্ডে সেট হয়েছে।" +
                      ("\n⚠️ সতর্কতা: কম ইন্টারভাল API লিমিট দ্রুত শেষ করবে।" if sec < 60 else ""))

# ---------- মেনু হ্যান্ডলার ----------
@bot.message_handler(func=lambda m: m.text in [
    "⚽ ম্যাচ", "📅 আজকের ম্যাচ", "📋 আমার ম্যাচ",
    "⭐ পছন্দের দল", "⭐ টপ স্কোরার", "⚙️ সেটিংস", "❓ সাহায্য",
    "🧠 প্রেডিকশন", "📊 পয়েন্ট"
])
def menu_text_handler(msg):
    uid = str(msg.from_user.id)
    user = get_user(uid)
    user["username"] = msg.from_user.username or msg.from_user.first_name
    save_data()
    text = msg.text

    if text in ("⚽ ম্যাচ", "📅 আজকের ম্যাচ"):
        matches = get_today_fixtures()
        if not matches:
            bot.send_message(msg.chat.id, "কোনো ম্যাচ পাওয়া যায়নি।")
            return
        show_matches(msg.chat.id, matches[:10], user)
    elif text == "📋 আমার ম্যাচ":
        followed = user.get("followed", [])
        if not followed:
            bot.send_message(msg.chat.id, "আপনি কোনো ম্যাচ ফলো করছেন না।")
        else:
            for mid in followed:
                data = get_match(int(mid))
                if data:
                    show_match_line(msg.chat.id, data, user)
    elif text == "⭐ পছন্দের দল":
        kb = InlineKeyboardMarkup(row_width=3)
        for t in TEAMS:
            kb.add(InlineKeyboardButton(t, callback_data=f"favteam_{t}"))
        if user.get("fav_team"):
            kb.add(InlineKeyboardButton("❌ সরিয়ে দিন", callback_data="favteam_remove"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        curr = user.get("fav_team", "কোনো দল নেই")
        bot.send_message(msg.chat.id, f"আপনার পছন্দের দল: {curr}\nনতুন দল বেছে নিন:", reply_markup=kb)
    elif text == "⭐ টপ স্কোরার":
        bot.send_message(msg.chat.id, "⚠️ এই API-তে টপ স্কোরার নেই।")
    elif text == "⚙️ সেটিংস":
        tz = user["timezone"]
        ms = user["mute_start"] if user["mute_start"] is not None else "না"
        me = user["mute_end"] if user["mute_end"] is not None else "না"
        notif = "✅ চালু" if user.get("notifications_enabled", True) else "🔕 বন্ধ"
        interval = user.get("poll_interval", 90)
        txt = (f"⚙️ আপনার সেটিংস:\n🕒 টাইমজোন: {tz}\n"
               f"🔇 নীরব: {ms}:00 - {me}:00\n🔔 নোটিফিকেশন: {notif}\n"
               f"⏱ পোলিং ইন্টারভাল: {interval} সেকেন্ড")
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("🕒 টাইমজোন বদলান", callback_data="set_tz"))
        kb.add(InlineKeyboardButton("🔇 নীরবতা সেট করুন", callback_data="set_mute"))
        kb.add(InlineKeyboardButton("🔇 নীরবতা বন্ধ করুন", callback_data="clear_mute"))
        kb.add(InlineKeyboardButton("🔕 নোটিফিকেশন টগল", callback_data="toggle_notif"))
        kb.add(InlineKeyboardButton("⏱ পোলিং ইন্টারভাল", callback_data="set_poll_interval"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(msg.chat.id, txt, reply_markup=kb)
    elif text == "❓ সাহায্য":
        txt = ("ℹ️ সাহায্য:\n\n"
               "⚽ ম্যাচ – আজকের সব ম্যাচ দেখুন ও ফলো করুন।\n"
               "📋 আমার ম্যাচ – ফলো করা ম্যাচ।\n"
               "⭐ পছন্দের দল – প্রিয় দল সেট।\n"
               "🧠 প্রেডিকশন – নির্দিষ্ট মিনিটে স্কোর অনুমান করে পয়েন্ট জিতুন।\n"
               "📊 পয়েন্ট – আপনার পয়েন্ট।\n"
               "⚙️ সেটিংস – টাইমজোন, নীরবতা, নোটিফিকেশন, পোলিং ইন্টারভাল।\n\n"
               "লাইভ ম্যাচ চলাকালীন আপনার সেট করা ইন্টারভালে ইভেন্ট আপডেট আসে।")
        bot.send_message(msg.chat.id, txt)
    elif text == "🧠 প্রেডিকশন":
        # লাইভ ম্যাচের তালিকা
        fixtures = get_today_fixtures()
        live_matches = []
        for f in fixtures:
            status = f.get("fixture", {}).get("status", {}).get("short", f.get("status", {}).get("short", ""))
            if status in ("1H", "2H"):
                live_matches.append(f)
        if not live_matches:
            bot.send_message(msg.chat.id, "এখন কোনো লাইভ ম্যাচ নেই।")
            return
        kb = InlineKeyboardMarkup(row_width=1)
        for f in live_matches:
            mid = str(f.get("fixture", {}).get("id", f.get("id")))
            home = f.get("teams", {}).get("home", {}).get("name", "?")
            away = f.get("teams", {}).get("away", {}).get("name", "?")
            minute = f.get("fixture", {}).get("status", {}).get("elapsed", "?")
            kb.add(InlineKeyboardButton(f"{home} 🆚 {away} ({minute}′)", callback_data=f"predict_{mid}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(msg.chat.id, "প্রেডিকশনের জন্য লাইভ ম্যাচ বেছে নিন:", reply_markup=kb)
    elif text == "📊 পয়েন্ট":
        pts = user.get("points", 0)
        bot.send_message(msg.chat.id, f"📊 আপনার পয়েন্ট: {pts}")

# ---------- ম্যাচ দেখানো ----------
def show_match_line(chat_id, data, user):
    fixture = data.get("fixture", data)
    mid = str(fixture.get("id"))
    teams = data.get("teams", {})
    home = teams.get("home", {}).get("name", "?")
    away = teams.get("away", {}).get("name", "?")
    status = fixture.get("status", {}).get("short", "")
    if status in ("1H","2H"):
        st_text = "🔴 লাইভ"
    elif status == "FT":
        st_text = "✅ শেষ"
    else:
        st_text = "⏰ আসন্ন"
    date_str = utc_to_dhaka(fixture.get("date", ""))
    fav = user.get("fav_team")
    h_disp = f"⭐ {home}" if fav and fav.lower() == home.lower() else home
    a_disp = f"⭐ {away}" if fav and fav.lower() == away.lower() else away
    txt = f"{h_disp} 🆚 {a_disp}\n{st_text} | {date_str}"
    kb = InlineKeyboardMarkup()
    if mid in user.get("followed", []):
        kb.add(InlineKeyboardButton("❌ আনফলো", callback_data=f"unfollow_{mid}"))
    else:
        kb.add(InlineKeyboardButton("➕ ফলো", callback_data=f"follow_{mid}"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
    bot.send_message(chat_id, txt, reply_markup=kb)

def show_matches(chat_id, matches, user):
    for m in matches:
        show_match_line(chat_id, m, user)

# ---------- প্রেডিকশন ইন্টারফেস কলব্যাক ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("predict_"))
def predict_match(call):
    mid = call.data.split("_")[1]
    uid = str(call.from_user.id)
    # প্রেডিকশনের জন্য নির্দিষ্ট মিনিট বাছাই মেনু
    kb = InlineKeyboardMarkup(row_width=3)
    # বর্তমান মিনিট জানা দরকার
    data = get_match(int(mid))
    if not data:
        bot.answer_callback_query(call.id, "ম্যাচ ডাটা পাওয়া যায়নি।")
        return
    fixture = data.get("fixture", data)
    current_min = fixture.get("status", {}).get("elapsed", 0)
    if current_min is None:
        current_min = 0
    # প্রিসেট মিনিট: +5, +10, +15, +20, +30, +45
    options = [current_min + 5, current_min + 10, current_min + 15, current_min + 20, current_min + 30, current_min + 45]
    # 90 এর বেশি না হয়
    options = [o for o in options if o <= 90]
    for opt in options:
        kb.add(InlineKeyboardButton(f"{opt}′", callback_data=f"predmin_{mid}_{opt}"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
    bot.edit_message_text("কত মিনিটে স্কোর অনুমান করবেন?", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("predmin_"))
def pred_min_chosen(call):
    parts = call.data.split("_")
    mid = parts[1]
    pred_minute = int(parts[2])
    # এখন হোম গোল বাছাই করতে বলুন
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(6):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"predhome_{mid}_{pred_minute}_{i}"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data=f"predict_{mid}"))
    bot.edit_message_text(f"{pred_minute}′ - হোম দলের গোল সংখ্যা (0-5):", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("predhome_"))
def pred_home_chosen(call):
    parts = call.data.split("_")
    mid = parts[1]
    pred_minute = int(parts[2])
    home_goals = int(parts[3])
    # অ্যাওয়ে গোল বাছাই
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(6):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"predaway_{mid}_{pred_minute}_{home_goals}_{i}"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data=f"predict_{mid}"))
    bot.edit_message_text(f"{pred_minute}′ - অ্যাওয়ে দলের গোল সংখ্যা (0-5):", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("predaway_"))
def pred_away_chosen(call):
    parts = call.data.split("_")
    mid = parts[1]
    pred_minute = int(parts[2])
    home_goals = int(parts[3])
    away_goals = int(parts[4])
    uid = str(call.from_user.id)
    # প্রেডিকশন সংরক্ষণ
    with lock:
        if mid not in predictions:
            predictions[mid] = []
        # একই ইউজার একই ম্যাচে পুরোনো unresolved প্রেডিকশন থাকলে ওভাররাইট
        predictions[mid] = [p for p in predictions[mid] if not (p["user_id"] == uid and p["type"] == "score_at_minute" and not p["resolved"])]
        predictions[mid].append({
            "user_id": uid,
            "type": "score_at_minute",
            "minute": pred_minute,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "resolved": False,
            "correct": None
        })
    # ম্যাচটি ফলো না থাকলে অটো ফলো করিয়ে দিই, যাতে চেকার মনিটর করে
    user = get_user(uid)
    if mid not in user.get("followed", []):
        user.setdefault("followed", []).append(mid)
    save_data()
    bot.answer_callback_query(call.id, "✅ প্রেডিকশন জমা হয়েছে!")
    bot.edit_message_text(f"✅ আপনার প্রেডিকশন: {pred_minute}′ এ স্কোর হবে {home_goals}-{away_goals}\nফলাফল সময়মতো জানানো হবে।", chat_id=call.message.chat.id, message_id=call.message.message_id)

# ---------- অন্যান্য কলব্যাক (সেটিংস, ফলো, ফ্যাভটিম ইত্যাদি) ----------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = str(call.from_user.id)
    user = get_user(uid)
    data = call.data
    if data == "menu_main":
        try:
            bot.edit_message_text("⚽ প্রধান মেনুতে ফিরেছেন।", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except:
            pass
        return
    if data.startswith("follow_"):
        mid = data.split("_")[1]
        if mid not in user.get("followed", []):
            user.setdefault("followed", []).append(mid)
            save_data()
            bot.answer_callback_query(call.id, "✅ ফলো করা হয়েছে!")
        else:
            bot.answer_callback_query(call.id, "আগেই ফলো করা আছে।")
    elif data.startswith("unfollow_"):
        mid = data.split("_")[1]
        if mid in user.get("followed", []):
            user["followed"].remove(mid)
            save_data()
            bot.answer_callback_query(call.id, "❌ আনফলো করা হয়েছে।")
        else:
            bot.answer_callback_query(call.id, "আপনি এটি ফলো করছেন না।")
    elif data.startswith("favteam_"):
        team = data.split("_", 1)[1]
        if team == "remove":
            user["fav_team"] = None
            save_data()
            bot.answer_callback_query(call.id, "পছন্দের দল সরানো হয়েছে।")
        else:
            user["fav_team"] = team
            save_data()
            bot.answer_callback_query(call.id, f"⭐ {team} এখন আপনার প্রিয় দল।")
    elif data == "clear_mute":
        user["mute_start"] = None
        user["mute_end"] = None
        save_data()
        bot.answer_callback_query(call.id, "নীরবতা বন্ধ করা হয়েছে।")
    elif data == "toggle_notif":
        current = user.get("notifications_enabled", True)
        user["notifications_enabled"] = not current
        save_data()
        state = "চালু" if user["notifications_enabled"] else "বন্ধ"
        bot.answer_callback_query(call.id, f"নোটিফিকেশন {state} করা হয়েছে।")
    elif data == "set_tz":
        tzs = ["Asia/Dhaka", "Asia/Kolkata", "Europe/London", "America/New_York", "Asia/Dubai"]
        kb = InlineKeyboardMarkup(row_width=2)
        for t in tzs:
            kb.add(InlineKeyboardButton(t, callback_data=f"tz_{t}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(call.message.chat.id, "আপনার টাইমজোন বেছে নিন:", reply_markup=kb)
    elif data.startswith("tz_"):
        new_tz = data.split("_", 1)[1]
        user["timezone"] = new_tz
        save_data()
        bot.answer_callback_query(call.id, f"টাইমজোন {new_tz} সেট হয়েছে।")
    elif data == "set_mute":
        kb = InlineKeyboardMarkup(row_width=6)
        for h in range(0, 24):
            kb.add(InlineKeyboardButton(str(h), callback_data=f"mutehour_{h}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(call.message.chat.id, "নীরবতার শুরু ঘণ্টা (0-23):", reply_markup=kb)
    elif data.startswith("mutehour_"):
        h = int(data.split("_")[1])
        user["mute_start"] = h
        save_data()
        bot.answer_callback_query(call.id, f"শুরু: {h}:00")
        kb = InlineKeyboardMarkup(row_width=6)
        for h2 in range(0, 24):
            kb.add(InlineKeyboardButton(str(h2), callback_data=f"muteend_{h}_{h2}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(call.message.chat.id, "শেষ ঘণ্টা বেছে নিন:", reply_markup=kb)
    elif data.startswith("muteend_"):
        parts = data.split("_")
        s, e = int(parts[1]), int(parts[2])
        user["mute_start"] = s
        user["mute_end"] = e
        save_data()
        bot.answer_callback_query(call.id, f"নীরবতা: {s}:00 - {e}:00")
    elif data == "set_poll_interval":
        kb = InlineKeyboardMarkup(row_width=4)
        options = [30, 45, 60, 90, 120, 180, 300, 600]
        buttons = []
        for opt in options:
            buttons.append(InlineKeyboardButton(f"{opt}s", callback_data=f"pollint_{opt}"))
        kb.add(*buttons)
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(call.message.chat.id, "পোলিং ইন্টারভাল বেছে নিন:", reply_markup=kb)
    elif data.startswith("pollint_"):
        sec = int(data.split("_")[1])
        user["poll_interval"] = sec
        save_data()
        bot.answer_callback_query(call.id, f"পোলিং ইন্টারভাল {sec} সেকেন্ড সেট হয়েছে।")
        bot.send_message(call.message.chat.id, f"⏱ আপনার ইন্টারভাল {sec} সেকেন্ডে আপডেট করা হয়েছে।" +
                         ("\n⚠️ সতর্কতা: ছোট ইন্টারভাল API লিমিট দ্রুত শেষ করবে।" if sec < 60 else ""))
    else:
        bot.answer_callback_query(call.id)

# ---------- অন্যান্য টেক্সট ----------
@bot.message_handler(func=lambda m: True)
def any_msg(msg):
    bot.send_message(msg.chat.id, "নিচের মেনু ব্যবহার করুন 👇", reply_markup=main_reply_keyboard())

# ---------- ফ্লাস্ক ----------
@app.route("/")
def home():
    return "Bot is running!"

if __name__ == "__main__":
    bot.remove_webhook()
    time.sleep(1)
    threading.Thread(target=match_checker, daemon=True).start()
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
