from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web_scraper_service.crawlers.nfra_equity_extractor import (
    build_user_prompt,
    extract_rows_llm,
    is_equity_candidate,
    parse_llm_rows,
)

EQUITY_HTML = """
<html><head>
<meta name="ArticleTitle" content="重庆金融监管局关于重庆小米消费金融有限公司股权变更的批复">
<meta name="PubDate" content="2026-06-18">
</head><body>
<div class="wenzhang-title">重庆金融监管局关于重庆小米消费金融有限公司股权变更的批复</div>
<div ng-bind-html="data.documentNo">渝金管复〔2026〕58号</div>
<div class="xxgkInfoTablePlain xxyywidth">发布时间: 2026-06-18</div>
<div id="wenzhang-content">重庆小米消费金融有限公司：同意小米通讯技术有限公司转入股份15000股，变更后持股比例0.6；同意重庆金山控股（集团）有限公司转出股份15000股。2026年6月18日</div>
</body></html>
"""

OPENING_HTML = """
<html><head>
<meta name="ArticleTitle" content="国家金融监督管理总局关于瑞众人寿保险有限责任公司及其分支机构开业的批复">
<meta name="PubDate" content="2023-07-28">
</head><body>
<div class="wenzhang-title">国家金融监督管理总局关于瑞众人寿保险有限责任公司及其分支机构开业的批复</div>
<div ng-bind-html="data.documentNo">金复〔2023〕88号</div>
<div id="wenzhang-content">瑞众人寿保险有限责任公司：同意瑞众人寿保险有限责任公司开业。九州启航（北京）股权投资基金（有限合伙）出资339亿元，持股比例0.6；中国保险保障基金有限责任公司出资226亿元，持股比例0.4。2023年7月28日</div>
</body></html>
"""


def test_is_equity_candidate() -> None:
    assert is_equity_candidate("重庆金融监管局关于重庆小米消费金融有限公司股权变更的批复") is True
    assert is_equity_candidate("国家金融监督管理总局关于瑞众人寿保险有限责任公司开业的批复") is True
    # 任职资格 / 仅注册资本变更 不属于股权候选
    assert is_equity_candidate("江苏金融监管局关于张伟任职资格的批复") is False
    assert is_equity_candidate("江苏金融监管局关于南京银行股份有限公司变更注册资本的批复") is False


def test_build_user_prompt_contains_equity_fields() -> None:
    prompt = build_user_prompt("某标题", "某文号", "某正文")
    assert "shareholder_name" in prompt
    assert "change_method" in prompt
    assert "只抽取总公司" in prompt


