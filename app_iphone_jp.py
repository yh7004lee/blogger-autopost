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

# 블로그 ID / URL (일본 버전으로 고정)
BLOG_ID = "7573892357971022707"
BLOG_URL = "https://jpapp.appsos.kr/"

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
        OPENAI_API_KEY = ""
if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None

# =============== Google Sheets 인증 (sheet4 사용) ===============
def get_sheet4():
    # 서비스 계정 인증
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws4 = sh.worksheet("sheet4")   # 시트 이름이 'sheet4'인 경우
    except Exception:
        ws4 = sh.get_worksheet(3)      # 0부터 시작 → 네 번째 탭
    return ws4

ws4 = get_sheet4()

# =============== Google Drive 인증 ===============
def get_drive_service():
    # GitHub Actions 등에서 사용자 토큰을 pickle 로 저장해서 사용하는 경우를 가정
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

# =============== 썸네일 생성 ===============
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        # 랜덤 배경 선택
        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        # 폰트 설정 (일본어 지원 폰트로 교체 필요할 수 있음)
        try:
            font = ImageFont.truetype(os.path.join("assets", "fonts", "NotoSansJP-VariableFont_wght.ttf"), 48)
        except Exception:
            font = ImageFont.load_default()

        # 캔버스 생성
        canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
        canvas.paste(bg, (0, 0))

        # 텍스트 배경 박스
        rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
        canvas.paste(rectangle, (0, 125), rectangle)

        # 텍스트 그리기
        draw = ImageDraw.Draw(canvas)

        var_title_wrap = textwrap.wrap(var_title, width=12)
        bbox = font.getbbox("가")  # 기준 글자
        line_height = (bbox[3] - bbox[1]) + 12
        total_text_height = len(var_title_wrap) * line_height
        y = 500 / 2 - total_text_height / 2

        for line in var_title_wrap:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            x = (500 - text_width) / 2
            draw.text((x, y), line, "#FFEECB", font=font)
            y += line_height

        # 크기 조정 후 저장
        canvas = canvas.resize((400, 400))
        canvas.save(save_path, "PNG")
        return True
    except Exception as e:
        print(f"에러: 썸네일 생성 실패: {e}")
        return False

# =============== Google Drive 업로드 → 공개 URL 반환 ===============
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

        # 공개 권한 부여
        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        # ✅ Google CDN(lh3) 주소 반환
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
    # 일본어 버전 제목 구성
    front_choices = ["iPhone iPad", "iPad iPhone"]
    back_choices = ["アプリ おすすめ", "おすすめ アプリ", "アプリ AppStore", "AppStore アプリ"]
    return f"{random.choice(front_choices)} {keyword} {random.choice(back_choices)}"

def make_post_labels(sheet_row: list) -> list:
    # 항상 "アプリ" + 시트 B열 라벨
    label_val = sheet_row[1].strip() if len(sheet_row) > 1 and sheet_row[1] else ""
    return ["アプリ", label_val] if label_val else ["アプリ"]


