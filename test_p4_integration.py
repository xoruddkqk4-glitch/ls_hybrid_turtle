# test_p4_integration.py
# P4-1 통합 테스트
#
# 확인 항목:
#   [1] 경로 A: 목표가 돌파 → 30분 유지 → TARGET_30MIN 신호
#   [2] 경로 B: 20일 신고가 돌파 → 즉시 TURTLE_S1 신호 (30분 가드 없음)
#   [3] 경로 B: 55일 신고가 돌파 → 즉시 TURTLE_S2 신호
#   [4] 중복 방지: 같은 종목이 두 경로에 해당할 때 우선순위 높은 것만 포함
#   [5] 09:05 확정 시 unheld_record에 목표가·기준가 즉시 기록
#   [6] 보유 종목 held_record에 pending_target·reference_price 필드 기록
#   [7] 감시 목록에서 빠진 보유 종목도 손절 감시 정상 작동
#
# 실행: python test_p4_integration.py

import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytz

# 한국 표준시
KST = pytz.timezone("Asia/Seoul")


# ══════════════════════════════════════════════════════════
# [헬퍼] 테스트용 데이터 만들기
# ══════════════════════════════════════════════════════════

def _kst_str(minutes_ago: int) -> str:
    """현재 시각에서 N분 전의 KST 시각 문자열을 반환한다."""
    dt = datetime.now(KST) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _make_candles(highs: list) -> list:
    """고가 목록으로 OHLCV 캔들 리스트를 만든다 (indicator_calc 테스트용)."""
    return [{"open": h, "high": h, "low": h, "close": h, "volume": 0}
            for h in highs]


# ══════════════════════════════════════════════════════════
# 테스트 1-A: indicator_calc.calc_n_day_high — N일 신고가 계산
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
# 테스트 1-B: timer_agent.check_30min_passed — 30분 경과 판단
# ══════════════════════════════════════════════════════════

class TestCheck30minPassed(unittest.TestCase):
    """timer_agent.check_30min_passed — 30분 가드 로직이 올바른지 확인"""

    def setUp(self):
        import timer_agent
        self.fn = timer_agent.check_30min_passed
        self.wl_patch = patch("timer_agent.get_watchlist",
                              return_value={"005930": {"name": "삼성전자"}})

    def test_35분_경과_True(self):
        unheld = {"005930": {"above_target_since": _kst_str(35)}}
        with self.wl_patch:
            self.assertTrue(self.fn("005930", unheld), "35분 경과 → True")

    def test_10분_경과_False(self):
        unheld = {"005930": {"above_target_since": _kst_str(10)}}
        with self.wl_patch:
            self.assertFalse(self.fn("005930", unheld), "10분 경과 → False")

    def test_타이머_미시작_False(self):
        # above_target_since = null: 한 번도 목표가를 넘지 않은 상태
        unheld = {"005930": {"above_target_since": None}}
        with self.wl_patch:
            self.assertFalse(self.fn("005930", unheld), "타이머 미시작 → False")


# ══════════════════════════════════════════════════════════
# 테스트 2: timer_agent.run_timer_check — 두 진입 경로 통합
# ══════════════════════════════════════════════════════════

