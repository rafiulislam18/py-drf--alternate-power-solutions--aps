"""
Find your Telegram chat ID.

Steps:
  1. Create a bot via @BotFather and put the token in TELEGRAM_BOT_TOKEN (.env),
     or TELEGRAM_LOG_BOT_TOKEN for the dedicated logs bot.
  2. Add the bot to the chat/group and send it any message (e.g. "hello"). For
     groups, turn OFF the bot's Group Privacy in @BotFather (or make it admin) so
     it can see the message.
  3. Run one of:
       python manage.py telegram_chat_id             # uses TELEGRAM_BOT_TOKEN
       python manage.py telegram_chat_id --logs       # uses TELEGRAM_LOG_BOT_TOKEN
       python manage.py telegram_chat_id --token XXX   # any token, no .env needed
     It calls getUpdates and prints the chat IDs it can see. Put the right one in
     TELEGRAM_CHAT_ID / TELEGRAM_LOG_CHAT_ID (.env).
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Print Telegram chat IDs a bot can currently see (via getUpdates)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            help="Bot token to query. Overrides settings. Use this to look up a "
                 "chat for the logs bot without editing .env.",
        )
        parser.add_argument(
            '--logs',
            action='store_true',
            help="Use TELEGRAM_LOG_BOT_TOKEN (the dedicated logs bot) instead of "
                 "TELEGRAM_BOT_TOKEN.",
        )

    def handle(self, *args, **options):
        if options.get('token'):
            token = options['token']
        elif options.get('logs'):
            token = getattr(settings, 'TELEGRAM_LOG_BOT_TOKEN', None)
            if not token:
                raise CommandError("TELEGRAM_LOG_BOT_TOKEN is not set in your .env.")
        else:
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
