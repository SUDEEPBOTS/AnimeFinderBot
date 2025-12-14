# bot.py

import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType
from google import genai

from db import (
    add_new_user, get_all_users, find_anime_by_search_term, update_search_synonym,
    add_anime_record, find_anime_by_temp_id, remove_temp_id_prompt, anime_collection
)
from config import (
    ADMIN_START_MESSAGE, USER_WELCOME, USER_NOT_FOUND, USER_BROADCAST_ALERT,
    ADMIN_PROMPT_NAME, ADMIN_PROMPT_LINK, ADMIN_FINAL_INSTRUCTION, 
    ADMIN_SUCCESS, ADMIN_FAIL, GEMINI_SEARCH_PROMPT
)

# --- Environment Variables ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID")) # Channel ID (e.g., -100xxxxxxxxxx)

# --- Clients Initialization ---
app = Client(
    "anime_finder_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

try:
    gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    GEMINI_MODEL = "gemini-2.5-flash" # Fast model for search
    print("✅ Gemini Client initialized.")
except Exception as e:
    print(f"❌ Error initializing Gemini Client: {e}")
    exit()

# --- Global State for Admin Process ---
ADMIN_STATE = {} # {user_id: {"step": "step_name", "data": {}}}

# --- Utility Functions ---

async def auto_delete_message(chat_id: int, message_id: int):
    """Message ko 15 minutes ke baad automatically delete karta hai."""
    DELETE_DELAY = 15 * 60 # 900 seconds
    await asyncio.sleep(DELETE_DELAY)
    try:
        await app.delete_messages(chat_id=chat_id, message_ids=message_id)
    except Exception as e:
        print(f"Could not delete message ID {message_id} in {chat_id}: {e}")


async def broadcast_new_anime(client, anime_name: str, channel_post_id: int):
    """Sabhi users ko naye anime ke baare mein inform karta hai."""
    users_list = get_all_users()
    
    # Message ko forward/copy karke bhejo
    for user_id in users_list:
        try:
            await client.copy_message(
                chat_id=user_id,
                from_chat_id=CHANNEL_ID,
                message_id=channel_post_id,
                caption=USER_BROADCAST_ALERT.format(anime_name=anime_name)
            )
            # Broadcasted message ko delete nahi karna hai, isliye auto_delete nahi lagaya
        except Exception as e:
            # Agar user ne block kiya hai
            print(f"Failed to send broadcast to user {user_id}: {e}")

# --- Handlers ---

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    
    # User ID DB mein save karo
    add_new_user(user_id)
    
    if user_id == ADMIN_ID:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add New Anime", callback_data="add_anime_start")]
        ])
        await message.reply_text(
            ADMIN_START_MESSAGE,
            reply_markup=keyboard
        )
    else:
        await message.reply_text(USER_WELCOME)

# --- 1. ADMIN ADD ANIME FLOW (Callback Query) ---

@app.on_callback_query(filters.regex("^add_anime_start$"))
async def add_anime_start_callback(client, callback_query):
    user_id = callback_query.from_user.id
    
    if user_id != ADMIN_ID:
        await callback_query.answer("Aap Admin nahi hain.", show_alert=True)
        return

    # Step 1: Naam poocho
    ADMIN_STATE[user_id] = {"step": "awaiting_name", "data": {}}
    await callback_query.edit_message_text(ADMIN_PROMPT_NAME)


# --- 2. ADMIN ADD ANIME FLOW (Text Input) ---

@app.on_message(filters.text & filters.private & filters.user(ADMIN_ID))
async def admin_input_handler(client, message):
    user_id = message.from_user.id
    input_text = message.text.strip()
    
    if user_id not in ADMIN_STATE:
        return # Agar admin koi aur command de raha hai
    
    current_state = ADMIN_STATE[user_id]

    if current_state["step"] == "awaiting_name":
        # Step 2: Link poocho aur naam save karo
        current_state["data"]["anime_name"] = input_text
        current_state["step"] = "awaiting_link"
        await message.reply_text(ADMIN_PROMPT_LINK)

    elif current_state["step"] == "awaiting_link":
        # Step 3: Final instructions do aur record DB mein daalo (initial temp record)
        temp_id = f"ANIME-{os.urandom(3).hex().upper()}"
        anime_name = current_state["data"]["anime_name"]
        view_link = input_text
        
        # Temporary record DB mein daalo (channel_post_id abhi missing hai)
        add_anime_record(anime_name, 0, view_link, temp_id)
        
        # Admin ko final instructions do
        final_message = ADMIN_FINAL_INSTRUCTION.format(temp_id=temp_id)
        await message.reply_text(final_message)
        
        # State clear karo
        del ADMIN_STATE[user_id]

