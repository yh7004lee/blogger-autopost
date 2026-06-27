# -*- coding: utf-8 -*-
import os
import sys
import json
import re
import time
import random
import traceback
import requests
import logging
import pickle
from urllib.parse import quote
import glob
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

import gspread
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from google import genai
from openai import OpenAI

# ==================================================
# 콘솔 출력 인코딩
# ==================================================
try:
    sys.stdout.reconfigure(encoding="utf-8")
except:
    pass

# ==================================================
# 디버그 로그
# ==================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================================================
# 시크릿 로드
# ==================================================
SECRETS_API = os.getenv("SECRETS_API")
if not SECRETS_API:
    raise RuntimeError("SECRETS_API 환경변수가 없습니다. GitHub Actions Secrets 를 확인하세요.")

secrets = json.loads(SECRETS_API)

TOUR_API_KEY = secrets.get("TOUR_API_KEY", "")
OPENROUTER_API_KEY = secrets.get("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", "")
SERPER_API_KEY = secrets.get("SERPER_API_KEY", "")
GOOGLE_MAPS_API_KEY = secrets.get("GOOGLE_MAPS_API_KEY", "")
GROQ_API_KEY = secrets.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY = secrets.get("CEREBRAS_API_KEY", "")
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY가 없습니다.")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY가 없습니다.")

# ==================================================
# 고정 설정
# ==================================================
SHEET_ID = "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs"
SHEET_GID = 2131907983
HISTORY_PATH = "processed_overseas_blogger.json"
BLOG_ID = "7573892357971022707"

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

LABELS = ["海外旅行", "旅行"]

# ==================================================
# 카테고리 설정
# ==================================================
POST_CONFIGS = [
    {
        "name": "맛집",
        "keywords": ["맛집", "グルメ", "レストラン"],
        "title_templates": [
            "{country} {city} グルメ人気スポット TOP10",
            "{country} {city} グルメ有名スポット BEST10",
            "{country} {city} グルメおすすめスポット 10選",
        ],
        "search_terms": [
            "{country} {city} 맛집",
            "{country} {city} レストラン",
            "{country} {city} グルメ",
        ],
    },
    {
        "name": "피자",
        "keywords": ["피자", "ピザ"],
        "title_templates": [
            "{country} {city} ピザ人気スポット TOP10",
            "{country} {city} ピザ有名スポット BEST10",
            "{country} {city} ピザおすすめスポット 10選",
        ],
        "search_terms": [
            "{country} {city} ピザ",
            "{country} {city} ピザ店",
            "{country} {city} ピザショップ",
        ],
    },
    {
        "name": "커피",
        "keywords": ["커피", "カフェ", "コーヒー"],
        "title_templates": [
            "{country} {city} カフェ人気スポット TOP10",
            "{country} {city} カフェ有名スポット BEST10",
            "{country} {city} カフェおすすめスポット 10選",
        ],
        "search_terms": [
            "{country} {city} カフェ",
            "{country} {city} コーヒー",
            "{country} {city} 喫茶店",
        ],
    },
    {
        "name": "베이커리",
        "keywords": ["베이커리", "ベーカリー", "パン"],
        "title_templates": [
            "{country} {city} ベーカリー人気スポット TOP10",
            "{country} {city} ベーカリー有名スポット BEST10",
            "{country} {city} ベーカリーおすすめスポット 10選",
        ],
        "search_terms": [
            "{country} {city} ベーカリー",
            "{country} {city} パン屋",
            "{country} {city} ベーカリーショップ",
        ],
    },
    {
        "name": "액티비티",
        "keywords": ["액티비티", "アクティビティ", "体験"],
        "title_templates": [
            "{country} {city} アクティビティ人気スポット TOP10",
            "{country} {city} アクティビティ有名スポット BEST10",
            "{country} {city} アクティビティおすすめスポット 10選",
        ],
        "search_terms": [
            "{country} {city} アクティビティ",
            "{country} {city} 体験",
            "{country} {city} レジャー",
        ],
    },
    {
        "name": "바",
        "keywords": ["바", "バー", "居酒屋"],
        "title_templates": [
            "{country} {city} バー人気スポット TOP10",
            "{country} {city} バー有名スポット BEST10",
            "{country} {city} バーおすすめスポット 10選",
        ],
        "search_terms": [
            "{country} {city} バー",
            "{country} {city} 居酒屋",
            "{country} {city} ナイトスポット",
        ],
    },
]

