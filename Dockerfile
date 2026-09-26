FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY data/state/notices.sqlite3 ./data/state/notices.sqlite3
COPY scripts ./scripts
COPY src ./src

CMD ["python", "scripts/cloud_run_job.py"]
