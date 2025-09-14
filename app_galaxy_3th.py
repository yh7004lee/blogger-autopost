import os
import re
import time
import json
import random
import pickle
import traceback
import urllib.parse
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
import gspread
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
# ================================
# OpenAI 인증
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
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
    return gc.open_by_key(SHEET_ID).sheet1

ws = get_sheet()

# ================================
# 시트에서 키워드 조합
# ================================
def get_next_keyword_row():
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        if len(row) < 4:
            continue
        prefix, keyword, suffix, status = row[:4]
        if keyword and (not status or status.strip() != "완"):
            title = f"{prefix.strip()} {keyword.strip()} {suffix.strip()}".strip()
            return i, prefix.strip(), keyword.strip(), suffix.strip(), title
    return None, None, None, None, None

row_idx, prefix, keyword, suffix, title = get_next_keyword_row()
if not keyword:
    print("✅ 모든 행이 완료됨. 종료.")
    exit()

print(f"👉 이번 실행: {title}")

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
            raise RuntimeError("⚠️ drive_token_2nd.pickle이 없거나 만료됨")
        with open("drive_token_2nd.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("drive", "v3", credentials=creds)

# ================================
# Google Drive 업로드
# ================================
def upload_to_drive(file_path, file_name, folder_name="blogger"):
    drive_service = get_drive_service()
    # blogger 폴더 확인
    query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    items = results.get("files", [])
    if items:
        folder_id = items[0]["id"]
    else:
        folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
        folder_id = folder["id"]

    media = MediaFileUpload(file_path, mimetype="image/webp", resumable=True)
    file = drive_service.files().create(body={"name": file_name, "parents": [folder_id]},
                                        media_body=media, fields="id").execute()
    drive_service.permissions().create(fileId=file["id"],
                                       body={"role": "reader", "type": "anyone"}).execute()
    return f"https://lh3.googleusercontent.com/d/{file['id']}"

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)

# ================================
# GPT로 앱 소개문 새로쓰기
# ================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = BeautifulSoup(original_html, "html.parser").get_text(separator=" ", strip=True)
    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "사실은 유지하되 문장과 구성을 완전히 새로 쓰고, "
        "사람이 직접 적은 듯 자연스럽고 따뜻한 톤으로 풀어줘. "
        "마크다운 금지, <p data-ke-size=\"size18\"> 문단만 사용. "
        "출력은 반드시 3~4개의 문단으로 나눠서 작성하되, "
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
        max_tokens=500,
    )
    rewritten = resp.choices[0].message.content.strip()
    if "<p" not in rewritten:
        rewritten = f'<p data-ke-size="size18">{rewritten}</p>'
    return rewritten

# ================================
# Selenium 실행 (Firefox headless)
# ================================
options = webdriver.FirefoxOptions()
options.add_argument("--headless")
service = Service(executable_path=GeckoDriverManager().install())
chrome = webdriver.Firefox(service=service, options=options)
chrome.implicitly_wait(10)

# ================================
# 앱 크롤링 시작
# ================================
url = f"https://play.google.com/store/search?q={keyword}&c=apps"
chrome.get(url)
time.sleep(2)
soup = BeautifulSoup(chrome.page_source, "html.parser")
source = soup.find_all(class_="ULeU3b")

app_links = []
for s in source[:15]:
    link = "https://play.google.com" + s.find("a")["href"]
    app_links.append(link)

del app_links[:3]  # 광고 제거
print(f"👉 수집된 앱 링크: {len(app_links)}개")

# ================================
# 서론
# ================================
intro = f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">
스마트폰만 있으면 생활이 훨씬 편리해집니다. 이번 글에서는 "{title}" 관련 앱들을 집중적으로 소개합니다. 
구글플레이스토어에서 "{keyword}" 검색 시 상위 노출되는 앱들을 기준으로 선정했으며, 
각 앱의 특징과 실제 사용 후기를 함께 담아 끝까지 읽으시면 앱 선택에 큰 도움이 될 것입니다.
</p>
<span><!--more--></span>
"""

html = intro

# ================================
# 앱별 HTML 생성
# ================================
for j, app_url in enumerate(app_links, 1):
    if j > 5:
        break
    chrome.get(app_url)
    time.sleep(1)
    soup = BeautifulSoup(chrome.page_source, "html.parser")
    try:
        h1 = soup.find("h1").text.strip()
    except:
        h1 = f"앱{j}"
    try:
        desc_div = soup.find("div", class_="bARER") or soup.find("div", class_="fysCi")
        raw_html = str(desc_div) if desc_div else ""
        contents_text = rewrite_app_description(raw_html, h1, keyword)
    except Exception:
        contents_text = ""

    html += f"""
    <h2 data-ke-size="size26">{j}. {h1} 어플 소개</h2>
    {contents_text}
    <p style="text-align:center;" data-ke-size="size18"><a class="myButton" href="{app_url}">👉 {h1} 다운로드</a></p>
    """

# ================================
# 마무리
# ================================
html += f"""
<div style="margin:40px 0 20px 0;">
<p data-ke-size="size18">
오늘 소개한 "{title}" 관련 앱들이 일상 속에서 유용하게 활용되길 바랍니다. 
앞으로도 다양한 앱 정보를 꾸준히 전해드리겠습니다. 
댓글과 의견도 남겨주시면 큰 힘이 됩니다.
</p>
</div>
"""

# ================================
# Blogger 업로드
# ================================
BLOG_IDS = ["1271002762142343021", "4265887538424434999", "6159101125292617147"]
index_file = "last_blog_index.pkl"
last_index = -1
if os.path.exists(index_file):
    with open(index_file, "rb") as f:
        last_index = pickle.load(f)
next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]

with open(index_file, "wb") as f:
    pickle.dump(next_index, f)

blogger = get_blogger_service()
post_body = {"content": html, "title": title, "labels": ["앱", "어플", "추천"], "blog": {"id": BLOG_ID}}
res = blogger.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False, fetchImages=True).execute()

print("✅ 업로드 성공:", res["url"])

# ✅ 엑셀/시트에 결과 기록
ws.update_cell(row_idx, 4, "완")       # D열 "완"
ws.update_cell(row_idx, 5, post_url)   # E열에 포스팅 URL 기록

chrome.quit()



