import os, json, time, random, threading, requests
from datetime import datetime
import pytz
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ---------- কনফিগ ----------
TOKEN = "8913363649:AAGb41xxF2fzrEgVtc862v9kT2Zx30ApfBo"
API_KEY = "6046069a0ac14753b91b0af15b94c834"
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

# ---------- ডাটা ----------
user_data = {}
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
            "followed": [],
            "timezone": "Asia/Dhaka",
            "mute_start": None,
            "mute_end": None,
            "username": None,
            "fav_team": None,
            "notifications_enabled": True,
            "auto_unfollow_times": {}
        }
    return user_data[uid]

# ---------- API ----------
def api_get(url):
    headers = {"X-Auth-Token": API_KEY}
    try:
        return requests.get(url, headers=headers, timeout=10).json()
    except:
        return None

def get_match(mid):
    return api_get(f"https://api.football-data.org/v4/matches/{mid}")

def get_live_upcoming():
    data = api_get("https://api.football-data.org/v4/competitions/WC/matches?status=LIVE,SCHEDULED")
    return data.get("matches", []) if data else []

def get_live_matches():
    data = api_get("https://api.football-data.org/v4/competitions/WC/matches?status=LIVE")
    return data.get("matches", []) if data else []

def get_today_matches():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    url = f"https://api.football-data.org/v4/competitions/WC/matches?dateFrom={today}&dateTo={today}"
    data = api_get(url)
    return data.get("matches", []) if data else []

def get_scorers():
    data = api_get("https://api.football-data.org/v4/competitions/WC/scorers?limit=10")
    return data.get("scorers", []) if data else []

def get_match_status_text(m):
    status = m["status"]
    if status == "IN_PLAY": return "🔴 লাইভ"
    if status == "SCHEDULED": return "⏰ আসন্ন"
    if status == "FINISHED": return "✅ শেষ"
    if status == "PAUSED": return "⏸️ বিরতি"
    return status

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

# ---------- স্থায়ী কিবোর্ড ----------
def main_reply_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.row(KeyboardButton("⚽ ম্যাচ"), KeyboardButton("📅 আজকের ম্যাচ"))
    kb.row(KeyboardButton("📊 লাইভ স্ট্যাটস"), KeyboardButton("📋 আমার ম্যাচ"))
    kb.row(KeyboardButton("⭐ পছন্দের দল"), KeyboardButton("⭐ টপ স্কোরার"))
    kb.row(KeyboardButton("⚙️ সেটিংস"), KeyboardButton("❓ সাহায্য"))
    return kb

# ---------- নোটিফিকেশন চেক ----------
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

# ---------- ব্যাকগ্রাউন্ড চেকার (১০ সেকেন্ড পরপর) ----------
def match_checker():
    while True:
        with lock:
            all_users = dict(user_data)
        for uid, u in all_users.items():
            auto_times = u.get("auto_unfollow_times", {})
            for mid in list(auto_times.keys()):
                if time.time() >= auto_times[mid]:
                    if mid in u.get("followed", []):
                        u["followed"].remove(mid)
                    del user_data[uid]["auto_unfollow_times"][mid]

            for mid in u.get("followed", []):
                notify = can_notify(u)
                data = get_match(mid)
                if not data:
                    continue
                status = data["status"]
                home_team = data["homeTeam"]["name"]
                away_team = data["awayTeam"]["name"]
                home_goals = data["score"]["fullTime"]["home"] or 0
                away_goals = data["score"]["fullTime"]["away"] or 0
                fav = u.get("fav_team")

                last_key = f"last_{mid}"
                last = u.get(last_key, {
                    "status": None, "hg": 0, "ag": 0,
                    "booking_count": 0,
                    "substitution_count": 0,
                    "last_goal_count": 0
                })
                ls = last["status"]
                l_booking = last.get("booking_count", 0)
                l_sub = last.get("substitution_count", 0)
                l_goals = last.get("last_goal_count", 0)

                h_display = f"⭐ {home_team}" if fav and fav.lower() == home_team.lower() else home_team
                a_display = f"⭐ {away_team}" if fav and fav.lower() == away_team.lower() else away_team

                if ls != "IN_PLAY" and status == "IN_PLAY":
                    if notify:
                        bot.send_message(uid, f"⚽ ম্যাচ শুরু!\n{h_display} 🆚 {a_display}")

                current_goals = home_goals + away_goals
                if status == "IN_PLAY" and current_goals > l_goals:
                    if notify:
                        goals_data = data.get("goals", [])
                        if goals_data:
                            latest = goals_data[-1]
                            scorer = latest["scorer"]["name"]
                            minute = latest["minute"]
                            extra = ""
                            if latest.get("penalty"): extra = " (পেনাল্টি)"
                            elif latest.get("ownGoal"): extra = " (আত্মঘাতী)"
                            msg = f"⚽️ গোল! {scorer} {minute}′{extra}\n{h_display} {home_goals} - {away_goals} {a_display}"
                        else:
                            msg = f"⚽️ গোল!\n{h_display} {home_goals} - {away_goals} {a_display}"
                        bot.send_message(uid, msg)

                bookings = data.get("bookings", [])
                current_booking_count = len(bookings)
                if current_booking_count > l_booking:
                    if notify:
                        new_bookings = bookings[l_booking:]
                        for b in new_bookings:
                            player = b["player"]["name"]
                            card = b["card"]
                            minute = b["minute"]
                            icon = "🟨" if card == "YELLOW" else "🟥"
                            bot.send_message(uid, f"{icon} {card} কার্ড: {player} ({minute}′)")

                subs = data.get("substitutions", [])
                current_sub_count = len(subs)
                if current_sub_count > l_sub:
                    if notify:
                        new_subs = subs[l_sub:]
                        for s in new_subs:
                            out = s["playerOut"]["name"]
                            inp = s["playerIn"]["name"]
                            minute = s["minute"]
                            bot.send_message(uid, f"🔄 বদল: {out} ↓ / {inp} ↑ ({minute}′)")

                if ls != "FINISHED" and status == "FINISHED":
                    if notify:
                        bot.send_message(uid, f"🏁 খেলা শেষ!\n{home_team} {home_goals} - {away_goals} {away_team}")
                    user_data[uid].setdefault("auto_unfollow_times", {})[mid] = time.time() + 60

                user_data[uid][last_key] = {
                    "status": status,
                    "hg": home_goals,
                    "ag": away_goals,
                    "booking_count": current_booking_count,
                    "substitution_count": current_sub_count,
                    "last_goal_count": current_goals
                }
        save_data()
        time.sleep(10)

