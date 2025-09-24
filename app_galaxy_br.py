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

# ✅ 블로그 고정 (일본어 블로그용)
BLOG_ID = "7573892357971022707"
BLOG_URL = "https://jpapp.appsos.kr/"

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
# Google Sheets 인증 (시트4 사용)
# A열: 일본어 키워드 / B열: 카테고리 / D열: 영어 키워드
# ================================
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).get_worksheet(3)  # index=3 → 4번째 시트

ws = get_sheet()

# ================================
# 추천글 박스 (일본어 버전)
# ================================
def get_related_posts(blog_id, count=6):
    import feedparser, random
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    # 랜덤으로 count개 추출
    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    # HTML 박스 생성 (일본어 문구 적용)
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151);
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold;
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16"
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px;
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ 一緒に読むと役立つ記事</p>
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
# 제목 생성 (G1 인덱스 활용, 일본어 패턴)
# ================================
def make_rotating_title(ws, keyword: str) -> str:
    front_choices = ["スマホ", "携帯", "スマートフォン", "Android"]
    back_choices = ["アプリおすすめ", "おすすめアプリ"]

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

    # 예: スマホ {keyword} アプリおすすめ
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
# 배경 이미지 랜덤 선택
# ================================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join("assets/backgrounds", ext)))
    return random.choice(files) if files else ""

# ================================
# 썸네일 생성 (일본어 폰트 적용, 안전한 줄바꿈)
# ================================
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        # ✅ 일본어 폰트 적용
        try:
            font = ImageFont.truetype("assets/fonts/NotoSansJP-VariableFont_wght.ttf", 48)
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

        bbox = font.getbbox("あ")
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
        print(f"エラー: サムネイル生成失敗: {e}")
        return False

# ================================
# Google Drive 업로드
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
        print(f"エラー: Google Drive アップロード失敗: {e}")
        return ""

# ================================
# 썸네일 생성 + 로그 기록 + 업로드 → URL 반환
# ================================
def make_thumb_with_logging(ws, row_idx, save_path, title):
    try:
        log_thumb_step(ws, row_idx, "サムネイル開始")
        ok = make_thumb(save_path, title)
        if ok:
            log_thumb_step(ws, row_idx, "サムネイル完了")
            url = upload_to_drive(save_path, os.path.basename(save_path))
            if url:
                log_thumb_step(ws, row_idx, "アップロード完了")
                return url
            else:
                log_thumb_step(ws, row_idx, "アップロード失敗")
                return ""
        else:
            log_thumb_step(ws, row_idx, "サムネイル失敗")
            return ""
    except Exception as e:
        log_thumb_step(ws, row_idx, f"エラー:{e}")
        return ""

# ================================
# OpenAI GPT 처리 (일본어 블로그 글용)
# ================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    if not client:
        return original_html
    compact = BeautifulSoup(original_html, 'html.parser').get_text(separator=' ', strip=True)
    system_msg = (
        "あなたは日本語ブログ記事を書くコピーライターです。"
        "事実は維持しつつ、文体と構成を自然で親しみやすく書き直してください。"
        "文章は読みやすく温かいトーンにしてください。"
        "出力は必ず <p data-ke-size='size18'> の段落で構成してください。"
    )
    user_msg = f"[アプリ名] {app_name}\n[キーワード] {keyword_str}\n\n{compact}"
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
        print(f"エラー: GPT 処理失敗: {e}")
        return original_html

# ================================
# 序文・結論ランダム (SEO最適化 + 文章拡張)
# ================================
intro_start = [
    "スマートフォン一台で、さまざまな作業を快適にこなせる時代になりました。 ",
    "最近は、スマホとアプリの使い分けだけで、生活の多くを効率化できます。 ",
    "スマートフォンは通信手段を超えて、日常の必需品として活躍しています。 ",
    "モバイル環境の進化に合わせて、アプリは私たちの暮らしをさらに便利にします。 ",
    "検索から仕事、学習、エンタメまで、今やスマホで何でもできる時代です。 ",
    "手のひらサイズのデバイスをうまく活用すれば、生活の質をぐっと高められます。 ",
    "誰でも手軽に使えるアプリのおかげで、日常はよりスムーズになっています。 ",
    "目的に合ったアプリを選べば、スマホが頼れる相棒へと進化します。 ",
    "いつでもどこでも必要な情報や娯楽にアクセスできるのがアプリの魅力です。 "
]

