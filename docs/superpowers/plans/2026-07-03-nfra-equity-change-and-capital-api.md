# nfra 注册资本 API 补全 + 变更股权全新链路 Implementation Plan

**Goal:** 补全 nfra 注册资本/开业数据的 API（现有计划 Task 3-5 未做），并从零新建「变更股权」数据链路（存储 → LLM 抽取 → 爬虫编排 → Celery task → API），使两类数据均可经 RESTful 接口手动触发采集与分页查询。

**Scope 来源:** 用户确认「两个都做」。注册资本变更 API 缺现有计划 `docs/superpowers/plans/2026-07-02-nfra-capital-change.md` 的 Task 3-5；变更股权是全新链路（xlsx `docs/股东股权变更批复数据结构-0630.xlsx` 定义字段，目前无表无抽取器）。

---

## 现状

| 模块 | 注册资本变更 | 变更股权 |
|------|------|------|
| 存储表 | ✅ `capital_change_data`（已提交） | ❌ 无 |
| LLM 抽取器 | ✅ `nfra_capital_extractor.py`（已提交） | ❌ 无 |
| 爬虫编排 + CLI | ❌ Task 3 未做 | ❌ 无 |
| Celery task | ❌ Task 4 未做 | ❌ 无 |
| API 端点 | ❌ Task 4 未做 | ❌ 无 |
| 文档 | ❌ Task 5 未做 | ❌ 无 |

已有就绪件：`CapitalChangeDataRepoD` 依赖（`api/deps.py`）、`init_capital_change_table()`（`main.py` lifespan 已调用）、`CapitalChangeDataRepo.{existing_doc_ids,list_by_crawl_time,count_by_crawl_time,insert_many}`。

数据来源：列表发现复用 `crawlers/nfra.py` 的 `discover_doc_rows(session, item_id, pages)` 与 `build_detail_html_url(doc_id)`；item_id 仍为 4110 + 4291。详情用渲染后 DOM（`resp.html_content`）。

---

## Part A — 补全注册资本变更（执行现有计划 Task 3-5）

完全按 `docs/superpowers/plans/2026-07-02-nfra-capital-change.md` 的 Task 3、4、5 实现，代码已在计划文档中逐行给出。要点：

### A1. 爬虫编排 + CLI（Task 3）
- 新建 `src/web_scraper_service/crawlers/nfra_capital.py`：`run_crawl(item_id=None, pages=5, concurrency=2, download_delay=1.0)`。复用 `discover_doc_rows`；标题过滤用 `is_capital_candidate`（含「注册资本」或「开业」）；跳过已存在 doc；详情用 `AsyncDynamicSession` 并发抽取，边抽边写 `capital_change_data`。
- 新建 `scripts/crawl_nfra_capital.py`：CLI 入口，`--pages/--item-id/--concurrency/--download-delay/--json-out`，镜像 `scripts/crawl_nfra.py`。
- 新建 `tests/test_nfra/test_capital_crawler.py`：默认双 item_id + 标题过滤、跳过已存在 doc 两个测试（计划已给）。

### A2. Celery task + API（Task 4）
- `scheduler/engine.py` 增 `nfra_capital_crawl_task(self, item_id: int | None, pages: int)`：subprocess 调 `scripts/crawl_nfra_capital.py --json-out`，解析末行 JSON。镜像 `nfra_crawl_task`。
- `api/v1/nfra.py` 增 `CapitalCrawlRequest`（`item_id: int | None = None`, `pages: int = 5`）+ 三个端点：
  - `POST /api/v1/nfra/capital/crawl`
  - `GET /api/v1/nfra/capital/crawl/{job_id}`
  - `GET /api/v1/nfra/capital/data`
- `tests/test_api/test_nfra.py` 追加 5 个 capital API 测试（计划已给）。

**对计划文档的两处一致性微调**（与现有 `/nfra/crawl/{job_id}` 对齐，不改变语义）：
- `capital_crawl_status` 用 `AsyncResult(job_id, app=celery_app)`（计划漏了 `app=`）。
- FAILURE 状态映射为 `"failed"`（计划写 `"failure"`），与现有任职资格状态端点一致。

### A3. 文档（Task 5）
- `docs/API.md` 第 7 节追加「7.4 nfra 注册资本/开业采集」小节（端点表 + 请求体 + 响应行字段示例）。
- 第 9 节附录数据模型追加 `capital_change_data` 表字段表。

---

## Part B — 变更股权全新链路

镜像 Part A 与现有任职资格链路的结构，独立表/抽取器/爬虫/API。代码风格与 `nfra_capital_extractor.py`、`nfra.py` 对齐。

### B1. 存储 `src/web_scraper_service/storage/equity_change_data.py`

表 `equity_change_data`（库 `zbd_crawler_data`，复用 `snapshot_engine`，`CREATE TABLE IF NOT EXISTS`，不走 Alembic）。

