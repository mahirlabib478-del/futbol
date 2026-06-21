import os, json, time, random, threading, requests
from datetime import datetime
import pytz
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------- কনফিগ ----------
TOKEN = "8913363649:AAGb41xxF2fzrEgVtc862v9kT2Zx30ApfBo"
API_KEY = "6046069a0ac14753b91b0af15b94c834"
DATA_FILE = "user_data.json"

app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

# ---------- ডাটা লোড/সেভ ----------
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
            "quiz_score": 0
        }
    return user_data[uid]

# ---------- API হেল্পার ----------
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

def get_standings():
    data = api_get("https://api.football-data.org/v4/competitions/WC/standings")
    return data.get("standings", []) if data else []

def get_scorers():
    data = api_get("https://api.football-data.org/v4/competitions/WC/scorers?limit=10")
    return data.get("scorers", []) if data else []

def get_team_matches(team_name):
    all_m = get_live_upcoming()
    return [m for m in all_m if m["homeTeam"]["name"].lower() == team_name.lower() or m["awayTeam"]["name"].lower() == team_name.lower()]

def get_match_status_text(m):
    status = m["status"]
    if status == "IN_PLAY": return "🔴 লাইভ"
    if status == "SCHEDULED": return "⏰ আসন্ন"
    if status == "FINISHED": return "✅ শেষ"
    if status == "PAUSED": return "⏸️ বিরতি"
    return status

# ---------- মেনু ----------
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚽ ম্যাচ", callback_data="menu_matches"),
        InlineKeyboardButton("📋 আমার ম্যাচ", callback_data="menu_mymatches"),
        InlineKeyboardButton("🔍 সার্চ (দল)", callback_data="menu_search"),
        InlineKeyboardButton("📊 পয়েন্ট টেবিল", callback_data="menu_standings"),
        InlineKeyboardButton("⭐ টপ স্কোরার", callback_data="menu_scorers"),
        InlineKeyboardButton("🧠 কুইজ", callback_data="menu_quiz"),
        InlineKeyboardButton("⚙️ সেটিংস", callback_data="menu_settings"),
        InlineKeyboardButton("❓ সাহায্য", callback_data="menu_help")
    )
    return kb

def back_button(data="menu_main"):
    return InlineKeyboardButton("⬅️ ফিরে যান", callback_data=data)

# ---------- মেসেজ ----------
def edit_or_send(call, text, kb=None):
    try:
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=text, reply_markup=kb)
    except:
        bot.send_message(call.message.chat.id, text, reply_markup=kb)

def can_notify(user):
    if user["mute_start"] is not None and user["mute_end"] is not None:
        tz = pytz.timezone(user["timezone"])
        now = datetime.now(tz).hour
        s, e = user["mute_start"], user["mute_end"]
        if s < e:
            if s <= now < e: return False
        else:
            if now >= s or now < e: return False
    return True

