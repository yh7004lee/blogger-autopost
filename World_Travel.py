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
if not TOUR_API_KEY:
    raise RuntimeError("TOUR_API_KEY가 없습니다.")
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
BLOG_ID = "6498243474990332474"

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

LABELS = ["해외여행", "여행"]


# ==================================================
# 클라이언트
# ==================================================
client_genai = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ==================================================
# 시트 연결
# ==================================================
def get_sheet4():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("Sheet4")
    print(f"selected worksheet: {ws.title} / {ws.id}")
    return ws

ws4 = get_sheet4()


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

def save_processed_pair(country, city):
    processed = load_processed_pairs()
    key = f"{country}|{city}"
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

def find_next_row():
    rows = read_sheet_rows()
    for i, row in enumerate(rows[1:], start=2):
        country = row[0].strip() if len(row) > 0 and row[0] else ""
        city = row[1].strip() if len(row) > 1 and row[1] else ""
        status = row[2].strip() if len(row) > 2 and row[2] else ""
        debug(f"[ROW {i}] country={country}, city={city}, status={status}")
        if country and city and status != "완":
            return i, country, city
    return None, None, None


# ==================================================
# 파일 / 이미지
# ==================================================
def pick_random_background():
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

def textwrap_wrap_kor(text, width):
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [text[i:i+width] for i in range(0, len(text), width)]
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if len(test) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def make_thumb(save_path, var_title):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()
    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))
    try:
        font = ImageFont.truetype(ASSETS_FONT_TTF, 48)
    except:
        font = ImageFont.load_default()
    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    draw = ImageDraw.Draw(canvas)
    lines = textwrap_wrap_kor(var_title, 12)
    bbox = font.getbbox("가")
    line_height = (bbox[3] - bbox[1]) + 12
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
def generate_random_title(country, city):
    keywords = ["여행지", "숨은 명소", "데이트 코스", "가족여행", "당일치기 코스", "주말여행", "핫플레이스"]
    suffixes = ["TOP10", "BEST10", "추천 10선"]
    return f"{country} {city} 가볼만한곳 {random.choice(keywords)} {random.choice(suffixes)}"

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

def make_intro_prompt(country, city, title):
    return f"""
너는 한국어 해외여행 블로그 전문 작성자다.
아래 정보를 바탕으로 서론만 작성해라.

국가: {country}
도시: {city}
제목: {title}

조건:
- 3~4문장
- 핵심 키워드 자연스럽게 포함
- 첫 문장에서 호기심을 강하게 끌 것
- 너무 짧지 않게, 그러나 장황하지 않게
- 여행 동선, 추천 이유, 기대감이 느껴지게
- HTML 사용 가능하지만 <p>와 <br>만 사용
- <p data-ke-size="size18"> 로 시작할 것
- 마크다운 금지
- 중국어/일본어 금지
""".strip()

def make_section_prompt(country, city, place_title, addr, overview):
    return f"""
너는 한국어 해외여행 블로그 전문 작성자다.

관광지 정보:
- 국가: {country}
- 도시: {city}
- 관광지명: {place_title}
- 주소: {addr}
- 참고설명: {overview}

작성 조건:
- 자연스러운 한국어
- 여행 블로그 스타일
- 4문단
- 350자 이상
- 장점 설명
- 방문 포인트 설명
- HTML 사용 가능, <p>와 <br>만 사용
- <p data-ke-size="size18"> 로 시작
- 제목 태그 금지
- 마크다운 금지
- 중국어/일본어 금지
""".strip()

def make_last(country, city):
    return (
        f"{country} {city} 여행은 생각보다 동선이 중요해서, 미리 핵심 명소를 정리해두면 훨씬 편하게 움직일 수 있습니다. "
        f"이번 글에서 소개한 곳들은 {country} {city}의 분위기와 매력을 함께 느끼기 좋은 곳들로 구성했습니다. "
        f"일정이 짧아도 충분히 알차게 둘러볼 수 있으니, 취향에 맞게 코스를 조합해 보시면 좋습니다."
    )


