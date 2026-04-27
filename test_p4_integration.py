# test_p4_integration.py
# P4-1 통합 테스트
#
# 확인 항목:
#   [1] indicator_calc.calc_n_day_high — N일 신고가 계산
#   [2] timer_agent._check_turtle_30min — 터틀 30분 가드 (10시 필터 포함)
#   [3] timer_agent.run_timer_check — 터틀 AND 30분 가드 진입 신호 통합
#   [4] 09:05 확정 시 unheld_record에 목표가·기준가 즉시 기록
#   [5] 보유 종목 held_record에 pending_target·reference_price 필드 기록
#   [6] 감시 목록에서 빠진 보유 종목도 손절 감시 정상 작동
#
# 실행: python test_p4_integration.py

import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# ──────────────────────────────────────────────────────────
# API 연결이 필요한 모듈을 Mock으로 미리 등록한다.
# timer_agent → target_manager → daily_chart_cache → ls_client 순서로
# 임포트가 연쇄되는데, ls_client가 .env / API 키를 요구하므로
# 테스트 환경에서는 빈 Mock으로 대체해서 임포트 오류를 막는다.
# ──────────────────────────────────────────────────────────
for _mod in ["ls_client", "daily_chart_cache", "sector_cache",
             "programgarden_finance", "trade_ledger", "dotenv"]:
    sys.modules.setdefault(_mod, MagicMock())

import pytz

# 한국 표준시
KST = pytz.timezone("Asia/Seoul")

# ──────────────────────────────────────────────────────────
# 테스트용 고정 시각 상수
# (날짜는 임의, 중요한 건 시·분만)
# ──────────────────────────────────────────────────────────
_DATE = (2026, 4, 27)
FIXED_1030 = KST.localize(datetime(*_DATE, 10, 30, 0))   # 10:30 — 10시 이후
FIXED_0950 = KST.localize(datetime(*_DATE,  9, 50, 0))   # 09:50 — 10시 이전
FIXED_1000 = KST.localize(datetime(*_DATE, 10,  0, 0))   # 10:00 — 정각
FIXED_1010 = KST.localize(datetime(*_DATE, 10, 10, 0))   # 10:10


