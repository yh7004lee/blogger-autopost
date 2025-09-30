import sys
sys.stdout.reconfigure(encoding="utf-8")
import os, re, json, random, requests, traceback, pickle, glob, textwrap
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from PIL import Image, ImageDraw, ImageFont
import urllib.parse

# ================================
# 환경 변수 및 기본 설정
# ================================
SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")

# ✅ 블로그 고정 (베트남 버전)
BLOG_ID = "7550707353079627944"
BLOG_URL = "https://appvn.appsos.kr/"

# ================================
# OpenAI API Key 로드
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
# Google Sheets 인증 (시트9 사용)
# A열: 베트남어 키워드 / B열: 카테고리
# ================================
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).get_worksheet(8)  # index=8 → 9번째 시트

ws = get_sheet()

# ================================
# 추천글 박스 (베트남 버전)
# ================================
def get_related_posts(blog_id, count=6):
    import feedparser, random
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    # 랜덤으로 count개 추출
    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    # HTML 박스 생성 (베트남어 문구 적용)
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151);
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold;
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16"
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px;
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ Hãy xem thêm những bài viết hữu ích này</p>
"""
    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box

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
# 제목 생성 (G1 인덱스 활용, 베트남어 패턴)
# ================================
def make_rotating_title(ws, keyword: str) -> str:
    front_choices = ["Điện thoại", "Ứng dụng Android", "Smartphone"]
    back_choices = ["Ứng dụng đề xuất", "Ứng dụng hay nhất"]

    # G1 셀에서 인덱스 불러오기 (없으면 0)
    try:
        idx_val = ws.cell(1, 7).value
        idx = int(idx_val) if idx_val else 0
    except:
        idx = 0

    # 로테이션
    front = front_choices[idx % len(front_choices)]
    back = back_choices[(idx // len(front_choices)) % len(back_choices)]

    # 다음 인덱스 저장
    ws.update_cell(1, 7, str(idx + 1))

    # 예: Điện thoại {keyword} Ứng dụng đề xuất
    return f"{front} {keyword} {back}"

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
# 앱 이미지 4개 추출 (구글플레이 상세 페이지)
# ================================
def get_app_images(soup, app_name: str):
    images_html = ""
    try:
        # 스크린샷 영역 (role=list)
        img_div = soup.find("div", attrs={"role": "list"})
        imgs = img_div.find_all("img") if img_div else []
        for cc, img in enumerate(imgs[:4], 1):   # 최대 4장
            img_url = img.get("srcset") or img.get("src")
            if not img_url:
                continue
            # srcset이면 가장 큰 해상도 추출
            if "," in img_url:
                img_url = img_url.split(",")[-1].strip()
            img_url = img_url.split()[0]

            # 해상도 업스케일 (가끔 wXXX-hYYY-rw 패턴을 크게 치환)
            import re
            img_url = re.sub(r"w\d+-h\d+-rw", "w2048-h1100-rw", img_url)

            images_html += f"""
            <div class="img-wrap">
              <img src="{img_url}" alt="{app_name}_{cc}" style="border-radius:10px;">
            </div>
            """
    except Exception as e:
        print(f"[이미지 수집 오류] {e}")
    return images_html


# ================================
# 배경 이미지 랜덤 선택
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join("assets/backgrounds", ext)))
    return random.choice(files) if files else ""


# ================================
# 썸네일 생성 (베트남 전용 폰트 적용, 안전한 줄바꿈)
# ================================
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        # ✅ 베트남어 전용 폰트 적용
        try:
            font = ImageFont.truetype("assets/fonts/BeVietnamPro-SemiBold.ttf", 48)
        except:
            font = ImageFont.load_default()

        canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
        canvas.paste(bg, (0, 0))

        # 검은 반투명 박스
        rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
        canvas.paste(rectangle, (0, 125), rectangle)

        draw = ImageDraw.Draw(canvas)

        # ✅ 실제 픽셀 기반 줄바꿈 함수
        def wrap_text(text, font, max_width):
            lines = []
            line = ""
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

        # 🔹 텍스트를 460픽셀 기준으로 줄바꿈 (500 여백 고려)
        var_title_wrap = wrap_text(var_title, font, max_width=460)

        bbox = font.getbbox("A")
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
        print(f"[에러] 썸네일 생성 실패: {e}")
        return False
# ================================
# Google Drive 업로드 (베트남용)
# ================================
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        folder_id = DRIVE_FOLDER_ID

        # 기본 폴더 설정 (없으면 "blogger" 폴더 자동 생성)
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

        # 파일 업로드
        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id"
        ).execute()

        # 공개 권한 부여
        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        return f"https://lh3.googleusercontent.com/d/{file['id']}"
    except Exception as e:
        print(f"[Lỗi] Google Drive tải lên thất bại: {e}")
        return ""


# ================================
# 썸네일 생성 + 로그 기록 + 업로드 → URL 반환 (베트남용)
# ================================
def make_thumb_with_logging(ws, row_idx, save_path, title):
    try:
        log_thumb_step(ws, row_idx, "Bắt đầu tạo thumbnail")
        ok = make_thumb(save_path, title)
        if ok:
            log_thumb_step(ws, row_idx, "Hoàn thành thumbnail")
            url = upload_to_drive(save_path, os.path.basename(save_path))
            if url:
                log_thumb_step(ws, row_idx, "Tải lên hoàn tất")
                return url
            else:
                log_thumb_step(ws, row_idx, "Tải lên thất bại")
                return ""
        else:
            log_thumb_step(ws, row_idx, "Tạo thumbnail thất bại")
            return ""
    except Exception as e:
        log_thumb_step(ws, row_idx, f"[Lỗi]{e}")
        return ""

# ================================
# OpenAI GPT 처리 (베트남 블로그 글용)
# ================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    if not client:
        return original_html
    compact = BeautifulSoup(original_html, 'html.parser').get_text(separator=' ', strip=True)
    system_msg = (
        "Bạn là một blogger chuyên nghiệp, tạo nội dung bằng tiếng Việt. "
        "Viết lại nội dung dựa trên thông tin thực tế nhưng giữ văn phong tự nhiên, mạch lạc và thân thiện. "
        "Nội dung dễ hiểu và thu hút người đọc. "
        "Kết quả phải được xuất dưới dạng <p data-ke-size='size18'> từng đoạn văn."
    )
    user_msg = f"[Tên ứng dụng] {app_name}\n[Từ khóa] {keyword_str}\n\n{compact}"
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.7,
            max_tokens=700
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Lỗi] Xử lý GPT thất bại: {e}")
        return original_html
# ================================
# 서론 · 결론 랜덤 (SEO 최적화 + 문장 확장, 베트남어 버전)
# ================================
intro_start = [
    "Ngày nay, chỉ với một chiếc điện thoại thông minh, bạn có thể thực hiện nhiều công việc một cách dễ dàng. ",
    "Bằng cách kết hợp điện thoại và ứng dụng, cuộc sống trở nên thuận tiện hơn nhiều. ",
    "Điện thoại thông minh đã trở thành vật dụng không thể thiếu, vượt xa việc giao tiếp thông thường. ",
    "Sự phát triển của thế giới di động giúp các ứng dụng trở nên cực kỳ tiện lợi trong cuộc sống hàng ngày. ",
    "Từ nghiên cứu đến công việc, từ giáo dục đến giải trí, mọi thứ đều có thể thực hiện trên điện thoại. ",
    "Chỉ với một thiết bị trong tay, bạn có thể nâng cao chất lượng cuộc sống. ",
    "Nhờ các ứng dụng dễ sử dụng, cuộc sống hàng ngày trở nên năng động và đơn giản hơn. ",
    "Khi chọn đúng ứng dụng, điện thoại sẽ trở thành trợ thủ đắc lực thực sự. ",
    "Ưu điểm lớn nhất của các ứng dụng là luôn tiếp cận được thông tin và giải trí."
]

intro_middle = [
    "Chúng cung cấp các chức năng hữu ích trong cuộc sống hàng ngày và tăng tính tiện dụng đáng kể.",
    "Giúp tiết kiệm thời gian và hỗ trợ đưa ra quyết định tốt hơn trong các tình huống khác nhau.",
    "Được sử dụng trong công việc, giáo dục và giải trí, trở thành công cụ không thể thiếu cho mọi lứa tuổi.",
    "Mang đến những trải nghiệm mới và mở rộng khả năng vượt xa sự tiện lợi thông thường.",
    "Nhờ các ứng dụng đa dạng và trực quan, sự hài lòng của người dùng ngày càng tăng.",
    "Tạo ra môi trường mà thông tin và giải trí luôn trong tầm tay bạn.",
    "Theo dõi các xu hướng mới, các ứng dụng phát triển nhanh chóng.",
    "Nhiều ứng dụng miễn phí mang chất lượng đáng ngạc nhiên và dễ thử nghiệm.",
    "Khi sử dụng đúng cách, các vấn đề nhỏ trong cuộc sống hàng ngày có thể giải quyết dễ dàng."
]

intro_end = [
    "Trong bài viết này, chúng tôi đã tổng hợp các ứng dụng phổ biến và hữu ích mà bạn cần biết.",
    "Chúng tôi sẽ giới thiệu các ứng dụng thực tế, tiện lợi và có điểm đánh giá cao cho việc sử dụng hàng ngày.",
    "Chúng tôi đã chọn các ứng dụng phổ biến và giải thích cách tận dụng chúng tốt nhất.",
    "Để giúp bạn dễ dàng lựa chọn, các ứng dụng cần thiết được trình bày một cách có tổ chức.",
    "Bạn sẽ thấy các ứng dụng đáng tin cậy và hữu ích, cải thiện thói quen hàng ngày của mình.",
    "Chúng tôi tập trung vào các ứng dụng đáng tin cậy và giải thích các tính năng cơ bản.",
    "Chia sẻ các ứng dụng được tìm kiếm nhiều nhất kèm theo trải nghiệm thực tế của người dùng.",
    "Chúng tôi đã chọn các ứng dụng không thể thiếu mà bạn nên cài đặt trên điện thoại."
]

def make_intro(title, keyword):
    intro = random.choice(intro_start) + random.choice(intro_middle) + " " + random.choice(intro_end)
    return f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">
{intro}
Trong bài viết này, chúng tôi tập trung vào các ứng dụng liên quan đến “{keyword}”.
Các lựa chọn dựa trên kết quả tìm kiếm hàng đầu trên Google Play khi tìm kiếm “{keyword}”.
Nếu bạn là người dùng điện thoại thông minh, hãy xem qua những lựa chọn tiện lợi này và tận dụng đúng lúc.
</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
"""

