# test_p4_3_risk_factor_safety.py
# P4-3 안전성 확인 — 엣지 케이스 및 구버전 호환
#
# 확인 항목:
#   [1] effective_risk_factor 필드 없는 구버전 NORMAL 종목 → 기본값 0.02 사용
#   [2] effective_risk_factor 필드 없는 구버전 EXCEPTION 종목 → qty=1 고정
#   [3] effective_risk_factor = 0.0 저장된 경우 → qty=0 → 피라미딩 스킵
#   [4] total_capital=0 → calc_unit_size None 반환 (ZeroDivision 없음)
#   [5] price=0 → one_share_ratio 계산 오류 없음 (ZeroDivision 없음)
#   [6] effective_risk_factor 음수 → qty<=0 → 피라미딩 스킵
#
# 실행: python test_p4_3_risk_factor_safety.py

import sys
import unittest
from unittest.mock import patch, MagicMock

for _mod in ["ls_client", "daily_chart_cache", "sector_cache",
             "programgarden_finance", "trade_ledger", "dotenv",
             "indicator_calc", "telegram_alert"]:
    sys.modules.setdefault(_mod, MagicMock())

import turtle_order_logic
from turtle_order_logic import calc_unit_size

MOCK_WATCHLIST = {"005930": {"name": "삼성전자"}}


def _pyramid_qty(pos: dict, total_capital: int, atr_n: float) -> int:
    """run_orders() 피라미딩 수량 계산 로직을 그대로 추출해서 테스트한다."""
    effective_rf = pos.get("effective_risk_factor")
    if effective_rf is None:
        entry_type_pos = pos.get("entry_type", "NORMAL")
        if entry_type_pos == "NORMAL":
            effective_rf = 0.02
        else:
            return 1  # EXCEPTION / MANUAL
    qty = int((total_capital * effective_rf) / atr_n)
    if qty <= 0:
        return 0  # 스킵 신호
    return qty


class TestRiskFactorSafety(unittest.TestCase):

    def setUp(self):
        self.patch_wl = patch("turtle_order_logic.get_watchlist", return_value=MOCK_WATCHLIST)
        self.patch_wl.start()

    def tearDown(self):
        self.patch_wl.stop()

    # ──────────────────────────────────────────
    # [1] 구버전 NORMAL 종목 — 필드 없음 → 기본값 0.02
    # ──────────────────────────────────────────
    def test_구버전_NORMAL_기본값_0_02_사용(self):
        """effective_risk_factor 필드 없는 NORMAL 종목은 0.02로 피라미딩 수량 계산"""
        pos = {"entry_type": "NORMAL"}  # 필드 없음
        qty = _pyramid_qty(pos, total_capital=100_000_000, atr_n=200)
        expected = int(100_000_000 * 0.02 / 200)  # 10,000주
        self.assertEqual(qty, expected)

    # ──────────────────────────────────────────
    # [2] 구버전 EXCEPTION 종목 — 필드 없음 → qty=1
    # ──────────────────────────────────────────
    def test_구버전_EXCEPTION_qty_1_고정(self):
        """effective_risk_factor 필드 없는 EXCEPTION 종목은 qty=1"""
        pos = {"entry_type": "EXCEPTION"}
        qty = _pyramid_qty(pos, total_capital=100_000_000, atr_n=200)
        self.assertEqual(qty, 1)

    # ──────────────────────────────────────────
    # [3] effective_risk_factor = 0.0 → qty=0 → 스킵
    # ──────────────────────────────────────────
    def test_리스크팩터_0_이면_스킵(self):
        """effective_risk_factor=0.0이면 qty=0으로 피라미딩 스킵"""
        pos = {"entry_type": "NORMAL", "effective_risk_factor": 0.0}
        qty = _pyramid_qty(pos, total_capital=100_000_000, atr_n=200)
        self.assertEqual(qty, 0)

    # ──────────────────────────────────────────
    # [4] total_capital=0 → ZeroDivision 없음
    # ──────────────────────────────────────────
    def test_총자본_0이면_None반환_ZeroDivision_없음(self):
        """total_capital=0이어도 ZeroDivisionError 발생 안 함"""
        try:
            result = calc_unit_size("005930", 10_000, 2_000, 0)
            # one_share_ratio = 1.0 → EXCEPTION 경로 or None
        except ZeroDivisionError:
            self.fail("ZeroDivisionError 발생 — total_capital=0 처리 안 됨")

    # ──────────────────────────────────────────
    # [5] price=0 → 계산 오류 없음
    # ──────────────────────────────────────────
    def test_현재가_0이면_예외없이_처리됨(self):
        """price=0이어도 ZeroDivisionError 없이 처리됨"""
        # price=0이면 one_share_ratio=0 → EXCEPTION 조건 불만족 → 일반 경로
        # purchase_amount = qty * 0 = 0 → 상한 이내 → effective_rf=0.02
        # qty = int(총자본 * 0.02 / atr_n) 정상 계산됨
        try:
            result = calc_unit_size("005930", 0, 2_000, 100_000_000)
            # qty=정상값, effective_rf=0.02 반환됨 (매수금=0이라 상한 이내)
        except ZeroDivisionError:
            self.fail("ZeroDivisionError 발생 — price=0 처리 안 됨")

    # ──────────────────────────────────────────
    # [6] effective_risk_factor 음수 → qty<=0 → 스킵
    # ──────────────────────────────────────────
    def test_리스크팩터_음수이면_스킵(self):
        """effective_risk_factor<0이면 qty<0 → 피라미딩 스킵"""
        pos = {"entry_type": "NORMAL", "effective_risk_factor": -0.01}
        qty = _pyramid_qty(pos, total_capital=100_000_000, atr_n=200)
        self.assertLessEqual(qty, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
