# Claude Code — `ls_hybrid_turtle` 진입 명세 (`CLAUDE.md`)

**Claude Code 세션은 본 파일만으로 시작한다.**

**이 프로젝트가 다루는 것:** LS증권 API를 이용한 **국내 상장 주식(KRX) 자동매매 시스템**.  
매매 전략: **터틀 트레이딩(자금 관리) + 동적 목표가(진입 검증) 하이브리드** 전략.  
**상세 전략 명세:** `ls_hybrid_turtle.md` 참고.

---

## Claude Code 협업 규칙

- **사용자는 코딩을 전혀 모르는 왕초보**다. 설명할 때는 전문 용어를 피하고, 일상적인 말로 쉽게 풀어서 설명한다.
- **모든 코드에 한글 주석을 달아야 한다.** 함수·변수·로직 단위로 "이 코드가 무엇을 하는지"를 한글로 설명한다.
- 오류 메시지나 결과를 보여줄 때도 한글로 해석해서 전달한다.

---

## 기술 스택

| 항목 | 내용 |
|------|------|
| **LS증권 API 라이브러리** | `programgarden-finance` (LS OpenAPI 래퍼 패키지) |
| **알림** | 텔레그램 봇 (`python-telegram-bot` 또는 `requests` 기반 Webhook) |
| **시간대** | KST (`pytz`) |
| **데이터 저장** | 로컬 JSON + Google Sheets (`gspread`) |

> `ls_client.py`는 `programgarden-finance` 패키지를 내부적으로 사용해 LS증권 API에 접근한다.  
> 전략 파일에서 `programgarden-finance` API를 직접 호출하지 말고, 반드시 `ls_client.py`를 경유한다.

---

## 핵심 아키텍처 (한눈에)

```
(run_all.py 또는 각 모듈 개별 실행)
├── [SA-FOUNDATION]
│   ├── ls_client.py         — LS OpenAPI 래퍼 (URL 하드코딩 금지)
│   ├── indicator_calc.py    — ATR(N), 이동평균선(20MA, 5MA), 10일 신저가
│   ├── trade_ledger.py      — append_trade(record) 단일 진입점 + Google Sheets
│   ├── telegram_alert.py    — SendMessage(msg) 단일 진입점
│   └── config.py            — lovely_stock_list 상수 정의
├── [SA-MODULE-ENTRY]
│   ├── target_manager.py    — 동적 목표가 산출, unheld_stock_record.json 관리
│   └── timer_agent.py       — 30분 안착 검증 타이머
└── [SA-MODULE-TRADE]
    ├── turtle_order_logic.py — 수량 계산, 피라미딩 주문 실행
    └── risk_guardian.py      — 2N 손절 + 트레일링 스탑 모니터링
```

## 전체 파일 목록

| 파일 | 역할 |
|------|------|
| `ls_client.py` | LS OpenAPI 래퍼 (토큰 관리, 시세·주문·잔고) |
| `indicator_calc.py` | ATR(N), 20MA, 5MA, 10일 신저가 지표 계산 |
| `trade_ledger.py` | 체결 원장 기록 + Google Sheets 동기화 |
| `telegram_alert.py` | 텔레그램 알림 단일 모듈 |
| `config.py` | `lovely_stock_list` 상수 (종목코드·시장) |
| `target_manager.py` | 동적 목표가(`pending_target`) 산출 및 미보유 종목 상태 관리 |
| `timer_agent.py` | 30분 가드 타이머 (가짜 돌파 필터) |
| `turtle_order_logic.py` | 리스크 기반 Unit 수량 계산, 피라미딩 주문, 예외 진입 처리 |
| `risk_guardian.py` | 2N 하드 손절 및 트레일링 스탑 실시간 감시 |
| `run_all.py` | 통합 배치 실행기 — 장 시간 체크 후 모든 모듈을 올바른 순서로 실행 |
| `test_dummy_trade.py` | 더미 체결 기록 테스트 스크립트 (개발·검증 전용, 실계좌 무관) |
| `.env` | API 키·계좌·텔레그램·Google 설정 (커밋 금지) |
| `.env.example` | 환경변수 템플릿 (`.env` 작성 참고용) |
| `requirements.txt` | 의존성 목록 (`programgarden-finance` 포함) |
| `.gitignore` | 민감 파일·런타임 JSON 제외 규칙 |

**런타임 중 자동 생성되는 JSON 파일 (커밋 금지):**
| 파일 | 내용 |
|------|------|
| `unheld_stock_record.json` | 미보유 종목의 동적 목표가 및 30분 가드 타이머 상태 |
| `position_state.json` | 보유 종목의 Unit 수·마지막 매수가·손절가·피라미딩 트리거가 |
| `trade_ledger.json` | 체결 원장 전체 기록 |

**서브에이전트 실행 순서 (구현 시):**  
SA-FOUNDATION 완료 → SA-MODULE-ENTRY · SA-MODULE-TRADE 병렬

