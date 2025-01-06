import os
from typing import List
from dotenv import load_dotenv
from src.twitter_follower_monitor.bot import TwitterMonitorBot


def main() -> None:
    load_dotenv()

    authorized_users: List[str] = os.getenv("AUTHORIZED_USERS", "").split(",")
    authorized_users = [user.strip() for user in authorized_users if user.strip()]

    if not authorized_users:
        raise ValueError("No authorized users specified in AUTHORIZED_USERS")

    bot = TwitterMonitorBot(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        twitter_email=os.getenv("TWITTER_EMAIL", ""),
        twitter_password=os.getenv("TWITTER_PASSWORD", ""),
        authorized_users=authorized_users,
        check_interval=int(os.getenv("CHECK_INTERVAL", "10"))
    )
    
    bot.run()


if __name__ == "__main__":
    main()
