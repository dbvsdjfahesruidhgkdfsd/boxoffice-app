from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

# 페이지 기본 설정은 반드시 가장 먼저 호출되어야 합니다.
st.set_page_config(page_title="KOBIS 박스오피스 대시보드", page_icon="🎬", layout="wide", initial_sidebar_state="expanded")

API_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"
CODE_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/code/searchCodeList.json"
KST = timezone(timedelta(hours=9))
YESTERDAY = datetime.now(KST).date() - timedelta(days=1)
NUMBER_COLUMNS = ["rank", "audiCnt", "audiAcc", "scrnCnt", "salesAmt"]
DISPLAY_COLUMNS = {
    "rank": "KOBIS 순위",
    "movieNm": "영화명",
    "audiCnt": "일일 관객(명)",
    "audiAcc": "누적 관객(명)",
    "scrnCnt": "스크린(개)",
    "salesAmt": "일일 매출(원)",
    "rankOldAndNew": "진입 구분",
}

class KobisError(Exception):
    pass

def request_kobis(url, params):
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        raise KobisError("🔌 KOBIS 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.") from None

    if "faultInfo" in data:
        code = data["faultInfo"].get("errorCode")
        if code == "320010":
            raise KobisError("🔑 KOBIS 인증 키를 확인하지 못했습니다. 키를 확인하세요.")
        raise KobisError("⚠️ KOBIS가 요청을 처리하지 못했습니다. 키 상태와 요청 한도를 확인하세요.")
    return data

@st.cache_data(ttl=3600)
def fetch_boxoffice(day, api_key, area_code=""):
    params = {
        "key": api_key,
        "targetDt": day.strftime("%Y%m%d"),
        "itemPerPage": 10,
    }
    if area_code:
        params["wideAreaCd"] = area_code
    data = request_kobis(API_URL, params)
    rows = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList")
    if rows is None:
        raise KobisError("📉 KOBIS 응답에서 일별 박스오피스 목록을 찾을 수 없습니다.")

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for column in NUMBER_COLUMNS:
        df[column] = pd.to_numeric(df[column])
    return df.sort_values("rank")

@st.cache_data(ttl=86400)
def fetch_regions(api_key):
    params = {"key": api_key, "comCode": "0105000000"}
    data = request_kobis(CODE_URL, params)
    codes = data.get("codes", [])
    if not codes:
        raise KobisError("🗺️ KOBIS에서 지역 코드 목록을 가져오지 못했습니다.")
    return {row["comCodeNm"]: row["fullCd"] for row in codes}

def show_table(df):
    view = df[list(DISPLAY_COLUMNS)].rename(columns=DISPLAY_COLUMNS)
    view["진입 구분"] = view["진입 구분"].replace({"NEW": "🆕 신규", "OLD": "➖ 기존"})
    
    # [수정됨] Streamlit 버전에 구애받지 않는 안전한 Pandas 포맷팅 방식 적용
    for col in ["일일 관객(명)", "누적 관객(명)", "스크린(개)", "일일 매출(원)"]:
        view[col] = view[col].apply(lambda x: f"{int(x):,}")
        
    st.dataframe(view, hide_index=True, use_container_width=True)

def show_ranking(df):
    st.markdown("### 🏆 오늘의 전국 박스오피스 TOP 10")
    
    top = df.head(10)
    total = int(top["audiCnt"].sum())
    share = top.iloc[0]["audiCnt"] / total * 100 if total else 0
    new_count = int(top["rankOldAndNew"].eq("NEW").sum())
    
    col1, col2, col3 = st.columns(3)
    col1.metric(label="👥 전체 TOP 10 관객 합계", value=f"{total:,} 명")
    col2.metric(label="👑 1위 영화 관객 점유율", value=f"{share:.1f} %")
    col3.metric(label="✨ 신규 진입 영화", value=f"{new_count} 편")
    
    st.caption("※ 점유율은 표에 표시된 TOP 10 영화의 일일 관객 합계를 기준으로 계산되었습니다.")
    st.divider()

    col_sort, _ = st.columns([1, 3])
    with col_sort:
        sort_options = {
            "KOBIS 순위 (기본)": "rank",
            "일일 관객순": "audiCnt",
            "누적 관객순": "audiAcc",
            "스크린 수 많은순": "scrnCnt",
            "일일 매출순": "salesAmt",
        }
        sort_label = st.selectbox("정렬 기준", list(sort_options))
        
    column = sort_options[sort_label]
    show_table(top.sort_values(column, ascending=(column == "rank"), kind="stable"))

