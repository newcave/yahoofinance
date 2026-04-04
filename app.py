import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import plotly.graph_objects as go

# ── 페이지 설정 ──────────────────────────────────────────────────
st.set_page_config(
    page_title="미국 3대 지수 + 두바이유 + SMP",
    page_icon="📈",
    layout="centered",
)

# ── 상수 ────────────────────────────────────────────────────────
INDICES = {
    "S&P 500": "^GSPC",
    "다우존스": "^DJI",
    "나스닥":   "^IXIC",
}

COLORS = {
    "S&P 500":  "#4C9EFF",
    "다우존스": "#34D399",
    "나스닥":   "#A78BFA",
    "두바이유": "#FB923C",
    "SMP(육지)":"#F43F5E",
}

KST = pytz.timezone("Asia/Seoul")
ET  = pytz.timezone("America/New_York")

# ── KPX API 키 ───────────────────────────────────────────────────
# Encoding 키를 URL에 직접 삽입 → requests의 이중인코딩 방지
_KPX_KEY_ENC = "24g7V5xi4Wvb9S3eAk6u1%2BFjKNl6i0%2FCHSrkKKAZoHJZ%2Fvw7tNfXpSmiq0bXBcnK18B0jdDRxHntUOlOek%2B2DQ%3D%3D"
_KPX_BASE    = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"

def _kpx_url(req_date: str, num_of_rows: int = 24) -> str:
    return (f"{_KPX_BASE}?serviceKey={_KPX_KEY_ENC}"
            f"&numOfRows={num_of_rows}&pageNo=1"
            f"&returnType=json&reqDate={req_date}")


# ── 3대 지수 ────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_indices(days: int = 10) -> pd.DataFrame:
    end   = datetime.now(ET)
    start = end - timedelta(days=days)
    series_list = []
    for name, ticker in INDICES.items():
        try:
            raw = yf.download(ticker, start=start, end=end,
                              progress=False, auto_adjust=True)
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            close = raw["Close"].copy()
            close.name = name
            series_list.append(close)
        except Exception as e:
            st.warning(f"{name} 로드 실패: {e}")
    if not series_list:
        return pd.DataFrame()
    combined = pd.concat(series_list, axis=1).dropna()
    combined.index = pd.to_datetime(combined.index).tz_localize(None)
    return combined.tail(3)


# ── 두바이유 (오피넷) ────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_dubai(days: int = 10) -> pd.Series:
    try:
        end   = datetime.today()
        start = end - timedelta(days=days)
        resp = requests.post(
            "https://www.opinet.co.kr/gloptotSelect.do",
            data={
                "startDtYear":  start.strftime("%Y"),
                "startDtMonth": start.strftime("%m"),
                "startDtDay":   start.strftime("%d"),
                "endDtYear":    end.strftime("%Y"),
                "endDtMonth":   end.strftime("%m"),
                "endDtDay":     end.strftime("%d"),
                "gubunList":    "D",
                "viewType":     "D",
            },
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": "https://www.opinet.co.kr/gloptotSelect.do"},
            timeout=8,
        )
        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        rows  = table.find_all("tr")[1:]
        records = {}
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) >= 2 and cols[1]:
                try:
                    val = float(cols[1])
                except ValueError:
                    continue
                if val > 500:
                    continue
                raw_date = cols[0].replace("년","-").replace("월","-").replace("일","")
                parts = raw_date.split("-")
                date  = datetime(2000+int(parts[0]), int(parts[1]), int(parts[2]))
                records[date] = val
        s = pd.Series(records, name="두바이유")
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index().tail(3)
    except Exception as e:
        st.warning(f"두바이유 수집 실패: {e}")
        return pd.Series(name="두바이유", dtype=float)


