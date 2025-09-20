#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")
import urllib.parse
# =============== Imports ===============
import os, re, json, random, requests, traceback, pickle, glob, textwrap, time
from bs4 import BeautifulSoup

# Google Sheets / Drive / Blogger
import gspread
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# OpenAI (선택)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# PIL for thumbnail
from PIL import Image, ImageDraw, ImageFont

# =============== 환경 변수 및 기본 설정 ===============
SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")

# 블로그 1개 ID (고정)
BLOG_ID = "6533996132181172904"
BLOG_URL = "https://apk.appsos.kr/"

# Google Custom Search (선택적: 없으면 앱스토어 직접 검색 파서로 대체)
GCS_API_KEY = os.getenv("GCS_API_KEY", "").strip()
GCS_CX = os.getenv("GCS_CX", "").strip()

# OpenAI API Key 로드 (openai.json 또는 환경변수) — 선택 사용
OPENAI_API_KEY = ""
if os.path.exists("openai.json"):
    try:
        with open("openai.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            OPENAI_API_KEY = data.get("api_key", "").strip()
    except Exception:
        OPENAI_API_KEY = ""
if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None

# =============== Google Sheets 인증 (sheet3 사용) ===============
def get_sheet3():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws3 = sh.worksheet("sheet3")
    except Exception:
        ws3 = sh.get_worksheet(2)  # 0-based index, 세 번째 탭
    return ws3

ws3 = get_sheet3()

# =============== Google Drive 인증 ===============
def get_drive_service():
    # GitHub Actions 등에서 사용자 토큰을 pickle로 보관한 경우를 가정
    # 필요에 맞게 조정 가능
    token_path = "drive_token_2nd.pickle"
    if not os.path.exists(token_path):
        raise RuntimeError("drive_token_2nd.pickle 없음 — Drive API 사용자 토큰이 필요합니다.")
    with open(token_path, "rb") as f:
        creds = pickle.load(f)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds)

# =============== Blogger 인증 ===============
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise RuntimeError("blogger_token.json 없음 — Blogger 사용자 인증 정보가 필요합니다.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data, ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# =============== 썸네일 로깅 (H열 사용) ===============
def log_thumb_step(ws, row_idx, message):
    try:
        prev = ws.cell(row_idx, 8).value or ""   # H열
        new_val = prev + (";" if prev else "") + message
        ws.update_cell(row_idx, 8, new_val)
    except Exception as e:
        print("[로깅 실패]", e)

# =============== 배경 이미지 랜덤 선택 ===============
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join("assets", "backgrounds", ext)))
    return random.choice(files) if files else ""

# =============== 썸네일 생성 ===============
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        try:
            font = ImageFont.truetype(os.path.join("assets", "fonts", "KimNamyun.ttf"), 48)
        except Exception:
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
        y = 500 / 2 - total_text_height / 2

        for line in var_title_wrap:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            x = (500 - text_width) / 2
            draw.text((x, y), line, "#FFEECB", font=font)
            y += line_height

        canvas = canvas.resize((400, 400))
        canvas.save(save_path, "PNG")
        return True
    except Exception as e:
        print(f"에러: 썸네일 생성 실패: {e}")
        return False

# =============== Google Drive 업로드 → 공개 URL 반환 ===============
# ================================
# Google Drive 업로드 → 공개 URL(lh3) 반환
# ================================
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        folder_id = DRIVE_FOLDER_ID

        if not folder_id or folder_id == "YOUR_DRIVE_FOLDER_ID":
            # 기본 blogger 폴더 사용
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

        # 공개 권한
        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        # ✅ 원래 방식 복구 (lh3 구글 CDN 경유)
        return f"https://lh3.googleusercontent.com/d/{file['id']}"
    except Exception as e:
        print(f"에러: 구글드라이브 업로드 실패: {e}")
        return ""


