from urllib.parse import urlparse, parse_qs
import re, json, requests, random, os, textwrap, glob, sys, traceback
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from google.oauth2.credentials import Credentials as UserCredentials
import feedparser

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# 단계별 로그 기록 함수 (P열 사용)
# ================================
def log_step(msg):
    try:
        if target_row:
            prev = ws.cell(target_row, 16).value or ""  # P열 (16번째)
            new_log = prev + f"{msg}\n"
            ws.update_cell(target_row, 16, new_log)
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)

# ================================
# OpenAI API 키 로드
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
log_step("1단계: Google Sheets 인증 성공")

# ================================
# 경로 및 리소스 설정
# ================================
ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"
DRIVE_FOLDER_ID = "1Z6WF4Lt-Ou8S70SKkE5M4tTHxrXJHxKU"

# ================================
# Google Sheet에서 URL 가져오기
# ================================
target_row, my_url = None, None
rows = ws.get_all_values()
for i, row in enumerate(rows[1:], start=2):
    url_cell = row[4] if len(row) > 4 else ""
    status_cell = row[6] if len(row) > 6 else ""  # G열
    if url_cell and (not status_cell or status_cell.strip() != "완"):
        my_url, target_row = url_cell, i
        break
if not my_url:
    log_step("2단계: 처리할 URL 없음 (모든 행 완료)")
    sys.exit(0)
log_step(f"2단계: URL 추출 성공 ({my_url})")

# ================================
# 썸네일 생성 (랜덤 배경)
# ================================
def pick_random_background():
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

def make_thumb(save_path, var_title):
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

# ================================
# Google Drive 업로드
# ================================
def get_drive_service():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/drive.file"])
    return build("drive", "v3", credentials=creds)

def upload_to_drive(file_path, file_name):
    drive_service = get_drive_service()
    file_metadata = {"name": file_name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
    file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    drive_service.permissions().create(fileId=file["id"], body={"role": "reader", "type": "anyone"}).execute()
    return f"https://lh3.googleusercontent.com/d/{file['id']}"

# ================================
# Blogger 인증 (refresh_token JSON)
# ================================
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise FileNotFoundError("blogger_token.json 파일 없음")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()
log_step("3단계: Blogger 인증 성공")

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
                {"role": "system", "content": "너는 한국어 블로그 글을 쓰는 카피라이터야. 친절하고 SEO 친화적인 HTML 글만 출력해."},
                {"role": "user", "content": f"[섹션 제목] {keyword} {section_title}\n[원문]\n{raw_text}"}
            ],
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log_step(f"GPT 변환 실패 ({section_title}): {e}")
        return f"<p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# 7문단 서론 / 7문단 마무리
# ================================
def choice(word, synonyms):
    return random.choice(synonyms.get(word, [word]))

synonyms = {
    "도움": ["도움", "지원", "혜택", "보탬", "이익", "유익", "안정망"],
    "안내": ["안내", "소개", "정리", "가이드", "설명", "풀이"],
    "중요한": ["중요한", "핵심적인", "필수적인", "꼭 알아야 할"],
    "쉽게": ["쉽게", "간단히", "수월하게", "편리하게"],
    "정보": ["정보", "내용", "자료", "소식", "데이터"],
    "살펴보겠습니다": ["살펴보겠습니다", "알아보겠습니다", "정리하겠습니다"],
}

def make_intro(keyword):
    parts = [
        f"{keyword}은 많은 분들이 관심을 갖는 {choice('중요한', synonyms)} 제도입니다.",
        "정부는 이를 통해 생활의 어려움을 덜어주고자 합니다.",
        f"제도를 잘 이해하면 혜택을 더욱 {choice('쉽게', synonyms)} 받을 수 있습니다.",
        f"오늘은 {keyword}의 개요부터 신청 방법까지 꼼꼼히 {choice('살펴보겠습니다', synonyms)}.",
        "실제 생활에서 어떻게 활용되는지 사례를 통해 설명드리겠습니다.",
        "끝까지 읽으시면 제도를 이해하는 데 큰 보탬이 되실 겁니다.",
        "여러분께 꼭 필요한 지식과 혜택을 전해드리겠습니다.",
    ]
    return " ".join(random.sample(parts, 7))

def make_last(keyword):
    parts = [
        f"오늘은 {keyword} 제도를 {choice('안내', synonyms)}했습니다.",
        f"이 {choice('정보', synonyms)}를 참고하셔서 실제 신청에 {choice('도움', synonyms)}이 되시길 바랍니다.",
        "앞으로도 다양한 복지 정보를 전해드리겠습니다.",
        "댓글과 의견도 남겨주시면 큰 힘이 됩니다.",
        "앞으로 다룰 주제에 대한 의견도 기다리겠습니다.",
        "끝까지 읽어주셔서 감사드리며, 다음 글도 기대해 주세요.",
        "여러분의 생활이 더 나아지길 바라며 글을 마칩니다.",
    ]
    return " ".join(random.sample(parts, 7))

# ================================
# 추천글 박스
# ================================
def get_related_posts(blog_id, count=4):
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        return ""
    entries = random.sample(feed.entries, min(count, len(feed.entries)))
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ 같이 보면 좋은글</p>
"""
    for entry in entries:
        html_box += f'<a href="{entry.link}" style="color: #555555; font-weight: normal;">● {entry.title}</a><br>\n'
    html_box += "</div>\n"
    return html_box

# ================================
# 본문 생성 + 포스팅
# ================================
try:
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
    img_url = upload_to_drive(thumb_path, f"{safe_keyword}.png")
    log_step("4단계: 썸네일 생성 + 업로드 성공")

    intro = make_intro(keyword)
    last = make_last(keyword)

    html = f"""
    <div id="jm">&nbsp;</div>
    <p data-ke-size="size18">{intro}</p><br />
    <p style="text-align:center;">
      <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
    </p>
    <span><!--more--></span><br />
    """

    fields = {"개요":"wlfareInfoOutlCn","지원대상":"wlfareSprtTrgtCn","서비스내용":"wlfareSprtBnftCn","신청방법":"aplyMtdDc","추가정보":"etct"}
    for title_k, key in fields.items():
        value = data.get(key, "")
        if value and value.strip() not in ["", "정보 없음"]:
            processed = process_with_gpt(title_k, clean_html(value), keyword)
            html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"

    related_box = get_related_posts(os.getenv("BLOG_ID", "5711594645656469839"))

    html += f"""
<div style="margin:40px 0px 20px 0px;">
<p style="text-align: center;" data-ke-size="size18">
<a class="myButton" href="{my_url}" target="_blank">👉 {keyword} 바로가기</a></p><br />
<p data-ke-size="size18">{last}</p>
</div>
{related_box}
"""

    BLOG_ID = os.getenv("BLOG_ID", "5711594645656469839")
    post_body = {"content": html, "title": title, "labels": ["복지","정부지원"], "blog": {"id": BLOG_ID}}
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False, fetchImages=True).execute()

    ws.update_cell(target_row, 7, "완")  # G열 기록
    log_step("5단계: 블로그 업로드 성공")

    print(f"[완료] 블로그 포스팅: {res['url']}")
except Exception as e:
    tb = traceback.format_exc()
    log_step(f"블로그 업로드 실패: {e}\n{tb}")
    raise
