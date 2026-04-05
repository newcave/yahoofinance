import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import plotly.graph_objects as go

# ── 페이지 설정 ───────────────────────────────────────────────────
st.set_page_config(
    page_title="글로벌 경제 지표 대시보드",
    page_icon="📈",
    layout="centered",
)

# ── 상수 ─────────────────────────────────────────────────────────
INDICES = {"S&P 500": "^GSPC", "다우존스": "^DJI", "나스닥": "^IXIC"}

COLORS = {
    "S&P 500":  "#4C9EFF",
    "다우존스": "#34D399",
    "나스닥":   "#A78BFA",
    "두바이유": "#FB923C",
    "SMP(육지)":"#F43F5E",
    "SMP(제주)":"#818CF8",
}

KST = pytz.timezone("Asia/Seoul")
ET  = pytz.timezone("America/New_York")

SMP_API_URL = (
    "https://apis.data.go.kr/B552115/SmpWithForecastDemand"
    "/getSmpWithForecastDemand"
)

# ── 데이터 수집 ───────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_indices(days: int = 10) -> pd.DataFrame:
    """미국 3대 지수 — yfinance"""
    end, start = datetime.now(ET), datetime.now(ET) - timedelta(days=days)
    series_list = []
    for name, ticker in INDICES.items():
        try:
            raw = yf.download(ticker, start=start, end=end,
                              progress=False, auto_adjust=True)
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            close = raw["Close"].rename(name)
            series_list.append(close)
        except Exception as e:
            st.warning(f"{name} 로드 실패: {e}")
    if not series_list:
        return pd.DataFrame()
    combined = pd.concat(series_list, axis=1).dropna()
    combined.index = pd.to_datetime(combined.index).tz_localize(None)
    return combined.tail(3)


@st.cache_data(ttl=3600)
def load_dubai(days: int = 10) -> pd.Series:
    """두바이유 — 오피넷 POST 크롤"""
    try:
        end, start = datetime.today(), datetime.today() - timedelta(days=days)
        resp = requests.post(
            "https://www.opinet.co.kr/gloptotSelect.do",
            data={
                "startDtYear":  start.strftime("%Y"),
                "startDtMonth": start.strftime("%m"),
                "startDtDay":   start.strftime("%d"),
                "endDtYear":    end.strftime("%Y"),
                "endDtMonth":   end.strftime("%m"),
                "endDtDay":     end.strftime("%d"),
                "gubunList": "D", "viewType": "D",
            },
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": "https://www.opinet.co.kr/gloptotSelect.do"},
            timeout=12,
        )
        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        records = {}
        for row in table.find_all("tr")[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) < 2 or not cols[1]:
                continue
            try:
                val = float(cols[1])
            except ValueError:
                continue
            if not (10 < val < 500):   # 이상값 제거
                continue
            # 한글 날짜 → datetime (re.sub으로 안정 파싱)
            raw_date = re.sub(r"[년월]", "-", cols[0]).replace("일", "").strip()
            parts = [p.strip() for p in raw_date.split("-") if p.strip()]
            if len(parts) < 3:
                continue
            try:
                date = datetime(2000 + int(parts[0]), int(parts[1]), int(parts[2]))
                records[date] = val
            except ValueError:
                continue
        s = pd.Series(records, name="두바이유")
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index().tail(3)
    except Exception as e:
        st.warning(f"두바이유 수집 실패: {e}")
        return pd.Series(name="두바이유", dtype=float)


