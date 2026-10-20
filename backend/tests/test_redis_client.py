from app.integrations import redis_client


class _FakeRedis:
    """Duck-types the redis.asyncio.Redis methods redis_client.py actually
    calls, backed by an in-memory dict -- avoids needing a real Redis
    server or an extra fakeredis dependency just to test the caching logic
    itself (get/set/TTL wiring, JSON round-trip, no-op-without-url)."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ex))


class _RaisingRedis:
    async def get(self, key: str):
        raise ConnectionError("simulated Redis outage")

    async def set(self, key: str, value: str, ex: int | None = None):
        raise ConnectionError("simulated Redis outage")


def _install_fake_client(monkeypatch, client):
    monkeypatch.setattr(redis_client, "_client", client)
    monkeypatch.setattr(redis_client, "_client_initialized", True)


async def test_cache_get_returns_none_when_redis_not_configured(monkeypatch):
    monkeypatch.setattr(redis_client, "_client", None)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    assert await redis_client.cache_get_json("some:key") is None


async def test_cache_set_is_a_noop_when_redis_not_configured(monkeypatch):
    # Must not raise even though there's nothing to write to.
    monkeypatch.setattr(redis_client, "_client", None)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    await redis_client.cache_set_json("some:key", {"total": 7808}, 3600)


async def test_cache_set_then_get_round_trips_json(monkeypatch):
    fake = _FakeRedis()
    _install_fake_client(monkeypatch, fake)

    await redis_client.cache_set_json("searchapi:listing_price:123", 7808.0, 3600)
    result = await redis_client.cache_get_json("searchapi:listing_price:123")

    assert result == 7808.0
    assert fake.set_calls == [("searchapi:listing_price:123", "7808.0", 3600)]


async def test_cache_get_returns_none_on_cache_miss(monkeypatch):
    fake = _FakeRedis()
    _install_fake_client(monkeypatch, fake)
    assert await redis_client.cache_get_json("never:set") is None


async def test_cache_get_handles_redis_connection_failure_gracefully(monkeypatch):
    _install_fake_client(monkeypatch, _RaisingRedis())
    # Must not raise -- falls through to "cache miss" so callers fetch live.
    assert await redis_client.cache_get_json("some:key") is None


async def test_cache_set_handles_redis_connection_failure_gracefully(monkeypatch):
    _install_fake_client(monkeypatch, _RaisingRedis())
    # Must not raise -- a Redis outage should never break a pricing quote.
    await redis_client.cache_set_json("some:key", 100.0, 3600)


async def test_cache_stores_list_values(monkeypatch):
    fake = _FakeRedis()
    _install_fake_client(monkeypatch, fake)

    await redis_client.cache_set_json("searchapi:comparable:Goa", [4500.0, 5200.0, 3900.0], 86400)
    result = await redis_client.cache_get_json("searchapi:comparable:Goa")

    assert result == [4500.0, 5200.0, 3900.0]


async def test_cache_get_returns_none_for_malformed_stored_value(monkeypatch):
    fake = _FakeRedis()
    fake.store["corrupt:key"] = "not valid json{{"
    _install_fake_client(monkeypatch, fake)

    assert await redis_client.cache_get_json("corrupt:key") is None
