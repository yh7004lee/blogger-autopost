import re
import json
import requests
import random
import os
import urllib.parse
import sys, traceback
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials
from googleapiclient.http import MediaFileUpload
import gspread
from openai import OpenAI

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
    print("[ERROR] OpenAI API 키가 없습니다.")
    sys.exit(1)

client = OpenAI(api_key=OPENAI_API_KEY)

# ================================
# Google Sheets 인증
# ================================
SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

try:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
except Exception as e:
    print(f"[ERROR] Google Sheets 인증 실패: {e}")
    sys.exit(1)

SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
ws = gc.open_by_key(SHEET_ID).sheet1

# ================================
# 블로그 ID 로테이션 (O1 셀)
# ================================
BLOG_IDS = ["1271002762142343021", "4265887538424434999", "6159101125292617147"]

try:
    last_index = int(ws.acell("O1").value or "-1")
except:
    last_index = -1

next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]
ws.update_acell("O1", str(next_index))
print(f"👉 이번 포스팅 블로그 ID: {BLOG_ID}")

# ================================
# URL 가져오기 (E열 URL, G열 상태)
# ================================
target_row, my_url = None, None
rows = ws.get_all_values()

for i, row in enumerate(rows[1:], start=2):  # 2행부터
    url_cell = row[4] if len(row) > 4 else ""   # E열 (URL)
    status_cell = row[6] if len(row) > 6 else "" # G열 ("완")
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
# 복지 서비스 데이터 가져오기
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
# GPT 변환
# ================================
def process_with_gpt(section_title: str, raw_text: str, keyword: str) -> str:
    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "주제는 정부 복지서비스이고, 주어진 원문을 "
        "1) 먼저 <b>태그로 굵게 요약(한두 문장)</b>, "
        "2) 그 아래에 친절하고 자세한 설명을 붙이는 형태로 가공해. "
        "출력은 반드시 <p data-ke-size=\"size18\"> 태그를 사용해 문단을 나누고 "
        "마크다운은 절대 쓰지 마."
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
        return f"<p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# Blogger 인증 (refresh_token JSON 방식)
# ================================
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise FileNotFoundError("[ERROR] blogger_token.json 파일이 없습니다.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data,
        ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# ================================
# 본문 생성 및 업로드
# ================================
data = fetch_welfare_info(wlfareInfoId)
keyword = clean_html(data.get("wlfareInfoNm", "복지 서비스"))
title = f"2025 {keyword} 지원 대상 신청방법 총정리"
print("자동 생성된 제목:", title)

fields = {
    "개요": "wlfareInfoOutlCn",
    "지원대상": "wlfareSprtTrgtCn",
    "서비스내용": "wlfareSprtBnftCn",
    "신청방법": "aplyMtdDc",
    "추가정보": "etct"
}

html = f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{keyword}은 많은 분들이 관심을 갖는 중요한 제도입니다.</p><br />
<span><!--more--></span><br />
"""

for title_k, key in fields.items():
    value = data.get(key, "")
    if not value or value.strip() in ["", "정보 없음"]:
        continue
    processed_text = process_with_gpt(title_k, clean_html(value), keyword)
    html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed_text}<br /><br />"

html += f"""
<div class="custom-button">
  <a href="{my_url}" target="_blank">👉 {keyword} 자세히 알아보기</a>
</div>
"""

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
# ✅ 구글시트 업데이트 (G열 "완")
# ================================


# ✅ 구글시트 업데이트 (G열 "완")
ws.update_cell(target_row, 7, "완")  # G열
ws.update_cell(target_row, 15, res['url']) # O열 (15번째 열)
print("✅ 구글시트 업데이트 완료 (G열 '완' + O열 포스팅 URL 기록)")


print(title)