# ==================================================
# 클라이언트
# ==================================================
client_genai = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ==================================================
# 시트 연결
# ==================================================
def get_sheet5():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("Sheet5")
    print(f"selected worksheet: {ws.title} / {ws.id}")
    return ws

ws4 = get_sheet5()

# ==================================================
# 히스토리 관리
# ==================================================
def load_processed_pairs():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("pairs", [])
    except Exception:
        return []

def save_processed_pair(country, city, category):
    processed = load_processed_pairs()
    key = f"{country}|{city}|{category}"
    if key not in processed:
        processed.append(key)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"pairs": processed}, f, ensure_ascii=False, indent=2)

def debug(msg):
    print(msg)
    logger.debug(msg)

def log_step(row, msg):
    try:
        prev = ws4.cell(row, 16).value or ""
        ws4.update_cell(row, 16, f"{prev} | {msg}" if prev else msg)
    except Exception as e:
        debug(f"⚠️ 로그 기록 실패: {e}")

def read_sheet_rows():
    values = ws4.get_all_values()
    debug(f"시트 전체 행 수: {len(values)}")
    return values

def is_done_cell(value):
    return str(value or "").strip() == "完"

def find_next_rows_bulk(limit=6):
    rows = read_sheet_rows()
    targets = []
    for i in range(len(rows) - 1, 0, -1):
        row = rows[i]
        country = row[0].strip() if len(row) > 0 and row[0] else ""
        city = row[1].strip() if len(row) > 1 and row[1] else ""
        done = row[2].strip() if len(row) > 2 and row[2] else ""
        debug(f"[ROW {i+1}] country={country}, city={city}, done={done}")
        if country and city and not is_done_cell(done):
            targets.append((i + 1, country, city))
        if len(targets) >= limit:
            break
    return targets

# ==================================================
# 파일 / 이미지
# ==================================================
def pick_random_background():
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

def textwrap_wrap_jpn(text, width):
    if not text:
        return [""]
    if len(text) <= width:
        return [text]
    lines = [text[i:i+width] for i in range(0, len(text), width)]
    return lines

def make_thumb(save_path, var_title):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()
    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    try:
        font = ImageFont.truetype(
            os.path.join("assets", "fonts", "NotoSansJP-VariableFont_wght.ttf"),
            42
        )
    except:
        font = ImageFont.load_default()
    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    draw = ImageDraw.Draw(canvas)
    lines = textwrap_wrap_jpn(var_title, 12)
    try:
        bbox = font.getbbox("あ")
        line_height = (bbox[3] - bbox[1]) + 12
    except:
        line_height = 56
    total_h = len(lines) * line_height
    y = 250 - total_h / 2
    for line in lines:
        tb = draw.textbbox((0, 0), line, font=font)
        x = (500 - (tb[2] - tb[0])) / 2
        draw.text((x, y), line, "#FFEECB", font=font)
        y += line_height
    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")

def get_drive_service():
    token_path = "drive_token_2nd.pickle"
    if not os.path.exists(token_path):
        raise RuntimeError("drive_token_2nd.pickle 없음")
    with open(token_path, "rb") as f:
        creds = pickle.load(f)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds)

def ensure_drive_folder(drive_service, folder_name):
    q = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    folder = drive_service.files().create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    return folder["id"]

