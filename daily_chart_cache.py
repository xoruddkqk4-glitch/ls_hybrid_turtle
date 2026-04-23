# daily_chart_cache.py
# 일봉·240분봉 캐시 관리 모듈
#
# 역할:
#   1. 09:05 market_open 직후 build_cache() 로 감시 종목 전체의 일봉·240분봉을 한 번에 저장
#   2. 이후 모든 모듈이 get_daily_cached() / get_minute240_cached() 로 파일에서 꺼내 씀
#   3. 240분봉은 30분 TTL — 30분 경과 시 빈 리스트 반환 → 호출자가 API 재조회 후 update_minute240_cache() 호출
#   4. 캐시 미존재·오류 시 빈 리스트 반환 → 호출자에서 API 직접 조회(폴백) 처리
#
# 저장 파일: daily_chart_cache.json
# JSON 구조:
# {
#   "005930": {
#     "daily":                [...],   ← 일봉 60개 (OHLCV 리스트, 오름차순)
#     "daily_date":           "2026-04-16",
#     "minute240":            [...],   ← 240분봉 25개
#     "minute240_fetched_at": "2026-04-16 09:05:30"
#   }
# }

import json
import os
import time
from datetime import datetime

import pytz

import ls_client

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")

# 캐시 파일 경로 (스크립트 폴더 기준 절대 경로)
_DIR        = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_DIR, "daily_chart_cache.json")

# 240분봉 유효 시간: 30분 (초 단위)
_MINUTE240_TTL_SEC = 30 * 60


# ─────────────────────────────────────────
# 내부 헬퍼 — 파일 읽기·쓰기
# ─────────────────────────────────────────

def _load_cache() -> dict:
    """캐시 파일을 읽어서 딕셔너리로 반환한다. 파일이 없거나 오류 시 빈 딕셔너리 반환."""
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[chart_cache] 캐시 파일 읽기 오류: {e}")
        return {}


def _save_cache(cache: dict) -> None:
    """캐시 딕셔너리를 파일에 저장한다."""
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[chart_cache] 캐시 파일 저장 오류: {e}")


# ─────────────────────────────────────────
# 공개 함수 — 캐시 빌드
# ─────────────────────────────────────────

def build_cache(watchlist: dict) -> None:
    """09:05 market_open 완료 직후 1회 호출 — 감시 종목 전체의 일봉·240분봉을 받아서 저장한다.

    이 함수가 실행된 뒤부터 run_all.py의 모든 모듈은 API 대신 캐시 파일을 읽는다.
    종목 간 2초 대기로 t8451·t8452 API 속도 제한을 지킨다.

    Args:
        watchlist: dynamic_watchlist.json의 내용 딕셔너리 {"종목코드": {"name": ..., ...}, ...}
    """
    cache = _load_cache()
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    codes = list(watchlist.keys())
    total = len(codes)

    print(f"[chart_cache] 일봉·240분봉 캐시 빌드 시작 ({total}개 종목)")

    for i, code in enumerate(codes, 1):
        name = watchlist.get(code, {}).get("name", code)
        print(f"[chart_cache]   {i}/{total}: {name}({code})")

        # 모든 종목(첫 종목 포함) API 호출 전 2초 대기
        # 스크리너 직후 바로 실행되므로 첫 종목도 반드시 대기해야 호출 제한 오류를 피할 수 있다
        time.sleep(2.0)

        # 일봉 60개 조회 (20일 ATR·MA + 55일 신고가 계산 모두 커버)
        daily = ls_client.get_daily_chart(code, count=60)

        # 일봉·240분봉 사이에도 2초 대기 (같은 종목이라도 연속 호출은 제한에 걸릴 수 있음)
        time.sleep(2.0)

        # 240분봉 25개 조회 (20MA 계산 + 여유)
        minute240 = ls_client.get_minute_chart(code, minute=240, count=25)

        now_str = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")

        # 종목별 데이터를 캐시 딕셔너리에 저장
        cache[code] = {
            "daily":                daily    or [],  # API 오류 시 빈 리스트로 저장
            "daily_date":           today,
            "minute240":            minute240 or [],
            "minute240_fetched_at": now_str,
        }

    _save_cache(cache)
    print(f"[chart_cache] 캐시 빌드 완료 → {_CACHE_FILE}")


