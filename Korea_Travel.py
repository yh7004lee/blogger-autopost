#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import json
import re
import time
import random
import traceback
import urllib.parse
import glob
import pickle
from bs4 import BeautifulSoup
import requests
from PIL import Image, ImageDraw, ImageFont

import gspread
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from google import genai
except Exception:
    genai = None

import feedparser


API_KEYS_JSON = os.getenv("API_KEYS_JSON")

if not API_KEYS_JSON:
    raise RuntimeError("API_KEYS_JSON 환경변수가 없습니다. GitHub Secrets 를 확인하세요.")

try:
    secrets = json.loads(API_KEYS_JSON)
except Exception as e:
    raise RuntimeError(f"API_KEYS_JSON 파싱 실패: {e}")

OPENROUTER_API_KEY = secrets.get("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", "")
SHEET_ID = "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs"
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "")
GOOGLE_MAPS_API_KEY = "AIzaSyBiLiWI4rTtdk_IW-f26uEIkhnKjEBHI1w"
TOUR_API_KEY = secrets.get("TOUR_API_KEY", "")


client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
genai_client = None
if GEMINI_API_KEY and genai:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        genai_client = None

BLOG_ID = "6498243474990332474"
HISTORY_PATH = "processed_regions_blogger.json"
SHEET_GID = 2131907983

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"


def get_sheet3():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    for ws in sh.worksheets():
        if ws.id == SHEET_GID:
            return ws
    raise RuntimeError(f"gid={SHEET_GID} 시트를 찾지 못했습니다.")


ws3 = get_sheet3()


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


def ensure_drive_folder(drive_service, folder_name):
    q = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
    return folder.get("id")


def upload_to_drive(file_path, file_name):
    drive_service = get_drive_service()
    folder_id = (DRIVE_FOLDER_ID or "").strip()
    if not folder_id or folder_id == "YOUR_DRIVE_FOLDER_ID":
        folder_id = ensure_drive_folder(drive_service, "blogger")
    media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
    meta = {"name": file_name, "parents": [folder_id]}
    try:
        uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    except Exception:
        folder_id = ensure_drive_folder(drive_service, "blogger")
        meta = {"name": file_name, "parents": [folder_id]}
        uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader", "allowFileDiscovery": False}
    ).execute()
    return f"https://lh3.googleusercontent.com/d/{uploaded['id']}"


def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise RuntimeError("blogger_token.json 없음 — Blogger 사용자 인증 정보가 필요합니다.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)


blog_handler = get_blogger_service()


def load_processed_regions():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("regions", [])
    except:
        return []


def save_processed_region(region, city):
    processed = load_processed_regions()
    key = f"{region} {city}"
    if key not in processed:
        processed.append(key)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"regions": processed}, f, ensure_ascii=False, indent=2)


def log_step(row, msg: str):
    try:
        prev = ws3.cell(row, 16).value or ""
        ws3.update_cell(row, 16, f"{prev} | {msg}" if prev else msg)
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)


def pick_random_background() -> str:
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


def _thumb(save_path: str, var_title: str):
    os.dirs(os.path.dirname(save_path), exist_ok=True)
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
    var_title_wrap = textwrap_wrap_kor(var_title, 12)
    bbox = font.getbbox("가")
    line_height = (bbox[3] - bbox[1]) + 12
    total_text_height = len(var_title_wrap) * line_height
    var_y_point = 500 / 2 - total_text_height / 2
    for line in var_title_wrap:
        text_bbox = draw.textbbox((0, 0), line, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        x = (500 - text_width) / 2
        draw.text((x, var_y_point), line, "#FFEECB", font=font)
        var_y_point += line_height
    canvas = canvas.resize((400, 400))
    canvas.save(save_path, "PNG")


def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)


