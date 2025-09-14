import re
import json
import requests
import time
import random
from bs4 import BeautifulSoup
import os
import pickle
import urllib.parse
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload
from openai import OpenAI
import sys
sys.stdout.reconfigure(encoding="utf-8")
# ================================
# OpenAI API Key 로드
# ================================
OPENAI_API_KEY = ""
if os.path.exists("openai.json"):
    with open("openai.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        OPENAI_API_KEY = data.get("api_key", "").strip()
if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ================================
# Google Sheets 인증
# ================================
SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")

def get_sheet():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).sheet1

ws = get_sheet()

# ================================
# Google Drive 인증
# ================================
def get_drive_service():
    creds = None
    if os.path.exists("drive_token_2nd.pickle"):
        with open("drive_token_2nd.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("drive_token_2nd.pickle이 없거나 만료됨. Secrets에서 복원 필요.")
        with open("drive_token_2nd.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("drive", "v3", credentials=creds)

# ================================
# Blogger 인증
# ================================
from google.oauth2.credentials import Credentials as UserCredentials

def get_blogger_service():
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)

# ================================
# 구글시트에서 키워드 가져오기
# ================================
rows = ws.get_all_values()
keyword, title, row_idx = None, None, None

for i, row in enumerate(rows[1:], start=2):  # 2행부터 시작
    if len(row) >= 4:
        if not row[3] or row[3].strip() != "완":  # D열 확인
            prefix = row[0].strip() if len(row) > 0 else ""
            main_kw = row[1].strip() if len(row) > 1 else ""
            suffix = row[2].strip() if len(row) > 2 else ""
            keyword = main_kw
            title = f"{prefix} {main_kw} {suffix}".strip()
            row_idx = i
            break

if not keyword:
    print("처리할 키워드가 없습니다.")
    exit()

print(f"이번 실행: {title}")

# ================================
# Google Play 크롤링 (requests + BeautifulSoup)
# ================================
url = f"https://play.google.com/store/search?q={keyword}&c=apps&hl=ko&gl=kr"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(resp.text, "html.parser")

app_links = []
for a in soup.select("a[href^='/store/apps/details']"):
    link = "https://play.google.com" + a["href"]
    if link not in app_links:
        app_links.append(link)
    if len(app_links) >= 12:
        break

# 상위 광고성 3개 제거
del app_links[:3]
print(f"수집된 앱 링크: {len(app_links)}개")

# ================================
# GPT로 앱 설명 새로 쓰기
# ================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = BeautifulSoup(original_html, 'html.parser').get_text(separator=' ', strip=True)
    if not client:
        return f"<p data-ke-size='size18'>{compact}</p>"

    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "사실은 유지하되 문장과 구성을 완전히 새로 쓰고, "
        "사람이 직접 적은 듯 자연스럽고 따뜻한 톤으로 풀어줘. "
        "마크다운 금지, <p data-ke-size=\"size18\"> 문단만 사용. "
        "출력은 반드시 3~4개의 문단으로 나눠서 작성하되, "
        "각 문단 사이에는 <p data-ke-size=\"size18\"> 태그를 사용하고 "
        "빈 줄(줄바꿈)으로 구분해. "
        "각 문단은 3~4문장 이내로만 작성해."
    )
    user_msg = f"[앱명] {app_name}\n[키워드] {keyword_str}\n아래 원문을 참고해서 블로그용 소개문을 새로 작성해줘.\n\n{compact}"

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.7,
        max_tokens=600,
    )
    rewritten = resp.choices[0].message.content.strip()
    if "<p" not in rewritten:
        rewritten = f'<p data-ke-size="size18">{rewritten}</p>'
    return rewritten

# ================================
# 앱별 상세 페이지 크롤링
# ================================
html_content = ""
for j, app_url in enumerate(app_links, 1):
    if j > 7:
        break

    resp = requests.get(app_url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(resp.text, "html.parser")

    # 앱 이름
    h1 = soup.find("h1")
    app_name = h1.text.strip() if h1 else f"앱 {j}"
    h2_title = re.sub(r"[^\uAC00-\uD7A30-9a-zA-Z\s]", "", app_name).replace(" ", "")

    # 설명
    desc_div = soup.select_one("div[itemprop='description']")
    raw_html = str(desc_div) if desc_div else ""
    rewritten_desc = rewrite_app_description(raw_html, app_name, keyword)

    con_f = f"""
    <br />
    <h2 data-ke-size="size26">{j}. {app_name} 어플 소개</h2>
    <p data-ke-size="size18"><b>1) {app_name} 어플 소개</b></p>
    <p data-ke-size="size18">이 어플은 구글플레이스토어에서 "{keyword}"로 검색했을 때 {j}번째로 나오는 앱입니다.</p>
    """

    con_l = f"""
    <p style="text-align: center;" data-ke-size="size18"><a class="myButton" href="{app_url}">{h2_title} 앱 다운</a></p>
    <p data-ke-size="size18">{keyword} 관련 앱 설명은 위와 같습니다.</p>
    <br />
    """

    html_content += con_f + rewritten_desc + con_l

# ================================
# Blogger 업로드
# ================================
blog_handler = get_blogger_service()

BLOG_IDS = [
    "1271002762142343021",
    "4265887538424434999",
    "6159101125292617147"
]

INDEX_FILE = "last_blog_index.pkl"
if os.path.exists(INDEX_FILE):
    with open(INDEX_FILE, "rb") as f:
        last_index = pickle.load(f)
else:
    last_index = -1

next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]

with open(INDEX_FILE, "wb") as f:
    pickle.dump(next_index, f)

intro = f"<p data-ke-size='size18'>{title} 관련 앱들을 소개합니다.</p>"
last = f"<p data-ke-size='size18'>마무리: {title} 관련 앱들이 도움이 되길 바랍니다.</p>"

full_html = intro + html_content + last

data = {
    "content": full_html,
    "title": title,
    "labels": ["어플", "앱", "추천"],
    "blog": {"id": BLOG_ID},
}

res = blog_handler.posts().insert(blogId=BLOG_ID, body=data, isDraft=False, fetchImages=True).execute()
post_url = res.get("url", "")

print(f"업로드 성공: {post_url}")

# ================================
# 시트 업데이트
# ================================
if row_idx:
    ws.update_cell(row_idx, 4, "완")      # D열
    ws.update_cell(row_idx, 7, post_url)  # G열

