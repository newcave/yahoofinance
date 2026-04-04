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
    "나스닥":   "^IXIC",
}

KST = pytz.timezone("Asia/Seoul")
ET  = pytz.timezone("America/New_York")


# ── 데이터 로드 ──────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_data(days: int = 10) -> pd.DataFrame:
    """각 티커를 개별로 받아 단순 DataFrame으로 합침 → MultiIndex 문제 회피."""
    end   = datetime.now(ET)
    start = end - timedelta(days=days)

    series_list = []
    for name, ticker in INDICES.items():
        try:
            raw = yf.download(
                ticker,
                start=start,
                end=end,
                progress=False,
                auto_adjust=True,
            )
            if raw.empty:
                st.warning(f"{name} 데이터 없음")
                continue

            # yfinance 최신 버전: MultiIndex 컬럼 (Close, ^GSPC) 평탄화
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


# ── 헬퍼 ────────────────────────────────────────────────────────
def delta_info(cur: float, prev: float):
    diff = cur - prev
    pct  = diff / prev * 100
    sign = "+" if diff >= 0 else ""
    return diff, pct, f"{sign}{diff:,.2f} ({sign}{pct:.2f}%)"


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
date_labels = [d.strftime("%m/%d (%a)") for d in df.index]

# ── 날짜별 섹션 ──────────────────────────────────────────────────
for i in range(len(df)):
    st.subheader(f"🗓 {date_labels[i]}")
    cols = st.columns(3)

    for j, name in enumerate(INDICES.keys()):
        cur = float(df.iloc[i][name])
        with cols[j]:
            if i > 0:
                prev = float(df.iloc[i - 1][name])
                diff, pct, label = delta_info(cur, prev)
                st.metric(
                    label=name,
                    value=f"{cur:,.2f}",
                    delta=label,
                    delta_color="normal" if diff >= 0 else "inverse",
                )
            else:
                st.metric(label=name, value=f"{cur:,.2f}")

    st.divider()

# ── 정규화 라인차트 ──────────────────────────────────────────────
st.subheader("📊 3일 추이 비교 (정규화)")
norm = df.div(df.iloc[0]) * 100
norm.index = date_labels
st.line_chart(norm, use_container_width=True)
st.caption("※ 첫 거래일 종가를 100으로 정규화한 상대 지수입니다.")

# ── 원본 테이블 ──────────────────────────────────────────────────
with st.expander("원본 데이터 보기"):
    display = df.copy()
    display.index = date_labels
    st.dataframe(display.style.format("{:,.2f}"), use_container_width=True)

# ── 새로고침 ────────────────────────────────────────────────────
if st.button("🔄 새로고침"):
    st.cache_data.clear()
    st.rerun()
