# test_p4_3_safety.py
# P4-3 무한루프·오류 안전성 확인
#
# 검증 항목:
#   [1] _check_pullback_retest — entry_ready 필드 없는 종목 → False (예외 없음)
#   [2] _check_pullback_retest — 종목 자체가 unheld_record에 없음 → False
#   [3] _check_pullback_retest — entry_ready=None → False (예외 없음)
#   [4] run_timer_check — unheld_record 비어있음 → [] (예외 없음)
#   [5] run_timer_check — turtle_s1_signal 필드 없는 종목 → 스킵 (예외 없음)
#   [6] run_timer_check — signal=True이지만 entry_ready 없음 → [] (예외 없음)
#   [7] run_timer_check — 감시 목록 외 종목 → 스킵 (예외 없음)

import sys
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytz

for _mod in ["ls_client", "daily_chart_cache", "sector_cache",
             "programgarden_finance", "trade_ledger", "dotenv"]:
    sys.modules.setdefault(_mod, MagicMock())

import timer_agent

KST = pytz.timezone("Asia/Seoul")

def _kst(y, mo, d, h, mi):
    return KST.localize(datetime(y, mo, d, h, mi, 0))


# ══════════════════════════════════════════════════════════
class TestCheckPullbackRetestSafety(unittest.TestCase):
    """_check_pullback_retest — 오류 입력에 예외 없이 False 반환"""

    WL    = {"005930": {"name": "삼성전자"}}
    FIXED = _kst(2026, 4, 27, 10, 30)

    def _run(self, unheld):
        with patch("timer_agent._now_kst",      return_value=self.FIXED), \
             patch("timer_agent.get_watchlist", return_value=self.WL):
            return timer_agent._check_pullback_retest(
                "005930", "turtle_s1_entry_ready", unheld)

    def test_entry_ready_필드없음_False(self):
        """entry_ready 필드가 아예 없는 종목 → False (예외 없음)"""
        unheld = {"005930": {"turtle_s1_signal": True}}
        self.assertFalse(self._run(unheld))

    def test_entry_ready_None_False(self):
        """entry_ready=None → False (예외 없음)"""
        unheld = {"005930": {"turtle_s1_entry_ready": None}}
        self.assertFalse(self._run(unheld))

    def test_종목없음_False(self):
        """unheld_record에 종목 자체가 없음 → False (예외 없음)"""
        self.assertFalse(self._run({}))

    def test_entry_ready_False_False(self):
        """entry_ready=False → False (정상 케이스)"""
        unheld = {"005930": {"turtle_s1_entry_ready": False}}
        self.assertFalse(self._run(unheld))


# ══════════════════════════════════════════════════════════
class TestRunTimerCheckSafety(unittest.TestCase):
    """run_timer_check — 비정상 입력에서 빈 리스트 반환 (예외 없음)"""

    WL    = {"005930": {"name": "삼성전자"}}
    FIXED = _kst(2026, 4, 27, 10, 30)

    def _run(self, unheld):
        with patch("timer_agent.get_watchlist",      return_value=self.WL), \
             patch("timer_agent.load_unheld_record", return_value=unheld), \
             patch("timer_agent._now_kst",           return_value=self.FIXED):
            return timer_agent.run_timer_check()

    def test_unheld_record_비어있음_빈리스트(self):
        """unheld_record 가 {} → [] 반환 (예외 없음)"""
        self.assertEqual(self._run({}), [])

    def test_turtle_s1_signal_필드없음_스킵(self):
        """turtle_s1_signal 필드가 아예 없는 종목 → 스킵, 신호 없음 (예외 없음)"""
        unheld = {"005930": {}}
        self.assertEqual(self._run(unheld), [])

    def test_signal_True이지만_entry_ready_없음_빈리스트(self):
        """S1 신호 True이지만 entry_ready 필드 없음 → 신호 없음 (예외 없음)"""
        unheld = {"005930": {
            "turtle_s1_signal":      True,
            "turtle_s2_signal":      False,
            "turtle_s1_peak_price":  50_000,
            "turtle_s1_peak_locked": True,
            # entry_ready 필드 누락
        }}
        self.assertEqual(self._run(unheld), [])

    def test_감시목록_외_종목_스킵(self):
        """watchlist에 없는 종목은 처리하지 않고 스킵 (예외 없음)"""
        unheld = {"999999": {
            "turtle_s1_signal":      True,
            "turtle_s1_entry_ready": True,
        }}
        self.assertEqual(self._run(unheld), [])

    def test_signal_True_entry_ready_True_10시이전_빈리스트(self):
        """entry_ready=True이지만 09:50 → 신호 없음 (10시 이전)"""
        unheld = {"005930": {
            "turtle_s1_signal":      True,
            "turtle_s2_signal":      False,
            "turtle_s1_peak_price":  50_000,
            "turtle_s1_peak_locked": True,
            "turtle_s1_entry_ready": True,
            "turtle_s2_peak_price":  None,
            "turtle_s2_peak_locked": False,
            "turtle_s2_entry_ready": False,
        }}
        BEFORE_10 = _kst(2026, 4, 27, 9, 50)
        with patch("timer_agent.get_watchlist",      return_value=self.WL), \
             patch("timer_agent.load_unheld_record", return_value=unheld), \
             patch("timer_agent._now_kst",           return_value=BEFORE_10):
            result = timer_agent.run_timer_check()
        self.assertEqual(result, [], "10시 이전이면 신호 없음")


# ══════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("P4-3 안전성 테스트 시작")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    for cls in [
        TestCheckPullbackRetestSafety,
        TestRunTimerCheckSafety,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    if result.wasSuccessful():
        print("✅ 모든 테스트 통과!")
    else:
        print(f"❌ 실패: {len(result.failures)}건, 오류: {len(result.errors)}건")

    sys.exit(0 if result.wasSuccessful() else 1)
