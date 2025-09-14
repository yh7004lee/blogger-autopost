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

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# 단계별 로그 기록 함수
# ================================
def log_step(msg):
    """단계별 로그를 구글시트 P열에 누적 기록"""
    try:
        if target_row:
            prev = ws.cell(target_row, 16).value or ""  # P열 값 읽기
            new_log = prev + f"{msg}\n"
            ws.update_cell(target_row, 16, new_log)
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)

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
try:
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
    ws = gc.open_by_key(SHEET_ID).sheet1
    log_step("1단계: Google Sheets 인증 성공")
except Exception as e:
    log_step(f"1단계: Google Sheets 인증 실패: {e}")
    raise

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

# ================================
# Google Sheet에서 URL 가져오기
# ================================
target_row, my_url = None, None
try:
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        url_cell = row[4] if len(row) > 4 else ""
        status_cell = row[8] if len(row) > 8 else ""
        if url_cell and (not status_cell or status_cell.strip() != "완"):
            my_url, target_row = url_cell, i
            break
    if not my_url:
        log_step("2단계: 처리할 URL 없음 (모든 행 완료)")
        exit()
    log_step(f"2단계: URL 추출 성공 ({my_url})")
except Exception as e:
    log_step(f"2단계: URL 추출 실패: {e}")
    raise

# ================================
# 썸네일 생성
# ================================
# ================================
# 썸네일 생성 (좌우/상하 정렬 개선)
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""


def make_thumb(save_path: str, var_title: str):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()

    # 배경 불러오기 (없으면 흰색)
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

    # 줄바꿈 처리
    var_title_wrap = textwrap.wrap(var_title, width=12)

    # 줄 간격 계산 (getbbox 사용)
    bbox = font.getbbox("가")  # (xmin, ymin, xmax, ymax)
    line_height = (bbox[3] - bbox[1]) + 12
    total_text_height = len(var_title_wrap) * line_height
    var_y_point = 500 / 2 - total_text_height / 2

    for line in var_title_wrap:
        # 텍스트 폭 계산 후 중앙 정렬
        text_bbox = draw.textbbox((0, 0), line, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        x = (500 - text_width) / 2
        draw.text((x, var_y_point), line, "#FFEECB", font=font)
        var_y_point += line_height

    # 최종 리사이즈
    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")


# ================================
# Google Drive 인증 (서비스 계정)
# ================================
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

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
            # GitHub Actions에서는 로컬 서버 실행 불가 → 반드시 사전 복원 필요
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

        # blogger 폴더 확인
        query = "mimeType='application/vnd.google-apps.folder' and name='blogger' and trashed=false"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        items = results.get("files", [])
        if items:
            folder_id = items[0]["id"]
        else:
            folder_metadata = {"name": "blogger", "mimeType": "application/vnd.google-apps.folder"}
            folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
            folder_id = folder.get("id")

        # 파일 업로드
        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        # anyone 권한 부여
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
# Blogger 인증 (refresh_token 사용)
# ================================
def get_blogger_service():
    try:
        if not os.path.exists("blogger_token.json"):
            raise FileNotFoundError("blogger_token.json 파일이 없습니다. 먼저 로컬에서 발급하세요.")

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
# GPT API 변환
# ================================
def process_with_gpt(section_title, raw_text, keyword):
    if not client:
        log_step("4단계: GPT 미사용 (API 키 없음)")
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
        log_step(f"4단계: GPT 변환 성공 ({section_title})")
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log_step(f"4단계: GPT 변환 실패 ({section_title}): {e}")
        return f"<p data-ke-size='size18'>{clean_html(raw_text)}</p>"

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
    log_step("6단계: 썸네일 생성 성공")

    img_url = upload_to_drive(thumb_path, f"{safe_keyword}.png")

    html = f"""
    <div id="jm">&nbsp;</div>
    <p data-ke-size="size18">{keyword}은 많은 분들이 관심을 갖는 제도입니다.</p><br />
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

    BLOG_ID = os.getenv("BLOG_ID", "5711594645656469839")
    post_body = {"content": html, "title": title, "labels": ["복지","정부지원"], "blog": {"id": BLOG_ID}}

    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False, fetchImages=True).execute()
    ws.update_cell(target_row, 9, "완")

    final_html = res.get("content", "")
    soup = BeautifulSoup(final_html, "html.parser")
    img_tag = soup.find("img")
    final_url = img_tag["src"] if img_tag else ""
    ws.update_cell(target_row, 16, f"{ws.cell(target_row,16).value}\n7단계: 업로드 성공 → IMG={final_url}")

    print(f"[완료] 블로그 포스팅: {res['url']}")
except Exception as e:
    tb = traceback.format_exc()
    log_step(f"7단계: 블로그 업로드 실패: {e}\n{tb}")
