"""Download the paired MANH (NYS Mesonet) and KNYC (Central Park ASOS) history.

Sources (all public, no key):
  * NYS Mesonet timeseries2 API  -> MANH 5-minute observations (60-day windows)
  * IEM asos.py                  -> KNYC routine METAR + SPECI with T-group
  * IEM asos1min.py              -> KNYC one-minute ASOS temperatures (NCEI 6406)
  * IEM json/cli.py              -> KNYC Daily Climate Report (CLI) highs
  * IEM mos.py (NBS)             -> National Blend of Models text guidance for KNYC

Usage:
  python3 research/manh_knyc/fetch_data.py --out DATA_DIR --start 2021-01-01 --end 2026-10-10
Files are cached per window, so the script is safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

UA = "MercuryEdge-research/0.1 (MANH-KNYC calibration)"
NYSM_URL = "https://api.nysmesonet.org/data/dynserv/timeseries2"
IEM = "https://mesonet.agron.iastate.edu"
NYSM_VARS = ["tair", "tslo", "relh", "srad", "pres", "wspd_sonic", "wdir_sonic", "precip"]


def http(url: str, body: bytes | None = None, retries: int = 5) -> bytes:
    headers = {"User-Agent": UA}
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - network retry loop
            if attempt == retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"  retry {attempt + 1} after {exc!r}; sleeping {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def windows(start: date, end: date, days: int):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def fetch_manh(out: Path, start: date, end: date, station: str = "MANH", dataset: str = "nysm") -> None:
    target = out / dataset / station
    target.mkdir(parents=True, exist_ok=True)
    for a, b in windows(start, end, 59):
        path = target / f"{a.isoformat()}_{b.isoformat()}.json"
        if path.exists() and path.stat().st_size > 200:
            continue
        body = json.dumps({
            "dataset": dataset,
            "variables": NYSM_VARS,
            "start": f"{a.isoformat()}T00:00:00.000",
            # API end is inclusive; stop one 5-minute step before the next window.
            "end": f"{(b - timedelta(days=1)).isoformat()}T23:55:00.000",
            "stations": [station],
        }).encode()
        raw = http(NYSM_URL, body)
        payload = json.loads(raw)
        if not payload.get("success"):
            raise RuntimeError(f"NYSM error for {a}: {payload.get('response')}")
        path.write_bytes(raw)
        print(f"{station} {a}..{b}: {len(payload['response']['coords']['time']['data'])} rows")
        time.sleep(0.5)


def fetch_metar(out: Path, start: date, end: date) -> None:
    target = out / "iem_metar"
    target.mkdir(parents=True, exist_ok=True)
    for a, b in windows(start, end, 366):
        path = target / f"KNYC_{a.isoformat()}_{b.isoformat()}.csv"
        if path.exists() and path.stat().st_size > 1000:
            continue
        url = (
            f"{IEM}/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=dwpf&data=sknt&data=drct"
            f"&data=skyc1&data=skyl1&data=skyc2&data=skyl2&data=skyc3&data=skyl3&data=wxcodes&data=metar"
            f"&year1={a.year}&month1={a.month}&day1={a.day}&year2={b.year}&month2={b.month}&day2={b.day}"
            "&tz=Etc/UTC&format=onlycomma&latlon=no&elev=no&missing=M&trace=T&direct=no"
            "&report_type=3&report_type=4"
        )
        path.write_bytes(http(url))
        print(f"METAR {a}..{b}")
        time.sleep(1)


def fetch_1min(out: Path, start: date, end: date) -> None:
    target = out / "iem_1min"
    target.mkdir(parents=True, exist_ok=True)
    cur = date(start.year, start.month, 1)
    while cur < end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        path = target / f"KNYC_{cur.strftime('%Y-%m')}.csv"
        if not (path.exists() and path.stat().st_size > 100):
            url = (
                f"{IEM}/cgi-bin/request/asos1min.py?station=NYC&vars=tmpf&vars=dwpf"
                f"&sts={cur.isoformat()}T00:00Z&ets={nxt.isoformat()}T00:00Z"
                "&sample=1min&what=download&tz=UTC"
            )
            path.write_bytes(http(url))
            print(f"1min {cur:%Y-%m}")
            time.sleep(1)
        cur = nxt


def fetch_1min_station(out: Path, start: date, end: date, station: str) -> None:
    """One-minute ASOS temperature and wind for an airport neighbour (e.g. LGA)."""
    target = out / f"iem_1min_{station}"
    target.mkdir(parents=True, exist_ok=True)
    cur = date(start.year, start.month, 1)
    while cur < end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        path = target / f"{station}_{cur.strftime('%Y-%m')}.csv"
        if not (path.exists() and path.stat().st_size > 100):
            url = (
                f"{IEM}/cgi-bin/request/asos1min.py?station={station}&vars=tmpf&vars=drct&vars=sknt"
                f"&sts={cur.isoformat()}T00:00Z&ets={nxt.isoformat()}T00:00Z"
                "&sample=1min&what=download&tz=UTC"
            )
            path.write_bytes(http(url))
            print(f"1min {station} {cur:%Y-%m}")
            time.sleep(1)
        cur = nxt


def fetch_airport_reports(out: Path, start: date, end: date, station: str) -> None:
    """Archived live feeds for an airport: MADIS 5-minute HF reports (whole degrees C, as
    published in real time) and routine/special METARs (T-group tenths)."""
    for kind, types in (("hf", "&report_type=1"), ("metar", "&report_type=3&report_type=4")):
        target = out / f"iem_{kind}_{station}"
        target.mkdir(parents=True, exist_ok=True)
        for a, b in windows(start, end, 183):
            path = target / f"{station}_{a.isoformat()}_{b.isoformat()}.csv"
            if path.exists() and path.stat().st_size > 1000:
                continue
            url = (
                f"{IEM}/cgi-bin/request/asos.py?station={station}&data=tmpf&data=drct&data=sknt&data=metar"
                f"&year1={a.year}&month1={a.month}&day1={a.day}&year2={b.year}&month2={b.month}&day2={b.day}"
                "&tz=Etc/UTC&format=onlycomma&latlon=no&elev=no&missing=M&trace=T&direct=no" + types
            )
            path.write_bytes(http(url))
            print(f"{kind} {station} {a}..{b}")
            time.sleep(1)


def fetch_cli(out: Path, start: date, end: date) -> None:
    target = out / "iem_cli"
    target.mkdir(parents=True, exist_ok=True)
    for year in range(start.year, end.year + 1):
        path = target / f"KNYC_{year}.json"
        if path.exists() and year < datetime.utcnow().year:
            continue
        path.write_bytes(http(f"{IEM}/json/cli.py?station=KNYC&year={year}"))
        print(f"CLI {year}")
        time.sleep(1)


def fetch_nbm(out: Path, start: date, end: date) -> None:
    """NBM short-range text guidance (NBS) for KNYC: hourly temps and TXN max/min + spread."""
    target = out / "iem_nbm"
    target.mkdir(parents=True, exist_ok=True)
    for a, b in windows(start, end, 92):
        path = target / f"KNYC_NBS_{a.isoformat()}_{b.isoformat()}.csv"
        if path.exists() and path.stat().st_size > 1000:
            continue
        url = (
            f"{IEM}/cgi-bin/request/mos.py?station=KNYC&model=NBS"
            f"&sts={a.isoformat()}T00:00Z&ets={b.isoformat()}T00:00Z&format=csv"
        )
        path.write_bytes(http(url))
        print(f"NBM {a}..{b}")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--only", choices=["manh", "metar", "1min", "cli", "nbm", "lga", "airports"], default=None)
    parser.add_argument("--nysm-stations", default="MANH")
    parser.add_argument("--nysm-dataset", default="nysm", help="nysm (standard network) or nyc (NYC Micronet)")
    parser.add_argument("--airports", default="EWR,CDW,JRB", help="IEM ids for --only airports (1-minute ASOS)")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.only in (None, "manh"):
        for stid in args.nysm_stations.split(","):
            fetch_manh(args.out, start, end, stid.strip().upper(), args.nysm_dataset)
    if args.only in (None, "metar"):
        fetch_metar(args.out, start, end)
    if args.only in (None, "cli"):
        fetch_cli(args.out, start, end)
    if args.only in (None, "nbm"):
        fetch_nbm(args.out, start, end)
    if args.only in (None, "1min"):
        fetch_1min(args.out, start, end)
    if args.only in (None, "lga"):
        fetch_1min_station(args.out, start, end, "LGA")
    if args.only == "airports":
        for station in args.airports.split(","):
            fetch_airport_reports(args.out, start, end, station.strip().upper())
            fetch_1min_station(args.out, start, end, station.strip().upper())


if __name__ == "__main__":
    main()