# =============== 썸네일 생성 + 로그 + 업로드 ===============
def make_thumb_with_logging(ws, row_idx, save_path, title):
    try:
        log_thumb_step(ws, row_idx, "썸네일 시작")
        ok = make_thumb(save_path, title)
        if ok:
            log_thumb_step(ws, row_idx, "썸네일 완료")
            url = upload_to_drive(save_path, os.path.basename(save_path))
            if url:
                log_thumb_step(ws, row_idx, f"업로드 완료 → {url}")
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

# =============== 제목/라벨 생성 ===============
def make_post_title(keyword: str) -> str:
    front_choices = ["아이폰 아이패드", "아이패드 아이폰"]
    back_choices = ["앱 추천 어플", "어플 추천 앱", "어플 앱스토어", "앱스토어 어플"]
    return f"{random.choice(front_choices)} {keyword} {random.choice(back_choices)}"

def make_post_labels(sheet_row: list) -> list:
    # 항상 "어플" + 시트 B열 라벨
    label_val = sheet_row[1].strip() if len(sheet_row) > 1 and sheet_row[1] else ""
    return ["어플", label_val] if label_val else ["어플"]

# =============== OpenAI GPT 재작성 (앱 설명) ===============
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = BeautifulSoup(original_html or "", 'html.parser').get_text(separator=' ', strip=True)
    if not client:
        if compact:
            return "".join([f"<p data-ke-size='size18'>{line.strip()}</p>" for line in compact.splitlines() if line.strip()]) or f"<p data-ke-size='size18'>{app_name} 소개</p>"
        return f"<p data-ke-size='size18'>{app_name} 소개</p>"

    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "사실은 유지하되 문장과 구성을 완전히 새로 쓰고, "
        "사람이 직접 적은 듯 자연스럽고 따뜻한 톤으로 풀어줘. "
        "마크다운 금지, <p data-ke-size='size18'> 문단만 사용. "
        "출력은 반드시 3~4개의 문단으로 나눠서 작성하고, "
        "각 문단은 <p data-ke-size='size18'> 태그를 사용해줘."
    )
    user_msg = (
        f"[앱명] {app_name}\n"
        f"[키워드] {keyword_str}\n"
        "아래 원문을 참고해서 블로그용 소개문을 새로 작성해줘.\n\n"
        f"{compact}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=700,
        )
        rewritten = resp.choices[0].message.content.strip()
        if "<p" not in rewritten:
            rewritten = f'<p data-ke-size="size18">{rewritten}</p>'
        return rewritten
    except Exception as e:
        print("[OpenAI 오류]", e)
        if compact:
            return f"<p data-ke-size='size18'>{compact}</p>"
        return f"<p data-ke-size='size18'>{app_name} 소개</p>"

# =============== 앱스토어 앱 ID 추출 (iTunes Search API) ===============
def search_app_store_ids(keyword, limit=10, country="kr"):
    import urllib.parse
    encoded = urllib.parse.quote(keyword)
    url = f"https://itunes.apple.com/search?term={encoded}&country={country}&entity=software&limit={limit}"
    print("[iTunes API 요청]", url)

    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            print(f"[iTunes API 실패] HTTP {res.status_code}")
            return []

        data = res.json()
        results = data.get("results", [])

        apps = []
        for app in results:
            if "trackId" in app and "trackName" in app:
                apps.append({"id": str(app["trackId"]), "name": app["trackName"]})

        # 중복 제거 (trackId 기준)
        seen = set()
        unique_apps = []
        for app in apps:
            if app["id"] not in seen:
                seen.add(app["id"])
                unique_apps.append(app)

        print(f"[iTunes API 결과] {[(a['id'], a['name']) for a in unique_apps]}")
        return unique_apps

    except Exception as e:
        print("[iTunes API 예외]", e)
        print(traceback.format_exc())
        return []

