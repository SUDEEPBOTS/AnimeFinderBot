# config.py

# --- ADMIN FEATURE MESSAGES ---
ADMIN_START_MESSAGE = "👋 **Hello Admin!** Aap naye anime add kar sakte hain ya stats check kar sakte hain."
ADMIN_PROMPT_NAME = "✅ Great! Ab naye **Anime ka Poora Naam** enter karein."
ADMIN_PROMPT_LINK = "🔗 Ab is anime ko dekhne ka **Link (URL)** enter karein."
ADMIN_FINAL_INSTRUCTION = (
    "🚀 **Final Step:**\n\n"
    "1. **ID Copy Karo:** `|{temp_id}|`\n"
    "2. Is ID ko apne Channel post ke **Title** (Caption) mein daalo.\n"
    "3. Post ko **Anime Channel** mein bhej do.\n\n"
    "Bot khud channel monitor karke is post ko record kar lega."
)
ADMIN_SUCCESS = "🥳 **Anime Successfully Added!** Record database mein save ho gaya hai. Ab sab users ko broadcast bheja jayega."
ADMIN_FAIL = "❌ **Error!** Process fail ho gaya. Temp ID: `{temp_id}`."

# --- USER MESSAGES ---
USER_WELCOME = (
    "👋 **Welcome!** Main aapka Anime Finder Bot hoon.\n\n"
    "Mujhe bas us **anime ka naam** batao jo aap dhoondh rahe ho. Agar spelling mistake bhi hui toh main samajh jaunga!\n\n"
    "Example: `Naruto`, `One Piece`"
)
USER_NOT_FOUND = "😔 Sorry, mujhe `{query}` naam ka koi anime nahi mila. Kripya naam dobara check karein."
USER_BROADCAST_ALERT = "🥳 **NEW ANIME ALERT!** 🥳\n\nEk fresh anime abhi-abhi channel par upload hua hai: **{anime_name}**!\n\n"

# --- GEMINI PROMPT ---
# Ye prompt Gemini ko search query ko correct karne aur identify karne mein help karega
GEMINI_SEARCH_PROMPT = (
    "A user is searching for an anime. They typed: '{query}'. "
    "Check if this query is a common misspelling, synonym, or a close match for one of the following anime titles: {anime_list}. "
    "Respond only with the **exact correct anime title** from the list that matches the user's intent. "
    "If you cannot find a strong match, respond with the word 'NONE'."
    "\n\nAnime List: {anime_list}"
)
