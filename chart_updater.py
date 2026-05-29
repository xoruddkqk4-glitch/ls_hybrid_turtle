# chart_updater.py
# 실현 손익 차트 업데이터
#
# 구글 스프레드시트의 "포트폴리오 추이" 시트에서
# 일별 실현손익·누적수익금을 읽어,
# "손익차트" 시트에 콤보 차트(막대 + 선)를 자동으로 그린다.
#
# X축 기간 단위(일/주/월/분기/년)를 드롭다운으로 골라서 볼 수 있다.
#   - 일별 데이터를 5가지 기준으로 미리 집계해 숨김 시트("차트데이터")에 저장
#   - "손익차트" 탭의 드롭다운(F1)을 바꾸면 표가 수식으로 다시 계산되고
#     차트가 자동으로 그 기준에 맞춰 바뀐다 (구글시트 기본 기능, 앱스스크립트 불필요)
#   - 집계 규칙: 일일손익 = 기간 합계 / 누적손익 = 기간 마지막 값
#
# 사용법:
#   python chart_updater.py
#
# 다른 파일에서 import해서 사용하는 방법:
#   from chart_updater import update_pnl_chart
#   update_pnl_chart()

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# 구글 시트 탭 이름
CHART_SHEET_NAME = "손익차트"        # 차트를 그릴 탭
DATA_SHEET_NAME  = "차트데이터"      # 집계 데이터를 담는 숨김 탭
PORTFOLIO_SHEET_NAME = "포트폴리오 추이"  # 원본 데이터 탭

# 드롭다운에서 고를 수 있는 기간 단위 (한글 = 화면 표시 / 영문 = 내부 집계 키)
GRAN_KO = ["일", "주", "월", "분기", "년"]
GRAN_EN = ["day", "week", "month", "quarter", "year"]

# 드롭다운 셀 위치 (손익차트 시트 기준): E1=안내문구, F1=실제 드롭다운
DROPDOWN_LABEL_CELL = "E1"
DROPDOWN_CELL       = "F1"


# ─────────────────────────────────────────
# 내부 함수 — 구글 시트 연결
# ─────────────────────────────────────────

def _get_spreadsheet():
    """구글 스프레드시트에 연결하고 Spreadsheet 객체를 반환한다.

    trade_ledger.py 와 동일한 인증 방식(oauth2client + gspread)을 사용한다.
    """
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    json_path   = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
    sheet_title = os.getenv("GOOGLE_SPREADSHEET_TITLE", "LS Stock Trade History")

    # 서비스 계정 파일 존재 여부 확인
    if not os.path.exists(json_path):
        raise FileNotFoundError(
            f"Google 서비스 계정 파일이 없습니다: {json_path}\n"
            ".env 파일에서 GOOGLE_SERVICE_ACCOUNT_JSON 경로를 확인해 주세요."
        )

    # Google API 인증 (Drive + Sheets 권한)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = ServiceAccountCredentials.from_json_keyfile_name(json_path, scope)
    client = gspread.authorize(creds)

    # 스프레드시트 열기
    try:
        spreadsheet = client.open(sheet_title)
    except gspread.SpreadsheetNotFound:
        raise RuntimeError(
            f"스프레드시트 '{sheet_title}' 를 찾을 수 없습니다.\n"
            "먼저 trade_ledger.record_portfolio_snapshot() 을 실행해 주세요."
        )

    return spreadsheet


def _parse_money(cell_value) -> int:
    """금액 문자열을 정수로 변환한다.

    예: "+1,200" -> 1200, "-500" -> -500, "" -> 0
    """
    if cell_value is None:
        return 0
    s = str(cell_value).strip()
    if not s:
        return 0
    s = s.replace(",", "").replace("+", "")
    try:
        return int(float(s))
    except ValueError:
        return 0


# ─────────────────────────────────────────
# 내부 함수 — 데이터 만들기 / 집계
# ─────────────────────────────────────────

