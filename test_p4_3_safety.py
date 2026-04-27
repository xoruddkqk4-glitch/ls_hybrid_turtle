# test_p4_3_safety.py
# P4-3 무한루프·오류 안전성 확인
#
# 검증 항목:
#   [1] _market_minutes_elapsed — 역순 시각(end < start) → 0.0 (무한루프 없음)
#   [2] _market_minutes_elapsed — 같은 시각 → 0.0
#   [3] _market_minutes_elapsed — 주말 사이 간격은 장외 시간 제외
#   [4] _check_turtle_30min — breakout_since 형식 오류 → False (예외 없음)
#   [5] _check_turtle_30min — 종목 자체가 unheld_record에 없음 → False
#   [6] run_timer_check — unheld_record 비어있음 → [] (예외 없음)
#   [7] run_timer_check — 종목에 turtle_s1_signal 필드 없음 → 스킵 (예외 없음)
#   [8] run_timer_check — turtle_s1_signal=True인데 breakout_since=None → [] (예외 없음)

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


class TestMarketMinutesElapsed(unittest.TestCase):
    """_market_minutes_elapsed — 경계 조건 안전성"""

    def test_역순시각_0반환(self):
        """end < start 이면 0.0 반환 (루프 진입 없음)"""
        start = _kst(2026, 4, 27, 10, 30)
        end   = _kst(2026, 4, 27,  9, 50)
        self.assertEqual(timer_agent._market_minutes_elapsed(start, end), 0.0)

    def test_같은시각_0반환(self):
        """start == end 이면 0.0 반환"""
        t = _kst(2026, 4, 27, 10, 0)
        self.assertEqual(timer_agent._market_minutes_elapsed(t, t), 0.0)

    def test_주말_포함_간격_주말제외(self):
        """금요일 15:00 → 월요일 09:30 → 실제 장중 경과는 30분 + 30분 = 60분"""
        fri_1500 = _kst(2026, 4, 24, 15,  0)  # 금 15:00
        mon_0930 = _kst(2026, 4, 27,  9, 30)  # 월 09:30
        elapsed  = timer_agent._market_minutes_elapsed(fri_1500, mon_0930)
        # 금 15:00 ~ 15:30 = 30분 + 월 09:00 ~ 09:30 = 30분 = 60분
        self.assertAlmostEqual(elapsed, 60.0, places=1)

    def test_장외시간_제외(self):
        """09:00 이전 시작 → 09:00부터 카운트 시작"""
        start = _kst(2026, 4, 27,  8, 0)   # 장전
        end   = _kst(2026, 4, 27, 10, 0)   # 장중
        elapsed = timer_agent._market_minutes_elapsed(start, end)
        # 09:00~10:00 = 60분 (08:00~09:00 제외)
        self.assertAlmostEqual(elapsed, 60.0, places=1)


class TestCheckTurtle30minSafety(unittest.TestCase):
    """_check_turtle_30min — 오류 입력에 예외 없이 False 반환"""

    WL = {"005930": {"name": "삼성전자"}}
    FIXED = _kst(2026, 4, 27, 10, 30)

    def _run(self, unheld):
        with patch("timer_agent._now_kst", return_value=self.FIXED), \
             patch("timer_agent.get_watchlist", return_value=self.WL):
            return timer_agent._check_turtle_30min(
                "005930", "turtle_s1_breakout_since", unheld)

    def test_breakout_since_형식오류_False(self):
        """날짜 형식이 잘못된 문자열 → ValueError → False (예외 없음)"""
        unheld = {"005930": {"turtle_s1_breakout_since": "NOT-A-DATE"}}
        self.assertFalse(self._run(unheld))

    def test_breakout_since_None_False(self):
        """breakout_since 가 None → False (예외 없음)"""
        unheld = {"005930": {"turtle_s1_breakout_since": None}}
        self.assertFalse(self._run(unheld))

    def test_종목없음_False(self):
        """unheld_record에 종목 자체가 없음 → False (예외 없음)"""
        self.assertFalse(self._run({}))

    def test_breakout_since_숫자타입_False(self):
        """breakout_since 가 문자열이 아닌 숫자 → TypeError → False (예외 없음)"""
        unheld = {"005930": {"turtle_s1_breakout_since": 12345}}
        self.assertFalse(self._run(unheld))


class TestRunTimerCheckSafety(unittest.TestCase):
    """run_timer_check — 비정상 입력에서 빈 리스트 반환 (예외 없음)"""

    WL = {"005930": {"name": "삼성전자"}}
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
        unheld = {"005930": {}}   # 아무 필드도 없음
        self.assertEqual(self._run(unheld), [])

    def test_signal_True이지만_breakout_since_없음_빈리스트(self):
        """S1 신호 True이지만 breakout_since 없음 → 신호 없음 (예외 없음)"""
        unheld = {"005930": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": None,   # 시각 미기록
            "turtle_s2_breakout_since": None,
        }}
        self.assertEqual(self._run(unheld), [])

    def test_감시목록_외_종목_스킵(self):
        """watchlist에 없는 종목은 처리하지 않고 스킵 (예외 없음)"""
        unheld = {"999999": {   # WL에 없는 종목코드
            "turtle_s1_signal":         True,
            "turtle_s1_breakout_since": "2026-04-27 09:00:00",
        }}
        self.assertEqual(self._run(unheld), [])


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
        TestMarketMinutesElapsed,
        TestCheckTurtle30minSafety,
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
