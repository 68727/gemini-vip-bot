import os
import io
import re
import sys
import asyncio
import logging
import sqlite3
from urllib.parse import quote
from datetime import date, datetime
from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, helpers
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from keep_alive import keep_alive
from exam_intelligence import (
    EXAM_PATTERNS,
    generate_prediction_prompt,
    generate_mock_test_prompt,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
SUBSCRIBE_URL = os.getenv("SUBSCRIBE_URL", "https://t.me/").strip()

try:
    VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
except ValueError:
    VIP_CHANNEL_ID = 0

try:
    DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "3"))
except ValueError:
    DAILY_FREE_LIMIT = 3

try:
    BONUS_PER_REFERRAL = int(os.getenv("BONUS_PER_REFERRAL", "5"))
except ValueError:
    BONUS_PER_REFERRAL = 5

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
except ValueError:
    ADMIN_USER_ID = 0

# Initialize Google GenAI client
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

DB_PATH = "usage.db"

SYSTEM_PROMPT = """
You are an elite, high-precision tutor and exam paper analyst for Physics, Mathematics, Chemistry, and Computer Science (tailored specifically for Indian board exams like ISC/CBSE and competitive exams like JEE Main & JEE Advanced).

CRITICAL TELEGRAM FORMATTING RULES:
1. PARSE MODE IS HTML: Use ONLY Telegram-supported HTML tags: <b>bold</b>, <i>italic</i>, <code>inline formula/code</code>, and <pre>code block</pre>.
2. NEVER USE Markdown (**bold**, # headers, or triple-backtick fences) and NEVER use unsupported HTML tags.
3. ESCAPE HTML ENTITIES: Whenever you write less-than, greater-than, or ampersand symbols in math or code (outside of the allowed HTML tags), ALWAYS write them as &lt;, &gt;, and &amp;.
4. ZERO LATEX: Telegram cannot render LaTeX. NEVER use $, $$, \\frac, \\sqrt, \\int, or \\cdot.
5. USE UNICODE MATH: Always use clean Unicode symbols for equations:
   - Powers & Subscripts: ⁰ ¹ ² ³ ⁴ ⁵ ⁻ ¹ ₀ ₁ ₂ ₃ (e.g., v² = u² + 2as, R₁ + R₂)
   - Symbols: ×, ÷, ±, √, π, θ, α, β, γ, Δ, ω, λ, μ, ε, ∞, ∫, ∑, →, ⇒, ≈, ≠, ≤, ≥
   - Wrap key formulas in <code>tags</code> so they stand out on mobile screens.
6. MOBILE BREVITY: Keep total output clean and readable on a phone screen. Use blank lines between sections and bullet points (•).

SUBJECT-SPECIFIC RESPONSE BLUEPRINTS:
[IF PHYSICS QUESTION]
• <b>Concept & Formula:</b> State the core physical law and formula inside <code>tags</code>.
• <b>Given Data (SI Units):</b> List all given values and convert non-SI units immediately.
• <b>Step-by-Step Solution:</b> Show clear substitution and algebra line by line.
• <b>Final Answer:</b> Highlight the final value with proper SI units in <b>bold</b>.
• <b>Exam Trap / Shortcut:</b> Add 1 short sentence pointing out a common unit/sign mistake or dimensional shortcut.

[IF MATHEMATICS QUESTION]
• <b>Identity / Approach:</b> Name the theorem, formula, or substitution method used.
• <b>Step-by-Step Working:</b> Number each step (1, 2, 3...) clearly using Unicode symbols.
• <b>Final Result:</b> State the final simplified answer in <b>bold</b>.
• <b>Speed Check:</b> Show a 1-line verification or option-elimination trick where applicable.

[IF COMPUTER SCIENCE / CODING QUESTION]
• <b>Logic / Algorithm:</b> Explain the core approach in 2 to 3 crisp bullet points.
• <b>Clean Code:</b> Provide well-commented code inside <pre>tags</pre> (default to Java unless Python/C++ is specified). Escape &lt;, &gt;, and &amp; inside <pre>.
• <b>Complexity & Dry Run:</b> State Time and Space Complexity and include a compact Variable Description list (Variable | Type | Purpose).

[IF EXAM PREDICTION / TEST PAPER REQUEST]
• Deliver accurate section-wise marks, high-probability questions, and step-wise marking schemes matching the requested exam (CBSE, ISC, or JEE).
"""

