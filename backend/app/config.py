from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    backend_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"
    # Extra origins allowed to hit the API via CORS, comma-separated (e.g.
    # "http://localhost:3000,https://staging.example.com"). Useful for hitting
    # a deployed backend from a local frontend during development. The
    # frontend_base_url above is always allowed and doesn't need to be repeated.
    cors_extra_origins: str = ""

    @property
    def cors_allowed_origins(self) -> list[str]:
        extra = [origin.strip() for origin in self.cors_extra_origins.split(",") if origin.strip()]
        return list(dict.fromkeys([self.frontend_base_url, *extra]))

    database_url: str = "postgresql+asyncpg://mira:mira@localhost:5432/mira_dev"
    # Whether DATABASE_URL asked for TLS via ?sslmode=require or ?ssl=require
    # (e.g. Neon). Derived from the raw URL by _use_asyncpg_driver below (that
    # query param is then stripped from database_url itself, since asyncpg's
    # connect() rejects an unrecognized "sslmode"/"ssl" kwarg forwarded from
    # the URL). Consumed as connect_args={"ssl": True} in database.py /
    # alembic/env.py, where asyncpg wants a bool/SSLContext, not a
    # query-string value. Render's own internal DB URL has no such param, so
    # this correctly stays False there and TLS isn't forced on it.
    database_requires_ssl: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_database_url(cls, data: dict) -> dict:
        value = data.get("database_url")
        if not value:
            return data
        # Render (and most hosts) hand out a bare postgres:// or postgresql://
        # connection string -- SQLAlchemy's async engine needs the asyncpg
        # driver named explicitly in the scheme.
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]

        parsed = urlsplit(value)
        params = parse_qsl(parsed.query)
        mode = next((v for k, v in params if k in ("sslmode", "ssl")), None)
        data["database_requires_ssl"] = mode is not None and mode not in ("disable", "false", "0")
        if parsed.query:
            remaining = [(k, v) for k, v in params if k not in ("sslmode", "ssl")]
            parsed = parsed._replace(query=urlencode(remaining))
            value = urlunsplit(parsed)

        data["database_url"] = value
        return data

    # Clerk owns auth entirely (see app/auth/dependencies.py's
    # get_current_user) -- the old bcrypt/HS256-JWT/refresh-token system this
    # replaced is gone, not just unused. clerk_secret_key verifies session
    # JWTs server-side (via JWKS, derived from clerk_publishable_key);
    # clerk_publishable_key is frontend-only but kept here too for
    # completeness/single source of truth. clerk_dev_org_id is the one Clerk
    # Organization whose members see internal-only dashboard features (e.g.
    # "Talk to Mira") -- compared directly against a verified token's active
    # org id, no extra Clerk API call needed per request.
    clerk_secret_key: str | None = None
    clerk_publishable_key: str | None = None
    clerk_dev_org_id: str | None = None

    llm_provider: Literal["groq", "anthropic", "openrouter"] = "groq"
    groq_api_key: str | None = None
    # llama-3.3-70b-versatile was deprecated by Groq on 2026-06-17;
    # openai/gpt-oss-120b is Groq's recommended replacement.
    groq_model: str = "openai/gpt-oss-120b"
    # Fallback chain tried in order when llm_provider="groq". groq_model
    # (above) stays as the configured default/first choice -- this list is
    # what _build_llm() in app/voice/pipeline.py actually walks, skipping any
    # model app/main.py's periodic health check has marked down (e.g. from a
    # 429 rate limit on gpt-oss-120b -- account is on a paid Groq plan as of
    # 2026-07-07, not free tier, but per-model limits still apply under
    # bursty call load). All three IDs
    # confirmed live via client.models.list() against this account -- Groq
    # renames/deprecates model ids periodically (see the groq_model comment
    # above re: llama-3.3-70b-versatile), so re-check before editing this
    # list. llama-3.1-8b-instant and gpt-oss-20b are on separate rate-limit
    # pools from gpt-oss-120b, so a burst that exhausts one doesn't take down
    # the others; both also support function calling, required for our tools
    # (app/voice/tools.py) -- don't add a model here without confirming that.
    groq_models: list[str] = ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    # OpenRouter (https://openrouter.ai) -- one account/balance, swap models
    # by changing openrouter_model alone (e.g. "openai/gpt-4.1",
    # "anthropic/claude-sonnet-4.6"), no new integration code per model.
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4.1"

    # Bright Data Web Scraper API (Airbnb dataset gd_ld7ll037kqy322v05) --
    # onboarding's "import from Airbnb" flow. Their Airbnb product takes
    # individual listing URLs, not a host-profile URL that returns every
    # listing -- confirmed against their docs and sample dataset (no
    # discovery-by-host mode exists for Airbnb, unlike some of their other
    # scrapers). The host pastes each listing URL instead of one profile
    # link. See app/integrations/bright_data_client.py.
    bright_data_api_key: str | None = None

    # SearchApi.io's Airbnb engines (https://www.searchapi.io/airbnb-api) --
    # two uses, see app/integrations/searchapi_client.py: (1) daily
    # comparable-listing pricing for smart_pricing_service.py, feeding
    # Property.smart_price_estimate, one call per unique city; (2) live
    # per-listing price fetch for exact_airbnb_pricing properties during
    # get_pricing/negotiate_rate/check_calendar. Free tier is a small
    # one-time/monthly request allowance (SearchApi.io doesn't publish
    # which) -- redis_url below caches both to stretch it further. Unset =
    # both no-op/fall back cleanly (same pattern as bright_data_api_key).
    searchapi_api_key: str | None = None

    # TWO independent uses, deliberately not merged (see app/integrations/
    # redis_client.py's own module docstring for why):
    #   1. Optional TTL cache for SearchApi responses (redis_client.py),
    #      keyed off the same small free-tier allowance as searchapi_api_key
    #      above. Unset = every call hits SearchApi live; never blocks or
    #      errors a pricing quote on Redis being unreachable -- same
    #      "don't crash, don't block" pattern as everything else optional
    #      in this file.
    #   2. CallCoordinator's active-call ownership/lease mechanism (see
    #      app/services/call_coordinator.py, app/integrations/
    #      redis_lease_client.py) -- Redis is the SOLE source of truth for
    #      "is this host/property already on a live call?" as of the Redis
    #      migration; Postgres no longer participates. Unlike use 1, an
    #      unreachable Redis here is NOT a silent no-op: acquire_or_reject
    #      logs it loudly (lease_redis_unavailable) as a degraded-protection
    #      signal and fails OPEN (the call still proceeds -- a live guest
    #      call must never be rejected solely because Redis is down), while
    #      renew/release always fail open silently (never allowed to
    #      terminate/endanger an in-progress call). See call_coordinator.py's
    #      own module docstring for the full failure-policy reasoning.
    redis_url: str | None = None

    # Twilio WhatsApp Sandbox (https://www.twilio.com/docs/whatsapp/sandbox)
    # -- no Meta Business verification needed, unlike a real WhatsApp
    # Business number. The tradeoff: it can only message numbers that have
    # first opted in by texting "join <sandbox-code>" to
    # twilio_whatsapp_from from WhatsApp (Twilio's console shows the code
    # for this account's sandbox) -- fine for testing against your own
    # phone, not usable for arbitrary real guests until upgraded to a real
    # WhatsApp Business number. See app/integrations/twilio_client.py.
    # Unset = send_whatsapp/send_photos fall back to the in-app notification
    # stand-in only (same "don't crash, don't block" pattern as SMTP/Bright
    # Data above).
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str = "whatsapp:+14155238886"  # Twilio's shared sandbox number

    # ContentSid of the "mira_escalation" twilio/call-to-action template
    # (see scripts/create_escalation_template.py) -- gives the escalation
    # WhatsApp message a real "Go to Dashboard" button instead of a raw,
    # auto-linkified URL (which also triggers WhatsApp's link-preview
    # card). Unset = escalate_to_host falls back to a plain-text message
    # with the bare URL.
    twilio_escalation_template_sid: str | None = None

    # ContentSid of the "mira_busy_recovery" twilio/text template (see
    # scripts/create_busy_recovery_template.py) -- the numbered-menu message
    # RecoveryService sends a guest whose call was rejected as BUSY_RECOVERY
    # (see app/services/recovery_service.py, app/services/call_coordinator.py).
    # Unset = falls back to an equivalent plain-text message built inline,
    # same fallback discipline as twilio_escalation_template_sid above.
    twilio_busy_recovery_template_sid: str | None = None

    # ContentSid of the `mira_availability` WhatsApp template (see
    # scripts/create_availability_template.py) -- sent to a busy-recovery
    # guest once Mira's CallCoordinator lease for that host/property is
    # actually released (see app/services/recovery_service.py's
    # process_availability_recovery). Unset = falls back to an equivalent
    # plain-text message, same fallback discipline as
    # twilio_busy_recovery_template_sid above.
    twilio_availability_template_sid: str | None = None

    # ContentSid of the `mira_guest_calling` twilio/call-to-action template
    # (see scripts/create_guest_calling_template.py) -- Phase 5's "guest is
    # calling Mira right now" host notification (see app/services/
    # guest_calling_notification.py), fired once per live MIRA-owned call.
    # Unset = falls back to an equivalent plain-text message, same fallback
    # discipline as twilio_escalation_template_sid above.
    twilio_guest_calling_template_sid: str | None = None

    # HS256 signing secret for the Take Call action token (Phase 6, see
    # app/services/take_call_token.py) -- a short-lived, single-use grant
    # embedded in the WhatsApp "Take Call" link, verified with no Clerk
    # session involved. Deliberately a separate secret from every other
    # token/secret in this file (never reused across purposes) so rotating
    # it can never affect Exotel/Twilio webhook auth or Clerk sessions.
    # "change-me" default matches this file's existing convention for
    # every other must-be-set-in-production secret (exotel_webhook_token,
    # twilio_voice_webhook_token, etc.) -- fails loudly/obviously in a real
    # deployment that forgot to set it, rather than silently using a weak
    # default. UNLIKE those shared-secret tokens (compared via
    # hmac.compare_digest, never used as cryptographic key material), this
    # one IS an HMAC signing key -- pyjwt itself warns
    # (InsecureKeyLengthWarning) if it's under 32 bytes for HS256. Set a
    # real value at least 32 bytes long in production (e.g.
    # `openssl rand -hex 32`), not a short human-chosen phrase.
    take_call_token_secret: str = "change-me"

    # Shared-secret path token for the inbound WhatsApp webhook (see
    # app/api/v1/webhooks/whatsapp.py, app/services/whatsapp_reply_service.py)
    # -- same "path segment, not Twilio's own HMAC scheme" convention as
    # twilio_voice_webhook_token/exotel_webhook_token above. Configured as
    # this account's WhatsApp sandbox "WHEN A MESSAGE COMES IN" webhook URL
    # in the Twilio console.
    twilio_whatsapp_webhook_token: str = "change-me"

    # Twilio Voice -- an entirely separate integration from the WhatsApp
    # sandbox above and from Exotel telephony (app/api/v1/voice.py's
    # exotel_voice_ws / app/voice/pipeline.py's run_voice_pipeline), added
    # so real-call testing can continue on Twilio's free trial when Exotel
    # credits run out, without touching any Exotel code path. Reuses
    # twilio_account_sid/twilio_auth_token above -- same Twilio account, just
    # also used for Voice now. Shared-secret path token, same pattern as
    # exotel_webhook_token (Twilio signs webhooks with its own HMAC scheme
    # too, but a shared-secret path segment is simpler and consistent with
    # the rest of this codebase). See app/integrations/twilio_client.py's
    # verify_voice_webhook_token and app/voice/pipeline.py's
    # run_voice_pipeline_twilio.
    twilio_voice_webhook_token: str = "change-me"

    # Cloudinary -- re-hosts property photos scraped via Bright Data
    # (see app/integrations/cloudinary_client.py) so listing images survive
    # even if the source Airbnb listing is edited/removed, and so MIRA can
    # serve a resized/optimized version to guests later rather than hotlink
    # Airbnb's own CDN. All three required together; any missing = upload
    # skipped silently (same "don't crash, don't block" pattern as
    # bright_data_api_key/SMTP_*).
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None

    sarvam_api_key: str | None = None
    sarvam_stt_model: str = "saaras:v3"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_speaker: str = "roopa"

    # Server-side STT VAD/noise-rejection tuning (saaras:v3 only -- see
    # SarvamSTTSettings in pipecat/services/sarvam/stt.py). Distinct from,
    # and currently unrelated to, app/voice/vad.py's _VAD_PARAMS: that tunes
    # the LOCAL Silero VAD pipecat runs client-side for interruption/turn-
    # timing purposes only. Local VAD does not gate what audio reaches
    # Sarvam -- every AudioRawFrame is streamed to Sarvam continuously
    # regardless of local VAD state (confirmed via pipecat's STTService.
    # process_frame/process_audio_frame) -- so Sarvam's OWN server-side VAD
    # is the actual, and until now unconfigured, gate on what counts as
    # speech worth transcribing at all. Root cause: a background voice loud/
    # sustained enough to clear Sarvam's own (currently default) speech-
    # start threshold gets transcribed as if it were the guest, and nothing
    # downstream (SilenceWatchdogProcessor's blank-only filter, the turn-stop
    # strategy) can tell the difference -- there is no confidence field on
    # the transcript that measures speech-authenticity (see
    # language_probability's docstring: it measures LANGUAGE-detection
    # confidence, not whether the segment is genuine caller speech, so it is
    # deliberately not used as a noise-rejection signal here).
    #
    # Every field below defaults to None ("not sent, use Sarvam's own
    # server default") -- a fresh deploy with none of these set is BYTE-
    # IDENTICAL in behavior to before this setting existed. Starting points,
    # not empirically tuned against real call logs yet (same "starting
    # point" caveat semantic_search_timeout_seconds above documents for
    # itself) -- Phase 0/1 investigation found no existing baseline in this
    # repo to derive a specific number from, unlike the local VAD's
    # start_secs=0.35 (which WAS tuned against real call logs). Deliberately
    # conservative in the one direction that matters most: biased toward
    # never cutting off a real guest, since a false negative here (rejecting
    # real speech) is worse than a false positive (occasionally transcribing
    # background noise) -- see CLAUDE.md-adjacent guidance on this tradeoff.
    # Requires real-call validation before being trusted as a production
    # default; see documentation/ for the Phase 1 validation matrix.
    sarvam_vad_positive_speech_threshold: float | None = None
    sarvam_vad_negative_speech_threshold: float | None = None
    # Minimum consecutive speech frames required before Sarvam starts a
    # speech segment at all -- the most direct lever against a short,
    # single-syllable noise blip ("No", a mic bump) being transcribed,
    # without touching turn-taking timing (unlike the local VAD's
    # start_secs, this only affects whether Sarvam STARTS a segment, not
    # pipecat's own interruption/turn-stop logic). 2 is a conservative
    # starting point -- Sarvam's own unconfigured default already accepts
    # 1, so this asks for one additional frame of sustained speech before a
    # segment begins, without approaching an aggressive value that risks
    # clipping a genuine short reply like "Yes."
    sarvam_vad_min_speech_frames: int | None = 2
    sarvam_vad_first_turn_min_speech_frames: int | None = None
    sarvam_vad_negative_frames_count: int | None = None
    sarvam_vad_negative_frames_window: int | None = None
    # Volume floor (dB) below which audio is too quiet to be considered
    # speech -- the most direct lever against a distant/background talker on
    # a speakerphone call (quieter than the near-field guest) without
    # affecting segment-start timing at all. Left unset (None) by default:
    # unlike min_speech_frames' frame-count semantics (where "one extra
    # frame" has an intuitive, bounded worst case), a dB threshold's safe
    # value depends on real call audio levels this investigation has no
    # measured baseline for -- setting a wrong value here risks silently
    # rejecting a genuinely quiet guest, which constraint (18) explicitly
    # prohibits trading off casually. Available to configure once real-call
    # audio levels are observed (Phase 1 validation matrix).
    sarvam_vad_start_speech_volume_threshold: float | None = None
    sarvam_vad_interrupt_min_speech_frames: int | None = None
    sarvam_vad_pre_speech_pad_frames: int | None = None
    sarvam_vad_num_initial_ignored_frames: int | None = None

    # Phase 2: independent hard ceiling on live-call lifetime -- a reliability
    # backstop, NOT the primary background-audio fix (that's the sarvam_vad_*
    # tuning above). SilenceWatchdogProcessor's inactivity detection depends
    # entirely on trusting non-blank transcripts as real caller activity (see
    # app/voice/silence_watchdog.py) -- if background noise/speech keeps
    # generating non-blank transcripts, that mechanism can never fire, no
    # matter how well-tuned. This setting exists to guarantee a call still
    # ends even in that failure mode: a single, unconditional timer, started
    # once per call, that never resets for any reason (not STT activity, not
    # VAD, not LLM responses, not tool calls, not interruptions) -- see
    # app/voice/pipeline.py's _enforce_max_call_duration.
    #
    # 600s (10 minutes) is a DELIBERATELY GENEROUS starting point, not a
    # tuned production value -- this repository has no historical call-
    # duration data (no logs, no fixtures, no analytics) to derive an
    # optimal number from, confirmed by repo-wide search during Phase 2's
    # investigation. Chosen to be safely above any real booking/pricing/
    # negotiation conversation's plausible length (so a normal call should
    # never come close to hitting it) while still being a genuine backstop
    # against a call that's stuck open indefinitely. Needs real-call
    # validation (real call-length distribution data) before being tuned
    # down to a tighter production value -- same "starting point, not
    # empirically tuned" discipline as the sarvam_vad_* settings above.
    max_call_duration_seconds: int = 600

    # TURN relay for the in-dashboard "test in browser" WebRTC feature.
    # STUN alone (just discovering each side's public address) is enough on
    # localhost, but most cloud hosts don't allow the resulting direct UDP
    # media connection through at all -- ICE then times out instead of
    # connecting. TURN relays the actual audio through a third server over
    # a connection that looks like normal outbound traffic, which works
    # around that. Optional: only used if turn_url is set; falls back to
    # STUN-only (works on localhost, not on most cloud hosts) otherwise.
    turn_url: str | None = None
    turn_username: str | None = None
    turn_credential: str | None = None

    # Mobile carrier networks frequently block or throttle plain UDP (what
    # turn_url above uses) far more aggressively than WiFi/broadband --
    # confirmed by browser test calls failing on mobile data while working
    # fine on WiFi. A TURNS-over-TCP relay on port 443 looks identical to
    # normal HTTPS traffic to any carrier NAT/firewall, so it's offered as a
    # second ICE server alongside turn_url rather than replacing it -- the
    # browser tries all candidates and uses whichever the network allows.
    # Same username/credential as turn_url (Metered and most providers issue
    # one credential pair valid across all of their relay endpoints).
    turn_url_tls: str | None = None

    @field_validator("turn_url", "turn_url_tls")
    @classmethod
    def _validate_turn_url(cls, value: str | None) -> str | None:
        if value is not None and not (value.startswith("turn:") or value.startswith("turns:")):
            raise ValueError(
                f"TURN_URL must start with 'turn:' or 'turns:', got: {value!r}"
            )
        return value

    exotel_sid: str | None = None
    exotel_api_key: str | None = None
    exotel_api_token: str | None = None
    exotel_subdomain: str = "api.exotel.com"
    exotel_webhook_token: str = "change-me"
    exotel_gateway_ip: str | None = None
    exotel_gateway_port: int = 5070

    # Email escalation summaries -- interim stand-in for a WhatsApp Business
    # API host notification (that needs Meta business verification + Exotel
    # KYC approval, no instant sandbox exists unlike Twilio's). Any SMTP
    # provider works (Gmail app password, Zoho, Amazon SES SMTP, ...); unset
    # = escalate_to_host still creates the in-app notification, just skips
    # the email (see app/integrations/email_client.py).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True

    ical_sync_interval_minutes: int = 15

    # Experimental (shagun branch only, not deployed to main/production).
    # "vad_fixed" is today's proven SpeechTimeoutUserTurnStopStrategy
    # (user_speech_timeout=0.9s) in app/voice/pipeline.py, unchanged by
    # default. "hybrid_experimental" swaps in
    # app/voice/turn_strategies.HybridCompletenessUserTurnStopStrategy, which
    # extends the wait (up to a hard cap) when the transcript looks
    # mid-sentence, instead of firing at a fixed timeout. Local-only via
    # TURN_DETECTION_STRATEGY in backend/.env -- deliberately not added to
    # render.yaml or CLAUDE.md's env var table, since those are shared
    # production config.
    turn_detection_strategy: Literal["vad_fixed", "hybrid_experimental"] = "vad_fixed"

    # Kill-switch for recommend_properties' semantic search layer
    # (app/services/property/retrieval/semantic_search.py) -- the one place
    # in this codebase that makes a synchronous embedding API call on the
    # live voice-call path (every other embedding use is write-time
    # fire-and-forget or read-time against already-stored vectors). Default
    # on since the layer is already timeout-guarded and fails open to
    # SQL-only results, but this gives an instant, no-redeploy way to
    # disable it entirely if real-call latency/cost turns out worse than
    # expected.
    enable_semantic_property_search: bool = True
    # Hard cap on the one live-call embedding request semantic_search.py
    # makes. Starting point, not empirically tuned yet -- see that module's
    # docstring. Never allowed to stack with retries; one attempt, one
    # timeout, done.
    semantic_search_timeout_seconds: float = 1.5

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