# =============== 앱 상세 페이지 수집 (이름/설명/스크린샷) ===============
def fetch_app_detail(app_id: str, country="kr"):
    import html
    url = f"https://apps.apple.com/{country}/app/id{app_id}"
    name = f"앱 {app_id}"
    desc_html, images = "", []

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.encoding = "utf-8"
        # lxml 미설치 환경 대비
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")

        # 앱 이름
        h1 = soup.find("h1")
        if h1:
            name = html.unescape(h1.get_text(strip=True))
        else:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = html.unescape(og_title["content"])

        # 앱 설명
        desc_div = soup.find("div", class_=re.compile(r"(section__description|description)"))
        if desc_div:
            ps = desc_div.find_all("p")
            if ps:
                desc_html = "".join(
                    f"<p data-ke-size='size18'>{html.unescape(p.get_text(strip=True))}</p>"
                    for p in ps if p.get_text(strip=True)
                )

        if not desc_html:
            meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
            if meta_desc and meta_desc.get("content"):
                desc_html = f"<p data-ke-size='size18'>{html.unescape(meta_desc['content'].strip())}</p>"

        # 스크린샷
        for s in soup.find_all("source"):
            srcset = s.get("srcset", "")
            if srcset:
                img_url = srcset.split(" ")[0]
                if img_url and img_url.startswith("https"):
                    images.append(img_url)

        if not images:
            for img in soup.find_all("img"):
                src = img.get("src") or ""
                if "mzstatic.com" in src:
                    images.append(src)

        # 중복 제거 + 최대 4개
        images = list(dict.fromkeys(images))[:4]

        return {
            "url": url,
            "name": name,
            "desc_html": desc_html,
            "images": images
        }
    except Exception as e:
        print(f"[앱 상세 수집 실패] id={app_id}, error={e}")
        return {"url": url, "name": name, "desc_html": "", "images": []}

# =============== CSS 블록 (한 번만 출력) ===============
def build_css_block() -> str:
    return """
<style>
.img-group {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
}
.img-wrap {
  flex: 0 0 48%;
  margin: 1%;
}
.img-wrap img {
  width: 100%;
  height: auto;
  border-radius: 10px;
}
@media (max-width: 768px) {
  .img-wrap {
    flex: 0 0 100%;
    margin: 5px 0;
  }
}
</style>
"""

# =============== 서론/마무리 블록 ===============
def build_intro_block(title: str, keyword: str) -> str:
    intro_groups = [
        [
            f"스마트폰은 이제 단순한 통신 수단을 넘어 우리의 생활 전반을 책임지는 필수품이 되었습니다.",
            f"손안의 작은 기기 하나로도 '{keyword}' 같은 다양한 기능을 즐길 수 있는 시대가 열렸습니다.",
            f"현대 사회에서 '{keyword}' 앱은 없어서는 안 될 필수 도구로 자리잡고 있습니다.",
            f"특히 '{title}' 같은 주제는 많은 분들이 실제로 궁금해하는 부분입니다.",
            f"스마트폰 기술이 발전하면서 '{keyword}' 관련 앱의 활용도도 점점 높아지고 있습니다.",
            f"누구나 사용하는 스마트폰을 통해 '{keyword}'를 더욱 편리하게 즐길 수 있습니다."
        ],
        [
            f"특히 다양한 앱들이 출시되면서 '{keyword}' 앱의 선택 폭도 넓어졌습니다.",
            f"'{title}'을 찾는 분들이 늘어날 만큼 관심이 점점 커지고 있습니다.",
            f"앱을 통해 생활, 학습, 취미는 물론 '{keyword}'까지 즐길 수 있습니다.",
            f"스마트폰 앱은 시간을 절약하고 효율적인 생활을 가능하게 합니다.",
            f"'{keyword}' 앱은 사용자에게 새로운 경험과 편리함을 동시에 제공합니다.",
            f"새로운 '{keyword}' 앱들이 매일 등장하며, 그만큼 선택의 재미도 늘어납니다."
        ],
        [
            f"예를 들어 업무 효율을 높이는 앱부터 '{keyword}'를 즐길 수 있는 앱까지 다양합니다.",
            f"'{title}'은 많은 사람들이 찾는 인기 있는 카테고리 중 하나입니다.",
            f"게임, 엔터테인먼트와 함께 '{keyword}' 앱은 여가 시간을 풍성하게 만들어 줍니다.",
            f"쇼핑, 금융, 교통과 더불어 '{keyword}' 앱은 생활의 중요한 부분이 되었습니다.",
            f"사진, 영상과 함께 '{keyword}' 콘텐츠를 관리할 수 있는 앱도 많습니다.",
            f"커뮤니케이션 앱 못지않게 '{keyword}' 앱도 많은 관심을 받고 있습니다."
        ],
        [
            f"이처럼 '{keyword}' 앱은 단순한 기능을 넘어 생활 전반을 바꾸고 있습니다.",
            f"'{title}'을 활용하면 삶의 질이 한층 더 높아질 수 있습니다.",
            f"필요한 순간 '{keyword}' 앱으로 원하는 기능을 쉽게 누릴 수 있습니다.",
            f"편리함뿐 아니라 '{keyword}' 앱은 새로운 경험까지 제공합니다.",
            f"많은 사람들이 '{keyword}' 앱 덕분에 더 스마트한 생활을 누리고 있습니다.",
            f"'{keyword}' 앱 하나가 생활 패턴 전체를 바꾸기도 합니다."
        ]
    ]

    intro_sentences = []
    for group in intro_groups:
        intro_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    intro_text = " ".join(intro_sentences)

    first = f'''
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{intro_text}</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
'''
    return first

