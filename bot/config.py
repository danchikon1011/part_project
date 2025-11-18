from dataclasses import dataclass
import os


@dataclass
class Settings:
    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./bot.db"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise RuntimeError("Environment variable BOT_TOKEN is required")
        return cls(bot_token=token)


settings = Settings.from_env()
