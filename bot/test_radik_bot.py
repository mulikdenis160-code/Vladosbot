import unittest
from copy import deepcopy

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
            "signal_searching": bot.signal_searching,
            "next_signal_at": bot.next_signal_at,
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
            "send_incomplete": bot.send_incomplete_series_to_telegram,
            "market_snapshot": bot.market_snapshot,
        }
        bot.active_signal = None
        bot.pending_signal = None
        bot.signal_searching = False
        bot.market_pause_until = 0
        bot.market_pause_started_at = 0
        bot.market_pause_last_check = 0
        bot.market_pause_pair_index = 0
        bot.market_pause_pairs = []
        bot.market_pause_recovery_seen = False

    def tearDown(self):
        bot.active_signal = self.saved["active_signal"]
        bot.pending_signal = self.saved["pending_signal"]
        bot.signal_searching = self.saved["signal_searching"]
        bot.next_signal_at = self.saved["next_signal_at"]
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
        bot.send_incomplete_series_to_telegram = self.saved["send_incomplete"]
        bot.market_snapshot = self.saved["market_snapshot"]

    def test_two_losses_become_overlays_then_third_is_final_loss(self):
        overlays, finals, recorded, pauses = [], [], [], []
        bot.in_signal_window = lambda: True
        bot.schedule_signal_entry = lambda value: {**value, "entry_at": 200, "expires_at": None}
        bot.send_signal_to_telegram = lambda value, prefix, image: overlays.append(
            (deepcopy(value), prefix, image)
        )
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value: recorded.append(deepcopy(value))
        bot.start_market_pause = lambda now, value: pauses.append((now, deepcopy(value)))

        first = signal(0)
        bot.active_signal = first
        bot.handle_signal_outcome(first, "LOSS", 100)
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0][1], "🔁 ПЕРЕКРИТТЯ 1/2\n")
        self.assertEqual(overlays[0][2], "overlay")
        self.assertEqual(len(finals), 0)
        self.assertEqual(len(recorded), 0)
        self.assertEqual(bot.pending_signal["attempt"], 1)

        second = signal(1)
        bot.active_signal, bot.pending_signal = second, None
        bot.handle_signal_outcome(second, "LOSS", 200)
        self.assertEqual(len(overlays), 2)
        self.assertEqual(overlays[1][1], "🔁 ПЕРЕКРИТТЯ 2/2\n")
        self.assertEqual(len(finals), 0)
        self.assertEqual(len(recorded), 0)
        self.assertEqual(bot.pending_signal["attempt"], 2)

        third = signal(2)
        bot.active_signal, bot.pending_signal = third, None
        bot.handle_signal_outcome(third, "LOSS", 300)
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["result"], "LOSS")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(len(pauses), 1)
        self.assertIsNone(bot.active_signal)

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

    def test_early_recovery_does_not_resume_if_expiry_check_is_flat(self):
        bot.send_market_pause_to_telegram = lambda *args, **kwargs: None
        recovered = {
            "closes": [1.0] * 25 + [1.0, 1.01, 1.02, 1.03, 1.04],
            "trend": "up",
            "volatility": 0.01,
        }
        flat = {"closes": [1.2] * 30, "trend": "up", "volatility": 0.0}
        snapshots = [recovered, flat]
        bot.market_snapshot = lambda pair: snapshots.pop(0)
        bot.start_market_pause(100, signal(2))
        pause_end = 100 + bot.MARKET_PAUSE_SEC
        self.assertTrue(bot.monitor_market_pause(pause_end - 1))
        self.assertTrue(bot.monitor_market_pause(pause_end))
        self.assertGreater(bot.market_pause_until, pause_end)

    def test_incomplete_overlay_outside_working_window_is_not_a_result(self):
        finals, recorded, incomplete = [], [], []
        bot.in_signal_window = lambda: False
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value: recorded.append(deepcopy(value))
        bot.send_incomplete_series_to_telegram = lambda value: incomplete.append(deepcopy(value))
        current = signal(0)
        bot.active_signal = current

        bot.handle_signal_outcome(current, "LOSS", 100)

        self.assertEqual(finals, [])
        self.assertEqual(recorded, [])
        self.assertEqual(len(incomplete), 1)
        self.assertIsNone(bot.active_signal)

    def test_unentered_signal_gets_terminal_unknown_before_new_search(self):
        finals, recorded = [], []
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value: recorded.append(deepcopy(value))
        current = signal(0)
        bot.pending_signal = current

        bot.finalize_pending_signal(current, 100, "ціна входу недоступна")

        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["result"], "UNKNOWN")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["result"], "UNKNOWN")
        self.assertIsNone(bot.pending_signal)

    def test_failed_terminal_message_keeps_signal_locked_until_delivery(self):
        current = signal(2)
        current["result"] = "WIN"
        sent, recorded = [], []
        bot.record_signal_result = lambda value: recorded.append(deepcopy(value))

        def unavailable(_value):
            raise ConnectionError("telegram unavailable")

        bot.send_result_to_telegram = unavailable
        bot.active_signal = current
        bot.handle_signal_outcome(current, "WIN", 100)

        self.assertEqual(len(recorded), 1)
        self.assertIs(bot.active_signal, current)
        self.assertTrue(current["result_delivery_pending"])
        self.assertTrue(bot.signal_is_in_progress())

        bot.send_result_to_telegram = lambda value: sent.append(deepcopy(value))
        self.assertTrue(bot.deliver_terminal_result(current, 105))
        self.assertEqual([item["result"] for item in sent], ["WIN"])
        self.assertIsNone(bot.active_signal)

    def test_non_loss_results_do_not_create_pause(self):
        finals, pauses = [], []
        bot.send_result_to_telegram = lambda value: finals.append(deepcopy(value))
        bot.record_signal_result = lambda value: None
        bot.start_market_pause = lambda now, value: pauses.append((now, deepcopy(value)))

        for outcome in ("WIN", "DRAW", "UNKNOWN"):
            current = signal(2)
            current["result"] = outcome
            bot.active_signal = current
            bot.handle_signal_outcome(current, outcome, 100)

        self.assertEqual([item["result"] for item in finals], ["WIN", "DRAW", "UNKNOWN"])
        self.assertEqual(pauses, [])

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