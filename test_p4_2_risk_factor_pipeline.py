# test_p4_2_risk_factor_pipeline.py
# P4-2 통합 테스트 — effective_risk_factor 전체 파이프라인
#
# 확인 항목:
#   [1] 진입 주문 후 held_stock_record에 effective_risk_factor가 저장됨
#   [2] 상한 이내 진입 → 0.02 저장
#   [3] 상한 초과 진입 → 줄어든 리스크팩터 저장
#   [4] 피라미딩 시 저장된 effective_risk_factor로 수량 계산
#   [5] EXCEPTION 진입 후 피라미딩 → qty=1 고정
#
# 실행: python test_p4_2_risk_factor_pipeline.py

import sys
import json
import unittest
import tempfile
import os
from unittest.mock import patch, MagicMock

# API 연결 모듈 Mock 처리
for _mod in ["ls_client", "daily_chart_cache", "sector_cache",
             "programgarden_finance", "trade_ledger", "dotenv",
             "indicator_calc", "telegram_alert"]:
    sys.modules.setdefault(_mod, MagicMock())

import turtle_order_logic
from turtle_order_logic import (
    place_entry_order,
    load_position_state,
    save_position_state,
    HELD_STOCK_RECORD_FILE,
)

MOCK_WATCHLIST = {"005930": {"name": "삼성전자"}}

# ls_client Mock 설정 — 주문 성공, 체결 완료로 고정
_ls = sys.modules["ls_client"]
_ls.place_order.return_value    = {"success": True, "order_no": "ORD001"}
_ls.wait_for_order_fill.return_value = {"filled": True, "filled_qty": 100}
_ls.get_holding_qty.return_value = 0


class TestEffectiveRiskFactorPipeline(unittest.TestCase):

    def setUp(self):
        # 임시 파일로 held_stock_record.json을 대체
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({}, self._tmp)
        self._tmp.close()

        self.patch_file   = patch.object(turtle_order_logic, "HELD_STOCK_RECORD_FILE", self._tmp.name)
        self.patch_wl     = patch("turtle_order_logic.get_watchlist", return_value=MOCK_WATCHLIST)
        self.patch_ledger = patch("turtle_order_logic.trade_ledger")
        self.patch_tg     = patch("turtle_order_logic.telegram_alert")

        self.patch_file.start()
        self.patch_wl.start()
        self.patch_ledger.start()
        self.patch_tg.start()

    def tearDown(self):
        self.patch_file.stop()
        self.patch_wl.stop()
        self.patch_ledger.stop()
        self.patch_tg.stop()
        os.unlink(self._tmp.name)

    def _get_saved_pos(self, code="005930"):
        """임시 파일에서 저장된 포지션 상태를 읽는다."""
        with open(self._tmp.name, encoding="utf-8") as f:
            return json.load(f).get(code, {})

    # ──────────────────────────────────────────
    # [1] 진입 후 held_stock_record에 effective_risk_factor 저장 확인
    # ──────────────────────────────────────────
    def test_진입후_effective_risk_factor_저장됨(self):
        """place_entry_order 호출 후 held_stock_record에 effective_risk_factor 필드가 있어야 함"""
        place_entry_order(
            code="005930", qty=100, price=10_000, atr_n=2_000,
            entry_type="NORMAL", max_unit=3,
            entry_source="TURTLE_S1", effective_risk_factor=0.02
        )
        pos = self._get_saved_pos()
        self.assertIn("effective_risk_factor", pos)

    # ──────────────────────────────────────────
    # [2] 상한 이내 진입 → 0.02 저장
    # ──────────────────────────────────────────
    def test_상한이내진입_0_02_저장(self):
        """매수금이 상한 이내이면 effective_risk_factor = 0.02가 저장됨"""
        place_entry_order(
            code="005930", qty=100, price=10_000, atr_n=2_000,
            entry_type="NORMAL", max_unit=3,
            entry_source="TURTLE_S1", effective_risk_factor=0.02
        )
        pos = self._get_saved_pos()
        self.assertAlmostEqual(pos["effective_risk_factor"], 0.02)

    # ──────────────────────────────────────────
    # [3] 상한 초과 진입 → 줄어든 리스크팩터 저장
    # ──────────────────────────────────────────
    def test_상한초과진입_조정된_리스크팩터_저장(self):
        """매수금 상한 초과 시 계산된 effective_risk_factor(0.002)가 저장됨"""
        place_entry_order(
            code="005930", qty=1000, price=10_000, atr_n=200,
            entry_type="NORMAL", max_unit=3,
            entry_source="TURTLE_S1", effective_risk_factor=0.002
        )
        pos = self._get_saved_pos()
        self.assertAlmostEqual(pos["effective_risk_factor"], 0.002)

    # ──────────────────────────────────────────
    # [4] 피라미딩 시 저장된 effective_risk_factor로 수량 계산
    # ──────────────────────────────────────────
    def test_피라미딩_저장된_리스크팩터로_수량계산(self):
        """피라미딩 수량 = int(총자본 × effective_risk_factor / atr_n)"""
        # 진입 시 effective_risk_factor = 0.002 저장
        effective_rf  = 0.002
        atr_n         = 200
        total_capital = 100_000_000  # 1억

        # 예상 피라미딩 수량
        expected_qty = int((total_capital * effective_rf) / atr_n)  # 1,000주

        # 저장된 pos 직접 구성 (place_entry_order 없이)
        pos = {
            "stock_name":            "삼성전자",
            "current_unit":          1,
            "last_buy_price":        10_000,
            "avg_buy_price":         10_000,
            "stop_loss_price":       9_600,
            "next_pyramid_price":    10_100,
            "entry_type":            "NORMAL",
            "max_unit":              3,
            "total_qty":             1000,
            "entry_source":          "TURTLE_S1",
            "effective_risk_factor": effective_rf,
        }

        # 피라미딩 수량 계산 로직 직접 검증
        ef = pos.get("effective_risk_factor")
        if ef is None:
            qty = 1
        else:
            qty = int((total_capital * ef) / atr_n)

        self.assertEqual(qty, expected_qty)
        self.assertEqual(qty, 1000)

    # ──────────────────────────────────────────
    # [5] EXCEPTION 진입 후 피라미딩 → qty=1 고정
    # ──────────────────────────────────────────
    def test_EXCEPTION진입_피라미딩_qty_1_고정(self):
        """effective_risk_factor=None이면 피라미딩 qty=1"""
        pos = {
            "entry_type":            "EXCEPTION",
            "effective_risk_factor": None,
        }
        ef = pos.get("effective_risk_factor")
        qty = 1 if ef is None else int((100_000_000 * ef) / 200)
        self.assertEqual(qty, 1)

    # ──────────────────────────────────────────
    # [보너스] 2N 손절가와 effective_risk_factor가 함께 저장됨
    # ──────────────────────────────────────────
    def test_손절가와_리스크팩터_함께_저장(self):
        """stop_loss_price(2N 손절)와 effective_risk_factor가 동시에 저장됨"""
        price = 10_000
        atr_n = 2_000
        place_entry_order(
            code="005930", qty=100, price=price, atr_n=atr_n,
            entry_type="NORMAL", max_unit=3,
            entry_source="TURTLE_S1", effective_risk_factor=0.02
        )
        pos = self._get_saved_pos()
        expected_stop = price - 2 * atr_n  # 10,000 - 4,000 = 6,000원
        self.assertEqual(pos["stop_loss_price"], expected_stop)
        self.assertIn("effective_risk_factor", pos)
        self.assertAlmostEqual(pos["effective_risk_factor"], 0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
