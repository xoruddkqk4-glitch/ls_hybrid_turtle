# trade_ledger.py
# 체결 원장 기록 모듈 (단일 진입점)
#
# 모든 매매 체결 내역은 반드시 이 모듈의 append_trade()를 통해 기록한다.
# 로컬 JSON 파일(trade_ledger.json)과 Google Sheets에 동시 저장한다.
#
# 사용법:
#   from trade_ledger import append_trade
#   append_trade({
#       "side": "BUY", "stock_code": "005930", "stock_name": "삼성전자",
#       "qty": 10, "unit_price": 75000, "order_no": "12345",
#       "order_type": "MARKET", "source": "TURTLE_ENTRY",
#   })

import json
import os
import uuid
from datetime import datetime
from typing import Optional

import pytz
from dotenv import load_dotenv

from telegram_alert import SendMessage

load_dotenv()

# 파일 경로 (스크립트 위치 기준 절대 경로)
_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER_FILE = os.path.join(_DIR, "trade_ledger.json")

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")

# source 허용 값 목록 (CLAUDE.md 계약)
# 매수: ENTRY_30MIN(목표가30분) / ENTRY_S1(20일신고가) / ENTRY_S2(55일신고가) / PYRAMID(피라미딩)
# 매도: EXIT_STOP(2N하드손절) / EXIT_10LOW(10일신저가익절) / EXIT_5MA(5MA익절)
VALID_SOURCES = {
    "ENTRY_30MIN", "ENTRY_S1", "ENTRY_S2",   # 매수 — 신규 진입
    "PYRAMID",                                 # 매수 — 피라미딩
    "EXIT_STOP", "EXIT_10LOW", "EXIT_5MA",    # 매도 — 청산
    "MANUAL_SYNC",                             # 수동 동기화
}

# 체결 원장 시트 이름 (기본 sheet1 사용)
# 포트폴리오 추이 시트 이름
PORTFOLIO_SHEET_NAME = "포트폴리오 추이"

# 하루 1회 스냅샷 여부를 기록하는 로컬 파일 (커밋 제외 대상)
DAILY_SNAPSHOT_FILE = os.path.join(_DIR, "daily_snapshot.json")

# 수수료율 (직접 계산 방식)
# LS증권 위탁수수료: 0.015% (매수·매도 공통)
# 증권거래세: 0.18% (매도 시만 부과 — 코스피·코스닥 공통, 2024년 기준)
LS_BROKER_FEE_RATE = 0.00015   # 위탁수수료 0.015%
SELL_TAX_RATE      = 0.00180   # 거래세 0.18%

# 체결 원장 열제목 (한글) — 순서 변경 시 _save_to_sheets의 row 리스트도 함께 수정
SHEET_HEADERS = [
    "기록ID",           # record_id
    "기록시각(KST)",    # ts_kst
    "계좌",             # account_id
    "매수/매도",        # side
    "종목코드",         # stock_code
    "종목명",           # stock_name
    "주문번호",         # order_no
    "체결번호",         # exec_no
    "수량(주)",         # qty
    "단가(원)",         # unit_price
    "거래금액(원)",     # gross_amount (qty × unit_price)
    "수수료(원)",       # fee
    "실수령금액(원)",   # net_amount
    "주문유형",         # order_type (MARKET / LIMIT)
    "매매구분",         # source (TURTLE_ENTRY / EXIT 등)
    "수익률(%)",        # profit_rate — SELL일 때만 입력, BUY는 빈칸
    "수익금(원)",       # profit_amount — SELL일 때만 입력 (매도가-평균매입가) × 수량
    "비고",             # note
]

