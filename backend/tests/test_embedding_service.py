from app.integrations import redis_client
from app.services import embedding_service


class _FakeRedis:
    """Same in-memory fake as tests/test_redis_client.py -- avoids needing
    a real Redis server just to test the cache-first branch in
    get_embedding."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value


def _install_fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "_client", fake)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    return fake


def _fake_openai_client(monkeypatch, embedding: list[float], calls: list[str]):
    """Monkeypatches the AsyncOpenAI import get_embedding does internally --
    counts how many times the embeddings API is actually hit, so tests can
    assert a cache hit avoided a second call."""

    class _FakeEmbeddingsResource:
        async def create(self, model, input):
            calls.append(input)

            class _Data:
                pass

            class _Response:
                data = [_Data()]

            _Response.data[0].embedding = embedding
            return _Response()

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.embeddings = _FakeEmbeddingsResource()

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeClient)


async def test_get_embedding_returns_none_without_openrouter_key(monkeypatch):
    monkeypatch.setattr(embedding_service.settings, "openrouter_api_key", None)
    assert await embedding_service.get_embedding("romantic getaway") is None


async def test_get_embedding_caches_across_calls(monkeypatch):
    """Cost Optimization ("Phase 16"): a second call for the exact same
    text must hit the Redis cache, not the embeddings API again."""
    monkeypatch.setattr(embedding_service.settings, "openrouter_api_key", "test-key")
    _install_fake_redis(monkeypatch)
    calls: list[str] = []
    _fake_openai_client(monkeypatch, [0.1, 0.2, 0.3], calls)

    first = await embedding_service.get_embedding("romantic getaway")
    second = await embedding_service.get_embedding("romantic getaway")

    assert first == [0.1, 0.2, 0.3]
    assert second == [0.1, 0.2, 0.3]
    assert len(calls) == 1  # the second call was served from cache


async def test_get_embedding_cache_key_normalizes_case_and_whitespace(monkeypatch):
    """A guest/model phrasing the same intent with different casing or
    incidental whitespace ("Romantic Getaway" vs "romantic getaway ") must
    still hit the same cache entry -- the embedding captures meaning, not
    surface formatting."""
    monkeypatch.setattr(embedding_service.settings, "openrouter_api_key", "test-key")
    _install_fake_redis(monkeypatch)
    calls: list[str] = []
    _fake_openai_client(monkeypatch, [0.4, 0.5, 0.6], calls)

    await embedding_service.get_embedding("romantic getaway")
    await embedding_service.get_embedding("  Romantic   Getaway  ")

    assert len(calls) == 1


async def test_get_embedding_different_text_is_not_a_cache_hit(monkeypatch):
    monkeypatch.setattr(embedding_service.settings, "openrouter_api_key", "test-key")
    _install_fake_redis(monkeypatch)
    calls: list[str] = []
    _fake_openai_client(monkeypatch, [0.1, 0.2, 0.3], calls)

    await embedding_service.get_embedding("romantic getaway")
    await embedding_service.get_embedding("workcation")

    assert len(calls) == 2


async def test_get_embedding_still_works_when_redis_not_configured(monkeypatch):
    """No REDIS_URL (the pre-existing default) must behave exactly as
    before this cache was added -- every call reaches the live API."""
    monkeypatch.setattr(embedding_service.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(redis_client, "_client", None)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    calls: list[str] = []
    _fake_openai_client(monkeypatch, [0.7, 0.8, 0.9], calls)

    first = await embedding_service.get_embedding("romantic getaway")
    second = await embedding_service.get_embedding("romantic getaway")

    assert first == [0.7, 0.8, 0.9]
    assert second == [0.7, 0.8, 0.9]
    assert len(calls) == 2  # no cache available -- both calls hit the API


async def test_get_embedding_failure_is_not_cached(monkeypatch):
    """A failed embedding call must return None and must not poison the
    cache with a failure -- the next call should retry live, not keep
    returning None forever from a cached non-result."""
    monkeypatch.setattr(embedding_service.settings, "openrouter_api_key", "test-key")
    fake = _install_fake_redis(monkeypatch)

    import openai

    class _RaisingClient:
        def __init__(self, *args, **kwargs):
            pass

        class embeddings:
            @staticmethod
            async def create(model, input):
                raise ConnectionError("simulated outage")

    monkeypatch.setattr(openai, "AsyncOpenAI", _RaisingClient)

    result = await embedding_service.get_embedding("romantic getaway")

    assert result is None
    assert fake.store == {}


def test_embedding_cache_key_is_stable_and_model_scoped():
    key1 = embedding_service._embedding_cache_key("romantic getaway")
    key2 = embedding_service._embedding_cache_key("romantic getaway")
    assert key1 == key2
    assert embedding_service.EMBEDDING_MODEL in key1
