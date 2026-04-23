# stock_screener.py
# 동적 감시 종목 선정 모듈
#
# 매일 두 번 실행해서 감시 종목 50개를 자동으로 골라낸다.
#
#   08:40 배치: run_premarket_screening()
#     → t1463(거래대금상위) + t1442(52주 신고가 돌파)로 후보 80~120개 선별
#     → stock_candidates.json 저장
#
#   09:05 배치: run_market_open_screening()
#     → 당일 거래대금 기준으로 후보 재정렬
#     → 화이트리스트 포함, 블랙리스트 제거
#     → 최종 50개 확정 → dynamic_watchlist.json 저장
#
# 선정 기준 (D1~D10 확정값):
#   - 가격:  5,000원 이상 ~ 500,000원 이하
#   - 시총:  3,000억원 이상 (300,000백만원)
#   - 변동성: ATR / 종가 ≥ 1.5%
#   - 과열 컷: 20일 이동평균 대비 +25% 초과 종목 제외
#   - 스코어: 거래대금순위×0.4 + 신고가근접도×0.4 + 정배열×0.2

from __future__ import annotations  # 파이썬 3.9 이하에서도 dict | None 형식 허용

import json
import os
import time
from datetime import datetime

import pytz

import daily_chart_cache
import indicator_calc
import ls_client
import target_manager
from telegram_alert import SendMessage

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")

# ─────────────────────────────────────────
# 설정 상수
# ─────────────────────────────────────────

VOLUME_POOL_SIZE  = 200       # t1463에서 수집할 거래대금 상위 수
CANDIDATE_TARGET  = 100       # 장 전 후보 목표 수 (80~120 범위 목표)
WATCHLIST_SIZE    = 50        # 최종 감시 종목 수

PRICE_MIN         = 5_000     # 가격 하한 (원)
PRICE_MAX         = 500_000   # 가격 상한 (원)
CAP_MIN_BAEK      = 300_000   # 시총 하한 (300,000백만원 = 3,000억원)
ATR_RATIO_MIN     = 0.015     # ATR/종가 최소 (1.5%)
OVERHEATING_RATIO = 0.25      # 20일선 대비 이격도 상한 (25%)

SCORE_W_VOL  = 0.4   # 거래대금 순위 가중치
SCORE_W_HIGH = 0.4   # 신고가 근접도 가중치
SCORE_W_MA   = 0.2   # 정배열(5일선 > 20일선) 가중치

# 스크립트 위치 기준 절대 경로 (어느 디렉토리에서 실행해도 같은 위치에 저장)
_DIR = os.path.dirname(os.path.abspath(__file__))
CANDIDATES_PATH = os.path.join(_DIR, "stock_candidates.json")   # 08:40 배치 결과 저장 파일
WATCHLIST_PATH  = os.path.join(_DIR, "dynamic_watchlist.json")  # 09:05 배치 결과 저장 파일
CONFIG_PATH     = os.path.join(_DIR, "watchlist_config.json")   # 수동 화이트리스트/블랙리스트 설정


# ─────────────────────────────────────────
# 공개 함수 (외부에서 호출하는 진입점)
# ─────────────────────────────────────────