# ==================================================
# AI 5차시도
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
# 장소 수집
# ==================================================
def is_valid_address(addr):
    if not addr:
        return False
    addr = addr.strip()
    bad_words = ["분이면", "추천", "방문", "둘러볼", "관람", "체험", "좋은", "유명", "명소", "코스", "시간", "거리", "산책", "여행"]
    if any(word in addr for word in bad_words):
        return False
    if any(keyword in addr for keyword in ["로", "길", "대로", "번길", "동", "읍", "면", "리"]):
        return True
    if any(ch.isdigit() for ch in addr):
        return True
    return False

def get_default_places(country, city):
    fallback = [
        f"{city} 대표 명소",
        f"{city} 야경 명소",
        f"{city} 산책 코스",
        f"{city} 전통시장",
        f"{city} 공원",
        f"{city} 전망대",
        f"{city} 포토스팟",
        f"{city} 문화명소",
        f"{city} 맛집거리",
        f"{city} 인기 관광지",
    ]
    return [{
        "contentId": "",
        "title": name,
        "addr": "",
        "image": "",
        "raw": {},
        "score": 0
    } for name in fallback]

def get_naver_places(country, city):
    keyword = f"{country} {city} 가볼만한곳"
    debug(f"네이버 플레이스 검색어: {keyword}")
    return []

def serper_places_search(country, city):
    try:
        conn = requests.Session()
        res = conn.post(
            "https://google.serper.dev/places",
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json"
            },
            json={"q": f"{country} {city} 여행지", "num": 10, "gl": "kr", "hl": "ko"},
            timeout=30
        )
        data = res.json()
        return data.get("places", [])
    except Exception as e:
        debug(f"⚠️ Serper 장소 검색 실패: {e}")
        return []

def score_place(item):
    text = " ".join([
        item.get("title", ""),
        item.get("address", ""),
        item.get("description", ""),
        " ".join(item.get("categories", []) if isinstance(item.get("categories", []), list) else [])
    ]).lower()

    score = 0
    hot_words = ["핫플", "명소", "전망대", "공원", "시장", "카페", "미술관", "뮤지엄", "테마", "축제", "야경", "체험", "해변", "호수", "강변", "산책", "포토"]
    for w in hot_words:
        if w in text:
            score += 3

    if item.get("rating"):
        try:
            score += float(item["rating"])
        except:
            pass

    if item.get("reviewsCount"):
        try:
            score += min(int(item["reviewsCount"]) / 100.0, 5)
        except:
            pass

    return score

def normalize_place_title(title):
    title = (title or "").split("|")[0].strip()
    title = re.sub(r"\s+", " ", title)
    return title

def get_places(country, city):
    naver_places = get_naver_places(country, city)
    results = []
    seen = set()

    for name in naver_places:
        name = normalize_place_title(name)
        if not name:
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        results.append({
            "contentId": "",
            "title": name,
            "addr": "",
            "raw": {},
            "score": 0
        })
        if len(results) >= 10:
            return results[:10]

    if len(results) < 10:
        try:
            raw_places = serper_places_search(country, city)
        except Exception as e:
            print(f"⚠️ Serper 장소 검색 실패: {e}")
            raw_places = []

        for item in raw_places:
            title = normalize_place_title(item.get("title", ""))
            addr = item.get("address", "").strip()

            if not title:
                continue
            if title.lower() in seen:
                continue
            seen.add(title.lower())

            if addr and not is_valid_address(addr):
                addr = ""

            results.append({
                "contentId": item.get("contentId", "") or "",
                "title": title,
                "addr": addr,
                "raw": item,
                "score": score_place(item)
            })

            if len(results) >= 10:
                return results[:10]

    return results[:10]

