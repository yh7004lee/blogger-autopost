from urllib.parse import urlparse, parse_qs
import re, json, requests, random, os, textwrap, glob, sys, traceback, pickle
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# 단계별 로그 기록 함수 (P열, 한 줄 유지)
# ================================
def log_step(msg: str):
    """단계별 로그를 구글시트 P열(16)에 누적 기록. 줄바꿈 대신 ' | ' 사용."""
    try:
        tr = globals().get("target_row", None)
        if tr:
            prev = ws.cell(tr, 16).value or ""
            sep = " | " if prev else ""
            ws.update_cell(tr, 16, f"{prev}{sep}{msg}")
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)

# ================================
# OpenAI 키 로드 (선택)
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
try:
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
    ws = gc.open_by_key(SHEET_ID).sheet1
    log_step("1단계: Google Sheets 인증 성공")
except Exception as e:
    print("❌ Google Sheets 인증 실패:", e)
    raise

# ================================
# 경로 및 리소스 설정
# ================================
ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

# ================================
# 블로그 ID 로테이션 (3개)
# ================================
BLOG_IDS = [
    "1271002762142343021",
    "4265887538424434999",
    "6159101125292617147",
]

# ================================
# Google Sheet에서 처리할 URL 찾기 (E열=URL, G열='완' 체크)
# ================================
target_row, my_url = None, None
rows = ws.get_all_values()
for i, row in enumerate(rows[1:], start=2):
    url_cell = row[4] if len(row) > 4 else ""   # E열
    status_cell = row[6] if len(row) > 6 else ""  # G열
    if url_cell and (not status_cell or status_cell.strip() != "완"):
        my_url, target_row = url_cell, i
        break
if not my_url:
    log_step("2단계: 처리할 URL 없음 (모든 행 완료)")
    sys.exit(0)
log_step(f"2단계: URL 추출 성공 ({my_url})")

# ================================
# 로테이션 인덱스 읽기 (O1)
# ================================
def read_rotation_index():
    try:
        val = (ws.acell("O1").value or "").strip()
        idx = int(val)
        if idx < -1 or idx >= len(BLOG_IDS):
            return -1
        return idx
    except:
        return -1

last_index = read_rotation_index()
next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]
log_step(f"회전 인덱스: last={last_index} -> next={next_index} (BLOG_ID={BLOG_ID})")

# ================================
# 썸네일 생성 (랜덤 배경)
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

def make_thumb(save_path: str, var_title: str):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()

    # 배경 불러오기 (없으면 흰색 캔버스)
    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

    try:
        font = ImageFont.truetype(ASSETS_FONT_TTF, 48)
    except:
        font = ImageFont.load_default()

    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))

    # 검은 반투명 박스
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)

    draw = ImageDraw.Draw(canvas)

    # 글자 줄바꿈 처리
    var_title_wrap = textwrap.wrap(var_title, width=12)

    # 줄 간격: 폰트 크기 + 여유 (10px)
    line_height = font.getsize("가")[1] + 10  

    total_text_height = len(var_title_wrap) * line_height
    var_y_point = 500 / 2 - total_text_height / 2

    for line in var_title_wrap:
        draw.text((250, var_y_point), line, "#FFEECB", anchor="mm", font=font)
        var_y_point += line_height

    # 최종 크기 조정
    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")

