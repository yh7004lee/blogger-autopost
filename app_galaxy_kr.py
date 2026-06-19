죄송합니다. 이전 코드 버전을 합치는 과정에서 아이콘이나 등급 마크 같은 작은 이미지들을 걸러내는 로직이 누락되었던 것 같습니다.

스크린샷을 수집할 때 **너비(width) 또는 높이(height)가 일정 크기(예: 300px) 이상인 큰 이미지만 수집**하도록 이미지 필터링 로직을 추가 및 수정했습니다.

다음과 같이 `fetch_app_detail` 함수 내 이미지 처리 부분을 변경해 두었습니다.

```python
            real_url = re.sub(r"=w\d+-h\d+", "=w2048-h4096", real_url)
            real_url = re.sub(r"w\d+-h\d+-rw", "w2048-h4096-rw", real_url)

            # =========================================================
            # [추가] 픽셀 크기(가로/세로)를 강제로 체크하여 작은 아이콘/등급 마크 필터링
            # =========================================================
            try:
                # URL에서 w\d+ 또는 h\d+ 파라미터 추출 시도
                dimension_match = re.search(r"w(\d+)-h(\d+)", real_url)
                if dimension_match:
                    w_size = int(dimension_match.group(1))
                    h_size = int(dimension_match.group(2))
                    # 가로나 세로 중 하나라도 300픽셀 미만인 작은 아이콘/배지는 수집하지 않음
                    if w_size < 300 and h_size < 300:
                        print(f"[필터링] 작은 이미지 제외 (크기: {w_size}x{h_size}): {real_url}")
                        continue
            except Exception as dim_e:
                print(f"[크기 체크 실패] 무시하고 진행: {dim_e}")
            # =========================================================

            if real_url in downloaded:
                continue
            downloaded.add(real_url)

```

위의 로직을 적용한 전체 실행 코드입니다.

