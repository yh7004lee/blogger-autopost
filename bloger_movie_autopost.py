#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Excel(MOVIE_ID) → TMDB → Blogger 자동 포스팅 파이프라인
- movies_discover.xlsx 읽기: A=제목, B=MOVIE_ID, C=개봉일, D=평점, E=투표수, F=완료표시
- F열이 "완"인 행은 건너뜨고, 첫 번째 미완료 행(B열의 MOVIE_ID)로 포스팅
- TMDB 상세/출연/이미지/리뷰/추천/예고편 수집
- 랜덤 스피너: 서론(6문장), 섹션 리드(4문장), 마무리(6문장)
- Blogger API로 발행 (blogId=7755804984438912295)
- 성공 시 해당 행 F열에 "완" 기록 후 저장
"""
import urllib.parse
import os, sys, html, textwrap, requests, random, time, pickle
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request


sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ===============================
# 📝 포스팅 설정
POST_COUNT =1     # 몇 번 포스팅할지 (예: 10 이면 10회 반복)
POST_DELAY_MIN = 1   # 각 포스팅 후 대기 시간 (분 단위, 0 이면 즉시 다음 실행)
# ===============================
# 🔧 환경/경로 설정

BLOG_ID = "7755804984438912295"       # 요청하신 블로그 ID
CLIENT_SECRET_FILE = r"D:/py/cc.json" # 본인 구글 OAuth 클라이언트 시크릿 JSON 경로
BLOGGER_TOKEN_PICKLE = "blogger_token.pickle"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

# ===============================
# 🈶 TMDB 설정 (요청: 키를 가리지 말 것 — 사용자가 제공한 값을 그대로 사용)
LANG = "ko-KR"
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"

# 🔑 TMDB 인증정보 (사용자가 예시로 제공한 값 — 그대로 둠)
BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI1NmY0YTNiY2UwNTEyY2RjMjAxNzFhODMxNTNjMjVkNiIsIm5iZiI6MTc1NjY0NjE4OC40MTI5OTk5LCJzdWIiOiI2OGI0NGIyYzI1NzIyYjIzNDdiNGY0YzQiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.ShX_ZJwMuZ1WffeUR6PloXx2E7pjBJ4nAlQoI4l7nKY"
API_KEY = "56f4a3bce0512cdc20171a83153c25d6"



# 🔑 유튜브 API 인증정보
YOUTUBE_API_KEY = "AIzaSyD92QjYwV12bmLdUpdJU1BpFX3Cg9RwN4o"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# 🏷️ 해시태그 생성 함수
def make_hashtags_from_title(title: str) -> str:
    import re
    # 괄호 안 숫자도 분리
    words = re.findall(r"[가-힣A-Za-z0-9]+|\([^)]+\)", title)
    hashtags = ["#" + w.strip() for w in words if w.strip()]
    return " ".join(hashtags)


def get_youtube_trailers(title_ko, title_en=None, max_results=2):
    """유튜브에서 예고편 검색 (한국어 먼저, 없으면 영어로)"""
    def search(query):
        params = {
            "part": "snippet",
            "q": query,
            "key": YOUTUBE_API_KEY,
            "maxResults": max_results,
            "type": "video",
            "videoEmbeddable": "true"
        }
        try:
            r = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            videos = []
            for item in data.get("items", []):
                vid = item["id"]["videoId"]
                vtitle = item["snippet"]["title"]
                videos.append((vid, vtitle))
            return videos
        except Exception as e:
            print(f"❌ YouTube API 오류: {e}")
            return []

    # 1차: 한국어 제목 + "예고편"
    results = search(f"{title_ko} 예고편")
    if results:
        return results

    # 2차: 영어 제목 + "trailer"
    if title_en:
        results = search(f"{title_en} trailer")
        if results:
            return results

    return []


# ===============================
# Google Sheets 연결
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = ServiceAccountCredentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
    return gc.open_by_key(SHEET_ID).sheet1



# ===============================
# 공통 유틸
def tmdb_get(path, params=None, bearer=None, api_key=None):
    url = f"{TMDB_V3_BASE}{path}"
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if params is None:
        params = {}
    if api_key and "api_key" not in params and not bearer:
        params["api_key"] = api_key
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def img_url(path, size="w780"):
    if not path:
        return None
    return f"{IMG_BASE}/{size}{path}"

def choose(*options):
    return random.choice(options)

def maybe(value, prob=0.5):
    return value if random.random() < prob else ""

# ===============================
# TMDB 수집기
def get_movie_bundle(movie_id, lang="ko-KR", bearer=None, api_key=None):
    params = {
        "language": lang,
        "append_to_response": "credits,images",
        "include_image_language": "ko,en,null"
    }
    return tmdb_get(f"/movie/{movie_id}", params=params, bearer=bearer, api_key=api_key)

def get_movie_reviews(movie_id, lang="ko-KR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/reviews", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])

def get_movie_videos(movie_id, lang="ko-KR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/videos", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])

def get_movie_recommendations(movie_id, lang="ko-KR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/recommendations", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])

def get_movie_release_cert(movie_id, bearer=None, api_key=None):
    def map_kr(cert):
        mapping = {
            "ALL": "전체관람가", "G": "전체관람가", "0": "전체관람가",
            "12": "12세 관람가",
            "15": "15세 관람가",
            "18": "청소년 관람불가", "19": "청소년 관람불가", "R": "청소년 관람불가"
        }
        if cert in mapping: return mapping[cert]
        return cert if cert else ""

    data = tmdb_get(f"/movie/{movie_id}/release_dates", bearer=bearer, api_key=api_key)
    results = data.get("results", [])

    def find_cert(cc):
        for r in results:
            if r.get("iso_3166_1") == cc:
                for d in r.get("release_dates", []):
                    c = (d.get("certification") or "").strip()
                    if c:
                        return c
        return ""

    kr = find_cert("KR")
    if kr: return map_kr(kr)
    us = find_cert("US")
    if us: return us
    return ""

# ===============================
def make_intro_6(title, year, genres_str, director_names, main_cast, cert_label, runtime_min, keywords):
    title_bold = f"{title}"
    year_txt = f"{year}년" if year else "개봉연도 미상"
    genre_phrase = genres_str if genres_str else "장르"
    director_one = director_names[0] if director_names else ""
    star_one = main_cast[0] if main_cast else ""
    star_two = main_cast[1] if len(main_cast) > 1 else ""
    runtime_txt = f"{runtime_min}분" if runtime_min else "러닝타임 미상"
    cert_txt = cert_label or "등급 정보 미상"

    # 1. 오프닝 인사 & 영화 소개
    s1 = choose(
        f"안녕하세요! 오늘은 {year_txt} 개봉했던 화제작 <b>{title_bold}</b> 얘기를 준비했어요.",
        f"혹시 <b>{title_bold}</b> 들어보셨나요? {year_txt}에 나온 작품인데 꽤 볼만합니다.",
        f"오늘은 제가 좋아하는 작품 중 하나인 <b>{title_bold}</b>({year_txt})을 소개해드리려 합니다.",
        f"친구들이랑 얘기하다가 떠올라서 <b>{title_bold}</b> 이야기를 준비했어요.",
        f"오늘은 조금 특별하게 <b>{title_bold}</b>({year_txt}) 이야기를 풀어볼까 해요.",
        f"<b>{title_bold}</b>({year_txt}), 보신 분도 계실 테고 아직 못 보신 분도 계실 텐데요.",
        f"오늘은 <b>{title_bold}</b>({year_txt})에 대해 같이 얘기해보면 어떨까 해요.",
        f"<b>{title_bold}</b>({year_txt})라는 영화, 오늘 함께 살펴보겠습니다.",
        f"오늘의 주인공은 바로 <b>{title_bold}</b>({year_txt})입니다.",
        f"가볍게 즐기기 좋은 <b>{title_bold}</b>({year_txt}) 이야기로 시작해볼까요?"
    )

    # 2. 장르 & 분위기
    s2 = choose(
        f"장르는 {genre_phrase}인데, 생각보다 {choose('탄탄하게', '세련되게', '깔끔하게')} 잘 어울려 있어요.",
        f"{genre_phrase} 특유의 재미가 살아 있어서 장르 좋아하신다면 분명 만족하실 겁니다.",
        f"분위기가 {choose('잔잔하면서도 긴장감 있고', '따뜻하면서도 묵직하고', '밝으면서도 여운이 길게 남는')} 작품이에요.",
        f"{genre_phrase} 요소들이 자연스럽게 녹아 들어가 있어서 부담 없이 보실 수 있습니다.",
        f"{genre_phrase}라서 그런지 전체적인 무드가 꽤 매력적으로 다가옵니다.",
        f"톤이 {choose('유쾌하면서도 진지하고', '차분하면서도 몰입감 있고', '화려하면서도 잔잔한')} 느낌이라 재미있어요.",
        f"장르적인 색채가 잘 드러나면서도 과하지 않은 작품이에요.",
        f"분위기와 장르가 잘 맞물려서 흡입력이 강합니다.",
        f"보는 내내 {genre_phrase} 장르만의 매력이 꾸준히 이어집니다.",
        f"{genre_phrase}라서 그런지 몰입하는 재미가 확실히 있더라고요."
    )

    # 3. 감독/연출
    s3 = (
        choose(
            f"연출은 {director_one} 감독이 맡았는데, 역시 {choose('감각적이고', '디테일이 살아 있고', '호흡이 안정적인')} 부분이 눈에 띄더라고요.",
            f"{director_one} 감독 특유의 {choose('리듬감', '섬세한 연출', '독특한 톤')}이 잘 드러납니다.",
            f"한 장면 한 장면에 {director_one} 감독의 색깔이 묻어 있어요.",
            f"{director_one} 감독의 스타일이 영화 전체를 관통합니다.",
            f"{director_one} 감독이 보여주는 디테일이 참 인상적이었어요."
        ) if director_one else choose(
            "연출이 전체적으로 깔끔해서 보는 내내 편안했습니다.",
            "장면마다 흐름이 매끄러워서 큰 끊김 없이 몰입할 수 있었습니다.",
            "전체적인 톤이 안정적이라 부담 없이 볼 수 있어요.",
            "영상미와 연출이 크게 과하지 않아 좋았습니다.",
            "스토리 전개가 안정감 있게 이어져서 보기 편했어요."
        )
    )

    # 4. 배우 & 케미
    s4 = (
        choose(
            f"배우진도 화려합니다. {star_one}{('·'+star_two) if star_two else ''} 등이 나오는데, 케미가 정말 좋아요.",
            f"특히 {star_one}의 연기가 돋보였고{(' '+star_two+'와의 합도 멋졌습니다.') if star_two else ''}",
            f"{star_one}이 끌어가는 힘이 강했고, {star_two}와의 시너지도 좋았습니다." if star_two else f"{star_one}의 존재감이 작품을 꽉 채웠습니다.",
            f"배우들이 서로 호흡이 잘 맞아서 캐릭터가 살아 움직였어요.",
            f"출연진 모두 연기가 자연스러워서 관객이 쉽게 몰입할 수 있습니다."
        ) if star_one else choose(
            "배우들의 합이 꽤 좋아서 보는 재미가 있었습니다.",
            "출연진의 호흡이 자연스러워 캐릭터가 잘 살아났어요.",
            "배우들 간의 연기가 어색하지 않아 몰입하기 좋습니다.",
            "전체적으로 안정적인 연기를 보여줬습니다.",
            "케미가 좋아서 장면마다 활기가 느껴졌습니다."
        )
    )

    # 5. 러닝타임/관람 등급
    s5 = choose(
        f"상영시간은 {runtime_txt}인데 흐름이 끊기지 않아 지루하지 않았습니다.",
        f"{runtime_txt} 동안 집중해서 보기에 충분히 매력적인 구성이에요.",
        f"길다고 느껴지지 않고 오히려 빠르게 지나가는 {runtime_txt}이었습니다.",
        f"전체 러닝타임 {runtime_txt}, 알차게 채워져 있습니다.",
        f"{runtime_txt} 동안 몰입하기에 충분한 작품이에요."
    ) + " " + (
        choose(
            f"관람 등급은 {cert_txt}라서 {choose('가족과 함께 보기에도 괜찮습니다.', '연인·친구와 즐기기에도 좋아요.')}",
            f"연령 제한은 {cert_txt}인데, {choose('생각보다 부담 없이 보실 수 있어요.', '연령대 상관없이 즐기기 좋은 영화입니다.')}"
        ) if cert_label else "등급 정보는 없지만 누구나 편하게 즐길 수 있는 느낌이에요."
    )

    # 6. 안내 멘트 & 키워드
    s6 = choose(
        "아래에서는 줄거리, 출연진, 스틸컷, 평점, 리뷰, 예고편까지 차근차근 정리해드릴게요.",
        "이후 내용에서는 작품의 매력 포인트를 조금 더 자세히 풀어보겠습니다.",
        "이제 본격적으로 영화의 매력 포인트를 하나씩 살펴보겠습니다.",
        "스크롤을 내리시면 다양한 정보와 자료가 이어집니다.",
        "줄거리와 배우들 이야기를 포함해 재미있는 포인트들을 정리했습니다."
    ) + " " + choose(
        f"참고 키워드: {', '.join(keywords[:6])}",
        f"관련 키워드: {', '.join(keywords[:6])}",
        f"이 글의 키워드는 {', '.join(keywords[:6])}입니다.",
        f"검색 키워드: {', '.join(keywords[:6])}"
    )

    return " ".join([s1, s2, s3, s4, s5, s6])



def make_section_lead(name, title, year, genres_str, cert_label, extras=None):
    """각 섹션용 4문장 리드 (친근하고 풍성한 톤, 매우 많은 조합)"""
    extras = extras or {}
    year_txt = f"{year}년" if year else ""
    genre_phrase = genres_str if genres_str else "장르"
    cert_txt = cert_label or "등급 정보 미상"
    cast_top = extras.get("cast_top", [])
    who = "·".join(cast_top[:3]) if cast_top else ""
    director_one = extras.get("director_one", "")
    runtime_min = extras.get("runtime_min", None)
    runtime_txt = f"{runtime_min}분" if runtime_min else ""

 
    if name == "줄거리":
        base = [
            choose(
                f"{title}{f'({year_txt})' if year_txt else ''}의 줄거리를 스포일러는 최대한 피하면서 가볍게 풀어볼게요.",
                f"혹시 아직 안 보신 분들을 위해 {title}의 큰 줄거리만 깔끔하게 정리해드릴게요.",
                f"{title}의 줄거리, 디테일은 아껴두고 핵심 흐름만 편하게 짚어봅시다.",
                f"관람 재미는 지키면서 <b>{title}</b>의 이야기 뼈대를 함께 따라가 보실까요?",
                f"줄거리를 처음 접하시는 분도 이해하기 쉽도록 간단히 풀어드릴게요.",
                f"큰 스포일러 없이 {title}의 주요 사건만 콕콕 짚어드리겠습니다.",
                f"한눈에 흐름을 잡을 수 있도록 {title}의 이야기를 살짝 미리 보여드릴게요.",
                f"줄거리를 차근차근 정리해서 관람 전에 미리 감을 잡아보세요.",
                f"과하지 않게, 하지만 궁금증은 남기도록 {title}의 스토리를 풀어드릴게요.",
                f"궁금증을 해치지 않는 선에서 {title}의 이야기 줄기를 따라가봅시다."
            ),
            choose(
                f"초반에는 설정이 자연스럽게 자리 잡고, 중반부에서는 {choose('갈등이 깊어지고', '긴장이 차오르고', '관계가 얽히고')} 후반으로 갈수록 {choose('감정이 터집니다', '퍼즐이 맞춰집니다', '메시지가 선명해집니다')}.",
                f"{choose('첫 장면은 담백하게', '시작부터 긴장감 있게', '도입은 차분하게')} 열리고, 이어서 {choose('캐릭터들이 본격적으로 움직이고', '숨겨진 비밀이 드러나고', '관계의 갈등이 선명해지고')} 흡입력을 높입니다.",
                f"전체 구조는 {choose('설정→갈등→해결', '출발→위기→성장', '만남→갈등→선택')}으로 이어지며, 장면마다 포인트가 톡톡 살아있어요.",
                f"중반부에 들어서면 이야기가 훨씬 더 속도를 내면서 긴장도가 높아집니다.",
                f"후반부로 갈수록 쌓아온 복선들이 하나둘씩 풀리며 재미가 커져요."
            ),
            choose(
                f"{genre_phrase} 특유의 분위기가 전개에 스며들어 톤이 {choose('균형 있게', '과하지 않게', '차분하게')} 유지됩니다.",
                f"설명이 많지 않아도 장면만으로도 몰입이 이어집니다.",
                f"큰 반전은 직접 보실 수 있도록 아껴두고, 분위기만 살짝 알려드릴게요.",
                f"스토리 라인이 과하지 않아서 자연스럽게 따라갈 수 있어요.",
                f"대사보다는 흐름과 연출로 설득하는 타입이라 색다른 재미가 있습니다."
            ),
            choose(
                f"관람 등급은 {cert_txt}이고, 취향에 따라 {choose('가족과 함께 보셔도 좋습니다.', '친구와 편하게 보기에도 괜찮습니다.', '혼자 집중해서 감상해도 좋아요.')}",
                f"{cert_txt} 기준이라 부담 없이 보실 수 있고, 분위기만 따라가시면 충분히 즐겁습니다.",
                f"등급은 {cert_txt}이니 참고하시고, 감상은 자유롭게 즐기시면 됩니다.",
                f"연령 제한은 {cert_txt}이지만, 누구나 공감할 만한 주제를 담고 있어요."
            )
        ]
        if maybe(True, 0.4):
            base.append(
                choose(
                    "아래에서 조금 더 디테일을 담아 정리해 드릴게요.",
                    "이제 주요 장면과 감정선을 하나씩 따라가 보시죠.",
                    "큰 줄기는 파악했으니, 디테일을 이어서 보시면 더 재미있습니다."
                )
            )


    
    elif name == "출연진":
        base = [
            choose(
                f"이번 캐스팅은 {who} {('등' if who else '')}으로 꾸려졌는데, 이름만 들어도 왜 화제가 됐는지 감이 오실 거예요.",
                f"배우 라인업부터 시선이 가죠{': ' + who if who else ''}. 화면 장악력이 탄탄합니다.",
                f"첫 크레딧부터 반가운 얼굴들이 줄줄이 등장합니다{(' — ' + who) if who else ''}.",
                f"{who} {('등' if who else '')} 출연진 덕분에 영화 보는 내내 든든합니다." if who else "출연진 라인업만 봐도 기대감이 차오릅니다.",
                f"이름만 들어도 알 만한 배우들이 모여서 작품에 힘을 실어줍니다.",
                f"배우 명단만 봐도 ‘아, 이건 볼만하겠다’ 싶은 느낌이 들어요.",
                f"출연진 구성을 보면 제작진이 왜 이렇게 자신 있었는지 알 수 있습니다.",
                f"한 명 한 명 존재감이 확실한 배우들이 모였습니다.",
                f"주요 배역들 이름만 나와도 ‘와’ 하고 감탄이 나오죠.",
                f"믿고 보는 배우들이 출연하는 작품이라는 점에서 벌써 설렘이 느껴집니다."
            ),
            choose(
                f"{choose('주연과 조연의 균형이 잘 맞아', '톤이 서로 잘 붙어서', '대사 호흡이 찰떡같아서')} 캐릭터가 자연스럽게 살아납니다.",
                f"{choose('눈빛과 제스처가', '리액션 타이밍이', '호흡의 간격이')} 장면을 밀어줘서 과장되지 않고 매끄러워요.",
                f"배우들 호흡이 좋아서 감정선이 {choose('자연스럽게 이어지고', '무리 없이 쌓이고', '점점 고조되며')} 클라이맥스에서 빛납니다.",
                f"연기 톤이 통일감이 있어서 몰입이 잘 됩니다.",
                f"대사 전달이 과하지 않고 자연스러워서 설득력이 있어요.",
                f"주연진과 조연진이 균형을 맞춰서 캐릭터가 살아납니다.",
                f"연기 톤이 안정적이라 관객이 편안하게 몰입할 수 있습니다.",
                f"출연진의 합이 좋아 장면마다 긴장감이 살아납니다.",
                f"대사와 감정의 호흡이 착착 맞아떨어집니다.",
                f"인위적인 느낌이 거의 없어 실제 상황처럼 몰입이 됩니다."
            ),
            choose(
                f"특히 {choose('캐릭터 대비', '세대 차이', '가치관 충돌')}에서 오는 케미가 볼만합니다.",
                f"{choose('콤비 플레이', '앙상블 연기', '팀플레이')}가 잘 맞아서 장면마다 재미가 배가됩니다.",
                f"짧게 등장하는 카메오도 포인트가 되니 눈여겨보세요.",
                f"조연진의 존재감이 커서 이야기가 더욱 풍성해졌습니다.",
                f"배우들의 시너지가 장면마다 톡톡 튀어납니다.",
                f"예상 못 했던 조합이 만들어내는 묘한 긴장감도 있습니다.",
                f"인물 간의 대비가 뚜렷해 주제가 더 선명하게 다가옵니다.",
                f"작은 배역까지도 제 몫을 다해서 빈틈이 없습니다.",
                f"단역조차도 인상 깊은 연기를 보여줍니다.",
                f"한 장면만 나와도 존재감을 확실히 남기는 배우들이 있습니다."
            ),
            choose(
                "아래에서는 주요 배역과 간단한 소개를 정리해 드릴게요.",
                "이제 배우별로 어떤 캐릭터를 맡았는지 살펴보겠습니다.",
                "바로 이어서 출연진 정보를 하나씩 소개해 드릴게요.",
                "어떤 배우가 어떤 역할을 맡았는지 바로 확인해 보시죠.",
                "이제 출연진 리스트를 조금 더 자세히 들여다봅시다.",
                "각 배우의 배역과 특징을 간단히 정리해 드리겠습니다.",
                "배우들이 맡은 캐릭터를 하나씩 소개할게요.",
                "출연진을 캐릭터별로 짚어드리겠습니다.",
                "아래에서 출연진과 배역 정보를 확인해 보시죠.",
                "각 배우가 연기한 인물이 어떤 색깔을 가졌는지 알려드릴게요."
            )
        ]


 
    elif name == "스틸컷":
        base = [
            choose(
                "스틸컷만 봐도 영화의 공기가 먼저 느껴지죠.",
                "몇 장의 스틸컷만 훑어도 영화 분위기가 훤히 보입니다.",
                "이미지 몇 장만으로도 작품의 색깔이 전해져요.",
                "스틸컷을 보는 순간 영화가 어떤 톤인지 감이 오실 거예요.",
                "한두 장의 사진만 봐도 영화 무드가 확 들어옵니다.",
                "스틸컷은 짧지만 영화의 핵심 감정을 먼저 보여줍니다.",
                "몇 장면만 보셔도 전체적인 무드가 선명하게 다가와요.",
                "스틸컷은 영화의 첫인상이라고 할 수 있습니다.",
                "짧은 컷에서도 영화가 가진 분위기가 생생하게 느껴집니다.",
                "이미지 몇 장으로도 스토리의 결을 어림짐작할 수 있습니다."
            ),
            choose(
                f"{choose('프레임 구도', '촬영 각도', '여백 활용')}이 안정적이라 눈이 편안합니다.",
                f"{choose('색감 톤', '조명', '콘트라스트')}이 {choose('세련돼서', '차분해서', '강렬해서')} 장면이 오래 남습니다.",
                f"프로덕션 디자인이 {choose('상황에 딱 맞고', '과하지 않아서', '감정선과 맞물려서')} 화면이 꽉 차 보입니다.",
                f"화면 구도가 균형감 있게 잡혀서 보는 재미가 있어요.",
                f"빛과 색을 다루는 방식이 인상 깊습니다.",
                f"작은 디테일 하나까지도 신경 쓴 흔적이 스틸컷에 묻어나요.",
                f"구성과 색채가 조화를 이뤄 장면이 한 폭의 그림 같습니다.",
                f"카메라 워크의 느낌이 스틸컷에도 고스란히 담겼습니다.",
                f"색감이 분위기를 결정짓는 큰 힘을 발휘합니다.",
                f"촬영 미술이 영화의 무드를 그대로 보여줍니다."
            ),
            choose(
                "컷만 봐도 감정선의 흐름이 어렴풋이 이어집니다.",
                "정지된 장면 속에서도 인물들의 감정이 전해져요.",
                "스틸컷만 보고도 다음 장면이 궁금해집니다.",
                "사진 속 움직임만으로도 이야기가 이어지는 것 같아요.",
                "정적인 이미지인데도 긴장감이 묻어나옵니다.",
                "짧은 순간을 담았는데도 여운이 길게 남습니다.",
                "스틸컷에서만 보이는 디테일이 은근히 많습니다.",
                "사진 몇 장만으로도 스토리의 퍼즐이 맞춰지는 느낌이에요.",
                "컷 속 인물들의 표정만으로도 많은 이야기를 읽을 수 있습니다.",
                "짧은 장면이지만 영화 전체의 무드를 대변합니다."
            ),
            choose(
                "아래 이미지들을 보면서 영화의 분위기를 미리 만나보세요.",
                "스틸컷을 먼저 보고 나면 본편을 볼 때 더 몰입이 됩니다.",
                "사진을 통해 영화의 매력을 한 발 먼저 느껴보시죠.",
                "이미지를 보고 나면 작품의 디테일이 더 눈에 들어옵니다.",
                "컷들을 보며 관람 포인트를 미리 체크해 두시면 좋아요.",
                "스틸컷은 본편 감상의 작은 예고편 같은 역할을 합니다.",
                "사진을 먼저 보고 나면 영화에 들어갈 준비가 됩니다.",
                "이 장면들을 기억해두시면 본편에서 반가움이 배가됩니다.",
                "이미지로 먼저 분위기를 잡아두면 감상이 한층 풍부해집니다.",
                "스틸컷을 보며 어떤 장면을 특히 기대해야 할지 골라보세요."
            )
        ]


   
    elif name == "평점 및 인기":
        base = [
            choose(
                f"{title}의 평점은 관객 반응을 한눈에 보여주는 지표예요.",
                f"이 작품의 평점만 봐도 대중적인 반응이 어느 정도인지 감이 옵니다.",
                f"평점은 작품에 대한 첫인상을 빠르게 확인할 수 있는 방법입니다.",
                f"숫자로 표현된 평점은 관객들의 솔직한 마음을 보여주죠.",
                f"{title}의 점수는 화제성과 인기를 짐작하게 해줍니다.",
                f"평점은 작품이 얼마나 사랑받았는지 보여주는 잣대가 되기도 합니다.",
                f"관객들의 기대치와 만족도를 가늠할 수 있는 게 바로 평점이죠.",
                f"평점은 작품의 흥행 흐름을 가장 쉽게 확인할 수 있는 지표입니다.",
                f"{title}의 점수는 사람들이 얼마나 열광했는지를 간접적으로 보여줍니다.",
                f"한눈에 보기 좋은 평점으로 작품의 위상을 확인할 수 있습니다."
            ),
            choose(
                "투표 수와 평균 점수는 단순 숫자 이상으로 작품의 화제성을 말해줍니다.",
                "참여한 투표 수가 많을수록 점수의 신뢰도가 올라가요.",
                "평균값과 함께 표본 크기를 보면 더 정확한 느낌이 옵니다.",
                "투표가 많이 쌓일수록 작품의 대중적 인지도를 실감할 수 있습니다.",
                "평점 수가 많다는 건 그만큼 화제가 된 작품이라는 뜻이겠죠.",
                "많은 사람들이 평가에 참여했다는 건 작품에 관심이 높았다는 의미예요.",
                "평균 점수만이 아니라 표본 크기도 중요하게 보셔야 합니다.",
                "투표 수는 작품이 얼마나 널리 알려졌는지를 가늠하게 해줍니다.",
                "참여자 수와 평균 점수를 함께 보면 작품의 입지를 알 수 있어요.",
                "평점 데이터는 단순히 점수 이상의 의미를 담고 있습니다."
            ),
            choose(
                "물론 숫자가 모든 걸 말해주진 않아요. 결국 직접 보는 게 가장 정확합니다.",
                "평점이 높다고 무조건 재밌는 건 아니고, 낮다고 재미없는 것도 아니죠.",
                "점수는 참고만 하시고 본인의 취향이 훨씬 중요합니다.",
                "평점은 가이드일 뿐, 최종 판단은 본인이 하시는 게 좋아요.",
                "수치가 높아도 취향에 맞지 않으면 재미없을 수 있습니다.",
                "점수가 낮아도 본인에게는 인생작이 될 수 있죠.",
                "결국 가장 중요한 건 내가 재미있게 보느냐입니다.",
                "평점은 참고 자료일 뿐이니 너무 신경 쓰지 마세요.",
                "점수는 대중의 목소리일 뿐, 본인의 느낌이 제일 중요해요.",
                "평점은 가벼운 가이드라인 정도로만 활용하시면 됩니다."
            ),
            choose(
                "아래 수치를 보시고, 가볍게 참고만 해주세요.",
                "숫자는 참고용으로만 확인하시고, 나머지는 본능에 맡기세요.",
                "표를 보면서 대중적 반응을 한눈에 확인해보세요.",
                "수치와 함께 관객들의 생생한 반응도 같이 보시면 더 재미있습니다.",
                "데이터는 가볍게 참고만 하시고, 본편에서 직접 답을 찾아보세요.",
                "평균값보다는 분포와 분위기를 보는 것도 재미있습니다.",
                "숫자만 보지 마시고, 리뷰와 함께 읽으면 더 도움이 됩니다.",
                "아래 표는 빠르게 반응을 정리한 거라 가볍게만 보시면 돼요.",
                "결국 선택은 본인 취향이니, 숫자는 그냥 참고만 하시면 충분합니다.",
                "표와 수치를 보며 작품의 반응 흐름만 살짝 체크해 보세요."
            )
        ]


    
    elif name == "베스트 리뷰":
        base = [
            choose(
                "관객 리뷰는 짧은 글 속에서도 생생한 감정이 묻어나요.",
                "리뷰 한 줄만 읽어도 실제 관람 분위기가 느껴집니다.",
                "관객들의 후기를 보면 작품이 어떻게 받아들여졌는지 바로 알 수 있어요.",
                "짧지만 강렬한 리뷰가 영화의 매력을 잘 보여줍니다.",
                "리뷰는 수치보다 더 직접적인 관객의 목소리예요.",
                "짧은 코멘트에도 진짜 관객의 감정이 스며 있습니다.",
                "한두 줄 리뷰에도 의외로 영화의 핵심이 담겨 있어요.",
                "관객들의 솔직한 느낌은 데이터보다 더 와 닿습니다.",
                "실제 본 사람들의 말이라 신뢰가 가죠.",
                "리뷰는 생생한 현장감이 있어 읽는 재미가 쏠쏠합니다."
            ),
            choose(
                "취향에 따라 호불호가 갈릴 수 있지만, 그게 또 영화의 매력이죠.",
                "좋다는 의견도, 아쉽다는 의견도 모두 작품의 또 다른 해석입니다.",
                "긍정적인 반응과 비판적인 시선이 함께 어우러져 전체적인 그림을 만듭니다.",
                "감상평은 다양할수록 영화가 가진 폭이 넓다는 뜻이에요.",
                "호평과 혹평이 동시에 존재한다는 건 그만큼 화제가 됐다는 증거죠.",
                "서로 다른 시선이 모여서 영화의 다층적인 면을 보여줍니다.",
                "리뷰는 같아도 사람마다 해석이 달라지는 게 흥미롭습니다.",
                "찬반이 갈려도, 결국은 그게 영화의 재미 아닐까요?",
                "누군가에겐 인생작, 누군가에겐 평범작. 이런 다양성이 소중합니다.",
                "취향이 다른 만큼 감상평도 다채롭게 나오는 게 자연스러워요."
            ),
            choose(
                "아래에는 인상적인 리뷰들을 간추려 담아봤습니다.",
                "스포일러를 최대한 피해 간단한 후기만 모아봤습니다.",
                "대표적인 리뷰를 모아 작품의 인상을 엿보실 수 있습니다.",
                "짧고 굵은 리뷰들을 모아 읽는 재미가 있어요.",
                "후기를 요약해 정리했으니 편하게 확인해 보세요.",
                "관람 후기를 정리해 두었으니 참고하시면 도움이 됩니다.",
                "짧은 코멘트들이지만, 영화의 분위기를 충분히 전합니다.",
                "핵심만 정리된 리뷰라 빠르게 훑어보기 좋습니다.",
                "인상적인 문장 위주로 추려 소개해 드립니다.",
                "짧은 후기들이 모여 영화의 또 다른 면을 보여줍니다."
            ),
            choose(
                "읽다 보면 본인이 어떤 포인트를 좋아하는지 자연스럽게 드러납니다.",
                "마음에 드는 표현이 있다면 감상 후 다시 확인해 보셔도 좋아요.",
                "리뷰 속 문장이 나의 감정과 겹칠 때 묘한 공감이 생깁니다.",
                "다른 사람의 시선에서 작품을 다시 보는 재미가 있습니다.",
                "후기를 읽으며 관람 포인트를 미리 체크할 수 있습니다.",
                "짧은 감상 속에서도 ‘아, 이런 느낌이구나’ 하고 감이 옵니다.",
                "리뷰를 통해 영화의 또 다른 매력을 발견할 수도 있어요.",
                "관객들의 말에서 영화의 숨은 포인트가 보일 때가 있습니다.",
                "다른 시각의 후기를 읽으면 내 감상이 더 깊어질 수도 있어요.",
                "리뷰 속 감정이 내 취향과 맞아떨어질 때 묘하게 기분이 좋아집니다."
            )
        ]


    
    elif name == "예고편":
        base = [
            choose(
                "예고편은 영화의 분위기와 톤을 가장 빠르게 알려줍니다.",
                "짧은 예고편 속에 영화의 핵심 무드가 담겨 있어요.",
                "예고편만 봐도 이 영화가 어떤 느낌인지 감이 옵니다.",
                "짧지만 강렬한 예고편이 본편에 대한 기대를 높여줍니다.",
                "예고편은 본격 감상 전, 작품의 색깔을 엿볼 수 있는 창문 같아요.",
                "예고편은 영화의 첫인상을 보여주는 명함 같은 역할을 합니다.",
                "몇 초 안 되는 영상만으로도 영화의 매력이 살아납니다.",
                "짧게 스쳐가는 장면들만 봐도 전체적인 무드가 전달됩니다.",
                "예고편은 본편에 들어가기 전 흥미를 끌어올리는 장치입니다.",
                "예고편만으로도 영화의 매력을 충분히 맛볼 수 있습니다."
            ),
            choose(
                "스포일러 걱정 없이 분위기만 살짝 확인하실 수 있습니다.",
                "예고편은 긴장감을 살짝 풀어주면서도 궁금증을 남겨줍니다.",
                "짧은 시간 안에 영화의 리듬과 감정을 전해줍니다.",
                "컷 편집과 사운드만으로도 몰입감을 체감할 수 있어요.",
                "예고편을 보고 나면 본편이 더 궁금해집니다.",
                "음악과 장면의 배치만으로도 영화의 성격이 드러납니다.",
                "리듬과 템포가 본편의 기운을 미리 전해줍니다.",
                "예고편은 짧지만 서사의 방향을 암시합니다.",
                "음향과 영상만으로도 ‘아, 이런 영화구나’ 하고 감이 옵니다.",
                "짧은 영상인데도 인상적인 장면이 많이 담겨 있습니다."
            ),
            choose(
                f"{runtime_txt+' 동안 ' if runtime_txt else ''}집중해서 예고편을 보시면 본편의 색깔이 빠르게 잡힙니다.",
                "예고편 속 몇 초의 대사가 영화의 전체 톤을 대변합니다.",
                "첫 장면과 마지막 장면이 본편의 힌트를 담고 있기도 합니다.",
                "짧은 영상이지만 메시지가 꽤 강렬하게 다가옵니다.",
                "예고편만 보고도 어떤 감정선을 따라갈지 감이 옵니다.",
                "영상미와 음악만으로도 매력을 전달하기 충분합니다.",
                "예고편 속 한 장면이 본편을 관람하게 만드는 결정적 계기가 되기도 합니다.",
                "짧은 분량에도 몰입을 이끌어내는 힘이 있습니다.",
                "예고편은 본편의 작은 티저지만 여운은 길게 남습니다.",
                "몇 장면만 보고도 영화의 주제 의식이 드러나기도 합니다."
            ),
            choose(
                "가능하다면 이어폰으로 한 번, 큰 스피커로 한 번 보세요. 느낌이 달라집니다.",
                "자막을 켜고 보면 대사 톤과 뉘앙스가 더 잘 들어옵니다.",
                "짧지만 몰입해서 보시면 본편의 매력이 더 크게 다가올 거예요.",
                "첫 10초, 마지막 10초에 영화의 매력이 압축돼 있는 경우도 많습니다.",
                "짧은 예고편을 여러 번 돌려보면 숨겨진 디테일이 보입니다.",
                "예고편 속 장면을 기억해두면 본편에서 만났을 때 반가움이 배가됩니다.",
                "빠르게 훑어보는 것보다 집중해서 보는 게 훨씬 좋습니다.",
                "짧은 시간에도 제작진의 디테일이 숨어 있으니 눈여겨보세요.",
                "사운드와 화면의 합이 본편 못지않게 인상적일 때가 있습니다.",
                "짧은 예고편이지만 영화의 무드를 제대로 보여줍니다."
            ),
            choose(
                "아래 영상을 보시고 끌린다면 본편으로 자연스럽게 이어가 보세요.",
                "예고편은 본편의 작은 맛보기이자 초대장 같은 역할을 합니다.",
                "짧은 클립을 보고 마음이 움직이면 본편도 즐겁게 보실 수 있을 거예요.",
                "취향에 맞는지 확인하기에 예고편만큼 좋은 것도 없습니다.",
                "예고편을 보고 나면 본편 선택이 훨씬 쉬워집니다.",
                "영상 하나로도 이 작품이 내 취향인지 금방 알 수 있어요.",
                "예고편은 본편에 대한 기대를 키우는 가장 좋은 도구입니다.",
                "작품을 처음 접할 때는 예고편이 가장 좋은 길잡이가 됩니다.",
                "마음이 움직였다면 본편으로 바로 이어가도 후회 없으실 거예요.",
                "예고편을 통해 영화의 매력을 가볍게 먼저 느껴보세요."
            )
        ]


    

 
    elif name == "추천 영화":
        base = [
            choose(
                f"{title}을(를) 보셨다면 아래 포스터 속 작품들도 한 번 눈여겨보세요.",
                f"{title}을(를) 즐기셨다면 분위기가 비슷한 영화들을 모아봤습니다.",
                f"{title}과(와) 어울리는 추천작들을 포스터로 준비했습니다.",
                f"비슷한 무드의 영화들을 이미지로 소개해드려요.",
                f"아래 포스터에서 취향에 맞는 작품을 찾아보세요.",
                f"{title}과(와) 연결되는 또 다른 영화들을 포스터로 만나보세요."
            ),
            choose(
                "이번 추천 영화들은 간단히 제목과 포스터만 담았습니다.",
                "상세 설명 대신 깔끔하게 이미지로만 준비했어요.",
                "부담 없이 스크롤하면서 가볍게 확인해 보시면 됩니다.",
                "짧고 직관적으로 포스터만 모아봤습니다.",
                "텍스트 설명은 생략하고, 직관적인 이미지로 보여드립니다."
            ),
            choose(
                "마음에 드는 포스터가 있다면 체크해 두세요.",
                "눈에 들어오는 작품은 바로 리스트에 담아두셔도 좋습니다.",
                "포스터만 봐도 무드가 전해질 거예요.",
                "한눈에 비교하면서 골라보시면 재미있습니다.",
                "이미지 속 작품 중에서 ‘오늘의 영화’를 골라보세요."
            ),
            choose(
                "그럼 추천 영화 포스터들을 함께 살펴보겠습니다.",
                "아래 이미지를 보면서 취향에 맞는 영화를 골라보세요.",
                "포스터만으로도 충분히 매력을 느끼실 수 있을 겁니다.",
                "아래 영화들을 가볍게 확인해보세요.",
                "즐겁게 감상할 수 있는 추천작들을 지금 만나보세요."
            )
        ]


    else:
        base = [
            choose(
                "핵심만 빠르게 훑고, 필요한 건 바로 체크할 수 있게 정리했어요.",
                "한눈에 들어오는 구조로 준비했으니, 편하게 내려가며 보시면 됩니다.",
                "중요한 포인트부터 차근차근 담았으니, 필요한 부분만 골라 읽어도 좋아요."
            ),
            choose(
                "섹션은 직관적인 순서로 배치했고, 각 항목마다 짧은 코멘트를 붙였습니다.",
                "장면·정보·후기 흐름으로 이어지니, 자연스럽게 따라오실 거예요.",
                "필요하면 북마크해 두고 천천히 보셔도 좋습니다."
            ),
            choose(
                "중간중간 개인 취향 팁도 살짝 얹어둘게요.",
                "과한 수사는 덜고, 실용적인 힌트를 조금 더 챙겼습니다.",
                "부담 없이 읽히는 길이로 맞춰놨어요."
            ),
            choose(
                "그럼 아래부터 바로 보시죠.",
                "이제 본격적으로 들어가 볼게요."
            )
        ]

    return " ".join(base)


def make_outro_6(title, year, genres_str, director_names, keywords):
    director_one = director_names[0] if director_names else ""
    year_txt = year if year else "개봉연도 미상"

    # 1. 오프닝 멘트
    s1 = choose(
        f"지금까지 <b>{title}</b>({year_txt})에 대해 함께 이야기 나눠봤습니다.",
        f"오늘은 <b>{title}</b>({year_txt})의 매력 포인트를 정리해봤는데 어떠셨나요?",
        f"<b>{title}</b>({year_txt})의 주요 정보들을 쭉 살펴봤습니다.",
        f"<b>{title}</b>({year_txt})에 대해 제가 느낀 부분들을 정리해봤습니다.",
        f"짧게나마 <b>{title}</b>({year_txt})를 정리했는데, 도움이 되셨길 바랍니다.",
        f"오늘 포스팅은 <b>{title}</b>({year_txt})에 대한 제 생각과 정보였어요.",
        f"<b>{title}</b>({year_txt}) 관람 포인트들을 한 번에 모아봤습니다.",
        f"<b>{title}</b>({year_txt})가 가진 매력들을 요약해서 정리했어요.",
        f"<b>{title}</b>({year_txt})에 대한 소개, 여러분 마음에 와닿았길 바랍니다.",
        f"저와 함께한 <b>{title}</b>({year_txt}) 이야기, 어떠셨나요?"
    )

    # 2. 내용 요약
    s2 = choose(
        "줄거리부터 연출, 배우들의 연기까지 한눈에 담아봤고,",
        "스토리 라인과 연출, 연기 톤을 고르게 다뤄봤으며,",
        "작품 전반의 흐름과 감정선, 그리고 연출 포인트까지 정리했고,",
        "연출·연기·스토리 균형을 중심으로 살펴봤고,",
        "영상미, 캐릭터, 사운드까지 빠짐없이 챙겨봤고,",
        "주요 장면과 디테일을 놓치지 않고 담아봤으며,",
        "전체적인 톤과 메시지, 그리고 배우들의 합까지 다뤄봤습니다.",
        "작품이 가진 강점들을 종합적으로 점검해봤습니다.",
        "여러 요소를 함께 훑어보면서 균형 있게 정리해봤습니다.",
        "작품을 이해하는 데 꼭 필요한 핵심만 간추려봤습니다."
    ) + " " + choose(
        f"{genres_str} 장르 특유의 매력도 함께 확인해봤습니다." if genres_str else "장르적 매력도 함께 다뤘습니다.",
        f"{director_one} 감독의 연출 색깔도 눈여겨봤습니다." if director_one else "연출의 흐름과 호흡도 챙겨봤습니다.",
        "연출적인 디테일도 살짝 짚어봤습니다.",
        "전체적인 톤 앤 무드도 함께 살펴봤습니다."
    )

    # 3. 평점/판단 관련 멘트
    s3 = choose(
        "관객 평점과 인기 지수는 참고만 하시고,",
        "평점과 수치는 어디까지나 가이드일 뿐이고,",
        "숫자와 지표는 보조 도구일 뿐이니,",
        "평점과 지표는 감상 전에 참고만 해보시고,",
        "수치보다는 직접적인 감상이 더 중요하니,",
        "숫자는 참고만 하고 너무 의존하지 마시고,",
        "지표는 방향만 알려줄 뿐이니,",
        "수치는 참고 자료일 뿐이라는 점, 잊지 마세요.",
        "평점은 참고하시되, 최종 선택은 본인의 몫이고,",
        "데이터는 보조 수단일 뿐이니,"
    ) + " " + choose(
        "최종 판단은 여러분 취향에 맡기시면 됩니다.",
        "결정은 결국 본인이 가장 즐길 수 있는 쪽으로 하시면 돼요.",
        "선택은 본인의 감각을 믿는 게 가장 좋습니다.",
        "마지막 결정은 스스로의 감정에 맡기시면 됩니다.",
        "본인이 느끼는 감각이 가장 정확한 기준이 될 거예요.",
        "취향에 맞는 작품을 직접 선택하시는 게 제일 좋습니다.",
        "최종 선택은 본인이 하고 싶은 대로 하시면 충분해요.",
        "여러분의 직감이 가장 좋은 나침반이 될 겁니다.",
        "결국 본인이 끌리는 작품을 고르는 게 정답이에요.",
        "스스로의 감각을 믿으시면 됩니다."
    )

    # 4. 추천작 안내
    s4 = choose(
        "비슷한 장르의 추천작도 함께 소개해드렸으니 이어서 감상하시면 좋습니다.",
        "추천 영화들도 함께 준비했으니 재미있게 이어가 보세요.",
        "분위기가 닮은 작품들을 함께 보시면 더 깊은 여운이 남을 겁니다.",
        "비슷한 결의 작품들을 챙기면 감상이 훨씬 풍성해집니다.",
        "비슷한 장르의 영화들을 이어서 보면 몰입도가 배가됩니다.",
        "추천작까지 챙기시면 영화 여행이 훨씬 즐거워질 거예요.",
        "분위기나 톤이 닮은 영화들이라 연달아 보기도 좋습니다.",
        "비슷한 무드의 작품을 함께 보면 감정선이 이어져요.",
        "비슷한 영화들을 골라보시면 의외의 인생작을 만날 수도 있어요.",
        "추천작을 통해 새로운 작품도 발견해 보시길 바랍니다."
    )

    # 5. 키워드 안내
    s5 = choose(
        f"검색 키워드: {', '.join(keywords[:8])}",
        f"관련 키워드: {', '.join(keywords[:8])}",
        f"이번 글의 주요 키워드는 {', '.join(keywords[:8])}입니다.",
        f"키워드 정리: {', '.join(keywords[:8])}",
        f"참고 키워드: {', '.join(keywords[:8])}",
        f"검색 시 참고할 키워드: {', '.join(keywords[:8])}",
        f"작품 연관 키워드: {', '.join(keywords[:8])}",
        f"함께 보면 좋은 키워드: {', '.join(keywords[:8])}",
        f"관련 검색어: {', '.join(keywords[:8])}",
        f"이번 리뷰 키워드: {', '.join(keywords[:8])}"
    )

    # 6. 마무리 멘트
    s6 = choose(
        "오늘 글이 영화 고르실 때 작은 도움이 되었길 바라요. 편하게 댓글로 얘기 나눠주세요 🙂",
        "읽어주셔서 감사합니다! 도움이 되셨다면 따뜻한 ♥ 눌러주시면 힘이 됩니다.",
        "오늘 내용이 괜찮으셨다면 다른 영화 글들도 함께 확인해 보세요!",
        "끝까지 읽어주셔서 감사드려요. 댓글로 의견 남겨주시면 소통할 수 있어 즐겁습니다.",
        "앞으로도 다양한 작품 소개해드릴게요. 이웃 추가하시면 새 글도 바로 보실 수 있습니다!",
        "영화 이야기 함께 나누는 시간이 즐겁네요. 다음 글도 기대해 주세요!",
        "조금이나마 도움이 되었다면 정말 기쁩니다. 공감 ♥ 부탁드려요.",
        "혹시 더 궁금한 점 있으면 댓글 남겨주세요. 같이 이야기 나눠봐요!",
        "도움이 되셨길 바라며, 여러분의 영화 생활이 더 즐겁길 응원합니다.",
        "오늘의 포스팅이 유익하셨다면 주변에도 공유해 주시면 큰 힘이 됩니다!"
    )

    return " ".join([s1, s2, s3, s4, s5, s6])


# ===============================
# HTML 빌더
def get_related_posts(blog_id, count=4):
    import feedparser
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    # 랜덤으로 count개 추출
    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    # HTML 박스 생성 (요청하신 스타일 적용)
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ 같이 보면 좋은글</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box


def build_html(post, cast_count=10, stills_count=8):
    esc = html.escape
    title = esc(post.get("title") or post.get("original_title") or "제목 미상")
    
    overview = esc(post.get("overview") or "줄거리 정보가 아직 준비되지 않았습니다.")
    release_date = esc(post.get("release_date") or "")
    year = release_date[:4] if release_date else ""
    runtime = post.get("runtime") or 0
    genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
    genres_str = ", ".join(genres_list)
    tagline = esc(post.get("tagline") or "")
    adult_flag = bool(post.get("adult", False))
       # 장르
    genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
    genres_str = ", ".join(genres_list)

    # 제작 국가
    countries = [c.get("name","") for c in post.get("production_countries",[]) if c.get("name")]
    country_str = ", ".join(countries) if countries else "국가 정보 없음"

    backdrop = img_url(post.get("backdrop_path"), "w1280")

    credits = post.get("credits", {}) or {}
    cast = credits.get("cast", [])[:cast_count]
    crew = credits.get("crew", [])
    directors = [c for c in crew if c.get("job") == "Director"]
    director_names = [esc(d.get("name","")) for d in directors]
    cast_names = [esc(p.get("name","")) for p in cast]

    backdrops = (post.get("images", {}) or {}).get("backdrops", [])
    backdrops = sorted(backdrops, key=lambda b: (b.get("vote_count",0), b.get("vote_average",0)), reverse=True)[:stills_count]

    cert = get_movie_release_cert(post["id"], bearer=BEARER, api_key=API_KEY)
    if not cert and adult_flag: cert = "성인 컨텐츠"

    # 키워드 생성
    base_keywords = []
    for w in (title.replace(":", " ").replace("-", " ").split()):
        if len(w) > 1: base_keywords.append(w)
    base_keywords += genres_list
    base_keywords += director_names[:2]
    base_keywords += cast_names[:3]
    if year: base_keywords.append(year)
    if cert: base_keywords.append(cert)
    
        # 키워드 생성
    base_keywords = []
    for w in (title.replace(":", " ").replace("-", " ").split()):
        if len(w) > 1:
            base_keywords.append(str(w))

    # 장르, 감독, 배우 이름도 문자열만
    for g in genres_list:
        if g: base_keywords.append(str(g))
    for d in director_names[:2]:
        if d: base_keywords.append(str(d))
    for c in cast_names[:3]:
        if c: base_keywords.append(str(c))

    if year:
        base_keywords.append(str(year))
    if cert:
        base_keywords.append(str(cert))

    # 고정 키워드
    base_keywords += ["리뷰", "평점", "출연진", "예고편", "스틸컷", "추천영화", "관람포인트", "해석"]

    # 중복 제거 (문자열만)
    seen = set()
    keywords = []
    for k in base_keywords:
        if isinstance(k, str):
            if k and k not in seen:
                keywords.append(k)
                seen.add(k)


    intro_6 = make_intro_6(title, year, genres_str, director_names, cast_names, cert, runtime, keywords)

    # 출연진 테이블
    cast_rows = []
    for p in cast:
        name = esc(p.get("name",""))
        ch = esc(p.get("character",""))
        prof = img_url(p.get("profile_path"), "w185")
        img_tag = f'<img src="{prof}" alt="{name}" style="width:72px;height:auto;border-radius:8px;">' if prof else ""
        cast_rows.append(
            f'<tr>'
            f'<td style="vertical-align:top;padding:8px 10px;white-space:nowrap;">{img_tag}</td>'
            f'<td style="vertical-align:top;padding:8px 10px;"><b>{name}</b><br><span style="color:#666;">{ch}</span></td>'
            f'</tr>'
        )
    cast_table = (
        '<table style="width:100%;border-collapse:collapse;border:1px solid #eee;">' +
        "".join(cast_rows or ['<tr><td style="padding:10px;">출연진 정보가 없습니다.</td></tr>']) +
        '</table>'
    )

    # 스틸컷
    still_divs = []
    for b in backdrops:
        p = img_url(b.get("file_path"), "w780")
        if not p: continue
        still_divs.append(
            f'<div style="flex:0 0 49%;margin:0.5%;"><img src="{p}" alt="{title} 스틸컷" style="width:100%;height:auto;border-radius:10px;"></div>'
        )
    stills_html = (
        '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;">' +
        "".join(still_divs or ['<div style="padding:10px;">스틸컷 이미지가 없습니다.</div>']) +
        '</div>'
    )

    # 평점·예고편
    rating_lead = make_section_lead("평점 및 인기", title, year, genres_str, cert)

    vote_avg = post.get("vote_average", 0)
    vote_count = post.get("vote_count", 0)
    popularity = post.get("popularity", 0)

    rating_html = f"""
    <div style="background:linear-gradient(135deg,#f9f9f9,#ececec);
                border:2px solid #ddd;border-radius:15px;
                padding:30px;margin:20px 0;
                box-shadow:0 4px 12px rgba(0,0,0,0.08);
                text-align:center;">
    <div style="font-size:20px;font-weight:bold;margin-bottom:12px;color:#333;">
        ⭐ 평점 & 📊 인기 지수
    </div>
    <div style="font-size:18px;color:#222;margin:8px 0;">
        <b style="color:#ff9800;">평균 평점:</b> {vote_avg:.1f}/10
    </div>
    <div style="font-size:16px;color:#555;margin:6px 0;">
        투표 참여자: {vote_count:,}명
    </div>
    <div style="font-size:18px;color:#0066cc;margin-top:10px;">
        <b>인기 지수:</b> {popularity:.1f}
    </div>
    </div>
    """


   
    # 🎬 예고편 영역
    video_html = ""
    video_lead = make_section_lead("예고편", title, year, genres_str, cert)

    # ✅ 안내 문구 후보 (항상 정의)
    video_notice_variants = [
        "아래 예고편 영상이 가끔 오류로 인해 다른 유튜브 영상이 보일 수도 있습니다. 참고 부탁드려요 😊",
        "간혹 아래 예고편 영상이 정상적으로 로드되지 않거나 다른 영상이 나올 수 있습니다. 양해 바랍니다 🙏",
        "예고편 영상이 불안정하게 표시될 수 있으며, 때때로 다른 영상이 뜰 수 있습니다. 참고하세요 ^^",
        "아래 동영상은 자동으로 불러오는 과정에서 오류가 생길 경우 엉뚱한 영상이 재생될 수도 있어요.",
        "예고편 영상이 제대로 안 보이거나 다른 영상으로 연결될 수 있습니다. 참고 부탁드립니다.",
        "예고편 영상이 정상적으로 보이지 않거나 다른 영상이 표시될 수 있으니 양해 부탁드립니다.",
        "아래 영상은 자동으로 가져오므로 가끔은 다른 영상이 나타날 수 있습니다. 미리 알려드려요!",
        "예고편 로딩 중 오류가 생기면 엉뚱한 영상이 나올 수 있으니 감안하고 시청해주세요 ^^",
        "유튜브 영상이 간혹 오류로 인해 다른 영상이 보일 수 있습니다. 이해 부탁드려요.",
        "예고편 영상이 간혹 정상적으로 재생되지 않을 수 있어요. 다른 영상이 뜨면 무시해 주세요.",
        "동영상 로딩 문제로 인해 다른 콘텐츠가 표시될 수 있습니다.",
        "예고편 영상은 자동으로 연결되며, 오류 시 다른 영상이 표시될 수 있습니다.",
        "아래 유튜브 영상이 잘못 연결될 수 있습니다. 감안하시고 봐주세요 ^^",
        "영상 불러오기 오류가 생기면 예고편 대신 다른 영상이 나올 수도 있어요.",
        "예고편 영상이 정상적으로 표시되지 않을 경우 다른 영상이 보일 수 있습니다.",
        "동영상 불러오기 과정에서 오류가 발생하면 다른 영상이 표시될 수 있어요.",
        "예고편 영상이 불안정하게 뜨거나 다른 영상이 재생될 수 있으니 참고 바랍니다.",
        "간혹 예고편 대신 관련 없는 영상이 뜰 수 있습니다. 양해 부탁드립니다.",
        "예고편 영상은 자동으로 불러오기 때문에 다른 영상이 나타날 수 있습니다.",
        "예고편 영상이 오류로 인해 제대로 표시되지 않을 수도 있습니다."
    ]

    # 1) TMDB 공식 예고편 먼저 확인
    videos = get_movie_videos(post["id"], lang=LANG, bearer=BEARER, api_key=API_KEY)
    yt = next((v for v in videos if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")), None)
    if yt:
        yt_key = yt.get("key")
        video_html += f"<p>{video_lead}</p><iframe width='560' height='315' src='https://www.youtube.com/embed/{yt_key}' frameborder='0' allowfullscreen></iframe>"

    # 2) YouTube API 검색으로 보조 영상 가져오기
    query = f"{title} 예고편"
        # 2) YouTube API 검색 (한국어 → 영어 fallback)
    yt_results = get_youtube_trailers(
        post.get("title") or "",
        post.get("original_title") or "",
        max_results=2
    )

    if yt_results:
        # ✅ 안내문 출력 (항상 공식 영상 아래, 유튜브 검색 영상 위)
        video_notice = random.choice(video_notice_variants)
        video_html += f"<br /><p>{video_notice}</p>"

        for vid, vtitle in yt_results:
            video_html += (
                f"<p><b>{vtitle}</b></p>"
                f"<iframe width='560' height='315' src='https://www.youtube.com/embed/{vid}' "
                f"frameborder='0' allowfullscreen></iframe><br>"
            )


    # 리뷰 (없으면 섹션 생략)
    reviews = get_movie_reviews(post["id"], lang=LANG, bearer=BEARER, api_key=API_KEY)
    reviews_html = ""
    if reviews:
        review_blocks = []
        for r in reviews[:5]:
            auth = html.escape(r.get("author",""))
            rating = r.get("author_details",{}).get("rating")
            content = html.escape((r.get("content","") or "").strip())
            if len(content) > 300:
                content = content[:300] + "..."
            review_blocks.append(f"<div style='margin:10px 0;'><b>{auth}</b> ({rating if rating else 'N/A'}점)<br>{content}</div>")
        reviews_html = "<br /><br /><br />\n<h2>"+title+" 베스트 리뷰</h2>" + "".join(review_blocks)

    # 추천 영화
    recs = get_movie_recommendations(post["id"], lang=LANG, bearer=BEARER, api_key=API_KEY)[:6]
    rec_html = ""
    if recs:
        cards = []
        for m in recs:
            mtitle = html.escape(m.get("title", ""))
            year2 = (m.get("release_date") or "")[:4]
            poster2 = img_url(m.get("poster_path"), "w185")
            poster_tag = f"<img src='{poster2}' style='width:100%;border-radius:10px;'>" if poster2 else ""

            # 🔗 블로그스팟 검색 링크 만들기
            # "이치, 더 킬러 (2001)" → URL 인코딩된 쿼리 문자열
            query = urllib.parse.quote(f"{mtitle} ({year2})")
            search_url = f"https://movie.appsos.kr/search?q={query}"

            # 카드 HTML에 링크 적용 (포스터 + 제목 모두 클릭 가능)
            cards.append(
            f"<div style='flex:0 0 30%;margin:1%;text-align:center;'>"
            f"<a href='{search_url}' target='_blank' style='color:#000; !important; text-decoration:none;'>{poster_tag}<br>{mtitle} ({year2})</a>"
            "</div>"
            )


        # ✅ 리드 멘트 추가
        rec_lead = make_section_lead("추천 영화", title, year, genres_str, cert)

        rec_html = (
            "<br /><br /><br />\n<h2>비슷한 장르의 추천 영화</h2>"
            f"<p>{rec_lead}</p>"
            "<div style='display:flex;flex-wrap:wrap;'>"
            + "".join(cards) +
            "</div>"
        )



    outro_6 = make_outro_6(title, year, genres_str, director_names, keywords)

     # ✅ 블로그 추천글 4개 추가
    related_box = get_related_posts(BLOG_ID, count=4)

    # 최종 HTML (전체 래퍼로 감싸기)

    blog_title1 = f"영화 {title} ({year}) 줄거리 출연진 주인공 예고편"
    hashtags = make_hashtags_from_title(blog_title1)


    html_out = f"""

