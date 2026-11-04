import base64
import logging
import re

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import Settings
from .crypto import encrypt_customer_registration
from .database import save_customer_registration, verify_database_connection

logger = logging.getLogger(__name__)

WAITING_FOR_PHONE, WAITING_FOR_PASSPORT, WAITING_FOR_PHOTO = range(3)
MAX_PASSPORT_PHOTO_BYTES = 5 * 1024 * 1024
PASSPORT_PATTERN = re.compile(r"[A-Za-z]{2}[0-9]{7}", re.ASCII)


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
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    chat = update.effective_chat
    context.user_data.clear()

    if message is None or chat is None:
        return ConversationHandler.END

    if chat.type != ChatType.PRIVATE:
        await message.reply_text(
            "For privacy, customer registration must be completed in a private chat."
        )
        return ConversationHandler.END

    await message.reply_text(
        "Register as a customer to share your phone number, passport number, and "
        "passport photo. These details are encrypted before storage.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Register", callback_data="customer_registration:start")]]
        ),
    )
    return ConversationHandler.END


async def begin_registration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    query = update.callback_query
    chat = update.effective_chat
    if query is None or chat is None:
        return ConversationHandler.END

    await query.answer()
    if chat.type != ChatType.PRIVATE or query.message is None:
        return ConversationHandler.END

    context.user_data.clear()
    await query.message.reply_text(
        "Share your own phone number using the button below.",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("Share my phone number", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return WAITING_FOR_PHONE


async def prompt_for_phone(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Please use the button to share your own phone number."
        )
    return WAITING_FOR_PHONE


async def receive_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    user = update.effective_user
    contact = message.contact if message is not None else None
    if message is None or user is None or contact is None:
        return WAITING_FOR_PHONE

    if contact.user_id != user.id or not contact.phone_number.strip():
        await message.reply_text(
            "Please share your own phone number using the button."
        )
        return WAITING_FOR_PHONE

    context.user_data["customer_registration"] = {
        "phone_number": contact.phone_number.strip(),
    }
    await message.reply_text(
        "Enter your passport series and number, for example AB1234567.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return WAITING_FOR_PASSPORT


async def prompt_for_passport(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Enter two letters followed by seven digits, for example AB1234567."
        )
    return WAITING_FOR_PASSPORT


async def receive_passport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    registration = context.user_data.get("customer_registration")
    passport_number = message.text.strip() if message and message.text else ""

    if registration is None:
        if message is not None:
            await message.reply_text("Registration expired. Send /start to begin again.")
        return ConversationHandler.END

    if PASSPORT_PATTERN.fullmatch(passport_number) is None:
        if message is not None:
            await message.reply_text(
                "That format is invalid. Enter two letters followed by seven digits, "
                "for example AB1234567."
            )
        return WAITING_FOR_PASSPORT

    registration["passport_number"] = passport_number.upper()
    if message is not None:
        await message.reply_text(
            "Upload a clear photo of your passport's identity page as a photo or "
            "JPEG/PNG image."
        )
    return WAITING_FOR_PHOTO


async def prompt_for_photo(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Please upload the passport identity page as a photo or JPEG/PNG image."
        )
    return WAITING_FOR_PHOTO


def detect_image_type(image: bytes) -> str | None:
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


async def receive_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> int:
    message = update.effective_message
    user = update.effective_user
    registration = context.user_data.get("customer_registration")
    if message is None or user is None or registration is None:
        if message is not None:
            await message.reply_text("Registration expired. Send /start to begin again.")
        return ConversationHandler.END

    if message.photo:
        media = message.photo[-1]
    elif message.document is not None and message.document.mime_type in {
        "image/jpeg",
        "image/png",
    }:
        media = message.document
    else:
        await message.reply_text(
            "Please upload a JPEG or PNG photo of the passport identity page."
        )
        return WAITING_FOR_PHOTO

    if media.file_size is not None and media.file_size > MAX_PASSPORT_PHOTO_BYTES:
        await message.reply_text("The image must be 5 MB or smaller. Please upload it again.")
        return WAITING_FOR_PHOTO

    remote_file = await context.bot.get_file(media.file_id)
    image = bytes(await remote_file.download_as_bytearray())
    if len(image) > MAX_PASSPORT_PHOTO_BYTES:
        await message.reply_text("The image must be 5 MB or smaller. Please upload it again.")
        return WAITING_FOR_PHOTO

    mime_type = detect_image_type(image)
    if mime_type is None:
        await message.reply_text(
            "That file is not a supported JPEG or PNG image. Please upload it again."
        )
        return WAITING_FOR_PHOTO

    try:
        user_key, encrypted_payload = encrypt_customer_registration(
            master_key=settings.customer_data_encryption_key,
            telegram_user_id=user.id,
            phone_number=registration["phone_number"],
            passport_number=registration["passport_number"],
            photo=image,
            photo_mime_type=mime_type,
        )
        await save_customer_registration(
            settings.database_url,
            user_key,
            encrypted_payload,
        )
    except Exception as error:
        logger.error("Customer registration failed (%s).", type(error).__name__)
        context.user_data.clear()
        await message.reply_text(
            "We couldn't save your registration. Please send /start to try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    context.user_data.clear()
    await message.reply_text(
        "Your customer registration is complete. Your information was saved encrypted.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def report_update_error(
    _update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    error_type = type(context.error).__name__ if context.error is not None else "UnknownError"
    logger.error("Telegram update failed (%s).", error_type)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_environment()
    redaction_filter = SecretRedactionFilter(
        (
            settings.telegram_bot_token,
            settings.database_url,
            base64.urlsafe_b64encode(
                settings.customer_data_encryption_key
            ).decode("ascii"),
        )
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction_filter)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    async def on_startup(_application: Application) -> None:
        await verify_database_connection(settings.database_url)
        logger.info("Telegram bot startup checks passed.")

    async def handle_photo_update(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        return await receive_photo(update, context, settings)

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .build()
    )
    registration_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(
                begin_registration,
                pattern=r"^customer_registration:start$",
            ),
        ],
        states={
            WAITING_FOR_PHONE: [
                MessageHandler(filters.CONTACT, receive_phone),
                MessageHandler(filters.ALL & ~filters.COMMAND, prompt_for_phone),
            ],
            WAITING_FOR_PASSPORT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_passport,
                ),
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    prompt_for_passport,
                ),
            ],
            WAITING_FOR_PHOTO: [
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE,
                    handle_photo_update,
                ),
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    prompt_for_photo,
                ),
            ],
        },
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )
    application.add_handler(registration_conversation)
    application.add_error_handler(report_update_error)
    application.run_polling()
