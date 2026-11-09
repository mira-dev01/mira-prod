import hmac

from app.config import is_placeholder_secret, settings


def verify_shared_secret_token(token: str | None, expected: str) -> bool:
    """Shared-secret path/query-param token check used by every inbound
    webhook this codebase has (Exotel call-status, Exotel voice websocket,
    Twilio voice, Twilio WhatsApp) -- none of these providers sign callbacks
    the way a real HMAC-signature scheme would, so a shared secret embedded
    in the callback URL itself is what actually secures them.
    Constant-time comparison (hmac.compare_digest, on bytes so a non-ASCII
    token can't raise) so a timing side-channel can't be used to guess the
    secret one byte at a time.

    Fails closed in production when the expected secret is still the
    "change-me" placeholder (or blank): that value is public in the repo, so
    accepting it would mean the endpoint is effectively unauthenticated.
    Outside production the placeholder still works, so local dev and tests
    need no extra configuration.
    """
    if not token:
        return False
    if settings.is_production and is_placeholder_secret(expected):
        return False
    return hmac.compare_digest(token.encode(), expected.encode())
