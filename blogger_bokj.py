import re
import json
import requests
import random
from bs4 import BeautifulSoup
import os
import pickle
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
import textwrap
from PIL import Image, ImageDraw, ImageFont
import glob
import sys
from urllib.parse import urlparse, parse_qs

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding='utf-8')

# ================================
# OpenAI 키 불러오기 (openai.json → fallback: ENV)
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
THUMB_DIR       = "thumbnails"

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
# 썸네일 생성
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

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
    var_y_point = 250 - (len(var_title_wrap) * 30) / 2
    draw = ImageDraw.Draw(canvas)
    for line in var_title_wrap:
        draw.text((250, var_y_point), line, "#FFEECB", anchor="mm", font=font)
        var_y_point += 40
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
# GPT 가공
# ================================
def process_with_gpt(section_title: str, raw_text: str, keyword: str) -> str:
    if not client:
        return f"<b>{keyword} {section_title}</b><p data-ke-size='size18'>{clean_html(raw_text)}</p>"
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "너는 한국어 블로그 카피라이터다. 반드시 <p data-ke-size=\"size18\"> 태그 사용."},
                {"role": "user", "content": f"[섹션] {keyword} {section_title}\n[원문]\n{raw_text}"}
            ],
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        err = f"❌ GPT 실패: {e}"
        if target_row:
            ws.update_cell(target_row, 16, err)  # P열
        return f"<b>{keyword} {section_title}</b><p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    creds = None
    if os.path.exists("blogger_token.pickle"):
        with open("blogger_token.pickle","rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "cc.json",["https://www.googleapis.com/auth/blogger"])
            creds = flow.run_local_server(port=0)
        with open("blogger_token.pickle","wb") as token:
            pickle.dump(creds, token)
    return build("blogger","v3",credentials=creds)

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

intro = f"{keyword}은 많은 분들이 관심을 갖는 제도입니다. 오늘은 {keyword}의 내용을 정리합니다."
last  = f"오늘은 {keyword} 제도를 소개했습니다. 도움이 되셨길 바랍니다."

# 썸네일 생성
os.makedirs(THUMB_DIR, exist_ok=True)
thumb_path = os.path.join(THUMB_DIR,f"{safe_keyword}.png")
make_thumb(thumb_path,title)

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
  <img src="" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p>
<span><!--more--></span><br />
"""

for title_k,key in fields.items():
    value = data.get(key,"")
    if not value.strip():
        continue
    processed = process_with_gpt(title_k,value,keyword)
    html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"

html += f"""
<div style="margin:40px 0 20px 0;">
  <p style="text-align:center;" data-ke-size="size18"><a class="myButton" href="{my_url}">{keyword}</a></p><br />
  <p data-ke-size="size18">{last}</p>
</div>
"""

# ================================
# Blogger 포스팅 + 이미지 업로드
# ================================
BLOG_ID = os.getenv("BLOG_ID","5711594645656469839")
post_body = {"content": html, "title": title, "labels": ["복지","정부지원"], "blog": {"id": BLOG_ID}}

try:
    media = MediaFileUpload(thumb_path, mimetype="image/png")
    res = blog_handler.posts().insert(
        blogId=BLOG_ID,
        body=post_body,
        isDraft=False,
        fetchImages=True,
        media_body=media
    ).execute()

    img_url = ""
    if "images" in res and res["images"]:
        img_url = res["images"][0].get("url","")

    if img_url:
        html = html.replace('src=""', f'src="{img_url}"')
        res = blog_handler.posts().update(
            blogId=BLOG_ID,
            postId=res["id"],
            body={"content": html, "title": title, "labels": ["복지","정부지원"]},
            fetchImages=True
        ).execute()

    ws.update_cell(target_row, 9, "완")  # I열
    print(f"[완료] 블로그 포스팅: {res['url']}")
except Exception as e:
    err = f"❌ Blogger 업로드 실패: {e}"
    print(err)
    if target_row:
        ws.update_cell(target_row, 16, err)  # P열