def run_premarket_screening():
    """08:40 배치: 장 전 후보 80~120개를 선별해서 stock_candidates.json에 저장한다.

    흐름:
      1. 거래대금 상위 200개 수집 (t1463, 전일 기준)
      2. 52주 신고가 돌파 종목 수집 (t1442)
      3. 두 풀 합집합
      4. 가격·시총 필터
      5. 종목별 지표 계산 (ATR, MA, 52주 최고가)
      6. 변동성 필터 + 과열 컷 + 스코어 계산
      7. stock_candidates.json 저장
    """
    now_str = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[screener] ▶ 장 전 후보 선별 시작 ({now_str})")

    # 1. 거래대금 상위 풀 수집 (전일 기준)
    volume_pool = _fetch_volume_pool()
    print(f"[screener] 거래대금 풀: {len(volume_pool)}개")

    # 2. 52주 신고가 돌파 풀 수집
    highprice_pool = _fetch_highprice_pool()
    print(f"[screener] 52주 신고가 풀: {len(highprice_pool)}개")

    # 3. 두 풀 합집합
    merged = _merge_pools(volume_pool, highprice_pool)
    print(f"[screener] 합집합 후: {len(merged)}개")

    # 4. 가격·시총 필터
    filtered = _apply_price_cap_filter(merged)
    print(f"[screener] 가격·시총 필터 후: {len(filtered)}개")

    if not filtered:
        msg = "⚠️ [screener] 가격·시총 필터 후 후보 없음 — 장 전 선별 중단"
        print(msg)
        SendMessage(msg)
        return

    # 5. 종목별 지표 계산 (ATR, MA 등)
    print(f"[screener] 지표 계산 중 ({len(filtered)}개)...")
    candidates = _calc_indicators(filtered)
    print(f"[screener] 지표 계산 완료: {len(candidates)}개 유효")

    # 6. 변동성 필터 + 과열 컷 + 스코어 계산 + 상위 선발
    ranked = _score_and_rank(candidates, top_n=CANDIDATE_TARGET)
    print(f"[screener] 스코어 정렬 후 후보: {len(ranked)}개")

    # 7. stock_candidates.json 저장
    _save_candidates(ranked)

    msg = f"✅ [screener] 장 전 후보 {len(ranked)}개 선별 완료 ({now_str})"
    print(msg)
    SendMessage(msg)


def run_market_open_screening():
    """09:05 배치: 후보를 당일 거래대금으로 재정렬해서 최종 50개를 확정한다.

    흐름:
      1. stock_candidates.json 로드 (없으면 run_premarket_screening 재실행)
      2. 당일 거래대금으로 순위 갱신 (t1463, 당일 기준)
      3. 스코어 재계산
      4. 화이트리스트 포함, 블랙리스트 제거
      5. 최종 50개 확정
      6. dynamic_watchlist.json 저장
    """
    now_str = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[screener] ▶ 장 초 최종 50개 확정 시작 ({now_str})")

    # 1. 후보 파일 로드
    candidates = _load_candidates()
    if candidates is None:
        # 후보 파일이 없으면 장 전 선별부터 다시 실행
        print("[screener] 후보 파일 없음 → 장 전 선별 재실행")
        run_premarket_screening()
        candidates = _load_candidates()
        if candidates is None:
            msg = "⚠️ [screener] 후보 재선별 실패 → 장 초 선별 중단"
            print(msg)
            SendMessage(msg)
            return

    # 2. 당일 거래대금으로 순위 갱신
    candidates = _refresh_volume(candidates)

    # 3. 스코어 재계산 (갱신된 volume_rank 반영)
    total = len(candidates)
    for info in candidates.values():
        info["score"] = _calc_score(info, total)

    # 4. 화이트리스트·블랙리스트 적용
    candidates = _apply_whitelist_blacklist(candidates)

    # 5. 최종 50개 확정
    watchlist = _finalize_top_n(candidates, top_n=WATCHLIST_SIZE)

    # 6. dynamic_watchlist.json 저장
    _save_watchlist(watchlist)

    # 7. 신규 종목의 목표가·기준가 즉시 초기화 (09:05 확정 직후)
    #    이미 unheld_stock_record.json에 있는 종목은 건드리지 않는다 (기존 타이머 보존)
    try:
        target_manager.initialize_unheld_record(watchlist)
    except Exception as e:
        print(f"[screener] 목표가 초기화 오류 (계속 진행): {e}")

    # 8. 일봉·240분봉 캐시 빌드 (이후 run_all.py 실행 시 API 재호출 없이 파일에서 읽음)
    try:
        daily_chart_cache.build_cache(watchlist)
    except Exception as e:
        print(f"[screener] 차트 캐시 빌드 오류 (계속 진행): {e}")

    # 텔레그램 알림: 전체 종목 목록
    all_stocks_str = "\n".join(
        f"  {i+1}. {info['name']}({code}) 점수:{info['score']:.2f}"
        for i, (code, info) in enumerate(watchlist.items())
    )
    msg = (
        f"✅ [screener] 최종 감시 종목 {len(watchlist)}개 확정 ({now_str})\n"
        f"전체 목록:\n{all_stocks_str}"
    )
    print(msg)
    SendMessage(msg)


