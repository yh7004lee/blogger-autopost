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

# PIL (썸네일 생성용)
from PIL import Image, ImageDraw, ImageFont

# =============== 환경 변수 및 기본 설정 ===============
SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")

# 블로그 ID / URL
BLOG_ID = "7550707353079627944"
BLOG_URL = "https://appvn.appsos.kr/"

# Google Custom Search (선택 사항: 미사용 시 앱스토어 직접 파싱)
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
        print("OpenAI 키 로드 중 오류 발생")
        OPENAI_API_KEY = ""
if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None

# =============== Google Sheets 인증 (sheet9 사용) ===============
def get_sheet9():
    try:
        # 서비스 계정 인증
        service_account_file = "sheetapi.json"
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        try:
            ws9 = sh.worksheet("sheet9")   # 시트 이름이 'sheet9'인 경우
        except Exception:
            ws9 = sh.get_worksheet(8)      # 0부터 시작 → 아홉 번째 탭
        return ws9
    except Exception as e:
        print(f"Google Sheets 인증 중 오류 발생: {e}")
        return None

ws9 = get_sheet9()

# =============== 폰트 로드 설정 ===============
FONT_PATH = os.path.join(os.getcwd(), "BeVietnamPro-SemiBold.ttf")
try:
    font = ImageFont.truetype(FONT_PATH, 36)
except Exception as e:
    print(f"폰트 로드 실패: {e}")
    font = None

# =============== 제목 생성 함수 (베트남어) ===============
def make_post_title(keyword: str) -> str:
    # 베트남어 제목 패턴
    patterns = [
        f"{keyword} ứng dụng — Lựa chọn tốt nhất cho iPhone",
        f"Gợi ý các ứng dụng {keyword} cho người dùng iPhone",
        f"Các ứng dụng {keyword} phổ biến nhất trên iPhone",
        f"{keyword} iOS — Ứng dụng tốt nhất bạn nên tải",
        f"Ứng dụng {keyword} được đề xuất cho iPhone"
    ]
    return random.choice(patterns)

# =============== Google Drive 인증 ===============
def get_drive_service():
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

# =============== 제목 생성 ===============
def make_post_title(keyword: str) -> str:
    # 베트남어 제목 패턴
    patterns = [
        f"{keyword} ứng dụng iPhone — Lựa chọn tốt nhất",
        f"{keyword} iOS — Các ứng dụng bạn nên tải",
        f"Gợi ý các ứng dụng {keyword} cho người dùng iPhone",
        f"Các ứng dụng {keyword} phổ biến nhất trên iPhone",
        f"{keyword} — Ứng dụng hữu ích trên iPhone"
    ]
    return random.choice(patterns)

# =============== 라벨 생성 ===============
def make_post_labels(sheet_row: list) -> list:
    label_val = sheet_row[1].strip() if len(sheet_row) > 1 and sheet_row[1] else ""
    labels = ["Ứng dụng", "iPhone"]
    if label_val:
        labels.append(label_val)
    return labels

# =============== 앱스토어 검색 (iTunes Search API) ===============
def search_app_store_ids(keyword, limit=20, country="vn", eng_keyword=""):
    import requests, urllib.parse

    def fetch(term):
        url = f"https://itunes.apple.com/search?term={urllib.parse.quote(term)}&country={country}&entity=software&limit={limit}"
        try:
            res = requests.get(url, timeout=12)
            res.raise_for_status()
            data = res.json()
            return [{"id": str(app["trackId"]), "name": app["trackName"]}
                    for app in data.get("results", []) if "trackId" in app]
        except Exception as e:
            print("[iTunes API 오류]", e)
            return []

    apps = fetch(keyword)
    if len(apps) < 7:
        apps += fetch(f"{keyword} app")
    if len(apps) < 7:
        apps += fetch(f"{keyword} ứng dụng")
    if len(apps) < 7 and eng_keyword:
        apps += fetch(eng_keyword)

    seen, uniq = set(), []
    for a in apps:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return uniq

