import sys
sys.stdout.reconfigure(encoding="utf-8")
import os, re, json, random, requests, traceback, pickle
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from PIL import Image, ImageDraw, ImageFont
import textwrap

# ================================
# 환경 변수 및 기본 설정
# ================================
SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")

# 블로그 3개 ID (순환)
BLOG_IDS = [
    "1271002762142343021",
    "4265887538424434999",
    "6159101125292617147"
]

# OpenAI API Key 로드
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
    return gc.open_by_key(SHEET_ID).sheet1

ws = get_sheet()

# ================================
# Google Drive 인증
# ================================
def get_drive_service():
    if not os.path.exists("drive_token_2nd.pickle"):
        raise RuntimeError("drive_token_2nd.pickle 없음")
    with open("drive_token_2nd.pickle", "rb") as f:
        creds = pickle.load(f)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open("drive_token_2nd.pickle", "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds)

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data, ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# ================================
# 썸네일 로깅 함수
# ================================
import glob

# ================================
# 배경 이미지 랜덤 선택
# ================================

# ================================
# 썸네일 로깅 함수 (H열 사용)
# ================================
def log_thumb_step(ws, row_idx, message):
    try:
        prev = ws.cell(row_idx, 8).value or ""   # H열
        new_val = prev + (";" if prev else "") + message
        ws.update_cell(row_idx, 8, new_val)
    except Exception as e:
        print("[로깅 실패]", e)

# ================================
# 배경 이미지 랜덤 선택
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join("assets/backgrounds", ext)))
    return random.choice(files) if files else ""

# ================================
# 썸네일 생성 (랜덤 배경 + 반투명 박스 + 중앙정렬 텍스트)
# ================================
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        try:
            font = ImageFont.truetype("assets/fonts/KimNamyun.ttf", 48)
        except:
            font = ImageFont.load_default()

        canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
        canvas.paste(bg, (0, 0))

        # 검은 반투명 박스
        rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
        canvas.paste(rectangle, (0, 125), rectangle)

        draw = ImageDraw.Draw(canvas)

        # 텍스트 줄바꿈 처리
        var_title_wrap = textwrap.wrap(var_title, width=12)
        bbox = font.getbbox("가")
        line_height = (bbox[3] - bbox[1]) + 12
        total_text_height = len(var_title_wrap) * line_height
        y = 500 / 2 - total_text_height / 2

        for line in var_title_wrap:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            x = (500 - text_width) / 2
            draw.text((x, y), line, "#FFEECB", font=font)
            y += line_height

        # 최종 크기 축소 및 저장
        canvas = canvas.resize((400, 400))
        canvas.save(save_path, "PNG")
        return True
    except Exception as e:
        print(f"에러: 썸네일 생성 실패: {e}")
        return False

# ================================
# Google Drive 업로드
# ================================
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        folder_id = DRIVE_FOLDER_ID

        if not folder_id or folder_id == "YOUR_DRIVE_FOLDER_ID":
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

        return f"https://lh3.googleusercontent.com/d/{file['id']}"
    except Exception as e:
        print(f"에러: 구글드라이브 업로드 실패: {e}")
        return ""

# ================================
# 썸네일 생성 + 로그 기록 + 업로드 → URL 반환
# ================================
def make_thumb_with_logging(ws, row_idx, save_path, title):
    try:
        log_thumb_step(ws, row_idx, "썸네일 시작")
        ok = make_thumb(save_path, title)
        if ok:
            log_thumb_step(ws, row_idx, "썸네일 완료")
            url = upload_to_drive(save_path, os.path.basename(save_path))
            if url:
                log_thumb_step(ws, row_idx, "업로드 완료")
                return url
            else:
                log_thumb_step(ws, row_idx, "업로드 실패")
                return ""
        else:
            log_thumb_step(ws, row_idx, "썸네일 실패")
            return ""
    except Exception as e:
        log_thumb_step(ws, row_idx, f"에러:{e}")
        return ""


# ================================
# OpenAI GPT 처리
# ================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    if not client:
        return original_html
    compact = BeautifulSoup(original_html, 'html.parser').get_text(separator=' ', strip=True)
    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "사실은 유지하되 문장과 구성을 새로 쓰고, "
        "자연스럽고 따뜻한 톤으로 풀어줘. "
        "출력은 반드시 <p data-ke-size='size18'> 단락으로 나눠서."
    )
    user_msg = f"[앱명] {app_name}\n[키워드] {keyword_str}\n\n{compact}"
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": system_msg},{"role": "user", "content": user_msg}],
        temperature=0.7,
        max_tokens=600
    )
    return resp.choices[0].message.content.strip()

