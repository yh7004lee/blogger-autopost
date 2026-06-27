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

# =========================
# 환경변수 / 시크릿
# =========================
API_KEYS_JSON = os.getenv("API_KEYS_JSON")
if not API_KEYS_JSON:
    raise RuntimeError("API_KEYS_JSON 환경변수가 없습니다. GitHub Secrets 를 확인하세요.")

secrets = json.loads(API_KEYS_JSON)

SHEET_ID = "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs"
SHEET_GID = 2131907983
HISTORY_PATH = "processed_overseas_blogger.json"

BLOG_ID = "6498243474990332474"
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "")
TOUR_API_KEY = secrets.get("TOUR_API_KEY", "")
GOOGLE_MAPS_API_KEY = "AIzaSyBiLiWI4rTtdk_IW-f26uEIkhnKjEBHI1w"

OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

COUNTRY_CITY_PAIRS = [
    ("일본", "도쿄"), ("일본", "오사카"), ("일본", "교토"), ("일본", "고베"), ("일본", "후쿠오카"),
    ("일본", "삿포로"), ("일본", "나고야"), ("일본", "오키나와"), ("일본", "요코하마"), ("일본", "가나자와"),
    ("베트남", "다낭"), ("베트남", "나트랑"), ("베트남", "하노이"), ("베트남", "호치민"), ("베트남", "푸꾸옥"),
    ("베트남", "하롱베이"), ("베트남", "호이안"), ("베트남", "사파"), ("베트남", "후에"), ("베트남", "달랏"),
    ("중국", "상하이"), ("중국", "베이징"), ("중국", "칭다오"), ("중국", "장가계"), ("중국", "광저우"),
    ("중국", "선전"), ("중국", "항저우"), ("중국", "시안"), ("중국", "청두"), ("중국", "하이난"),
    ("태국", "방콕"), ("태국", "푸켓"), ("태국", "파타야"), ("태국", "치앙마이"), ("태국", "끄라비"),
    ("태국", "코사무이"), ("태국", "후아힌"), ("태국", "아유타야"), ("태국", "카오락"), ("태국", "수코타이"),
    ("필리핀", "세부"), ("필리핀", "보라카이"), ("필리핀", "보홀"), ("필리핀", "마닐라"), ("필리핀", "팔라완"),
    ("필리핀", "클락"), ("필리핀", "다바오"), ("필리핀", "수빅"), ("필리핀", "일로일로"), ("필리핀", "바기오"),
    ("미국", "뉴욕"), ("미국", "로스앤젤레스"), ("미국", "하와이"), ("미국", "라스베이거스"), ("미국", "샌프란시스코"),
    ("미국", "시애틀"), ("미국", "시카고"), ("미국", "마이애미"), ("미국", "워싱턴"), ("미국", "보스턴"),
    ("대만", "타이베이"), ("대만", "가오슝"), ("대만", "타이중"), ("대만", "지우펀"), ("대만", "단수이"),
    ("대만", "타이난"), ("대만", "화롄"), ("대만", "컨딩"), ("대만", "예류"), ("대만", "알리산"),
    ("홍콩", "침사추이"), ("홍콩", "센트럴"), ("홍콩", "몽콕"), ("홍콩", "빅토리아피크"), ("홍콩", "디즈니랜드"),
    ("홍콩", "란타우섬"), ("홍콩", "소호"), ("홍콩", "스탠리"), ("홍콩", "완차이"), ("홍콩", "사이쿵"),
    ("싱가포르", "마리나베이"), ("싱가포르", "센토사"), ("싱가포르", "가든스바이더베이"), ("싱가포르", "오차드"), ("싱가포르", "클락키"),
    ("싱가포르", "차이나타운"), ("싱가포르", "리틀인디아"), ("싱가포르", "부기스"), ("싱가포르", "주롱"), ("싱가포르", "맥리치"),
    ("말레이시아", "쿠알라룸푸르"), ("말레이시아", "페낭"), ("말레이시아", "랑카위"), ("말레이시아", "코타키나발루"), ("말레이시아", "조호바루"),
    ("말레이시아", "말라카"), ("말레이시아", "쿠칭"), ("말레이시아", "티오만섬"), ("말레이시아", "겐팅하이랜드"), ("말레이시아", "타만네가타"),
    ("프랑스", "파리"), ("프랑스", "니스"), ("프랑스", "칸"), ("프랑스", "리옹"), ("프랑스", "마르세유"),
    ("프랑스", "스트라스부르"), ("프랑스", "보르도"), ("프랑스", "아비뇽"), ("프랑스", "툴루즈"), ("프랑스", "몽생미셸"),
    ("영국", "런던"), ("영국", "옥스퍼드"), ("영국", "맨체스터"), ("영국", "에든버러"), ("영국", "리버풀"),
    ("영국", "브라이튼"), ("영국", "바스"), ("영국", "리즈"), ("영국", "캠브리지"), ("영국", "글래스고"),
    ("스페인", "바르셀로나"), ("스페인", "마드리드"), ("스페인", "세비야"), ("스페인", "그라나다"), ("스페인", "발렌시아"),
    ("스페인", "빌바오"), ("스페인", "말라가"), ("스페인", "비고"), ("스페인", "톨레도"), ("스페인", "사라고사"),
    ("이탈리아", "로마"), ("이탈리아", "밀라노"), ("이탈리아", "베네치아"), ("이탈리아", "피렌체"), ("이탈리아", "나폴리"),
    ("이탈리아", "베로나"), ("이탈리아", "토리노"), ("이탈리아", "피사"), ("이탈리아", "볼로냐"), ("이탈리아", "아말피"),
    ("체코", "프라하"), ("체코", "체스키크룸로프"), ("체코", "브르노"), ("체코", "카를로비바리"), ("체코", "올로모우츠"),
    ("체코", "플젠"), ("체코", "오스트라바"), ("체코", "쿠트나호라"), ("체코", "리베레츠"), ("체코", "텔치"),
    ("독일", "프랑크푸르트"), ("독일", "뮌헨"), ("독일", "베를린"), ("독일", "함부르크"), ("독일", "쾰른"),
    ("독일", "드레스덴"), ("독일", "하이델베르크"), ("독일", "뒤셀도르프"), ("독일", "라이프치히"), ("독일", "뉘른베르크"),
    ("네덜란드", "암스테르담"), ("네덜란드", "로테르담"), ("네덜란드", "헤이그"), ("네덜란드", "위트레흐트"), ("네덜란드", "마스트리흐트"),
    ("네덜란드", "하를럼"), ("네덜란드", "델프트"), ("네덜란드", "잔담"), ("네덜란드", "레이던"), ("네덜란드", "킨더다이크"),
    ("오스트리아", "빈"), ("오스트리아", "잘츠부르크"), ("오스트리아", "인스브루크"), ("오스트리아", "그라츠"), ("오스트리아", "할슈타트"),
    ("오스트리아", "린츠"), ("오스트리아", "클라겐푸르트"), ("오스트리아", "첼암제"), ("오스트리아", "바트이슐"), ("오스트리아", "메멜링"),
    ("스위스", "취리히"), ("스위스", "제네바"), ("스위스", "루체른"), ("스위스", "인터라켄"), ("스위스", "베른"),
    ("스위스", "바젤"), ("스위스", "로잔"), ("스위스", "융프라우"), ("스위스", "몽트뢰"), ("스위스", "체르마트"),
    ("포르투갈", "리스본"), ("포르투갈", "포르투"), ("포르투갈", "신트라"), ("포르투갈", "카스카이스"), ("포르투갈", "코임브라"),
    ("포르투갈", "파티마"), ("포르투갈", "브라가"), ("포르투갈", "에보라"), ("포르투갈", "알가르브"), ("포르투갈", "마데이라"),
    ("그리스", "아테네"), ("그리스", "산토리니"), ("그리스", "미코노스"), ("그리스", "테살로니키"), ("그리스", "크레타"),
    ("그리스", "코르푸"), ("그리스", "로도스"), ("그리스", "델포이"), ("그리스", "메테오라"), ("그리스", "나플리오"),
    ("헝가리", "부다페스트"), ("헝가리", "데브레첸"), ("헝가리", "세게드"), ("헝가리", "쇼프론"), ("헝가리", "페치"),
    ("헝가리", "에게르"), ("헝가리", "케치케메트"), ("헝가리", "제르"), ("헝가리", "미슈콜츠"), ("헝가리", "시게트"),
    ("크로아티아", "두브로브니크"), ("크로아티아", "스플리트"), ("크로아티아", "자그레브"), ("크로아티아", "자다르"), ("크로아티아", "로빈"),
    ("크로아티아", "플리트비체"), ("크로아티아", "오파티아"), ("크로아티아", "시베니크"), ("크로아티아", "풀라"), ("크로아티아", "트로기르"),
    ("스웨덴", "스톡홀름"), ("스웨덴", "예테보리"), ("스웨덴", "말뫼"), ("스웨덴", "웁살라"), ("스웨덴", "룰레오"),
    ("스웨덴", "오레브로"), ("스웨덴", "비스뷔"), ("스웨덴", "예블레"), ("스웨덴", "헬싱보리"), ("스웨덴", "칼마르"),
]

