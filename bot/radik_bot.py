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
import base64
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

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
SIGNAL_DURATION_SEC = int(os.getenv("SIGNAL_DURATION_SEC", "120"))
SIGNAL_SEARCH_TIMEOUT_SEC = int(os.getenv("SIGNAL_SEARCH_TIMEOUT_SEC", "180"))
SIGNAL_SCAN_PAIRS = max(1, int(os.getenv("SIGNAL_SCAN_PAIRS", "1")))
GEMINI_MIN_REQUEST_INTERVAL_SEC = int(os.getenv("GEMINI_MIN_REQUEST_INTERVAL_SEC", "15"))
MAX_OVERLAYS = min(2, max(0, int(os.getenv("MAX_OVERLAYS", "2"))))
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BRIDGE_URL = os.getenv("TELEGRAM_BRIDGE_URL", "http://127.0.0.1:8010").rstrip("/")
SIGNAL_TIMEZONE = os.getenv("SIGNAL_TIMEZONE", "Europe/Kyiv")
SIGNAL_START = clock_time.fromisoformat(os.getenv("SIGNAL_START_HOUR", "09:00"))
SIGNAL_END = clock_time.fromisoformat(os.getenv("SIGNAL_END_HOUR", "23:00"))
ASSET_DIR = Path(__file__).resolve().parent / "assets"
TELEGRAM_IMAGES = {
    "signal": ASSET_DIR / "signal.jpg",
    "overlay": ASSET_DIR / "overlay.jpg",
    "summary": ASSET_DIR / "summary.jpg",
    "search": ASSET_DIR / "search.jpg",
}

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
analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
signal_stop = threading.Event()
signal_state_lock = threading.Lock()
active_signal: Optional[dict[str, Any]] = None
next_signal_at = 0.0
search_started_at = 0.0
scan_cursor = 0
gemini_next_request_at = 0.0
signal_thread: Optional[threading.Thread] = None


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
    global gemini_next_request_at
    now = time.monotonic()
    if now < gemini_next_request_at:
        raise RuntimeError("Gemini cooldown")
    gemini_next_request_at = now + GEMINI_MIN_REQUEST_INTERVAL_SEC
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY не настроен")
    prompt = (
        "Ти аналізуєш короткостроковий ринок бінарних опціонів. "
        "Це освітній аналітичний сигнал, а не гарантія прибутку. "
        'Поверни лише JSON виду {"direction":"ВГОРУ" або "ВНИЗ",'
        '"confidence":0-100,"reason":"коротко українською мовою"}. '
        f"Пара {snapshot['active']}, ціна {snapshot['price']}, тренд {snapshot['trend']}, "
        f"волатильність {snapshot['volatility']}, останні закриття {snapshot['closes']}."
    )
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=25,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:500].replace("\n", " ")
        retry_match = re.search(r"retry in ([\d.]+)s", detail, flags=re.IGNORECASE)
        if response.status_code == 429 and retry_match:
            gemini_next_request_at = max(
                gemini_next_request_at,
                time.monotonic() + float(retry_match.group(1)) + 1,
            )
        raise RuntimeError(f"Gemini HTTP {response.status_code}: {detail}") from exc
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    result = json.loads(text)
    direction = result.get("direction")
    if direction == "ВВЕРХ":
        direction = "ВГОРУ"
    if direction not in {"ВГОРУ", "ВНИЗ"}:
        raise ValueError("ИИ вернул недопустимое направление")
    return {
        "direction": direction,
        "confidence": max(0, min(100, int(result.get("confidence", 0)))),
        "reason": str(result.get("reason", "Аналіз завершено")),
        "source": "gemini",
    }


def in_signal_window() -> bool:
    now = datetime.now(ZoneInfo(SIGNAL_TIMEZONE)).time()
    return SIGNAL_START <= now < SIGNAL_END


