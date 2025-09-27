import os
import json
import time
import advertools as adv
import httplib2
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# ✅ 사용자 설정 영역
# ==========================================
# 각 사이트맵에서 몇 개씩 색인 요청할지
POST_COUNT_PER_SITEMAP = 5

# 어디서부터 시작할지 (0=최신부터, 5=6번째부터, 10=11번째부터 …)
OFFSET = 5

# 요청 간격(초)
REQUEST_DELAY = 0.2
# ==========================================

# ✅ 여러 개 블로그 sitemap 리스트
sitemaps = [
    "https://movie.appsos.kr/sitemap.xml",
    "https://jpapp.appsos.kr/sitemap.xml",
    "https://apk.appsos.kr/sitemap.xml",
    "https://appbr.appsos.kr/sitemap.xml",
    "https://appid.appsos.kr/sitemap.xml",
    "https://apptk.appsos.kr/sitemap.xml",
    "https://japan.appsos.kr/sitemap.xml",
    "https://cinebr.appsos.kr/sitemap.xml",
    "https://cineindo.appsos.kr/sitemap.xml",
    "https://cinetrk.appsos.kr/sitemap.xml",
]

# ✅ GitHub Actions에서 base64 → 디코딩된 JSON 키 파일
JSON_KEY_FILE = "service_account.json"

SCOPES = ["https://www.googleapis.com/auth/indexing"]
ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"

# ✅ 구글 인증
credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scopes=SCOPES)
http = credentials.authorize(httplib2.Http())

# ✅ 통계 변수
total_urls = 0
success_count = 0
fail_count = 0
fail_list = []

for sitemap in sitemaps:
    try:
        sitemap_urls = adv.sitemap_to_df(sitemap)
        url_lists = sitemap_urls["loc"].to_list()
        selected_urls = url_lists[OFFSET : OFFSET + POST_COUNT_PER_SITEMAP]  # ✅ 구간 설정 반영

        print(f"\n📌 {sitemap} → {len(selected_urls)}개 색인 요청 시작 (OFFSET={OFFSET})")

        for url in selected_urls:
            total_urls += 1
            content = {"url": url, "type": "URL_UPDATED"}
            json_content = json.dumps(content)

            try:
                response, content = http.request(ENDPOINT, method="POST", body=json_content)
                result = json.loads(content.decode())

                if response.status == 200:
                    success_count += 1
                    print(f"✅ 성공: {url}")
                else:
                    fail_count += 1
                    fail_list.append(url)
                    print(f"❌ 실패: {url} → {result}")

            except Exception as e:
                fail_count += 1
                fail_list.append(url)
                print(f"⚠️ 오류 발생: {url} → {e}")

            time.sleep(REQUEST_DELAY)

    except Exception as e:
        print(f"⚠️ 사이트맵 오류: {sitemap} → {e}")

# ✅ 최종 결과 요약
print("\n================ 최종 실행 결과 ================")
print(f"총 요청 URL: {total_urls}개")
print(f"성공: {success_count}개")
print(f"실패: {fail_count}개")

if fail_list:
    print("\n❌ 실패한 URL 목록:")
    for u in fail_list:
        print("-", u)
print("================================================")