# ─────────────────────────────────────────
# 내부 함수 — 데이터 수집
# ─────────────────────────────────────────

def _fetch_volume_pool() -> list:
    """t1463으로 거래대금 상위 종목을 수집한다 (전일 기준).

    Returns:
        [{"code": ..., "name": ..., "price": ..., "value": ...,
          "jnil_value": ..., "total_cap": ..., "volume_rank": ...,
          "source": "volume"}, ...]
    """
    raw = ls_client.get_trading_value_ranking(n=VOLUME_POOL_SIZE, prev_day=True)
    # "출처" 태그를 붙여서 반환
    for item in raw:
        item["source"] = "volume"
    return raw


def _fetch_highprice_pool() -> list:
    """t1442으로 52주 신고가 돌파 종목을 수집한다.

    Returns:
        [{"code": ..., "name": ..., "price": ..., "prev_high": ...,
          "source": "highprice"}, ...]
    """
    raw = ls_client.get_52week_high_stocks()
    for item in raw:
        item["source"] = "highprice"
        # t1442 응답에는 거래대금·시총이 없으므로 0으로 초기화
        item.setdefault("value", 0)
        item.setdefault("jnil_value", 0)
        item.setdefault("total_cap", 0)
        item.setdefault("volume_rank", 9999)  # 거래대금 순위 미확정
    return raw


def _merge_pools(volume_pool: list, highprice_pool: list) -> dict:
    """두 풀을 합치고 중복 종목은 정보를 병합한다.

    두 풀에 모두 등장하는 종목은 source를 "both"로 표시하고
    거래대금·시총은 volume_pool 값을 우선 사용한다.

    Returns:
        {종목코드: {name, price, value, jnil_value, total_cap,
                   volume_rank, source, prev_high(있으면)}, ...}
    """
    merged: dict = {}

    # volume_pool 먼저 등록
    for item in volume_pool:
        code = item["code"]
        merged[code] = dict(item)

    # highprice_pool 병합 (중복이면 source만 "both"로 변경, 나머지는 유지)
    for item in highprice_pool:
        code = item["code"]
        if code in merged:
            # 이미 있는 종목: source 업데이트 + prev_high 추가
            merged[code]["source"]    = "both"
            merged[code]["prev_high"] = item.get("prev_high", 0)
        else:
            merged[code] = dict(item)

    return merged


# ─────────────────────────────────────────
# 내부 함수 — 필터
# ─────────────────────────────────────────

def _apply_price_cap_filter(pool: dict) -> dict:
    """가격 범위와 시가총액 하한 필터를 적용한다.

    t1442 출신(시총 정보 없음) 종목은 가격 필터만 적용한다.

    탈락 조건:
      - 현재가 < 5,000원 또는 > 500,000원
      - 시총 정보가 있고 < 300,000백만원 (3,000억원 미만)
      - 종목명에 '리츠' 또는 '인프라' 포함 (REITs — API 비트마스크로 걸러지지 않음)
    """
    # 이름 기반으로 제외할 키워드 (리츠·인프라 펀드)
    _EXCLUDE_KEYWORDS = ("리츠", "인프라")

    result = {}
    for code, info in pool.items():
        price     = info.get("price", 0)
        total_cap = info.get("total_cap", 0)
        name      = info.get("name", "")

        # 이름 필터: 리츠·인프라 종목 제외 (t1463/t1442 bitmask 미지원)
        if any(kw in name for kw in _EXCLUDE_KEYWORDS):
            print(f"[screener] {name}({code}) 리츠/인프라 → 제외")
            continue

        # 가격 범위 체크
        if price < PRICE_MIN or price > PRICE_MAX:
            continue

        # 시총 체크 (volume 풀 출신이고 시총 정보가 있을 때만)
        if info.get("source") != "highprice" and total_cap > 0:
            if total_cap < CAP_MIN_BAEK:
                continue

        result[code] = info

    return result


# ─────────────────────────────────────────
# 내부 함수 — 지표 계산
# ─────────────────────────────────────────