def _build_daily_rows(spreadsheet):
    """
    "포트폴리오 추이" 시트의 실현손익·누적수익금을 읽어
    날짜 오름차순의 (YYYY-MM-DD, 일일손익, 누적손익) 목록을 만든다.

    날짜가 비어 있는 날은 일일손익 0원, 누적손익은 직전값으로 채운다.
    (즉, 0원인 날도 날짜별로 빠짐없이 들어간다)

    반환값: [("2026-05-29", 일일손익, 누적손익), ...]
    """
    import gspread

    try:
        ws = spreadsheet.worksheet(PORTFOLIO_SHEET_NAME)
    except gspread.WorksheetNotFound:
        print(f"[차트] '{PORTFOLIO_SHEET_NAME}' 시트가 없어 차트 데이터를 만들 수 없습니다.")
        return []

    all_rows = ws.get_all_values()
    if not all_rows or len(all_rows) <= 1:
        print(f"[차트] '{PORTFOLIO_SHEET_NAME}' 데이터가 없습니다.")
        return []

    # 날짜별로 마지막 기록값을 사용 (같은 날짜에 여러 행이 있을 수 있음)
    by_date = {}
    for row in all_rows[1:]:
        if not row:
            continue
        ts = row[0].strip() if len(row) > 0 else ""
        if len(ts) < 10:
            continue
        try:
            day = datetime.strptime(ts[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        realized   = _parse_money(row[6] if len(row) > 6 else "0")   # G열 실현손익
        cumulative = _parse_money(row[9] if len(row) > 9 else "0")   # J열 누적수익금
        by_date[day] = (realized, cumulative)

    if not by_date:
        print(f"[차트] '{PORTFOLIO_SHEET_NAME}'에 차트용 손익 데이터가 없습니다.")
        return []

    # 첫 날짜~마지막 날짜까지 빈 날짜를 0원으로 채운다.
    start_day = min(by_date.keys())
    end_day   = max(by_date.keys())
    prev_cumulative = 0
    result = []

    day = start_day
    while day <= end_day:
        if day in by_date:
            realized, cumulative = by_date[day]
            prev_cumulative = cumulative
        else:
            realized   = 0
            cumulative = prev_cumulative
        result.append((day.strftime("%Y-%m-%d"), realized, cumulative))
        day += timedelta(days=1)

    return result


def _period_label(day, gran: str) -> str:
    """날짜(date)를 기간 단위별 라벨 문자열로 바꾼다.

    - day(일):     "2026-05-29"
    - week(주):    "2026-W22"  (ISO 주 번호)
    - month(월):   "2026-05"
    - quarter(분기): "2026-Q2"
    - year(년):    "2026"
    """
    if gran == "day":
        return day.strftime("%Y-%m-%d")
    if gran == "week":
        iso = day.isocalendar()  # (ISO연도, 주번호, 요일)
        return f"{iso[0]}-W{iso[1]:02d}"
    if gran == "month":
        return f"{day.year}-{day.month:02d}"
    if gran == "quarter":
        q = (day.month - 1) // 3 + 1
        return f"{day.year}-Q{q}"
    if gran == "year":
        return f"{day.year}"
    return day.strftime("%Y-%m-%d")


def _aggregate(daily_rows, gran: str):
    """일별 데이터를 기간 단위(gran)로 집계한다.

    - 일일손익: 기간 안의 합계 (예: 한 주 동안의 실현손익 총합)
    - 누적손익: 기간 안의 마지막 값 (누적은 이미 running total이므로 마지막 값이 곧 그 기간 말 누적)

    Args:
        daily_rows: [("YYYY-MM-DD", 일일손익, 누적손익), ...] 날짜 오름차순
        gran:       "day" | "week" | "month" | "quarter" | "year"

    Returns:
        [(라벨, 일일손익합, 기간말누적), ...] 시간 순서
    """
    buckets = {}   # 라벨 -> [일일합, 마지막누적]
    order   = []   # 라벨 등장 순서 보존 (daily_rows가 이미 오름차순)

    for date_str, daily, cumulative in daily_rows:
        try:
            day = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        label = _period_label(day, gran)
        if label not in buckets:
            buckets[label] = [0, 0]
            order.append(label)
        buckets[label][0] += daily        # 일일손익 누적 합
        buckets[label][1]  = cumulative   # 마지막 값으로 계속 덮어씀 → 기간말 누적

    return [(label, buckets[label][0], buckets[label][1]) for label in order]


def _compute_all_blocks(daily_rows):
    """5가지 기간 단위 집계 결과를 한꺼번에 만든다.

    Returns:
        {"일": [...], "주": [...], "월": [...], "분기": [...], "년": [...]}
    """
    blocks = {}
    for ko, en in zip(GRAN_KO, GRAN_EN):
        blocks[ko] = _aggregate(daily_rows, en)
    return blocks


# ─────────────────────────────────────────
# 내부 함수 — 숨김 데이터 시트 쓰기
# ─────────────────────────────────────────

def _write_data_sheet(spreadsheet, blocks):
    """5개 집계 블록을 숨김 시트("차트데이터")에 나란히 기록한다.

    블록 배치 (각 블록은 라벨/일일/누적 3열, 사이에 빈 열 1개):
        일   : A B C
        주   : E F G
        월   : I J K
        분기 : M N O
        년   : Q R S

    Returns:
        max_len: 가장 긴 블록의 데이터 행 수 (보통 '일' 블록)
    """
    import gspread

    # 기존 데이터 시트 삭제 후 새로 생성 (항상 최신값으로 덮어씀)
    try:
        old = spreadsheet.worksheet(DATA_SHEET_NAME)
        spreadsheet.del_worksheet(old)
    except gspread.WorksheetNotFound:
        pass

    max_len = max((len(blocks[ko]) for ko in GRAN_KO), default=0)
    height  = max_len + 1   # 헤더 1행 + 데이터
    width   = 19            # A~S (5블록 × 3열 + 사이 빈 열)

    ws_data = spreadsheet.add_worksheet(
        title=DATA_SHEET_NAME,
        rows=max(height + 5, 10),
        cols=width + 1,
    )

    # 빈 격자 준비 (모두 빈 문자열)
    grid = [["" for _ in range(width)] for _ in range(height)]

    # 블록별로 채우기
    for bi, ko in enumerate(GRAN_KO):
        col0 = bi * 4  # 0,4,8,12,16
        # 헤더
        grid[0][col0]     = f"기간({ko})"
        grid[0][col0 + 1] = "일일손익"
        grid[0][col0 + 2] = "누적손익"
        # 데이터
        for ri, (label, daily, cumulative) in enumerate(blocks[ko], start=1):
            grid[ri][col0]     = label
            grid[ri][col0 + 1] = daily
            grid[ri][col0 + 2] = cumulative

    # RAW: 라벨("2026-W22" 등)이 날짜/수식으로 오해되지 않도록 그대로 저장
    ws_data.update(values=grid, range_name="A1", value_input_option="RAW")
    print(f"[차트] '{DATA_SHEET_NAME}' 집계 데이터 기록 완료 (최대 {max_len}행)")

    return ws_data, max_len


# ─────────────────────────────────────────
# 내부 함수 — 손익차트 시트 (드롭다운 + 수식 + 차트)
# ─────────────────────────────────────────

def _read_prev_selection(spreadsheet) -> str:
    """기존 손익차트 시트의 드롭다운(F1) 선택값을 읽어 반환한다.

    시트가 없거나 값이 이상하면 기본값 "일"을 반환한다.
    (차트를 매번 새로 그려도 사용자가 고른 기간 단위가 유지되도록)
    """
    import gspread
    try:
        ws = spreadsheet.worksheet(CHART_SHEET_NAME)
        val = (ws.acell(DROPDOWN_CELL).value or "").strip()
        if val in GRAN_KO:
            return val
    except gspread.WorksheetNotFound:
        pass
    except Exception:
        pass
    return "일"


def _view_formulas(max_len: int):
    """손익차트 A2/B2/C2에 넣을 배열 수식 3개를 만든다.

    드롭다운(F1) 선택값에 따라 '차트데이터'의 해당 블록을 골라
    라벨/일일손익/누적손익 열을 채운다.
    선택한 블록의 실제 데이터 개수를 넘는 행은 ""(빈칸)으로 둬서
    차트에 0이 잘못 찍히지 않도록 한다.
    """
    sht = DATA_SHEET_NAME
    end = max_len + 1  # 데이터 마지막 행 (1-based, 헤더가 1행이므로 +1)

    # 블록별 열 문자
    label_cols = ["A", "E", "I", "M", "Q"]
    daily_cols = ["B", "F", "J", "N", "R"]
    cum_cols   = ["C", "G", "K", "O", "S"]

    # 드롭다운 F1 → 1~5 번호
    idx = 'MATCH($F$1,{"일","주","월","분기","년"},0)'

    # 선택 블록의 데이터 개수 (라벨 열 COUNTA)
    cnt_args   = ",".join(f"COUNTA('{sht}'!${c}$2:${c}${end})" for c in label_cols)
    choose_cnt = f"CHOOSE({idx},{cnt_args})"

    # 행 번호 드라이버 (2..end → 1..max_len)
    row_drv = f"(ROW('{sht}'!$A$2:$A${end})-1)"

    def choose(cols):
        args = ",".join(f"'{sht}'!${c}$2:${c}${end}" for c in cols)
        return f"CHOOSE({idx},{args})"

    a = f"=ARRAYFORMULA(IF({row_drv}<={choose_cnt},{choose(label_cols)},\"\"))"
    b = f"=ARRAYFORMULA(IF({row_drv}<={choose_cnt},{choose(daily_cols)},\"\"))"
    c = f"=ARRAYFORMULA(IF({row_drv}<={choose_cnt},{choose(cum_cols)},\"\"))"
    return a, b, c


def _build_chart_sheet(spreadsheet, max_len: int, prev_sel: str):
    """손익차트 시트를 새로 만들고 헤더·드롭다운·수식 표를 채운다.

    Returns:
        ws: 새로 만든 손익차트 Worksheet
    """
    import gspread

    # 기존 손익차트 시트 삭제 (차트 포함 완전 초기화)
    try:
        old_ws = spreadsheet.worksheet(CHART_SHEET_NAME)
        spreadsheet.del_worksheet(old_ws)
        print(f"[차트] 기존 '{CHART_SHEET_NAME}' 시트 삭제 완료")
    except gspread.WorksheetNotFound:
        pass

    # 새 시트 생성 (행: 헤더+데이터+여유 / 열: 차트 영역까지 넉넉히)
    num_rows = max(max_len + 11, 50)
    ws = spreadsheet.add_worksheet(title=CHART_SHEET_NAME, rows=num_rows, cols=20)
    print(f"[차트] '{CHART_SHEET_NAME}' 시트 새로 생성")

    # ① 표 헤더 (A1:C1) — 차트 범례·축 이름으로 사용됨
    ws.update(
        values=[["기간", "일일 실현손익(원)", "누적 실현손익(원)"]],
        range_name="A1:C1",
        value_input_option="RAW",
    )

    # ② 드롭다운 안내 문구(E1) + 선택값(F1)
    ws.update(
        values=[["기간 단위 선택 ▶", prev_sel]],
        range_name=f"{DROPDOWN_LABEL_CELL}:{DROPDOWN_CELL}",
        value_input_option="RAW",
    )

    # ③ 배열 수식 표 (A2/B2/C2) — 드롭다운에 따라 자동으로 내용이 바뀜
    a, b, c = _view_formulas(max_len)
    ws.update(values=[[a, b, c]], range_name="A2:C2", value_input_option="USER_ENTERED")

    print(f"[차트] 드롭다운 + 수식 표 작성 완료 (현재 선택: {prev_sel})")
    return ws


def _add_dropdown_validation(requests, sheet_id):
    """F1 셀에 일/주/월/분기/년 드롭다운(데이터 확인) 규칙을 추가한다."""
    requests.append({
        "setDataValidation": {
            "range": {
                "sheetId":          sheet_id,
                "startRowIndex":    0,   # 1행
                "endRowIndex":      1,
                "startColumnIndex": 5,   # F열
                "endColumnIndex":   6,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in GRAN_KO],
                },
                "showCustomUi": True,   # 셀에 화살표(드롭다운 UI) 표시
                "strict": True,         # 목록 밖 값 입력 금지
            },
        }
    })


