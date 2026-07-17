# NFRA 数据查询默认排序改为 publish_date 倒序

## 背景

`/api/v1/nfra/djg/data`、`/api/v1/nfra/capital/data`、`/api/v1/nfra/equity/data` 三个接口目前默认按照 `crawl_time DESC, id DESC` 排序，并以 `crawl_time` 作为 `start_date`/`end_date` 的过滤字段。业务方希望默认按公文真正的发布日期 `publish_date` 倒序展示，而不是按采集时间。

## 目标

让三个 NFRA 数据查询接口：

1. 默认按 `publish_date` 倒序排列。
2. `start_date`/`end_date` 改为过滤 `publish_date` 范围。
3. `publish_date IS NULL` 的行排在最后。
4. 同日期行按 `id DESC` 作为稳定 tie-breaker。

## 设计

### 方案选择

采用**方案 A：重命名仓库方法以匹配新语义**。

- 方法名从 `list_by_crawl_time` / `count_by_crawl_time` 改为 `list_by_publish_date` / `count_by_publish_date`，避免名不副实。
- 改动文件少，逻辑清晰，无向后兼容包袱（这些方法只在对应的 API 路由中使用）。

### 变更范围

| 文件 | 变更 |
|------|------|
| `src/web_scraper_service/storage/djg_data.py` | 重命名方法；改为按 `publish_date` 过滤/排序 |
| `src/web_scraper_service/storage/capital_change_data.py` | 同上 |
| `src/web_scraper_service/storage/equity_change_data.py` | 同上 |
| `src/web_scraper_service/api/v1/nfra.py` | 调用改为 `list_by_publish_date` / `count_by_publish_date` |
| `tests/test_api/test_nfra.py` | 更新 mock 的方法名 |
| `docs/API.md` | 更新三个接口的排序与参数说明 |

### 数据流

```
GET /api/v1/nfra/{djg|capital|equity}/data
  ?start_date={date from}&end_date={date to}&page=&size=
  → Pagination dependency
  → repo.list_by_publish_date(start_date, end_date, limit, offset)
  → SELECT ...
      WHERE (start_date IS NULL OR publish_date >= start_date)
        AND (end_date IS NULL OR publish_date <= end_date)
      ORDER BY publish_date DESC NULLS LAST, id DESC
      LIMIT ... OFFSET ...
  → repo.count_by_publish_date(...)
  → JSON response with pagination
```

### 边界情况

- `start_date > end_date`：返回空列表（保持现状，不报错）。
- `publish_date IS NULL`：通过 `NULLS LAST` 置于末尾。
- 分页参数非法：由 `Pagination` 依赖统一校验并返回 `422`。

### 索引建议

`publish_date` 成为默认排序和过滤列后，建议在三个表上建立索引：

```sql
CREATE INDEX IF NOT EXISTS idx_djg_data_publish_date
    ON djg_data (publish_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_capital_change_data_publish_date
    ON capital_change_data (publish_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_equity_change_data_publish_date
    ON equity_change_data (publish_date DESC NULLS LAST);
```

可以通过 Alembic migration 或现有的 `init_*_table()` 函数添加。

### 测试计划

1. 更新现有测试，mock 的方法名改为 `list_by_publish_date` / `count_by_publish_date`。
2. 新增断言，验证 `list_by_publish_date` 收到正确的 `start_date`、`end_date`、`limit`、`offset`。
3. 可选：通过检查 SQL 语句或添加集成测试，验证 `ORDER BY publish_date DESC NULLS LAST, id DESC`。

## 决策记录

- 不新增 `sort_by` 查询参数：需求只是改变默认排序，增加参数属于过度设计。
- `start_date`/`end_date` 同步改为过滤 `publish_date`：与默认排序语义一致，避免用户混淆。
- `NULLS LAST`：业务上未标注发布日期的行应该排在最近发布行之后。
