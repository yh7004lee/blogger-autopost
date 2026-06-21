from urllib.parse import urlparse, parse_qs
import re, json, requests, random, os, textwrap, glob, sys, traceback, pickle, time
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# 디버그 모드
# ================================
DEBUG_MODE = True

def debug(msg: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

# ================================
# 단계별 로그 기록 함수 (P열, 한 줄 유지)
# ================================
def log_step(msg: str):
    try:
        tr = globals().get("target_row", None)
        if tr and "ws" in globals():
            prev = ws.cell(tr, 16).value or ""
            sep = " | " if prev else ""
            ws.update_cell(tr, 16, f"{prev}{sep}{msg}")
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)
    print(msg)

# ================================
# API 키 - GitHub Secrets 에서 읽기
# ================================
API_KEYS_JSON = os.getenv("API_KEYS_JSON")

if not API_KEYS_JSON:
    raise RuntimeError("API_KEYS_JSON 환경변수가 없습니다. GitHub Secrets 를 확인하세요.")

try:
    secrets = json.loads(API_KEYS_JSON)
except Exception as e:
    raise RuntimeError(f"API_KEYS_JSON 파싱 실패: {e}")

OPENROUTER_API_KEY = secrets.get("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", "")
GROQ_API_KEY = secrets.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY = secrets.get("CEREBRAS_API_KEY", "")
SHEET_ID = "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs"
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")
GCS_API_KEY = secrets.get("GCS_API_KEY", "")
GCS_CX = secrets.get("GCS_CX", "")

client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
genai_client = None
if GEMINI_API_KEY:
    try:
        from google import genai
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        debug(f"Gemini client init 실패: {e}")
        genai_client = None

# ================================
# Google Sheets 인증
# ================================
try:
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(SHEET_ID)
    debug(f"스프레드시트 제목: {sh.title}")
    debug(f"탭 목록: {[w.title for w in sh.worksheets()]}")

    ws = sh.worksheet(sh.worksheets()[0].title)
    debug(f"선택된 탭: {ws.title}")

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
# 시트 덤프 디버그
# ================================
def dump_sheet_preview():
    try:
        rows = ws.get_all_values()
        debug(f"전체 행 수: {len(rows)}")
        for i, row in enumerate(rows[:8], start=1):
            e = row[4].strip() if len(row) > 4 and row[4] else ""
            g = row[6].strip() if len(row) > 6 and row[6] else ""
            debug(f"{i}행 | E='{e}' | G='{g}' | row={row}")
    except Exception as e:
        debug(f"시트 미리보기 실패: {e}")

dump_sheet_preview()

# ================================
# Google Sheet에서 처리할 URL 찾기
# ================================
target_row, my_url = None, None
rows = ws.get_all_values()

for i, row in enumerate(rows[1:], start=2):
    url_cell = row[4].strip() if len(row) > 4 and row[4] else ""    # E열
    statuscell = row[6].strip() if len(row) > 6 and row[6] else ""  # G열

    debug(f"검사중 {i}행 -> URL='{urlcell}', STATUS='{statuscell}'")

    if urlcell and statuscell != "완":
        my_url, target_row = urlcell, i
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
        debug(f"O1 값: '{val}'")
        idx = int(val)
        if idx < -1 or idx >= len(BLOG_IDS):
            return -1
        return idx
    except Exception as e:
        debug(f"회전 인덱스 읽기 실패: {e}")
        return -1

last_index = read_rotation_index()
next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]
log_step(f"회전 인덱스: last={last_index} -> next={next_index} (BLOG_ID={BLOG_ID})")

# ================================
# 배경 이미지 랜덤 선택
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    debug(f"배경 파일 수: {len(files)}")
    return random.choice(files) if files else ""