end_start = [
    "Chúng tôi hy vọng các ứng dụng được giới thiệu sẽ làm cho cuộc sống hàng ngày của bạn thuận tiện và thú vị hơn.",
    "Chúng tôi mong rằng bộ sưu tập ứng dụng này sẽ hữu ích trong nhiều tình huống khác nhau.",
    "Chúng tôi không chỉ chú ý đến chức năng mà còn đến cách sử dụng thực tế của ứng dụng.",
    "Bằng cách sử dụng các ứng dụng được đề xuất, thói quen hàng ngày của bạn sẽ trở nên hiệu quả hơn.",
    "Hãy thử ứng dụng bạn quan tâm nhất và tìm ra ứng dụng phù hợp với bạn."
]

end_summary = [
    "Chúng tôi tóm tắt điểm mạnh và lợi ích của từng ứng dụng để giúp bạn dễ dàng lựa chọn.",
    "Chúng tôi trình bày các tính năng nổi bật của mỗi ứng dụng một cách rõ ràng và so sánh.",
    "Dựa trên đánh giá thực tế của người dùng để đảm bảo lựa chọn an toàn.",
    "Chỉ đề xuất các ứng dụng đáng tin cậy và phổ biến.",
    "Để đáp ứng các nhu cầu khác nhau, chúng tôi bao gồm cả ứng dụng miễn phí và trả phí."
]

