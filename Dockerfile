FROM python:3.12-slim as builder

# Create and use a virtual environment
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install system deps
RUN apt-get update && apt-get install --no-install-recommends -y \
    python3-venv curl gnupg ca-certificates lsb-release wget unzip xz-utils procps cron git && \
    python3 -m venv $VIRTUAL_ENV && \
    $VIRTUAL_ENV/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

# Install latest Jellyfin FFmpeg binary release from GitHub
RUN apt-get update && apt-get install --no-install-recommends -y \
    curl wget tar xz-utils ca-certificates && \
    set -e; \
    latest_tag=$(curl -Ls -o /dev/null -w %{url_effective} https://github.com/jellyfin/jellyfin-ffmpeg/releases/latest | awk -F'/' '{print $NF}'); \
    echo "Latest tag: $latest_tag"; \
    asset_url="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/$latest_tag/jellyfin-ffmpeg_${latest_tag#v}_portable_linux64-gpl.tar.xz"; \
    echo "Downloading: $asset_url"; \
    wget -O ffmpeg.tar.xz "$asset_url" && \
    mkdir -p /opt/jellyfin-ffmpeg && \
    tar -xf ffmpeg.tar.xz -C /opt/jellyfin-ffmpeg --strip-components=1 && \
    rm ffmpeg.tar.xz && \
    ln -sf /opt/jellyfin-ffmpeg/ffmpeg /usr/bin/ffmpeg && \
    ln -sf /opt/jellyfin-ffmpeg/ffprobe /usr/bin/ffprobe


# Clone repo and apply patches
RUN git clone "https://github.com/CanOfSocks/livestream_dl" /app/livestream_dl && \
    wget -q -O "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py

# Copy requirements and install Python deps into venv
COPY requirements.txt /app/requirements.txt
RUN $VIRTUAL_ENV/bin/pip install --no-cache-dir -r /app/requirements.txt && \
    $VIRTUAL_ENV/bin/pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab"

# Patch yt-dlp
RUN (sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" "$VIRTUAL_ENV/lib/python3.12/site-packages/chat_downloader/sites/youtube.py") && \
    (sed -i '/if fmt.get('\'targetDurationSec\''):$/,/    continue$/s/^/#/' "$VIRTUAL_ENV/lib/python3.12/site-packages/yt_dlp/extractor/youtube/_video.py")


# Final image
FROM debian:stable-slim

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
# Copy venv and ffmpeg/ffprobe
COPY --from=builder /opt/venv $VIRTUAL_ENV
COPY --from=builder /usr/bin/ffmpeg /usr/bin/
COPY --from=builder /usr/bin/ffprobe /usr/bin/

# Symlink virtualenv Python to global path
RUN ln -sf $VIRTUAL_ENV/bin/python /usr/local/bin/python && \
    ln -sf $VIRTUAL_ENV/bin/pip /usr/local/bin/pip

RUN chmod -R a+rX $VIRTUAL_ENV
# Copy app files
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py
COPY . /app
WORKDIR /app

# Install minimal runtime deps
RUN apt-get update && apt-get install --no-install-recommends -y procps cron git && apt-get clean -y

# Set permissions
RUN chmod +x *.py /app/startCron.sh

# Env and entrypoint
ENV VIDEOSCHEDULE='*/2 * * * *'
ENV MEMBERSCHEDULE='*/5 * * * *'
ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT [ "bash", "-c", "/app/startCron.sh" ]
