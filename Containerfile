# Container image for the agentic load-test tool.
# Works with both `podman build` and `docker build`. Runs as a non-root user and
# is compatible with the OpenShift restricted SCC (arbitrary UID in group 0).
#
# NOTE: build for the cluster's architecture. On Apple Silicon, OpenShift nodes
# are usually amd64, so build with:  podman build --platform=linux/amd64 .
FROM registry.access.redhat.com/ubi9/python-311:latest

# The UBI Python image defaults to USER 1001; switch to root for setup so we can
# install packages and fix group permissions, then drop back to non-root.
USER 0

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

# OpenShift runs the container with an arbitrary UID that always belongs to the
# root group (GID 0). Make the app tree group-owned by 0 and group-writable so
# that UID can read everything and write the persisted run config at runtime.
RUN chgrp -R 0 /app && chmod -R g=rwX /app

EXPOSE 8080

# Run as non-root. OpenShift overrides this with its own arbitrary UID; locally
# this keeps the container off root too.
USER 1001

CMD ["python", "-m", "agentic_loadtest.main"]