intro_middle = [
    "日々のさまざまな場面で役立つ機能を提供し、利便性を大きく引き上げてくれます。",
    "時間を節約し、より良い選択をサポートしてくれるのがアプリの強みです。",
    "仕事や勉強、趣味まで幅広く活用でき、幅広い世代の必需品となっています。",
    "便利さに加えて新しい体験をもたらし、スマホ活用の幅を広げてくれます。",
    "誰でも直感的に使える良質なアプリが増え、満足度も高まっています。",
    "情報もエンタメも、思い立った時にすぐ楽しめる環境を作ってくれます。",
    "最新トレンドを反映したアプリは進化が早く、ユーザーの期待に応えます。",
    "無料でも十分使える優秀なアプリが多く、気軽に試せるのも魅力です。",
    "上手に使い分ければ、日常の小さな不便を解消し、暮らしが整います。"
]

intro_end = [
    "今回は、特にチェックしておきたい人気＆定番のアプリをまとめてご紹介します。",
    "この記事では、ユーザー満足度が高く実用性のあるアプリを中心に解説します。",
    "日常で使い勝手の良いアプリを厳選し、活用ポイントまで丁寧にお届けします。",
    "必要な時にすぐ使える定番アプリをまとめ、比較しやすい形で整理しました。",
    "よく使われている実力派アプリと便利な使い道を、わかりやすくまとめました。",
    "信頼できる人気アプリを中心に、各アプリの特徴をやさしく解説します。",
    "検索上位に入るアプリを中心に、実際の使用感も交えてご紹介します。",
    "インストールしておくと助かるアプリを、総合的にピックアップしました。"
]

