import unittest
from copy import deepcopy
from unittest.mock import patch

from bot import radik_bot as bot


def signal(attempt: int = 0) -> dict:
    return {
        "pair": "EUR/USD",
        "raw_pair": "EURUSD_otc",
        "direction": "ВГОРУ",
        "entry_price": 1.2,
        "close_price": 1.1,
        "attempt": attempt,
        "result": "LOSS",
        "confidence": 90,
        "payout": 92,
        "reason": "test",
        "expires_at": 10,
    }


class OverlayAndPauseTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "active_signal": bot.active_signal,
            "pending_signal": bot.pending_signal,
            "pending_result_deliveries": list(bot.pending_result_deliveries),
            "result_delivery_next_retry_at": bot.result_delivery_next_retry_at,
            "result_delivery_last_error": bot.result_delivery_last_error,
            "signal_searching": bot.signal_searching,
            "last_signal_outcome": bot.last_signal_outcome,
            "next_signal_at": bot.next_signal_at,
            "signal_retry_delay": bot.SIGNAL_RETRY_DELAY_SEC,
            "session_initial_search_pending": bot.session_initial_search_pending,
            "suppress_next_search_notice": bot.suppress_next_search_notice,
            "pause_until": bot.market_pause_until,
            "pause_started_at": bot.market_pause_started_at,
            "pause_last_check": bot.market_pause_last_check,
            "pause_pair_index": bot.market_pause_pair_index,
            "pause_pairs": list(bot.market_pause_pairs),
            "pause_recovery_seen": bot.market_pause_recovery_seen,
            "in_signal_window": bot.in_signal_window,
            "schedule_signal_entry": bot.schedule_signal_entry,
            "send_signal": bot.send_signal_to_telegram,
            "send_result": bot.send_result_to_telegram,
            "record_result": bot.record_signal_result,
            "start_pause": bot.start_market_pause,
            "send_pause": bot.send_market_pause_to_telegram,
            "send_resume": bot.send_market_resume_to_telegram,
            "send_startup_pause": bot.send_startup_safety_pause_to_telegram,
            "session_started_notice_date": bot.session_started_notice_date,
            "send_overlay_preparation": bot.send_overlay_preparation_to_telegram,
            "send_overlay_cancelled": bot.send_overlay_cancelled_to_telegram,
            "market_snapshot": bot.market_snapshot,
            "persist_signal_state": bot.persist_signal_state,
            "telegram_request": bot.telegram_request,
            "last_telegram_message_at": bot.last_telegram_message_at,
            "last_telegram_message_sent_at": bot.last_telegram_message_sent_at,
            "telegram_chat_id": bot.TELEGRAM_CHAT_ID,
            "telegram_public_base_url": bot.TELEGRAM_PUBLIC_BASE_URL,
            "max_overlays": bot.MAX_OVERLAYS,
        }
        bot.active_signal = None
        bot.pending_signal = None
        bot.pending_result_deliveries = []
        bot.last_telegram_message_at = 0.0
        bot.last_telegram_message_sent_at = 0.0
        bot.result_delivery_next_retry_at = 0
        bot.result_delivery_last_error = None
        bot.signal_searching = False
        bot.last_signal_outcome = None
        bot.session_initial_search_pending = False
        bot.suppress_next_search_notice = False
        self.overlay_preparations = []
        bot.send_overlay_preparation_to_telegram = (
            lambda number, pair=None: (
                self.overlay_preparations.append((number, pair)) or True
            )
        )
        bot.send_overlay_cancelled_to_telegram = lambda: None
        bot.market_pause_until = 0
        bot.market_pause_started_at = 0
        bot.market_pause_last_check = 0
        bot.market_pause_pair_index = 0
        bot.market_pause_pairs = []
        bot.market_pause_recovery_seen = False
        bot.persist_signal_state = lambda: None
        bot.TELEGRAM_CHAT_ID = "test-chat"
        bot.TELEGRAM_PUBLIC_BASE_URL = "https://example.test"
        bot.MAX_OVERLAYS = len(bot.OVERLAY_STAKE_PERCENTS)
        bot.SIGNAL_RETRY_DELAY_SEC = 180

    def tearDown(self):
        bot.active_signal = self.saved["active_signal"]
        bot.pending_signal = self.saved["pending_signal"]
        bot.pending_result_deliveries = self.saved["pending_result_deliveries"]
        bot.result_delivery_next_retry_at = self.saved["result_delivery_next_retry_at"]
        bot.result_delivery_last_error = self.saved["result_delivery_last_error"]
        bot.signal_searching = self.saved["signal_searching"]
        bot.last_signal_outcome = self.saved["last_signal_outcome"]
        bot.next_signal_at = self.saved["next_signal_at"]
        bot.SIGNAL_RETRY_DELAY_SEC = self.saved["signal_retry_delay"]
        bot.session_initial_search_pending = self.saved["session_initial_search_pending"]
        bot.suppress_next_search_notice = self.saved["suppress_next_search_notice"]
        bot.market_pause_until = self.saved["pause_until"]
        bot.market_pause_started_at = self.saved["pause_started_at"]
        bot.market_pause_last_check = self.saved["pause_last_check"]
        bot.market_pause_pair_index = self.saved["pause_pair_index"]
        bot.market_pause_pairs = self.saved["pause_pairs"]
        bot.market_pause_recovery_seen = self.saved["pause_recovery_seen"]
        bot.in_signal_window = self.saved["in_signal_window"]
        bot.schedule_signal_entry = self.saved["schedule_signal_entry"]
        bot.send_signal_to_telegram = self.saved["send_signal"]
        bot.send_result_to_telegram = self.saved["send_result"]
        bot.record_signal_result = self.saved["record_result"]
        bot.start_market_pause = self.saved["start_pause"]
        bot.send_market_pause_to_telegram = self.saved["send_pause"]
        bot.send_market_resume_to_telegram = self.saved["send_resume"]
        bot.send_startup_safety_pause_to_telegram = self.saved["send_startup_pause"]
        bot.session_started_notice_date = self.saved["session_started_notice_date"]
        bot.send_overlay_preparation_to_telegram = self.saved["send_overlay_preparation"]
        bot.send_overlay_cancelled_to_telegram = self.saved["send_overlay_cancelled"]
        bot.market_snapshot = self.saved["market_snapshot"]
        bot.persist_signal_state = self.saved["persist_signal_state"]
        bot.telegram_request = self.saved["telegram_request"]
        bot.last_telegram_message_at = self.saved["last_telegram_message_at"]
        bot.last_telegram_message_sent_at = self.saved["last_telegram_message_sent_at"]
        bot.TELEGRAM_CHAT_ID = self.saved["telegram_chat_id"]
        bot.TELEGRAM_PUBLIC_BASE_URL = self.saved["telegram_public_base_url"]
        bot.MAX_OVERLAYS = self.saved["max_overlays"]

    def test_three_overlays_use_configured_stakes_then_fourth_loss_is_final(self):
        overlays, finals, recorded, pauses = [], [], [], []
        generated_candidates = [
            {
                **signal(0),
                "pair": "GBP/USD",
                "raw_pair": "GBPUSD_otc",
                "direction": "ВНИЗ",
                "entry_price": 1.3,
                "result": None,
            },
            {
                **signal(0),
                "pair": "USD/JPY",
                "raw_pair": "USDJPY_otc",
                "direction": "ВГОРУ",
                "entry_price": 150.0,
                "result": None,
            },
            {
                **signal(0),
                "pair": "AUD/CAD",
                "raw_pair": "AUDCAD_otc",
                "direction": "ВНИЗ",
                "entry_price": 0.91,
                "result": None,
            },
        ]
        excluded_on_recovery = []
        original_generator = bot._generate_best_signal
        original_search_notice = bot.send_search_to_telegram

        def generate_recovery(excluded_pairs=None):
            excluded_on_recovery.append(set(excluded_pairs or set()))
            return deepcopy(generated_candidates.pop(0)) if generated_candidates else None

        bot.in_signal_window = lambda: True
        bot.schedule_signal_entry = lambda value: {**value, "entry_at": 200, "expires_at": None}
        bot.send_signal_to_telegram = lambda value, *args, **kwargs: overlays.append(deepcopy(value))
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value, *args: recorded.append(
            {**deepcopy(value), "result": args[0] if args else value["result"]}
        )
        bot.start_market_pause = lambda now, value: pauses.append((now, deepcopy(value)))
        bot._generate_best_signal = generate_recovery
        bot.send_search_to_telegram = lambda: None

        def resolve_loss_with_pause(current, at):
            bot.active_signal, bot.pending_signal = current, None
            bot.handle_signal_outcome(current, "LOSS", at)
            self.assertIs(bot.active_signal, current)
            self.assertTrue(current["overlay_waiting"])
            delay = bot.OVERLAY_SIGNAL_DELAY_SEC
            self.assertEqual(bot.next_signal_at, at + delay)
            bot.handle_signal_outcome(current, "LOSS", at + delay - 1)
            self.assertIs(bot.active_signal, current)
            self.assertIsNone(bot.pending_signal)
            bot.handle_signal_outcome(current, "LOSS", at + delay)

        try:
            first = signal(0)
            resolve_loss_with_pause(first, 100)
            self.assertEqual(len(overlays), 1)
            self.assertEqual(overlays[0]["pair"], "GBP/USD")
            self.assertEqual(overlays[0]["direction"], "ВНИЗ")
            self.assertEqual(overlays[0]["attempt"], 1)
            self.assertEqual(overlays[0]["series_pairs"], ["EURUSD_otc", "GBPUSD_otc"])
            self.assertEqual([item["result"] for item in finals], ["LOSS"])
            self.assertEqual(self.overlay_preparations, [(1, "EUR/USD")])
            self.assertEqual(recorded, [])
            self.assertEqual(excluded_on_recovery, [{"EURUSD_otc"}])

            second = deepcopy(bot.pending_signal)
            second["result"] = "LOSS"
            second["close_price"] = 1.25
            resolve_loss_with_pause(second, 400)
            self.assertEqual(len(overlays), 2)
            self.assertEqual(overlays[1]["pair"], "USD/JPY")
            self.assertEqual(overlays[1]["direction"], "ВГОРУ")
            self.assertEqual(overlays[1]["attempt"], 2)
            self.assertEqual(excluded_on_recovery[1], {"EURUSD_otc", "GBPUSD_otc"})
            self.assertEqual([item["result"] for item in finals], ["LOSS", "LOSS"])
            self.assertEqual(
                self.overlay_preparations,
                [(1, "EUR/USD"), (2, "GBP/USD")],
            )
            self.assertEqual(recorded, [])

            third = deepcopy(bot.pending_signal)
            third["result"] = "LOSS"
            third["close_price"] = 1.24
            resolve_loss_with_pause(third, 700)
            self.assertEqual(len(overlays), 3)
            self.assertEqual(overlays[2]["pair"], "AUD/CAD")
            self.assertEqual(overlays[2]["attempt"], 3)
            self.assertEqual(
                self.overlay_preparations,
                [(1, "EUR/USD"), (2, "GBP/USD"), (3, "USD/JPY")],
            )
            self.assertEqual(len(finals), 3)
            self.assertEqual([item["result"] for item in finals], ["LOSS", "LOSS", "LOSS"])

            fourth = deepcopy(bot.pending_signal)
            fourth["result"] = "LOSS"
            bot.active_signal, bot.pending_signal = fourth, None
            bot.handle_signal_outcome(fourth, "LOSS", 400)
            self.assertEqual(len(finals), 4)
            self.assertEqual([item["result"] for item in finals], ["LOSS"] * 4)
            self.assertEqual([item["result"] for item in recorded], ["LOSS"])
            self.assertEqual(pauses, [])
            self.assertEqual(bot.market_pause_until, 0)
            self.assertIsNone(bot.active_signal)
        finally:
            bot._generate_best_signal = original_generator
            bot.send_search_to_telegram = original_search_notice

    def test_no_alternative_pair_keeps_series_blocked_for_another_search(self):
        finals, recorded = [], []
        original_search_notice = bot.send_search_to_telegram
        bot.in_signal_window = lambda: True
        bot._generate_best_signal = lambda excluded_pairs=None: None
        bot.send_search_to_telegram = lambda: None
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value, *args: recorded.append(
            {**deepcopy(value), "result": args[0] if args else value["result"]}
        )

        try:
            current = signal(0)
            bot.active_signal = current
            bot.handle_signal_outcome(current, "LOSS", 100)
            self.assertIs(current, bot.active_signal)
            self.assertTrue(current["overlay_waiting"])

            bot.handle_signal_outcome(current, "LOSS", 220)

            self.assertIsNone(bot.pending_signal)
            self.assertIs(current, bot.active_signal)
            self.assertTrue(current["overlay_waiting"])
            self.assertEqual(bot.next_signal_at, 220 + bot.SIGNAL_RETRY_DELAY_SEC)
            self.assertEqual(recorded, [])
            self.assertEqual(finals, [])

            bot.in_signal_window = lambda: False
            bot.handle_signal_outcome(
                current, "LOSS", current["overlay_due_at"]
            )

            self.assertIsNone(bot.active_signal)
            self.assertEqual([item["result"] for item in recorded], ["LOSS"])
            self.assertEqual([item["result"] for item in finals], ["LOSS"])
        finally:
            bot.send_search_to_telegram = original_search_notice

    def test_pair_rotation_skips_all_pairs_already_used_in_series(self):
        eligible = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc"]
        first, cursor = bot.select_rotating_pairs(
            eligible, 0, 1, {"EURUSD_otc"}
        )
        second, cursor = bot.select_rotating_pairs(
            eligible, cursor, 1, {"EURUSD_otc", "GBPUSD_otc"}
        )
        none, _ = bot.select_rotating_pairs(
            eligible, cursor, 1, set(eligible)
        )

        self.assertEqual(first, ["GBPUSD_otc"])
        self.assertEqual(second, ["USDJPY_otc"])
        self.assertEqual(none, [])

    def test_three_series_losses_count_as_one_daily_loss(self):
        previous_date, previous_stats = bot.daily_stats_date, bot.daily_stats
        try:
            bot.daily_stats_date = bot.result_day(100)
            bot.daily_stats = {"total": 0, "WIN": 0, "LOSS": 0, "DRAW": 0, "UNKNOWN": 0}
            completed_series = signal(2)
            bot.record_signal_result(completed_series, "LOSS", 100)
            bot.record_signal_result(completed_series, "LOSS", 100)

            self.assertEqual(bot.daily_stats["total"], 1)
            self.assertEqual(bot.daily_stats["LOSS"], 1)
        finally:
            bot.daily_stats_date, bot.daily_stats = previous_date, previous_stats

    def test_telegram_messages_drop_trailing_periods(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        with patch("bot.radik_bot.requests.post", return_value=Response()) as post:
            bot.telegram_request("/send-message", {"text": "Текст сообщения..."})

        self.assertEqual(post.call_args.kwargs["json"]["text"], "Текст сообщения")

    def test_telegram_messages_wait_for_the_two_minute_gap(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        bot.last_telegram_message_at = 50.0
        with (
            patch("bot.radik_bot.time.monotonic", return_value=100.0),
            patch("bot.radik_bot.time.sleep") as sleep,
            patch("bot.radik_bot.requests.post", return_value=Response()),
        ):
            bot.telegram_request("/send-message", {"text": "Следующее сообщение"})

        sleep.assert_called_once_with(bot.TELEGRAM_MIN_MESSAGE_INTERVAL_SEC - 50.0)

    def test_equal_close_price_is_counted_as_loss_not_unknown_or_draw(self):
        current = signal(0)
        with patch.object(bot, "close_price", return_value=current["entry_price"]):
            self.assertEqual(bot.evaluate_signal(current), "LOSS")
            self.assertEqual(current["result"], "LOSS")

    def test_pause_extends_for_flat_market(self):
        messages = []
        bot.send_market_pause_to_telegram = lambda minutes, extension=False: messages.append(
            (minutes, extension)
        )
        bot.market_snapshot = lambda pair: {
            "closes": [1.2] * 30,
            "trend": "up",
            "volatility": 0.0,
        }
        bot.start_market_pause(100, signal(2))
        original_end = bot.market_pause_until
        self.assertTrue(bot.monitor_market_pause(original_end + 1))
        self.assertGreater(bot.market_pause_until, original_end + 1)
        self.assertEqual(messages, [(bot.MARKET_PAUSE_SEC // 60, False), (bot.MARKET_PAUSE_SEC // 60, True)])

    def test_startup_safety_pause_blocks_search_after_a_restart(self):
        messages = []
        bot.send_startup_safety_pause_to_telegram = lambda minutes: messages.append(minutes)

        bot.start_startup_safety_pause(100)

        self.assertEqual(bot.market_pause_until, 100 + bot.STARTUP_SAFETY_PAUSE_SEC)
        self.assertEqual(messages, [bot.STARTUP_SAFETY_PAUSE_SEC // 60])
        self.assertEqual(len(bot.market_pause_pairs), bot.MARKET_MONITOR_PAIRS)

    def test_pause_resumes_only_after_minimum_duration_and_recovery(self):
        resumed = []
        bot.send_market_pause_to_telegram = lambda *args, **kwargs: None
        bot.send_market_resume_to_telegram = lambda: resumed.append(True)
        bot.market_snapshot = lambda pair: {
            "closes": [1.0] * 25 + [1.0, 1.01, 1.02, 1.03, 1.04],
            "trend": "up",
            "volatility": 0.01,
        }
        bot.start_market_pause(100, signal(2))
        self.assertTrue(bot.monitor_market_pause(200))
        self.assertGreater(bot.market_pause_until, 200)
        self.assertFalse(bot.monitor_market_pause(100 + bot.MARKET_PAUSE_SEC + 1))
        self.assertEqual(bot.market_pause_until, 0)
        self.assertEqual(resumed, [True])

    def test_flat_market_is_rechecked_at_pause_expiry(self):
        bot.send_market_pause_to_telegram = lambda *args, **kwargs: None
        bot.market_snapshot = lambda pair: {
            "closes": [1.2] * 30,
            "trend": "up",
            "volatility": 0.0,
        }
        bot.start_market_pause(100, signal(2))
        pause_end = 100 + bot.MARKET_PAUSE_SEC
        self.assertTrue(bot.monitor_market_pause(pause_end - 1))
        self.assertTrue(bot.monitor_market_pause(pause_end))
        self.assertGreater(bot.market_pause_until, pause_end)

    def test_pause_needs_recovery_from_multiple_pairs_at_expiry(self):
        bot.send_market_pause_to_telegram = lambda *args, **kwargs: None
        recovered = {
            "closes": [1.0] * 25 + [1.0, 1.01, 1.02, 1.03, 1.04],
            "trend": "up",
            "volatility": 0.01,
        }
        flat = {"closes": [1.2] * 30, "trend": "up", "volatility": 0.0}
        bot.market_snapshot = lambda pair: recovered if pair == "EURUSD_otc" else flat
        bot.start_market_pause(100, signal(2))
        pause_end = 100 + bot.MARKET_PAUSE_SEC

        self.assertTrue(bot.monitor_market_pause(pause_end + 1))
        self.assertGreater(bot.market_pause_until, pause_end + 1)

    def test_early_recovery_does_not_resume_if_expiry_check_is_flat(self):
        bot.send_market_pause_to_telegram = lambda *args, **kwargs: None
        recovered = {
            "closes": [1.0] * 25 + [1.0, 1.01, 1.02, 1.03, 1.04],
            "trend": "up",
            "volatility": 0.01,
        }
        flat = {"closes": [1.2] * 30, "trend": "up", "volatility": 0.0}
        calls = []

        def snapshot(_pair):
            calls.append(True)
            return recovered if len(calls) == 1 else flat

        bot.market_snapshot = snapshot
        bot.start_market_pause(100, signal(2))
        pause_end = 100 + bot.MARKET_PAUSE_SEC
        self.assertTrue(bot.monitor_market_pause(pause_end - 1))
        self.assertTrue(bot.monitor_market_pause(pause_end))
        self.assertGreater(bot.market_pause_until, pause_end)

    def test_overlay_outside_working_window_keeps_result_without_stop_notice(self):
        finals, recorded = [], []
        bot.in_signal_window = lambda: False
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value, *args: recorded.append(
            {**deepcopy(value), "result": args[0] if args else value["result"]}
        )
        current = signal(0)
        bot.active_signal = current

        bot.handle_signal_outcome(current, "LOSS", 100)

        self.assertEqual([item["result"] for item in finals], ["LOSS"])
        self.assertEqual([item["result"] for item in recorded], ["LOSS"])
        self.assertIsNone(bot.active_signal)
        self.assertFalse(hasattr(bot, "send_incomplete_series_to_telegram"))

    def test_known_loss_is_not_lost_when_later_overlay_is_unknown(self):
        recorded, finals = [], []
        original_generator = bot._generate_best_signal
        bot.in_signal_window = lambda: True
        bot.schedule_signal_entry = lambda value: {**value, "entry_at": 200, "expires_at": None}
        bot.send_signal_to_telegram = lambda *args, **kwargs: None
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value, *args: recorded.append(
            {**deepcopy(value), "result": args[0] if args else value["result"]}
        )
        bot._generate_best_signal = lambda excluded_pairs=None: {
            **signal(0),
            "pair": "GBP/USD",
            "raw_pair": "GBPUSD_otc",
            "direction": "ВНИЗ",
            "result": None,
        }

        try:
            first = signal(0)
            bot.active_signal = first
            bot.handle_signal_outcome(first, "LOSS", 100)
            bot.handle_signal_outcome(first, "LOSS", 280)

            later = signal(1)
            later["result"] = "UNKNOWN"
            bot.active_signal, bot.pending_signal = later, None
            bot.handle_signal_outcome(later, "UNKNOWN", 300)

            self.assertEqual(recorded, [])
            self.assertEqual([item["result"] for item in finals], ["LOSS"])
            self.assertIs(bot.active_signal, later)
            self.assertEqual(later["expires_at"], 305)
        finally:
            bot._generate_best_signal = original_generator

    def test_unentered_signal_gets_terminal_unknown_before_new_search(self):
        finals, recorded = [], []
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value, *args: recorded.append(
            {**deepcopy(value), "result": args[0] if args else value["result"]}
        )
        current = signal(0)
        bot.pending_signal = current

        bot.finalize_pending_signal(current, 100, "ціна входу недоступна")

        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["result"], "NOT_ENTERED")
        self.assertEqual(recorded, [])
        self.assertIsNone(bot.pending_signal)

    def test_failed_terminal_message_is_queued_without_blocking_new_search(self):
        current = signal(2)
        current["result"] = "WIN"
        sent, recorded = [], []
        bot.record_signal_result = lambda value, *args: recorded.append(
            {**deepcopy(value), "result": args[0] if args else value["result"]}
        )

        def unavailable(_value):
            raise ConnectionError("telegram unavailable")

        bot.send_result_to_telegram = unavailable
        bot.active_signal = current
        bot.handle_signal_outcome(current, "WIN", 100)

        self.assertEqual(len(recorded), 1)
        self.assertIsNone(bot.active_signal)
        self.assertTrue(current["result_delivery_pending"])
        self.assertEqual(len(bot.pending_result_deliveries), 1)
        self.assertFalse(bot.signal_is_in_progress())

        bot.send_result_to_telegram = lambda value: sent.append(deepcopy(value))
        bot.retry_result_deliveries(160)
        self.assertEqual([item["result"] for item in sent], ["WIN"])
        self.assertEqual(bot.pending_result_deliveries, [])
        self.assertIsNone(bot.result_delivery_last_error)

    def test_result_is_text_only_and_in_russian(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 123,
        }
        current = signal(0)
        current["result"] = "WIN"

        bot.send_result_to_telegram(current)

        self.assertEqual([request[0] for request in requests], ["/send-message"])
        self.assertEqual(requests[0][1]["text"], "EUR/USD OTC\n+++")

    def test_result_never_sends_a_photo(self):
        requests = []

        def request(path, payload):
            requests.append((path, payload))
            return {"ok": True, "message_id": 124}

        bot.telegram_request = request
        current = signal(0)
        current["result"] = "WIN"

        bot.send_result_to_telegram(current)

        self.assertEqual([request[0] for request in requests], ["/send-message"])
        self.assertEqual(requests[0][1]["text"], "EUR/USD OTC\n+++")

    def test_signal_is_sent_as_one_plain_line_with_two_minute_expiry(self):
        requests = []
        original_duration = bot.SIGNAL_DURATION_SEC

        def request(path, payload):
            requests.append((path, payload))
            return {"ok": True, "message_id": 125}

        try:
            bot.SIGNAL_DURATION_SEC = 120
            bot.telegram_request = request
            current = signal(0)
            current["pair"] = "AUD/CAD"
            current["direction"] = "ВНИЗ"
            current["entry_at"] = 1_800_000_040
            with patch("bot.radik_bot.time.time", return_value=1_800_000_000):
                bot.send_signal_to_telegram(current)

            self.assertEqual([item[0] for item in requests], ["/send-message"])
            self.assertEqual(
                requests[0][1]["text"],
                "AUD/CAD OTC\nНа 2 минуты вниз\nЗахожу с новой свечи через 40 сек.",
            )
        finally:
            bot.SIGNAL_DURATION_SEC = original_duration

    def test_signal_delivery_delay_preserves_displayed_wait(self):
        current = signal(0)
        current["entry_at"] = 1_040
        clock = [1_000]
        requests = []

        def request(path, payload):
            requests.append((path, payload))
            if path == "/send-message":
                clock[0] = 1_045
                return {"ok": True, "message_id": 777}
            return {"ok": True, "message_id": 777}

        bot.telegram_request = request
        with patch("bot.radik_bot.time.time", side_effect=lambda: clock[0]):
            bot.send_signal_to_telegram(current)

        self.assertEqual([path for path, _ in requests], ["/send-message"])
        self.assertIn("через 40 сек.", requests[0][1]["text"])
        self.assertEqual(current["entry_at"], 1_085)

    def test_overlay_signal_identifies_attempt_and_stake(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 130,
        }
        current = signal(1)

        bot.send_signal_to_telegram(current)

        self.assertEqual(
            requests[0][1]["text"],
            "EUR/USD OTC\n"
            "Перекрытие 1/3 • ставка 2%\n"
            "На 2 минуты вверх\n"
            "Захожу с новой свечи.",
        )

        third_overlay = signal(3)
        bot.send_signal_to_telegram(third_overlay)
        self.assertIn("Перекрытие 3/3 • ставка 8%", requests[1][1]["text"])

    def test_entry_countdown_rounds_up_and_never_goes_negative(self):
        with patch("bot.radik_bot.time.time", return_value=100.0):
            self.assertEqual(bot.seconds_until_entry(134.2), 35)
            self.assertEqual(bot.seconds_until_entry(100.0), 0)
            self.assertEqual(bot.seconds_until_entry(99.0), 0)

    def test_search_notice_is_simple(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 126,
        }

        bot.send_search_to_telegram()

        self.assertEqual([path for path, _ in requests], ["/send-message"])
        self.assertEqual(
            requests[0][1]["text"],
            "Меняю пару, ищу следующую ситуацию.",
        )

    def test_win_search_notice_stays_simple(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 127,
        }
        bot.last_signal_outcome = "WIN"

        bot.send_search_to_telegram()

        self.assertEqual(
            requests[0][1]["text"],
            "Меняю пару, ищу следующую ситуацию.",
        )
        self.assertIsNone(bot.last_signal_outcome)

    def test_search_notice_waits_two_minutes_before_generating_signal(self):
        class StopAfterOneTick:
            def __init__(self):
                self.ticks = 0

            def wait(self, timeout):
                self.ticks += 1
                return self.ticks > 1

        notices = []
        generated = []
        with (
            patch.object(bot, "signal_stop", StopAfterOneTick()),
            patch.object(bot, "connected", True),
            patch.object(bot, "active_signal", None),
            patch.object(bot, "pending_signal", None),
            patch.object(bot, "next_signal_at", 0),
            patch.object(bot, "search_started_at", 0),
            patch.object(bot, "session_initial_search_pending", False),
            patch.object(bot, "suppress_next_search_notice", False),
            patch.object(bot, "market_pause_until", 0),
            patch.object(bot, "retry_result_deliveries"),
            patch.object(bot, "maybe_send_session_started_notice"),
            patch.object(bot, "maybe_send_daily_summary"),
            patch.object(bot, "in_signal_window", return_value=True),
            patch.object(bot, "signal_is_in_progress", return_value=False),
            patch.object(bot, "send_search_to_telegram", side_effect=lambda: notices.append(True)),
            patch.object(bot, "generate_best_signal", side_effect=lambda: generated.append(True)),
            patch("bot.radik_bot.time.time", return_value=100),
        ):
            bot.signal_loop()

            self.assertEqual(notices, [True])
            self.assertEqual(generated, [])
            self.assertEqual(bot.next_signal_at, 100 + bot.SESSION_PAIR_DELAY_SEC)

    def test_session_start_greeting_is_sent_once_per_day(self):
        requests = []
        bot.in_signal_window = lambda: True
        bot.session_started_notice_date = None
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True,
        }

        bot.maybe_send_session_started_notice(100)
        bot.maybe_send_session_started_notice(101)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], "/send-message")
        self.assertEqual(
            requests[0][1]["text"],
            "Всем привет! Начинаю сессию. Ищу валютную пару, подбираю хорошую ситуацию.",
        )
        self.assertEqual(bot.next_signal_at, 100 + bot.SESSION_PAIR_DELAY_SEC)
        self.assertTrue(bot.session_initial_search_pending)

    def test_entry_confirmation_is_only_the_word_entered(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 128,
        }

        bot.send_entry_confirmation(signal(0))

        self.assertEqual(requests, [("/send-message", {"text": "Зашел"})])

    def test_entry_confirmation_waits_until_scheduled_entry_time(self):
        clock = [100.0]
        delivered_at = []
        bot.telegram_request = lambda path, payload: (
            delivered_at.append(clock[0]) or {"ok": True, "message_id": 133}
        )

        def advance(seconds):
            clock[0] += seconds

        with (
            patch("bot.radik_bot.time.time", side_effect=lambda: clock[0]),
            patch("bot.radik_bot.time.sleep", side_effect=advance) as sleep,
        ):
            bot.send_entry_confirmation({**signal(0), "entry_at": 131.5})

        self.assertGreaterEqual(delivered_at[0], 131.5)
        self.assertGreaterEqual(sleep.call_count, 1)

    def test_entry_confirmation_bypasses_the_two_minute_cooldown(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "message_id": 132}

        bot.last_telegram_message_at = bot.time.monotonic()
        with (
            patch("bot.radik_bot.time.sleep") as sleep,
            patch("bot.radik_bot.requests.post", return_value=Response()) as post,
        ):
            bot.send_entry_confirmation(signal(0))

        sleep.assert_not_called()
        self.assertEqual(post.call_args.kwargs["json"], {"text": "Зашел"})
        self.assertGreaterEqual(
            bot.time.monotonic() - bot.last_telegram_message_at, 0
        )

    def test_overlay_entry_confirmation_names_the_overlay(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 131,
        }

        bot.send_entry_confirmation(signal(1))
        bot.send_entry_confirmation(signal(2))
        bot.send_entry_confirmation(signal(3))

        self.assertEqual(
            [payload["text"] for _, payload in requests],
            [
                "Зашел на перекрытие 1/3 • ставка 2%",
                "Зашел на перекрытие 2/3 • ставка 4%",
                "Зашел на перекрытие 3/3 • ставка 8%",
            ],
        )

    def test_each_overlay_preparation_names_its_stake_and_number(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True, "message_id": 129,
        }

        self.saved["send_overlay_preparation"](1, "EUR/USD")
        self.saved["send_overlay_preparation"](2, "GBP/USD")
        self.saved["send_overlay_preparation"](3, "USD/JPY")

        texts = [payload["text"] for _, payload in requests]
        self.assertEqual(
            texts,
            [
                "Минус. Меняю пару, ищу следующую ситуацию, готовлю первое "
                "перекрытие, буду заходить на 2%.",
                "Минус. Меняю пару, ищу следующую ситуацию, готовлю второе "
                "перекрытие, буду заходить на 4%.",
                "Минус. Меняю пару, ищу следующую ситуацию, готовлю третье "
                "перекрытие, буду заходить на 8%.",
            ],
        )
        self.assertIn("2%", texts[0])
        self.assertIn("4%", texts[1])
        self.assertIn("8%", texts[2])

    def test_overlay_is_cancelled_if_preparation_notice_cannot_be_sent(self):
        recorded, delivered = [], []

        def deliver_result(value, now):
            delivered.append((value, now))
            bot.active_signal = None
            bot.pending_signal = None
            bot.next_signal_at = now + bot.SIGNAL_RETRY_DELAY_SEC
            return True

        current = signal(0)
        bot.active_signal = current

        with (
            patch.object(bot, "in_signal_window", return_value=True),
            patch.object(
                bot,
                "send_overlay_preparation_to_telegram",
                side_effect=ConnectionError("Telegram unavailable"),
            ),
            patch.object(
                bot,
                "record_signal_result",
                side_effect=lambda value, outcome, *args: recorded.append(
                    (value, outcome)
                ),
            ),
            patch.object(bot, "deliver_terminal_result", side_effect=deliver_result),
        ):
            bot.handle_signal_outcome(current, "LOSS", 100)

        self.assertEqual(recorded, [(current, "LOSS")])
        self.assertEqual(delivered, [(current, 100)])
        self.assertFalse(current.get("overlay_waiting"))
        self.assertIsNone(bot.active_signal)
        self.assertIsNone(bot.pending_signal)
        self.assertEqual(bot.next_signal_at, 100 + bot.SIGNAL_RETRY_DELAY_SEC)

    def test_terminal_loss_does_not_create_market_pause(self):
        finals, pauses = [], []
        bot.in_signal_window = lambda: True
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value, *args: None
        bot.start_market_pause = lambda now, value: pauses.append((now, deepcopy(value)))

        current = signal(3)
        current["result"] = "LOSS"
        bot.active_signal = current
        bot.handle_signal_outcome(current, "LOSS", 100)

        self.assertEqual([item["result"] for item in finals], ["LOSS"])
        self.assertEqual(pauses, [])
        self.assertEqual(bot.market_pause_until, 0)
        self.assertEqual(bot.next_signal_at, 100 + bot.SIGNAL_RETRY_DELAY_SEC)

    def test_winning_overlay_announces_return_to_one_percent(self):
        requests = []
        bot.telegram_request = lambda path, payload: requests.append((path, payload)) or {
            "ok": True,
        }
        bot.record_signal_result = lambda value, *args: None
        bot.deliver_terminal_result = lambda value, now: True
        current = signal(1)
        current["result"] = "WIN"
        bot.active_signal = current

        bot.handle_signal_outcome(current, "WIN", 100)

        self.assertEqual(
            [payload["text"] for _, payload in requests],
            ["Меняю пару, ищу следующую ситуацию, возвращаюсь на 1%."],
        )
        self.assertTrue(bot.suppress_next_search_notice)

    def test_search_is_blocked_while_a_series_or_search_is_active(self):
        calls = []
        original_generate = bot._generate_best_signal
        bot._generate_best_signal = lambda: calls.append(True) or signal()
        try:
            for state in (
                {"active_signal": signal(0), "pending_signal": None, "signal_searching": False},
                {"active_signal": None, "pending_signal": signal(1), "signal_searching": False},
                {"active_signal": None, "pending_signal": None, "signal_searching": True},
            ):
                bot.active_signal = state["active_signal"]
                bot.pending_signal = state["pending_signal"]
                bot.signal_searching = state["signal_searching"]
                self.assertTrue(bot.signal_is_in_progress())
                self.assertIsNone(bot.generate_best_signal())
        finally:
            bot._generate_best_signal = original_generate
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()