FROM node:26-bookworm-slim@sha256:0042e17d232a87a2cd485be1da7900fca4d10a4ad11b29e9285da1d2e54798c9 AS node

FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    TF_CPP_MIN_LOG_LEVEL=2 \
    OPENAI_API_KEY=worksfr-fake-key \
    ANTHROPIC_API_KEY=worksfr-fake-key \
    GOOGLE_API_KEY=worksfr-fake-key \
    GEMINI_API_KEY=worksfr-fake-key \
    GROQ_API_KEY=worksfr-fake-key \
    COHERE_API_KEY=worksfr-fake-key \
    MISTRAL_API_KEY=worksfr-fake-key \
    HUGGINGFACEHUB_API_TOKEN=worksfr-fake-key

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libc6-dev \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global --no-audit --no-fund tsx@4.23.15

RUN useradd --create-home --uid 1000 sandbox \
    && python -m venv /opt/venv \
    && chown -R sandbox:sandbox /opt/venv \
    && mkdir -p /opt/worksfr /home/sandbox/work \
    && chown sandbox:sandbox /home/sandbox/work

COPY --chown=root:root --chmod=0444 runner.py /opt/worksfr/runner.py

WORKDIR /home/sandbox/work
USER sandbox

RUN pip install --no-cache-dir --upgrade numpy pandas scikit-learn matplotlib tensorflow-cpu keras

CMD ["python", "-u", "/opt/worksfr/runner.py"]