def upload_to_drive(file_path, file_name):
    drive_service = get_drive_service()
    folder_id = (DRIVE_FOLDER_ID or "").strip()
    if not folder_id or folder_id == "YOUR_DRIVE_FOLDER_ID":
        folder_id = ensure_drive_folder(drive_service, "blogger")
    media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
    meta = {"name": file_name, "parents": [folder_id]}
    uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader", "allowFileDiscovery": False}
    ).execute()
    return f"https://lh3.googleusercontent.com/d/{uploaded['id']}"

# ==================================================
# Blogger
# ==================================================
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise RuntimeError("blogger_token.json 없음")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# ==================================================
# 글 생성용 유틸
# ==================================================
def generate_random_title(country, city, cfg):
    return random.choice(cfg["title_templates"]).format(country=country, city=city)

def clean_html(raw_html):
    return BeautifulSoup(raw_html or "", "html.parser").get_text(separator="\n", strip=True)

def normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()

def remove_redundant_tokens(text, country, city):
    text = normalize_text(text)
    if not text:
        return text
    for token in [country, city]:
        if token:
            text = re.sub(rf"^\s*{re.escape(token)}\s+", "", text).strip()
    return text

def build_display_title(country, city, place_title):
    clean_place = remove_redundant_tokens(place_title, country, city)
    if clean_place:
        return f"{country} {city} {clean_place}"
    return f"{country} {city}"

def build_map_search_keyword(country, city, place_title):
    clean_place = remove_redundant_tokens(place_title, country, city)
    if clean_place:
        return f"{country} {city} {clean_place}"
    return f"{country} {city}"

def make_intro_prompt(country, city, title, category_name):
    return f"""
あなたは日本語の海外旅行ブログ専門ライターです。以下の情報をもとに、導入文（序章）を日本語で作成してください。

国: {country}
都市: {city}
カテゴリ: {category_name}
タイトル: {title}

条件:
- 3〜4文で簡潔に
- キーワードを自然に含める
- 最初の文で強い興味を引くこと
- 旅の流れやおすすめ理由、期待感が伝わるように
- HTMLは使用可能ですが、<p>と<br>のみ使用
- <p data-ke-size="size18"> で開始すること
- マークダウン禁止
""".strip()

def make_section_prompt(country, city, place_title, addr, overview, category_name):
    addr_text = addr or ""
    overview_text = overview or ""
    return f"""
あなたは日本語の海外旅行ブログ専門ライターです。

カテゴリ: {category_name}
情報:
- 国: {country}
- 都市: {city}
- スポット名: {place_title}
- 住所: {addr_text}
- 概要: {overview_text}

作成条件:
- 自然で読みやすい日本語
- 旅行ブログの口調
- 段落を分けて、合計4段落程度
- 350文字以上（可能な範囲で）
- 長所・見どころを具体的に記載
- 訪問時のポイント（交通、混雑、ベストタイム等）を含める
- HTMLは使用可能、<p>と<br>のみ使用
- <p data-ke-size="size18"> で開始
- 見出しタグは使用禁止
- マークダウン禁止
""".strip()

def make_last(country, city, category_name):
    return f"{country} {city} の{category_name}は、事前に回る順番を決めておくと効率よく楽しめます。"

# ==================================================
# AI 생성
# ==================================================
def generate_ai_review(prompt, keyword=""):
    tries = [
        ("Gemini Flash", lambda: client_genai.models.generate_content(model="gemini-2.5-flash", contents=prompt).text.strip()),
        ("Gemini Flash Lite", lambda: client_genai.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt).text.strip()),
        ("OpenRouter", lambda: requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=40
        ).json()["choices"][0]["message"]["content"].strip()),
        ("OpenAI", lambda: openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1400
        ).choices[0].message.content.strip()),
        ("Cerebras/Groq fallback", lambda: requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=40
        ).json()["choices"][0]["message"]["content"].strip()),
    ]

    last_err = None
    for idx, (name, fn) in enumerate(tries, start=1):
        try:
            debug(f"🤖 AI 시도 {idx}: {name}")
            text = fn()
            if text and isinstance(text, str):
                debug(f"✅ AI 성공: {name}")
                return text
            raise RuntimeError(f"{name} 응답이 비어있음")
        except Exception as e:
            last_err = e
            debug(f"⚠️ AI 실패 {idx}: {name} / {e}")
            continue

    debug(f"❌ AI 최종 실패: {last_err}")
    return f"{keyword} 설명 생성 실패"

