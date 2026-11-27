FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

COPY requirements.txt /opt/worksfr-video-requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --no-deps --require-hashes -r /opt/worksfr-video-requirements.txt \
    && useradd --create-home --uid 10001 --user-group worker

WORKDIR /out
USER worker
