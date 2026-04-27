# test_p4_unit_risk_factor.py
# P4-1 단위 테스트 — calc_unit_size() 리스크팩터 및 매수금 상한 로직
#
# 확인 항목:
#   [1] 매수금이 상한(총자본 × 10%) 이내 → effective_risk_factor = 0.02 유지
#   [2] 매수금이 상한 초과 → effective_risk_factor 줄어서 매수금 상한에 맞춤
#   [3] 예외 진입 (1주 가격 > 총자본 × 2%) → (1, "EXCEPTION", 2, None)
#   [4] ATR = 0 → None 반환
#   [5] qty = 0 (ATR이 매우 큼) → None 반환
#   [6] 반환 튜플 구조 — 4개 항목 확인
#
# 실행: python test_p4_unit_risk_factor.py

import sys
import unittest
from unittest.mock import patch, MagicMock

# API 연결이 필요한 모듈을 Mock으로 대체한다
for _mod in ["ls_client", "daily_chart_cache", "sector_cache",
             "programgarden_finance", "trade_ledger", "dotenv",
             "indicator_calc", "telegram_alert"]:
    sys.modules.setdefault(_mod, MagicMock())

import turtle_order_logic
from turtle_order_logic import calc_unit_size, MAX_UNIT_PURCHASE_RATIO


# 테스트 전용 감시 종목 (get_watchlist Mock)
MOCK_WATCHLIST = {"005930": {"name": "삼성전자"}}


