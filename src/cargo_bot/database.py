import logging
import re
from dataclasses import dataclass

import psycopg

from .crypto import decrypt_customer_registration

logger = logging.getLogger(__name__)
TRACKING_CODE_PATTERN = re.compile(r"UZ[0-9]{5,19}", re.ASCII)


@dataclass(frozen=True)
class CustomerRecord:
    user_key: bytes
    tracking_code: str | None
    encrypted_payload: bytes
    phone_lookup_key: bytes | None


@dataclass(frozen=True)
class TrackingCodeAuditEntry:
    old_tracking_code: str | None
    new_tracking_code: str
    changed_by_telegram_id: int | None
    actor_type: str
    created_at: object


async def _next_tracking_code(cursor: psycopg.AsyncCursor) -> str:
    await cursor.execute(
        """
        INSERT INTO customer_tracking_code_counters (id, last_number)
        VALUES (1, 10000)
        ON CONFLICT (id) DO NOTHING
        """
    )
    await cursor.execute(
        """
        UPDATE customer_tracking_code_counters
        SET last_number = last_number + 1
        WHERE id = 1
        RETURNING last_number
        """
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Tracking code counter is unavailable.")
    return f"UZ{row[0]:05d}"


async def _record_tracking_code_change(
    cursor: psycopg.AsyncCursor,
    user_key: bytes,
    old_code: str | None,
    new_code: str,
    changed_by_telegram_id: int | None,
    actor_type: str,
) -> None:
    await cursor.execute(
        """
        INSERT INTO customer_tracking_code_audit (
            user_key,
            old_tracking_code,
            new_tracking_code,
            changed_by_telegram_id,
            actor_type
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            user_key,
            old_code,
            new_code,
            changed_by_telegram_id,
            actor_type,
        ),
    )


async def verify_database_connection(database_url: str) -> None:
    """Verify PostgreSQL connectivity without creating or modifying any tables."""
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
                if row != (1,):
                    raise RuntimeError("Database returned an unexpected health-check result.")
    except (psycopg.Error, ValueError) as error:
        error_type = type(error).__name__
        logger.error("Database connectivity check failed (%s).", error_type)
        raise RuntimeError(
            f"Database connectivity check failed ({error_type})."
        ) from None

    logger.info("Database connection verified.")


async def save_customer_registration(
    database_url: str,
    user_key: bytes,
    encrypted_payload: bytes,
    phone_lookup_key: bytes,
) -> str:
    """Save an encrypted registration and return its unique tracking code."""
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT tracking_code
                        FROM customer_registrations
                        WHERE user_key = %s
                        FOR UPDATE
                        """,
                        (user_key,),
                    )
                    existing = await cursor.fetchone()
                    if existing is not None:
                        existing_code = existing[0]
                        if existing_code is None:
                            existing_code = await _next_tracking_code(cursor)
                            await cursor.execute(
                                """
                                UPDATE customer_registrations
                                SET encrypted_payload = %s,
                                    phone_lookup_key = %s,
                                    tracking_code = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE user_key = %s
                                """,
                                (
                                    encrypted_payload,
                                    phone_lookup_key,
                                    existing_code,
                                    user_key,
                                ),
                            )
                            await _record_tracking_code_change(
                                cursor,
                                user_key,
                                None,
                                existing_code,
                                None,
                                "registration",
                            )
                        else:
                            await cursor.execute(
                                """
                                UPDATE customer_registrations
                                SET encrypted_payload = %s,
                                    phone_lookup_key = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE user_key = %s
                                """,
                                (encrypted_payload, phone_lookup_key, user_key),
                            )
                        return existing_code

                    tracking_code = await _next_tracking_code(cursor)
                    await cursor.execute(
                        """
                        INSERT INTO customer_registrations (
                            user_key,
                            encrypted_payload,
                            phone_lookup_key,
                            tracking_code
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_key) DO NOTHING
                        RETURNING tracking_code
                        """,
                        (
                            user_key,
                            encrypted_payload,
                            phone_lookup_key,
                            tracking_code,
                        ),
                    )
                    inserted = await cursor.fetchone()
                    if inserted is not None:
                        await _record_tracking_code_change(
                            cursor,
                            user_key,
                            None,
                            inserted[0],
                            None,
                            "registration",
                        )
                        return inserted[0]

                    await cursor.execute(
                        """
                        SELECT tracking_code
                        FROM customer_registrations
                        WHERE user_key = %s
                        FOR UPDATE
                        """,
                        (user_key,),
                    )
                    existing = await cursor.fetchone()
                    if existing is None:
                        raise RuntimeError("Customer registration could not be saved.")

                    existing_code = existing[0]
                    if existing_code is None:
                        existing_code = await _next_tracking_code(cursor)
                        await cursor.execute(
                            """
                            UPDATE customer_registrations
                            SET encrypted_payload = %s,
                                phone_lookup_key = %s,
                                tracking_code = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE user_key = %s
                            """,
                            (
                                encrypted_payload,
                                phone_lookup_key,
                                existing_code,
                                user_key,
                            ),
                        )
                        await _record_tracking_code_change(
                            cursor,
                            user_key,
                            None,
                            existing_code,
                            None,
                            "registration",
                        )
                    else:
                        await cursor.execute(
                            """
                            UPDATE customer_registrations
                            SET encrypted_payload = %s,
                                phone_lookup_key = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE user_key = %s
                            """,
                            (encrypted_payload, phone_lookup_key, user_key),
                        )
                    return existing_code
    except psycopg.Error as error:
        error_type = type(error).__name__
        logger.error("Customer registration save failed (%s).", error_type)
        raise RuntimeError(
            f"Customer registration save failed ({error_type})."
        ) from None


async def find_customer_by_tracking_code(
    database_url: str,
    tracking_code: str,
) -> CustomerRecord | None:
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT user_key, tracking_code, encrypted_payload, phone_lookup_key
                    FROM customer_registrations
                    WHERE tracking_code = %s
                    """,
                    (tracking_code,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                return CustomerRecord(
                    user_key=bytes(row[0]),
                    tracking_code=row[1],
                    encrypted_payload=bytes(row[2]),
                    phone_lookup_key=bytes(row[3]) if row[3] is not None else None,
                )
    except psycopg.Error as error:
        error_type = type(error).__name__
        logger.error("Customer search failed (%s).", error_type)
        raise RuntimeError(f"Customer search failed ({error_type}).") from None


async def find_customers_by_phone_lookup_key(
    database_url: str,
    phone_lookup_key: bytes,
) -> list[CustomerRecord]:
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT user_key, tracking_code, encrypted_payload, phone_lookup_key
                    FROM customer_registrations
                    WHERE phone_lookup_key = %s OR phone_lookup_key IS NULL
                    ORDER BY tracking_code NULLS LAST
                    LIMIT 1000
                    """,
                    (phone_lookup_key,),
                )
                rows = await cursor.fetchall()
                return [
                    CustomerRecord(
                        user_key=bytes(row[0]),
                        tracking_code=row[1],
                        encrypted_payload=bytes(row[2]),
                        phone_lookup_key=(
                            bytes(row[3]) if row[3] is not None else None
                        ),
                    )
                    for row in rows
                ]
    except psycopg.Error as error:
        error_type = type(error).__name__
        logger.error("Customer search failed (%s).", error_type)
        raise RuntimeError(f"Customer search failed ({error_type}).") from None


