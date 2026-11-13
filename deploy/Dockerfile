# Migration-tool control plane. Dependency-free (Python stdlib); ships an SSH client so it
# can drive the toolchain on the VM host.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -u 10001 -m app
WORKDIR /app
COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV MT_PORT=8080 \
    MT_DATA_DIR=/data \
    MT_RUNNER=local

EXPOSE 8080
USER 10001
WORKDIR /app/backend
ENTRYPOINT ["python", "server.py"]