字段（源自 xlsx「变更股权」sheet）：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | bigserial PK | |
| `doc_id` | bigint, index | 文档 ID |
| `publish_date` | date, null | 发布日期 |
| `issue_date` | text | 发文日期（原文，如 2026年6月18日） |
| `issuing_authority` | text | 发文监管机构 |
| `doc_number` | text | 发文函号 |
| `change_type` | text | `变更股权` 或 `机构成立` |
| `institution_name` | text | 机构名称（总公司/法人） |
| `shareholder_name` | text | 股东名称 |
| `shareholding_before` | text | 变更前持股比例（原文，如 0.5） |
| `change_method` | text | 变更方式：`转入` / `转出` |
| `transferred_shares` | text | 受让股份（原文，如 1,748,794,139股） |
| `transferred_ratio` | text | 受让比例（原文） |
| `shares_after` | text | 变更后股份（原文） |
| `shareholding_after` | text | 变更后持股比例（原文） |
| `contribution_amount` | text | 出资额（原文） |
| `doc_title` | text | 发文名称 |
| `doc_url` | text | 发文链接 |
| `crawl_time` | timestamptz, server_default now() | 采集时间 |

唯一约束：`uq_equity_change_doc_institution_shareholder_method` = `(doc_id, institution_name, shareholder_name, change_method)`，`ON CONFLICT DO NOTHING`。

Repo `EquityChangeDataRepo`：`existing_doc_ids`、`list_by_crawl_time`、`count_by_crawl_time`、`insert_many`——签名与 `CapitalChangeDataRepo` 完全一致。

`init_equity_change_table()`：`async with snapshot_engine.begin()` → `run_sync(metadata.create_all)`。

**接线：**
- `api/deps.py`：import `EquityChangeDataRepo`，加 `get_equity_change_data_repo(session)` + `EquityChangeDataRepoD`（镜像 capital）。
- `main.py`：import `init_equity_change_table`，lifespan 在 `await init_capital_change_table()` 后调用 `await init_equity_change_table()`。

### B2. 抽取器 `src/web_scraper_service/crawlers/nfra_equity_extractor.py`

镜像 `nfra_capital_extractor.py` 结构。复用 `nfra_extractor.py` 的 `clean_prose`、`doc_title`、`doc_number`、`issuing_authority`、`publish_date`（代码侧取可靠字段，LLM 取股权字段）。

- `is_equity_candidate(title) -> bool`：`"股权" in title or "开业" in title`。
  - 注：「开业」文档同时被 capital extractor 抽（取注册资本）和 equity extractor 抽（取股东），写各自表，互不影响。
  - 「增加注册资本、变更股权及调整股权结构」类标题含「注册资本」+「股权」，被两个 extractor 同时命中，正确。
- `parse_llm_rows(content)`：解析 `{"rows":[...]}`，校验 `change_type ∈ {变更股权, 机构成立}`、`institution_name` 非空、`shareholder_name` 非空；机构成立时过滤分支机构（复用 `_BRANCH_WORDS = (分公司,支公司,中心支公司,营业部,分行,支行)`，与 capital 一致）。
- LLM prompt（`qwen3.5-35b-a3b`，`response_format=json_object`）输出字段：`issue_date, issuing_authority, change_type, institution_name, shareholder_name, shareholding_before, change_method, transferred_shares, transferred_ratio, shares_after, shareholding_after, contribution_amount`。规则要点：
  1. 变更股权批复 → `change_type=变更股权`；总公司开业批复中的股东 → `change_type=机构成立`。
  2. 一位股东一行；`change_method` 只允许 `转入`/`转出`。
  3. 机构成立只抽总公司股东，不抽分支机构。
  4. 文章只涉及任职资格/注册资本变更（无股东信息）→ `{"rows": []}`。
  5. 比例/股份/金额保留原文，不做数值归一化。
- `extract_rows_llm(doc_id, html, doc_url)`：合并代码侧字段（doc_id/publish_date/doc_number/doc_title/doc_url）+ LLM 行，`issuing_authority` 用 LLM 值或回退 `issuing_authority(title)`。
- `_call_llm` 加 `tenacity` 重试（3 次，指数退避），与 `nfra_extractor.py` 一致（capital extractor 未加重试，equity 对齐主 extractor 的稳健做法）。

### B3. 爬虫编排 + CLI
- `src/web_scraper_service/crawlers/nfra_equity.py`：`run_crawl(item_id=None, pages=5, concurrency=2, download_delay=1.0)`，结构镜像计划中的 `nfra_capital.py`——`discover_doc_rows` → `is_equity_candidate` 过滤 → `EquityChangeDataRepo.existing_doc_ids` 跳过 → `AsyncDynamicSession` 并发详情抽取 → 边抽边写。返回 `{"discovered","qualified","pending","extracted_rows","stored"}`。
- `scripts/crawl_nfra_equity.py`：CLI，镜像 `crawl_nfra_capital.py`。
- `tests/test_nfra/test_equity_crawler.py`：两个测试，镜像 `test_capital_crawler.py`（默认双 item_id + 标题过滤；跳过已存在 doc）。

