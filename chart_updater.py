# chart_updater.py
# 실현 손익 차트 업데이터
#
# 구글 스프레드시트의 "포트폴리오 추이" 시트에서 일별 실현 손익을 읽어,
# "손익차트" 시트에 콤보 차트(막대 + 선)를 자동으로 그린다.
#
# 사용법:
#   python chart_updater.py
#
# 다른 파일에서 import해서 사용하는 방법:
#   from chart_updater import update_pnl_chart
#   update_pnl_chart()

import json
import os
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

# 구글 시트 탭 이름
CHART_SHEET_NAME = "손익차트"  # 차트를 그릴 탭

# trade_ledger.json 경로 (스크립트 위치 기준)
_DIR         = os.path.dirname(os.path.abspath(__file__))
LEDGER_FILE  = os.path.join(_DIR, "trade_ledger.json")


# ─────────────────────────────────────────
# 내부 함수
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


def _build_chart_data():
    """
    trade_ledger.json에서 SELL 체결 건의 수익금을 날짜별로 합산해
    일일 손익과 누적 손익 목록을 반환한다.

    당일 수익금이 없는 날은 0으로 표시한다.
    profit_amount 필드가 없는 오래된 기록은 profit_rate에서 근사값으로 계산한다.

    반환값: [(MM/DD, 일일손익, 누적손익), ...]  날짜 오름차순
    """
    # trade_ledger.json 읽기
    if not os.path.exists(LEDGER_FILE):
        print(f"[차트] trade_ledger.json 없음 → 그릴 데이터가 없습니다.")
        return []

    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[차트] trade_ledger.json 읽기 오류: {e}")
        return []

    if not isinstance(records, list) or not records:
        print("[차트] trade_ledger.json에 데이터가 없습니다.")
        return []

    # 날짜별 수익금 합산 (SELL 거래만)
    daily_pnl: dict[str, int] = defaultdict(int)

    for rec in records:
        # 매도 건만 집계
        if rec.get("side") != "SELL":
            continue

        ts_str = rec.get("ts_kst", "")
        if len(ts_str) < 10:
            continue
        date_str = ts_str[:10]  # "YYYY-MM-DD"

        # ① profit_amount 필드가 있으면 그대로 사용
        profit_amount = rec.get("profit_amount", None)

        # ② 없으면 profit_rate와 gross_amount로 근사 계산
        #    profit_rate = (매도가 - 매입가) / 매입가 × 100
        #    profit_amount ≈ gross_amount × rate / (1 + rate)
        if profit_amount is None:
            profit_rate  = rec.get("profit_rate", None)
            gross_amount = rec.get("gross_amount", 0)
            if profit_rate is not None and isinstance(profit_rate, (int, float)) and gross_amount:
                r = profit_rate / 100
                denom = 1 + r
                profit_amount = int(gross_amount * r / denom) if abs(denom) > 1e-9 else 0
            else:
                profit_amount = 0

        daily_pnl[date_str] += int(profit_amount)

    if not daily_pnl:
        print("[차트] 매도 체결 내역이 없습니다.")
        return []

    # 날짜 오름차순 정렬 후 누적 손익 계산
    result = []
    cumulative = 0

    for date_str in sorted(daily_pnl.keys()):
        daily      = daily_pnl[date_str]
        cumulative += daily
        month_day  = f"{date_str[5:7]}/{date_str[8:10]}"  # "YYYY-MM-DD" → "MM/DD"
        result.append((month_day, daily, cumulative))

    return result


def _write_chart_sheet(spreadsheet, rows):
    """
    "손익차트" 시트를 새로 만들고 데이터를 기록한다.

    기존 시트가 있으면 삭제하고 새로 만든다
    (차트를 포함한 시트를 통째로 초기화하는 것이 가장 깔끔하다).

    Args:
        spreadsheet: gspread Spreadsheet 객체
        rows:        [(MM/DD, 일일손익, 누적손익), ...] 데이터

    Returns:
        ws: 새로 만든 Worksheet 객체
    """
    import gspread

    # 기존 "손익차트" 시트가 있으면 삭제 (차트 포함 완전 초기화)
    try:
        old_ws = spreadsheet.worksheet(CHART_SHEET_NAME)
        spreadsheet.del_worksheet(old_ws)
        print(f"[차트] 기존 '{CHART_SHEET_NAME}' 시트 삭제 완료")
    except gspread.WorksheetNotFound:
        pass  # 없으면 그냥 새로 만들기

    # 새 시트 생성 (행 수: 헤더 1행 + 데이터 n행 + 여유 10행)
    num_rows = max(len(rows) + 11, 50)
    ws = spreadsheet.add_worksheet(
        title=CHART_SHEET_NAME,
        rows=num_rows,
        cols=5,  # A~E (차트가 E열 오른쪽에 위치)
    )
    print(f"[차트] '{CHART_SHEET_NAME}' 시트 새로 생성")

    # 헤더 + 데이터를 리스트로 준비
    # 첫 행: 열 제목 (차트 범례에 자동으로 사용됨)
    sheet_data = [["날짜", "일일 실현손익(원)", "누적 실현손익(원)"]]
    for month_day, daily, cumulative in rows:
        sheet_data.append([month_day, daily, cumulative])

    # 한 번의 API 호출로 전체 데이터 업로드
    # RAW: "04/17" 같은 문자열이 날짜나 분수로 오해되지 않도록 그대로 저장
    ws.update(values=sheet_data, range_name="A1", value_input_option="RAW")
    print(f"[차트] 데이터 {len(rows)}일치 기록 완료")

    return ws


