"""Print the chat id of whoever last messaged the bot.

Usage: send any message to your bot in Telegram, then run
    uv run python -m jobalert.tools.get_chat_id
Reads TELEGRAM_BOT_TOKEN from .env. Prints only chat ids and names, never the token.
"""
from __future__ import annotations

from ..config import require_env
from ..notify.telegram import TelegramClient


def main() -> None:
    client = TelegramClient(require_env("TELEGRAM_BOT_TOKEN"), chat_id="", delay_seconds=0)
    updates = client.get_updates()
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
    if not chats:
        print("No messages found. Send any message (e.g. 'hi') to your bot in Telegram, then run this again.")
        return
    for chat_id, name in chats.items():
        print(f"chat id: {chat_id}   ({name})")
    print("\nPut this number in .env as TELEGRAM_CHAT_ID=<number>")


if __name__ == "__main__":
    main()
