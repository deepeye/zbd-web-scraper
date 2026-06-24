"""nfra.gov.cn 文档快照采集：纯逻辑 + 异步编排。

纯逻辑（build_*/parse_doc_ids/filter_pending）可单测；异步编排
(discover_doc_ids / fetch_snapshots / run_crawl) 涉及网络与浏览器，
靠手动 smoke 验收。
"""

from __future__ import annotations

import json
from typing import Any

BASE = "https://www.nfra.gov.cn"


def build_list_url(item_id: int, page: int, page_size: int = 18) -> str:
    return (
        f"{BASE}/cn/static/data/DocInfo/SelectDocByItemIdAndChild/"
        f"data_itemId={item_id},pageIndex={page},pageSize={page_size}.json"
    )


def build_detail_url(doc_id: int) -> str:
    return f"{BASE}/cn/static/data/DocInfo/SelectByDocId/data_docId={doc_id}.json"


def build_list_html_url(item_id: int) -> str:
    return (
        f"{BASE}/cn/view/pages/ItemList.html?itemPId=923&itemId={item_id}"
        f"&itemUrl=ItemListRightList.html&itemName=zhujiguan"
    )


def parse_doc_ids(body: str | bytes) -> list[int]:
    """解析列表接口响应，返回 docId 列表。rptCode!=200、无 rows、非法 JSON 一律返回 []。"""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            return []
    try:
        payload: dict[str, Any] = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if payload.get("rptCode") != 200:
        return []
    rows = (payload.get("data") or {}).get("rows") or []
    ids: list[int] = []
    for row in rows:
        doc_id = row.get("docId")
        if isinstance(doc_id, int):
            ids.append(doc_id)
    return ids


def filter_pending(doc_ids: list[int], existing: set[int]) -> list[int]:
    """去列表内重复 + 去已存在 doc_id，保持首次出现顺序。"""
    seen: set[int] = set()
    out: list[int] = []
    for doc_id in doc_ids:
        if doc_id in existing or doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out
