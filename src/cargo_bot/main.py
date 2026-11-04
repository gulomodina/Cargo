import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Settings
from .database import verify_database_connection

logger = logging.getLogger(__name__)


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            exception_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = self._redact(exception_text)
            record.exc_info = None
        return True


async def start(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "The cargo bot is online. Cargo features will be added when you instruct me."
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_environment()
    redaction_filter = SecretRedactionFilter(
        (settings.telegram_bot_token, settings.database_url)
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction_filter)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    async def on_startup(_application: Application) -> None:
        await verify_database_connection(settings.database_url)
        logger.info("Telegram bot startup checks passed.")

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.run_polling()
