FROM python:3-alpine

RUN apk add --no-cache git

# The base image's WORKDIR is /, so the `rm -fr *` below needs its own directory.
WORKDIR /tmp/gh-install

RUN wget -q  https://api.github.com/repos/cli/cli/releases/latest \
    && wget -q $(cat latest | grep linux_amd64.tar.gz | grep browser_download_url | grep -v .asc | cut -d '"' -f 4) \
    && tar -xvzf gh*.tar.gz \
    && mv gh*/bin/gh /usr/local/bin/ \
    && rm -fr *

WORKDIR /

COPY entrypoint.py /action/
COPY repo_sync/ /action/repo_sync/
ENTRYPOINT [ "python3", "/action/entrypoint.py" ]
