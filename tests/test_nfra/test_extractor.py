"""nfra 抽取模块单测（代码侧解析 + LLM 合并，mock 网络）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scraper_service.crawlers.nfra_extractor import (
    SYSTEM_PROMPT,  # noqa: F401
    build_user_prompt,
    clean_prose,
    doc_number,
    doc_title,
    extract_meta,
    extract_rows_llm,
    issuing_authority,
    parse_llm_rows,
    publish_date,
)

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_HTML = (FIXTURES / "doc_1258731_main.html").read_text(encoding="utf-8")
JS_HTML = (FIXTURES / "doc_1258343_jiangsu.html").read_text(encoding="utf-8")
FUZHOU_HTML = (FIXTURES / "doc_1263743_fuzhou.html").read_text(encoding="utf-8")


def test_extract_meta() -> None:
    assert extract_meta(MAIN_HTML, "ArticleTitle").startswith("国家金融监督管理总局关于")


def test_publish_date_from_rendered_detail_text() -> None:
    assert publish_date(MAIN_HTML) == date(2026, 5, 8)
    assert publish_date(JS_HTML) == date(2026, 5, 14)


def test_publish_date_missing_returns_none() -> None:
    assert publish_date("<html><body>无发布时间</body></html>") is None


def test_publish_date_invalid_parts_returns_none() -> None:
    assert publish_date("<html><body>发布时间：2026-13-40</body></html>") is None


def test_doc_title() -> None:
    assert "姜亦峰" in doc_title(MAIN_HTML)
    assert "张伟" in doc_title(JS_HTML)


def test_issuing_authority() -> None:
    assert issuing_authority("国家金融监督管理总局关于X的批复") == "国家金融监督管理总局"
    assert issuing_authority("江苏金融监管局关于X的批复") == "江苏金融监管局"
    assert issuing_authority("无关于字样的标题") == "无关于字样的标题"


def test_doc_number_dom_path() -> None:
    assert doc_number(MAIN_HTML) == "金复〔2026〕240号"


def test_doc_number_prose_fallback() -> None:
    assert doc_number(JS_HTML) == "苏金复〔2026〕139号"


def test_doc_number_four_char_prefix() -> None:
    """分局批复常用 4 字简称（如 抚金监复 = 抚州金融监管分局 复文），
    限 3 字会截掉首字变成 金监复。"""
    assert doc_number(FUZHOU_HTML) == "抚金监复〔2026〕47号"


def test_doc_number_missing() -> None:
    assert doc_number("<html></html>") == ""


def test_clean_prose() -> None:
    p = clean_prose(MAIN_HTML)
    assert "太平洋健康保险股份有限公司" in p
    assert "<" not in p  # tags stripped


def test_build_user_prompt_contains_fields() -> None:
    prompt = build_user_prompt("某标题", "某正文")
    assert "person_name" in prompt
    assert "position" in prompt
    assert "某标题" in prompt
    assert "某正文" in prompt


def test_parse_llm_rows_valid() -> None:
    content = json.dumps({"rows": [
        {"person_name": "张伟", "position": "董事", "institution_name": "苏州银行股份有限公司", "issue_date": "2026年5月14日"}  # noqa: E501
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["person_name"] == "张伟"


def test_parse_llm_rows_empty() -> None:
    assert parse_llm_rows(json.dumps({"rows": []})) == []


def test_parse_llm_rows_invalid_json() -> None:
    assert parse_llm_rows("not json") == []


def test_parse_llm_rows_filters_invalid_name() -> None:
    content = json.dumps({"rows": [
        {"person_name": "", "position": "董事", "institution_name": "X", "issue_date": "Y"},
        {"person_name": "张伟", "position": "董事", "institution_name": "X", "issue_date": "Y"},
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["person_name"] == "张伟"


@pytest.mark.asyncio
async def test_extract_rows_llm_merges_code_and_llm_fields() -> None:
    """mock LLM 返回 6 行，校验合并后含代码侧字段。"""
    llm_content = json.dumps({"rows": [
        {"person_name": "张伟", "position": "董事", "institution_name": "苏州银行股份有限公司", "issue_date": "2026年5月14日"},  # noqa: E501
        {"person_name": "毛竹春", "position": "董事", "institution_name": "苏州银行股份有限公司", "issue_date": "2026年5月14日"},  # noqa: E501
    ]})
    fake_msg = MagicMock()
    fake_msg.message.content = llm_content
    fake_choice = MagicMock()
    fake_choice.message = fake_msg.message
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    with patch("web_scraper_service.crawlers.nfra_extractor.AsyncOpenAI") as mock_client:
        client_inst = MagicMock()
        client_inst.chat = MagicMock()
        client_inst.chat.completions = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        mock_client.return_value = client_inst

        rows = await extract_rows_llm(
            doc_id=1258343, html=JS_HTML,
            doc_url="https://www.nfra.gov.cn/branch/jiangsu/view/pages/common/ItemDetail.html?docId=1258343",
        )

    assert len(rows) == 2
    r = rows[0]
    assert r["doc_id"] == 1258343
    assert r["doc_title"].startswith("江苏金融监管局关于")
    assert r["issuing_authority"] == "江苏金融监管局"
    assert r["doc_number"] == "苏金复〔2026〕139号"
    assert r["publish_date"] == date(2026, 5, 14)
    assert r["person_name"] == "张伟"
    assert r["position"] == "董事"
    assert r["doc_url"].startswith("https://www.nfra.gov.cn/")


@pytest.mark.asyncio
async def test_extract_rows_llm_empty_rows_returns_empty() -> None:
    llm_content = json.dumps({"rows": []})
    fake_msg = MagicMock()
    fake_msg.message.content = llm_content
    fake_choice = MagicMock()
    fake_choice.message = fake_msg.message
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    with patch("web_scraper_service.crawlers.nfra_extractor.AsyncOpenAI") as mock_client:
        client_inst = MagicMock()
        client_inst.chat = MagicMock()
        client_inst.chat.completions = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        mock_client.return_value = client_inst
        rows = await extract_rows_llm(1, MAIN_HTML, "https://x")
    assert rows == []