def test_parse_llm_rows_valid_equity_change() -> None:
    content = json.dumps({"rows": [
        {
            "issue_date": "2026年6月18日",
            "issuing_authority": "重庆监管局",
            "change_type": "变更股权",
            "institution_name": "重庆小米消费金融有限公司",
            "shareholder_name": "小米通讯技术有限公司",
            "shareholding_before": "",
            "change_method": "转入",
            "transferred_shares": "15000股",
            "transferred_ratio": "",
            "shares_after": "90000股",
            "shareholding_after": "0.6",
            "contribution_amount": "",
        },
        {
            "issue_date": "2026年6月18日",
            "issuing_authority": "重庆监管局",
            "change_type": "变更股权",
            "institution_name": "重庆小米消费金融有限公司",
            "shareholder_name": "重庆金山控股（集团）有限公司",
            "shareholding_before": "",
            "change_method": "转出",
            "transferred_shares": "15000股",
            "transferred_ratio": "",
            "shares_after": "",
            "shareholding_after": "",
            "contribution_amount": "",
        },
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 2
    assert rows[0]["shareholder_name"] == "小米通讯技术有限公司"
    assert rows[0]["change_method"] == "转入"
    assert rows[1]["change_method"] == "转出"


def test_parse_llm_rows_filters_branch_opening() -> None:
    content = json.dumps({"rows": [
        {
            "issue_date": "2023年7月28日",
            "issuing_authority": "国家金融监督管理总局",
            "change_type": "机构成立",
            "institution_name": "瑞众人寿保险有限责任公司",
            "shareholder_name": "九州启航（北京）股权投资基金（有限合伙）",
            "shareholding_before": "",
            "change_method": "转入",
            "transferred_shares": "339亿元",
            "transferred_ratio": "",
            "shares_after": "",
            "shareholding_after": "0.6",
            "contribution_amount": "339亿元",
        },
        {
            "issue_date": "2023年7月28日",
            "issuing_authority": "国家金融监督管理总局",
            "change_type": "机构成立",
            "institution_name": "瑞众人寿保险有限责任公司北京分公司",
            "shareholder_name": "某股东",
            "shareholding_before": "",
            "change_method": "转入",
            "transferred_shares": "",
            "transferred_ratio": "",
            "shares_after": "",
            "shareholding_after": "",
            "contribution_amount": "",
        },
    ]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["institution_name"] == "瑞众人寿保险有限责任公司"


def test_parse_llm_rows_invalid_change_method_dropped() -> None:
    content = json.dumps({"rows": [{
        "issue_date": "2026年6月18日",
        "issuing_authority": "重庆监管局",
        "change_type": "变更股权",
        "institution_name": "重庆小米消费金融有限公司",
        "shareholder_name": "小米通讯技术有限公司",
        "shareholding_before": "",
        "change_method": "增持",
        "transferred_shares": "15000股",
        "transferred_ratio": "",
        "shares_after": "",
        "shareholding_after": "0.6",
        "contribution_amount": "",
    }]})
    rows = parse_llm_rows(content)
    assert rows == []


def test_parse_llm_rows_opening_empty_change_method() -> None:
    """机构成立（开业）股东认购出资，无转入/转出方向，change_method 为空应保留。

    回归 1253577：百炼对开业批复返回 change_method=""，旧逻辑因不在 {转入,转出}
    整行丢弃，致开业文档 0 入库。
    """
    content = json.dumps({"rows": [{
        "issue_date": "2026年3月16日",
        "issuing_authority": "国家金融监督管理总局",
        "change_type": "机构成立",
        "institution_name": "中邮金融资产投资有限公司",
        "shareholder_name": "中国邮政储蓄银行股份有限公司",
        "shareholding_before": "",
        "change_method": "",
        "transferred_shares": "",
        "transferred_ratio": "",
        "shares_after": "",
        "shareholding_after": "100%",
        "contribution_amount": "100亿元人民币",
    }]})
    rows = parse_llm_rows(content)
    assert len(rows) == 1
    assert rows[0]["change_type"] == "机构成立"
    assert rows[0]["shareholder_name"] == "中国邮政储蓄银行股份有限公司"
    assert rows[0]["contribution_amount"] == "100亿元人民币"


def test_parse_llm_rows_invalid_json() -> None:
    assert parse_llm_rows("not json") == []


@pytest.mark.asyncio
async def test_extract_rows_llm_merges_code_fields() -> None:
    llm_content = json.dumps({"rows": [{
        "issue_date": "2026年6月18日",
        "issuing_authority": "重庆监管局",
        "change_type": "变更股权",
        "institution_name": "重庆小米消费金融有限公司",
        "shareholder_name": "小米通讯技术有限公司",
        "shareholding_before": "",
        "change_method": "转入",
        "transferred_shares": "15000股",
        "transferred_ratio": "",
        "shares_after": "90000股",
        "shareholding_after": "0.6",
        "contribution_amount": "",
    }]})
    fake_choice = MagicMock()
    fake_choice.message.content = llm_content
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    with patch("web_scraper_service.crawlers.nfra_equity_extractor.AsyncOpenAI") as mock_client:
        client_inst = MagicMock()
        client_inst.chat.completions.create = AsyncMock(return_value=fake_resp)
        mock_client.return_value = client_inst
        rows = await extract_rows_llm(1258291, EQUITY_HTML, "https://www.nfra.gov.cn/x")

    assert len(rows) == 1
    row = rows[0]
    assert row["doc_id"] == 1258291
    assert row["publish_date"] == date(2026, 6, 18)
    assert row["doc_number"] == "渝金管复〔2026〕58号"
    assert row["doc_title"].startswith("重庆金融监管局关于")
    assert row["institution_name"] == "重庆小米消费金融有限公司"
    assert row["shareholder_name"] == "小米通讯技术有限公司"
    assert row["change_method"] == "转入"
    assert row["shareholding_after"] == "0.6"
