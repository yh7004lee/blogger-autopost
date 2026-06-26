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


# =========================
# API 키 - GitHub Secrets 에서 읽기
# =========================
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
SHEET_ID = secrets.get("SHEET_ID", "6498243474990332474")
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "")
GOOGLE_MAPS_API_KEY = secrets.get("GOOGLE_MAPS_API_KEY", "")
TOUR_API_KEY = secrets.get("TOUR_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
genai_client = None
if GEMINI_API_KEY and genai:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        genai_client = None


# =========================
# 기본 설정
# =========================
BLOG_ID = "6498243474990332474"
SHEET_TAB_INDEX = 2  # Sheet3 = 세 번째 시트
HISTORY_PATH = "processed_regions_blogger.json"

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

error_logs = []


# =========================
# Google Sheets 인증 (Sheet3)
# =========================
def get_sheet3():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws3 = sh.worksheet("Sheet3")
    except Exception:
        ws3 = sh.get_worksheet(SHEET_TAB_INDEX)
    return ws3


ws3 = get_sheet3()


# =========================
# Google Drive 인증
# =========================
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
    folder_id = DRIVE_FOLDER_ID or ensure_drive_folder(drive_service, "blogger")
    media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
    meta = {"name": file_name, "parents": [folder_id]}
    uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader", "allowFileDiscovery": False}
    ).execute()
    return f"https://lh3.googleusercontent.com/d/{uploaded['id']}"


# =========================
# Blogger 인증
# =========================
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise RuntimeError("blogger_token.json 없음 — Blogger 사용자 인증 정보가 필요합니다.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)


blog_handler = get_blogger_service()


# =========================
# 히스토리 관리
# =========================
def load_processed_regions():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("regions", [])
    except:
        return []


def save_processed_region(region):
    processed = load_processed_regions()
    if region not in processed:
        processed.append(region)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"regions": processed}, f, ensure_ascii=False, indent=2)


# =========================
# 로그 기록
# =========================
def log_step(row, msg: str):
    try:
        prev = ws3.cell(row, 16).value or ""
        ws3.update_cell(row, 16, f"{prev} | {msg}" if prev else msg)
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)


# =========================
# 썸네일 생성
# =========================
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


# =========================
# HTML 정리 / AI
# =========================
def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)


def generate_ai_review(prompt, keyword):
    if genai_client:
        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            return response.text.strip()
        except:
            pass

    if client:
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1200
            )
            return res.choices[0].message.content.strip()
        except:
            pass

    return f"{keyword} 설명 생성 실패"


# =========================
# 장소 수집
# =========================
def get_queries(city):
    return [
        f"{city} 관광지",
        f"{city} 가볼만한곳",
        f"{city} 여행지",
        f"{city} 명소",
        f"{city} 핫플",
    ]


def google_text_search(query):
    if not GOOGLE_MAPS_API_KEY:
        return []
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_MAPS_API_KEY, "language": "ko"}
    res = requests.get(url, params=params, timeout=15)
    return res.json().get("results", [])


def is_valid_place(place):
    types = place.get("types", [])
    bad_types = [
        "school", "university", "gym", "hospital", "lodging",
        "real_estate_agency", "bank", "shopping_mall", "store",
        "restaurant", "cafe"
    ]
    return not any(t in bad_types for t in types)


def score_place(item):
    rating = item.get("rating", 0) or 0
    reviews = item.get("user_ratings_total", 0) or 0
    s = rating * 10
    s += min(reviews / 100, 20)
    if "tourist_attraction" in item.get("types", []):
        s += 5
    if "park" in item.get("types", []):
        s += 3
    if "museum" in item.get("types", []):
        s += 4
    if "point_of_interest" in item.get("types", []):
        s += 2
    return s


def get_places(region):
    pool = []
    seen = set()
    for q in get_queries(region):
        results = google_text_search(q)
        for r in results:
            name = r.get("name")
            if not name:
                continue
            key = name.lower()
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
    pool = sorted(pool, key=lambda x: x["score"], reverse=True)
    return pool[:10]


def get_overview_from_place(place):
    return place.get("raw", {}).get("formatted_address", "상세 설명이 제공되지 않습니다.")


# =========================
# 이미지 로직
# =========================
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
    except Exception as e:
        print(f"[Google 이미지 실패] {place_name} / {e}")
        return []


