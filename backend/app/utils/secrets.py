"""Encryption at rest for third-party secrets a host hands us (their Razorpay
key secret). Fernet (AES-128-CBC + HMAC), keyed by PAYMENT_ENCRYPTION_KEY.

Never logs or returns plaintext, and there is no fallback to storing it
unencrypted: with no key configured, `encrypt` refuses.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings


class SecretsError(Exception):
    """Safe to show the host."""


def _fernet() -> MultiFernet:
    raw = (settings.payment_encryption_key or "").strip()
    if not raw:
        raise SecretsError("Payments aren't enabled on this server yet. Ask support to finish setting them up.")
    try:
        return MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])
    except (ValueError, TypeError):
        raise SecretsError("Payments aren't set up correctly on this server. Ask support to check the encryption key.")


def is_configured() -> bool:
    try:
        _fernet()
        return True
    except SecretsError:
        return False


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretsError("The saved payment key can't be read any more. Please enter your Razorpay keys again.")