async def change_customer_tracking_code(
    database_url: str,
    current_code: str,
    new_code: str,
    changed_by_telegram_id: int,
    master_key: bytes,
) -> tuple[str, int | None]:
    if TRACKING_CODE_PATTERN.fullmatch(new_code) is None:
        raise ValueError("Tracking code format is invalid.")

    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT user_key, encrypted_payload
                        FROM customer_registrations
                        WHERE tracking_code = %s
                        FOR UPDATE
                        """,
                        (current_code,),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        return "not_found", None

                    user_key = bytes(row[0])
                    encrypted_payload = bytes(row[1])
                    if current_code == new_code:
                        return "unchanged", None

                    registration = decrypt_customer_registration(
                        master_key,
                        user_key,
                        encrypted_payload,
                    )
                    telegram_user_id = registration.get("telegram_user_id")
                    if (
                        not isinstance(telegram_user_id, int)
                        or telegram_user_id <= 0
                    ):
                        return "legacy_registration", None

                    new_number = int(new_code[2:])
                    await cursor.execute(
                        """
                        INSERT INTO customer_tracking_code_counters (id, last_number)
                        VALUES (1, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET last_number = GREATEST(
                            customer_tracking_code_counters.last_number,
                            EXCLUDED.last_number
                        )
                        """,
                        (new_number,),
                    )
                    await cursor.execute(
                        """
                        UPDATE customer_registrations
                        SET tracking_code = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_key = %s
                        """,
                        (new_code, user_key),
                    )
                    await _record_tracking_code_change(
                        cursor,
                        user_key,
                        current_code,
                        new_code,
                        changed_by_telegram_id,
                        "admin",
                    )
                    return "changed", telegram_user_id
    except psycopg.errors.UniqueViolation:
        return "duplicate", None
    except psycopg.Error as error:
        error_type = type(error).__name__
        logger.error("Tracking code update failed (%s).", error_type)
        raise RuntimeError(f"Tracking code update failed ({error_type}).") from None


async def get_customer_tracking_code_history(
    database_url: str,
    tracking_code: str,
) -> list[TrackingCodeAuditEntry]:
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT audit.old_tracking_code,
                           audit.new_tracking_code,
                           audit.changed_by_telegram_id,
                           audit.actor_type,
                           audit.created_at
                    FROM customer_tracking_code_audit AS audit
                    JOIN customer_registrations AS customer
                      ON customer.user_key = audit.user_key
                    WHERE customer.tracking_code = %s
                    ORDER BY audit.created_at ASC, audit.id ASC
                    """,
                    (tracking_code,),
                )
                rows = await cursor.fetchall()
                return [
                    TrackingCodeAuditEntry(
                        old_tracking_code=row[0],
                        new_tracking_code=row[1],
                        changed_by_telegram_id=row[2],
                        actor_type=row[3],
                        created_at=row[4],
                    )
                    for row in rows
                ]
    except psycopg.Error as error:
        error_type = type(error).__name__
        logger.error("Tracking code history lookup failed (%s).", error_type)
        raise RuntimeError(
            f"Tracking code history lookup failed ({error_type})."
        ) from None
