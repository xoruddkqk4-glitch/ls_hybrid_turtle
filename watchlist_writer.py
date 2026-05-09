# watchlist_writer.py
# 감시 종목 설정 파일 안전 갱신 모듈
#
# 텔레그램 봇 명령(/add, /remove, /block, /unblock)으로 호출되어
# watchlist_config.json과 dynamic_watchlist.json을 즉시 갱신한다.
#
# 안전 장치:
#   - 원자적 쓰기 (tempfile → os.replace) — 중간에 죽어도 파일 깨지지 않음
#   - idempotent — 같은 작업 두 번 호출해도 안전 (같은 응답)
#   - 보유 중인 종목은 결과 dict의 warn_held=True로 반영
#
# 사용법:
#   import watchlist_writer
#   result = watchlist_writer.add_to_whitelist("005930", "삼성전자")
#   if result["ok"]:
#       print(result["msg"])
#   if result["warn_held"]:
#       print("주의: 현재 보유 중인 종목입니다.")

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import pytz

# 한국 표준시 (KST, UTC+9)
_KST = pytz.timezone("Asia/Seoul")

# 파일 경로 (스크립트 위치 기준 절대 경로 — 어느 디렉토리에서 실행해도 같은 파일 참조)
_DIR          = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH  = os.path.join(_DIR, "watchlist_config.json")
_DYNAMIC_PATH = os.path.join(_DIR, "dynamic_watchlist.json")
_HELD_PATH    = os.path.join(_DIR, "held_stock_record.json")


# ─────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────