# ==================================================
# Google Places 수집
# ==================================================
def normalize_place_title(title):
    title = (title or "").split("|")[0].strip()
    title = re.sub(r"\s+", " ", title)
    return title

def get_google_places_textsearch(country, city, category_cfg, limit=10):
    if not GOOGLE_MAPS_API_KEY:
        debug("GOOGLE_MAPS_API_KEY가 설정되어 있지 않습니다.")
        return []
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    seen = set()
    places = []
    try:
        for raw_q in category_cfg["search_terms"]:
            if len(places) >= limit:
                break
            query = raw_q.format(country=country, city=city)
            params = {
                "query": query,
                "key": GOOGLE_MAPS_API_KEY,
                "language": "ja",
            }
            res = requests.get(url, params=params, timeout=30)
            data = res.json()
            for r in data.get("results", []):
                name = r.get("name", "").strip()
                if not name:
                    continue
                name_norm = normalize_place_title(name)
                key = name_norm.lower()
                if key in seen:
                    continue
                seen.add(key)
                addr = r.get("formatted_address", "").strip()
                places.append({
                    "contentId": r.get("place_id", ""),
                    "title": name_norm,
                    "addr": addr,
                    "raw": r,
                    "score": (r.get("rating") or 0) + min((r.get("user_ratings_total") or 0) / 100.0, 5)
                })
                if len(places) >= limit:
                    break

        return places[:limit]
    except Exception as e:
        debug(f"⚠️ Google Places TextSearch 실패: {e}")
        return []

def get_google_place_photos(place_name, count=3, country="", city=""):
    if not GOOGLE_MAPS_API_KEY:
        return []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        query = " ".join([x.strip() for x in [country, city, place_name] if x and x.strip()]).strip()

        search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        search_params = {
            "query": query,
            "key": GOOGLE_MAPS_API_KEY,
            "language": "ja"
        }
        res = requests.get(search_url, params=search_params, headers=headers, timeout=30)
        data = res.json()
        results = data.get("results", [])
        if not results:
            return []

        selected = results[0]
        place_id = selected.get("place_id", "")
        if not place_id:
            return []

        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            "place_id": place_id,
            "fields": "photos,name,formatted_address",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "ja"
        }
        data2 = requests.get(details_url, params=details_params, headers=headers, timeout=30).json()
        photos = data2.get("result", {}).get("photos", [])
        if isinstance(photos, dict):
            photos = [photos]

        photo_urls = []
        for p in photos[:count]:
            photo_ref = p.get("photo_reference")
            if not photo_ref:
                continue
            photo_url = (
                "https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=1600&photo_reference={photo_ref}&key={GOOGLE_MAPS_API_KEY}"
            )
            photo_urls.append(photo_url)
        return photo_urls[:count]
    except Exception as e:
        debug(f"[구글 이미지 실패] {country} {city} {place_name} / {e}")
        return []

def get_place_images(place, count=3, country="", city=""):
    title = place.get("title", "")
    images = []
    if place.get("image"):
        images.append(place["image"])
    google_images = get_google_place_photos(title, count=count, country=country, city=city)
    for img in google_images:
        if img and img not in images:
            images.append(img)
        if len(images) >= count:
            break
    return images[:count]

# ==================================================
# HTML 생성
# ==================================================
def build_images_html(place_title, image_list):
    if not image_list:
        return ""
    html = ""
    for img_url in image_list[:3]:
        html += f'''
        <div style="margin:20px 0;">
            <img src="{img_url}"
                 alt="{place_title}"
                 style="width:100%; height:auto; border-radius:8px;">
        </div>
        '''
    return html

