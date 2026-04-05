import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import re
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
UA  = {"User-Agent": "Mozilla/5.0"}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ① 미국 3대 지수 ── yfinance 공식 라이브러리
#    ticker(^GSPC / ^DJI / ^IXIC) 다운로드 → Close 컬럼 추출
@st.cache_data(ttl=300)
def load_indices(days: int = 10) -> tuple[pd.DataFrame, str]:
    end, start = datetime.now(ET), datetime.now(ET) - timedelta(days=days)
    series_list, err = [], ""
    for name, ticker in INDICES.items():
        try:
            raw = yf.download(ticker, start=start, end=end,
                              progress=False, auto_adjust=True)
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            series_list.append(raw["Close"].rename(name))
        except Exception as e:
            err = str(e)
    if not series_list:
        return pd.DataFrame(), err or "데이터 없음"
    combined = pd.concat(series_list, axis=1).dropna()
    combined.index = pd.to_datetime(combined.index).tz_localize(None)
    return combined.tail(3), ""


# ② 두바이유 ── 오피넷 POST 크롤 + BeautifulSoup HTML 파싱
#    POST 파라미터(날짜 범위, 유종코드 D) → <table> 파싱
#    한글 날짜(24년03월05일) → re.sub → datetime 변환
@st.cache_data(ttl=3600)
def load_dubai(days: int = 10) -> tuple[pd.Series, str]:
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
            headers={**UA, "Referer": "https://www.opinet.co.kr/gloptotSelect.do"},
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
            if not (10 < val < 500):
                continue
            raw_date = re.sub(r"[년월]", "-", cols[0]).replace("일", "").strip()
            parts = [p.strip() for p in raw_date.split("-") if p.strip()]
            if len(parts) < 3:
                continue
            try:
                records[datetime(2000 + int(parts[0]), int(parts[1]), int(parts[2]))] = val
            except ValueError:
                continue
        s = pd.Series(records, name="두바이유")
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index().tail(3), ""
    except Exception as e:
        return pd.Series(name="두바이유", dtype=float), str(e)


# ③④ SMP 육지/제주 ── KPX 전력정보앱 모바일 페이지 크롤
#     인증키 불필요, 당일 포함 7일치 시간별(1h~24h) + 가중평균 제공
#     GET → HTML <table> → 헤더(날짜) + '가중평균' 행 추출
#
#     URL:
#       육지: kpx.or.kr/smpInland.es?mid=a30301000000&device=mbl
#       제주: kpx.or.kr/smpJeju.es?mid=a30302000000&device=mbl
@st.cache_data(ttl=3600)
def load_smp_kpx() -> tuple[pd.Series, pd.Series, str]:
    SMP_URLS = {
        "SMP(육지)": "https://www.kpx.or.kr/smpInland.es?mid=a30301000000&device=mbl",
        "SMP(제주)": "https://www.kpx.or.kr/smpJeju.es?mid=a30302000000&device=mbl",
    }
    results, err = {}, ""

    for name, url in SMP_URLS.items():
        try:
            soup   = BeautifulSoup(requests.get(url, headers=UA, timeout=12).text, "html.parser")
            tables = soup.find_all("table")
            table  = tables[1] if len(tables) >= 2 else tables[0]
            rows   = table.find_all("tr")

            # 헤더: "03.30 (월)" → datetime
            header = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            today  = datetime.today()
            dates  = []
            for col in header[1:]:
                m = re.search(r"(\d{2})\.(\d{2})", col)
                if m:
                    mo, dy = int(m.group(1)), int(m.group(2))
                    yr = today.year if mo <= today.month else today.year - 1
                    dates.append(datetime(yr, mo, dy))
                else:
                    dates.append(None)

            # 가중평균 행
            records = {}
            for row in reversed(rows):
                if "가중평균" in row.get_text():
                    vals = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                    for date, val in zip(dates, vals[1:]):
                        if date is None:
                            continue
                        try:
                            records[date] = float(val)
                        except ValueError:
                            pass
                    break

            if records:
                s = pd.Series(records, name=name)
                s.index = pd.DatetimeIndex(s.index)
                results[name] = s.sort_index().tail(7)  # 7일 유지 → yfinance 날짜와 여유있게 매핑

        except Exception as e:
            err = str(e)

    empty = pd.Series(dtype=float)
    return (
        results.get("SMP(육지)", empty.rename("SMP(육지)")),
        results.get("SMP(제주)", empty.rename("SMP(제주)")),
        err,
    )




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
        if s.empty:
            result[col] = float("nan")
            continue
        mn, mx = s.min(), s.max()
        result[col] = 50.0 if mx == mn else (df[col] - mn) / (mx - mn) * 100
    return result

