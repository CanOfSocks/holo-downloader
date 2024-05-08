FROM python:3-slim

RUN mkdir -p /app

RUN mkdir -p /app/temp

RUN mkdir -p /app/Done

RUN mkdir -p /app/Members

RUN apt-get update && apt-get install --no-install-recommends wget unzip xz-utils procps cron git -y -qq && apt clean -y

RUN wget -q https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && \
         tar -C /usr/bin -xvf ffmpeg-master-latest-linux64-gpl.tar.xz --wildcards ffmpeg-master-latest-linux64-gpl/bin/ff* --strip-components 2 && \
         rm ffmpeg-master-latest-linux64-gpl.tar.xz && chmod +x /usr/bin/ff*

ARG YTA_VERSION=latest

RUN wget -q "https://github.com/Kethsar/ytarchive/releases/download/${YTA_VERSION}/ytarchive_linux_amd64.zip" && \
         unzip ytarchive_linux_amd64.zip -d /usr/bin && chmod +x /usr/bin/ytarchive && \
         rm ytarchive_linux_amd64.zip

WORKDIR /app

COPY . .

RUN chmod +x *.py
RUN chmod +x /app/startCron.sh

RUN pip install -q --no-cache-dir -r requirements.txt

# Install youtube-community-tab
RUN pip install -q --no-cache-dir -e "git+https://github.com/HoloArchivists/youtube-community-tab.git#egg=youtube-community-tab&subdirectory=youtube-community-tab"
RUN wget -q -o "/app/ytct.py" https://raw.githubusercontent.com/HoloArchivists/youtube-community-tab/master/ytct.py
RUN apt-get purge git -y && apt-get autopurge -y

#Apply chat_downloader patch
RUN sed -i "s/socs.value.startswith('CAA')/str(socs).startswith('CAA')/g" /usr/local/lib/python*/site-packages/chat_downloader/sites/youtube.py

RUN apt-get purge -y wget unzip xz-utils && apt-get autopurge -y && apt clean -y

ENV VIDEOSCHEDULE='*/2 * * * *'
ENV MEMBERSCHEDULE='*/5 * * * *'
#Setup Crontab
#RUN chown -R root /app/crontab && chmod -R 0644 /app/crontab
#RUN crontab /app/crontab

#ENTRYPOINT [ "cron", "-f" ]
ENTRYPOINT [ "bash", "-c", "/app/startCron.sh" ]