def get_best_place_image(place):
    candidates = []
    title = place.get("title", "").strip()
    region = place.get("region", "").strip()
    city = place.get("city", "").strip()

    if title:
        candidates.extend(get_google_place_photos_by_name(title, max_photos=3, region=region, city=city))

    if len(candidates) < 3 and region and city and title:
        more = get_google_place_photos_by_name(title, max_photos=3 - len(candidates), region=region, city=city)
        for url in more:
            if url not in candidates:
                candidates.append(url)

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


# =========================
# 본문 텍스트
# =========================
def make_intro(region, city, title):
    return (
        f"{region} {city} 여행을 준비하시는 분들을 위해, "
        f"현지에서 많이 찾는 명소를 중심으로 알차게 정리했습니다. "
        f"{title} 기준으로 꼭 참고할 만한 곳들만 선별해 소개합니다."
    )


def make_last(region, city):
    return (
        f"{region} {city} 여행이 더욱 알차고 즐거운 시간이 되시길 바랍니다. "
        f"방문 전 운영시간과 휴무일을 한 번 더 확인하시면 더 편안한 여행이 됩니다."
    )


def make_section_text(region, city, title, addr, overview):
    return (
        f"<p data-ke-size='size18'><b>{title}</b>은(는) {addr}에 위치한 {region} {city}의 대표적인 관광지입니다.</p>"
        f"<p data-ke-size='size18'>현지 분위기를 느끼기 좋고, 여행 동선에 넣기에도 부담이 적은 곳으로 많이 찾습니다.</p>"
        f"<p data-ke-size='size18'>{clean_html(overview) if overview else '방문 시에는 주변 명소와 함께 둘러보면 더욱 좋습니다.'}</p>"
        f"<p data-ke-size='size18'>여행 일정에 맞춰 여유롭게 방문하면 더 만족스러운 시간을 보낼 수 있습니다.</p>"
    )


# =========================
# 시트에서 대상 행 찾기
# A열=지역, B열=도시, D열=완
# =========================
def find_next_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        region = row[0].strip() if len(row) > 0 and row[0] else ""
        city = row[1].strip() if len(row) > 1 and row[1] else ""
        status = row[3].strip() if len(row) > 3 and row[3] else ""
        if region and city and status != "완":
            return i, region, city
    return None, None, None


# =========================
# Blogger 포스팅 본문 생성
# =========================
def build_post_html(region, city, title, places, thumb_url):
    intro_text = make_intro(region, city, title)
    last_text = make_last(region, city)

    list_items = ""
    for idx, item in enumerate(places, start=1):
        list_items += (
            f"&nbsp;&nbsp;<span style='color:#676767; text-decoration:underline;'>"
            f"{idx}. {item['title']}</span><br />\n"
        )

    summary_table_html = f"""
<p data-ke-size="size18">&nbsp;</p>
<table style="border-collapse: collapse; width: 100%;" border="1" data-ke-align="alignLeft">
<tbody>
<tr>
<td style="background-color: #ffffff;">
<div>
<br />
<span style="background-color: #ffffff; color: #555555;">&nbsp;&nbsp;■ 목차 (Table of Contents)</span>
<br /><p data-ke-size="size18">&nbsp;</p>
{list_items}
<br />
</div>
</td>
</tr>
</tbody>
</table>
<p data-ke-size="size18">&nbsp;</p>
"""

    sections_html = ""
    fallback_img = "https://via.placeholder.com/800x500?text=No+Image"

    H2_STYLE = (
        "font-size:21px;"
        "color:#1a2a40;"
        "border-left:10px solid #1a2a40;"
        "padding:15px 20px 5px 20px;"
        "background-color:#f7f9fa;"
        "font-weight:bold;"
        "letter-spacing:-0.5px;"
        "line-height:1.4;"
    )

    for idx, item in enumerate(places, start=1):
        images = item.get("images", [])
        img_html = ""

        if len(images) >= 3:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <a href="{images[0]}" target="_blank"><img src="{images[0]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px; margin-bottom:10px;" alt="{item['title']} 1"></a><br>
    <a href="{images[1]}" target="_blank"><img src="{images[1]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px; margin-bottom:10px;" alt="{item['title']} 2"></a><br>
    <a href="{images[2]}" target="_blank"><img src="{images[2]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px;" alt="{item['title']} 3"></a>
</div>
"""
        elif len(images) == 2:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <a href="{images[0]}" target="_blank"><img src="{images[0]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px; margin-bottom:10px;" alt="{item['title']} 1"></a><br>
    <a href="{images[1]}" target="_blank"><img src="{images[1]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px;" alt="{item['title']} 2"></a>
</div>
"""
        elif len(images) == 1:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <a href="{images[0]}" target="_blank"><img src="{images[0]}" onerror="this.onerror=null;this.src='{fallback_img}'" style="max-width:100%; height:auto; border-radius:8px;" alt="{item['title']} 1"></a>