def _add_combo_chart_request(requests, sheet_id, max_len: int):
    """콤보 차트(파란 막대=일일 / 빨간 선=누적) 생성 요청을 추가한다.

    데이터 출처: A열(기간), B열(일일), C열(누적) — 모두 수식으로 채워지는 표.
    드롭다운을 바꾸면 이 셀들이 다시 계산되어 차트가 자동으로 바뀐다.
    """
    start_row = 0
    end_row   = max_len + 1  # 헤더 1행 + 데이터 max_len행

    requests.append({
        "addChart": {
            "chart": {
                "spec": {
                    "title": "실현 손익 추이 (기간 단위는 F1 드롭다운으로 선택)",
                    "basicChart": {
                        "chartType": "COMBO",
                        "legendPosition": "TOP_LEGEND",
                        "axis": [
                            {"position": "BOTTOM_AXIS", "title": "기간"},
                            {"position": "LEFT_AXIS",  "title": "손익 (원)"},
                        ],
                        # X축: A열 기간 라벨
                        "domains": [{
                            "domain": {"sourceRange": {"sources": [{
                                "sheetId":          sheet_id,
                                "startRowIndex":    start_row,
                                "endRowIndex":      end_row,
                                "startColumnIndex": 0,  # A열
                                "endColumnIndex":   1,
                            }]}}
                        }],
                        "series": [
                            # 파란 막대 — B열 일일 실현손익
                            {
                                "series": {"sourceRange": {"sources": [{
                                    "sheetId":          sheet_id,
                                    "startRowIndex":    start_row,
                                    "endRowIndex":      end_row,
                                    "startColumnIndex": 1,  # B열
                                    "endColumnIndex":   2,
                                }]}},
                                "targetAxis": "LEFT_AXIS",
                                "type": "COLUMN",
                                "colorStyle": {"rgbColor": {"red": 0.44, "green": 0.68, "blue": 0.83}},
                            },
                            # 빨간 선 — C열 누적 실현손익
                            {
                                "series": {"sourceRange": {"sources": [{
                                    "sheetId":          sheet_id,
                                    "startRowIndex":    start_row,
                                    "endRowIndex":      end_row,
                                    "startColumnIndex": 2,  # C열
                                    "endColumnIndex":   3,
                                }]}},
                                "targetAxis": "LEFT_AXIS",
                                "type": "LINE",
                                "colorStyle": {"rgbColor": {"red": 0.84, "green": 0.15, "blue": 0.15}},
                                "lineStyle": {"width": 2},
                            },
                        ],
                        "headerCount": 1,  # 첫 행을 범례 이름으로 사용
                    }
                },
                # 차트 위치: H2 셀 기준 (드롭다운 E1:F1과 겹치지 않게 오른쪽 배치)
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId":     sheet_id,
                            "rowIndex":    1,  # 2행
                            "columnIndex": 7,  # H열
                        },
                        "widthPixels":  1000,
                        "heightPixels": 580,
                    }
                },
            }
        }
    })


