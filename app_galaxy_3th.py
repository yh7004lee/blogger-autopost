import sys
sys.stdout.reconfigure(encoding="utf-8")
import os, re, json, random, requests, traceback
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request

# ================================
# 환경 변수 및 기본 설정
# ================================
SHEET_ID = os.getenv("SHEET_ID", "YOUR_SHEET_ID")
BLOG_ID = os.getenv("BLOG_ID", "YOUR_BLOG_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")

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
    SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
    return gc.open_by_key(SHEET_ID).sheet1

ws = get_sheet()


# ================================
# Google Drive 인증
# ================================
def get_drive_service():
    if not os.path.exists("drive_token_2nd.pickle"):
        raise RuntimeError("drive_token_2nd.pickle 없음")
    import pickle
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
def log_thumb_step(ws, row_idx, message):
    try:
        prev = ws.cell(row_idx, 8).value or ""   # H열
        new_val = prev + (";" if prev else "") + message
        ws.update_cell(row_idx, 8, new_val)
    except Exception as e:
        print("[로깅 실패]", e)

# ================================
# 썸네일 생성
# ================================
from PIL import Image, ImageDraw, ImageFont

def make_thumb(save_path: str, var_title: str):
    try:
        bg = Image.new("RGB", (500, 500), (240, 240, 240))
        font = ImageFont.truetype("arial.ttf", 36)
        draw = ImageDraw.Draw(bg)

        # 줄바꿈 처리
        lines = []
        words = var_title.split()
        line = ""
        for word in words:
            test_line = f"{line} {word}".strip()
            w, h = draw.textsize(test_line, font=font)
            if w <= 460:
                line = test_line
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)

        total_h = len(lines) * (h + 10)
        y = (500 - total_h) // 2

        for line in lines:
            w, _ = draw.textsize(line, font=font)
            x = (500 - w) // 2
            draw.text((x, y), line, fill="black", font=font)
            y += h + 10

        bg.save(save_path)
    except Exception as e:
        raise RuntimeError(f"썸네일 생성 실패: {e}")

def make_thumb_with_logging(ws, row_idx, save_path, title):
    try:
        log_thumb_step(ws, row_idx, "시작")
        make_thumb(save_path, title)
        log_thumb_step(ws, row_idx, "완료")
        return True
    except Exception as e:
        log_thumb_step(ws, row_idx, f"에러:{e}")
        return False

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
# 서론·마무리 랜덤 (풍성하게)
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
여러 앱을 직접 사용해보고, 기능과 장단점을 자세히 살펴보았으며, 
구글플레이스토어에서 "{keyword}" 검색 시 실제 상위 노출되는 앱들을 기준으로 선정했습니다. 
각 앱의 특징과 실제 사용 후기를 함께 담아 끝까지 읽으시면 앱 선택에 큰 도움이 될 것입니다.
</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
"""

end_start = [
    "이번 글에서 소개한 앱들이 여러분의 생활에 도움이 되었길 바랍니다.",
    "오늘 정리한 앱들이 실제로 유용하게 활용되길 바랍니다.",
    "이번 포스팅에서 소개한 앱들이 실질적인 보탬이 되었으면 합니다."
]
end_summary = [
    "각 앱의 특징과 장점을 꼼꼼히 다뤘으니 선택에 참고하시기 바랍니다.",
    "앱들의 기능과 장단점을 함께 살펴본 만큼 현명한 선택에 도움이 되실 겁니다."
]
end_next = [
    "앞으로도 더 다양한 앱 정보를 준비해서 찾아뵙겠습니다.",
    "계속해서 알찬 정보와 추천 앱을 공유하도록 하겠습니다."
]
end_action = [
    "댓글과 좋아요는 큰 힘이 됩니다.",
    "궁금한 점이나 의견이 있다면 댓글로 남겨주세요."
]
end_greet = [
    "오늘도 즐겁고 행복한 하루 되시길 바랍니다~ ^^",
    "읽어주셔서 감사드리며, 늘 건강과 행복이 함께하시길 바랍니다~ ^^"
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
# 앱 크롤링 (requests + BS)
# ================================
def crawl_apps(keyword):
    url = f"https://play.google.com/store/search?q={keyword}&c=apps"
    resp = requests.get(url, headers={"User-Agent":"Mozilla/5.0"})
    soup = BeautifulSoup(resp.text, "html.parser")
    source = soup.find_all(class_="ULeU3b")
    app_links = []
    for k, s in enumerate(source):
        if k == 15:
            break
        a = s.find("a")
        if a:
            app_links.append("https://play.google.com" + a["href"])
    return app_links[3:]  # 상위 광고 제거

# ================================
# 메인 실행
# ================================
try:
    rows = ws.get_all_values()
    target_row, keyword = None, None
    for i, row in enumerate(rows[1:], start=2):
        if row[0] and (not row[3] or row[3].strip() != "완"):  # A열=키워드, D열=완 여부
            keyword, target_row = row[0], i
            break

    if not keyword:
        print("처리할 키워드 없음")
        exit()

    title = f"{keyword} 어플 추천 앱"
    print(f"이번 실행: {title}")

    # 썸네일 생성
    thumb_path = f"thumb_{keyword}.png"
    make_thumb_with_logging(ws, target_row, thumb_path, title)

    # 앱 크롤링
    app_links = crawl_apps(keyword)
    print(f"수집된 앱 링크: {len(app_links)}개")

    html = make_intro(title, keyword)

    for j, app_url in enumerate(app_links, 1):
        if j > 7:
            break
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

    # Blogger 업로드
    post_body = {"content": html, "title": title, "labels": ["앱","추천"]}
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False).execute()
    url = res.get("url","")
    print(f"✅ 업로드 성공: {url}")

    # 시트 업데이트
    ws.update_cell(target_row, 4, "완")   # D열 완료 표시
    ws.update_cell(target_row, 7, url)    # G열 포스팅 URL

except Exception as e:
    tb = traceback.format_exc()
    print("실패:", e, tb)


