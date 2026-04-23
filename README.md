# LS 하이브리드 터틀 자동매매 시스템

LS증권 API를 이용한 **국내 상장 주식(KRX) 자동매매 봇**입니다.  
전설적인 **터틀 트레이딩**의 자금 관리 원칙에, 가짜 돌파를 걸러내는 **동적 목표가 + 30분 가드** 로직을 결합한 하이브리드 전략으로 운영됩니다.

---

## 전략 한눈에 보기

| 단계 | 내용 |
|------|------|
| **종목 선정** | 매일 08:40 / 09:05 두 차례, 거래대금·신고가 기준으로 50개 자동 선정 |
| **진입 조건** | 동적 목표가(현재가 ×1.02 또는 240분봉 20MA ×1.005) 이상에서 30분 유지 시 매수 |
| **수량 결정** | 총자본 × 2% ÷ ATR(N) — 변동성에 맞춰 자동 계산 |
| **추가 매수** | 매수가 대비 0.5N 오를 때마다 1 Unit 추가 (최대 4 Unit) |
| **손절** | 최종 체결가 대비 2N 하락 시 전량 즉시 매도 |
| **익절** | 10일 신저가 경신 또는 5MA 하향 돌파 시 청산 |

---

## 시작 전 준비

### 1. 필요한 계정 및 키

- **LS증권 계좌** + **API 앱키/시크릿키** (LS 개발자 포털에서 발급)
- **텔레그램 봇 토큰** + **채팅 ID** (BotFather에서 발급)
- **Google 서비스 계정 JSON** (체결 원장 구글 시트 저장 시 필요)

### 2. 라이브러리 설치

```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정

`.env.example` 파일을 복사해서 `.env` 파일을 만들고, 값을 채웁니다.

```bash
cp .env.example .env
# 이후 .env 파일을 열어서 각 항목에 실제 값을 입력
```

`.env` 파일의 주요 항목:

```
LS_APP_KEY=앱키
LS_APP_SECRET_KEY=시크릿키
LS_PAPER_TRADING=True        # True = 모의투자, False = 실계좌
LS_ACCOUNT_NO=계좌번호
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=채팅ID
GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json
GOOGLE_SPREADSHEET_TITLE=하이브리드터틀_체결원장
```

> ⚠️ **처음에는 반드시 `LS_PAPER_TRADING=True`(모의투자)로 충분히 테스트한 뒤 실계좌로 전환하세요.**

---

## 실행 방법

### 자동 실행 (권장)

```bash
python run_all.py
```

모든 단계를 장 시간에 맞춰 자동으로 순서대로 실행합니다.

### 단계별 수동 실행

```bash
python stock_screener.py premarket    # 08:40 — 후보 종목 선별
python stock_screener.py market_open  # 09:05 — 최종 50개 감시 종목 확정
python target_manager.py              # 동적 목표가 갱신
python timer_agent.py                 # 30분 가드 타이머 체크
python turtle_order_logic.py          # 진입·피라미딩 주문
python risk_guardian.py               # 2N 손절 · 트레일링 스탑 감시
```

---

## 파일 구성

```
ls_hybrid_turtle/
│
├── run_all.py                — 통합 자동 실행기
│
├── [종목 선정]
│   └── stock_screener.py     — 매일 2회 배치: 거래대금·신고가 기준 50개 선정
│
├── [기반 모듈]
│   ├── ls_client.py          — LS증권 API 연결 (시세·주문·잔고)
│   ├── indicator_calc.py     — ATR, 이동평균선, 10일 신저가 계산
│   ├── trade_ledger.py       — 체결 원장 기록 + 구글 시트 자동 동기화
│   ├── telegram_alert.py     — 텔레그램 알림 발송
│   └── config.py             — 감시 종목 목록 로드
│
├── [진입 검증]
│   ├── target_manager.py     — 동적 목표가 산출 및 미보유 종목 상태 관리
│   └── timer_agent.py        — 30분 안착 검증 타이머
│
├── [주문·리스크]
│   ├── turtle_order_logic.py — 수량 계산, 피라미딩 주문 실행
│   └── risk_guardian.py      — 2N 손절 · 트레일링 스탑 실시간 감시
│
├── [보조 모듈]
│   ├── balance_sync.py       — 실제 잔고 ↔ 보유 종목 기록 동기화
│   ├── chart_updater.py      — 구글 시트 손익 차트 자동 생성
│   ├── sector_cache.py       — 종목 테마 캐시 관리
│   └── daily_chart_cache.py  — 일봉·240분봉 캐시 관리
│
├── .env.example              — 환경변수 템플릿 (이 파일을 복사해서 .env 작성)
├── requirements.txt          — 필요한 라이브러리 목록
└── ls_hybrid_turtle.md       — 전략 상세 명세서
```

### 자동으로 생성되는 데이터 파일 (git에 올리지 않음)

| 파일 | 내용 |
|------|------|
| `dynamic_watchlist.json` | 오늘 감시 대상 50개 종목 |
| `stock_candidates.json` | 08:40 배치 후보 종목 목록 |
| `held_stock_record.json` | 현재 보유 중인 종목 상태 |
| `unheld_stock_record.json` | 미보유 종목의 목표가·타이머 상태 |
| `trade_ledger.json` | 전체 체결 원장 기록 |
| `sector_cache.json` | 종목별 테마 캐시 |
| `daily_chart_cache.json` | 일봉·240분봉 캐시 |

---

## 리스크 관리 요약

| 항목 | 규칙 |
|------|------|
| 단일 종목 최대 손실 | 총자본의 약 4% 이내 (2N 기준) |
| 종목당 최대 보유 | 4 Unit |
| 예외 진입 종목 (1주 가격이 총자본 2~5%) | 피라미딩 최대 2 Unit |
| Unit당 최대 투자금액 | 총자본 ÷ 12 |
| 주문·감시 범위 | `dynamic_watchlist.json` 등록 종목만 |

---

## 구글 시트 연동

체결이 일어날 때마다 다음 세 시트가 자동으로 갱신됩니다.

- **체결기록** — 매수·매도 내역 원장
- **포트폴리오 추이** — 일별 총자산 변화
- **손익차트** — 일일 손익 막대 + 누적 손익 선 콤보 차트

---

## 주의사항

- `.env`, `service_account.json` 파일은 절대 git에 올리지 않습니다.
- 처음 실행 시 반드시 모의투자(`LS_PAPER_TRADING=True`)로 테스트합니다.
- 감시 목록(`dynamic_watchlist.json`) 외 종목에는 주문이 실행되지 않습니다.

---

## 기술 스택

| 항목 | 내용 |
|------|------|
| LS증권 API | `programgarden-finance` 패키지 |
| 텔레그램 알림 | `requests` 기반 Webhook |
| 시간대 | KST (`pytz`) |
| 데이터 저장 | 로컬 JSON + Google Sheets (`gspread`) |
