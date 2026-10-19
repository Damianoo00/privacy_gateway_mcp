# Private proxy only. External MCP servers are configured after deployment.
FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY privacy_gateway ./privacy_gateway
RUN pip install '.[pii]' \
    && python -m spacy download en_core_web_lg \
    && mkdir -p /sources /data /workspace \
    && chown 1000:1000 /sources /data /workspace
USER 1000:1000
ENTRYPOINT ["python", "-m", "privacy_gateway.container"]
