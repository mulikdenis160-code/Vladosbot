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
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("radik-bot")

RAW_SSID = os.getenv("POCKET_SSID", "").strip()
DEMO = os.getenv("POCKET_DEMO", "true").lower() in {"1", "true", "yes"}
API_PORT = int(os.getenv("API_PORT", "8765"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
REPLIT_GEMINI_MODEL = os.getenv("GEMINI_REPLIT_MODEL", "gemini-2.5-flash")
MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "92"))
MAX_PRICE_AGE_SEC = int(os.getenv("MAX_PRICE_AGE_SEC", "20"))
CANDLE_TIMEOUT_SEC = int(os.getenv("CANDLE_FETCH_TIMEOUT_SEC", "12"))
SIGNAL_DURATION_SEC = int(os.getenv("SIGNAL_DURATION_SEC", "120"))
SIGNAL_SEARCH_TIMEOUT_SEC = int(os.getenv("SIGNAL_SEARCH_TIMEOUT_SEC", "180"))
SIGNAL_RETRY_DELAY_SEC = max(30, int(os.getenv("SIGNAL_RETRY_DELAY_SEC", "300")))
SIGNAL_SCAN_PAIRS = max(1, int(os.getenv("SIGNAL_SCAN_PAIRS", "1")))
GEMINI_MIN_REQUEST_INTERVAL_SEC = int(os.getenv("GEMINI_MIN_REQUEST_INTERVAL_SEC", "15"))
SIGNAL_ENTRY_NOTICE_SEC = max(5, int(os.getenv("SIGNAL_ENTRY_NOTICE_SEC", "15")))
PRICE_RETRY_TIMEOUT_SEC = max(1, int(os.getenv("PRICE_RETRY_TIMEOUT_SEC", "15")))
MAX_OVERLAYS = min(2, max(0, int(os.getenv("MAX_OVERLAYS", "2"))))
MARKET_PAUSE_SEC = max(20 * 60, int(os.getenv("MARKET_PAUSE_SEC", str(20 * 60))))
STARTUP_SAFETY_PAUSE_SEC = max(
    0, int(os.getenv("STARTUP_SAFETY_PAUSE_SEC", str(MARKET_PAUSE_SEC)))
)
MARKET_MONITOR_INTERVAL_SEC = max(
    30, int(os.getenv("MARKET_MONITOR_INTERVAL_SEC", "60"))
)
MARKET_MONITOR_PAIRS = max(1, int(os.getenv("MARKET_MONITOR_PAIRS", "3")))
MARKET_RECOVERY_REQUIRED_PAIRS = min(
    MARKET_MONITOR_PAIRS,
    max(1, int(os.getenv("MARKET_RECOVERY_REQUIRED_PAIRS", "2"))),
)
MARKET_RECOVERY_VOLATILITY_MULTIPLIER = float(
    os.getenv("MARKET_RECOVERY_VOLATILITY_MULTIPLIER", "1.25")
)
MARKET_RECOVERY_MIN_MOVE_PCT = float(
    os.getenv("MARKET_RECOVERY_MIN_MOVE_PCT", "0.015")
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BRIDGE_URL = os.getenv("TELEGRAM_BRIDGE_URL", "http://127.0.0.1:8010").rstrip("/")
TELEGRAM_PUBLIC_BASE_URL = os.getenv("TELEGRAM_PUBLIC_BASE_URL", "").rstrip("/")
SIGNAL_TIMEZONE = os.getenv("SIGNAL_TIMEZONE", "Europe/Kyiv")
SIGNAL_START = clock_time.fromisoformat(os.getenv("SIGNAL_START_HOUR", "09:00"))
SIGNAL_END = clock_time.fromisoformat(os.getenv("SIGNAL_END_HOUR", "23:00"))
ASSET_DIR = Path(__file__).resolve().parent / "assets"
TELEGRAM_IMAGES = {
    "signal": ASSET_DIR / "signal.jpg",
    "overlay": ASSET_DIR / "overlay.jpg",
    "summary": ASSET_DIR / "summary.jpg",
    "search": ASSET_DIR / "search.jpg",
    "win": ASSET_DIR / "win.jpg",
    "loss": ASSET_DIR / "loss.jpg",
}
FINAL_RESULT_DELIVERY_MODE = "text-only"
SIGNAL_STATE_FILE = Path(
    os.getenv("RADIK_SIGNAL_STATE_FILE", "/tmp/radik-signal-state.json")
)

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
market_request_lock = threading.Lock()
# All calls into the PocketOption client (connect/get_payout/ChangeSymbol/
# get_server_timestamp/balance) must be serialized behind this lock. The
# underlying websocket client is not thread-safe; concurrent access from the
# scheduler thread, Flask request threads, and the candle worker thread caused
# a native "malloc(): unsorted double linked list corrupted" crash that killed
# the whole process with no way to recover in Python (see market-pause-policy
# memory notes). Use RLock because connect() and market_snapshot() may nest.
api_lock = threading.RLock()
analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
signal_search_lock = threading.Lock()
signal_stop = threading.Event()
signal_state_lock = threading.Lock()
daily_stats_lock = threading.Lock()
active_signal: Optional[dict[str, Any]] = None
pending_signal: Optional[dict[str, Any]] = None
signal_searching = False
next_signal_at = 0.0
search_started_at = 0.0
scan_cursor = 0
gemini_next_request_at = 0.0
signal_thread: Optional[threading.Thread] = None
daily_stats_date = datetime.now(ZoneInfo(SIGNAL_TIMEZONE)).date().isoformat()
daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
daily_summary_sent_date: Optional[str] = None
pending_result_deliveries: list[dict[str, Any]] = []
result_delivery_next_retry_at = 0.0
result_delivery_last_error: Optional[str] = None
RESULT_DELIVERY_RETRY_SEC = 60


def persist_signal_state() -> None:
    with signal_state_lock:
        payload = {
            "active_signal": active_signal,
            "pending_signal": pending_signal,
            "pending_result_deliveries": pending_result_deliveries,
            "saved_at": time.time(),
        }
    try:
        if (
            not payload["active_signal"]
            and not payload["pending_signal"]
            and not payload["pending_result_deliveries"]
        ):
            SIGNAL_STATE_FILE.unlink(missing_ok=True)
            return
        SIGNAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SIGNAL_STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(SIGNAL_STATE_FILE)
    except Exception as exc:
        log.warning("Не вдалося зберегти стан активної серії: %s", type(exc).__name__)


def restore_signal_state() -> None:
    global active_signal, pending_signal, pending_result_deliveries
    try:
        payload = json.loads(SIGNAL_STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:
        log.warning("Не вдалося відновити стан активної серії: %s", type(exc).__name__)
        return
    active = payload.get("active_signal")
    pending = payload.get("pending_signal")
    if not isinstance(active, dict):
        active = None
    if not isinstance(pending, dict):
        pending = None
    queued = payload.get("pending_result_deliveries") or []
    if not isinstance(queued, list):
        queued = []
    queued = [item for item in queued if isinstance(item, dict)]
    if active and active.get("result_delivery_pending"):
        queued.insert(0, active)
        active = None
    with signal_state_lock:
        active_signal = active
        pending_signal = pending
        pending_result_deliveries = queued
    if active or pending:
        restored = active or pending
        log.warning(
            "Відновлено незавершену серію %s після перезапуску; новий пошук заблоковано",
            restored.get("pair", "невідома пара"),
        )
    if queued:
        log.warning(
            "Відновлено %s результатів, які очікують доставки в Telegram",
            len(queued),
        )
market_pause_until = 0.0
market_pause_started_at = 0.0
market_pause_last_check = 0.0
market_pause_pair_index = 0
market_pause_pairs: list[str] = []
market_pause_recovery_seen = False


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
        with api_lock:
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
    with api_lock:
        with market_request_lock:
            api.api.history_new = None
            future = candle_pool.submit(api.ChangeSymbol, active, 60)
            try:
                if not future.result(timeout=CANDLE_TIMEOUT_SEC):
                    raise RuntimeError("Свечі не отримані")
            except FutureTimeoutError as exc:
                raise RuntimeError("Таймаут підписки на ринкові дані") from exc
        market = global_value.pairs.get(active) or {}
        candles = market.get("history") or []
        ticks = market.get("ticks") or []
        if len(candles) < 15:
            raise RuntimeError("Недостаточно свечей")
        live = next(
            (item for item in reversed(ticks)
             if isinstance(item, dict) and item.get("price") is not None),
            None,
        )
        if not live:
            raise RuntimeError("Нет live-котировки")
        price = float(live["price"])
        server_now = float(api.get_server_timestamp() or time.time())
    tick_time = float(live.get("time") or 0)
    age = max(0.0, server_now - tick_time) if tick_time else MAX_PRICE_AGE_SEC + 1
    if age > MAX_PRICE_AGE_SEC:
        raise RuntimeError("Котировка устарела")
    closes = [
        float(candle["close"])
        for candle in sorted(candles, key=lambda candle: candle.get("time", 0))
        if candle.get("close") is not None
    ][-30:]
    if len(closes) < 15:
        raise RuntimeError("Недостаточно закрытий свечей")
    price_cache[active], price_times[active] = price, time.time()
    return {
        "active": active, "price": price, "tick_time": tick_time,
        "price_age_sec": round(age, 2),
        "closes": closes, "trend": "up" if closes[-1] >= closes[-5] else "down",
        "volatility": statistics.pstdev(closes[-12:]),
    }


def latest_market_price(active: str) -> float:
    """Refresh the pair so settlement never relies on a cached tick."""
    return float(market_snapshot(active)["price"])


def wait_for_market_price(active: str, not_before: float = 0.0) -> float:
    deadline = time.monotonic() + PRICE_RETRY_TIMEOUT_SEC
    last_error_message = "Нет live-котировки"
    while True:
        try:
            snapshot = market_snapshot(active)
            if not_before and snapshot["tick_time"] < not_before:
                raise RuntimeError("Котировка ще не оновилась для часу угоди")
            return float(snapshot["price"])
        except Exception as exc:
            last_error_message = str(exc) or type(exc).__name__
            if time.monotonic() >= deadline:
                raise RuntimeError(last_error_message) from exc
            time.sleep(0.5)


def gemini_analysis(snapshot: dict[str, Any]) -> dict[str, Any]:
    global gemini_next_request_at
    now = time.monotonic()
    if now < gemini_next_request_at:
        raise RuntimeError("Gemini cooldown")
    gemini_next_request_at = now + GEMINI_MIN_REQUEST_INTERVAL_SEC
    replit_base_url = os.getenv("AI_INTEGRATIONS_GEMINI_BASE_URL", "").strip().rstrip("/")
    replit_key = os.getenv("AI_INTEGRATIONS_GEMINI_API_KEY", "").strip()
    if replit_base_url and replit_key:
        model = REPLIT_GEMINI_MODEL
        api_url = f"{replit_base_url}/models/{model}:generateContent"
        headers = {"x-goog-api-key": replit_key}
        source = "replit-gemini"
    else:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Gemini API не настроен")
        model = GEMINI_MODEL
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {}
        source = "gemini"
    prompt = (
        "Ти аналізуєш короткостроковий ринок бінарних опціонів. "
        "Це освітній аналітичний сигнал, а не гарантія прибутку. "
        'Поверни лише JSON виду {"direction":"ВГОРУ" або "ВНИЗ",'
        '"confidence":0-100,"reason":"коротко українською мовою"}. '
        f"Пара {snapshot['active']}, ціна {snapshot['price']}, тренд {snapshot['trend']}, "
        f"волатильність {snapshot['volatility']}, останні закриття {snapshot['closes']}."
    )
    response = requests.post(
        api_url,
        params={} if source == "replit-gemini" else {"key": key},
        headers=headers,
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
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
        "source": source,
    }


def in_signal_window() -> bool:
    now = datetime.now(ZoneInfo(SIGNAL_TIMEZONE)).time()
    if SIGNAL_START <= SIGNAL_END:
        return SIGNAL_START <= now < SIGNAL_END
    return now >= SIGNAL_START or now < SIGNAL_END


def next_candle_start(timestamp: float) -> float:
    """Return a future one-minute boundary with enough notice to enter."""
    entry_at = (math.floor(timestamp / 60) + 1) * 60
    if entry_at - timestamp < SIGNAL_ENTRY_NOTICE_SEC:
        entry_at += 60
    return float(entry_at)


def schedule_signal_entry(signal: dict[str, Any]) -> dict[str, Any]:
    pending = dict(signal)
    pending["entry_at"] = next_candle_start(time.time())
    pending["entry_price_preview"] = pending.get("entry_price")
    pending["entry_price"] = None
    pending["created_at"] = None
    pending["expires_at"] = None
    return pending


def entry_time_label(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo(SIGNAL_TIMEZONE)).strftime("%H:%M")


def signal_is_in_progress() -> bool:
    with signal_state_lock:
        return (
            active_signal is not None
            or pending_signal is not None
            or signal_searching
        )


def result_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo(SIGNAL_TIMEZONE)).date().isoformat()


def record_signal_result(signal: dict[str, Any]) -> None:
    global daily_stats_date, daily_stats
    timestamp = float(signal.get("entry_at") or signal.get("created_at") or time.time())
    day = result_day(timestamp)
    outcome = signal.get("result", "UNKNOWN")
    if outcome not in {"WIN", "LOSS", "DRAW", "UNKNOWN"}:
        outcome = "UNKNOWN"
    with daily_stats_lock:
        if day != daily_stats_date:
            daily_stats_date = day
            daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
        daily_stats["total"] += 1
        daily_stats[outcome] += 1


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


def telegram_image_url(kind: str) -> str:
    if kind not in TELEGRAM_IMAGES or not TELEGRAM_PUBLIC_BASE_URL:
        return ""
    return f"{TELEGRAM_PUBLIC_BASE_URL}/api/telegram-image/{kind}"


def send_signal_to_telegram(
    signal: dict[str, Any], caption_prefix: str = "", image_kind: str = "signal"
) -> None:
    if not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID не настроен, сигнал останется только в API")
        return
    entry_at = signal.get("entry_at")
    if entry_at:
        entry_line = (
            f"🕐 Вхід: з нової 1-хв свічки о {entry_time_label(entry_at)}:00 "
            f"({SIGNAL_TIMEZONE})"
        )
        price_line = "💰 Ціна входу: фіксується на початку свічки"
    else:
        entry_line = ""
        price_line = f"💰 Ціна входу: {signal['entry_price']}"
    text = (
        f"{caption_prefix}📊 СИГНАЛ • {signal['pair']} OTC\n\n"
        f"📈 Напрямок: {signal['direction']}\n"
        f"{price_line}\n"
        f"{entry_line}\n"
        f"⏱ Експірація: {SIGNAL_DURATION_SEC // 60} хв\n"
        f"💎 Виплата: {signal['payout']:.0f}%\n"
        f"🎯 Впевненість: {signal['confidence']}%\n\n"
        f"📝 {signal['reason']}"
    )
    image_url = telegram_image_url(image_kind)
    if image_url:
        try:
            telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": f"{image_kind}.jpg",
            })
            return
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати зображення сигналу %s (%s), пробую текст",
                signal["pair"], type(exc).__name__,
            )
    telegram_request("/send-message", {"text": text})


