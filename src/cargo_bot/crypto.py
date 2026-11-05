import base64
import hashlib
import hmac
import json
import re
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _derive_key(master_key: bytes, purpose: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"telegram-cargo-bot/" + purpose,
    ).derive(master_key)


def normalize_phone_number(phone_number: str) -> str:
    compact = re.sub(r"[\s().-]", "", phone_number.strip())
    if compact.startswith("+"):
        compact = compact[1:]
    if (
        not compact.isascii()
        or not compact.isdigit()
        or not 7 <= len(compact) <= 15
    ):
        raise ValueError("Phone number must contain 7 to 15 digits.")
    return compact


def customer_phone_lookup_key(master_key: bytes, phone_number: str) -> bytes:
    lookup_key = _derive_key(master_key, b"customer-phone-lookup-v1")
    normalized_phone = normalize_phone_number(phone_number)
    return hmac.new(
        lookup_key,
        normalized_phone.encode("ascii"),
        hashlib.sha256,
    ).digest()


def customer_user_key(master_key: bytes, telegram_user_id: int) -> bytes:
    lookup_key = _derive_key(master_key, b"customer-lookup-v1")
    return hmac.new(
        lookup_key,
        str(telegram_user_id).encode("ascii"),
        hashlib.sha256,
    ).digest()


def encrypt_customer_registration(
    master_key: bytes,
    telegram_user_id: int,
    phone_number: str,
    passport_number: str,
    photo: bytes,
    photo_mime_type: str,
) -> tuple[bytes, bytes]:
    """Return a pseudonymous lookup key and an authenticated encrypted payload."""
    user_key = customer_user_key(master_key, telegram_user_id)

    payload = json.dumps(
        {
            "phone_number": normalize_phone_number(phone_number),
            "passport_number": passport_number,
            "passport_photo": base64.b64encode(photo).decode("ascii"),
            "passport_photo_mime_type": photo_mime_type,
            "telegram_user_id": telegram_user_id,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    encryption_key = _derive_key(master_key, b"customer-record-v1")
    nonce = secrets.token_bytes(12)
    encrypted_payload = nonce + AESGCM(encryption_key).encrypt(
        nonce,
        payload,
        user_key,
    )
    return user_key, encrypted_payload


def decrypt_customer_registration(
    master_key: bytes,
    user_key: bytes,
    encrypted_payload: bytes,
) -> dict[str, object]:
    encryption_key = _derive_key(master_key, b"customer-record-v1")
    plaintext = AESGCM(encryption_key).decrypt(
        encrypted_payload[:12],
        encrypted_payload[12:],
        user_key,
    )
    registration = json.loads(plaintext)
    if not isinstance(registration, dict):
        raise ValueError("Encrypted customer registration is invalid.")
    return registration
