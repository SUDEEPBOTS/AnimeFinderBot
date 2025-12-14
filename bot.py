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
    ADMIN_PROMPT_NAME, ADMIN_PROMPT_LINK, ADMIN_FINAL_INSTRUCTION,
    ADMIN_SUCCESS, ADMIN_FAIL, GEMINI_SEARCH_PROMPT
)

# --- 1. FLASK WEB SERVER (Render Fix) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running perfectly!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# --- 2. CONFIGURATION & CLIENTS ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

app = Client(
    "anime_finder_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_MODEL = "gemini-2.0-flash"
    print("✅ Gemini Client initialized.")
except Exception as e:
    print(f"❌ Gemini Error: {e}")

ADMIN_STATE = {}

# --- 3. HELPER FUNCTIONS ---

async def auto_delete_message(chat_id: int, message_id: int):
    """Message ko 15 minutes ke baad delete karta hai."""
    await asyncio.sleep(15 * 60)
    try:
        await app.delete_messages(chat_id, message_id)
    except Exception:
        pass

async def broadcast_new_anime(client, anime_name: str, channel_post_id: int):
    users = get_all_users()
    count = 0
    for user_id in users:
        try:
            await client.copy_message(
                chat_id=user_id,
                from_chat_id=CHANNEL_ID,
                message_id=channel_post_id,
                caption=USER_BROADCAST_ALERT.format(anime_name=anime_name)
            )
            count += 1
            await asyncio.sleep(0.5)
        except Exception:
            pass
    print(f"Broadcast sent to {count} users.")

# --- 4. BOT HANDLERS ---

# Fixed: Added 'filters.incoming' to stop self-reply
@app.on_message(filters.command("start") & filters.private & filters.incoming)
async def start_command(client, message):
    user_id = message.from_user.id
    add_new_user(user_id)
    
    if user_id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add New Anime", callback_data="add_anime_start")]
        ])
        await message.reply_text(ADMIN_START_MESSAGE, reply_markup=keyboard)
    else:
        await message.reply_text(USER_WELCOME)

# --- ADMIN FLOW ---

@app.on_callback_query(filters.regex("^add_anime_start$"))
async def add_anime_start_callback(client, callback_query):
    if callback_query.from_user.id != ADMIN_ID: return
    
    ADMIN_STATE[ADMIN_ID] = {"step": "awaiting_name", "data": {}}
    await callback_query.edit_message_text(ADMIN_PROMPT_NAME)

# Fixed: Added 'filters.incoming'
@app.on_message(filters.text & filters.private & filters.incoming & filters.user(ADMIN_ID))
async def admin_input_handler(client, message):
    if message.from_user.id not in ADMIN_STATE:
        # Agar admin flow mein nahi hai, toh search handler par jaane do
        return await message.continue_propagation()

    state = ADMIN_STATE[ADMIN_ID]
    text = message.text.strip()

    if state["step"] == "awaiting_name":
        state["data"]["anime_name"] = text
        state["step"] = "awaiting_link"
        await message.reply_text(ADMIN_PROMPT_LINK)
        
    elif state["step"] == "awaiting_link":
        anime_name = state["data"]["anime_name"]
        view_link = text
        temp_id = f"ANIME-{os.urandom(3).hex().upper()}"
        
        add_anime_record(anime_name, 0, view_link, temp_id)
        del ADMIN_STATE[ADMIN_ID]
        
        await message.reply_text(ADMIN_FINAL_INSTRUCTION.format(temp_id=temp_id))

# --- CHANNEL MONITOR ---

@app.on_message(filters.chat(CHANNEL_ID) & filters.text)
async def channel_post_monitor(client, message):
    match = re.search(r'\|(ANIME-[A-Z0-9]+)\|', message.text)
    
    if match:
        temp_id = match.group(1)
        record = find_anime_by_temp_id(temp_id)
        
        if record:
            anime_collection.update_one(
                {"_id": record["_id"]},
                {"$set": {"channel_post_id": message.id}, "$unset": {"temp_id": ""}}
            )
            await app.send_message(ADMIN_ID, ADMIN_SUCCESS.format(anime_name=record['anime_name']))
            await broadcast_new_anime(client, record['anime_name'], message.id)
        else:
            await app.send_message(ADMIN_ID, ADMIN_FAIL.format(temp_id=temp_id))

# --- USER SEARCH FLOW (Spam Fix Here) ---

# Fixed: Added 'filters.incoming' - Ye loop ko rokega!
@app.on_message(filters.text & filters.private & filters.incoming & ~filters.command("start"))
async def anime_search_handler(client, message):
    query = message.text.strip()
    
    # 1. Direct DB Search
    record = find_anime_by_search_term(query)
    
    if not record:
        # 2. Gemini Fuzzy Search
        all_anime_names = [doc['anime_name'] for doc in anime_collection.find({})]
        
        if all_anime_names:
            try:
                prompt = GEMINI_SEARCH_PROMPT.format(query=query, anime_list=", ".join(all_anime_names))
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                corrected_name = response.text.strip()
                
                if corrected_name != "NONE" and corrected_name in all_anime_names:
                    record = find_anime_by_search_term(corrected_name)
                    if query.lower() != corrected_name.lower():
                        update_search_synonym(corrected_name, query)
            except Exception as e:
                print(f"Gemini API Error: {e}")

    # Output
    if record and record.get('channel_post_id'):
        try:
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open Anime Link", url=record['view_link'])]
            ])
            sent_msg = await client.copy_message(
                chat_id=message.chat.id,
                from_chat_id=CHANNEL_ID,
                message_id=record['channel_post_id'],
                reply_markup=btn
            )
            asyncio.create_task(auto_delete_message(message.chat.id, sent_msg.id))
        except Exception as e:
            await message.reply_text(f"Error: {e}")
    else:
        await message.reply_text(USER_NOT_FOUND.format(query=query))

# --- MAIN ---
if __name__ == "__main__":
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()
    print("🚀 Anime Finder Bot Started (Spam Fixed)...")
    app.run()
    