def generate_ai_review(prompt, keyword):
    last_err = None

    if genai_client:
        try:
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception as e:
            last_err = e
            print("⚠️ AI 실패 1: Gemini Flash /", e)

    if genai_client:
        try:
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception as e:
            last_err = e
            print("⚠️ AI 실패 2: Gemini Flash Lite /", e)

    try:
        res = requests.post(
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
        )
        data = res.json()
        choices = data.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")
            if text.strip():
                return text.strip()
        raise RuntimeError(f"OpenRouter 응답 구조 이상: {data}")
    except Exception as e:
        last_err = e
        print("⚠️ AI 실패 3: OpenRouter /", e)

    if client:
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1200
            )
            text = res.choices[0].message.content.strip()
            if text:
                return text
        except Exception as e:
            last_err = e
            print("⚠️ AI 실패 4: OpenAI /", e)

    if client:
        try:
            res = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1200
            )
            text = res.choices[0].message.content.strip()
            if text:
                return text
        except Exception as e:
            last_err = e
            print("⚠️ AI 실패 5: OpenAI GPT-4o /", e)

    return f"{keyword} 설명 생성 실패: {last_err}"


def get_queries(region, city):
    return [
        f"{region} {city} 맛집",
        f"{region} {city} 음식점",
        f"{region} {city} 현지인 추천 맛집",
        f"{region} {city} 유명 맛집",
        f"{region} {city} 가성비 맛집",
        f"{city} 맛집",
        f"{city} 음식점",
        f"{city} 현지인 추천 맛집",
        f"{city} 유명 맛집",
        f"{city} 가성비 맛집",
        f"{city} 대표 맛집",
        f"{city} 인기 맛집",
    ]


def google_text_search(query, city="", region=""):
    if not GOOGLE_MAPS_API_KEY:
        return []
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_MAPS_API_KEY, "language": "ko"}
    try:
        res = requests.get(url, params=params, timeout=15)
        data = res.json()
        status = data.get("status", "")
        if status in ["ZERO_RESULTS", "INVALID_REQUEST", "OVER_QUERY_LIMIT", "REQUEST_DENIED"]:
            return []
        return data.get("results", [])
    except:
        return []


def is_valid_place(place):
    types = place.get("types", [])
    bad_types = [
        "school", "university", "gym", "hospital", "lodging",
        "real_estate_agency", "bank", "shopping_mall", "store"
    ]
    return not any(t in bad_types for t in types)


def score_place(item):
    rating = item.get("rating", 0) or 0
    reviews = item.get("user_ratings_total", 0) or 0
    s = rating * 10
    s += min(reviews / 100, 20)
    if "restaurant" in item.get("types", []):
        s += 8
    if "meal_takeaway" in item.get("types", []):
        s += 4
    if "point_of_interest" in item.get("types", []):
        s += 2
    return s


def get_fallback_places(region, city):
    candidates = [
        f"{city} 맛집",
        f"{city} 전통시장",
        f"{city} 먹자골목",
        f"{city} 로컬푸드",
        f"{city} 분식거리",
        f"{city} 한식당",
        f"{city} 국밥거리",
        f"{city} 카페거리",
    ]
    places = []
    seen = set()
    for name in candidates:
        key = name.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        places.append({"title": name, "addr": f"{region} {city}", "raw": {}, "score": 0})
    return places


def get_places(region, city):
    pool = []
    seen = set()
    for q in get_queries(region, city):
        results = google_text_search(q, city=city, region=region)
        for r in results:
            name = r.get("name")
            if not name:
                continue
            key = name.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            if not is_valid_place(r):
                continue
            pool.append({
                "title": name,
                "addr": r.get("formatted_address", "주소 없음"),
                "raw": r,
                "score": score_place(r),
            })
        if len(pool) >= 10:
            break
    if not pool:
        pool = get_fallback_places(region, city)
    pool = sorted(pool, key=lambda x: x["score"], reverse=True)
    return pool[:10]


def get_overview_from_place(place):
    return place.get("raw", {}).get("formatted_address", "상세 설명이 제공되지 않습니다.")


def is_valid_image_url(url):
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10, stream=True)
        if res.status_code != 200:
            return False
        content_type = res.headers.get("content-type", "").lower()
        return "image" in content_type
    except:
        return False


