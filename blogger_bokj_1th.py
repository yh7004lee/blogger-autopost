import re
import json
import requests
import random
from bs4 import BeautifulSoup
import os
import urllib.parse
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from openai import OpenAI
import sys, traceback

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# OpenAI API 키 로드
# ================================
OPENAI_API_KEY = ""
if os.path.exists("openai.json"):
    try:
        with open("openai.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            OPENAI_API_KEY = data.get("api_key", "").strip()
    except Exception as e:
        print("[ERROR] openai.json 파싱 실패:", e)

if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

if not OPENAI_API_KEY:
    print("[ERROR] OpenAI API 키가 없습니다. openai.json 또는 환경변수 확인하세요.")
    sys.exit(1)

client = OpenAI(api_key=OPENAI_API_KEY)

# ================================
# 1. 구글 시트에서 URL 추출
# ================================
SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

if not os.path.exists(SERVICE_ACCOUNT_FILE):
    print(f"[ERROR] {SERVICE_ACCOUNT_FILE} 파일이 없습니다. GitHub Secrets 복원을 확인하세요.")
    sys.exit(1)

try:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
except Exception as e:
    print(f"[ERROR] Google Sheets 인증 실패: {e}")
    sys.exit(1)

SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
ws = gc.open_by_key(SHEET_ID).sheet1

target_row = None
my_url = None

rows = ws.get_all_values()
for i, row in enumerate(rows[1:], start=2):  # 2행부터
    url_cell = row[4] if len(row) > 4 else ""   # ✅ E열 (5번째)
    status_cell = row[7] if len(row) > 7 else "" # ✅ H열 (8번째)
    if url_cell and (not status_cell or status_cell.strip() != "완"):
        my_url, target_row = url_cell, i
        break

if not my_url:
    print("🔔 처리할 새로운 URL이 없습니다. (모든 행 완료됨)")
    sys.exit(0)

print("👉 이번에 처리할 URL:", my_url)

parsed = urllib.parse.urlparse(my_url)
params = urllib.parse.parse_qs(parsed.query)
wlfareInfoId = params.get("wlfareInfoId", [""])[0]
print("wlfareInfoId =", wlfareInfoId)

# ================================
# 2. 복지 서비스 데이터 가져오기
# ================================
def fetch_welfare_info(wlfareInfoId):
    url = f"https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={wlfareInfoId}&wlfareInfoReldBztpCd=01"
    resp = requests.get(url)
    resp.encoding = "utf-8"
    html = resp.text
    outer_match = re.search(r'initParameter\((\{.*?\})\);', html, re.S)
    if not outer_match:
        raise ValueError("initParameter JSON을 찾지 못했습니다.")
    outer_data = json.loads(outer_match.group(1))
    inner_str = outer_data["initValue"]["dmWlfareInfo"]
    return json.loads(inner_str)

def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)

# ================================
# 3. ChatGPT API로 본문 가공
# ================================
def process_with_gpt(section_title: str, raw_text: str, keyword: str, icon: str = "📌") -> str:
    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "출력 형식은 반드시 아래 구조로 만들어야 한다:\n\n"
        "<section class=\"custom-section\">\n"
        "<h2 class=\"section-title\">[아이콘] [섹션 제목]</h2>\n"
        "[본문 내용]\n"
        "<p><br /></p><p><br /></p>\n"
        "</section>\n\n"
        "본문 작성 규칙:\n"
        "1) 첫 <p>에는 <b>태그로 한두 문장 요약</b>\n"
        "2) 이어지는 <p>들은 친절하고 상세한 설명\n"
        "3) 필요 시 <strong>강조</strong>, 줄바꿈 <br /> 사용\n"
        "4) 마크다운 절대 금지, HTML만 사용\n"
        "5) 링크는 <a href=\"URL\" target=\"_blank\">텍스트</a> 형식"
    )
    user_msg = f"[섹션 제목] {keyword} {section_title}\n[원문]\n{raw_text}"

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=900,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("[WARN] GPT 처리 실패:", e)
        return f"<section class='custom-section'><h2>{keyword} {section_title}</h2><p>{clean_html(raw_text)}</p></section>"

# ================================
# 4. Blogger 인증 (refresh_token JSON 방식)
# ================================
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise FileNotFoundError("[ERROR] blogger_token.json 파일이 없습니다. 로컬에서 발급 후 업로드하세요.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data,
        ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

# ================================
# 5. 본문 생성 및 업로드
# ================================
data = fetch_welfare_info(wlfareInfoId)
keyword = clean_html(data.get("wlfareInfoNm", "복지 서비스"))

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|.]', '_', name)

safe_keyword = sanitize_filename(keyword)
title = f"2025 {keyword} 지원 대상 신청방법 총정리"
mytag = " ".join([f"#{word}" for word in title.split()])
print("자동 생성된 태그:", mytag)

fields = {
    "개요": "wlfareInfoOutlCn",
    "지원대상": "wlfareSprtTrgtCn",
    "서비스내용": "wlfareSprtBnftCn",
    "신청방법": "aplyMtdDc",
    "추가정보": "etct"
}

blog_handler = get_blogger_service()

html = "<div class=\"my-unique-wrapper\">\n"

icons = {
    "개요": "🚗",
    "지원대상": "👥",
    "서비스내용": "📋",
    "신청방법": "📝",
    "추가정보": "ℹ️"
}

for title_k, key in fields.items():
    value = data.get(key, "")
    if not value or value.strip() in ["", "정보 없음"]:
        continue
    text = clean_html(value)
    icon = icons.get(title_k, "📌")
    processed_text = process_with_gpt(title_k, text, keyword, icon)
    html += processed_text

html += f"""
<div class="custom-button">
  <a href="{my_url}" target="_blank">👉 {keyword} 자세히 알아보기</a>
</div>
</div>
"""

BLOG_ID = os.getenv("BLOG_ID", "4737456424227083027")

data_post = {
    'content': html,
    'title': title,
    'labels': ["복지", "정부지원", "복지서비스"],
    'blog': {'id': BLOG_ID},
}
try:
    posts = blog_handler.posts()
    res = posts.insert(blogId=BLOG_ID, body=data_post, isDraft=False, fetchImages=True).execute()
    print(f"[완료] 블로그 포스팅: {res['url']}")
except Exception as e:
    print("[ERROR] 블로그 업로드 실패:", e)
    traceback.print_exc()
    sys.exit(1)

# ================================
# 6. ✅ 구글시트 업데이트 (H열 "완")
# ================================
ws.update_cell(target_row, 8, "완")  # ✅ H열은 8번째
print("✅ 구글시트 업데이트 완료 (H열 '완' 기록)")
print(title)

