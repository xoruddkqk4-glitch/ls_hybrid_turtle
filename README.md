# LS 하이브리드 터틀 자동매매 시스템

LS증권 API를 이용한 **국내 상장 주식(KRX) 자동매매 봇**입니다.  
전설적인 **터틀 트레이딩**의 자금 관리 원칙에, 가짜 돌파를 걸러내는 **30분 가드 + 10시 이후 진입 필터**를 결합한 하이브리드 전략으로 운영됩니다.

---

## 전략 한눈에 보기

| 단계 | 내용 |
|------|------|
| **종목 선정** | 매일 08:40 / 09:05 두 차례, 거래대금·신고가 기준으로 50개 자동 선정 |
| **진입 조건** | 20일 또는 55일 신고가 돌파 **AND** 돌파 시각부터 30분 경과 **AND** 오전 10시 이후 — 세 조건 모두 충족 시 매수 |
| **수량 결정** | 총자본 × 리스크팩터 ÷ ATR(N) — 변동성에 맞춰 자동 계산 (1 Unit 매수금액이 총자본의 10%를 넘으면 리스크팩터 자동 조정) |
| **추가 매수** | 매수가 대비 0.5N 오를 때마다 1 Unit 추가 (최대 3 Unit, 예외 진입 시 최대 2 Unit) |
| **손절** | 최종 체결가 대비 2N 하락 시 전량 즉시 매도 |
| **익절** | 10일 신저가 경신 또는 5MA 하향 돌파 시 청산 |

---

## 진입 조건 상세

오전 9시~10시 사이의 가짜 돌파(휩소)를 최대한 차단하기 위해 세 가지 조건을 **동시에** 충족할 때만 매수합니다.

| 조건 | 내용 |
|------|------|
| ① 신고가 돌파 | 20일 신고가(S1) 또는 55일 신고가(S2) 돌파 |
| ② 30분 가드 | 돌파가 발생한 시각부터 장중 30분 이상 경과 |
| ③ 10시 이후 | 현재 시각이 오전 10:00 이상 |

**돌파 시각에 따른 실제 진입 시각 예시:**

| 돌파 시각 | 30분 도달 | 실제 진입 |
|-----------|----------|----------|
| 9시 20분 | 9시 50분 (10시 전) | **10시 00분** (10시 최소 보장) |
| 9시 40분 | 10시 10분 | **10시 10분** |
| 10시 30분 | 11시 00분 | **11시 00분** |

S1·S2 동시 해당 시 55일 신고가(S2) 신호가 우선 적용됩니다.

---

## 포지션 사이징

### 1 Unit 수량 계산

```
1 Unit 수량 = (총자본 × 리스크팩터) / ATR(N)
```

- **기본 리스크팩터**: 0.02 (총자본의 2% 위험 노출)
- **자동 조정**: 1 Unit 매수금액이 총자본의 10%를 초과하면, 리스크팩터를 자동으로 낮춰 매수금을 상한에 맞춤
  - 조정된 리스크팩터는 종목별로 `held_stock_record.json`에 저장되어 피라미딩 시에도 동일하게 적용됨

### 예외 진입

1주 가격이 총자본의 2%를 초과하는 경우:

