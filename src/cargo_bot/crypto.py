import base64
import hashlib
import hmac
import json
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


def encrypt_customer_registration(
    master_key: bytes,
    telegram_user_id: int,
    phone_number: str,
    passport_number: str,
    photo: bytes,
    photo_mime_type: str,
) -> tuple[bytes, bytes]:
    """Return a pseudonymous lookup key and an authenticated encrypted payload."""
    lookup_key = _derive_key(master_key, b"customer-lookup-v1")
    user_key = hmac.new(
        lookup_key,
        str(telegram_user_id).encode("ascii"),
        hashlib.sha256,
    ).digest()

    payload = json.dumps(
        {
            "phone_number": phone_number,
            "passport_number": passport_number,
            "passport_photo": base64.b64encode(photo).decode("ascii"),
            "passport_photo_mime_type": photo_mime_type,
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