def movie_picker(label, df, index=0):
    labels = {
        row.movieCd: f"{row.movieNm} ({row.rank}위)"
        for row in df.itertuples()
    }
    return st.selectbox(label, list(labels), index=index, format_func=labels.get)

def show_history(day, df, api_key):
    st.markdown("### 📈 주간 관객수 추이 분석")
    col1, _ = st.columns([1, 2])
    with col1:
        movie_code = movie_picker("🔎 추이를 분석할 영화 선택", df)
        
    history = []
    with st.spinner("최근 7일간의 데이터를 불러오는 중입니다..."):
        for offset in range(6, -1, -1):
            current_day = day - timedelta(days=offset)
            daily = fetch_boxoffice(current_day, api_key)
            audience = None
            if not daily.empty:
                found = daily.loc[daily["movieCd"] == movie_code, "audiCnt"]
                if not found.empty:
                    audience = int(found.iloc[0])
            history.append({"날짜": current_day, "일일 관객(명)": audience})

    chart = px.line(
        pd.DataFrame(history), 
        x="날짜", 
        y="일일 관객(명)", 
        markers=True,
    )
    chart.update_traces(connectgaps=False, line=dict(width=3, color='#FF4B4B'), marker=dict(size=8))
    chart.update_layout(xaxis_title="", yaxis_title="관객 수 (명)", hovermode="x unified")
    
    st.plotly_chart(chart, use_container_width=True)
    st.info("💡 일별 TOP 10에 진입하지 못한 날은 그래프에서 빈 구간으로 표시됩니다.")

def show_movies(df):
    st.markdown("### ⚔️ 다각도 영화 지표 비교")
    if len(df) < 2:
        st.warning("비교할 영화가 두 편 이상 있는 날짜를 선택하세요.")
        return

    col1, col2 = st.columns(2)
    with col1:
        first = movie_picker("🔵 영화 A", df)
    with col2:
        second = movie_picker("🔴 영화 B", df, index=1)
        
    if first == second:
        st.error("서로 다른 영화 두 편을 선택하세요.")
        return

    movies = df.set_index("movieCd")
    metrics = {
        "일일 관객": "audiCnt",
        "누적 관객": "audiAcc",
        "스크린 확보": "scrnCnt",
        "일일 매출": "salesAmt",
    }
    labels = [*metrics, "종합 순위 점수"]
    maxima = movies[list(metrics.values())].max()
    
    fig = go.Figure()
    colors = ['rgba(75, 139, 244, 0.5)', 'rgba(255, 75, 75, 0.5)']
    lines = ['#4B8BF4', '#FF4B4B']
    
    for idx, movie_code in enumerate((first, second)):
        movie = movies.loc[movie_code]
        scores = [
            float(movie[col] / maxima[col] * 100) if maxima[col] else 0
            for col in metrics.values()
        ]
        scores.append(110 - int(movie["rank"]) * 10)
        
        fig.add_trace(go.Scatterpolar(
            r=[*scores, scores[0]],
            theta=[*labels, labels[0]],
            fill="toself",
            name=movie["movieNm"],
            fillcolor=colors[idx],
            line=dict(color=lines[idx])
        ))
        
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        margin=dict(t=40, b=40, l=40, r=40)
    )
    
    st.plotly_chart(fig, use_container_width=True)
    st.caption("※ 선택 날짜의 전국 TOP 10을 기준으로 지표별 최댓값을 100점으로 환산한 방사형 그래프입니다.")

