# Use the existing livestream_dl as the base
FROM ghcr.io/canofsocks/livestream_dl:latest

ARG PYTHONDONTWRITEBYTECODE=1

# Switch to /app (the base image ends in /app/downloads) 
WORKDIR /app

RUN mkdir -p /app/livestream_dl && \
    find . -maxdepth 1 ! -name 'livestream_dl' ! -name '.' -exec mv {} /app/livestream_dl/ \;

# Keep github commit hash
ARG HOLO_COMMIT_HASH
ENV LIVESTREAM_DL_COMMIT_HASH=${COMMIT_HASH}
ENV COMMIT_HASH=${HOLO_COMMIT_HASH}

COPY . .

# Set permissions
RUN chmod +x *.py /app/start.sh

ADD https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py .

# --- System Dependencies ---
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/testing/" >> /etc/apk/repositories && \
    apk add --no-cache --virtual .build-deps git && \
    #
    # Install Python Dependencies in one go
    # We upgrade pip first, then install requirements + the git repo
    pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -U \
        gunicorn \
        -r /app/requirements.txt \
        "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab" && \
    #
    # Apply the YouTube chat patch
    sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" \
        $(python -c "import chat_downloader; import os; print(os.path.dirname(chat_downloader.__file__))")/sites/youtube.py && \
    #
    # Cleanup: Remove git and curl to save space
    apk del .build-deps

# --- Verify Tools ---
# Ensure the "Secret Sauce" Deno wrapper from the base image is still working [cite: 7, 10]
RUN python --version && deno --version && ffmpeg -version
CMD []
ENTRYPOINT [ "sh", "-c", "/app/start.sh" ]