def get_place_address_via_tour_api(country, city, place_name):
    keyword = f"{country} {city} {place_name}".strip()
    url = "https://apis.data.go.kr/B551011/KorService2/searchKeyword1"
    params = {
        "serviceKey": TOUR_API_KEY,
        "MobileOS": "ETC",
        "MobileApp": "travel_blog",
        "_type": "json",
        "numOfRows": 30,
        "pageNo": 1,
        "keyword": keyword,
        "arrange": "P"
    }
    try:
        res = requests.get(url, params=params, timeout=30)
        data = res.json()
        if "response" not in data or "body" not in data["response"] or "items" not in data["response"]["body"]:
            return ""
        items = data["response"]["body"]["items"]["item"]
        if isinstance(items, dict):
            items = [items]
        for item in items:
            title = (item.get("title") or "").strip()
            if title.lower() == place_name.lower():
                return (item.get("addr1") or "").strip()
        for item in items:
            title = (item.get("title") or "").strip()
            if place_name.lower() in title.lower() or title.lower() in place_name.lower():
                return (item.get("addr1") or "").strip()
        return ""
    except Exception as e:
        debug(f"⚠️ TourAPI 주소 재검색 실패: {place_name} / {e}")
        return ""

def get_overview(content_id, country, city, title):
    if not content_id:
        return f"{title}는 {country} {city} 여행에서 인기가 많은 관광지로, 현지 분위기를 즐기기 좋은 장소입니다."
    try:
        url = "https://apis.data.go.kr/B551011/KorService2/detailCommon2"
        params = {
            "serviceKey": TOUR_API_KEY,
            "MobileOS": "ETC",
            "MobileApp": "travel_blog",
            "_type": "json",
            "contentId": content_id,
            "defaultYN": "Y",
            "firstImageYN": "Y",
            "overviewYN": "Y"
        }
        res = requests.get(url, params=params, timeout=30)
        data = res.json()
        if "response" in data and "body" in data["response"] and "items" in data["response"]["body"]:
            item = data["response"]["body"]["items"]["item"][0]
            return item.get("overview", f"{title}는 {country} {city} 여행에서 추천할 만한 장소입니다.")
        return f"{title}는 {country} {city} 여행에서 추천할 만한 장소입니다."
    except Exception as e:
        debug(f"⚠️ 상세 정보 가져오기 실패: {e}")
        return f"{title}는 {country} {city} 여행에서 추천할 만한 장소입니다."

def get_google_place_photos(place_name, count=3, country="", city=""):
    if not GOOGLE_MAPS_API_KEY:
        return []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        parts = [x.strip() for x in [country, city, place_name] if x and x.strip()]
        query = " ".join(parts).strip()

        search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        search_params = {
            "query": query,
            "key": GOOGLE_MAPS_API_KEY,
            "language": "ko"
        }
        res = requests.get(search_url, params=search_params, headers=headers, timeout=30)
        data = res.json()
        results = data.get("results", [])
        if not results:
            return []

        target = place_name.strip().lower() if place_name else ""
        selected = None
        for r in results:
            name = (r.get("name") or "").strip().lower()
            if target and target in name:
                selected = r
                break
        if selected is None:
            selected = results[0]

        place_id = selected.get("place_id", "")
        if not place_id:
            return []

        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            "place_id": place_id,
            "fields": "photos,name,formatted_address",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "ko"
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
    html = '<div style="display:flex; gap:10px; flex-wrap:wrap; margin:20px 0;">'
    for img_url in image_list[:3]:
        html += f'''
        <div style="flex:1 1 30%; min-width:180px;">
            <img src="{img_url}" style="width:100%; height:auto; border-radius:8px;" alt="{place_title}">
        </div>
        '''
    html += "</div>"
    return html

