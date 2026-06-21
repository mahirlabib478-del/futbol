import os
import threading
import time
import requests
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------- কনফিগ ----------
TOKEN = "8913363649:AAGb41xxF2fzrEgVtc862v9kT2Zx30ApfBo"
API_KEY = "6046069a0ac14753b91b0af15b94c834"    # football-data.org টোকেন

# ---------- ফ্লাস্ক ও বট ----------
app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

# ---------- সাবস্ক্রিপশন স্টোর ----------
subscriptions = {}
lock = threading.Lock()

# ---------- API হেল্পার ----------
def get_match_data(match_id):
    url = f"https://api.football-data.org/v4/matches/{match_id}"
    headers = {"X-Auth-Token": API_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print("API error:", e)
        return None

def get_live_upcoming_fifa_matches():
    """ফিফা ওয়ার্ল্ড কাপ (WC) এবং কোয়ালিফায়ার (WCQ?) এর লাইভ ও আসন্ন ম্যাচ আনে।
    football-data.org-এ WC কম্পিটিশনেই কোয়ালিফায়ার সহ সব থাকে। তাই আমরা শুধু WC কোড ইউজ করবো।
    status ফিল্টার: LIVE, SCHEDULED"""
    url = "https://api.football-data.org/v4/competitions/WC/matches?status=LIVE,SCHEDULED"
    headers = {"X-Auth-Token": API_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json().get("matches", [])
        else:
            # ব্যাকআপ: /matches এন্ডপয়েন্ট ব্যবহার
            url2 = "https://api.football-data.org/v4/matches?competitions=WC&status=LIVE,SCHEDULED"
            r2 = requests.get(url2, headers=headers, timeout=10)
            if r2.status_code == 200:
                return r2.json().get("matches", [])
            return []
    except Exception as e:
        print("List API error:", e)
        return []

def send_message(chat_id, text, reply_markup=None):
    try:
        bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        print(f"Send error to {chat_id}: {e}")

# ---------- ব্যাকগ্রাউন্ড চেকার ----------
def match_checker():
    while True:
        with lock:
            subs_copy = dict(subscriptions)
        for chat_id, sub in subs_copy.items():
            match_id = sub["match_id"]
            data = get_match_data(match_id)
            if not data:
                continue

            status = data.get("status")
            home_goals = data["score"]["fullTime"]["home"] or 0
            away_goals = data["score"]["fullTime"]["away"] or 0
            home_team = data["homeTeam"]["name"]
            away_team = data["awayTeam"]["name"]

            last_status = sub["last_status"]
            last_home = sub["last_home"]
            last_away = sub["last_away"]

            if last_status != "IN_PLAY" and status == "IN_PLAY":
                send_message(chat_id, f"⚽ ম্যাচ শুরু!\n{home_team} 🆚 {away_team}")
            elif last_status == "IN_PLAY" and status == "IN_PLAY":
                if home_goals > last_home or away_goals > last_away:
                    send_message(chat_id, f"⚽️ গোল!\n{home_team} {home_goals} - {away_goals} {away_team}")
            elif last_status != "FINISHED" and status == "FINISHED":
                send_message(chat_id, f"🏁 খেলা শেষ!\n{home_team} {home_goals} - {away_goals} {away_team}")

            with lock:
                if chat_id in subscriptions:
                    subscriptions[chat_id]["last_status"] = status
                    subscriptions[chat_id]["last_home"] = home_goals
                    subscriptions[chat_id]["last_away"] = away_goals
        time.sleep(30)

# ---------- কমান্ড হ্যান্ডলার ----------
@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.reply_to(message,
        "⚽ ফিফা ম্যাচ ফলো বট\n\n"
        "কমান্ড:\n"
        "/matches – এখন খেলা / আসন্ন ম্যাচ দেখুন ও ফলো করুন\n"
        "/follow <match_id> – সরাসরি আইডি দিয়ে ফলো\n"
        "/unfollow – ফলো বন্ধ করুন")

@bot.message_handler(commands=['matches'])
def list_matches(message):
    matches = get_live_upcoming_fifa_matches()
    if not matches:
        bot.reply_to(message, "❌ কোনো ম্যাচ পাওয়া যায়নি। পরে চেষ্টা করুন।")
        return

    for m in matches[:8]:  # একবারে সর্বোচ্চ ৮টা ম্যাচ দেখাবে (স্প্যাম এড়াতে)
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        status = m.get("status")
        utc_time = m.get("utcDate", "সময় নেই")

        # স্ট্যাটাস বাংলা করা
        if status == "IN_PLAY":
            status_bn = "🔴 লাইভ"
        elif status == "SCHEDULED":
            status_bn = "⏰ আসন্ন"
        elif status == "FINISHED":
            status_bn = "✅ শেষ"
        else:
            status_bn = status

        match_id = m["id"]
        text = f"{home} 🆚 {away}\n{status_bn} | {utc_time[:16].replace('T', ' ')}"

        # ইনলাইন বাটন: Follow
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("➕ ফলো করুন", callback_data=f"follow_{match_id}"))
        send_message(message.chat.id, text, reply_markup=markup)

@bot.message_handler(commands=['follow'])
def follow_cmd(message):
    try:
        match_id = message.text.split()[1]
    except IndexError:
        bot.reply_to(message, "দয়া করে ম্যাচ আইডি দিন। যেমন: /follow 327125")
        return
    subscribe_match(message.chat.id, match_id, message)

def subscribe_match(chat_id, match_id, message_or_call=None):
    """ম্যাচ সাবস্ক্রাইব করার সাধারণ ফাংশন। message_or_call থেকে রিপ্লাই হবে।"""
    data = get_match_data(match_id)
    if not data or "status" not in data:
        if isinstance(message_or_call, telebot.types.Message):
            bot.reply_to(message_or_call, "ম্যাচ পাওয়া যায়নি বা API সমস্যা।")
        else:
            bot.answer_callback_query(message_or_call.id, "API সমস্যা, পরে চেষ্টা করুন।")
        return

    with lock:
        subscriptions[chat_id] = {
            "match_id": match_id,
            "last_status": data.get("status"),
            "last_home": data["score"]["fullTime"]["home"] or 0,
            "last_away": data["score"]["fullTime"]["away"] or 0
        }

    home = data["homeTeam"]["name"]
    away = data["awayTeam"]["name"]
    if isinstance(message_or_call, telebot.types.Message):
        bot.reply_to(message_or_call, f"✅ ফলো করা হচ্ছে:\n{home} 🆚 {away}\nম্যাচ শুরু/গোল/শেষে আপডেট পাবেন।")
    else: # CallbackQuery
        bot.answer_callback_query(message_or_call.id, f"{home} vs {away} ফলো করা হয়েছে।")
        # ঐ মেসেজ আপডেট করে দেওয়া যেতে পারে
        bot.edit_message_text(
            f"✅ {home} vs {away}\nফলো করা হচ্ছে।",
            chat_id=message_or_call.message.chat.id,
            message_id=message_or_call.message.message_id,
            reply_markup=None
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith("follow_"))
def callback_follow(call):
    match_id = call.data.split("_")[1]
    subscribe_match(call.message.chat.id, match_id, call)

@bot.message_handler(commands=['unfollow'])
def unfollow_cmd(message):
    with lock:
        if message.chat.id in subscriptions:
            del subscriptions[message.chat.id]
            bot.reply_to(message, "❌ ফলো বন্ধ করা হয়েছে।")
        else:
            bot.reply_to(message, "আপনি কোনো ম্যাচ ফলো করছেন না।")

# ---------- হেলথ চেক ----------
@app.route("/")
def home():
    return "Bot is running!"

# ---------- চালু ----------
if __name__ == "__main__":
    threading.Thread(target=match_checker, daemon=True).start()
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
