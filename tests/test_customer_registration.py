import asyncio
import base64
import json
import os
import re
import secrets
import struct
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
from psycopg.conninfo import make_conninfo
from telegram.constants import ChatType
from telegram.ext import ConversationHandler

from cargo_bot import admin, main
from cargo_bot.config import Settings
from cargo_bot.crypto import (
    customer_phone_lookup_key,
    customer_user_key,
    decrypt_customer_registration,
    encrypt_customer_registration,
)
from cargo_bot.database import (
    find_customer_by_tracking_code,
    get_customer_language,
    get_customer_tracking_code_history,
    save_customer_language,
    save_customer_registration,
)


FAKE_PHONE_NUMBER = "+15555550123"
FAKE_NORMALIZED_PHONE = "15555550123"
FAKE_PASSPORT_NUMBER = "AB1234567"
FAKE_ADMIN_ID = 987654321


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    content = chunk_type + data
    checksum = zlib.crc32(content) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + content + struct.pack(">I", checksum)


def _fake_png() -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\x12\x34\x56")
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", pixel)
        + _png_chunk(b"IEND", b"")
    )


class CustomerRegistrationAndTrackingTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ.get("DATABASE_URL", "")
        key_text = os.environ.get("CUSTOMER_DATA_ENCRYPTION_KEY", "")
        if not database_url or not key_text:
            raise unittest.SkipTest(
                "Development database and encryption key are required."
            )

        cls.schema_name = f"test_customer_{secrets.token_hex(8)}"
        if re.fullmatch(r"test_customer_[a-f0-9]{16}", cls.schema_name) is None:
            raise RuntimeError("Temporary test schema name is invalid.")
        cls.original_database_url = database_url
        cls.test_database_url = make_conninfo(
            database_url,
            options=f"-c search_path={cls.schema_name}",
        )
        cls.settings = Settings(
            telegram_bot_token="test-token-not-used",
            database_url=cls.test_database_url,
            customer_data_encryption_key=base64.b64decode(
                key_text,
                altchars=b"-_",
                validate=True,
            ),
            telegram_admin_ids=frozenset({FAKE_ADMIN_ID}),
        )
        asyncio.run(cls._create_isolated_test_schema())

    @classmethod
    async def _create_isolated_test_schema(cls) -> None:
        async with await psycopg.AsyncConnection.connect(
            cls.original_database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(f'CREATE SCHEMA "{cls.schema_name}"')

        statements = (
            """
            CREATE TABLE customer_registrations (
                user_key BYTEA PRIMARY KEY,
                encrypted_payload BYTEA NOT NULL,
                tracking_code VARCHAR(32) UNIQUE,
                phone_lookup_key BYTEA,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX customer_registrations_phone_lookup_idx
            ON customer_registrations(phone_lookup_key)
            """,
            """
            CREATE TABLE customer_tracking_code_counters (
                id INTEGER PRIMARY KEY,
                last_number BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE customer_tracking_code_audit (
                id BIGSERIAL PRIMARY KEY,
                user_key BYTEA NOT NULL REFERENCES customer_registrations(user_key)
                    ON DELETE CASCADE,
                old_tracking_code VARCHAR(32),
                new_tracking_code VARCHAR(32) NOT NULL UNIQUE,
                changed_by_telegram_id BIGINT,
                actor_type VARCHAR(16) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE customer_language_preferences (
                user_key BYTEA PRIMARY KEY,
                language_code VARCHAR(2) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
        )
        async with await psycopg.AsyncConnection.connect(
            cls.test_database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                for statement in statements:
                    await cursor.execute(statement)

    @classmethod
    def tearDownClass(cls) -> None:
        if not hasattr(cls, "schema_name"):
            return
        asyncio.run(cls._drop_isolated_test_schema())

    @classmethod
    async def _drop_isolated_test_schema(cls) -> None:
        async with await psycopg.AsyncConnection.connect(
            cls.original_database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(f'DROP SCHEMA "{cls.schema_name}" CASCADE')

    async def asyncSetUp(self) -> None:
        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    TRUNCATE TABLE customer_language_preferences,
                                   customer_tracking_code_audit,
                                   customer_registrations,
                                   customer_tracking_code_counters
                    RESTART IDENTITY CASCADE
                    """
                )

        self.photo = _fake_png()
        self.telegram_user_id = self._fake_telegram_id()
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    async def asyncTearDown(self) -> None:
        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    TRUNCATE TABLE customer_language_preferences,
                                   customer_tracking_code_audit,
                                   customer_registrations,
                                   customer_tracking_code_counters
                    RESTART IDENTITY CASCADE
                    """
                )
                await cursor.execute(
                    "SELECT COUNT(*) FROM customer_registrations"
                )
                row = await cursor.fetchone()
        self.assertEqual(row[0], 0)

    @staticmethod
    def _fake_telegram_id() -> int:
        return 1_000_000_000_000_000_000 + secrets.randbelow(
            900_000_000_000_000_000
        )

    async def _save_fake_customer(
        self,
        telegram_user_id: int,
        phone_number: str = FAKE_PHONE_NUMBER,
    ) -> tuple[bytes, str]:
        user_key, encrypted_payload = encrypt_customer_registration(
            master_key=self.settings.customer_data_encryption_key,
            telegram_user_id=telegram_user_id,
            phone_number=phone_number,
            passport_number=FAKE_PASSPORT_NUMBER,
            photo=self.photo,
            photo_mime_type="image/png",
        )
        phone_lookup = customer_phone_lookup_key(
            self.settings.customer_data_encryption_key,
            phone_number,
        )
        tracking_code = await save_customer_registration(
            self.settings.database_url,
            user_key,
            encrypted_payload,
            phone_lookup,
        )
        return user_key, tracking_code

    async def _select_language(self, language: str) -> SimpleNamespace:
        chat = SimpleNamespace(type=ChatType.PRIVATE)
        start_message = SimpleNamespace(reply_text=AsyncMock())
        start_update = SimpleNamespace(
            effective_message=start_message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        await main.start(start_update, self.context, self.settings)

        language_buttons = (
            start_message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
        )
        self.assertEqual(language_buttons[0][0].text, "🇺🇿 O‘zbek tili")
        self.assertEqual(language_buttons[1][0].text, "🇷🇺 Русский язык")

        menu_message = SimpleNamespace(reply_text=AsyncMock())
        language_query = SimpleNamespace(
            answer=AsyncMock(),
            data=f"language:{language}",
            message=menu_message,
        )
        language_update = SimpleNamespace(
            callback_query=language_query,
            effective_message=menu_message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        await main.select_language(language_update, self.context, self.settings)
        self.assertEqual(self.context.user_data["language_code"], language)
        self.assertEqual(
            await get_customer_language(
                self.settings.database_url,
                customer_user_key(
                    self.settings.customer_data_encryption_key,
                    self.telegram_user_id,
                ),
            ),
            language,
        )
        return menu_message

    async def test_registration_assigns_code_and_stores_encrypted_data(self) -> None:
        chat = SimpleNamespace(type=ChatType.PRIVATE)
        menu_message = await self._select_language("uz")
        registration_button = (
            menu_message.reply_text.await_args.kwargs["reply_markup"]
            .inline_keyboard[0][0]
        )
        self.assertEqual(
            registration_button.callback_data,
            "customer_registration:start",
        )

        registration_message = SimpleNamespace(reply_text=AsyncMock())
        callback_query = SimpleNamespace(
            answer=AsyncMock(),
            data="customer_registration:start",
            message=registration_message,
        )
        callback_update = SimpleNamespace(
            callback_query=callback_query,
            effective_chat=chat,
        )
        state = await main.begin_registration(callback_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PHONE)
        self.assertEqual(
            registration_message.reply_text.await_args.args[0],
            "Telefon raqamingizni quyidagi tugma orqali ulashing.",
        )
        contact_button = (
            registration_message.reply_text.await_args.kwargs["reply_markup"]
            .keyboard[0][0]
        )
        self.assertTrue(contact_button.request_contact)

        unowned_contact_message = SimpleNamespace(
            contact=SimpleNamespace(
                user_id=self.telegram_user_id + 1,
                phone_number=FAKE_PHONE_NUMBER,
            ),
            reply_text=AsyncMock(),
        )
        unowned_contact_update = SimpleNamespace(
            effective_message=unowned_contact_message,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        state = await main.receive_phone(unowned_contact_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PHONE)
        self.assertNotIn("customer_registration", self.context.user_data)

        phone_message = SimpleNamespace(
            contact=SimpleNamespace(
                user_id=self.telegram_user_id,
                phone_number=FAKE_PHONE_NUMBER,
            ),
            reply_text=AsyncMock(),
        )
        phone_update = SimpleNamespace(
            effective_message=phone_message,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        state = await main.receive_phone(phone_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PASSPORT)
        self.assertIn(
            "Pasport seriyasi va raqamini kiriting",
            phone_message.reply_text.await_args.args[0],
        )
        self.assertEqual(
            self.context.user_data["customer_registration"]["phone_number"],
            FAKE_NORMALIZED_PHONE,
        )

        invalid_passport_message = SimpleNamespace(
            text="A12345678",
            reply_text=AsyncMock(),
        )
        invalid_passport_update = SimpleNamespace(
            effective_message=invalid_passport_message,
        )
        state = await main.receive_passport(invalid_passport_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PASSPORT)
        self.assertIn(
            "Format noto‘g‘ri",
            invalid_passport_message.reply_text.await_args.args[0],
        )
        self.assertNotIn(
            "passport_number",
            self.context.user_data["customer_registration"],
        )

        passport_message = SimpleNamespace(
            text=FAKE_PASSPORT_NUMBER,
            reply_text=AsyncMock(),
        )
        passport_update = SimpleNamespace(effective_message=passport_message)
        state = await main.receive_passport(passport_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PHOTO)
        self.assertIn(
            "Pasportingizning",
            passport_message.reply_text.await_args.args[0],
        )

        remote_file = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(self.photo))
        )
        self.context.bot.get_file = AsyncMock(return_value=remote_file)
        photo_message = SimpleNamespace(
            photo=[
                SimpleNamespace(
                    file_id="synthetic-test-photo",
                    file_size=len(self.photo),
                )
            ],
            document=None,
            reply_text=AsyncMock(),
        )
        photo_update = SimpleNamespace(
            effective_message=photo_message,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        state = await main.receive_photo(photo_update, self.context, self.settings)

        self.assertEqual(state, ConversationHandler.END)
        success_message = photo_message.reply_text.await_args.args[0]
        self.assertIn("Ro‘yxatdan o‘tish yakunlandi", success_message)
        self.assertIn("UZ10001", success_message)
        self.context.bot.get_file.assert_awaited_once()
        self.assertEqual(self.context.user_data, {"language_code": "uz"})

        user_key, encrypted_payload = await self._saved_payload()
        self.assertNotIn(FAKE_NORMALIZED_PHONE.encode(), encrypted_payload)
        self.assertNotIn(FAKE_PASSPORT_NUMBER.encode(), encrypted_payload)
        self.assertNotIn(self.photo, encrypted_payload)
        saved_registration = decrypt_customer_registration(
            self.settings.customer_data_encryption_key,
            user_key,
            encrypted_payload,
        )
        self.assertTrue(
            saved_registration.get("phone_number") == FAKE_NORMALIZED_PHONE
        )
        self.assertTrue(
            saved_registration.get("passport_number") == FAKE_PASSPORT_NUMBER
        )
        self.assertEqual(saved_registration.get("telegram_user_id"), self.telegram_user_id)
        self.assertEqual(saved_registration.get("passport_photo_mime_type"), "image/png")
        self.assertEqual(
            base64.b64decode(saved_registration["passport_photo"]),
            self.photo,
        )

        record = await find_customer_by_tracking_code(
            self.settings.database_url,
            "UZ10001",
        )
        self.assertIsNotNone(record)
        self.assertEqual(
            record.phone_lookup_key,
            customer_phone_lookup_key(
                self.settings.customer_data_encryption_key,
                FAKE_PHONE_NUMBER,
            ),
        )
        history = await get_customer_tracking_code_history(
            self.settings.database_url,
            "UZ10001",
        )
        self.assertEqual(len(history), 1)
        registration_payload_before_language_change = (
            await self._saved_payload()
        )[1]

        settings_message = SimpleNamespace(reply_text=AsyncMock())
        settings_open_update = SimpleNamespace(
            callback_query=SimpleNamespace(
                answer=AsyncMock(),
                data="settings:open",
                message=settings_message,
            ),
            effective_message=settings_message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        await main.handle_settings_action(
            settings_open_update,
            self.context,
            self.settings,
        )
        self.assertIn("Sozlamalar", settings_message.reply_text.await_args.args[0])
        change_language_button = (
            settings_message.reply_text.await_args.kwargs["reply_markup"]
            .inline_keyboard[0][0]
        )
        self.assertEqual(change_language_button.text, "Tilni o‘zgartirish")

        language_settings_message = SimpleNamespace(reply_text=AsyncMock())
        language_settings_update = SimpleNamespace(
            callback_query=SimpleNamespace(
                answer=AsyncMock(),
                data="settings:language",
                message=language_settings_message,
            ),
            effective_message=language_settings_message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        await main.handle_settings_action(
            language_settings_update,
            self.context,
            self.settings,
        )
        language_menu = (
            language_settings_message.reply_text.await_args.kwargs["reply_markup"]
            .inline_keyboard
        )
        self.assertEqual(language_menu[0][0].text, "🇺🇿 O‘zbek tili")
        self.assertEqual(language_menu[1][0].text, "🇷🇺 Русский язык")

        russian_message = SimpleNamespace(reply_text=AsyncMock())
        russian_update = SimpleNamespace(
            callback_query=SimpleNamespace(
                answer=AsyncMock(),
                data="language:ru",
                message=russian_message,
            ),
            effective_message=russian_message,
            effective_chat=chat,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        await main.select_language(russian_update, self.context, self.settings)
        self.assertIn("Для регистрации потребуется", russian_message.reply_text.await_args.args[0])
        self.assertEqual(
            await get_customer_language(
                self.settings.database_url,
                customer_user_key(
                    self.settings.customer_data_encryption_key,
                    self.telegram_user_id,
                ),
            ),
            "ru",
        )
        self.assertEqual(
            (await self._saved_payload())[1],
            registration_payload_before_language_change,
        )
        self.assertIsNone(history[0].old_tracking_code)
        self.assertEqual(history[0].actor_type, "registration")

        _, repeated_code = await self._save_fake_customer(self.telegram_user_id)
        self.assertEqual(repeated_code, "UZ10001")
        history = await get_customer_tracking_code_history(
            self.settings.database_url,
            "UZ10001",
        )
        self.assertEqual(len(history), 1)

    async def _saved_payload(self) -> tuple[bytes, bytes]:
        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT user_key, encrypted_payload
                    FROM customer_registrations
                    """
                )
                row = await cursor.fetchone()
        self.assertIsNotNone(row)
        return bytes(row[0]), bytes(row[1])

    async def test_russian_registration_steps_and_confirmation(self) -> None:
        chat = SimpleNamespace(type=ChatType.PRIVATE)
        menu_message = await self._select_language("ru")
        self.assertIn(
            "Для регистрации потребуется",
            menu_message.reply_text.await_args.args[0],
        )

        fresh_context = SimpleNamespace(user_data={}, bot=SimpleNamespace())
        persisted_start_message = SimpleNamespace(reply_text=AsyncMock())
        await main.start(
            SimpleNamespace(
                effective_message=persisted_start_message,
                effective_chat=chat,
                effective_user=SimpleNamespace(id=self.telegram_user_id),
            ),
            fresh_context,
            self.settings,
        )
        self.assertEqual(
            persisted_start_message.reply_text.await_args.args[0],
            "Выберите язык:",
        )
        self.assertEqual(fresh_context.user_data["language_code"], "ru")

        register_button = (
            menu_message.reply_text.await_args.kwargs["reply_markup"]
            .inline_keyboard[0][0]
        )
        self.assertEqual(register_button.text, "Регистрация")

        registration_message = SimpleNamespace(reply_text=AsyncMock())
        callback_update = SimpleNamespace(
            callback_query=SimpleNamespace(
                answer=AsyncMock(),
                data="customer_registration:start",
                message=registration_message,
            ),
            effective_chat=chat,
        )
        state = await main.begin_registration(callback_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PHONE)
        self.assertIn(
            "Поделитесь своим номером",
            registration_message.reply_text.await_args.args[0],
        )
        self.assertEqual(
            registration_message.reply_text.await_args.kwargs["reply_markup"]
            .keyboard[0][0]
            .text,
            "Поделиться моим номером",
        )

        phone_message = SimpleNamespace(
            contact=SimpleNamespace(
                user_id=self.telegram_user_id,
                phone_number=FAKE_PHONE_NUMBER,
            ),
            reply_text=AsyncMock(),
        )
        phone_update = SimpleNamespace(
            effective_message=phone_message,
            effective_user=SimpleNamespace(id=self.telegram_user_id),
        )
        state = await main.receive_phone(phone_update, self.context)
        self.assertEqual(state, main.WAITING_FOR_PASSPORT)
        self.assertIn(
            "Введите серию и номер паспорта",
            phone_message.reply_text.await_args.args[0],
        )

        invalid_passport_message = SimpleNamespace(
            text="AB123",
            reply_text=AsyncMock(),
        )
        state = await main.receive_passport(
            SimpleNamespace(effective_message=invalid_passport_message),
            self.context,
        )
        self.assertEqual(state, main.WAITING_FOR_PASSPORT)
        self.assertIn(
            "Неверный формат",
            invalid_passport_message.reply_text.await_args.args[0],
        )

        passport_message = SimpleNamespace(
            text=FAKE_PASSPORT_NUMBER,
            reply_text=AsyncMock(),
        )
        state = await main.receive_passport(
            SimpleNamespace(effective_message=passport_message),
            self.context,
        )
        self.assertEqual(state, main.WAITING_FOR_PHOTO)
        self.assertIn(
            "Загрузите чёткую фотографию",
            passport_message.reply_text.await_args.args[0],
        )

        remote_file = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(self.photo))
        )
        self.context.bot.get_file = AsyncMock(return_value=remote_file)
        photo_message = SimpleNamespace(
            photo=[
                SimpleNamespace(
                    file_id="synthetic-russian-test-photo",
                    file_size=len(self.photo),
                )
            ],
            document=None,
            reply_text=AsyncMock(),
        )
        state = await main.receive_photo(
            SimpleNamespace(
                effective_message=photo_message,
                effective_user=SimpleNamespace(id=self.telegram_user_id),
            ),
            self.context,
            self.settings,
        )
        self.assertEqual(state, ConversationHandler.END)
        self.assertIn(
            "Регистрация завершена",
            photo_message.reply_text.await_args.args[0],
        )
        self.assertIn("UZ10001", photo_message.reply_text.await_args.args[0])

    async def test_admin_search_change_history_and_duplicate_protection(self) -> None:
        first_customer_id = self._fake_telegram_id()
        second_customer_id = self._fake_telegram_id()
        _, first_code = await self._save_fake_customer(first_customer_id)
        second_user_key, second_code = await self._save_fake_customer(
            second_customer_id,
            phone_number="+15555550124",
        )
        await save_customer_language(
            self.settings.database_url,
            second_user_key,
            "ru",
        )
        self.assertEqual(first_code, "UZ10001")
        self.assertEqual(second_code, "UZ10002")

        admin_message = SimpleNamespace(reply_text=AsyncMock())
        authorized_update = SimpleNamespace(
            effective_message=admin_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=FAKE_ADMIN_ID),
        )
        search_context = SimpleNamespace(args=[FAKE_PHONE_NUMBER])
        await admin.search_customers(
            authorized_update,
            search_context,
            self.settings,
        )
        search_response = admin_message.reply_text.await_args.args[0]
        self.assertIn(first_code, search_response)
        self.assertNotIn(FAKE_PHONE_NUMBER, search_response)
        self.assertNotIn(FAKE_PASSPORT_NUMBER, search_response)

        search_by_code_message = SimpleNamespace(reply_text=AsyncMock())
        search_by_code_update = SimpleNamespace(
            effective_message=search_by_code_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=FAKE_ADMIN_ID),
        )
        await admin.search_customers(
            search_by_code_update,
            SimpleNamespace(args=[second_code]),
            self.settings,
        )
        self.assertIn(second_code, search_by_code_message.reply_text.await_args.args[0])

        unauthorized_message = SimpleNamespace(reply_text=AsyncMock())
        unauthorized_update = SimpleNamespace(
            effective_message=unauthorized_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=FAKE_ADMIN_ID + 1),
        )
        await admin.change_tracking_code(
            unauthorized_update,
            SimpleNamespace(args=[first_code, "UZ50000"]),
            self.settings,
        )
        self.assertIn(
            "restricted to authorized admins",
            unauthorized_message.reply_text.await_args.args[0],
        )

        notification_bot = SimpleNamespace(send_message=AsyncMock())
        change_admin_message = SimpleNamespace(reply_text=AsyncMock())
        change_admin_update = SimpleNamespace(
            effective_message=change_admin_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=FAKE_ADMIN_ID),
        )
        first_notification_bot = SimpleNamespace(send_message=AsyncMock())
        await admin.change_tracking_code(
            change_admin_update,
            SimpleNamespace(
                args=[first_code, "UZ50000"],
                bot=first_notification_bot,
            ),
            self.settings,
        )
        self.assertIn(
            "customer was notified",
            change_admin_message.reply_text.await_args.args[0],
        )
        first_notification_bot.send_message.assert_awaited_once()
        self.assertEqual(
            first_notification_bot.send_message.await_args.kwargs["chat_id"],
            first_customer_id,
        )

        notification_context = SimpleNamespace(
            args=[second_code, "UZ60000"],
            bot=notification_bot,
        )
        # The first customer already changed to UZ50000; set the second customer code.
        await admin.change_tracking_code(
            change_admin_update,
            notification_context,
            self.settings,
        )
        notification_bot.send_message.assert_awaited_once()
        self.assertEqual(
            notification_bot.send_message.await_args.kwargs["chat_id"],
            second_customer_id,
        )
        self.assertIn(
            "UZ60000",
            notification_bot.send_message.await_args.kwargs["text"],
        )
        self.assertIn(
            "Ваш новый код отслеживания",
            notification_bot.send_message.await_args.kwargs["text"],
        )
        self.assertNotIn(
            FAKE_PASSPORT_NUMBER,
            notification_bot.send_message.await_args.kwargs["text"],
        )

        duplicate_admin_message = SimpleNamespace(reply_text=AsyncMock())
        duplicate_admin_update = SimpleNamespace(
            effective_message=duplicate_admin_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=FAKE_ADMIN_ID),
        )
        await admin.change_tracking_code(
            duplicate_admin_update,
            SimpleNamespace(args=["UZ50000", "UZ60000"]),
            self.settings,
        )
        self.assertIn(
            "already assigned or was used before",
            duplicate_admin_message.reply_text.await_args.args[0],
        )
        self.assertEqual(
            (await find_customer_by_tracking_code(
                self.settings.database_url,
                "UZ50000",
            )).tracking_code,
            "UZ50000",
        )

        history_message = SimpleNamespace(reply_text=AsyncMock())
        history_update = SimpleNamespace(
            effective_message=history_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=FAKE_ADMIN_ID),
        )
        await admin.show_tracking_code_history(
            history_update,
            SimpleNamespace(args=["UZ50000"]),
            self.settings,
        )
        history_response = history_message.reply_text.await_args.args[0]
        self.assertIn("new registration", history_response)
        self.assertIn("UZ50000", history_response)
        self.assertIn("admin", history_response)

        next_customer_id = self._fake_telegram_id()
        _, next_code = await self._save_fake_customer(next_customer_id)
        self.assertEqual(next_code, "UZ60001")

    async def test_concurrent_registrations_receive_distinct_codes(self) -> None:
        user_ids = [self._fake_telegram_id() for _ in range(8)]
        saved = await asyncio.gather(
            *(self._save_fake_customer(user_id) for user_id in user_ids)
        )
        codes = [tracking_code for _, tracking_code in saved]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(set(codes), {f"UZ{number:05d}" for number in range(10001, 10009)})


if __name__ == "__main__":
    unittest.main()