- 1주만 매수, 피라미딩 상한 **2 Unit**으로 제한
- `LS_ALLOW_EXCEPTION_ENTRY=False`로 비활성화 가능

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
LS_PAPER_TRADING=True         # True = 모의투자, False = 실계좌
LS_ACCOUNT_NO=계좌번호
LS_ALLOW_EXCEPTION_ENTRY=True # True = 고가 종목 1주 예외 진입 허용
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
python target_manager.py              # 신고가 돌파 신호 및 돌파 시각 기록
python timer_agent.py                 # 30분 가드 + 10시 이후 진입 조건 체크
python turtle_order_logic.py          # 진입·피라미딩 주문
python risk_guardian.py               # 2N 손절 · 트레일링 스탑 감시
python chart_updater.py               # 구글 시트 손익차트 수동 갱신
```

---

## 텔레그램 봇으로 감시 종목 관리

휴대폰 텔레그램 앱에서 봇과 채팅으로 화이트리스트·블랙리스트를 즉시 관리할 수 있습니다.
24시간 systemd 데몬으로 운영되며, 매매 봇과는 완전히 독립된 별도 프로세스입니다.

### 데몬 등록 (AWS 서버에서 1회)

```bash
sudo cp deploy/ls_telegram_listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ls_telegram_listener
sudo systemctl status ls_telegram_listener      # 상태 확인
journalctl -u ls_telegram_listener -f           # 실시간 로그
```

> ⚠️ `deploy/ls_telegram_listener.service` 안의 `User=ubuntu`는 본인 AWS 사용자명에 맞게 수정 필요할 수 있습니다 (ec2-user, admin 등).

### 명령어 (휴대폰에서)

**감시 종목 관리** — 즉시 `watchlist_config.json`과 `dynamic_watchlist.json` 동시 갱신
```
/add 005930 삼성전자       — 화이트리스트 추가
/remove 005930             — 화이트리스트 제거
/block 000660 SK하이닉스   — 블랙리스트 추가 (감시 제외)
/unblock 000660            — 블랙리스트 해제
```

**조회**
```
/list                      — 화이트리스트 + 블랙리스트
/watch                     — 오늘 감시 중인 종목 (점수순)
/held                      — 보유 종목 + 평균가/수익률/손절가
/balance                   — 계좌 잔고 (총자본/예수금/손익)
/status                    — 시스템 상태 (각 파일 갱신 시각)
/help                      — 명령어 안내
```

### 안전 장치

- **권한 검증**: `.env`의 `TELEGRAM_CHAT_ID`와 일치하는 사용자만 명령 실행 가능
- **침입 알림**: 권한 없는 사용자가 명령 보내면 거부 응답 + 관리자에게 알림 (1분 dedup)
- **Atomic 쓰기**: 임시파일 → `os.replace()`로 원자적 교체 — 중간에 죽어도 파일 안 깨짐
- **Idempotent**: 같은 명령 두 번 처리해도 부작용 없음
- **데몬 안정성**: 명령 처리 중 예외 발생해도 데몬은 살아있음, systemd가 자동 재시작

---

## 파일 구성

```
ls_hybrid_turtle/
│
├── run_all.py                  — 통합 자동 실행기
│
├── [종목 선정]
│   └── stock_screener.py       — 매일 2회 배치: 거래대금·신고가 기준 50개 선정
│
├── [기반 모듈]
│   ├── ls_client.py            — LS증권 API 연결 (시세·주문·잔고)
│   ├── indicator_calc.py       — ATR, 이동평균선, 10일 신저가 계산
│   ├── trade_ledger.py         — 체결 원장 기록 + 구글 시트 자동 동기화
│   ├── telegram_alert.py       — 텔레그램 알림 발송
│   └── config.py               — 감시 종목 목록 로드
│
├── [진입 검증]
│   ├── target_manager.py       — 신고가 돌파 신호 및 돌파 시각 기록
│   └── timer_agent.py          — 30분 가드 + 10시 이후 조건 체크, 진입 신호 전달
│
├── [주문·리스크]
│   ├── turtle_order_logic.py   — 수량 계산(리스크팩터 자동 조정 포함), 피라미딩 주문
│   └── risk_guardian.py        — 2N 손절 · 트레일링 스탑 실시간 감시
│
├── [보조 모듈]
│   ├── balance_sync.py         — 실제 잔고 ↔ 보유 종목 기록 동기화
│   ├── chart_updater.py        — 구글 시트 손익차트 자동 생성
│   ├── record_daily_snapshot.py — 일별 포트폴리오 수익률 스냅샷 기록
│   ├── sector_cache.py         — 종목 테마 캐시 관리
│   └── daily_chart_cache.py    — 일봉 캐시 관리
│
├── [텔레그램 봇] (24시간 별도 데몬, 매매 봇과 독립)
│   ├── telegram_listener.py    — 데몬 메인 루프 (10초 폴링·권한 검증·라우팅)
│   ├── telegram_commands.py    — 명령어 핸들러 10개
│   ├── watchlist_writer.py     — watchlist 안전 갱신 (atomic 쓰기)
│   └── deploy/
│       └── ls_telegram_listener.service  — systemd 서비스 정의
│
├── .env.example                — 환경변수 템플릿 (이 파일을 복사해서 .env 작성)
├── requirements.txt            — 필요한 라이브러리 목록
└── ls_hybrid_turtle.md         — 전략 상세 명세서
```

### 자동으로 생성되는 데이터 파일 (git에 올리지 않음)

| 파일 | 내용 |
|------|------|
| `dynamic_watchlist.json` | 오늘 감시 대상 50개 종목 |
| `stock_candidates.json` | 08:40 배치 후보 종목 목록 |
| `held_stock_record.json` | 현재 보유 중인 종목 상태 (effective_risk_factor 포함) |
| `unheld_stock_record.json` | 미보유 종목의 신고가 신호·돌파 시각·타이머 상태 |
| `trade_ledger.json` | 전체 체결 원장 기록 |
| `sector_cache.json` | 종목별 테마 캐시 |
| `daily_chart_cache.json` | 일봉 캐시 |
| `telegram_offset.json` | 텔레그램 봇 마지막 처리 메시지 ID |

---

## 리스크 관리 요약

| 항목 | 규칙 |
|------|------|
| 단일 종목 최대 손실 | 총자본의 약 4% 이내 (2N 기준) |
| 종목당 최대 보유 | 3 Unit (기본) |
| 예외 진입 종목 (1주 가격이 총자본 2% 초과) | 피라미딩 최대 2 Unit |
| 1 Unit 최대 투자금액 | 총자본의 10% (초과 시 리스크팩터 자동 조정) |
| 포트폴리오 전체 상한 | 15 Unit |
| 동일 테마 상한 | 6 Unit |
| 주문·감시 범위 | `dynamic_watchlist.json` 등록 종목만 |

---

## 구글 시트 연동

체결이 일어날 때마다 다음 시트가 자동으로 갱신됩니다.

| 시트 | 내용 |
|------|------|
| **체결기록** | 매수·매도 내역 원장 |
| **포트폴리오 추이** | 일별 총자산·실현손익·누적수익금 변화 |
| **손익차트** | 일일 손익 막대(파란색, 왼쪽 축) + 누적 손익 선(빨간색, 오른쪽 축) 콤보 차트 |

손익차트를 수동으로 다시 그리려면:

```bash
python chart_updater.py
```

---

## 주의사항

- `.env`, `service_account.json` 파일은 절대 git에 올리지 않습니다.
- 처음 실행 시 반드시 모의투자(`LS_PAPER_TRADING=True`)로 테스트합니다.
- 감시 목록(`dynamic_watchlist.json`) 외 종목에는 주문이 실행되지 않습니다.
- 수동으로 매수한 종목은 `balance_sync.py`가 자동으로 감지해 편입하고 매도 전략(손절·익절)만 적용합니다.

---

## 기술 스택

| 항목 | 내용 |
|------|------|
| LS증권 API | `programgarden-finance` 패키지 |
| 텔레그램 알림 | `requests` 기반 Webhook |
| 시간대 | KST (`pytz`) |
| 데이터 저장 | 로컬 JSON + Google Sheets (`gspread`) |
