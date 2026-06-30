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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Migrations run on every boot, before the server starts accepting traffic.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
