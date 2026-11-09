FROM python:3.12-slim

# aiortc (pulled in by pipecat-ai[webrtc], used by the dashboard's "test in
# browser" feature) compiles native extensions against ffmpeg/opus/vpx --
# these aren't present on Render's plain Python buildpack, which is why this
# is a Docker deploy rather than a native runtime one.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    libopus-dev \
    libvpx-dev \
    libavdevice-dev \
    libavfilter-dev \
    libavformat-dev \
    libavcodec-dev \
    libswresample-dev \
    libswscale-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.lock is the exact, hash-verified set of every package (direct and
# transitive) that the tests ran against. requirements.txt only holds the version
# RANGES we ask for; installing from it would let any rebuild silently pick up new
# FastAPI/SQLAlchemy/asyncpg/openai/groq/redis releases (the same drift that
# once broke Pipecat). To change dependencies: edit requirements.txt, then run
#   uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes -o requirements.lock
# and commit both files.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# Optional: WebRTC echo cancellation for phone calls (app/voice/echo_canceller.py,
# enabled by ECHO_CANCELLATION_ENABLED). No prebuilt Linux wheel exists, so this
# compiles from source. Deliberately separate from requirements.txt and allowed to
# fail: if the build breaks, the deploy still succeeds and calls run without echo
# cancellation (the processor fails open when the package is missing).
RUN pip install --no-cache-dir aec-audio-processing || echo "aec-audio-processing unavailable; continuing without echo cancellation"

COPY . .

EXPOSE 8000

# Migrations run on every boot, before the server starts accepting traffic.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
