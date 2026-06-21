import os, json, time, threading, requests, re, math
from datetime import datetime
import pytz
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ---------- কনফিগ ----------
TOKEN = "8913363649:AAGb41xxF2fzrEgVtc862v9kT2Zx30ApfBo"
DATA_FILE = "user_data.json"
TEAMS = ["Brazil", "Argentina", "Germany", "France", "England", "Spain",
         "Portugal", "Netherlands", "Italy", "Belgium", "Croatia", "Uruguay"]
BENGALI_MONTHS = {
    1: "জানুয়ারি", 2: "ফেব্রুয়ারি", 3: "মার্চ", 4: "এপ্রিল",
    5: "মে", 6: "জুন", 7: "জুলাই", 8: "আগস্ট",
    9: "সেপ্টেম্বর", 10: "অক্টোবর", 11: "নভেম্বর", 12: "ডিসেম্বর"
}

# FlashScore internal headers
FS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "x-fsign": "SW9D1eZo"   # স্থির কী (পরিবর্তন হতে পারে)
}

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

# ---------- ডাটা ----------
user_data = {}
predictions = {}
lock = threading.Lock()

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
            "followed": [],            # FlashScore match IDs (string)
            "timezone": "Asia/Dhaka",
            "mute_start": None,
            "mute_end": None,
            "username": None,
            "fav_team": None,
            "notifications_enabled": True,
            "auto_unfollow_times": {},
            "points": 0,
            "prediction_history": [],
            "fs_match_cache": {}       # {match_id: {home, away, ...}}
        }
    return user_data[uid]

