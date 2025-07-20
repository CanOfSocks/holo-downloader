FROM python:3.12-slim as builder

# Install dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
    curl gnupg ca-certificates lsb-release wget unzip xz-utils procps cron git && \
    apt-get clean -y

# Install Jellyfin GPG key and repository
RUN curl -m 15 -fsSL https://repo.jellyfin.org/debian/jellyfin_team.gpg.key | \
    gpg --dearmor --batch --yes -o /etc/apt/trusted.gpg.d/debian-jellyfin.gpg

RUN os_id=$(awk -F'=' '/^ID=/{ print $NF }' /etc/os-release) && \
    os_codename=$(awk -F'=' '/^VERSION_CODENAME=/{ print $NF }' /etc/os-release) && \
    echo "deb [arch=$(dpkg --print-architecture)] https://repo.jellyfin.org/$os_id $os_codename main" \
    > /etc/apt/sources.list.d/jellyfin.list

RUN apt-get update && \
    apt-get install --no-install-recommends --no-install-suggests -y jellyfin-ffmpeg7 && \
    rm -rf /var/lib/apt/lists/*

# Create symbolic links (they’ll be copied to the final image)
RUN ln -s /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/bin/ffmpeg && \
    ln -s /usr/lib/jellyfin-ffmpeg/ffprobe /usr/bin/ffprobe

# Download ytarchive and ytarchive-raw-go
#ARG YTA_VERSION=latest
#RUN wget -q "https://github.com/Kethsar/ytarchive/releases/download/${YTA_VERSION}/ytarchive_linux_amd64.zip" && \
#    unzip ytarchive_linux_amd64.zip -d /usr/bin && \
#    chmod +x /usr/bin/ytarchive && \
#    rm ytarchive_linux_amd64.zip

#RUN wget -q "https://github.com/HoloArchivists/ytarchive-raw-go/releases/latest/download/ytarchive-raw-go-linux-amd64" -O /usr/bin/ytarchive-raw-go && \
#    chmod +x /usr/bin/ytarchive-raw-go

# Clone the repository
RUN git clone "https://github.com/CanOfSocks/livestream_dl" /app/livestream_dl

# Apply patches
RUN wget -q -O "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py


# Final minimal image setup
FROM python:3.12-slim

# Copy only the necessary files from the builder stage
COPY --from=builder /usr/bin/ffmpeg /usr/bin/
COPY --from=builder /usr/bin/ffprobe /usr/bin/
#COPY --from=builder /usr/bin/ytarchive /usr/bin/
#COPY --from=builder /usr/bin/ytarchive-raw-go /usr/bin/
COPY --from=builder /app/livestream_dl /app/livestream_dl
COPY --from=builder /app/ytct.py /app/ytct.py

WORKDIR /app

# Copy application files
COPY . .

RUN apt-get update && apt-get install --no-install-recommends -y \
         procps cron git && \
         apt-get clean -y

# Set permissions for Python scripts and Cron file
RUN chmod +x *.py /app/startCron.sh

# Install remaining dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab"

# Modify yt-dlp
RUN (sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py) ; \
    (sed -i '/if fmt.get('\'targetDurationSec\''):$/,/    continue$/s/^/#/' "$(pip show yt-dlp | grep Location | awk '{print $2}')/yt_dlp/extractor/youtube/_video.py")

# Set environment variables and cron schedule
ENV VIDEOSCHEDULE='*/2 * * * *'
ENV MEMBERSCHEDULE='*/5 * * * *'

ENTRYPOINT [ "bash", "-c", "/app/startCron.sh" ]
