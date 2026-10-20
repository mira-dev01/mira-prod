from app.api.v1 import voice


def test_ice_servers_stun_only_by_default(monkeypatch):
    monkeypatch.setattr(voice.settings, "turn_url", None)

    servers = voice._ice_servers()

    assert len(servers) == 2
    assert all(s.urls.startswith("stun:") for s in servers)


def test_ice_servers_includes_turn_when_configured(monkeypatch):
    # Regression: STUN alone only helps discover each side's public address
    # -- it doesn't relay media, and real testing on Render showed ICE
    # timing out entirely without a TURN relay. When turn_url is set, it
    # must be included alongside STUN, with credentials attached.
    monkeypatch.setattr(voice.settings, "turn_url", "turn:turn.example.com:443")
    monkeypatch.setattr(voice.settings, "turn_username", "user1")
    monkeypatch.setattr(voice.settings, "turn_credential", "secret1")

    servers = voice._ice_servers()

    turn_servers = [s for s in servers if s.urls.startswith("turn:")]
    assert len(turn_servers) == 1
    assert turn_servers[0].urls == "turn:turn.example.com:443"
    assert turn_servers[0].username == "user1"
    assert turn_servers[0].credential == "secret1"