def telegram_request(path: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    response = requests.post(f"{TELEGRAM_BRIDGE_URL}{path}", json=payload, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Telegram bridge error"))
    return result


def chart_png(snapshot: dict[str, Any], direction: str) -> str:
    try:
        from PIL import Image, ImageDraw, ImageFont
        closes = snapshot["closes"][-30:]
        image = Image.new("RGB", (1000, 520), "#101827")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        draw.text((36, 28), f"{display_pair(snapshot['active'])}  •  2 MIN", fill="#f5f7fb", font=font)
        draw.text((36, 54), f"{direction}  |  entry {snapshot['price']}", fill="#6ee7b7" if direction == "ВГОРУ" else "#fb7185", font=font)
        low, high = min(closes), max(closes)
        spread = max(high - low, 1e-9)
        points = []
        for index, value in enumerate(closes):
            x = 40 + index * 920 / max(1, len(closes) - 1)
            y = 450 - (value - low) / spread * 340
            points.append((x, y))
        draw.line(points, fill="#60a5fa", width=5)
        for x, y in points[-3:]:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#f8fafc")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:
        log.warning("Не удалось построить изображение сигнала: %s", type(exc).__name__)
        return ""


def asset_base64(kind: str) -> str:
    path = TELEGRAM_IMAGES.get(kind)
    if not path or not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("ascii")


def send_signal_to_telegram(
    signal: dict[str, Any], caption_prefix: str = "", image_kind: str = "signal"
) -> None:
    if not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID не настроен, сигнал останется только в API")
        return
    text = (
        f"{caption_prefix}📊 СИГНАЛ • {signal['pair']} OTC\n\n"
        f"📈 Напрямок: {signal['direction']}\n"
        f"💰 Ціна входу: {signal['entry_price']}\n"
        f"⏱ Експірація: {SIGNAL_DURATION_SEC // 60} хв\n"
        f"💎 Виплата: {signal['payout']:.0f}%\n"
        f"🎯 Впевненість: {signal['confidence']}%\n\n"
        f"📝 {signal['reason']}\n\n"
        "ℹ️ Це аналітичний сигнал. Угоди автоматично не відкриваються."
    )
    image = asset_base64(image_kind) or signal.get("image_base64", "")
    if image:
        telegram_request("/send-photo", {
            "image_base64": image,
            "caption": text,
            "filename": f"{image_kind}.jpg",
        })
    else:
        telegram_request("/send-message", {"text": text})


def send_search_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-photo", {
        "image_base64": asset_base64("search"),
        "caption": "🔎 ПОШУК СИГНАЛУ\n\nАналізую ринок до 3 хвилин та чекаю на якісний сетап.",
        "filename": "search.jpg",
    })


def send_result_to_telegram(signal: dict[str, Any]) -> None:
    label = (
        "✅ ВИГРАШ" if signal["result"] == "WIN"
        else "❌ ПРОГРАШ" if signal["result"] == "LOSS"
        else "⚠️ НЕ ВИЗНАЧЕНО"
    )
    text = (
        f"📋 ПІДСУМОК • {signal['pair']} OTC\n\n"
        f"{label}\n"
        f"Ціна входу: {signal['entry_price']}\n"
        f"Ціна закриття: {signal.get('close_price', 'немає даних')}\n"
        f"Спроба: {signal['attempt'] + 1} із {MAX_OVERLAYS + 1}\n\n"
        "Угоди автоматично не відкриваються."
    )
    telegram_request("/send-photo", {
        "image_base64": asset_base64("summary"),
        "caption": text,
        "filename": "summary.jpg",
    })