def _since_str(base_dt: datetime, minutes_ago: int) -> str:
    """base_dt 기준 N분 전의 KST 시각 문자열을 반환한다."""
    return (base_dt - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _make_candles(highs: list) -> list:
    """고가 목록으로 OHLCV 캔들 리스트를 만든다 (indicator_calc 테스트용)."""
    return [{"open": h, "high": h, "low": h, "close": h, "volume": 0}
            for h in highs]


# ══════════════════════════════════════════════════════════
# 테스트 1: indicator_calc.calc_n_day_high — N일 신고가 계산
# ══════════════════════════════════════════════════════════

class TestNDayHigh(unittest.TestCase):
    """indicator_calc.calc_n_day_high — N일 고가 계산이 올바른지 확인"""

    def setUp(self):
        import indicator_calc
        self.fn = indicator_calc.calc_n_day_high

    def test_20일_정상(self):
        # 직전 20일 고가: 1~20, 오늘 고가: 999 (제외되어야 함)
        candles = _make_candles(list(range(1, 21)) + [999])  # 21개
        self.assertEqual(self.fn(candles, n=20), 20.0,
                         "오늘 캔들을 제외한 직전 20일 최고가 = 20")

    def test_55일_정상(self):
        # 직전 55일 고가: 1~55, 오늘: 9999
        candles = _make_candles(list(range(1, 56)) + [9999])  # 56개
        self.assertEqual(self.fn(candles, n=55), 55.0)

    def test_데이터_부족_0_반환(self):
        # 10개짜리 — 20일 계산에 필요한 21개 미만
        candles = _make_candles([100] * 10)
        self.assertEqual(self.fn(candles, n=20), 0.0,
                         "데이터 부족 시 0.0 반환")

    def test_딱_최소_개수(self):
        # n+1 = 21개 딱 맞으면 정상 처리
        candles = _make_candles([50] * 20 + [999])
        self.assertEqual(self.fn(candles, n=20), 50.0)


# ══════════════════════════════════════════════════════════
# 테스트 2: timer_agent._check_turtle_30min
#           터틀 30분 가드 + 10시 필터
# ══════════════════════════════════════════════════════════

class TestCheckTurtle30min(unittest.TestCase):
    """_check_turtle_30min — 30분 경과 AND 10시 이후 조건 확인"""

    WL = {"005930": {"name": "삼성전자"}}

    def _run(self, fixed_now: datetime, since_str) -> bool:
        """_now_kst를 고정 시각으로 대체하고 _check_turtle_30min 실행."""
        unheld = {"005930": {"turtle_s1_breakout_since": since_str}}
        with patch("timer_agent._now_kst", return_value=fixed_now), \
             patch("timer_agent.get_watchlist", return_value=self.WL):
            import timer_agent
            return timer_agent._check_turtle_30min(
                "005930", "turtle_s1_breakout_since", unheld)

    def test_35분경과_10시이후_True(self):
        """현재 10:30, 돌파 09:55 (35분 경과) → True"""
        since = _since_str(FIXED_1030, 35)   # 09:55
        self.assertTrue(self._run(FIXED_1030, since))

    def test_10분경과_10시이후_False(self):
        """현재 10:30, 돌파 10:20 (10분 경과) → False (30분 미달)"""
        since = _since_str(FIXED_1030, 10)   # 10:20
        self.assertFalse(self._run(FIXED_1030, since))

    def test_35분경과_10시이전_False(self):
        """현재 09:50, 돌파 09:15 (35분 경과) → False (10시 전 진입 불가)"""
        since = _since_str(FIXED_0950, 35)   # 09:15
        self.assertFalse(self._run(FIXED_0950, since))

    def test_breakout_since_없음_False(self):
        """돌파 시각 미기록 → False"""
        self.assertFalse(self._run(FIXED_1030, None))

    def test_9시40분돌파_10시에_20분만_경과_False(self):
        """9:40 돌파, 현재 10:00 → 20분 경과 → False"""
        since = "2026-04-27 09:40:00"
        self.assertFalse(self._run(FIXED_1000, since))

    def test_9시40분돌파_10시10분에_30분_경과_True(self):
        """9:40 돌파, 현재 10:10 → 30분 경과 → True"""
        since = "2026-04-27 09:40:00"
        self.assertTrue(self._run(FIXED_1010, since))

    def test_9시20분돌파_10시에_40분_경과_True(self):
        """9:20 돌파, 현재 10:00 → 40분 경과 AND 10시 → True"""
        since = "2026-04-27 09:20:00"
        self.assertTrue(self._run(FIXED_1000, since))


# ══════════════════════════════════════════════════════════
# 테스트 3: timer_agent.run_timer_check
#           터틀 AND 30분 가드 진입 신호 통합
# ══════════════════════════════════════════════════════════

class TestRunTimerCheck(unittest.TestCase):
    """run_timer_check — 진입 신호 생성 및 우선순위 확인"""

    WL = {
        "005930": {"name": "삼성전자"},
        "035420": {"name": "NAVER"},
        "000660": {"name": "SK하이닉스"},
    }

    def _run(self, unheld: dict, fixed_now: datetime = FIXED_1030) -> list:
        """감시 목록·미보유 상태·현재 시각을 고정해서 run_timer_check 실행."""
        with patch("timer_agent.get_watchlist", return_value=self.WL), \
             patch("timer_agent.load_unheld_record", return_value=unheld), \
             patch("timer_agent._now_kst", return_value=fixed_now):
            import timer_agent
            return timer_agent.run_timer_check()

    # ── S1 신호 ────────────────────────────────────────────

    def test_S1_30분가드_통과_신호발생(self):
        """S1 신호 + breakout_since 35분 전 + 10:30 → TURTLE_S1 신호"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": _since_str(FIXED_1030, 35),
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "035420")
        self.assertEqual(result[0]["entry_source"], "TURTLE_S1")

    def test_S1_30분_미달_신호없음(self):
        """S1 신호 + 10분만 경과 → 신호 없음"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": _since_str(FIXED_1030, 10),
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld)
        self.assertEqual(result, [], "30분 미달 → 신호 없음")

    def test_S1_10시이전_진입불가(self):
        """S1 신호 + 35분 경과 + 09:50 → 신호 없음 (10시 이전)"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": _since_str(FIXED_0950, 35),
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld, fixed_now=FIXED_0950)
        self.assertEqual(result, [], "10시 이전 → 신호 없음")

    def test_S1_breakout_since_없으면_신호없음(self):
        """S1 신호 True이지만 breakout_since 없음 → 신호 없음"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": None,   # 시각 미기록
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld)
        self.assertEqual(result, [], "breakout_since 없음 → 신호 없음")

    # ── S2 신호 ────────────────────────────────────────────

    def test_S2_30분가드_통과_신호발생(self):
        """S2 신호 + breakout_since 35분 전 + 10:30 → TURTLE_S2 신호"""
        unheld = {"000660": {
            "turtle_s1_signal":         False,
            "turtle_s2_signal":         True,
            "turtle_s1_breakout_since": None,
            "turtle_s2_breakout_since": _since_str(FIXED_1030, 35),
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "000660")
        self.assertEqual(result[0]["entry_source"], "TURTLE_S2")

    # ── 우선순위 (S2 > S1) ─────────────────────────────────

    def test_중복_S2가_S1보다_우선(self):
        """S1 + S2 동시 돌파 + 둘 다 30분 가드 통과 → TURTLE_S2 하나만"""
        unheld = {"005930": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         True,
            "turtle_s1_breakout_since": _since_str(FIXED_1030, 35),
            "turtle_s2_breakout_since": _since_str(FIXED_1030, 35),
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_source"], "TURTLE_S2")

    # ── 9시대 돌파 → 시간 누적 반영 ─────────────────────────

    def test_9시40분_돌파_10시에_20분경과_신호없음(self):
        """9:40 돌파, 현재 10:00 → 20분만 경과 → 신호 없음"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": "2026-04-27 09:40:00",
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld, fixed_now=FIXED_1000)
        self.assertEqual(result, [], "9:40 돌파 후 10:00엔 아직 20분 → 신호 없음")

    def test_9시40분_돌파_10시10분에_30분경과_신호발생(self):
        """9:40 돌파, 현재 10:10 → 30분 경과 + 10시 이후 → TURTLE_S1 신호"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": "2026-04-27 09:40:00",
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld, fixed_now=FIXED_1010)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_source"], "TURTLE_S1")

    def test_9시20분_돌파_10시에_40분경과_즉시신호(self):
        """9:20 돌파, 현재 10:00 → 40분 경과 + 10시 → 즉시 TURTLE_S1 신호"""
        unheld = {"035420": {
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": "2026-04-27 09:20:00",
            "turtle_s2_breakout_since": None,
        }}
        result = self._run(unheld, fixed_now=FIXED_1000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_source"], "TURTLE_S1")


# ══════════════════════════════════════════════════════════
# 테스트 4: target_manager.initialize_unheld_record
#           09:05 종목 확정 시 목표가·기준가 즉시 저장
# ══════════════════════════════════════════════════════════

class TestInitializeUnheldRecord(unittest.TestCase):
    """09:05 배치에서 신규 종목 목표가·기준가가 즉시 저장되는지 확인"""

    def test_신규_종목_필드_저장(self):
        import target_manager

        watchlist = {"005930": {"name": "삼성전자", "score": 0.9}}
        saved = {}

        with patch("target_manager.load_unheld_record", return_value={}), \
             patch("target_manager.save_unheld_record",
                   side_effect=lambda d: saved.update(d)), \
             patch("target_manager.ls_client.get_multi_price",
                   return_value={"005930": 70000}):
            target_manager.initialize_unheld_record(watchlist)

        self.assertIn("005930", saved, "005930이 저장되어야 함")
        rec = saved["005930"]

        # 필수 필드 존재 확인 (새 필드 포함)
        for field in ("pending_target", "reference_price",
                      "turtle_s1_signal", "turtle_s2_signal",
                      "above_target_since",
                      "turtle_s1_breakout_since", "turtle_s2_breakout_since"):
            self.assertIn(field, rec, f"'{field}' 필드가 있어야 함")

        # 초기값 확인
        self.assertEqual(rec["pending_target"], int(70000 * 1.02),
                         "초기 목표가 = 현재가 × 1.02")
        self.assertEqual(rec["reference_price"], 70000, "기준가 = 현재가")
        self.assertFalse(rec["turtle_s1_signal"],  "S1 신호 초기값 = False")
        self.assertFalse(rec["turtle_s2_signal"],  "S2 신호 초기값 = False")
        self.assertIsNone(rec["above_target_since"],        "타이머 초기값 = None")
        self.assertIsNone(rec["turtle_s1_breakout_since"],  "S1 돌파 시각 초기값 = None")
        self.assertIsNone(rec["turtle_s2_breakout_since"],  "S2 돌파 시각 초기값 = None")

    def test_기존_종목_타이머_보존(self):
        """이미 unheld_record에 있는 종목은 덮어쓰지 않음"""
        import target_manager

        watchlist = {"005930": {"name": "삼성전자"}}
        existing = {"005930": {
            "pending_target":           75000,
            "reference_price":          73000,
            "above_target_since":       "2026-04-15 09:15:00",
            "turtle_s1_signal":         True,
            "turtle_s2_signal":         False,
            "turtle_s1_breakout_since": "2026-04-15 09:15:00",
            "turtle_s2_breakout_since": None,
        }}
        saved = {}

        with patch("target_manager.load_unheld_record", return_value=existing), \
             patch("target_manager.save_unheld_record",
                   side_effect=lambda d: saved.update(d)), \
             patch("target_manager.ls_client.get_multi_price", return_value={}):
            target_manager.initialize_unheld_record(watchlist)

        rec = saved.get("005930", existing["005930"])
        self.assertEqual(rec["above_target_since"], "2026-04-15 09:15:00",
                         "기존 타이머가 그대로 보존되어야 함")
        self.assertEqual(rec["pending_target"], 75000,
                         "기존 목표가가 그대로 보존되어야 함")
        self.assertEqual(rec["turtle_s1_breakout_since"], "2026-04-15 09:15:00",
                         "기존 S1 돌파 시각이 보존되어야 함")


# ══════════════════════════════════════════════════════════
# 테스트 6: risk_guardian.run_guardian
#           감시 목록에서 빠진 보유 종목도 손절 감시
# ══════════════════════════════════════════════════════════

class TestRiskGuardianHeldOutsideWatchlist(unittest.TestCase):
    """감시 목록 밖 보유 종목의 손절 감시 여부 확인"""

    def test_목록_외_종목_하드_손절_실행(self):
        """watchlist에 없지만 held_stock_record에 있는 종목 → 손절 주문 실행"""
        import risk_guardian

        mock_wl      = {"OTHER": {"name": "다른종목"}}
        mock_balance = [
            {"code": "TEST01", "current_price": "49000", "sellable_qty": 10}
        ]
        mock_pos = {"TEST01": {
            "current_unit":       1,
            "last_buy_price":     55000,
            "avg_buy_price":      55000,
            "stop_loss_price":    50000,   # 현재가 49000 < 손절가 50000
            "next_pyramid_price": 57000,
            "entry_type":         "NORMAL",
            "max_unit":           4,
            "total_qty":          10,
            "entry_source":       "TURTLE_S1",
        }}
        order_log = []

        with patch("risk_guardian.get_watchlist",   return_value=mock_wl), \
             patch("risk_guardian.ls_client.get_balance", return_value=mock_balance), \
             patch("risk_guardian.load_position_state",   return_value=mock_pos), \
             patch("risk_guardian.ls_client.place_order",
                   side_effect=lambda c, q, s, t: order_log.append(
                       {"code": c, "qty": q, "side": s}
                   ) or {"success": True, "order_no": "T001"}), \
             patch("risk_guardian.save_position_state"), \
             patch("risk_guardian.trade_ledger.append_trade"), \
             patch("risk_guardian.telegram_alert.SendMessage"):
            risk_guardian.run_guardian()

        self.assertEqual(len(order_log), 1, "손절 매도 주문이 1번 실행되어야 함")
        self.assertEqual(order_log[0]["code"], "TEST01")
        self.assertEqual(order_log[0]["side"], "SELL")
        self.assertEqual(order_log[0]["qty"],  10)

    def test_목록_외_보유_기록_없으면_주문_안_함(self):
        """watchlist에도 없고 held_stock_record에도 없는 종목 → 매도 안 함"""
        import risk_guardian

        mock_wl      = {}
        mock_balance = [
            {"code": "MANUAL01", "current_price": "10000", "sellable_qty": 5}
        ]
        mock_pos  = {}
        order_log = []

        with patch("risk_guardian.get_watchlist",   return_value=mock_wl), \
             patch("risk_guardian.ls_client.get_balance", return_value=mock_balance), \
             patch("risk_guardian.load_position_state",   return_value=mock_pos), \
             patch("risk_guardian.ls_client.place_order",
                   side_effect=lambda *a, **k: order_log.append(True)
                   or {"success": True, "order_no": "SHOULD_NOT_CALL"}):
            risk_guardian.run_guardian()

        self.assertEqual(len(order_log), 0, "수동 보유 종목(기록 없음)은 주문하면 안 됨")


# ══════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("P4-1 통합 테스트 시작")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    for cls in [
        TestNDayHigh,
        TestCheckTurtle30min,
        TestRunTimerCheck,
        TestInitializeUnheldRecord,
        TestRiskGuardianHeldOutsideWatchlist,
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
