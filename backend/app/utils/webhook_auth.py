import hmac


def verify_shared_secret_token(token: str | None, expected: str) -> bool:
    """Shared-secret path/query-param token check used by every inbound
    webhook this codebase has (Exotel call-status, Exotel voice websocket,
    Twilio voice, Twilio WhatsApp) -- none of these providers sign callbacks
    the way a real HMAC-signature scheme would, so a shared secret embedded
    in the callback URL itself is what actually secures them.
    Constant-time comparison (hmac.compare_digest) so a timing side-channel
    can't be used to guess the secret one byte at a time.
    """
    if not token:
        return False
    return hmac.compare_digest(token, expected)
