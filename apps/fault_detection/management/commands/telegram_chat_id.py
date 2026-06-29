"""
Find your Telegram chat ID.

Steps:
  1. Create a bot via @BotFather and put the token in TELEGRAM_BOT_TOKEN (.env).
  2. Open a chat with your bot (or add it to a group) and send it any message
     (e.g. "hello"). For groups, you may need to send a message that mentions
     the bot, or make it admin.
  3. Run: python manage.py telegram_chat_id
     It calls getUpdates and prints the chat IDs it can see. Put the right one
     in TELEGRAM_CHAT_ID (.env).
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Print Telegram chat IDs the bot can currently see (via getUpdates)."

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in your .env.")

        url = f"https://api.telegram.org/bot{token}/getUpdates"
        try:
            resp = requests.get(url, timeout=getattr(settings, 'HA_REQUEST_TIMEOUT', 15))
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Failed to call Telegram getUpdates: {exc}")

        data = resp.json()
        if not data.get('ok'):
            raise CommandError(f"Telegram API error: {data}")

        updates = data.get('result', [])
        if not updates:
            self.stdout.write(self.style.WARNING(
                "No updates yet. Send your bot a message first (open the chat and "
                "type 'hello'), then run this again.\n"
                "Note: getUpdates only returns recent messages and won't work if a "
                "webhook is set."
            ))
            return

        seen = {}
        for upd in updates:
            msg = upd.get('message') or upd.get('channel_post') or {}
            chat = msg.get('chat') or {}
            chat_id = chat.get('id')
            if chat_id is not None and chat_id not in seen:
                title = chat.get('title') or chat.get('username') or chat.get('first_name') or ''
                seen[chat_id] = f"{chat.get('type', '?')}  {title}".strip()

        if not seen:
            self.stdout.write(self.style.WARNING("Updates found, but no chat IDs could be read."))
            return

        self.stdout.write(self.style.SUCCESS("Chat IDs the bot can see:\n"))
        for chat_id, desc in seen.items():
            self.stdout.write(f"  TELEGRAM_CHAT_ID = {chat_id}   ({desc})")
