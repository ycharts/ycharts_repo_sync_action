FROM alpine/git

RUN apk add --no-cache jq curl

RUN wget -q  https://api.github.com/repos/cli/cli/releases/latest \
    && wget -q $(cat latest | grep linux_amd64.tar.gz | grep browser_download_url | grep -v .asc | cut -d '"' -f 4) \
    && tar -xvzf gh*.tar.gz \
    && mv gh*/bin/gh /usr/local/bin/ \
    && rm -fr *

ADD entrypoint.sh /entrypoint.sh
ADD describe_pr.sh /describe_pr.sh
ADD close_prs.sh /close_prs.sh
ADD previous_pr_body.sh /previous_pr_body.sh
ADD build_pr_body.sh /build_pr_body.sh
RUN chmod +x /entrypoint.sh /describe_pr.sh /close_prs.sh /previous_pr_body.sh /build_pr_body.sh
ENTRYPOINT [ "/entrypoint.sh" ]
