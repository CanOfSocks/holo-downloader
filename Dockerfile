# Use the existing livestream_dl as the base
FROM ghcr.io/canofsocks/livestream_dl:latest

# Switch to /app (the base image ends in /app/downloads) 
WORKDIR /app

# --- Reorganize Files ---
# Create the subdirectory holo-downloader expects 
# Then move the existing livestream_dl files into it.
RUN mkdir -p /app/livestream_dl && \
    find . -maxdepth 1 ! -name 'livestream_dl' ! -name '.' -exec mv {} /app/livestream_dl/ \;

# --- System Dependencies ---
# Install curl to fetch the standalone script and git for the community-tab repo
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/testing/" >> /etc/apk/repositories && \
    apk add --no-cache git curl

# --- Fetch Missing Scripts ---
# Download the standalone script directly into /app 
RUN curl -s -o "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py

# --- Application Setup ---
# Copy your local holo-downloader files 
COPY . .

# Set permissions
RUN chmod +x *.py /app/start.sh

# --- Python Dependencies ---
# Install only the additional dependencies needed for holo-downloader
RUN pip install --no-cache-dir -r /app/livestream_dl/requirements.txt && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab" && \
    pip install --no-cache-dir -U gunicorn

# --- Patches ---
# Apply the specific YouTube chat patch
RUN sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py

# --- Verify Tools ---
# Ensure the "Secret Sauce" Deno wrapper from the base image is still working [cite: 7, 10]
RUN python --version && deno --version && ffmpeg -version
CMD []
ENTRYPOINT [ "sh", "-c", "/app/start.sh" ]