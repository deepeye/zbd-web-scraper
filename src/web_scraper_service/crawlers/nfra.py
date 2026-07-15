"""nfra.gov.cn 文档采集：纯逻辑 + 异步编排。

纯逻辑（build_*/parse_doc_rows）可单测；异步编排（discover_doc_rows /
run_crawl）涉及网络与浏览器，靠手动 smoke 验收。结果写入独立库
zbd_crawler_data.djg_data（经 storage/djg_data.py）。
"""

from __future__ import annotations

import asyncio
import enum
import json
from typing import Any
from urllib.parse import quote, urlparse

from loguru import logger

from web_scraper_service.crawlers.nfra_extractor import extract_rows_llm
from web_scraper_service.storage.djg_data import DjgDataRepo, init_djg_table
from web_scraper_service.storage.snapshot import SnapshotSession

BASE = "https://www.nfra.gov.cn"


def build_list_url(item_id: int, page: int, page_size: int = 18) -> str:
    return (
        f"{BASE}/cbircweb/DocInfo/SelectDocByItemIdAndChild"
        f"?itemId={item_id}&pageSize={page_size}&pageIndex={page}"
    )


def build_detail_html_url(doc_id: int) -> str:
    return f"{BASE}/cn/view/pages/ItemDetail.html?docId={doc_id}&itemId=4111&generaltype=0"


def build_list_html_url(item_id: int) -> str:
    return (
        f"{BASE}/cn/view/pages/ItemList.html?itemPId=923&itemId={item_id}"
        f"&itemUrl=ItemListRightList.html&itemName=zhujiguan"
    )


class _PageStatus(enum.Enum):
    HAS_DATA = "has_data"  # rptCode=200, rows 非空
    EMPTY = "empty"  # rptCode=200, rows=[]（数据真正结束）
    ERROR = "error"  # 非 JSON / HTML 错误 / rptCode≠200（网络/反爬拦截）


def _check_response(body: str | bytes) -> _PageStatus:
    """检查 API 响应状态，区分正常数据、真正无数据、错误响应。"""
    raw = body
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="replace")
        except Exception:
            logger.warning("API 响应 bytes 解码失败，原始长度: {}", len(raw) if isinstance(raw, bytes) else 0)
            return _PageStatus.ERROR
    try:
        payload: dict[str, Any] = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("API 响应非 JSON，原始内容: {}", str(body)[:300])
        return _PageStatus.ERROR
    if payload.get("rptCode") != 200:
        logger.warning("API rptCode 异常: rptCode={}, msg={}", payload.get("rptCode"), payload.get("msg", ""))
        return _PageStatus.ERROR
    rows = (payload.get("data") or {}).get("rows")
    if isinstance(rows, list) and len(rows) > 0:
        return _PageStatus.HAS_DATA
    return _PageStatus.EMPTY


def parse_doc_rows(body: str | bytes) -> list[dict[str, Any]]:
    """解析列表接口响应，返回 [{'docId': int, 'docTitle': str}, ...]。"""
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
    out: list[dict[str, Any]] = []
    for row in rows:
        doc_id = row.get("docId")
        if isinstance(doc_id, int):
            out.append({"docId": doc_id, "docTitle": str(row.get("docTitle") or "")})
        elif isinstance(doc_id, str) and doc_id.isdigit():
            out.append({"docId": int(doc_id), "docTitle": str(row.get("docTitle") or "")})
    return out


# ── 异步编排（网络/浏览器，靠手动 smoke 验收）──────────────

_MAX_ERROR_RETRIES = 3
_ERROR_BACKOFF_BASE = 5.0  # 首次重试等 5s，之后指数退避