@st.cache_data(ttl=3600)
def load_smp(days: int = 5) -> tuple[pd.Series, pd.Series]:
    """SMP 육지/제주 — 공공데이터포털 공식 API (시간별 → 일평균)
    
    ※ serviceKey는 params 딕셔너리 대신 URL에 직접 삽입
       → requests의 자동 인코딩으로 인한 이중 인코딩(+→%2B) 방지
    """
    try:
        api_key = st.secrets["keys"]["DATA_GO_KR"]
    except Exception:
        st.warning("SMP API 키 미설정 — Streamlit Cloud > Settings > Secrets 확인")
        empty = pd.Series(dtype=float)
        return empty.rename("SMP(육지)"), empty.rename("SMP(제주)")

    land_rec: dict = {}
    jeju_rec: dict = {}

    for delta in range(days):
        target   = datetime.today() - timedelta(days=delta)
        date_str = target.strftime("%Y%m%d")

        for area_cd, bucket in [("1", land_rec), ("9", jeju_rec)]:
            try:
                # serviceKey의 +, / 를 %2B, %2F 로 인코딩 후 URL에 직접 삽입
                encoded_key = quote(api_key, safe="")
                url = (
                    f"{SMP_API_URL}"
                    f"?serviceKey={encoded_key}"
                    f"&pageNo=1&numOfRows=24&dataType=JSON"
                    f"&areaCd={area_cd}&metrDt={date_str}"
                )
                resp = requests.get(url, timeout=12)

                # 응답이 JSON이 아닌 경우 (XML 오류 메시지 등) 처리
                if not resp.text.strip().startswith("{"):
                    # 디버그: 첫 호출 시 응답 일부 표시
                    if delta == 0 and area_cd == "1":
                        st.warning(f"SMP API 응답이 JSON이 아님 (area={area_cd}, {date_str}): {resp.text[:200]}")
                    continue

                data  = resp.json()
                items = (
                    data.get("response", {})
                        .get("body", {})
                        .get("items", {})
                        .get("item", [])
                )
                if not items:
                    continue

                # 응답 필드명 fallback (API 버전에 따라 다를 수 있음)
                prices = []
                for item in items:
                    for key in ("smp", "landsmp", "landSmp", "jejusmp", "SMP"):
                        if key in item and item[key] is not None:
                            try:
                                prices.append(float(item[key]))
                            except ValueError:
                                pass
                            break

                if prices:
                    bucket[datetime(target.year, target.month, target.day)] = (
                        round(sum(prices) / len(prices), 2)
                    )

            except Exception as e:
                st.warning(f"SMP API 오류 (area={area_cd}, {date_str}): {e}")

    def _to_series(rec: dict, name: str) -> pd.Series:
        if not rec:
            return pd.Series(name=name, dtype=float)
        s = pd.Series(rec, name=name)
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index().tail(3)

    return _to_series(land_rec, "SMP(육지)"), _to_series(jeju_rec, "SMP(제주)")


# ── 헬퍼 ─────────────────────────────────────────────────────────

def delta_info(cur: float, prev: float) -> tuple:
    diff = cur - prev
    pct  = diff / prev * 100
    sign = "+" if diff >= 0 else ""
    return diff, f"{sign}{diff:,.2f} ({sign}{pct:.2f}%)"


def get_nearest(series: pd.Series, row_date) -> float | None:
    valid = [d for d in series.index if d.date() <= row_date]
    return float(series[valid[-1]]) if valid else None


def align_to_index(base_index, series: pd.Series) -> list:
    return [get_nearest(series, d.date()) for d in base_index]


