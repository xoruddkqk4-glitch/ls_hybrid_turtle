# test_p4_2_pipeline.py
# P4-2 통합 테스트 — 전체 흐름 연결 확인
#
# P4-1(단위 테스트)과 차이점:
#   - P4-1 : 각 함수를 따로 검증 (JSON 파일 없이 dict로만)
#   - P4-2 : 실제 임시 JSON 파일을 사용해
#            target_manager.run_update() → unheld_stock_record.json →
#            timer_agent.run_timer_check() 순서로 데이터가 올바르게 흐르는지 확인
#
# 검증 항목:
#   [1] run_update() — 새 종목 등록 시 breakout_since 기록 여부
#   [2] run_update() x2 — 두 번 호출해도 기존 타임스탬프를 보존하는지
#   [3] run_update() — S1 신호가 꺼지면 breakout_since 초기화되는지
#   [4] 전체 파이프라인 — run_update() → run_timer_check() → S1 신호 발생
#   [5] 전체 파이프라인 — S1+S2 동시 신호 시 TURTLE_S2 우선
#   [6] 전체 파이프라인 — 10시 이전이면 신호 없음

import sys
import json
import os
import tempfile
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch, MagicMock, call

import pytz

# ──────────────────────────────────────────────────────────
# API 연결 모듈을 빈 Mock으로 미리 등록 (임포트 오류 방지)
# ──────────────────────────────────────────────────────────
for _mod in ["ls_client", "daily_chart_cache", "sector_cache",
             "programgarden_finance", "trade_ledger", "dotenv"]:
    sys.modules.setdefault(_mod, MagicMock())

import target_manager
import timer_agent

KST = pytz.timezone("Asia/Seoul")

# ── 고정 시각 상수 ──────────────────────────────────────────
_DATE = (2026, 4, 27)
FIXED_1030 = KST.localize(datetime(*_DATE, 10, 30, 0))  # 10:30
FIXED_0950 = KST.localize(datetime(*_DATE,  9, 50, 0))  # 09:50 (10시 이전)
FIXED_1000 = KST.localize(datetime(*_DATE, 10,  0, 0))  # 10:00

# ── 감시 종목 (테스트 전용) ───────────────────────────────────
WATCHLIST = {"035420": {"name": "NAVER", "score": 0.9}}
CODE = "035420"
CURRENT_PRICE = 50_000   # 현재가 5만원


def _make_indicator_side_effect(s1_high: int, s2_high: int):
    """calc_n_day_high 호출 시 n값에 따라 다른 고가를 반환하는 side_effect."""
    def _fn(candles, n):
        return s1_high if n == 20 else s2_high
    return _fn


