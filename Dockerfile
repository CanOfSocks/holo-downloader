# Use the stable livestream_dl "latest" image as the base. Override with
# --build-arg BASE_IMAGE=ghcr.io/canofsocks/livestream_dl:dev for the dev image.
ARG BASE_IMAGE=ghcr.io/canofsocks/livestream_dl:latest
FROM ${BASE_IMAGE}

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

# Set permissions. An empty cookies.txt placeholder keeps /app/cookies.txt a file when none is
# mounted (the real cookies are excluded by .dockerignore and must be mounted at runtime).
RUN chmod +x *.py /app/start.sh && touch /app/cookies.txt

ADD https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py .

# --- System Dependencies ---
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/testing/" >> /etc/apk/repositories && \
    # Add some dns caching because it is cheap to do
    apk add --no-cache dnsmasq && \
    #
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
    # Cleanup: Remove git and curl to save space
    apk del .build-deps

# --- Verify Tools ---
# Ensure the "Secret Sauce" Deno wrapper from the base image is still working [cite: 7, 10]
RUN python --version && deno --version && ffmpeg -version

# Workaround to help curb cache errors.
RUN mkdir -p /root/.cache ; chmod -R 777 /root/.cache

CMD []
# Exec form, so start.sh (which ends with `exec gunicorn`) replaces it as PID 1 and gunicorn receives
# the SIGTERM from `docker stop` directly. With `sh -c` a shell could stay PID 1 without passing the
# signal on, and Docker would kill everything after its stop timeout without a graceful shutdown.
ENTRYPOINT [ "/app/start.sh" ]