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
    page_title="미국 3대 지수 + 두바이유",
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
    "S&P 500": "#4C9EFF",
    "다우존스": "#34D399",
    "나스닥":   "#A78BFA",
    "두바이유": "#FB923C",
}

KST = pytz.timezone("Asia/Seoul")
ET  = pytz.timezone("America/New_York")


# ── 3대 지수 로드 ────────────────────────────────────────────────
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
                st.warning(f"{name} 데이터 없음")
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


# ── 두바이유 (오피넷 스크래핑) ────────────────────────────────────
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
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.opinet.co.kr/gloptotSelect.do",
            },
            timeout=8,
        )
        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        rows  = table.find_all("tr")[1:]

        records = {}
        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            # 달러 행만 (원화 행 제외) — 값이 100 이하인 행
            if len(cols) >= 2 and cols[1]:
                try:
                    val = float(cols[1])
                except ValueError:
                    continue
                if val > 500:   # 원화 환산값 스킵 (보통 1000원대)
                    continue
                raw_date = cols[0].replace("년", "-").replace("월", "-").replace("일", "")
                parts = raw_date.split("-")
                date  = datetime(2000 + int(parts[0]), int(parts[1]), int(parts[2]))
                records[date] = val

        s = pd.Series(records, name="두바이유")
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index().tail(3)

    except Exception as e:
        st.warning(f"두바이유 수집 실패: {e}")
        return pd.Series(name="두바이유", dtype=float)


# ── 민맥스 정규화 (탄력: 각 시리즈 자체 범위 기준) ───────────────
def minmax_elastic(df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=df.index)
    for col in df.columns:
        s  = df[col].dropna()
        mn, mx = s.min(), s.max()
        if mx == mn:
            result[col] = 50.0
        else:
            result[col] = (df[col] - mn) / (mx - mn) * 100
    return result


# ── 헬퍼 ────────────────────────────────────────────────────────
def delta_info(cur: float, prev: float):
    diff = cur - prev
    pct  = diff / prev * 100
    sign = "+" if diff >= 0 else ""
    return diff, pct, f"{sign}{diff:,.2f} ({sign}{pct:.2f}%)"


# ── UI ──────────────────────────────────────────────────────────
st.title("📈 미국 3대 지수 + 두바이유")
now_kst = datetime.now(KST)
st.caption(f"기준: {now_kst.strftime('%Y-%m-%d %H:%M')} KST  |  지수: Yahoo Finance  |  두바이유: 오피넷")

with st.spinner("데이터 불러오는 중…"):
    df     = load_indices()
    dubai  = load_dubai()

if df.empty:
    st.error("지수 데이터를 가져오지 못했습니다.")
    st.stop()

date_labels = [d.strftime("%m/%d (%a)") for d in df.index]

# ── 날짜별 섹션 ──────────────────────────────────────────────────
for i in range(len(df)):
    st.subheader(f"🗓 {date_labels[i]}")

    # 3대 지수 + 두바이유 컬럼
    cols = st.columns(4)

    for j, name in enumerate(INDICES.keys()):
        cur = float(df.iloc[i][name])
        with cols[j]:
            if i > 0:
                prev = float(df.iloc[i - 1][name])
                diff, pct, label = delta_info(cur, prev)
                st.metric(label=name, value=f"{cur:,.2f}", delta=label,
                          delta_color="normal" if diff >= 0 else "inverse")
            else:
                st.metric(label=name, value=f"{cur:,.2f}")

    # 두바이유 (날짜 매칭)
    with cols[3]:
        row_date = df.index[i].date()
        # 당일 or 가장 가까운 이전 날짜 값
        dubai_val = None
        for d in reversed(dubai.index):
            if d.date() <= row_date:
                dubai_val = dubai[d]
                break

        if dubai_val is not None:
            if i > 0:
                prev_date = df.index[i - 1].date()
                prev_val  = None
                for d in reversed(dubai.index):
                    if d.date() <= prev_date:
                        prev_val = dubai[d]
                        break
                if prev_val:
                    diff, pct, label = delta_info(dubai_val, prev_val)
                    st.metric(label="두바이유($/bbl)", value=f"${dubai_val:.2f}",
                              delta=label,
                              delta_color="normal" if diff >= 0 else "inverse")
                else:
                    st.metric(label="두바이유($/bbl)", value=f"${dubai_val:.2f}")
            else:
                st.metric(label="두바이유($/bbl)", value=f"${dubai_val:.2f}")
        else:
            st.metric(label="두바이유($/bbl)", value="N/A")

    st.divider()

# ── 민맥스 탄력 정규화 라인차트 (Plotly) ────────────────────────
st.subheader("📊 4변수 추이 비교 (민맥스 탄력 정규화)")

# 두바이유를 지수 날짜에 맞게 리인덱싱
dubai_aligned = pd.Series(dtype=float, name="두바이유")
for d in df.index:
    match = None
    for od in reversed(dubai.index):
        if od.date() <= d.date():
            match = dubai[od]
            break
    dubai_aligned[d] = match

combined = df.copy()
combined["두바이유"] = dubai_aligned.values

norm = minmax_elastic(combined)
norm.index = date_labels

fig = go.Figure()
for col in norm.columns:
    raw_vals = combined[col].values
    fig.add_trace(go.Scatter(
        x=norm.index,
        y=norm[col],
        name=col,
        mode="lines+markers",
        line=dict(color=COLORS.get(col, "#888"), width=2.5),
        marker=dict(size=7),
        customdata=raw_vals,
        hovertemplate=(
            f"<b>{col}</b><br>"
            "날짜: %{x}<br>"
            "정규화: %{y:.1f}<br>"
            "실제값: %{customdata:,.2f}<extra></extra>"
        ),
    ))

fig.update_layout(
    height=380,
    margin=dict(l=0, r=0, t=10, b=0),
    legend=dict(orientation="h", y=-0.2),
    yaxis=dict(
        title="민맥스 정규화 (각 시리즈 독립)",
        showgrid=True,
        gridcolor="#2a2a2a",
        range=[-5, 105],
    ),
    xaxis=dict(showgrid=False),
    plot_bgcolor="#0e1117",
    paper_bgcolor="#0e1117",
    font=dict(color="#ccc"),
    hovermode="x unified",
)
st.plotly_chart(fig, use_container_width=True)
st.caption("※ 각 변수가 표시 기간 내 자신의 최솟값→0, 최댓값→100 기준으로 독립 정규화됩니다.")

# ── 원본 테이블 ──────────────────────────────────────────────────
with st.expander("원본 데이터 보기"):
    display = combined.copy()
    display.index = date_labels
    st.dataframe(display.style.format("{:,.2f}"), use_container_width=True)

# ── 새로고침 ────────────────────────────────────────────────────
if st.button("🔄 새로고침"):
    st.cache_data.clear()
    st.rerun()
