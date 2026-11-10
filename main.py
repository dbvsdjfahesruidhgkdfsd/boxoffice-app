from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


st.set_page_config(page_title="KOBIS 박스오피스 분석", page_icon="🎬", layout="wide")

API_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/"
    "searchDailyBoxOfficeList.json"
)
CODE_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/code/"
    "searchCodeList.json"
)
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
        raise KobisError("KOBIS에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.") from None

    if "faultInfo" in data:
        code = data["faultInfo"].get("errorCode")
        if code == "320010":
            raise KobisError("KOBIS가 인증 키를 확인하지 못했습니다. 키를 확인하세요.")
        raise KobisError("KOBIS가 요청을 처리하지 못했습니다. 키 상태와 요청 한도를 확인하세요.")
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
        raise KobisError("KOBIS 응답에서 일별 박스오피스 목록을 찾을 수 없습니다.")

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
        raise KobisError("KOBIS에서 지역 코드 목록을 가져오지 못했습니다.")
    return {row["comCodeNm"]: row["fullCd"] for row in codes}


def show_table(df):
    view = df[list(DISPLAY_COLUMNS)].rename(columns=DISPLAY_COLUMNS)
    view["진입 구분"] = view["진입 구분"].replace({"NEW": "신규", "OLD": "기존"})
    st.dataframe(view, hide_index=True)


def show_ranking(df):
    count = st.slider("표시할 영화 수", 1, 10, 10)
    top = df.head(count)
    total = int(top["audiCnt"].sum())
    share = top.iloc[0]["audiCnt"] / total * 100 if total else 0
    new_count = int(top["rankOldAndNew"].eq("NEW").sum())
    a, b, c = st.columns(3)
    a.metric(f"표시된{len(top)}편 관객 합계", f"{total:,}명")
    b.metric("1위 관객 비중", f"{share:.1f}%")
    c.metric("신규 진입", f"{new_count}편")
    st.caption("비중은 표에 표시된 영화의 일일 관객 합계를 기준으로 계산합니다.")

    sort_options = {
        "KOBIS 순위": "rank",
        "일일 관객": "audiCnt",
        "누적 관객": "audiAcc",
        "스크린 수": "scrnCnt",
        "일일 매출": "salesAmt",
    }
    sort_label = st.selectbox("표 정렬 기준", list(sort_options))
    column = sort_options[sort_label]
    show_table(top.sort_values(column, ascending=column == "rank", kind="stable"))


def movie_picker(label, df, index=0):
    labels = {
        row.movieCd: f"{row.movieNm} ({row.rank}위)"
        for row in df.itertuples()
    }
    return st.selectbox(label, list(labels), index=index, format_func=labels.get)


def show_history(day, df, api_key):
    movie_code = movie_picker("추이를 볼 영화", df)
    history = []
    for offset in range(6, -1, -1):
        current_day = day - timedelta(days=offset)
        daily = fetch_boxoffice(current_day, api_key)
        audience = None
        if not daily.empty:
            found = daily.loc[daily["movieCd"] == movie_code, "audiCnt"]
            if not found.empty:
                audience = int(found.iloc[0])
        history.append({"날짜": current_day, "일일 관객(명)": audience})

    chart = px.line(pd.DataFrame(history), x="날짜", y="일일 관객(명)", markers=True)
    chart.update_traces(connectgaps=False)
    st.plotly_chart(chart)
    st.caption("일별 TOP 10 응답에 영화가 없는 날은 그래프에서 빈 구간으로 표시합니다.")


def show_movies(df):
    if len(df) < 2:
        st.info("비교할 영화가 두 편 이상 있는 날짜를 선택하세요.")
        return

    first = movie_picker("영화 A", df)
    second = movie_picker("영화 B", df, index=1)
    if first == second:
        st.info("서로 다른 영화 두 편을 선택하세요.")
        return

    movies = df.set_index("movieCd")
    metrics = {
        "일일 관객": "audiCnt",
        "누적 관객": "audiAcc",
        "스크린": "scrnCnt",
        "일일 매출": "salesAmt",
    }
    labels = [*metrics, "순위 점수"]
    maxima = movies[list(metrics.values())].max()
    fig = go.Figure()
    for movie_code in (first, second):
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
        ))
    fig.update_layout(polar={"radialaxis": {"range": [0, 100]}})
    st.plotly_chart(fig)
    st.caption("선택 날짜의 전국 TOP 10을 기준으로 지표별 최댓값은 100점입니다. 순위는 1위 100점부터 10위 10점까지입니다.")


def show_regions(day, api_key):
    regions = fetch_regions(api_key)
    names = list(regions)
    if len(names) < 2:
        st.info("비교할 지역 코드가 두 개 이상 필요합니다.")
        return
    gyeongbuk = next(
        (name for name in names if "경북" in name or "경상북도" in name),
        names[0],
    )
    a, b = st.columns(2)
    name_a = a.selectbox("지역 A", names, index=names.index(gyeongbuk))
    name_b = b.selectbox("지역 B", names, index=0 if gyeongbuk != names[0] else 1)
    if name_a == name_b:
        st.info("서로 다른 두 지역을 선택하세요.")
        return

    df_a = fetch_boxoffice(day, api_key, regions[name_a])
    df_b = fetch_boxoffice(day, api_key, regions[name_b])
    for panel, name, df in ((a, name_a, df_a), (b, name_b, df_b)):
        with panel:
            st.subheader(name)
            if df.empty:
                st.info("선택 날짜의 지역 순위 데이터가 없습니다.")
                continue
            total = int(df["audiCnt"].sum())
            winner = df.iloc[0]
            share = winner["audiCnt"] / total * 100 if total else 0
            st.metric(f"상위{len(df)}편 관객 합계", f"{total:,}명")
            st.write(f"1위{winner['movieNm']} · 관객 비중{share:.1f}%")
            show_table(df)

    if not df_a.empty and not df_b.empty:
        common = set(df_a["movieCd"]) & set(df_b["movieCd"])
        st.write(f"두 지역의 TOP 10에 함께 오른 영화{len(common)}편")


st.title("🎬 KOBIS 박스오피스 분석")
st.caption("데이터 출처 · 영화진흥위원회 영화관입장권통합전산망(KOBIS) 오픈API")

try:
    api_key = st.secrets["KOBIS_KEY"].strip()
except (KeyError, StreamlitSecretNotFoundError):
    api_key = ""
if not api_key or api_key == "발급받은_API_키":
    st.error("KOBIS_KEY를 로컬 secrets.toml 또는 Streamlit Cloud Secrets에 등록하세요.")
    st.stop()

day = st.date_input("분석 날짜 (한국 시간 기준)", YESTERDAY, max_value=YESTERDAY)
page = st.radio(
    "분석 메뉴",
    ["전국 TOP 10", "7일 관객 추이", "영화 비교", "지역 비교"],
    horizontal=True,
)

try:
    if page == "지역 비교":
        show_regions(day, api_key)
    else:
        nationwide = fetch_boxoffice(day, api_key)
        if nationwide.empty:
            st.info("선택 날짜의 박스오피스가 비어 있습니다. 이전 날짜로 조회해 보세요.")
        elif page == "전국 TOP 10":
            show_ranking(nationwide)
        elif page == "7일 관객 추이":
            show_history(day, nationwide, api_key)
        else:
            show_movies(nationwide)
except KobisError as error:
    st.error(str(error))