async def _make_list_session(item_id: int, proxy: str | None = None) -> Any:
    """创建浏览器 session 并导航到列表页，返回已就绪的 session。

    若指定代理失败，不回退无代理，直接抛出异常由上层重试下一个代理。
    """
    if proxy:
        proxy_str = _build_proxy_url(proxy)
        try:
            session = await _try_make_session(item_id, proxy_str)
            logger.info("代理 {} 连接成功", proxy)
            return session
        except Exception as exc:
            logger.warning("代理 {} 连接失败: {}", proxy, exc)
            raise
    return await _try_make_session(item_id, None)


async def _rebuild_session_with_proxy(
    item_id: int,
    pool: Any,
    current_proxy: str | None,
) -> tuple[Any, str | None]:
    """代理失败时逐个切换缓存中的代理，全部耗尽后等 5 分钟刷新再试。"""
    from web_scraper_service.fetchers.dynamic_proxy import DynamicProxyPool

    if pool is not None and current_proxy is not None:
        pool.mark_failed(current_proxy)

    if pool is not None:
        for _round in range(2):  # 最多两轮：当前批次 + 刷新后批次
            while True:
                next_proxy = pool.get_next()
                if next_proxy is None:
                    break  # 当前批次已全部尝试
                try:
                    session = await _make_list_session(item_id, proxy=next_proxy)
                    logger.info("代理 {} 可用", next_proxy)
                    return session, next_proxy
                except Exception as exc:
                    logger.warning("代理 {} 连接失败: {}", next_proxy, exc)
                    pool.mark_failed(next_proxy)

            # 当前批次全部失败，刷新后重试
            if pool.is_exhausted():
                await pool.wait_and_refresh()
            else:
                break  # 没有代理了（不应该到这里）

    # 全部失败，回退无代理
    logger.warning("所有代理不可用，回退到无代理模式")
    session = await _make_list_session(item_id, proxy=None)
    return session, None


def _build_proxy_url(server: str | None = None) -> str | None:
    """构建带认证的代理 URL。

    兼容 server 的多种格式：
    - ip:port
    - http://ip:port
    - user:pass@ip:port
    - http://user:pass@ip:port

    对用户名/密码做 URL encode，避免特殊字符破坏代理 URL。
    """
    from web_scraper_service.config import settings

    if not settings.proxy_enabled:
        return None

    raw = server
    if not raw:
        proxies = settings.proxies
        raw = proxies[0] if proxies else None
    if not raw:
        return None

    raw = raw.strip()
    if not raw:
        return None

    # proxy_pool_auth_* 仅对动态代理池生效；静态代理列表使用字符串内嵌认证
    is_dynamic_pool = bool(settings.proxy_pool_url)
    user: str | None = (settings.proxy_pool_auth_key or None) if is_dynamic_pool else None
    pwd: str | None = (settings.proxy_pool_auth_pwd or None) if is_dynamic_pool else None
    scheme = "http"
    host: str | None = None
    port: int | None = None
    parsed_user: str | None = None
    parsed_pwd: str | None = None

    if "://" in raw:
        parsed = urlparse(raw)
        scheme = parsed.scheme or scheme
        host = parsed.hostname
        port = parsed.port
        parsed_user = parsed.username
        parsed_pwd = parsed.password
    else:
        host_part = raw
        if "@" in raw:
            creds, host_part = raw.rsplit("@", 1)
            if ":" in creds:
                parsed_user, parsed_pwd = creds.split(":", 1)
        if ":" in host_part:
            host, port_str = host_part.rsplit(":", 1)
            if port_str.isdigit():
                port = int(port_str)
        else:
            host = host_part

    # 配置中的认证优先级高于 server 字符串中的认证
    if not user:
        user = parsed_user
        pwd = parsed_pwd or pwd

    if not host:
        return None

    server_part = f"{host}:{port}" if port else host
    if user:
        user_q = quote(user, safe="")
        pwd_q = quote(pwd or "", safe="")
        return f"{scheme}://{user_q}:{pwd_q}@{server_part}"
    return f"{scheme}://{server_part}"


