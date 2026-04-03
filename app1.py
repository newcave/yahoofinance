import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import pytz

# ── 페이지 설정 ──────────────────────────────────────────────────
st.set_page_config(
    page_title="미국 3대 지수 종가",
    page_icon="📈",
    layout="centered",
)

# ── 상수 ────────────────────────────────────────────────────────
INDICES = {
    "S&P 500": "^GSPC",
    "다우존스": "^DJI",
    "나스닥": "^IXIC",
}

INDEX_COLORS = {
    "S&P 500": "#1f77b4",
    "다우존스": "#ff7f0e",
    "나스닥":   "#2ca02c",
}

KST = pytz.timezone("Asia/Seoul")
ET  = pytz.timezone("America/New_York")


# ── 데이터 로드 (5분 캐시) ───────────────────────────────────────
@st.cache_data(ttl=300)
def load_data(days: int = 7) -> pd.DataFrame:
    """최근 N일 거래일 데이터를 가져와 마지막 3거래일만 반환."""
    end   = datetime.now(ET)
    start = end - timedelta(days=days)

    frames = []
    for name, ticker in INDICES.items():
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            continue
        df = df[["Close"]].copy()
        df.columns = [name]
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, axis=1).dropna()
    combined.index = pd.to_datetime(combined.index).tz_localize(None)
    return combined.tail(3)          # 최근 3거래일


# ── 헬퍼 ────────────────────────────────────────────────────────
def fmt_price(name: str, val: float) -> str:
    if name == "다우존스":
        return f"{val:,.2f}"
    return f"{val:,.2f}"

def delta_label(cur: float, prev: float) -> tuple[float, str]:
    diff  = cur - prev
    pct   = diff / prev * 100
    arrow = "▲" if diff >= 0 else "▼"
    color = "normal" if diff >= 0 else "inverse"
    return diff, pct, f"{arrow} {abs(diff):,.2f} ({abs(pct):.2f}%)", color


# ── UI ──────────────────────────────────────────────────────────
st.title("📈 미국 3대 지수 종가")
now_kst = datetime.now(KST)
st.caption(f"기준: {now_kst.strftime('%Y-%m-%d %H:%M')} KST  |  데이터: Yahoo Finance")

with st.spinner("데이터 불러오는 중…"):
    df = load_data()

if df.empty:
    st.error("데이터를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.")
    st.stop()

# 날짜 레이블
dates = [d.strftime("%m/%d(%a)") for d in df.index]

# ── 날짜별 섹션 ──────────────────────────────────────────────────
for i, (date_idx, row) in enumerate(df.iterrows()):
    date_label = dates[i]
    st.subheader(f"🗓 {date_label}")

    cols = st.columns(3)
    for j, (name, _) in enumerate(INDICES.items()):
        cur = float(row[name])
        with cols[j]:
            if i > 0:
                prev = float(df.iloc[i - 1][name])
                diff, pct, label, delta_color = delta_label(cur, prev)
                st.metric(
                    label=name,
                    value=fmt_price(name, cur),
                    delta=f"{'+' if diff>=0 else ''}{diff:,.2f} ({pct:+.2f}%)",
                    delta_color=delta_color,
                )
            else:
                st.metric(label=name, value=fmt_price(name, cur))

    st.divider()

# ── 라인 차트 ────────────────────────────────────────────────────
st.subheader("📊 3일 추이 비교 (정규화)")

norm = df.div(df.iloc[0]) * 100   # 첫날 = 100 기준

chart_df = norm.copy()
chart_df.index = dates
st.line_chart(chart_df, use_container_width=True)

st.caption("※ 첫 거래일 종가를 100으로 정규화한 상대 지수입니다.")

# ── 원본 테이블 ──────────────────────────────────────────────────
with st.expander("원본 데이터 보기"):
    display = df.copy()
    display.index = dates
    display.columns = list(INDICES.keys())
    st.dataframe(display.style.format("{:,.2f}"), use_container_width=True)

# ── 새로고침 버튼 ────────────────────────────────────────────────
if st.button("🔄 새로고침"):
    st.cache_data.clear()
    st.rerun()
