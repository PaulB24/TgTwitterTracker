from abc import ABC, abstractmethod
import asyncio
from telegram import Bot


class NotificationService(ABC):

    @abstractmethod
    def notify(self, message: str) -> None:
        pass


class TelegramNotifier(NotificationService):

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.loop = asyncio.get_event_loop()

    def notify(self, message: str) -> None:
 
        asyncio.run_coroutine_threadsafe(
            self.bot.send_message(
                chat_id=self.chat_id,
                text=message
            ),
            self.loop
        ) 