# ---------- /start ----------
@bot.message_handler(commands=['start'])
def start_cmd(msg):
    user = get_user(msg.from_user.id)
    user["username"] = msg.from_user.username or msg.from_user.first_name
    save_data()
    bot.send_message(msg.chat.id, "⚽ ফিফা বটে স্বাগতম!\nনিচের মেনু থেকে বেছে নিন 👇", reply_markup=main_reply_keyboard())

# ---------- মেনু হ্যান্ডলার ----------
@bot.message_handler(func=lambda m: m.text in [
    "⚽ ম্যাচ", "📅 আজকের ম্যাচ", "📊 লাইভ স্ট্যাটস", "📋 আমার ম্যাচ",
    "⭐ পছন্দের দল", "⭐ টপ স্কোরার", "⚙️ সেটিংস", "❓ সাহায্য"
])
def menu_text_handler(msg):
    uid = str(msg.from_user.id)
    user = get_user(uid)
    user["username"] = msg.from_user.username or msg.from_user.first_name
    save_data()
    text = msg.text

    if text == "⚽ ম্যাচ":
        matches = get_live_upcoming()[:10]
        show_matches(msg.chat.id, matches, user)

    elif text == "📅 আজকের ম্যাচ":
        matches = get_today_matches()[:10]
        if not matches:
            bot.send_message(msg.chat.id, "আজকের কোনো ম্যাচ নেই।")
        else:
            show_matches(msg.chat.id, matches, user)

    elif text == "📊 লাইভ স্ট্যাটস":
        live = get_live_matches()
        if not live:
            bot.send_message(msg.chat.id, "এখন কোনো লাইভ ম্যাচ নেই।")
            return
        for m in live:
            mid = m["id"]
            home = m["homeTeam"]["name"]
            away = m["awayTeam"]["name"]
            home_goals = m["score"]["fullTime"]["home"] or 0
            away_goals = m["score"]["fullTime"]["away"] or 0
            minute = m.get("minute", "N/A")
            txt = f"🔴 {home} {home_goals} - {away_goals} {away} | ⏱ {minute}′"
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("📊 বিস্তারিত", callback_data=f"livestats_{mid}"))
            kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
            bot.send_message(msg.chat.id, txt, reply_markup=kb)

    elif text == "📋 আমার ম্যাচ":
        followed = user.get("followed", [])
        if not followed:
            bot.send_message(msg.chat.id, "আপনি কোনো ম্যাচ ফলো করছেন না।")
        else:
            for mid in followed:
                m = get_match(mid)
                if m:
                    show_match_line(msg.chat.id, m, user)

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
        scorers = get_scorers()
        if not scorers:
            bot.send_message(msg.chat.id, "স্কোরার পাওয়া যায়নি।")
        else:
            txt = "⭐ টপ স্কোরার:\n"
            for i, s in enumerate(scorers[:10], 1):
                name = s["player"]["name"]
                goals = s["goals"]
                team = s["team"]["name"]
                txt += f"{i}. {name} ({team}) - {goals} গোল\n"
            bot.send_message(msg.chat.id, txt)

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
               "⚽ ম্যাচ – লাইভ/আসন্ন ম্যাচ দেখুন ও ফলো করুন।\n"
               "📅 আজকের ম্যাচ – শুধু আজকের ফিফা ম্যাচ।\n"
               "📊 লাইভ স্ট্যাটস – চলমান ম্যাচের বিস্তারিত (গোল, কার্ড, বদল)।\n"
               "📋 আমার ম্যাচ – ফলো করা ম্যাচ।\n"
               "⭐ পছন্দের দল – প্রিয় দল সেট করে নোটিফিকেশনে স্টার দেখুন।\n"
               "⭐ টপ স্কোরার – সেরা গোলদাতা।\n"
               "⚙️ সেটিংস – টাইমজোন, নীরবতা, নোটিফিকেশন অন/অফ।\n\n"
               "ফলো করলে অটো নোটিফিকেশন: শুরু, গোল, কার্ড, বদল, শেষ।\n"
               "ম্যাচ শেষের ১ মিনিট পর অটো আনফলো হয়ে যাবে।\n"
               "সময়সূচি সবসময় ঢাকা সময়ে দেখানো হয়।")
        bot.send_message(msg.chat.id, txt)

