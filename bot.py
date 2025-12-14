import os
import asyncio
import re
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from google import genai

# --- LOCAL IMPORTS ---
from db import (
    add_new_user, get_all_users, find_anime_by_search_term, update_search_synonym,
    add_anime_record, find_anime_by_temp_id, anime_collection
)
from config import (
    ADMIN_START_MESSAGE, USER_WELCOME, USER_NOT_FOUND, USER_BROADCAST_ALERT,
    ADMIN_PROMPT_NAME, ADMIN_FINAL_INSTRUCTION,
    ADMIN_SUCCESS, ADMIN_FAIL, GEMINI_SEARCH_PROMPT
)

# --- 1. FLASK SERVER ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running fine!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# --- 2. CONFIGURATION ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

app = Client("anime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("✅ Gemini Connected")
except:
    print("❌ Gemini Error")

ADMIN_STATE = {}

# --- 3. HELPER FUNCTIONS ---

async def auto_delete(chat_id, msg_id):
    await asyncio.sleep(900) # 15 min
    try: await app.delete_messages(chat_id, msg_id)
    except: pass

async def broadcast(client, anime_name, msg_id):
    for user_id in get_all_users():
        try:
            await client.copy_message(user_id, CHANNEL_ID, msg_id, caption=f"🔥 New Anime: {anime_name}")
            await asyncio.sleep(0.5)
        except: pass

# --- 4. HANDLERS ---

# ✅ SPAM FIX: filters.incoming added
@app.on_message(filters.command("start") & filters.private & filters.incoming)
async def start(c, m):
    add_new_user(m.from_user.id)
    if m.from_user.id == ADMIN_ID:
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Anime", callback_data="add")]])
        await m.reply(ADMIN_START_MESSAGE, reply_markup=btn)
    else:
        await m.reply(USER_WELCOME)

@app.on_callback_query(filters.regex("^add$"))
async def add_cb(c, q):
    if q.from_user.id == ADMIN_ID:
        ADMIN_STATE[ADMIN_ID] = "name"
        await q.message.edit(ADMIN_PROMPT_NAME)

# ✅ SPAM FIX: filters.incoming added
@app.on_message(filters.text & filters.private & filters.incoming & filters.user(ADMIN_ID))
async def admin_msg(c, m):
    if ADMIN_ID in ADMIN_STATE and ADMIN_STATE[ADMIN_ID] == "name":
        name = m.text
        tid = f"ANIME-{os.urandom(3).hex().upper()}"
        add_anime_record(name, 0, "Post", tid)
        del ADMIN_STATE[ADMIN_ID]
        await m.reply(f"✅ Name: {name}\n\nChannel Post Caption me ye code dalein:\n`|{tid}|`")
    else:
        # Agar admin process me nahi hai, to search handler par jane do
        await m.continue_propagation()

@app.on_message(filters.chat(CHANNEL_ID) & filters.caption)
async def channel_mon(c, m):
    match = re.search(r'\|(ANIME-[A-Z0-9]+)\|', m.caption or "")
    if match:
        tid = match.group(1)
        rec = find_anime_by_temp_id(tid)
        if rec:
            anime_collection.update_one({"_id": rec["_id"]}, {"$set": {"channel_post_id": m.id}, "$unset": {"temp_id": ""}})
            # Remove Code from Caption
            clean_cap = m.caption.replace(match.group(0), "").strip()
            try: await client.edit_message_caption(CHANNEL_ID, m.id, caption=clean_cap)
            except: pass
            await app.send_message(ADMIN_ID, f"✅ Added: {rec['anime_name']}")
            await broadcast(c, rec['anime_name'], m.id)

# ✅ SPAM FIX: filters.incoming added (Ye sabse important hai)
@app.on_message(filters.text & filters.private & filters.incoming & ~filters.command("start"))
async def search(c, m):
    q = m.text.strip()
    rec = find_anime_by_search_term(q)
    if not rec:
        # Gemini Logic
        names = [d['anime_name'] for d in anime_collection.find({})]
        if names:
            try:
                res = gemini_client.models.generate_content(model="gemini-2.0-flash", contents=GEMINI_SEARCH_PROMPT.format(query=q, anime_list=", ".join(names)))
                cor = res.text.strip()
                if cor in names: rec = find_anime_by_search_term(cor)
            except: pass
            
    if rec and rec.get('channel_post_id'):
        msg = await c.copy_message(m.chat.id, CHANNEL_ID, rec['channel_post_id'])
        asyncio.create_task(auto_delete(m.chat.id, msg.id))
    else:
        await m.reply(USER_NOT_FOUND.format(query=q))

if __name__ == "__main__":
    Thread(target=run_web_server).start()
    app.run()
    
