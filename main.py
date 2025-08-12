# main.py
import os
import asyncio
import logging
from collections import deque, defaultdict
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    filters,
    ContextTypes,
)

from telegram.error import BadRequest, Forbidden

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # set this in Render environment variables
# If you want to hardcode (not recommended), replace with e.g. "8185...."
GROUP_USERNAME = "@Raport_seminarr0"
CHANNEL_USERNAME = "@raport_smenarr"  # channel username with @

# Anti-spam settings
SPAM_MAX_MSG = 5       # max messages
SPAM_WINDOW_SEC = 10   # time window in seconds
WARNING_AUTO_DELETE_SEC = 60  # warnings / reminders auto-delete after 1 minute

# Timezone for midnight reset
TZ = ZoneInfo("Asia/Baghdad")  # Iraq timezone (UTC+3)
# ===========================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class ChannelEnforcementBot:
    def __init__(self, token: str):
        if not token:
            raise RuntimeError("BOT_TOKEN not set. Set BOT_TOKEN env var before running.")
        self.application = Application.builder().token(token).build()

        # spam trackers: user_id -> deque of timestamps
        self.msg_times: dict[int, deque] = defaultdict(lambda: deque())

        # warning counts: user_id -> int
        self.warnings: dict[int, int] = defaultdict(int)

        # optional: track last reminder sent to avoid flooding (user_id -> datetime)
        self.last_reminder: dict[int, datetime] = {}

        self.setup_handlers()

    def setup_handlers(self):
        # core handlers
        self.application.add_handler(
            MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, self.on_group_message)
        )
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        # admin command to force reset warnings (optional)
        self.application.add_handler(CommandHandler("resetwarnings", self.cmd_reset_warnings))

    async def is_user_in_channel(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if user is a member of the channel."""
        try:
            member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
            return member.status in ["member", "administrator", "creator", "owner"]
        except (BadRequest, Forbidden) as e:
            logger.debug(f"Membership check failed for {user_id}: {e}")
            return False

    async def send_join_reminder(self, chat_id: int, user_name: str, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """
        Kurdish join reminder text (as requested).
        Auto-deletes after WARNING_AUTO_DELETE_SEC seconds.
        Button label: "بەژداری دەکەم"
        """
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ بەژداری دەکەم", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
        )

        text = (
            f"👋 بەخێربێی بەڕێز {user_name}\n"
            f"تکایە بەژداری بکە لە کەناڵەکەمان بۆ ئەوەی بتوانی نامە بنێری لەم گرووپە\n"
            f"تەنها پەنجە بنێ بە دووگمەی **بەژداری دەکەم** لە خوارەوە\n"
            f"🙏 سوپاس بۆ تێگەیشتنتان"
        )
        try:
            msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="Markdown")
            await asyncio.sleep(WARNING_AUTO_DELETE_SEC)
            try:
                await msg.delete()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Could not send join reminder to chat {chat_id}: {e}")

    async def send_spam_warning(self, chat_id: int, user, count: int, context: ContextTypes.DEFAULT_TYPE):
        """
        Spam warning in Kurdish:
        بەڕێز {user name }
        پەیامێکی نایاسایی نارد
        ژمارەی ئاگاداریەکان {count of worning}
        """
        user_name = user.first_name or user.username or str(user.id)
        text = f"بەڕێز {user_name}\nپەیامێکی نایاسایی نارد\nژمارەی ئاگاداریەکان {count}"
        try:
            warn_msg = await context.bot.send_message(chat_id=chat_id, text=text)
            await asyncio.sleep(WARNING_AUTO_DELETE_SEC)
            try:
                await warn_msg.delete()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Could not send spam warning: {e}")

    async def on_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Main handler for group messages: anti-spam then force-join enforcement."""
        if not update.message:
            return

        user = update.effective_user
        chat_id = update.effective_chat.id

        # ignore bot itself
        if user.id == context.bot.id:
            return

        # --- ANTI-SPAM LOGIC ---
        now = datetime.now(tz=TZ)
        dq = self.msg_times[user.id]
        dq.append(now)

        # remove old timestamps outside the SPAM_WINDOW_SEC window
        cutoff = now - timedelta(seconds=SPAM_WINDOW_SEC)
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) > SPAM_MAX_MSG:
            # considered spam: delete this message and warn
            try:
                await update.message.delete()
            except Exception:
                pass

            # increment warning count
            self.warnings[user.id] += 1
            await self.send_spam_warning(chat_id, user, self.warnings[user.id], context)
            return  # stop further processing (do not run join-check for spam messages)

        # --- FORCE-JOIN CHECK ---
        in_channel = await self.is_user_in_channel(user.id, context)
        if not in_channel:
            # delete the user's message
            try:
                await update.message.delete()
            except Exception:
                pass

            # throttle reminders to avoid spamming the group: only one reminder per user per 2 minutes
            last = self.last_reminder.get(user.id)
            if not last or (datetime.now(tz=TZ) - last) > timedelta(minutes=2):
                display_name = f"@{user.username}" if user.username else user.first_name or str(user.id)
                await self.send_join_reminder(chat_id, display_name, user.id, context)
                self.last_reminder[user.id] = datetime.now(tz=TZ)
            return

        # If user is in channel and not spam, nothing to do (their message allowed through)
        return

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # placeholder for future button callbacks
        await update.callback_query.answer()
        # currently join-button is an external URL button so no callback data expected
        return

    # Admin command to reset warnings (optional)
    async def cmd_reset_warnings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # only allow group admins to run this in the group
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
            if member.status not in ["administrator", "creator"]:
                await update.message.reply_text("تەنها بەڕێوەبەر دەتوانێت ئەم فرمانە بەکاربێنێت.")
                return
        except Exception:
            pass

        self.warnings.clear()
        await update.message.reply_text("⚙️ هەموو ئاگاداریەکان سڕدرانەوە.")

    # scheduled daily reset at midnight (Asia/Baghdad)
    async def reset_warnings_daily(self, context: ContextTypes.DEFAULT_TYPE):
        logger.info("Daily midnight reset: clearing warnings and msg_times")
        self.warnings.clear()
        self.msg_times.clear()
        # optionally clear last_reminder too
        self.last_reminder.clear()

    async def run(self):
        # schedule the daily reset at midnight Baghdad time
        # run_daily(handler, time=time(hour, minute, tzinfo=...))
        midnight = dtime(hour=0, minute=0, tzinfo=TZ)
        # schedule job via job_queue after application initialized
        self.application.job_queue.run_daily(self.reset_warnings_daily, time=midnight)

        logger.info("Starting Channel Enforcement Bot...")
        # ensure no webhook collisions
        try:
            await self.application.bot.delete_webhook()
        except Exception:
            pass

        await self.application.run_polling()


async def main():
    token = BOT_TOKEN
    bot = ChannelEnforcementBot(token)
    await bot.run()


if __name__ == "__main__":
    import nest_asyncio

    nest_asyncio.apply()
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "This event loop is already running" in str(e):
            loop = asyncio.get_event_loop()
            task = loop.create_task(main())
            loop.run_forever()
        else:
            raise
