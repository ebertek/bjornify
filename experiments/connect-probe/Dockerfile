# Experimental, standalone READ-ONLY Connect authentication probe.
# This does not replace or start the Björnify Discord container.
FROM rust:slim-bookworm AS build

# Install the newest security-patched packages in this ephemeral test image.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /probe
COPY experiments/connect-probe/Cargo.toml ./Cargo.toml
COPY experiments/connect-probe/src/ ./src/
RUN cargo build --release

FROM debian:bookworm-slim
# Install the newest security-patched packages in this ephemeral test image.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /probe/target/release/bjornify-connect-auth-probe /usr/local/bin/connect-probe
# The job is a one-shot authentication probe, not a persistent service.
# This checks the probe executable rather than a background process.
HEALTHCHECK --interval=30s --timeout=3s --retries=1 CMD ["/usr/bin/test", "-x", "/usr/local/bin/connect-probe"]
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/connect-probe"]
