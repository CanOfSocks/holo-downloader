FROM python:3.12-slim as builder

# Create and use a virtual environment
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install system deps
RUN apt-get update && apt-get install --no-install-recommends -y \
    python3-venv curl gnupg ca-certificates lsb-release wget unzip xz-utils procps cron git && \
    python3 -m venv $VIRTUAL_ENV && \
    $VIRTUAL_ENV/bin/pip install --no-cache-dir --upgrade pip setuptools wheel

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

# Copy venv and ffmpeg/ffprobe
COPY --from=builder /opt/venv /opt/venv

# Symlink virtualenv Python to global path
RUN ln -sf /opt/venv/bin/python /usr/local/bin/python && \
    ln -sf /opt/venv/bin/pip /usr/local/bin/pip

RUN chmod -R a+rX /opt/venv
# Copy app files
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py
COPY . /app
WORKDIR /app

# Install Jellyfin FFmpeg
RUN curl -m 15 -fsSL https://repo.jellyfin.org/debian/jellyfin_team.gpg.key | \
    gpg --dearmor --batch --yes -o /etc/apt/trusted.gpg.d/debian-jellyfin.gpg && \
    os_id=$(awk -F'=' '/^ID=/{ print $NF }' /etc/os-release) && \
    os_codename=$(awk -F'=' '/^VERSION_CODENAME=/{ print $NF }' /etc/os-release) && \
    echo "deb [arch=$(dpkg --print-architecture)] https://repo.jellyfin.org/$os_id $os_codename main" > /etc/apt/sources.list.d/jellyfin.list && \
    apt-get update && \
    apt-get install --no-install-recommends --no-install-suggests -y jellyfin-ffmpeg7 && \
    ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/bin/ffmpeg && \
    ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/bin/ffprobe

# Install minimal runtime deps
RUN apt-get update && apt-get install --no-install-recommends -y procps cron git && apt-get clean -y

# Set permissions
RUN chmod +x *.py /app/startCron.sh

# Env and entrypoint
ENV VIDEOSCHEDULE='*/2 * * * *'
ENV MEMBERSCHEDULE='*/5 * * * *'
ENV PATH="/opt/venv/bin:$PATH"

ENTRYPOINT [ "bash", "-c", "/app/startCron.sh" ]
