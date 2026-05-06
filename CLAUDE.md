# Claude Code — `ls_hybrid_turtle` 진입 명세 (`CLAUDE.md`)

**Claude Code 세션은 본 파일만으로 시작한다.**

**이 프로젝트가 다루는 것:** LS증권 API를 이용한 **국내 상장 주식(KRX) 자동매매 시스템**.  
매매 전략: **오리지널 터틀 트레이딩** (신고가 돌파 + 30분 가드 + 10시 이후 진입).  
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
├── [SA-SCREENER]
│   └── stock_screener.py    — 동적 감시 종목 선정 (08:40 / 09:05 배치)
├── [SA-FOUNDATION]
│   ├── ls_client.py         — LS OpenAPI 래퍼 (URL 하드코딩 금지)
│   ├── indicator_calc.py    — ATR(N), 이동평균선(20MA, 5MA), 10일 신저가
│   ├── trade_ledger.py      — append_trade(record) 단일 진입점 + Google Sheets
│   ├── telegram_alert.py    — SendMessage(msg) 단일 진입점
│   └── config.py            — get_watchlist() (dynamic_watchlist.json 경유)
├── [SA-MODULE-ENTRY]
│   ├── target_manager.py    — 미보유 종목 터틀 신호 갱신, unheld_stock_record.json 관리
│   └── timer_agent.py       — 30분 안착 검증 타이머
└── [SA-MODULE-TRADE]
    ├── turtle_order_logic.py — 수량 계산, 피라미딩 주문 실행
    └── risk_guardian.py      — 2N 손절 + 트레일링 스탑 모니터링
