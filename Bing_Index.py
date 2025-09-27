import advertools as adv
import requests
import json
import time
import os

# ==========================================
# ✅ 사용자 설정 영역
# ==========================================
# 여러 사이트 입력 (sitemap.xml 자동 인식)
SITE_URLS = [
    "https://bokji.appsos.kr/",
    "https://info.alltopx.com/",
    "https://movie.appsos.kr/",
    "https://jpapp.appsos.kr/",
    "https://apk.appsos.kr/",
    "https://appbr.appsos.kr/",
    "https://appid.appsos.kr/",
    "https://apptk.appsos.kr/",
    "https://japan.appsos.kr/",
    "https://cinebr.appsos.kr/",
    "https://cineindo.appsos.kr/",
    "https://cinetrk.appsos.kr/",
    # 필요하면 추가
]

# 각 사이트별 제출할 개수
POST_COUNT_PER_SITE = 5

# OFFSET: 0=최신부터, 5=6번째부터, 10=11번째부터 …
OFFSET = 0

# 요청 간격(초)
REQUEST_DELAY = 0.2
# ==========================================

# ✅ GitHub Actions에서 디코딩된 JSON 파일에서 Bing 키 불러오기
with open("bing_key.json", "r") as f:
    bing_key_data = json.load(f)
BING_API_KEY = bing_key_data["bing_api_key"]

# ✅ Bing Submit URL
BING_ENDPOINT = f"https://ssl.bing.com/webmaster/api.svc/json/SubmitUrl?apikey={BING_API_KEY}"

# ✅ 함수 정의
def submit_url(data):
    headers = {
        "User-Agent": "curl/7.12.1",
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(url=BING_ENDPOINT, json=data, headers=headers)
        return r.status_code, r.text
    except Exception as e:
        return 500, str(e)


# ✅ 통계 변수
total_urls = 0
success_count = 0
fail_count = 0
fail_list = []

# ==========================================
# 각 사이트맵 실행
# ==========================================
for site in SITE_URLS:
    sitemap_url = f"{site}sitemap.xml"
    print(f"\n📌 {site} 사이트맵 요청 중: {sitemap_url}")

    try:
        sitemap_urls = adv.sitemap_to_df(sitemap_url)
        url_list = sitemap_urls["loc"].to_list()
    except Exception as e:
        print(f"⚠️ 사이트맵 오류: {sitemap_url} → {e}")
        continue

    # 최근 OFFSET부터 POST_COUNT_PER_SITE 만큼 선택
    selected_urls = url_list[OFFSET : OFFSET + POST_COUNT_PER_SITE]

    print(f"총 {len(url_list)}개 URL 중 {len(selected_urls)}개 제출 (OFFSET={OFFSET})")

    for url in selected_urls:
        total_urls += 1
        data = {
            "siteUrl": site,
            "url": url
        }
        status, result = submit_url(data)

        if status == 200:
            success_count += 1
            print(f"✅ 성공: {url}")
        else:
            fail_count += 1
            fail_list.append(url)
            print(f"❌ 실패: {url} → {result}")

        time.sleep(REQUEST_DELAY)

# ==========================================
# ✅ 최종 결과
# ==========================================
print("\n================ 최종 실행 결과 ================")
print(f"총 요청 URL: {total_urls}개")
print(f"성공: {success_count}개")
print(f"실패: {fail_count}개")

if fail_list:
    print("\n❌ 실패한 URL 목록:")
    for u in fail_list:
        print("-", u)
print("================================================")


