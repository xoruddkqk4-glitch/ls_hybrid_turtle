# record_daily_snapshot.py
# 일일 포트폴리오 수익률 기록 스크립트
#
# 역할:
#   장 마감 후 하루에 한 번 실행해 오늘의 총평가금액·누적수익률을
#   Google Sheets '포트폴리오 추이' 시트에 기록한다.
#   실행 시각은 AWS crontab이 결정한다. 이 파일에는 스케줄 정보를 담지 않는다.
#
# 필요한 .env 설정:
#   INITIAL_CAPITAL=10000000  ← 처음 투자한 원금(원 단위). 미설정 시 수익률 칸 빈칸.
#
# 실행 방법:
#   python record_daily_snapshot.py

import os
import sys
from datetime import datetime

import pytz
from dotenv import load_dotenv

import ls_client
import trade_ledger

load_dotenv()

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")


def main():
    """일일 포트폴리오 스냅샷 기록 메인 함수."""

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 50)
    print(f"  일일 포트폴리오 스냅샷 기록 — {now_str}")
    print("=" * 50)

    # ─────────────────────────────────────
    # STEP 1: LS증권 로그인
    # ─────────────────────────────────────
    print("\n[snapshot] ▶ STEP 1: LS증권 로그인")
    login_ok = ls_client.login()
    if not login_ok:
        print("[snapshot] 로그인 실패 → 종료")
        sys.exit(1)

    mode_str = "모의투자" if ls_client.is_paper_trading() else "실계좌"
    print(f"[snapshot] 로그인 성공 ({mode_str} 모드) ✅")

    # ─────────────────────────────────────
    # STEP 2: 잔고·총평가금액 조회 후 기록
    # ─────────────────────────────────────
    print("\n[snapshot] ▶ STEP 2: 포트폴리오 기록")
    try:
        # .env의 INITIAL_CAPITAL(초기 투자 원금)으로 누적수익률 계산
        # 설정하지 않았으면 0 → 수익률 칸 빈칸으로 남김
        initial_capital = int(os.getenv("INITIAL_CAPITAL", "0"))

        # 잔고 조회 (보유 종목 목록)
        balance = ls_client.get_balance()

        # 총평가금액 = 보유 주식 평가금액 + 예수금
        total_value = ls_client.get_total_capital()

        # 주식평가액만 따로 계산 (보유수량 × 현재가 합계)
        stock_value = sum(int(item["qty"] * item["current_price"]) for item in balance)

        # 현재 보유 종목 수
        holdings_cnt = len(balance)

        # Google Sheets '포트폴리오 추이' 시트에 기록
        # (내부에서 하루 1회 가드 동작 — 같은 날 두 번 호출해도 첫 번째만 기록됨)
        trade_ledger.record_portfolio_snapshot(
            total_value,
            stock_value,
            holdings_cnt,
            initial_capital,
        )

    except Exception as e:
        print(f"[snapshot] 오류 발생: {e}")
        sys.exit(1)

    # ─────────────────────────────────────
    # 완료
    # ─────────────────────────────────────
    end_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'=' * 50}")
    print(f"  스냅샷 기록 완료 — {end_str}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
