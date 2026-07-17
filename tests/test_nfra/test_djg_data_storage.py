from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Date

from web_scraper_service.storage.djg_data import DjgData, init_djg_table


def test_djg_data_publish_date_column_is_date() -> None:
    column = DjgData.__table__.c.publish_date
    assert isinstance(column.type, Date)
    assert column.nullable is True


@pytest.mark.asyncio
async def test_init_djg_table_adds_publish_date_column() -> None:
    conn = MagicMock()
    conn.run_sync = AsyncMock()
    conn.execute = AsyncMock()
    begin_context = AsyncMock()
    begin_context.__aenter__.return_value = conn
    begin_context.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.begin.return_value = begin_context

    with patch("web_scraper_service.storage.djg_data.snapshot_engine", mock_engine):
        await init_djg_table()

    assert conn.execute.await_count == 2
    ddl_statements = [str(call.args[0]) for call in conn.execute.await_args_list]
    alter_ddl = next(s for s in ddl_statements if "ALTER TABLE djg_data" in s)
    assert "ADD COLUMN IF NOT EXISTS publish_date DATE" in alter_ddl
    index_ddl = next(s for s in ddl_statements if "CREATE INDEX" in s)
    assert "idx_djg_data_publish_date" in index_ddl
    assert "publish_date DESC NULLS LAST" in index_ddl