def make_intro(title, keyword):
    intro = random.choice(intro_start) + random.choice(intro_middle) + " " + random.choice(intro_end)
    return f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">
{intro}
本記事では「{keyword}」に関連するアプリを中心に取り上げます。
Google Playで「{keyword}」と検索した際に上位表示される人気アプリを基準に厳選しました。
スマホユーザーなら入れておきたい実用的なアプリをチェックして、必要なタイミングで活用してみてください。
</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
"""

end_start = [
    "本記事で紹介したアプリが、皆さまのスマホ生活をさらに快適にしてくれることを願っています。",
    "今回まとめたアプリが、日常のさまざまな場面で役立ちますように。",
    "単なる機能紹介に留まらず、実際の使い道までイメージできるよう配慮しました。",
    "おすすめアプリを上手に使い分けて、毎日をもっとスムーズに過ごしましょう。",
    "まずは気になるアプリから試して、あなたに合う一つを見つけてください。"
]

end_summary = [
    "各アプリの強みや便利なポイントを整理したので、インストール時の参考になるでしょう。",
    "特徴や使いやすさを比較しやすくまとめたので、アプリ選びに役立ちます。",
    "実際の利用者の評価を反映しているので、安心して選択できます。",
    "人気と信頼性を考慮して厳選したアプリなので、自信を持っておすすめできます。",
    "無料・有料を問わず、目的に合ったものを選べるよう工夫しました。"
]

end_next = [
    "今後も最新トレンドと役立つアプリ情報をいち早くお届けします。",
    "引き続き便利で実用的なアプリ紹介をお楽しみください。",
    "話題のアプリや注目の新機能を継続的に紹介していきます。",
    "日常をサポートする多彩なアプリをこれからも取り上げます。",
    "スマホ活用のヒントを交えながら、役立つ情報を更新していきます。"
]

end_action = [
    "コメントやいいねは、今後の記事作成の励みになります。",
    "ご意見や質問があれば、ぜひコメントで教えてください。",
    "役立つと思ったら、周りの方にもシェアしていただけると嬉しいです。",
    "皆さまのフィードバックが、より良い記事づくりの力になります。",
    "興味のある方はフォローしていただければ、新着記事をすぐにチェックできます。"
]

end_greet = [
    "最後までお読みいただき、ありがとうございました。素敵な一日をお過ごしください！",
    "お読みいただき感謝いたします。皆さまの毎日が快適で楽しいものになりますように！",
    "次回も役立つ情報をお届けしますので、ぜひチェックしてくださいね！",
    "今後とも当ブログをよろしくお願いいたします。それでは、また！",
    "今日も良い一日をお過ごしください！"
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
# アプリクローリング (国/言語指定可能)
# ================================
def crawl_apps(keyword, lang="ja", country="JP"):
    url = f"https://play.google.com/store/search?q={keyword}&c=apps&hl={lang}&gl={country}"
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
# メイン実行 (シート4基盤、日本語ブログ固定)
# ================================
try:
    rows = ws.get_all_values()
    target_row, keyword, label, title = None, None, None, None

    # ✅ 対象行を検索 (A列=キーワード, F列 != "完")
    for i, row in enumerate(rows[1:], start=2):  # 2行目から
        kw = row[0].strip() if len(row) > 0 else ""   # A列: キーワード
        lb = row[1].strip() if len(row) > 1 else ""   # B列: ラベル
        done = row[5].strip() if len(row) > 5 else "" # F列: 完了フラグ
        if kw and done != "完":
            target_row, keyword, label = i, kw, lb
            title = make_rotating_title(ws, keyword)
            break

    if not keyword:
        print("処理するキーワードがありません")
        exit()

    print(f"👉 今回の実行: {title} (ラベル={label})")

    # ✅ サムネイル生成
    thumb_dir = "thumbnails"
    os.makedirs(thumb_dir, exist_ok=True)
    thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
    img_url = make_thumb_with_logging(ws, target_row, thumb_path, title)

    html = make_intro(title, keyword)

    # ✅ 自動目次 (序文の直後)
    html += """
    <div class="mbtTOC"><button>目次</button>
    <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
    </div>
    <p>&nbsp;</p>
    """

    if img_url:
        html += f"""
        <p style="text-align:center;">
          <img src="{img_url}" alt="{keyword} サムネイル" style="max-width:100%; height:auto; border-radius:10px;">
        </p>
        <br /><br />
        """

    # ✅ アプリクローリング
    app_links = crawl_apps(keyword)
    print(f"収集したアプリリンク: {len(app_links)}件")

    # 🔹 앱 개수 확인 (3개 미만이면 즉시 종료)
    if len(app_links) < 3:
        print("⚠️ アプリ数が3未満 → 自動的に完了処理")
        ws.update_cell(target_row, 6, "完")  # F列: 完了フラグ
        exit()

    # ✅ 本文作成
    tag_str = " ".join([f"#{t}" for t in title.split()])
    for j, app_url in enumerate(app_links, 1):
        if j > 7:
            break
        resp = requests.get(app_url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        h1 = soup.find("h1").text if soup.find("h1") else f"アプリ {j}"
        raw_desc = str(soup.find("div", class_="fysCi")) if soup.find("div", class_="fysCi") else ""
        desc = rewrite_app_description(raw_desc, h1, keyword)
    
        # ✅ ラベルリンク追加 (1番目, 3番目の見出し上)
        if j in (1, 3) and label:
            encoded_label = urllib.parse.quote(label)
            link_block = f"""
            <div class="ottistMultiRelated">
              <a class="extL alt" href="{BLOG_URL}search/label/{encoded_label}?&max-results=10">
                <span style="font-size: medium;"><strong>{label} アプリおすすめ記事を見る</strong></span>
                <i class="fas fa-link 2xs"></i>
              </a>
            </div>
            <br /><br /><br />
            """
            html += link_block
    
        # ✅ 見出し+本文
        html += f"""
        <h2 data-ke-size="size26">{j}. {h1} アプリ紹介</h2>
        <br />
        {desc}
        <br />
        <p style="text-align: center;" data-ke-size="size18">
          <a class="myButton" href="{app_url}">{h1} ダウンロード</a>
        </p><br /><br />
        <p data-ke-size="size18">{tag_str}</p>
        <br /><br /><br />
        """

    html += make_last(title)
    # ✅ 関連記事ボックス挿入
    related_box = get_related_posts(BLOG_ID, count=6)
    html += related_box

    # ✅ 自動目次スクリプト (末尾)
    html += "<script>mbtTOC();</script><br /><br />"

    # ✅ Blogger アップロード (固定 BLOG_ID + ラベル=B列)
    labels = [label, "Android"] if label else ["Android"]
    
    post_body = {
        "content": html,
        "title": title,
        "labels": labels
    }
    res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False).execute()
    url = res.get("url", "")
    print(f"✅ アップロード成功: {url}")

    # ✅ シート更新
    ws.update_cell(target_row, 6, "完")  # F列: 完了フラグ
    ws.update_cell(target_row, 10, url)  # J列: 投稿URL記録

except Exception as e:
    tb = traceback.format_exc()
    print("失敗:", e)
    if target_row:
        ws.update_cell(target_row, 11, str(e))  # K列: エラーメッセージ記録

