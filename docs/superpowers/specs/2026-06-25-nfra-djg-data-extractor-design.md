# nfra.gov.cn djg_data 结构化抽取 — 设计文档

## 概述

修改 nfra 采集流程的详情阶段：从「HTTP 取 JSON 存 web_snapshot」改为「DynamicFetcher 打开 HTML 详情页，抽取结构化字段，写入 `zbd_crawler_data.djg_data`」。只处理标题含「任职资格」的人员类批复，一人一行。

列表阶段不变。web_snapshot 代码保留但 `run_crawl` 不再写入。

## 已验证的页面结构（2026-06-25 探测）

主路径 `/cn/view/pages/ItemDetail.html?docId={id}&itemId=4111&generaltype=0` 与分机构路径 `/branch/jiangsu/view/pages/common/ItemDetail.html` 使用同一模板：

- **元数据**在 `<meta>` 标签：`ArticleTitle`（发文名称）、`PubDate`（网站发布时间）、`ContentSource`。
- **发文名称标题**在 `div.wenzhang-title`（渲染 `data.docTitle`）。
- **发文函号**在独立 DOM 元素 `[ng-bind-html*="data.documentNo"]`（如 `金复〔2026〕240号`、`苏金复〔2026〕139号`）——不在正文。
- **正文**在 `#wenzhang-content`（渲染 `data.docClob`，Word 导出 HTML，含内联 CSS）。去 `<style>` 与标签后为纯文本批复。
- **发文日期**在正文末尾中文日期「2026年5月14日」。
- **机构名称**在正文开头收件人「{机构名称}：」。
- **人员/职务**在批复句「核准{姓名列表}等N人...{职务}的任职资格」，一文可能多组职务。

## 流程

```
列表发现 discover_doc_ids（AsyncStealthySession，不变）
  → 标题过滤：仅 ArticleTitle 含「任职资格」的 doc
  → 跳过过滤：djg_data 中已存在该 doc_id 的整 doc 跳过
  → 详情抽取（AsyncDynamicSession 持久浏览器，Semaphore 并发 + download_delay）：
        url = build_detail_html_url(doc_id)
        page = await session.fetch(url, network_idle=True, ...)
        抽取 9 字段 → 多行（一人一行）
  → 写入 zbd_crawler_data.djg_data（ON CONFLICT (doc_id,person_name) DO NOTHING）
```

详情 URL 模板（替换 docId）：
`https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId={docId}&itemId=4111&generaltype=0`

## 抽取规则（hybrid：可靠字段用代码，歧义字段用 LLM）

抽取逻辑集中在 `crawlers/nfra_extractor.py`。结构化 meta 用选择器（更稳、省 token），正文派生、易错的「人→职务」映射交百炼 LLM。输入为已渲染 HTML + doc_id，输出为 `list[dict]`（每行一 dict）。

| 字段（列） | 来源 | 方式 |
|------|------|------|
| `doc_id` | URL 参数 | 代码：调用方传入 |
| `doc_url`（发文链接） | 构造 | 代码：详情页 URL |
| `doc_title`（发文名称） | meta | 代码：`meta[name=ArticleTitle]` 的 content |
| `issuing_authority`（发文监管机构） | 标题前缀 | 代码：`ArticleTitle` 中「关于」之前的部分 |
| `doc_number`（发文函号） | DOM 元素 | 代码：`[ng-bind-html*="data.documentNo"]` 文本去空白；缺失空串 |
| `issue_date` / `institution_name` / `person_name` / `position` | 批复正文 | **LLM 抽取**（见下方 prompt） |

### LLM 抽取设计

