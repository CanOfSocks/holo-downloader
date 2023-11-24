FROM python:3-alpine

RUN mkdir -p /app

RUN mkdir -p /app/temp

RUN mkdir -p /app/Done

RUN apk add --no-cache ffmpeg unzip busybox wget build-base linux-headers

ARG YTA_VERSION=latest

RUN wget -q "https://github.com/Kethsar/ytarchive/releases/download/${YTA_VERSION}/ytarchive_linux_amd64.zip" && \
         unzip ytarchive_linux_amd64.zip -d /usr/bin && chmod +x /usr/bin/ytarchive && \
         rm ytarchive_linux_amd64.zip

WORKDIR /app

COPY . .

RUN chmod +x *.py

RUN pip install -q --no-cache-dir -r requirements.txt

#Remove unneeded packages
RUN apk del unzip wget build-base

#Setup Crontab
RUN chown -R root /app/crontab && chmod -R 0644 /app/crontab
RUN crontab /app/crontab

ENTRYPOINT [ "crond", "-f" ]
