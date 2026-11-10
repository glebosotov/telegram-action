FROM golang:1.26.7-alpine3.23@sha256:b17af760035fc2f338eed92d448a6c67f2d45438844fc6c60678fa5f99e44b57 AS build
WORKDIR /src
ADD --checksum=sha256:c7037dac032dbfea9d349eab34351d223758d658683deb9a82161233e4dc6fd7 https://codeload.github.com/glebosotov/drone-telegram/tar.gz/db67a8481f4ef0ba3663f447e9d73a094c64006a /tmp/sender.tar.gz
RUN tar -xzf /tmp/sender.tar.gz --strip-components=1 && go test ./... && CGO_ENABLED=0 go build -trimpath -o /bin/drone-telegram .

FROM alpine:3.23@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0
RUN apk add --no-cache ca-certificates
COPY --from=build /bin/drone-telegram /bin/drone-telegram
COPY entrypoint.sh /entrypoint.sh
WORKDIR /github/workspace
ENTRYPOINT ["/entrypoint.sh"]