def build_post_html(country, city, title, places, thumb_url, category_name):
    intro_html = generate_ai_review(make_intro_prompt(country, city, title, category_name), title)
    intro_html = intro_html.replace('data-ke-size="size16"', 'data-ke-size="size18"')
    intro_html = intro_html.replace("size16", "size18")
    last_text = make_last(country, city, category_name)

    sections_html = ""
    for idx, item in enumerate(places, start=1):
        section_title = build_display_title(country, city, item["title"])
        map_keyword = build_map_search_keyword(country, city, item["title"])
        map_link_url = "https://www.google.com/maps/search/?api=1&query=" + quote(map_keyword)
        desc = item["desc"].replace("\n", "<br>")
        extra_images_html = build_images_html(item["title"], item.get("images", [])[1:3])
        img_html = ""
        if item.get("image"):
            img_html = f'''
<div style="text-align:center; margin:20px 0;">
    <a href="{map_link_url}" target="_blank">
        <img src="{item['image']}" style="max-width:100%; height:auto; border-radius:8px;" alt="{item['title']}">
    </a>
</div>
'''
        map_html = f'''
<div style="text-align:center; margin-bottom:25px;">
    <a href="{map_link_url}" target="_blank" style="color:#1a2a40;font-weight:bold;text-decoration:underline;font-size:15px;">
       🗺️ Google マップで場所を確認する
    </a>
</div>
'''
        sections_html += f"""
<br><br>
<h2>{idx}. {section_title}</h2>
<br>
{img_html}
{extra_images_html}
<br>
{map_html}
<br><br>
<p style="font-size:15px; color:#555; line-height:1.9;">
    {desc}
</p>
<br><br>
"""

    ai_review_text = f"<p data-ke-size='size18'>{country} {city} の主要スポットを組み合わせて効率よい旅程にできます。</p>"

    html_content = f"""
  {intro_html}
  <p style="text-align:center;">
   <p data-ke-size="size18"><br /></p>
   <p data-ke-size="size18"><br /></p>
    <img src="{thumb_url}" alt="{title} サムネイル" style="max-width:100%; height:auto; border-radius:8px;">
  </p>
  <div style="padding:12px;">
  <span><!--more--></span>
  <p data-ke-size="size18"><br /></p>
  <div class="mbtTOC"><button>  目次 </button>
  <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
  </div>
  {sections_html}
  <h2>{city} 旅行まとめ</h2>
  {ai_review_text}
  <p data-ke-size="size18">{last_text}</p>
  <script>mbtTOC();</script>
</div>
"""
    return html_content, LABELS