# --- SQLite Database Layer ---
def init_db():
    """Initializes the database schema with WAL mode and creates required tables."""
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                referred_by INTEGER,
                bonus_credits INTEGER DEFAULT 0,
                referral_count INTEGER DEFAULT 0,
                joined_at TEXT
            )
            """
        )
        conn.commit()

def register_user_and_referral(new_user_id: int, referrer_id: int | None) -> tuple[bool, int | None]:
    """
    Registers a new user and awards referral bonus if valid.
    Rules:
    - If user already exists, no referral bonus is given.
    - Prevents self-referral (referrer_id != new_user_id).
    - If valid and fresh, awards BONUS_PER_REFERRAL credits to referrer.
    Returns: (bonus_awarded: bool, valid_referrer_id: int | None)
    """
    now_iso = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        cur = conn.cursor()

        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (new_user_id,))
        if cur.fetchone() is not None:
            return False, None

        is_valid_ref = (
            referrer_id is not None
            and referrer_id != new_user_id
            and referrer_id > 0
        )

        if is_valid_ref:
            cur.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO users (user_id, referred_by, bonus_credits, referral_count, joined_at)
                    VALUES (?, NULL, ?, 1, ?)
                    """,
                    (referrer_id, BONUS_PER_REFERRAL, now_iso),
                )
            else:
                cur.execute(
                    """
                    UPDATE users
                    SET bonus_credits = bonus_credits + ?, referral_count = referral_count + 1
                    WHERE user_id = ?
                    """,
                    (BONUS_PER_REFERRAL, referrer_id),
                )

            cur.execute(
                """
                INSERT INTO users (user_id, referred_by, bonus_credits, referral_count, joined_at)
                VALUES (?, ?, 0, 0, ?)
                """,
                (new_user_id, referrer_id, now_iso),
            )
            conn.commit()
            return True, referrer_id
        else:
            cur.execute(
                """
                INSERT INTO users (user_id, referred_by, bonus_credits, referral_count, joined_at)
                VALUES (?, NULL, 0, 0, ?)
                """,
                (new_user_id, now_iso),
            )
            conn.commit()
            return False, None

def get_and_increment_usage(user_id: int, limit: int) -> tuple[bool, str, int]:
    """
    Smart Deduction Order:
    1. Always use the daily free allowance (DAILY_FREE_LIMIT) first.
    2. When today's free count is exhausted, check if bonus_credits > 0 in users.
       If yes, decrement bonus_credits by 1.
    3. If both are exhausted, return (False, "blocked", 0).
    """
    today = date.today().isoformat()
    now_iso = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        cur = conn.cursor()

        cur.execute(
            """
            INSERT OR IGNORE INTO users (user_id, referred_by, bonus_credits, referral_count, joined_at)
            VALUES (?, NULL, 0, 0, ?)
            """,
            (user_id, now_iso),
        )

        cur.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        )
        row = cur.fetchone()
        daily_count = row[0] if row else 0

        if daily_count < limit:
            new_count = daily_count + 1
            cur.execute(
                """
                INSERT INTO daily_usage (user_id, usage_date, count)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, usage_date) DO UPDATE SET count = ?
                """,
                (user_id, today, new_count, new_count),
            )
            conn.commit()
            return True, "daily", max(0, limit - new_count)

        cur.execute("SELECT bonus_credits FROM users WHERE user_id = ?", (user_id,))
        urow = cur.fetchone()
        bonus_credits = urow[0] if urow else 0

        if bonus_credits > 0:
            new_bonus = bonus_credits - 1
            cur.execute(
                "UPDATE users SET bonus_credits = ? WHERE user_id = ?",
                (new_bonus, user_id),
            )
            conn.commit()
            return True, "bonus", new_bonus

        return False, "blocked", 0

def refund_usage(user_id: int):
    """Refunds 1 usage unit if an AI or network error occurs during generation."""
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        )
        row = cur.fetchone()
        if row and row[0] > 0:
            cur.execute(
                "UPDATE daily_usage SET count = count - 1 WHERE user_id = ? AND usage_date = ?",
                (user_id, today),
            )
        else:
            cur.execute(
                "UPDATE users SET bonus_credits = bonus_credits + 1 WHERE user_id = ?",
                (user_id,),
            )
        conn.commit()

