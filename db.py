# db.py

import os
from pymongo import MongoClient
from datetime import datetime

# --- Environment Setup (Assumption: Ye variables set hain) ---
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = "AnimeFinderDB"
COLLECTION_NAME = "anime_posts"
USERS_COLLECTION_NAME = "bot_users"

# MongoDB Client setup
try:
    client = MongoClient(MONGO_URI)
    db = client[DATABASE_NAME]
    anime_collection = db[COLLECTION_NAME]
    users_collection = db[USERS_COLLECTION_NAME]
    print("✅ MongoDB connected successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    exit()

# --- Anime Data Management Functions ---

def add_anime_record(anime_name: str, channel_post_id: int, view_link: str, temp_id: str):
    """Naya anime record database mein save karta hai."""
    record = {
        "anime_name": anime_name.strip(),
        "channel_post_id": channel_post_id,
        "view_link": view_link,
        "temp_id": temp_id,
        "search_terms": [anime_name.lower().strip()],
        "created_at": datetime.now()
    }
    anime_collection.insert_one(record)
    return True

def find_anime_by_temp_id(temp_id: str):
    """Temporary ID se record dhoondhta hai (Admin Feature ke liye)."""
    return anime_collection.find_one({"temp_id": temp_id})

def find_anime_by_search_term(term: str):
    """Database mein search term (ya anime name) dhoondhta hai."""
    # Indexing ke liye 'search_terms' list mein check karo
    return anime_collection.find_one({"search_terms": term.lower().strip()})

def update_search_synonym(anime_name: str, synonym: str):
    """Existing anime record mein naya search term add karta hai (Gemini correction ke baad)."""
    anime_collection.update_one(
        {"anime_name": anime_name},
        {"$addToSet": {"search_terms": synonym.lower().strip()}}
    )

# --- User Data Management Functions (Broadcast ke liye) ---

def add_new_user(user_id: int):
    """Naye user ki ID database mein add karta hai."""
    # Sirf private chat users ko hi track karo
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"last_active": datetime.now()}},
        upsert=True
    )

def get_all_users():
    """Saare users ki IDs ki list return karta hai."""
    # Active users ki IDs return karo
    return [doc["_id"] for doc in users_collection.find({}, {"_id": 1})]

def remove_temp_id_prompt(temp_id: str):
    """Admin feature mein jab ID use ho jaye, toh usko remove kar do."""
    # Ya toh delete karo ya 'is_completed' flag set karo
    anime_collection.delete_one({"temp_id": temp_id, "channel_post_id": {"$exists": False}})

