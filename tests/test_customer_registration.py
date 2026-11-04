import base64
import json
import os
import secrets
import struct
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from telegram.constants import ChatType
from telegram.ext import ConversationHandler

from cargo_bot import main
from cargo_bot.config import Settings
from cargo_bot.crypto import _derive_key, encrypt_customer_registration


FAKE_PHONE_NUMBER = "+15555550123"
FAKE_PASSPORT_NUMBER = "AB1234567"


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


class CustomerRegistrationFlowTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database_url = os.environ.get("DATABASE_URL", "")
        key_text = os.environ.get("CUSTOMER_DATA_ENCRYPTION_KEY", "")
        if not database_url or not key_text:
            raise unittest.SkipTest(
                "Development database and encryption key are required."
            )

        cls.settings = Settings(
            telegram_bot_token="test-token-not-used",
            database_url=database_url,
            customer_data_encryption_key=base64.b64decode(
                key_text,
                altchars=b"-_",
                validate=True,
            ),
        )

    async def asyncSetUp(self) -> None:
        self.photo = _fake_png()
        self.telegram_user_id = 0
        self.user_key = b""

        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                while True:
                    self.telegram_user_id = (
                        1_000_000_000_000_000_000 + secrets.randbelow(900_000_000_000_000_000)
                    )
                    self.user_key, _ = encrypt_customer_registration(
                        master_key=self.settings.customer_data_encryption_key,
                        telegram_user_id=self.telegram_user_id,
                        phone_number=FAKE_PHONE_NUMBER,
                        passport_number=FAKE_PASSPORT_NUMBER,
                        photo=self.photo,
                        photo_mime_type="image/png",
                    )
                    await cursor.execute(
                        "SELECT 1 FROM customer_registrations WHERE user_key = %s",
                        (self.user_key,),
                    )
                    if await cursor.fetchone() is None:
                        break

        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    async def asyncTearDown(self) -> None:
        if not self.user_key:
            return
        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM customer_registrations WHERE user_key = %s",
                    (self.user_key,),
                )
                await cursor.execute(
                    "SELECT 1 FROM customer_registrations WHERE user_key = %s",
                    (self.user_key,),
                )
                self.assertIsNone(await cursor.fetchone())

    async def test_complete_registration_saves_only_encrypted_data(self) -> None:
        chat = SimpleNamespace(type=ChatType.PRIVATE)

        start_message = SimpleNamespace(reply_text=AsyncMock())
        start_update = SimpleNamespace(effective_message=start_message, effective_chat=chat)
        state = await main.start(start_update, self.context)

        self.assertEqual(state, ConversationHandler.END)
        registration_button = (
            start_message.reply_text.await_args.kwargs["reply_markup"]
            .inline_keyboard[0][0]
        )
        self.assertEqual(
            registration_button.callback_data,
            "customer_registration:start",
        )

        registration_message = SimpleNamespace(reply_text=AsyncMock())
        callback_query = SimpleNamespace(
            answer=AsyncMock(),
            message=registration_message,
        )
        callback_update = SimpleNamespace(
            callback_query=callback_query,
            effective_chat=chat,
        )
        state = await main.begin_registration(callback_update, self.context)

        self.assertEqual(state, main.WAITING_FOR_PHONE)
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
        self.assertTrue(
            self.context.user_data["customer_registration"]["phone_number"]
            == FAKE_PHONE_NUMBER
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
        self.assertTrue(
            self.context.user_data["customer_registration"]["passport_number"]
            == FAKE_PASSPORT_NUMBER
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
        self.assertIn("registration is complete", success_message)
        self.assertEqual(self.context.user_data, {})
        self.context.bot.get_file.assert_awaited_once()

        async with await psycopg.AsyncConnection.connect(
            self.settings.database_url,
            connect_timeout=5,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT encrypted_payload
                    FROM customer_registrations
                    WHERE user_key = %s
                    """,
                    (self.user_key,),
                )
                row = await cursor.fetchone()

        self.assertIsNotNone(row)
        encrypted_payload = bytes(row[0])
        self.assertGreater(len(encrypted_payload), 12)
        self.assertNotIn(FAKE_PHONE_NUMBER.encode(), encrypted_payload)
        self.assertNotIn(FAKE_PASSPORT_NUMBER.encode(), encrypted_payload)
        self.assertNotIn(self.photo, encrypted_payload)

        encryption_key = _derive_key(
            self.settings.customer_data_encryption_key,
            b"customer-record-v1",
        )
        decrypted_payload = AESGCM(encryption_key).decrypt(
            encrypted_payload[:12],
            encrypted_payload[12:],
            self.user_key,
        )
        saved_registration = json.loads(decrypted_payload)
        self.assertTrue(
            saved_registration.get("phone_number") == FAKE_PHONE_NUMBER
        )
        self.assertTrue(
            saved_registration.get("passport_number") == FAKE_PASSPORT_NUMBER
        )
        self.assertEqual(saved_registration.get("passport_photo_mime_type"), "image/png")
        self.assertEqual(
            base64.b64decode(saved_registration["passport_photo"]),
            self.photo,
        )


if __name__ == "__main__":
    unittest.main()
