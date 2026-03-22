FROM python:3.12-slim-bookworm

WORKDIR /backend

RUN set -e; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl gnupg; \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/jellyfin.gpg; \
      echo "deb [signed-by=/usr/share/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian bookworm main" \
        > /etc/apt/sources.list.d/jellyfin.list; \
      apt-get update; \
      apt-get install -y --no-install-recommends jellyfin-ffmpeg7; \
      ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg; \
      ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe; \
    else \
      apt-get install -y --no-install-recommends ffmpeg; \
    fi; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY dependencies/.env.example dependencies/.env.example
COPY entrypoint.sh /entrypoint.sh

# Create non-root user and required directories
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/* && \
    groupadd -g 1000 appgroup && \
    useradd -u 1000 -g appgroup -M -s /bin/false appuser && \
    mkdir -p /data/cutter-jobs /var/lib/media-renamer && \
    chown appuser:appgroup /data/cutter-jobs /var/lib/media-renamer && \
    chmod +x /entrypoint.sh

EXPOSE 3332

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3332"]