# =============== 앱 상세 크롤링 ===============
def fetch_app_detail(app_id: str, country="vn"):
    import html
    url = f"https://apps.apple.com/{country}/app/id{app_id}"
    name = f"Ứng dụng {app_id}"
    desc_html, images = "", []

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.encoding = "utf-8"
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

        # ✅ 스크린샷 수집 (홀수 인덱스만)
        images = []
        screenshot_div = soup.find("div", class_="we-screenshot-viewer__screenshots")
        if screenshot_div:
            sources = screenshot_div.find_all("source")
            for idx, src in enumerate(sources, start=1):
                if len(images) >= 4:  # 최대 4장
                    break
                if idx % 2 == 1:  # 1, 3, 5, ...
                    srcset = src.get("srcset", "")
                    if srcset:
                        img_url = srcset.split(" ")[0]
                        if img_url.startswith("http"):
                            images.append(img_url)

        # fallback
        if not images:
            for img in soup.find_all("img"):
                src = img.get("src") or ""
                if "mzstatic.com" in src and src.startswith("http"):
                    images.append(src)
            images = images[:4]

        return {
            "url": url,
            "name": name,
            "desc_html": desc_html,
            "images": images
        }
    except Exception as e:
        print(f"[앱 상세 수집 실패] id={app_id}, error={e}")
        return {"url": url, "name": name, "desc_html": "", "images": []}


# =============== 설명 리라이트 (OpenAI, fallback 있음) ===============
def rewrite_app_description(original_html: str, app_name: str, keyword: str) -> str:
    from bs4 import BeautifulSoup
    plain = BeautifulSoup(original_html or "", "html.parser").get_text(" ", strip=True)
    if not client:
        return f"<p data-ke-size='size18'>{plain or (app_name + ' Giới thiệu')}</p>"
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Viết bài giới thiệu tự nhiên như blogger Việt Nam. Đặt các đoạn văn trong <p data-ke-size='size18'>."},
                {"role": "user", "content": plain}
            ],
            temperature=0.7,
            max_tokens=600,
        )
        text = resp.choices[0].message.content.strip()
        if "<p" not in text:
            text = f"<p data-ke-size='size18'>{text}</p>"
        return text
    except Exception as e:
        print("[OpenAI 실패]", e)
        return f"<p data-ke-size='size18'>{plain or (app_name + ' Giới thiệu')}</p>"

# =============== 썸네일 로그 기록 (H열 사용) ===============
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


# =============== Google Drive 업로드 → 공개 URL 반환 ===============
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        folder_id = DRIVE_FOLDER_ID

        if not folder_id or folder_id == "YOUR_DRIVE_FOLDER_ID":
            # 'blogger' 폴더 검색/생성
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

        # 공개 권한 설정
        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        return f"https://lh3.googleusercontent.com/d/{file['id']}"
    except Exception as e:
        print(f"[에러] 구글드라이브 업로드 실패: {e}")
        return ""


# =============== 썸네일 생성 (픽셀 기준 줄바꿈 적용) ===============
def make_thumb(save_path: str, var_title: str, font_path: str = None):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        # 랜덤 배경 선택
        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        # 폰트 설정
        if font_path is None:
            font_path = os.path.join("assets", "fonts", "PlusJakartaSans-SemiBoldItalic.ttf")
        try:
            if not os.path.exists(font_path):
                print(f"폰트 로드 실패: {font_path} 파일이 존재하지 않습니다. 기본 폰트로 대체합니다.")
                font = ImageFont.load_default()
            else:
                font = ImageFont.truetype(font_path, 48)
        except Exception as e:
            print(f"폰트 로드 실패: {e}. 기본 폰트로 대체합니다.")
            font = ImageFont.load_default()

        # 캔버스 생성
        canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
        canvas.paste(bg, (0, 0))

        # 텍스트 배경 박스
        rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
        canvas.paste(rectangle, (0, 125), rectangle)

        # 텍스트 그리기
        draw = ImageDraw.Draw(canvas)

        def wrap_text(text, font, max_width):
            lines, line = [], ""
            for ch in text:
                test_line = line + ch
                text_width = draw.textlength(test_line, font=font)
                if text_width <= max_width:
                    line = test_line
                else:
                    lines.append(line)
                    line = ch
            if line:
                lines.append(line)
            return lines

        var_title_wrap = wrap_text(var_title, font, max_width=460)

        bbox = font.getbbox("A")  # 기준 글자
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


