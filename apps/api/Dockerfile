# Sitara API — development/runtime image (Phase 3A).
FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# requirements.txt is the pip-tools lock compiled from requirements.in:
# every transitive dependency pinned, installation verified by hash.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY . .

EXPOSE 8000

# Development server for Phase 3A local work; gunicorn (pinned in
# requirements.txt) takes over when production deployment is scaffolded.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
