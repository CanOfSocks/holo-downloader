# Stage 1: Get the Deno binary
FROM denoland/deno:bin AS deno_bin

# Stage 2: Get the GLIBC libraries (Required for Deno on Alpine)
FROM gcr.io/distroless/cc AS cc

# Stage 3: Builder (Clone repos and fetch scripts)
FROM alpine:latest AS builder
WORKDIR /build
RUN apk add --no-cache git wget

# Clone the repository
RUN git clone "https://github.com/CanOfSocks/livestream_dl" /app/livestream_dl

# Download standalone script
RUN wget -q -O "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py

# Stage 4: Final Image
FROM python:3.13-alpine

WORKDIR /app

# --- System Dependencies ---
# 1. Install Alpine-native tools
# Note: We use the native Alpine FFmpeg instead of the Jellyfin binary for stability.
# We add the 'testing' repo specifically to get 'gosu'.
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/testing/" >> /etc/apk/repositories && \
    apk add --no-cache \
    ffmpeg \
    git \
    curl

# --- Deno Setup (The Secret Sauce) ---
# 2. Copy GLIBC libraries for Deno (from Stage 2)
COPY --from=cc /lib/*-linux-gnu/* /usr/glibc/lib/
COPY --from=cc /lib/ld-linux-* /lib/

# 3. Set up the dynamic loader symlink
RUN mkdir /lib64 && ln -s /usr/glibc/lib/ld-linux-* /lib64/

# 4. Copy the Deno binary
COPY --from=deno_bin /deno /usr/local/bin/deno-raw

# 5. Create the Deno Wrapper
# This ensures Deno uses the side-loaded glibc, while the rest of the OS uses musl.
RUN echo '#!/bin/sh' > /usr/local/bin/deno && \
    echo 'LD_LIBRARY_PATH=/usr/glibc/lib exec /usr/local/bin/deno-raw "$@"' >> /usr/local/bin/deno && \
    chmod +x /usr/local/bin/deno

# --- Application Setup ---

# Copy repositories and scripts from builder
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py

# Copy local application files
COPY . .

# Set permissions
RUN chmod +x *.py /app/start.sh

# --- Python Dependencies ---
# We install build-base to ensure any C-extensions in pip packages can compile, then remove it to keep image small.
RUN pip install --no-cache-dir -r /app/livestream_dl/requirements.txt && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab" && \
    pip install --no-cache-dir -U gunicorn

# --- Patches ---
RUN (sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py) ; \
    (sed -i "s/\(^[[:space:]]*\)if[[:space:]]\+fmt_stream\.get('targetDurationSec'):/\1if fmt_stream.get('targetDurationSec') and not 'live_adaptive' in format_types:/" "$(pip show yt-dlp | awk '/Location/ {print $2}')/yt_dlp/extractor/youtube/_video.py")

# --- Verify Tools ---
RUN python --version && deno --version && ffmpeg -version

ENTRYPOINT [ "sh", "-c", "/app/start.sh" ]