def _calc_indicators(pool: dict) -> dict:
    """풀에 있는 종목 각각에 대해 ATR·MA·52주 최고가를 계산해서 추가한다.

    ATR 계산에 실패하거나, 변동성(ATR/종가)이 1.5% 미만이거나,
    20일선 대비 이격도가 25% 초과이면 해당 종목을 탈락시킨다.

    Returns:
        지표가 추가되고 필터를 통과한 종목만 담은 딕셔너리
    """
    result = {}
    total  = len(pool)

    for i, (code, info) in enumerate(pool.items(), 1):
        if i % 20 == 0 or i == total:
            print(f"[screener]   지표 계산 {i}/{total}...")

        time.sleep(1.5)  # 종목별 t8451 API 연속 호출 제한 방지 (0.5 → 1.5초로 증가)
        indicators = indicator_calc.get_screener_indicators(code)

        atr       = indicators["atr"]
        atr_ratio = indicators["atr_ratio"]
        ma5       = indicators["ma5"]
        ma20      = indicators["ma20"]
        high_52w  = indicators["high_52w"]

        # ATR 계산 실패 → 탈락
        if atr == 0.0:
            continue

        # 변동성 필터: ATR/종가 < 1.5% → 탈락
        if atr_ratio < ATR_RATIO_MIN:
            continue

        # 과열 컷: 현재가가 20일선 대비 25% 초과 → 탈락
        price = info.get("price", 0)
        if ma20 > 0 and price > ma20 * (1 + OVERHEATING_RATIO):
            continue

        # t1442 출신 종목은 prev_high(직전 52주 최고가)가 더 정확 → 덮어쓰기
        if info.get("prev_high", 0) > 0:
            high_52w = info["prev_high"]

        # 지표 저장
        info["atr"]       = atr
        info["atr_ratio"] = atr_ratio
        info["ma5"]       = ma5
        info["ma20"]      = ma20
        info["high_52w"]  = high_52w

        result[code] = info

    return result


# ─────────────────────────────────────────
# 내부 함수 — 스코어 계산 및 선발
# ─────────────────────────────────────────

def _calc_score(info: dict, total_count: int) -> float:
    """종목 하나의 종합 점수를 계산한다 (0.0 ~ 1.0, 높을수록 좋음).

    공식:
      거래대금 순위 점수 = 1 - (volume_rank / total_count)
                          ← 1위면 1.0, 꼴찌면 0.0 에 가까움
      신고가 근접도 점수 = price / high_52w  (1.0에 가까울수록 신고가 근접)
      정배열 점수        = 1 if ma5 > ma20 else 0

      종합 = 0.4×거래대금순위 + 0.4×신고가근접도 + 0.2×정배열
    """
    volume_rank  = info.get("volume_rank", 9999)
    price        = info.get("price", 1)
    high_52w     = info.get("high_52w", price) or price
    ma5          = info.get("ma5", 0.0)
    ma20         = info.get("ma20", 0.0)

    # 거래대금 순위 점수 (9999처럼 매우 큰 값이면 0에 가깝게)
    rank_score   = max(0.0, 1.0 - (volume_rank / max(total_count, 1)))
    # 신고가 근접도 점수
    high_score   = min(price / high_52w, 1.0) if high_52w > 0 else 0.0
    # 정배열 점수
    ma_score     = 1.0 if (ma5 > 0 and ma20 > 0 and ma5 > ma20) else 0.0

    return SCORE_W_VOL * rank_score + SCORE_W_HIGH * high_score + SCORE_W_MA * ma_score


def _score_and_rank(pool: dict, top_n: int) -> dict:
    """풀 전체에 스코어를 계산하고 상위 top_n개만 남긴다."""
    total = len(pool)

    # 각 종목에 스코어 계산
    for info in pool.values():
        info["score"] = _calc_score(info, total)

    # 스코어 내림차순 정렬 후 상위 top_n개 선발
    sorted_items = sorted(
        pool.items(),
        key=lambda x: x[1]["score"],
        reverse=True
    )

    return dict(sorted_items[:top_n])


# ─────────────────────────────────────────
# 내부 함수 — 화이트리스트·블랙리스트
# ─────────────────────────────────────────