def _atomic_write_json(path: str, data: dict):
    """JSON 파일을 원자적으로(atomic) 쓴다.

    같은 디렉터리에 임시파일을 만들어 데이터를 쓴 뒤,
    os.replace()로 한 번에 원본 파일을 교체한다.
    이 방식은 Linux/Windows 모두 atomic이라 쓰는 도중 프로세스가
    죽어도 원본 파일이 절대 깨지지 않는다.
    """
    dir_name = os.path.dirname(path) or "."
    # 같은 디렉터리에 임시파일 — cross-device(EXDEV) 오류 방지
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".json",
        dir=dir_name,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        # 실패 시 임시파일 청소 후 예외 재전파
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _load_json(path: str, default: dict) -> dict:
    """JSON 파일을 로드한다. 파일이 없거나 깨졌으면 default를 반환한다."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[watchlist_writer] {path} 읽기 오류: {e} → 기본값으로 처리")
        return default


def _to_item(entry) -> dict:
    """whitelist/blacklist 항목 정규화.

    문자열("005930")로 저장된 옛 형식과 딕셔너리({"code":..., "name":...}) 형식
    둘 다 받아서 항상 dict로 변환한다.
    """
    if isinstance(entry, str):
        return {"code": entry, "name": ""}
    return entry


def _load_config() -> dict:
    """watchlist_config.json 로드. 없으면 빈 구조 반환."""
    cfg = _load_json(_CONFIG_PATH, {"whitelist": [], "blacklist": []})
    cfg["whitelist"] = [_to_item(x) for x in cfg.get("whitelist", [])]
    cfg["blacklist"] = [_to_item(x) for x in cfg.get("blacklist", [])]
    return cfg


def _is_valid_code(code: str) -> bool:
    """종목코드가 6자리 숫자인지 확인."""
    return isinstance(code, str) and len(code) == 6 and code.isdigit()


def is_held(code: str) -> bool:
    """held_stock_record.json에 보유 중인 종목인지 확인.

    텔레그램 명령으로 보유 중인 종목을 watchlist에서 빼거나 차단할 때
    경고 메시지에 반영하기 위해 사용한다.
    """
    held = _load_json(_HELD_PATH, {})
    return code in held


# ─────────────────────────────────────────
# dynamic_watchlist.json 즉시 갱신
# ─────────────────────────────────────────

def _update_dynamic_watchlist(action: str, code: str, name: str = ""):
    """dynamic_watchlist.json을 즉시 갱신한다.

    Args:
        action: "add" (추가/이름 갱신) 또는 "remove" (제거)
        code:   종목코드 6자리
        name:   종목명 (action="add"일 때 사용)

    동작:
        - 파일이 없으면 새로 생성 (오늘 날짜로)
        - 있으면 stocks·count·updated_at·date를 모두 오늘 기준으로 갱신
        - "add" 시 새 종목은 score=1.0 (whitelist 강제 포함과 동일 취급)
    """
    now     = datetime.now(_KST)
    today   = now.strftime("%Y%m%d")
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    data = _load_json(_DYNAMIC_PATH, {
        "updated_at": now_str,
        "date":       today,
        "count":      0,
        "stocks":     {},
    })

    stocks = data.get("stocks", {})

    if action == "add":
        if code in stocks:
            # 기존 정보 보존하고 이름만 갱신
            if name:
                stocks[code]["name"] = name
        else:
            stocks[code] = {
                "name":   name or code,
                "market": "KOSPI",  # 기본값 (나중에 t1463이 갱신)
                "score":  1.0,      # whitelist 강제 포함 종목과 동일하게 최상위
                "atr":    0.0,      # 다음 09:05 배치에서 실제 값으로 갱신됨
            }
    elif action == "remove":
        stocks.pop(code, None)
    else:
        raise ValueError(f"_update_dynamic_watchlist: 잘못된 action: {action}")

    data["stocks"]     = stocks
    data["count"]      = len(stocks)
    data["date"]       = today      # 항상 오늘로 갱신 (config.get_watchlist 호환)
    data["updated_at"] = now_str

    _atomic_write_json(_DYNAMIC_PATH, data)


# ─────────────────────────────────────────
# 화이트리스트 관리
# ─────────────────────────────────────────

def add_to_whitelist(code: str, name: str) -> dict:
    """화이트리스트에 종목 추가 + dynamic_watchlist 즉시 반영.

    Returns:
        {
            "ok":        bool,  # 작업 성공 여부 (입력 검증 실패 시 False)
            "msg":       str,   # 사용자에게 보여줄 메시지
            "already":   bool,  # 이미 등록되어 있던 경우 True (idempotent)
            "warn_held": bool,  # 보유 중 종목이면 True (현재는 의미 없음 — 추가 시엔 항상 False)
        }
    """
    if not _is_valid_code(code):
        return {"ok": False,
                "msg": f"종목코드는 6자리 숫자여야 합니다: {code}",
                "already": False, "warn_held": False}
    if not name or not name.strip():
        return {"ok": False,
                "msg": "종목명을 입력해 주세요.",
                "already": False, "warn_held": False}

    name = name.strip()
    cfg       = _load_config()
    whitelist = cfg["whitelist"]
    blacklist = cfg["blacklist"]

    # 블랙리스트에 있으면 거부 (블랙리스트 우선 원칙)
    if any(item["code"] == code for item in blacklist):
        return {"ok": False,
                "msg": (f"{name}({code})은(는) 블랙리스트에 등록되어 있습니다. "
                        f"먼저 /unblock {code} 를 실행하세요."),
                "already": False, "warn_held": False}

    # 이미 화이트리스트에 있으면 idempotent — 이름만 갱신
    already = False
    for item in whitelist:
        if item["code"] == code:
            already = True
            item["name"] = name
            break
    if not already:
        whitelist.append({"code": code, "name": name})

    cfg["whitelist"] = whitelist
    _atomic_write_json(_CONFIG_PATH, cfg)

    # dynamic_watchlist.json 즉시 갱신 (다음 run_all.py 부터 매매 대상)
    _update_dynamic_watchlist("add", code, name)

    return {
        "ok":        True,
        "already":   already,
        "warn_held": False,
        "msg":       (f"이미 등록되어 있습니다 (이름 갱신): {name}({code})"
                      if already
                      else f"화이트리스트 추가: {name}({code})"),
    }


def remove_from_whitelist(code: str) -> dict:
    """화이트리스트에서 종목 제거 + dynamic_watchlist에서도 제거.

    Returns:
        {
            "ok":        bool,
            "msg":       str,
            "found":     bool,  # 실제로 제거됐으면 True / 처음부터 없었으면 False
            "warn_held": bool,  # 현재 보유 중이면 True (경고 메시지에 사용)
            "name":      str,   # 제거된 종목의 이전 이름
        }
    """
    if not _is_valid_code(code):
        return {"ok": False,
                "msg": f"종목코드는 6자리 숫자여야 합니다: {code}",
                "found": False, "warn_held": False, "name": ""}

    cfg       = _load_config()
    whitelist = cfg["whitelist"]

    found      = False
    found_name = ""
    new_list   = []
    for item in whitelist:
        if item["code"] == code:
            found      = True
            found_name = item.get("name", "")
        else:
            new_list.append(item)

    cfg["whitelist"] = new_list
    _atomic_write_json(_CONFIG_PATH, cfg)

    # dynamic_watchlist에서도 제거 (있을 수도 없을 수도 있음 — pop은 안전)
    _update_dynamic_watchlist("remove", code)

    warn_held = is_held(code)
    return {
        "ok":        True,
        "found":     found,
        "name":      found_name,
        "warn_held": warn_held,
        "msg":       (f"화이트리스트에서 제거: {found_name or code}({code})"
                      if found
                      else f"화이트리스트에 없음 (변경 없음): {code}"),
    }


# ─────────────────────────────────────────
# 블랙리스트 관리
# ─────────────────────────────────────────

def add_to_blacklist(code: str, name: str) -> dict:
    """블랙리스트에 종목 추가 (+ whitelist에서 제거 + dynamic에서 제거).

    블랙리스트는 화이트리스트보다 우선이라, 같은 종목이 화이트리스트에 있어도
    이번 명령으로 화이트리스트에서 제거된다.

    Returns:
        {
            "ok":        bool,
            "msg":       str,
            "already":   bool,  # 이미 블랙리스트에 있었으면 True
            "warn_held": bool,  # 현재 보유 중이면 True (강한 경고용)
        }
    """
    if not _is_valid_code(code):
        return {"ok": False,
                "msg": f"종목코드는 6자리 숫자여야 합니다: {code}",
                "already": False, "warn_held": False}
    if not name or not name.strip():
        return {"ok": False,
                "msg": "종목명을 입력해 주세요.",
                "already": False, "warn_held": False}

    name = name.strip()
    cfg       = _load_config()
    whitelist = cfg["whitelist"]
    blacklist = cfg["blacklist"]

    # 이미 블랙리스트에 있으면 idempotent — 이름만 갱신
    already = False
    for item in blacklist:
        if item["code"] == code:
            already = True
            item["name"] = name
            break
    if not already:
        blacklist.append({"code": code, "name": name})

    # 화이트리스트에서 제거 (블랙리스트 우선 원칙)
    whitelist = [item for item in whitelist if item["code"] != code]

    cfg["whitelist"] = whitelist
    cfg["blacklist"] = blacklist
    _atomic_write_json(_CONFIG_PATH, cfg)

    # dynamic_watchlist에서도 제거
    _update_dynamic_watchlist("remove", code)

    warn_held = is_held(code)
    return {
        "ok":        True,
        "already":   already,
        "warn_held": warn_held,
        "msg":       (f"이미 차단됨 (이름 갱신): {name}({code})"
                      if already
                      else f"블랙리스트 추가 (감시 제외): {name}({code})"),
    }


def remove_from_blacklist(code: str) -> dict:
    """블랙리스트에서 종목 제거 (차단 해제).

    화이트리스트와 dynamic_watchlist는 변경하지 않는다.
    다시 감시하고 싶으면 /add 명령을 별도로 실행해야 한다.

    Returns:
        {
            "ok":    bool,
            "msg":   str,
            "found": bool,  # 실제로 제거됐으면 True / 처음부터 없었으면 False
            "name":  str,   # 제거된 종목의 이전 이름
        }
    """
    if not _is_valid_code(code):
        return {"ok": False,
                "msg": f"종목코드는 6자리 숫자여야 합니다: {code}",
                "found": False, "name": ""}

    cfg       = _load_config()
    blacklist = cfg["blacklist"]

    found      = False
    found_name = ""
    new_list   = []
    for item in blacklist:
        if item["code"] == code:
            found      = True
            found_name = item.get("name", "")
        else:
            new_list.append(item)

    cfg["blacklist"] = new_list
    _atomic_write_json(_CONFIG_PATH, cfg)

    return {
        "ok":    True,
        "found": found,
        "name":  found_name,
        "msg":   (f"블랙리스트에서 제거 (차단 해제): {found_name or code}({code})"
                  if found
                  else f"블랙리스트에 없음 (변경 없음): {code}"),
    }
