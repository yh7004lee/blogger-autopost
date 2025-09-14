import requests
from bs4 import BeautifulSoup
import re
import time
import random
import os
import pickle
import json
import urllib.parse
import pyperclip

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
import gspread
from openai import OpenAI

# ================================
# OpenAI 클라이언트
# ================================
with open("openai.json", "r", encoding="utf-8") as f:
    api_data = json.load(f)
OPENAI_API_KEY = api_data["api_key"]
client = OpenAI(api_key=OPENAI_API_KEY)

# ================================
# Google Sheets 연결
# ================================
SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc"  # 실제 ID

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
            raise RuntimeError("drive_token_2nd.pickle이 없거나 만료됨. GitHub Secrets에서 복원 필요.")
        with open("drive_token_2nd.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("drive", "v3", credentials=creds)

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)

# ================================
# GPT로 앱 설명 리라이트
# ================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = BeautifulSoup(original_html, 'html.parser').get_text(separator=' ', strip=True)
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
    user_msg = (
        f"[앱명] {app_name}\n"
        f"[키워드] {keyword_str}\n"
        "아래 원문을 참고해서 블로그용 소개문을 새로 작성해줘.\n\n"
        f"{compact}"
    )

    resp = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.7,
        max_tokens=450,
    )
    rewritten = resp.choices[0].message.content.strip()
    if "<p" not in rewritten:
        rewritten = f'<p data-ke-size="size18">{rewritten}</p>'
    return rewritten

# ================================
# 앱 링크 수집 (requests)
# ================================
def get_app_links(keyword, max_apps=15):
    url = f"https://play.google.com/store/search?q={urllib.parse.quote(keyword)}&c=apps"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    source = soup.find_all("a", class_="Si6A0c Gy4nib")

    app_links = []
    for k, s in enumerate(source):
        if k == max_apps:
            break
        link = "https://play.google.com" + s["href"]
        app_links.append(link)

    # 상위 3개 제거 (광고 등)
    del app_links[:3]
    return app_links

# ================================
# 앱 상세 정보 크롤링
# ================================
def get_app_detail(app_url, keyword):
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(app_url, headers=headers)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    try:
        h1 = soup.find("h1").text.strip()
        h2_title = re.sub(r"[^\uAC00-\uD7A30-9a-zA-Z\s]", "", h1).replace(" ", "")
    except:
        h1 = "앱"
        h2_title = h1

    try:
        desc_div = soup.find("div", {"jsname": "sngebd"})
        if desc_div:
            raw_html = str(desc_div)
            contents_text = rewrite_app_description(raw_html, h1, keyword)
        else:
            contents_text = ""
    except Exception as e:
        contents_text = ""
        print(f"[어플소개 오류] {e}")

    images = ""
    try:
        img_tags = soup.select("img.T75of.sHb2Xb")
        for cc, img in enumerate(img_tags[:4], 1):
            img_url = img.get("src")
            if not img_url:
                continue
            img_url = re.sub(r"w\d+-h\d+-rw", "w2048-h1100-rw", img_url)
            images += f'''
            <div class="img-wrap">
              <img src="{img_url}" alt="{h2_title}_{cc}">
            </div>
            '''
    except Exception as e:
        print(f"[이미지 오류] {e}")

    return h1, h2_title, contents_text, images

# ================================
# 시트에서 키워드 읽기
# ================================
row_idx = None
rows = ws.get_all_values()
for idx, row in enumerate(rows[1:], start=2):
    if len(row) < 4 or row[3].strip() != "완":
        row_idx = idx
        break

if not row_idx:
    print("처리할 키워드 없음.")
    exit()

prefix = ws.cell(row_idx, 1).value or ""
keyword = ws.cell(row_idx, 2).value or ""
suffix = ws.cell(row_idx, 3).value or ""
title = f"{prefix} {keyword} {suffix}".strip()

print(f"👉 이번 실행: {title}")

# ================================
# 앱 크롤링 시작
# ================================
app_links = get_app_links(keyword)
print(f"👉 수집된 앱 링크: {len(app_links)}개")

html = ""
for j, app_url in enumerate(app_links, 1):
    if j > 7:
        break
    h1, h2_title, contents_text, images = get_app_detail(app_url, keyword)

    con_f = f"""
    <h2 data-ke-size="size26">{j}. {h1} 어플 소개</h2>
    <p data-ke-size="size18"><b>1) {h1} 어플 소개</b></p>
    <p data-ke-size="size18">이 어플은 구글플레이스토어에서 "{keyword}" 검색 시 {j}번째로 나오는 앱입니다.</p>
    """
    con_l = f"""
    <p style="text-align: center;" data-ke-size="size18"><a class="myButton" href="{app_url}"> {h2_title} 앱 다운 </a></p>
    <p data-ke-size="size18"><b>2) {h1} 어플 스크린샷 </b></p>
    <div class="img-group">{images}</div>
    """
    html += con_f + contents_text + con_l

# ================================
# Blogger 업로드
# ================================
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

blogger_service = get_blogger_service()
data_post = {
    "content": html,
    "title": title,
    "labels": ["어플", "앱", "추천"],
    "blog": {"id": BLOG_ID},
}
res = blogger_service.posts().insert(blogId=BLOG_ID, body=data_post, isDraft=False, fetchImages=True).execute()
post_url = res.get("url", "")
print("✅ 업로드 성공:", post_url)

# ================================
# 시트에 결과 기록
# ================================
ws.update_cell(row_idx, 4, "완")
ws.update_cell(row_idx, 5, post_url)

# ================================
# 마무리
# ================================
pyperclip.copy(post_url)
print("클립보드 복사 완료!")