def get_google_place_photos_by_name(place_name, max_photos=3, region="", city=""):
    if not GOOGLE_MAPS_API_KEY:
        return []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        q = " ".join([x for x in [region, city, place_name] if x]).strip()
        search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        search_params = {"query": q, "key": GOOGLE_MAPS_API_KEY, "language": "ko"}
        res = requests.get(search_url, params=search_params, headers=headers, timeout=15)
        data = res.json()
        if not data.get("results"):
            return []
        place_id = data["results"][0].get("place_id")
        if not place_id:
            return []
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {"place_id": place_id, "fields": "photos", "key": GOOGLE_MAPS_API_KEY, "language": "ko"}
        res2 = requests.get(details_url, params=details_params, headers=headers, timeout=15)
        data2 = res2.json()
        photos = data2.get("result", {}).get("photos", [])
        if isinstance(photos, dict):
            photos = [photos]
        photo_urls = []
        for p in photos[:max_photos]:
            ref = p.get("photo_reference")
            if not ref:
                continue
            photo_urls.append(
                "https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=1600&photo_reference={ref}&key={GOOGLE_MAPS_API_KEY}"
            )
        return photo_urls
    except:
        return []


def get_best_place_image(place):
    candidates = []
    title = place.get("title", "").strip()
    region = place.get("region", "").strip()
    city = place.get("city", "").strip()
    if title:
        candidates.extend(get_google_place_photos_by_name(title, max_photos=3, region=region, city=city))
    candidates = [x.strip() for x in candidates if x and isinstance(x, str)]
    verified = []
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if is_valid_image_url(url):
            verified.append(url)
        if len(verified) >= 3:
            break
    final_images = verified[:]
    for url in candidates:
        if len(final_images) >= 3:
            break
        if url not in final_images:
            final_images.append(url)
    fallback = "https://via.placeholder.com/800x500?text=No+Image"
    while len(final_images) < 3:
        final_images.append(fallback)
    return final_images[:3]


def _intro_prompt(region, city, title):
    return f"""
너는 한국 맛집 블로그 전문 작성자다.

아래 정보를 바탕으로 서론만 작성해라.
- 지역: {region}
- 도시: {city}
- 글 제목: {title}

조건:
- 5 문장
- 핵심 키워드 자연스럽게 포함
- 첫 문장에서 독자의 식욕과 호기심을 강하게 끌 것
- 너무 짧지 않게, 그러나 장황하지 않게
- 지역 분위기, 맛집 탐방 기대감, 추천 이유가 느껴지게
- HTML 사용 가능하지만 <p>와 <br>만 사용
- <p data-ke-size="size18"> 로 시작할 것
- 마크다운 금지
- 중국어/일본어 금지
"""


def _section_prompt(region, city, place_title, addr, overview):
    return f"""
너는 한국 맛집 블로그 전문 작성자다.

맛집 정보:
- 지역: {region}
- 도시: {city}
- 맛집명: {place_title}
- 주소: {addr}
- 참고설명: {overview}

작성 조건:
- 자연스러운 한국어
- 맛집 블로그 스타일
- 4문단
- 350자 이상
- 음식의 특징, 분위기, 추천 포인트, 방문 팁을 포함
- HTML 사용 가능, <p>와 <br>만 사용
- <p data-ke-size="size18"> 로 시작
- 제목 태그 금지
- 마크다운 금지
- 중국어/일본어 금지
"""


def clean_place_title(title, region, city):
    t = str(title or "").strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return ""

    # 1) 링크/마크다운 꼬리 제거
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)

    # 2) 다국어/설명 꼬리 제거
    #    예: "서울맛집 지강한식당 압구정본점 | restaurants | 韓国ナッコプセレストラン | 餐馆"
    #    -> "서울맛집 지강한식당 압구정본점"
    cut_markers = [
        " | ",
        "｜",
        " / ",
        " ・ ",
        " · ",
        " • ",
        " - ",
        " — ",
        " :: ",
    ]
    for marker in cut_markers:
        if marker in t:
            t = t.split(marker, 1)[0].strip()

    # 3) 언어별/설명형 키워드가 뒤에 붙은 경우 추가 제거
    #    영어/일본어/중국어/설명성 단어가 뒤로 이어질 때 정리
    t = re.split(
        r"\s+(?:restaurants?|restaurant|korean\s*restaurant|kbbq|kfood|grill|bar|cafe|"
        r"韓国料理|韓国焼肉レストラン|レストラン|グルメ|必食|餐馆|美食|食堂|"
        r"맛집|음식점|식당|branch|main\s*branch)\b",
        t,
        flags=re.IGNORECASE
    )[0].strip()

    # 4) 괄호 안 부가정보 제거
    t = re.sub(r"\s*\([^)]+\)\s*", " ", t).strip()
    t = re.sub(r"\s+", " ", t).strip()

    # 5) 지역명 반복 제거
    variants = [
        f"{region} {city}",
        f"{city} {city}",
        f"{region} {region}",
        city,
        region,
    ]
    for v in variants:
        v = v.strip()
        if not v:
            continue
        pattern = rf"^\s*{re.escape(v)}\s+"
        while re.match(pattern, t):
            t = re.sub(pattern, "", t).strip()

    t = re.sub(rf"^\s*{re.escape(city)}\s+", "", t).strip()
    t = re.sub(rf"^\s*{re.escape(region)}\s+", "", t).strip()
    t = re.sub(r"\s+", " ", t).strip()

    return t or title


