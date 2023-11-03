FROM python:3-slim

RUN mkdir -p /app

RUN mkdir -p /app/temp

RUN mkdir -p /app/Done

RUN apt-get update && apt-get install --no-install-recommends sed sensible-utils wget zip unzip xz-utils procps -y && apt clean -y

RUN wget https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && \
         tar -C /usr/bin -xvf ffmpeg-master-latest-linux64-gpl.tar.xz --wildcards ffmpeg-master-latest-linux64-gpl/bin/* --strip-components 2 && \
         rm ffmpeg-master-latest-linux64-gpl.tar.xz

RUN wget https://github.com/Kethsar/ytarchive/releases/download/v0.4.0/ytarchive_linux_amd64.zip && \
         unzip ytarchive_linux_amd64.zip -d /usr/bin && \
         rm ytarchive_linux_amd64.zip

WORKDIR /app

COPY . .

#RUN chmod +x /app/config.py /app/discord-web.py /app/downloadVid.sh /app/getJson.py /app/runDownload.sh /app/getVids.sh

RUN pip install --no-cache-dir -r requirements.txt

#RUN crontab crontab

ENTRYPOINT /app/runDownload.sh
