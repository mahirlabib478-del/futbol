import os
import threading
import time
import requests
from flask import Flask
import telebot

# ---------- কনফিগ ----------
TOKEN = "8913363649:AAGb41xxF2fzrEgVtc862v9kT2Zx30ApfBo"
API_KEY = "6046069a0ac14753b91b0af15b94c834"   # football-data.org টোকেন

# ---------- ফ্লাস্ক ও বট ----------
app = Flask(__name__)
bot = telebot.TeleBot(TOKEN)

# ---------- সাবস্ক্রিপশন স্টোর (in-memory) ----------
# { chat_id: { "match_id": ..., "last_status": None, "last_home": 0, "last_away": 0 } }
subscriptions = {}
lock = threading.Lock()

# ---------- হেল্পার ফাংশন ----------
def get_match_data(match_id):
    url = f"https://api.football-data.org/v4/matches/{match_id}"
    headers = {"X-Auth-Token": API_KEY}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print("API error:", e)
        return None

def send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text)
    except Exception as e:
        print(f"Send error to {chat_id}: {e}")

# ---------- ব্যাকগ্রাউন্ড ম্যাচ চেকার (প্রতি ৩০ সেকেন্ড) ----------
def match_checker():
    while True:
        with lock:
            subs_copy = dict(subscriptions)
        for chat_id, sub in subs_copy.items():
            match_id = sub["match_id"]
            data = get_match_data(match_id)
            if not data:
                continue

            status = data.get("status")         # SCHEDULED, IN_PLAY, FINISHED...
            home_goals = data["score"]["fullTime"]["home"] or 0
            away_goals = data["score"]["fullTime"]["away"] or 0
            home_team = data["homeTeam"]["name"]
            away_team = data["awayTeam"]["name"]

            last_status = sub["last_status"]
            last_home = sub["last_home"]
            last_away = sub["last_away"]

            # 1. ম্যাচ শুরু
            if last_status != "IN_PLAY" and status == "IN_PLAY":
                send_message(chat_id, f"⚽ ম্যাচ শুরু!\n{home_team} 🆚 {away_team}")

            # 2. গোল (ইন-প্লে অবস্থায় গোল বেড়েছে)
            elif last_status == "IN_PLAY" and status == "IN_PLAY":
                if home_goals > last_home or away_goals > last_away:
                    send_message(chat_id,
                        f"⚽️ গোল!\n{home_team} {home_goals} - {away_goals} {away_team}")

            # 3. ম্যাচ শেষ
            elif last_status != "FINISHED" and status == "FINISHED":
                send_message(chat_id,
                    f"🏁 খেলা শেষ!\n{home_team} {home_goals} - {away_goals} {away_team}")

            # স্টেট আপডেট
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
        "স্বাগতম! ফিফা ম্যাচ ফলো বটে।\n\n"
        "কমান্ড:\n"
        "/follow <match_id> – ম্যাচ ফলো করা\n"
        "/unfollow – ফলো বন্ধ করা\n\n"
        "উদাহরণ: /follow 327125")

@bot.message_handler(commands=['follow'])
def follow_cmd(message):
    try:
        match_id = message.text.split()[1]
    except IndexError:
        bot.reply_to(message, "দয়া করে ম্যাচ আইডি দিন। যেমন: /follow 327125")
        return

    data = get_match_data(match_id)
    if not data or "status" not in data:
        bot.reply_to(message, "ম্যাচ পাওয়া যায়নি বা API সমস্যা।")
        return

    with lock:
        subscriptions[message.chat.id] = {
            "match_id": match_id,
            "last_status": data.get("status"),
            "last_home": data["score"]["fullTime"]["home"] or 0,
            "last_away": data["score"]["fullTime"]["away"] or 0
        }

    home = data["homeTeam"]["name"]
    away = data["awayTeam"]["name"]
    bot.reply_to(message, f"✅ ফলো করা হচ্ছে:\n{home} 🆚 {away}\nম্যাচ শুরু/গোল/শেষে আপডেট পাবেন।")

@bot.message_handler(commands=['unfollow'])
def unfollow_cmd(message):
    with lock:
        if message.chat.id in subscriptions:
            del subscriptions[message.chat.id]
            bot.reply_to(message, "❌ ফলো বন্ধ করা হয়েছে।")
        else:
            bot.reply_to(message, "আপনি কোনো ম্যাচ ফলো করছেন না।")

# ---------- হেলথ চেক রুট ----------
@app.route("/")
def home():
    return "Bot is running!"

# ---------- অ্যাপ চালু ----------
if __name__ == "__main__":
    # ব্যাকগ্রাউন্ড ম্যাচ চেকার থ্রেড
    threading.Thread(target=match_checker, daemon=True).start()

    # টেলিগ্রাম বট পোলিং থ্রেড
    def start_polling():
        bot.infinity_polling()
    threading.Thread(target=start_polling, daemon=True).start()

    # ফ্লাস্ক (রেন্ডারের ওয়েব পোর্টে চলবে, হেলথ চেকের জন্য)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
