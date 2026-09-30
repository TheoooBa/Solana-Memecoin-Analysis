from smc_collector.parsing import index_included, parse_ohlcv_candles, parse_pool_entry, parse_pool_list

# Fixture calquée sur une vraie réponse de
# GET /networks/solana/trending_pools?include=base_token,quote_token,dex
# (structure vérifiée manuellement le 2026-09-21).
SAMPLE_TRENDING_RESPONSE = {
    "data": [
        {
            "id": "solana_Bd4wKg3xEBKJ4Xrw8skXMmJ4W65gk3x8yd7AovuBJisZ",
            "type": "pool",
            "attributes": {
                "base_token_price_usd": "0.000306939021169087785720566018542592143187696244549595984711524476",
                "address": "Bd4wKg3xEBKJ4Xrw8skXMmJ4W65gk3x8yd7AovuBJisZ",
                "name": "PEPENOM / SOL",
                "pool_created_at": "2026-09-11T00:55:05Z",
                "fdv_usd": "181595.34727526",
                "market_cap_usd": "181613.725519915",
                "transactions": {
                    "m5": {"buys": 2, "sells": 0, "buyers": 2, "sellers": 0},
                    "h1": {"buys": 166, "sells": 56, "buyers": 163, "sellers": 56},
                    "h24": {"buys": 12294, "sells": 4098, "buyers": 11810, "sellers": 3791},
                },
                "volume_usd": {"m5": "8.6068706098", "h1": "4968.238490348", "h24": "295484.617348529"},
                "reserve_in_usd": "111940.6788",
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump", "type": "token"}},
                "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112", "type": "token"}},
                "dex": {"data": {"id": "pumpswap", "type": "dex"}},
            },
        }
    ],
    "included": [
        {
            "id": "solana_EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump",
            "type": "token",
            "attributes": {"address": "EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump", "name": "PEPE BROCK", "symbol": "PEPENOM"},
        },
        {
            "id": "solana_So11111111111111111111111111111111111111112",
            "type": "token",
            "attributes": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
        },
        {"id": "pumpswap", "type": "dex", "attributes": {"name": "PumpSwap"}},
    ],
}


def test_parse_pool_list_extracts_core_fields():
    pools = parse_pool_list(SAMPLE_TRENDING_RESPONSE)
    assert len(pools) == 1
    pool = pools[0]
    assert pool.pool_address == "Bd4wKg3xEBKJ4Xrw8skXMmJ4W65gk3x8yd7AovuBJisZ"
    assert pool.dex == "PumpSwap"
    assert pool.base_token_symbol == "PEPENOM"
    assert pool.quote_token_symbol == "SOL"
    assert pool.pool_created_at == "2026-09-11T00:55:05Z"
    assert pool.reserve_usd == 111940.6788
    assert pool.volume_usd_h24 == 295484.617348529
    assert pool.buys_h24 == 12294
    assert pool.sellers_m5 == 0


def test_parse_pool_list_fields_absent_from_trending_are_none():
    """trending_pools/new_pools n'exposent ni locked_liquidity_percentage, ni
    pool_fee_percentage, ni quote_token_price_usd, ni price_change_percentage,
    ni les périodes m15/m30/h6 : ces champs doivent être None, pas planter."""
    pool = parse_pool_list(SAMPLE_TRENDING_RESPONSE)[0]
    assert pool.locked_liquidity_pct is None
    assert pool.pool_fee_pct is None
    assert pool.quote_token_price_usd is None
    assert pool.price_change_pct_m5 is None
    assert pool.price_change_pct_h24 is None
    assert pool.volume_usd_m15 is None
    assert pool.volume_usd_m30 is None
    assert pool.volume_usd_h6 is None
    assert pool.buys_m15 is None
    assert pool.sells_m30 is None
    assert pool.buyers_h6 is None
    assert pool.sellers_h6 is None


