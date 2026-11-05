import logging
import re

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from .config import Settings
from .crypto import (
    customer_phone_lookup_key,
    customer_user_key,
    decrypt_customer_registration,
    normalize_phone_number,
)
from .database import (
    change_customer_tracking_code,
    find_customer_by_tracking_code,
    find_customers_by_phone_lookup_key,
    get_customer_language,
    get_customer_tracking_code_history,
)
from .i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, text as localized_text

logger = logging.getLogger(__name__)
TRACKING_CODE_PATTERN = re.compile(r"UZ[0-9]{5,19}", re.ASCII)
MAX_SEARCH_RESULTS = 20


async def _is_authorized_admin(
    update: Update,
    settings: Settings,
) -> bool:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None:
        return False
    if chat is None or chat.type != ChatType.PRIVATE:
        await message.reply_text("Admin actions are only available in a private chat.")
        return False
    if user is None or user.id not in settings.telegram_admin_ids:
        await message.reply_text("This command is restricted to authorized admins.")
        return False
    return True


async def search_customers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> None:
    message = update.effective_message
    if message is None or not await _is_authorized_admin(update, settings):
        return

    query = " ".join(getattr(context, "args", [])).strip()
    if not query:
        await message.reply_text(
            "Usage: /customer_search <phone number or tracking code>"
        )
        return

    try:
        normalized_code = query.upper()
        if TRACKING_CODE_PATTERN.fullmatch(normalized_code):
            record = await find_customer_by_tracking_code(
                settings.database_url,
                normalized_code,
            )
            records = [record] if record is not None else []
        else:
            normalized_phone = normalize_phone_number(query)
            lookup_key = customer_phone_lookup_key(
                settings.customer_data_encryption_key,
                normalized_phone,
            )
            candidates = await find_customers_by_phone_lookup_key(
                settings.database_url,
                lookup_key,
            )
            records = []
            for record in candidates:
                if record.phone_lookup_key == lookup_key:
                    records.append(record)
                    continue
                registration = decrypt_customer_registration(
                    settings.customer_data_encryption_key,
                    record.user_key,
                    record.encrypted_payload,
                )
                saved_phone = registration.get("phone_number")
                if (
                    isinstance(saved_phone, str)
                    and normalize_phone_number(saved_phone) == normalized_phone
                ):
                    records.append(record)
    except ValueError:
        await message.reply_text(
            "Use a valid phone number or a tracking code such as UZ10001."
        )
        return
    except Exception as error:
        logger.error("Admin customer search failed (%s).", type(error).__name__)
        await message.reply_text("The customer search could not be completed.")
        return

    if not records:
        await message.reply_text("No matching customer was found.")
        return

    results = ["Matching customers:"]
    for record in records[:MAX_SEARCH_RESULTS]:
        if record.tracking_code is None:
            results.append(
                "A saved registration has no tracking code yet; the customer "
                "must complete registration again."
            )
        else:
            results.append(record.tracking_code)
    if len(records) > MAX_SEARCH_RESULTS:
        results.append("More matches exist; use a tracking code to narrow the search.")
    await message.reply_text("\n".join(results))


async def change_tracking_code(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if (
        message is None
        or user is None
        or not await _is_authorized_admin(update, settings)
    ):
        return

    args = getattr(context, "args", [])
    if len(args) != 2:
        await message.reply_text(
            "Usage: /customer_set_code <current code> <new code>"
        )
        return

    current_code, new_code = (value.upper() for value in args)
    if (
        TRACKING_CODE_PATTERN.fullmatch(current_code) is None
        or TRACKING_CODE_PATTERN.fullmatch(new_code) is None
    ):
        await message.reply_text(
            "Tracking codes must use the format UZ10001."
        )
        return

    try:
        status, customer_telegram_id = await change_customer_tracking_code(
            settings.database_url,
            current_code,
            new_code,
            user.id,
            settings.customer_data_encryption_key,
        )
    except Exception as error:
        logger.error("Admin tracking code update failed (%s).", type(error).__name__)
        await message.reply_text("The tracking code could not be updated.")
        return

    if status == "not_found":
        await message.reply_text("No customer has that current tracking code.")
        return
    if status == "duplicate":
        await message.reply_text(
            "That tracking code is already assigned or was used before."
        )
        return
    if status == "legacy_registration":
        await message.reply_text(
            "This registration cannot be notified. Ask the customer to complete "
            "registration again before changing the code."
        )
        return
    if status == "unchanged":
        await message.reply_text("The customer already has that tracking code.")
        return

    customer_language = DEFAULT_LANGUAGE
    if customer_telegram_id is not None:
        try:
            stored_language = await get_customer_language(
                settings.database_url,
                customer_user_key(
                    settings.customer_data_encryption_key,
                    customer_telegram_id,
                ),
            )
            if stored_language in SUPPORTED_LANGUAGES:
                customer_language = stored_language
        except Exception as error:
            logger.error(
                "Customer notification language lookup failed (%s).",
                type(error).__name__,
            )

    try:
        await context.bot.send_message(
            chat_id=customer_telegram_id,
            text=localized_text(
                customer_language,
                "tracking_code_changed",
                tracking_code=new_code,
            ),
        )
    except Exception as error:
        logger.error(
            "Customer tracking code notification failed (%s).",
            type(error).__name__,
        )
        await message.reply_text(
            "The tracking code was updated and audited, but Telegram could not "
            "deliver the customer notification."
        )
        return

    await message.reply_text(
        f"Tracking code updated to {new_code}; the customer was notified."
    )


async def show_tracking_code_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> None:
    message = update.effective_message
    if message is None or not await _is_authorized_admin(update, settings):
        return

    args = getattr(context, "args", [])
    if len(args) != 1:
        await message.reply_text(
            "Usage: /customer_code_history <current tracking code>"
        )
        return

    tracking_code = args[0].upper()
    if TRACKING_CODE_PATTERN.fullmatch(tracking_code) is None:
        await message.reply_text("Tracking codes must use the format UZ10001.")
        return

    try:
        history = await get_customer_tracking_code_history(
            settings.database_url,
            tracking_code,
        )
    except Exception as error:
        logger.error("Tracking code history lookup failed (%s).", type(error).__name__)
        await message.reply_text("The tracking code history could not be loaded.")
        return

    if not history:
        await message.reply_text("No tracking code history was found.")
        return

    lines = [f"Tracking code history for {tracking_code}:"]
    for entry in history:
        previous = entry.old_tracking_code or "new registration"
        if entry.actor_type == "admin" and entry.changed_by_telegram_id is not None:
            actor = f"admin {entry.changed_by_telegram_id}"
        else:
            actor = "registration"
        lines.append(
            f"{previous} → {entry.new_tracking_code} ({actor}; "
            f"{entry.created_at.isoformat()})"
        )
    await message.reply_text("\n".join(lines))