# ==================================================
# 단일 포스팅 실행
# ==================================================
def process_one_post(row_idx, country, city, category_cfg):
    category_name = category_cfg["name"]
    debug(f"▶ 시작: {country} {city} / {category_name}")
    log_step(row_idx, f"{category_name}: 1단계 시작")

    title = generate_random_title(country, city, category_cfg)
    debug(f"생성 제목: {title}")
    log_step(row_idx, f"{category_name}: 2단계 제목 생성 ({title})")

    places_raw = get_google_places_textsearch(country, city, category_cfg, limit=10)
    debug(f"수집된 장소 개수: {len(places_raw)}")

    if not places_raw:
        fallback = [
            f"{city} の代表的なスポット",
            f"{city} の人気スポット",
            f"{city} の定番スポット",
            f"{city} の写真スポット",
            f"{city} の穴場スポット",
            f"{city} の話題スポット",
            f"{city} のおすすめスポット",
            f"{city} の便利なスポット",
            f"{city} の注目スポット",
            f"{city} の人気施設",
        ]
        places_raw = [{"contentId": "", "title": name, "addr": "", "raw": {}, "score": 0} for name in fallback]

    post_sections = []
    for idx, place in enumerate(places_raw, start=1):
        debug(f"장소 처리 {idx}/{len(places_raw)}: {place['title']}")
        place["title"] = normalize_text(place["title"])
        place["overview"] = place.get("raw", {}).get("formatted_address", "") or f"{place['title']} は{city} の人気スポットです。"
        place["images"] = get_place_images(place, count=3, country=country, city=city)
        place["image"] = place["images"][0] if place["images"] else ""
        prompt = make_section_prompt(country, city, place["title"], place.get("addr", ""), place.get("overview", ""), category_name)
        place["desc"] = generate_ai_review(prompt, place["title"])
        place["desc"] = re.sub(r"<h1[^>]*>.*?</h1>", "", place["desc"], flags=re.IGNORECASE)
        place["desc"] = place["desc"].replace("**", "")
        place["desc"] = place["desc"].replace('data-ke-size="size16"', 'data-ke-size="size18"')
        place["desc"] = place["desc"].replace("size16", "size18")
        post_sections.append({
            "contentId": place.get("contentId", ""),
            "title": place["title"],
            "addr": place.get("addr", ""),
            "image": place.get("image", ""),
            "images": place.get("images", []),
            "overview": place.get("overview", ""),
            "desc": place.get("desc", "")
        })
        time.sleep(0.4)

    safe_title = re.sub(r'[\\/:*?"<>|.]', "_", title)
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, f"{safe_title}.png")
    make_thumb(thumb_path, title)
    debug("썸네일 생성 완료")
    log_step(row_idx, f"{category_name}: 3단계 썸네일 생성 완료")

    thumb_url = upload_to_drive(thumb_path, f"{safe_title}.png")
    debug(f"Drive 업로드 완료: {thumb_url}")
    log_step(row_idx, f"{category_name}: 4단계 썸네일 Drive 업로드 완료")

    html_content, labels = build_post_html(country, city, title, post_sections, thumb_url, category_name)
    debug(f"HTML 길이: {len(html_content)}")

    post_body = {
        "content": html_content,
        "title": title,
        "labels": labels,
        "blog": {"id": BLOG_ID}
    }

    res = blog_handler.posts().insert(
        blogId=BLOG_ID,
        body=post_body,
        isDraft=False,
        fetchImages=True
    ).execute()

    debug(f"Blogger 업로드 성공: {res.get('url', '')}")
    log_step(row_idx, f"{category_name}: 5단계 Blogger 업로드 성공 ({res.get('url', '')})")
    save_processed_pair(country, city, category_name)
    return res.get("url", "")

# ==================================================
# 실행
# ==================================================
def main():
    try:
        targets = find_next_rows_bulk(limit=6)
        if not targets:
            debug("처리할 대상이 없습니다.")
            return

        debug(f"선택된 대상 수: {len(targets)}")
        for idx, (row_idx, country, city) in enumerate(targets, start=1):
            category_cfg = POST_CONFIGS[(idx - 1) % len(POST_CONFIGS)]
            debug(f"현재 순서 {idx}/{len(targets)} / 행 {row_idx} / {country} {city} / {category_cfg['name']}")

            try:
                url = process_one_post(row_idx, country, city, category_cfg)
                ws4.update_cell(row_idx, 4, category_cfg["name"])
                ws4.update_cell(row_idx, 3, "完")
                try:
                    ws4.update_cell(row_idx, 15, url)
                except Exception as e:
                    debug(f"URL 기록 실패: {e}")
                debug(f"✅ 완료: {country} {city} / {category_cfg['name']}")
                time.sleep(1.2)
            except Exception as e:
                tb = traceback.format_exc()
                debug(f"❌ 실패: {country} {city} / {category_cfg['name']} / {e}")
                debug(tb)
                continue

    except Exception as e:
        tb = traceback.format_exc()
        debug(f"❌ 최종 실패: {e}")
        debug(tb)
        raise

if __name__ == "__main__":
    main()