def build_ending_block(title: str, keyword: str) -> str:
    end_groups = [
        [
            f"이번 글에서 소개한 {title} 관련 앱들이 여러분의 스마트폰 생활에 도움이 되었길 바랍니다.",
            f"오늘 정리해드린 {title} 앱들이 실제 생활 속에서 유용하게 쓰이길 바랍니다.",
            f"이번 포스팅을 통해 만난 {title} 관련 앱들이 스마트한 선택에 보탬이 되었으면 합니다.",
            f"오늘 소개한 {title} 앱들이 독자 여러분의 일상에 꼭 필요한 도구가 되길 바랍니다.",
            f"{title}에 관심 있는 분들에게 이번 정리가 의미 있는 시간이 되었길 바랍니다.",
            f"다양한 {keyword} 앱들을 살펴본 만큼 스마트폰 활용이 훨씬 풍성해지길 바랍니다."
        ],
        [
            f"각 앱의 기능과 장점을 꼼꼼히 다뤘으니 {keyword} 앱 선택에 참고하시기 바랍니다.",
            f"앱들의 특징과 장단점을 비교했으니 {title} 선택에 큰 도움이 되실 겁니다.",
            f"이번 정리를 바탕으로 본인에게 맞는 {keyword} 앱을 쉽게 찾으시길 바랍니다.",
            f"필요할 때 바로 활용할 수 있도록 핵심 정보를 모아 두었으니 꼭 참고해 보세요.",
            f"앞으로 {keyword} 앱을 고르실 때 이번 글이 든든한 가이드가 되길 바랍니다.",
            f"다양한 앱을 비교해본 만큼 현명한 선택에 한 발 더 다가가셨길 바랍니다."
        ],
        [
            "앞으로도 더 다양한 앱 정보를 준비해 찾아뵙겠습니다.",
            f"계속해서 {keyword}와 관련된 알찬 정보와 추천 앱을 공유하겠습니다.",
            "독자분들의 의견을 반영해 더욱 유익한 포스팅으로 돌아오겠습니다.",
            "지속적으로 새로운 앱과 흥미로운 기능들을 소개할 예정입니다.",
            "앞으로도 꼭 필요한 실속 있는 정보를 꾸준히 전해드리겠습니다.",
            f"'{title}'처럼 많은 관심을 받는 주제를 더 자주 다루겠습니다."
        ],
        [
            "댓글과 좋아요는 큰 힘이 됩니다. 가볍게 참여해주시면 감사하겠습니다.",
            "궁금한 점이나 의견이 있다면 댓글로 남겨주시면 적극 반영하겠습니다.",
            "여러분의 피드백은 더 나은 글을 만드는 데 큰 도움이 됩니다.",
            "좋아요와 댓글로 응원해 주시면 더 좋은 정보로 보답하겠습니다.",
            "관심 있는 앱이나 기능이 있으면 댓글에 알려주세요. 참고해서 포스팅하겠습니다.",
            f"{keyword} 앱에 대한 여러분의 생각도 댓글로 자유롭게 남겨주세요."
        ]
    ]

    end_sentences = []
    for group in end_groups:
        end_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    end_text = " ".join(end_sentences)

    last = f"""
<p data-ke-size="size18">&nbsp;</p>
<div style="margin:40px 0px 20px 0px;">
<p data-ke-size="size18">{end_text}</p>
<p data-ke-size="size18">&nbsp;</p>
</div>
"""
    return last
