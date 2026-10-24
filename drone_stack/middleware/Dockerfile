# Image for the LLM-stack Python services (MAVProxy hub, MCP server, monitor,
# mcpo). Used by drone_stack/docker-compose.yml profile "llm-stack".
# The repo is bind-mounted at /repo at runtime, so code edits need no rebuild;
# only requirements.txt changes do:  docker compose --profile llm-stack build
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2-dev libxslt1-dev gcc procps && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /repo
