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
import subprocess
from datetime import datetime
import requests
from PIL import Image, ImageDraw, ImageFont

import gspread
from google.oauth2.service_account import Credentials as SA_Credentials
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

DEBUG_MODE = True

def dprint(*args):
    if DEBUG_MODE:
        print("[DEBUG]", *args)

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

TARGET_GITHUB_PAT = os.getenv("TARGET_GITHUB_PAT", "")
TARGET_REPO = "jm7004lee/jm7004lee.github.io"
TARGET_BRANCH = "main"
REPO_PATH = os.getenv("TARGET_REPO_PATH", os.getcwd())
POSTS_DIR = "_posts"

client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
genai_client = None
if GEMINI_API_KEY and genai:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        dprint("genai init failed:", e)
        genai_client = None

HISTORY_PATH = "processed_regions_blogger.json"
SHEET_GID = 2131907983

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

GITIGNORE_CONTENT = """2nd.json
2nd.json.b64
blogger_token.json
cc.json
cc.json.b64
drive_token_2nd.pickle
drive_token_2nd.pickle.b64
openai.json
openai.json.b64
sheetapi.json
sheetapi.json.b64
thumbnails/
"""

def ensure_gitignore(repo_path):
    path = os.path.join(repo_path, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(GITIGNORE_CONTENT)

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
    except Exception as e:
        dprint("drive upload first attempt failed:", e)
        folder_id = ensure_drive_folder(drive_service, "blogger")
        meta = {"name": file_name, "parents": [folder_id]}
        uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader", "allowFileDiscovery": False}
    ).execute()
    return f"https://lh3.googleusercontent.com/d/{uploaded['id']}"

def load_processed_regions():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("regions", [])
    except Exception as e:
        dprint("load_processed_regions failed:", e)
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
        return [text[i:i + width] for i in range(0, len(text), width)]
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

def make_thumb(save_path: str, var_title: str):
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

def generate_ai_review(prompt, keyword):
    last_err = None
    if genai_client:
        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception as e:
            last_err = e
            dprint("AI 실패 1:", e)
    if genai_client:
        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception as e:
            last_err = e
            dprint("AI 실패 2:", e)
    try:
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": "openrouter/auto", "messages": [{"role": "user", "content": prompt}]},
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
        dprint("AI 실패 3:", e)
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
            dprint("AI 실패 4:", e)
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
            dprint("AI 실패 5:", e)
    return f"{keyword} 설명 생성 실패: {last_err}"

def get_queries(region, city):
    return [
        f"{region} {city} 맛집",
        f"{region} {city} 식당",
        f"{region} {city} 추천 맛집",
        f"{region} {city} 인기 식당",
        f"{region} {city} 가볼만한 맛집",
        f"{city} 맛집",
        f"{city} 식당",
        f"{city} 추천 맛집",
        f"{city} 인기 식당",
        f"{city} 가볼만한 맛집",
    ]

def google_text_search(query, city="", region=""):
    if not GOOGLE_MAPS_API_KEY:
        return []
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_MAPS_API_KEY, "language": "ko", "type": "restaurant"}
    try:
        res = requests.get(url, params=params, timeout=15)
        data = res.json()
        status = data.get("status", "")
        if status in ["ZERO_RESULTS", "INVALID_REQUEST", "OVER_QUERY_LIMIT", "REQUEST_DENIED"]:
            return []
        return data.get("results", [])
    except Exception as e:
        dprint("google_text_search failed:", query, e)
        return []

def is_valid_place(place):
    types = place.get("types", [])
    bad_types = ["school", "university", "gym", "hospital", "lodging", "real_estate_agency", "bank", "shopping_mall", "store"]
    good_types = ["restaurant", "meal_takeaway", "meal_delivery", "point_of_interest", "food", "cafe"]
    return any(t in good_types for t in types) and not any(t in bad_types for t in types)

def score_place(item):
    rating = item.get("rating", 0) or 0
    reviews = item.get("user_ratings_total", 0) or 0
    s = rating * 10
    s += min(reviews / 100, 20)
    if "restaurant" in item.get("types", []):
        s += 10
    if "food" in item.get("types", []):
        s += 6
    if "meal_takeaway" in item.get("types", []):
        s += 4
    return s

