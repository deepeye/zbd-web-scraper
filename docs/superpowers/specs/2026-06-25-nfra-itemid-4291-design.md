# nfra itemId=4291 第二入口支持 — 设计文档

## 概述

新增 nfra 列表采集入口 itemId=4291（与已实现的 itemId=4110 并列）。采集流程、网页内容抽取方式与 4110 完全一致。两次独立运行（各自独立浏览器会话/cookie）。

## 已验证事实（2026-06-25）

- 现有代码已按 `item_id` 全链路参数化：`run_crawl(item_id=4110, ...)`、`discover_doc_rows(session, item_id, pages)`、`build_list_url(item_id, page)`、`build_list_html_url(item_id)`。
- CLI 已支持 `--item-id`；Makefile `crawl-nfra` 已支持 `NFRA_ITEM_ID` 环境变量（`--item-id $(or ${NFRA_ITEM_ID},4110)`）。
- itemId=4291 列表 API 用同一 AsyncStealthySession cookie-bootstrap 流程可正常获取：探测返回 18 行，首条标题「毕节金融监管分局关于刘志勇建行毕节市分行行长任职资格的批复」（含「任职资格」，标题过滤会保留）。
- 详情/抽取流程与 4110 同模板（同一站点），无需改 extractor 或 storage。

结论：**无需改爬虫/抽取/存储代码**。4110 仅是默认值，4291 已可用。

## 设计

### 1. 无代码改动

`run_crawl` 及下游均参数化；两次独立运行 = 两次独立进程调用，浏览器/cookie 天然隔离。保持单 item_id 接口不变。

### 2. Makefile 便捷目标

在 `crawl-nfra` 之后新增 `crawl-nfra-4291`：

```make
crawl-nfra-4291:
	uv run python scripts/crawl_nfra.py --pages $(or ${NFRA_PAGES},5) --item-id 4291
```

`crawl-nfra`（4110）与 `NFRA_ITEM_ID` 环境变量覆盖保持不变。

### 3. README 文档

在 README（中/英）的本地数据库或爬虫章节，记录两个 itemId 入口与运行方式：

- `make crawl-nfra`（默认 itemId=4110 总局机关）
- `make crawl-nfra-4291`（itemId=4291）
- 或 `NFRA_ITEM_ID=<id> make crawl-nfra` 自定义

### 4. 端到端 smoke 验收（itemId=4291）

- `make crawl-nfra-4291` 或 `python scripts/crawl_nfra.py --item-id 4291 --pages 1`
- 验证 djg_data 落库（4291 的 doc_id 行）
- 重跑验证 skip 语义（stored=0）
- 全量测试套件无回归（29/29）

## 改动文件清单

| 文件 | 动作 |
|------|------|
| `Makefile` | 修改 — 新增 `crawl-nfra-4291` target |
| `README.md` | 修改 — 记录两个 itemId 入口 |
| `README.zh-CN.md` | 修改 — 同上 |

## 不改动

- `src/web_scraper_service/crawlers/nfra.py`、`nfra_extractor.py`、`storage/djg_data.py`、`config.py`、`scripts/crawl_nfra.py`、测试——均不动。

## 注意事项

- 两次独立运行之间 djg_data 共表，doc_id 全局唯一（ON CONFLICT (doc_id,person_name) DO NOTHING），不会冲突。
- 4291 的文档若标题不含「任职资格」会被列表阶段过滤跳过（与 4110 行为一致）。
