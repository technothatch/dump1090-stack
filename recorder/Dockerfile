# dump1090-recorder: capture the Beast TCP feed to rotating files.
#
# Runs only under the compose "recorder" profile:
#   docker compose --profile recorder up -d
FROM alpine:3.23

RUN apk add --no-cache tzdata

COPY --chmod=755 record.sh /usr/local/bin/record.sh

# /recordings is bind-mounted by compose.yaml.
VOLUME ["/recordings"]

ENTRYPOINT ["/usr/local/bin/record.sh"]