# ─────────────────────────────────────────
# 공개 함수 — 캐시 읽기
# ─────────────────────────────────────────

def get_daily_cached(code: str, count: int = 25) -> list:
    """오늘 날짜의 일봉 캐시를 반환한다.

    캐시가 없거나 오늘 날짜가 아니면 빈 리스트를 반환한다.
    빈 리스트를 받은 호출자는 ls_client.get_daily_chart() 로 직접 조회(폴백)해야 한다.

    Args:
        code:  종목코드 6자리 (예: "005930")
        count: 반환할 최대 캔들 수 (최신 기준, 기본 25개)
               캐시에 count개 미만이 저장된 경우 전체 반환

    Returns:
        OHLCV 딕셔너리 리스트 (날짜 오름차순). 캐시 없으면 빈 리스트.
    """
    cache = _load_cache()
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    entry = cache.get(code)

    # 캐시 항목 없음
    if not entry:
        return []

    # 날짜 불일치 (전날 캐시)
    if entry.get("daily_date") != today:
        return []

    daily = entry.get("daily", [])
    if not daily:
        return []

    # 요청 count만큼 최신 데이터 슬라이싱
    return daily[-count:] if len(daily) >= count else daily


def get_minute240_cached(code: str) -> list:
    """240분봉 캐시를 반환한다. 마지막 저장 시각으로부터 30분이 지났으면 빈 리스트를 반환한다.

    빈 리스트를 받은 호출자는 ls_client.get_minute_chart() 로 재조회한 뒤
    update_minute240_cache() 를 호출해서 캐시를 갱신해야 한다.

    Args:
        code: 종목코드 6자리

    Returns:
        240분봉 딕셔너리 리스트. 캐시 없거나 TTL(30분) 만료면 빈 리스트.
    """
    cache = _load_cache()
    entry = cache.get(code)

    if not entry or "minute240_fetched_at" not in entry:
        return []

    # 마지막 저장 시각과 현재 시각의 차이 계산
    fetched_str = entry["minute240_fetched_at"]
    try:
        fetched_at = _KST.localize(
            datetime.strptime(fetched_str, "%Y-%m-%d %H:%M:%S")
        )
    except ValueError:
        return []  # 날짜 파싱 실패 → 캐시 무효 처리

    elapsed_sec = (datetime.now(_KST) - fetched_at).total_seconds()
    if elapsed_sec > _MINUTE240_TTL_SEC:
        return []  # 30분 TTL 만료

    return entry.get("minute240", [])


# ─────────────────────────────────────────
# 공개 함수 — 240분봉 캐시 갱신
# ─────────────────────────────────────────

def update_minute240_cache(code: str, minute240_data: list) -> None:
    """240분봉 재조회 결과를 캐시에 덮어쓴다. 해당 종목의 240분봉 항목만 갱신한다.

    Args:
        code:           종목코드 6자리
        minute240_data: ls_client.get_minute_chart() 가 반환한 240분봉 리스트
    """
    cache   = _load_cache()
    now_str = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    today   = datetime.now(_KST).strftime("%Y-%m-%d")

    if code in cache:
        # 기존 항목의 240분봉만 교체 (일봉은 그대로 유지)
        cache[code]["minute240"]            = minute240_data
        cache[code]["minute240_fetched_at"] = now_str
    else:
        # 캐시에 없는 종목 (예: 보유 중이지만 감시 목록 밖인 경우)
        cache[code] = {
            "daily":                [],
            "daily_date":           today,
            "minute240":            minute240_data,
            "minute240_fetched_at": now_str,
        }

    _save_cache(cache)