def send_search_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    text = "🔎 ПОШУК СИГНАЛУ\n\nАналізую ринок до 3 хвилин та чекаю на якісний сетап."
    image_url = telegram_image_url("search")
    if image_url:
        try:
            telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": "search.jpg",
            })
            return
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати зображення пошуку (%s), пробую текст",
                type(exc).__name__,
            )
    telegram_request("/send-message", {"text": text})


def send_search_finished_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            "⏸ ПОШУК ЗАВЕРШЕНО\n\n"
            f"За {SIGNAL_SEARCH_TIMEOUT_SEC // 60} хв якісний сигнал не підтверджено. "
            f"Наступна перевірка через {SIGNAL_RETRY_DELAY_SEC // 60} хв."
        ),
    })


def send_result_to_telegram(signal: dict[str, Any]) -> None:
    label = (
        "✅ ВИГРАШ" if signal["result"] == "WIN"
        else "❌ ПРОГРАШ" if signal["result"] == "LOSS"
        else "➖ НІЧИЯ / ПОВЕРНЕННЯ" if signal["result"] == "DRAW"
        else "⚠️ НЕ ВИЗНАЧЕНО"
    )
    text = (
        f"📌 РЕЗУЛЬТАТ • {signal['pair']} OTC\n\n"
        f"{label}\n"
        f"Ціна входу: {signal.get('entry_price') or 'не зафіксовано'}\n"
        f"Ціна закриття: {signal.get('close_price', 'немає даних')}\n"
        f"Спроба: {signal['attempt'] + 1} із {MAX_OVERLAYS + 1}"
    )
    image_kind = {"WIN": "win", "LOSS": "loss"}.get(signal.get("result"))
    image_url = telegram_image_url(image_kind) if image_kind else ""
    if image_url:
        try:
            delivery = telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": f"{image_kind}.jpg",
            })
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати картинку результату %s (%s), пробую текст",
                signal["pair"], type(exc).__name__,
            )
            delivery = telegram_request("/send-message", {"text": text})
    else:
        delivery = telegram_request("/send-message", {"text": text})
    log.info(
        "Фінальний результат %s підтверджено Telegram message_id=%s",
        signal["pair"], delivery.get("message_id", "невідомо"),
    )


def send_daily_summary_to_telegram(summary_date: str, stats: dict[str, int]) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    net = stats["WIN"] - stats["LOSS"]
    net_label = f"+{net}" if net > 0 else str(net)
    text = (
        f"📊 ПІДСУМОК ДНЯ • {summary_date}\n\n"
        f"Усього спроб: {stats['total']}\n"
        f"✅ Виграшів: {stats['WIN']}\n"
        f"❌ Програшів: {stats['LOSS']}\n"
        f"➖ Нічиїх: {stats['DRAW']}\n"
        f"⚠️ Не визначено: {stats['UNKNOWN']}\n\n"
        f"Баланс результатів: {net_label}"
    )
    image_url = telegram_image_url("summary")
    if image_url:
        try:
            telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": "daily-summary.jpg",
            })
            return
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати зображення підсумку (%s), пробую текст",
                type(exc).__name__,
            )
    telegram_request("/send-message", {"text": text})


def maybe_send_daily_summary(now: float) -> None:
    global daily_summary_sent_date, daily_stats_date, daily_stats
    local_now = datetime.fromtimestamp(now, ZoneInfo(SIGNAL_TIMEZONE))
    if local_now.time() < SIGNAL_END:
        return
    with signal_state_lock:
        if active_signal is not None or pending_signal is not None:
            return
    summary_date = local_now.date().isoformat()
    with daily_stats_lock:
        if daily_summary_sent_date == summary_date:
            return
        if daily_stats_date != summary_date:
            daily_stats_date = summary_date
            daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
        stats = dict(daily_stats)
        daily_summary_sent_date = summary_date
    try:
        send_daily_summary_to_telegram(summary_date, stats)
        log.info(
            "Денний підсумок надіслано: %s total=%s win=%s loss=%s draw=%s unknown=%s",
            summary_date, stats["total"], stats["WIN"], stats["LOSS"],
            stats["DRAW"], stats["UNKNOWN"],
        )
    except Exception as exc:
        log.warning("Не вдалося надіслати денний підсумок: %s", type(exc).__name__)


def send_market_pause_to_telegram(minutes: int, extension: bool = False) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    prefix = "⏸ ПАУЗА ПРОДОВЖЕНА" if extension else "⏸ СИГНАЛИ ПРИЗУПИНЕНО"
    telegram_request("/send-message", {
        "text": (
            f"{prefix}\n\n"
            f"Після серії з трьох невдалих угод нові сигнали зупинено на {minutes} хвилин.\n"
            "Бот стежить за свіжими даними ринку й продовжить пошук, коли умови покращаться."
        ),
    })


