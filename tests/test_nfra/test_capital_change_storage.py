from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Date

from web_scraper_service.storage.capital_change_data import (
    CapitalChangeData,
    init_capital_change_table,
)


def test_capital_change_table_columns_and_unique_constraint() -> None:
    table = CapitalChangeData.__table__
    assert table.name == "capital_change_data"
    assert isinstance(table.c.publish_date.type, Date)
    assert table.c.publish_date.nullable is True
    assert {"doc_id", "institution_name", "change_type"}.issubset(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints}
    assert "uq_capital_change_doc_institution_type" in constraints


@pytest.mark.asyncio
async def test_init_capital_change_table_creates_table() -> None:
    conn = MagicMock()
    conn.run_sync = AsyncMock()
    conn.execute = AsyncMock()
    begin_context = AsyncMock()
    begin_context.__aenter__.return_value = conn
    begin_context.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.begin.return_value = begin_context

    with patch("web_scraper_service.storage.capital_change_data.snapshot_engine", mock_engine):
        await init_capital_change_table()

    conn.run_sync.assert_awaited_once()
    assert conn.execute.await_count == 1
    ddl_statements = [str(call.args[0]) for call in conn.execute.await_args_list]
    index_ddl = next(s for s in ddl_statements if "CREATE INDEX" in s)
    assert "idx_capital_change_data_publish_date" in index_ddl
    assert "publish_date DESC NULLS LAST" in index_ddl