end_next = [
    "Chúng tôi sẽ tiếp tục chia sẻ các xu hướng và ứng dụng mới nhất.",
    "Trong các bài viết tiếp theo, bạn sẽ tìm thấy thêm các đề xuất ứng dụng hữu ích và thú vị.",
    "Các tính năng mới và ứng dụng nổi bật sẽ sớm được cập nhật tại đây.",
    "Chúng tôi sẽ tiếp tục giới thiệu các ứng dụng giúp cải thiện thói quen hàng ngày.",
    "Cập nhật thường xuyên các mẹo và thông tin hữu ích về cách sử dụng ứng dụng."
]

end_action = [
    "Nếu bạn thấy nội dung hữu ích, hãy để lại bình luận và nhấn thích bài viết.",
    "Ý kiến của bạn rất quan trọng, hãy chia sẻ suy nghĩ của bạn trong phần bình luận.",
    "Nếu thấy hữu ích, hãy chia sẻ với bạn bè và gia đình.",
    "Phản hồi của bạn giúp chúng tôi cải thiện nội dung hơn nữa.",
    "Theo dõi chúng tôi để nhận thông báo về các bài viết mới."
]

end_greet = [
    "Cảm ơn bạn đã đọc đến cuối! Chúc bạn một ngày tuyệt vời!",
    "Cảm ơn đã đọc, hy vọng cuộc sống của bạn trở nên thuận tiện và hạnh phúc hơn!",
    "Chúng tôi sẽ sớm chia sẻ nhiều nội dung hữu ích hơn, hãy tiếp tục theo dõi!",
    "Cảm ơn bạn đã theo dõi blog, hẹn gặp lại trong bài viết tiếp theo!",
    "Chúc bạn một ngày tuyệt vời đầy thành công và niềm vui!"
]

