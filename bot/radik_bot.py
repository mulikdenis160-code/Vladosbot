#!/usr/bin/env python3
"""RADIK AI BOT: Pocket Option WebSocket market scanner.

This is the service core extracted from the source repository. It deliberately
does not contain a web UI and never returns the SSID or Gemini key.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("radik-bot")

RAW_SSID = os.getenv("POCKET_SSID", "").strip()
DEMO = os.getenv("POCKET_DEMO", "true").lower() in {"1", "true", "yes"}
API_PORT = int(os.getenv("API_PORT", "8765"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "92"))
MAX_PRICE_AGE_SEC = int(os.getenv("MAX_PRICE_AGE_SEC", "20"))
CANDLE_TIMEOUT_SEC = int(os.getenv("CANDLE_FETCH_TIMEOUT_SEC", "8"))

OTC_PAIRS = [
    "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "USDCAD_otc",
    "USDCHF_otc", "NZDUSD_otc", "EURGBP_otc", "EURJPY_otc", "GBPJPY_otc",
    "AUDJPY_otc", "EURCHF_otc", "AUDCAD_otc", "AUDNZD_otc", "CADJPY_otc",
    "CHFJPY_otc", "EURAUD_otc", "EURCAD_otc", "GBPAUD_otc", "GBPCAD_otc",
]


def normalize_ssid(raw: str, is_demo: bool) -> tuple[str, dict[str, Any]]:
    if not raw:
        raise ValueError("POCKET_SSID пустой")
    match = re.search(r'42\s*\[\s*"auth"\s*,\s*(\{.*\})\s*\]', raw, re.DOTALL)
    payload = match.group(1) if match else raw.strip()
    if not payload.startswith("{"):
        raise ValueError("Не удалось разобрать SSID")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("SSID JSON имеет неверный формат") from exc
    session = data.get("session") or data.get("sessionToken") or data.get("token")
    if not session:
        raise ValueError("В SSID нет session или sessionToken")
    try:
        uid = int(data.get("uid") or data.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    normalized = {
        "session": str(session),
        "isDemo": int(bool(data.get("isDemo", 1 if is_demo else 0))),
        "uid": uid,
        "platform": int(data.get("platform") or 2),
        "isFastHistory": True,
        "isOptimized": True,
    }
    for key in ("lang", "currentUrl", "isChart"):
        if key in data:
            normalized[key] = data[key]
    return '42["auth",' + json.dumps(normalized, separators=(",", ":")) + "]", normalized


try:
    SSID, SSID_META = normalize_ssid(RAW_SSID, DEMO)
except ValueError as exc:
    log.error("%s", exc)
    sys.exit(1)

try:
    from pocketoptionapi import global_value
    from pocketoptionapi.stable_api import PocketOption
except ImportError:
    log.error("Установи PocketOptionAPI-v2 из requirements-bot.txt")
    sys.exit(1)

api: Optional[PocketOption] = None
connected = False
last_error: Optional[str] = None
last_balance: Optional[float] = None
payout_cache: dict[str, float] = {}
payout_cache_at = 0.0
price_cache: dict[str, float] = {}
price_times: dict[str, float] = {}
candle_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="candles")


def display_pair(active: str) -> str:
    clean = active.replace("_otc", "").upper()
    return f"{clean[:3]}/{clean[3:]}" if len(clean) == 6 else clean


def normalize_payout(value: Any) -> Optional[float]:
    try:
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", ".").strip()
        payout = float(value)
    except (TypeError, ValueError):
        return None
    if 0 < payout <= 1:
        payout *= 100
    return round(payout, 2) if 0 <= payout <= 100 else None


def refresh_payouts(force: bool = False) -> dict[str, float]:
    global payout_cache, payout_cache_at
    if not connected or api is None:
        return payout_cache
    if payout_cache and not force and time.time() - payout_cache_at < 30:
        return payout_cache
    try:
        raw = api.get_payout()
        if isinstance(raw, str):
            raw = json.loads(raw)
        fresh: dict[str, float] = {}
        for row in raw if isinstance(raw, list) else []:
            if isinstance(row, (list, tuple)) and len(row) >= 6:
                payout = normalize_payout(row[5])
                if payout is not None:
                    fresh[str(row[1])] = payout
            elif isinstance(row, dict):
                pair = row.get("pair") or row.get("active") or row.get("symbol")
                payout = normalize_payout(row.get("payout") or row.get("profit") or row.get("percent"))
                if pair and payout is not None:
                    fresh[str(pair)] = payout
        if fresh:
            payout_cache, payout_cache_at = fresh, time.time()
    except Exception as exc:
        log.warning("Не удалось обновить payout: %s", type(exc).__name__)
    return payout_cache


def market_snapshot(active: str) -> dict[str, Any]:
    if not connected or api is None:
        raise RuntimeError(last_error or "Не подключено")
    future = candle_pool.submit(api.get_candles, active, 60, count=60, count_request=1)
    try:
        if not future.result(timeout=CANDLE_TIMEOUT_SEC):
            raise RuntimeError("Свечи не получены")
    except FutureTimeoutError as exc:
        raise RuntimeError("Таймаут получения свечей") from exc
    market = global_value.pairs.get(active) or {}
    frame = market.get("dataframe")
    history = market.get("history") or []
    if frame is None or len(frame) < 15:
        raise RuntimeError("Недостаточно свечей")
    live = next((item for item in reversed(history) if isinstance(item, dict) and item.get("price") is not None), None)
    if not live:
        raise RuntimeError("Нет live-котировки")
    price = float(live["price"])
    server_now = float(api.get_server_timestamp() or time.time())
    tick_time = float(live.get("time") or 0)
    age = max(0.0, server_now - tick_time) if tick_time else MAX_PRICE_AGE_SEC + 1
    if age > MAX_PRICE_AGE_SEC:
        raise RuntimeError("Котировка устарела")
    closes = [float(value) for value in frame["close"].tail(30).tolist()]
    price_cache[active], price_times[active] = price, time.time()
    return {
        "active": active, "price": price, "price_age_sec": round(age, 2),
        "closes": closes, "trend": "up" if closes[-1] >= closes[-5] else "down",
        "volatility": statistics.pstdev(closes[-12:]),
    }


def gemini_analysis(snapshot: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY не настроен")
    prompt = (
        "Ты анализируешь краткосрочный рынок бинарных опционов. "
        "Это образовательный аналитический сигнал, не гарантия прибыли. "
        'Верни только JSON вида {"direction":"ВВЕРХ" или "ВНИЗ",'
        '"confidence":0-100,"reason":"кратко по-русски"}. '
        f"Пара {snapshot['active']}, цена {snapshot['price']}, тренд {snapshot['trend']}, "
        f"волатильность {snapshot['volatility']}, последние закрытия {snapshot['closes']}."
    )
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=25,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    result = json.loads(text)
    if result.get("direction") not in {"ВВЕРХ", "ВНИЗ"}:
        raise ValueError("ИИ вернул недопустимое направление")
    return {
        "direction": result["direction"],
        "confidence": max(0, min(100, int(result.get("confidence", 0)))),
        "reason": str(result.get("reason", "Анализ завершён")),
        "source": "gemini",
    }


def connect() -> bool:
    global api, connected, last_error, last_balance
    try:
        api = PocketOption(bool(SSID_META["isDemo"]), SSID)
        api.connect()
        time.sleep(3)
        for method_name in ("get_balance", "GetBalance", "getbalance"):
            method = getattr(api, method_name, None)
            if callable(method):
                try:
                    balance = method()
                    if balance is not None:
                        last_balance = float(balance)
                        connected, last_error = True, None
                        log.info("Pocket Option подключён (demo=%s)", bool(SSID_META["isDemo"]))
                        return True
                except Exception:
                    continue
        last_error = "Баланс не получен: проверь SSID"
    except Exception as exc:
        last_error = "Ошибка подключения Pocket Option: " + str(exc)
        log.exception("WebSocket connection failed")
    connected = False
    return False


app = Flask(__name__)
CORS(app)


@app.get("/health")
def health():
    latest = max(price_times.values(), default=0)
    return jsonify({
        "status": "ok" if connected else "error", "connected": connected,
        "demo": bool(SSID_META["isDemo"]), "pairs_count": len(OTC_PAIRS),
        "payouts_loaded": len(payout_cache), "min_payout": MIN_PAYOUT,
        "ai_available": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "latest_price_age_sec": round(time.time() - latest, 1) if latest else None,
        "error": last_error,
    })


@app.get("/pairs")
def pairs():
    payouts = refresh_payouts(force=True)
    return jsonify({
        "min_payout": MIN_PAYOUT,
        "pairs": [{"pair": display_pair(pair), "raw": pair, "payout": payouts.get(pair),
                   "eligible": payouts.get(pair, -1) >= MIN_PAYOUT} for pair in OTC_PAIRS],
    })


@app.get("/signal")
def signal():
    payouts = refresh_payouts(force=True)
    eligible = [pair for pair in OTC_PAIRS if payouts.get(pair, -1) >= MIN_PAYOUT]
    if not eligible:
        return jsonify({"error": f"Нет пар с подтверждённой выплатой ≥ {MIN_PAYOUT:g}%", "min_payout": MIN_PAYOUT}), 503
    best: Optional[dict[str, Any]] = None
    snapshots: list[tuple[str, dict[str, Any]]] = []
    for active in eligible:
        try:
            snapshots.append((active, market_snapshot(active)))
        except Exception as exc:
            log.warning("Пропущена live-котировка %s: %s", active, type(exc).__name__)
    errors = len(eligible) - len(snapshots)
    for active, snapshot in snapshots:
        try:
            analysis = gemini_analysis(snapshot)
            candidate = {**analysis, "pair": display_pair(active), "raw_pair": active,
                         "payout": payouts[active], "entry_price": snapshot["price"],
                         "price_age_sec": snapshot["price_age_sec"], "created_at": time.time()}
            if best is None or candidate["confidence"] > best["confidence"]:
                best = candidate
        except Exception as exc:
            errors += 1
            log.warning("Пропущен AI-анализ %s: %s", active, type(exc).__name__)
    if best is None:
        return jsonify({"error": "ИИ не смог проанализировать подходящие пары", "eligible_pairs": len(eligible),
                        "failed_pairs": errors}), 503
    return jsonify({**best, "status": "ready", "min_payout": MIN_PAYOUT,
                    "message": "Сигнал выбран только среди пар с подтверждённой выплатой."})


def main() -> None:
    log.info("RADIK AI BOT: пар ≥ %.2f%%, всего OTC-пар %s", MIN_PAYOUT, len(OTC_PAIRS))
    connect()
    app.run(host="0.0.0.0", port=API_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()