def _load_watchlist_config() -> dict:
    """watchlist_config.json을 로드한다. 파일이 없으면 빈 설정을 반환한다.

    Returns:
        {"whitelist": [...], "blacklist": [...]}
    """
    if not os.path.exists(CONFIG_PATH):
        return {"whitelist": [], "blacklist": []}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return {
            "whitelist": cfg.get("whitelist", []),
            "blacklist": cfg.get("blacklist", []),
        }
    except Exception as e:
        print(f"[screener] watchlist_config.json 읽기 오류: {e}")
        return {"whitelist": [], "blacklist": []}


def _apply_whitelist_blacklist(candidates: dict) -> dict:
    """화이트리스트 종목을 강제 포함하고, 블랙리스트 종목을 제거한다.

    화이트리스트 종목이 candidates에 없으면 기본 정보로 추가한다.
    블랙리스트 종목은 화이트리스트보다 우선 제거한다.
    """
    cfg = _load_watchlist_config()

    # whitelist/blacklist 항목이 문자열("005930")일 수도 있고
    # 딕셔너리({"code": "005930"})일 수도 있어서 두 형식 모두 처리한다
    def _to_item(entry):
        """문자열이면 {"code": ...} 딕셔너리로 변환, 딕셔너리면 그대로 반환"""
        if isinstance(entry, str):
            return {"code": entry}
        return entry

    whitelist = {_to_item(entry)["code"]: _to_item(entry) for entry in cfg["whitelist"]}
    blacklist = {_to_item(entry)["code"] for entry in cfg["blacklist"]}

    # 블랙리스트 제거
    result = {code: info for code, info in candidates.items() if code not in blacklist}

    # 화이트리스트 강제 추가 (블랙리스트에 없는 것만)
    for code, witem in whitelist.items():
        if code in blacklist:
            continue  # 블랙리스트 우선
        if code not in result:
            # candidates에 없으면 기본 정보로 추가 (score=1.0으로 최상위 보장)
            result[code] = {
                "name":        witem.get("name", code),
                "market":      witem.get("market", "KOSPI"),
                "price":       0,
                "volume_rank": 0,
                "value":       0,
                "jnil_value":  0,
                "total_cap":   0,
                "atr":         0.0,
                "atr_ratio":   0.0,
                "ma5":         0.0,
                "ma20":        0.0,
                "high_52w":    0,
                "score":       1.0,   # 최상위 점수로 강제 포함
                "source":      "whitelist",
            }
            print(f"[screener] 화이트리스트 강제 추가: {witem.get('name', code)}({code})")

    return result


# ─────────────────────────────────────────
# 내부 함수 — 당일 거래대금 갱신
# ─────────────────────────────────────────

def _refresh_volume(candidates: dict) -> dict:
    """당일 거래대금 순위를 t1463(당일 기준)으로 새로 받아서 candidates를 갱신한다.

    당일 200위 밖으로 밀린 종목은 volume_rank를 9999로 강등한다.
    당일 신규 진입 종목(전일 후보에 없던 종목)은 소량 지표를 계산해 추가한다.
    """
    today_pool = ls_client.get_trading_value_ranking(n=VOLUME_POOL_SIZE, prev_day=False)
    today_rank = {item["code"]: item for item in today_pool}

    updated = {}
    for code, info in candidates.items():
        if code in today_rank:
            # 당일 거래대금 정보로 갱신
            t = today_rank[code]
            info["value"]       = t["value"]
            info["volume_rank"] = t["volume_rank"]
        else:
            # 당일 200위 밖으로 밀림 → 순위 강등
            info["volume_rank"] = 9999
        updated[code] = info

    # 당일 신규 진입 종목 추가 (소량: 최대 20개)
    added = 0
    for code, t_item in today_rank.items():
        if code in updated or added >= 20:
            continue
        # 가격 필터 먼저
        if t_item["price"] < PRICE_MIN or t_item["price"] > PRICE_MAX:
            continue
        if t_item["total_cap"] > 0 and t_item["total_cap"] < CAP_MIN_BAEK:
            continue

        # 지표 계산
        ind = indicator_calc.get_screener_indicators(code)
        if ind["atr"] == 0.0 or ind["atr_ratio"] < ATR_RATIO_MIN:
            continue
        price = t_item["price"]
        if ind["ma20"] > 0 and price > ind["ma20"] * (1 + OVERHEATING_RATIO):
            continue

        t_item.update({
            "atr":       ind["atr"],
            "atr_ratio": ind["atr_ratio"],
            "ma5":       ind["ma5"],
            "ma20":      ind["ma20"],
            "high_52w":  ind["high_52w"],
            "score":     0.0,
            "source":    "volume",
        })
        updated[code] = t_item
        added += 1

    if added > 0:
        print(f"[screener] 당일 신규 진입 종목 {added}개 추가")

    return updated