class TestRunTimerCheck(unittest.TestCase):
    """timer_agent.run_timer_check — 진입 신호 생성 및 우선순위 확인"""

    # 테스트용 감시 목록
    WL = {
        "005930": {"name": "삼성전자"},
        "035420": {"name": "NAVER"},
        "000660": {"name": "SK하이닉스"},
    }

    def _run(self, unheld: dict) -> list:
        """감시 목록·미보유 상태를 모조(mock) 데이터로 교체해서 run_timer_check 실행."""
        with patch("timer_agent.get_watchlist", return_value=self.WL), \
             patch("timer_agent.load_unheld_record", return_value=unheld):
            import timer_agent
            return timer_agent.run_timer_check()

    # ── 경로 A ────────────────────────────────────────────

    def test_경로A_TARGET_30MIN(self):
        """목표가 위 30분 유지 → TARGET_30MIN 신호"""
        unheld = {"005930": {
            "pending_target":    70000,
            "reference_price":   68000,
            "above_target_since": _kst_str(35),  # 35분 경과
            "turtle_s1_signal":  False,
            "turtle_s2_signal":  False,
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "005930")
        self.assertEqual(result[0]["entry_source"], "TARGET_30MIN")

    def test_경로A_30분_미달_신호_없음(self):
        """10분 경과에 그치면 신호 없음"""
        unheld = {"005930": {
            "pending_target":    70000,
            "reference_price":   68000,
            "above_target_since": _kst_str(10),  # 10분만 경과
            "turtle_s1_signal":  False,
            "turtle_s2_signal":  False,
        }}
        result = self._run(unheld)
        self.assertEqual(result, [], "30분 미달 → 신호 없음")

    # ── 경로 B ────────────────────────────────────────────

    def test_경로B_TURTLE_S1_30분_가드_없음(self):
        """20일 신고가 돌파 → 타이머 없이 즉시 TURTLE_S1 신호"""
        unheld = {"035420": {
            "pending_target":    200000,
            "reference_price":   195000,
            "above_target_since": None,    # 타이머 미시작이어도 OK
            "turtle_s1_signal":  True,     # 20일 신고가 돌파
            "turtle_s2_signal":  False,
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "035420")
        self.assertEqual(result[0]["entry_source"], "TURTLE_S1")

    def test_경로B_TURTLE_S2(self):
        """55일 신고가 돌파 → 즉시 TURTLE_S2 신호"""
        unheld = {"000660": {
            "pending_target":    150000,
            "reference_price":   148000,
            "above_target_since": None,
            "turtle_s1_signal":  False,
            "turtle_s2_signal":  True,    # 55일 신고가 돌파
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["code"], "000660")
        self.assertEqual(result[0]["entry_source"], "TURTLE_S2")

    # ── 중복 방지 ──────────────────────────────────────────

    def test_중복_S2가_30MIN보다_우선(self):
        """S2 + 30분 동시 해당 → TURTLE_S2 하나만"""
        unheld = {"005930": {
            "pending_target":    70000,
            "reference_price":   68000,
            "above_target_since": _kst_str(35),  # 30분 조건도 충족
            "turtle_s1_signal":  False,
            "turtle_s2_signal":  True,            # S2도 충족
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1, "중복 방지: 한 종목 한 번만")
        self.assertEqual(result[0]["entry_source"], "TURTLE_S2", "S2가 30MIN보다 우선")

    def test_중복_S2가_S1보다_우선(self):
        """S1 + S2 동시 돌파 → TURTLE_S2 하나만"""
        unheld = {"005930": {
            "pending_target":    70000,
            "reference_price":   68000,
            "above_target_since": None,
            "turtle_s1_signal":  True,
            "turtle_s2_signal":  True,
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_source"], "TURTLE_S2")

    def test_중복_S1이_30MIN보다_우선(self):
        """S1 + 30분 동시 해당 → TURTLE_S1 하나만"""
        unheld = {"005930": {
            "pending_target":    70000,
            "reference_price":   68000,
            "above_target_since": _kst_str(35),
            "turtle_s1_signal":  True,
            "turtle_s2_signal":  False,
        }}
        result = self._run(unheld)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_source"], "TURTLE_S1")


# ══════════════════════════════════════════════════════════
# 테스트 3: target_manager.initialize_unheld_record
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

        # 필수 필드 존재 확인
        for field in ("pending_target", "reference_price",
                      "turtle_s1_signal", "turtle_s2_signal",
                      "above_target_since"):
            self.assertIn(field, rec, f"'{field}' 필드가 있어야 함")

        # 초기값 확인
        self.assertEqual(rec["pending_target"], int(70000 * 1.02),
                         "초기 목표가 = 현재가 × 1.02")
        self.assertEqual(rec["reference_price"], 70000,
                         "기준가 = 현재가")
        self.assertFalse(rec["turtle_s1_signal"], "S1 신호 초기값 = False")
        self.assertFalse(rec["turtle_s2_signal"], "S2 신호 초기값 = False")
        self.assertIsNone(rec["above_target_since"], "타이머 초기값 = None")

    def test_기존_종목_타이머_보존(self):
        """이미 unheld_record에 있는 종목은 덮어쓰지 않음"""
        import target_manager

        watchlist = {"005930": {"name": "삼성전자"}}
        existing = {"005930": {
            "pending_target":    75000,
            "reference_price":   73000,
            "above_target_since": "2026-04-15 09:15:00",  # 기존 타이머
            "turtle_s1_signal":  True,
            "turtle_s2_signal":  False,
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


# ══════════════════════════════════════════════════════════
# 테스트 4: target_manager.update_held_stock_targets
#           보유 종목 pending_target·reference_price 기록
# ══════════════════════════════════════════════════════════

class TestUpdateHeldStockTargets(unittest.TestCase):
    """held_stock_record에 목표가·기준가 필드가 초기화되는지 확인"""

    def test_필드_없으면_현재가_기준_초기화(self):
        import target_manager

        # 매수 직후: pending_target / reference_price 필드 없음
        held = {"005930": {
            "current_unit":    1,
            "last_buy_price":  70000,
            "avg_buy_price":   70000,
            "stop_loss_price": 66000,
            # pending_target, reference_price 없음!
        }}
        saved = {}

        with patch("target_manager._load_held_record",
                   return_value=held), \
             patch("target_manager._save_held_record",
                   side_effect=lambda d: saved.update(d)), \
             patch("target_manager.ls_client.get_multi_price",
                   return_value={"005930": 72000}), \
             patch("target_manager.get_watchlist",
                   return_value={"005930": {"name": "삼성전자"}}):
            target_manager.update_held_stock_targets()

        self.assertIn("005930", saved, "005930이 저장되어야 함")
        rec = saved["005930"]
        self.assertIn("pending_target", rec,
                      "pending_target 필드가 저장되어야 함")
        self.assertIn("reference_price", rec,
                      "reference_price 필드가 저장되어야 함")
        # 초기값: 현재가(72000) × 1.02
        self.assertEqual(rec["pending_target"], int(72000 * 1.02))
        self.assertEqual(rec["reference_price"], 72000)

    def test_가격_상승_시_목표가_고정(self):
        """현재가 ≥ 기준가면 목표가를 올리지 않음 (잠금 로직)"""
        import target_manager

        held = {"005930": {
            "current_unit":    1,
            "last_buy_price":  70000,
            "avg_buy_price":   70000,
            "stop_loss_price": 66000,
            "pending_target":  71400,   # 기존 목표가
            "reference_price": 70000,  # 기준가
        }}
        saved = {}

        with patch("target_manager._load_held_record",
                   return_value=held), \
             patch("target_manager._save_held_record",
                   side_effect=lambda d: saved.update(d)), \
             patch("target_manager.ls_client.get_multi_price",
                   return_value={"005930": 75000}), \
             patch("target_manager.get_watchlist",
                   return_value={"005930": {"name": "삼성전자"}}):
            target_manager.update_held_stock_targets()

        # 가격 상승 → 목표가 변경 없음 → save 호출 안 됨
        # saved가 비어있으면 변경 없음 (save_held_record 미호출)
        if "005930" in saved:
            self.assertEqual(saved["005930"]["pending_target"], 71400,
                             "가격 상승 시 목표가가 올라가면 안 됨")


# ══════════════════════════════════════════════════════════
# 테스트 5: risk_guardian.run_guardian
#           감시 목록에서 빠진 보유 종목도 손절 감시
# ══════════════════════════════════════════════════════════

class TestRiskGuardianHeldOutsideWatchlist(unittest.TestCase):
    """감시 목록 밖 보유 종목의 손절 감시 여부 확인"""

    def test_목록_외_종목_하드_손절_실행(self):
        """watchlist에 없지만 held_stock_record에 있는 종목 → 손절 주문 실행"""
        import risk_guardian

        # 감시 목록에 TEST01 없음 (오늘 배치에서 제외됨)
        mock_wl = {"OTHER": {"name": "다른종목"}}

        # 잔고에 TEST01 보유, 현재가가 손절가 아래
        mock_balance = [
            {"code": "TEST01", "current_price": "49000", "sellable_qty": 10}
        ]

        # held_stock_record에는 TEST01 존재
        mock_pos = {"TEST01": {
            "current_unit":       1,
            "last_buy_price":     55000,
            "avg_buy_price":      55000,
            "stop_loss_price":    50000,  # 현재가 49000 < 손절가 50000
            "next_pyramid_price": 57000,
            "entry_type":         "NORMAL",
            "max_unit":           4,
            "total_qty":          10,
            "entry_source":       "TURTLE_S1",
        }}

        order_log = []

        with patch("risk_guardian.get_watchlist", return_value=mock_wl), \
             patch("risk_guardian.ls_client.get_balance",
                   return_value=mock_balance), \
             patch("risk_guardian.load_position_state",
                   return_value=mock_pos), \
             patch("risk_guardian.ls_client.place_order",
                   side_effect=lambda c, q, s, t: order_log.append(
                       {"code": c, "qty": q, "side": s}
                   ) or {"success": True, "order_no": "T001"}), \
             patch("risk_guardian.save_position_state"), \
             patch("risk_guardian.trade_ledger.append_trade"), \
             patch("risk_guardian.telegram_alert.SendMessage"):
            risk_guardian.run_guardian()

        self.assertEqual(len(order_log), 1,
                         "손절 매도 주문이 1번 실행되어야 함")
        self.assertEqual(order_log[0]["code"], "TEST01")
        self.assertEqual(order_log[0]["side"], "SELL")
        self.assertEqual(order_log[0]["qty"], 10)

    def test_목록_외_보유_기록_없으면_주문_안_함(self):
        """watchlist에도 없고 held_stock_record에도 없는 종목 → 매도 안 함"""
        import risk_guardian

        mock_wl = {}
        mock_balance = [
            {"code": "MANUAL01", "current_price": "10000", "sellable_qty": 5}
        ]
        # held_stock_record에도 없음 — 완전 수동 보유 종목
        mock_pos = {}

        order_log = []

        with patch("risk_guardian.get_watchlist", return_value=mock_wl), \
             patch("risk_guardian.ls_client.get_balance",
                   return_value=mock_balance), \
             patch("risk_guardian.load_position_state",
                   return_value=mock_pos), \
             patch("risk_guardian.ls_client.place_order",
                   side_effect=lambda *a, **k: order_log.append(True)
                   or {"success": True, "order_no": "SHOULD_NOT_CALL"}):
            risk_guardian.run_guardian()

        self.assertEqual(len(order_log), 0,
                         "수동 보유 종목(기록 없음)은 주문하면 안 됨")


# ══════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("P4-1 통합 테스트 시작")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    # 테스트 클래스 순서대로 추가
    for cls in [
        TestNDayHigh,
        TestCheck30minPassed,
        TestRunTimerCheck,
        TestInitializeUnheldRecord,
        TestUpdateHeldStockTargets,
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