# ================================
# 서론·마무리 랜덤
# ================================
intro_start = [
    "스마트폰 하나만으로도 ", "요즘은 스마트폰만 잘 활용해도 ",
    "현대 생활에서 스마트폰은 ", "하루가 다르게 발전하는 모바일 환경에서 ",
    "이제는 스마트폰을 통해 "
]
intro_middle = [
    "일상 속 많은 것들을 손쉽게 해결할 수 있습니다.",
    "생활의 효율을 높이고 시간을 절약할 수 있습니다.",
    "업무, 학습, 취미 등 다양한 영역에서 도움을 줍니다.",
    "편리함과 동시에 새로운 경험을 제공합니다.",
    "누구나 원하는 기능을 빠르게 활용할 수 있습니다."
]
intro_end = [
    "오늘은 그중에서도 꼭 알아두면 좋은 앱들을 정리했습니다.",
    "이번 글에서는 특히 많은 분들이 찾는 앱들을 소개하려 합니다.",
    "이번 포스팅에서는 실제로 유용하게 쓰이는 앱들을 살펴보겠습니다.",
    "필요할 때 바로 활용할 수 있는 인기 앱들을 모아봤습니다.",
    "생활 속에서 자주 쓰이는 실속 있는 앱들을 준비했습니다."
]
def make_intro(title, keyword):
    intro = random.choice(intro_start) + random.choice(intro_middle) + " " + random.choice(intro_end)
    return f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">
{intro}
이번 글에서는 특히 "{title}" 관련 앱들을 집중적으로 소개합니다. 
구글플레이스토어에서 "{keyword}" 검색 시 상위 노출되는 앱들을 기준으로 선정했습니다. 
</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
"""

end_start = [
    "이번 글에서 소개한 앱들이 여러분의 생활에 도움이 되었길 바랍니다.",
    "오늘 정리한 앱들이 실제로 유용하게 활용되길 바랍니다."
]
end_summary = [
    "각 앱의 특징과 장점을 꼼꼼히 다뤘으니 선택에 참고하시기 바랍니다.",
    "앱들의 기능과 장단점을 함께 살펴본 만큼 도움이 되실 겁니다."
]
end_next = [
    "앞으로도 더 다양한 앱 정보를 준비해서 찾아뵙겠습니다.",
    "계속해서 알찬 정보와 추천 앱을 공유하도록 하겠습니다."
]
end_action = ["댓글과 좋아요는 큰 힘이 됩니다.", "궁금한 점이나 의견이 있다면 댓글로 남겨주세요."]
end_greet = ["오늘도 즐겁고 행복한 하루 되시길 바랍니다~ ^^", "읽어주셔서 감사드립니다~ ^^"]

def make_last(title):
    return f"""
<p data-ke-size="size18">&nbsp;</p>
<div style="margin:40px 0px 20px 0px;">
<p data-ke-size="size18">
{random.choice(end_start)}  
{random.choice(end_summary)}  
{random.choice(end_next)}  
{random.choice(end_action)}  
{random.choice(end_greet)}
</p>
<p data-ke-size="size18">&nbsp;</p>
</div>
"""

# ================================
# 앱 크롤링
# ================================
def crawl_apps(keyword):
    url = f"https://play.google.com/store/search?q={keyword}&c=apps"
    resp = requests.get(url, headers={"User-Agent":"Mozilla/5.0"})
    soup = BeautifulSoup(resp.text, "html.parser")
    source = soup.find_all(class_="ULeU3b")
    app_links = []
    for k, s in enumerate(source):
        if k == 15: break
        a = s.find("a")
        if a: app_links.append("https://play.google.com" + a["href"])
    return app_links[3:]

# ================================
# 메인 실행
# ================================
try:
    rows = ws.get_all_values()
    target_row, keyword = None, None
    for i, row in enumerate(rows[1:], start=2):
        if row[0] and (not row[3] or row[3].strip() != "완"):
            keyword, target_row = row[0], i
            break

    if not keyword:
        print("처리할 키워드 없음")
        exit()

    title = f"{keyword} 어플 추천 앱"
    print(f"이번 실행: {title}")

    # 썸네일 생성
    thumb_dir = "thumbnails"
    os.makedirs(thumb_dir, exist_ok=True)
    thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
    img_url = make_thumb_with_logging(ws, target_row, thumb_path, title)
    
    html = make_intro(title, keyword)
    if img_url:
        html += f"""
        <p style="text-align:center;">
          <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
        </p>
        """


    # 앱 크롤링
    app_links = crawl_apps(keyword)
    print(f"수집된 앱 링크: {len(app_links)}개")

   
    for j, app_url in enumerate(app_links, 1):
        if j > 7: break
        resp = requests.get(app_url, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        h1 = soup.find("h1").text if soup.find("h1") else f"앱 {j}"
        raw_desc = str(soup.find("div", class_="fysCi")) if soup.find("div", class_="fysCi") else ""
        desc = rewrite_app_description(raw_desc, h1, keyword)
        html += f"""
        <h2 data-ke-size="size26">{j}. {h1} 어플 소개</h2>
        {desc}
        <p data-ke-size="size18"><a href="{app_url}">앱 다운로드</a></p>
        """
    html += make_last(title)

    # 현재 블로그 인덱스 읽기 (G1 셀)
    try:
        blog_idx_val = ws.cell(1, 7).value  # G1
        blog_idx = int(blog_idx_val) if blog_idx_val else 0
    except:
        blog_idx = 0

    blog_idx = blog_idx % len(BLOG_IDS)
    BLOG_ID = BLOG_IDS[blog_idx]

    # Blogger 업로드
    post_body = {"content": html, "title": title, "labels": ["앱","추천"]}
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False).execute()
    url = res.get("url","")
    print(f"✅ 업로드 성공: {url}")

    # 시트 업데이트
    ws.update_cell(target_row, 4, "완")   # D열 완료 표시
    ws.update_cell(target_row, 7, url)    # G열 포스팅 URL
    ws.update_cell(1, 7, (blog_idx+1) % len(BLOG_IDS))  # 다음 블로그 인덱스 기록

except Exception as e:
    tb = traceback.format_exc()
    print("실패:", e, tb)