def send_market_resume_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": "▶️ ПОШУК ВІДНОВЛЕНО\n\nРинок показав достатній рух для відновлення пошуку сигналів.",
    })


def send_startup_safety_pause_to_telegram(minutes: int) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            "⏸ ПЕРЕВІРКА ПІСЛЯ ПЕРЕЗАПУСКУ\n\n"
            f"Нові сигнали призупинено на {minutes} хвилин. "
            "Бот перевірить свіжі дані кількох пар і відновить пошук лише після підтвердження ринку."
        ),
    })


def send_incomplete_series_to_telegram(signal: dict[str, Any]) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            f"⏹ СЕРІЮ ЗУПИНЕНО • {signal['pair']} OTC\n\n"
            "Наступне перекриття не відкрито, бо завершився робочий час. "
            "Ця незавершена серія не врахована як програш у статистиці."
        ),
    })


def market_has_recovered(snapshot: dict[str, Any]) -> bool:
    closes = [
        float(value) for value in snapshot.get("closes", [])
        if value is not None
    ]
    if len(closes) < 5:
        return False
    recent = closes[-5:]
    deltas = [recent[index] - recent[index - 1] for index in range(1, len(recent))]
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    dominant = max(positive, negative)
    if dominant < 3:
        return False
    movement = abs(recent[-1] - recent[0])
    volatility = max(float(snapshot.get("volatility") or 0), abs(recent[-1]) * 1e-8)
    minimum_move = abs(recent[-1]) * MARKET_RECOVERY_MIN_MOVE_PCT / 100
    return movement >= max(volatility * MARKET_RECOVERY_VOLATILITY_MULTIPLIER, minimum_move)


def start_market_pause(now: float, signal: dict[str, Any]) -> None:
    global market_pause_until, market_pause_started_at
    global market_pause_last_check, market_pause_pair_index
    global market_pause_pairs, market_pause_recovery_seen
    market_pause_started_at = now
    market_pause_until = now + MARKET_PAUSE_SEC
    market_pause_last_check = 0.0
    market_pause_pair_index = 0
    market_pause_recovery_seen = False
    raw_pair = signal.get("raw_pair")
    seen_pairs: set[str] = set()
    market_pause_pairs = []
    for pair in [raw_pair] + OTC_PAIRS:
        if pair and pair not in seen_pairs:
            seen_pairs.add(pair)
            market_pause_pairs.append(pair)
        if len(market_pause_pairs) >= MARKET_MONITOR_PAIRS:
            break
    log.warning(
        "Пауза нових сигналів після третьої невдачі: %s хв; моніторинг пар=%s",
        MARKET_PAUSE_SEC // 60, ", ".join(market_pause_pairs),
    )
    try:
        send_market_pause_to_telegram(MARKET_PAUSE_SEC // 60)
    except Exception as exc:
        log.warning("Не вдалося повідомити про паузу в Telegram: %s", type(exc).__name__)


def start_startup_safety_pause(now: float) -> None:
    global market_pause_until, market_pause_started_at
    global market_pause_last_check, market_pause_pair_index
    global market_pause_pairs, market_pause_recovery_seen
    if not STARTUP_SAFETY_PAUSE_SEC:
        return
    market_pause_started_at = now
    market_pause_until = now + STARTUP_SAFETY_PAUSE_SEC
    market_pause_last_check = 0.0
    market_pause_pair_index = 0
    market_pause_recovery_seen = False
    market_pause_pairs = list(OTC_PAIRS[:MARKET_MONITOR_PAIRS])
    log.warning(
        "Захисна пауза після запуску: %s хв; моніторинг пар=%s",
        STARTUP_SAFETY_PAUSE_SEC // 60, ", ".join(market_pause_pairs),
    )
    try:
        send_startup_safety_pause_to_telegram(STARTUP_SAFETY_PAUSE_SEC // 60)
    except Exception as exc:
        log.warning("Не вдалося повідомити про захисну паузу після запуску: %s", type(exc).__name__)


def monitor_market_pause(now: float) -> bool:
    global market_pause_until, market_pause_started_at
    global market_pause_last_check, market_pause_pair_index
    global market_pause_recovery_seen
    if not market_pause_until:
        return False
    minimum_end = market_pause_started_at + MARKET_PAUSE_SEC
    if (
        now < minimum_end
        and
        market_pause_last_check
        and now - market_pause_last_check < MARKET_MONITOR_INTERVAL_SEC
    ):
        return market_pause_until > now
    if not market_pause_pairs:
        market_pause_pairs.extend(OTC_PAIRS[:MARKET_MONITOR_PAIRS])
    def check_pair(pair: str) -> bool:
        try:
            snapshot = market_snapshot(pair)
        except Exception as exc:
            log.warning(
                "Моніторинг паузи: не вдалося отримати свіжі дані %s: %s",
                display_pair(pair), type(exc).__name__,
            )
            return False
        recovered = market_has_recovered(snapshot)
        log.info(
            "Моніторинг паузи %s: trend=%s volatility=%s recovered=%s",
            display_pair(pair), snapshot.get("trend"),
            snapshot.get("volatility"), recovered,
        )
        return recovered

    market_pause_last_check = now
    if now < minimum_end:
        pair = market_pause_pairs[market_pause_pair_index % len(market_pause_pairs)]
        market_pause_pair_index += 1
        market_pause_recovery_seen = check_pair(pair)
        return True
    recovered_pairs = sum(check_pair(pair) for pair in market_pause_pairs)
    market_pause_recovery_seen = recovered_pairs >= MARKET_RECOVERY_REQUIRED_PAIRS
    if market_pause_recovery_seen:
        market_pause_until = 0.0
        market_pause_started_at = 0.0
        market_pause_recovery_seen = False
        log.info(
            "Пауза завершена: рух підтвердили %s із %s контрольних пар",
            recovered_pairs, len(market_pause_pairs),
        )
        try:
            send_market_resume_to_telegram()
        except Exception as exc:
            log.warning("Не вдалося повідомити про відновлення пошуку: %s", type(exc).__name__)
        return False
    market_pause_started_at = now
    market_pause_until = now + MARKET_PAUSE_SEC
    market_pause_recovery_seen = False
    log.warning(
        "Ринок не покращився: рух підтвердили лише %s із %s пар "
        "(потрібно щонайменше %s); пауза продовжена ще на %s хв",
        recovered_pairs, len(market_pause_pairs), MARKET_RECOVERY_REQUIRED_PAIRS,
        MARKET_PAUSE_SEC // 60,
    )
    try:
        send_market_pause_to_telegram(MARKET_PAUSE_SEC // 60, extension=True)
    except Exception as exc:
        log.warning("Не вдалося повідомити про продовження паузи: %s", type(exc).__name__)
    return True


def generate_best_signal() -> Optional[dict[str, Any]]:
    global signal_searching
    if not signal_search_lock.acquire(blocking=False):
        return None
    with signal_state_lock:
        if active_signal is not None or pending_signal is not None or signal_searching:
            signal_search_lock.release()
            return None
        signal_searching = True
    try:
        return _generate_best_signal()
    finally:
        with signal_state_lock:
            signal_searching = False
        signal_search_lock.release()


def _generate_best_signal() -> Optional[dict[str, Any]]:
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
            log.warning(
                "Пропущено отримання даних %s: %s: %s",
                active, type(exc).__name__, str(exc)[:180],
            )

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


def send_entry_confirmation(signal: dict[str, Any]) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            f"🟢 ВХІД ПІДТВЕРДЖЕНО • {signal['pair']} OTC\n\n"
            f"Фактична ціна входу: {signal['entry_price']}\n"
            f"Експірація: {SIGNAL_DURATION_SEC // 60} хв"
        ),
    })


def close_price(active: str, not_before: float = 0.0) -> float:
    return wait_for_market_price(active, not_before)


def evaluate_signal(signal: dict[str, Any]) -> str:
    try:
        price = close_price(signal["raw_pair"], signal["expires_at"])
        signal["close_price"] = price
        if math.isclose(price, signal["entry_price"], rel_tol=0.0, abs_tol=1e-10):
            signal["result"] = "DRAW"
        elif signal["direction"] == "ВГОРУ":
            signal["result"] = "WIN" if price > signal["entry_price"] else "LOSS"
        else:
            signal["result"] = "WIN" if price < signal["entry_price"] else "LOSS"
        return signal["result"]
    except Exception as exc:
        signal["result"] = "UNKNOWN"
        signal["result_error"] = type(exc).__name__
        log.warning("Не удалось проверить результат %s: %s", signal["pair"], type(exc).__name__)
        return "UNKNOWN"


def format_result(signal: dict[str, Any]) -> str:
    return (
        f"📋 ПІДСУМОК • {signal['pair']} OTC\n"
        f"{signal['result']} | вхід: {signal['entry_price']} | закриття: {signal.get('close_price', 'немає даних')}\n"
        f"Спроба: {signal['attempt'] + 1} із {MAX_OVERLAYS + 1}"
    )


