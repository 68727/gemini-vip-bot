import os
import io
import sys
import logging
import sqlite3
from datetime import date
from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from keep_alive import keep_alive

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
VIP_CHANNEL_ID_STR = os.getenv("VIP_CHANNEL_ID", "0").strip()
SUBSCRIBE_URL = os.getenv("SUBSCRIBE_URL", "https://t.me/").strip()
DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "0"))

# Bot Creator / Admin IDs with permanent lifetime VIP access
ADMIN_USER_IDS_STR = os.getenv("ADMIN_USER_IDS", "6040715913").strip()
ADMIN_USER_IDS = set()
for uid in ADMIN_USER_IDS_STR.split(","):
    uid = uid.strip()
    if uid.isdigit():
        ADMIN_USER_IDS.add(int(uid))

try:
    VIP_CHANNEL_ID = int(VIP_CHANNEL_ID_STR)
except ValueError:
    VIP_CHANNEL_ID = 0
    logger.warning("VIP_CHANNEL_ID is invalid or not an integer; channel gate disabled.")

# Validate essential credentials
if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_botfather_token_here":
    logger.error("TELEGRAM_BOT_TOKEN is missing or not set in .env! Please update your .env file.")
if not GEMINI_API_KEY or GEMINI_API_KEY == "your_ai_studio_key_here":
    logger.error("GEMINI_API_KEY is missing or not set in .env! Please update your .env file.")

# Initialize the Google GenAI SDK client
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SYSTEM_PROMPT = (
    "You are a friendly, encouraging, and razor-sharp personal tutor and mentor. "
    "Talk naturally like a real human—warm, direct, empathetic, and engaging. "
    "Never use robotic filler phrases like 'As an AI language model' or 'Certainly! Here is your solution'. "
    "Instead, jump straight into the solution with energy and clarity. "
    "When explaining problems: "
    "• Start with an intuitive, friendly 1-sentence breakdown of the core concept. "
    "• Walk through clear, numbered steps with bold headers. "
    "• Present formulas and numbers cleanly so they look great on a phone. "
    "• End with a clear final answer box and a quick '💡 Pro Tip' or memory trick when helpful."
)

DB_PATH = "usage.db"

# --- SQLite Freemium Tracker ---
def init_db():
    """Initialize SQLite database with WAL mode for fast, concurrent access."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id INTEGER,
                usage_date TEXT,
                count INTEGER,
                PRIMARY KEY (user_id, usage_date)
            )
            """
        )
        conn.commit()

def check_usage_available(user_id: int, limit: int) -> tuple[bool, int]:
    """Check if the user has questions remaining today without consuming quota."""
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        )
        row = cur.fetchone()
        current_count = row[0] if row else 0
        return (current_count < limit), current_count

def increment_usage(user_id: int):
    """Increment the usage counter for today once an answer is successfully generated."""
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO daily_usage (user_id, usage_date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, usage_date) DO UPDATE SET count = count + 1
            """,
            (user_id, today),
        )
        conn.commit()

# --- VIP Subscription Check ---
async def is_vip_subscriber(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is the bot creator or an active subscriber in the VIP channel."""
    # Bot creator/admins always have permanent unlimited VIP access
    if user_id in ADMIN_USER_IDS:
        return True

    if VIP_CHANNEL_ID == 0:
        return False
    try:
        member = await context.bot.get_chat_member(
            chat_id=VIP_CHANNEL_ID, user_id=user_id
        )
        # Active statuses: creator/owner, administrator, or regular member
        if member.status in ("creator", "administrator", "member", "owner"):
            return True
        # Restricted users who still retain channel membership
        if member.status == "restricted" and getattr(member, "is_member", False):
            return True
        return False
    except Exception as e:
        logger.debug(f"VIP check exception for user {user_id}: {e}")
        return False

# --- Message Chunking & Zero-Error Delivery Utility ---
async def _safe_reply(update: Update, text: str):
    """Attempt sending with Markdown; if parsing fails due to unescaped tokens, gracefully fallback to plain text."""
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        # Fallback to plain text to guarantee 0 message delivery errors
        await update.message.reply_text(text)

