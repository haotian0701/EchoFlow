# Official prebuilt MinIO images are no longer reliably available. Build the
# pinned upstream security release instead of depending on third-party mirrors.
FROM golang:1.24-alpine AS builder
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio.git .
RUN CGO_ENABLED=0 go build -trimpath -o /minio .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates curl \
    && addgroup -g 10001 minio && adduser -D -u 10001 -G minio minio \
    && mkdir /data && chown minio:minio /data
COPY --from=builder /minio /usr/local/bin/minio
COPY --from=builder /src/LICENSE /licenses/minio-LICENSE
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
