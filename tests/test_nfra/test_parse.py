"""nfra 采集纯逻辑单测（不打网络）。"""

from __future__ import annotations

from web_scraper_service.crawlers.nfra import (
    _response_has_rows,
    build_detail_html_url,
    build_list_html_url,
    build_list_url,
    parse_doc_rows,
)


def test_build_list_url() -> None:
    assert build_list_url(4110, 1) == (
        "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild"
        "?itemId=4110&pageSize=18&pageIndex=1"
    )
    assert build_list_url(4110, 3, page_size=50) == (
        "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild"
        "?itemId=4110&pageSize=50&pageIndex=3"
    )


def test_build_detail_html_url() -> None:
    assert build_detail_html_url(1258731) == (
        "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html"
        "?docId=1258731&itemId=4111&generaltype=0"
    )


def test_build_list_html_url() -> None:
    url = build_list_html_url(4110)
    assert url.startswith("https://www.nfra.gov.cn/cn/view/pages/ItemList.html")
    assert "itemId=4110" in url


def test_parse_doc_rows_extracts() -> None:
    body = (
        '{"rptCode":200,"msg":"成功","data":{"total":2,"rows":['
        '{"docId":1258731,"docTitle":"a"},{"docId":1259537,"docTitle":"b"}]}}'
    )
    assert parse_doc_rows(body) == [
        {"docId": 1258731, "docTitle": "a"},
        {"docId": 1259537, "docTitle": "b"},
    ]


def test_parse_doc_rows_accepts_bytes() -> None:
    assert parse_doc_rows(b'{"rptCode":200,"data":{"rows":[{"docId":7,"docTitle":"x"}]}}') == [
        {"docId": 7, "docTitle": "x"}
    ]


def test_parse_doc_rows_empty_rows() -> None:
    assert parse_doc_rows('{"rptCode":200,"data":{"total":0,"rows":[]}}') == []


def test_parse_doc_rows_missing_rows() -> None:
    assert parse_doc_rows('{"rptCode":200,"data":{}}') == []


def test_parse_doc_rows_bad_code() -> None:
    assert parse_doc_rows('{"rptCode":404,"msg":"失败","data":{"rows":[{"docId":1}]}}') == []


def test_parse_doc_rows_invalid_json() -> None:
    assert parse_doc_rows("<html>404</html>") == []


def test_parse_doc_rows_docid_as_string() -> None:
    """docId 为数字字符串时也能正确解析为 int。"""
    body = (
        '{"rptCode":200,"data":{"total":2,"rows":['
        '{"docId":"1258731","docTitle":"a"},{"docId":"1259537","docTitle":"b"}]}}'
    )
    assert parse_doc_rows(body) == [
        {"docId": 1258731, "docTitle": "a"},
        {"docId": 1259537, "docTitle": "b"},
    ]


def test_parse_doc_rows_docid_non_digit_string_skipped() -> None:
    """非数字字符串 docId 被跳过。"""
    body = '{"rptCode":200,"data":{"rows":[{"docId":"abc","docTitle":"x"}]}}'
    assert parse_doc_rows(body) == []


# ── _response_has_rows ──────────────────────────────────────


def test_response_has_rows_true() -> None:
    assert _response_has_rows(
        '{"rptCode":200,"data":{"rows":[{"docId":1}]}}'
    ) is True


def test_response_has_rows_empty_rows() -> None:
    assert _response_has_rows(
        '{"rptCode":200,"data":{"rows":[]}}'
    ) is False


def test_response_has_rows_missing_rows() -> None:
    assert _response_has_rows('{"rptCode":200,"data":{}}') is False


def test_response_has_rows_bad_code() -> None:
    assert _response_has_rows('{"rptCode":404,"data":{"rows":[{"docId":1}]}}') is False


def test_response_has_rows_invalid_json() -> None:
    assert _response_has_rows("<html>404</html>") is False


def test_response_has_rows_accepts_bytes() -> None:
    assert _response_has_rows(b'{"rptCode":200,"data":{"rows":[{"docId":1}]}}') is True


def test_response_has_rows_docid_string() -> None:
    """docId 为字符串时 _response_has_rows 仍返回 True（不做类型过滤）。"""
    assert _response_has_rows(
        '{"rptCode":200,"data":{"rows":[{"docId":"1263990"}]}}'
    ) is True