# ---------- ম্যাচ দেখানো ----------
def show_match_line(chat_id, m, user):
    mid = m["id"]
    home = m["homeTeam"]["name"]
    away = m["awayTeam"]["name"]
    st = get_match_status_text(m)
    time_str = utc_to_dhaka(m["utcDate"])
    fav = user.get("fav_team")
    h_disp = f"⭐ {home}" if fav and fav.lower() == home.lower() else home
    a_disp = f"⭐ {away}" if fav and fav.lower() == away.lower() else away
    txt = f"{h_disp} 🆚 {a_disp}\n{st} | {time_str}"
    kb = InlineKeyboardMarkup()
    if str(mid) in user.get("followed", []):
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

# ---------- ইনলাইন কলব্যাক ----------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = str(call.from_user.id)
    user = get_user(uid)
    data = call.data

    if data == "menu_main":
        try:
            bot.edit_message_text("⚽ প্রধান মেনুতে ফিরেছেন।",
                                  chat_id=call.message.chat.id,
                                  message_id=call.message.message_id)
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
    elif data.startswith("livestats_"):
        mid = data.split("_")[1]
        m = get_match(mid)
        if not m:
            bot.answer_callback_query(call.id, "ম্যাচ পাওয়া যায়নি।")
            return
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        home_g = m["score"]["fullTime"]["home"] or 0
        away_g = m["score"]["fullTime"]["away"] or 0
        minute = m.get("minute", "N/A")
        txt = f"🔴 {home} {home_g} - {away_g} {away}\n⏱ {minute}′\n\n"
        goals = m.get("goals", [])
        if goals:
            txt += "⚽ গোল:\n"
            for g in goals:
                txt += f"{g['scorer']['name']} {g['minute']}′\n"
        bookings = m.get("bookings", [])
        if bookings:
            txt += "\n🟨/🟥 কার্ড:\n"
            for b in bookings:
                txt += f"{b['player']['name']} ({b['card']}) {b['minute']}′\n"
        subs = m.get("substitutions", [])
        if subs:
            txt += "\n🔄 বদল:\n"
            for s in subs:
                txt += f"{s['playerOut']['name']} ↓ / {s['playerIn']['name']} ↑ {s['minute']}′\n"
        if not goals and not bookings and not subs:
            txt += "এখনো কোনো উল্লেখযোগ্য ঘটনা নেই।"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 রিফ্রেশ", callback_data=f"livestats_{mid}"))
        kb.add(InlineKeyboardButton("⬅️ ফিরে যান", callback_data="menu_main"))
        bot.send_message(call.message.chat.id, txt, reply_markup=kb)
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

# ---------- চালু (ওয়েবহুক মুছে পোলিং শুরু) ----------
if __name__ == "__main__":
    # পুরনো ওয়েবহুক থাকলে তা সরিয়ে ফেলা
    bot.remove_webhook()
    time.sleep(1)  # নিশ্চিত হতে একটু অপেক্ষা

    # ব্যাকগ্রাউন্ড চেকার থ্রেড
    threading.Thread(target=match_checker, daemon=True).start()

    # টেলিগ্রাম পোলিং থ্রেড
    threading.Thread(target=bot.infinity_polling, daemon=True).start()

    # ফ্লাস্ক ওয়েব সার্ভার
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