# ================================
# 썸네일 생성 및 WebP 변환
# ================================
def make_thumb(save_path: str, var_title: str):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()
    debug(f"선택된 배경: {bg_path}")

    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

    try:
        font = ImageFont.truetype(ASSETS_FONT_TTF, 48)
    except Exception as e:
        debug(f"폰트 로드 실패, 기본 폰트 사용: {e}")
        font = ImageFont.load_default()

    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    draw = ImageDraw.Draw(canvas)

    var_title_wrap = textwrap.wrap(var_title, width=12)
    bbox = font.getbbox("가")
    line_height = (bbox[3] - bbox[1]) + 12
    total_text_height = len(var_title_wrap) * line_height
    var_y_point = 500 / 2 - total_text_height / 2

    for line in var_title_wrap:
        text_bbox = draw.textbbox((0, 0), line, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        x = (500 - text_width) / 2
        draw.text((x, var_y_point), line, "#FFEECB", font=font)
        var_y_point += line_height

    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")
    to_webp(save_path)

def to_webp(file_path):
    try:
        img = Image.open(file_path)
        webp_path = file_path.replace('.png', '.webp')
        img.save(webp_path, 'WEBP')
        debug(f"WebP 변환 완료: {webp_path}")
    except Exception as e:
        debug(f"⚠️ WebP 변환 실패: {e}")

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
# Google Drive 업로드
# ================================
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
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
# Blogger 인증
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
# 복지 데이터 가져오기 (네이버 파싱 방식 도입, 5회 재시도 적용)
# ================================
def fetch_welfare_info(wlfareInfoId):
    url = f"https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={wlfareInfoId}&wlfareInfoReldBztpCd=01"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.bokjiro.go.kr/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    for attempt in range(5):
        try:
            time.sleep(random.uniform(5, 10))  # 5~10초 대기열
            debug(f"복지로 데이터 호출 시도 URL: {url}, 회차: {attempt+1}/5")
            
            resp = requests.get(url, headers=headers, timeout=30)
            resp.encoding = "utf-8"
            
            if resp.status_code == 200:
                html = resp.text
                outer_match = re.search(r'initParameter\((\{.*?\})\);', html, re.S)
                if not outer_match:
                    raise ValueError("initParameter JSON 을 찾지 못했습니다.")
                return json.loads(json.loads(outer_match.group(1))["initValue"]["dmWlfareInfo"])
            else:
                debug(f"⚠️ 상태 코드 {resp.status_code} - 재시도 ({attempt + 1}/5)")
                time.sleep(3)
                
        except Exception as e:
            debug(f"⚠️ 연결 실패 ({attempt + 1}/5): {e}")
            if attempt < 4:
                time.sleep(5)
            else:
                raise
                
    raise ValueError("최대 재시도 횟수 초과")

def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)

# ================================
# AI REVIEW (5차 시도 폴백)
# ================================
def generate_ai_review(prompt):
    if genai_client:
        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            print("✅ AI 성공: Gemini Flash")
            return response.text.strip()
        except Exception as e:
            print("⚠️ Gemini Flash 실패:", e)

    if genai_client:
        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            print("✅ AI 성공: Gemini Flash Lite")
            return response.text.strip()
        except Exception as e:
            print("⚠️ Gemini Flash Lite 실패:", e)

    if GROQ_API_KEY:
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "You must output ONLY final answer in Korean. No reasoning, no analysis, no English."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 650
                },
                timeout=20
            )
            data = res.json()
            if res.status_code == 200 and data.get("choices"):
                text = data["choices"][0]["message"].get("content", "")
                if text.strip():
                    print("✅ AI 성공: Groq")
                    return text.strip()
        except Exception as e:
            print("⚠️ Groq 실패:", e)

    if OPENROUTER_API_KEY:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openrouter/auto",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 650
                },
                timeout=20
            )
            data = res.json()
            if res.status_code == 200 and data.get("choices"):
                text = data["choices"][0]["message"].get("content", "")
                if text.strip():
                    print("✅ AI 성공: OpenRouter")
                    return text.strip()
        except Exception as e:
            print("⚠️ OpenRouter 실패:", e)

    if client:
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=650
            )
            text = res.choices[0].message.content.strip()
            print("✅ AI 성공: GPT-4o-mini")
            return text
        except Exception as e:
            print("❌ 모든 AI 실패:", e)

    return "설명 정보입니다. 실패"

def process_with_gpt(section_title: str, raw_text: str, keyword: str) -> str:
    if not (genai_client or GROQ_API_KEY or OPENROUTER_API_KEY or client):
        return f"<p data-ke-size='size18'><b>{keyword} {section_title}</b></p><p data-ke-size='size18'>{clean_html(raw_text)}</p>"

    prompt = f"""
너는 한국어 블로그 글을 쓰는 카피라이터야.
주제는 정부 복지서비스이고, 주어진 원문을
1) 먼저 <b>태그로 굵게 요약 (한두 문장),
2) 그 아래에 친절하고 자세한 설명을 붙이는 형태로 가공해.
출력은 반드시 3~4 개의 문단으로 나눠서 작성하되,
각 문단 사이에는 <p data-ke-size="size18"> 태그를 사용하고
빈 줄 (줄바꿈) 으로 구분해.
마크다운 금지, 반드시 <p data-ke-size="size18"> 태그 사용.
사람이 직접 경험하고 작성한 것처럼 따뜻한 대화체 톤으로 풀어나가줘.
자연스러운 한국어
중요: 오직 한국어로만 작성해야해 중간중간에 한자나 일본어 같은 외국어가 들어가면 안된다.
/////////////////////////
[섹션 제목] {keyword} {section_title}
[원문]
{raw_text}
"""
    rewritten = generate_ai_review(prompt)
    rewritten = rewritten.replace("<body>", "").replace("</body>", "")
    if "<p" not in rewritten:
        rewritten = f"<p data-ke-size='size18'>{rewritten}</p>"
    return rewritten

# ================================
# 서론/마무리
# ================================
_syn = {
    "도움": ["도움", "지원", "혜택", "보탬", "유익"],
    "안내": ["안내", "소개", "정리", "가이드", "설명"],
    "중요한": ["중요한", "핵심적인", "필수적인", "꼭 알아야 할"],
    "쉽게": ["쉽게", "간단히", "수월하게", "편리하게"],
    "정보": ["정보", "내용", "자료", "소식", "data"],
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
        "끝까지 읽으셔서 감사드리며, 다음 글도 기대해 주세요.",
        "여러분의 생활이 더 나아지길 바라며 글을 마칠게요.",
    ]
    return " ".join(parts)