# 포트폴리오 추이 시트 열제목
PORTFOLIO_HEADERS = [
    "기록시각(KST)",    # 기록 시각
    "총평가금액(원)",   # 추정순자산 (주식 + 현금)
    "주식평가액(원)",   # 보유 주식 평가금액만
    "예수금(원)",       # 현금 잔고 (총자산 - 주식평가액)
    "매입금액(원)",     # 보유 주식 매입 원금 합계
    "평가손익(원)",     # 미실현 손익 (주식평가액 - 매입금액)
    "실현손익(원)",     # 오늘 확정된 손익
    "보유종목수",       # 현재 보유 중인 종목 수
    "보유종목목록",     # 보유 종목명 (쉼표 구분)
    "누적수익금(원)",   # 총평가금액 - 초기자본 (절대 금액)
]


# ─────────────────────────────────────────
# 내부 함수
# ─────────────────────────────────────────

def _generate_record_id(stock_code: str) -> str:
    """체결 원장 고유 ID를 생성한다.

    중복 방지를 위해 날짜+시간+종목코드+랜덤 4자리를 조합한다.
    형식: YYYYMMDD_HHMMSS_종목코드_랜덤4자리
    예시: 20260413_103045_005930_a3f2
    """
    now_str      = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    random_part  = uuid.uuid4().hex[:4]
    return f"{now_str}_{stock_code}_{random_part}"


