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

import pytz
from dotenv import load_dotenv

load_dotenv()

# 체결 원장 JSON 파일 경로
LEDGER_FILE = "trade_ledger.json"

# 한국 표준시 (KST, UTC+9)
KST = pytz.timezone("Asia/Seoul")

# source 허용 값 목록 (CLAUDE.md 계약)
VALID_SOURCES = {"TURTLE_ENTRY", "TURTLE_PYRAMID", "TURTLE_EXIT", "MANUAL_SYNC"}

# Google Sheets 열제목 (한글) — 순서 변경 시 _save_to_sheets의 row 리스트도 함께 수정
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
    "비고",             # note
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

        # 첫 행이 비어있으면 한글 열제목 추가 (새 시트 또는 기존 빈 시트 모두 처리)
        first_row = sheet.row_values(1)
        if not first_row:
            sheet.append_row(SHEET_HEADERS)
            print(f"[원장] Google Sheets 열제목 추가 완료")

        # 수익률: SELL이고 profit_rate 필드가 있을 때만 표시, 그 외 빈칸
        profit_rate_val = record.get("profit_rate", "")
        if profit_rate_val != "" and isinstance(profit_rate_val, (int, float)):
            profit_rate_str = f"{profit_rate_val:+.2f}"  # 예: "+12.34" 또는 "-5.67"
        else:
            profit_rate_str = ""

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
            record.get("net_amount",   0),
            record.get("order_type",  ""),
            record.get("source",      ""),
            profit_rate_str,
            record.get("note",        ""),
        ]
        sheet.append_row(row)
        print(f"[원장] Google Sheets 저장 완료")

    except ImportError:
        print("[원장] gspread 미설치 → Sheets 저장 스킵 (pip install gspread oauth2client)")
    except Exception as e:
        # Google Sheets 오류는 치명적이지 않으므로 로그만 남기고 계속 진행
        print(f"[원장] Google Sheets 저장 오류 (무시하고 계속): {e}")


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

    # JSON 저장
    _save_to_json(record)

    # Google Sheets 저장
    _save_to_sheets(record)

    # 콘솔 확인 로그
    side_kor = "매수" if record.get("side") == "BUY" else "매도"
    name     = record.get("stock_name", stock_code)
    qty      = record.get("qty", 0)
    price    = record.get("unit_price", 0)
    src      = record.get("source", "")
    print(f"[원장] 기록 완료 | {side_kor} {name}({stock_code}) {qty}주 @{price:,}원 [{src}]")