# ================================
# 같이 보면 좋은글 박스 (RSS 랜덤 4개)
# ================================
def get_related_posts(blog_id, count=4):
    import feedparser, random
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    # 랜덤으로 count개 추출
    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    # HTML 박스 생성
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ 같이 보면 좋은글</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box

# =============== 대상 행/키워드/라벨 선택 ===============
def pick_target_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        a = row[0].strip() if len(row) > 0 and row[0] else ""  # A열 = 키워드
        d = row[3].strip() if len(row) > 3 and row[3] else ""  # D열 = 완료
        if a and d != "완":
            return i, row
    return None, None

# =============== H열 로그 누적 ===============
def sheet_append_log(ws, row_idx, message, tries=3, delay=2):
    """H열(8열)에 타임스탬프+메시지를 이어 붙여 기록"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    line = f"[{ts}] {message}"
    for t in range(1, tries+1):
        try:
            prev = ws.cell(row_idx, 8).value or ""   # H열
            new_val = (prev + (";" if prev else "") + line)
            ws.update_cell(row_idx, 8, new_val)
            print(f"[LOG:H{row_idx}] {line}")
            return True
        except Exception as e:
            print(f"[WARN] 로그기록 재시도 {t}/{tries}: {e}")
            time.sleep(delay * t)
    print(f"[FAIL] 로그기록 실패: {line}")
    return False

# =============== 메인 실행 ===============
if __name__ == "__main__":
    try:
        # 1) sheet3에서 대상 행/데이터
        target_row, row = pick_target_row(ws3)
        if not target_row or not row:
            sheet_append_log(ws3, 2, "처리할 키워드 없음(A열)")
            raise SystemExit(0)

        keyword = row[0].strip()  # A열 = 키워드
        label_val = row[1].strip() if len(row) > 1 else ""  # B열 = 라벨

        sheet_append_log(ws3, target_row, f"대상 행={target_row}, 키워드='{keyword}', 라벨='{label_val}'")

        # 2) 제목 생성
        title = make_post_title(keyword)
        sheet_append_log(ws3, target_row, f"타이틀='{title}'")

        # 3) 썸네일 생성 & 업로드
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
        sheet_append_log(ws3, target_row, "썸네일 생성 시작")
        thumb_url = make_thumb_with_logging(ws3, target_row, thumb_path, title)
        sheet_append_log(ws3, target_row, f"썸네일 결과: {thumb_url or '실패'}")

        # 4) 앱 ID 목록 검색
        sheet_append_log(ws3, target_row, "앱 ID 검색 시작")
        apps = search_app_store_ids(keyword, limit=10)
        if not apps:
            sheet_append_log(ws3, target_row, "앱 ID 없음 → 종료")
            raise SystemExit(0)
        sheet_append_log(ws3, target_row, f"앱 ID={[(a['id'], a['name']) for a in apps]}")

        # 5) 서론
        html_full = build_css_block()  # CSS 블록 추가
        html_full += build_intro_block(title, keyword)
        sheet_append_log(ws3, target_row, "서론 블록 생성 완료")

        # 6) 썸네일 본문 삽입
        if thumb_url:
            html_full += f"""
<p style="text-align:center;">
  <img src="{thumb_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p><br /><br />
