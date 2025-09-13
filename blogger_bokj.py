import re
import json
import requests
import time
import random
from bs4 import BeautifulSoup
import os
import pickle
from urllib.parse import urlparse, parse_qs
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials

import io
import textwrap
from PIL import Image, ImageDraw, ImageFont
import glob
import sys

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding='utf-8')

# ================================
# OpenAI 키 불러오기
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
# 구글시트 인증
# ================================
SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)

SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
sh = gc.open_by_key(SHEET_ID)
ws = sh.sheet1

ASSETS_BG_DIR   = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"

# ================================
# 시트에서 첫 번째 미완료 URL 찾기
# ================================
rows = ws.get_all_values()
target_row = None
my_url = None
for i, row in enumerate(rows[1:], start=2):
    url_cell = row[4] if len(row) > 4 else ""   # F열
    status_cell = row[8] if len(row) > 8 else "" # I열
    if url_cell and (not status_cell or status_cell.strip() != "완"):
        my_url = url_cell
        target_row = i
        break

if not my_url:
    print("🔔 처리할 새로운 URL이 없습니다.")
    exit()

print("👉 이번에 처리할 URL:", my_url)

# ================================
# 썸네일 생성 + Blogger 업로드
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

def upload_image_to_blogger(blog_service, blog_id, image_path, title):
    with open(image_path, "rb") as f:
        image_data = f.read()
    media = {
        "name": os.path.basename(image_path),
        "mimeType": "image/png"
    }
    media_body = io.BytesIO(image_data)
    req = blog_service.media().upload(
        blogId=blog_id,
        media_body=media_body,
        media_mime_type="image/png"
    )
    return req.execute().get("url")

def make_thumb(save_path: str, var_title: str):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()
    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    try:
        font = ImageFont.truetype(ASSETS_FONT_TTF, 48)
    except Exception:
        font = ImageFont.load_default()
    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    var_title_wrap = textwrap.wrap(var_title, width=12)
    draw = ImageDraw.Draw(canvas)
    y_point = 250 - (len(var_title_wrap) * 30) // 2
    for line in var_title_wrap:
        draw.text((250, y_point), line, "#FFEECB", anchor="mm", font=font)
        y_point += 40
    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")
    print("✅ 썸네일 생성 완료:", save_path)

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
    return json.loads(outer_data["initValue"]["dmWlfareInfo"])

def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)

# ================================
# ChatGPT API로 본문 가공
# ================================
def process_with_gpt(section_title: str, raw_text: str, keyword: str) -> str:
    if not client:
        msg = "⚠️ OpenAI client가 초기화되지 않았습니다. (API 키 없음)"
        try: ws.update_cell(target_row, 16, msg)
        except: pass
        return f"<p data-ke-size='size18'><b>{keyword} {section_title}</b></p><p data-ke-size='size18'>{clean_html(raw_text)}</p>"

    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "주제는 정부 복지서비스이고, 주어진 원문을 "
        "1) 먼저 <b>태그로 굵게 요약(한두 문장)</b>, "
        "2) 그 아래에 친절하고 자세한 설명을 붙이는 형태로 가공해. "
        "출력은 반드시 3~4개의 문단으로 나눠서 작성하되, "
        "각 문단 사이에는 <p data-ke-size=\"size18\"> 태그를 사용하고 "
        "빈 줄(줄바꿈)으로 구분해. "
        "마크다운 금지, 반드시 <p data-ke-size=\"size18\"> 태그 사용."
    )
    user_msg = f"[섹션 제목] {keyword} {section_title}\n[원문]\n{raw_text}"

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "system", "content": system_msg},{"role": "user", "content": user_msg}],
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        err_msg = f"❌ GPT API 호출 실패: {e}"
        print(err_msg)
        try: ws.update_cell(target_row, 16, err_msg)
        except: pass
        return f"<p data-ke-size='size18'><b>{keyword} {section_title}</b></p><p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    creds = None
    if os.path.exists('blogger_token.pickle'):
        with open('blogger_token.pickle','rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('cc.json',['https://www.googleapis.com/auth/blogger'])
            creds = flow.run_local_server(port=0)
        with open('blogger_token.pickle','wb') as token:
            pickle.dump(creds, token)
    return build('blogger','v3',credentials=creds)

blog_handler = get_blogger_service()

# ================================
# 본문 생성
# ================================
parsed = urlparse(my_url)
params = parse_qs(parsed.query)
wlfareInfoId = params.get("wlfareInfoId", [""])[0]
data = fetch_welfare_info(wlfareInfoId)
keyword = clean_html(data.get("wlfareInfoNm","복지 서비스"))
title = f"2025 {keyword} 지원 자격 신청방법"
safe_keyword = re.sub(r'[\\/:*?"<>|.]','_',keyword)

intro = f"{keyword}은 많은 분들이 관심을 갖는 중요한 제도입니다. 정부는 이를 통해 생활의 어려움을 덜어주고자 합니다."
last  = f"오늘은 {keyword} 제도를 안내했습니다. 앞으로도 다양한 복지 정보를 전해드리겠습니다."

# 썸네일 생성 후 Blogger 업로드
os.makedirs("thumbnails", exist_ok=True)
thumb_path = os.path.join("thumbnails",f"{safe_keyword}.png")
make_thumb(thumb_path,title)
img_url = upload_image_to_blogger(blog_handler, os.getenv("BLOG_ID","5711594645656469839"), thumb_path, title)

fields = {
    "개요":"wlfareInfoOutlCn",
    "지원대상":"wlfareSprtTrgtCn",
    "서비스내용":"wlfareSprtBnftCn",
    "신청방법":"aplyMtdDc",
    "추가정보":"etct"
}

html = f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{intro}</p><br />
<p style="text-align:center;">
  <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p>
<span><!--more--></span><br />
"""

for title_k,key in fields.items():
    value = data.get(key,"")
    if not value or value.strip() in ["","정보 없음"]: continue
    processed = process_with_gpt(title_k, value, keyword)
    html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"

html += f"""
<div style="margin:40px 0 20px 0;">
  <p style="text-align:center;" data-ke-size="size18"><a class="myButton" href="{my_url}">{keyword}</a></p><br />
  <p data-ke-size="size18">{last}</p>
</div>
"""

labels = ["복지","정부지원"]
for word in ["청년","장애인","소상공인","여성","임산부","지원금"]:
    if word in title: labels.append(word)

BLOG_ID = os.getenv("BLOG_ID","5711594645656469839")
post_body = {'content':html,'title':title,'labels':labels,'blog':{'id':BLOG_ID}}
res = blog_handler.posts().insert(blogId=BLOG_ID,body=post_body,isDraft=False,fetchImages=True).execute()

ws.update_cell(target_row,9,"완")
print(f"[완료] 블로그 포스팅: {res['url']}")
