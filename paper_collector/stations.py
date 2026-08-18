from __future__ import annotations

"""Canonical station universe for live paper-data capture.

Priority is deliberately separated from collection breadth:
- Market L2 / public trades / AWC / Kalshi rules: collect every historically tested station.
- IEM/MADIS high-frequency polling: focus on stations with manual edge evidence,
  actual old stale-price observations, or unusually heavy reaction-lag tails.

These are RESEARCH priorities, not claims that a station has a proven tradable edge.
The latest historical run (latency-v0.1.0, through 2026-08-13) used minute-candle
quote proxies and an older settlement-compatibility model; live L2 + fail-closed
compatibility must validate every candidate before money.
"""

STATIONS: dict[str, dict[str, str]] = {
    "KNYC": {"city": "New York", "series": "KXHIGHNY", "network": "NY_ASOS", "tier": "manual_case", "region": "northeast"},
    "KPHL": {"city": "Philadelphia", "series": "KXHIGHPHIL", "network": "PA_ASOS", "tier": "manual_case", "region": "northeast"},
    "KLAX": {"city": "Los Angeles", "series": "KXHIGHLAX", "network": "CA_ASOS", "tier": "manual_case", "region": "west_coast"},
    "KPHX": {"city": "Phoenix", "series": "KXHIGHTPHX", "network": "AZ_ASOS", "tier": "heavy_tail", "region": "southwest"},
    "KMDW": {"city": "Chicago", "series": "KXHIGHCHI", "network": "IL_ASOS", "tier": "historical_stale_price", "region": "midwest"},
    "KMSP": {"city": "Minneapolis", "series": "KXHIGHTMIN", "network": "MN_ASOS", "tier": "historical_stale_price", "region": "midwest"},
    "KDCA": {"city": "Washington", "series": "KXHIGHTDC", "network": "VA_ASOS", "tier": "heavy_tail", "region": "mid_atlantic"},
    "KATL": {"city": "Atlanta", "series": "KXHIGHTATL", "network": "GA_ASOS", "tier": "heavy_tail", "region": "southeast"},
    "KMSY": {"city": "New Orleans", "series": "KXHIGHTNOLA", "network": "LA_ASOS", "tier": "heavy_tail", "region": "gulf"},
    "KBOS": {"city": "Boston", "series": "KXHIGHTBOS", "network": "MA_ASOS", "tier": "heavy_tail", "region": "northeast"},
    "KMIA": {"city": "Miami", "series": "KXHIGHMIA", "network": "FL_ASOS", "tier": "control_high_frequency", "region": "southeast"},
    "KOKC": {"city": "Oklahoma City", "series": "KXHIGHTOKC", "network": "OK_ASOS", "tier": "broad", "region": "plains"},
    "KDFW": {"city": "Dallas", "series": "KXHIGHTDAL", "network": "TX_ASOS", "tier": "broad", "region": "texas"},
    "KDEN": {"city": "Denver", "series": "KXHIGHDEN", "network": "CO_ASOS", "tier": "broad", "region": "mountain"},
    "KAUS": {"city": "Austin", "series": "KXHIGHAUS", "network": "TX_ASOS", "tier": "broad", "region": "texas"},
    "KHOU": {"city": "Houston", "series": "KXHIGHTHOU", "network": "TX_ASOS", "tier": "broad", "region": "texas"},
    "KLAS": {"city": "Las Vegas", "series": "KXHIGHTLV", "network": "NV_ASOS", "tier": "broad", "region": "southwest"},
    "KSAT": {"city": "San Antonio", "series": "KXHIGHTSATX", "network": "TX_ASOS", "tier": "broad", "region": "texas"},
}

# All 18 stations had historical market/weather coverage in the latest stored run.
WEATHER_STATIONS: tuple[str, ...] = tuple(STATIONS)
MARKET_SERIES: tuple[str, ...] = tuple(dict.fromkeys(v["series"] for v in STATIONS.values()))

# High-frequency HTTP polling is intentionally narrower to avoid hammering IEM.
# These ten stations combine manual cases, the only two historical <100c NO entry
# proxies, and the strongest heavy-tail reaction-lag candidates.
OMO_PRIORITY_STATIONS: tuple[str, ...] = (
    "KNYC", "KPHL", "KLAX",
    "KPHX", "KMDW", "KMSP",
    "KDCA", "KATL", "KMSY", "KBOS",
)
OMO_DEFAULT_NETWORKS: dict[str, str] = {
    station: STATIONS[station]["network"] for station in OMO_PRIORITY_STATIONS
}

# Kept as an explicit control: Miami had many historical executable proxies but
# no sub-$1 NO entry in the old minute-candle run. It can be enabled through
# OMO_STATION_NETWORKS without changing code when a control feed is desired.
OMO_CONTROL_STATION = "KMIA"