def _mask_proxy_url(proxy_url: str | None) -> str:
    """打印用：隐藏代理认证信息。"""
    if not proxy_url:
        return "无代理"
    parsed = urlparse(proxy_url)
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    return f"{server} (with auth)" if parsed.username else server


async def _try_make_session(item_id: int, proxy: str | None) -> Any:
    """创建 session 并导航到列表页。"""
    from scrapling.fetchers import AsyncStealthySession

    kwargs: dict[str, Any] = {"headless": True}
    proxy_url: str | None = None
    if proxy:
        proxy_url = _build_proxy_url(proxy)
        if proxy_url:
            kwargs["proxy"] = proxy_url
            logger.info("nfra 列表页使用代理: {}", _mask_proxy_url(proxy_url))
        else:
            logger.warning("nfra 列表页代理配置无效，server={}", proxy)
    session = AsyncStealthySession(**kwargs)
    await session.__aenter__()
    try:
        await session.fetch(build_list_html_url(item_id), network_idle=True, timeout=15000)
    except Exception:
        await session.__aexit__(None, None, None)
        raise
    return session


async def _close_session(session: Any) -> None:
    """安全关闭浏览器 session。"""
    try:
        await session.__aexit__(None, None, None)
    except Exception:
        pass


async def discover_doc_rows(
    item_id: int,
    pages: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """用浏览器持久会话遍历列表 API，返回含标题的行和当前使用的代理 server。

    遇到 403/网络错误时切换代理重建 session 并重试，不因反爬拦截而停止翻页。
    只有当 API 返回真正的空数据（rptCode=200, rows=[]）时才停止。
    """
    from web_scraper_service.config import settings
    from web_scraper_service.fetchers.dynamic_proxy import DynamicProxyPool, get_proxy_pool

    pool: DynamicProxyPool | None = None
    current_proxy: str | None = None
    if settings.proxy_enabled and settings.proxy_pool_url:
        pool = get_proxy_pool()
        await pool.start()
        current_proxy = pool.get_next()
        if current_proxy:
            logger.info("使用代理 {} 开始采集", current_proxy)

    rows: list[dict[str, Any]] = []
    session = await _make_list_session(item_id, proxy=current_proxy)
    consecutive_errors = 0

    try:
        for page in range(1, pages + 1):
            url = build_list_url(item_id, page)
            try:
                resp = await session.fetch(url)
                status = _check_response(resp.body)
            except Exception as exc:
                logger.warning("列表第 {} 页请求异常: {}", page, exc)
                status = _PageStatus.ERROR

            if status == _PageStatus.EMPTY:
                logger.info("列表第 {} 页无数据，停止翻页", page)
                break

            if status == _PageStatus.ERROR:
                consecutive_errors += 1

                if consecutive_errors > _MAX_ERROR_RETRIES:
                    logger.warning("连续 {} 次错误，跳过第 {} 页继续", consecutive_errors, page)
                    consecutive_errors = 0
                    continue

                backoff = _ERROR_BACKOFF_BASE * (2 ** (consecutive_errors - 1))
                logger.warning("列表第 {} 页响应异常，{}s 后切换代理重试", page, backoff)
                await asyncio.sleep(backoff)

                await _close_session(session)
                session, current_proxy = await _rebuild_session_with_proxy(
                    item_id, pool, current_proxy,
                )
                proxy_info = f"代理 {current_proxy}" if current_proxy else "无代理"
                logger.info("切换到 {}，重试第 {} 页", proxy_info, page)

                # 重试当前页
                try:
                    resp = await session.fetch(url)
                    status = _check_response(resp.body)
                except Exception as exc:
                    logger.warning("列表第 {} 页重试仍失败: {}", page, exc)
                    status = _PageStatus.ERROR
                if status != _PageStatus.HAS_DATA:
                    logger.warning("列表第 {} 页重试后仍无数据，跳过继续", page)
                    continue
                consecutive_errors = 0
                # fall through to HAS_DATA handling below

            if status == _PageStatus.HAS_DATA:
                consecutive_errors = 0
                page_rows = parse_doc_rows(resp.body)
                rows.extend(page_rows)
                logger.info("列表第 {} 页获得 {} 条，累计 {}", page, len(page_rows), len(rows))
                await asyncio.sleep(0.5)
    finally:
        await _close_session(session)
        if pool is not None:
            await pool.stop()

    return rows, current_proxy


async def _fetch_detail_rows(
    session: Any,
    doc_id: int,
    doc_url: str,
    download_delay: float,
) -> list[dict[str, Any]]:
    """打开详情 HTML，LLM 抽取，返回 djg_data 行列表。"""
    try:
        resp = await session.fetch(doc_url, network_idle=True, timeout=60000)
        # 用渲染后 DOM（html_content），非原始响应体 body——抽取器选择器针对渲染 DOM 设计。
        # body 是 angular 模板壳子，分局文档的 doc_number 仅在渲染后正文/绑定元素中可见。
        html = resp.html_content or ""
        rows = await extract_rows_llm(doc_id, html, doc_url)
        return rows
    except Exception as exc:
        logger.error("详情 doc_id={} 抽取失败: {}", doc_id, exc)
        return []
    finally:
        if download_delay > 0:
            await asyncio.sleep(download_delay)


async def run_crawl(
    item_id: int = 4110,
    pages: int = 5,
    concurrency: int = 2,
    download_delay: float = 1.0,
) -> dict[str, Any]:
    """完整编排：列表发现 → 标题过滤 → 跳过已存在 → 详情抽取 → 写 djg_data。"""
    from scrapling.fetchers import AsyncDynamicSession

    await init_djg_table()

    # 阶段 1：列表发现（含标题）
    rows, current_proxy = await discover_doc_rows(item_id, pages)
    logger.info("共发现 {} 条文档", len(rows))
    if not rows:
        return {"discovered": 0, "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 2：标题过滤（仅任职资格类）
    qualified = [r for r in rows if "任职资格" in r["docTitle"]]
    logger.info("标题含「任职资格」 {} 条", len(qualified))
    if not qualified:
        return {"discovered": len(rows), "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 3：跳过已存在
    pending_ids = [r["docId"] for r in qualified]
    async with SnapshotSession() as db:
        repo = DjgDataRepo(db)
        existing = await repo.existing_doc_ids(set(pending_ids))
    pending = [r for r in qualified if r["docId"] not in existing]
    logger.info("待抓取 {} 个（已存在 {} 个）", len(pending), len(existing))
    if not pending:
        return {"discovered": len(rows), "pending": 0, "extracted_rows": 0, "stored": 0}

    # 阶段 4+5：详情抽取并逐 doc 写入（边抽边写，崩溃只丢当前未完成 doc）
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(r: dict[str, Any]) -> tuple[int, int]:
        """抽一个 doc 的行，立即写入 djg_data；返回 (extracted, stored)。"""
        async with sem:
            batch = await _fetch_detail_rows(
                session, r["docId"], build_detail_html_url(r["docId"]), download_delay
            )
        if not batch:
            return 0, 0
        async with SnapshotSession() as db:
            repo = DjgDataRepo(db)
            stored = await repo.insert_many(batch)
        logger.info("doc_id={} 抽取 {} 行，写入 {} 行", r["docId"], len(batch), stored)
        return len(batch), stored

    async with AsyncDynamicSession(headless=True, proxy=_build_proxy_url(current_proxy)) as session:
        results = await asyncio.gather(*(_guarded(r) for r in pending))
    extracted_rows = sum(r[0] for r in results)
    stored = sum(r[1] for r in results)
    logger.info("抽取行数 {}，写入 djg_data {} 行", extracted_rows, stored)
    return {
        "discovered": len(rows),
        "pending": len(pending),
        "extracted_rows": extracted_rows,
        "stored": stored,
    }
