"""
単体テスト：スキーマ（共通）

設計書：設計書/スキーマ（schemas）/共通
"""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import JstDatetime, PageQuery


def test_page_query_defaults():
    query = PageQuery()
    assert (query.page, query.page_size) == (1, 20)


@pytest.mark.parametrize("page,page_size", [(0, 20), (1, 0), (1, 101)])
def test_page_query_out_of_range(page, page_size):
    with pytest.raises(ValidationError):
        PageQuery(page=page, page_size=page_size)


def test_jst_datetime_serialization():
    class Sample(BaseModel):
        at: JstDatetime

    sample = Sample(at=datetime(2026, 9, 25, 15, 0, tzinfo=UTC))
    assert sample.model_dump(mode="json")["at"] == "2026-09-26T00:00:00+09:00"
