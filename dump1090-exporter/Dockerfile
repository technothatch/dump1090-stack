# dump1090-exporter built from the technothatch/dump1090-exporter sources.
#
# The exporter repository does not ship a Dockerfile (it is a plain Go
# binary), so this build clones it at image build time.

FROM golang:1.27-alpine AS build

ARG EXPORTER_REPO=https://github.com/technothatch/dump1090-exporter.git
ARG EXPORTER_REF=main

RUN apk add --no-cache git ca-certificates
WORKDIR /src
RUN git clone --depth 1 --branch "${EXPORTER_REF}" "${EXPORTER_REPO}" . \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" \
        -o /out/dump1090-exporter ./cmd/dump1090-exporter

FROM alpine:3.23

RUN apk add --no-cache ca-certificates tzdata \
    && adduser -D -H -u 10001 exporter
COPY --from=build /out/dump1090-exporter /usr/local/bin/dump1090-exporter

USER exporter
EXPOSE 9702

ENTRYPOINT ["/usr/local/bin/dump1090-exporter"]