- 模型：百炼 `qwen3.5-35b-a3b`，OpenAI 兼容 API（`base_url=https://dashscope.aliyuncs.com/compatible-mode/v1`，`api_key` 来自 `DASHSCOPE_API_KEY`）。
- 输入：批复标题（`doc_title`）+ 清洗后正文 prose（去 `<style>`、去标签、去多余空白）。
- 输出：强制 `response_format={"type":"json_object"}`，schema `{"rows":[{"person_name":"","position":"","institution_name":"","issue_date":""}]}`。
- 合并：LLM 每行与代码侧 doc_id/doc_title/doc_url/doc_number/issuing_authority 拼成完整行。
- 校验：解析后过滤空/非法行（person_name 必须非空 2-4 汉字；position/institution/issue_date 缺则空串）。无行则该 doc 不产出（符合「只处理任职资格类」）。

### Prompt（固化在抽取模块）

**System**
```
你是一个金融监管文件信息抽取助手，专门从「金融机构人员任职资格批复」正文中抽取结构化信息。
严格按规则抽取，只输出 JSON，不要任何解释或多余文字。
```

**User**（`{title}`/`{prose}` 运行时填充）
```
任务：从下方批复正文中，为每一位被核准任职资格的人员抽取一行记录。

批复标题：{title}
批复正文：
{prose}

抽取字段：
- person_name：人员姓名（2-4 个汉字，从「核准……的任职资格」句中提取）
- position：职务（核准其任职资格的岗位，如 董事/独立董事/监事/监事会主席/董事长/行长/副行长/总经理/副总经理 等，取原文措辞）
- institution_name：被批复的金融机构全称（如「苏州银行股份有限公司」，取正文收件人）
- issue_date：发文日期（正文末尾的中文日期，格式 YYYY年M月D日，如 2026年5月14日）

规则：
1. 一人一行。若一句「核准 A、B、C 等3人……董事的任职资格」核准多人同一职务，拆为多行，职务相同。
2. 若不同句核准不同职务（如有的任董事、有的任独立董事），各自取对应职务。
3. 人员姓名必须是真实人名，不得包含机构名、标点或「等N人」。
4. 若正文不属于人员任职资格批复（无「核准……任职资格」内容），返回 {"rows": []}。
5. 严格输出 JSON，schema：{"rows":[{"person_name":"","position":"","institution_name":"","issue_date":""}]}，无其他文字。

示例输入标题：江苏金融监管局关于张伟等6人苏州银行董事、独立董事任职资格的批复
示例输入正文：苏金复〔2026〕139号 苏州银行股份有限公司：……一、核准张伟、毛竹春、蒋亮等3人苏州银行股份有限公司董事的任职资格；核准夏平、赵欣、吴杰等3人苏州银行股份有限公司独立董事的任职资格。……2026年5月14日
示例输出：{"rows":[{"person_name":"张伟","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"},{"person_name":"毛竹春","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"},{"person_name":"蒋亮","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"},{"person_name":"夏平","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"},{"person_name":"赵欣","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"},{"person_name":"吴杰","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}]}
```

已验证样本（LLM 应产出）：
- 江苏 docId=1258343 → 6 行（张伟/毛竹春/蒋亮=董事，夏平/赵欣/吴杰=独立董事，机构=苏州银行股份有限公司，日期=2026年5月14日）
- 总局 docId=1258731 → 1 行（姜亦峰=董事，机构=太平洋健康保险股份有限公司，日期=2026年5月7日）

## djg_data 表结构（zbd_crawler_data 库，与 web_snapshot 同库）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | bigserial PK | 自增主键 |
| `doc_id` | bigint | 文档 ID（多行相同） |
| `issue_date` | text | 发文日期 |
| `issuing_authority` | text | 发文监管机构 |
| `doc_number` | text | 发文函号 |
| `institution_name` | text | 机构名称 |
| `person_name` | text | 人员姓名 |
| `position` | text | 职务 |
| `doc_title` | text | 发文名称 |
| `doc_url` | text | 发文链接 |
| `crawl_time` | timestamptz | 抓取时间，默认 now() |

- 唯一约束 `(doc_id, person_name)`，写入 `ON CONFLICT (doc_id, person_name) DO NOTHING`。
- 跳过粒度：详情抓取前查 `djg_data` 已有 doc_id 集合，整 doc 跳过。
- 列名英文 snake_case，与 web_snapshot 风格一致。
- 表由脚本启动时 `CREATE TABLE IF NOT EXISTS`，不纳入 Alembic。

