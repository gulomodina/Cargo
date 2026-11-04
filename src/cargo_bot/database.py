import logging

import psycopg

logger = logging.getLogger(__name__)


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
) -> None:
    """Insert or update a customer's encrypted registration."""
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO customer_registrations (user_key, encrypted_payload)
                    VALUES (%s, %s)
                    ON CONFLICT (user_key) DO UPDATE
                    SET encrypted_payload = EXCLUDED.encrypted_payload,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (user_key, encrypted_payload),
                )
    except psycopg.Error as error:
        error_type = type(error).__name__
        logger.error("Customer registration save failed (%s).", error_type)
        raise RuntimeError(
            f"Customer registration save failed ({error_type})."
        ) from None
