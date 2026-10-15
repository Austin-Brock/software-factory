# Builder agent for AgentCore Runtime (ARM64). Like Dockerfile, plus Node/npm
# so the builder can run the target repo's install, lint, test and build.
FROM --platform=linux/arm64 node:24-bookworm-slim AS node

FROM --platform=linux/arm64 python:3.11-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Makes BedrockAgentCoreApp bind 0.0.0.0 instead of 127.0.0.1.
    DOCKER_CONTAINER=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY agents/ agents/

RUN useradd --create-home --uid 1000 agent
USER agent

EXPOSE 8080

CMD ["python", "-m", "agents.builder_service"]
