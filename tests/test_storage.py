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


def test_schema_change_mid_day_does_not_corrupt_existing_file(tmp_path):
    """Reproduit le scénario réel d'un changement de schéma en cours de
    journée (le cas exact d'une migration v1->v2) : un fichier du jour existe
    déjà avec un en-tête plus court (colonnes manquantes). Ajouter des lignes
    au nouveau schéma ne doit JAMAIS être écrit dans ce fichier avec un
    en-tête qui ne correspond plus — ça désalignerait les colonnes en
    silence. Un nouveau fichier suffixé doit être créé à la place."""
    dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    day_file = day_path(tmp_path, dt)
    day_file.write_text("pool_address,price\naddr_v1,1.0\n", encoding="utf-8")

    writer_v2 = AppendOnlyCsvWriter(tmp_path, ["pool_address", "price", "new_column"])
    writer_v2.append_rows([{"pool_address": "addr_v2", "price": "2.0", "new_column": "x"}], dt=dt)

    # Le fichier v1 original n'a pas été touché.
    assert day_file.read_text() == "pool_address,price\naddr_v1,1.0\n"

    # Un nouveau fichier a été créé avec le bon en-tête complet.
    v2_file = tmp_path / f"{dt.strftime('%Y-%m-%d')}_v2.csv"
    assert v2_file.exists()
    assert v2_file.read_text().splitlines()[0] == "pool_address,price,new_column"

    # read_all_rows lit les deux fichiers, dans l'ordre chronologique
    # (le fichier de base d'abord grâce au tri lexicographique "." < "_").
    rows = list(read_all_rows(tmp_path))
    assert [r["pool_address"] for r in rows] == ["addr_v1", "addr_v2"]
    assert rows[0].get("new_column") is None  # colonne absente du fichier v1
    assert rows[1]["new_column"] == "x"


def test_same_schema_reuses_existing_file_across_multiple_writers(tmp_path):
    """Deux instances successives de AppendOnlyCsvWriter avec le MÊME schéma
    (cas normal : deux runs successifs le même jour) continuent d'écrire dans
    le même fichier, pas un nouveau à chaque fois."""
    dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    AppendOnlyCsvWriter(tmp_path, ["a", "b"]).append_rows([{"a": "1", "b": "2"}], dt=dt)
    AppendOnlyCsvWriter(tmp_path, ["a", "b"]).append_rows([{"a": "3", "b": "4"}], dt=dt)

    assert list(tmp_path.glob("*.csv")) == [day_path(tmp_path, dt)]
    assert len(list(read_all_rows(tmp_path))) == 2


def test_third_schema_variant_gets_next_suffix(tmp_path):
    dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    AppendOnlyCsvWriter(tmp_path, ["a"]).append_rows([{"a": "1"}], dt=dt)
    AppendOnlyCsvWriter(tmp_path, ["a", "b"]).append_rows([{"a": "2", "b": "x"}], dt=dt)
    AppendOnlyCsvWriter(tmp_path, ["a", "b", "c"]).append_rows([{"a": "3", "b": "y", "c": "z"}], dt=dt)

    names = sorted(p.name for p in tmp_path.glob("*.csv"))
    assert names == ["2026-09-21.csv", "2026-09-21_v2.csv", "2026-09-21_v3.csv"]
