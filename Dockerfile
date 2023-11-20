FROM python:3-slim

RUN mkdir -p /app

RUN mkdir -p /app/temp

RUN mkdir -p /app/Done

RUN apt-get update && apt-get install --no-install-recommends sed bash wget zip unzip xz-utils procps cron ffmpeg -y -qq && apt clean -y

#RUN wget -q https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && \
#         tar -C /usr/bin -xvf ffmpeg-master-latest-linux64-gpl.tar.xz --wildcards ffmpeg-master-latest-linux64-gpl/bin/* --strip-components 2 && \
#         rm ffmpeg-master-latest-linux64-gpl.tar.xz && chmod +x /usr/bin/ff*

RUN wget -q https://github.com/Kethsar/ytarchive/releases/download/v0.4.0/ytarchive_linux_amd64.zip && \
         unzip ytarchive_linux_amd64.zip -d /usr/bin && chmod +x /usr/bin/ytarchive && \
         rm ytarchive_linux_amd64.zip

WORKDIR /app

COPY . .

RUN chmod +x *.py *.sh

RUN pip install -q --no-cache-dir -r requirements.txt

#Setup Crontab
RUN chown -R root /app/crontab && chmod -R 0644 /app/crontab
RUN crontab /app/crontab

ENTRYPOINT [ "cron", "-f" ]
