# Container image for the agentic load-test tool.
# Works with both `podman build` and `docker build`. Designed to run as an
# arbitrary (non-root) UID, as required by the OpenShift restricted SCC.
FROM registry.access.redhat.com/ubi9/python-311:latest

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, config and fixtures.
COPY src ./src
COPY config ./config
COPY fixtures ./fixtures

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    ALT_HOST=0.0.0.0 \
    ALT_PORT=8080 \
    ALT_CONFIG=/app/config/config.example.yaml

# OpenShift assigns an arbitrary UID in the root group; make app dir writable.
RUN chgrp -R 0 /app && chmod -R g=u /app

EXPOSE 8080

CMD ["python", "-m", "agentic_loadtest.main"]