"""
            sheet_append_log(ws3, target_row, "본문에 썸네일 삽입")
        else:
            sheet_append_log(ws3, target_row, "본문 썸네일 없음")

        # 7) 해시태그
        tag_items = title.split()
        tag_str = " ".join([f"#{t}" for t in tag_items]) + " #앱스토어"
        sheet_append_log(ws3, target_row, f"해시태그='{tag_str}'")

        # 8) 앱 상세 수집 → 본문 조립
        for j, app in enumerate(apps, 1):
            if j > 5:
                break
            try:
                sheet_append_log(ws3, target_row, f"[{j}] 앱 수집 시작 id={app['id']}")
                detail = fetch_app_detail(app["id"])
                app_url = detail["url"]
                app_name = detail["name"]
                src_html = detail["desc_html"]
                images = detail["images"]

                desc_html = rewrite_app_description(src_html, app_name, keyword)
                sheet_append_log(ws3, target_row, f"[{j}] {app_name} 설명 리라이트 성공")

                img_group_html = "".join(
                    f'<div class="img-wrap"><img src="{img_url}" alt="{app_name}_{cc}"></div>'
                    for cc, img_url in enumerate(images, 1)
                )

                section_html = f"""
                <h2 data-ke-size="size26">{j}. {app_name} 어플 소개</h2>
                <br />
                {desc_html}
                <p data-ke-size="size18"><b>2) {app_name} 어플 스크린샷</b></p>
                <div class="img-group">{img_group_html}</div>
                <br />
                <p data-ke-size="size18" style="text-align:center;">
                  <a href="{app_url}" class="myButton">{app_name} 앱 다운</a>
                </p>
                <br />
                <p data-ke-size="size18">{tag_str}</p>
                <br /><br />
                """
                # ✅ 3번째 섹션이면 라벨 기반 추천 박스 삽입
                if j == 3 and label_val:
                    encoded_label = urllib.parse.quote(label_val)
                    section_html += f"""
                <div class="ottistMultiRelated">
                  <a class="extL alt" href="{BLOG_URL}search/label/{encoded_label}?&max-results=10">
                    <span style="font-size: medium;"><strong>추천 {label_val} 어플 보러가기</strong></span>
                    <i class="fas fa-link 2xs"></i>
                  </a>
                </div>
                <br /><br />
                """
                
                html_full += section_html


               
                sheet_append_log(ws3, target_row, f"[{j}] {app_name} 섹션 완료")
            except Exception as e_each:
                sheet_append_log(ws3, target_row, f"[{j}] 앱 처리 실패: {e_each}")

        # 9) 마무리
        html_full += build_ending_block(title, keyword)
        sheet_append_log(ws3, target_row, "마무리 블록 생성 완료")
        related_box = get_related_posts(BLOG_ID, count=4)
        html_full += related_box

        # 10) 업로드
        try:
            labels = make_post_labels(row)  # ["어플", B열 값]
            post_body = {"content": html_full, "title": title, "labels": labels}
            res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body,
                                              isDraft=False, fetchImages=True).execute()
            post_url = res.get("url", "")
            sheet_append_log(ws3, target_row, f"업로드 성공: {post_url}")
        except Exception as up_e:
            sheet_append_log(ws3, target_row, f"업로드 실패: {up_e}")
            raise

        # 11) 시트 기록
        ws3.update_cell(target_row, 4, "완")      # D열 완료
        ws3.update_cell(target_row, 7, post_url)  # G열 = URL
        sheet_append_log(ws3, target_row, f"시트 기록 완료: D='완', G='{post_url}'")

        # 12) 완료
        sheet_append_log(ws3, target_row, "작업 정상 종료")

    except SystemExit:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        row_for_err = target_row if 'target_row' in locals() and target_row else 2
        sheet_append_log(ws3, row_for_err, f"실패: {e}")
        sheet_append_log(ws3, row_for_err, f"Trace: {tb.splitlines()[-1]}")
        print("실패:", e, tb)





