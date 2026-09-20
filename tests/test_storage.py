from datetime import datetime, timezone

from smc_collector.storage import AppendOnlyCsvWriter, day_path, read_all_rows


def test_append_rows_writes_header_once(tmp_path):
    writer = AppendOnlyCsvWriter(tmp_path, ["a", "b"])
    dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    writer.append_rows([{"a": "1", "b": "2"}], dt=dt)
    writer.append_rows([{"a": "3", "b": "4"}], dt=dt)

    path = day_path(tmp_path, dt)
    content = path.read_text().splitlines()
    assert content[0] == "a,b"
    assert content[1] == "1,2"
    assert content[2] == "3,4"
    assert len(content) == 3


def test_append_rows_empty_list_is_noop(tmp_path):
    writer = AppendOnlyCsvWriter(tmp_path, ["a"])
    n = writer.append_rows([], dt=datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert n == 0
    assert list(tmp_path.glob("*.csv")) == []


def test_read_all_rows_across_multiple_days(tmp_path):
    writer = AppendOnlyCsvWriter(tmp_path, ["pool_address"])
    day1 = datetime(2026, 9, 20, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 21, tzinfo=timezone.utc)
    writer.append_rows([{"pool_address": "p1"}], dt=day1)
    writer.append_rows([{"pool_address": "p2"}], dt=day2)

    rows = list(read_all_rows(tmp_path))
    assert [r["pool_address"] for r in rows] == ["p1", "p2"]


def test_read_all_rows_missing_directory_yields_nothing(tmp_path):
    assert list(read_all_rows(tmp_path / "does_not_exist")) == []


def test_never_overwrites_previous_rows(tmp_path):
    """Garantie centrale du projet : un pool mort ne disparaît jamais d'un fichier existant."""
    writer = AppendOnlyCsvWriter(tmp_path, ["pool_address", "status"])
    dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    writer.append_rows([{"pool_address": "p1", "status": "alive"}], dt=dt)
    writer.append_rows([{"pool_address": "p1", "status": "dead"}], dt=dt)

    rows = list(read_all_rows(tmp_path))
    assert len(rows) == 2
    assert rows[0]["status"] == "alive"
    assert rows[1]["status"] == "dead"