def show_regions(day, api_key):
    st.markdown("### 🗺️ 권역별 박스오피스 비교")
    regions = fetch_regions(api_key)
    names = list(regions)
    if len(names) < 2:
        st.info("비교할 지역 코드가 두 개 이상 필요합니다.")
        return
    
    gyeongbuk = next((name for name in names if "경북" in name or "경상북도" in name), names[0])
    
    col1, col2 = st.columns(2)
    with col1:
        name_a = st.selectbox("📍 비교 지역 A", names, index=names.index(gyeongbuk))
    with col2:
        name_b = st.selectbox("📍 비교 지역 B", names, index=0 if gyeongbuk != names[0] else 1)
        
    if name_a == name_b:
        st.warning("서로 다른 두 지역을 선택하세요.")
        return

    df_a = fetch_boxoffice(day, api_key, regions[name_a])
    df_b = fetch_boxoffice(day, api_key, regions[name_b])
    
    st.divider()
    
    panel_a, panel_b = st.columns(2)
    for panel, name, df in ((panel_a, name_a, df_a), (panel_b, name_b, df_b)):
        with panel:
            st.markdown(f"#### {name}")
            if df.empty:
                st.info(f"{name} 지역의 순위 데이터가 없습니다.")
                continue
            total = int(df["audiCnt"].sum())
            winner = df.iloc[0]
            share = winner["audiCnt"] / total * 100 if total else 0
            
            m1, m2 = st.columns(2)
            m1.metric("총 관객수", f"{total:,} 명")
            m2.metric("1위 점유율", f"{share:.1f} %")
            
            st.success(f"🏆 1위 영화: **{winner['movieNm']}**")
            show_table(df)

    if not df_a.empty and not df_b.empty:
        common = set(df_a["movieCd"]) & set(df_b["movieCd"])
        st.info(f"💡 두 지역의 TOP 10에 공통으로 진입한 영화는 총 **{len(common)}편** 입니다.")

# ==========================================
# 사이드바 (Sidebar) - 대시보드 컨트롤 패널
# ==========================================
with st.sidebar:
    st.title("메뉴 및 설정")
    day = st.date_input("📅 분석 날짜 선택", YESTERDAY, max_value=YESTERDAY)
    
    st.divider()
    st.markdown("### ℹ️ 데이터 정보")
    st.caption("이 대시보드는 영화진흥위원회 영화관입장권통합전산망(KOBIS) 오픈API를 활용하여 제작되었습니다.")
    st.caption("실시간 트렌드 및 권역별 성과 지표를 한눈에 파악할 수 있습니다.")

# ==========================================
# 메인 화면 (Main Content)
# ==========================================
st.title("🎬 KOBIS 박스오피스 인텔리전스")
st.markdown("어제 하루 동안의 대한민국 영화관람 트렌드를 깊이 있게 분석해보세요.")

try:
    api_key = st.secrets["KOBIS_KEY"].strip()
except (KeyError, StreamlitSecretNotFoundError):
    api_key = ""
    
if not api_key or api_key == "발급받은_API_키":
    st.error("🔒 KOBIS_KEY를 로컬 `.streamlit/secrets.toml` 또는 Streamlit Cloud Secrets에 등록해 주세요.")
    st.stop()

try:
    nationwide = fetch_boxoffice(day, api_key)
    
    if nationwide.empty:
        st.warning(f"🚫 {day.strftime('%Y년 %m월 %d일')}의 박스오피스 데이터가 존재하지 않습니다. 이전 날짜로 조회해 보세요.")
    else:
        tab1, tab2, tab3, tab4 = st.tabs(["🏆 전국 TOP 10", "📈 7일 관객 추이", "⚔️ 영화 비교", "🗺️ 지역 비교"])
        
        with tab1:
            show_ranking(nationwide)
        with tab2:
            show_history(day, nationwide, api_key)
        with tab3:
            show_movies(nationwide)
        with tab4:
            show_regions(day, api_key)
            
except KobisError as error:
    st.error(str(error))