# =========================
# 공통 유틸
# =========================
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

def get_next_pair():
    processed = set(load_processed_pairs())
    unprocessed = [p for p in COUNTRY_CITY_PAIRS if f"{p[0]}|{p[1]}" not in processed]
    if not unprocessed:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump({"pairs": []}, f, ensure_ascii=False, indent=2)
        unprocessed = COUNTRY_CITY_PAIRS
    return unprocessed[0]

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
    total_text_height = len(lines) * line_height
    y = 250 - total_text_height / 2
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

# =========================
# 시트 읽기
# 첫 행은 패스, 둘째 행부터
# A=나라, B=도시, C=완
# =========================
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

# =========================
# 제목 / 토큰 정리
# =========================
def generate_random_title(country, city):
    keywords = ["여행지", "숨은 명소", "데이트 코스", "가족여행", "당일치기 코스", "주말여행", "핫플레이스"]
    suffixes = ["TOP10", "BEST10", "추천 10선"]
    return f"{country} {city} 가볼만한곳 {random.choice(keywords)} {random.choice(suffixes)}"

def remove_redundant_tokens(text, country, city):
    text = normalize_text(text)
    if not text:
        return text
    for token in [country, city]:
        if token:
            while text.startswith(token + " "):
                text = text[len(token) + 1:].strip()
            while f"{token} {token}" in text:
                text = text.replace(f"{token} {token}", token)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def clean_place_title(title, country, city):
    t = normalize_text(title)
    t = remove_redundant_tokens(t, country, city)
    for token in [country, city]:
        if token:
            if t.startswith(token + " "):
                t = t[len(token) + 1:].strip()
            if t == token:
                return ""
    return t