def _add_combo_chart(spreadsheet, ws, num_data_rows):
    """
    "손익차트" 시트에 콤보 차트를 생성한다.

    파란 막대(COLUMN): B열 — 일일 실현손익
    빨간 선(LINE):     C열 — 누적 실현손익

    Google Sheets API v4 batchUpdate 를 직접 호출해 차트를 생성한다.

    Args:
        spreadsheet:   gspread Spreadsheet 객체
        ws:            차트를 그릴 Worksheet 객체
        num_data_rows: 헤더를 제외한 데이터 행 수
    """
    sheet_id = ws.id

    # 데이터 범위 (행 인덱스는 0-based, 시트 1행 = 인덱스 0)
    # 헤더(인덱스 0) + 데이터(인덱스 1 ~ num_data_rows)
    start_row = 0
    end_row   = num_data_rows + 1  # exclusive

    chart_request = {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "실현 손익 추이",
                    "basicChart": {
                        # 막대와 선을 하나의 차트에 혼합하는 COMBO 타입
                        "chartType": "COMBO",
                        "legendPosition": "BOTTOM_LEGEND",
                        "axis": [
                            {
                                # X축: 날짜 (A열)
                                "position": "BOTTOM_AXIS",
                                "title": "날짜",
                            },
                            {
                                # Y축: 손익 금액 (B, C열 공용)
                                "position": "LEFT_AXIS",
                                "title": "손익 (원)",
                            },
                        ],
                        # X축 데이터 출처: A열 날짜 레이블
                        "domains": [
                            {
                                "domain": {
                                    "sourceRange": {
                                        "sources": [
                                            {
                                                "sheetId":          sheet_id,
                                                "startRowIndex":    start_row,
                                                "endRowIndex":      end_row,
                                                "startColumnIndex": 0,  # A열
                                                "endColumnIndex":   1,
                                            }
                                        ]
                                    }
                                }
                            }
                        ],
                        "series": [
                            # 시리즈 1: 파란 막대 — B열 일일 실현손익
                            {
                                "series": {
                                    "sourceRange": {
                                        "sources": [
                                            {
                                                "sheetId":          sheet_id,
                                                "startRowIndex":    start_row,
                                                "endRowIndex":      end_row,
                                                "startColumnIndex": 1,  # B열
                                                "endColumnIndex":   2,
                                            }
                                        ]
                                    }
                                },
                                "targetAxis": "LEFT_AXIS",
                                "type": "COLUMN",  # 막대 차트
                                "colorStyle": {
                                    "rgbColor": {
                                        "red":   0.27,
                                        "green": 0.51,
                                        "blue":  0.71,
                                    }
                                },
                            },
                            # 시리즈 2: 빨간 선 — C열 누적 실현손익
                            {
                                "series": {
                                    "sourceRange": {
                                        "sources": [
                                            {
                                                "sheetId":          sheet_id,
                                                "startRowIndex":    start_row,
                                                "endRowIndex":      end_row,
                                                "startColumnIndex": 2,  # C열
                                                "endColumnIndex":   3,
                                            }
                                        ]
                                    }
                                },
                                "targetAxis": "LEFT_AXIS",
                                "type": "LINE",  # 선 차트
                                "colorStyle": {
                                    "rgbColor": {
                                        "red":   0.83,
                                        "green": 0.18,
                                        "blue":  0.18,
                                    }
                                },
                                # 선 두께 2픽셀
                                "lineStyle": {
                                    "width": 2,
                                },
                            },
                        ],
                        # 첫 행을 범례 이름으로 사용
                        # (B1: "일일 실현손익(원)", C1: "누적 실현손익(원)")
                        "headerCount": 1,
                    }
                },
                # 차트 위치: E2 셀 기준으로 배치, 크기 900×500 픽셀
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId":     sheet_id,
                            "rowIndex":    1,  # 2행
                            "columnIndex": 4,  # E열
                        },
                        "widthPixels":  900,
                        "heightPixels": 500,
                    }
                },
            }
        }
    }

    spreadsheet.batch_update({"requests": [chart_request]})
    print("[차트] 콤보 차트 생성 완료 (파란 막대: 일일 손익 / 빨간 선: 누적 손익)")


# ─────────────────────────────────────────
# 공개 함수 (단일 진입점)
# ─────────────────────────────────────────

def update_pnl_chart():
    """
    실현 손익 차트를 구글 스프레드시트에 업데이트한다.

    "포트폴리오 추이" 시트의 전체 데이터를 읽어 "손익차트" 시트에
    콤보 차트(일일 막대 + 누적 선)를 그린다.
    시트가 이미 있으면 삭제하고 새로 만들어 최신 상태로 유지한다.
    """
    try:
        # ① trade_ledger.json에서 날짜별 수익금 합산 (로컬 파일, API 호출 없음)
        print("[차트] trade_ledger.json에서 수익금 데이터 읽는 중...")
        rows = _build_chart_data()
        if not rows:
            print("[차트] 그릴 데이터가 없습니다. 종료합니다.")
            return

        print(f"[차트] {len(rows)}일치 데이터 준비 완료")

        # ② 구글 시트 연결 (차트 쓰기용)
        print("[차트] 구글 스프레드시트 연결 중...")
        spreadsheet = _get_spreadsheet()
        print(f"[차트] 연결 완료: '{spreadsheet.title}'")

        # ③ "손익차트" 시트 (재)생성 + 데이터 기록
        ws = _write_chart_sheet(spreadsheet, rows)

        # ④ 콤보 차트 생성
        _add_combo_chart(spreadsheet, ws, len(rows))

        print(
            f"\n[차트] ✅ 완료!\n"
            f"구글 시트 '{spreadsheet.title}' 에서 "
            f"'{CHART_SHEET_NAME}' 탭을 열어 확인하세요."
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