# --- 3. CHANNEL MONITORING (Final Step) ---

@app.on_message(filters.chat(CHANNEL_ID) & filters.text)
async def channel_post_monitor(client, message):
    """Channel mein naye posts ko monitor karke DB mein final ID save karta hai."""
    
    # ID ko pipe symbols '|...|' ke beech dhoondho
    import re
    match = re.search(r'\|(ANIME-[A-Z0-9]+)\|', message.text)
    
    if match:
        temp_id = match.group(1)
        post_id = message.id
        
        # Temp ID se record dhoondho
        record = find_anime_by_temp_id(temp_id)
        
        if record:
            # Record update karo (channel_post_id aur temp_id remove karo)
            anime_collection.update_one(
                {"_id": record["_id"]},
                {"$set": {"channel_post_id": post_id}, "$unset": {"temp_id": ""}}
            )
            
            # Admin ko success message bhejo
            await app.send_message(
                chat_id=ADMIN_ID,
                text=ADMIN_SUCCESS.format(anime_name=record['anime_name'])
            )
            
            # Broadcast start karo
            await broadcast_new_anime(client, record['anime_name'], post_id)
        else:
            await app.send_message(
                chat_id=ADMIN_ID,
                text=ADMIN_FAIL.format(temp_id=temp_id)
            )

# --- 4. SMART ANIME SEARCH (Main User Feature) ---

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def anime_search_handler(client, message):
    user_query = message.text.strip()
    
    # 1. DB mein direct search (Agar pehle se save ho)
    found_record = find_anime_by_search_term(user_query)
    
    if found_record:
        print(f"Direct match found for: {user_query}")
        anime_name = found_record['anime_name']
        channel_post_id = found_record['channel_post_id']
    else:
        # 2. Gemini se Fuzzy Search aur Correction
        
        # Saare anime titles ki list lo
        all_anime_names = [doc['anime_name'] for doc in anime_collection.find({})]
        if not all_anime_names:
            await message.reply_text("Database mein koi anime nahi hai. Admin ko bolen add karein.")
            return

        prompt = GEMINI_SEARCH_PROMPT.format(query=user_query, anime_list=", ".join(all_anime_names))
        
        try:
            # Gemini ko search ke liye bhejo
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            
            corrected_name = response.text.strip()
            print(f"Gemini corrected '{user_query}' to: {corrected_name}")
            
            if corrected_name.upper() != "NONE" and corrected_name in all_anime_names:
                # Correction successful! Ab DB se dhoondho
                found_record = find_anime_by_search_term(corrected_name)
                
                # Naye synonym ko DB mein save karo taki agli baar Gemini na use karna pade
                if user_query.lower() != corrected_name.lower():
                    update_search_synonym(corrected_name, user_query)
                    
                anime_name = corrected_name
                channel_post_id = found_record['channel_post_id'] if found_record else None
            else:
                channel_post_id = None # Not found
                
        except Exception as e:
            print(f"Gemini API Error: {e}")
            channel_post_id = None

    # --- FINAL OUTPUT ---
    if channel_post_id:
        try:
            # Link ke liye Inline Button banao
            link_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open Anime Link", url=found_record['view_link'])]
            ])
            
            # Channel Post ko user ko copy karo
            sent_message = await client.copy_message(
                chat_id=message.chat.id,
                from_chat_id=CHANNEL_ID,
                message_id=channel_post_id,
                reply_markup=link_button
            )
            
            # Auto-Delete task start karo
            asyncio.create_task(auto_delete_message(message.chat.id, sent_message.id))

        except Exception as e:
            await message.reply_text(f"Error forwarding post: {e}")
    else:
        await message.reply_text(USER_NOT_FOUND.format(query=user_query))


# --- Bot Run Karna ---
if __name__ == "__main__":
    print("Starting Anime Finder Bot...")
    app.run()
      
