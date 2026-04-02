"""
Telegram Bot for collecting client documents and forwarding them to a private admin group.

Setup:
  1. Create a bot via @BotFather on Telegram → copy the token into .env as TELEGRAM_BOT_TOKEN
  2. Create a private Telegram group and add your bot as an admin.
  3. Get the group's chat_id (it's a negative integer, e.g. -1001234567890).
     Easy way: add @username_to_id_bot to your group temporarily, it will print the id.
     Then set it in .env as TELEGRAM_GROUP_CHAT_ID.
  4. Run: python telegram_bot/bot.py

SQLite database:
  The file `requests.db` is created automatically in the working directory
  from which you run the bot. It stores the mapping between group message IDs
  and client chat IDs so that admin replies are routed back correctly even
  after a bot restart.
"""

import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()  # reads .env from the working directory

# Insert your bot token in .env:  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# Insert your private group chat_id in .env:  TELEGRAM_GROUP_CHAT_ID=-1001234567890
GROUP_CHAT_ID: int = int(os.environ["TELEGRAM_GROUP_CHAT_ID"])

# Path to the SQLite database (persists across restarts)
DB_PATH = Path(__file__).parent / "requests.db"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def db_connect() -> sqlite3.Connection:
    """Open (or create) the SQLite database and ensure the schema exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_map (
            group_message_id INTEGER PRIMARY KEY,
            client_chat_id   INTEGER NOT NULL,
            client_name      TEXT,
            service_type     TEXT
        )
        """
    )
    conn.commit()
    return conn


def save_mapping(
    group_message_id: int,
    client_chat_id: int,
    client_name: str | None,
    service_type: str | None,
) -> None:
    """Store the link between a forwarded group message and the original client."""
    with db_connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO message_map
                (group_message_id, client_chat_id, client_name, service_type)
            VALUES (?, ?, ?, ?)
            """,
            (group_message_id, client_chat_id, client_name, service_type),
        )


def lookup_client(group_message_id: int) -> dict | None:
    """Return client info for a given group message id, or None if not found."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT client_chat_id, client_name, service_type FROM message_map WHERE group_message_id = ?",
            (group_message_id,),
        ).fetchone()
    if row is None:
        return None
    return {"client_chat_id": row[0], "client_name": row[1], "service_type": row[2]}

# ---------------------------------------------------------------------------
# Bot & router setup
# ---------------------------------------------------------------------------

router = Router()

