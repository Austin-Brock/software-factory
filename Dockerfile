# AgentCore Runtime runs ARM64 containers.
FROM --platform=linux/arm64 python:3.11-slim-bookworm

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

CMD ["python", "-m", "agents.planner_service"]
