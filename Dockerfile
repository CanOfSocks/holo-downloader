# syntax=docker/dockerfile:1
# (The syntax line enables `ADD <git repository>` and `RUN --mount`, used for ytct below.)

# Use the stable livestream_dl "latest" image as the base. Override with
# --build-arg BASE_IMAGE=ghcr.io/canofsocks/livestream_dl:dev for the dev image.
ARG BASE_IMAGE=ghcr.io/canofsocks/livestream_dl:latest
# ytct.py (imported by communityPosts.py) and its youtube_community_tab library
ARG YTCT_REPO=CanOfSocks/youtube-community-tab
ARG YTCT_BRANCH=dev

# Both come from one commit of the branch. ADD of a git repository looks up the branch's current
# commit on every build, so a new commit always rebuilds the install step below; a
# `pip install git+...` step would be served from the build cache and keep an old library.
FROM scratch AS ytct-source
ARG YTCT_REPO
ARG YTCT_BRANCH
ADD https://github.com/${YTCT_REPO}.git#${YTCT_BRANCH} /

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

# --- System Dependencies ---
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/testing/" >> /etc/apk/repositories && \
    # Add some dns caching because it is cheap to do
    apk add --no-cache dnsmasq && \
    #
    # Install Python Dependencies in one go
    # We upgrade pip first, then install requirements
    pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -U \
        gunicorn \
        -r /app/requirements.txt

# ytct.py and the youtube_community_tab library from the ytct-source stage. The checkout is mounted
# (writable, as pip builds in it; the changes are discarded) rather than copied, so it isn't left in the image.
RUN --mount=type=bind,from=ytct-source,target=/tmp/youtube-community-tab,rw \
    cp /tmp/youtube-community-tab/ytct.py /app/ytct.py && \
    pip install --no-cache-dir /tmp/youtube-community-tab/youtube-community-tab

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