def generate_best_signal() -> Optional[dict[str, Any]]:
    global scan_cursor
    if time.monotonic() < gemini_next_request_at:
        return None
    payouts = refresh_payouts(force=True)
    eligible = sorted(
        (pair for pair in OTC_PAIRS if payouts.get(pair, -1) >= MIN_PAYOUT),
        key=lambda pair: payouts[pair],
        reverse=True,
    )
    if not eligible:
        return None
    batch_size = min(SIGNAL_SCAN_PAIRS, len(eligible))
    selected = [eligible[(scan_cursor + index) % len(eligible)] for index in range(batch_size)]
    scan_cursor = (scan_cursor + batch_size) % len(eligible)

    snapshots: list[tuple[str, dict[str, Any]]] = []
    for active in selected:
        try:
            snapshot = market_snapshot(active)
            snapshots.append((active, snapshot))
        except Exception as exc:
            log.warning("Пропущено отримання даних %s: %s", active, type(exc).__name__)

    def analyze_snapshot(item: tuple[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
        active, snapshot = item
        try:
            analysis = gemini_analysis(snapshot)
            return {
                **analysis, "pair": display_pair(active), "raw_pair": active,
                "payout": payouts[active], "entry_price": snapshot["price"],
                "price_age_sec": snapshot["price_age_sec"], "created_at": time.time(),
                "expires_at": time.time() + SIGNAL_DURATION_SEC, "attempt": 0,
                "snapshot": snapshot,
            }
        except Exception as exc:
            log.warning("Пропущено Gemini-аналіз %s: %s", active, str(exc)[:600])
            return None

    candidates = [result for result in analysis_pool.map(analyze_snapshot, snapshots) if result]
    best: Optional[dict[str, Any]] = max(candidates, key=lambda item: item["confidence"], default=None)
    if best:
        best["image_base64"] = chart_png(best["snapshot"], best["direction"])
        best.pop("snapshot", None)
    return best


def close_price(active: str) -> float:
    return float(market_snapshot(active)["price"])


def evaluate_signal(signal: dict[str, Any]) -> bool:
    try:
        price = close_price(signal["raw_pair"])
        signal["close_price"] = price
        signal["result"] = "WIN" if (
            price > signal["entry_price"] if signal["direction"] == "ВГОРУ"
            else price < signal["entry_price"]
        ) else "LOSS"
        return signal["result"] == "WIN"
    except Exception as exc:
        signal["result"] = "UNKNOWN"
        signal["result_error"] = type(exc).__name__
        log.warning("Не удалось проверить результат %s: %s", signal["pair"], type(exc).__name__)
        return False


def format_result(signal: dict[str, Any]) -> str:
    return (
        f"📋 ПІДСУМОК • {signal['pair']} OTC\n"
        f"{signal['result']} | вхід: {signal['entry_price']} | закриття: {signal.get('close_price', 'немає даних')}\n"
        f"Спроба: {signal['attempt'] + 1} із {MAX_OVERLAYS + 1}"
    )


def signal_loop() -> None:
    global active_signal, next_signal_at, search_started_at
    while not signal_stop.wait(1):
        if not connected:
            continue
        now = time.time()
        with signal_state_lock:
            current = active_signal
        if current and now >= current["expires_at"]:
            won = evaluate_signal(current)
            try:
                send_result_to_telegram(current)
            except Exception as exc:
                log.warning("Не вдалося надіслати підсумок у Telegram: %s", type(exc).__name__)
            if not won and current["attempt"] < MAX_OVERLAYS and in_signal_window():
                replacement = dict(current)
                replacement["attempt"] += 1
                replacement["entry_price"] = current.get("close_price", current["entry_price"])
                replacement["created_at"] = now
                replacement["expires_at"] = now + SIGNAL_DURATION_SEC
                replacement["result"] = None
                replacement["image_base64"] = ""
                with signal_state_lock:
                    active_signal = replacement
                try:
                    send_signal_to_telegram(
                        replacement,
                        f"🔁 ПЕРЕКРИТТЯ {replacement['attempt']}/{MAX_OVERLAYS}\n",
                        "overlay",
                    )
                except Exception as exc:
                    log.warning("Не вдалося надіслати перекриття у Telegram: %s", type(exc).__name__)
            else:
                with signal_state_lock:
                    active_signal = None
                next_signal_at = now + 2
            continue
        if not in_signal_window():
            continue
        if current or now < next_signal_at:
            continue
        if not search_started_at:
            search_started_at = now
            try:
                send_search_to_telegram()
            except Exception as exc:
                log.warning("Не вдалося надіслати повідомлення про пошук: %s", type(exc).__name__)
        candidate = generate_best_signal()
        if candidate is None:
            if now - search_started_at >= SIGNAL_SEARCH_TIMEOUT_SEC:
                search_started_at = 0.0
                next_signal_at = now + 2
            else:
                next_signal_at = max(now + 5, time.time() + (gemini_next_request_at - time.monotonic()))
            continue
        search_started_at = 0.0
        with signal_state_lock:
            active_signal = candidate
        try:
            send_signal_to_telegram(candidate)
        except Exception as exc:
            log.warning("Не удалось отправить сигнал в Telegram: %s", type(exc).__name__)
        next_signal_at = now + SIGNAL_DURATION_SEC


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
    best = generate_best_signal()
    if best is None:
        return jsonify({
            "error": f"Немає доступного сигналу з підтвердженою виплатою ≥ {MIN_PAYOUT:g}%",
            "min_payout": MIN_PAYOUT,
        }), 503
    response = {
        **best,
        "status": "ready",
        "min_payout": MIN_PAYOUT,
        "message": "Сигнал обрано лише серед пар із підтвердженою виплатою.",
    }
    response.pop("image_base64", None)
    return jsonify(response)


def main() -> None:
    log.info("RADIK AI BOT: пар ≥ %.2f%%, всего OTC-пар %s", MIN_PAYOUT, len(OTC_PAIRS))
    connect()
    global signal_thread
    signal_thread = threading.Thread(target=signal_loop, name="signal-scheduler", daemon=True)
    signal_thread.start()
    log.info(
        "Планировщик сигналов включён: %s–%s %s, экспирация %ss, перекрытия до %s",
        SIGNAL_START.isoformat(), SIGNAL_END.isoformat(), SIGNAL_TIMEZONE,
        SIGNAL_DURATION_SEC, MAX_OVERLAYS,
    )
    app.run(host="0.0.0.0", port=API_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()