# ── SMP (KPX 공공데이터 API) ──────────────────────────────────────
@st.cache_data(ttl=3600)
def load_smp(days: int = 7) -> pd.Series:
    """최근 days일을 순회하며 SMP 육지 데이터 수집 → tail(3) 반환"""
    records = {}

    for i in range(days):
        target   = datetime.today() - timedelta(days=i)
        req_date = target.strftime("%Y%m%d")
        try:
            resp = requests.get(_kpx_url(req_date, num_of_rows=24), timeout=8)
            data  = resp.json()
            items = (data.get("response", {})
                         .get("body", {})
                         .get("items", {})
                         .get("item", []))
            if isinstance(items, dict):
                items = [items]
            if not items:
                continue

            # 육지 SMP 필드 자동 탐색
            sample = items[0]
            land_key = next(
                (k for k in sample
                 if "land" in k.lower() or "smp" in k.lower()),
                None
            )

            if land_key:
                # 가중평균: 단일값이면 그대로, 시간대별이면 평균
                vals = [float(it[land_key]) for it in items if it.get(land_key)]
                if vals:
                    date = datetime.strptime(req_date, "%Y%m%d")
                    records[date] = sum(vals) / len(vals)

        except Exception:
            continue

    if not records:
        return pd.Series(name="SMP(육지)", dtype=float)

    s = pd.Series(records, name="SMP(육지)")
    s.index = pd.DatetimeIndex(s.index)
    return s.sort_index().tail(3)


# ── 헬퍼 ────────────────────────────────────────────────────────
def delta_info(cur: float, prev: float):
    diff = cur - prev
    pct  = diff / prev * 100
    sign = "+" if diff >= 0 else ""
    return diff, pct, f"{sign}{diff:,.2f} ({sign}{pct:.2f}%)"


def get_nearest(series: pd.Series, row_date) -> float | None:
    for d in reversed(series.index):
        if d.date() <= row_date:
            return float(series[d])
    return None


def align_to_index(base_index, series: pd.Series) -> list:
    return [get_nearest(series, d.date()) for d in base_index]


# ── 민맥스 탄력 정규화 ────────────────────────────────────────────
def minmax_elastic(df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=df.index)
    for col in df.columns:
        s = df[col].dropna()
        mn, mx = s.min(), s.max()
        result[col] = 50.0 if mx == mn else (df[col] - mn) / (mx - mn) * 100
    return result


# ── UI ──────────────────────────────────────────────────────────
st.title("📈 미국 3대 지수 + 두바이유 + SMP")
now_kst = datetime.now(KST)
st.caption(
    f"기준: {now_kst.strftime('%Y-%m-%d %H:%M')} KST  |  "
    "지수: Yahoo Finance  |  두바이유: 오피넷  |  SMP: KPX 공공데이터"
)

with st.spinner("데이터 불러오는 중…"):
    df    = load_indices()
    dubai = load_dubai()
    smp   = load_smp()

if df.empty:
    st.error("지수 데이터를 가져오지 못했습니다.")
    st.stop()

date_labels = [d.strftime("%m/%d (%a)") for d in df.index]