# =============== 썸네일 생성 + 로그 + 업로드 ===============
def make_thumb_with_logging(ws, row_idx, save_path, title, font_path: str = None):
    try:
        log_thumb_step(ws, row_idx, "썸네일 시작")
        ok = make_thumb(save_path, title, font_path=font_path)
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
# =============== 서론 블록 ===============
def build_intro_block(title: str, keyword: str) -> str:
    intro_groups = [
        [
            f"Hôm nay, điện thoại thông minh không chỉ là công cụ liên lạc mà còn là trợ thủ quan trọng trong cuộc sống hiện đại.",
            f"Với thiết bị trong tay, bạn có thể tận dụng 『{keyword}』 một cách nhanh chóng và tiện lợi.",
            f"Trong sinh hoạt hàng ngày, ứng dụng 『{keyword}』 đã trở thành không thể thiếu để mang lại sự thuận tiện.",
            f"Chủ đề như 『{title}』 luôn thu hút sự quan tâm của nhiều người dùng công nghệ.",
            f"Với sự phát triển của smartphone, việc sử dụng các ứng dụng liên quan đến 『{keyword}』 ngày càng phổ biến.",
            f"Bất kỳ ai cũng có thể tận hưởng 『{keyword}』 dễ dàng thông qua các ứng dụng chuyên dụng."
        ],
        [
            f"Có rất nhiều ứng dụng khác nhau và lựa chọn 『{keyword}』 ngày càng tăng.",
            f"Tìm kiếm 『{title}』 cho thấy chủ đề này đang rất “hot”.",
            f"Công việc, học tập, giải trí và cả 『{keyword}』 đều có thể tối ưu nhờ ứng dụng.",
            f"Apps giúp tiết kiệm thời gian và làm cuộc sống hiệu quả hơn.",
            f"Ứng dụng 『{keyword}』 mang lại trải nghiệm mới và tiện ích cho người dùng.",
            f"Các ứng dụng mới về 『{keyword}』 xuất hiện hàng ngày, tăng khả năng lựa chọn."
        ],
        [
            f"Từ ứng dụng tăng năng suất đến giải trí, tất cả đều liên quan đến 『{keyword}』.",
            f"『{title}』 là một trong những hạng mục phổ biến với người dùng.",
            f"Giống như game hay streaming, ứng dụng 『{keyword}』 làm thời gian rảnh thú vị hơn.",
            f"Mua sắm, tài chính và vận chuyển đều dựa vào app — và 『{keyword}』 cũng vậy.",
            f"Nhiều ứng dụng cho phép quản lý nội dung như ảnh, video và 『{keyword}』 dễ dàng.",
            f"Ứng dụng 『{keyword}』 ngày càng được chú ý, giống như các app giao tiếp."
        ],
        [
            f"Nhờ đó, ứng dụng 『{keyword}』 không chỉ thực hiện chức năng cơ bản mà còn nâng cao trải nghiệm người dùng.",
            f"Với 『{title}』, bạn có thể nâng cao chất lượng cuộc sống hàng ngày.",
            f"Khi cần, chỉ cần mở app 『{keyword}』 để sử dụng tính năng mong muốn.",
            f"Không chỉ tiện lợi, app 『{keyword}』 còn mang đến những trải nghiệm mới.",
            f"Nhiều người đã tận dụng app 『{keyword}』 để có một thói quen thông minh hơn.",
            f"Chỉ một app 『{keyword}』 cũng có thể thay đổi hoàn toàn lối sống."
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

# =============== 마무리 블록 ===============
def build_ending_block(title: str, keyword: str) -> str:
    end_groups = [
        [
            f"Hy vọng các ứng dụng liên quan đến 『{title}』 được giới thiệu ở đây sẽ làm cuộc sống số của bạn tốt hơn.",
            f"Bài viết này tổng hợp các app 『{title}』 chắc chắn hữu ích trong sinh hoạt hàng ngày.",
            f"Các lựa chọn 『{title}』 được giới thiệu có thể giúp bạn đưa ra quyết định thông minh hơn.",
            f"Nếu app 『{title}』 trở thành công cụ không thể thiếu, chúng tôi sẽ rất vui.",
            f"Đối với ai quan tâm 『{title}』, tóm tắt này sẽ rất đáng đọc.",
            f"Khi biết nhiều app về 『{keyword}』, việc sử dụng smartphone của bạn sẽ đầy đủ hơn."
        ],
        [
            f"Chúng tôi trình bày tính năng và ưu điểm của từng app để bạn dễ chọn 『{keyword}』.",
            f"So sánh điểm mạnh và điểm yếu giúp bạn quyết định app 『{title}』 phù hợp.",
            f"Dựa trên tóm tắt này, bạn có thể tìm được app 『{keyword}』 lý tưởng.",
            f"Thông tin được tổng hợp có thể dùng làm hướng dẫn tham khảo.",
            f"Khi chọn app 『{keyword}』, bài viết này sẽ là trợ thủ đáng tin cậy.",
            f"So sánh nhiều app chắc chắn giúp đưa ra quyết định sáng suốt hơn."
        ],
        [
            "Chúng tôi sẽ tiếp tục cung cấp thông tin và tin tức hữu ích về ứng dụng.",
            f"Trong tương lai, thêm nhiều nội dung về 『{keyword}』 và app gợi ý sẽ được đăng tải.",
            "Ý kiến của độc giả rất quan trọng để cung cấp bài viết đầy đủ hơn.",
            "Sắp tới, các app và tính năng nổi bật sẽ tiếp tục được giới thiệu.",
            "Chúng tôi sẽ cập nhật thông tin thực tiễn giúp ích trong cuộc sống hàng ngày.",
            f"Các chủ đề phổ biến như 『{title}』 sẽ tiếp tục được khai thác trên blog."
        ],
        [
            "Bình luận và like là động lực lớn — hãy tham gia thoải mái!",
            "Nếu có thắc mắc hoặc gợi ý, hãy chia sẻ ở phần bình luận để chúng tôi cải thiện.",
            "Phản hồi của các bạn giúp tạo ra nội dung hữu ích hơn.",
            "Hãy ủng hộ bằng like hoặc bình luận để tiếp tục nhận thông tin chất lượng.",
            "Nếu có app bạn quan tâm, hãy gợi ý trong bình luận.",
            f"Ý kiến của bạn về 『{keyword}』 rất quý giá — hãy chia sẻ!"
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
# 관련 추천글 박스 (RSS 랜덤 4개, 베트남어 버전)
# ================================
def get_related_posts(blog_id, count=4):
    import feedparser, random
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        print("[INFO] RSS 피드 항목 없음")
        return ""

    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ Bài Viết Liên Quan</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    print(f"[INFO] 관련 글 박스 생성 완료 ({len(entries)}개 항목)")
    return html_box


# =============== 대상 행/키워드/라벨 선택 (시트9) ===============
def pick_target_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        keyword = row[0].strip() if len(row) > 0 and row[0] else ""  # A열 = 키워드
        status = row[4].strip() if len(row) > 4 and row[4] else ""   # E열 = 완료 플래그
        if keyword and status != "OK":
            print(f"[INFO] 대상 행 선택: {i} (키워드='{keyword}')")
            return i, row
    print("[INFO] 처리할 대상 행 없음")
    return None, None


# =============== H열 로그 누적 ===============
def sheet_append_log(ws, row_idx, message, tries=3, delay=2):
    """H열(8열)에 타임스탬프+메시지를 이어 붙여 기록"""
    import time
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    line = f"[{ts}] {message}"
    for t in range(1, tries+1):
        try:
            prev = ws.cell(row_idx, 8).value or ""   # H열
            new_val = prev + (";" if prev else "") + line
            ws.update_cell(row_idx, 8, new_val)
            print(f"[LOG:H{row_idx}] {line}")
            return True
        except Exception as e:
            print(f"[WARN] 로그 기록 재시도 {t}/{tries}: {e}")
            time.sleep(delay * t)
    print(f"[FAIL] 로그 기록 실패: {line}")
    return False
# =============== 메인 실행 (시트9 + 베트남어) ===============
if __name__ == "__main__":
    import os, time, traceback

    try:
        # 1) 시트9에서 대상 행/데이터
        target_row, row = pick_target_row(ws9)  # ws9 사용
        if not target_row or not row:
            sheet_append_log(ws9, 2, "처리할 키워드가 없습니다(A열)")
            raise SystemExit(0)

        keyword = row[0].strip()   # A열 = 키워드
        label_val = row[1].strip() if len(row) > 1 else ""  # B열 = 라벨
        sheet_append_log(ws9, target_row, f"대상 행={target_row}, 키워드='{keyword}', 라벨='{label_val}'")

        # 2) 제목 생성
        title = make_post_title(keyword)
        sheet_append_log(ws9, target_row, f"제목='{title}'")

        # 3) 썸네일 생성 & 업로드
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
        sheet_append_log(ws9, target_row, "썸네일 생성 시작")
        thumb_url = make_thumb_with_logging(ws9, target_row, thumb_path, title)
        sheet_append_log(ws9, target_row, f"썸네일 결과: {thumb_url or '실패'}")

        # 4) 앱 ID 검색
        sheet_append_log(ws9, target_row, "앱 ID 검색 시작")
        eng_keyword = row[3].strip() if len(row) > 3 else ""  # D열 = 영어 키워드
        apps = search_app_store_ids(keyword, limit=20, eng_keyword=eng_keyword)

        # ✅ 앱이 없으면 종료
        if not apps:
            sheet_append_log(ws9, target_row, "앱 ID 없음 → 종료")
            ws9.update_cell(target_row, 5, "OK")      # E열 완료
            ws9.update_cell(target_row, 7, "")        # G열 비움
            sheet_append_log(ws9, target_row, "시트 기록 완료: E='OK', G='' (검색 결과 없음)")
            raise SystemExit(0)

        # ✅ 앱 3개 미만 → 완료 처리
        if len(apps) < 3:
            sheet_append_log(ws9, target_row, "앱 개수 3개 미만 → 완료 처리")
            ws9.update_cell(target_row, 5, "OK")
            ws9.update_cell(target_row, 7, "")
            sheet_append_log(ws9, target_row, "시트 기록 완료: E='OK', G='' (앱 수 부족)")
            raise SystemExit(0)

        # ✅ 앱 충분 → 로그 기록
        sheet_append_log(ws9, target_row, f"앱 ID 목록={[(a['id'], a['name']) for a in apps]}")

        # 5) 서론
       
        html_full = build_css_block()
        html_full += build_intro_block(title, keyword)  # 베트남어
        html_full += """
        <div class="mbtTOC"><button>Mục lục</button>
        <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
        </div>
        <p>&nbsp;</p>
        """
        sheet_append_log(ws9, target_row, "인트로 생성 완료")

        html_full += """
        <div class="mbtTOC"><button>Mục lục</button>
        <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
        </div>
        <p>&nbsp;</p>
        """
        sheet_append_log(ws9, target_row, "인트로 생성 완료")

        # 6) 썸네일 본문 삽입
        if thumb_url:
            html_full += f"""
<p style="text-align:center;">
  <img src="{thumb_url}" alt="{keyword} hình thu nhỏ" style="max-width:100%; height:auto; border-radius:10px;">
</p><br /><br />
"""
            sheet_append_log(ws9, target_row, "본문에 썸네일 삽입")
        else:
            sheet_append_log(ws9, target_row, "썸네일 없음")

        # 7) 해시태그
        tag_items = title.split()
        tag_str = " ".join([f"#{t}" for t in tag_items]) + " #AppStore"
        sheet_append_log(ws9, target_row, f"해시태그='{tag_str}'")

        # 8) 앱 상세 수집 및 본문 조립
        for j, app in enumerate(apps, 1):
            if j > 7:  # 최대 7개
                break
            try:
                sheet_append_log(ws9, target_row, f"[{j}] 앱 수집 시작 id={app['id']}")
                detail = fetch_app_detail(app["id"], country="vi")  # 베트남 국가 코드
                app_url = detail["url"]
                app_name = detail["name"]
                src_html = detail["desc_html"]
                images = detail["images"]

                desc_html = rewrite_app_description(src_html, app_name, keyword)
                sheet_append_log(ws9, target_row, f"[{j}] {app_name} 설명 리라이트 완료")

                img_group_html = "".join(
                    f'<div class="img-wrap"><img src="{img_url}" alt="{app_name}_{cc}"></div>'
                    for cc, img_url in enumerate(images, 1)
                )

                section_html = f"""
                <h2 data-ke-size="size26">{j}. {app_name} — Giới thiệu ứng dụng</h2>
                <br />
                {desc_html}
                <p data-ke-size="size18"><b>Ảnh màn hình {app_name}</b></p>
                <div class="img-group">{img_group_html}</div>
                <br />
                <p data-ke-size="size18" style="text-align:center;">
                  <a href="{app_url}" class="myButton">Tải {app_name}</a>
                </p>
                <br />
                <p data-ke-size="size18">{tag_str}</p>
                <br /><br />
                """
                html_full += section_html
                sheet_append_log(ws9, target_row, f"[{j}] {app_name} 섹션 완료")
            except Exception as e_each:
                sheet_append_log(ws9, target_row, f"[{j}] 앱 처리 실패: {e_each}")

        # 9) 마무리
        html_full += build_ending_block(title, keyword)
        sheet_append_log(ws9, target_row, "엔딩 생성 완료")
        related_box = get_related_posts(BLOG_ID, count=6)
        html_full += related_box
        html_full += "<script>mbtTOC();</script><br /><br />"

        # 10) 업로드
        try:
            labels = make_post_labels(row)
            post_body = {"content": html_full, "title": title, "labels": labels}
            res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body,
                                              isDraft=False, fetchImages=True).execute()
            post_url = res.get("url", "")
            sheet_append_log(ws9, target_row, f"업로드 성공: {post_url}")
        except Exception as up_e:
            sheet_append_log(ws9, target_row, f"업로드 실패: {up_e}")
            raise

        # 11) 시트 기록
        ws9.update_cell(target_row, 5, "OK")
        ws9.update_cell(target_row, 7, post_url)
        sheet_append_log(ws9, target_row, f"시트 기록 완료: E='OK', G='{post_url}'")

        # 12) 완료
        sheet_append_log(ws9, target_row, "정상 종료")

    except SystemExit:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        row_for_err = target_row if 'target_row' in locals() and target_row else 2
        sheet_append_log(ws9, row_for_err, f"실패: {e}")
        sheet_append_log(ws9, row_for_err, f"Trace: {tb.splitlines()[-1]}")
        print("실패:", e, tb)





















