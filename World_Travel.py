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
import requests
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


API_KEYS_JSON = os.getenv("API_KEYS_JSON")
if not API_KEYS_JSON:
    raise RuntimeError("API_KEYS_JSON 환경변수가 없습니다. GitHub Secrets 를 확인하세요.")

secrets = json.loads(API_KEYS_JSON)

SHEET_ID = "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs"
SHEET_GID = 2131907983
HISTORY_PATH = "processed_overseas_blogger.json"

BLOG_ID = "6498243474990332474"
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "")
TOUR_API_KEY = "b44cf66c9e3e7aa2d0bf19c049280d1859ddbd841ef14a571b79aab21d044a7f"
GOOGLE_MAPS_API_KEY = "AIzaSyBiLiWI4rTtdk_IW-f26uEIkhnKjEBHI1w"


OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"


def clean_html(raw_html):
    return BeautifulSoup(raw_html or "", "html.parser").get_text(separator="\n", strip=True)

def normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()

def load_processed_pairs():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("pairs", [])
    except:
        return []

def save_processed_pair(country, city):
    processed = load_processed_pairs()
    key = f"{country}|{city}"
    if key not in processed:
        processed.append(key)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"pairs": processed}, f, ensure_ascii=False, indent=2)

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

def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise RuntimeError("blogger_token.json 없음 — Blogger 사용자 인증 정보가 필요합니다.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(data, ["https://www.googleapis.com/auth/blogger"])
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()


def get_sheet4():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    for ws in sh.worksheets():
        if ws.id == SHEET_GID:
            return ws
    raise RuntimeError(f"gid={SHEET_GID} 시트를 찾지 못했습니다.")

ws4 = get_sheet4()

def find_next_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        country = row[0].strip() if len(row) > 0 and row[0] else ""
        city = row[1].strip() if len(row) > 1 and row[1] else ""
        status = row[2].strip() if len(row) > 2 and row[2] else ""
        if country and city and status != "완":
            return i, country, city
    return None, None, None

def log_step(row, msg):
    try:
        prev = ws4.cell(row, 16).value or ""
        ws4.update_cell(row, 16, f"{prev} | {msg}" if prev else msg)
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)

def generate_random_title(country, city):
    keywords = ["여행지", "숨은 명소", "데이트 코스", "가족여행", "당일치기 코스", "주말여행", "핫플레이스"]
    suffixes = ["TOP10", "BEST10", "추천 10선"]
    return f"{country} {city} 가볼만한곳 {random.choice(keywords)} {random.choice(suffixes)}"

def clean_place_title(title, country, city):
    t = normalize_text(title)
    for token in [country, city]:
        if token:
            t = re.sub(rf"^\s*{re.escape(token)}\s+", "", t).strip()
    return t

def build_display_title(country, city, place_title):
    clean_place = clean_place_title(place_title, country, city)
    return f"{country} {city} {clean_place}".strip() if clean_place else f"{country} {city}"

def build_map_search_keyword(country, city, place_title):
    clean_place = clean_place_title(place_title, country, city)
    return f"{country} {city} {clean_place}".strip() if clean_place else f"{country} {city}"

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
        f"{city} 대표 명소", f"{city} 야경 명소", f"{city} 산책 코스", f"{city} 전통시장", f"{city} 공원",
        f"{city} 전망대", f"{city} 포토스팟", f"{city} 문화명소", f"{city} 맛집거리", f"{city} 인기 관광지",
    ]
    return [{"contentId": "", "title": name, "addr": "", "raw": {}, "score": 0} for name in fallback]

def serper_places_search(country, city):
    return []

def get_places(country, city):
    results = []
    seen = set()
    try:
        raw_places = serper_places_search(country, city)
    except Exception:
        raw_places = []

    for item in raw_places:
        title = clean_place_title(item.get("title", ""), country, city)
        addr = item.get("address", "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        if addr and not is_valid_address(addr):
            addr = ""
        results.append({"contentId": item.get("contentId", "") or "", "title": title, "addr": addr, "raw": item, "score": 0})
        if len(results) >= 10:
            break

    if len(results) < 10:
        for p in get_default_places(country, city):
            if len(results) >= 10:
                break
            if p["title"].lower() not in seen:
                results.append(p)

    return results[:10]

def get_overview(content_id, country, city, title):
    return f"{title}는 {country} {city} 여행에서 추천할 만한 장소입니다."

def get_place_images(place, count=3, country="", city=""):
    return []

def generate_ai_review(prompt, keyword=""):
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
                max_tokens=1400
            )
            return res.choices[0].message.content.strip()
        except:
            pass
    return f"{keyword} 설명 생성 실패"

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

