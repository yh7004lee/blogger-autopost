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
# 앱 이미지 추출 함수 (Google Play 상세 페이지, 최대 4장)
# ================================
def get_app_images(soup, app_name: str):
    images_html = ""
    try:
        img_div = soup.find("div", attrs={"role": "list"})
        imgs = img_div.find_all("img") if img_div else []

        for cc, img in enumerate(imgs[:4], 1):  # 최대 4장
            img_url = img.get("srcset") or img.get("src")
            if not img_url:
                continue

            if "," in img_url:  # srcset → 가장 큰 해상도
                img_url = img_url.split(",")[-1].strip()
            img_url = img_url.split()[0]

            # 🔹 해상도 업스케일
            img_url = re.sub(r"w\d+-h\d+-rw", "w2048-h1100-rw", img_url)

            images_html += f"""
            <div class="img-wrap">
              <img src="{img_url}" alt="{app_name}_{cc}" style="border-radius:10px;" loading="lazy">
            </div>
            """
    except Exception as e:
        print(f"[이미지 수집 에러] {e}")
    return images_html if images_html else "<!-- 스크린샷 없음 -->"



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
# 서론·마무리 랜덤 (SEO 최적화 + 문장 확장)
# ================================
intro_start = [
    "스마트폰 하나만 있어도 다양한 작업을 손쉽게 해결할 수 있는 시대가 되었습니다. ",
    "요즘은 스마트폰과 어플만 잘 활용해도 일상에서 필요한 거의 모든 것을 처리할 수 있습니다. ",
    "현대 생활에서 스마트폰은 단순한 통신 도구를 넘어 필수적인 생활 도구로 자리잡았습니다. ",
    "하루가 다르게 발전하는 모바일 환경 속에서 어플과 앱은 우리의 생활을 더욱 스마트하게 바꿔주고 있습니다. ",
    "이제는 스마트폰을 통해 정보 검색은 물론 업무, 학습, 오락까지 모두 해결할 수 있습니다. ",
    "손안의 작은 기기만 잘 활용해도 생활의 질을 높이고 시간을 절약할 수 있습니다. ",
    "누구나 쉽게 접근할 수 있는 스마트폰 어플 덕분에 생활은 점점 더 편리해지고 있습니다. ",
    "앱과 어플을 알맞게 선택하고 활용하면 단순한 스마트폰이 강력한 개인 비서로 변신합니다. ",
    "스마트폰만 있으면 언제 어디서든 필요한 정보와 즐길 거리를 빠르게 얻을 수 있습니다. "
]

intro_middle = [
    "일상 속 다양한 순간에 꼭 필요한 기능을 제공하며, 사용자들의 편의를 크게 높여주고 있습니다.",
    "생활의 효율성을 높이고 시간을 절약하면서 동시에 더 나은 선택을 할 수 있도록 돕습니다.",
    "업무, 학습, 취미 생활까지 폭넓게 활용되며, 다양한 연령층에서 필수 도구로 자리잡았습니다.",
    "편리함과 동시에 새롭고 흥미로운 경험을 제공하여 스마트폰 활용도를 한층 끌어올립니다.",
    "누구나 원하는 기능을 쉽고 빠르게 이용할 수 있어 생활 속 만족감을 높여줍니다.",
    "정보와 오락을 언제 어디서든 즐길 수 있는 환경을 만들어주며, 선택의 폭을 넓혀줍니다.",
    "최신 트렌드를 반영한 어플은 시대 흐름에 맞게 빠르게 진화하며 사용자들의 요구를 충족시킵니다.",
    "무료로도 충분히 유용한 기능을 제공하는 앱들이 많아 누구나 부담 없이 활용할 수 있습니다.",
    "추천 앱을 적절히 활용하면 생활의 작은 불편을 해결하고 더 나은 라이프스타일을 완성할 수 있습니다."
]

intro_end = [
    "오늘은 그중에서도 꼭 알아두면 좋은 인기 앱과 필수 어플들을 한자리에 정리했습니다.",
    "이번 글에서는 실제 사용자들이 많이 찾고 높은 만족도를 보이는 어플들을 중심으로 소개합니다.",
    "이번 포스팅에서는 실생활에서 활용도가 높고 유용하게 쓰이는 앱들을 하나하나 살펴보겠습니다.",
    "필요할 때 바로 꺼내 쓸 수 있는 인기 어플들을 엄선하여 정리해 보았습니다.",
    "생활 속에서 자주 사용되는 실속 있는 앱들을 중심으로 다양한 활용법까지 담았습니다.",
    "많은 이용자들이 만족하며 사용하는 필수 앱들을 모아 보다 쉽게 비교할 수 있도록 구성했습니다.",
    "스마트폰 생활을 더욱 편리하고 즐겁게 만들어줄 강력한 어플들을 선별해 추천드립니다.",
    "검색 상위권에 꾸준히 오르는 인기 앱들을 모아 실제 후기를 함께 소개합니다.",
    "누구나 쉽게 설치해 활용할 수 있는 최신 앱들을 모아 종합적으로 정리했습니다."
]