# ---------------------------------------------------------------------------
# /start — greeting for clients
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Welcome message shown to clients when they open the bot."""
    await message.answer(
        "👋 Hello! Please send me:\n\n"
        "1️⃣  Your <b>full name</b>\n"
        "2️⃣  The <b>type of service</b> you need\n"
        "3️⃣  Any <b>document</b> (passport, certificate, etc.) as a file\n\n"
        "You can send these in any order. Once I have everything, your request "
        "will be forwarded to our team and we will get back to you shortly.",
        parse_mode=ParseMode.HTML,
    )

# ---------------------------------------------------------------------------
# Client messages: text → collect name / service type
# ---------------------------------------------------------------------------

# Simple in-memory session store (survives as long as the bot process runs).
# Keys are client chat IDs; values are dicts with optional "name" and "service".
_sessions: dict[int, dict] = {}


def _session(chat_id: int) -> dict:
    """Return (or create) the session dict for a client."""
    if chat_id not in _sessions:
        _sessions[chat_id] = {}
    return _sessions[chat_id]


async def _try_forward_request(bot: Bot, message: Message, session: dict) -> None:
    """
    If we have all three pieces of information (name, service, document) send
    the full request to the admin group and record the mapping.
    """
    name = session.get("name")
    service = session.get("service")
    document = session.get("document_message")  # the original Message object

    if not (name and service and document):
        return  # not ready yet — wait for missing pieces

    client_id = message.chat.id

    # 1. Send a text summary to the group
    summary_text = (
        f"📋 <b>New client request</b>\n\n"
        f"🆔 Client ID: <code>{client_id}</code>\n"
        f"👤 Name: {name}\n"
        f"🛠 Service: {service}"
    )
    summary_msg: Message = await bot.send_message(
        GROUP_CHAT_ID,
        summary_text,
        parse_mode=ParseMode.HTML,
    )

    # 2. Forward the document to the group
    forwarded: Message = await document.forward(GROUP_CHAT_ID)

    # 3. Persist both message IDs → client mapping so replies are routable
    save_mapping(summary_msg.message_id, client_id, name, service)
    save_mapping(forwarded.message_id, client_id, name, service)

    # 4. Log the new request
    logger.info(
        "New request forwarded to group | client_id=%s name=%r service=%r "
        "summary_msg_id=%s doc_msg_id=%s",
        client_id, name, service, summary_msg.message_id, forwarded.message_id,
    )

    # 5. Confirm to the client
    await message.answer(
        "✅ Your request has been received! Our team will review it and get back to you soon."
    )

    # Clear the session so the client can submit a new request later
    _sessions.pop(client_id, None)


@router.message(F.chat.type == "private", F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    """
    Collect name and service type from clients via plain text messages.

    Heuristic: if we don't have a name yet, treat the first text as the name.
    If we already have a name, treat subsequent texts as service type.
    """
    # Ignore commands (they're handled by their own handlers)
    if message.text and message.text.startswith("/"):
        return

    session = _session(message.chat.id)
    text = (message.text or "").strip()

    if not session.get("name"):
        session["name"] = text
        await message.answer(
            f"Got it, <b>{text}</b>! Now please send me the <b>type of service</b> you need.",
            parse_mode=ParseMode.HTML,
        )
    elif not session.get("service"):
        session["service"] = text
        await message.answer(
            "Thanks! Now please send me your <b>document</b> (as a file attachment).",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer(
            "I already have your name and service type. "
            "Please send your <b>document</b> as a file to complete your request.",
            parse_mode=ParseMode.HTML,
        )

    await _try_forward_request(bot, message, session)

# ---------------------------------------------------------------------------
# Client messages: document / photo
# ---------------------------------------------------------------------------

@router.message(F.chat.type == "private", F.document | F.photo | F.video | F.audio)
async def handle_document(message: Message, bot: Bot) -> None:
    """Accept any file type from the client and trigger forwarding when ready."""
    session = _session(message.chat.id)
    session["document_message"] = message

    if not session.get("name"):
        await message.answer(
            "📎 File received! Now please send me your <b>full name</b>.",
            parse_mode=ParseMode.HTML,
        )
    elif not session.get("service"):
        await message.answer(
            "📎 File received! Now please send me the <b>type of service</b> you need.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer("📎 File received! Sending your request to our team…")

    await _try_forward_request(bot, message, session)

# ---------------------------------------------------------------------------
# Admin replies in the group → forwarded back to the client
# ---------------------------------------------------------------------------

@router.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def handle_admin_reply(message: Message, bot: Bot) -> None:
    """
    When an admin replies to any message in the group, look up the original
    client and forward the reply text back to them.

    Access control: only messages from the private group are processed here
    (enforced by `F.chat.id == GROUP_CHAT_ID`).
    """
    replied_to_id = message.reply_to_message.message_id
    client_info = lookup_client(replied_to_id)

    if client_info is None:
        # The replied-to message isn't in our database — ignore silently.
        return

    client_chat_id = client_info["client_chat_id"]
    reply_text = message.text or message.caption or ""

    if not reply_text:
        # Admin replied with only a media file — not handled in this version
        await message.reply(
            "⚠️ Only text replies are forwarded to clients at the moment."
        )
        return

    # Forward the admin's reply to the client
    await bot.send_message(
        client_chat_id,
        f"💬 <b>Reply from our team:</b>\n\n{reply_text}",
        parse_mode=ParseMode.HTML,
    )

    logger.info(
        "Admin reply forwarded to client | client_id=%s replied_msg_id=%s",
        client_chat_id,
        replied_to_id,
    )

# ---------------------------------------------------------------------------
# Optional /reply command for admins
# ---------------------------------------------------------------------------

@router.message(F.chat.id == GROUP_CHAT_ID, Command("reply"))
async def cmd_reply(message: Message, bot: Bot) -> None:
    """
    /reply <client_id> <text>

    Alternative way for admins to send a message to a specific client by ID
    without needing to reply to a group message.
    """
    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await message.reply(
            "Usage: /reply <client_id> <message text>\n"
            "Example: /reply 123456789 Your documents have been approved."
        )
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.reply("❌ Invalid client ID — must be a number.")
        return

    text = args[2]
    try:
        await bot.send_message(
            target_id,
            f"💬 <b>Reply from our team:</b>\n\n{text}",
            parse_mode=ParseMode.HTML,
        )
        await message.reply(f"✅ Message sent to client <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
        logger.info("Admin used /reply to send message to client_id=%s", target_id)
    except Exception as exc:
        await message.reply(f"❌ Failed to send message: {exc}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot starting — polling for updates…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
