from urllib.parse import urlparse, parse_qs
import re, json, requests, random, os, pickle, textwrap, glob, sys, base64
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# OpenAI 키 로드
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
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
ws = gc.open_by_key(SHEET_ID).sheet1

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

# ================================
# Google Sheet에서 URL 가져오기
# ================================
rows = ws.get_all_values()
target_row, my_url = None, None
for i, row in enumerate(rows[1:], start=2):
    url_cell = row[4] if len(row) > 4 else ""
    status_cell = row[8] if len(row) > 8 else ""
    if url_cell and (not status_cell or status_cell.strip() != "완"):
        my_url, target_row = url_cell, i
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
    bg = Image.open(bg_path).convert("RGBA").resize((500, 500)) if (bg_path and os.path.exists(bg_path)) else Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    try:
        font = ImageFont.truetype(ASSETS_FONT_TTF, 48)
    except:
        font = ImageFont.load_default()
    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    draw = ImageDraw.Draw(canvas)
    var_title_wrap = textwrap.wrap(var_title, width=12)
    var_y_point = 500/2 - (len(var_title_wrap) * 30) / 2
    for line in var_title_wrap:
        draw.text((250, var_y_point), line, "#FFEECB", anchor="mm", font=font)
        var_y_point += 40
    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")
    print("✅ 썸네일 생성 완료:", save_path)

# ================================
# 복지 데이터 가져오기
# ================================
def fetch_welfare_info(wlfareInfoId):
    url = f"https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={wlfareInfoId}&wlfareInfoReldBztpCd=01"
    resp = requests.get(url)
    resp.encoding = "utf-8"
    html = resp.text
    outer_match = re.search(r'initParameter\((\{.*?\})\);', html, re.S)
    if not outer_match:
        raise ValueError("initParameter JSON을 찾지 못했습니다.")
    return json.loads(json.loads(outer_match.group(1))["initValue"]["dmWlfareInfo"])

def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)

# ================================
# GPT API 변환
# ================================
def process_with_gpt(section_title, raw_text, keyword):
    if not client:
        return f"<p data-ke-size='size18'><b>{keyword} {section_title}</b></p><p data-ke-size='size18'>{clean_html(raw_text)}</p>"
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "너는 한국어 블로그 글을 쓰는 카피라이터야. ..."},
                {"role": "user", "content": f"[섹션 제목] {keyword} {section_title}\n[원문]\n{raw_text}"}
            ],
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        err = f"❌ GPT 실패: {e}"
        if target_row:
            ws.update_cell(target_row, 16, err)  # P열
        return f"<p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    creds = None
    if os.path.exists("blogger_token.pickle"):
        with open("blogger_token.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("cc.json", ["https://www.googleapis.com/auth/blogger"])
            creds = flow.run_local_server(port=0)
        with open("blogger_token.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# ================================
# 본문 생성 + 포스팅
# ================================
parsed = urlparse(my_url)
params = parse_qs(parsed.query)
wlfareInfoId = params.get("wlfareInfoId", [""])[0]
data = fetch_welfare_info(wlfareInfoId)
keyword = clean_html(data.get("wlfareInfoNm", "복지 서비스"))
title = f"2025 {keyword} 지원 자격 신청방법"
safe_keyword = re.sub(r'[\\/:*?"<>|.]', "_", keyword)

os.makedirs(THUMB_DIR, exist_ok=True)
thumb_path = os.path.join(THUMB_DIR, f"{safe_keyword}.png")
make_thumb(thumb_path, title)

html = f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{keyword}은 많은 분들이 관심을 갖는 제도입니다.</p><br />
<p style="text-align:center;">
  <img src="file://{os.path.abspath(thumb_path)}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p>
<span><!--more--></span><br />
"""

fields = {"개요":"wlfareInfoOutlCn","지원대상":"wlfareSprtTrgtCn","서비스내용":"wlfareSprtBnftCn","신청방법":"aplyMtdDc","추가정보":"etct"}
for title_k, key in fields.items():
    value = data.get(key, "")
    if value and value.strip() not in ["", "정보 없음"]:
        processed = process_with_gpt(title_k, clean_html(value), keyword)
        html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"

BLOG_ID = os.getenv("BLOG_ID", "5711594645656469839")
post_body = {"content": html, "title": title, "labels": ["복지","정부지원"], "blog": {"id": BLOG_ID}}

try:
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False, fetchImages=True).execute()
    ws.update_cell(target_row, 9, "완")

    # ✅ 업로드 후 HTML에서 최종 이미지 URL 추출
    final_html = res.get("content", "")
    soup = BeautifulSoup(final_html, "html.parser")
    img_tag = soup.find("img")
    final_url = img_tag["src"] if img_tag else ""
    ws.update_cell(target_row, 16, f"IMG={final_url}")  # P열에 최종 URL 기록

    print(f"[완료] 블로그 포스팅: {res['url']}")
    print("최종 이미지 URL:", final_url)

except Exception as e:
    err = f"❌ Blogger 업로드 실패: {e}"
    print(err)
    if target_row:
        ws.update_cell(target_row, 16, err)  # P열
