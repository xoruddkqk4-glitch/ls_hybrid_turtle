# run_all.py
# 하이브리드 터틀 자동매매 — 통합 배치 실행기
#
# 역할:
#   매매 전략 모듈을 올바른 순서로 한 번씩 실행하고 종료한다.
#   실행 시각·간격은 AWS crontab이 결정한다. 이 파일에는 스케줄 정보를 담지 않는다.
#
# ※ 스크리너(종목 선정)는 crontab이 직접 stock_screener.py를 호출한다.
#   08:40 KST → stock_screener.py premarket
#   09:05 KST → stock_screener.py market_open
#   이 파일은 매매 로직(손절·진입·피라미딩)만 담당한다.
#
# 실행 순서 (이 순서를 바꾸면 안 됨):
#   1. LS증권 로그인
#   2. risk_guardian  — 기존 포지션 손절·익절 감시 (기존 자산 보호 최우선)
#   3. target_manager — 미보유 종목 목표가 갱신
#   4. timer_agent    — 30분 가드 체크 (진입 신호 종목 목록 생성)
#   5. turtle_order_logic — 진입·피라미딩 주문 실행
#
# 실행 방법:
#   python run_all.py

import io
import logging
import logging.handlers
import sys
from datetime import datetime

import pytz

import ls_client
import balance_sync
import sector_cache
import risk_guardian
import target_manager
import timer_agent
import turtle_order_logic
from telegram_alert import SendMessage

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")

# ─────────────────────────────────────
# 로그 파일 설정
# ─────────────────────────────────────

_LOG_FILE    = "run_all.log"   # 로그 파일 이름
_LOG_MAX_MB  = 5               # 파일 하나당 최대 크기 (MB)
_LOG_BACKUPS = 3               # 백업 파일 수 (run_all.log.1 ~ .3)


class _TeeLogger(io.TextIOBase):
    """print() 출력을 콘솔과 로그 파일 두 곳에 동시에 기록하는 중간 다리.

    sys.stdout / sys.stderr 를 이 클래스로 교체하면
    다른 모듈의 print() 출력까지 자동으로 로그 파일에 기록된다.
    """

    def __init__(self, handler: logging.handlers.RotatingFileHandler, original):
        self._handler  = handler
        self._original = original
        # 매번 LogRecord 를 새로 만들면 느리므로 템플릿을 재사용
        self._record = logging.LogRecord(
            "run_all", logging.INFO, "", 0, "", (), None
        )

    def write(self, msg: str) -> int:
        # 콘솔(원래 stdout/stderr)에 그대로 출력
        self._original.write(msg)
        self._original.flush()
        # 내용이 있는 줄만 로그 파일에 기록 (빈 줄·개행만인 줄 제외)
        stripped = msg.rstrip("\n")
        if stripped.strip():
            self._record.msg = stripped
            self._handler.emit(self._record)
        return len(msg)

    def flush(self):
        self._original.flush()
        self._handler.flush()


def _setup_log():
    """로그 파일 자동 순환(Rotating)을 설정한다.

    파일 크기가 5MB를 넘으면 자동으로 새 파일로 교체하고,
    이전 파일은 run_all.log.1 / .2 / .3 으로 최대 3개까지 보관한다.
    총 최대 용량: 5MB × 4개 = 20MB

    ※ crontab 에서 '>> run_all.log 2>&1' 리디렉션을 사용 중이라면
      아래와 같이 리디렉션 없이 호출해야 이 함수가 제대로 동작합니다:
        python run_all.py
      (리디렉션이 있으면 Python 과 셸이 같은 파일에 동시에 쓰게 됩니다)
    """
    handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=_LOG_MAX_MB * 1024 * 1024,
        backupCount=_LOG_BACKUPS,
        encoding="utf-8",
    )
    # 로그 형식: 메시지만 (타임스탬프·레벨 없음 — print()와 동일한 느낌으로)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # stdout 과 stderr 를 TeeLogger 로 교체
    # → 이후 모든 print() 호출이 콘솔 + 로그 파일 양쪽에 기록됨
    sys.stdout = _TeeLogger(handler, sys.__stdout__)
    sys.stderr = _TeeLogger(handler, sys.__stderr__)


def _step_start(label: str) -> datetime:
    """단계 시작 시각을 출력하고 반환한다 (소요 시간 측정용)."""
    now = datetime.now(KST)
    print(f"\n[run_all] ▶ {label}  ({now.strftime('%H:%M:%S')})")
    return now


def _step_done(start: datetime, label: str):
    """단계 종료 시각과 소요 시간을 출력한다."""
    elapsed = (datetime.now(KST) - start).total_seconds()
    print(f"[run_all]   완료: {label} — {elapsed:.1f}초 소요")