def _save_to_json(record: dict):
    """체결 내역을 로컬 JSON 파일(trade_ledger.json)에 저장한다.

    파일이 없으면 새로 만들고, 있으면 기존 목록에 추가한다.
    """
    # 기존 데이터 불러오기 (파일이 없거나 손상된 경우 빈 목록으로 시작)
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
        except (json.JSONDecodeError, IOError):
            print(f"[원장] {LEDGER_FILE} 읽기 오류 → 새 파일로 시작")
            data = []
    else:
        data = []

    # 새 체결 내역 추가
    data.append(record)

    # 다시 저장
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_to_sheets(record: dict):
    """체결 내역을 Google Sheets에 저장한다.

    서비스 계정 JSON 파일이 없거나 오류 발생 시
    경고 메시지만 출력하고 계속 진행한다 (치명적 오류 아님).
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        # .env에서 Google 설정 읽기
        json_path   = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
        sheet_title = os.getenv("GOOGLE_SPREADSHEET_TITLE", "LS Stock Trade History")
        folder_id   = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

        # 서비스 계정 파일이 없으면 스킵
        if not os.path.exists(json_path):
            print(f"[원장] Google 서비스 계정 파일 없음 ({json_path}) → Sheets 저장 스킵")
            return

        # Google API 인증
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = ServiceAccountCredentials.from_json_keyfile_name(json_path, scope)
        client = gspread.authorize(creds)

        # 스프레드시트 열기 (없으면 새로 생성)
        try:
            spreadsheet = client.open(sheet_title)
            sheet = spreadsheet.sheet1
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(sheet_title)
            if folder_id:
                # 지정한 드라이브 폴더로 이동
                spreadsheet.share(None, perm_type="anyone", role="reader")
            sheet = spreadsheet.sheet1

        # 첫 행이 "기록ID"로 시작하지 않으면 열제목을 1행에 삽입
        # 열 수가 다르거나 마지막 컬럼명이 다르면 헤더 행 전체를 업데이트
        first_row = sheet.row_values(1)
        if not first_row or first_row[0] != "기록ID":
            sheet.insert_row(SHEET_HEADERS, 1)
            print("[원장] Google Sheets 열제목 추가 완료")
        elif len(first_row) < len(SHEET_HEADERS) or first_row[len(SHEET_HEADERS) - 1] != SHEET_HEADERS[-1]:
            sheet.update("A1", [SHEET_HEADERS])
            print("[원장] Google Sheets 열제목 업데이트 완료 (수익금 컬럼 추가)")

        # 수익률: SELL이고 profit_rate 필드가 있을 때만 표시, 그 외 빈칸
        profit_rate_val = record.get("profit_rate", "")
        if profit_rate_val != "" and isinstance(profit_rate_val, (int, float)):
            profit_rate_str = f"{profit_rate_val:+.2f}"  # 예: "+12.34" 또는 "-5.67"
        else:
            profit_rate_str = ""

        # 수익금: SELL이고 profit_amount 필드가 있을 때만 표시, 그 외 빈칸
        profit_amount_val = record.get("profit_amount", "")
        if profit_amount_val != "" and isinstance(profit_amount_val, (int, float)):
            profit_amount_str = f"{int(profit_amount_val):+,}"  # 예: "+37,000" 또는 "-15,000"
        else:
            profit_amount_str = ""

        # 실수령금액: append_trade()에서 이미 계산됨 (gross_amount - fee)
        # net_amount가 0이거나 없는 경우에만 gross_amount로 보수적 대체
        net_amount_val = record.get("net_amount", "")
        if (net_amount_val == "" or net_amount_val == 0) and record.get("side") == "SELL":
            net_amount_val = record.get("gross_amount", 0)

        # 데이터 행 추가 (SHEET_HEADERS 순서와 일치)
        row = [
            record.get("record_id",   ""),
            record.get("ts_kst",      ""),
            record.get("account_id",  ""),
            record.get("side",        ""),
            record.get("stock_code",  ""),
            record.get("stock_name",  ""),
            record.get("order_no",    ""),
            record.get("exec_no",     ""),
            record.get("qty",          0),
            record.get("unit_price",   0),
            record.get("gross_amount", 0),
            record.get("fee",          0),
            net_amount_val,
            record.get("order_type",  ""),
            record.get("source",      ""),
            profit_rate_str,
            profit_amount_str,
            record.get("note",        ""),
        ]
        sheet.append_row(row)
        print(f"[원장] Google Sheets 저장 완료")
        return True  # 저장 성공

    except ImportError:
        print("[원장] gspread 미설치 → Sheets 저장 스킵 (pip install gspread oauth2client)")
    except Exception as e:
        # Google Sheets 오류는 치명적이지 않으므로 로그만 남기고 계속 진행
        print(f"[원장] Google Sheets 저장 오류 (무시하고 계속): {e}")

    return False  # 저장 실패 또는 스킵


# ─────────────────────────────────────────
# 공개 함수 (단일 진입점)
# ─────────────────────────────────────────

def append_trade(record: dict):
    """체결 원장에 새 거래를 기록한다 (단일 진입점).

    로컬 JSON 파일과 Google Sheets에 동시 저장한다.
    모든 매매 체결(진입·피라미딩·청산)은 반드시 이 함수를 통해 기록한다.

    필수 필드:
        side (str):        "BUY" 또는 "SELL"
        stock_code (str):  종목코드 6자리
        qty (int):         체결 수량 (주)
        unit_price (int):  체결 단가 (원)
        order_type (str):  "MARKET" 또는 "LIMIT"
        source (str):      "TURTLE_ENTRY" | "TURTLE_PYRAMID" | "TURTLE_EXIT" | "MANUAL_SYNC"

    자동으로 채워지는 필드:
        record_id:    중복 방지 고유 ID (자동 생성)
        ts_kst:       KST 기준 기록 시각 (자동 생성)
        gross_amount: qty × unit_price (자동 계산)
        account_id:   .env의 LS_ACCOUNT_NO (자동)
    """
    # source 유효성 검사
    source = record.get("source", "")
    if source not in VALID_SOURCES:
        print(f"[원장] 경고: source 값이 올바르지 않음 → '{source}'. 허용: {VALID_SOURCES}")

    # 자동 값 채우기 (이미 있으면 덮어쓰지 않음)
    stock_code = record.get("stock_code", "UNKNOWN")
    record.setdefault("record_id",    _generate_record_id(stock_code))
    record.setdefault("ts_kst",       datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"))
    record.setdefault("gross_amount", record.get("qty", 0) * record.get("unit_price", 0))
    record.setdefault("account_id",   os.getenv("LS_ACCOUNT_NO", "****"))

    # 수수료 자동 계산 (fee 필드가 없을 때만 계산)
    # 위탁수수료 = 거래금액 × 0.015% (매수·매도 공통)
    # 거래세     = 거래금액 × 0.18%  (매도 시만 추가)
    if "fee" not in record:
        gross = record.get("gross_amount", 0)
        broker_fee = int(gross * LS_BROKER_FEE_RATE)
        sell_tax   = int(gross * SELL_TAX_RATE) if record.get("side") == "SELL" else 0
        record["fee"] = broker_fee + sell_tax

    # 매도 실수령금액 = 거래금액 - 수수료 (net_amount 미지정 시 자동 계산)
    if record.get("side") == "SELL" and "net_amount" not in record:
        record["net_amount"] = record.get("gross_amount", 0) - record["fee"]

    # JSON 저장
    _save_to_json(record)

    # Google Sheets 저장
    sheets_ok = _save_to_sheets(record)

    # 콘솔 확인 로그
    side_kor = "매수" if record.get("side") == "BUY" else "매도"
    name     = record.get("stock_name", stock_code)
    qty      = record.get("qty", 0)
    price    = record.get("unit_price", 0)
    src      = record.get("source", "")
    print(f"[원장] 기록 완료 | {side_kor} {name}({stock_code}) {qty}주 @{price:,}원 [{src}]")

    # Google Sheets 저장 성공 시 텔레그램 알림 발송
    if sheets_ok:
        source_kor = {
            "ENTRY_30MIN": "진입(목표가30분)",
            "ENTRY_S1":    "진입(20일신고가)",
            "ENTRY_S2":    "진입(55일신고가)",
            "PYRAMID":     "피라미딩",
            "EXIT_STOP":   "손절(2N하드)",
            "EXIT_10LOW":  "익절(10일신저가)",
            "EXIT_5MA":    "익절(5MA)",
            "MANUAL_SYNC": "수동 동기화",
        }.get(src, src)

        gross = record.get("gross_amount", qty * price)

        # 매도 체결 알림에는 실수령금액·수익률·수익금 추가 표시
        msg_lines = [
            f"📋 [체결 기록] Google Sheets 저장 완료",
            f"종목: {name}({stock_code})",
            f"구분: {side_kor} / {source_kor}",
            f"수량: {qty}주 @{price:,}원",
            f"거래금액: {gross:,}원",
        ]
        if record.get("side") == "SELL":
            net_val = record.get("net_amount", gross)
            if isinstance(net_val, (int, float)) and net_val > 0:
                msg_lines.append(f"실수령금액: {int(net_val):,}원")
            pr = record.get("profit_rate", "")
            pa = record.get("profit_amount", "")
            if pr != "" and isinstance(pr, (int, float)) and pa != "" and isinstance(pa, (int, float)):
                sign = "+" if pr >= 0 else ""
                msg_lines.append(f"수익률: {sign}{pr:.2f}% / 수익금: {int(pa):+,}원")
        msg_lines.append(f"기록시각: {record.get('ts_kst', '')}")
        SendMessage("\n".join(msg_lines))

    # SELL 체결 시 포트폴리오 추이·손익차트 자동 갱신
    # 각 함수 내부에도 try/except가 있지만, 이중 방어로 append_trade가 절대 중단되지 않도록 감쌈
    if record.get("side") == "SELL":
        try:
            _refresh_portfolio_today()
        except Exception as e:
            print(f"[원장] 포트폴리오 추이 자동 갱신 실패 (무시하고 계속): {e}")
        try:
            _refresh_chart_after_sell()
        except Exception as e:
            print(f"[원장] 손익차트 자동 갱신 실패 (무시하고 계속): {e}")


def get_today_realized_pnl() -> int:
    """오늘 날짜의 실현손익을 trade_ledger.json에서 계산해 반환한다.

    SELL 체결 건의 profit_amount를 오늘 날짜 기준으로 합산한다.
    포트폴리오 추이 시트와 손익차트가 동일한 값을 참조하도록 단일 계산 진입점으로 사용한다.

    Returns:
        오늘 실현손익 합계 (원). 매도 내역이 없거나 파일이 없으면 0.
    """
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    if not os.path.exists(LEDGER_FILE):
        return 0

    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            return 0
    except (json.JSONDecodeError, IOError):
        return 0

    total = 0
    for rec in records:
        # SELL 건만 집계
        if rec.get("side") != "SELL":
            continue
        # 오늘 날짜 기준 필터 (ts_kst: "YYYY-MM-DD HH:MM:SS")
        if not rec.get("ts_kst", "").startswith(today_str):
            continue
        profit_amount = rec.get("profit_amount", None)
        if isinstance(profit_amount, (int, float)):
            total += int(profit_amount)

    return total


def _load_daily_snapshot() -> dict:
    """daily_snapshot.json을 읽어 반환한다.

    파일이 없으면 빈 딕셔너리를 반환한다.
    구조 예시: {"last_recorded_date": "2026-04-13"}
    """
    if os.path.exists(DAILY_SNAPSHOT_FILE):
        try:
            with open(DAILY_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_daily_snapshot(data: dict):
    """daily_snapshot.json에 오늘 날짜를 기록한다."""
    try:
        with open(DAILY_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[원장] daily_snapshot.json 저장 오류: {e}")


def _get_portfolio_worksheet():
    """포트폴리오 추이 시트에 연결하고 (spreadsheet, worksheet) 를 반환한다.

    서비스 계정 파일이 없거나 오류 발생 시 (None, None) 을 반환한다.
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        json_path   = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
        sheet_title = os.getenv("GOOGLE_SPREADSHEET_TITLE", "LS Stock Trade History")

        if not os.path.exists(json_path):
            print(f"[원장] Google 서비스 계정 파일 없음 ({json_path}) → 포트폴리오 시트 접근 스킵")
            return None, None

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = ServiceAccountCredentials.from_json_keyfile_name(json_path, scope)
        client = gspread.authorize(creds)

        try:
            spreadsheet = client.open(sheet_title)
        except gspread.SpreadsheetNotFound:
            print(f"[원장] 스프레드시트 '{sheet_title}' 없음 → 포트폴리오 시트 접근 스킵")
            return None, None

        # 포트폴리오 추이 시트 열기 (없으면 새로 생성)
        try:
            ws = spreadsheet.worksheet(PORTFOLIO_SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=PORTFOLIO_SHEET_NAME, rows=1000, cols=10)
            print(f"[원장] '{PORTFOLIO_SHEET_NAME}' 시트 새로 생성")

        return spreadsheet, ws

    except ImportError:
        print("[원장] gspread 미설치 → 포트폴리오 시트 접근 스킵")
        return None, None
    except Exception as e:
        print(f"[원장] 포트폴리오 시트 접근 오류: {e}")
        return None, None