# ── 날짜별 섹션 ──────────────────────────────────────────────────
for i in range(len(df)):
    st.subheader(f"🗓 {date_labels[i]}")
    row_date = df.index[i].date()
    cols     = st.columns(5)

    # 3대 지수
    for j, name in enumerate(INDICES.keys()):
        cur = float(df.iloc[i][name])
        with cols[j]:
            if i > 0:
                prev = float(df.iloc[i-1][name])
                diff, _, label = delta_info(cur, prev)
                st.metric(name, f"{cur:,.2f}", label,
                          delta_color="normal" if diff >= 0 else "inverse")
            else:
                st.metric(name, f"{cur:,.2f}")

    # 두바이유
    with cols[3]:
        cur_v = get_nearest(dubai, row_date)
        if cur_v is not None:
            if i > 0:
                prev_v = get_nearest(dubai, df.index[i-1].date())
                if prev_v:
                    diff, _, label = delta_info(cur_v, prev_v)
                    st.metric("두바이유($/bbl)", f"${cur_v:.2f}", label,
                              delta_color="normal" if diff >= 0 else "inverse")
                else:
                    st.metric("두바이유($/bbl)", f"${cur_v:.2f}")
            else:
                st.metric("두바이유($/bbl)", f"${cur_v:.2f}")
        else:
            st.metric("두바이유($/bbl)", "N/A")

    # SMP
    with cols[4]:
        cur_v = get_nearest(smp, row_date)
        if cur_v is not None:
            if i > 0:
                prev_v = get_nearest(smp, df.index[i-1].date())
                if prev_v:
                    diff, _, label = delta_info(cur_v, prev_v)
                    st.metric("SMP(원/kWh)", f"{cur_v:.2f}", label,
                              delta_color="normal" if diff >= 0 else "inverse")
                else:
                    st.metric("SMP(원/kWh)", f"{cur_v:.2f}")
            else:
                st.metric("SMP(원/kWh)", f"{cur_v:.2f}")
        else:
            st.metric("SMP(원/kWh)", "N/A")

    st.divider()

# ── 5변수 민맥스 탄력 정규화 차트 ────────────────────────────────
st.subheader("📊 5변수 추이 비교 (민맥스 탄력 정규화)")

combined = df.copy()
combined["두바이유"]  = align_to_index(df.index, dubai)
combined["SMP(육지)"] = align_to_index(df.index, smp)

norm = minmax_elastic(combined)
norm.index = date_labels

UNITS = {"두바이유": "$/bbl", "SMP(육지)": "원/kWh"}

fig = go.Figure()
for col in norm.columns:
    unit     = UNITS.get(col, "pt")
    raw_vals = combined[col].values
    fig.add_trace(go.Scatter(
        x=norm.index,
        y=norm[col],
        name=col,
        mode="lines+markers",
        line=dict(color=COLORS.get(col, "#888"), width=2.5),
        marker=dict(size=7),
        customdata=[[v, unit] for v in raw_vals],
        hovertemplate=(
            f"<b>{col}</b><br>"
            "날짜: %{x}<br>"
            "정규화: %{y:.1f}<br>"
            "실제: %{customdata[0]:,.2f} %{customdata[1]}"
            "<extra></extra>"
        ),
    ))

fig.update_layout(
    height=420,
    margin=dict(l=0, r=0, t=10, b=0),
    legend=dict(orientation="h", y=-0.22),
    yaxis=dict(title="민맥스 정규화 (각 시리즈 독립)",
               showgrid=True, gridcolor="#2a2a2a", range=[-5, 105]),
    xaxis=dict(showgrid=False),
    plot_bgcolor="#0e1117",
    paper_bgcolor="#0e1117",
    font=dict(color="#ccc"),
    hovermode="x unified",
)
st.plotly_chart(fig, use_container_width=True)
st.caption("※ 각 변수가 표시 기간 내 자신의 최솟값→0, 최댓값→100 기준으로 독립 정규화됩니다.")

# ── SMP API 응답 디버그 (확인 후 삭제 가능) ───────────────────────
with st.expander("🔧 SMP API 응답 확인 (디버그)"):
    try:
        test_date = datetime.today().strftime("%Y%m%d")
        resp = requests.get(_kpx_url(test_date, num_of_rows=5), timeout=8)
        st.code(resp.text[:3000], language="json")
    except Exception as e:
        st.error(f"디버그 실패: {e}")

# ── 원본 데이터 ──────────────────────────────────────────────────
with st.expander("원본 데이터 보기"):
    display = combined.copy()
    display.index = date_labels
    st.dataframe(
        display.style.format(lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) and x == x else "N/A"),
        use_container_width=True,
    )

# ── 새로고침 ────────────────────────────────────────────────────
if st.button("🔄 새로고침"):
    st.cache_data.clear()
    st.rerun()
