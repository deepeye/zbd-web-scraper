"""nfra 详情页抽取：代码侧选择器解析 + 百炼 LLM 抽取人/职务/机构/日期。

代码侧取 doc_title/issuing_authority/doc_number/clean_prose（可靠、省 token）；
person_name/position/institution_name/issue_date 交百炼 LLM。合并为 djg_data 行。
"""
# ruff: noqa: E501  (long Chinese prompt template lines cannot be wrapped)

from __future__ import annotations

import html as _html
import json
import re
from datetime import date
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from web_scraper_service.config import settings

# ── 代码侧解析 ───────────────────────────────────────────────


def extract_meta(html: str, name: str) -> str:
    """取 <meta name="..."> 的 content。"""
    m = re.search(rf'<meta\s+name="{name}"\s+content="([^"]*)">', html)
    return m.group(1) if m else ""


def doc_title(html: str) -> str:
    return extract_meta(html, "ArticleTitle")


def publish_date(html: str) -> date | None:
    match = re.search(r"发布时间\s*[:：]\s*(\d{4})-(\d{2})-(\d{2})", html)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def issuing_authority(title: str) -> str:
    """标题「关于」之前的部分；无「关于」或属于「无关于」则返回整标题。"""
    idx = title.find("关于")
    if idx == -1 or (idx > 0 and title[idx - 1] == "无"):
        return title
    return title[:idx]


def doc_number(html: str) -> str:
    """发文函号：优先 DOM [ng-bind-html*="data.documentNo"]，回退正文搜索。

    回退时在正文里找所有「XX〔YYYY〕N号」候选，优先含 复/批/监 等批复类字样的
    前缀（避免抓到《请示》里的引用号），取首个。前缀限 2-3 字避免多抓机构名尾字。
    """
    m = re.search(r'ng-bind-html="data\.documentNo[^"]*"[^>]*>([^<]*)<', html)
    if m and m.group(1).strip():
        return re.sub(r"\s+", "", m.group(1))
    norm = re.sub(r"\s+", "", clean_prose(html))
    candidates = [str(c) for c in re.findall(r"[一-龥A-Za-z]{2,3}〔\d{4}〕\d+号", norm)]
    pifu = [c for c in candidates if any(k in c for k in ("复", "批", "监", "通", "准", "核"))]
    if pifu:
        return pifu[0]
    return candidates[0] if candidates else ""


def clean_prose(html: str) -> str:
    """提取 #wenzhang-content 正文，去 style/标签/多余空白。"""
    m = re.search(r'id="wenzhang-content"[^>]*>(.*)', html, re.S)
    body = m.group(1) if m else ""
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()


# ── LLM 抽取 ─────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "你是一个金融监管文件信息抽取助手，专门从「金融机构人员任职资格批复」正文中抽取结构化信息。"
    "严格按规则抽取，只输出 JSON，不要任何解释或多余文字。"
)

_USER_TEMPLATE = """任务：从下方批复正文中，为每一位被核准任职资格的人员抽取一行记录。

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
4. 若正文不属于人员任职资格批复（无「核准……任职资格」内容），返回 {{"rows": []}}。
5. 严格输出 JSON，schema：{{"rows":[{{"person_name":"","position":"","institution_name":"","issue_date":""}}]}}，无其他文字。

示例输入标题：江苏金融监管局关于张伟等6人苏州银行董事、独立董事任职资格的批复
示例输入正文：苏金复〔2026〕139号 苏州银行股份有限公司：……一、核准张伟、毛竹春、蒋亮等3人苏州银行股份有限公司董事的任职资格；核准夏平、赵欣、吴杰等3人苏州银行股份有限公司独立董事的任职资格。……2026年5月14日
示例输出：{{"rows":[{{"person_name":"张伟","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"毛竹春","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"蒋亮","position":"董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"夏平","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"赵欣","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}},{{"person_name":"吴杰","position":"独立董事","institution_name":"苏州银行股份有限公司","issue_date":"2026年5月14日"}}]}}"""


def build_user_prompt(title: str, prose: str) -> str:
    return _USER_TEMPLATE.format(title=title, prose=prose)


def parse_llm_rows(content: str) -> list[dict[str, str]]:
    """解析 LLM 返回的 JSON，校验并过滤非法行。"""
    try:
        payload: dict[str, Any] = json.loads(content)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    rows = payload.get("rows") or []
    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("person_name", "")).strip()
        # 只保留 2-4 汉字的合法人名
        if not re.fullmatch(r"[一-龥]{2,4}", name):
            continue
        out.append({
            "person_name": name,
            "position": str(r.get("position", "")).strip(),
            "institution_name": str(r.get("institution_name", "")).strip(),
            "issue_date": str(r.get("issue_date", "")).strip(),
        })
    return out


def _llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.bailian_base_url,
    )


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _call_llm(title: str, prose: str) -> str:
    client = _llm_client()
    resp = await client.chat.completions.create(
        model=settings.bailian_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(title, prose)},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


async def extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict[str, Any]]:
    """主入口：代码侧取结构化字段，LLM 取人/职务/机构/日期，合并为 djg_data 行。"""
    title = doc_title(html)
    prose = clean_prose(html)
    number = doc_number(html)
    authority = issuing_authority(title)
    code_fields = {
        "doc_id": doc_id,
        "doc_title": title,
        "doc_url": doc_url,
        "doc_number": number,
        "issuing_authority": authority,
        "publish_date": publish_date(html),
    }
    try:
        content = await _call_llm(title, prose)
    except Exception as exc:
        logger.error("LLM 抽取失败 doc_id={}: {}", doc_id, exc)
        return []
    llm_rows = parse_llm_rows(content)
    return [{**code_fields, **r} for r in llm_rows]