## 组件

### 1. `src/web_scraper_service/crawlers/nfra_extractor.py`（新建）

抽取模块：
- 代码侧选择器/解析（纯函数，可单测）：`_meta(html, name) -> str`、`_clean_prose(html) -> str`（去 `<style>`、去标签、压空白）、`_doc_title(html) -> str`、`_authority(title) -> str`、`_doc_number(html) -> str`。
- `SYSTEM_PROMPT` / `user_prompt(title, prose)` 模板（上文 prompt 固化为常量/函数）。
- `async extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict]`：主入口。代码侧取 doc_title/prose/doc_number/authority，调百炼 LLM 抽 person_name/position/institution_name/issue_date，校验合并为完整行列表。
- 百炼客户端：`AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.bailian_base_url)`，`model=settings.bailian_model`，`response_format={"type":"json_object"}`。
- tenacity 重试（429/503 指数退避，最多 3 次）；LLM 调用或解析失败返回 []，记日志。
- 校验：person_name 非空且 `fullmatch(r'[一-龥]{2,4}')`，否则丢弃该行；其他字段缺则空串。

### 2. `src/web_scraper_service/storage/djg_data.py`（新建）

独立于 web_snapshot 的存储（同库不同表）：
- `DjgData` 模型（`_DjgBase(DeclarativeBase)`，与 snapshot.py 同库连接 `snapshot_engine`）。
- `DjgDataRepo`：`async existing_doc_ids(doc_ids: set[int]) -> set[int]`、`async insert_many(rows: list[dict]) -> int`（`ON CONFLICT (doc_id, person_name) DO NOTHING`）。
- `init_djg_table()`：`CREATE TABLE IF NOT EXISTS`。
- 复用 `snapshot.py` 的 `snapshot_engine` / `SnapshotSession`（同库），不新建 engine。

### 3. `src/web_scraper_service/crawlers/nfra.py`（修改）

- 新增 `build_detail_html_url(doc_id: int) -> str`。
- 新增 `parse_doc_rows(body) -> list[dict]`：解析列表 rows，每项含 `docId`(int)、`docTitle`(str)。保留 `parse_doc_ids` 向后兼容。
- 新增 `discover_doc_rows(session, item_id, pages) -> list[dict]`：列表发现返回含标题的行。
- `run_crawl` 详情阶段替换：
  - 标题过滤：`[r for r in rows if "任职资格" in r["docTitle"]]`。
  - 跳过：`DjgDataRepo.existing_doc_ids`。
  - `AsyncDynamicSession` 持久浏览器，并发（Semaphore=默认 2，浏览器+LLM 双开销）+ download_delay。
  - 每页 `session.fetch(html_url, network_idle=True)` → `extract_rows_llm` → 收集行。
  - 写入 `djg_data`。
- 返回统计：discovered / pending / extracted_rows / stored。

### 4. `scripts/crawl_nfra.py`（微调）

默认参数调整（详情改浏览器+LLM，并发降为 2，download_delay 1.0），日志输出 extracted_rows。

### 5. `tests/test_nfra/test_extractor.py`（新建）

代码侧解析 + LLM 合并逻辑单测（mock 百炼响应，不打网络）：
- `_meta`/`_doc_title`/`_authority`/`_doc_number`/`_clean_prose`：用两样本 fixture（总局 1258731、江苏 1258343）HTML 校验。
- `extract_rows_llm`：mock `AsyncOpenAI` 返回示例 JSON，校验合并后行数与字段；mock 返回 `{"rows":[]}` → []。
- 边界：无批复内容 → []；标题无「关于」→ authority 取整标题。
- LLM 真实调用靠 smoke 验收。

## 标题过滤时机

在**列表阶段**即按标题含「任职资格」过滤，避免为非任职类 doc 打开详情页（省浏览器开销）。

