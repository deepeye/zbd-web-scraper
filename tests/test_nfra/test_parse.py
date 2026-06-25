"""nfra 采集纯逻辑单测（不打网络）。"""

from __future__ import annotations

from web_scraper_service.crawlers.nfra import (
    build_detail_url,
    build_list_html_url,
    build_list_url,
    filter_pending,
    parse_doc_ids,
)


def test_build_list_url() -> None:
    assert build_list_url(4110, 1) == (
        "https://www.nfra.gov.cn/cn/static/data/DocInfo/"
        "SelectDocByItemIdAndChild/data_itemId=4110,pageIndex=1,pageSize=18.json"
    )
    assert build_list_url(4110, 3, page_size=50).endswith(
        "data_itemId=4110,pageIndex=3,pageSize=50.json"
    )


def test_build_detail_url() -> None:
    assert build_detail_url(1258731) == (
        "https://www.nfra.gov.cn/cn/static/data/DocInfo/"
        "SelectByDocId/data_docId=1258731.json"
    )


def test_build_list_html_url() -> None:
    url = build_list_html_url(4110)
    assert url.startswith("https://www.nfra.gov.cn/cn/view/pages/ItemList.html")
    assert "itemId=4110" in url


def test_parse_doc_ids_extracts_ids() -> None:
    body = (
        '{"rptCode":200,"msg":"成功","data":{"total":2,"rows":['
        '{"docId":1258731,"docTitle":"a"},{"docId":1259537,"docTitle":"b"}]}}'
    )
    assert parse_doc_ids(body) == [1258731, 1259537]


def test_parse_doc_ids_accepts_bytes() -> None:
    assert parse_doc_ids(b'{"rptCode":200,"data":{"rows":[{"docId":7}]}}') == [7]


def test_parse_doc_ids_empty_rows() -> None:
    assert parse_doc_ids('{"rptCode":200,"data":{"total":0,"rows":[]}}') == []


def test_parse_doc_ids_missing_rows() -> None:
    assert parse_doc_ids('{"rptCode":200,"data":{}}') == []


def test_parse_doc_ids_bad_code() -> None:
    assert parse_doc_ids('{"rptCode":404,"msg":"失败","data":{"rows":[{"docId":1}]}}') == []


def test_parse_doc_ids_invalid_json() -> None:
    assert parse_doc_ids("<html>404</html>") == []


def test_filter_pending_dedup_and_skip() -> None:
    doc_ids = [1, 2, 2, 3, 4]
    existing = {2, 4}
    assert filter_pending(doc_ids, existing) == [1, 3]


def test_filter_pending_empty() -> None:
    assert filter_pending([], {1, 2}) == []
