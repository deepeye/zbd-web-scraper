"""nfra 采集纯逻辑单测（不打网络）。"""

from __future__ import annotations

import pytest

from web_scraper_service.crawlers.nfra import (
    _build_proxy_url,
    _check_response,
    _PageStatus,
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


# ── _check_response ────────────────────────────────────────


def test_check_response_has_data() -> None:
    assert _check_response(
        '{"rptCode":200,"data":{"rows":[{"docId":1}]}}'
    ) == _PageStatus.HAS_DATA


def test_check_response_empty_rows() -> None:
    assert _check_response(
        '{"rptCode":200,"data":{"rows":[]}}'
    ) == _PageStatus.EMPTY


def test_check_response_missing_rows() -> None:
    assert _check_response('{"rptCode":200,"data":{}}') == _PageStatus.EMPTY


def test_check_response_bad_code() -> None:
    assert _check_response('{"rptCode":404,"data":{"rows":[{"docId":1}]}}') == _PageStatus.ERROR


def test_check_response_invalid_json() -> None:
    assert _check_response("<html>403 Forbidden</html>") == _PageStatus.ERROR


def test_check_response_accepts_bytes() -> None:
    assert _check_response(b'{"rptCode":200,"data":{"rows":[{"docId":1}]}}') == _PageStatus.HAS_DATA


def test_check_response_docid_string() -> None:
    """docId 为字符串时 _check_response 仍返回 HAS_DATA（不做类型过滤）。"""
    assert _check_response(
        '{"rptCode":200,"data":{"rows":[{"docId":"1263990"}]}}'
    ) == _PageStatus.HAS_DATA


# ── _build_proxy_url ───────────────────────────────────────


def _patch_proxy_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    key: str = "5CDBEC47",
    pwd: str = "48BC8939D827",
    pool_url: str = "https://pool.example/get",
    proxy_list: str = "",
) -> None:
    from web_scraper_service.config import settings

    monkeypatch.setattr(settings, "proxy_enabled", enabled)
    monkeypatch.setattr(settings, "proxy_pool_auth_key", key)
    monkeypatch.setattr(settings, "proxy_pool_auth_pwd", pwd)
    monkeypatch.setattr(settings, "proxy_pool_url", pool_url)
    monkeypatch.setattr(settings, "proxy_list", proxy_list)


def test_build_proxy_url_basic_ip_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy_settings(monkeypatch)

    assert _build_proxy_url("115.226.144.16:15827") == (
        "http://5CDBEC47:48BC8939D827@115.226.144.16:15827"
    )


def test_build_proxy_url_with_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy_settings(monkeypatch)

    assert _build_proxy_url("http://115.226.144.16:15827") == (
        "http://5CDBEC47:48BC8939D827@115.226.144.16:15827"
    )


def test_build_proxy_url_settings_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy_settings(monkeypatch)

    assert _build_proxy_url("old_user:old_pass@115.226.144.16:15827") == (
        "http://5CDBEC47:48BC8939D827@115.226.144.16:15827"
    )


def test_build_proxy_url_encodes_special_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy_settings(monkeypatch, key="user@domain", pwd="p@ss:w#rd")

    assert _build_proxy_url("115.226.144.16:15827") == (
        "http://user%40domain:p%40ss%3Aw%23rd@115.226.144.16:15827"
    )


def test_build_proxy_url_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy_settings(monkeypatch, enabled=False)

    assert _build_proxy_url("115.226.144.16:15827") is None


def test_build_proxy_url_static_proxy_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy_settings(monkeypatch, pool_url="", proxy_list="http://static.proxy:8080")

    assert _build_proxy_url() == "http://static.proxy:8080"