def make_last(title):
    return f"""
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
# 앱 크롤링 (베트남 블로그용, 시트9 전용)
# ================================
def crawl_apps(keyword, lang="vi", country="VN"):
    url = f"https://play.google.com/store/search?q={keyword}&c=apps&hl={lang}&gl={country}"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(resp.text, "html.parser")
    source = soup.find_all(class_="ULeU3b")
    app_links = []
    for k, s in enumerate(source):
        if k == 15: 
            break
        a = s.find("a")
        if a: 
            app_links.append("https://play.google.com" + a["href"])
    return app_links[3:]

# ================================
# 메인 실행 (시트9 기반, 베트남 블로그)
# ================================
try:
    rows = ws.get_all_values()
    target_row, keyword, label, title = None, None, None, None

    # ✅ 대상 행 찾기 (A열=키워드, F열 != "OK")
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        kw = row[0].strip() if len(row) > 0 else ""   # A열: 키워드
        lb = row[1].strip() if len(row) > 1 else ""   # B열: 라벨
        done = row[5].strip() if len(row) > 5 else "" # F열: 완료 플래그
        if kw and done != "OK":
            target_row, keyword, label = i, kw, lb
            title = make_rotating_title(ws, keyword)
            break

    if not keyword:
        print("Không có từ khóa để xử lý.")
        exit()

    print(f"👉 Bài viết lần này: {title} (Nhãn={label})")

    # ✅ 썸네일 생성
    thumb_dir = "thumbnails"
    os.makedirs(thumb_dir, exist_ok=True)
    thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
    img_url = make_thumb_with_logging(ws, target_row, thumb_path, title)

    html = make_intro(title, keyword)

    # ✅ 스크린샷 레이아웃 스타일
    html += """
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

    # ✅ 자동 목차 (서론 직후)
    html += """
    <div class="mbtTOC"><button>Mục lục</button>
    <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
    </div>
    <p>&nbsp;</p>
    """

    if img_url:
        html += f"""
        <p style="text-align:center;">
          <img src="{img_url}" alt="{keyword} thumbnail" style="max-width:100%; height:auto; border-radius:10px;">
        </p>
        <br /><br />
        """

    # ✅ 앱 크롤링
    app_links = crawl_apps(keyword)
    print(f"Số lượng liên kết ứng dụng thu thập: {len(app_links)}")

    if len(app_links) < 3:
        print("⚠️ Số lượng ứng dụng ít hơn 3 → đánh dấu hoàn tất")
        ws.update_cell(target_row, 6, "OK")
        exit()

    # ✅ 본문 생성
    tag_str = " ".join([f"#{t}" for t in title.split()])
    for j, app_url in enumerate(app_links, 1):
        if j > 7:
            break
        resp = requests.get(app_url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")

        # 앱 제목
        h1 = soup.find("h1").text if soup.find("h1") else f"Ứng dụng {j}"

        # 앱 설명
        raw_desc = str(soup.find("div", class_="fysCi")) if soup.find("div", class_="fysCi") else ""
        desc = rewrite_app_description(raw_desc, h1, keyword)

        # ✅ 앱 스크린샷 4장
        images_html = get_app_images(soup, h1)

        # ✅ 라벨 링크 추가 (1번째, 3번째 제목 위)
        if j in (1, 3) and label:
            encoded_label = urllib.parse.quote(label)
            link_block = f"""
            <div class="ottistMultiRelated">
              <a class="extL alt" href="{BLOG_URL}search/label/{encoded_label}?&max-results=10">
                <span style="font-size: medium;"><strong>Xem thêm bài viết về {label}</strong></span>
                <i class="fas fa-link 2xs"></i>
              </a>
            </div>
            <br /><br /><br />
            """
            html += link_block

        # ✅ 제목+본문+스크린샷
        html += f"""
        <h2 data-ke-size="size26">{j}. {h1} — Giới thiệu ứng dụng</h2>
        <br />
        {desc}
        <br />
        <p data-ke-size="size18"><b>Ảnh chụp màn hình: {h1}</b></p>
        <div class="img-group">{images_html}</div>
        <br />
        <p style="text-align: center;" data-ke-size="size18">
          <a class="myButton" href="{app_url}">Tải {h1}</a>
        </p><br /><br />
        <p data-ke-size="size18">{tag_str}</p>
        <br /><br /><br />
        """

    html += make_last(title)

    # ✅ 관련 글 박스 삽입
    related_box = get_related_posts(BLOG_ID, count=6)
    html += related_box

    # ✅ 자동 목차 스크립트
    html += "<script>mbtTOC();</script><br /><br />"

    # ✅ Blogger 업로드 (고정 BLOG_ID + 라벨=B열)
    labels = [label, "Android"] if label else ["Android"]
    post_body = {
        "content": html,
        "title": title,
        "labels": labels
    }
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False).execute()
    url = res.get("url", "")
    print(f"✅ Upload thành công: {url}")

    # ✅ 시트 업데이트
    ws.update_cell(target_row, 6, "OK")   # F열: 완료 플래그
    ws.update_cell(target_row, 10, url)   # J열: 포스팅 URL 기록

except Exception as e:
    tb = traceback.format_exc()
    print("Thất bại:", e)
    if target_row:
        ws.update_cell(target_row, 11, str(e))  # K열: 에러 메시지 기록