def make_title(region, city):
    prefixes = [
        "현지인 추천",
        "요즘 핫한",
        "가성비 좋은",
        "재방문각",
        "로컬이 인정한",
        "숨은",
        "인기",
        "꼭 가봐야 할",
        "요즘 뜨는",
        "후회 없는",
        "줄 서는",
        "웨이팅 있는",
        "분위기 좋은",
        "실패 없는",
        "찐",
        "믿고 가는",
        "한 번쯤 가볼",
        "SNS에서 핫한",
        "주말에 가기 좋은",
        "입소문 난",
    ]

    suffixes = ["베스트 10", "top10"]

    prefix = random.choice(prefixes)
    suffix = random.choice(suffixes)

    return f"{region} {city} 맛집 {prefix} 식당 {suffix}"


def make_last(region, city):
    return (
        f"{city} 맛집은 지역 특색과 개성이 잘 드러나는 곳이 많아서 동선에 맞춰 고르면 만족도가 높습니다. "
        f"이번 글에서 소개한 곳들은 {city} 분위기와 잘 어울리는 식당들로 구성했습니다. "
        f"짧은 일정이라도 충분히 만족스러운 식사를 즐길 수 있으니 취향에 맞게 골라보시면 좋습니다."
    )


def build_post_html(region, city, title, places, thumb_url):
    intro_html = generate_ai_review(make_intro_prompt(region, city, title), title)
    intro_html = intro_html.replace('data-ke-size="size16"', 'data-ke-size="size18"')
    intro_html = intro_html.replace("size16", "size18")

    last_text = make_last(region, city)
    sections_html = ""
    fallback_img = "https://via.placeholder.com/800x500?text=No+Image"
    H2_STYLE = ""

    for idx, item in enumerate(places, start=1):
        clean_title = clean_place_title(item["title"], region, city)
        section_title = f"{city} {clean_title}"
        images = item.get("images", [])
        img_html = ""

        if len(images) >= 3:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <a href="{images[0]}" target="_blank"><img src="{images[0]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px; margin-bottom:10px;" alt="{clean_title} 1"></a><br>
    <a href="{images[1]}" target="_blank"><img src="{images[1]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px; margin-bottom:10px;" alt="{clean_title} 2"></a><br>
    <a href="{images[2]}" target="_blank"><img src="{images[2]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px;" alt="{clean_title} 3"></a>
</div>
"""
        elif len(images) == 2:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <a href="{images[0]}" target="_blank"><img src="{images[0]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px; margin-bottom:10px;" alt="{clean_title} 1"></a><br>
    <a href="{images[1]}" target="_blank"><img src="{images[1]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px;" alt="{clean_title} 2"></a>
</div>
"""
        elif len(images) == 1:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <a href="{images[0]}" target="_blank"><img src="{images[0]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px;" alt="{clean_title} 1"></a>