# ---------- ব্যাকগ্রাউন্ড চেকার ----------
def match_checker():
    while True:
        with lock:
            all_users = dict(user_data)
        for uid, u in all_users.items():
            for mid in u.get("followed", []):
                if not can_notify(u):
                    continue
                data = get_match(mid)
                if not data:
                    continue
                status = data["status"]
                home_team = data["homeTeam"]["name"]
                away_team = data["awayTeam"]["name"]
                home_goals = data["score"]["fullTime"]["home"] or 0
                away_goals = data["score"]["fullTime"]["away"] or 0

                last_key = f"last_{mid}"
                last = u.get(last_key, {
                    "status": None, "hg": 0, "ag": 0,
                    "booking_count": 0,
                    "substitution_count": 0,
                    "last_goal_count": 0
                })
                ls = last["status"]
                lhg, lag = last["hg"], last["ag"]
                l_booking = last.get("booking_count", 0)
                l_sub = last.get("substitution_count", 0)
                l_goals = last.get("last_goal_count", 0)

                # ---- ম্যাচ শুরু ----
                if ls != "IN_PLAY" and status == "IN_PLAY":
                    bot.send_message(uid, f"⚽ ম্যাচ শুরু!\n{home_team} 🆚 {away_team}")

                # ---- গোল ----
                current_goals = home_goals + away_goals
                if status == "IN_PLAY" and current_goals > l_goals:
                    goals_data = data.get("goals", [])
                    if goals_data:
                        latest = goals_data[-1]
                        scorer = latest["scorer"]["name"]
                        minute = latest["minute"]
                        extra = ""
                        if latest.get("penalty"):
                            extra = " (পেনাল্টি)"
                        elif latest.get("ownGoal"):
                            extra = " (আত্মঘাতী)"
                        msg = f"⚽️ গোল! {scorer} {minute}′{extra}\n{home_team} {home_goals} - {away_goals} {away_team}"
                    else:
                        msg = f"⚽️ গোল!\n{home_team} {home_goals} - {away_goals} {away_team}"
                    bot.send_message(uid, msg)

                # ---- বুকিং (কার্ড) ----
                bookings = data.get("bookings", [])
                current_booking_count = len(bookings)
                if current_booking_count > l_booking:
                    new_bookings = bookings[l_booking:]
                    for b in new_bookings:
                        player = b["player"]["name"]
                        card = b["card"]
                        minute = b["minute"]
                        icon = "🟨" if card == "YELLOW" else "🟥"
                        bot.send_message(uid, f"{icon} {card} কার্ড: {player} ({minute}′)")

                # ---- খেলোয়াড় বদল ----
                subs = data.get("substitutions", [])
                current_sub_count = len(subs)
                if current_sub_count > l_sub:
                    new_subs = subs[l_sub:]
                    for s in new_subs:
                        out = s["playerOut"]["name"]
                        inp = s["playerIn"]["name"]
                        minute = s["minute"]
                        bot.send_message(uid, f"🔄 বদল: {out} ↓ / {inp} ↑ ({minute}′)")

                # ---- ম্যাচ শেষ ----
                if ls != "FINISHED" and status == "FINISHED":
                    bot.send_message(uid, f"🏁 খেলা শেষ!\n{home_team} {home_goals} - {away_goals} {away_team}")

                # স্টেট আপডেট
                user_data[uid][last_key] = {
                    "status": status,
                    "hg": home_goals,
                    "ag": away_goals,
                    "booking_count": current_booking_count,
                    "substitution_count": current_sub_count,
                    "last_goal_count": current_goals
                }
        save_data()
        time.sleep(30)

# ---------- কমান্ড ----------
@bot.message_handler(commands=['start'])
def start_cmd(msg):
    bot.send_message(msg.chat.id, "⚽ ফিফা বটে স্বাগতম!\nনিচের বাটন থেকে বেছে নিন:", reply_markup=main_menu_keyboard())

