FROM python:3.13-alpine AS builder

# Install build dependencies
RUN apk add --no-cache \
    curl \
    wget \
    tar \
    xz \
    ca-certificates \
    findutils

# ------------------------------------------
# 1. Install Jellyfin FFmpeg (ROBUST METHOD)
# ------------------------------------------
RUN set -e; \
    latest_tag=$(curl -Ls -o /dev/null -w %{url_effective} https://github.com/jellyfin/jellyfin-ffmpeg/releases/latest | awk -F'/' '{print $NF}'); \
    echo "Latest tag: $latest_tag"; \
    asset_url="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/$latest_tag/jellyfin-ffmpeg_${latest_tag#v}_portable_linux64-gpl.tar.xz"; \
    echo "Downloading: $asset_url"; \
    wget -O ffmpeg.tar.xz "$asset_url"; \
    # Create temp directory
    mkdir -p /tmp/ffmpeg-extract; \
    # Extract WITHOUT stripping components (safer)
    tar -xvf ffmpeg.tar.xz -C /tmp/ffmpeg-extract; \
    # Prepare target directory
    mkdir -p /build-bin; \
    # USE FIND to locate the binary regardless of folder structure
    find /tmp/ffmpeg-extract -name "ffmpeg" -type f -exec cp {} /build-bin/ \;; \
    find /tmp/ffmpeg-extract -name "ffprobe" -type f -exec cp {} /build-bin/ \;; \
    # Verify they exist (will fail build if not found)
    ls -lh /build-bin/ffmpeg; \
    ls -lh /build-bin/ffprobe; \
    # Cleanup
    rm -rf ffmpeg.tar.xz /tmp/ffmpeg-extract

# ------------------------------------------
# 2. Install Deno
# ------------------------------------------
ARG DENO_INSTALL=/build-deno
RUN mkdir -p /build-deno
RUN curl -fsSL https://deno.land/install.sh | sh

# ------------------------------------------
# 3. Prepare App Repos
# ------------------------------------------
RUN apk add --no-cache git
RUN git clone "https://github.com/CanOfSocks/livestream_dl" /app/livestream_dl
RUN wget -q -O "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py


# ==========================================
# Stage 2: Final Image
# ==========================================
FROM python:3.13-alpine

# Install compatibility libraries for glibc binaries (FFmpeg/Deno) on Alpine
RUN apk add --no-cache \
    libc6-compat \
    libstdc++ \
    gcompat \
    git \
    curl \
    ca-certificates

WORKDIR /app

# Copy Binaries
COPY --from=builder /build-bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=builder /build-bin/ffprobe /usr/local/bin/ffprobe
COPY --from=builder /build-deno/bin/deno /usr/local/bin/deno

# Set execution permissions
RUN chmod 755 /usr/local/bin/ffmpeg \
    /usr/local/bin/ffprobe \
    /usr/local/bin/deno

# Copy Application Files
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py
COPY . .

# Set permissions for scripts
RUN chmod +x *.py /app/start.sh

# Install Python Dependencies
RUN pip install --no-cache-dir -r /app/livestream_dl/requirements.txt && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab"

# Patches
RUN (sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py) ; \
    (sed -i "/if[[:space:]]\+fmt_stream\.get('targetDurationSec'):/,/^[[:space:]]*continue/s/^[[:space:]]*/&#/" "$(pip show yt-dlp | awk '/Location/ {print $2}')/yt_dlp/extractor/youtube/_video.py")

ENV VIDEOSCHEDULE='*/2 * * * *'
ENV MEMBERSCHEDULE='*/5 * * * *'

ENTRYPOINT [ "sh", "-c", "/app/start.sh" ]