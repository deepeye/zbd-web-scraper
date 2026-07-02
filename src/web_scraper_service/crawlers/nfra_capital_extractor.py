"""LLM extraction for nfra capital change and head-office opening approvals."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from web_scraper_service.config import settings
from web_scraper_service.crawlers.nfra_extractor import (
    clean_prose,
    doc_number,
    doc_title,
    issuing_authority,
    publish_date,
)

SYSTEM_PROMPT = """你是一个金融监管文件信息抽取助手，专门从「金融机构注册资本变更批复」和「总公司开业批复」正文中抽取结构化信息。
严格按规则抽取，只输出 JSON，不要任何解释或多余文字。"""

_USER_TEMPLATE = """任务：从下方批复正文中抽取注册资本变更或总公司开业信息。

批复标题：{title}
发文函号：{number}
批复正文：
{prose}

输出字段：
- issue_date：发文日期，取正文末尾日期，格式保留原文，如 2025年11月20日
- issuing_authority：发文监管机构，如 江苏监管局、国家金融监督管理总局
- change_type：只允许 变更注册资本 或 机构成立
- institution_name：机构名称，必须是被批复的总公司或法人机构全称
- registered_capital_before：变更前注册资本，原文没有则空串
- registered_capital_change_method：注册资本变更方式，如 可转债转股、增加注册资本，原文没有则空串
- change_amount：变更金额，原文没有则空串
- registered_capital_after：变更后注册资本；开业文章写总公司注册资本

规则：
1. 标题或正文属于注册资本变更批复时，change_type 写 变更注册资本。
2. 标题或正文属于总公司开业批复时，change_type 写 机构成立。
3. 开业文章只抽取总公司，不抽取分支机构、分公司、支公司、营业部。
4. 一篇文章如包含多个符合条件的机构，每个机构一行。
5. 如果文章只涉及股权、任职资格、分支机构开业或其他无关内容，返回 {{"rows": []}}。
6. 金额和单位保留原文表达，不做数值归一化。
7. 严格输出 JSON，schema：{{"rows":[{{"issue_date":"","issuing_authority":"","change_type":"","institution_name":"","registered_capital_before":"","registered_capital_change_method":"","change_amount":"","registered_capital_after":""}}]}}。
"""

_FIELDS = (
    "issue_date",
    "issuing_authority",
    "change_type",
    "institution_name",
    "registered_capital_before",
    "registered_capital_change_method",
    "change_amount",
    "registered_capital_after",
)
_BRANCH_WORDS = ("分公司", "支公司", "中心支公司", "营业部", "分行", "支行")


def is_capital_candidate(title: str) -> bool:
    return "注册资本" in title or "开业" in title


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
        if normalized["change_type"] not in {"变更注册资本", "机构成立"}:
            continue
        if not normalized["institution_name"]:
            continue
        if _is_branch_opening(normalized):
            continue
        parsed.append(normalized)
    return parsed


def _llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.bailian_base_url)


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
        logger.error("资本变更 LLM 抽取失败 doc_id={}: {}", doc_id, exc)
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
