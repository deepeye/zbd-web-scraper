# 同步接口文档与部署文档

**日期**: 2026-07-08
**类型**: 文档同步

## 背景

上次文档更新（`7c671ed`）之后，capital/equity 采集功能已全部完成，但部分配置文件和部署文档未同步新增的 env var 和端点。

## 目标

补齐以下文件中的缺失内容，保持现有结构不变：

1. `.env.example` — 本地开发环境变量模板
2. `.env.docker.example` — Docker 部署环境变量模板
3. `docs/DEPLOY.md` — Linux 部署指南

## 改动

### 1. `.env.example`

在 nfra 定时调度段末尾追加两行：

```bash
NFRA_CAPITAL_SCHEDULE_ENABLED=true
NFRA_EQUITY_SCHEDULE_ENABLED=true
```

位置：`NFRA_SCHEDULE_PAGES=5` 之后，`# ── Scrapling / Fetcher` 注释之前。

### 2. `.env.docker.example`

同 `.env.example`，在 `NFRA_SCHEDULE_PAGES=5` 之后追加两行：

```bash
NFRA_CAPITAL_SCHEDULE_ENABLED=true
NFRA_EQUITY_SCHEDULE_ENABLED=true
```

### 3. `docs/DEPLOY.md`

三处改动：

**a) 手动触发采集段**（"运行"章节第一个代码块之后），增加 capital 和 equity 的 curl 示例：

```bash
# 注册资本/开业采集
curl -X POST localhost:8000/api/v1/nfra/capital/crawl \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"pages": 5}'
# 股权变更采集
curl -X POST localhost:8000/api/v1/nfra/equity/crawl \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"pages": 5}'
```

**b) 定时说明段**，在 `NFRA_SCHEDULE_ENABLED=false` 后追加：

> `NFRA_CAPITAL_SCHEDULE_ENABLED=false` / `NFRA_EQUITY_SCHEDULE_ENABLED=false` 可单独关闭各类采集。

**c) 查询数据段**，在现有 `GET /api/v1/nfra/data` 说明后，补充 capital/equity 查询路径：

```bash
# 查询注册资本/开业数据
curl "localhost:8000/api/v1/nfra/capital/data?start_date=...&end_date=..." \
  -H "X-API-Key: $API_KEY"
# 查询股权变更数据
curl "localhost:8000/api/v1/nfra/equity/data?start_date=...&end_date=..." \
  -H "X-API-Key: $API_KEY"
```

## 验证

- 确认 `.env.example` 和 `.env.docker.example` 中 `NFRA_CAPITAL_SCHEDULE_ENABLED` 和 `NFRA_EQUITY_SCHEDULE_ENABLED` 已存在
- 确认 `docs/DEPLOY.md` 中包含 capital/equity 的 curl 示例和 env var 说明