def render_metric(col, label, cur, prev=None, prefix="", suffix=""):
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터 소스 상태 패널
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_status(df, dubai, smp_land, smp_jeju, errs):
    sources = [
        {
            "name":   "① 미국 3대 지수 (S&P·다우·나스닥)",
            "method": "yfinance API",
            "ok":     not df.empty,
            "detail": f"{len(df)}일치 수집 완료" if not df.empty
                      else f"오류: {errs.get('indices', '알 수 없음')}",
        },
        {
            "name":   "② 두바이유 ($/bbl)",
            "method": "오피넷 POST 크롤 + BeautifulSoup",
            "ok":     not dubai.empty,
            "detail": f"{len(dubai)}일치 수집 완료" if not dubai.empty
                      else f"오류: {errs.get('dubai', '알 수 없음')}",
        },
        {
            "name":   "③ SMP 육지 (원/kWh)",
            "method": "KPX 전력정보앱 모바일 크롤",
            "ok":     not smp_land.empty,
            "detail": f"{len(smp_land)}일치 수집 완료" if not smp_land.empty
                      else f"오류: {errs.get('smp', '알 수 없음')}",
        },
        {
            "name":   "④ SMP 제주 (원/kWh)",
            "method": "KPX 전력정보앱 모바일 크롤",
            "ok":     not smp_jeju.empty,
            "detail": f"{len(smp_jeju)}일치 수집 완료" if not smp_jeju.empty
                      else f"오류: {errs.get('smp', '알 수 없음')}",
        },
    ]

    with st.expander("🔌 데이터 소스 현황", expanded=False):
        for s in sources:
            icon = "✅" if s["ok"] else "❌"
            a, b = st.columns([5, 4])
            with a:
                st.markdown(f"**{icon} {s['name']}**  \n`{s['method']}`")
            with b:
                if s["ok"]:
                    st.success(s["detail"])
                else:
                    st.error(s["detail"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.title("📈 글로벌 경제 지표 대시보드")
st.caption(
    f"기준: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST  |  "
    "지수: Yahoo Finance  |  두바이유: 오피넷  |  SMP: KPX 크롤"
)

with st.spinner("데이터 불러오는 중…"):
    df,       err_idx             = load_indices()
    dubai,    err_dub             = load_dubai()
    smp_land, smp_jeju, err_smp  = load_smp_kpx()

errs = {"indices": err_idx, "dubai": err_dub, "smp": err_smp}

# 상태 패널 (상단 고정)
show_status(df, dubai, smp_land, smp_jeju, errs)

if df.empty:
    st.error("지수 데이터를 가져오지 못했습니다.")
    st.stop()

date_labels = [d.strftime("%m/%d (%a)") for d in df.index]

# ── 날짜별 섹션 ──────────────────────────────────────────────────
for i in range(len(df)):
    st.subheader(f"🗓 {date_labels[i]}")
    row_date  = df.index[i].date()
    prev_date = df.index[i - 1].date() if i > 0 else None

    # 상단 4열: S&P 500 / 다우존스 / 나스닥 / 두바이유
    top = st.columns(4)
    for j, name in enumerate(INDICES.keys()):
        cur  = float(df.iloc[i][name])
        prev = float(df.iloc[i - 1][name]) if i > 0 else None
        render_metric(top[j], name, cur, prev)

    cur_d  = get_nearest(dubai, row_date)
    prev_d = get_nearest(dubai, prev_date) if prev_date else None
    render_metric(top[3], "두바이유($/bbl)", cur_d, prev_d, prefix="$")

    # 하단 2열: SMP육지 / SMP제주
    bot = st.columns(2)

    cur_sl  = get_nearest(smp_land, row_date)
    prev_sl = get_nearest(smp_land, prev_date) if prev_date else None
    render_metric(bot[0], "SMP육지(원/kWh)", cur_sl, prev_sl)

    cur_sj  = get_nearest(smp_jeju, row_date)
    prev_sj = get_nearest(smp_jeju, prev_date) if prev_date else None
    render_metric(bot[1], "SMP제주(원/kWh)", cur_sj, prev_sj)


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

for y_val, lbl in [(100, "최고"), (50, "중간"), (0, "최저")]:
    fig.add_hline(
        y=y_val,
        line=dict(color="rgba(255,255,255,0.10)", width=1, dash="dot"),
        annotation_text=lbl, annotation_position="left",
        annotation_font=dict(size=10, color="rgba(200,200,200,0.4)"),
    )

for col in norm.columns:
    unit     = UNITS.get(col, "pt")
    raw_vals = combined[col].values
    color    = COLORS.get(col, "#888888")
    fig.add_trace(go.Scatter(
        x=norm.index, y=norm[col], name=col,
        mode="lines+markers+text",
        line=dict(color=color, width=2.5),
        marker=dict(size=10, color=color, line=dict(width=2, color="#0e1117")),
        text=[f"{v:,.1f}" if v is not None else "" for v in raw_vals],
        textposition="top center",
        textfont=dict(size=10, color=color),
        customdata=[[v, unit] for v in raw_vals],
        hovertemplate=(
            f"<b style='color:{color}'>{col}</b><br>"
            "날짜: %{x}<br>정규화: %{y:.1f}<br>"
            "실제값: %{customdata[0]:,.2f} %{customdata[1]}<extra></extra>"
        ),
    ))

fig.update_layout(
    height=500, margin=dict(l=10, r=10, t=20, b=10),
    legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center",
                font=dict(size=12, color="#cccccc"), bgcolor="rgba(0,0,0,0)"),
    yaxis=dict(
        title="정규화 지수 (각 변수 독립, 0=최저·100=최고)",
        title_font=dict(size=11, color="#888888"),
        tickfont=dict(size=11, color="#888888"),
        tickvals=[0, 25, 50, 75, 100],
        showgrid=True, gridcolor="rgba(255,255,255,0.06)",
        zeroline=False, range=[-12, 118],
    ),
    xaxis=dict(showgrid=False, tickfont=dict(size=13, color="#dddddd")),
    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
    font=dict(color="#cccccc"),
    hovermode="x unified",
    hoverlabel=dict(bgcolor="#1a1f2e", bordercolor="rgba(255,255,255,0.15)",
                    font=dict(size=12, color="#ffffff"), namelength=-1),
)

st.plotly_chart(fig, width="stretch")
st.caption(
    "※ 각 지표가 표시 기간 내 자신의 최솟값→0, 최댓값→100으로 독립 정규화됩니다.\n"
    "※ SMP: KPX 전력정보앱 기준 시간별 가중평균"
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
