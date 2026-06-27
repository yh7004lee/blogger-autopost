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
BLOG_ID = "8582128276301125850"

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = os.path.join("assets", "fonts", "PlusJakartaSans-SemiBoldItalic.ttf")
THUMB_DIR = "thumbnails"

LABELS = ["viajes", "turismo"]

# ==================================================
# 클라이언트
# ==================================================
client_genai = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ==================================================
# 시트 연결
# ==================================================
def get_sheet6():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("Sheet6")
    print(f"selected worksheet: {ws.title} / {ws.id}")
    return ws

ws4 = get_sheet6()

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

def textwrap_wrap_spa(text, width):
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
        font = ImageFont.truetype(ASSETS_FONT_TTF, 42)
    except:
        font = ImageFont.load_default()
    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    draw = ImageDraw.Draw(canvas)
    lines = textwrap_wrap_spa(var_title, 12)
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
def generate_random_title(country, city):
    keywords = ["atracciones", "lugares populares", "sitios recomendados", "sitios destacados", "puntos de interés"]
    suffixes = ["TOP10", "BEST10", "10 recomendados"]
    return f"{country} {city} {random.choice(keywords)} {random.choice(suffixes)}"

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
Eres un redactor experto en blogs de viajes internacionales en español.
Con la información siguiente, redacta solo la introducción en español natural.

País: {country}
Ciudad: {city}
Título: {title}

Condiciones:
- 3 o 4 frases
- Usa las palabras clave de manera natural
- La primera frase debe captar el interés de inmediato
- Debe transmitir el ritmo del viaje, por qué se recomienda y la emoción del recorrido
- Se puede usar HTML, pero solo <p> y <br>
- Debe comenzar con <p data-ke-size="size18">
- No uses markdown
- No uses japonés ni coreano
""".strip()

def make_section_prompt(country, city, place_title, addr, overview):
    return f"""
Eres un redactor experto en blogs de viajes internacionales en español.

Información del lugar:
- País: {country}
- Ciudad: {city}
- Nombre del lugar: {place_title}
- Dirección: {addr}
- Descripción de referencia: {overview}

Condiciones:
- Español natural y fluido
- Estilo de blog de viajes
- 4 párrafos
- Más de 350 caracteres
- Explica atractivos y puntos destacados
- Incluye consejos de visita como transporte, afluencia y mejor momento
- Se puede usar HTML, pero solo <p> y <br>
- Debe comenzar con <p data-ke-size="size18">
- Prohibido usar encabezados
- No uses markdown
- No uses japonés ni coreano
""".strip()

def make_last(country, city):
    return (
        f"Viajar por {country} {city} requiere organizar bien la ruta. "
        f"Si combinas los principales puntos de interés con antelación, podrás disfrutar mucho más incluso en una estancia corta."
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
# Google Places 수집
# ==================================================
def normalize_place_title(title):
    title = (title or "").split("|")[0].strip()
    title = re.sub(r"\s+", " ", title)
    return title

def get_google_places_textsearch(country, city, limit=10):
    if not GOOGLE_MAPS_API_KEY:
        debug("GOOGLE_MAPS_API_KEY가 설정되어 있지 않습니다.")
        return []
    query = f"{country} {city} atracciones"
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": GOOGLE_MAPS_API_KEY,
        "language": "es",
    }
    try:
        res = requests.get(url, params=params, timeout=30)
        data = res.json()
        results = data.get("results", [])
        places = []
        seen = set()
        for r in results:
            name = r.get("name", "").strip()
            if not name:
                continue
            name_norm = normalize_place_title(name)
            if name_norm.lower() in seen:
                continue
            seen.add(name_norm.lower())
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

        if len(places) < limit:
            alt_queries = [f"{city} lugares populares", f"{city} sitios de interés", f"{city} atracciones turísticas"]
            for q in alt_queries:
                if len(places) >= limit:
                    break
                params["query"] = f"{country} {q}"
                res = requests.get(url, params=params, timeout=30)
                data = res.json()
                for r in data.get("results", []):
                    name = r.get("name", "").strip()
                    if not name:
                        continue
                    name_norm = normalize_place_title(name)
                    if name_norm.lower() in seen:
                        continue
                    seen.add(name_norm.lower())
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
        parts 
