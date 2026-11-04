import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    database_url: str

    @classmethod
    def from_environment(cls) -> "Settings":
        values = {
            "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            "DATABASE_URL": os.environ.get("DATABASE_URL", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variable(s): {names}")

        return cls(
            telegram_bot_token=values["TELEGRAM_BOT_TOKEN"],
            database_url=values["DATABASE_URL"],
        )