def _find_today_row(ws) -> Optional[int]:
    """포트폴리오 추이 시트에서 오늘 날짜로 시작하는 행 번호(1-based)를 반환한다.

    오늘 날짜 행이 없으면 None을 반환한다.
    """
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    all_rows  = ws.get_all_values()
    for i, row in enumerate(all_rows):
        if row and row[0].startswith(today_str):
            return i + 1  # gspread는 행 번호가 1부터 시작
    return None


def _calc_prev_cumulative(all_rows: list, today_row_idx: Optional[int]) -> int:
    """오늘 이전 마지막 행의 누적수익금(J열, 인덱스 9)을 반환한다.

    오늘 행이 있으면 그 위 행 기준, 없으면 전체 마지막 행 기준으로 계산한다.
    헤더 행(인덱스 0)은 제외한다.
    값이 없거나 읽기 실패 시 0을 반환한다.
    """
    if today_row_idx is not None:
        # today_row_idx 는 1-based → 바로 위 행은 0-based 인덱스로 today_row_idx - 2
        prev_idx = today_row_idx - 2
    else:
        prev_idx = len(all_rows) - 1  # 마지막 행 (0-based)

    # 헤더(0-based 인덱스 0)보다 위면 이전 데이터 없음
    if prev_idx < 1:
        return 0

    prev_row = all_rows[prev_idx]
    if len(prev_row) >= 10 and prev_row[9]:
        try:
            # "+1,500,000" 또는 "-300,000" 형식에서 숫자만 추출
            return int(prev_row[9].replace(",", "").replace("+", "").strip())
        except ValueError:
            return 0
    return 0


