# Experimental, standalone READ-ONLY Connect authentication probe.
# This does not replace or start the Björnify Discord container.
FROM rust:stable-slim-bookworm AS build

RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /probe
COPY experiments/connect-probe/Cargo.toml ./Cargo.toml
COPY experiments/connect-probe/src/ ./src/
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /probe/target/release/bjornify-connect-auth-probe /usr/local/bin/connect-probe
USER nobody
ENTRYPOINT ["/usr/local/bin/connect-probe"]
