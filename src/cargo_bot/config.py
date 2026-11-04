import base64
import binascii
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    database_url: str
    customer_data_encryption_key: bytes

    @classmethod
    def from_environment(cls) -> "Settings":
        values = {
            "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            "DATABASE_URL": os.environ.get("DATABASE_URL", "").strip(),
            "CUSTOMER_DATA_ENCRYPTION_KEY": os.environ.get(
                "CUSTOMER_DATA_ENCRYPTION_KEY", ""
            ).strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variable(s): {names}")

        try:
            encryption_key = base64.b64decode(
                values["CUSTOMER_DATA_ENCRYPTION_KEY"],
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError):
            raise RuntimeError(
                "CUSTOMER_DATA_ENCRYPTION_KEY must be URL-safe Base64 for a random AES key."
            ) from None

        if len(encryption_key) not in {16, 24, 32}:
            raise RuntimeError(
                "CUSTOMER_DATA_ENCRYPTION_KEY must decode to 16, 24, or 32 random bytes."
            )

        return cls(
            telegram_bot_token=values["TELEGRAM_BOT_TOKEN"],
            database_url=values["DATABASE_URL"],
            customer_data_encryption_key=encryption_key,
        )
