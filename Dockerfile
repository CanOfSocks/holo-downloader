FROM python:3.13-alpine AS builder

# Install core dependencies using apk
# Alpine uses 'apk add' instead of 'apt-get install'
# We install the Alpine equivalent packages:
#   - wget, unzip, xz, procps, crond (for cron), git
RUN apk update && apk add --no-cache \
    wget \
    unzip \
    xz \
    procps \
    crond \
    git \
    curl \
    tar \
    ca-certificates

# Install latest Jellyfin FFmpeg binary release from GitHub
# All necessary packages (curl, wget, tar, xz, ca-certificates) are already installed above.
RUN set -e; \
    latest_tag=$(curl -Ls -o /dev/null -w %{url_effective} https://github.com/jellyfin/jellyfin-ffmpeg/releases/latest | awk -F'/' '{print $NF}'); \
    echo "Latest tag: $latest_tag"; \
    asset_url="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/$latest_tag/jellyfin-ffmpeg_${latest_tag#v}_portable_linux64-gpl.tar.xz"; \
    echo "Downloading: $asset_url"; \
    wget -O ffmpeg.tar.xz "$asset_url" && \
    mkdir -p /opt/jellyfin-ffmpeg && \
    tar -C /usr/bin -xvf ffmpeg.tar.xz && \
    rm ffmpeg.tar.xz

# Clone the repository
RUN git clone "https://github.com/CanOfSocks/livestream_dl" /app/livestream_dl

# Apply patches
RUN wget -q -O "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py

ARG DENO_INSTALL=/usr
# Install Deno
RUN curl -fsSL https://deno.land/install.sh | sh

# Final minimal image setup
FROM python:3.13-alpine

# Copy only the necessary files from the builder stage
#COPY --from=builder /usr/bin/ffmpeg /usr/bin/
#COPY --from=builder /usr/bin/ffprobe /usr/bin/
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py
#COPY --from=builder /usr/bin/deno /usr/bin/

WORKDIR /app

# Copy application files
COPY . .

RUN apk add --no-cache \
        git \
        curl \
        ffmpeg

# Set permissions for Python scripts and Cron file
RUN chmod +x *.py /app/start.sh

# Install remaining dependencies
RUN pip install --no-cache-dir -r /app/livestream_dl/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab"
RUN pip install --no-cache-dir -U deno

# Modify yt-dlp
RUN (sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py) ; (sed -i "/if[[:space:]]\+fmt_stream\.get('targetDurationSec'):/,/^[[:space:]]*continue/s/^[[:space:]]*/&#/" "$(pip show yt-dlp | awk '/Location/ {print $2}')/yt_dlp/extractor/youtube/_video.py")

# Set environment variables and cron schedule
ENV VIDEOSCHEDULE='*/2 * * * *'
ENV MEMBERSCHEDULE='*/5 * * * *'

ENTRYPOINT [ "sh", "-c", "/app/start.sh" ]
