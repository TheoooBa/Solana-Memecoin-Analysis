from smc_collector.collect import _snapshot_from_parsed
from smc_collector.models import SCHEMA_VERSION
from smc_collector.parsing import parse_pool_list

from test_parsing import SAMPLE_MULTI_POOLS_RESPONSE, SAMPLE_TRENDING_RESPONSE


class TestSnapshotFromParsed:
    def test_maps_multi_pools_fields_including_new_ones(self):
        pool = parse_pool_list(SAMPLE_MULTI_POOLS_RESPONSE)[0]
        snapshot = _snapshot_from_parsed(pool, "solana", "2026-09-21T00:00:00+00:00")
        row = snapshot.as_row()

        assert row["locked_liquidity_pct"] == 100.0
        assert row["pool_fee_pct"] == 0.25
        assert row["quote_token_price_usd"] == 186.42
        assert row["volume_usd_m15"] == 259.65
        assert row["buys_m30"] == 114
        assert row["price_change_pct_h24"] == 34.845
        assert row["schema_version"] == SCHEMA_VERSION
        assert row["source"] == "snapshot"

    def test_fields_absent_from_trending_response_are_none_not_missing(self):
        """Un champ absent de la réponse source (trending_pools n'a pas
        locked_liquidity_percentage) doit devenir None dans la ligne, jamais
        faire planter la construction du snapshot de base."""
        pool = parse_pool_list(SAMPLE_TRENDING_RESPONSE)[0]
        snapshot = _snapshot_from_parsed(pool, "solana", "2026-09-21T00:00:00+00:00")
        row = snapshot.as_row()

        assert row["locked_liquidity_pct"] is None
        assert row["pool_fee_pct"] is None
        assert row["quote_token_price_usd"] is None
        assert row["price_change_pct_m5"] is None
        assert row["volume_usd_m15"] is None
        # Le socle de base (prix, liquidité, volume 24h) reste bien renseigné.
        assert row["price_usd"] is not None
        assert row["reserve_usd"] == 111940.6788
        assert row["volume_usd_h24"] == 295484.617348529
        assert row["schema_version"] == SCHEMA_VERSION
