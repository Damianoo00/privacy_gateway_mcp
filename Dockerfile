FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY privacy_gateway ./privacy_gateway
RUN pip install '.[documents]' && python -m spacy download en_core_web_lg
ENTRYPOINT ["privacy-gateway"]