# ================================
# Google Drive 인증 (OAuth: 2nd.json + drive_token_2nd.pickle)
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
            # 액션 환경에서는 토큰을 사전 복원해야 함 (로컬서버 불가)
            raise RuntimeError("drive_token_2nd.pickle이 없거나 만료됨. GitHub Secrets에서 복원 필요.")
        with open("drive_token_2nd.pickle", "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)

# ================================
# Google Drive 업로드 (blogger 폴더)
# ================================
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        # blogger 폴더 확인/생성
        query = "mimeType='application/vnd.google-apps.folder' and name='blogger' and trashed=false"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        items = results.get("files", [])
        if items:
            folder_id = items[0]["id"]
        else:
            folder_metadata = {"name": "blogger", "mimeType": "application/vnd.google-apps.folder"}
            folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
            folder_id = folder.get("id")

        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        # anyone 읽기 권한
        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        file_id = file["id"]
        log_step("3단계: 구글드라이브 업로드 성공")
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    except Exception as e:
        log_step(f"3단계: 구글드라이브 업로드 실패: {e}")
        raise

# ================================
# Blogger 인증 (refresh_token JSON)
# ================================
def get_blogger_service():
    try:
        if not os.path.exists("blogger_token.json"):
            raise FileNotFoundError("blogger_token.json 파일 없음")
        with open("blogger_token.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
        return build("blogger", "v3", credentials=creds)
    except Exception as e:
        log_step(f"블로거 인증 실패: {e}")
        raise

blog_handler = get_blogger_service()
log_step("5단계: Blogger 인증 성공")

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
# ChatGPT API로 본문 가공 (요약 + 3~4문단, <p size18> 강제)
# ================================
def process_with_gpt(section_title: str, raw_text: str, keyword: str) -> str:
    if not client:
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
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        log_step(f"4단계: GPT 변환 성공 ({section_title})")
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log_step(f"4단계: GPT 변환 실패 ({section_title}): {e}")
        return f"<p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# 서론/마무리 7문장 랜덤
# ================================
_syn = {
    "도움": ["도움", "지원", "혜택", "보탬", "유익"],
    "안내": ["안내", "소개", "정리", "가이드", "설명"],
    "중요한": ["중요한", "핵심적인", "필수적인", "꼭 알아야 할"],
    "쉽게": ["쉽게", "간단히", "수월하게", "편리하게"],
    "정보": ["정보", "내용", "자료", "소식", "데이터"],
    "살펴보겠습니다": ["살펴보겠습니다", "알아보겠습니다", "정리하겠습니다"],
}
def _c(w): return random.choice(_syn.get(w, [w]))

def make_intro(keyword):
    parts = [
        f"{keyword}은 많은 분들이 관심을 갖는 {_c('중요한')} 제도입니다.",
        "정부는 이를 통해 생활의 어려움을 덜어주고자 합니다.",
        f"제도를 잘 이해하면 혜택을 더욱 {_c('쉽게')} 받을 수 있습니다.",
        f"오늘은 {keyword}의 개요부터 신청 방법까지 꼼꼼히 {_c('살펴보겠습니다')}.",
        "실제 생활에서 어떻게 활용되는지 사례를 통해 설명드리겠습니다.",
        "끝까지 읽으시면 제도를 이해하는 데 큰 보탬이 되실 겁니다.",
        "여러분께 꼭 필요한 지식과 혜택을 전해드리겠습니다.",
    ]
    return " ".join(parts)

def make_last(keyword):
    parts = [
        f"오늘은 {keyword} 제도를 {_c('안내')}했습니다.",
        f"이 {_c('정보')}를 참고하셔서 실제 신청에 {_c('도움')}이 되시길 바랍니다.",
        "꼭 필요한 분들이 혜택을 누리시길 바랍니다.",
        "앞으로도 다양한 복지 정보를 전해드리겠습니다.",
        "댓글과 의견도 남겨주시면 큰 힘이 됩니다.",
        "끝까지 읽어주셔서 감사드리며, 다음 글도 기대해 주세요.",
        "여러분의 생활이 더 나아지길 바라며 글을 마칩니다.",
    ]
    return " ".join(parts)

# ================================
# 추천글 박스 (feedparser 필요)
# ================================
def get_related_posts(blog_id, count=4):
    try:
        import feedparser
    except ImportError:
        log_step("추천글 박스 생략(feedparser 미설치)")
        return ""
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        return ""
    entries = random.sample(feed.entries, min(count, len(feed.entries)))
    html_box = """
<div style="background:#efede9;border-radius:8px;border:2px dashed #a7a297;
            box-shadow:#efede9 0 0 0 10px;color:#565656;font-weight:bold;
            margin:2em 10px;padding:2em;">
  <p data-ke-size="size16"
     style="border-bottom:1px solid #555;color:#555;font-size:16px;
            margin-bottom:15px;padding-bottom:5px;">♡♥ 같이 보면 좋은글</p>
"""
    for entry in entries:
        html_box += f'<a href="{entry.link}" style="color:#555;font-weight:normal;">● {entry.title}</a><br>\n'
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

    # 썸네일 생성/업로드
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, f"{safe_keyword}.png")
    make_thumb(thumb_path, title)
    log_step("6단계: 썸네일 생성 성공")
    img_url = upload_to_drive(thumb_path, f"{safe_keyword}.png")

    # 서론/마무리
    intro = make_intro(keyword)
    last = make_last(keyword)

    # HTML 조립
    html = f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{intro}</p><br />
<p style="text-align:center;">
  <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p>
<span><!--more--></span><br />
"""

    fields = {
        "개요": "wlfareInfoOutlCn",
        "지원대상": "wlfareSprtTrgtCn",
        "서비스내용": "wlfareSprtBnftCn",
        "신청방법": "aplyMtdDc",
        "추가정보": "etct"
    }
    for title_k, key in fields.items():
        value = data.get(key, "")
        if value and value.strip() not in ["", "정보 없음"]:
            processed = process_with_gpt(title_k, clean_html(value), keyword)
            html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"

    # CTA + 마무리 + 추천글
    related_box = get_related_posts(BLOG_ID)
    html += f"""
<div style="margin:40px 0 20px 0;">
  <p style="text-align:center;" data-ke-size="size18">
    <a class="myButton" href="{my_url}" target="_blank">👉 {keyword} 자세히 보기</a>
  </p><br />
  <p data-ke-size="size18">{last}</p>
</div>
{related_box}
"""

    # 게시
    post_body = {
        "content": html,
        "title": title,
        "labels": ["복지", "정부지원"],
        "blog": {"id": BLOG_ID}
    }
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False, fetchImages=True).execute()

    # === 포스팅 완료 후 시트 업데이트 ===
    ws.update_cell(target_row, 7, "완")          # G열: "완"
    ws.update_cell(target_row, 15, res["url"])   # O열: 포스팅 URL만
    final_html = res.get("content", "")
    soup = BeautifulSoup(final_html, "html.parser")
    img_tag = soup.find("img")
    final_url = img_tag["src"] if img_tag else ""
    log_step(f"7단계: 업로드 성공 → IMG={final_url}")
    ws.update_acell("O1", str(next_index))       # O1: 사용한 인덱스 저장

    print(f"[완료] 블로그 포스팅: {res['url']}")
except Exception as e:
    tb = traceback.format_exc().replace("\n", " | ")
    log_step(f"7단계: 블로그 업로드 실패: {e} | {tb}")
    raise