def make_intro(title, keyword):
    intro = random.choice(intro_start) + random.choice(intro_middle) + " " + random.choice(intro_end)
    return f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">
{intro}
이번 글에서는 특히 "{title}" 관련 앱들을 집중적으로 소개합니다. 
구글플레이스토어에서 "{keyword}" 검색 시 상위에 노출되는 인기 앱들을 기준으로 꼼꼼히 선정했습니다. 
스마트폰 사용자라면 꼭 알아야 할 필수 어플들을 함께 확인해 보시고, 필요할 때 바로 활용해 보시길 바랍니다.
</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
"""

end_start = [
    "이번 글에서 소개한 앱들이 독자 여러분의 스마트폰 생활을 더욱 편리하게 만들어 드리길 바랍니다.",
    "오늘 정리해드린 어플들이 실제 생활 속에서 유용하게 쓰이며 만족스러운 결과를 가져오길 바랍니다.",
    "소개한 앱들이 단순히 기능적인 부분을 넘어 일상 속 작은 변화를 만들어 주길 기대합니다.",
    "추천드린 앱들이 여러분의 스마트폰 활용도를 높이고 새로운 가능성을 열어주었으면 합니다.",
    "필수 앱들을 잘 활용해 더욱 편리하고 스마트한 하루를 보내시길 바랍니다."
]

end_summary = [
    "각 앱의 주요 기능과 장점을 꼼꼼히 다뤘으니 스마트폰에 설치할 때 참고하시면 도움이 될 것입니다.",
    "앱들의 다양한 기능과 장단점을 함께 소개했으니 본인에게 맞는 앱을 선택하는 데 유익할 것입니다.",
    "실제 사용자가 만족한 포인트들을 반영해 정리했으니 꼭 필요한 앱을 찾는 데 큰 도움이 될 것입니다.",
    "인기와 평점을 고려해 엄선한 앱들이므로 믿고 선택하셔도 좋습니다.",
    "무료와 유료 앱을 나누어 정리했으니 상황과 목적에 맞게 고르실 수 있습니다."
]

end_next = [
    "앞으로도 최신 트렌드와 다양한 앱 정보를 빠르게 전달하겠습니다.",
    "계속해서 알찬 정보와 유용한 앱 추천으로 찾아뵙겠습니다.",
    "새로운 트렌드와 인기 앱들을 더 빠르게 소개할 수 있도록 꾸준히 업데이트하겠습니다.",
    "필수 앱부터 최신 인기 어플까지 꾸준히 모아 정리해드리겠습니다.",
    "스마트폰과 어플 관련 다양한 팁과 정보를 지속적으로 공유하겠습니다."
]

end_action = [
    "댓글과 좋아요는 앞으로 더 좋은 글을 쓰는 데 큰 힘이 됩니다.",
    "궁금한 점이나 의견이 있다면 댓글로 남겨주세요. 바로 반영하도록 하겠습니다.",
    "주변 분들에게 공유해 주시면 더 많은 분들께 도움이 될 수 있습니다.",
    "여러분의 의견은 더 나은 콘텐츠를 만드는 원동력이 됩니다.",
    "관심 있으신 분들은 구독과 알림을 설정해주시면 빠르게 새 글을 받아보실 수 있습니다."
]

end_greet = [
    "오늘도 즐겁고 행복한 하루 되시길 바랍니다~ ^^",
    "끝까지 읽어주셔서 감사드리며 늘 건강과 행복이 함께하시길 바랍니다~ ^^",
    "다음 포스팅에서 더 유익한 정보로 찾아뵙겠습니다. 좋은 하루 되세요~ ^^",
    "앞으로도 꾸준히 찾아와 주시는 모든 분들께 감사의 말씀 드립니다~ ^^",
    "늘 소중한 하루 보내시고, 오늘도 좋은 일만 가득하시길 바랍니다~ ^^"
]

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
# 메인 실행 (마지막 완 행 기반 순차 로테이션)
# ================================
A_CANDIDATES = ["스마트폰", "핸드폰", "휴대폰", "안드로이드"]
C_CANDIDATES = ["어플 추천 앱", "앱 추천 어플"]

def get_last_completed(ws):
    rows = ws.get_all_values()
    last_a, last_c = None, None
    for i in range(len(rows)-1, 0, -1):  # 마지막 행부터 위로 탐색
        if len(rows[i]) > 3 and rows[i][3].strip() == "완":  # D열=완
            last_a = rows[i][0].strip() if len(rows[i]) > 0 else ""
            last_c = rows[i][2].strip() if len(rows[i]) > 2 else ""
            break
    return last_a, last_c

try:
    # ✅ 마지막 "완" 행의 A, C 값 읽기
    prev_a, prev_c = get_last_completed(ws)

    # A 후보 순차 선택
    if prev_a in A_CANDIDATES:
        a_idx = (A_CANDIDATES.index(prev_a) + 1) % len(A_CANDIDATES)
    else:
        a_idx = 0
    chosen_a = A_CANDIDATES[a_idx]

    # C 후보 순차 선택
    if prev_c in C_CANDIDATES:
        c_idx = (C_CANDIDATES.index(prev_c) + 1) % len(C_CANDIDATES)
    else:
        c_idx = 0
    chosen_c = C_CANDIDATES[c_idx]

    # ✅ 이번 대상 행 찾기
    rows = ws.get_all_values()
    target_row, keyword, title = None, None, None
    for i, row in enumerate(rows[1:], start=2):
        if row[1] and (not row[3] or row[3].strip() != "완"):  # B열 값 있고, 아직 '완' 아님
            target_row = i
            b_val = row[1].strip()
            keyword = b_val
            title = f"{chosen_a} {b_val} {chosen_c}".strip()
            break

    if not keyword or not title:
        print("처리할 키워드 없음")
        exit()

    print(f"이번 실행: {title}")

    # 썸네일 생성
    thumb_dir = "thumbnails"
    os.makedirs(thumb_dir, exist_ok=True)
    thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
    img_url = make_thumb_with_logging(ws, target_row, thumb_path, title)

    html = make_intro(title, keyword)
    # ✅ 스크린샷 2열 고정 CSS (반응형 끔)
    html += """
    <style>
    .img-group{
      display:flex;
      flex-wrap:wrap;
      justify-content:center;
      gap:2%;
    }
    .img-wrap{
      flex:0 0 48% !important;   /* 두 칸 고정 */
      max-width:48% !important;
      margin:0 0 12px 0;
      box-sizing:border-box;
    }
    .img-wrap img{
      width:100%;
      height:auto;
      border-radius:10px;
      display:block;
    }
    </style>
    if img_url:
        html += f"""
        <p style="text-align:center;">
          <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
        </p>
        <br /><br />
        """

    # 앱 크롤링
    app_links = crawl_apps(keyword)
    print(f"수집된 앱 링크: {len(app_links)}개")

    # 해시태그 문자열 생성
    tag_list = title.split()
    tag_str = " ".join([f"#{t}" for t in tag_list])
    for j, app_url in enumerate(app_links, 1):
        if j > 7: break
        resp = requests.get(app_url, headers={"User-Agent":"Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        h1 = soup.find("h1").text if soup.find("h1") else f"앱 {j}"
        raw_desc = str(soup.find("div", class_="fysCi")) if soup.find("div", class_="fysCi") else ""
        desc = rewrite_app_description(raw_desc, h1, keyword)
       # 앱 이미지 4장 추출
        images_html = get_app_images(soup, h1)
        
        html += f"""
        <h2 data-ke-size="size26">{j}. {h1} 어플 소개</h2>
        {desc}
        <p data-ke-size="size18"><b>{h1} 스크린샷</b></p>
        <div class="img-group">{images_html}</div>
        <br />
        <p style="text-align: center;" data-ke-size="size18">
          <a class="myButton" href="{app_url}">{h1} 앱 다운로드</a>
        </p>
        <p data-ke-size="size18">{tag_str}</p>
        <br /><br /><br />
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
    url = res.get("url", "")
    print(f"✅ 업로드 성공: {url}")

    # ✅ 시트 업데이트 (선택된 A, C 값 기록)
    ws.update_cell(target_row, 1, chosen_a)  # A열 갱신
    ws.update_cell(target_row, 3, chosen_c)  # C열 갱신
    ws.update_cell(target_row, 4, "완")      # D열 완료 표시
    ws.update_cell(target_row, 7, url)       # G열 포스팅 URL
    ws.update_cell(1, 7, (blog_idx + 1) % len(BLOG_IDS))  # 다음 블로그 인덱스 기록



except Exception as e:
    tb = traceback.format_exc()
    print("실패:", e, tb)











