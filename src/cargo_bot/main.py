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
from telegram.error import InvalidToken
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .admin import change_tracking_code, search_customers, show_tracking_code_history
from .config import Settings
from .crypto import (
    customer_phone_lookup_key,
    customer_user_key,
    encrypt_customer_registration,
    normalize_phone_number,
)
from .database import (
    get_customer_language,
    save_customer_language,
    save_customer_registration,
    verify_database_connection,
)
from .i18n import (
    DEFAULT_LANGUAGE,
    INITIAL_LANGUAGE_PROMPT,
    LANGUAGE_BUTTONS,
    SUPPORTED_LANGUAGES,
    text as localized_text,
)

logger = logging.getLogger(__name__)

WAITING_FOR_PHONE, WAITING_FOR_PASSPORT, WAITING_FOR_PHOTO = range(3)
MAX_PASSPORT_PHOTO_BYTES = 5 * 1024 * 1024
PASSPORT_PATTERN = re.compile(r"[A-Za-z]{2}[0-9]{7}", re.ASCII)
LANGUAGE_CALLBACK_PATTERN = re.compile(r"^language:(uz|ru)$", re.ASCII)
SETTINGS_CALLBACK_PATTERN = re.compile(r"^settings:(open|language|menu)$", re.ASCII)


def get_context_language(context: ContextTypes.DEFAULT_TYPE) -> str:
    language = context.user_data.get("language_code", DEFAULT_LANGUAGE)
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


async def get_user_language(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> str:
    current = context.user_data.get("language_code")
    if current in SUPPORTED_LANGUAGES:
        return current

    user = update.effective_user
    if user is not None:
        user_key = customer_user_key(
            settings.customer_data_encryption_key,
            user.id,
        )
        try:
            language = await get_customer_language(settings.database_url, user_key)
        except Exception as error:
            logger.error("Customer language lookup failed (%s).", type(error).__name__)
            language = None
        if language in SUPPORTED_LANGUAGES:
            context.user_data["language_code"] = language
            return language

    context.user_data["language_code"] = DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE


def language_selection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LANGUAGE_BUTTONS["uz"],
                    callback_data="language:uz",
                )
            ],
            [
                InlineKeyboardButton(
                    LANGUAGE_BUTTONS["ru"],
                    callback_data="language:ru",
                )
            ],
        ]
    )


def customer_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    localized_text(language, "register_button"),
                    callback_data="customer_registration:start",
                )
            ],
            [
                InlineKeyboardButton(
                    localized_text(language, "settings_button"),
                    callback_data="settings:open",
                )
            ],
        ]
    )


def settings_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    localized_text(language, "change_language_button"),
                    callback_data="settings:language",
                )
            ],
            [
                InlineKeyboardButton(
                    localized_text(language, "back_to_menu_button"),
                    callback_data="settings:menu",
                )
            ],
        ]
    )


def clear_registration_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    language = get_context_language(context)
    context.user_data.clear()
    context.user_data["language_code"] = language


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
    settings: Settings,
) -> int:
    message = update.effective_message
    chat = update.effective_chat
    context.user_data.clear()

    if message is None or chat is None:
        return ConversationHandler.END

    stored_language: str | None = None
    user = update.effective_user
    if user is not None:
        user_key = customer_user_key(
            settings.customer_data_encryption_key,
            user.id,
        )
        try:
            stored_language = await get_customer_language(
                settings.database_url,
                user_key,
            )
        except Exception as error:
            logger.error("Customer language lookup failed (%s).", type(error).__name__)
    language = (
        stored_language
        if stored_language in SUPPORTED_LANGUAGES
        else DEFAULT_LANGUAGE
    )
    context.user_data["language_code"] = language
    if chat.type != ChatType.PRIVATE:
        await message.reply_text(
            localized_text(language, "private_only")
        )
        return ConversationHandler.END

    await message.reply_text(
        localized_text(language, "language_prompt")
        if stored_language in SUPPORTED_LANGUAGES
        else INITIAL_LANGUAGE_PROMPT,
        reply_markup=language_selection_keyboard(),
    )
    return ConversationHandler.END


