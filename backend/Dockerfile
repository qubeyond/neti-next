# syntax=docker/dockerfile:1
FROM golang:1.27.4-alpine AS build
WORKDIR /src
COPY go.mod *.go ./
RUN --mount=type=cache,target=/root/.cache/go-build \
    test -z "$(gofmt -l .)" && go test ./... && \
    mkdir -p /out && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/backend .

FROM alpine:3.23 AS runtime
COPY --from=build /out/backend /usr/local/bin/backend
USER 10001:10001
EXPOSE 8000
CMD ["backend"]