def _refresh_portfolio_today():
    """SELL 체결 후 포트폴리오 추이 시트의 오늘 실현손익·누적수익금을 갱신한다.

    오늘 행이 있으면 G열(실현손익)과 J열(누적수익금)만 업데이트한다.
    오늘 행이 없으면 실현손익·누적수익금만 채운 임시 행을 추가한다.
    오류 발생 시 로그만 남기고 계속 진행한다.
    """
    try:
        _, ws = _get_portfolio_worksheet()
        if ws is None:
            return

        # 오늘 실현손익 합산 (trade_ledger.json 기반, API 호출 없음)
        today_realized = get_today_realized_pnl()
        all_rows       = ws.get_all_values()
        today_row_idx  = _find_today_row(ws)  # 1-based, 없으면 None
        prev_cumul     = _calc_prev_cumulative(all_rows, today_row_idx)
        new_cumul      = prev_cumul + today_realized
        new_cumul_str  = f"{new_cumul:+,}"

        if today_row_idx is not None:
            # 오늘 행이 이미 있으면 G열(7번째)·J열(10번째)만 값 교체
            ws.update_cell(today_row_idx, 7,  today_realized)
            ws.update_cell(today_row_idx, 10, new_cumul_str)
            print(
                f"[원장] 포트폴리오 추이 갱신 "
                f"— 실현손익: {today_realized:+,}원 / 누적: {new_cumul_str}원"
            )
        else:
            # 오늘 행이 없으면 실현손익·누적수익금만 채운 임시 행 추가
            first_row = ws.row_values(1)
            if not first_row or first_row[0] != "기록시각(KST)":
                ws.insert_row(PORTFOLIO_HEADERS, 1)
                print("[원장] 포트폴리오 추이 열제목 추가 완료")

            ts_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row([
                ts_kst,          # A: 기록시각
                "",              # B: 총평가금액 (미집계)
                "",              # C: 주식평가액 (미집계)
                "",              # D: 예수금 (미집계)
                "",              # E: 매입금액 (미집계)
                "",              # F: 평가손익 (미집계)
                today_realized,  # G: 실현손익
                "",              # H: 보유종목수 (미집계)
                "",              # I: 보유종목목록 (미집계)
                new_cumul_str,   # J: 누적수익금
            ])
            print(
                f"[원장] 포트폴리오 추이 임시 행 추가 "
                f"— 실현손익: {today_realized:+,}원 / 누적: {new_cumul_str}원"
            )

    except Exception as e:
        print(f"[원장] 포트폴리오 추이 갱신 오류 (무시하고 계속): {e}")