def build_post_html(country, city, title, places, thumb_url):
    intro_html = generate_ai_review(make_intro_prompt(country, city, title), title)
    intro_html = intro_html.replace('data-ke-size="size16"', 'data-ke-size="size18"').replace("size16", "size18")
    last_text = make_last(country, city)

    sections_html = ""
    for idx, item in enumerate(places, start=1):
        section_title = build_display_title(country, city, item["title"])
        map_keyword = build_map_search_keyword(country, city, item["title"])
        map_link_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(map_keyword)
        desc = item["desc"].replace("\n", "<br>")

        sections_html += f"""
<br><br>
<h2>{idx}. {section_title}</h2>
<br>
<div style="text-align:center; margin-bottom:25px;">
    <a href="{map_link_url}" target="_blank" style="color:#1a2a40;font-weight:bold;text-decoration:underline;font-size:15px;">
      🗺️ 구글 지도에서 위치 확인하기
    </a>
</div>
<br><br>
<p style="font-size:15px; color:#555; line-height:1.9;">
    {desc}
</p>
<br><br>
"""

    ai_review_text = f"<p data-ke-size='size18'>{country} {city}의 대표 관광지들을 중심으로 여행 코스를 구성하면 더욱 알찬 일정이 됩니다.</p>"
    labels = ["해외여행", "여행"]

    html_content = f"""
<div style="padding:12px;">
  <span><!--more--></span>
  <p data-ke-size="size18"><br /></p>
  <p data-ke-size="size18"><br /></p>
  <div class="mbtTOC"><button> 목차 </button>
  <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
  </div>
  <p style="font-size:18px; color:#333; font-weight:bold;">{title}</p>
  {intro_html}
  <p style="text-align:center;">
    <img src="{thumb_url}" alt="{title} 썸네일" style="max-width:100%; height:auto; border-radius:8px;">
  </p>
  {sections_html}
  <h2>{city} 여행 총평</h2>
  {ai_review_text}
  <p data-ke-size="size18">{last_text}</p>
  <div style="margin-top:20px; color:#888;">{' '.join(['#' + x for x in labels])}</div>
  <script>mbtTOC();</script>
</div>
"""
    return html_content, labels

def main():
    rows = ws4.get_all_values()
    row_idx, country, city = None, None, None

    for i, row in enumerate(rows[1:], start=2):
        c = row[0].strip() if len(row) > 0 and row[0] else ""
        ci = row[1].strip() if len(row) > 1 and row[1] else ""
        status = row[2].strip() if len(row) > 2 and row[2] else ""
        if c and ci and status != "완":
            row_idx, country, city = i, c, ci
            break

    if not row_idx:
        print("처리할 행이 없습니다.")
        return

    log_step(row_idx, "1단계: 대상 행 선택")
    title = generate_random_title(country, city)
    log_step(row_idx, f"2단계: 제목 생성 ({title})")

    places = get_places(country, city)
    if not places:
        places = get_default_places(country, city)

    travel_sections = []
    for p in places:
        p["title"] = clean_place_title(p["title"], country, city)
        p["overview"] = get_overview(p.get("contentId", ""), country, city, p["title"])
        p["images"] = get_place_images(p, count=3, country=country, city=city)
        p["image"] = p["images"][0] if p["images"] else ""
        prompt = make_section_prompt(country, city, p["title"], p.get("addr", ""), p.get("overview", ""))
        p["desc"] = generate_ai_review(prompt, p["title"])
        p["desc"] = re.sub(r"<h1[^>]*>.*?</h1>", "", p["desc"], flags=re.IGNORECASE)
        p["desc"] = p["desc"].replace("**", "")
        p["desc"] = p["desc"].replace('data-ke-size="size16"', 'data-ke-size="size18"')
        p["desc"] = p["desc"].replace("size16", "size18")
        travel_sections.append(p)
        time.sleep(0.4)

    safe_title = re.sub(r'[\\/:*?"<>|.]', "_", title)
    os.makedirs(THUMB_DIR, exist_ok=True)
    thumb_path = os.path.join(THUMB_DIR, f"{safe_title}.png")
    make_thumb(thumb_path, title)
    log_step(row_idx, "3단계: 썸네일 생성 완료")

    thumb_url = upload_to_drive(thumb_path, f"{safe_title}.png")
    log_step(row_idx, "4단계: 썸네일 Drive 업로드 완료")

    html_content, labels = build_post_html(country, city, title, travel_sections, thumb_url)

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

        ws4.update_cell(row_idx, 3, "완")
        try:
            ws4.update_cell(row_idx, 15, res.get("url", ""))
        except:
            pass

        log_step(row_idx, f"5단계: Blogger 업로드 성공 ({res.get('url', '')})")
        print(f"[완료] 블로그 포스팅: {res.get('url', '')}")
        save_processed_pair(country, city)

    except Exception as e:
        tb = traceback.format_exc().replace("\n", " | ")
        log_step(row_idx, f"5단계: Blogger 업로드 실패: {e} | {tb}")
        raise

if __name__ == "__main__":
    main()