def get_user_stats(user_id: int) -> dict:
    """Returns wallet, daily allowance, and referral statistics for a user."""
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        )
        drow = cur.fetchone()
        daily_used = drow[0] if drow else 0

        cur.execute(
            "SELECT bonus_credits, referral_count FROM users WHERE user_id = ?",
            (user_id,),
        )
        urow = cur.fetchone()
        bonus_credits = urow[0] if urow else 0
        referral_count = urow[1] if urow else 0

    daily_left = max(0, DAILY_FREE_LIMIT - daily_used)
    return {
        "daily_used": daily_used,
        "daily_left": daily_left,
        "bonus_credits": bonus_credits,
        "referral_count": referral_count,
        "total_available": daily_left + bonus_credits,
    }

# --- VIP Channel Verification ---
async def is_vip_subscriber(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if a user is an Admin or active subscriber in the VIP Telegram Channel.
    Grants unlimited access without checking or deducting SQLite limits.
    """
    if ADMIN_USER_ID != 0 and user_id == ADMIN_USER_ID:
        return True

    if VIP_CHANNEL_ID == 0:
        return False

    try:
        member = await context.bot.get_chat_member(chat_id=VIP_CHANNEL_ID, user_id=user_id)
        if member.status in ("member", "administrator", "creator", "owner"):
            return True
        if member.status == "restricted" and getattr(member, "is_member", False):
            return True
        return False
    except Exception as e:
        logger.debug(f"VIP check exception for user {user_id}: {e}")
        return False

# --- Helper: Generate Paywall Keyboard ---
def get_paywall_keyboard(user_id: int, bot_username: str) -> InlineKeyboardMarkup:
    """Generates the 3-button paywall inline keyboard."""
    ref_link = helpers.create_deep_linked_url(bot_username, f"ref_{user_id}")
    share_text = "Ace your Physics, Math & CS board/JEE exams with this AI Doubt Solver! Join with my link to get free instant solutions:"
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(share_text)}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ 1. Unlock Unlimited VIP (₹99 UPI)", url=SUBSCRIBE_URL)],
        [InlineKeyboardButton("🎁 2. Invite a Friend (+5 Free Questions)", url=share_url)],
        [InlineKeyboardButton("✅ I Paid & Joined! Verify My VIP Status", callback_data="verify_vip")],
    ])

# --- Helper: Chunked Safe Message Delivery ---
async def send_chunked_html_reply(message_target, text: str, max_chunk: int = 3800):
    """Splits long prediction/mock test answers cleanly and falls back safely if HTML tags are malformed."""
    if len(text) <= max_chunk:
        try:
            await message_target.reply_text(text, parse_mode=ParseMode.HTML)
        except BadRequest:
            plain = re.sub(r"<[^>]+>", "", text)
            await message_target.reply_text(plain)
        return

    paragraphs = text.split("\n\n")
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_chunk:
            current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para
        else:
            if current_chunk:
                try:
                    await message_target.reply_text(current_chunk, parse_mode=ParseMode.HTML)
                except BadRequest:
                    plain = re.sub(r"<[^>]+>", "", current_chunk)
                    await message_target.reply_text(plain)
                current_chunk = ""
            current_chunk = para

    if current_chunk:
        try:
            await message_target.reply_text(current_chunk, parse_mode=ParseMode.HTML)
        except BadRequest:
            plain = re.sub(r"<[^>]+>", "", current_chunk)
            await message_target.reply_text(plain)

# --- Core Gemini Generation Engine with Multi-Model Failover ---
async def generate_gemini_content(contents: list, system_instruction: str) -> str:
    """Invokes Google Gemini with automatic failover across models to prevent 404/503 disruptions."""
    if not ai_client:
        raise ValueError("GEMINI_API_KEY is not configured.")

    models_to_try = list(dict.fromkeys([
        GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]))

    last_error = None
    for model_name in models_to_try:
        try:
            response = await ai_client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                ),
            )
            if response and response.text:
                return response.text
        except Exception as err:
            logger.warning(f"Model {model_name} failed: {err}. Trying fallback...")
            last_error = err

    if last_error:
        raise last_error
    return "I could not generate an answer. Please try again."

# --- Bot Commands ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username or "geminivips_bot"

    # 1. Parse deep-link referral argument (e.g. ref_123456)
    referrer_id = None
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("ref_"):
            clean_ref = arg.replace("ref_", "").strip()
            if clean_ref.isdigit():
                referrer_id = int(clean_ref)

    # 2. Register user & award bonus if new
    awarded, ref_id = register_user_and_referral(user_id, referrer_id)
    if awarded and ref_id:
        try:
            await context.bot.send_message(
                chat_id=ref_id,
                text=f"🎉 <b>Referral Unlocked!</b> A classmate joined using your link. <b>+{BONUS_PER_REFERRAL} Bonus Questions</b> added to your wallet!",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.debug(f"Referral alert could not be delivered to {ref_id}: {e}")

    # 3. Check VIP status
    is_vip = await is_vip_subscriber(user_id, context)
    stats = get_user_stats(user_id)

    if is_vip:
        status_line = "⭐ <b>Plan: VIP Member (Unlimited Access)</b>"
    else:
        status_line = f"📊 <b>Plan: Freemium Tier</b> ({stats['daily_left']} of {DAILY_FREE_LIMIT} free questions left today)"

    ref_link = helpers.create_deep_linked_url(bot_username, f"ref_{user_id}")
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote('Ace your Physics, Math & CS board/JEE exams with this AI Doubt Solver! Join with my link:')}"

    welcome_text = (
        f"👋 <b>Welcome to your AI Exam Doubt Solver & Paper Predictor!</b>\n\n"
        f"{status_line}\n\n"
        f"🎯 <b>What I Can Do:</b>\n"
        f"• <b>Physics, Maths, Chem & CS Doubts:</b> Instant step-by-step solutions with formulas & working.\n"
        f"• <b>Paper Prediction:</b> /predict - 10-year trend predictions & recurring derivations (CBSE, ISC, JEE).\n"
        f"• <b>Mock Paper Generator:</b> /mocktest - Instant 30-min sprint mock tests with marking schemes!\n\n"
        f"🎁 <b>Referral Program:</b>\n"
        f"Share your link via /refer to earn <b>+{BONUS_PER_REFERRAL} permanent questions</b> for every friend who joins!\n\n"
        f"📌 <b>Commands:</b>\n"
        f"• /predict - AI Exam Paper & Topic Predictor\n"
        f"• /mocktest - Generate a 30-min timed practice paper\n"
        f"• /status - Check daily quota & permanent bonus wallet\n"
        f"• /refer - Get your personal referral link\n"
        f"• /help - Full guide & formatting tips\n\n"
        f"<i>Send any question as text or snap a cropped photo to get started!</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔮 Predict Exam Questions", callback_data="menu_predict")],
        [InlineKeyboardButton("📝 Generate Mock Test", callback_data="menu_mocktest")],
        [InlineKeyboardButton("⚡ Unlock Unlimited VIP (₹99)", url=SUBSCRIBE_URL)],
        [InlineKeyboardButton("🎁 Invite Friends (+5 Questions)", url=share_url)],
    ])

    await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username or "geminivips_bot"
    is_vip = await is_vip_subscriber(user_id, context)
    stats = get_user_stats(user_id)

    ref_link = helpers.create_deep_linked_url(bot_username, f"ref_{user_id}")
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote('Ace your Physics, Math & CS board/JEE exams with this AI Doubt Solver! Join with my link:')}"

    if is_vip:
        msg = (
            "⭐ <b>Plan: VIP Member (Unlimited Access)</b>\n\n"
            "• <b>Daily Allowance:</b> Unlimited\n"
            "• <b>Permanent Bonus Wallet:</b> " + str(stats['bonus_credits']) + " questions\n"
            "• <b>Friends Referred:</b> " + str(stats['referral_count']) + "\n\n"
            "<i>Your VIP subscription is active. Ask as many doubts and generate as many papers as you like!</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Invite Friends (+5 Questions)", url=share_url)]
        ])
    else:
        msg = (
            "📊 <b>Plan: Freemium Tier</b>\n\n"
            f"• <b>Daily Free Left Today:</b> <b>{stats['daily_left']} of {DAILY_FREE_LIMIT}</b>\n"
            f"• <b>Permanent Bonus Questions:</b> <b>{stats['bonus_credits']}</b>\n"
            f"• <b>Friends Referred:</b> <b>{stats['referral_count']}</b>\n"
            f"• <b>Total Questions Available Now:</b> <b>{stats['total_available']}</b>\n\n"
            "<i>Need more questions? Invite classmates with /refer or unlock Unlimited VIP!</i>"
        )
        keyboard = get_paywall_keyboard(user_id, bot_username)

    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username or "geminivips_bot"
    stats = get_user_stats(user_id)

    ref_link = helpers.create_deep_linked_url(bot_username, f"ref_{user_id}")
    share_text = "Ace your Physics, Math & CS board/JEE exams with this AI Doubt Solver! Join with my link to get instant step-by-step solutions:"
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(share_text)}"

    msg = (
        "🎁 <b>Invite Classmates & Earn Free Questions!</b>\n\n"
        f"For every classmate who joins using your link, you instantly get <b>+{BONUS_PER_REFERRAL} permanent questions</b> added to your wallet!\n\n"
        f"🔗 <b>Your Personal Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"📊 <b>Your Referral Stats:</b>\n"
        f"• Friends Joined: <b>{stats['referral_count']}</b>\n"
        f"• Permanent Bonus Wallet: <b>{stats['bonus_credits']} questions</b>\n\n"
        "Tap below to share directly with your classmates:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Link with Classmates", url=share_url)]
    ])

    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "💡 <b>Exam Solver & Prediction Guide:</b>\n\n"
        "1. <b>Text Doubts:</b> Type your question clearly. For physics/math, specify what is given and what to find.\n"
        "2. <b>Photo Doubts:</b> Take a well-lit, clearly cropped photo of the problem.\n"
        "3. <b>Question Paper Predictions:</b>\n"
        "• Type <code>/predict physics cbse</code> or <code>/predict maths jee</code> to view top-probability questions & derivations based on 10-year trends.\n"
        "4. <b>Mock Test Generator:</b>\n"
        "• Type <code>/mocktest physics cbse</code> to generate a timed 25-mark sprint paper with marking schemes.\n\n"
        "📚 <b>Supported Exams:</b> CBSE Class 12, ISC Class 12, JEE Main, and JEE Advanced.\n\n"
        "🎟️ <b>Need More Questions?</b>\n"
        "• Send /refer to invite classmates and earn <b>+5 permanent questions</b> each.\n"
        "• Or unlock unlimited access with the ₹99 VIP Pass!"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

# --- Prediction & Mock Paper Generators ---
async def execute_prediction(message_target, user_id: int, context: ContextTypes.DEFAULT_TYPE, subject: str, exam: str, chapter: str | None = None):
    """Executes the AI prediction workflow with quota checking and streaming delivery."""
    vip = await is_vip_subscriber(user_id, context)
    if not vip:
        allowed, source, remaining = get_and_increment_usage(user_id, DAILY_FREE_LIMIT)
        if not allowed:
            bot_username = context.bot.username or "geminivips_bot"
            keyboard = get_paywall_keyboard(user_id, bot_username)
            await message_target.reply_text(
                "🛑 <b>Daily Free Limit & Bonus Credits Exhausted!</b>\n\n"
                f"You have used all {DAILY_FREE_LIMIT} free questions for today.\n\n"
                "Unlock VIP or invite a classmate to access Exam Predictions!",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return

    await context.bot.send_chat_action(chat_id=message_target.chat_id, action=ChatAction.TYPING)
    status_msg = await message_target.reply_text(
        f"🔮 <i>Analyzing 10-year trends and predicting high-probability questions for <b>{exam.upper()} {subject.capitalize()}</b>...</i>",
        parse_mode=ParseMode.HTML,
    )

    try:
        prompt = generate_prediction_prompt(subject, exam, chapter)
        reply = await generate_gemini_content([prompt], SYSTEM_PROMPT)
        await status_msg.delete()
        await send_chunked_html_reply(message_target, reply)
    except Exception as e:
        logger.error(f"Prediction error: {e}", exc_info=True)
        if not vip:
            refund_usage(user_id)
        await status_msg.edit_text(
            f"⚠️ <b>Prediction Failed:</b> Could not generate report right now. Your credit has been refunded.",
            parse_mode=ParseMode.HTML,
        )

async def execute_mocktest(message_target, user_id: int, context: ContextTypes.DEFAULT_TYPE, subject: str, exam: str):
    """Executes the AI Mock Test workflow with quota checking and clean formatting."""
    vip = await is_vip_subscriber(user_id, context)
    if not vip:
        allowed, source, remaining = get_and_increment_usage(user_id, DAILY_FREE_LIMIT)
        if not allowed:
            bot_username = context.bot.username or "geminivips_bot"
            keyboard = get_paywall_keyboard(user_id, bot_username)
            await message_target.reply_text(
                "🛑 <b>Daily Free Limit & Bonus Credits Exhausted!</b>\n\n"
                f"You have used all {DAILY_FREE_LIMIT} free questions for today.\n\n"
                "Unlock VIP or invite a classmate to generate Mock Papers!",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return

    await context.bot.send_chat_action(chat_id=message_target.chat_id, action=ChatAction.TYPING)
    status_msg = await message_target.reply_text(
        f"📝 <i>Designing 30-min Sprint Mock Paper for <b>{exam.upper()} {subject.capitalize()}</b> with answer keys & marking scheme...</i>",
        parse_mode=ParseMode.HTML,
    )

    try:
        prompt = generate_mock_test_prompt(subject, exam, duration_mins=30)
        reply = await generate_gemini_content([prompt], SYSTEM_PROMPT)
        await status_msg.delete()
        await send_chunked_html_reply(message_target, reply)
    except Exception as e:
        logger.error(f"Mock test error: {e}", exc_info=True)
        if not vip:
            refund_usage(user_id)
        await status_msg.edit_text(
            f"⚠️ <b>Mock Test Generation Failed:</b> Could not compile paper right now. Your credit has been refunded.",
            parse_mode=ParseMode.HTML,
        )

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args or len(args) == 0:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📘 CBSE 12 Physics", callback_data="pred_physics_cbse"),
                InlineKeyboardButton("📐 CBSE 12 Maths", callback_data="pred_maths_cbse"),
            ],
            [
                InlineKeyboardButton("🎯 JEE Main Physics", callback_data="pred_physics_jee"),
                InlineKeyboardButton("🧪 JEE Main Chem", callback_data="pred_chemistry_jee"),
            ],
            [
                InlineKeyboardButton("🏛️ ISC 12 Physics", callback_data="pred_physics_isc"),
                InlineKeyboardButton("💻 CBSE/ISC CS", callback_data="pred_cs_cbse"),
            ],
        ])
        await update.message.reply_text(
            "🔮 <b>AI Exam Paper Predictor</b>\n\n"
            "Predict high-probability questions, recurring derivations, and weightage clusters based on 10-year exam trends.\n\n"
            "Select an exam below or type your custom query:\n"
            "• <code>/predict physics cbse</code>\n"
            "• <code>/predict maths jee</code>\n"
            "• <code>/predict chemistry isc</code>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    subject = args[0]
    exam = args[1] if len(args) > 1 else "cbse"
    chapter = " ".join(args[2:]) if len(args) > 2 else None
    await execute_prediction(update.message, user_id, context, subject, exam, chapter)

async def mocktest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    if not args or len(args) == 0:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 CBSE 12 Physics (25M)", callback_data="mock_physics_cbse"),
                InlineKeyboardButton("📝 CBSE 12 Maths (25M)", callback_data="mock_maths_cbse"),
            ],
            [
                InlineKeyboardButton("🎯 JEE Main Physics", callback_data="mock_physics_jee"),
                InlineKeyboardButton("🎯 JEE Main Maths", callback_data="mock_maths_jee"),
            ],
            [
                InlineKeyboardButton("🏛️ ISC 12 Physics (25M)", callback_data="mock_physics_isc"),
            ],
        ])
        await update.message.reply_text(
            "📝 <b>Instant Mock Test Paper Generator</b>\n\n"
            "Generate a realistic 30-minute sprint mock exam paper with full marking scheme & answer guidelines.\n\n"
            "Tap a subject below or type:\n"
            "• <code>/mocktest physics cbse</code>\n"
            "• <code>/mocktest maths jee</code>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    subject = args[0]
    exam = args[1] if len(args) > 1 else "cbse"
    await execute_mocktest(update.message, user_id, context, subject, exam)

# --- Admin Commands ---
async def adminstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ADMIN_USER_ID == 0 or user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ <b>Unauthorized:</b> Admin only command.", parse_mode=ParseMode.HTML)
        return

    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT user_id) FROM daily_usage WHERE usage_date = ?", (today,))
        active_today = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(count), 0) FROM daily_usage WHERE usage_date = ?", (today,))
        questions_today = cur.fetchone()[0]

        cur.execute("SELECT COALESCE(SUM(bonus_credits), 0) FROM users")
        bonus_pool = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
        total_referrals = cur.fetchone()[0]

    stats_msg = (
        "📈 <b>Admin Dashboard & Analytics</b>\n\n"
        f"• <b>Total Registered Users:</b> <code>{total_users}</code>\n"
        f"• <b>Active Users Today:</b> <code>{active_today}</code>\n"
        f"• <b>Questions Solved Today:</b> <code>{questions_today}</code>\n"
        f"• <b>Total Successful Referrals:</b> <code>{total_referrals}</code>\n"
        f"• <b>Total Bonus Credits in Circulation:</b> <code>{bonus_pool}</code>\n"
    )
    await update.message.reply_text(stats_msg, parse_mode=ParseMode.HTML)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ADMIN_USER_ID == 0 or user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ <b>Unauthorized:</b> Admin only command.", parse_mode=ParseMode.HTML)
        return

    parts = update.message.text.split("/broadcast", 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "⚠️ <b>Usage:</b> <code>/broadcast Your message text here</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    broadcast_text = parts[1].strip()

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        user_rows = cur.fetchall()

    target_ids = [row[0] for row in user_rows]
    total_targets = len(target_ids)

    status_msg = await update.message.reply_text(
        f"🚀 <b>Starting broadcast to {total_targets} users...</b>",
        parse_mode=ParseMode.HTML,
    )

    success_count = 0
    fail_count = 0

    for uid in target_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=broadcast_text,
                parse_mode=ParseMode.HTML,
            )
            success_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• <b>Delivered:</b> {success_count}\n"
        f"• <b>Failed / Blocked:</b> {fail_count}\n"
        f"• <b>Total Targets:</b> {total_targets}",
        parse_mode=ParseMode.HTML,
    )

# --- Callback Query Router ---
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    bot_username = context.bot.username or "geminivips_bot"

    if data == "verify_vip":
        is_vip = await is_vip_subscriber(user_id, context)
        if is_vip:
            await query.edit_message_text(
                "🎉 <b>VIP Status Verified!</b>\n\n"
                "Welcome to the VIP Club! Your unlimited pass is now active with zero limits. "
                "Send any Physics, Math, or Computer Science question to begin!",
                parse_mode=ParseMode.HTML,
            )
        else:
            keyboard = get_paywall_keyboard(user_id, bot_username)
            await query.edit_message_text(
                "⚠️ <b>VIP Membership Not Found Yet</b>\n\n"
                "We could not find you in our VIP Telegram channel.\n\n"
                "1. Complete the ₹99 UPI payment.\n"
                "2. Join the private VIP channel.\n"
                "3. Tap <b>Verify My VIP Status</b> below:",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
    elif data == "menu_predict":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📘 CBSE 12 Physics", callback_data="pred_physics_cbse"),
                InlineKeyboardButton("📐 CBSE 12 Maths", callback_data="pred_maths_cbse"),
            ],
            [
                InlineKeyboardButton("🎯 JEE Main Physics", callback_data="pred_physics_jee"),
                InlineKeyboardButton("🧪 JEE Main Chem", callback_data="pred_chemistry_jee"),
            ],
            [
                InlineKeyboardButton("🏛️ ISC 12 Physics", callback_data="pred_physics_isc"),
            ],
        ])
        await query.message.reply_text(
            "🔮 <b>Select an Exam to Predict Questions:</b>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    elif data == "menu_mocktest":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 CBSE 12 Physics (25M)", callback_data="mock_physics_cbse"),
                InlineKeyboardButton("📝 CBSE 12 Maths (25M)", callback_data="mock_maths_cbse"),
            ],
            [
                InlineKeyboardButton("🎯 JEE Main Physics", callback_data="mock_physics_jee"),
                InlineKeyboardButton("🎯 JEE Main Maths", callback_data="mock_maths_jee"),
            ],
        ])
        await query.message.reply_text(
            "📝 <b>Select a Subject for Timed Mock Test:</b>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    elif data.startswith("pred_"):
        parts = data.split("_")
        subject = parts[1]
        exam = parts[2]
        await execute_prediction(query.message, user_id, context, subject, exam)
    elif data.startswith("mock_"):
        parts = data.split("_")
        subject = parts[1]
        exam = parts[2]
        await execute_mocktest(query.message, user_id, context, subject, exam)

# --- Question & Photo Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_user.id
    bot_username = context.bot.username or "geminivips_bot"
    vip = await is_vip_subscriber(user_id, context)

    # Freemium quota check if not VIP
    if not vip:
        allowed, source, remaining = get_and_increment_usage(user_id, DAILY_FREE_LIMIT)
        if not allowed:
            keyboard = get_paywall_keyboard(user_id, bot_username)
            await update.message.reply_text(
                "🛑 <b>Daily Free Limit & Bonus Credits Exhausted!</b>\n\n"
                f"You have used all {DAILY_FREE_LIMIT} free questions for today and have 0 referral bonus credits.\n\n"
                "Choose an option below to continue solving doubts immediately:",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return

    # Visual feedback in chat while AI generates answer
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        contents = []

        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image = Image.open(io.BytesIO(photo_bytes))
            if image.mode != "RGB":
                image = image.convert("RGB")
            # Downscale large mobile camera captures to 1280px max for speed
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            caption = (
                update.message.caption
                or "Please solve this exam problem step by step with clear formulas, working, and final answer."
            )
            contents = [image, caption]
        elif update.message.text:
            text = update.message.text.strip()
            # Check if student is asking for a prediction or mock test in natural language
            lower_text = text.lower()
            if any(k in lower_text for k in ["predict", "prediction", "paper pattern", "expected questions", "important derivations"]):
                exam = "jee" if "jee" in lower_text else ("isc" if "isc" in lower_text else "cbse")
                subject = "maths" if ("math" in lower_text) else ("chemistry" if "chem" in lower_text else ("computer" if "cs" in lower_text or "computer" in lower_text else "physics"))
                prompt = generate_prediction_prompt(subject, exam)
                contents = [prompt]
            elif any(k in lower_text for k in ["mock test", "mock paper", "sample paper", "practice test", "question paper"]):
                exam = "jee" if "jee" in lower_text else ("isc" if "isc" in lower_text else "cbse")
                subject = "maths" if ("math" in lower_text) else ("chemistry" if "chem" in lower_text else ("computer" if "cs" in lower_text or "computer" in lower_text else "physics"))
                prompt = generate_mock_test_prompt(subject, exam, duration_mins=30)
                contents = [prompt]
            else:
                contents = [text]
        else:
            await update.message.reply_text("Please send your question as text or as a photo.")
            return

        reply = await generate_gemini_content(contents, SYSTEM_PROMPT)
        await send_chunked_html_reply(update.message, reply)

    except Exception as e:
        logger.error(f"Error processing question for user {user_id}: {e}", exc_info=True)
        if not vip:
            refund_usage(user_id)
        await update.message.reply_text(
            "⚠️ <b>Temporary Error:</b> Could not process your doubt right now.\n"
            "Your question credit has been refunded. Please try again in a few moments.",
            parse_mode=ParseMode.HTML,
        )

def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        print("\n[ERROR] TELEGRAM_BOT_TOKEN is not configured in .env.")
        sys.exit(1)

    init_db()
    keep_alive()  # Starts background keep-alive web server for Render 24/7 uptime

    print("=" * 60)
    print("🤖 Freemium + VIP Exam Doubt Solver & Predictor is starting...")
    print(f"• Model: {GEMINI_MODEL}")
    print(f"• Daily Free Quota: {DAILY_FREE_LIMIT}")
    print(f"• Bonus Per Referral: {BONUS_PER_REFERRAL}")
    print(f"• VIP Channel ID: {VIP_CHANNEL_ID}")
    print(f"• Admin User ID: {ADMIN_USER_ID}")
    print("=" * 60)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("refer", refer_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("mocktest", mocktest_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("adminstats", adminstats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))

    # Unified callback query router (VIP verify, predictions, mock tests)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Message handlers (Text & Photo)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
