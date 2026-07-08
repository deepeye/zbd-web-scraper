"""LLM extraction for nfra equity (shareholder) changes and opening shareholders.

从「金融机构股权变更批复」与「总公司开业批复」正文中抽取股东级结构化信息，
写入 zbd_crawler_data.equity_change_data。代码侧取 doc_title/issuing_authority/
doc_number/clean_prose/publish_date（可靠、省 token）；股东与持股字段交百炼 LLM。
"""
# ruff: noqa: E501  (long Chinese prompt template lines cannot be wrapped)

from __future__ import annotations

import json
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
from web_scraper_service.crawlers.nfra_extractor import (
    clean_prose,
    doc_number,
    doc_title,
    issuing_authority,
    publish_date,
)

SYSTEM_PROMPT = """你是一个金融监管文件信息抽取助手，专门从「金融机构股权变更批复」和「总公司开业批复」正文中抽取股东级结构化信息。
严格按规则抽取，只输出 JSON，不要任何解释或多余文字。"""

_USER_TEMPLATE = """任务：从下方批复正文中抽取股东变更或总公司开业时的股东信息。

批复标题：{title}
发文函号：{number}
批复正文：
{prose}

输出字段：
- issue_date：发文日期，取正文末尾日期，格式保留原文，如 2026年6月18日
- issuing_authority：发文监管机构，如 湖南监管局、国家金融监督管理总局
- change_type：只允许 变更股权 或 机构成立
- institution_name：被批复的金融机构全称（总公司/法人机构，不取分支机构）
- shareholder_name：股东名称（法人全称或自然人姓名，原文多股东用「、」分隔时拆成多行）
- shareholding_before：变更前持股比例，原文没有则空串
- change_method：变更方式，只允许 转入 或 转出
- transferred_shares：受让股份，原文表达，如 1,748,794,139股、4200万股；原文没有则空串
- transferred_ratio：受让比例，原文表达，如 0.8992；原文没有则空串
- shares_after：变更后股份，原文表达；原文没有则空串
- shareholding_after：变更后持股比例，原文表达，如 0.6；原文没有则空串
- contribution_amount：出资额，原文表达，如 1,748,794,139元；原文没有则空串

规则：
1. 标题或正文属于股权变更批复时，change_type 写 变更股权。
2. 标题或正文属于总公司开业批复时，change_type 写 机构成立，抽取各股东及其认购/出资信息。
3. 开业文章只抽取总公司股东，不抽取分支机构、分公司、支公司、营业部。
4. 一位股东或一组股东一行；同一股东既有转入又有转出时，按原文方向各成一行。
5. 股权变更必须同时抽取转入方和转出方，两边缺一不可。转入方是受让股权的主体，转出方是出让股权的主体。
6. 原文「A、B、C合计」「A、B、C等N人」表示一组股东合计持有同一比例，保留原文名称序列不变，合并为一行，不拆开。
7. 如果文章只涉及任职资格、注册资本变更（无股东信息）或其他无关内容，返回 {{"rows": []}}。
8. 比例、股份、金额保留原文表达，不做数值归一化。
9. 严格输出 JSON，schema：{{"rows":[{{"issue_date":"","issuing_authority":"","change_type":"","institution_name":"","shareholder_name":"","shareholding_before":"","change_method":"","transferred_shares":"","transferred_ratio":"","shares_after":"","shareholding_after":"","contribution_amount":""}}]}}。
"""

_FIELDS = (
    "issue_date",
    "issuing_authority",
    "change_type",
    "institution_name",
    "shareholder_name",
    "shareholding_before",
    "change_method",
    "transferred_shares",
    "transferred_ratio",
    "shares_after",
    "shareholding_after",
    "contribution_amount",
)
_BRANCH_WORDS = ("分公司", "支公司", "中心支公司", "营业部", "分行", "支行")
_CHANGE_METHODS = {"转入", "转出"}


def is_equity_candidate(title: str) -> bool:
    return "股权" in title or "开业" in title


def build_user_prompt(title: str, number: str, prose: str) -> str:
    return _USER_TEMPLATE.format(title=title, number=number, prose=prose)


def _is_branch_opening(row: dict[str, str]) -> bool:
    if row.get("change_type") != "机构成立":
        return False
    institution = row.get("institution_name", "")
    return any(word in institution for word in _BRANCH_WORDS)


def parse_llm_rows(content: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = {field: str(row.get(field) or "").strip() for field in _FIELDS}
        if normalized["change_type"] not in {"变更股权", "机构成立"}:
            continue
        # 变更股权必须有转入/转出方向；机构成立（开业）股东认购出资无方向，change_method 合法为空。
        if normalized["change_type"] == "变更股权" and normalized["change_method"] not in _CHANGE_METHODS:
            continue
        if not normalized["institution_name"]:
            continue
        if not normalized["shareholder_name"]:
            continue
        if _is_branch_opening(normalized):
            continue
        parsed.append(normalized)
    return parsed


def _llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.bailian_base_url)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _call_llm(title: str, number: str, prose: str) -> str:
    client = _llm_client()
    resp = await client.chat.completions.create(
        model=settings.bailian_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(title, number, prose)},
        ],
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


async def extract_rows_llm(doc_id: int, html: str, doc_url: str) -> list[dict[str, Any]]:
    title = doc_title(html)
    number = doc_number(html)
    prose = clean_prose(html)
    code_authority = issuing_authority(title)
    code_fields = {
        "doc_id": doc_id,
        "publish_date": publish_date(html),
        "doc_number": number,
        "doc_title": title,
        "doc_url": doc_url,
    }
    try:
        content = await _call_llm(title, number, prose)
    except Exception as exc:
        logger.error("股权变更 LLM 抽取失败 doc_id={}: {}", doc_id, exc)
        return []
    llm_rows = parse_llm_rows(content)
    return [
        {
            **code_fields,
            **row,
            "issuing_authority": row["issuing_authority"] or code_authority,
        }
        for row in llm_rows
    ]