def handle_signal_outcome(signal: dict[str, Any], outcome: str, now: float) -> None:
    global active_signal, pending_signal, next_signal_at
    log.info(
        "Результат спроби: %s %s entry=%s close=%s attempt=%s",
        signal["pair"], outcome, signal["entry_price"],
        signal.get("close_price", "немає даних"), signal["attempt"] + 1,
    )
    has_overlay_left = signal["attempt"] < MAX_OVERLAYS
    if outcome == "LOSS" and has_overlay_left and in_signal_window():
        replacement = dict(signal)
        replacement["attempt"] += 1
        replacement["entry_price"] = signal.get("close_price", signal["entry_price"])
        replacement["result"] = None
        replacement.pop("close_price", None)
        replacement["image_base64"] = ""
        replacement = schedule_signal_entry(replacement)
        with signal_state_lock:
            active_signal = None
            pending_signal = replacement
        persist_signal_state()
        log.info(
            "Невдала спроба %s/%s переведена в перекриття %s/%s",
            signal["attempt"] + 1, MAX_OVERLAYS + 1,
            replacement["attempt"], MAX_OVERLAYS,
        )
        try:
            send_signal_to_telegram(
                replacement,
                f"🔁 ПЕРЕКРИТТЯ {replacement['attempt']}/{MAX_OVERLAYS}\n",
                "overlay",
            )
        except Exception as exc:
            log.warning("Не вдалося надіслати перекриття у Telegram: %s", type(exc).__name__)
        return

    if outcome == "LOSS" and has_overlay_left:
        log.warning(
            "Серію %s не продовжено: наступне перекриття поза робочим вікном; "
            "проміжний результат не враховано",
            signal["pair"],
        )
        try:
            send_incomplete_series_to_telegram(signal)
        except Exception as exc:
            log.warning("Не вдалося повідомити про незавершену серію: %s", type(exc).__name__)
        with signal_state_lock:
            active_signal = None
        persist_signal_state()
        next_signal_at = now + 2
        maybe_send_daily_summary(now)
        return
    record_signal_result(signal)
    signal["result_recorded"] = True
    if outcome == "LOSS" and not has_overlay_left:
        start_market_pause(now, signal)
    deliver_terminal_result(signal, now)


def queue_result_delivery(signal: dict[str, Any], now: float, error: Exception) -> None:
    global active_signal, pending_signal, pending_result_deliveries
    global result_delivery_next_retry_at, result_delivery_last_error
    signal["result_delivery_pending"] = True
    signal["result_delivery_attempts"] = int(signal.get("result_delivery_attempts", 0)) + 1
    with signal_state_lock:
        if active_signal is signal:
            active_signal = None
        if pending_signal is signal:
            pending_signal = None
        pending_result_deliveries.append(signal)
    result_delivery_last_error = type(error).__name__
    result_delivery_next_retry_at = now + RESULT_DELIVERY_RETRY_SEC
    persist_signal_state()


def retry_result_deliveries(now: float) -> None:
    global pending_result_deliveries, result_delivery_next_retry_at
    global result_delivery_last_error
    if not pending_result_deliveries or now < result_delivery_next_retry_at:
        return
    signal = pending_result_deliveries[0]
    try:
        send_result_to_telegram(signal)
    except Exception as exc:
        attempts = int(signal.get("result_delivery_attempts", 1))
        delay = min(5 * 60, RESULT_DELIVERY_RETRY_SEC * (2 ** min(attempts - 1, 3)))
        signal["result_delivery_attempts"] = attempts + 1
        result_delivery_last_error = type(exc).__name__
        result_delivery_next_retry_at = now + delay
        persist_signal_state()
        log.warning(
            "Не вдалося доставити відкладений результат %s (%s); "
            "нова спроба через %ss",
            signal.get("pair", "невідома пара"), type(exc).__name__, delay,
        )
        return
    pending_result_deliveries.pop(0)
    signal.pop("result_delivery_pending", None)
    result_delivery_last_error = None
    result_delivery_next_retry_at = now + RESULT_DELIVERY_RETRY_SEC if pending_result_deliveries else 0.0
    persist_signal_state()
    log.info(
        "Відкладений результат %s доставлено в Telegram",
        signal.get("pair", "невідома пара"),
    )


def deliver_terminal_result(signal: dict[str, Any], now: float) -> bool:
    global active_signal, pending_signal, next_signal_at
    try:
        send_result_to_telegram(signal)
    except Exception as exc:
        queue_result_delivery(signal, now, exc)
        next_signal_at = now + 2
        log.warning(
            "Фінальний результат %s не доставлено в Telegram (%s); "
            "результат поставлено в чергу, пошук нової серії продовжиться",
            signal["pair"], type(exc).__name__,
        )
        return False
    signal.pop("result_delivery_pending", None)
    with signal_state_lock:
        if active_signal is signal:
            active_signal = None
        if pending_signal is signal:
            pending_signal = None
    persist_signal_state()
    next_signal_at = now + 2
    maybe_send_daily_summary(now)
    return True


def finalize_pending_signal(
    signal: dict[str, Any], now: float, reason: str
) -> None:
    global active_signal, pending_signal, next_signal_at
    signal["result"] = "UNKNOWN"
    signal["result_error"] = reason
    log.warning(
        "Сигнал %s не активовано: %s; новий пошук буде дозволено після результату",
        signal["pair"], reason,
    )
    record_signal_result(signal)
    signal["result_recorded"] = True
    deliver_terminal_result(signal, now)


def signal_loop() -> None:
    global active_signal, pending_signal, next_signal_at, search_started_at
    while not signal_stop.wait(1):
        now = time.time()
        retry_result_deliveries(now)
        if not connected:
            continue
        with signal_state_lock:
            current = active_signal
        if current and now >= current["expires_at"]:
            outcome = evaluate_signal(current)
            handle_signal_outcome(current, outcome, now)
            continue
        with signal_state_lock:
            pending = pending_signal
        if pending:
            if now < pending["entry_at"]:
                continue
            if not in_signal_window():
                finalize_pending_signal(pending, now, "початок свічки поза робочим вікном")
                continue
            if now >= pending["entry_at"] + SIGNAL_DURATION_SEC:
                finalize_pending_signal(pending, now, "пропущено час входу")
                continue
            try:
                pending["entry_price"] = wait_for_market_price(
                    pending["raw_pair"], pending["entry_at"]
                )
            except Exception as exc:
                log.warning(
                    "Не вдалося отримати ціну на початку свічки: %s: %s; "
                    "серія залишається заблокованою до завершення очікування",
                    type(exc).__name__, str(exc)[:160],
                )
                next_signal_at = now + 5
                continue
            pending["created_at"] = pending["entry_at"]
            pending["expires_at"] = pending["entry_at"] + SIGNAL_DURATION_SEC
            pending.pop("entry_price_preview", None)
            with signal_state_lock:
                active_signal = pending
                pending_signal = None
            persist_signal_state()
            log.info(
                "Вхід активовано з нової 1-хв свічки: %s о %s, ціна=%s, експірація=%ss",
                pending["pair"], entry_time_label(pending["entry_at"]),
                pending["entry_price"], SIGNAL_DURATION_SEC,
            )
            try:
                send_entry_confirmation(pending)
            except Exception as exc:
                log.warning("Не вдалося надіслати підтвердження входу: %s", type(exc).__name__)
            continue
        maybe_send_daily_summary(now)
        if not in_signal_window():
            continue
        if market_pause_until:
            if monitor_market_pause(now):
                search_started_at = 0.0
                continue
        if current or now < next_signal_at:
            continue
        if signal_is_in_progress():
            search_started_at = 0.0
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
                try:
                    send_search_finished_to_telegram()
                except Exception as exc:
                    log.warning(
                        "Не вдалося надіслати завершення пошуку у Telegram: %s",
                        type(exc).__name__,
                    )
                log.warning(
                    "Пошук сигналу завершено без результату; наступна спроба через %ss",
                    SIGNAL_RETRY_DELAY_SEC,
                )
                search_started_at = 0.0
                next_signal_at = now + SIGNAL_RETRY_DELAY_SEC
            else:
                next_signal_at = max(now + 5, time.time() + (gemini_next_request_at - time.monotonic()))
            continue
        search_started_at = 0.0
        candidate = schedule_signal_entry(candidate)
        with signal_state_lock:
            if active_signal is not None or pending_signal is not None:
                log.warning("Знайдений сигнал відкинуто: інша серія вже активна")
                next_signal_at = now + 2
                continue
            pending_signal = candidate
            log.info(
                "Сигнал поставлено в очікування: %s; пошук заблоковано до фінального результату",
                candidate["pair"],
            )
        persist_signal_state()
        try:
            send_signal_to_telegram(candidate)
        except Exception as exc:
            log.warning("Не удалось отправить сигнал в Telegram: %s", type(exc).__name__)
        next_signal_at = now + SIGNAL_DURATION_SEC


def connect() -> bool:
    global api, connected, last_error, last_balance
    try:
        with api_lock:
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


