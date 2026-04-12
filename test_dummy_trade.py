# test_dummy_trade.py
# 더미 체결 데이터 테스트 스크립트
#
# 목적:
#   1. 가짜 매수·매도 거래 데이터를 Google Sheets에 저장
#   2. 텔레그램으로 요약 알림 발송
#   3. 열제목(수익률 포함)이 정상적으로 표시되는지 확인
#
# 사용법:
#   python test_dummy_trade.py

import trade_ledger
import telegram_alert

# ─────────────────────────────────────────
# 더미 데이터 준비
# ─────────────────────────────────────────

# 테스트용 매수 기록: 삼성전자 10주 @206,000원
dummy_buy = {
    "side":       "BUY",
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "qty":        10,
    "unit_price": 206000,
    "order_no":   "TEST-BUY-001",
    "order_type": "MARKET",
    "source":     "TURTLE_ENTRY",
    "note":       "1차 Unit 진입 (더미 테스트)",
}

# 테스트용 매도 기록: 삼성전자 10주 @230,000원 → 수익률 +11.65%
#   (230,000 - 206,000) / 206,000 × 100 = 11.6504...%
dummy_sell = {
    "side":        "SELL",
    "stock_code":  "005930",
    "stock_name":  "삼성전자",
    "qty":         10,
    "unit_price":  230000,
    "order_no":    "TEST-SELL-001",
    "order_type":  "MARKET",
    "source":      "TURTLE_EXIT",
    "profit_rate": round((230000 - 206000) / 206000 * 100, 2),  # +11.65%
    "note":        "5MA 하향 돌파 익절 (더미 테스트)",
}

# ─────────────────────────────────────────
# 체결 원장 저장 (JSON + Google Sheets)
# ─────────────────────────────────────────

print("=" * 50)
print("[테스트] 더미 매수 기록 저장 중...")
trade_ledger.append_trade(dummy_buy)

print()
print("[테스트] 더미 매도 기록 저장 중...")
trade_ledger.append_trade(dummy_sell)

# ─────────────────────────────────────────
# 텔레그램 요약 알림
# ─────────────────────────────────────────

profit_rate = dummy_sell["profit_rate"]
buy_price   = dummy_buy["unit_price"]
sell_price  = dummy_sell["unit_price"]
qty         = dummy_sell["qty"]
gross_profit = (sell_price - buy_price) * qty  # 총 수익 (원)

msg = (
    "📊 [더미 테스트] 체결 원장 기록 완료\n"
    "\n"
    "▶ 매수 체결\n"
    f"  종목: 삼성전자(005930)\n"
    f"  수량: {qty:,}주 | 단가: {buy_price:,}원\n"
    f"  매매구분: TURTLE_ENTRY\n"
    "\n"
    "▶ 매도 체결\n"
    f"  종목: 삼성전자(005930)\n"
    f"  수량: {qty:,}주 | 단가: {sell_price:,}원\n"
    f"  매매구분: TURTLE_EXIT\n"
    f"  수익률: +{profit_rate:.2f}% (수익 {gross_profit:,}원)\n"
    "\n"
    "✅ Google Sheets 및 trade_ledger.json 저장 완료"
)

print()
print("[테스트] 텔레그램 알림 발송 중...")
telegram_alert.SendMessage(msg)
print("[테스트] 완료!")
print("=" * 50)