```

## 전체 파일 목록

| 파일 | 역할 |
|------|------|
| `stock_screener.py` | 동적 감시 종목 선정 — t1463·t1442 API로 매일 50개 자동 선정 |
| `ls_client.py` | LS OpenAPI 래퍼 (토큰 관리, 시세·주문·잔고) |
| `indicator_calc.py` | ATR(N), 20MA, 5MA, 10일 신저가 지표 계산 |
| `trade_ledger.py` | 체결 원장 기록 + Google Sheets 동기화 — 매도 체결 시 '포트폴리오 추이'·'손익차트' 시트 자동 갱신 |
| `telegram_alert.py` | 텔레그램 알림 단일 모듈 |
| `config.py` | `get_watchlist()` — `dynamic_watchlist.json`을 읽어 감시 종목 반환 |
| `target_manager.py` | 미보유 종목 터틀 신호(`turtle_s1/s2_signal`) 및 돌파 시각(`turtle_s1/s2_breakout_since`) 갱신 |
| `timer_agent.py` | 30분 가드 타이머 (가짜 돌파 필터) |
| `turtle_order_logic.py` | 리스크 기반 Unit 수량 계산, 피라미딩 주문, 예외 진입 처리 |
| `risk_guardian.py` | 2N 하드 손절 및 트레일링 스탑 실시간 감시 |
| `balance_sync.py` | 실행 시작 시 실제 잔고 ↔ held_stock_record.json 동기화 — 수동 매수 종목 발견 시 1회 알림 후 자동 편입 (매도 전략만 감시) |
| `chart_updater.py` | 구글 시트 "포트폴리오 추이" 데이터로 "손익차트" 탭에 콤보 차트(일일 막대 + 누적 선) 자동 생성 |
| `sector_cache.py` | 종목별 테마 캐시 관리 (t1532 API, sector_cache.json) |
| `daily_chart_cache.py` | 일봉 캐시 관리 — 09:05 1회 빌드 후 당일 재사용 |
| `run_all.py` | 통합 배치 실행기 — 장 시간 체크 후 모든 모듈을 올바른 순서로 실행 |
| `test_dummy_trade.py` | 더미 체결 기록 테스트 스크립트 (개발·검증 전용, 실계좌 무관) |
| `.env` | API 키·계좌·텔레그램·Google 설정 (커밋 금지) |
| `.env.example` | 환경변수 템플릿 (`.env` 작성 참고용) |
| `requirements.txt` | 의존성 목록 (`programgarden-finance` 포함) |
| `.gitignore` | 민감 파일·런타임 JSON 제외 규칙 |

**런타임 중 자동 생성되는 JSON 파일 (커밋 금지):**
| 파일 | 내용 |
|------|------|
| `stock_candidates.json` | 08:40 배치 후보 종목 (스코어·지표 포함) |
| `dynamic_watchlist.json` | 09:05 배치 최종 감시 종목 50개 — 모든 모듈이 이 파일을 참조 |
| `watchlist_config.json` | 수동 화이트리스트/블랙리스트 (없으면 자동 선정만 사용) |
| `unheld_stock_record.json` | 미보유 종목의 터틀 신호(`turtle_s1/s2_signal`)·돌파 시각(`turtle_s1/s2_breakout_since`) |
| `held_stock_record.json` | 보유 종목의 Unit 수·마지막 매수가·손절가·피라미딩 트리거가·종목별 유효 리스크팩터(`effective_risk_factor`) |
| `trade_ledger.json` | 체결 원장 전체 기록 |
| `sector_cache.json` | 종목별 테마 캐시 (t1532 API 결과) |
| `daily_chart_cache.json` | 일봉(60개) 캐시 — 09:05 market_open 직후 생성 |

**서브에이전트 실행 순서 (구현 시):**  
SA-SCREENER 완료 → SA-FOUNDATION 완료 → SA-MODULE-ENTRY · SA-MODULE-TRADE 병렬

---

## 감시 종목 선정 방식

진입·감시·주문 대상은 **`dynamic_watchlist.json`에 저장된 종목만**으로 한정한다.  
리스트 밖 종목은 주문·상태 변경을 하지 않는다.

**자동 선정 (매일 2회 배치):**
- **08:40 배치**: t1463(거래대금 상위 200개) + t1442(52주 신고가 돌파) → 가격·시총·변동성 필터 → 스코어 계산 → `stock_candidates.json` 저장
- **09:05 배치**: 당일 거래대금으로 재정렬 → 최종 50개 확정 → `dynamic_watchlist.json` 저장

**수동 조정 (`watchlist_config.json`):**
```json
{
  "whitelist": ["005930", "034020"],
  "blacklist": ["000000"]
}
```
- `whitelist`: 자동 선정 결과와 무관하게 강제 포함 (score=1.0)
- `blacklist`: 자동 선정됐더라도 강제 제외
- 파일이 없으면 자동 선정 결과만 사용

**자동 제외 조건 (API 비트마스크 + 코드 후처리):**
- ETF·ETN·관리종목·투자경고·투자위험·우선주 → API 파라미터로 제거
- 리츠(REITs)·인프라펀드 → 종목명 키워드 후처리로 제거

---

## 핵심 전략 요약

### 진입 (Entry)
- **터틀 신고가 돌파**: 20일 신고가(S1) 또는 55일 신고가(S2) 돌파 시 진입 후보
- **30분 가드**: 돌파 발생 시각(`turtle_s1/s2_breakout_since`)으로부터 장중 30분 이상 경과 **AND** 현재 시각 10:00 이상 → 1차 Unit 매수
  - 9시대 돌파도 시각은 즉시 기록되며, 10:00 도달 시 이미 30분 경과면 즉시 진입
  - S1·S2 동시 해당 시 TURTLE_S2 우선 (55일 > 20일)

### 포지션 사이징 및 피라미딩
- **N(ATR)**: 최근 20일 True Range 평균, 매일 갱신
- **1 Unit 수량**: `(총 자본 × effective_risk_factor) / N`
  - 기본 `effective_risk_factor = 0.02` (총자본의 2% 리스크 노출)
  - 1 Unit 매수금이 총자본 × 10% 초과 시: `effective_risk_factor`를 자동으로 낮춰 매수금을 상한에 맞춤 → 종목마다 다른 리스크팩터 적용, `held_stock_record.json`에 저장
  - 예외: 1주 가격이 총 자본 2% 초과 → 1주 매수, 피라미딩 상한 **2 Unit**, `effective_risk_factor = None`
- **피라미딩**: 마지막 매수가 대비 0.5N 상승 시마다 1 Unit 추가 (기본 상한 **3 Unit**)
  - 피라미딩 수량은 진입 시 저장한 `effective_risk_factor` 재사용 (종목별 고정)
- **포트폴리오 상한**: 전체 **15 Unit**, 동일 테마 **6 Unit**

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
| `net_amount` | number | SELL 시 실수령금액 (수수료 미반영 근사값 = gross_amount) |
| `order_type` | string | `MARKET` / `LIMIT` |
| `source` | string | 위 4가지 중 하나 |
| `profit_rate` | number | (SELL 전용) 수익률 (%) — (매도가-평균매입가)/평균매입가×100 |
| `profit_amount` | number | (SELL 전용) 수익금 (원) — (매도가-평균매입가)×수량 |
| `note` | string | (선택) Unit 차수·손절/익절 구분 |

`trade_ledger` 스키마 변경 시 이 표와 전략 명세서를 함께 수정한다.

---

## 금지 사항

- 전략 파일에 시세/주문 URL 직접 기록
- `record_id` 없이 원장 무한 증식
- 비밀·전체 계좌번호를 로그·텔레그램·커밋에 노출
- `dynamic_watchlist.json` 외 종목에 주문·상태 변경 수행
- Foundation 변경과 대량 전략 변경을 한 PR에 혼재

---

## 실행 (배치 예시)

```bash
cd ls_hybrid_turtle
python stock_screener.py premarket    # 08:40 — 후보 선별 (stock_candidates.json)
python stock_screener.py market_open  # 09:05 — 최종 50개 확정 (dynamic_watchlist.json)
python target_manager.py              # 미보유 종목 터틀 신호 갱신
python timer_agent.py                 # 30분 가드 체크
python turtle_order_logic.py          # 진입·피라미딩 주문
python risk_guardian.py               # 손절·익절 감시
# 또는
python run_all.py                     # 모든 단계 자동 실행 (스크리너 포함)
```

---

**구현 상세(진입 수식·수량 계산·손절 로직·JSON 필드)는 모두 `ls_hybrid_turtle.md`에 있다.**

---

> 마지막 업데이트: 2026-05-06 (240분봉·동적 목표가(pending_target) 제거 — 매매에 안 쓰이는 죽은 코드 정리)

