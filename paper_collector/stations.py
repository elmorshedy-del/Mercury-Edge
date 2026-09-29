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
    "KNYC": {"city": "New York", "series": "KXHIGHNY", "network": "NY_ASOS", "tier": "manual_case", "region": "northeast", "timezone": "America/New_York"},
    "KPHL": {"city": "Philadelphia", "series": "KXHIGHPHIL", "network": "PA_ASOS", "tier": "manual_case", "region": "northeast", "timezone": "America/New_York"},
    "KLAX": {"city": "Los Angeles", "series": "KXHIGHLAX", "network": "CA_ASOS", "tier": "manual_case", "region": "west_coast", "timezone": "America/Los_Angeles"},
    "KPHX": {"city": "Phoenix", "series": "KXHIGHTPHX", "network": "AZ_ASOS", "tier": "heavy_tail", "region": "southwest", "timezone": "America/Phoenix"},
    "KMDW": {"city": "Chicago", "series": "KXHIGHCHI", "network": "IL_ASOS", "tier": "historical_stale_price", "region": "midwest", "timezone": "America/Chicago"},
    "KMSP": {"city": "Minneapolis", "series": "KXHIGHTMIN", "network": "MN_ASOS", "tier": "historical_stale_price", "region": "midwest", "timezone": "America/Chicago"},
    "KDCA": {"city": "Washington", "series": "KXHIGHTDC", "network": "VA_ASOS", "tier": "heavy_tail", "region": "mid_atlantic", "timezone": "America/New_York"},
    "KATL": {"city": "Atlanta", "series": "KXHIGHTATL", "network": "GA_ASOS", "tier": "heavy_tail", "region": "southeast", "timezone": "America/New_York"},
    "KMSY": {"city": "New Orleans", "series": "KXHIGHTNOLA", "network": "LA_ASOS", "tier": "heavy_tail", "region": "gulf", "timezone": "America/Chicago"},
    "KBOS": {"city": "Boston", "series": "KXHIGHTBOS", "network": "MA_ASOS", "tier": "heavy_tail", "region": "northeast", "timezone": "America/New_York"},
    "KMIA": {"city": "Miami", "series": "KXHIGHMIA", "network": "FL_ASOS", "tier": "control_high_frequency", "region": "southeast", "timezone": "America/New_York"},
    "KOKC": {"city": "Oklahoma City", "series": "KXHIGHTOKC", "network": "OK_ASOS", "tier": "broad", "region": "plains", "timezone": "America/Chicago"},
    "KDFW": {"city": "Dallas", "series": "KXHIGHTDAL", "network": "TX_ASOS", "tier": "broad", "region": "texas", "timezone": "America/Chicago"},
    "KDEN": {"city": "Denver", "series": "KXHIGHDEN", "network": "CO_ASOS", "tier": "broad", "region": "mountain", "timezone": "America/Denver"},
    "KAUS": {"city": "Austin", "series": "KXHIGHAUS", "network": "TX_ASOS", "tier": "broad", "region": "texas", "timezone": "America/Chicago"},
    "KHOU": {"city": "Houston", "series": "KXHIGHTHOU", "network": "TX_ASOS", "tier": "broad", "region": "texas", "timezone": "America/Chicago"},
    "KLAS": {"city": "Las Vegas", "series": "KXHIGHTLV", "network": "NV_ASOS", "tier": "broad", "region": "southwest", "timezone": "America/Los_Angeles"},
    "KSAT": {"city": "San Antonio", "series": "KXHIGHTSATX", "network": "TX_ASOS", "tier": "broad", "region": "texas", "timezone": "America/Chicago"},
}

# api.weather.gov climate-product location identifiers. Kept explicit rather
# than inferred from the ICAO station because the product API keys on NWS
# product locations, not on Mercury's own station naming convention.
NWS_VALIDATION_LOCATIONS: dict[str, str] = {
    "KNYC": "NYC",
    "KPHL": "PHL",
    "KLAX": "LAX",
    "KPHX": "PHX",
    "KMDW": "MDW",
    "KMSP": "MSP",
    "KDCA": "DCA",
    "KATL": "ATL",
    "KMSY": "MSY",
    "KBOS": "BOS",
    "KMIA": "MIA",
    "KOKC": "OKC",
    "KDFW": "DFW",
    "KDEN": "DEN",
    "KAUS": "AUS",
    "KHOU": "HOU",
    "KLAS": "LAS",
    "KSAT": "SAT",
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
