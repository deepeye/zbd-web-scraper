# ── Stage 1: Build ──────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# ── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.12-slim

# Playwright/Chromium 运行依赖 + CJK 字体（中文页渲染）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libasound2 libxshmfence1 \
    libxkbcommon0 libgtk-3-0 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini scripts/ ./

# 下载浏览器二进制（playwright + patchright chromium），baked 进镜像默认缓存路径
RUN scrapling install

EXPOSE 8000
CMD ["uvicorn", "web_scraper_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