async def select_language(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> int:
    query = update.callback_query
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if query is None or message is None or chat is None or user is None:
        return ConversationHandler.END

    await query.answer()
    if chat.type != ChatType.PRIVATE:
        await message.reply_text(localized_text(DEFAULT_LANGUAGE, "private_only"))
        return ConversationHandler.END

    match = LANGUAGE_CALLBACK_PATTERN.fullmatch(query.data or "")
    if match is None:
        return ConversationHandler.END
    language = match.group(1)
    context.user_data["language_code"] = language
    user_key = customer_user_key(
        settings.customer_data_encryption_key,
        user.id,
    )
    try:
        await save_customer_language(
            settings.database_url,
            user_key,
            language,
        )
    except Exception as error:
        logger.error("Customer language save failed (%s).", type(error).__name__)
        await message.reply_text(
            localized_text(language, "language_save_failed"),
            reply_markup=language_selection_keyboard(),
        )
        return ConversationHandler.END

    await message.reply_text(
        f"{localized_text(language, 'privacy_notice')}\n\n"
        f"{localized_text(language, 'menu_prompt')}",
        reply_markup=customer_menu_keyboard(language),
    )
    return ConversationHandler.END


async def handle_settings_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> int:
    query = update.callback_query
    message = update.effective_message
    chat = update.effective_chat
    if query is None or message is None or chat is None:
        return ConversationHandler.END

    await query.answer()
    language = await get_user_language(update, context, settings)
    if chat.type != ChatType.PRIVATE:
        await message.reply_text(localized_text(language, "private_only"))
        return ConversationHandler.END

    callback_data = query.data or ""
    if SETTINGS_CALLBACK_PATTERN.fullmatch(callback_data) is None:
        return ConversationHandler.END

    if callback_data == "settings:language":
        await message.reply_text(
            localized_text(language, "language_prompt"),
            reply_markup=language_selection_keyboard(),
        )
    elif callback_data == "settings:menu":
        await message.reply_text(
            f"{localized_text(language, 'privacy_notice')}\n\n"
            f"{localized_text(language, 'menu_prompt')}",
            reply_markup=customer_menu_keyboard(language),
        )
    else:
        await message.reply_text(
            f"{localized_text(language, 'settings_title')}\n"
            f"{localized_text(language, 'language_label')}",
            reply_markup=settings_keyboard(language),
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

    language = get_context_language(context)
    clear_registration_data(context)
    await query.message.reply_text(
        localized_text(language, "phone_request"),
        reply_markup=ReplyKeyboardMarkup(
            [[
                KeyboardButton(
                    localized_text(language, "share_phone_button"),
                    request_contact=True,
                )
            ]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return WAITING_FOR_PHONE


async def prompt_for_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            localized_text(get_context_language(context), "phone_own_only")
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
            localized_text(get_context_language(context), "phone_own_only")
        )
        return WAITING_FOR_PHONE

    try:
        phone_number = normalize_phone_number(contact.phone_number)
    except ValueError:
        await message.reply_text(
            localized_text(get_context_language(context), "phone_invalid")
        )
        return WAITING_FOR_PHONE

    context.user_data["customer_registration"] = {"phone_number": phone_number}
    await message.reply_text(
        localized_text(get_context_language(context), "passport_request"),
        reply_markup=ReplyKeyboardRemove(),
    )
    return WAITING_FOR_PASSPORT


async def prompt_for_passport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            localized_text(get_context_language(context), "passport_prompt")
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
            await message.reply_text(
                localized_text(get_context_language(context), "registration_expired")
            )
        return ConversationHandler.END

    if PASSPORT_PATTERN.fullmatch(passport_number) is None:
        if message is not None:
            await message.reply_text(
                localized_text(get_context_language(context), "passport_invalid")
            )
        return WAITING_FOR_PASSPORT

    registration["passport_number"] = passport_number.upper()
    if message is not None:
        await message.reply_text(
            localized_text(get_context_language(context), "photo_request")
        )
    return WAITING_FOR_PHOTO


async def prompt_for_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            localized_text(get_context_language(context), "photo_prompt")
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
            await message.reply_text(
                localized_text(get_context_language(context), "registration_expired")
            )
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
            localized_text(get_context_language(context), "photo_prompt")
        )
        return WAITING_FOR_PHOTO

    if media.file_size is not None and media.file_size > MAX_PASSPORT_PHOTO_BYTES:
        await message.reply_text(
            localized_text(get_context_language(context), "photo_too_large")
        )
        return WAITING_FOR_PHOTO

    remote_file = await context.bot.get_file(media.file_id)
    image = bytes(await remote_file.download_as_bytearray())
    if len(image) > MAX_PASSPORT_PHOTO_BYTES:
        await message.reply_text(
            localized_text(get_context_language(context), "photo_too_large")
        )
        return WAITING_FOR_PHOTO

    mime_type = detect_image_type(image)
    if mime_type is None:
        await message.reply_text(
            localized_text(get_context_language(context), "photo_invalid")
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
        phone_lookup = customer_phone_lookup_key(
            settings.customer_data_encryption_key,
            registration["phone_number"],
        )
        tracking_code = await save_customer_registration(
            settings.database_url,
            user_key,
            encrypted_payload,
            phone_lookup,
        )
    except Exception as error:
        logger.error("Customer registration failed (%s).", type(error).__name__)
        clear_registration_data(context)
        await message.reply_text(
            localized_text(get_context_language(context), "registration_failed"),
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    language = get_context_language(context)
    clear_registration_data(context)
    await message.reply_text(
        localized_text(
            language,
            "registration_complete",
            tracking_code=tracking_code,
        ),
        reply_markup=customer_menu_keyboard(language),
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

    async def handle_start(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        return await start(update, context, settings)

    async def handle_language_selection(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        return await select_language(update, context, settings)

    async def handle_settings_callback(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        return await handle_settings_action(update, context, settings)

    async def handle_photo_update(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        return await receive_photo(update, context, settings)

    async def handle_admin_search(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await search_customers(update, context, settings)

    async def handle_admin_code_change(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await change_tracking_code(update, context, settings)

    async def handle_admin_code_history(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await show_tracking_code_history(update, context, settings)

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .build()
    )
    registration_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", handle_start),
            CallbackQueryHandler(
                handle_language_selection,
                pattern=LANGUAGE_CALLBACK_PATTERN,
            ),
            CallbackQueryHandler(
                begin_registration,
                pattern=r"^customer_registration:start$",
            ),
            CallbackQueryHandler(
                handle_settings_callback,
                pattern=SETTINGS_CALLBACK_PATTERN,
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
        fallbacks=[],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )
    application.add_handler(registration_conversation)
    application.add_handler(CommandHandler("customer_search", handle_admin_search))
    application.add_handler(
        CommandHandler("customer_set_code", handle_admin_code_change)
    )
    application.add_handler(
        CommandHandler("customer_code_history", handle_admin_code_history)
    )
    application.add_error_handler(report_update_error)
    try:
        application.run_polling()
    except InvalidToken:
        logger.critical("Telegram API rejected the configured bot token.")
        raise SystemExit(1) from None