# ---------- কলব্যাক হ্যান্ডলার ----------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = str(call.from_user.id)
    user = get_user(uid)
    data = call.data

    # --- মেনু নেভিগেশন ---
    if data == "menu_main":
        edit_or_send(call, "⚽ প্রধান মেনু:", main_menu_keyboard())

    elif data == "menu_matches":
        matches = get_live_upcoming()[:10]
        if not matches:
            kb = InlineKeyboardMarkup().add(back_button())
            edit_or_send(call, "কোনো ম্যাচ পাওয়া যায়নি।", kb)
        else:
            for m in matches:
                mid = m["id"]
                ht, at = m["homeTeam"]["name"], m["awayTeam"]["name"]
                st = get_match_status_text(m)
                utc = m["utcDate"][:16].replace("T", " ")
                txt = f"{ht} 🆚 {at}\n{st} | {utc}"
                kb = InlineKeyboardMarkup()
                if str(mid) in user.get("followed", []):
                    kb.add(InlineKeyboardButton("❌ আনফলো", callback_data=f"unfollow_{mid}"))
                else:
                    kb.add(InlineKeyboardButton("➕ ফলো", callback_data=f"follow_{mid}"))
                kb.add(back_button("menu_matches"))
                bot.send_message(call.message.chat.id, txt, reply_markup=kb)

    elif data == "menu_mymatches":
        followed = user.get("followed", [])
        if not followed:
            kb = InlineKeyboardMarkup().add(back_button())
            edit_or_send(call, "আপনি কোনো ম্যাচ ফলো করছেন না।", kb)
        else:
            for mid in followed:
                m = get_match(mid)
                if m:
                    ht, at = m["homeTeam"]["name"], m["awayTeam"]["name"]
                    st = get_match_status_text(m)
                    txt = f"{ht} 🆚 {at}\n{st}"
                    kb = InlineKeyboardMarkup()
                    kb.add(InlineKeyboardButton("❌ আনফলো", callback_data=f"unfollow_{mid}"))
                    kb.add(back_button("menu_mymatches"))
                    bot.send_message(call.message.chat.id, txt, reply_markup=kb)
            kb = InlineKeyboardMarkup().add(back_button())
            bot.send_message(call.message.chat.id, "উপরে আপনার ফলো করা ম্যাচ।", reply_markup=kb)

    elif data == "menu_search":
        teams = ["Brazil", "Argentina", "Germany", "France", "England", "Spain", "Portugal", "Netherlands", "Italy", "Belgium", "Croatia", "Uruguay"]
        kb = InlineKeyboardMarkup(row_width=3)
        for t in teams:
            kb.add(InlineKeyboardButton(t, callback_data=f"search_{t}"))
        kb.add(back_button())
        edit_or_send(call, "একটি দল বেছে নিন:", kb)

    elif data.startswith("search_"):
        team = data.split("_", 1)[1]
        matches = get_team_matches(team)[:5]
        if not matches:
            kb = InlineKeyboardMarkup().add(back_button("menu_search"))
            edit_or_send(call, f"{team} দলের কোনো লাইভ/আসন্ন ম্যাচ নেই।", kb)
        else:
            for m in matches:
                mid = m["id"]
                ht, at = m["homeTeam"]["name"], m["awayTeam"]["name"]
                st = get_match_status_text(m)
                utc = m["utcDate"][:16].replace("T", " ")
                txt = f"{ht} 🆚 {at}\n{st} | {utc}"
                kb = InlineKeyboardMarkup()
                if str(mid) in user.get("followed", []):
                    kb.add(InlineKeyboardButton("❌ আনফলো", callback_data=f"unfollow_{mid}"))
                else:
                    kb.add(InlineKeyboardButton("➕ ফলো", callback_data=f"follow_{mid}"))
                kb.add(back_button("menu_search"))
                bot.send_message(call.message.chat.id, txt, reply_markup=kb)

    elif data == "menu_standings":
        groups = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
        kb = InlineKeyboardMarkup(row_width=5)
        for g in groups:
            kb.add(InlineKeyboardButton(f"গ্রুপ {g}", callback_data=f"group_{g}"))
        kb.add(back_button())
        edit_or_send(call, "গ্রুপ বেছে নিন:", kb)

    elif data.startswith("group_"):
        grp = data.split("_")[1]
        st_data = get_standings()
        table = None
        for s in st_data:
            if s["group"] == f"GROUP_{grp}":
                table = s["table"]
                break
        if not table:
            kb = InlineKeyboardMarkup().add(back_button("menu_standings"))
            edit_or_send(call, f"গ্রুপ {grp} এর ডাটা পাওয়া যায়নি।", kb)
        else:
            txt = f"📊 গ্রুপ {grp}:\n"
            for row in table:
                team = row["team"]["name"]
                pts = row["points"]
                gd = row["goalDifference"]
                txt += f"{team} - {pts}pts (GD {gd})\n"
            kb = InlineKeyboardMarkup().add(back_button("menu_standings"))
            edit_or_send(call, txt, kb)

    elif data == "menu_scorers":
        scorers = get_scorers()
        if not scorers:
            kb = InlineKeyboardMarkup().add(back_button())
            edit_or_send(call, "স্কোরার পাওয়া যায়নি।", kb)
        else:
            txt = "⭐ টপ স্কোরার:\n"
            for i, s in enumerate(scorers[:10], 1):
                name = s["player"]["name"]
                goals = s["goals"]
                team = s["team"]["name"]
                txt += f"{i}. {name} ({team}) - {goals} গোল\n"
            kb = InlineKeyboardMarkup().add(back_button())
            edit_or_send(call, txt, kb)

    elif data == "menu_quiz":
        q = random.choice([
            {"q": "বিশ্বকাপে সবচেয়ে বেশি গোলদাতা কে?", "opts": ["মিরোস্লাভ ক্লোজে", "রোনালদো", "পেলে", "ম্যারাডোনা"], "ans": 0},
            {"q": "২০২২ বিশ্বকাপের চ্যাম্পিয়ন কোন দেশ?", "opts": ["আর্জেন্টিনা", "ফ্রান্স", "ব্রাজিল", "জার্মানি"], "ans": 0},
            {"q": "ফিফা বিশ্বকাপ কত বছর পরপর হয়?", "opts": ["৪ বছর", "২ বছর", "৩ বছর", "৫ বছর"], "ans": 0},
            {"q": "বাংলাদেশ কোন কনফেডারেশনের অন্তর্ভুক্ত?", "opts": ["এএফসি", "উয়েফা", "কনকাকাফ", "কাফ"], "ans": 0},
            {"q": "পুসকাস পুরস্কার কীসের জন্য দেওয়া হয়?", "opts": ["সেরা গোল", "সেরা খেলোয়াড়", "সেরা কোচ", "ফেয়ার প্লে"], "ans": 0},
        ])
        user["quiz_current"] = q
        kb = InlineKeyboardMarkup(row_width=2)
        for i, opt in enumerate(q["opts"]):
            kb.add(InlineKeyboardButton(opt, callback_data=f"quiz_ans_{i}"))
        kb.add(back_button())
        edit_or_send(call, "🧠 " + q["q"], kb)

    elif data.startswith("quiz_ans_"):
        idx = int(data.split("_")[2])
        q = user.get("quiz_current")
        if not q:
            bot.answer_callback_query(call.id, "কুইজের মেয়াদ শেষ।")
            return
        correct = idx == q["ans"]
        if correct:
            user["quiz_score"] = user.get("quiz_score", 0) + 10
            bot.answer_callback_query(call.id, "✅ সঠিক! +10 পয়েন্ট")
        else:
            bot.answer_callback_query(call.id, f"❌ ভুল! উত্তর: {q['opts'][q['ans']]}")
        user.pop("quiz_current", None)
        save_data()
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔄 আরেকটি কুইজ", callback_data="menu_quiz"),
            back_button()
        )
        edit_or_send(call, f"আপনার স্কোর: {user['quiz_score']}", kb)

    elif data == "menu_settings":
        tz = user["timezone"]
        ms = user["mute_start"] if user["mute_start"] is not None else "না"
        me = user["mute_end"] if user["mute_end"] is not None else "না"
        txt = f"⚙️ আপনার সেটিংস:\n🕒 টাইমজোন: {tz}\n🔇 নীরব: {ms}:00 - {me}:00"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🕒 টাইমজোন বদলান", callback_data="set_tz"))
        kb.add(InlineKeyboardButton("🔇 নীরবতা সেট করুন", callback_data="set_mute"))
        kb.add(InlineKeyboardButton("🔇 নীরবতা বন্ধ করুন", callback_data="clear_mute"))
        kb.add(back_button())
        edit_or_send(call, txt, kb)

    elif data == "clear_mute":
        user["mute_start"] = None
        user["mute_end"] = None
        save_data()
        bot.answer_callback_query(call.id, "নীরবতা বন্ধ করা হয়েছে।")
        edit_or_send(call, "✅ নীরবতা বন্ধ। সব নোটিফিকেশন আসবে।", InlineKeyboardMarkup().add(back_button("menu_settings")))

    elif data == "set_tz":
        tzs = ["Asia/Dhaka", "Asia/Kolkata", "Europe/London", "America/New_York", "Asia/Dubai"]
        kb = InlineKeyboardMarkup(row_width=2)
        for t in tzs:
            kb.add(InlineKeyboardButton(t, callback_data=f"tz_{t}"))
        kb.add(back_button("menu_settings"))
        edit_or_send(call, "আপনার টাইমজোন বেছে নিন:", kb)

    elif data.startswith("tz_"):
        new_tz = data.split("_", 1)[1]
        user["timezone"] = new_tz
        save_data()
        bot.answer_callback_query(call.id, f"টাইমজোন {new_tz} সেট হয়েছে।")
        edit_or_send(call, f"✅ টাইমজোন: {new_tz}", InlineKeyboardMarkup().add(back_button("menu_settings")))

    elif data == "set_mute":
        kb = InlineKeyboardMarkup(row_width=6)
        for h in range(0, 24):
            kb.add(InlineKeyboardButton(str(h), callback_data=f"mutehour_{h}"))
        kb.add(back_button("menu_settings"))
        edit_or_send(call, "নীরবতার শুরু ঘণ্টা বেছে নিন (24h ফরম্যাট):", kb)

    elif data.startswith("mutehour_"):
        h = int(data.split("_")[1])
        if user.get("mute_start") is None:
            user["mute_start"] = h
            save_data()
            bot.answer_callback_query(call.id, f"শুরু: {h}:00")
            kb = InlineKeyboardMarkup(row_width=6)
            for h2 in range(0, 24):
                kb.add(InlineKeyboardButton(str(h2), callback_data=f"muteend_{h}_{h2}"))
            kb.add(back_button("menu_settings"))
            edit_or_send(call, f"নীরবতার শেষ ঘণ্টা বেছে নিন:", kb)

    elif data.startswith("muteend_"):
        parts = data.split("_")
        s = int(parts[1])
        e = int(parts[2])
        user["mute_start"] = s
        user["mute_end"] = e
        save_data()
        bot.answer_callback_query(call.id, f"নীরবতা: {s}:00 - {e}:00")
        edit_or_send(call, f"✅ নীরবতা সেট: {s}:00 - {e}:00", InlineKeyboardMarkup().add(back_button("menu_settings")))

    elif data == "menu_help":
        txt = ("ℹ️ সাহায্য:\n\n"
               "⚽ ম্যাচ – লাইভ/আসন্ন ম্যাচ দেখুন ও ফলো করুন।\n"
               "📋 আমার ম্যাচ – আপনার ফলো করা ম্যাচ দেখুন।\n"
               "🔍 সার্চ – জনপ্রিয় দলের ম্যাচ খুঁজুন।\n"
               "📊 পয়েন্ট টেবিল – গ্রুপভিত্তিক স্ট্যান্ডিং।\n"
               "⭐ টপ স্কোরার – সেরা গোলদাতাদের তালিকা।\n"
               "🧠 কুইজ – ফুটবল কুইজ খেলে পয়েন্ট জিতুন।\n"
               "⚙️ সেটিংস – টাইমজোন ও নীরবতা সেট করুন।\n\n"
               "ফলো করলে অটো নোটিফিকেশন:\n"
               "• ম্যাচ শুরু\n"
               "• গোল (নাম, মিনিট, পেনাল্টি/আত্মঘাতী)\n"
               "• হলুদ/লাল কার্ড\n"
               "• খেলোয়াড় বদল\n"
               "• ম্যাচ শেষ ও ফলাফল")
        kb = InlineKeyboardMarkup().add(back_button())
        edit_or_send(call, txt, kb)

    # --- ফলো / আনফলো ---
    elif data.startswith("follow_"):
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

    else:
        bot.answer_callback_query(call.id)

# ---------- যেকোনো টেক্সট = মেনু ----------
@bot.message_handler(func=lambda m: True)
def any_msg(msg):
    bot.send_message(msg.chat.id, "⚽ প্রধান মেনু:", reply_markup=main_menu_keyboard())

# ---------- হেলথ চেক ----------
@app.route("/")
def home():
    return "Bot is running!"

# ---------- চালু ----------
if __name__ == "__main__":
    # ব্যাকগ্রাউন্ড চেকার
    threading.Thread(target=match_checker, daemon=True).start()
    # টেলিগ্রাম পোলিং
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    # ফ্লাস্ক
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
