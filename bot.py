import os
import asyncio
import re
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from google import genai

# --- LOCAL IMPORTS (Aapki db.py aur config.py files se) ---
# Ensure ki db.py aur config.py usi folder mein hain
from db import (
    add_new_user, get_all_users, find_anime_by_search_term, update_search_synonym,
    add_anime_record, find_anime_by_temp_id, anime_collection
)
from config import (
    ADMIN_START_MESSAGE, USER_WELCOME, USER_NOT_FOUND, USER_BROADCAST_ALERT,
    ADMIN_PROMPT_NAME, ADMIN_PROMPT_LINK, ADMIN_FINAL_INSTRUCTION,
    ADMIN_SUCCESS, ADMIN_FAIL, GEMINI_SEARCH_PROMPT
)

# --- 1. FLASK WEB SERVER (Render "No Port" Error Fix) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running perfectly!"

def run_web_server():
    # Render environment se PORT leta hai, default 8080
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# --- 2. CONFIGURATION & CLIENTS ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Pyrogram Client
app = Client(
    "anime_finder_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Gemini Client
try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    GEMINI_MODEL = "gemini-2.0-flash" # Fast and smart model
    print("✅ Gemini Client initialized.")
except Exception as e:
    print(f"❌ Gemini Error: {e}")

# Admin State (Kaunsa admin abhi kya kar raha hai)
ADMIN_STATE = {}

# --- 3. HELPER FUNCTIONS ---

async def auto_delete_message(chat_id: int, message_id: int):
    """Message ko 15 minutes (900 seconds) ke baad delete karta hai."""
    await asyncio.sleep(15 * 60)
    try:
        await app.delete_messages(chat_id, message_id)
    except Exception:
        pass # Agar message pehle hi delete ho gaya ho

async def broadcast_new_anime(client, anime_name: str, channel_post_id: int):
    """Naya anime aane par sabhi users ko notify karta hai."""
    users = get_all_users()
    count = 0
    for user_id in users:
        try:
            # Channel post ko copy karke user ko bhejo
            await client.copy_message(
                chat_id=user_id,
                from_chat_id=CHANNEL_ID,
                message_id=channel_post_id,
                caption=USER_BROADCAST_ALERT.format(anime_name=anime_name)
            )
            count += 1
            await asyncio.sleep(0.5) # Flood wait se bachne ke liye
        except Exception:
            pass # User ne bot block kiya ho sakta hai
    print(f"Broadcast sent to {count} users.")

# --- 4. BOT HANDLERS ---

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    add_new_user(user_id) # User ko DB mein save karo
    
    if user_id == ADMIN_ID:
        # Admin Panel Button
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add New Anime", callback_data="add_anime_start")]
        ])
        await message.reply_text(ADMIN_START_MESSAGE, reply_markup=keyboard)
    else:
        # User Welcome
        await message.reply_text(USER_WELCOME)

# --- ADMIN FLOW: Add Anime ---

# Step 1: Button Click
@app.on_callback_query(filters.regex("^add_anime_start$"))
async def add_anime_start_callback(client, callback_query):
    if callback_query.from_user.id != ADMIN_ID: return
    
    ADMIN_STATE[ADMIN_ID] = {"step": "awaiting_name", "data": {}}
    await callback_query.edit_message_text(ADMIN_PROMPT_NAME)

# Step 2 & 3: Input Handling
@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID))
async def admin_input_handler(client, message):
    # Agar Admin "Add Anime" process mein nahi hai, toh message ko Search Handler ke paas bhej do
    if message.from_user.id not in ADMIN_STATE:
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
        
        # Temp ID generate karo
        temp_id = f"ANIME-{os.urandom(3).hex().upper()}"
        
        # Initial record DB mein save karo
        add_anime_record(anime_name, 0, view_link, temp_id)
        
        # Process complete, state clear karo
        del ADMIN_STATE[ADMIN_ID]
        
        # Admin ko final instruction do
        await message.reply_text(ADMIN_FINAL_INSTRUCTION.format(temp_id=temp_id))

# --- CHANNEL MONITOR (Finalizing Upload) ---

@app.on_message(filters.chat(CHANNEL_ID) & filters.text)
async def channel_post_monitor(client, message):
    """Channel post ke title mein Temp ID dhoondhta hai."""
    # Regex se ID nikalo: |ANIME-XXXXXX|
    match = re.search(r'\|(ANIME-[A-Z0-9]+)\|', message.text)
    
    if match:
        temp_id = match.group(1)
        record = find_anime_by_temp_id(temp_id)
        
        if record:
            # DB update karo: Temp ID hatao, Real Post ID lagao
            anime_collection.update_one(
                {"_id": record["_id"]},
                {"$set": {"channel_post_id": message.id}, "$unset": {"temp_id": ""}}
            )
            
            # Admin ko confirm karo
            await app.send_message(ADMIN_ID, ADMIN_SUCCESS.format(anime_name=record['anime_name']))
            
            # Users ko Broadcast karo
            await broadcast_new_anime(client, record['anime_name'], message.id)
        else:
            await app.send_message(ADMIN_ID, ADMIN_FAIL.format(temp_id=temp_id))

# --- USER SEARCH FLOW (Gemini + DB) ---

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def anime_search_handler(client, message):
    query = message.text.strip()
    
    # 1. Direct DB Search
    record = find_anime_by_search_term(query)
    
    if not record:
        # 2. Gemini Fuzzy Search (Agar direct na mile)
        all_anime_names = [doc['anime_name'] for doc in anime_collection.find({})]
        
        if all_anime_names:
            try:
                # Gemini prompt banavo
                prompt = GEMINI_SEARCH_PROMPT.format(query=query, anime_list=", ".join(all_anime_names))
                response = gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                corrected_name = response.text.strip()
                
                # Agar Gemini ne koi valid naam diya
                if corrected_name != "NONE" and corrected_name in all_anime_names:
                    record = find_anime_by_search_term(corrected_name)
                    
                    # Synonym save karo taaki agli baar fast ho
                    if query.lower() != corrected_name.lower():
                        update_search_synonym(corrected_name, query)
            except Exception as e:
                print(f"Gemini API Error: {e}")

    # Result bhejo
    if record and record.get('channel_post_id'):
        try:
            # Open Button
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open Anime Link", url=record['view_link'])]
            ])
            
            # Message Copy karo
            sent_msg = await client.copy_message(
                chat_id=message.chat.id,
                from_chat_id=CHANNEL_ID,
                message_id=record['channel_post_id'],
                reply_markup=btn
            )
            
            # Auto-Delete Schedule karo
            asyncio.create_task(auto_delete_message(message.chat.id, sent_msg.id))
            
        except Exception as e:
            await message.reply_text(f"Error: {e}")
    else:
        await message.reply_text(USER_NOT_FOUND.format(query=query))

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # 1. Flask Server ko alag thread mein start karo
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()
    
    # 2. Bot start karo
    print("🚀 Anime Finder Bot Started with Web Server...")
    app.run()
        