@app.get("/api/telegram-image/<kind>")
def telegram_image(kind: str):
    path = TELEGRAM_IMAGES.get(kind)
    if not path or not path.exists():
        return jsonify({"error": "Image not found"}), 404
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@app.get("/health")
@app.get("/api/healthz")
@app.get("/api")
def health():
    latest = max(price_times.values(), default=0)
    pause_remaining = max(0, round(market_pause_until - time.time()))
    with signal_state_lock:
        if active_signal is not None:
            series_state = "active"
            series_pair = active_signal.get("pair")
        elif pending_signal is not None:
            series_state = "pending"
            series_pair = pending_signal.get("pair")
        elif signal_searching:
            series_state = "searching"
            series_pair = None
        else:
            series_state = "idle"
            series_pair = None
    return jsonify({
        "status": "ok" if connected else "error", "connected": connected,
        "demo": bool(SSID_META["isDemo"]), "pairs_count": len(OTC_PAIRS),
        "payouts_loaded": len(payout_cache), "min_payout": MIN_PAYOUT,
        "ai_available": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "latest_price_age_sec": round(time.time() - latest, 1) if latest else None,
        "market_pause_active": pause_remaining > 0,
        "market_pause_remaining_sec": pause_remaining,
        "final_result_delivery_mode": FINAL_RESULT_DELIVERY_MODE,
        "series_state": series_state,
        "series_pair": series_pair,
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
    if signal_is_in_progress():
        return jsonify({
            "error": "Сигнал уже активний, очікує входу або виконується пошук",
            "status": "in_progress",
        }), 409
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
    restore_signal_state()
    connect()
    if active_signal is None and pending_signal is None:
        start_startup_safety_pause(time.time())
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
    main()#!/usr/bin/env python3
"""RADIK AI BOT: Pocket Option WebSocket market scanner.

This is the service core extracted from the source repository. It deliberately
does not contain a web UI and never returns the SSID or Gemini key.
"""

# Redundant copy of the module; the future import already appears at file start.

import json
import logging
import os
import re
import statistics
import sys
import base64
import io
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("radik-bot")

RAW_SSID = os.getenv("POCKET_SSID", "").strip()
DEMO = os.getenv("POCKET_DEMO", "true").lower() in {"1", "true", "yes"}
API_PORT = int(os.getenv("API_PORT", "8765"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
REPLIT_GEMINI_MODEL = os.getenv("GEMINI_REPLIT_MODEL", "gemini-2.5-flash")
MIN_PAYOUT = float(os.getenv("MIN_PAYOUT", "92"))
MAX_PRICE_AGE_SEC = int(os.getenv("MAX_PRICE_AGE_SEC", "20"))
CANDLE_TIMEOUT_SEC = int(os.getenv("CANDLE_FETCH_TIMEOUT_SEC", "12"))
SIGNAL_DURATION_SEC = int(os.getenv("SIGNAL_DURATION_SEC", "120"))
SIGNAL_SEARCH_TIMEOUT_SEC = int(os.getenv("SIGNAL_SEARCH_TIMEOUT_SEC", "180"))
SIGNAL_RETRY_DELAY_SEC = max(30, int(os.getenv("SIGNAL_RETRY_DELAY_SEC", "300")))
SIGNAL_SCAN_PAIRS = max(1, int(os.getenv("SIGNAL_SCAN_PAIRS", "1")))
GEMINI_MIN_REQUEST_INTERVAL_SEC = int(os.getenv("GEMINI_MIN_REQUEST_INTERVAL_SEC", "15"))
SIGNAL_ENTRY_NOTICE_SEC = max(5, int(os.getenv("SIGNAL_ENTRY_NOTICE_SEC", "15")))
PRICE_RETRY_TIMEOUT_SEC = max(1, int(os.getenv("PRICE_RETRY_TIMEOUT_SEC", "15")))
MAX_OVERLAYS = min(2, max(0, int(os.getenv("MAX_OVERLAYS", "2"))))
MARKET_PAUSE_SEC = max(20 * 60, int(os.getenv("MARKET_PAUSE_SEC", str(20 * 60))))
STARTUP_SAFETY_PAUSE_SEC = max(
    0, int(os.getenv("STARTUP_SAFETY_PAUSE_SEC", str(MARKET_PAUSE_SEC)))
)
MARKET_MONITOR_INTERVAL_SEC = max(
    30, int(os.getenv("MARKET_MONITOR_INTERVAL_SEC", "60"))
)
MARKET_MONITOR_PAIRS = max(1, int(os.getenv("MARKET_MONITOR_PAIRS", "3")))
MARKET_RECOVERY_REQUIRED_PAIRS = min(
    MARKET_MONITOR_PAIRS,
    max(1, int(os.getenv("MARKET_RECOVERY_REQUIRED_PAIRS", "2"))),
)
MARKET_RECOVERY_VOLATILITY_MULTIPLIER = float(
    os.getenv("MARKET_RECOVERY_VOLATILITY_MULTIPLIER", "1.25")
)
MARKET_RECOVERY_MIN_MOVE_PCT = float(
    os.getenv("MARKET_RECOVERY_MIN_MOVE_PCT", "0.015")
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BRIDGE_URL = os.getenv("TELEGRAM_BRIDGE_URL", "http://127.0.0.1:8010").rstrip("/")
TELEGRAM_PUBLIC_BASE_URL = os.getenv("TELEGRAM_PUBLIC_BASE_URL", "").rstrip("/")
SIGNAL_TIMEZONE = os.getenv("SIGNAL_TIMEZONE", "Europe/Kyiv")
SIGNAL_START = clock_time.fromisoformat(os.getenv("SIGNAL_START_HOUR", "09:00"))
SIGNAL_END = clock_time.fromisoformat(os.getenv("SIGNAL_END_HOUR", "23:00"))
ASSET_DIR = Path(__file__).resolve().parent / "assets"
TELEGRAM_IMAGES = {
    "signal": ASSET_DIR / "signal.jpg",
    "overlay": ASSET_DIR / "overlay.jpg",
    "summary": ASSET_DIR / "summary.jpg",
    "search": ASSET_DIR / "search.jpg",
    "win": ASSET_DIR / "win.jpg",
    "loss": ASSET_DIR / "loss.jpg",
}
FINAL_RESULT_DELIVERY_MODE = "text-only"
SIGNAL_STATE_FILE = Path(
    os.getenv("RADIK_SIGNAL_STATE_FILE", "/tmp/radik-signal-state.json")
)

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
market_request_lock = threading.Lock()
# All calls into the PocketOption client (connect/get_payout/ChangeSymbol/
# get_server_timestamp/balance) must be serialized behind this lock. The
# underlying websocket client is not thread-safe; concurrent access from the
# scheduler thread, Flask request threads, and the candle worker thread caused
# a native "malloc(): unsorted double linked list corrupted" crash that killed
# the whole process with no way to recover in Python (see market-pause-policy
# memory notes). Use RLock because connect() and market_snapshot() may nest.
api_lock = threading.RLock()
analysis_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
signal_search_lock = threading.Lock()
signal_stop = threading.Event()
signal_state_lock = threading.Lock()
daily_stats_lock = threading.Lock()
active_signal: Optional[dict[str, Any]] = None
pending_signal: Optional[dict[str, Any]] = None
signal_searching = False
next_signal_at = 0.0
search_started_at = 0.0
scan_cursor = 0
gemini_next_request_at = 0.0
signal_thread: Optional[threading.Thread] = None
daily_stats_date = datetime.now(ZoneInfo(SIGNAL_TIMEZONE)).date().isoformat()
daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
daily_summary_sent_date: Optional[str] = None
pending_result_deliveries: list[dict[str, Any]] = []
result_delivery_next_retry_at = 0.0
result_delivery_last_error: Optional[str] = None
RESULT_DELIVERY_RETRY_SEC = 60


def persist_signal_state() -> None:
    with signal_state_lock:
        payload = {
            "active_signal": active_signal,
            "pending_signal": pending_signal,
            "pending_result_deliveries": pending_result_deliveries,
            "saved_at": time.time(),
        }
    try:
        if (
            not payload["active_signal"]
            and not payload["pending_signal"]
            and not payload["pending_result_deliveries"]
        ):
            SIGNAL_STATE_FILE.unlink(missing_ok=True)
            return
        SIGNAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SIGNAL_STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(SIGNAL_STATE_FILE)
    except Exception as exc:
        log.warning("Не вдалося зберегти стан активної серії: %s", type(exc).__name__)


def restore_signal_state() -> None:
    global active_signal, pending_signal, pending_result_deliveries
    try:
        payload = json.loads(SIGNAL_STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:
        log.warning("Не вдалося відновити стан активної серії: %s", type(exc).__name__)
        return
    active = payload.get("active_signal")
    pending = payload.get("pending_signal")
    if not isinstance(active, dict):
        active = None
    if not isinstance(pending, dict):
        pending = None
    queued = payload.get("pending_result_deliveries") or []
    if not isinstance(queued, list):
        queued = []
    queued = [item for item in queued if isinstance(item, dict)]
    if active and active.get("result_delivery_pending"):
        queued.insert(0, active)
        active = None
    with signal_state_lock:
        active_signal = active
        pending_signal = pending
        pending_result_deliveries = queued
    if active or pending:
        restored = active or pending
        log.warning(
            "Відновлено незавершену серію %s після перезапуску; новий пошук заблоковано",
            restored.get("pair", "невідома пара"),
        )
    if queued:
        log.warning(
            "Відновлено %s результатів, які очікують доставки в Telegram",
            len(queued),
        )
market_pause_until = 0.0
market_pause_started_at = 0.0
market_pause_last_check = 0.0
market_pause_pair_index = 0
market_pause_pairs: list[str] = []
market_pause_recovery_seen = False


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
        with api_lock:
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
    with api_lock:
        with market_request_lock:
            api.api.history_new = None
            future = candle_pool.submit(api.ChangeSymbol, active, 60)
            try:
                if not future.result(timeout=CANDLE_TIMEOUT_SEC):
                    raise RuntimeError("Свечі не отримані")
            except FutureTimeoutError as exc:
                raise RuntimeError("Таймаут підписки на ринкові дані") from exc
        market = global_value.pairs.get(active) or {}
        candles = market.get("history") or []
        ticks = market.get("ticks") or []
        if len(candles) < 15:
            raise RuntimeError("Недостаточно свечей")
        live = next(
            (item for item in reversed(ticks)
             if isinstance(item, dict) and item.get("price") is not None),
            None,
        )
        if not live:
            raise RuntimeError("Нет live-котировки")
        price = float(live["price"])
        server_now = float(api.get_server_timestamp() or time.time())
    tick_time = float(live.get("time") or 0)
    age = max(0.0, server_now - tick_time) if tick_time else MAX_PRICE_AGE_SEC + 1
    if age > MAX_PRICE_AGE_SEC:
        raise RuntimeError("Котировка устарела")
    closes = [
        float(candle["close"])
        for candle in sorted(candles, key=lambda candle: candle.get("time", 0))
        if candle.get("close") is not None
    ][-30:]
    if len(closes) < 15:
        raise RuntimeError("Недостаточно закрытий свечей")
    price_cache[active], price_times[active] = price, time.time()
    return {
        "active": active, "price": price, "tick_time": tick_time,
        "price_age_sec": round(age, 2),
        "closes": closes, "trend": "up" if closes[-1] >= closes[-5] else "down",
        "volatility": statistics.pstdev(closes[-12:]),
    }


def latest_market_price(active: str) -> float:
    """Refresh the pair so settlement never relies on a cached tick."""
    return float(market_snapshot(active)["price"])


def wait_for_market_price(active: str, not_before: float = 0.0) -> float:
    deadline = time.monotonic() + PRICE_RETRY_TIMEOUT_SEC
    last_error_message = "Нет live-котировки"
    while True:
        try:
            snapshot = market_snapshot(active)
            if not_before and snapshot["tick_time"] < not_before:
                raise RuntimeError("Котировка ще не оновилась для часу угоди")
            return float(snapshot["price"])
        except Exception as exc:
            last_error_message = str(exc) or type(exc).__name__
            if time.monotonic() >= deadline:
                raise RuntimeError(last_error_message) from exc
            time.sleep(0.5)


def gemini_analysis(snapshot: dict[str, Any]) -> dict[str, Any]:
    global gemini_next_request_at
    now = time.monotonic()
    if now < gemini_next_request_at:
        raise RuntimeError("Gemini cooldown")
    gemini_next_request_at = now + GEMINI_MIN_REQUEST_INTERVAL_SEC
    replit_base_url = os.getenv("AI_INTEGRATIONS_GEMINI_BASE_URL", "").strip().rstrip("/")
    replit_key = os.getenv("AI_INTEGRATIONS_GEMINI_API_KEY", "").strip()
    if replit_base_url and replit_key:
        model = REPLIT_GEMINI_MODEL
        api_url = f"{replit_base_url}/models/{model}:generateContent"
        headers = {"x-goog-api-key": replit_key}
        source = "replit-gemini"
    else:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Gemini API не настроен")
        model = GEMINI_MODEL
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {}
        source = "gemini"
    prompt = (
        "Ти аналізуєш короткостроковий ринок бінарних опціонів. "
        "Це освітній аналітичний сигнал, а не гарантія прибутку. "
        'Поверни лише JSON виду {"direction":"ВГОРУ" або "ВНИЗ",'
        '"confidence":0-100,"reason":"коротко українською мовою"}. '
        f"Пара {snapshot['active']}, ціна {snapshot['price']}, тренд {snapshot['trend']}, "
        f"волатильність {snapshot['volatility']}, останні закриття {snapshot['closes']}."
    )
    response = requests.post(
        api_url,
        params={} if source == "replit-gemini" else {"key": key},
        headers=headers,
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
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
        "source": source,
    }


def in_signal_window() -> bool:
    now = datetime.now(ZoneInfo(SIGNAL_TIMEZONE)).time()
    if SIGNAL_START <= SIGNAL_END:
        return SIGNAL_START <= now < SIGNAL_END
    return now >= SIGNAL_START or now < SIGNAL_END


def next_candle_start(timestamp: float) -> float:
    """Return a future one-minute boundary with enough notice to enter."""
    entry_at = (math.floor(timestamp / 60) + 1) * 60
    if entry_at - timestamp < SIGNAL_ENTRY_NOTICE_SEC:
        entry_at += 60
    return float(entry_at)


def schedule_signal_entry(signal: dict[str, Any]) -> dict[str, Any]:
    pending = dict(signal)
    pending["entry_at"] = next_candle_start(time.time())
    pending["entry_price_preview"] = pending.get("entry_price")
    pending["entry_price"] = None
    pending["created_at"] = None
    pending["expires_at"] = None
    return pending


def entry_time_label(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo(SIGNAL_TIMEZONE)).strftime("%H:%M")


def signal_is_in_progress() -> bool:
    with signal_state_lock:
        return (
            active_signal is not None
            or pending_signal is not None
            or signal_searching
        )


def result_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, ZoneInfo(SIGNAL_TIMEZONE)).date().isoformat()


def record_signal_result(signal: dict[str, Any]) -> None:
    global daily_stats_date, daily_stats
    timestamp = float(signal.get("entry_at") or signal.get("created_at") or time.time())
    day = result_day(timestamp)
    outcome = signal.get("result", "UNKNOWN")
    if outcome not in {"WIN", "LOSS", "DRAW", "UNKNOWN"}:
        outcome = "UNKNOWN"
    with daily_stats_lock:
        if day != daily_stats_date:
            daily_stats_date = day
            daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
        daily_stats["total"] += 1
        daily_stats[outcome] += 1


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


def telegram_image_url(kind: str) -> str:
    if kind not in TELEGRAM_IMAGES or not TELEGRAM_PUBLIC_BASE_URL:
        return ""
    return f"{TELEGRAM_PUBLIC_BASE_URL}/api/telegram-image/{kind}"


def send_signal_to_telegram(
    signal: dict[str, Any], caption_prefix: str = "", image_kind: str = "signal"
) -> None:
    if not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID не настроен, сигнал останется только в API")
        return
    entry_at = signal.get("entry_at")
    if entry_at:
        entry_line = (
            f"🕐 Вхід: з нової 1-хв свічки о {entry_time_label(entry_at)}:00 "
            f"({SIGNAL_TIMEZONE})"
        )
        price_line = "💰 Ціна входу: фіксується на початку свічки"
    else:
        entry_line = ""
        price_line = f"💰 Ціна входу: {signal['entry_price']}"
    text = (
        f"{caption_prefix}📊 СИГНАЛ • {signal['pair']} OTC\n\n"
        f"📈 Напрямок: {signal['direction']}\n"
        f"{price_line}\n"
        f"{entry_line}\n"
        f"⏱ Експірація: {SIGNAL_DURATION_SEC // 60} хв\n"
        f"💎 Виплата: {signal['payout']:.0f}%\n"
        f"🎯 Впевненість: {signal['confidence']}%\n\n"
        f"📝 {signal['reason']}"
    )
    image_url = telegram_image_url(image_kind)
    if image_url:
        try:
            telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": f"{image_kind}.jpg",
            })
            return
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати зображення сигналу %s (%s), пробую текст",
                signal["pair"], type(exc).__name__,
            )
    telegram_request("/send-message", {"text": text})


def send_search_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    text = "🔎 ПОШУК СИГНАЛУ\n\nАналізую ринок до 3 хвилин та чекаю на якісний сетап."
    image_url = telegram_image_url("search")
    if image_url:
        try:
            telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": "search.jpg",
            })
            return
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати зображення пошуку (%s), пробую текст",
                type(exc).__name__,
            )
    telegram_request("/send-message", {"text": text})


