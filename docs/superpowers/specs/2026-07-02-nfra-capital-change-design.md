# nfra.gov.cn 注册资本/开业数据抽取 — 设计文档

## 概述

新增一条与现有 `djg_data` 任职资格采集并行的 nfra 数据链路，用于抽取「变更注册资本」与「总公司开业」批复数据。

新链路复用现有 nfra 列表页发现、详情页 URL 构造和动态详情抓取能力，但使用独立标题过滤、LLM prompt、存储表、Celery task 和 API。数据写入 `zbd_crawler_data` 下的新表，不合并到现有 `djg_data`。

列表来源仍为 `item_id=4110` 和 `item_id=4291`。

## 数据来源与过滤规则

列表页来源：

- `item_id=4110`
- `item_id=4291`

标题过滤：

- 保留标题包含「注册资本」的文章。
- 保留标题包含「开业」的文章。
- 「开业」文章只抽取总公司开业数据，不抽取分支机构、分公司、支公司、营业部等分支开业数据。

详情页来源：

- 使用列表页返回的 `docId` 构造并访问发文链接。
- 使用渲染后的 DOM 作为抽取输入，保持与现有 nfra 详情抽取一致。

## 表结构

新增表命名为 `capital_change_data`，位于 `zbd_crawler_data` 库。

字段按 `docs/股东股权变更批复数据结构-0630.xlsx` 的「变更注册资本」sheet 列名映射，并补充系统字段。

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | bigserial PK | 自增主键 |
| `doc_id` | bigint | nfra 文档 ID |
| `publish_date` | date nullable | 网站发布时间，来自详情页 meta |
| `issue_date` | text | 发文日期 |
| `issuing_authority` | text | 发文监管机构 |
| `doc_number` | text | 发文函号 |
| `change_type` | text | 变更类型，如 `变更注册资本`、`机构成立` |
| `institution_name` | text | 机构名称 |
| `registered_capital_before` | text | 变更前注册资本 |
| `registered_capital_change_method` | text | 注册资本变更方式 |
| `change_amount` | text | 变更金额 |
| `registered_capital_after` | text | 变更后注册资本；开业文章写总公司注册资本 |
| `doc_title` | text | 发文名称 |
| `doc_url` | text | 发文链接 |
| `crawl_time` | timestamptz | 抓取时间，默认 `now()` |

唯一约束：`(doc_id, institution_name, change_type)`。

写入策略：`ON CONFLICT DO NOTHING`，重复数据跳过。

建表策略：与现有 `djg_data` 一致，由启动或任务执行时 `CREATE TABLE IF NOT EXISTS` 自愈，不纳入 Alembic。

## 抽取规则

新增资本变更专用 extractor，例如 `src/web_scraper_service/crawlers/nfra_capital_extractor.py`。

代码侧稳定抽取：

- `doc_id`：调用方传入。
- `doc_url`：由详情页 URL 构造。
- `doc_title`：详情页标题或 meta `ArticleTitle`。
- `publish_date`：详情页 meta `PubDate`。
- `doc_number`：详情页文号 DOM。
- `issuing_authority`：优先由标题「关于」之前的部分推导，必要时交给 LLM 校正。

LLM 抽取字段：

- `issue_date`
- `issuing_authority`
- `change_type`
- `institution_name`
- `registered_capital_before`
- `registered_capital_change_method`
- `change_amount`
- `registered_capital_after`

LLM 输入：发文标题、文号、清洗后的正文纯文本。

LLM 输出 schema：

```json
{
  "rows": [
    {
      "issue_date": "",
      "issuing_authority": "",
      "change_type": "",
      "institution_name": "",
      "registered_capital_before": "",
      "registered_capital_change_method": "",
      "change_amount": "",
      "registered_capital_after": ""
    }
  ]
}
```

抽取规则：

1. 标题或正文属于注册资本变更批复时，`change_type` 写 `变更注册资本`。
2. 标题或正文属于总公司开业批复时，`change_type` 写 `机构成立`。
3. 注册资本变更文章应尽量抽取变更前注册资本、变更方式、变更金额和变更后注册资本；原文没有的字段写空串。
4. 开业文章只抽取总公司，不抽取分支机构开业；开业注册资本写入 `registered_capital_after`，其他注册资本变更字段写空串。
5. 一篇文章如包含多个符合条件的机构，每个机构一行。
6. 如果文章只涉及股权、任职资格、分支机构开业或其他无关内容，返回空 `rows`。
7. 金额和单位保留原文表达，不做数值归一化。

## 采集流程

```text
遍历 item_id=4110、4291 的列表页
  → 过滤标题包含「注册资本」或「开业」的文章
  → 查询 capital_change_data 中已有唯一键相关 doc_id，减少重复抓取
  → 使用动态详情页抓取渲染后 DOM
  → 代码抽取稳定字段，LLM 抽取资本/开业字段
  → 按 doc_id + institution_name + change_type 写入 capital_change_data
```

流程应边抽边写，单篇文章失败只影响当前文章，不阻断整批。

## API 与 Celery

新增独立 Celery task，例如 `nfra_capital_crawl_task`，不复用现有 `nfra_crawl_task` 的任职资格语义。

新增独立脚本入口，例如 `scripts/crawl_nfra_capital.py`，供 Celery subprocess 调用，避免 worker 内事件循环和异步 engine 绑定问题。

新增 API：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/nfra/capital/crawl` | 手动触发资本/开业采集，参数支持 `item_id?`、`pages?`；未传 `item_id` 时采集 4110 和 4291 |
| GET | `/api/v1/nfra/capital/crawl/{job_id}` | 查询 Celery 任务状态 |
| GET | `/api/v1/nfra/capital/data` | 按 `crawl_time` 范围分页查询新表数据 |

`POST /crawl` 返回独立 `job_id`。查询接口响应字段直接对应新表字段，保持现有统一响应格式。

## 错误处理

- 列表页单页失败：记录 warning 并停止当前 item_id 翻页，继续其他 item_id。
- 详情页失败：记录 warning，跳过当前文章。
- LLM 调用失败或返回非法 JSON：记录 error，当前文章产出 0 行。
- 数据冲突：由唯一约束跳过，不视为失败。
- API task 状态查询：沿用现有 `AsyncResult` 状态返回模式。

## 测试计划

单元测试：

- 标题过滤只保留「注册资本」和「开业」。
- 开业文章过滤掉分支机构开业，只保留总公司开业。
- LLM JSON 解析能处理合法 rows、空 rows 和非法输出。
- 新 repo `insert_many` 对 `(doc_id, institution_name, change_type)` 冲突跳过。

接口测试：

- `POST /api/v1/nfra/capital/crawl` 返回 `job_id`。
- `GET /api/v1/nfra/capital/crawl/{job_id}` 返回任务状态。
- `GET /api/v1/nfra/capital/data` 返回分页字段和新表字段。

人工验证：

- 用 Excel 中的示例发文链接抽取，核对字段是否能匹配表格样例。
- 验证 `item_id=4110` 与 `4291` 都能发现候选文章。

## 不做范围

- 不做金额数值归一化。
- 不合并到现有 `djg_data`。
- 不改变现有任职资格采集 API、表结构或调度。
- 不抽取股权变更 sheet。