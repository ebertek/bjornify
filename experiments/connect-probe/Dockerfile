# Experimental, standalone READ-ONLY Connect authentication probe.
# This does not replace or start the Björnify Discord container.
FROM rust:stable-slim-bookworm AS build

# hadolint ignore=DL3008 (experimental image gets latest security updates)
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /probe
COPY experiments/connect-probe/Cargo.toml ./Cargo.toml
COPY experiments/connect-probe/src/ ./src/
RUN cargo build --release

FROM debian:bookworm-slim
# hadolint ignore=DL3008 (experimental image gets latest security updates)
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /probe/target/release/bjornify-connect-auth-probe /usr/local/bin/connect-probe
# The job is a one-shot authentication probe, not a persistent service.
# This checks the probe executable rather than a background process.
HEALTHCHECK --interval=30s --timeout=3s --retries=1 CMD ["/usr/bin/test", "-x", "/usr/local/bin/connect-probe"]
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/connect-probe"]
