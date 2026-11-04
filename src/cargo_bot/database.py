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