def build_display_title(country, city, place_title):
    clean_place = clean_place_title(place_title, country, city)
    return f"{country} {city} {clean_place}".strip() if clean_place else f"{country} {city}"

def build_map_search_keyword(country, city, place_title):
    clean_place = clean_place_title(place_title, country, city)
    return f"{country} {city} {clean_place}".strip() if clean_place else f"{country} {city}"

# =========================
# 장소 수집
# =========================
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
    if not content_id:
        return f"{title}는 {country} {city} 여행에서 인기가 많은 관광지로, 현지 분위기를 즐기기 좋은 장소입니다."
    return f"{title}는 {country} {city} 여행에서 추천할 만한 장소입니다."

def get_place_images(place, count=3, country="", city=""):
    return []

# =========================
# 서론 / 마무리
# =========================
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

def make_last(country, city):
    return (
        f"{country} {city} 여행은 생각보다 동선이 중요해서, 미리 핵심 명소를 정리해두면 훨씬 편하게 움직일 수 있습니다. "
        f"이번 글에서 소개한 곳들은 {country} {city}의 분위기와 매력을 함께 느끼기 좋은 곳들로 구성했습니다. "
        f"일정이 짧아도 충분히 알차게 둘러볼 수 있으니, 취향에 맞게 코스를 조합해 보시면 좋습니다."
    )

# =========================
# 목차 자동생성용 구조
# =========================
def build_post_html(country, city, title, places, thumb_url):
    intro_html = generate_ai_review(make_intro_prompt(country, city, title), title)
    intro_html = intro_html.replace('data-ke-size="size16"', 'data-ke-size="size18"').replace("size16", "size18")

    last_text = make_last(country, city)

    sections_html = ""
    for idx, item in enumerate(places, start=1):
        section_title = build_display_title(country, city, item["title"])
        map_keyword = build_map_search_keyword(country, city, item["title"])
        map_link_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(map_keyword)
        img_url = item.get("image", "")
        extra_images = item.get("images", [])

        img_html = ""
        if img_url:
            img_html = f'''
<div style="text-align:center; margin:20px 0;">
    <a href="{map_link_url}" target="_blank">
        <img src="{img_url}" style="max-width:100%; height:auto; border-radius:8px;" alt="{item["title"]}">
    </a>
</div>
'''
        extra_images_html = ""
        if len(extra_images) > 1:
            extra_images_html = '<div style="display:flex; gap:10px; flex-wrap:wrap; margin:20px 0;">'
            for img in extra_images[1:3]:
                extra_images_html += f'''
                <div style="flex:1 1 30%; min-width:180px;">
                    <img src="{img}" style="width:100%; height:auto; border-radius:8px;" alt="{item["title"]}">
                </div>
                '''
            extra_images_html += "</div>"

        map_html = f'''
<div style="text-align:center; margin-bottom:25px;">
    <a href="{map_link_url}" target="_blank" style="color:#1a2a40;font-weight:bold;text-decoration:underline;font-size:15px;">
      🗺️ 구글 지도에서 위치 확인하기
    </a>
</div>
'''

        desc = item["desc"].replace("\n", "<br>")
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

# =========================
# AI 섹션 생성
# =========================
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

# =========================
# 메인
# =========================
def main():
    row_idx = None
    country = None
    city = None

    rows = ws4.get_all_values()
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