<p>{intro_6}</p>
<!--more-->   <!-- ✅ 점프 브레이크 추가 --><br />
{"<p><img src='"+backdrop+"' style='width:100%;height:auto;border-radius:12px;'></p>" if backdrop else ""}
{"<p><i>"+html.escape(tagline)+"</i></p>" if tagline else ""}


<br /><br /><br />
<h2>영화 {title} 줄거리</h2>
<p><b>제작 국가:</b> {country_str} | <b>장르:</b> {genres_str if genres_str else "장르 정보 없음"}</p>
<p>{make_section_lead("줄거리", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://movie.appsos.kr/search/label/{year}?&max-results=10">
    <span style="font-size: medium;"><strong>{year}년 추천영화 보러가기</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if year else ''}

<div style="background:#fafafa;border:2px solid #ddd;border-radius:12px;
            padding:10px 18px 25px;margin:18px 0;line-height:1.7;color:#333;
            box-shadow:0 3px 8px rgba(0,0,0,0.05);">
  <p style="font-weight:bold;font-size:16px;margin-bottom:10px;">🎬 {title} 줄거리</p>
  {overview}
</div>
<br />
{hashtags}

<br /><br /><br />
<h2>영화 {title} 출연진</h2>
<p>{make_section_lead("출연진", title, year, genres_str, cert, extras={"cast_top": cast_names})}</p>

{cast_table}
<br />
{hashtags}
<br /><br /><br />
<h2>{title} 스틸컷</h2>
<p>{make_section_lead("스틸컷", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://movie.appsos.kr/search/label/{urllib.parse.quote(genres_list[0])}?&max-results=10">
    <span style="font-size: medium;"><strong>추천 {genres_list[0]} 영화 보러가기</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if genres_list else ''}

{stills_html}
<br />
{hashtags}
<br /><br /><br />

<h2>영화 {title} 평점 및 예고편</h2>
<p>{rating_lead}</p>
{rating_html}
{video_html}

{reviews_html}

{rec_html}
<br />
{hashtags}
<br /><br />
<p>{outro_6}</p>

{related_box}   <!-- ✅ 여기서 추천글 박스 삽입 -->

<p style="font-size:12px;color:#666;">
본 콘텐츠는 <a href="https://www.themoviedb.org/" target="_blank" style="color:#666;text-decoration:underline;">TMDB</a> 데이터를 기반으로 작성되었습니다.
</p>


"""
    return textwrap.dedent(html_out).strip()

# ===============================
# Blogger 인증/발행
from google.oauth2.credentials import Credentials

BLOGGER_TOKEN_JSON = "blogger_token.json"  # refresh_token 포함 JSON 파일
SCOPES = ["https://www.googleapis.com/auth/blogger"]

def get_blogger_service():
    try:
        if not os.path.exists(BLOGGER_TOKEN_JSON):
            raise FileNotFoundError("❌ blogger_token.json 파일이 없습니다. 먼저 발급 받아주세요.")

        creds = Credentials.from_authorized_user_file(BLOGGER_TOKEN_JSON, SCOPES)

        # 액세스 토큰이 만료되었으면 자동으로 새로고침
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # 갱신된 토큰을 다시 저장
            with open(BLOGGER_TOKEN_JSON, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        return build("blogger", "v3", credentials=creds)

    except Exception as e:
        print(f"❌ Blogger 인증 실패: {e}", file=sys.stderr)
        raise


def post_to_blogger(service, blog_id, title, html_content, labels=None, is_draft=False):
    body = {
        "title": title,
        "content": html_content
    }
    if labels:
        body["labels"] = labels
    posts = service.posts()
    res = posts.insert(blogId=blog_id, body=body, isDraft=is_draft, fetchImages=True).execute()
    return res

# ===============================


# ===============================
## 메인 실행부
def main():
    import io, sys

    # 로그 버퍼 설정
    log_buffer = io.StringIO()
    class Logger:
        def write(self, msg):
            log_buffer.write(msg)
            sys.__stdout__.write(msg)  # 콘솔에도 그대로 출력
        def flush(self):
            sys.__stdout__.flush()

    sys.stdout = Logger()
    sys.stderr = Logger()

    ws = get_sheet()
    service = get_blogger_service()

    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        movie_id = row[1].strip() if len(row) > 1 else ""
        done_flag = row[5].strip() if len(row) > 5 else ""

        if movie_id and done_flag != "완":
            print(f"👉 대상 행: {i} (MOVIE_ID={movie_id})")

            try:
                # 1) TMDB에서 상세 번들 수집
                post = get_movie_bundle(movie_id, lang=LANG, bearer=BEARER, api_key=API_KEY)

                # 2) HTML 구성
                html_out = build_html(post, cast_count=CAST_COUNT, stills_count=STILLS_COUNT)

                # 3) 포스트 제목
                title = (post.get("title") or post.get("original_title") or f"movie_{movie_id}")
                year = (post.get("release_date") or "")[:4]
                blog_title = f"영화 {title} ({year}) 줄거리 출연진 주인공 예고편"

                # 4) Blogger 발행
                genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
                labels = ["영화"] + ([year] if year else []) + genres_list
                res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=labels, is_draft=False)
                print(f"✅ 발행 완료: {res.get('url','(URL 미확인)')}")

                # 5) Google Sheets 업데이트 (완)
                ws.update_cell(i, 6, "완")
                print(f"✅ Google Sheets 업데이트 완료 (행 {i})")

            except Exception as e:
                print(f"❌ 실행 중 오류 발생: {e}")

            finally:
                # 6) 로그 기록 (P열 = 16열, append)
                try:
                    prev = ws.cell(i, 16).value or ""
                    new_val = (prev + "\n" if prev else "") + log_buffer.getvalue().strip()
                    ws.update_cell(i, 16, new_val)
                    print(f"📌 실행 로그 기록 완료 (행 {i}, P열)")
                except Exception as log_e:
                    sys.__stdout__.write(f"❌ 로그 기록 실패: {log_e}\n")

            break  # ✅ 한 건만 처리 후 종료

# ===============================
# 메인 호출부
# ===============================
if __name__ == "__main__":
    for n in range(POST_COUNT):
        print(f"\n🚀 {n+1}/{POST_COUNT} 번째 포스팅 시작")
        main()

        if n < POST_COUNT - 1 and POST_DELAY_MIN > 0:
            print(f"⏳ {POST_DELAY_MIN}분 대기 후 다음 포스팅...")
            time.sleep(POST_DELAY_MIN * 60)






