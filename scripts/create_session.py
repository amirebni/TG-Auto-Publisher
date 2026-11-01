"""Create a Telegram StringSession. Run once on your own computer.

    pip install -r requirements.txt
    python scripts/create_session.py

The printed string is TG_SESSION. It gives FULL access to the account:
keep it only in GitHub Secrets, never in code or a public repo.
Prefer a dedicated secondary account over your main one.
"""
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(input("Telegram API ID: ").strip())
api_hash = input("Telegram API Hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nLogin successful.")
    print("\nYour TG_SESSION (keep it secret):\n")
    print(client.session.save())
    print("\nNever publish this string.")
