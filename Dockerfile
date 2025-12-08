# ==========================================
# Stage 1: Builder
# ==========================================
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
# 1. Install Jellyfin FFmpeg (Robust Method)
# ------------------------------------------
#RUN set -e; \
#    latest_tag=$(curl -Ls -o /dev/null -w %{url_effective} https://github.com/jellyfin/jellyfin-ffmpeg/releases/latest | awk -F'/' '{print $NF}'); \
#    echo "Latest tag: $latest_tag"; \
#    asset_url="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/$latest_tag/jellyfin-ffmpeg_${latest_tag#v}_portable_linux64-gpl.tar.xz"; \
#    echo "Downloading: $asset_url"; \
#    wget -O ffmpeg.tar.xz "$asset_url"; \
#    mkdir -p /tmp/ffmpeg-extract; \
#    tar -xvf ffmpeg.tar.xz -C /tmp/ffmpeg-extract; \
#    mkdir -p /build-bin; \
#    # Find binaries regardless of folder structure
#    find /tmp/ffmpeg-extract -name "ffmpeg" -type f -exec cp {} /build-bin/ \;; \
#    find /tmp/ffmpeg-extract -name "ffprobe" -type f -exec cp {} /build-bin/ \;; \
#    rm -rf ffmpeg.tar.xz /tmp/ffmpeg-extract

# ------------------------------------------
# 2. Install Deno
# ------------------------------------------
#ARG DENO_INSTALL=/build-deno
#RUN mkdir -p /build-deno
#RUN curl -fsSL https://deno.land/install.sh | sh

# ------------------------------------------
# 3. Prepare App Repos
# ------------------------------------------
RUN apk add --no-cache git
RUN git clone "https://github.com/CanOfSocks/livestream_dl" /app/livestream_dl
RUN wget -q -O "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py

FROM denoland/deno:alpine AS deno_source

FROM python:3.13-alpine


# ------------------------------------------
# Install System Dependencies
# ------------------------------------------
# Note: Removed libc6-compat and gcompat as we now have real glibc
# Kept libstdc++ as many binaries still link to it
RUN apk add --no-cache \
    git \
    curl \
    ffmpeg \
    libc6-compat

WORKDIR /app

# ------------------------------------------
# Copy Binaries & Set Permissions
# ------------------------------------------
#COPY --from=builder /build-bin/ffmpeg /usr/local/bin/ffmpeg
#COPY --from=builder /build-bin/ffprobe /usr/local/bin/ffprobe
#COPY --from=builder /build-deno/bin/deno /usr/local/bin/deno

# 2. Copy the Deno Binary
# This corresponds to Line 12 in your image (copy /deno/deno)
COPY --from=deno_source /usr/local/deno /usr/local/bin/deno

# 3. Copy the glibc Compatibility Libraries (CRITICAL STEP)
# This corresponds to Lines 3 and 4 in your image which install the necessary lib files.
# The official image installs them to /usr/local/lib/
COPY --from=deno_source /usr/local/lib/lib*linux-gnu* /usr/local/lib/
COPY --from=deno_source /usr/local/lib/ld-linux-* /usr/local/lib/
ENV LD_LIBRARY_PATH=/usr/local/lib
#RUN chmod 755 /usr/local/bin/ffmpeg \
#    /usr/local/bin/ffprobe \
#    /usr/local/bin/deno

# ------------------------------------------
# Copy Application Files
# ------------------------------------------
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py
COPY . .

RUN chmod +x *.py /app/start.sh

# ------------------------------------------
# Install Python Dependencies
# ------------------------------------------
RUN pip install --no-cache-dir -r /app/livestream_dl/requirements.txt && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab"

# ------------------------------------------
# Patches
# ------------------------------------------
RUN (sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py) ; \
    (sed -i "/if[[:space:]]\+fmt_stream\.get('targetDurationSec'):/,/^[[:space:]]*continue/s/^[[:space:]]*/&#/" "$(pip show yt-dlp | awk '/Location/ {print $2}')/yt_dlp/extractor/youtube/_video.py")

RUN python --version && deno --version

ENTRYPOINT [ "sh", "-c", "/app/start.sh" ]