class TestCalcUnitSize(unittest.TestCase):

    def setUp(self):
        # get_watchlist를 Mock으로 고정
        self.patch_wl = patch(
            "turtle_order_logic.get_watchlist",
            return_value=MOCK_WATCHLIST
        )
        self.patch_wl.start()

    def tearDown(self):
        self.patch_wl.stop()

    # ──────────────────────────────────────────
    # [1] 매수금이 상한 이내 → effective_risk_factor = 0.02
    # ──────────────────────────────────────────
    def test_상한이내_리스크팩터_0_02_유지(self):
        """매수금이 총자본 × 10% 이하이면 effective_risk_factor = 0.02 그대로"""
        total_capital = 10_000_000  # 1천만원
        price         = 10_000      # 1만원 (총자본의 0.1% → 정상 경로)
        atr_n         = 2_000       # ATR 2천원
        # qty = int(1천만 × 0.02 / 2천) = 100주
        # 매수금 = 100 × 1만 = 100만원
        # 상한 = 1천만 × 0.1 = 100만원  ← 딱 맞음 (초과 아님)
        result = calc_unit_size("005930", price, atr_n, total_capital)
        self.assertIsNotNone(result)
        qty, entry_type, max_unit, effective_rf = result
        self.assertEqual(entry_type, "NORMAL")
        self.assertEqual(effective_rf, 0.02)
        self.assertEqual(qty * price, 100 * price)  # 100주

    # ──────────────────────────────────────────
    # [2] 매수금이 상한 초과 → 리스크팩터 줄어서 매수금 상한에 맞춤
    # ──────────────────────────────────────────
    def test_상한초과_리스크팩터_감소(self):
        """매수금이 총자본 × 10% 초과 시 effective_risk_factor를 낮추고 매수금을 상한에 맞춤"""
        total_capital = 100_000_000  # 1억원
        price         = 10_000       # 1만원 (총자본의 0.01%)
        atr_n         = 200          # ATR 200원
        # qty_raw = int(1억 × 0.02 / 200) = 10,000주
        # 매수금_raw = 10,000 × 1만 = 1억원
        # 상한 = 1억 × 0.1 = 1천만원 ← 초과!
        # effective_rf = (1천만 × 200) / (1억 × 1만) = 0.002
        # qty = int(1억 × 0.002 / 200) = 1,000주
        # 매수금 = 1,000 × 1만 = 1천만원
        result = calc_unit_size("005930", price, atr_n, total_capital)
        self.assertIsNotNone(result)
        qty, entry_type, max_unit, effective_rf = result
        self.assertEqual(entry_type, "NORMAL")
        self.assertAlmostEqual(effective_rf, 0.002, places=6)
        self.assertEqual(qty, 1000)
        self.assertEqual(qty * price, 10_000_000)  # 정확히 상한 = 1천만원

    def test_상한초과_매수금이_상한_이하임을_확인(self):
        """리스크팩터 조정 후 매수금이 반드시 상한(총자본 × 10%) 이하"""
        total_capital = 50_000_000  # 5천만원
        price         = 5_000       # 5천원
        atr_n         = 100         # ATR 100원
        max_unit_amount = total_capital * MAX_UNIT_PURCHASE_RATIO  # 5백만원
        result = calc_unit_size("005930", price, atr_n, total_capital)
        self.assertIsNotNone(result)
        qty, _, _, effective_rf = result
        purchase = qty * price
        self.assertLessEqual(purchase, max_unit_amount + price)  # 1주 오차 허용

    # ──────────────────────────────────────────
    # [3] 예외 진입 (1주 가격 > 총자본 2%)
    # ──────────────────────────────────────────
    def test_예외진입_1주가격이_총자본_2퍼센트_초과(self):
        """1주 가격이 총자본의 2% 초과이면 EXCEPTION 진입, effective_risk_factor = None"""
        total_capital = 10_000_000   # 1천만원
        price         = 250_000      # 25만원 (총자본의 2.5%)
        atr_n         = 5_000
        result = calc_unit_size("005930", price, atr_n, total_capital)
        self.assertIsNotNone(result)
        qty, entry_type, max_unit, effective_rf = result
        self.assertEqual(qty, 1)
        self.assertEqual(entry_type, "EXCEPTION")
        self.assertEqual(max_unit, 2)
        self.assertIsNone(effective_rf)

    def test_예외진입_비활성화시_None반환(self):
        """LS_ALLOW_EXCEPTION_ENTRY=False이면 예외 진입 불가 → None"""
        with patch.dict("os.environ", {"LS_ALLOW_EXCEPTION_ENTRY": "False"}):
            result = calc_unit_size("005930", 250_000, 5_000, 10_000_000)
        self.assertIsNone(result)

    # ──────────────────────────────────────────
    # [4] ATR = 0 → None
    # ──────────────────────────────────────────
    def test_atr_0_이면_None반환(self):
        """ATR이 0이면 수량 계산 불가 → None"""
        result = calc_unit_size("005930", 10_000, 0, 10_000_000)
        self.assertIsNone(result)

    # ──────────────────────────────────────────
    # [5] qty = 0 (ATR이 매우 클 때) → None
    # ──────────────────────────────────────────
    def test_qty_0이면_None반환(self):
        """ATR이 총자본의 2%를 초과하면 qty=0 → None"""
        total_capital = 1_000_000   # 100만원
        atr_n         = 1_000_000   # ATR 100만원 (극단값)
        price         = 5_000       # 0.5% (정상 경로)
        result = calc_unit_size("005930", price, atr_n, total_capital)
        self.assertIsNone(result)

    # ──────────────────────────────────────────
    # [6] 반환 튜플 구조 — 4개 항목
    # ──────────────────────────────────────────
    def test_반환값_4개_항목(self):
        """반환값이 (qty, entry_type, max_unit, effective_risk_factor) 4개임을 확인"""
        result = calc_unit_size("005930", 10_000, 2_000, 100_000_000)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 4)
        qty, entry_type, max_unit, effective_rf = result
        self.assertIsInstance(qty, int)
        self.assertIn(entry_type, ("NORMAL", "EXCEPTION"))
        self.assertIsInstance(max_unit, int)
        # effective_rf는 float이거나 None
        if effective_rf is not None:
            self.assertIsInstance(effective_rf, float)

    def test_NORMAL진입_max_unit_3(self):
        """일반 진입의 max_unit은 3"""
        result = calc_unit_size("005930", 10_000, 2_000, 10_000_000)
        self.assertIsNotNone(result)
        _, entry_type, max_unit, _ = result
        self.assertEqual(entry_type, "NORMAL")
        self.assertEqual(max_unit, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