def main():
    """자동매매 배치 실행 메인 함수."""

    # 가장 먼저 로그 파일 순환을 설정한다
    # → 이후 모든 print() 출력이 run_all.log 에도 기록됨
    _setup_log()

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 55)
    print(f"  하이브리드 터틀 자동매매 실행 — {now_str}")
    print(f"  로그 파일: {_LOG_FILE} (최대 {_LOG_MAX_MB}MB × {_LOG_BACKUPS + 1}개)")
    print("=" * 55)

    # ─────────────────────────────────────
    # STEP 1: LS증권 로그인
    # ─────────────────────────────────────
    t = _step_start("STEP 1: LS증권 로그인")
    login_ok = ls_client.login()

    if not login_ok:
        msg = "⚠️ [run_all] 로그인 실패 → 자동매매 중단"
        print(msg)
        SendMessage(msg)
        sys.exit(1)

    # 모의투자/실계좌 모드 표시
    mode_str = "모의투자" if ls_client.is_paper_trading() else "실계좌"
    print(f"[run_all] 로그인 성공 ({mode_str} 모드) ✅")
    _step_done(t, "STEP 1: LS증권 로그인")

    # 이후 단계에서 중복 조회를 피하기 위해 잔고를 1회만 조회해 공유한다.
    # (balance_sync, risk_guardian에서 재사용)
    t = _step_start("STEP 1-0: 잔고 1회 조회 (API 최적화)")
    shared_balance = ls_client.get_balance()
    _step_done(t, "STEP 1-0: 잔고 1회 조회")

    # ─────────────────────────────────────
    # STEP 1-A: 잔고 동기화 (수동 매매 반영)
    # ─────────────────────────────────────
    t = _step_start("STEP 1-A: 잔고 동기화")
    sync_ok = balance_sync.run_balance_sync(shared_balance)
    if not sync_ok:
        msg = "⚠️ [run_all] 잔고 조회 실패 → 자동매매 중단"
        print(msg)
        SendMessage(msg)
        sys.exit(1)
    _step_done(t, "STEP 1-A: 잔고 동기화")

    # ─────────────────────────────────────
    # STEP 1-B: 종목별 테마 캐시 갱신 (당일 최초 실행 시만 API 호출)
    # ─────────────────────────────────────
    t = _step_start("STEP 1-B: 테마 캐시 갱신 (첫 실행 시 최대 50초)")
    try:
        sector_cache.update_sector_cache()
    except Exception as e:
        # 테마 캐시 실패는 치명적이지 않음 — 기존 캐시로 계속 진행
        msg = f"⚠️ [run_all] 테마 캐시 갱신 오류 (기존 캐시로 계속): {e}"
        print(msg)
        SendMessage(msg)
    _step_done(t, "STEP 1-B: 테마 캐시 갱신")

    # ─────────────────────────────────────
    # STEP 2: 기존 포지션 손절·익절 감시 (최우선)
    # ─────────────────────────────────────
    t = _step_start("STEP 2: 손절·익절 감시")
    held_codes_after_guard = set()
    try:
        held_codes_after_guard = risk_guardian.run_guardian(shared_balance)
    except Exception as e:
        msg = f"⚠️ [run_all] 손절·익절 감시 오류: {e}"
        print(msg)
        SendMessage(msg)
        # 손절 감시 오류는 심각 — 후속 진입·피라미딩 실행을 중단한다
        sys.exit(1)
    _step_done(t, "STEP 2: 손절·익절 감시")

    # ─────────────────────────────────────
    # STEP 3: 미보유 종목 목표가 갱신
    # ─────────────────────────────────────
    t = _step_start("STEP 3: 목표가 갱신")
    try:
        target_manager.run_update(held_codes_after_guard)
    except Exception as e:
        # 목표가 갱신 오류는 치명적이지 않음 — 로그만 남기고 계속 진행
        msg = f"⚠️ [run_all] 목표가 갱신 오류 (계속 진행): {e}"
        print(msg)
        SendMessage(msg)
    _step_done(t, "STEP 3: 목표가 갱신")

    # ─────────────────────────────────────
    # STEP 4: 30분 가드 체크 (진입 신호 종목 파악)
    # ─────────────────────────────────────
    t = _step_start("STEP 4: 30분 가드 체크")
    entry_signals = []
    try:
        entry_signals = timer_agent.run_timer_check()
    except Exception as e:
        msg = f"⚠️ [run_all] 타이머 체크 오류 (계속 진행): {e}"
        print(msg)
        SendMessage(msg)
    _step_done(t, "STEP 4: 30분 가드 체크")

    # ─────────────────────────────────────
    # STEP 5: 진입·피라미딩 주문 실행
    # ─────────────────────────────────────
    t = _step_start("STEP 5: 주문 실행")
    try:
        turtle_order_logic.run_orders(entry_signals)
    except Exception as e:
        msg = f"⚠️ [run_all] 주문 실행 오류: {e}"
        print(msg)
        SendMessage(msg)
    _step_done(t, "STEP 5: 주문 실행")

    # ─────────────────────────────────────
    # 완료
    # ─────────────────────────────────────
    end_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'=' * 55}")
    print(f"  자동매매 배치 실행 완료 — {end_str}")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
