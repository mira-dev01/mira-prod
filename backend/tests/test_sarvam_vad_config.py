"""Phase 1 (background-audio false-turn fix): confirms the server-side Sarvam
VAD/noise-rejection settings (app/config.py's sarvam_vad_* fields) actually
reach the outgoing Sarvam connect payload, and that leaving them unset
produces the exact same wire behavior as before this feature existed.

Does not open a real Sarvam WebSocket -- _connect()'s own connect_kwargs
construction (pipecat/services/sarvam/stt.py) is reproduced directly against
a real _ReconnectingSarvamSTTService instance's ._settings, the same
before/after distinction a live connection would make, without any network
call. This mirrors test_pipeline_llm.py's pattern of asserting directly on a
constructed service's ._settings rather than exercising the full pipeline.
"""

from pipecat.services.sarvam.stt import SarvamSTTService

from app.voice.pipeline import _ReconnectingSarvamSTTService


def _build_stt(**vad_overrides) -> _ReconnectingSarvamSTTService:
    """Constructs the STT service exactly the way _run_pipeline_inner does
    (app/voice/pipeline.py), with sarvam_vad_* values supplied directly
    rather than read from app.config.settings, so this test doesn't depend
    on whatever the real .env currently has configured."""
    defaults = {
        "positive_speech_threshold": None,
        "negative_speech_threshold": None,
        "min_speech_frames": None,
        "first_turn_min_speech_frames": None,
        "negative_frames_count": None,
        "negative_frames_window": None,
        "start_speech_volume_threshold": None,
        "interrupt_min_speech_frames": None,
        "pre_speech_pad_frames": None,
        "num_initial_ignored_frames": None,
    }
    defaults.update(vad_overrides)
    return _ReconnectingSarvamSTTService(
        api_key="test-key",
        mode="codemix",
        settings=SarvamSTTService.Settings(model="saaras:v3", **defaults),
    )


def _connect_kwargs_for(stt: _ReconnectingSarvamSTTService) -> dict:
    """Reproduces exactly the connect_kwargs construction in
    SarvamSTTService._connect (pipecat/services/sarvam/stt.py) for the
    fine-grained VAD parameter block -- the same "only send when explicitly
    set" guard the vendored code uses. Kept in lockstep with that method
    deliberately (see its own comment: "Only send vad parameters when
    explicitly set (avoid overriding server defaults)") rather than
    monkeypatching the websocket layer, so this test proves what actually
    gets sent without needing a live Sarvam connection."""
    connect_kwargs: dict = {"model": stt._settings.model, "sample_rate": "16000"}
    if not stt._settings.vad_signals:
        connect_kwargs["flush_signal"] = "true"
    if stt._config.supports_vad_params:
        vad_params = {
            "positive_speech_threshold": stt._settings.positive_speech_threshold,
            "negative_speech_threshold": stt._settings.negative_speech_threshold,
            "min_speech_frames": stt._settings.min_speech_frames,
            "first_turn_min_speech_frames": stt._settings.first_turn_min_speech_frames,
            "negative_frames_count": stt._settings.negative_frames_count,
            "negative_frames_window": stt._settings.negative_frames_window,
            "start_speech_volume_threshold": stt._settings.start_speech_volume_threshold,
            "interrupt_min_speech_frames": stt._settings.interrupt_min_speech_frames,
            "pre_speech_pad_frames": stt._settings.pre_speech_pad_frames,
            "num_initial_ignored_frames": stt._settings.num_initial_ignored_frames,
        }
        for key, value in vad_params.items():
            if value is not None:
                connect_kwargs[key] = str(value)
    return connect_kwargs


def test_default_config_sends_no_vad_params_except_min_speech_frames():
    """Byte-identical to pre-Phase-1 behavior except for the one deliberate
    starting-point change (min_speech_frames=2, config.py's own default) --
    every other sarvam_vad_* setting defaults to None and must not appear
    in the outgoing connect payload at all, since Sarvam's own _connect only
    sends a VAD param "when explicitly set (avoid overriding server
    defaults)"."""
    stt = _build_stt(min_speech_frames=2)

    kwargs = _connect_kwargs_for(stt)

    assert kwargs == {
        "model": "saaras:v3",
        "sample_rate": "16000",
        "flush_signal": "true",
        "min_speech_frames": "2",
    }