def send_search_finished_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            "⏸ ПОШУК ЗАВЕРШЕНО\n\n"
            f"За {SIGNAL_SEARCH_TIMEOUT_SEC // 60} хв якісний сигнал не підтверджено. "
            f"Наступна перевірка через {SIGNAL_RETRY_DELAY_SEC // 60} хв."
        ),
    })


def send_result_to_telegram(signal: dict[str, Any]) -> None:
    label = (
        "✅ ВИГРАШ" if signal["result"] == "WIN"
        else "❌ ПРОГРАШ" if signal["result"] == "LOSS"
        else "➖ НІЧИЯ / ПОВЕРНЕННЯ" if signal["result"] == "DRAW"
        else "⚠️ НЕ ВИЗНАЧЕНО"
    )
    text = (
        f"📌 РЕЗУЛЬТАТ • {signal['pair']} OTC\n\n"
        f"{label}\n"
        f"Ціна входу: {signal.get('entry_price') or 'не зафіксовано'}\n"
        f"Ціна закриття: {signal.get('close_price', 'немає даних')}\n"
        f"Спроба: {signal['attempt'] + 1} із {MAX_OVERLAYS + 1}"
    )
    image_kind = {"WIN": "win", "LOSS": "loss"}.get(signal.get("result"))
    image_url = telegram_image_url(image_kind) if image_kind else ""
    if image_url:
        try:
            delivery = telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": f"{image_kind}.jpg",
            })
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати картинку результату %s (%s), пробую текст",
                signal["pair"], type(exc).__name__,
            )
            delivery = telegram_request("/send-message", {"text": text})
    else:
        delivery = telegram_request("/send-message", {"text": text})
    log.info(
        "Фінальний результат %s підтверджено Telegram message_id=%s",
        signal["pair"], delivery.get("message_id", "невідомо"),
    )


def send_daily_summary_to_telegram(summary_date: str, stats: dict[str, int]) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    net = stats["WIN"] - stats["LOSS"]
    net_label = f"+{net}" if net > 0 else str(net)
    text = (
        f"📊 ПІДСУМОК ДНЯ • {summary_date}\n\n"
        f"Усього спроб: {stats['total']}\n"
        f"✅ Виграшів: {stats['WIN']}\n"
        f"❌ Програшів: {stats['LOSS']}\n"
        f"➖ Нічиїх: {stats['DRAW']}\n"
        f"⚠️ Не визначено: {stats['UNKNOWN']}\n\n"
        f"Баланс результатів: {net_label}"
    )
    image_url = telegram_image_url("summary")
    if image_url:
        try:
            telegram_request("/send-photo", {
                "image_url": image_url,
                "caption": text,
                "filename": "daily-summary.jpg",
            })
            return
        except Exception as exc:
            log.warning(
                "Не вдалося надіслати зображення підсумку (%s), пробую текст",
                type(exc).__name__,
            )
    telegram_request("/send-message", {"text": text})


def maybe_send_daily_summary(now: float) -> None:
    global daily_summary_sent_date, daily_stats_date, daily_stats
    local_now = datetime.fromtimestamp(now, ZoneInfo(SIGNAL_TIMEZONE))
    if local_now.time() < SIGNAL_END:
        return
    with signal_state_lock:
        if active_signal is not None or pending_signal is not None:
            return
    summary_date = local_now.date().isoformat()
    with daily_stats_lock:
        if daily_summary_sent_date == summary_date:
            return
        if daily_stats_date != summary_date:
            daily_stats_date = summary_date
            daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
        stats = dict(daily_stats)
        daily_summary_sent_date = summary_date
    try:
        send_daily_summary_to_telegram(summary_date, stats)
        log.info(
            "Денний підсумок надіслано: %s total=%s win=%s loss=%s draw=%s unknown=%s",
            summary_date, stats["total"], stats["WIN"], stats["LOSS"],
            stats["DRAW"], stats["UNKNOWN"],
        )
    except Exception as exc:
        log.warning("Не вдалося надіслати денний підсумок: %s", type(exc).__name__)