- 列表解析改为同时取 `(docId, docTitle)`：新增 `parse_doc_rows(body) -> list[dict]`（每项含 `docId`、`docTitle`），保留原 `parse_doc_ids` 向后兼容。
- 列表发现改为 `discover_doc_rows(session, item_id, pages) -> list[dict]`，返回含标题的行。
- 标题过滤：`[r for r in rows if "任职资格" in r["docTitle"]]`。
- `run_crawl` 用过滤后的 docId 列表进入详情阶段。

## 配置与依赖（新增）

- `pyproject.toml` 依赖加 `openai>=1.50`（百炼用 OpenAI 兼容 SDK）。
- `src/web_scraper_service/config.py` 新增：
  - `dashscope_api_key: str = ""`
  - `bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"`
  - `bailian_model: str = "qwen3.5-35b-a3b"`
- `.env.example` 加 `DASHSCOPE_API_KEY=`、`BAILIAN_MODEL=`。

## 错误处理

| 场景 | 处理 |
|------|------|
| 详情页打开失败 | tenacity 重试 2 次；最终失败记录日志跳过 |
| LLM 调用失败（429/503/超时） | tenacity 指数退避重试 3 次；最终失败返回 []，日志记录 |
| LLM 返回非法 JSON 或缺 rows | 解析失败返回 []，日志记录 |
| 抽取返回 0 行（非任职类或解析失败） | 跳过写入，日志记录 |
| 写入单批失败 | 回滚，记录失败 doc_id |
| 浏览器会话异常 | 整体退出并报错 |

## 测试

- `test_extractor.py`：代码侧解析 + LLM 合并逻辑（mock `AsyncOpenAI`，不打网络）。
- LLM 真实调用 + 详情浏览器路径：手动 smoke 验收（`--pages 1`）。
- 既有 `test_parse.py` 列表纯逻辑测试不回归。

## 改动文件清单

| 文件 | 动作 |
|------|------|
| `pyproject.toml` | 修改 — 加 `openai>=1.50` 依赖 |
| `src/web_scraper_service/config.py` | 修改 — 加 dashscope_api_key / bailian_base_url / bailian_model |
| `.env.example` | 修改 — 加 `DASHSCOPE_API_KEY=`、`BAILIAN_MODEL=` |
| `src/web_scraper_service/crawlers/nfra_extractor.py` | 新建 — 代码侧解析 + LLM 抽取 |
| `src/web_scraper_service/storage/djg_data.py` | 新建 — 模型 + repo + init_table |
| `src/web_scraper_service/crawlers/nfra.py` | 修改 — 详情阶段改 DynamicSession+LLM抽取+写 djg_data；标题过滤；build_detail_html_url；parse_doc_rows/discover_doc_rows |
| `scripts/crawl_nfra.py` | 微调 — 默认参数（并发2/delay1.0）+ 日志 |
| `tests/test_nfra/test_extractor.py` | 新建 — 抽取单测（mock LLM） |
| `tests/test_nfra/fixtures/` | 新建 — 两样本 HTML fixture |

## 注意事项

- 详情改用浏览器（DynamicFetcher）+ LLM 后，速度显著慢于原 HTTP 方案；默认并发降为 2、download_delay 1.0。
- `AsyncDynamicSession` 为持久浏览器会话，复用 tabs；并发由外层 Semaphore 控制。
- 人员/职务/机构/日期抽取交 LLM，对批复措辞鲁棒性远好于正则；但依赖百炼可用性与 `DASHSCOPE_API_KEY`，smoke 前需配置。
- 模型 id `qwen3.5-35b-a3b` 由用户确认；smoke 阶段实调，若 id 不符需用户提供正确 id。
- web_snapshot 相关代码保留不动（不删，避免影响已验证的存储层）；仅 `run_crawl` 不再调用。
- 抽取规则固化在独立模块；后续类似网页只需改 `nfra_extractor.py` 的选择器与 prompt。