</div>
"""
        else:
            img_html = f"""
<div style="text-align:center; margin:20px 0;">
    <img src="{fallback_img}" style="max-width:100%; height:auto; border-radius:8px;" alt="{item['title']}">
</div>
"""

        map_link_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(
            f"{region} {city} {item['title']}"
        )
        map_html = f"""
<div style="text-align:center; margin-bottom:25px;">
    <a href="{map_link_url}" target="_blank"
       style="color:#1a2a40; font-weight:bold; text-decoration:underline; font-size:15px;">
       🗺️ 구글 지도에서 위치 확인하기
    </a>
</div>
"""

        sections_html += f"""
<br><br>
<h2 style="{H2_STYLE}">{idx}. {region} {city} {item['title']}</h2>
<br>
{img_html}
<br>
{map_html}
<br><br>
{item['desc']}
<br><br>
"""

    ai_review_text = f"<p data-ke-size='size18'>{region} {city}의 대표 관광지들을 중심으로 여행 코스를 구성하면 더욱 알찬 일정이 됩니다.</p>"
    labels = ["여행", "국내여행", region, city]

    html_content = f"""
<div style="padding:12px;">
  <p style="font-size:18px; color:#333; font-weight:bold;">{title}</p>
  <p data-ke-size="size18">{intro_text}</p>
  <p style="text-align:center;">
    <img src="{thumb_url}" alt="{title} 썸네일" style="max-width:100%; height:auto; border-radius:8px;">
  </p>
  {summary_table_html}
  {sections_html}
  <h2 style="{H2_STYLE}">여행 총평</h2>
  {ai_review_text}
  <p data-ke-size="size18">{last_text}</p>
  <div style="margin-top:20px; color:#888;">{' '.join(['#'+x for x in labels])}</div>
</div>
"""
    return html_content, labels


# =========================
# 제목 생성
# =========================
def generate_random_title(region, city):
    keywords = ["여행지", "숨은 명소", "데이트 코스", "가족여행", "당일치기 코스", "주말여행", "핫플레이스"]
    suffixes = ["TOP10", "BEST10", "추천 10선"]
    return f"{region} {city} 가볼만한곳 {random.choice(keywords)} {random.choice(suffixes)}"


# =========================
# 메인
# =========================
def main():
    row_idx, region, city = find_next_row(ws3)
    if not row_idx:
        print("처리할 행이 없습니다.")
        return

    log_step(row_idx, "1단계: 대상 행 선택")
    title = generate_random_title(region, city)
    log_step(row_idx, f"2단계: 제목 생성 ({title})")

    places = get_places(f"{region} {city}")
    if not places:
        places = get_places(city)
    if not places:
        raise RuntimeError("관광지 후보를 찾지 못했습니다.")

    for p in places:
        p["region"] = region
        p["city"] = city
        p["images"] = get_best_place_image(p)
        p["overview"] = get_overview_from_place(p)

        prompt = f"""
관광지명 : {p['title']}
지역 : {region}
도시 : {city}
주소 : {p.get('addr', '')}
원본 설명 : {p.get('overview', '')}

조건
- 여행 블로그 스타일
- 자연스러운 한국어
- 4 문단
- 350 자 이상
- 장점 설명
- 방문 포인트 설명
- HTML 사용 가능 (단, <p>, <br>만 사용)
- 마크다운 절대 금지
- <h1>, <h2>, <h3> 등 제목 태그 절대 넣지 말 것
- "**" 마크다운 강조 기호 절대 넣지 말 것
- <p data-ke-size="size18"> 을 넣어서 문장을 만들것
- 중국어나 일본어 금지
"""
        p["desc"] = generate_ai_review(prompt, p["title"])
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

        ws3.update_cell(row_idx, 4, "완")
        try:
            ws3.update_cell(row_idx, 15, res.get("url", ""))
        except:
            pass

        log_step(row_idx, f"5단계: Blogger 업로드 성공 ({res.get('url', '')})")
        print(f"[완료] 블로그 포스팅: {res.get('url', '')}")

        save_processed_region(f"{region} {city}")

    except Exception as e:
        tb = traceback.format_exc().replace("\n", " | ")
        log_step(row_idx, f"5단계: Blogger 업로드 실패: {e} | {tb}")
        raise


if __name__ == "__main__":
    main()
