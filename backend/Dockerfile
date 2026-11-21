# syntax=docker/dockerfile:1
FROM golang:1.27.4-alpine AS build
WORKDIR /src
COPY go.mod go.sum *.go ./
RUN --mount=type=cache,target=/root/.cache/go-build \
    test -z "$(gofmt -l .)" && go test ./... && \
    mkdir -p /out && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/backend .

FROM alpine:3.23 AS runtime
RUN apk add --no-cache ca-certificates && install -d -m 0700 -o 10001 -g 10001 /var/lib/neti
ENV BACKEND_DATA_FILE=/var/lib/neti/workspace.db BACKEND_AGENT_URL=http://agent:8001
COPY --from=build /out/backend /usr/local/bin/backend
USER 10001:10001
EXPOSE 8000
CMD ["backend"]