def fmt(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "N/A"


def minmax_norm(df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=df.index)
    for col in df.columns:
        s = df[col].dropna()
        mn, mx = s.min(), s.max()
        result[col] = 50.0 if mx == mn else (df[col] - mn) / (mx - mn) * 100
    return result


# ── 메트릭 렌더 헬퍼 ──────────────────────────────────────────────

def render_metric(col, label: str, cur, prev=None, prefix="", suffix=""):
    """delta 포함 or 단순 st.metric 렌더링."""
    display = f"{prefix}{cur:,.2f}{suffix}" if isinstance(cur, float) else "N/A"
    with col:
        if cur is None:
            st.metric(label, "N/A")
            return
        if prev is not None:
            diff, lbl = delta_info(cur, prev)
            st.metric(label, display, lbl,
                      delta_color="normal" if diff >= 0 else "inverse")
        else:
            st.metric(label, display)


# ── UI ───────────────────────────────────────────────────────────

st.title("📈 글로벌 경제 지표 대시보드")
st.caption(
    f"기준: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST  |  "
    "지수: Yahoo Finance  |  두바이유: 오피넷  |  SMP: 공공데이터포털 API"
)

with st.spinner("데이터 불러오는 중…"):
    df        = load_indices()
    dubai     = load_dubai()
    smp_land, smp_jeju = load_smp()

if df.empty:
    st.error("지수 데이터를 가져오지 못했습니다.")
    st.stop()

date_labels = [d.strftime("%m/%d (%a)") for d in df.index]

# ── 날짜별 섹션 ──────────────────────────────────────────────────
for i in range(len(df)):
    st.subheader(f"🗓 {date_labels[i]}")
    row_date = df.index[i].date()
    prev_date = df.index[i - 1].date() if i > 0 else None
    cols = st.columns(6)

    # 3대 지수
    for j, name in enumerate(INDICES.keys()):
        cur  = float(df.iloc[i][name])
        prev = float(df.iloc[i - 1][name]) if i > 0 else None
        render_metric(cols[j], name, cur, prev)

    # 두바이유
    cur_d  = get_nearest(dubai, row_date)
    prev_d = get_nearest(dubai, prev_date) if prev_date else None
    render_metric(cols[3], "두바이유($/bbl)", cur_d, prev_d, prefix="$")

    # SMP 육지
    cur_sl  = get_nearest(smp_land, row_date)
    prev_sl = get_nearest(smp_land, prev_date) if prev_date else None
    render_metric(cols[4], "SMP육지(원/kWh)", cur_sl, prev_sl)

    # SMP 제주
    cur_sj  = get_nearest(smp_jeju, row_date)
    prev_sj = get_nearest(smp_jeju, prev_date) if prev_date else None
    render_metric(cols[5], "SMP제주(원/kWh)", cur_sj, prev_sj)

    st.divider()


# ── Plotly 차트 ──────────────────────────────────────────────────
st.subheader("📊 6변수 추이 비교 (민맥스 정규화)")

combined = df.copy()
combined["두바이유"]  = align_to_index(df.index, dubai)
combined["SMP(육지)"] = align_to_index(df.index, smp_land)
combined["SMP(제주)"] = align_to_index(df.index, smp_jeju)

norm = minmax_norm(combined)
norm.index = date_labels

UNITS = {"두바이유": "$/bbl", "SMP(육지)": "원/kWh", "SMP(제주)": "원/kWh"}

fig = go.Figure()

# 기준선 (0 / 50 / 100)
for y_val, label in [(100, "최고"), (50, "중간"), (0, "최저")]:
    fig.add_hline(
        y=y_val,
        line=dict(color="rgba(255,255,255,0.10)", width=1, dash="dot"),
        annotation_text=label,
        annotation_position="left",
        annotation_font=dict(size=10, color="rgba(200,200,200,0.4)"),
    )

# 각 지표 trace
for col in norm.columns:
    unit     = UNITS.get(col, "pt")
    raw_vals = combined[col].values
    color    = COLORS.get(col, "#888888")

    fig.add_trace(go.Scatter(
        x=norm.index,
        y=norm[col],
        name=col,
        mode="lines+markers+text",
        line=dict(color=color, width=2.5),
        marker=dict(
            size=10,
            color=color,
            line=dict(width=2, color="#0e1117"),   # 마커 테두리 → 가시성↑
        ),
        text=[f"{v:,.1f}" if v is not None else "" for v in raw_vals],
        textposition="top center",
        textfont=dict(size=10, color=color),
        customdata=[[v, unit] for v in raw_vals],
        hovertemplate=(
            f"<b style='color:{color}'>{col}</b><br>"
            "날짜: %{x}<br>"
            "정규화: %{y:.1f}<br>"
            "실제값: %{customdata[0]:,.2f} %{customdata[1]}"
            "<extra></extra>"
        ),
    ))

fig.update_layout(
    height=500,
    margin=dict(l=10, r=10, t=20, b=10),
    legend=dict(
        orientation="h",
        y=-0.22,
        x=0.5,
        xanchor="center",
        font=dict(size=12, color="#cccccc"),
        bgcolor="rgba(0,0,0,0)",
        borderwidth=0,
    ),
    yaxis=dict(
        title="정규화 지수 (각 변수 독립, 0=최저·100=최고)",
        title_font=dict(size=11, color="#888888"),
        tickfont=dict(size=11, color="#888888"),
        tickvals=[0, 25, 50, 75, 100],
        showgrid=True,
        gridcolor="rgba(255,255,255,0.06)",
        zeroline=False,
        range=[-12, 118],
    ),
    xaxis=dict(
        showgrid=False,
        tickfont=dict(size=13, color="#dddddd"),
    ),
    plot_bgcolor="#0e1117",
    paper_bgcolor="#0e1117",
    font=dict(color="#cccccc", family="sans-serif"),
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1a1f2e",
        bordercolor="rgba(255,255,255,0.15)",
        font=dict(size=12, color="#ffffff"),
        namelength=-1,
    ),
)

st.plotly_chart(fig, width="stretch")
st.caption(
    "※ 각 지표가 표시 기간 내 자신의 최솟값→0, 최댓값→100으로 독립 정규화됩니다.\n"
    "※ SMP는 공공데이터포털 공식 API 기준 시간별 평균값 (일별 집계)"
)

# ── 원본 데이터 ──────────────────────────────────────────────────
with st.expander("원본 데이터 보기"):
    display = combined.copy()
    display.index = date_labels
    st.dataframe(display.style.format(fmt), width="stretch")

# ── 새로고침 ─────────────────────────────────────────────────────
if st.button("🔄 새로고침"):
    st.cache_data.clear()
    st.rerun()
