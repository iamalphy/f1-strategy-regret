FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY sql/ ./sql/

# Data is mounted from the host at runtime; declared as a volume in the
# Dockerfile it would shadow the bind mount with an empty anonymous volume.
CMD ["python", "pipeline/extract.py"]
