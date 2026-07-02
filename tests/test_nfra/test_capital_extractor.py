from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scraper_service.crawlers.nfra_capital_extractor import (
    build_user_prompt,
    extract_rows_llm,
    is_capital_candidate,
    parse_llm_rows,
)

CAPITAL_HTML = """
<html><head>
<meta name="ArticleTitle" content="江苏金融监管局关于南京银行股份有限公司变更注册资本的批复">
<meta name="PubDate" content="2025-11-20">
</head><body>
<div class="wenzhang-title">江苏金融监管局关于南京银行股份有限公司变更注册资本的批复</div>
<div ng-bind-html="data.documentNo">苏金复〔2025〕411号</div>
<div class="xxgkInfoTablePlain xxyywidth">发布时间: 2025-11-20</div>
<div id="wenzhang-content">南京银行股份有限公司：同意你行注册资本由10,007,016,973元变更为12,363,567,245元。2025年11月20日</div>
</body></html>
"""

OPENING_HTML = """
<html><head>
<meta name="ArticleTitle" content="国家金融监督管理总局关于瑞众人寿保险有限责任公司及其分支机构开业的批复">
<meta name="PubDate" content="2023-07-28">
</head><body>
<div class="wenzhang-title">国家金融监督管理总局关于瑞众人寿保险有限责任公司及其分支机构开业的批复</div>
<div ng-bind-html="data.documentNo">金复〔2023〕88号</div>
<div id="wenzhang-content">瑞众人寿保险有限责任公司：同意瑞众人寿保险有限责任公司开业，注册资本565亿元。其分支机构同时开业。2023年7月28日</div>
</body></html>
"""


def test_is_capital_candidate() -> None:
    assert is_capital_candidate("江苏金融监管局关于南京银行股份有限公司变更注册资本的批复") is True
    assert is_capital_candidate("国家金融监督管理总局关于瑞众人寿保险有限责任公司开业的批复") is True
    assert is_capital_candidate("江苏金融监管局关于张伟任职资格的批复") is False


def test_build_user_prompt_contains_capital_fields() -> None:
    prompt = build_user_prompt("某标题", "某文号", "某正文")
    assert "registered_capital_before" in prompt
    assert "registered_capital_after" in prompt
    assert "只抽取总公司" in prompt


def test_parse_llm_rows_valid_capital_change() -> None:
    content = json.dumps({"rows": [{
        "issue_date": "2025年11月20日",
        "issuing_authority": "江苏监管局",
        "change_type": "变更注册资本",
        "institution_name": "南京银行股份有限公司",
        "registered_capital_before": "10,007,016,973元",
        "registered_capital_change_method": "可转债转股",
        "change_amount": "",
        "registered_capital_after": "12,363,567,245元",
    }]})
    rows = parse_llm_rows(content)
    assert rows == [{
        "issue_date": "2025年11月20日",
        "issuing_authority": "江苏监管局",
        "change_type": "变更注册资本",
        "institution_name": "南京银行股份有限公司",
        "registered_capital_before": "10,007,016,973元",
        "registered_capital_change_method": "可转债转股",
        "change_amount": "",
        "registered_capital_after": "12,363,567,245元",
    }]


def test_parse_llm_rows_filters_branch_opening() -> None:
    content = json.dumps({"rows": [
        {
            "issue_date": "2023年7月28日",
            "issuing_authority": "国家金融监督管理总局",
            "change_type": "机构成立",
            "institution_name": "瑞众人寿保险有限责任公司",
            "registered_capital_before": "",
            "registered_capital_change_method": "",
            "change_amount": "",
            "registered_capital_after": "565亿元",
        },
        {
            "issue_date": "2023年7月28日",
            "issuing_authority": "国家金融监督管理总局",
            "change_type": "机构成立",
            "institution_name": "瑞众人寿保险有限责任公司北京分公司",
            "registered_capital_before": "",
            "registered_capital_change_method": "",
            "change_amount": "",
            "registered_capital_after": "",
        },
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["institution_name"] == "瑞众人寿保险有限责任公司"


def test_parse_llm_rows_invalid_json() -> None:
    assert parse_llm_rows("not json") == []


@pytest.mark.asyncio
async def test_extract_rows_llm_merges_code_fields() -> None:
    llm_content = json.dumps({"rows": [{
        "issue_date": "2025年11月20日",
        "issuing_authority": "江苏监管局",
        "change_type": "变更注册资本",
        "institution_name": "南京银行股份有限公司",
        "registered_capital_before": "10,007,016,973元",
        "registered_capital_change_method": "可转债转股",
        "change_amount": "",
        "registered_capital_after": "12,363,567,245元",
    }]})
    fake_choice = MagicMock()
    fake_choice.message.content = llm_content
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    with patch("web_scraper_service.crawlers.nfra_capital_extractor.AsyncOpenAI") as mock_client:
        client_inst = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        mock_client.return_value = client_inst
        rows = await extract_rows_llm(1234814, CAPITAL_HTML, "https://www.nfra.gov.cn/x")

    assert len(rows) == 1
    row = rows[0]
    assert row["doc_id"] == 1234814
    assert row["publish_date"] == date(2025, 11, 20)
    assert row["doc_number"] == "苏金复〔2025〕411号"
    assert row["doc_title"].startswith("江苏金融监管局关于")
    assert row["institution_name"] == "南京银行股份有限公司"
