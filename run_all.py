# run_all.py
# 하이브리드 터틀 자동매매 — 통합 배치 실행기
#
# 역할:
#   매매 전략 모듈을 올바른 순서로 한 번씩 실행하고 종료한다.
#   실행 시각·간격은 AWS crontab이 결정한다. 이 파일에는 스케줄 정보를 담지 않는다.
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

import sys
from datetime import datetime

import pytz

import ls_client
import risk_guardian
import target_manager
import timer_agent
import turtle_order_logic
from telegram_alert import SendMessage

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")


def main():
    """자동매매 배치 실행 메인 함수."""

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 55)
    print(f"  하이브리드 터틀 자동매매 실행 — {now_str}")
    print("=" * 55)

    # ─────────────────────────────────────
    # STEP 1: LS증권 로그인
    # ─────────────────────────────────────
    print("\n[run_all] ▶ STEP 1: LS증권 로그인")
    login_ok = ls_client.login()

    if not login_ok:
        msg = "⚠️ [run_all] 로그인 실패 → 자동매매 중단"
        print(msg)
        SendMessage(msg)
        sys.exit(1)

    # 모의투자/실계좌 모드 표시
    mode_str = "모의투자" if ls_client.is_paper_trading() else "실계좌"
    print(f"[run_all] 로그인 성공 ({mode_str} 모드) ✅")

    # ─────────────────────────────────────
    # STEP 2: 기존 포지션 손절·익절 감시 (최우선)
    # ─────────────────────────────────────
    print("\n[run_all] ▶ STEP 2: 손절·익절 감시")
    try:
        risk_guardian.run_guardian()
    except Exception as e:
        msg = f"⚠️ [run_all] 손절·익절 감시 오류: {e}"
        print(msg)
        SendMessage(msg)
        # 손절 감시 오류는 심각 — 후속 진입·피라미딩 실행을 중단한다
        sys.exit(1)

    # ─────────────────────────────────────
    # STEP 3: 미보유 종목 목표가 갱신
    # ─────────────────────────────────────
    print("\n[run_all] ▶ STEP 3: 목표가 갱신")
    try:
        target_manager.run_update()
    except Exception as e:
        # 목표가 갱신 오류는 치명적이지 않음 — 로그만 남기고 계속 진행
        msg = f"⚠️ [run_all] 목표가 갱신 오류 (계속 진행): {e}"
        print(msg)
        SendMessage(msg)

    # ─────────────────────────────────────
    # STEP 4: 30분 가드 체크 (진입 신호 종목 파악)
    # ─────────────────────────────────────
    print("\n[run_all] ▶ STEP 4: 30분 가드 체크")
    entry_signals = []
    try:
        entry_signals = timer_agent.run_timer_check()
    except Exception as e:
        msg = f"⚠️ [run_all] 타이머 체크 오류 (계속 진행): {e}"
        print(msg)
        SendMessage(msg)

    # ─────────────────────────────────────
    # STEP 5: 진입·피라미딩 주문 실행
    # ─────────────────────────────────────
    print("\n[run_all] ▶ STEP 5: 주문 실행")
    try:
        turtle_order_logic.run_orders(entry_signals)
    except Exception as e:
        msg = f"⚠️ [run_all] 주문 실행 오류: {e}"
        print(msg)
        SendMessage(msg)

    # ─────────────────────────────────────
    # 완료
    # ─────────────────────────────────────
    end_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'=' * 55}")
    print(f"  자동매매 배치 실행 완료 — {end_str}")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
