#!/usr/bin/env python3
"""Build a full A-share candidate feed from public market-data endpoints."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import random
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
EASTMONEY_APIS = (
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://7.push2.eastmoney.com/api/qt/clist/get",
)
TENCENT_KLINE_API = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_QUOTE_API = "https://qt.gtimg.cn/q="
SINA_QUOTE_API = "https://hq.sinajs.cn/list="
MARKET_SEGMENTS = {
    "Shanghai Main Board": "m:1+t:2",
    "STAR Market": "m:1+t:23",
    "Shenzhen Main Board": "m:0+t:6",
    "ChiNext": "m:0+t:80",
    "Beijing": "m:0+t:81+s:2048",
}
FIELDS = ",".join(
    (
        "f2", "f3", "f5", "f6", "f8", "f10", "f12", "f14", "f15", "f16",
        "f17", "f18", "f20", "f21", "f26", "f62", "f66", "f69", "f72",
        "f75", "f100", "f124", "f184",
    )
)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AStockAI-Market-Scanner/1.0"


def request_bytes(url: str, *, referer: str | None = None, attempts: int = 4, timeout: int = 18) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # network providers can fail transiently
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep((1.5 + random.random()) * (attempt + 1))
    raise RuntimeError(f"request failed after {attempts} attempts: {url}") from last_error


def request_json(url: str, *, attempts: int = 4, timeout: int = 18) -> dict[str, Any]:
    return json.loads(request_bytes(url, attempts=attempts, timeout=timeout).decode("utf-8"))


def number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def symbol(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    if code.startswith(("5", "6", "9")):
        return "sh" + code
    return "sz" + code


def market_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("diff") or []
    if isinstance(raw, dict):
        return [row for row in raw.values() if isinstance(row, dict)]
    return [row for row in raw if isinstance(row, dict)]


def universe_query(market_filter: str, page: int, page_size: int) -> str:
    return urllib.parse.urlencode(
        {
            "pn": page,
            "pz": page_size,
            "po": 1,
            "np": 2,
            "fltt": 2,
            "invt": 2,
            # Sort by immutable stock code so pagination cannot duplicate equal price-change rows.
            "fid": "f12",
            "fs": market_filter,
            "fields": FIELDS,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        }
    )


def fetch_segment(name: str, market_filter: str, page_size: int = 500) -> list[dict[str, Any]]:
    # Per-exchange bulk responses avoid the provider's deep-pagination limit.
    for endpoint in EASTMONEY_APIS:
        try:
            payload = request_json(
                f"{endpoint}?{universe_query(market_filter, 1, 50_000)}", attempts=2, timeout=20
            )
            data = payload.get("data") or {}
            total = int(data.get("total") or 0)
            page_rows = market_rows(data)
            unique = {str(row.get("f12", "")): row for row in page_rows if str(row.get("f12", "")).isdigit()}
            if total > 0 and len(unique) >= total:
                return list(unique.values())
        except Exception as exc:
            print(f"warning: {name} bulk request failed via {endpoint}: {exc}", flush=True)

    # Fallback: rotate provider hosts between smaller pages and slow down to avoid burst limiting.
    rows: list[dict[str, Any]] = []
    page = 1
    total = None
    while total is None or len(rows) < total:
        payload = None
        errors = []
        for offset in range(len(EASTMONEY_APIS)):
            endpoint = EASTMONEY_APIS[(page + offset) % len(EASTMONEY_APIS)]
            try:
                payload = request_json(
                    f"{endpoint}?{universe_query(market_filter, page, page_size)}", attempts=2, timeout=15
                )
                break
            except Exception as exc:
                errors.append(f"{endpoint}: {exc}")
        if payload is None:
            raise RuntimeError(f"all Eastmoney hosts failed for {name} page {page}: {' | '.join(errors)}")
        data = payload.get("data") or {}
        page_rows = market_rows(data)
        total = int(data.get("total") or 0)
        if not page_rows:
            break
        rows.extend(page_rows)
        page += 1
        time.sleep(0.35 + random.random() * 0.2)
    unique = {str(row.get("f12", "")): row for row in rows if str(row.get("f12", "")).isdigit()}
    if total is None or total <= 0 or len(unique) < total:
        raise RuntimeError(f"incomplete {name} universe: expected={total}, received={len(unique)}")
    return list(unique.values())


def fetch_universe() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, market_filter in MARKET_SEGMENTS.items():
        segment = fetch_segment(name, market_filter)
        print(f"fetched {name}: {len(segment)}", flush=True)
        rows.extend(segment)
    unique = {str(row.get("f12", "")): row for row in rows if str(row.get("f12", "")).isdigit()}
    if len(unique) < 4_000:
        raise RuntimeError(f"incomplete A-share universe after merge: received={len(unique)}")
    return list(unique.values())


def listed_for_days(value: Any, now: dt.datetime) -> int | None:
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return (now.date() - dt.datetime.strptime(text, "%Y%m%d").date()).days
    except ValueError:
        return None


def is_eligible(row: dict[str, Any], now: dt.datetime) -> bool:
    name = str(row.get("f14") or "").upper()
    price = number(row.get("f2"))
    change = number(row.get("f3"), -99)
    volume = number(row.get("f5"))
    amount = number(row.get("f6"))
    turnover = number(row.get("f8"))
    volume_ratio = number(row.get("f10"), 1.0)
    float_cap = number(row.get("f21"))
    listing_days = listed_for_days(row.get("f26"), now)
    return all(
        (
            name != "",
            "ST" not in name,
            "退" not in name,
            price > 0,
            volume > 0,
            amount >= 50_000_000,
            float_cap >= 1_000_000_000,
            -4.5 <= change <= 8.5,
            0.35 <= turnover <= 25,
            0.45 <= volume_ratio <= 6,
            listing_days is None or listing_days >= 60,
        )
    )


def snapshot_score(row: dict[str, Any]) -> float:
    main_net = number(row.get("f62"))
    main_ratio = number(row.get("f184"))
    super_ratio = number(row.get("f69"))
    large_ratio = number(row.get("f75"))
    amount = max(number(row.get("f6")), 1)
    turnover = number(row.get("f8"))
    volume_ratio = number(row.get("f10"), 1.0)
    change = number(row.get("f3"))

    flow_ratio_score = clamp((main_ratio + 2) / 14, 0, 1) * 26
    flow_size_score = clamp((math.log10(max(main_net, 1)) - 6) / 3, 0, 1) * 14
    large_order_score = clamp((super_ratio + large_ratio + 2) / 15, 0, 1) * 10
    liquidity_score = clamp((math.log10(amount) - 7.7) / 2.0, 0, 1) * 12
    turnover_score = clamp(1 - abs(turnover - 5.0) / 12.0, 0, 1) * 12
    volume_score = clamp(1 - abs(volume_ratio - 1.6) / 3.0, 0, 1) * 12
    change_score = clamp(1 - abs(change - 2.0) / 8.0, 0, 1) * 14
    if main_net <= 0:
        flow_size_score = 0
        flow_ratio_score *= 0.35
    return round(clamp(flow_ratio_score + flow_size_score + large_order_score + liquidity_score + turnover_score + volume_score + change_score, 0, 100), 3)


def fetch_kline(code: str, count: int = 70) -> list[dict[str, float | str]]:
    ticker = symbol(code)
    param = f"{ticker},day,,,{count},qfq"
    url = f"{TENCENT_KLINE_API}?{urllib.parse.urlencode({'param': param})}"
    stock = (request_json(url, attempts=1, timeout=6).get("data") or {}).get(ticker) or {}
    rows = stock.get("qfqday") or stock.get("day") or []
    result = []
    for row in rows:
        if len(row) < 6:
            continue
        result.append(
            {
                "date": row[0],
                "open": number(row[1]),
                "close": number(row[2]),
                "high": number(row[3]),
                "low": number(row[4]),
                "volume": number(row[5]),
            }
        )
    return result


def technical_metrics(rows: list[dict[str, float | str]]) -> dict[str, float | bool]:
    if len(rows) < 25:
        raise ValueError("insufficient daily bars")
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    ma5 = statistics.fmean(closes[-5:])
    ma10 = statistics.fmean(closes[-10:])
    ma20 = statistics.fmean(closes[-20:])
    price = closes[-1]
    prior_volume = statistics.fmean(volumes[-25:-5]) or 1.0
    volume_ratio_5d = statistics.fmean(volumes[-5:]) / prior_volume
    momentum_20d = (price / closes[-21] - 1) * 100
    distance_ma20 = (price / ma20 - 1) * 100

    trend_score = 0.0
    if ma5 > ma10 > ma20:
        trend_score += 35
    elif ma5 > ma10:
        trend_score += 25
    elif price > ma20:
        trend_score += 16
    if -2 <= distance_ma20 <= 8:
        trend_score += 25
    elif -5 <= distance_ma20 < -2 or 8 < distance_ma20 <= 12:
        trend_score += 12
    trend_score += clamp(1 - abs(momentum_20d - 6) / 22, 0, 1) * 25
    trend_score += clamp(1 - abs(volume_ratio_5d - 1.35) / 1.8, 0, 1) * 15
    return {
        "ma5": round(ma5, 3),
        "ma10": round(ma10, 3),
        "ma20": round(ma20, 3),
        "above_ma20": price >= ma20,
        "distance_ma20_pct": round(distance_ma20, 3),
        "momentum_20d_pct": round(momentum_20d, 3),
        "volume_ratio_5d": round(volume_ratio_5d, 3),
        "score": round(clamp(trend_score, 0, 100), 3),
        "data_quality": "DAILY_KLINE",
    }


def snapshot_technical_metrics(item: dict[str, Any]) -> dict[str, Any]:
    price = number(item.get("f2"))
    open_price = number(item.get("f17"), price)
    high = number(item.get("f15"), price)
    low = number(item.get("f16"), price)
    change = number(item.get("f3"))
    volume_ratio = number(item.get("f10"), 1.0)
    intraday_position = (price - low) / (high - low) if high > low else 0.5
    score = 42.0
    score += clamp((change + 2) / 8, 0, 1) * 20
    score += clamp(1 - abs(volume_ratio - 1.5) / 3, 0, 1) * 22
    score += clamp(intraday_position, 0, 1) * 10
    score += 6 if price >= open_price else 0
    return {
        "ma5": None,
        "ma10": None,
        "ma20": None,
        "above_ma20": None,
        "distance_ma20_pct": None,
        "momentum_20d_pct": None,
        "volume_ratio_5d": None,
        "score": round(clamp(score, 0, 100), 3),
        "data_quality": "INTRADAY_PROXY",
    }


def enrich_one(item: dict[str, Any]) -> dict[str, Any]:
    try:
        metrics = technical_metrics(fetch_kline(str(item["f12"])))
    except Exception:
        metrics = snapshot_technical_metrics(item)
    enriched = dict(item)
    enriched["technical"] = metrics
    base = snapshot_score(item)
    final = base * 0.64 + float(metrics["score"]) * 0.36
    if number(item.get("f3")) > 7.5:
        final -= 10
    enriched["score"] = round(clamp(final, 0, 100), 2)
    return enriched


def parse_tencent_quotes(text: str) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    for line in text.split(";"):
        if '="' not in line:
            continue
        key, payload = line.split('="', 1)
        values = payload.rstrip('"\r\n').split("~")
        if len(values) <= 32:
            continue
        code = str(values[2])
        price = number(values[3])
        if len(code) == 6 and price > 0:
            quotes[code] = {
                "name": values[1].strip(),
                "price": price,
                "change_pct": number(values[32]),
                "timestamp": values[30],
                "source": "TENCENT",
            }
    return quotes


def parse_sina_quotes(text: str) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    for line in text.split(";"):
        if '="' not in line:
            continue
        key, payload = line.split('="', 1)
        code = key.rsplit("_", 1)[-1][-6:]
        values = payload.rstrip('"\r\n').split(",")
        if len(values) <= 31:
            continue
        price = number(values[3])
        previous = number(values[2])
        if len(code) == 6 and price > 0:
            quotes[code] = {
                "name": values[0].strip(),
                "price": price,
                "change_pct": (price / previous - 1) * 100 if previous > 0 else 0,
                "timestamp": f"{values[30]} {values[31]}",
                "source": "SINA",
            }
    return quotes


def fetch_quote_checks(codes: Iterable[str]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    tickers = [symbol(code) for code in codes]
    tencent: dict[str, dict[str, Any]] = {}
    sina: dict[str, dict[str, Any]] = {}
    try:
        url = TENCENT_QUOTE_API + ",".join(tickers)
        tencent = parse_tencent_quotes(request_bytes(url).decode("gb18030", errors="replace"))
    except Exception as exc:
        print(f"warning: Tencent quote verification failed: {exc}")
    try:
        url = SINA_QUOTE_API + ",".join(tickers)
        sina = parse_sina_quotes(
            request_bytes(url, referer="https://finance.sina.com.cn/").decode("gb18030", errors="replace")
        )
    except Exception as exc:
        print(f"warning: Sina quote verification failed: {exc}")
    return tencent, sina


def money_text(value: float) -> str:
    absolute = abs(value)
    if absolute >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿元"
    return f"{value / 10_000:.0f}万元"


def candidate_record(item: dict[str, Any], rank: int, market_count: int, eligible_count: int, tencent: dict[str, Any] | None, sina: dict[str, Any] | None) -> dict[str, Any]:
    code = str(item["f12"])
    snapshot_price = number(item.get("f2"))
    primary = tencent or sina
    price = number(primary.get("price")) if primary else snapshot_price
    main_net = number(item.get("f62"))
    main_ratio = number(item.get("f184"))
    deviation = None
    if tencent and sina and number(tencent.get("price")) > 0 and number(sina.get("price")) > 0:
        deviation = abs(number(tencent["price"]) / number(sina["price"]) - 1) * 100
    reasons = [
        f"全A扫描：覆盖{market_count}只，{eligible_count}只通过基础过滤",
        f"主力净流入{money_text(main_net)}，净流入占比{main_ratio:.2f}%",
        f"换手率{number(item.get('f8')):.2f}%，量比{number(item.get('f10'), 1.0):.2f}",
    ]
    technical = item["technical"]
    if technical["data_quality"] == "INTRADAY_PROXY":
        reasons.append("腾讯日线暂不可用，采用涨跌幅/量比/日内位置代理评分")
    elif technical["above_ma20"]:
        reasons.append("现价位于20日均线上方")
    else:
        reasons.append("现价仍在20日均线下方，需等待确认")
    if tencent and sina and deviation is not None:
        reasons.append(f"腾讯/新浪双源复核，价差{deviation:.3f}%")
    elif primary:
        reasons.append(f"{primary['source']}单源复核；另一行情源暂不可用")
    else:
        reasons.append("实时复核失败，仅保留东财扫描快照")

    score = number(item.get("score"))
    if deviation is not None and deviation > 1.0:
        score = max(0, score - 10)
        reasons.append("双源价格偏差超过1%，评分已降级")
    buy_low = price * 0.975
    buy_high = price * 1.005
    return {
        "rank": rank,
        "code": code,
        "name": str((primary or {}).get("name") or item.get("f14") or code),
        "industry": str(item.get("f100") or "未知"),
        "score": round(score, 2),
        "snapshot_price": round(snapshot_price, 3),
        "change_pct": round(number((primary or {}).get("change_pct"), number(item.get("f3"))), 3),
        "amount": round(number(item.get("f6")), 2),
        "turnover_rate": round(number(item.get("f8")), 3),
        "volume_ratio": round(number(item.get("f10"), 1.0), 3),
        "main_net_inflow": round(main_net, 2),
        "main_net_ratio": round(main_ratio, 3),
        "technical": technical,
        "quote_check": {
            "price": round(price, 3),
            "timestamp": str((primary or {}).get("timestamp") or item.get("f124") or ""),
            "primary_source": str((primary or {}).get("source") or "EASTMONEY"),
            "secondary_source": "SINA" if tencent and sina else None,
            "price_deviation_pct": round(deviation, 4) if deviation is not None else None,
        },
        "reasons": reasons,
        "buy_zone": {"low": round(buy_low, 2), "high": round(buy_high, 2)},
        "confirmation_price": round(price * 1.025, 2),
        "risk_price": round(
            min(price * 0.94, number(technical["ma20"], price) * 0.97)
            if number(technical["ma20"], price) > 0 else price * 0.94,
            2,
        ),
    }


def validate_feed(feed: dict[str, Any]) -> None:
    coverage = feed.get("coverage") or {}
    candidates = feed.get("candidates") or []
    if feed.get("schema_version") != 1:
        raise ValueError("unsupported schema version")
    if int(coverage.get("market_count") or 0) < 4_000:
        raise ValueError("market coverage is incomplete")
    if not 10 <= len(candidates) <= 50:
        raise ValueError("candidate count is outside the safe range")
    codes = [item.get("code") for item in candidates]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate candidate codes")
    if any(not (isinstance(code, str) and len(code) == 6 and code.isdigit()) for code in codes):
        raise ValueError("invalid candidate code")
    if any(not 0 <= number(item.get("score"), -1) <= 100 for item in candidates):
        raise ValueError("invalid candidate score")


def build_feed(top: int) -> dict[str, Any]:
    now = dt.datetime.now(SHANGHAI)
    universe = fetch_universe()
    eligible = [row for row in universe if is_eligible(row, now)]
    if len(eligible) < 100:
        raise RuntimeError(f"too few eligible stocks: {len(eligible)}")
    preselected = sorted(eligible, key=snapshot_score, reverse=True)[:96]

    enriched: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(enrich_one, row) for row in preselected]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            if item:
                enriched.append(item)
    if len(enriched) < top:
        raise RuntimeError(f"technical source returned too few stocks: {len(enriched)}")
    shortlist = sorted(enriched, key=lambda row: number(row.get("score")), reverse=True)[: max(top + 15, 45)]
    tencent, sina = fetch_quote_checks(str(row["f12"]) for row in shortlist)
    records = [
        candidate_record(row, index + 1, len(universe), len(eligible), tencent.get(str(row["f12"])), sina.get(str(row["f12"])))
        for index, row in enumerate(shortlist)
    ]
    records = sorted(records, key=lambda row: number(row.get("score")), reverse=True)[:top]
    for index, row in enumerate(records, start=1):
        row["rank"] = index
    verified = sum(1 for row in records if row["quote_check"]["primary_source"] in {"TENCENT", "SINA"})
    feed = {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "trade_date": now.date().isoformat(),
        "market": "CN_A",
        "strategy": "capital_technical_v1",
        "coverage": {
            "markets": ["Shanghai", "Shenzhen", "Beijing"],
            "market_count": len(universe),
            "eligible_count": len(eligible),
            "technical_checked_count": len(enriched),
            "daily_kline_count": sum(
                1 for row in enriched if row["technical"]["data_quality"] == "DAILY_KLINE"
            ),
            "quote_verified_count": verified,
        },
        "sources": {
            "universe_and_capital_flow": "EASTMONEY_WEB",
            "technical_bars": "TENCENT_WEB",
            "primary_quote_check": "TENCENT_WEB",
            "secondary_quote_check": "SINA_WEB",
        },
        "filters": {
            "excluded": ["ST/*ST", "delisting", "suspended", "listed_under_60_days", "low_liquidity"],
            "minimum_daily_amount_cny": 50_000_000,
            "minimum_float_market_cap_cny": 1_000_000_000,
        },
        "limitations": [
            "Public web endpoints have no official availability SLA.",
            "A candidate is an observation shortlist, not an investment recommendation or order signal.",
            "A failed scan never overwrites the last known-good feed.",
        ],
        "candidates": records,
    }
    validate_feed(feed)
    return feed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/candidates.json")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()
    if not 10 <= args.top <= 50:
        raise SystemExit("--top must be between 10 and 50")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    feed = build_feed(args.top)
    temporary.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(
        f"published {len(feed['candidates'])} candidates from "
        f"{feed['coverage']['market_count']} A shares; "
        f"verified={feed['coverage']['quote_verified_count']}"
    )


if __name__ == "__main__":
    main()