def _refresh_chart_after_sell():
    """SELL 체결 후 손익차트 시트를 최신 상태로 갱신한다.

    chart_updater.update_pnl_chart() 를 호출한다.
    오류 발생 시 로그만 남기고 계속 진행한다.
    """
    try:
        from chart_updater import update_pnl_chart
        print("[원장] 손익차트 갱신 중...")
        update_pnl_chart()
    except Exception as e:
        print(f"[원장] 손익차트 갱신 오류 (무시하고 계속): {e}")


def record_portfolio_snapshot(
    total_value: int,
    stock_value: int = 0,
    cash: int = 0,
    purchase_amount: int = 0,
    unrealized_pnl: int = 0,
    realized_pnl: int = 0,
    holdings_count: int = 0,
    holdings_names: str = "",
    initial_capital: int = 0,
):
    """포트폴리오 추이를 별도 시트('포트폴리오 추이')에 기록한다.

    오늘 날짜 행이 이미 있으면 (SELL 체결로 생긴 임시 행 포함) 전체 컬럼을 덮어쓴다.
    오늘 날짜 행이 없으면 새 행을 추가한다.

    Args:
        total_value:      총평가금액 (추정순자산, 원)
        stock_value:      주식평가액 (원)
        cash:             예수금 (원)
        purchase_amount:  매입금액 (원)
        unrealized_pnl:   평가손익 — 미실현 (원)
        realized_pnl:     실현손익 (원)
        holdings_count:   보유 종목 수
        holdings_names:   보유 종목명 (쉼표 구분 문자열)
        initial_capital:  (사용 안 함 — 이전 버전과의 호환을 위해 파라미터만 유지)
    """
    try:
        _, ws = _get_portfolio_worksheet()
        if ws is None:
            return

        # 헤더 확인 및 삽입·업데이트
        first_row = ws.row_values(1)
        if not first_row or first_row[0] != "기록시각(KST)":
            ws.insert_row(PORTFOLIO_HEADERS, 1)
            print(f"[원장] 포트폴리오 추이 열제목 추가 완료")
        elif len(first_row) < len(PORTFOLIO_HEADERS) or first_row[len(PORTFOLIO_HEADERS) - 1] != PORTFOLIO_HEADERS[-1]:
            ws.update("A1", [PORTFOLIO_HEADERS])
            print(f"[원장] 포트폴리오 추이 열제목 업데이트 완료")

        # 시트 전체를 한 번만 읽어 today_row_idx와 prev_cumul 계산에 재사용
        all_rows         = ws.get_all_values()
        today_str_prefix = datetime.now(KST).strftime("%Y-%m-%d")
        today_row_idx    = next(
            (i + 1 for i, row in enumerate(all_rows) if row and row[0].startswith(today_str_prefix)),
            None,
        )
        prev_cumul            = _calc_prev_cumulative(all_rows, today_row_idx)
        cumulative_profit     = prev_cumul + realized_pnl
        cumulative_profit_str = f"{cumulative_profit:+,}"

        ts_kst   = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        row_data = [
            ts_kst,
            total_value,
            stock_value,
            cash,
            purchase_amount,
            unrealized_pnl,
            realized_pnl,
            holdings_count,
            holdings_names,
            cumulative_profit_str,
        ]

        if today_row_idx is not None:
            # 오늘 행이 있으면 전체 컬럼 덮어쓰기 (임시 행 → 완전한 데이터로 교체)
            ws.update(f"A{today_row_idx}:J{today_row_idx}", [row_data])
            action_msg = "갱신(덮어쓰기)"
        else:
            # 오늘 행이 없으면 새 행 추가
            ws.append_row(row_data)
            action_msg = "신규 추가"

        print(
            f"[원장] 포트폴리오 추이 {action_msg} 완료 "
            f"— 총평가금액: {total_value:,}원, 보유종목: {holdings_count}개"
            f", 누적수익금: {cumulative_profit_str}원"
        )

        # 텔레그램 알림 발송
        tg_names = f"\n보유종목: {holdings_names}" if holdings_names else "\n보유종목: 없음"
        msg = (
            f"📊 [포트폴리오 추이] Google Sheets {action_msg} 완료\n"
            f"총평가금액: {total_value:,}원\n"
            f"주식평가액: {stock_value:,}원\n"
            f"예수금: {cash:,}원\n"
            f"평가손익: {unrealized_pnl:+,}원\n"
            f"실현손익: {realized_pnl:+,}원\n"
            f"보유종목수: {holdings_count}개"
            f"{tg_names}"
            f"\n누적수익금: {cumulative_profit_str}원\n"
            f"기록시각: {ts_kst}"
        )
        SendMessage(msg)

    except Exception as e:
        print(f"[원장] 포트폴리오 추이 저장 오류 (무시하고 계속): {e}")