def _run_update_with_mocks(
    tmp_json_path: str,
    s1_high: int,
    s2_high: int,
    fixed_now: datetime,
):
    """
    target_manager.run_update()를 실행한다.

    - tmp_json_path: 임시 JSON 파일 경로 (실제 IO 발생)
    - s1_high: 20일 신고가 (CURRENT_PRICE > s1_high 이면 S1 True)
    - s2_high: 55일 신고가 (CURRENT_PRICE > s2_high 이면 S2 True)
    - fixed_now: datetime.now(KST)를 대체할 고정 시각
    """
    dummy_candles = [{"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}] * 60

    with patch("target_manager.UNHELD_RECORD_FILE", tmp_json_path), \
         patch("target_manager.get_watchlist",       return_value=WATCHLIST), \
         patch("target_manager.ls_client.get_balance", return_value=[]), \
         patch("target_manager.ls_client.get_multi_price",
               return_value={CODE: CURRENT_PRICE}), \
         patch("target_manager.daily_chart_cache.get_daily_cached",
               return_value=dummy_candles), \
         patch("target_manager.daily_chart_cache.update_daily_cache"), \
         patch("target_manager.indicator_calc.calc_n_day_high",
               side_effect=_make_indicator_side_effect(s1_high, s2_high)), \
         patch("target_manager.time.sleep"), \
         patch("target_manager.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.now.side_effect  = None
        target_manager.run_update()


def _load_tmp(path: str) -> dict:
    """임시 JSON 파일을 읽어 dict로 반환한다."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════
class TestRunUpdateBreakoutSince(unittest.TestCase):
    """run_update() — breakout_since 기록·보존·초기화 검증"""

    def setUp(self):
        # 매 테스트마다 깨끗한 임시 파일 생성
        fd, self.tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.tmp)   # 빈 상태에서 시작 (파일 없음)

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.unlink(self.tmp)

    # ── [1] 새 종목 — S1 첫 돌파 시 breakout_since 기록 ──────────

    def test_신규종목_S1돌파_breakout_since_기록(self):
        """새 종목이 처음 등록될 때 S1 돌파 시각이 기록되어야 한다."""
        _run_update_with_mocks(
            self.tmp,
            s1_high  = 48_000,   # 현재가 50000 > 48000 → S1 True
            s2_high  = 55_000,   # 현재가 50000 < 55000 → S2 False
            fixed_now= FIXED_1030,
        )
        rec = _load_tmp(self.tmp).get(CODE, {})

        self.assertTrue(rec.get("turtle_s1_signal"),  "S1 신호가 True여야 함")
        self.assertFalse(rec.get("turtle_s2_signal"), "S2 신호가 False여야 함")
        self.assertIsNotNone(rec.get("turtle_s1_breakout_since"),
                             "S1 돌파 시각이 기록되어야 함")
        self.assertIsNone(rec.get("turtle_s2_breakout_since"),
                          "S2 돌파 시각은 None이어야 함")

    def test_신규종목_S2돌파_breakout_since_기록(self):
        """S1과 S2 모두 돌파 시 둘 다 breakout_since가 기록되어야 한다."""
        _run_update_with_mocks(
            self.tmp,
            s1_high  = 48_000,   # S1 True
            s2_high  = 48_000,   # S2 True (같은 값이면 둘 다 True)
            fixed_now= FIXED_1030,
        )
        rec = _load_tmp(self.tmp).get(CODE, {})

        self.assertTrue(rec.get("turtle_s1_signal"))
        self.assertTrue(rec.get("turtle_s2_signal"))
        self.assertIsNotNone(rec.get("turtle_s1_breakout_since"))
        self.assertIsNotNone(rec.get("turtle_s2_breakout_since"))

    # ── [2] 두 번 호출 — 기존 타임스탬프 보존 ────────────────────

    def test_두번호출_타임스탬프_보존(self):
        """
        첫 번째 run_update()에서 기록된 breakout_since가
        두 번째 run_update()에서 덮어써지지 않아야 한다.

        (타이머가 리셋되면 30분 가드를 영원히 통과할 수 없게 됨)
        """
        # 1차 — 09:55에 S1 돌파 기록
        T1 = KST.localize(datetime(*_DATE, 9, 55, 0))
        _run_update_with_mocks(self.tmp, 48_000, 55_000, T1)
        first_since = _load_tmp(self.tmp)[CODE]["turtle_s1_breakout_since"]

        # 2차 — 10:30에 실행 (S1 여전히 True)
        _run_update_with_mocks(self.tmp, 48_000, 55_000, FIXED_1030)
        second_since = _load_tmp(self.tmp)[CODE]["turtle_s1_breakout_since"]

        self.assertEqual(first_since, second_since,
                         "두 번째 run_update()가 기존 breakout_since를 바꾸면 안 됨")

    # ── [3] 신호 소멸 — 타임스탬프 초기화 ───────────────────────

    def test_신호소멸_타임스탬프_초기화(self):
        """
        S1 신호가 True → False 로 바뀌면 breakout_since 가 None 으로 초기화된다.
        (가격이 다시 신고가 아래로 내려간 상황)
        """
        # 1차 — S1 True (돌파)
        _run_update_with_mocks(self.tmp, 48_000, 55_000, FIXED_1030)
        self.assertIsNotNone(_load_tmp(self.tmp)[CODE]["turtle_s1_breakout_since"])

        # 2차 — S1 False (현재가 50000 < s1_high 52000 → 돌파 아님)
        _run_update_with_mocks(self.tmp, 52_000, 55_000, FIXED_1030)
        rec = _load_tmp(self.tmp)[CODE]

        self.assertFalse(rec.get("turtle_s1_signal"), "S1 신호가 False여야 함")
        self.assertIsNone(rec.get("turtle_s1_breakout_since"),
                          "신호가 꺼지면 breakout_since 가 None이어야 함")


# ══════════════════════════════════════════════════════════
class TestFullPipeline(unittest.TestCase):
    """run_update() → JSON → run_timer_check() 전체 파이프라인 검증"""

    def setUp(self):
        fd, self.tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.tmp)

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.unlink(self.tmp)

    def _full_run(
        self,
        s1_high: int,
        s2_high: int,
        update_now: datetime,
        check_now: datetime,
    ) -> list:
        """
        run_update()로 JSON을 생성한 뒤
        run_timer_check()가 그 JSON을 읽어 신호를 반환하는 전체 흐름을 실행한다.
        """
        # ① 상태 파일 갱신
        _run_update_with_mocks(self.tmp, s1_high, s2_high, update_now)

        # ② 진입 신호 체크 (임시 JSON 경유, 고정 시각 사용)
        with patch("target_manager.UNHELD_RECORD_FILE", self.tmp), \
             patch("timer_agent.get_watchlist", return_value=WATCHLIST), \
             patch("timer_agent._now_kst",      return_value=check_now):
            return timer_agent.run_timer_check()

    # ── [4] S1 신호 전체 파이프라인 ──────────────────────────────

    def test_S1_파이프라인_30분_경과_신호발생(self):
        """
        run_update() 09:55에 S1 돌파 기록 →
        run_timer_check() 10:30에 확인 → 35분 경과 + 10시 이후 → TURTLE_S1 신호
        """
        UPDATE_AT = KST.localize(datetime(*_DATE, 9, 55, 0))
        signals = self._full_run(
            s1_high   = 48_000,
            s2_high   = 55_000,
            update_now= UPDATE_AT,
            check_now = FIXED_1030,
        )
        self.assertEqual(len(signals), 1, "신호가 1개여야 함")
        self.assertEqual(signals[0]["code"],         CODE)
        self.assertEqual(signals[0]["entry_source"], "TURTLE_S1")

    def test_S1_파이프라인_30분_미달_신호없음(self):
        """
        run_update() 10:10에 S1 돌파 기록 →
        run_timer_check() 10:30에 확인 → 20분만 경과 → 신호 없음
        """
        UPDATE_AT = KST.localize(datetime(*_DATE, 10, 10, 0))
        signals = self._full_run(
            s1_high   = 48_000,
            s2_high   = 55_000,
            update_now= UPDATE_AT,
            check_now = FIXED_1030,
        )
        self.assertEqual(signals, [], "30분 미달이면 신호 없음")

    # ── [5] S1+S2 동시 — S2 우선 ─────────────────────────────────

    def test_S1_S2_동시_S2가_우선_반환(self):
        """
        S1·S2 모두 돌파 + 둘 다 30분 가드 통과 → TURTLE_S2 하나만 반환
        """
        UPDATE_AT = KST.localize(datetime(*_DATE, 9, 55, 0))
        signals = self._full_run(
            s1_high   = 48_000,   # S1 True
            s2_high   = 48_000,   # S2 True
            update_now= UPDATE_AT,
            check_now = FIXED_1030,
        )
        self.assertEqual(len(signals), 1, "같은 종목이면 S2 하나만")
        self.assertEqual(signals[0]["entry_source"], "TURTLE_S2")

    # ── [6] 10시 이전 — 신호 없음 ────────────────────────────────

    def test_10시이전_S1돌파_신호없음(self):
        """
        S1 돌파가 35분 이상 경과했더라도 현재 시각이 10시 이전이면 신호 없음
        """
        UPDATE_AT = KST.localize(datetime(*_DATE, 9, 15, 0))
        signals = self._full_run(
            s1_high   = 48_000,
            s2_high   = 55_000,
            update_now= UPDATE_AT,
            check_now = FIXED_0950,  # 09:50 — 10시 이전
        )
        self.assertEqual(signals, [], "10시 이전이면 신호 없음")


# ══════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("P4-2 통합 테스트 시작")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    for cls in [
        TestRunUpdateBreakoutSince,
        TestFullPipeline,
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
