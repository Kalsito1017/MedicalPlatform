# syntax=docker/dockerfile:1
# Self-hosted whisper.cpp speech service (CPU only, Tiny multilingual model).
#
# Pinned release and checksums: the source tarball and the model are both
# verified before use, and the model is baked into the image so the runtime
# never downloads anything. Not exposed publicly in docker-compose.yml: only
# the `app` service on the same private network can reach port 8080.

# --- Build whisper.cpp -------------------------------------------------------
FROM debian:bookworm-slim AS builder

ARG WHISPER_VERSION=v1.9.5
ARG WHISPER_SHA256=ff1a9053feb509ff9d7729703355541ae9690073a6b1c40eb692c962e0dc1720

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl -fsSL -o whisper.tar.gz \
        "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/${WHISPER_VERSION}.tar.gz" \
    && echo "${WHISPER_SHA256}  whisper.tar.gz" | sha256sum -c - \
    && tar -xzf whisper.tar.gz \
    && rm whisper.tar.gz

WORKDIR /build/whisper.cpp-1.9.5
RUN cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
        -DWHISPER_BUILD_SERVER=ON \
    && cmake --build build --target whisper-server --parallel "$(nproc)"

# --- Verified model ----------------------------------------------------------
FROM debian:bookworm-slim AS model

ARG MODEL_SHA256=be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /models \
    && curl -fsSL -o /models/ggml-tiny.bin \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin" \
    && echo "${MODEL_SHA256}  /models/ggml-tiny.bin" | sha256sum -c -

# --- Runtime -----------------------------------------------------------------
FROM debian:bookworm-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    WHISPER_THREADS=2 \
    WHISPER_PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 speech \
    && useradd --system --uid 10001 --gid speech --home-dir /app --no-create-home speech

COPY --from=builder /build/whisper.cpp-1.9.5/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=model --chown=speech:speech /models/ggml-tiny.bin /models/ggml-tiny.bin

USER speech
EXPOSE 8080

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${WHISPER_PORT}/health" >/dev/null || exit 1

# CPU only; the model path is fixed and baked into the image.
ENTRYPOINT ["sh", "-c", "exec whisper-server -m /models/ggml-tiny.bin --host 0.0.0.0 --port \"${WHISPER_PORT}\" -t \"${WHISPER_THREADS}\" --no-gpu"]