def send_market_pause_to_telegram(minutes: int, extension: bool = False) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    prefix = "⏸ ПАУЗА ПРОДОВЖЕНА" if extension else "⏸ СИГНАЛИ ПРИЗУПИНЕНО"
    telegram_request("/send-message", {
        "text": (
            f"{prefix}\n\n"
            f"Після серії з трьох невдалих угод нові сигнали зупинено на {minutes} хвилин.\n"
            "Бот стежить за свіжими даними ринку й продовжить пошук, коли умови покращаться."
        ),
    })


def send_market_resume_to_telegram() -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": "▶️ ПОШУК ВІДНОВЛЕНО\n\nРинок показав достатній рух для відновлення пошуку сигналів.",
    })


def send_startup_safety_pause_to_telegram(minutes: int) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            "⏸ ПЕРЕВІРКА ПІСЛЯ ПЕРЕЗАПУСКУ\n\n"
            f"Нові сигнали призупинено на {minutes} хвилин. "
            "Бот перевірить свіжі дані кількох пар і відновить пошук лише після підтвердження ринку."
        ),
    })


def send_incomplete_series_to_telegram(signal: dict[str, Any]) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            f"⏹ СЕРІЮ ЗУПИНЕНО • {signal['pair']} OTC\n\n"
            "Наступне перекриття не відкрито, бо завершився робочий час. "
            "Ця незавершена серія не врахована як програш у статистиці."
        ),
    })


def market_has_recovered(snapshot: dict[str, Any]) -> bool:
    closes = [
        float(value) for value in snapshot.get("closes", [])
        if value is not None
    ]
    if len(closes) < 5:
        return False
    recent = closes[-5:]
    deltas = [recent[index] - recent[index - 1] for index in range(1, len(recent))]
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    dominant = max(positive, negative)
    if dominant < 3:
        return False
    movement = abs(recent[-1] - recent[0])
    volatility = max(float(snapshot.get("volatility") or 0), abs(recent[-1]) * 1e-8)
    minimum_move = abs(recent[-1]) * MARKET_RECOVERY_MIN_MOVE_PCT / 100
    return movement >= max(volatility * MARKET_RECOVERY_VOLATILITY_MULTIPLIER, minimum_move)