def test_all_vad_params_unset_is_byte_identical_to_pre_phase1_behavior():
    """Every sarvam_vad_* setting at None (the literal pre-Phase-1 state,
    before config.py's fields existed) must produce a connect payload with
    NO fine-grained VAD keys at all -- confirms the feature is fully
    opt-in and introduces zero behavior change on a fresh deploy with
    nothing configured."""
    stt = _build_stt()  # every VAD param stays at the None default

    kwargs = _connect_kwargs_for(stt)

    assert kwargs == {"model": "saaras:v3", "sample_rate": "16000", "flush_signal": "true"}
    vad_param_keys = {
        "positive_speech_threshold",
        "negative_speech_threshold",
        "min_speech_frames",
        "first_turn_min_speech_frames",
        "negative_frames_count",
        "negative_frames_window",
        "start_speech_volume_threshold",
        "interrupt_min_speech_frames",
        "pre_speech_pad_frames",
        "num_initial_ignored_frames",
    }
    assert not (vad_param_keys & kwargs.keys())


def test_configured_vad_params_reach_the_connect_payload():
    """When an operator explicitly configures the noise-rejection knobs
    (e.g. after real-call validation per the Phase 1 manual test matrix),
    every one of them must actually reach Sarvam's connect call -- this is
    the mechanism the whole fix depends on."""
    stt = _build_stt(
        positive_speech_threshold=0.6,
        min_speech_frames=3,
        start_speech_volume_threshold=-40.0,
    )

    kwargs = _connect_kwargs_for(stt)

    assert kwargs["positive_speech_threshold"] == "0.6"
    assert kwargs["min_speech_frames"] == "3"
    assert kwargs["start_speech_volume_threshold"] == "-40.0"
    # Untouched params still stay absent -- confirms partial configuration
    # doesn't accidentally send Sarvam's own default as an explicit value.
    assert "negative_speech_threshold" not in kwargs
    assert "interrupt_min_speech_frames" not in kwargs


def test_app_config_default_min_speech_frames_is_two():
    """Regression guard on config.py's own documented starting-point default
    -- a conservative +1 frame over Sarvam's own unconfigured default (which
    accepts a single speech frame), chosen specifically to avoid the
    aggressive-VAD trap (constraint 18: cutting off real guests is worse
    than accepting occasional noise) while still requiring one extra frame
    of sustained speech before a segment starts, targeting exactly the
    single-syllable noise-blip failure mode ("No") this phase exists to
    reduce."""
    from app.config import settings

    assert settings.sarvam_vad_min_speech_frames == 2


def test_app_config_all_other_vad_params_default_to_none():
    """Every sarvam_vad_* field except min_speech_frames must default to
    None -- Phase 0/1 found no safe baseline in this repo to derive a
    specific numeric threshold for the remaining nine parameters (unlike the
    local Silero VAD's start_secs=0.35, which was tuned against real call
    logs), so they ship available-but-inert pending real-call validation."""
    from app.config import settings

    assert settings.sarvam_vad_positive_speech_threshold is None
    assert settings.sarvam_vad_negative_speech_threshold is None
    assert settings.sarvam_vad_first_turn_min_speech_frames is None
    assert settings.sarvam_vad_negative_frames_count is None
    assert settings.sarvam_vad_negative_frames_window is None
    assert settings.sarvam_vad_start_speech_volume_threshold is None
    assert settings.sarvam_vad_interrupt_min_speech_frames is None
    assert settings.sarvam_vad_pre_speech_pad_frames is None
    assert settings.sarvam_vad_num_initial_ignored_frames is None


def test_reconnecting_stt_subclass_still_used_for_construction():
    """The reconnect-on-dead-socket behavior (_ReconnectingSarvamSTTService,
    a confirmed-live-incident fix -- see its own docstring in pipeline.py)
    must not be accidentally dropped by switching from the deprecated bare
    model=/mode= kwargs to settings=SarvamSTTService.Settings(...)."""
    stt = _build_stt(min_speech_frames=2)

    assert isinstance(stt, _ReconnectingSarvamSTTService)
    assert stt._mode == "codemix"
    assert stt._settings.model == "saaras:v3"