async def send_response_chunked(update: Update, text: str, max_chunk_size: int = 3800):
    """Split long answers cleanly so messages never exceed Telegram's limit, with zero formatting errors."""
    if len(text) <= max_chunk_size:
        await _safe_reply(update, text)
        return

    # Split by paragraphs or double newlines when possible
    paragraphs = text.split("\n\n")
    current_chunk = ""

    for paragraph in paragraphs:
        if len(current_chunk) + len(paragraph) + 2 <= max_chunk_size:
            current_chunk = f"{current_chunk}\n\n{paragraph}" if current_chunk else paragraph
        else:
            if current_chunk:
                await _safe_reply(update, current_chunk)
                current_chunk = ""
            
            # If a single paragraph exceeds the chunk limit, split by lines
            if len(paragraph) > max_chunk_size:
                lines = paragraph.split("\n")
                for line in lines:
                    if len(current_chunk) + len(line) + 1 <= max_chunk_size:
                        current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
                    else:
                        if current_chunk:
                            await _safe_reply(update, current_chunk)
                        current_chunk = line[:max_chunk_size]
            else:
                current_chunk = paragraph

    if current_chunk:
        await _safe_reply(update, current_chunk)

# --- Bot Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in ADMIN_USER_IDS:
        welcome_text = (
            "👑 **Welcome Boss / Creator!**\n\n"
            "You have **Permanent Lifetime VIP Access**.\n"
            "Ask any question, formula, or upload photos with zero restrictions.\n\n"
            "Useful Commands:\n"
            "• /status - Check plan details\n"
            "• /help - Tips for getting answers"
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")
        return

    is_vip = await is_vip_subscriber(user_id, context)
    if is_vip:
        welcome_text = (
            "⭐ **Welcome VIP Member!**\n\n"
            "Your subscription is active. Send me any question as text or snap a photo of a problem!\n\n"
            "• /status - Check subscription\n"
            "• /help - Tips for best solutions"
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")
        return

    if DAILY_FREE_LIMIT > 0:
        welcome_text = (
            "👋 **Welcome to your AI Problem Solver & Tutor!**\n\n"
            "Send me any difficult numerical, conceptual question, or snap a photo of a problem.\n\n"
            f"🔹 **Free Tier:** {DAILY_FREE_LIMIT} questions per day\n"
            "⭐ **VIP Pass:** Unlimited text + photo solutions with zero limits\n\n"
            "Useful Commands:\n"
            "• /status - Check your current plan & quota\n"
            "• /upgrade - Get the VIP Pass\n"
            "• /help - How to get the best answers"
        )
    else:
        welcome_text = (
            "🔒 **Welcome to the VIP Problem Solver & Tutor!**\n\n"
            "This bot is exclusive to **VIP Members**.\n"
            "To unlock unlimited answers, step-by-step problem solutions, and image recognition, "
            "please subscribe using the button below:"
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⭐ Get Unlimited VIP Pass", url=SUBSCRIBE_URL)]]
    )
    await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_USER_IDS:
        await update.message.reply_text(
            "👑 **Plan: Bot Creator / Administrator**\n\n"
            "• Status: Lifetime VIP (Unlimited Access)\n"
            "• Quota: Infinite (Bypasses all paywalls)\n"
            f"• Your Telegram ID: `{user_id}`",
            parse_mode="Markdown",
        )
        return

    if await is_vip_subscriber(user_id, context):
        await update.message.reply_text(
            "⭐ **Current Plan: VIP Member (Unlimited Access)**\n\n"
            "Your subscription is active in our VIP Channel. Ask as many questions as you like!",
            parse_mode="Markdown",
        )
        return

    if DAILY_FREE_LIMIT <= 0:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🚀 Unlock VIP Pass Now", url=SUBSCRIBE_URL)]]
        )
        await update.message.reply_text(
            "🔒 **Plan: Free Tier (Access Locked)**\n\n"
            "This bot is strictly exclusive to VIP members. Please subscribe to unlock instant, unlimited access:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return

    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        )
        row = cur.fetchone()
        used = row[0] if row else 0

    remaining = max(0, DAILY_FREE_LIMIT - used)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⭐ Upgrade to Unlimited VIP", url=SUBSCRIBE_URL)]]
    )
    await update.message.reply_text(
        f"📊 **Plan: Free Tier**\n\n"
        f"• Questions used today: {used}\n"
        f"• Questions remaining: {remaining} of {DAILY_FREE_LIMIT}\n\n"
        "Upgrade to VIP for unlimited solutions anytime:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🚀 Unlock VIP Pass Now", url=SUBSCRIBE_URL)]]
    )
    await update.message.reply_text(
        "⭐ **Unlock VIP Member Access**\n\n"
        "Get instant, unlimited access to:\n"
        "✔ Unlimited step-by-step problem breakdowns\n"
        "✔ Multimodal photo & diagram analysis\n"
        "✔ Ultra-fast answers powered by Gemini 2.5 Flash\n"
        "✔ Zero daily caps\n\n"
        "Tap the button below to join:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "💡 **How to use this bot:**\n\n"
        "1. **Text Questions:** Type out your question or math formula.\n"
        "2. **Photo Problems:** Take a clear, well-lit photo of the textbook page or handwritten problem.\n"
        "3. **Captions:** Add a caption to your photo to specify what to solve (e.g. 'Solve part b only').\n\n"
        "Commands:\n"
        "/status - Check daily quota\n"
        "/upgrade - Join VIP channel\n"
        "/start - Reset greeting"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_user.id
    vip = await is_vip_subscriber(user_id, context)

    # Paywall / VIP gate check
    if not vip:
        allowed, used_count = check_usage_available(user_id, DAILY_FREE_LIMIT)
        if not allowed or DAILY_FREE_LIMIT <= 0:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🚀 Unlock VIP Pass Now", url=SUBSCRIBE_URL)]]
            )
            msg = (
                "🔒 **VIP Subscription Required!**\n\n"
                "This bot is strictly exclusive to VIP members.\n"
                "Please subscribe to unlock instant, unlimited text and photo solutions!"
                if DAILY_FREE_LIMIT <= 0
                else f"🛑 **Daily Free Limit Reached!**\n\n"
                     f"You have used all {DAILY_FREE_LIMIT} free questions for today.\n\n"
                     "Upgrade to VIP to continue asking unlimited questions instantly:"
            )
            await update.message.reply_text(
                msg,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return

    # Visual feedback in chat while AI generates answer
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        contents = []

        if update.message.photo:
            # Highest resolution photo is the last element
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image = Image.open(io.BytesIO(photo_bytes))
            # Normalize to RGB to eliminate format/color-profile errors
            if image.mode != "RGB":
                image = image.convert("RGB")
            # Downscale large mobile photos to max 1280px for 4x faster API transfer and inference
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            caption = (
                update.message.caption
                or "Please solve the problem shown in this image step by step."
            )
            contents = [image, caption]
        elif update.message.text:
            contents = [update.message.text]
        else:
            await update.message.reply_text(
                "Please send a text message or a photo of a problem."
            )
            return

        if not ai_client:
            await update.message.reply_text(
                "⚠️ Bot configuration error: GEMINI_API_KEY is not configured in .env."
            )
            return

        # Query Gemini API asynchronously with auto-fallback for high availability
        models_to_try = list(dict.fromkeys([GEMINI_MODEL, "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"]))
        response = None
        last_error = None

        for model_name in models_to_try:
            try:
                response = await ai_client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.6,
                        max_output_tokens=2048,
                    ),
                )
                if response and response.text:
                    break
            except Exception as model_err:
                logger.warning(f"Model {model_name} failed: {model_err}. Trying fallback...")
                last_error = model_err

        if not response or not response.text:
            if last_error:
                raise last_error
            reply = "I could not generate an explanation. Please try rephrasing."
        else:
            reply = response.text
        
        # Deliver response cleanly without truncation
        await send_response_chunked(update, reply)

        # Only increment quota on successful delivery for non-VIP users
        if not vip:
            increment_usage(user_id)

    except Exception as e:
        logger.error(f"Error processing message for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text(
            f"⚠️ An error occurred while solving your problem:\n`{str(e)[:300]}`\n\n"
            "Your quota was not consumed. Please try again or rephrase.",
            parse_mode="Markdown",
        )

def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_botfather_token_here":
        print("\n[ERROR] TELEGRAM_BOT_TOKEN is not configured.")
        print("Please edit D:\\gemini-vip-bot\\.env and paste your BotFather token.\n")
        sys.exit(1)

    init_db()
    keep_alive()  # Starts lightweight background web listener for Render / UptimeRobot keep-alive
    print("=" * 60)
    print("🤖 Gemini VIP Monetized Telegram Bot is starting...")
    print(f"• Model: {GEMINI_MODEL}")
    print(f"• Daily Free Quota: {DAILY_FREE_LIMIT}")
    print(f"• VIP Channel ID: {VIP_CHANNEL_ID}")
    print("=" * 60)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("upgrade", upgrade_command))
    app.add_handler(CommandHandler("help", help_command))

    # Messages (Text & Photos)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()