# ================================
# 추천글 박스
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
# 본문 생성 + 포스팅 (깔끔한 요약 테이블 적용)
# ================================
try:
    parsed = urlparse(my_url)
    params = parse_qs(parsed.query)
    wlfareInfoId = params.get("wlfareInfoId", [""])[0]

    debug(f"최종 URL: {my_url}")
    debug(f"파싱된 파라미터: {params}")
    debug(f"wlfareInfoId: {wlfareInfoId}")

    data = fetch_welfare_info(wlfareInfoId)
    keyword = clean_html(data.get("wlfareInfoNm", "복지 서비스")).strip()
    title = f"2026 {keyword} 지원 자격 신청방법"
    safe_keyword = re.sub(r'[\\/:*?"<>|.]', "_", keyword)

    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, f"{safe_keyword}.png")
    make_thumb(thumb_path, title)
    log_step("6단계: 썸네일(WebP 변환포함) 생성 성공")
    
    # 드라이브 링크는 확장자 .webp로 변경
    img_url = upload_to_drive(thumb_path.replace('.png', '.webp'), f"{safe_keyword}.webp")

    intro = make_intro(keyword)
    last = make_last(keyword)

    fields = {
        "개요": "wlfareInfoOutlCn",
        "지원대상": "wlfareSprtTrgtCn",
        "서비스내용": "wlfareSprtBnftCn",
        "신청방법": "aplyMtdDc",
        "추가정보": "etct"
    }

    # 1. 요약 테이블 (Summary Table) HTML 구성
    summary_table_rows = ""
    for title_k, key in fields.items():
        val_text = clean_html(data.get(key, ""))
        if val_text and val_text.strip() not in ["", "정보 없음"]:
            summary_table_rows += f"""
            <tr style="border-bottom: 1px solid #eaeaea;">
                <td style="padding: 14px 16px; font-size: 15px; font-weight: bold; color: #222; background-color: #fcfcfc; width: 25%; text-align: center;">{title_k}</td>
                <td style="padding: 14px 16px; font-size: 15px; color: #333; font-weight: bold; text-align: justify;">{val_text}</td>
            </tr>"""
            
    summary_table_html = f"""
    <table style="width: 100%; border-collapse: collapse; border-top: 2px solid #1a2a40; border-bottom: 1px solid #1a2a40; margin: 20px 0 35px 0; text-align: left;">
        <tbody>{summary_table_rows}</tbody>
    </table>"""

    # 기본 본문 구성 (인트로 + 썸네일 + 요약테이블)
    html = f"""
<div id="jm"> </div>
<p data-ke-size="size18">{intro}</p><br />
<p style="text-align:center;">
  <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p>
<span></span><br />

<br /><h2 data-ke-size='size26' style="border-left: 8px solid #1a2a40; padding-left: 10px;">{keyword} 주요 정보 요약</h2><br />
<p data-ke-size='size18'>정책 검토 시 기준이 되는 <b>{keyword}</b>의 핵심 요약 데이터 표입니다.</p>
{summary_table_html}
<br />
"""

    # 2. 본문 상세 섹션 추가
    for title_k, key in fields.items():
        value = data.get(key, "")
        if value and value.strip() not in ["", "정보 없음"]:
            try:
                processed = process_with_gpt(title_k, clean_html(value), keyword)
                html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"
                debug(f"{title_k} 변환 성공")
            except Exception as e:
                log_step(f"본문 변환 실패({title_k}): {e}")
                html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br /><p data-ke-size='size18'>{clean_html(value)}</p><br /><br />"

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

    post_body = {
        "content": html,
        "title": title,
        "labels": ["복지", "정부지원"],
        "blog": {"id": BLOG_ID}
    }

    debug("Blogger 게시 직전")
    res = blog_handler.posts().insert(
        blogId=BLOG_ID,
        body=post_body,
        isDraft=False,
        fetchImages=True
    ).execute()

    debug(f"Blogger 응답 원본: {res}")

    if not res or not res.get("url"):
        raise RuntimeError(f"Blogger 응답이 비정상입니다: {res}")

    ws.update_cell(target_row, 7, "완")
    ws.update_cell(target_row, 15, res["url"])

    final_html = res.get("content", "")
    soup = BeautifulSoup(final_html, "html.parser")
    img_tag = soup.find("img")
    final_url = img_tag["src"] if img_tag else ""
    log_step(f"7단계: 업로드 성공 → POST={res['url']} | IMG={final_url}")

    ws.update_acell("O1", str(next_index))
    print(f"[완료] 블로그 포스팅: {res['url']}")

except Exception as e:
    tb = traceback.format_exc().replace("\n", " | ")
    print("❌ 최종 실패:", e)
    print(tb)
    try:
        log_step(f"7단계: 실패: {e} | {tb}")
        if target_row:
            ws.update_cell(target_row, 7, "실폐")
    except Exception as inner:
        print(f"⚠️ 실패 로그 기록도 실패: {inner}")
    raise
