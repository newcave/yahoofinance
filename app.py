import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import re
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


# ── SMP (EPSIS .ajax 스크래핑, 월별) ─────────────────────────────
@st.cache_data(ttl=3600)
def load_smp(months: int = 3) -> pd.Series:
    """
    EPSIS 가중평균SMP 월별 데이터 스크래핑.
    갱신: 익월 말 → 최근 months개월 중 가장 최신값을 3대 지수 날짜에 매핑.
    """
    try:
        resp = requests.get(
            "https://epsis.kpx.or.kr/epsisnew/selectEkmaSmpSmpChart.ajax",
            headers={"Referer": "https://epsis.kpx.or.kr/epsisnew/selectEkmaSmpSmpChart.do?menuId=040201"},
            timeout=8,
        )
        text = resp.text

        # chartData.push({"Date":"2026/03/01","Value":Number("123.45"),...}) 파싱
        pattern = r'chartData\.push\(\{"Date":"(\d{4}/\d{2}/\d{2})","Value":Number\("([^"]+)"\)'
        matches = re.findall(pattern, text)

        records = {}
        for date_str, val_str in matches:
            try:
                date = datetime.strptime(date_str, "%Y/%m/%d")
                records[date] = float(val_str)
            except Exception:
                continue

        if not records:
            return pd.Series(name="SMP(육지)", dtype=float)

        s = pd.Series(records, name="SMP(육지)")
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index().tail(months)

    except Exception as e:
        st.warning(f"SMP 수집 실패: {e}")
        return pd.Series(name="SMP(육지)", dtype=float)


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


def fmt(x):
    if x is None:
        return "N/A"
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "N/A"


# ── 민맥스 탄력 정규화 ────────────────────────────────────────────
def minmax_elastic(df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=df.index)
    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            result[col] = float("nan")
            continue
        mn, mx = s.min(), s.max()
        result[col] = 50.0 if mx == mn else (df[col] - mn) / (mx - mn) * 100
    return result


# ── UI ──────────────────────────────────────────────────────────
st.title("📈 미국 3대 지수 + 두바이유 + SMP")
now_kst = datetime.now(KST)
st.caption(
    f"기준: {now_kst.strftime('%Y-%m-%d %H:%M')} KST  |  "
    "지수: Yahoo Finance  |  두바이유: 오피넷  |  SMP: EPSIS(월별)"
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

    with cols[4]:
        cur_v = get_nearest(smp, row_date)
        label_smp = "SMP(원/kWh)"
        if cur_v is not None:
            # SMP는 월별이라 delta 표시 의미 없음 → 당월값만 표시
            smp_date = max((d for d in smp.index if d.date() <= row_date),
                           default=None)
            month_label = smp_date.strftime("%y년%m월") if smp_date else ""
            st.metric(label_smp, f"{cur_v:.2f}", month_label,
                      delta_color="off")
        else:
            st.metric(label_smp, "N/A")

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
st.plotly_chart(fig, width="stretch")
st.caption("※ 각 변수가 표시 기간 내 자신의 최솟값→0, 최댓값→100 기준으로 독립 정규화됩니다.\n"
           "※ SMP는 EPSIS 월별 가중평균 (익월 말 갱신)")

# ── 원본 데이터 ──────────────────────────────────────────────────
with st.expander("원본 데이터 보기"):
    display = combined.copy()
    display.index = date_labels
    st.dataframe(display.style.format(fmt), width="stretch")

# ── 새로고침 ────────────────────────────────────────────────────
if st.button("🔄 새로고침"):
    st.cache_data.clear()
    st.rerun()
