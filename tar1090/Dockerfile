# tar1090 web UI + /data (decoder JSON) served by nginx.
FROM nginx:1.30-alpine

# Fetch the upstream tar1090 single-page application at build time.
# Pin a release by replacing master with a tag from
# https://github.com/wiedehopf/tar1090/tags (archive/refs/tags/<tag>.tar.gz).
RUN apk add --no-cache curl tar \
    && curl -fsSL https://github.com/wiedehopf/tar1090/archive/refs/heads/master.tar.gz \
        -o /tmp/tar1090.tar.gz \
    && tar -xzf /tmp/tar1090.tar.gz -C /tmp \
    && mkdir -p /usr/share/nginx/html \
    && cp -a /tmp/tar1090-master/html/. /usr/share/nginx/html/ \
    && rm -rf /tmp/tar1090*

COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# Inherits the stock nginx entrypoint: nginx -g "daemon off;".