def start_market_pause(now: float, signal: dict[str, Any]) -> None:
    global market_pause_until, market_pause_started_at
    global market_pause_last_check, market_pause_pair_index
    global market_pause_pairs, market_pause_recovery_seen
    market_pause_started_at = now
    market_pause_until = now + MARKET_PAUSE_SEC
    market_pause_last_check = 0.0
    market_pause_pair_index = 0
    market_pause_recovery_seen = False
    raw_pair = signal.get("raw_pair")
    seen_pairs: set[str] = set()
    market_pause_pairs = []
    for pair in [raw_pair] + OTC_PAIRS:
        if pair and pair not in seen_pairs:
            seen_pairs.add(pair)
            market_pause_pairs.append(pair)
        if len(market_pause_pairs) >= MARKET_MONITOR_PAIRS:
            break
    log.warning(
        "Пауза нових сигналів після третьої невдачі: %s хв; моніторинг пар=%s",
        MARKET_PAUSE_SEC // 60, ", ".join(market_pause_pairs),
    )
    try:
        send_market_pause_to_telegram(MARKET_PAUSE_SEC // 60)
    except Exception as exc:
        log.warning("Не вдалося повідомити про паузу в Telegram: %s", type(exc).__name__)


def start_startup_safety_pause(now: float) -> None:
    global market_pause_until, market_pause_started_at
    global market_pause_last_check, market_pause_pair_index
    global market_pause_pairs, market_pause_recovery_seen
    if not STARTUP_SAFETY_PAUSE_SEC:
        return
    market_pause_started_at = now
    market_pause_until = now + STARTUP_SAFETY_PAUSE_SEC
    market_pause_last_check = 0.0
    market_pause_pair_index = 0
    market_pause_recovery_seen = False
    market_pause_pairs = list(OTC_PAIRS[:MARKET_MONITOR_PAIRS])
    log.warning(
        "Захисна пауза після запуску: %s хв; моніторинг пар=%s",
        STARTUP_SAFETY_PAUSE_SEC // 60, ", ".join(market_pause_pairs),
    )
    try:
        send_startup_safety_pause_to_telegram(STARTUP_SAFETY_PAUSE_SEC // 60)
    except Exception as exc:
        log.warning("Не вдалося повідомити про захисну паузу після запуску: %s", type(exc).__name__)


def monitor_market_pause(now: float) -> bool:
    global market_pause_until, market_pause_started_at
    global market_pause_last_check, market_pause_pair_index
    global market_pause_recovery_seen
    if not market_pause_until:
        return False
    minimum_end = market_pause_started_at + MARKET_PAUSE_SEC
    if (
        now < minimum_end
        and
        market_pause_last_check
        and now - market_pause_last_check < MARKET_MONITOR_INTERVAL_SEC
    ):
        return market_pause_until > now
    if not market_pause_pairs:
        market_pause_pairs.extend(OTC_PAIRS[:MARKET_MONITOR_PAIRS])
    def check_pair(pair: str) -> bool:
        try:
            snapshot = market_snapshot(pair)
        except Exception as exc:
            log.warning(
                "Моніторинг паузи: не вдалося отримати свіжі дані %s: %s",
                display_pair(pair), type(exc).__name__,
            )
            return False
        recovered = market_has_recovered(snapshot)
        log.info(
            "Моніторинг паузи %s: trend=%s volatility=%s recovered=%s",
            display_pair(pair), snapshot.get("trend"),
            snapshot.get("volatility"), recovered,
        )
        return recovered

    market_pause_last_check = now
    if now < minimum_end:
        pair = market_pause_pairs[market_pause_pair_index % len(market_pause_pairs)]
        market_pause_pair_index += 1
        market_pause_recovery_seen = check_pair(pair)
        return True
    recovered_pairs = sum(check_pair(pair) for pair in market_pause_pairs)
    market_pause_recovery_seen = recovered_pairs >= MARKET_RECOVERY_REQUIRED_PAIRS
    if market_pause_recovery_seen:
        market_pause_until = 0.0
        market_pause_started_at = 0.0
        market_pause_recovery_seen = False
        log.info(
            "Пауза завершена: рух підтвердили %s із %s контрольних пар",
            recovered_pairs, len(market_pause_pairs),
        )
        try:
            send_market_resume_to_telegram()
        except Exception as exc:
            log.warning("Не вдалося повідомити про відновлення пошуку: %s", type(exc).__name__)
        return False
    market_pause_started_at = now
    market_pause_until = now + MARKET_PAUSE_SEC
    market_pause_recovery_seen = False
    log.warning(
        "Ринок не покращився: рух підтвердили лише %s із %s пар "
        "(потрібно щонайменше %s); пауза продовжена ще на %s хв",
        recovered_pairs, len(market_pause_pairs), MARKET_RECOVERY_REQUIRED_PAIRS,
        MARKET_PAUSE_SEC // 60,
    )
    try:
        send_market_pause_to_telegram(MARKET_PAUSE_SEC // 60, extension=True)
    except Exception as exc:
        log.warning("Не вдалося повідомити про продовження паузи: %s", type(exc).__name__)
    return True


def generate_best_signal() -> Optional[dict[str, Any]]:
    global signal_searching
    if not signal_search_lock.acquire(blocking=False):
        return None
    with signal_state_lock:
        if active_signal is not None or pending_signal is not None or signal_searching:
            signal_search_lock.release()
            return None
        signal_searching = True
    try:
        return _generate_best_signal()
    finally:
        with signal_state_lock:
            signal_searching = False
        signal_search_lock.release()


def _generate_best_signal() -> Optional[dict[str, Any]]:
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
            log.warning(
                "Пропущено отримання даних %s: %s: %s",
                active, type(exc).__name__, str(exc)[:180],
            )

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


def send_entry_confirmation(signal: dict[str, Any]) -> None:
    if not TELEGRAM_CHAT_ID:
        return
    telegram_request("/send-message", {
        "text": (
            f"🟢 ВХІД ПІДТВЕРДЖЕНО • {signal['pair']} OTC\n\n"
            f"Фактична ціна входу: {signal['entry_price']}\n"
            f"Експірація: {SIGNAL_DURATION_SEC // 60} хв"
        ),
    })


def close_price(active: str, not_before: float = 0.0) -> float:
    return wait_for_market_price(active, not_before)


def evaluate_signal(signal: dict[str, Any]) -> str:
    try:
        price = close_price(signal["raw_pair"], signal["expires_at"])
        signal["close_price"] = price
        if math.isclose(price, signal["entry_price"], rel_tol=0.0, abs_tol=1e-10):
            signal["result"] = "DRAW"
        elif signal["direction"] == "ВГОРУ":
            signal["result"] = "WIN" if price > signal["entry_price"] else "LOSS"
        else:
            signal["result"] = "WIN" if price < signal["entry_price"] else "LOSS"
        return signal["result"]
    except Exception as exc:
        signal["result"] = "UNKNOWN"
        signal["result_error"] = type(exc).__name__
        log.warning("Не удалось проверить результат %s: %s", signal["pair"], type(exc).__name__)
        return "UNKNOWN"


def format_result(signal: dict[str, Any]) -> str:
    return (
        f"📋 ПІДСУМОК • {signal['pair']} OTC\n"
        f"{signal['result']} | вхід: {signal['entry_price']} | закриття: {signal.get('close_price', 'немає даних')}\n"
        f"Спроба: {signal['attempt'] + 1} із {MAX_OVERLAYS + 1}"
    )


def handle_signal_outcome(signal: dict[str, Any], outcome: str, now: float) -> None:
    global active_signal, pending_signal, next_signal_at
    log.info(
        "Результат спроби: %s %s entry=%s close=%s attempt=%s",
        signal["pair"], outcome, signal["entry_price"],
        signal.get("close_price", "немає даних"), signal["attempt"] + 1,
    )
    has_overlay_left = signal["attempt"] < MAX_OVERLAYS
    if outcome == "LOSS" and has_overlay_left and in_signal_window():
        replacement = dict(signal)
        replacement["attempt"] += 1
        replacement["entry_price"] = signal.get("close_price", signal["entry_price"])
        replacement["result"] = None
        replacement.pop("close_price", None)
        replacement["image_base64"] = ""
        replacement = schedule_signal_entry(replacement)
        with signal_state_lock:
            active_signal = None
            pending_signal = replacement
        persist_signal_state()
        log.info(
            "Невдала спроба %s/%s переведена в перекриття %s/%s",
            signal["attempt"] + 1, MAX_OVERLAYS + 1,
            replacement["attempt"], MAX_OVERLAYS,
        )
        try:
            send_signal_to_telegram(
                replacement,
                f"🔁 ПЕРЕКРИТТЯ {replacement['attempt']}/{MAX_OVERLAYS}\n",
                "overlay",
            )
        except Exception as exc:
            log.warning("Не вдалося надіслати перекриття у Telegram: %s", type(exc).__name__)
        return

    if outcome == "LOSS" and has_overlay_left:
        log.warning(
            "Серію %s не продовжено: наступне перекриття поза робочим вікном; "
            "проміжний результат не враховано",
            signal["pair"],
        )
        try:
            send_incomplete_series_to_telegram(signal)
        except Exception as exc:
            log.warning("Не вдалося повідомити про незавершену серію: %s", type(exc).__name__)
        with signal_state_lock:
            active_signal = None
        persist_signal_state()
        next_signal_at = now + 2
        maybe_send_daily_summary(now)
        return
    record_signal_result(signal)
    signal["result_recorded"] = True
    if outcome == "LOSS" and not has_overlay_left:
        start_market_pause(now, signal)
    deliver_terminal_result(signal, now)


def queue_result_delivery(signal: dict[str, Any], now: float, error: Exception) -> None:
    global active_signal, pending_signal, pending_result_deliveries
    global result_delivery_next_retry_at, result_delivery_last_error
    signal["result_delivery_pending"] = True
    signal["result_delivery_attempts"] = int(signal.get("result_delivery_attempts", 0)) + 1
    with signal_state_lock:
        if active_signal is signal:
            active_signal = None
        if pending_signal is signal:
            pending_signal = None
        pending_result_deliveries.append(signal)
    result_delivery_last_error = type(error).__name__
    result_delivery_next_retry_at = now + RESULT_DELIVERY_RETRY_SEC
    persist_signal_state()


def retry_result_deliveries(now: float) -> None:
    global pending_result_deliveries, result_delivery_next_retry_at
    global result_delivery_last_error
    if not pending_result_deliveries or now < result_delivery_next_retry_at:
        return
    signal = pending_result_deliveries[0]
    try:
        send_result_to_telegram(signal)
    except Exception as exc:
        attempts = int(signal.get("result_delivery_attempts", 1))
        delay = min(5 * 60, RESULT_DELIVERY_RETRY_SEC * (2 ** min(attempts - 1, 3)))
        signal["result_delivery_attempts"] = attempts + 1
        result_delivery_last_error = type(exc).__name__
        result_delivery_next_retry_at = now + delay
        persist_signal_state()
        log.warning(
            "Не вдалося доставити відкладений результат %s (%s); "
            "нова спроба через %ss",
            signal.get("pair", "невідома пара"), type(exc).__name__, delay,
        )
        return
    pending_result_deliveries.pop(0)
    signal.pop("result_delivery_pending", None)
    result_delivery_last_error = None
    result_delivery_next_retry_at = now + RESULT_DELIVERY_RETRY_SEC if pending_result_deliveries else 0.0
    persist_signal_state()
    log.info(
        "Відкладений результат %s доставлено в Telegram",
        signal.get("pair", "невідома пара"),
    )


def deliver_terminal_result(signal: dict[str, Any], now: float) -> bool:
    global active_signal, pending_signal, next_signal_at
    try:
        send_result_to_telegram(signal)
    except Exception as exc:
        queue_result_delivery(signal, now, exc)
        next_signal_at = now + 2
        log.warning(
            "Фінальний результат %s не доставлено в Telegram (%s); "
            "результат поставлено в чергу, пошук нової серії продовжиться",
            signal["pair"], type(exc).__name__,
        )
        return False
    signal.pop("result_delivery_pending", None)
    with signal_state_lock:
        if active_signal is signal:
            active_signal = None
        if pending_signal is signal:
            pending_signal = None
    persist_signal_state()
    next_signal_at = now + 2
    maybe_send_daily_summary(now)
    return True


def finalize_pending_signal(
    signal: dict[str, Any], now: float, reason: str
) -> None:
    global active_signal, pending_signal, next_signal_at
    signal["result"] = "UNKNOWN"
    signal["result_error"] = reason
    log.warning(
        "Сигнал %s не активовано: %s; новий пошук буде дозволено після результату",
        signal["pair"], reason,
    )
    record_signal_result(signal)
    signal["result_recorded"] = True
    deliver_terminal_result(signal, now)


def signal_loop() -> None:
    global active_signal, pending_signal, next_signal_at, search_started_at
    while not signal_stop.wait(1):
        now = time.time()
        retry_result_deliveries(now)
        if not connected:
            continue
        with signal_state_lock:
            current = active_signal
        if current and now >= current["expires_at"]:
            outcome = evaluate_signal(current)
            handle_signal_outcome(current, outcome, now)
            continue
        with signal_state_lock:
            pending = pending_signal
        if pending:
            if now < pending["entry_at"]:
                continue
            if not in_signal_window():
                finalize_pending_signal(pending, now, "початок свічки поза робочим вікном")
                continue
            if now >= pending["entry_at"] + SIGNAL_DURATION_SEC:
                finalize_pending_signal(pending, now, "пропущено час входу")
                continue
            try:
                pending["entry_price"] = wait_for_market_price(
                    pending["raw_pair"], pending["entry_at"]
                )
            except Exception as exc:
                log.warning(
                    "Не вдалося отримати ціну на початку свічки: %s: %s; "
                    "серія залишається заблокованою до завершення очікування",
                    type(exc).__name__, str(exc)[:160],
                )
                next_signal_at = now + 5
                continue
            pending["created_at"] = pending["entry_at"]
            pending["expires_at"] = pending["entry_at"] + SIGNAL_DURATION_SEC
            pending.pop("entry_price_preview", None)
            with signal_state_lock:
                active_signal = pending
                pending_signal = None
            persist_signal_state()
            log.info(
                "Вхід активовано з нової 1-хв свічки: %s о %s, ціна=%s, експірація=%ss",
                pending["pair"], entry_time_label(pending["entry_at"]),
                pending["entry_price"], SIGNAL_DURATION_SEC,
            )
            try:
                send_entry_confirmation(pending)
            except Exception as exc:
                log.warning("Не вдалося надіслати підтвердження входу: %s", type(exc).__name__)
            continue
        maybe_send_daily_summary(now)
        if not in_signal_window():
            continue
        if market_pause_until:
            if monitor_market_pause(now):
                search_started_at = 0.0
                continue
        if current or now < next_signal_at:
            continue
        if signal_is_in_progress():
            search_started_at = 0.0
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
                try:
                    send_search_finished_to_telegram()
                except Exception as exc:
                    log.warning(
                        "Не вдалося надіслати завершення пошуку у Telegram: %s",
                        type(exc).__name__,
                    )
                log.warning(
                    "Пошук сигналу завершено без результату; наступна спроба через %ss",
                    SIGNAL_RETRY_DELAY_SEC,
                )
                search_started_at = 0.0
                next_signal_at = now + SIGNAL_RETRY_DELAY_SEC
            else:
                next_signal_at = max(now + 5, time.time() + (gemini_next_request_at - time.monotonic()))
            continue
        search_started_at = 0.0
        candidate = schedule_signal_entry(candidate)
        with signal_state_lock:
            if active_signal is not None or pending_signal is not None:
                log.warning("Знайдений сигнал відкинуто: інша серія вже активна")
                next_signal_at = now + 2
                continue
            pending_signal = candidate
            log.info(
                "Сигнал поставлено в очікування: %s; пошук заблоковано до фінального результату",
                candidate["pair"],
            )
        persist_signal_state()
        try:
            send_signal_to_telegram(candidate)
        except Exception as exc:
            log.warning("Не удалось отправить сигнал в Telegram: %s", type(exc).__name__)
        next_signal_at = now + SIGNAL_DURATION_SEC


def connect() -> bool:
    global api, connected, last_error, last_balance
    try:
        with api_lock:
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


@app.get("/api/telegram-image/<kind>")
def telegram_image(kind: str):
    path = TELEGRAM_IMAGES.get(kind)
    if not path or not path.exists():
        return jsonify({"error": "Image not found"}), 404
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@app.get("/health")
@app.get("/api/healthz")
@app.get("/api")
def health():
    latest = max(price_times.values(), default=0)
    pause_remaining = max(0, round(market_pause_until - time.time()))
    with signal_state_lock:
        if active_signal is not None:
            series_state = "active"
            series_pair = active_signal.get("pair")
        elif pending_signal is not None:
            series_state = "pending"
            series_pair = pending_signal.get("pair")
        elif signal_searching:
            series_state = "searching"
            series_pair = None
        else:
            series_state = "idle"
            series_pair = None
    return jsonify({
        "status": "ok" if connected else "error", "connected": connected,
        "demo": bool(SSID_META["isDemo"]), "pairs_count": len(OTC_PAIRS),
        "payouts_loaded": len(payout_cache), "min_payout": MIN_PAYOUT,
        "ai_available": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "latest_price_age_sec": round(time.time() - latest, 1) if latest else None,
        "market_pause_active": pause_remaining > 0,
        "market_pause_remaining_sec": pause_remaining,
        "final_result_delivery_mode": FINAL_RESULT_DELIVERY_MODE,
        "series_state": series_state,
        "series_pair": series_pair,
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
    if signal_is_in_progress():
        return jsonify({
            "error": "Сигнал уже активний, очікує входу або виконується пошук",
            "status": "in_progress",
        }), 409
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
    restore_signal_state()
    connect()
    if active_signal is None and pending_signal is None:
        start_startup_safety_pause(time.time())
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