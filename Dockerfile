# ── Stage 1: Build ──────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app

# 先只拷贝依赖声明文件，利用 Docker 层缓存：pyproject.toml/uv.lock
# 不变时该层及 uv sync 都不会重新执行。
COPY pyproject.toml uv.lock README.md ./

# 仅安装第三方依赖（不装项目本身），下载的包缓存在 BuildKit cache mount
# 中，即使层失效重建也不会重复下载。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 再拷贝源码（此后层在 src 变更时失效，但依赖已缓存）
COPY src/ src/

# 安装项目本身（仅链接/复制，无需下载，秒级完成）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

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
COPY alembic.ini ./
COPY scripts/ scripts/

# 下载浏览器二进制（playwright + patchright chromium），baked 进镜像默认缓存路径
RUN scrapling install

EXPOSE 8000
CMD ["uvicorn", "web_scraper_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