---

## 관심 종목 리스트 (`lovely_stock_list`)

진입·감시·주문 대상은 **`lovely_stock_list`에 포함된 종목만**으로 한정한다.  
리스트 밖 종목은 주문·상태 변경을 하지 않는다.

| 종목명 | 종목코드 | 시장 |
|--------|---------|------|
| 삼성전자 | 005930 | KOSPI |
| 두산에너빌리티 | 034020 | KOSPI |
| 현대로템 | 064350 | KOSPI |
| 케이뱅크 | 279570 | KOSPI |
| 하이브 | 352820 | KOSDAQ |
| LG에너지솔루션 | 373220 | KOSPI |
| 두산로보틱스 | 454910 | KOSDAQ |

> 구현·실행 전 **종목코드·상장 여부**를 반드시 재확인한다 (상장 폐지·코드 변경 가능).

---

## 핵심 전략 요약

### 진입 (Entry)
- **동적 목표가(`pending_target`)**: `max(현재가 × 1.02, 240분봉 20MA × 1.005)`
- **30분 가드**: 현재가가 `pending_target` 이상에서 30분 유지 시 1차 Unit 매수

### 포지션 사이징 및 피라미딩
- **N(ATR)**: 최근 20일 True Range 평균, 매일 갱신
- **1 Unit 수량**: `(총 자본 × 0.02) / (N × 1주 가격)`
  - 1주 가격 > 총 자본 × 0.02 → 매수 스킵 (기본)
  - 예외: 1주 가격이 총 자본 2%~5% 이내 → 1주 매수, 피라미딩 상한 **2 Unit**
- **피라미딩**: 마지막 매수가 대비 0.5N 상승 시마다 1 Unit 추가 (기본 상한 **4 Unit**)

### 청산 및 손절
- **하드 손절(2N Stop)**: 최종 체결가 대비 2N 하락 시 전량 즉시 매도
- **트레일링 스탑**: 10일 신저가 경신 또는 5MA 하향 돌파 시 익절 청산

---

## 공통 계약 (모든 모듈 준수)

- **종목 키:** 6자리 종목코드 전역 통일
- **시간:** 사용자-facing은 **KST** (`pytz`)
- **LS 접근:** `ls_client`만 경유 — 전략 파일에 LS URL 직접 기록 금지
- **체결 원장:** `trade_ledger.append_trade(record)` 단일 진입점  
  `source` ∈ `TURTLE_ENTRY` | `TURTLE_PYRAMID` | `TURTLE_EXIT` | `MANUAL_SYNC`
- **알림:** 텔레그램 봇 — `telegram_alert.SendMessage(msg)` 단일 모듈 경유
- **보안:** 키·계좌·서비스계정 JSON 커밋 금지 (`.env`, `.gitignore`)

---

## 체결 원장 스키마 (`trade_ledger.append_trade` record 필드)

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `record_id` | string | 중복 방지 고유 ID |
| `ts_kst` | string | `YYYY-MM-DD HH:MM:SS` (KST) |
| `ts_unix` | number | (선택) |
| `account_id` | string | 계좌 별칭/마스킹 |
| `side` | string | `BUY` / `SELL` |
| `stock_code` | string | 6자리 |
| `stock_name` | string | (선택) |
| `order_no` | string | LS 주문번호 |
| `exec_no` | string | (선택) 체결번호 |
| `qty` | number | 주 |
| `unit_price` | number | 원 |
| `gross_amount` | number | 단가×수량 (세전·수수료 전) |
| `fee` | number | (선택) |
| `net_amount` | number | (선택) 현금 증감 |
| `order_type` | string | `MARKET` / `LIMIT` |
| `source` | string | 위 4가지 중 하나 |
| `note` | string | (선택) Unit 차수·손절/익절 구분 |

`trade_ledger` 스키마 변경 시 이 표와 전략 명세서를 함께 수정한다.

---

## 금지 사항

- 전략 파일에 시세/주문 URL 직접 기록
- `record_id` 없이 원장 무한 증식
- 비밀·전체 계좌번호를 로그·텔레그램·커밋에 노출
- `lovely_stock_list` 외 종목에 주문·상태 변경 수행
- Foundation 변경과 대량 전략 변경을 한 PR에 혼재

---

## 실행 (배치 예시)

```bash
cd ls_hybrid_turtle
python target_manager.py     # 동적 목표가 갱신
python timer_agent.py        # 30분 가드 체크
python turtle_order_logic.py # 진입·피라미딩 주문
python risk_guardian.py      # 손절·익절 감시
# 또는
python run_all.py
```

---

**구현 상세(진입 수식·수량 계산·손절 로직·JSON 필드)는 모두 `ls_hybrid_turtle.md`에 있다.**

---

> 마지막 업데이트: 2026-04-13 (전체 구현 완료 — run_all.py·test_dummy_trade.py 추가, 런타임 JSON 파일 목록 추가)
