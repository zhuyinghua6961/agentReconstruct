FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --retries 10 --timeout 120 --progress-bar off --prefer-binary \
      fastapi \
      uvicorn[standard] \
      gunicorn \
      httpx \
      pydantic \
      python-dotenv \
      redis \
      pymysql \
      minio \
      chromadb \
      openai \
      tiktoken \
      numpy \
      tqdm \
      PyMuPDF \
      pandas \
      openpyxl \
      python-multipart \
      flask \
      flask-cors \
      langchain-core \
      langchain-openai \
      langchain-community

WORKDIR /app
COPY . /app