# ---------- FlashScore স্ক্র্যাপিং ----------
def fs_request(url):
    try:
        resp = requests.get(url, headers=FS_HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.text
        return None
    except:
        return None

def get_fs_matches_today():
    """আজকের তারিখের সব ফুটবল ম্যাচের FlashScore আইডি ও সংক্ষিপ্ত তথ্য আনে"""
    date_str = datetime.utcnow().strftime("%Y%m%d")
    url = f"https://d.flashscore.com/x/feed/tr_1_{date_str}_1"  # 1 = football
    raw = fs_request(url)
    if not raw:
        return []
    matches = []
    parts = raw.split("¬")
    # ডাটা স্ট্রাকচার: header ... ¬~AA¬ ... (match blocks)
    # সরলীকৃত পার্সিং: প্রতিটি ম্যাচ ব্লক "~AA" দিয়ে শুরু
    idx = 0
    while idx < len(parts):
        if parts[idx] == "~AA":
            idx += 1
            if idx + 6 >= len(parts):
                break
            mid = parts[idx]        # match ID
            # পরের অংশগুলোতে হোম, অ্যাওয়ে, স্কোর ইত্যাদি
            home = parts[idx+2] if idx+2 < len(parts) else "?"
            away = parts[idx+4] if idx+4 < len(parts) else "?"
            status_code = parts[idx+1] if idx+1 < len(parts) else "0"
            # স্ট্যাটাস বোঝা: 0=আসন্ন, 1=লাইভ, 2=অর্ধ, 3=শেষ ইত্যাদি
            matches.append({
                "id": mid,
                "home": home,
                "away": away,
                "status": "IN_PLAY" if status_code == "1" else ("FINISHED" if status_code == "3" else "SCHEDULED")
            })
            idx += 7  # পরবর্তী ব্লকে যাই
        else:
            idx += 1
    return matches

def get_fs_match_feed(mid):
    """একটি ম্যাচের বিস্তারিত ফিড (ইভেন্ট, স্কোর) আনে"""
    url = f"https://d.flashscore.com/x/feed/df_{mid}_1"
    raw = fs_request(url)
    if not raw:
        return None
    parts = raw.split("¬")
    # ফিড ফর্ম্যাট: শুরুতে কিছু হেডার, তারপর "~AA" দিয়ে ইনফো, তারপর ইনসিডেন্ট
    data = {
        "id": mid,
        "home": "?",
        "away": "?",
        "home_score": 0,
        "away_score": 0,
        "minute": None,
        "status": "SCHEDULED",
        "events": []
    }
    idx = 0
    # ম্যাচ ইনফো ব্লক খুঁজি
    while idx < len(parts):
        if parts[idx] == "~AA":
            idx += 1
            if idx+6 >= len(parts):
                break
            data["home"] = parts[idx+2]
            data["away"] = parts[idx+4]
            # স্কোর অংশ: idx+5 ও idx+6 ? আসলে স্কোর অন্য জায়গায়, তবে ফিডে সাধারণত "~AD" ব্লকে পয়েন্ট থাকে
            # সহজে পরে ইভেন্ট থেকে নেই
            idx += 7
        elif parts[idx] == "~AD":
            idx += 1
            if idx+3 < len(parts):
                # সাধারণত: home_score, away_score, ...
                data["home_score"] = int(parts[idx]) if parts[idx].isdigit() else 0
                data["away_score"] = int(parts[idx+1]) if parts[idx+1].isdigit() else 0
                data["minute"] = parts[idx+2] if idx+2 < len(parts) else None
                try:
                    data["minute"] = int(data["minute"]) if data["minute"] and data["minute"].isdigit() else None
                except:
                    data["minute"] = None
                # স্ট্যাটাস
                if "1" in parts[idx+3] if idx+3 < len(parts) else "":
                    data["status"] = "IN_PLAY"
                idx += 4
            else:
                idx += 1
        elif parts[idx] == "~AE":
            idx += 1
            # ইনসিডেন্ট শুরু: প্রতিটি ইভেন্ট ব্লক থাকে
            while idx < len(parts) and parts[idx] != "~AF":
                if parts[idx] in ["GL","YL","RD","SUB","FL","CK","PN","PM","IN","ST","HT","FT"]:
                    etype = parts[idx]
                    idx += 1
                    event = {"type": etype}
                    if etype in ["GL","FL","CK","PN","PM","ST"]:
                        if idx < len(parts): event["minute"] = parts[idx]; idx += 1
                        if idx < len(parts): event["team"] = "home" if parts[idx] == "1" else "away"; idx += 1
                        if idx < len(parts): event["player"] = parts[idx]; idx += 1
                        # extra info (own goal, penalty etc.)
                    elif etype in ["YL","RD"]:
                        if idx < len(parts): event["minute"] = parts[idx]; idx += 1
                        if idx < len(parts): event["team"] = "home" if parts[idx] == "1" else "away"; idx += 1
                        if idx < len(parts): event["player"] = parts[idx]; idx += 1
                    elif etype == "SUB":
                        if idx < len(parts): event["minute"] = parts[idx]; idx += 1
                        if idx < len(parts): event["team"] = "home" if parts[idx] == "1" else "away"; idx += 1
                        if idx < len(parts): event["player_out"] = parts[idx]; idx += 1
                        if idx < len(parts): event["player_in"] = parts[idx]; idx += 1
                    elif etype == "IN":
                        if idx < len(parts): event["minute"] = parts[idx]; idx += 1
                        if idx < len(parts): event["team"] = "home" if parts[idx] == "1" else "away"; idx += 1
                        if idx < len(parts): event["player"] = parts[idx]; idx += 1
                    elif etype == "HT":
                        event["info"] = "Half Time"
                    elif etype == "FT":
                        event["info"] = "Full Time"
                        data["status"] = "FINISHED"
                    data["events"].append(event)
                else:
                    idx += 1
            if idx < len(parts) and parts[idx] == "~AF":
                idx += 1
        else:
            idx += 1
    return data

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
    kb.row(KeyboardButton("📋 আমার ম্যাচ"), KeyboardButton("🧠 প্রেডিকশন"))
    kb.row(KeyboardButton("⭐ পছন্দের দল"), KeyboardButton("⭐ টপ স্কোরার"))
    kb.row(KeyboardButton("📊 পয়েন্ট"), KeyboardButton("⚙️ সেটিংস"))
    kb.row(KeyboardButton("❓ সাহায্য"))
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

# ---------- প্রেডিকশন ইভ্যালুয়েটর (অপরিবর্তিত) ----------
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
            resolved = False
            correct = None
            ptype = pred["type"]
            if ptype == "next_goal_team":
                if event_type == "goal":
                    correct = (pred["choice"] == event_data["team"])
                    resolved = True
                elif event_type == "match_end":
                    correct = (pred["choice"] == "none")
                    resolved = True
            elif ptype == "goal_in_5_min":
                block_start = pred["block_start"]
                block_end = block_start + 5
                if event_type == "goal":
                    if block_start <= event_data["minute"] < block_end:
                        correct = (pred["choice"] == "yes")
                        resolved = True
                elif event_type == "block_end" and event_data["block_start"] == block_start:
                    correct = (pred["choice"] == "no")
                    resolved = True
                elif event_type == "match_end":
                    correct = (pred["choice"] == "no")
                    resolved = True
            elif ptype == "final_score":
                if event_type == "match_end":
                    hg = event_data["home_goals"]
                    ag = event_data["away_goals"]
                    correct = (pred["choice"]["home"] == hg and pred["choice"]["away"] == ag)
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
                if ptype == "next_goal_team":
                    detail = "পরবর্তী গোল"
                elif ptype == "goal_in_5_min":
                    detail = f"{pred['block_start']}′-{pred['block_start']+5}′ ব্লকে গোল"
                else:
                    detail = "ফাইনাল স্কোর"
                resolved_to_notify.append((uid, f"🏁 {detail} প্রেডিকশন: {msg}"))
                preds.remove(pred)
        if not predictions[match_id]:
            del predictions[match_id]
    for uid, message in resolved_to_notify:
        try:
            bot.send_message(uid, message)
        except:
            pass

# ---------- ব্যাকগ্রাউন্ড চেকার (স্ক্র্যাপিং ভিত্তিক) ----------
def match_checker():
    while True:
        with lock:
            all_users = dict(user_data)
        # অটো আনফলো
        for uid, u in all_users.items():
            auto_times = u.get("auto_unfollow_times", {})
            for mid in list(auto_times.keys()):
                if time.time() >= auto_times[mid]:
                    if mid in u.get("followed", []):
                        u["followed"].remove(mid)
                    del user_data[uid]["auto_unfollow_times"][mid]

        # সংগ্রহ করি সব ফলো করা ম্যাচের FlashScore আইডি
        followed_mids = set()
        for u in all_users.values():
            followed_mids.update(u.get("followed", []))
        if not followed_mids:
            time.sleep(10)
            continue

        # প্রতিটি ম্যাচের জন্য ফিড নিয়ে নোটিফিকেশন চেক
        for mid in followed_mids:
            feed = get_fs_match_feed(mid)
            if not feed:
                continue
            # ক্যাশে ম্যাচের নাম রাখি
            home = feed["home"]
            away = feed["away"]
            status = feed["status"]
            minute = feed.get("minute")
            home_goals = feed["home_score"]
            away_goals = feed["away_score"]
            events = feed["events"]

            # যেসব ইউজার ফলো করছে
            users_to_notify = [uid for uid, u in all_users.items() if mid in u.get("followed", [])]

            for uid in users_to_notify:
                u = get_user(uid)
                # ক্যাশ আপডেট
                u.setdefault("fs_match_cache", {})[mid] = {"home": home, "away": away}
                last_key = f"last_fs_{mid}"
                last = u.get(last_key, {"status": None, "hg": 0, "ag": 0, "event_index": 0})
                ls = last["status"]
                l_events_idx = last.get("event_index", 0)

                if not can_notify(u):
                    continue

                h_disp = f"⭐ {home}" if u.get("fav_team", "").lower() == home.lower() else home
                a_disp = f"⭐ {away}" if u.get("fav_team", "").lower() == away.lower() else away

                # ম্যাচ শুরু নোটিফ
                if ls != "IN_PLAY" and status == "IN_PLAY":
                    bot.send_message(uid, f"⚽ ম্যাচ শুরু!\n{h_disp} 🆚 {a_disp}")

                # নতুন ইভেন্ট পরীক্ষা
                new_events = events[l_events_idx:]
                for ev in new_events:
                    etype = ev["type"]
                    if etype == "GL":
                        scorer = ev.get("player", "?")
                        minu = ev.get("minute", "?")
                        team_side = ev["team"]
                        extra = ""
                        # (পেনাল্টি/আত্মঘাতী চেক করার জন্য আরও ডিটেইল দরকার, সরলীকৃত)
                        msg = f"⚽️ গোল! {scorer} {minu}′{extra}\n{h_disp} {home_goals} - {away_goals} {a_disp}"
                        bot.send_message(uid, msg)
                        # প্রেডিকশন মূল্যায়ন
                        resolve_predictions(mid, "goal", {"minute": int(minu) if minu.isdigit() else 0, "team": team_side})
                    elif etype == "YL":
                        player = ev.get("player", "?")
                        minu = ev.get("minute", "?")
                        bot.send_message(uid, f"🟨 হলুদ কার্ড: {player} ({minu}′)")
                    elif etype == "RD":
                        player = ev.get("player", "?")
                        minu = ev.get("minute", "?")
                        bot.send_message(uid, f"🟥 লাল কার্ড: {player} ({minu}′)")
                    elif etype == "SUB":
                        out = ev.get("player_out", "?")
                        inp = ev.get("player_in", "?")
                        minu = ev.get("minute", "?")
                        bot.send_message(uid, f"🔄 বদল: {out} ↓ / {inp} ↑ ({minu}′)")
                    elif etype == "FL":
                        player = ev.get("player", "?")
                        minu = ev.get("minute", "?")
                        team = home if ev["team"] == "home" else away
                        bot.send_message(uid, f"🦵 ফাউল: {player} ({team}) {minu}′")
                    elif etype == "CK":
                        minu = ev.get("minute", "?")
                        team = home if ev["team"] == "home" else away
                        bot.send_message(uid, f"🏁 কর্নার: {team} {minu}′")
                    elif etype == "PN":
                        minu = ev.get("minute", "?")
                        team = home if ev["team"] == "home" else away
                        bot.send_message(uid, f"🎯 পেনাল্টি অ্যাওয়ার্ড: {team} {minu}′")
                    elif etype == "PM":
                        player = ev.get("player", "?")
                        minu = ev.get("minute", "?")
                        team = home if ev["team"] == "home" else away
                        bot.send_message(uid, f"❌ পেনাল্টি মিস: {player} ({team}) {minu}′")
                    elif etype == "IN":
                        player = ev.get("player", "?")
                        minu = ev.get("minute", "?")
                        bot.send_message(uid, f"🤕 ইনজুরি: {player} {minu}′")
                    elif etype == "ST":
                        minu = ev.get("minute", "?")
                        team = home if ev["team"] == "home" else away
                        bot.send_message(uid, f"⚡ শট অন টার্গেট: {team} {minu}′")
                    elif etype == "HT":
                        bot.send_message(uid, "⏸️ প্রথমার্ধ শেষ!")
                    elif etype == "FT":
                        bot.send_message(uid, f"🏁 খেলা শেষ!\n{home} {home_goals} - {away_goals} {away}")
                        resolve_predictions(mid, "match_end", {"home_goals": home_goals, "away_goals": away_goals})
                        # অটো আনফলো
                        user_data[uid].setdefault("auto_unfollow_times", {})[mid] = time.time() + 60

                # শেষ স্টেট সংরক্ষণ
                u[last_key] = {
                    "status": status,
                    "hg": home_goals,
                    "ag": away_goals,
                    "event_index": len(events)
                }
        save_data()
        time.sleep(10)

# ---------- /start, /points ----------
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

# ---------- মেনু হ্যান্ডলার (আপডেটেড) ----------
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

    if text == "⚽ ম্যাচ" or text == "📅 আজকের ম্যাচ":
        matches = get_fs_matches_today()
        if not matches:
            bot.send_message(msg.chat.id, "কোনো ম্যাচ পাওয়া যায়নি।")
            return
        show_matches(msg.chat.id, matches[:10], user)
    elif text == "📋 আমার ম্যাচ":
        followed = user.get("followed", [])
        if not followed:
            bot.send_message(msg.chat.id, "আপনি কোনো ম্যাচ ফলো করছেন না।")
        else:
            # আমরা fs_match_cache থেকে নাম দেখাব
            for mid in followed:
                info = user.get("fs_match_cache", {}).get(mid, {})
                home = info.get("home", "?")
                away = info.get("away", "?")
                txt = f"{home} 🆚 {away}"
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("❌ আনফলো", callback_data=f"unfollow_{mid}"))
                kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
                bot.send_message(msg.chat.id, txt, reply_markup=kb)
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
        bot.send_message(msg.chat.id, "টপ স্কোরার ফিচার বর্তমানে অনুপলব্ধ (ডেটা সীমাবদ্ধতার জন্য)।")
    elif text == "⚙️ সেটিংস":
        tz = user["timezone"]
        ms = user["mute_start"] if user["mute_start"] is not None else "না"
        me = user["mute_end"] if user["mute_end"] is not None else "না"
        notif = "✅ চালু" if user.get("notifications_enabled", True) else "🔕 বন্ধ"
        txt = f"⚙️ আপনার সেটিংস:\n🕒 টাইমজোন: {tz}\n🔇 নীরব: {ms}:00 - {me}:00\n🔔 নোটিফিকেশন: {notif}"
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("🕒 টাইমজোন বদলান", callback_data="set_tz"))
        kb.add(InlineKeyboardButton("🔇 নীরবতা সেট করুন", callback_data="set_mute"))
        kb.add(InlineKeyboardButton("🔇 নীরবতা বন্ধ করুন", callback_data="clear_mute"))
        kb.add(InlineKeyboardButton("🔕 নোটিফিকেশন টগল", callback_data="toggle_notif"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(msg.chat.id, txt, reply_markup=kb)
    elif text == "❓ সাহায্য":
        txt = ("ℹ️ সাহায্য:\n\n"
               "⚽ ম্যাচ – আজকের সব ফুটবল ম্যাচ দেখুন ও ফলো করুন।\n"
               "📋 আমার ম্যাচ – ফলো করা ম্যাচ।\n"
               "⭐ পছন্দের দল – প্রিয় দল সেট।\n"
               "🧠 প্রেডিকশন – লাইভ ম্যাচে পরবর্তী গোল, ৫ মিনিটে গোল, ফাইনাল স্কোর অনুমান করে পয়েন্ট জিতুন।\n"
               "📊 পয়েন্ট – আপনার পয়েন্ট।\n"
               "⚙️ সেটিংস – টাইমজোন, নীরবতা, নোটিফিকেশন।\n\n"
               "নোটিফিকেশন: গোল, কার্ড, বদল, ফাউল, কর্নার, পেনাল্টি, ইনজুরি ইত্যাদি।")
        bot.send_message(msg.chat.id, txt)
    elif text == "🧠 প্রেডিকশন":
        # যে কোনো লাইভ ম্যাচ বেছে নিতে হবে (ফলো করা থাকুক বা না থাকুক)
        matches = get_fs_matches_today()
        live = [m for m in matches if m["status"] == "IN_PLAY"]
        if not live:
            bot.send_message(msg.chat.id, "এখন কোনো লাইভ ম্যাচ নেই।")
            return
        kb = InlineKeyboardMarkup(row_width=1)
        for m in live:
            kb.add(InlineKeyboardButton(f"{m['home']} 🆚 {m['away']}", callback_data=f"predict_{m['id']}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(msg.chat.id, "লাইভ ম্যাচ বেছে নিন প্রেডিকশনের জন্য:", reply_markup=kb)
    elif text == "📊 পয়েন্ট":
        pts = user.get("points", 0)
        bot.send_message(msg.chat.id, f"📊 আপনার পয়েন্ট: {pts}")

# ---------- ম্যাচ দেখানো ফাংশন (স্ক্র্যাপিং) ----------
def show_match_line(chat_id, m, user):
    mid = m["id"]
    home = m["home"]
    away = m["away"]
    st = m["status"]
    st_text = "🔴 লাইভ" if st == "IN_PLAY" else ("✅ শেষ" if st == "FINISHED" else "⏰ আসন্ন")
    txt = f"{home} 🆚 {away}\n{st_text}"
    kb = InlineKeyboardMarkup()
    if mid in user.get("followed", []):
        kb.add(InlineKeyboardButton("❌ আনফলো", callback_data=f"unfollow_{mid}"))
    else:
        kb.add(InlineKeyboardButton("➕ ফলো", callback_data=f"follow_{mid}"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
    bot.send_message(chat_id, txt, reply_markup=kb)

def show_matches(chat_id, matches, user):
    if not matches:
        bot.send_message(chat_id, "কোনো ম্যাচ পাওয়া যায়নি।")
        return
    for m in matches:
        show_match_line(chat_id, m, user)

# ---------- ফলো / আনফলো কলব্যাক ----------
@bot.callback_query_handler(func=lambda call: call.data.startswith("follow_") or call.data.startswith("unfollow_"))
def follow_unfollow(call):
    uid = str(call.from_user.id)
    user = get_user(uid)
    data = call.data
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

# ---------- প্রেডিকশন কলব্যাক (অপরিবর্তিত, কিন্তু mid FlashScore ID) ----------
# পূর্বের প্রেডিকশন কলব্যাক কোড এখানে আবার লিখতে হবে (predict_, predtype_, fshome_, fsaway_, predpick_)
# যেহেতু mid এখন FlashScore ID, কোনো সংখ্যা বা স্ট্রিং, তাই int conversion সাবধানে
@bot.callback_query_handler(func=lambda call: call.data.startswith("predict_"))
def predict_match(call):
    mid = call.data.split("_", 1)[1]  # পুরো mid (স্ট্রিং)
    feed = get_fs_match_feed(mid)
    if not feed or feed["status"] != "IN_PLAY":
        bot.answer_callback_query(call.id, "ম্যাচটি এখন আর লাইভ নেই!")
        return
    home = feed["home"]
    away = feed["away"]
    minute = feed.get("minute", "?")
    txt = f"🧠 {home} 🆚 {away} ({minute}′)\nকী প্রেডিকশন দিতে চান?"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("⚽ পরবর্তী গোল কে করবে?", callback_data=f"predtype_{mid}_nextgoal"))
    kb.add(InlineKeyboardButton("⏳ এই ৫ মিনিটে গোল হবে?", callback_data=f"predtype_{mid}_5min"))
    kb.add(InlineKeyboardButton("🏁 ফাইনাল স্কোর অনুমান", callback_data=f"predtype_{mid}_finalscore"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
    bot.edit_message_text(txt, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("predtype_"))
def pred_type_choice(call):
    parts = call.data.split("_", 2)
    mid = parts[1]
    ptype = parts[2]
    feed = get_fs_match_feed(mid)
    if not feed or feed["status"] != "IN_PLAY":
        bot.answer_callback_query(call.id, "ম্যাচটি আর লাইভ নেই!")
        return
    home = feed["home"]
    away = feed["away"]
    if ptype == "nextgoal":
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton(f"🏠 {home}", callback_data=f"predpick_{mid}_nextgoal_home"))
        kb.add(InlineKeyboardButton(f"🏟 {away}", callback_data=f"predpick_{mid}_nextgoal_away"))
        kb.add(InlineKeyboardButton("🚫 কোনো গোল হবে না", callback_data=f"predpick_{mid}_nextgoal_none"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data=f"predict_{mid}"))
        bot.edit_message_text("পরবর্তী গোল কে করবে?", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)
    elif ptype == "5min":
        minute = feed.get("minute")
        if minute is None:
            bot.answer_callback_query(call.id, "এখন মিনিট জানা নেই।")
            return
        block_start = (minute // 5) * 5
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("✅ হ্যাঁ", callback_data=f"predpick_{mid}_5min_yes_{block_start}"))
        kb.add(InlineKeyboardButton("❌ না", callback_data=f"predpick_{mid}_5min_no_{block_start}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data=f"predict_{mid}"))
        bot.edit_message_text(f"⏳ {block_start}′ - {block_start+5}′ এর মধ্যে গোল হবে?", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)
    elif ptype == "finalscore":
        kb = InlineKeyboardMarkup(row_width=5)
        for i in range(6):
            kb.insert(InlineKeyboardButton(str(i), callback_data=f"fshome_{mid}_{i}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data=f"predict_{mid}"))
        bot.edit_message_text(f"🏟 {home} এর গোল সংখ্যা বেছে নিন (0-5):", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fshome_"))
def fs_home(call):
    _, mid, hg = call.data.split("_")
    mid = mid; hg = int(hg)
    feed = get_fs_match_feed(mid)
    away = feed["away"]
    kb = InlineKeyboardMarkup(row_width=5)
    for i in range(6):
        kb.insert(InlineKeyboardButton(str(i), callback_data=f"fsaway_{mid}_{hg}_{i}"))
    kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data=f"predict_{mid}"))
    bot.edit_message_text(f"🏟 {away} এর গোল সংখ্যা বেছে নিন (0-5):", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fsaway_"))
def fs_away(call):
    parts = call.data.split("_")
    mid = parts[1]; hg = int(parts[2]); ag = int(parts[3])
    uid = str(call.from_user.id)
    user = get_user(uid)
    # অটো-ফলো
    if mid not in user.get("followed", []):
        user.setdefault("followed", []).append(mid)
    with lock:
        if mid not in predictions:
            predictions[mid] = []
        predictions[mid].append({
            "user_id": uid,
            "type": "final_score",
            "choice": {"home": hg, "away": ag},
            "resolved": False,
            "correct": None
        })
    save_data()
    bot.answer_callback_query(call.id, "আপনার ফাইনাল স্কোর প্রেডিকশন জমা হয়েছে!")
    bot.edit_message_text(f"✅ আপনার প্রেডিকশন: {hg} - {ag}\nম্যাচ শেষে ফল জানানো হবে।", chat_id=call.message.chat.id, message_id=call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("predpick_"))
def pred_pick(call):
    data = call.data.split("_")
    mid = data[1]
    ptype = data[2]
    value = data[3]
    block_start = None
    if len(data) > 4:
        block_start = int(data[4])
    uid = str(call.from_user.id)
    user = get_user(uid)
    # অটো-ফলো
    if mid not in user.get("followed", []):
        user.setdefault("followed", []).append(mid)
    with lock:
        if mid not in predictions:
            predictions[mid] = []
        if ptype == "nextgoal":
            predictions[mid].append({
                "user_id": uid,
                "type": "next_goal_team",
                "choice": value,
                "resolved": False,
                "correct": None
            })
            confirm_text = "হোম টিম" if value=="home" else ("অ্যাওয়ে টিম" if value=="away" else "কোনো গোল হবে না")
        elif ptype == "5min":
            predictions[mid].append({
                "user_id": uid,
                "type": "goal_in_5_min",
                "choice": value,
                "block_start": block_start,
                "resolved": False,
                "correct": None
            })
            confirm_text = f"{block_start}′-{block_start+5}′ ব্লকে গোল { 'হবে' if value=='yes' else 'হবে না' }"
        else:
            return
    save_data()
    bot.answer_callback_query(call.id, "✅ প্রেডিকশন জমা হয়েছে!")
    bot.edit_message_text(f"✅ {confirm_text} – আপনার প্রেডিকশন জমা হয়েছে।", chat_id=call.message.chat.id, message_id=call.message.message_id)

# ---------- অন্যান্য কলব্যাক (সেটিংস, favteam) অপরিবর্তিত ----------
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
    if data.startswith("favteam_"):
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
