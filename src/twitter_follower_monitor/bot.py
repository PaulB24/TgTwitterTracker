import threading
from typing import Optional, List
from telegram import Update, Chat
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)

from .monitor import FollowerMonitor
from .database import DatabaseManager
from .notifications import TelegramNotifier


class TwitterMonitorBot:

    def __init__(
        self,
        telegram_token: str,
        twitter_username: str,
        twitter_email: str,
        twitter_password: str,
        authorized_users: List[str],
        check_interval: int = 300
    ) -> None:

        self.telegram_token = telegram_token
        self.twitter_username = twitter_username
        self.twitter_email = twitter_email
        self.twitter_password = twitter_password
        self.check_interval = check_interval
        self.authorized_users = set(authorized_users)  
        
        self.db_manager = DatabaseManager()
        self.monitor_thread: Optional[threading.Thread] = None
        self.monitor: Optional[FollowerMonitor] = None
        self.chat_id: Optional[int] = None

    async def _check_auth(self, update: Update) -> bool:

        if not update.effective_chat or not update.effective_user:
            return False

        if update.effective_chat.type not in [Chat.GROUP, Chat.SUPERGROUP]:
            await update.message.reply_text("This bot can only be used in groups!")
            return False

        username = update.effective_user.username
        if not username or username not in self.authorized_users:
            await update.message.reply_text("You are not authorized to use this bot!")
            return False

        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return

        self.chat_id = update.effective_chat.id
        notifier = TelegramNotifier(context.bot, self.chat_id)
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            await update.message.reply_text("Monitoring is already running!")
            return

        self.monitor = FollowerMonitor(
            notifier=notifier,
            check_interval=self.check_interval,
            twitter_username=self.twitter_username,
            twitter_email=self.twitter_email,
            twitter_password=self.twitter_password,
            db_manager=self.db_manager
        )

        usernames = self.db_manager.get_all_users()
        if not usernames:
            await update.message.reply_text(
                "No users to monitor! Add users with /add_user username"
            )
            return

        self.monitor_thread = threading.Thread(
            target=self.monitor.start_monitoring,
            args=(usernames,),
            daemon=True
        )
        self.monitor_thread.start()
        
        await update.message.reply_text("Started monitoring Twitter followers!")

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

        if not await self._check_auth(update):
            return

        if self.monitor:
            self.monitor.stop_monitoring()
            self.monitor = None
            await update.message.reply_text("Stopped monitoring Twitter followers!")
        else:
            await update.message.reply_text("Monitoring is not running!")

    async def add_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

        if not await self._check_auth(update):
            return

        if not context.args:
            await update.message.reply_text("Please provide one or more usernames!")
            return

        added_users = []
        failed_users = []

        for username in context.args:
            username = username.strip('@')
            try:
                self.db_manager.add_user(username)
                added_users.append(username)
            except Exception as e:
                failed_users.append(username)
                print(f"Failed to add user {username}: {str(e)}")

        response = []
        if added_users:
            users_list = ", ".join(f"@{user}" for user in added_users)
            response.append(f"Added users: {users_list}")
        
        if failed_users:
            users_list = ", ".join(f"@{user}" for user in failed_users)
            response.append(f"Failed to add users: {users_list}")
        
        if not response:
            response.append("No users were added")

        await update.message.reply_text("\n".join(response))

    async def remove_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return

        if not context.args:
            await update.message.reply_text("Please provide one or more usernames!")
            return

        existing_users = self.db_manager.get_all_users()
        for username in context.args:
            if username.strip('@') not in existing_users:
                await update.message.reply_text(f"User @{username} does not exist in the database.")
                return
        removed_users = []
        failed_users = []

        for username in context.args:
            username = username.strip('@')
            try:
                self.db_manager.remove_user(username)
                removed_users.append(username)
            except Exception as e:
                failed_users.append(username)
                print(f"Failed to remove user {username}: {str(e)}")

        response = []
        if removed_users:
            users_list = ", ".join(f"@{user}" for user in removed_users)
            response.append(f"Removed users: {users_list}")
        
        if failed_users:
            users_list = ", ".join(f"@{user}" for user in failed_users)
            response.append(f"Failed to remove users: {users_list}")
        
        if not response:
            response.append("No users were removed")

        await update.message.reply_text("\n".join(response))

    async def list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

        if not await self._check_auth(update):
            return

        users = self.db_manager.get_all_users()
        if users:
            user_list = "\n".join(f"@{user}" for user in users)
            await update.message.reply_text(f"Monitored users:\n{user_list}")
        else:
            await update.message.reply_text("No users are being monitored!")

    async def get_following(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return

        if not context.args:
            await update.message.reply_text("Please provide a username!")
            return

        username = context.args[0].strip('@')
        
        try:
            count = self.db_manager.get_following_count(username)
            if count is not None:
                await update.message.reply_text(
                    f"@{username} is currently following {count} accounts"
                )
            else:
                await update.message.reply_text(
                    f"No following count available for @{username}. "
                    "User might not be monitored or data hasn't been collected yet."
                )
        except Exception as e:
            await update.message.reply_text(f"Error getting following count: {str(e)}")

    def run(self) -> None:
        application = Application.builder().token(self.telegram_token).build()

        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("stop", self.stop))
        application.add_handler(CommandHandler("add_user", self.add_user))
        application.add_handler(CommandHandler("remove_user", self.remove_user))
        application.add_handler(CommandHandler("list_users", self.list_users))
        application.add_handler(CommandHandler("get_following", self.get_following))

        application.run_polling() 