# =============== OpenAI GPT 재작성 (앱 설명) ===============
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = BeautifulSoup(original_html or "", 'html.parser').get_text(separator=' ', strip=True)
    if not client:
        if compact:
            return "".join([f"<p data-ke-size='size18'>{line.strip()}</p>" for line in compact.splitlines() if line.strip()]) or f"<p data-ke-size='size18'>{app_name} 紹介</p>"
        return f"<p data-ke-size='size18'>{app_name} 紹介</p>"

    system_msg = (
        "あなたは日本語のブログ記事を書くコピーライターです。"
        "内容の事実は保持しつつ、文章や構成を完全にリライトしてください。"
        "人間が書いたように自然で温かいトーンでお願いします。"
        "Markdownは禁止、<p data-ke-size='size18'> タグのみ使用してください。"
        "必ず3〜4つの段落に分けて、各段落は <p data-ke-size='size18'> タグを使用してください。"
    )
    user_msg = (
        f"[アプリ名] {app_name}\n"
        f"[キーワード] {keyword_str}\n"
        "以下の原文を参考に、ブログ用の紹介文を新しく日本語で書いてください。\n\n"
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
        return f"<p data-ke-size='size18'>{app_name} 紹介</p>"


# =============== 앱스토어 앱 ID 추출 (iTunes Search API, 일본) ===============
def search_app_store_ids(keyword, limit=10, country="jp"):
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


# =============== 앱 상세 페이지 수집 (이름/설명/스크린샷, 일본) ===============
def fetch_app_detail(app_id: str, country="jp"):
    import html
    url = f"https://apps.apple.com/{country}/app/id{app_id}"
    name = f"アプリ {app_id}"
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

        # 스크린샷 수집
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

# =============== 서론 블록 ===============
def build_intro_block(title: str, keyword: str) -> str:
    intro_groups = [
        [
            f"スマートフォンは今や単なる通信手段を超え、私たちの生活全般を支える必需品となっています。",
            f"手のひらサイズのデバイス一つで『{keyword}』のような多彩な機能を楽しめる時代になりました。",
            f"現代社会において『{keyword}』アプリは欠かせない便利ツールとして定着しています。",
            f"特に『{title}』のようなテーマは、多くの方が気になる話題の一つです。",
            f"スマートフォン技術の進化に伴い『{keyword}』関連アプリの活用度もますます高まっています。",
            f"誰もが利用するスマートフォンを通じて『{keyword}』をより便利に楽しめます。"
        ],
        [
            f"多様なアプリが登場し『{keyword}』アプリの選択肢も広がっています。",
            f"『{title}』を探す方が増えるほど注目度も高まっています。",
            f"生活、学習、趣味、そして『{keyword}』までもアプリで簡単に楽しめます。",
            f"スマホアプリは時間を節約し、効率的なライフスタイルを可能にします。",
            f"『{keyword}』アプリはユーザーに新しい体験と利便性を同時に提供します。",
            f"毎日のように新しい『{keyword}』アプリが登場し、選ぶ楽しさも増えています。"
        ],
        [
            f"例えば仕事効率を高めるアプリから『{keyword}』を楽しめるエンタメ系まで種類は豊富です。",
            f"『{title}』は多くの人に人気のカテゴリの一つです。",
            f"ゲームやエンターテインメントと並び『{keyword}』アプリは余暇を豊かにしてくれます。",
            f"ショッピング、金融、交通と同じく『{keyword}』アプリも生活に欠かせない存在です。",
            f"写真や動画と一緒に『{keyword}』コンテンツを管理できるアプリも多くあります。",
            f"コミュニケーションアプリに負けないくらい『{keyword}』アプリも注目を集めています。"
        ],
        [
            f"このように『{keyword}』アプリは単なる機能を超え、生活全般を変える力を持っています。",
            f"『{title}』を活用することで、暮らしの質がさらに向上するでしょう。",
            f"必要なときに『{keyword}』アプリで欲しい機能をすぐに利用できます。",
            f"便利さだけでなく『{keyword}』アプリは新しい体験も提供してくれます。",
            f"多くの人が『{keyword}』アプリのおかげでよりスマートな生活を楽しんでいます。",
            f"『{keyword}』アプリ一つが生活スタイル全体を変えることもあります。"
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
            f"今回ご紹介した『{title}』関連アプリが皆さんのスマホライフに役立てば幸いです。",
            f"本記事でまとめた『{title}』アプリが日常生活で便利に活用されることを願っています。",
            f"今回取り上げた『{title}』関連アプリがスマートな選択に役立つことを期待しています。",
            f"本記事で紹介した『{title}』アプリが皆様の生活に欠かせないツールとなれば嬉しいです。",
            f"『{title}』に関心のある方にとって今回のまとめが有意義な時間となれば幸いです。",
            f"さまざまな『{keyword}』アプリを見てきたことで、スマホ活用がさらに豊かになるでしょう。"
        ],
        [
            f"各アプリの機能や特徴をしっかり解説しましたので『{keyword}』アプリ選びの参考にしてください。",
            f"アプリの特徴や長所・短所を比較しましたので『{title}』選びにきっと役立つはずです。",
            f"今回のまとめをもとに、自分に合った『{keyword}』アプリを見つけていただければと思います。",
            f"必要なときにすぐ使えるよう、重要な情報を整理しましたのでぜひ参考にしてください。",
            f"これから『{keyword}』アプリを選ぶ際に本記事が心強いガイドになるでしょう。",
            f"複数のアプリを比較したことで、より賢い選択に近づけたのではないでしょうか。"
        ],
        [
            "今後もさまざまなアプリ情報を準備してお届けします。",
            f"これからも『{keyword}』に関する役立つ情報やおすすめアプリを紹介していきます。",
            "読者の皆様のご意見を反映し、より有益な記事をお届けできるよう努めます。",
            "引き続き新しいアプリや注目の機能を紹介していく予定です。",
            "これからも必要とされる実用的な情報を継続的に発信していきます。",
            f"『{title}』のように注目されるテーマをこれからも積極的に扱っていきます。"
        ],
        [
            "コメントやいいねは大きな励みになります。気軽に参加していただけると嬉しいです。",
            "ご質問やご意見があればぜひコメントでお知らせください。積極的に反映していきます。",
            "皆様のフィードバックはより良い記事作りに欠かせない力となります。",
            "いいねやコメントで応援していただければ、さらに充実した情報をお届けします。",
            "気になるアプリや機能があればぜひコメントで教えてください。参考にして取り上げます。",
            f"『{keyword}』アプリに関する皆様の考えも、ぜひコメントで自由に共有してください。"
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
# 관련 추천글 박스 (RSS 랜덤 4개)
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
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ 関連おすすめ記事</p>
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
        if a and d != "完":  # 일본 버전에서는 완료 표시를 '完'으로 기록
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
        # 1) sheet4에서 대상 행/데이터
        target_row, row = pick_target_row(ws4)
        if not target_row or not row:
            sheet_append_log(ws4, 2, "処理するキーワードがありません(A列)")
            raise SystemExit(0)

        keyword = row[0].strip()  # A열 = 키워드
        label_val = row[1].strip() if len(row) > 1 else ""  # B열 = 라벨

        sheet_append_log(ws4, target_row, f"対象行={target_row}, キーワード='{keyword}', ラベル='{label_val}'")

        # 2) 제목 생성
        title = make_post_title(keyword)
        sheet_append_log(ws4, target_row, f"タイトル='{title}'")

        # 3) 썸네일 생성 & 업로드
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
        sheet_append_log(ws4, target_row, "サムネイル生成開始")
        thumb_url = make_thumb_with_logging(ws4, target_row, thumb_path, title)
        sheet_append_log(ws4, target_row, f"サムネイル結果: {thumb_url or '失敗'}")

        # 4) 앱 ID 목록 검색
        sheet_append_log(ws4, target_row, "アプリID検索開始")
        apps = search_app_store_ids(keyword, limit=10)
        if not apps:
            sheet_append_log(ws4, target_row, "アプリIDなし → 終了")
            raise SystemExit(0)
        sheet_append_log(ws4, target_row, f"アプリID={[(a['id'], a['name']) for a in apps]}")

        # 5) 서론
        html_full = build_css_block()
        html_full += build_intro_block(title, keyword)
        # ✅ 목차 블록 추가
        html_full += """
        <div class="mbtTOC"><button> 目次 </button>
        <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
        </div>
        <p>&nbsp;</p>
        """
        sheet_append_log(ws4, target_row, "イントロ生成完了")

        # 6) 썸네일 본문 삽입
        if thumb_url:
            html_full += f"""
<p style="text-align:center;">
  <img src="{thumb_url}" alt="{keyword} サムネイル" style="max-width:100%; height:auto; border-radius:10px;">
</p><br /><br />
"""
            sheet_append_log(ws4, target_row, "本文にサムネイル挿入")
        else:
            sheet_append_log(ws4, target_row, "サムネイルなし")

        # 7) 해시태그
        tag_items = title.split()
        tag_str = " ".join([f"#{t}" for t in tag_items]) + " #AppStore"
        sheet_append_log(ws4, target_row, f"ハッシュタグ='{tag_str}'")

        # 8) 앱 상세 수집 → 본문 조립
        for j, app in enumerate(apps, 1):
            if j > 7:  # 일본 버전은 7개까지
                break
            try:
                sheet_append_log(ws4, target_row, f"[{j}] アプリ収集開始 id={app['id']}")
                detail = fetch_app_detail(app["id"])
                app_url = detail["url"]
                app_name = detail["name"]
                src_html = detail["desc_html"]
                images = detail["images"]

                desc_html = rewrite_app_description(src_html, app_name, keyword)
                sheet_append_log(ws4, target_row, f"[{j}] {app_name} 説明リライト成功")

                img_group_html = "".join(
                    f'<div class="img-wrap"><img src="{img_url}" alt="{app_name}_{cc}"></div>'
                    for cc, img_url in enumerate(images, 1)
                )

                section_html = f"""
                <h2 data-ke-size="size26">{j}. {app_name} アプリ紹介</h2>
                <br />
                {desc_html}
                <p data-ke-size="size18"><b>2) {app_name} スクリーンショット</b></p>
                <div class="img-group">{img_group_html}</div>
                <br />
                <p data-ke-size="size18" style="text-align:center;">
                  <a href="{app_url}" class="myButton">{app_name} ダウンロード</a>
                </p>
                <br />
                <p data-ke-size="size18">{tag_str}</p>
                <br /><br />
                """
                # ✅ 3번째 섹션이면 라벨 기반 추천 박스 삽입
                if j == 2 and label_val:
                    encoded_label = urllib.parse.quote(label_val)
                    section_html += f"""
                <div class="ottistMultiRelated">
                  <a class="extL alt" href="{BLOG_URL}search/label/{encoded_label}?&max-results=10">
                    <span style="font-size: medium;"><strong>おすすめ {label_val} アプリを見る</strong></span>
                    <i class="fas fa-link 2xs"></i>
                  </a>
                </div>
                <br /><br /><br />
                """
                
                html_full += section_html
                sheet_append_log(ws4, target_row, f"[{j}] {app_name} セクション完了")
            except Exception as e_each:
                sheet_append_log(ws4, target_row, f"[{j}] アプリ処理失敗: {e_each}")

        # 9) 마무리
        html_full += build_ending_block(title, keyword)
        sheet_append_log(ws4, target_row, "エンディング生成完了")
        related_box = get_related_posts(BLOG_ID, count=4)
        html_full += related_box
        # ✅ 자동 목차 스크립트 호출
        html_full += "<script>mbtTOC();</script>"

        # 10) 업로드
        try:
            labels = make_post_labels(row)  # ["アプリ", B열 값]
            post_body = {"content": html_full, "title": title, "labels": labels}
            res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body,
                                              isDraft=False, fetchImages=True).execute()
            post_url = res.get("url", "")
            sheet_append_log(ws4, target_row, f"アップロード成功: {post_url}")
        except Exception as up_e:
            sheet_append_log(ws4, target_row, f"アップロード失敗: {up_e}")
            raise

        # 11) 시트 기록
        ws4.update_cell(target_row, 4, "完")      # D열 완료
        ws4.update_cell(target_row, 7, post_url)  # G열 = URL
        sheet_append_log(ws4, target_row, f"シート記録完了: D='完', G='{post_url}'")

        # 12) 완료
        sheet_append_log(ws4, target_row, "正常終了")

    except SystemExit:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        row_for_err = target_row if 'target_row' in locals() and target_row else 2
        sheet_append_log(ws4, row_for_err, f"失敗: {e}")
        sheet_append_log(ws4, row_for_err, f"Trace: {tb.splitlines()[-1]}")
        print("失敗:", e, tb)










