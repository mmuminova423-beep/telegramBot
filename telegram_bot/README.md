# Telegram Request Bot

A Telegram bot built with **aiogram 3** that:

- Accepts documents (passport, certificate, etc.), a full name, and service type from clients
- Forwards the data to a private admin group
- Routes admin replies back to the original client
- Persists message-to-client mappings in SQLite so nothing is lost on restart

---

## Quick Start

### 1. Create the bot

1. Open Telegram and start a chat with **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the token you receive.

### 2. Get your group chat ID

1. Create a private Telegram group and add your bot as an **admin** (it needs permission to read messages and send messages).
2. Add **@username_to_id_bot** to the group — it will print the group's chat ID (a negative number like `-1001234567890`).
3. Remove the helper bot.

### 3. Configure credentials

On **Replit**: add the two secrets in the Secrets tab:

| Key | Value |
|-----|-------|
| `TELEGRAM_BOT_TOKEN` | your token from BotFather |
| `TELEGRAM_GROUP_CHAT_ID` | the negative number from step 2 |

Locally: copy `.env.example` → `.env` and fill in the values.

### 4. Install dependencies

```bash
pip install -r telegram_bot/requirements.txt
```

### 5. Run the bot

```bash
python telegram_bot/bot.py
```

---

## How It Works

### Client flow

1. Client sends `/start` — receives instructions.
2. Client sends their **full name** (text).
3. Client sends the **type of service** (text).
4. Client sends a **document** (file, photo, video, etc.).

Steps 2–4 can be done in any order. Once all three are received the bot:

- Sends a summary message to the admin group (with client ID and name)
- Forwards the document to the admin group
- Confirms receipt to the client

### Admin reply flow

In the private group, **reply to any forwarded message** and the bot will automatically send your reply text back to that client.

Alternatively, use the `/reply` command:

```
/reply <client_id> Your documents are approved!
```

### Database

The SQLite file `requests.db` is created automatically in `telegram_bot/`. It stores the mapping `group_message_id → client_chat_id` so that replies survive bot restarts.

---

## Security

- Bot token is loaded from environment variables / `.env` — never hardcoded.
- Admin commands and reply routing only work inside the configured private group (`TELEGRAM_GROUP_CHAT_ID`).
- `.env` is listed in `.gitignore` and must never be committed.