def get_fallback_places(region, city):
    candidates = [
        f"{city} 맛집", f"{city} 식당", f"{city} 한식당", f"{city} 고기집",
        f"{city} 찌개집", f"{city} 국밥집", f"{city} 분식집", f"{city} 밥집"
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
            pool.append({"title": name, "addr": r.get("formatted_address", "주소 없음"), "raw": r, "score": score_place(r)})
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
            photo_urls.append("https://maps.googleapis.com/maps/api/place/photo?maxwidth=1600&photo_reference=" + ref + f"&key={GOOGLE_MAPS_API_KEY}")
        return photo_urls
    except Exception as e:
        dprint("place photo lookup failed:", place_name, e)
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

def make_intro_prompt(region, city, title):
    return f"""너는 한국 맛집 블로그 전문 작성자다.

아래 정보를 바탕으로 서론만 작성해라.
- 지역: {region}
- 도시: {city}
- 글 제목: {title}

조건:
- 5 문장
- 핵심 키워드 자연스럽게 포함
- 첫 문장에서 독자의 흥미를 끌 것
- 너무 짧지 않게, 그러나 장황하지 않게
- 지역 분위기, 식당 탐방 기대감, 추천 이유가 느껴지게
- 마크다운 금지
- <p>와 <br> 태그만 사용 가능
- <p> 로 시작할 것
- 중국어/일본어 금지
"""

def make_section_prompt(region, city, place_title, addr, overview):
    return f"""너는 한국 맛집 블로그 전문 작성자다.

맛집 정보:
- 지역: {region}
- 도시: {city}
- 장소명: {place_title}
- 주소: {addr}
- 참고설명: {overview}

작성 조건:
- 자연스러운 한국어
- 맛집 블로그 스타일
- 4문단
- 350자 이상
- 음식, 분위기, 추천 포인트, 방문 팁을 포함
- 마크다운 금지
- <p>와 <br> 태그만 사용 가능
- <p> 로 시작
- 제목 태그 금지
- 중국어/일본어 금지
"""

def clean_place_title(title, region, city):
    t = str(title or "").strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return ""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or title

def make_title(region, city):
    endings = ["BEST10", "TOP10", "추천 리스트10"]

    patterns = [
        "{region} {city} 맛집 {phrase} {ending}",
        "{region} {city} {phrase} 맛집 {ending}",
        "{region} {city} 맛집 추천 {phrase} {ending}",
        "{region} {city} 맛집 {phrase} 추천 {ending}",
    ]

    phrases = [
        "유명한 식당",
        "인기 식당",
        "현지인이 추천하는 식당",
        "음식점 추천 순위",
        "가성비 추천 음식점",
        "내돈내산 식당 순위",
        "내돈내산 식당 추천",
    ]

    phrase = random.choice(phrases)
    ending = random.choice(endings)
    pattern = random.choice(patterns)

    title = pattern.format(region=region, city=city, phrase=phrase, ending=ending)
    title = re.sub(r"\s+", " ", title).strip()

    # "추천" 중복 방지: 같은 제목 안에서 2번 이상 나오면 뒤쪽 것을 제거
    if title.count("추천") > 1:
        title = title.replace("추천 ", "", 1).strip()
        title = re.sub(r"\s+", " ", title).strip()

    return title

def generate_random_title(region, city):
    return make_title(region, city)

import random

def make_last(region, city):
    s1 = random.choice([
        f"{city} 맛집은 지역마다 개성이 달라서 취향에 맞는 식당을 찾는 재미가 있습니다.",
        f"{city}에는 분위기 좋은 맛집과 식당이 많아 외식 코스로도 만족도가 높습니다.",
        f"{city} 맛집을 찾고 있다면 이번 리스트를 기준으로 동선을 짜보는 것을 추천합니다.",
        f"{city}은(는) 먹거리 선택지가 다양해 한 끼 식사부터 특별한 외식까지 즐기기 좋습니다.",
        f"{city}에는 가족, 연인, 친구와 함께 방문하기 좋은 식당이 다양하게 있습니다.",
        f"{city} 맛집 여행은 짧은 일정으로도 충분히 만족스러운 식도락을 즐길 수 있습니다.",
        f"{city}은(는) 사계절 내내 다양한 메뉴를 맛볼 수 있는 인기 지역입니다.",
        f"{city}에는 사진 찍기 좋은 식당과 분위기 좋은 맛집이 많아 만족도가 높습니다.",
        f"{city}은(는) 지역만의 맛과 분위기를 함께 느낄 수 있는 식도락 코스로 유명합니다.",
        f"{city} 맛집은 누구와 함께 가도 좋은 다양한 식당을 만나볼 수 있습니다."
    ])

    s2 = random.choice([
        "대표 식당뿐 아니라 숨은 맛집도 함께 방문하면 더욱 알찬 외식이 됩니다.",
        "유명한 맛집과 로컬 식당을 함께 둘러보면 더욱 만족도가 높습니다.",
        "동선을 미리 계획하면 하루 동안 여러 곳을 효율적으로 방문할 수 있습니다.",
        "계절에 따라 색다른 메뉴와 분위기를 즐길 수 있는 것도 큰 장점입니다.",
        "주말 외식이나 당일치기 식도락 코스로도 부담 없이 다녀오기 좋습니다.",
        "사진 촬영하기 좋은 식당도 많아 추억을 남기기에도 좋습니다.",
        "카페와 함께 방문하면 더욱 풍성한 하루가 됩니다.",
        "아이들과 함께 방문하기 좋은 식당도 다양하게 준비되어 있습니다.",
        "연인들의 데이트 식당으로도 꾸준히 인기를 얻고 있습니다.",
        "가족 외식 코스로도 만족도가 높은 지역입니다."
    ])

    s3 = random.choice([
        f"이번에 소개한 {city} 맛집은 실제 방문 만족도가 높은 장소를 중심으로 선정했습니다.",
        f"이번 {city} 식당 추천 리스트는 많은 사람들이 찾는 인기 장소를 담았습니다.",
        f"소개한 {city} 맛집들은 처음 방문하는 분들에게도 추천할 만한 곳들입니다.",
        f"{city} 대표 식당을 중심으로 외식 계획을 세우면 더욱 편리합니다.",
        f"식도락 일정을 짤 때 이번 리스트를 참고하면 도움이 될 것입니다.",
        f"여행과 외식을 함께 계획하는 분들에게 도움이 되는 장소만 엄선했습니다.",
        f"현지에서도 많이 찾는 식당을 중심으로 정리했습니다.",
        f"다양한 취향을 고려하여 인기 맛집을 골고루 소개했습니다.",
        f"짧은 일정에도 둘러보기 좋은 식당을 중심으로 구성했습니다.",
        f"재방문 만족도가 높은 맛집을 우선적으로 선정했습니다."
    ])

    s4 = random.choice([
        "취향에 맞는 식당을 선택해 여유롭게 둘러보시기 바랍니다.",
        "외식 일정에 맞춰 원하는 장소를 자유롭게 선택해 보세요.",
        "여유 있는 일정이라면 주변 식당도 함께 방문하는 것을 추천합니다.",
        "가까운 카페와 함께 둘러보면 더욱 만족스러운 하루가 됩니다.",
        "사진 촬영 포인트도 함께 찾아보면 더욱 즐거운 시간이 됩니다.",
        "계절에 따라 전혀 다른 분위기를 느낄 수 있습니다.",
        "날씨가 좋은 날 방문하면 더욱 좋은 경험이 됩니다.",
        "주변 맛집과 함께 코스를 구성하면 더욱 알찬 일정이 됩니다.",
        "당일치기 식도락 여행으로도 충분히 만족할 수 있습니다.",
        "동선을 미리 계획하면 시간을 더욱 효율적으로 사용할 수 있습니다."
    ])

    s5 = random.choice([
        f"즐거운 {city} 맛집 탐방 되시길 바랍니다.",
        f"{city}에서 좋은 식당 많이 발견하시기 바랍니다.",
        f"이번 외식이 오래 기억에 남는 시간이 되길 바랍니다.",
        f"소중한 사람과 함께 행복한 식사를 즐겨보세요.",
        f"알찬 맛집 코스로 멋진 하루를 보내시기 바랍니다.",
        f"다음 맛집 정보로 다시 찾아뵙겠습니다.",
        f"식사 계획에 이번 정보가 도움이 되었기를 바랍니다.",
        f"맛있고 즐거운 하루 되시기 바랍니다.",
        f"행복한 식도락과 멋진 추억을 만들어 보세요.",
        f"만족스러운 외식이 되기를 응원합니다."
    ])

    return "\n\n".join([s1, s2, s3, s4, s5])

def build_markdown_post(region, city, title, places, thumb_url, date_str):
    intro = generate_ai_review(make_intro_prompt(region, city, title), title)
    sections = []
    for idx, item in enumerate(places, start=1):
        clean_title = clean_place_title(item["title"], region, city)
        section_title = f"{region} {city} 맛집추천 - {clean_title}"
        images = item.get("images", [])
        overview = item.get("overview", "")
        body = generate_ai_review(make_section_prompt(region, city, clean_title, item.get("addr", ""), overview), clean_title)
        sec = []
        sec.append(f"## {idx}. {section_title}")
        sec.append("")
        for img in images[:3]:
            sec.append(f"![{clean_title}]({img})")
            sec.append("")
        if images:
            sec.append(f"[구글 지도에서 위치 확인하기](https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(region + ' ' + city + ' ' + clean_title)})")
            sec.append("")
        if item.get("addr"):
            sec.append(f"- 주소: {item['addr']}")
            sec.append("")
        sec.append(body)
        sec.append("")
        sections.append("\n".join(sec))
    last_text = make_last(region, city)
    cat = "맛집추천"
    md = f"""---
title: "{title}"
date: {date_str}
categories: [{cat}]
tags: [{cat}, {city}, {region}]
image: {thumb_url}
---

{intro}

![{title}]({thumb_url})

{chr(10).join(sections)}

## {city} 맛집 총평

{last_text}
"""
    return md

def find_next_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        city = row[0].strip() if len(row) > 0 and row[0] else ""
        region = row[1].strip() if len(row) > 1 and row[1] else ""
        code = row[2].strip() if len(row) > 2 and row[2] else ""
        status = row[5].strip() if len(row) > 5 and row[5] else ""
        if city and region and code and status != "완":
            return i, region, city
    return None, None, None

def git_run(cmd, cwd=None, env=None):
    dprint("git cmd:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    return result

def push_post_to_github(file_path, repo_path):
    if not TARGET_GITHUB_PAT:
        raise RuntimeError("TARGET_GITHUB_PAT 환경변수가 없습니다.")
    if not os.path.exists(os.path.join(repo_path, ".git")):
        raise RuntimeError(f"Git 저장소가 아닙니다: {repo_path}")
    ensure_gitignore(repo_path)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    rel_path = os.path.relpath(file_path, repo_path)
    git_run(["git", "config", "user.name", "github-actions[bot]"], cwd=repo_path, env=env)
    git_run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=repo_path, env=env)
    remote_url = f"https://x-access-token:{TARGET_GITHUB_PAT}@github.com/{TARGET_REPO}.git"
    git_run(["git", "remote", "set-url", "origin", remote_url], cwd=repo_path, env=env)
    git_run(["git", "fetch", "origin", TARGET_BRANCH], cwd=repo_path, env=env)
    git_run(["git", "switch", "main"], cwd=repo_path, env=env)
    git_run(["git", "reset", "--hard", f"origin/{TARGET_BRANCH}"], cwd=repo_path, env=env)
    git_run(["git", "add", rel_path], cwd=repo_path, env=env)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env).stdout.strip()
    if not status:
        return "no changes"
    git_run(["git", "commit", "-m", f"Add post: {os.path.basename(file_path)}"], cwd=repo_path, env=env)
    git_run(["git", "push", "origin", TARGET_BRANCH], cwd=repo_path, env=env)
    return f"pushed to {TARGET_BRANCH}"

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
        time.sleep(0.4)
    safe_title = re.sub(r'[\\/:*?"<>|.]', "_", title)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S +0900")
    post_filename = f"{datetime.now().strftime('%Y-%m-%d')}-{safe_title}.md"
    post_path = os.path.join(REPO_PATH, POSTS_DIR, post_filename)
    os.makedirs(os.path.dirname(post_path), exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, f"{safe_title}.png")
    make_thumb(thumb_path, title)
    log_step(row_idx, "3단계: 썸네일 생성 완료")
    thumb_url = upload_to_drive(thumb_path, f"{safe_title}.png")
    log_step(row_idx, "4단계: 썸네일 Drive 업로드 완료")
    markdown_content = build_markdown_post(region, city, title, places, thumb_url, date_str)
    with open(post_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    log_step(row_idx, "5단계: Markdown 파일 생성 완료")
    push_state = push_post_to_github(post_path, REPO_PATH)
    ws3.update_cell(row_idx, 6, "완")
    try:
        ws3.update_cell(row_idx, 15, f"https://github.com/{TARGET_REPO}/blob/{TARGET_BRANCH}/{POSTS_DIR}/{post_filename}")
    except Exception as e:
        dprint("link write failed:", e)
    log_step(row_idx, f"6단계: GitHub 업로드 {push_state}")
    print(f"[완료] {post_path}")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
