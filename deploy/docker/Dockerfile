# Deskhand server as a container (runs on the home server; Oracle's nginx is the public front door).

# ---- stage 1: build the React web app (web-app/) into static files. Only this stage needs Node.
# package files first: the slow `npm ci` layer is reused until a dependency changes.
FROM node:20-slim AS webbuild
WORKDIR /web
COPY web-app/package.json web-app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web-app/ ./
RUN npm run build

# ---- stage 2: the Python server
FROM python:3.12-slim

# the build (GitHub Actions) passes the commit being built, so /v1/version can say exactly what is running
ARG COMMIT=""
ENV DESKHAND_COMMIT=$COMMIT
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# dependencies first, so a code-only change rebuilds in seconds
COPY pyproject.toml ./
RUN mkdir -p src/taskpilot && touch src/taskpilot/__init__.py \
 && pip install ".[server]" \
 && pip uninstall -y taskpilot

COPY src ./src
COPY --from=webbuild /web/dist ./src/taskpilot/webapp
COPY server_policy.example.json ./server_policy.example.json
RUN pip install --no-deps ".[server]"

RUN useradd -m -u 10001 deskhand && mkdir -p /data && chown deskhand /data
USER deskhand
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/v1/version', timeout=4).status == 200 else 1)"

CMD ["taskpilot-server", "--provider", "openai", "--model", "nvidia/nemotron-3.5-super-vl-120b-a12b", "--db", "/data/taskpilot.db", "--policy", "/data/server_policy.json", "--host", "0.0.0.0", "--port", "8765"]