# Fixture calquée sur une vraie réponse de
# GET /networks/solana/pools/multi/{addresses} (structure vérifiée le
# 2026-09-21) : seul cet endpoint expose locked_liquidity_percentage et
# pool_fee_percentage, absents de trending_pools/new_pools ci-dessus.
SAMPLE_MULTI_POOLS_RESPONSE = {
    "data": [
        {
            "id": "solana_Bd4wKg3xEBKJ4Xrw8skXMmJ4W65gk3x8yd7AovuBJisZ",
            "type": "pool",
            "attributes": {
                "base_token_price_usd": "0.0003023082822739739",
                "quote_token_price_usd": "186.42",
                "address": "Bd4wKg3xEBKJ4Xrw8skXMmJ4W65gk3x8yd7AovuBJisZ",
                "name": "PEPENOM / SOL",
                "pool_created_at": "2026-09-11T00:55:05Z",
                "fdv_usd": "181306.054267829",
                "market_cap_usd": "181324.403234765",
                "reserve_in_usd": "112004.5836",
                "locked_liquidity_percentage": 100.0,
                "pool_fee_percentage": 0.25,
                "price_change_percentage": {
                    "m5": "-0.119", "m15": "0.15", "m30": "0.964",
                    "h1": "1.464", "h6": "3.482", "h24": "34.845",
                },
                "transactions": {
                    "m5": {"buys": 4, "sells": 3, "buyers": 4, "sellers": 3},
                    "m15": {"buys": 20, "sells": 4, "buyers": 20, "sellers": 4},
                    "m30": {"buys": 114, "sells": 37, "buyers": 114, "sellers": 37},
                    "h1": {"buys": 167, "sells": 57, "buyers": 165, "sellers": 56},
                    "h6": {"buys": 721, "sells": 266, "buyers": 692, "sellers": 247},
                    "h24": {"buys": 12303, "sells": 4100, "buyers": 11819, "sellers": 3792},
                },
                "volume_usd": {
                    "m5": "109.37", "m15": "259.65", "m30": "3621.21",
                    "h1": "4914.09", "h6": "15732.48", "h24": "295643.49",
                },
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_EpEfnZxQyiBXppSKi8sncc8w4corn1UJbF9G91fQpump", "type": "token"}},
                "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112", "type": "token"}},
                "dex": {"data": {"id": "pumpswap", "type": "dex"}},
            },
        }
    ],
    "included": [],
}


def test_parse_pool_list_multi_pools_exposes_liquidity_and_fee():
    pool = parse_pool_list(SAMPLE_MULTI_POOLS_RESPONSE)[0]
    assert pool.locked_liquidity_pct == 100.0
    assert pool.pool_fee_pct == 0.25
    assert pool.quote_token_price_usd == 186.42


def test_parse_pool_list_multi_pools_all_periods_present():
    pool = parse_pool_list(SAMPLE_MULTI_POOLS_RESPONSE)[0]
    assert pool.volume_usd_m15 == 259.65
    assert pool.volume_usd_m30 == 3621.21
    assert pool.volume_usd_h6 == 15732.48
    assert pool.buys_m15 == 20
    assert pool.sells_m30 == 37
    assert pool.buyers_h6 == 692
    assert pool.sellers_h6 == 247
    assert pool.price_change_pct_m5 == -0.119
    assert pool.price_change_pct_h24 == 34.845


def test_parse_pool_list_missing_included_is_defensive():
    minimal = {"data": SAMPLE_TRENDING_RESPONSE["data"]}  # pas de bloc "included"
    pools = parse_pool_list(minimal)
    assert len(pools) == 1
    pool = pools[0]
    assert pool.base_token_symbol is None
    assert pool.dex == "pumpswap"  # repli sur l'id brut de la relation


def test_parse_pool_list_empty_data():
    assert parse_pool_list({"data": []}) == []


def test_parse_pool_list_handles_missing_data_key():
    assert parse_pool_list({}) == []


def test_index_included_keys_by_type_and_id():
    idx = index_included(SAMPLE_TRENDING_RESPONSE)
    assert ("dex", "pumpswap") in idx
    assert idx[("dex", "pumpswap")]["attributes"]["name"] == "PumpSwap"


SAMPLE_OHLCV_RESPONSE = {
    "data": {
        "id": "31e44f21-0fc2-4493-b027-7e159ed2daa3",
        "type": "ohlcv_request_response",
        "attributes": {
            "ohlcv_list": [
                [1789942620, 4.96e-11, 4.97e-11, 4.95e-11, 4.96e-11, 0.0099],
                [1789942680, 4.96e-11, 4.98e-11, 4.94e-11, 4.97e-11, 1.2],
            ]
        },
    }
}


def test_parse_ohlcv_candles():
    candles = parse_ohlcv_candles(SAMPLE_OHLCV_RESPONSE)
    assert len(candles) == 2
    ts, o, h, l, c, v = candles[0]
    assert ts == 1789942620
    assert v == 0.0099


def test_parse_ohlcv_candles_defensive_on_bad_shape():
    assert parse_ohlcv_candles({"data": {}}) == []
    assert parse_ohlcv_candles({}) == []