</div>
"""
        else:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <img src="{fallback_img}" style="max-width:100%; height:auto; border-radius:8px;" alt="{clean_title}">
</div>
"""

        map_link_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(f"{region} {city} {clean_title}")
        map_html = f"""
<div style="text-align:center; margin-bottom:25px;">
    <a href="{map_link_url}" target="_blank"
       style="color:#1a2a40; font-weight:bold; text-decoration:underline; font-size:15px;">
       🗺️ 구글 지도에서 위치 확인하기
    </a>
</div>
"""

        section_body = generate_ai_review(make_section_prompt(region, city, clean_title, item.get("addr", ""), item.get("overview", "")), clean_title)
        section_body = section_body.replace('data-ke-size="size16"', 'data-ke-size="size18"')
        section_body = section_body.replace("size16", "size18")

        sections_html += f"""
<br><br>
<h2 style="{H2_STYLE}">{idx}. {section_title}</h2>
<br>
{img_html}
<br>
{map_html}
<br><br>
{section_body}
<br><br>
"""

    ai_review_text = f"<p data-ke-size='size18'>{city}의 대표 맛집들을 중심으로 코스를 구성하면 훨씬 만족도 높은 식도락 여행이 됩니다.</p>"
    labels = ["맛집", "국내여행"]

    html_content = f"""
  <p data-ke-size="size18"><br /></p>
  {intro_html}
  <p style="text-align:center;">
  <p data-ke-size="size18"><br /></p>  
    <img src="{thumb_url}" alt="{title} 썸네일" style="max-width:100%; height:auto; border-radius:8px;">
  </p>
  <p data-ke-size="size18"><br /></p>  
  <div style="padding:12px;">
  <span><!--more--></span>
  <p data-ke-size="size18"><br /></p>
  <p data-ke-size="size18"><br /></p>
  <div class="mbtTOC"><button>  목차 </button>
  <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
  </div>
  
  {sections_html}
  <h2 style="{H2_STYLE}">{city} 맛집 총평</h2>
  {ai_review_text}
  <p data-ke-size="size18">{last_text}</p>
  <div style="margin-top:20px; color:#888;">{' '.join(['#'+x for x in labels])}</div>
  <script>mbtTOC();</script>
</div>
"""
    return html_content, labels


def generate_random_title(region, city):
    return make_title(region, city)


def find_next_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        city = row[0].strip() if len(row) > 0 and row[0] else ""
        region = row[1].strip() if len(row) > 1 and row[1] else ""
        code = row[2].strip() if len(row) > 2 and row[2] else ""
        status = row[4].strip() if len(row) > 4 and row[4] else ""
        if city and region and code and status != "완":
            return i, region, city
    return None, None, None


def main():
    row_idx, region, city = find_next_row(ws3)
    if not row_idx:
        print("처리할 행이 없습니다.")
        return

    log_step(row_idx, "1단계: 대상 행 선택")
    title = generate_random_title(region, city)
    log_step(row_idx, f"2단계: 제목 생성 ({title})")

    places = get_places(region, city)
    if not places:
        places = get_fallback_places(region, city)

    for p in places:
        p["region"] = region
        p["city"] = city
        p["title"] = clean_place_title(p["title"], region, city)
        p["images"] = get_best_place_image(p)
        p["overview"] = get_overview_from_place(p)
        p["desc"] = generate_ai_review(make_section_prompt(region, city, p["title"], p.get("addr", ""), p["overview"]), p["title"])
        p["desc"] = p["desc"].replace('data-ke-size="size16"', 'data-ke-size="size18"')
        p["desc"] = p["desc"].replace("size16", "size18")
        time.sleep(0.4)

    safe_title = re.sub(r'[\\/:*?"<>|.]', "_", title)
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, f"{safe_title}.png")
    make_thumb(thumb_path, title)
    log_step(row_idx, "3단계: 썸네일 생성 완료")

    thumb_url = upload_to_drive(thumb_path, f"{safe_title}.png")
    log_step(row_idx, "4단계: 썸네일 Drive 업로드 완료")

    html_content, labels = build_post_html(region, city, title, places, thumb_url)

    post_body = {
        "content": html_content,
        "title": title,
        "labels": labels,
        "blog": {"id": BLOG_ID}
    }

    try:
        res = blog_handler.posts().insert(
            blogId=BLOG_ID,
            body=post_body,
            isDraft=False,
            fetchImages=True
        ).execute()

        ws3.update_cell(row_idx, 5, "완")
        try:
            ws3.update_cell(row_idx, 15, res.get("url", ""))
        except:
            pass

        log_step(row_idx, f"5단계: Blogger 업로드 성공 ({res.get('url', '')})")
        print(f"[완료] 블로그 포스팅: {res.get('url', '')}")
        save_processed_region(region, city)

    except Exception as e:
        tb = traceback.format_exc().replace("\n", " | ")
        log_step(row_idx, f"5단계: Blogger 업로드 실패: {e} | {tb}")
        raise


if __name__ == "__main__":
    main()
