FROM python:3.12-slim

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
