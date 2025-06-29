import os, time, threading, ffmpeg
from flask import Flask, jsonify
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from dotenv import load_dotenv

# Load ENV
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))  # Replace with your Telegram ID

# MongoDB
mongo = MongoClient(MONGO_URI)
db = mongo.get_database()
users = db.users

# Flask
app = Flask(__name__)
@app.route("/healthz")
def health(): return jsonify({"status": "ok", "msg": "Bot running"})

# Bot
bot = Client("mergebot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_videos = {}  # temp store

def get_user(uid):
    user = users.find_one({"_id": uid})
    if not user:
        users.insert_one({"_id": uid, "premium": False})
        return {"_id": uid, "premium": False}
    return user

def set_premium(uid, value=True):
    users.update_one({"_id": uid}, {"$set": {"premium": value}}, upsert=True)

def get_all_users(): return users.find()

def get_bar(current, total):
    pct = current * 100 / total
    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    return f"[{bar}] {pct:.2f}%"

@bot.on_message(filters.command("start"))
async def start(_, m: Message):
    get_user(m.from_user.id)
    await m.reply_text(
        "👋 Send 2+ videos to merge.\nFree: 2 videos\nPremium: 10 videos\nUse /merge when ready.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade", callback_data="upgrade")]])
    )

@bot.on_callback_query(filters.regex("upgrade"))
async def upgrade_cb(_, q): await q.answer("Contact admin to upgrade 💎", show_alert=True)

@bot.on_message(filters.video)
async def collect(_, m: Message):
    uid = str(m.from_user.id)
    user = get_user(int(uid))
    limit = 10 if user["premium"] else 2
    user_videos.setdefault(uid, [])
    if len(user_videos[uid]) >= limit:
        return await m.reply("🚫 Limit reached. Upgrade for more.")
    user_videos[uid].append(m)
    await m.reply(f"✅ Added ({len(user_videos[uid])}/{limit})")

@bot.on_message(filters.command("merge"))
async def merge_cmd(c: Client, m: Message):
    uid = str(m.from_user.id)
    vids = user_videos.get(uid)
    if not vids or len(vids) < 2: return await m.reply("❌ Need at least 2 videos.")
    msg = await m.reply("📥 Downloading...")

    paths = []
    for i, v in enumerate(vids):
        raw = await c.download_media(v, file_name=f"{uid}_{i}")
        base, ext = os.path.splitext(raw)
        mp4 = f"{base}.mp4"
        if ext.lower() != ".mp4":
            await msg.edit(f"🔄 Converting {os.path.basename(raw)}")
            ffmpeg.input(raw).output(mp4, vcodec='libx264', acodec='aac').run(overwrite_output=True)
            os.remove(raw)
        else:
            mp4 = raw
        paths.append(mp4)
        await msg.edit(f"✅ Ready: {i+1}/{len(vids)}")

    await msg.edit("🔗 Merging...")
    with open("input.txt", "w") as f:
        for p in paths: f.write(f"file '{p}'\n")

    out_file = f"{uid}_merged.mp4"
    ffmpeg.input("input.txt", format="concat", safe=0).output(out_file, c="copy").run(overwrite_output=True)
    os.remove("input.txt")

    await msg.edit("📤 Uploading...")
    start = time.time()
    await c.send_video(m.chat.id, out_file, caption="✅ Merged!",
        progress=progress_cb, progress_args=(msg, start))
    
    for f in paths: os.remove(f)
    os.remove(out_file)
    user_videos.pop(uid)

async def progress_cb(current, total, msg, start):
    bar = get_bar(current, total)
    speed = current / (time.time() - start + 1)
    eta = (total - current) / speed
    try:
        await msg.edit_text(f"📤 Uploading...\n{bar}\nSpeed: {speed/1024:.2f} KB/s\nETA: {int(eta)}s")
    except: pass

# Admin Commands
@bot.on_message(filters.command("promote") & filters.user(ADMIN_ID))
async def promote(_, m): 
    if len(m.command) < 2: return await m.reply("Usage: /promote <user_id>")
    set_premium(int(m.command[1]), True)
    await m.reply("✅ Promoted")

@bot.on_message(filters.command("demote") & filters.user(ADMIN_ID))
async def demote(_, m): 
    if len(m.command) < 2: return await m.reply("Usage: /demote <user_id>")
    set_premium(int(m.command[1]), False)
    await m.reply("🚫 Demoted")

@bot.on_message(filters.command("broadcast") & filters.user(ADMIN_ID))
async def bc(c, m):
    if len(m.command) < 2: return await m.reply("Usage: /broadcast <text>")
    txt, count = m.text.split(None, 1)[1], 0
    for u in get_all_users():
        try: await c.send_message(u["_id"], txt); count += 1
        except: pass
    await m.reply(f"✅ Broadcast sent to {count} users.")

# Run Flask + Bot
def run_flask(): app.run("0.0.0.0", 8000)
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run()
