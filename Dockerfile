FROM alpine/git

RUN apk add --no-cache jq curl

RUN wget -q  https://api.github.com/repos/cli/cli/releases/latest \
    && wget -q $(cat latest | grep linux_amd64.tar.gz | grep browser_download_url | grep -v .asc | cut -d '"' -f 4) \
    && tar -xvzf gh*.tar.gz \
    && mv gh*/bin/gh /usr/local/bin/ \
    && rm -fr *

ADD entrypoint.sh /entrypoint.sh
ADD commit_files.sh /commit_files.sh
ADD describe_pr.sh /describe_pr.sh
RUN chmod +x /entrypoint.sh /commit_files.sh /describe_pr.sh
ENTRYPOINT [ "/entrypoint.sh" ]