### B4. Celery task + API
- `scheduler/engine.py` 增 `nfra_equity_crawl_task(self, item_id: int | None, pages: int)`：subprocess 调 `scripts/crawl_nfra_equity.py --json-out`。镜像 `nfra_capital_crawl_task`。
- `api/v1/nfra.py` 增 `EquityCrawlRequest`（`item_id: int | None = None`, `pages: int = 5`）+ 三端点：
  - `POST /api/v1/nfra/equity/crawl`
  - `GET /api/v1/nfra/equity/crawl/{job_id}`（用 `AsyncResult(job_id, app=celery_app)`，FAILURE→`"failed"`）
  - `GET /api/v1/nfra/equity/data`（`EquityChangeDataRepoD` + `Pagination`，返回全字段）
- `tests/test_api/test_nfra.py` 追加 equity API 测试，镜像 capital 的 5 个（默认触发、自定义 item_id、非法 pages、状态查询、data 查询）。

### B5. 存储与抽取器单测
- `tests/test_nfra/test_equity_change_storage.py`：表名、列类型、唯一约束、`init_equity_change_table` 调用 `run_sync`（镜像 `test_capital_change_storage.py`）。
- `tests/test_nfra/test_equity_extractor.py`：`is_equity_candidate`、prompt 含股权字段、`parse_llm_rows` 合法变更股权行、过滤分支机构、非法 JSON、`extract_rows_llm` 合并代码字段（镜像 `test_capital_extractor.py`，HTML fixture 用 xlsx 里的湖南长银/重庆小米案例）。

---

## Part C — 文档与 Makefile

- `docs/API.md`：
  - 7.4 节「nfra 注册资本/开业采集」（Part A3）。
  - 7.5 节「nfra 变更股权采集」：端点表 + 请求体 + `GET /equity/data` 响应行字段示例（用 xlsx 真实行）。
  - 第 9 节附录追加 `capital_change_data` 与 `equity_change_data` 两张表字段表。
- `Makefile`：加 `crawl-nfra-capital` 与 `crawl-nfra-equity` 目标，镜像 `crawl-nfra`（支持 `NFRA_ITEM_ID`/`NFRA_PAGES` 覆盖；capital/equity 默认不传 `--item-id` 即双栏目）。
- `CLAUDE.md` 第 7 节 nfra 采集器表与「nfra 采集」API 表：补 capital/equity 端点行；数据模型小节补两表。

---

## 验证

1. `pytest tests/test_nfra/test_capital_change_storage.py tests/test_nfra/test_capital_extractor.py tests/test_nfra/test_capital_crawler.py tests/test_nfra/test_equity_change_storage.py tests/test_nfra/test_equity_extractor.py tests/test_nfra/test_equity_crawler.py tests/test_api/test_nfra.py -v` → 全 PASS。
2. `ruff check` 改动文件 → PASS。
3. `make lint` → PASS（无关既有失败记录不修）。
4. 手动 smoke（凭据/浏览器就绪时）：`python scripts/crawl_nfra_capital.py --pages 1 --json-out` 与 `python scripts/crawl_nfra_equity.py --pages 1 --json-out`，末行 JSON 含 5 个统计键；缺凭据则跳过并注明。
5. 重启 worker + API 后，`POST /api/v1/nfra/capital/crawl` 与 `POST /api/v1/nfra/equity/crawl` 返回 `job_id`，`GET /capital/data`、`GET /equity/data` 可分页查询。

---

## 不在本次范围（follow-up）

- **每日定时调度**：现有 `init_nfra_schedule` 只派发任职资格 crawl（4110+4291）。本次不把 capital/equity 接入 8 点定时（避免每日自动触发 3 类 × 2 栏目 = 6 次 LLM 浏览器采集的成本惊喜）。如需自动采集，后续可加 `nfra_capital_schedule_enabled`/`nfra_equity_schedule_enabled` 开关或在 `_run_nfra()` 里追加 `nfra_capital_crawl_task.delay(None, pages)` 等。
- 股权变更的数值归一化（比例/股数/金额保持原文）。
- 任职资格链路不动。

---

## Self-Review

- 范围对齐：Part A 严格执行已有计划 Task 3-5（仅两处一致性微调并说明）；Part B 镜像 A 的结构与现有 `nfra.py`/`nfra_extractor.py` 模式。
- 命名一致：`EquityChangeData`/`EquityChangeDataRepo`/`init_equity_change_table`/`extract_rows_llm`/`run_crawl`/`nfra_equity_crawl_task` 跨文件一致。
- 表/约束：equity 唯一键 `(doc_id, institution_name, shareholder_name, change_method)` 覆盖同一 doc 多股东多方向，`ON CONFLICT DO NOTHING` 幂等。
- 标题过滤无遗漏：「开业」doc 同时进 capital+equity 两表（分别取注册资本与股东），符合 xlsx 两 sheet 均含「机构成立」行的事实。
- 无 placeholder。