def _hide_sheet_request(requests, sheet_id):
    """데이터 시트를 숨김 처리하는 요청을 추가한다 (보기 깔끔하게)."""
    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "hidden": True},
            "fields": "hidden",
        }
    })


# ─────────────────────────────────────────
# 공개 함수 (단일 진입점)
# ─────────────────────────────────────────

def update_pnl_chart():
    """
    실현 손익 차트를 구글 스프레드시트에 업데이트한다.

    "포트폴리오 추이"의 일별 데이터를 읽어 일/주/월/분기/년 5가지로 집계한 뒤,
    "손익차트" 탭에 드롭다운(F1) + 콤보 차트(일일 막대 + 누적 선)를 그린다.
    드롭다운으로 기간 단위를 바꾸면 차트가 자동으로 그 기준으로 바뀐다.
    시트가 이미 있으면 (기간 단위 선택값을 유지한 채) 새로 그린다.
    """
    try:
        # ① 구글 시트 연결
        print("[차트] 구글 스프레드시트 연결 중...")
        spreadsheet = _get_spreadsheet()
        print(f"[차트] 연결 완료: '{spreadsheet.title}'")

        # ② 일별 데이터 생성
        print(f"[차트] '{PORTFOLIO_SHEET_NAME}'에서 실현손익/누적수익금 읽는 중...")
        daily_rows = _build_daily_rows(spreadsheet)
        if not daily_rows:
            print("[차트] 그릴 데이터가 없습니다. 종료합니다.")
            return
        print(f"[차트] 일별 {len(daily_rows)}일치 데이터 준비 완료")

        # ③ 5가지 기간 단위로 집계
        blocks = _compute_all_blocks(daily_rows)

        # ④ 기존 드롭다운 선택값 읽어두기 (새로 그려도 선택 유지)
        prev_sel = _read_prev_selection(spreadsheet)

        # ⑤ 숨김 데이터 시트에 집계 결과 기록
        ws_data, max_len = _write_data_sheet(spreadsheet, blocks)
        if max_len <= 0:
            print("[차트] 집계된 데이터가 없습니다. 종료합니다.")
            return

        # ⑥ 손익차트 시트 (재)생성 + 드롭다운 + 수식 표
        ws_chart = _build_chart_sheet(spreadsheet, max_len, prev_sel)

        # ⑦ 드롭다운 규칙 + 차트 + 데이터시트 숨김을 한 번의 API 호출로 적용
        requests = []
        _add_dropdown_validation(requests, ws_chart.id)
        _add_combo_chart_request(requests, ws_chart.id, max_len)
        _hide_sheet_request(requests, ws_data.id)
        spreadsheet.batch_update({"requests": requests})
        print("[차트] 드롭다운·콤보 차트 생성 + 데이터 시트 숨김 완료")

        print(
            f"\n[차트] ✅ 완료!\n"
            f"구글 시트 '{spreadsheet.title}' 의 '{CHART_SHEET_NAME}' 탭에서\n"
            f"F1 드롭다운으로 일/주/월/분기/년을 골라 보세요. (현재: {prev_sel})"
        )

    except FileNotFoundError as e:
        print(f"[차트] 오류: {e}")
    except RuntimeError as e:
        print(f"[차트] 오류: {e}")
    except ImportError:
        print("[차트] gspread 또는 oauth2client 가 설치되지 않았습니다.\n"
              "pip install gspread oauth2client 를 실행해 주세요.")
    except Exception as e:
        print(f"[차트] 예상치 못한 오류: {e}")


# ─────────────────────────────────────────
# 단독 실행 지원 (python chart_updater.py)
# ─────────────────────────────────────────

if __name__ == "__main__":
    update_pnl_chart()