# ─────────────────────────────────────────
# 내부 함수 — 최종 선발
# ─────────────────────────────────────────

def _finalize_top_n(candidates: dict, top_n: int) -> dict:
    """스코어 내림차순으로 최종 top_n개를 선발한다.

    화이트리스트(score=1.0) 종목이 먼저 들어오고, 나머지는 스코어 순으로 채운다.
    """
    sorted_items = sorted(
        candidates.items(),
        key=lambda x: x[1]["score"],
        reverse=True
    )
    return dict(sorted_items[:top_n])


# ─────────────────────────────────────────
# 내부 함수 — 파일 저장·로드
# ─────────────────────────────────────────

def _save_candidates(candidates: dict):
    """후보 종목을 stock_candidates.json에 저장한다."""
    today_str = datetime.now(_KST).strftime("%Y%m%d")
    data = {
        "updated_at": datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S"),
        "date":       today_str,
        "count":      len(candidates),
        "candidates": candidates,
    }
    try:
        with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[screener] stock_candidates.json 저장 완료 ({len(candidates)}개)")
    except Exception as e:
        print(f"[screener] stock_candidates.json 저장 오류: {e}")


def _load_candidates() -> dict | None:
    """stock_candidates.json을 로드한다.

    오늘 날짜 파일이 아니거나 읽기 오류 시 None을 반환한다.
    """
    if not os.path.exists(CANDIDATES_PATH):
        return None

    try:
        with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 오늘 날짜 확인
        today_str = datetime.now(_KST).strftime("%Y%m%d")
        if data.get("date") != today_str:
            print(f"[screener] stock_candidates.json 날짜 불일치 (파일:{data.get('date')}, 오늘:{today_str})")
            return None

        return data.get("candidates", {})

    except Exception as e:
        print(f"[screener] stock_candidates.json 로드 오류: {e}")
        return None


def _save_watchlist(watchlist: dict):
    """최종 50개 감시 종목을 dynamic_watchlist.json에 저장한다.

    저장 형식은 기존 lovely_stock_list와 호환되도록
    {"종목코드": {"name": ..., "market": ..., "score": ..., "atr": ...}} 구조를 사용한다.
    """
    today_str = datetime.now(_KST).strftime("%Y%m%d")

    # 기존 매매 모듈과 호환되는 형식으로 변환
    stocks = {}
    for code, info in watchlist.items():
        # market 필드가 없으면 총자본 기준 KOSPI/KOSDAQ 추정 생략 → 기본값 "KOSPI"
        stocks[code] = {
            "name":   info.get("name", code),
            "market": info.get("market", "KOSPI"),
            "score":  round(info.get("score", 0.0), 4),
            "atr":    round(info.get("atr", 0.0), 2),
        }

    data = {
        "updated_at": datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S"),
        "date":       today_str,
        "count":      len(stocks),
        "stocks":     stocks,
    }
    try:
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[screener] dynamic_watchlist.json 저장 완료 ({len(stocks)}개)")
    except Exception as e:
        print(f"[screener] dynamic_watchlist.json 저장 오류: {e}")


# ─────────────────────────────────────────
# 단독 실행 지원 (테스트용)
# ─────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # 명령줄 인수로 실행할 배치를 선택한다
    # 사용법:
    #   python stock_screener.py premarket    → 08:40 배치 (후보 선별)
    #   python stock_screener.py market_open  → 09:05 배치 (최종 50개)
    #   python stock_screener.py              → 기본: premarket 실행

    if not ls_client.login():
        print("[screener] 로그인 실패 → 종료")
        sys.exit(1)

    mode = sys.argv[1] if len(sys.argv) > 1 else "premarket"

    if mode == "market_open":
        run_market_open_screening()
    else:
        run_premarket_screening()
