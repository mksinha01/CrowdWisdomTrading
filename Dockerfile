FROM python:3.11-slim

# ffmpeg for rendering; node 22 for the optional HyperFrames backend;
# curl/git for the Hermes installer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        ca-certificates \
        build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Hermes Agent. There is NO pip package — pip installs are explicitly unsupported
# upstream. The shell installer is the only supported path.
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive
ENV PATH="/root/.local/bin:/root/.hermes/bin:${PATH}"

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/runs && chmod +x scripts/*.sh

# Port map — see Rule P1. These must never collide.
#   9119  Hermes kanban dashboard
#   8000  reserved; unused by this project
#   0     any video-backend preview server MUST bind an ephemeral port
EXPOSE 9119

CMD ["cwt", "run", "--engine", "hermes"]