```python
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
from datetime import datetime
from bs4 import BeautifulSoup
import requests
from PIL import Image, ImageDraw, ImageFont

# Google Sheets / Drive / Blogger
import gspread
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

# OpenAI
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# Google Play
from google_play_scraper import Sort, reviews

# feedparser for related posts
import feedparser

# Selenium
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions


# =========================
# API 키 - GitHub Secrets 에서 읽기
# =========================
API_KEYS_JSON = os.getenv("API_KEYS_JSON")

if not API_KEYS_JSON:
    raise RuntimeError("API_KEYS_JSON 환경변수가 없습니다. GitHub Secrets 를 confirm하세요.")

try:
    secrets = json.loads(API_KEYS_JSON)
except Exception as e:
    raise RuntimeError(f"API_KEYS_JSON 파싱 실패: {e}")

# 키 가져오기
OPENROUTER_API_KEY = secrets.get("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = secrets.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = secrets.get("GEMINI_API_KEY", "")
GROQ_API_KEY = secrets.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY = secrets.get("CEREBRAS_API_KEY", "")
SHEET_ID = secrets.get("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
DRIVE_FOLDER_ID = secrets.get("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")
GCS_API_KEY = secrets.get("GCS_API_KEY", "")
GCS_CX = secrets.get("GCS_CX", "")

# OpenAI 클라이언트
client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None
genai_client = None
if GEMINI_API_KEY:
    try:
        from google import genai
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        genai_client = None


# =========================
# 기본 설정
# =========================
BLOG_IDS = ["1271002762142343021", "4265887538424434999", "6159101125292617147"]
BLOG_URL = "https://apk.appsos.kr/"

error_logs = []


# =========================
# Google Sheets 인증 (sheet3 사용)
# =========================
def get_sheet3():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws3 = sh.worksheet("sheet3")
    except Exception:
        ws3 = sh.get_worksheet(2)  # 0-based index, 세 번째 탭
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


# =========================
# Blogger 인증
# =========================
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


# =========================
# 썸네일 로깅 (H 열 사용)
# =========================
def log_thumb_step(ws, row_idx, message):
    try:
        prev = ws.cell(row_idx, 8).value or ""  # H 열
        new_val = prev + (";" if prev else "") + message
        ws.update_cell(row_idx, 8, new_val)
    except Exception as e:
        print("[로깅 실패]", e)


# =========================
# 배경 이미지 랜덤 선택
# =========================
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join("assets", "backgrounds", ext)))
    return random.choice(files) if files else ""


# =========================
# 썸네일 생성
# =========================
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        try:
            font = ImageFont.truetype(os.path.join("assets", "fonts", "KimNamyun.ttf"), 48)
        except Exception:
            font = ImageFont.load_default()

        canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
        canvas.paste(bg, (0, 0))

        rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
        canvas.paste(rectangle, (0, 125), rectangle)

        draw = ImageDraw.Draw(canvas)

        import textwrap
        var_title_wrap = textwrap.wrap(var_title, width=12)
        bbox = font.getbbox("가")
        line_height = (bbox[3] - bbox[1]) + 12
        total_text_height = len(var_title_wrap) * line_height
        y = 500 / 2 - total_text_height / 2

        for line in var_title_wrap:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            x = (500 - text_width) / 2
            draw.text((x, y), line, "#FFEECB", font=font)
            y += line_height

        canvas = canvas.resize((400, 400))
        canvas.save(save_path, "PNG")
        return True
    except Exception as e:
        print(f"에러: 썸네일 생성 실패: {e}")
        return False


# =========================
# Google Drive 업로드 → 공개 URL 반환
# =========================
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        folder_id = DRIVE_FOLDER_ID

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

        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        return f"https://lh3.googleusercontent.com/d/{file['id']}"
    except Exception as e:
        print(f"에러: 구글드라이브 업로드 실패: {e}")
        return ""


# =========================
# 썸네일 생성 + 로그 + 업로드
# =========================
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


# =========================
# AI REVIEW (4 차 시도 폴백)
# =========================
def generate_ai_review(prompt):
    if genai_client:
        try:
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            print("✅ AI 성공: Gemini Flash")
            return response.text.strip()
        except Exception as e:
            print("⚠️ Gemini Flash 실패:", e)

    if genai_client:
        try:
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            print("✅ AI 성공: Gemini Flash Lite")
            return response.text.strip()
        except Exception as e:
            print("⚠️ Gemini Flash Lite 실패:", e)

    if OPENROUTER_API_KEY:
        try:
            print("🚀 [3 차 시도] OpenRouter Auto")

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
                timeout=15
            )

            data = res.json()

            if (
                "choices" in data
                and len(data["choices"]) > 0
                and data["choices"][0]["message"].get("content")
            ):
                text = data["choices"][0]["message"]["content"]
                print("✅ AI 성공: OpenRouter Auto")
                return text.strip()

            print("⚠️ OpenRouter Auto 실패: 응답 없음")

        except Exception as e:
            print("⚠️ OpenRouter Auto 실패:", e)

    if client:
        try:
            print("🚀 [4 차 시도] GPT-4o-mini (Paid)")
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=650
            )

            print("✅ AI 성공: GPT (paid)")
            return res.choices[0].message.content.strip()
        except Exception as e:
            print("❌ 모든 AI 실패:", e)

    return f"분석 생성에 실패했지만 기본 정보 기반으로 안정적인 성능을 가진 앱입니다."


# =========================
# OpenAI GPT 재작성 (앱 설명)
# =========================
def _shrink_text(raw_html: str, max_chars: int = 1400) -> str:
    try:
        txt = BeautifulSoup(raw_html, 'html.parser').get_text(separator=' ', strip=True)
    except Exception:
        txt = raw_html or ""
    txt = re.sub(r'https?://\S+|www\.\S+', ' ', txt)
    txt = re.sub(r'\S+@\S+\.\S+', ' ', txt)
    txt = re.sub(r'[\u200b\u200c\u200d\uFEFF]', '', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()

    seen = set()
    dedup_sentences = []
    for s in re.split(r'(?<=[.!?])\s+', txt):
        s = s.strip()
        if len(s) < 3:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup_sentences.append(s)
    txt = ' '.join(dedup_sentences)
    txt = re.sub(r'\b(v|ver|version)?\s?\d+(\.\d+){1,3}\b', ' 버전 ', txt)

    if len(txt) > max_chars:
        txt = txt[:max_chars].rsplit(' ', 1)[0] + ' …'
    return txt


def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = _shrink_text(original_html, max_chars=1400)

    prompt = f"""
너는 어플 소개 블로그 글을 쓰는 전문 마케터야.
아래 내용을 토대로 3 개 문단의 부드러운 추천 리뷰글로 변환해줘.
사람이 직접 경험하고 작성한 것처럼 따뜻한 대화체 톤으로 풀어나가줘.
문단 태그는 <p data-ke-size='size18'>만 사용할 것.
블로그 후기 스타일
문장을 끝까지 완성
자연스러운 한국어
어플 이름을 제외하고는 모두 한국어로 작성해야해
[중요] 오직 한국어로만 작성해야해 중간중간에 한자나 일본어 같은 외국어가 들어가면 안된다.
/////////////////////////
앱이름 : {app_name}
키워드 : {keyword_str}
원문 요약:
{compact}
"""

    rewritten = generate_ai_review(prompt)
    rewritten = rewritten.replace("<body>", "").replace("</body>", "")
    if "<p" not in rewritten:
        rewritten = f'<p data-ke-size="size18">{rewritten}</p>'
    return rewritten


# =========================
# 제목 생성
# =========================
def make_post_title(keyword: str) -> str:
    front_choices = ["구글플레이", " android ", "플레이스토어", "원스토어"]
    back_choices = ["앱 추천 어플", "어플 추천 앱", "어플 앱스토어", "앱스토어 어플", "핵꿀잼 어플", "필수 유틸 앱"]
    return f"{random.choice(front_choices)} {keyword} {random.choice(back_choices)}"


def make_post_labels(sheet_row: list) -> list:
    label_val = sheet_row[1].strip() if len(sheet_row) > 1 and sheet_row[1] else ""
    labels = ["어플", "android", "구글플레이"]
    if label_val:
        labels.append(label_val)
    return labels


# =========================
# 3 블로그 로테이션
# =========================
def get_next_blog_index(ws):
    try:
        val = ws.cell(1, 1).value or "0"
        idx = int(val) % len(BLOG_IDS)
        return idx
    except Exception as e:
        print("[WARN] 시트 블로그 인덱스 읽기 실패:", e)
        return 0


def save_next_blog_index(ws, next_index):
    try:
        ws.update_cell(1, 1, str((next_index + 1) % len(BLOG_IDS)))
    except Exception as e:
        print("[WARN] 시트 블로그 인덱스 저장 실패:", e)


# =========================
# H 열 로그 누적
# =========================
def sheet_append_log(ws, row_idx, message, tries=3, delay=2):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    line = f"[{ts}] {message}"
    for t in range(1, tries+1):
        try:
            prev = ws.cell(row_idx, 8).value or ""  # H 열
            new_val = (prev + (";" if prev else "") + line)
            ws.update_cell(row_idx, 8, new_val)
            print(f"[LOG:H{row_idx}] {line}")
            return True
        except Exception as e:
            print(f"[WARN] 로그기록 재시도 {t}/{tries}: {e}")
            time.sleep(delay * t)
    print(f"[FAIL] 로그기록 실패: {line}")
    return False


# =========================
# 대상 행/키워드/라벨 선택
# =========================
def pick_target_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        a = row[0].strip() if len(row) > 0 and row[0] else ""  # A 열 = 키워드
        f = row[5].strip() if len(row) > 5 and row[5] else ""  # F 열 = 완료
        if a and f != "완":
            return i, row
    return None, None


# =========================
# 구글플레이 스토어 앱 크롤링
# =========================
def fetch_google_play_apps(keyword, folder):
    url = f"https://play.google.com/store/search?q={keyword}&c=apps"

    edge_options = EdgeOptions()
    edge_options.add_argument("--headless=new")
    edge_options.add_argument("--disable-gpu")
    edge_options.add_argument("--no-sandbox")
    edge_options.add_argument("--disable-dev-shm-usage")
    edge_options.add_argument("--disable-extensions")
    edge_options.add_argument("--disable-logging")
    edge_options.add_argument("--log-level=3")
    edge_options.add_argument("--num-raster-threads=4")

    chrome = webdriver.Edge(options=edge_options)
    chrome.implicitly_wait(2)
    chrome.get(url)
    chrome.set_window_size(1280, 1600)

    fast_wait = WebDriverWait(chrome, 6)
    
    search_result_selector = "//a[contains(@href, '/store/apps/details?id=')]"
    fast_wait.until(EC.presence_of_element_located((By.XPATH, search_result_selector)))

    html_source = chrome.page_source
    soup = BeautifulSoup(html_source, 'html.parser')

    source = soup.find_all("a", href=re.compile(r"/store/apps/details\?id="))

    app_list = []
    k = 0
    for s in source:
        if int(k) == 15:
            break
        link = s["href"]
        if not link.startswith("http"):
            link = "https://play.google.com" + link
        if link not in app_list:
            app_list.append(link)
            k = 1 + k

    chrome.quit()

    if len(app_list) > 3:
        app_list = app_list[3:]

    return app_list


# =========================
# 앱 상세 페이지 수집
# =========================
def fetch_app_detail(app_link, keyword):
    edge_options = EdgeOptions()
    edge_options.add_argument("--headless=new")
    edge_options.add_argument("--disable-gpu")
    edge_options.add_argument("--no-sandbox")
    
    chrome = webdriver.Edge(options=edge_options)
    chrome.implicitly_wait(2)
    chrome.get(app_link)
    chrome.set_window_size(1280, 1600)

    fast_wait = WebDriverWait(chrome, 6)

    html_source = chrome.page_source
    soup = BeautifulSoup(html_source, 'html.parser')

    h1 = soup.find("h1")
    if not h1:
        chrome.quit()
        return None

    app_name = h1.text
    app_name = re.sub(r"[^\uAC00-\uD7A30-9a-zA-Z\s]", "", app_name)
    app_name = app_name.replace(" ", "")

    img_selector = "c-wiz div div div div div div div c-wiz div div div div div img, img.T75of, img[src*='googleusercontent'], img[src*='ggpht']"
    try:
        fast_wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, img_selector)))
    except:
        pass

    screenshot_selectors = [
        "img.T75of",
        "img[src*='googleusercontent']",
        "img[src*='ggpht']",
    ]

    selenium_imgs = []
    for sel in screenshot_selectors:
        try:
            found = chrome.find_elements(By.CSS_SELECTOR, sel)
            for f in found:
                if f not in selenium_imgs:
                    selenium_imgs.append(f)
        except Exception:
            pass

    html_img_urls = []
    downloaded = set()
    cc = 1

    for img_el in selenium_imgs:
        if cc > 6:
            break
        try:
            alt_text = (img_el.get_attribute("alt") or "").strip()
            if "아이콘 이미지" in alt_text or "콘텐츠 등급" in alt_text:
                continue

            possible_urls = [
                img_el.get_attribute("src"),
                img_el.get_attribute("srcset"),
                img_el.get_attribute("data-src"),
                img_el.get_attribute("data-srcset"),
            ]

            real_url = None
            for val in possible_urls:
                if not val:
                    continue
                val = val.strip()
                if "," in val:
                    parts = val.split(",")
                    val = parts[-1].strip()
                    if " " in val:
                        val = val.split(" ")[0]

                if "ggpht.com" in val or "googleusercontent.com" in val:
                    real_url = val
                    break

            if not real_url:
                continue

            real_url = re.sub(r"=w\d+-h\d+", "=w2048-h4096", real_url)
            real_url = re.sub(r"w\d+-h\d+-rw", "w2048-h4096-rw", real_url)

            # =========================================================
            # 이미지 픽셀 크기(가로/세로)를 강제로 체크하여 작은 아이콘/등급 마크 필터링
            # =========================================================
            try:
                dimension_match = re.search(r"w(\d+)-h(\d+)", real_url)
                if dimension_match:
                    w_size = int(dimension_match.group(1))
                    h_size = int(dimension_match.group(2))
                    if w_size < 300 and h_size < 300:
                        print(f"[필터링] 작은 이미지 제외 (크기: {w_size}x{h_size}): {real_url}")
                        continue
            except Exception as dim_e:
                print(f"[크기 체크 실패] 무시하고 진행: {dim_e}")
            # =========================================================

            if real_url in downloaded:
                continue
            downloaded.add(real_url)

            html_img_urls.append(real_url)
            cc += 1
        except Exception as e:
            print(f"[이미지 실패] {e}")

    success = False
    try:
        detail_btn = fast_wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, '정보 더 보기') or contains(@aria-label, '상세정보')]")))
        chrome.execute_script("arguments[0].click();", detail_btn)
        success = True
    except:
        base_xpath = '/html/body/c-wiz[{}]/div/div/div[1]/div/div[2]/div/div[1]/div[1]/c-wiz[2]/div/section/header/div/div[2]/button/i'  
        index = 2
        while not success and index < 7: 
            xpath = base_xpath.format(index)
            try:
                element = chrome.find_element(By.XPATH, xpath)
                chrome.execute_script("arguments[0].click();", element)
                success = True
            except:
                index += 1

    time.sleep(0.2)

    html_source = chrome.page_source
    soup = BeautifulSoup(html_source, 'html.parser')

    contents_div = soup.find("div", attrs={"class": "fysCi"}) or soup.find("div", re.compile(r"b0vY9b|description"))
    if contents_div:
        contents_list = contents_div.find_all("div")
        if contents_list:
            contents = str(contents_list[0])
        else:
            contents = str(contents_div)
    else:
        contents = "<p>어플 소개 정보를 불러오지 못했습니다.</p>"

    chrome.quit()

    target_images = html_img_urls[1:7] if len(html_img_urls) > 1 else []

    if target_images:
        images = '<div style="display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; width: 100%;">'
        for idx, img_src in enumerate(target_images):
            images += f'''
            <div style="flex: 0 0 calc(33.333% - 7px); max-width: calc(33.333% - 7px); box-sizing: border-box; text-align: center;">
                <img src="{img_src}" alt="{app_name}_{idx+1}" style="width: 100%; height: auto; border-radius: 8px; display: block;">
            </div>'''
        images += '</div>'
    else:
        images = '<p data-ke-size="size18">등록된 스크린샷 이미지가 없습니다.</p>'

    return {
        "name": app_name,
        "contents": contents,
        "screenshots": target_images,
        "url": app_link
    }


# =========================
# CSS 블록
# =========================
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


# =========================
# 서론/마무리 블록
# =========================
def build_intro_block(title: str, keyword: str) -> str:
    intro_groups = [
        [
            f"스마트폰은 이제 단순한 통신 수단을 넘어 우리의 생활 전반을 책임지는 필수품이 되었습니다.",
            f"손안의 작은 기기 하나로도 '{keyword}' 같은 다채로운 기능을 원활하게 즐길 수 있는 디지털 시대가 활짝 열렸습니다.",
            f"현대인의 바쁜 일상 속에서 '{keyword}' 관련 어플은 시간을 절약하고 삶의 질을 높여주는 유용한 도구로 자리매김하고 있습니다.",
            f"최근 많은 분들이 편리한 일상생활을 위해 관련 정보나 유용한 툴을 적극적으로 찾아보고 계십니다.",
            f"디지털 기술이 눈부시게 발전함에 따라 스마트폰을 활용한 편의성은 점점 더 강조되고 있습니다.",
            f"남녀노소 불문하고 누구나 손쉽게 휴대폰을 통해 새로운 트렌드나 유용한 기능들을 경험하고 계실 텐데요."
        ],
        [
            f"특히 시중에 수많은 모바일 서비스들이 쏟아져 나오면서 내 입맛에 꼭 맞는 제품을 고르는 폭이 무척 넓어졌습니다.",
            f"'{title}' 같은 주제를 꼼꼼히 비교하며 탐색하는 분들이 늘어날 만큼 관심도가 폭발적으로 커지고 있습니다.",
            f"단순한 검색을 넘어 일상, 학습, 취미 생활까지 스마트하게 아우를 수 있는 스마트한 시대가 되었습니다.",
            f"어플리케이션 하나만 잘 활용해도 일상에 새로운 활력을 불어넣고 효율적인 스케줄 관리가 가능해집니다.",
            f"이러한 모바일 프로그램들은 사용자들에게 시간적 여유와 색다른 경험을 동시에 선물해 줍니다.",
            f"매일 새롭고 기발한 기능으로 무장한 유틸리티들이 등장하고 있어 새로운 것을 탐색하는 재미도 쏠쏠합니다."
        ],
        [
            f"다양한 플랫폼 사이에서 어떤 서비스를 선택해야 할지 고민이셨던 분들을 위해 오늘은 특별한 정보를 준비해 보았습니다.",
            f"수많은 라인업 중에서도 실사용자들의 평점과 만족도가 높은 알짜배기 정보들만 엄선하여 정리해 드리고자 합니다.",
            f"주변 지인들에게 추천받거나 온라인상에서 핫하게 떠오르는 인기 서비스들을 직접 살펴볼 수 있는 좋은 기회가 될 것입니다.",
            f"어떤 기능이 어떻게 구현되어 있는지 궁금해하셨던 분들이라면 이번 포스팅을 통해 궁금증을 시원하게 해소하실 수 있습니다."
        ]
    ]

    intro_sentences = []
    for group in intro_groups:
        intro_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    intro_text = " ".join(intro_sentences)

    first = f'''
<div id="jm">  </div>
<p data-ke-size="size18">{intro_text}</p>
<span></span>
<p data-ke-size="size18">  </p>
'''
    return first


def build_ending_block(title: str, keyword: str) -> str:
    end_groups = [
        [
            f"이번 글에서 소개한 {title} 관련 앱들이 여러분의 스마트폰 라이프에 든든한 활력소가 되었길 바랍니다.",
            f"오늘 꼼꼼하게 정리해드린 {title} 앱들이 일상생활 속에서 유용하게 쓰기를 진심으로 바랍니다.",
            f"이번 포스팅을 통해 알게 되신 {title} 관련 어플들이 현명한 선택을 하는 데 작은 보탬이 되었으면 합니다.",
            f"오늘 소개해 드린 {title} 앱들이 독자 여러분들의 스마트한 일상에 꼭 필요한 도구로 활용되기를 기대해 봅니다."
        ],
        [
            f"각 서비스의 세부적인 기능과 특장점들을 폭넓게 다루어 보았으니 본인에게 알맞은 {keyword} 앱을 고르는 데 참고해 보시기 바랍니다.",
            f"각 라인업의 특징과 장단점을 객관적으로 비교해 드렸으니 {title}을 선택하는 과정에서 큰 도움이 되실 겁니다.",
            f"오늘 공유해 드린 내용을 바탕으로 평소 필요로 하셨던 {keyword} 관련 툴을 수월하게 찾아보실 수 있을 것입니다."
        ],
        [
            "앞으로도 더욱 새롭고 알찬 모바일 정보들을 풍성하게 준비해서 다시 찾아뵙겠습니다.",
            f"앞으로도 {keyword}와 관련된 유익한 꿀팁 정보들과 최신 추천 어플들을 꾸준히 공유해 드리겠습니다.",
            "독자분들의 소중한 의견을 적극 반영하여 더욱 깊이 있고 유익한 포스팅으로 보답하겠습니다."
        ],
        [
            "콘텐츠가 유익하셨다면 따뜻한 공감과 댓글 한 줄은 블로그 운영에 아주 큰 원동력이 됩니다.",
            "궁금하신 점이나 추가로 다루어 주었으면 하는 주제가 있다면 편하게 댓글로 남겨주시면 적극 반영하겠습니다.",
            "독자분들의 솔직한 피드백은 더 나은 양질의 글을 작성하는 데 커다란 밑거름이 됩니다."
        ]
    ]

    end_sentences = []
    for group in end_groups:
        end_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    end_text = " ".join(end_sentences)

    last = f"""
<p data-ke-size="size18">  </p>
<div style="margin:40px 0px 20px 0px;">
<p data-ke-size="size18">{end_text}</p>
<p data-ke-size="size18">  </p>
</div>
"""
    return last


# =========================
# 같이 보면 좋은글 박스
# =========================
def get_related_posts(blog_id, count=4):
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    html_box = """
<div style="background: rgb(239, 237, 237); border-radius: 8px; border: 2px dashed rgb(167, 162, 151);
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold;
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16"
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px;
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ 같이 보면 좋은글</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box


# =========================
# 메인 실행
# =========================
if __name__ == "__main__":
    target_row = None
    try:
        # 1) sheet3 에서 대상 행/데이터
        target_row, row = pick_target_row(ws3)
        if not target_row or not row:
            sheet_append_log(ws3, 2, "처리할 키워드 없음 (A 열)")
            raise SystemExit(0)

        keyword = row[0].strip()  # A 열 = 키워드
        label_val = row[1].strip() if len(row) > 1 else ""  # B 열 = 라벨

        sheet_append_log(ws3, target_row, f"대상 행={target_row}, 키워드='{keyword}', 라벨='{label_val}'")

        # 2) 제목 생성
        title = make_post_title(keyword)
        sheet_append_log(ws3, target_row, f"타이틀='{title}'")

        # 3) 썸네일 생성 & 업로드
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
        sheet_append_log(ws3, target_row, "썸네일 생성 시작")
        thumb_url = make_thumb_with_logging(ws3, target_row, thumb_path, title)
        sheet_append_log(ws3, target_row, f"썸네일 결과: {thumb_url or '실패'}")

        # 4) 구글플레이 앱 목록 검색
        sheet_append_log(ws3, target_row, "앱 목록 검색 시작")
        folder = f"thumbnails/{keyword}"
        os.makedirs(folder, exist_ok=True)
        app_links = fetch_google_play_apps(keyword, folder)

        if not app_links:
            sheet_append_log(ws3, target_row, "앱 없음 → 종료 및 완료 처리")
            ws3.update_cell(target_row, 5, "완")  # F 열 완료 안전장치
            ws3.update_cell(target_row, 9, "")    # J 열 비움
            sheet_append_log(ws3, target_row, "시트 기록 완료: F='완', J='' (검색결과 없음)")
            raise SystemExit(0)

        if len(app_links) < 3:
            sheet_append_log(ws3, target_row, "앱 수가 3 개 미만 → 자동 완료 처리")
            ws3.update_cell(target_row, 5, "완")  # F 열 완료 안전장치
            ws3.update_cell(target_row, 9, "")
            sheet_append_log(ws3, target_row, "시트 기록 완료: F='완', J='' (앱 수 부족)")
            raise SystemExit(0)

        sheet_append_log(ws3, target_row, f"앱 목록={app_links}")

        # 5) 서론 조립
        html_full = build_css_block()
        html_full += build_intro_block(title, keyword)
        html_full += """
        <div class="mbtTOC"><button> 목차 </button>
        <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
        </div>
        <p>  </p>
        """
        sheet_append_log(ws3, target_row, "서론 블록 생성 완료")

        # 6) 썸네일 본문 삽입
        if thumb_url:
            html_full += f'''
<p style="text-align:center;">
  <img src="{thumb_url}" alt="{keyword} 썸네일" style="max-width:100%; height:auto; border-radius:10px;">
</p><br /><br />
'''
            sheet_append_log(ws3, target_row, "본문에 썸네일 삽입")
        else:
            sheet_append_log(ws3, target_row, "본문 썸네일 없음")

        # 7) 해시태그
        tag_items = title.split()
        tag_str = " ".join([f"#{t}" for t in tag_items]) + " #구글플레이 #android"
        sheet_append_log(ws3, target_row, f"해시태그='{tag_str}'")

        # 8) 3 블로그 로테이션
        next_index = get_next_blog_index(ws3)
        BLOG_ID = BLOG_IDS[next_index]
        save_next_blog_index(ws3, next_index)
        sheet_append_log(ws3, target_row, f"로테이션 블로그 ID: {BLOG_ID}")

        # 9) 앱 상세 수집 → 본문 조립
        for j, app_link in enumerate(app_links, 1):
            if j > 7:
                break
            try:
                sheet_append_log(ws3, target_row, f"[{j}] 앱 수집 시작 {app_link}")

                app_info = fetch_app_detail(app_link, keyword)
                if not app_info:
                    sheet_append_log(ws3, target_row, f"[{j}] 앱 정보 조회 실패")
                    continue

                app_name = app_info["name"]
                contents = app_info["contents"]
                screenshots = app_info["screenshots"]
                app_url = app_info["url"]

                desc_html = rewrite_app_description(contents, app_name, keyword)
                sheet_append_log(ws3, target_row, f"[{j}] {app_name} 설명 리라이트 성공")

                img_group_html = "".join(
                    f'<div class="img-wrap"><img src="{img_url}" alt="{app_name}_{cc+1}"></div>'
                    for cc, img_url in enumerate(screenshots)
                )

                section_html = f'''
                <h2 data-ke-size="size26">{j}. {app_name} 어플 소개</h2>
                <br />
                {desc_html}
                <p data-ke-size="size18"><b>2) {app_name} 어플 스크린샷</b></p>
                <div class="img-group">{img_group_html}</div>
                <br />
                <p data-ke-size="size18" style="text-align:center;">
                  <a href="{app_url}" class="myButton">{app_name} 앱 다운</a>
                </p>
                <br />
                <p data-ke-size="size18">{tag_str}</p>
                <br /><br />
                '''

                html_full += section_html
                sheet_append_log(ws3, target_row, f"[{j}] {app_name} 섹션 완료")

            except Exception as e_each:
                sheet_append_log(ws3, target_row, f"[{j}] 앱 처리 실패: {e_each}")

        # 10) 마무리
        html_full += build_ending_block(title, keyword)
        sheet_append_log(ws3, target_row, "마무리 블록 생성 완료")
        related_box = get_related_posts(BLOG_ID, count=6)
        html_full += related_box
        html_full += "<script>mbtTOC();</script><br /><br />"

        # 11) 업로드
        try:
            labels = make_post_labels(row)
            post_body = {"content": html_full, "title": title, "labels": labels}
            res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body,
                                              isDraft=False, fetchImages=True).execute()
            post_url = res.get("url", "")
            sheet_append_log(ws3, target_row, f"업로드 성공: {post_url}")
        except Exception as up_e:
            sheet_append_log(ws3, target_row, f"업로드 실패: {up_e}")
            raise

        # 12) 시트 기록 (F 열="완", J 열=URL)
        ws3.update_cell(target_row, 5, "완")  # F 열 완료
        ws3.update_cell(target_row, 9, post_url)  # J 열 = URL
        sheet_append_log(ws3, target_row, f"시트 기록 완료: F='완', J='{post_url}'")

        # 13) 완료
        sheet_append_log(ws3, target_row, "작업 정상 종료")

    except SystemExit:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        row_for_err = target_row if target_row else 2
        sheet_append_log(ws3, row_for_err, f"실패: {e}")
        sheet_append_log(ws3, row_for_err, f"Trace: {tb.splitlines()[-1]}")
        print("실패:", e, tb)

```