def build_post_html(country, city, title, places, thumb_url):
    intro_html = generate_ai_review(make_intro_prompt(country, city, title), title)
    intro_html = intro_html.replace('data-ke-size="size16"', 'data-ke-size="size18"')
    intro_html = intro_html.replace("size16", "size18")
    last_text = make_last(country, city)

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
       🗺️ 구글 지도에서 위치 확인하기
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

    ai_review_text = f"<p data-ke-size='size18'>{country} {city}의 대표 관광지들을 중심으로 여행 코스를 구성하면 더욱 알찬 일정이 됩니다.</p>"

    html_content = f"""

  
  {intro_html}
  <p style="text-align:center;">
   <p data-ke-size="size18"><br /></p>
   <p data-ke-size="size18"><br /></p>
    <img src="{thumb_url}" alt="{title} 썸네일" style="max-width:100%; height:auto; border-radius:8px;">
  </p>
  <div style="padding:12px;">
  <span><!--more--></span>  
  <p data-ke-size="size18"><br /></p>
  <div class="mbtTOC"><button> 목차 </button>
  <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
  </div>
  {sections_html}
  <h2>{city} 여행 총평</h2>
  {ai_review_text}
  <p data-ke-size="size18">{last_text}</p>
  <script>mbtTOC();</script>
</div>
"""
    return html_content, LABELS


# ==================================================
# 실행
# ==================================================
def main():
    try:
        row_idx, country, city = find_next_row()
        if not row_idx:
            debug("처리할 행이 없습니다.")
            return

        debug(f"선택된 행: {row_idx}, {country}, {city}")
        log_step(row_idx, "1단계: 대상 행 선택")

        title = generate_random_title(country, city)
        debug(f"생성 제목: {title}")
        log_step(row_idx, f"2단계: 제목 생성 ({title})")

        places = get_places(country, city)
        debug(f"수집된 장소 개수: {len(places)}")

        travel_sections = []
        for idx, place in enumerate(places, start=1):
            debug(f"장소 처리 {idx}/{len(places)}: {place['title']}")
            place["title"] = normalize_text(place["title"])
            place["overview"] = get_overview(place.get("contentId", ""), country, city, place["title"])
            place["images"] = get_place_images(place, count=3, country=country, city=city)
            place["image"] = place["images"][0] if place["images"] else ""
            if not place.get("addr") or not is_valid_address(place.get("addr", "")):
                new_addr = get_place_address_via_tour_api(country, city, place["title"])
                if new_addr:
                    place["addr"] = new_addr
                    debug(f"주소 보강 완료: {new_addr}")
            prompt = make_section_prompt(country, city, place["title"], place.get("addr", ""), place.get("overview", ""))
            place["desc"] = generate_ai_review(prompt, place["title"])
            place["desc"] = re.sub(r"<h1[^>]*>.*?</h1>", "", place["desc"], flags=re.IGNORECASE)
            place["desc"] = place["desc"].replace("**", "")
            place["desc"] = place["desc"].replace('data-ke-size="size16"', 'data-ke-size="size18"')
            place["desc"] = place["desc"].replace("size16", "size18")
            travel_sections.append({
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
        log_step(row_idx, "3단계: 썸네일 생성 완료")

        thumb_url = upload_to_drive(thumb_path, f"{safe_title}.png")
        debug(f"Drive 업로드 완료: {thumb_url}")
        log_step(row_idx, "4단계: 썸네일 Drive 업로드 완료")

        html_content, labels = build_post_html(country, city, title, travel_sections, thumb_url)
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
        ws4.update_cell(row_idx, 3, "완")
        try:
            ws4.update_cell(row_idx, 15, res.get("url", ""))
        except Exception as e:
            debug(f"URL 기록 실패: {e}")

        log_step(row_idx, f"5단계: Blogger 업로드 성공 ({res.get('url', '')})")
        save_processed_pair(country, city)
        debug(f"✅ {country} {city} 처리 완료")

    except Exception as e:
        tb = traceback.format_exc()
        debug(f"❌ 최종 실패: {e}")
        debug(tb)
        raise


if __name__ == "__main__":
    main()
