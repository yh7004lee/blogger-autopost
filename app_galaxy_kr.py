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

# feedparser for related posts
import feedparser

# Selenium for Google Play
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions

# Google Play reviews
try:
    from google_play_scraper import Sort, reviews
except Exception:
    reviews = None


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
BLOG_ID_FIXED = "6533996132181172904"  # 블로그 1 개 ID (고정)
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
        prev = ws.cell(row_idx, 8).value or ""   # H 열
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
# 제목/라벨 생성 (중복 제거)
# =========================
def make_post_title(keyword: str) -> str:
    # 수정: "안드로이드 안드로이드앱" → "안드로이드 앱" (중복 제거)
    front_choices = ["안드로이드 앱", "안드로이드 어플", "안드로이드모바일", "스마트안드로이드"]
    back_choices = ["앱 추천 어플", "어플 추천 앱", "어플 앱스토어", "앱스토어 어플", "구글플레이 앱"]
    return f"{random.choice(front_choices)} {keyword} {random.choice(back_choices)}"


def make_post_labels(sheet_row: list) -> list:
    label_val = sheet_row[1].strip() if len(sheet_row) > 1 and sheet_row[1] else ""
    labels = ["어플", "안드로이드"]
    if label_val:
        labels.append(label_val)
    return labels


# =========================
# AI REVIEW (5 차 시도 폴백)
# =========================
def generate_ai_review(prompt):
    # 1. Gemini Flash
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

    # 2. Gemini Flash Lite
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

    # 3. Groq
    if GROQ_API_KEY:
        try:
            print("🚀 [3 차 시도] Groq")
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "You must output ONLY final answer in Korean. No reasoning, no analysis, no English."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 650
                },
                timeout=20
            )
            data = res.json()
            if res.status_code != 200:
                print("⚠️ Groq API 오류:", data)
            if "choices" in data and data["choices"] and data["choices"][0]["message"].get("content"):
                text = data["choices"][0]["message"]["content"]
                print("✅ AI 성공: Groq")
                return text.strip()
            print("⚠️ Groq 응답 없음")
        except Exception as e:
            print("⚠️ Groq 실패:", e)

    # 4. OpenRouter
    if OPENROUTER_API_KEY:
        try:
            print("🚀 [4 차 시도] OpenRouter")
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openrouter/auto",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 650
                },
                timeout=20
            )
            data = res.json()
            if res.status_code != 200:
                print("⚠️ OpenRouter API 오류:", data)
            if "choices" in data and data["choices"] and data["choices"][0]["message"].get("content"):
                text = data["choices"][0]["message"]["content"]
                print("✅ AI 성공: OpenRouter")
                return text.strip()
            print("⚠️ OpenRouter 응답 없음")
        except Exception as e:
            print("⚠️ OpenRouter 실패:", e)

    # 5. GPT-4o-mini
    if client:
        try:
            print("🚀 [5 차 시도] GPT-4o-mini")
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=650
            )
            print("✅ AI 성공: GPT-4o-mini")
            return res.choices[0].message.content.strip()
        except Exception as e:
            print("❌ 모든 AI 실패:", e)

    return f"어플 소개 정보입니다. 실패"


# =========================
# OpenAI GPT 재작성 (앱 설명)
# =========================
def _shrink_text(raw_html: str, max_chars: int = 1400) -> str:
    try:
        txt = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
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

    if not client and not genai_client:
        if compact:
            return "".join([f"<p data-ke-size='size18'>{line.strip()}</p>" for line in compact.splitlines() if line.strip()]) or f"<p data-ke-size='size18'>{app_name} 소개</p>"
        return f"<p data-ke-size='size18'>{app_name} 소개</p>"

    prompt = f"""
너는 어플 소개 블로그 글을 쓰는 전문 마커터야.
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
# 구글플레이 스토어 앱 검색 (Selenium)
# =========================
def search_google_play_apps(keyword, limit=15):
    """구글플레이 스토어에서 키워드 검색 → 앱 ID 목록 반환"""
    
    edge_options = EdgeOptions()
    edge_options.add_argument("--headless=new")
    edge_options.add_argument("--disable-gpu")
    edge_options.add_argument("--no-sandbox")
    edge_options.add_argument("--disable-dev-shm-usage")
    edge_options.add_argument("--disable-extensions")
    edge_options.add_argument("--disable-logging")
    edge_options.add_argument("--log-level=3")
    
    chrome = None
    try:
        chrome = webdriver.Edge(options=edge_options)
        chrome.implicitly_wait(3)
        
        search_url = f"https://play.google.com/store/search?q={keyword}&c=apps"
        print("[구글플레이 검색]", search_url)
        chrome.get(search_url)
        
        fast_wait = WebDriverWait(chrome, 5)
        fast_wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/store/apps/details?id=')]")))
        
        html_source = chrome.page_source
        soup = BeautifulSoup(html_source, 'html.parser')
        
        source = soup.find_all("a", href=re.compile(r"/store/apps/details\?id="))
        
        app_list = []
        for s in source:
            if len(app_list) >= limit:
                break
            link = s["href"]
            if not link.startswith("http"):
                link = "https://play.google.com" + link
            
            # 앱 ID 추출
            app_id_match = re.search(r"id=([a-zA-Z0-9_.]+)", link)
            if app_id_match:
                app_id = app_id_match.group(1)
                app_name = s.text.strip()
                if app_id and app_name:
                    app_list.append({"id": app_id, "name": app_name})
        
        print(f"[구글플레이 검색 결과] {len(app_list)}개: {[(a['id'], a['name']) for a in app_list]}")
        return app_list
    
    except Exception as e:
        print(f"[구글플레이 검색 실패] {e}")
        return []
    
    finally:
        if chrome:
            try:
                chrome.quit()
            except:
                pass


# =========================
# 구글플레이 스토어 앱 상세 정보 수집
# =========================
def get_play_app_info(app_id):
    """구글플레이 스토어 앱 상세 페이지 → 스크린샷 + 상세설명 수집"""
    
    edge_options = EdgeOptions()
    edge_options.add_argument("--headless=new")
    edge_options.add_argument("--disable-gpu")
    edge_options.add_argument("--no-sandbox")
    edge_options.add_argument("--disable-dev-shm-usage")
    edge_options.add_argument("--disable-extensions")
    edge_options.add_argument("--disable-logging")
    edge_options.add_argument("--log-level=3")
    
    chrome = None
    try:
        chrome = webdriver.Edge(options=edge_options)
        chrome.implicitly_wait(3)
        
        app_url = f"https://play.google.com/store/apps/details?id={app_id}"
        print(f"[앱 상세 페이지]", app_url)
        chrome.get(app_url)
        
        fast_wait = WebDriverWait(chrome, 5)
        
        # ========== 제목 구하기 ==========
        html_source = chrome.page_source
        soup = BeautifulSoup(html_source, 'html.parser')
        
        h1 = soup.find("h1")
        if not h1:
            print("[제목 유실 패스]")
            return None
        
        app_name = h1.text.strip()
        print(f"[앱 이름] {app_name}")
        
        # ========== [이미지 수집 - 강화된 필터링] ==========
        html_img_urls = []
        cc = 1
        downloaded = set()
        
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
        
        print(f"[INFO] 발견 이미지 수: {len(selenium_imgs)}")
        
        for img_el in selenium_imgs:
            if cc > 8:
                break
            try:
                alt_text = (img_el.get_attribute("alt") or "").strip()
                title_text = (img_el.get_attribute("title") or "").strip()
                
                # 1.-alt/title 기반 필터링
                # 앱 아이콘, 연령제한 (ESRB), 콘텐츠 등급 제외
                exclude_keywords = [
                    "아이콘 이미지", "콘텐츠 등급", "ESRB", "age rating", "내용 평가",
                    "아이콘", "app icon", "로고", "logo", "버전", "version"
                ]
                if any(kw in alt_text.lower() or kw in title_text.lower() for kw in exclude_keywords):
                    print(f"[제외] alt/title 필터: {alt_text} / {title_text}")
                    continue
                
                # 2. src 기반 필터링 (파일명/경로 분석)
                src = img_el.get_attribute("src") or ""
                if any(kw in src.lower() for kw in ["icon", "logo", "badge", "rating", "esrb", "age"]):
                    print(f"[제외] src 필터: {src}")
                    continue
                
                # 3. srcset/data-srcset 기반 크기 필터링
                srcset = img_el.get_attribute("data-srcset") or img_el.get_attribute("srcset") or ""
                min_width = 999999
                if srcset and "," in srcset:
                    try:
                        parts = srcset.split(",")
                        for part in parts:
                            part = part.strip()
                            if " " in part:
                                width_str = part.split(" ")[0]
                                if width_str.isdigit():
                                    min_width = min(min_width, int(width_str))
                            elif "=" in part:
                                # 예: "url 100w" 형태
                                width_str = part.split("=")[-1].replace("w", "").replace("x", "")
                                if width_str.isdigit():
                                    min_width = min(min_width, int(width_str))
                    except:
                        pass
                
                # 150px 미만 이미지 제외 (연령제한/작은 이미지 필터)
                if min_width < 150:
                    print(f"[제외] 사이즈 필터: {min_width}px")
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
                        # 해상도 업그레이드
                        real_url = re.sub(r"=w\d+-h\d+", "=w2048-h4096", val)
                        real_url = re.sub(r"w\d+-h\d+-rw", "w2048-h4096-rw", real_url)
                        break
                
                if not real_url:
                    continue
                
                if real_url in downloaded:
                    continue
                downloaded.add(real_url)
                
                html_img_urls.append(real_url)
                print(f"[수집] 이미지 {cc}: {real_url[:80]}...")
                cc += 1
                
            except Exception as e:
                print(f"[이미지 실패] {e}")
        
        # target_images: 1 번째부터 7 번째 (인덱스 1~6) = 6 개
        target_images = html_img_urls[1:7] if len(html_img_urls) > 1 else []
        
        print(f"[최종 스크린샷] {len(target_images)}개")
        
        # ========== 상세 설명 크롤링 ==========
        contents = "<p>어플 소개 정보를 불러오지 못했습니다.</p>"
        try:
            # "정보 더 보기" 버튼 클릭
            detail_btn = fast_wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(@aria-label, '정보 더 보기') or contains(@aria-label, '상세정보')]")
            ))
            chrome.execute_script("arguments[0].click();", detail_btn)
            time.sleep(0.3)
        except:
            # 대체 XPath 로 시도
            base_xpath = '/html/body/c-wiz[{}]/div/div/div[1]/div/div[2]/div/div[1]/div[1]/c-wiz[2]/div/section/header/div/div[2]/button/i'
            for index in range(2, 7):
                try:
                    xpath = base_xpath.format(index)
                    element = chrome.find_element(By.XPATH, xpath)
                    chrome.execute_script("arguments[0].click();", element)
                    time.sleep(0.2)
                    break
                except:
                    continue
        
        html_source = chrome.page_source
        soup = BeautifulSoup(html_source, 'html.parser')
        
        # 설명 div 찾기: class="fysCi" 또는 b0vY9b/description
        contents_div = soup.find("div", attrs={"class": "fysCi"}) or \
                     soup.find("div", re.compile(r"b0vY9b|description"))
        
        if contents_div:
            contents_list = contents_div.find_all("div")
            if contents_list:
                contents = str(contents_list[0])
            else:
                contents = str(contents_div)
        
        print(f"[상세 설명] {len(contents)} 문자")
        
        return {
            "title": app_name,
            "app_id": app_id,
            "screenshots": target_images,
            "description": contents,
        }
    
    except Exception as e:
        print(f"[앱 상세 정보 수집 실패] {e}")
        return None
    
    finally:
        if chrome:
            try:
                chrome.quit()
            except:
                pass


# =========================
# CSS 블록 (한 번만 출력)
# =========================
def build_css_block() -> str:
    return """
<style>
.img-group {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
}
.img-wrap {
  flex: 0 0 calc(33.333% - 7px);
  max-width: calc(33.333% - 7px);
  margin: 0;
}
.img-wrap img {
  width: 100%;
  height: auto;
  border-radius: 10px;
}
@media (max-width: 768px) {
  .img-wrap {
    flex: 0 0 100%;
    max-width: 100%;
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
            f"손안의 작은 기기 하나로도 '{keyword}' 같은 다양한 기능을 즐길 수 있는 시대가 열렸습니다.",
            f"현대 사회에서 '{keyword}' 앱은 없어서는 안 될 필수 도구로 자리잡고 있습니다.",
            f"특히 '{title}' 같은 주제는 많은 분들이 실제로 궁금해하는 부분입니다.",
            f"스마트폰 기술이 발전하면서 '{keyword}' 관련 앱의 활용도도 점점 높아지고 있습니다.",
            f"누구나 사용하는 스마트폰을 통해 '{keyword}'를 더욱 편리하게 즐길 수 있습니다."
        ],
        [
            f"특히 다양한 앱들이 출시되면서 '{keyword}' 앱의 선택 폭도 넓어졌습니다.",
            f"'{title}'을 찾는 분들이 늘어날 만큼 관심이 점점 커가고 있습니다.",
            f"앱을 통해 생활, 학습, 취미는 물론 '{keyword}'까지 즐길 수 있습니다.",
            f"스마트폰 앱은 시간을 절약하고 효율적인 생활을 가능하게 합니다.",
            f"'{keyword}' 앱은 사용자에게 새로운 경험과 편리함을 동시에 제공합니다.",
            f"새로운 '{keyword}' 앱들이 매일 등장하며, 그만큼 선택의 재미도 늘어납니다."
        ],
        [
            f"예를 들어 업무 효율을 높이는 앱부터 '{keyword}'를 즐길 수 있는 앱까지 다양합니다.",
            f"'{title}'은 많은 사람들이 찾는 인기 있는 카테고리 중 하나입니다.",
            f"게임, 엔터테인먼트와 함께 '{keyword}' 앱은 여가 시간을 풍성하게 만들어 줍니다.",
            f"쇼핑, 금융, 교통과 더불어 '{keyword}' 앱은 생활의 중요한 부분이 되었습니다.",
            f"사진, 영상과 함께 '{keyword}' 콘텐츠를 관리할 수 있는 앱도 많습니다.",
            f"커뮤니케이션 앱 못지않게 '{keyword}' 앱도 많은 관심을 받고 있습니다."
        ],
        [
            f"이처럼 '{keyword}' 앱은 단순한 기능을 넘어 생활 전반을 바꾸고 있습니다.",
            f"'{title}'을 활용하면 삶의 질이 한층 더 높아질 수 있습니다.",
            f"필요한 순간 '{keyword}' 앱으로 원하는 기능을 쉽게 누릴 수 있습니다.",
            f"편리함뿐 아니라 '{keyword}' 앱은 새로운 경험까지 제공합니다.",
            f"많은 사람들이 '{keyword}' 앱 덕분에 더 스마트한 생활을 누리고 있습니다.",
            f"'{keyword}' 앱 하나가 생활 패턴 전체를 바꾸기도 합니다."
        ]
    ]

    intro_sentences = []
    for group in intro_groups:
        intro_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    intro_text = " ".join(intro_sentences)

    first = f'''
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{intro_text}</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
'''
    return first


def build_ending_block(title: str, keyword: str) -> str:
    end_groups = [
        [
            f"이번 글에서 소개한 {title} 관련 앱들이 여러분의 스마트폰 생활에 도움이 되었길 바랍니다.",
            f"오늘 정리해드린 {title} 앱들이 실제 생활 속에서 유용하게 쓰이길 바랍니다.",
            f"이번 포스팅을 통해 만난 {title} 관련 앱들이 스마트한 선택에 보탬이 되었으면 합니다.",
            f"오늘 소개한 {title} 앱들이 독자 여러분의 일상에 꼭 필요한 도구가 되길 바랍니다.",
            f"{title}에 관심 있는 분들에게 이번 정리가 의미 있는 시간이 되었길 바랍니다.",
            f"다양한 {keyword} 앱들을 살펴본 만큼 스마트폰 활용이 훨씬 풍성해지길 바랍니다."
        ],
        [
            f"각 앱의 기능과 장点を 꼼꼼히 다뤘으니 {keyword} 앱 선택에 참고하시기 바랍니다.",
            f"앱들의 특징과 장단점을 비교했으니 {title} 선택에 큰 도움이 되실 겁니다.",
            f"이번 정리를 바탕으로 본인에게 맞는 {keyword} 앱을 쉽게 찾으시길 바랍니다.",
            f"필요할 때 바로 활용할 수 있도록 핵심 정보를 모아 두었으니 꼭 참고해 보세요.",
            f"앞으로 {keyword} 앱을 고르실 때 이번 글이 든든한 가이드가 되길 바랍니다.",
            f"다양한 앱을 비교해본 만큼 현명한 선택에 한 발 더 다가가셨길 바랍니다."
        ],
        [
            "앞으로도 더 다양한 앱 정보를 준비해 찾아뵙겠습니다.",
            f"계속해서 {keyword}와 관련된 알찬 정보와 추천 앱을 공유하겠습니다.",
            "독자분들의 의견을 반영해 더욱 유익한 포스팅으로 돌아오겠습니다.",
            "지속적으로 새로운 앱과 흥미로운 기능들을 소개할 예정입니다.",
            "앞으로도 꼭 필요한 실속 있는 정보를 꾸준히 전해드리겠습니다.",
            f"'{title}'처럼 많은 관심을 받는 주제를 더 자주 다루겠습니다."
        ],
        [
            "댓글과 좋아요는 큰 힘이 됩니다. 가볍게 참여해주시면 감사하겠습니다.",
            "궁금한 점이나 의견이 있다면 댓글로 남겨주시면 적극 반영하겠습니다.",
            "여러분의 피드백은 더 나은 글을 만드는 데 큰 도움이 됩니다.",
            "좋아요와 댓글로 응원해 주시면 더 좋은 정보로 보답하겠습니다.",
            "관심 있는 앱이나 기능이 있으면 댓글에 알려주세요. 참고해서 포스팅하겠습니다.",
            f"{keyword} 앱에 대한 여러분의 생각도 댓글로 자유롭게 남겨주세요."
        ]
    ]

    end_sentences = []
    for group in end_groups:
        end_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    end_text = " ".join(end_sentences)

    last = f"""
<p data-ke-size="size18">&nbsp;</p>
<div style="margin:40px 0px 20px 0px;">
<p data-ke-size="size18">{end_text}</p>
<p data-ke-size="size18">&nbsp;</p>
</div>
"""
    return last


# =========================
# 같이 보면 좋은글 박스 (RSS 랜덤 4 개)
# =========================
def get_related_posts(blog_id, count=4):
    if not feedparser:
        return ""
    
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
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
# 대상 행/키워드/라벨 선택
# =========================
def pick_target_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2 행부터
        a = row[0].strip() if len(row) > 0 and row[0] else ""  # A 열 = 키워드
        f = row[5].strip() if len(row) > 5 and row[5] else ""  # F 열 = 완료 (0-based: 5)
        if a and f != "완":
            return i, row
    return None, None


# =========================
# H 열 로그 누적
# =========================
def sheet_append_log(ws, row_idx, message, tries=3, delay=2):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    line = f"[{ts}] {message}"
    for t in range(1, tries+1):
        try:
            prev = ws.cell(row_idx, 8).value or ""   # H 열
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
# 3 블로그 로테이션 (시트 셀 기반 - B1 셀 사용)
# =========================
def get_next_blog_index(ws):
    # 시트 1 탭, B1 셀에서 다음 블로그 인덱스 읽기
    try:
        val = ws.cell(1, 2).value or "0"  # B1 셀 (컬럼 2)
        idx = int(val) % len(BLOG_IDS)
        return idx
    except Exception as e:
        print("[WARN] 시트 블로그 인덱스 읽기 실패:", e)
        return 0


def save_next_blog_index(ws, next_index):
    try:
        ws.update_cell(1, 2, str((next_index + 1) % len(BLOG_IDS)))  # B1 셀에 다음 인덱스 저장
    except Exception as e:
        print("[WARN] 시트 블로그 인덱스 저장 실패:", e)


# =========================
# 메인 실행
# =========================
if __name__ == "__main__":
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

        # 4) 구글플레이 앱 ID 목록 검색
        sheet_append_log(ws3, target_row, "앱 ID 검색 시작 (구글플레이)")
        apps = search_google_play_apps(keyword, limit=15)

        if not apps:
            sheet_append_log(ws3, target_row, "앱 ID 없음 → 종료")
            ws3.update_cell(target_row, 6, "완")      # F 열 완료 (0-based: 5)
            ws3.update_cell(target_row, 10, "")        # J 열 = URL 비움 (0-based: 9)
            sheet_append_log(ws3, target_row, "시트 기록 완료: F='완', J='' (검색결과 없음)")
            raise SystemExit(0)

        if len(apps) < 3:
            sheet_append_log(ws3, target_row, "앱 수가 3 개 미만 → 자동 완료 처리")
            ws3.update_cell(target_row, 6, "완")      
            ws3.update_cell(target_row, 10, "")        
            sheet_append_log(ws3, target_row, "시트 기록 완료: F='완', J='' (앱 수 부족)")
            raise SystemExit(0)
            
        sheet_append_log(ws3, target_row, f"앱 ID={[(a['id'], a['name']) for a in apps]}")

        # 5) 서론
        html_full = build_css_block()
        html_full += build_intro_block(title, keyword)
        html_full += """
        <div class="mbtTOC"><button> 목차 </button>
        <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
        </div>
        <p>&nbsp;</p>
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
        tag_str = " ".join([f"#{t}" for t in tag_items]) + " #구글플레이"
        sheet_append_log(ws3, target_row, f"해시태그='{tag_str}'")

        # 8) 3 블로그 로테이션 (B1 셀 사용)
        next_index = get_next_blog_index(ws3)
        BLOG_ID = BLOG_IDS[next_index]
        save_next_blog_index(ws3, next_index)
        sheet_append_log(ws3, target_row, f"로테이션 블로그 ID: {BLOG_ID}")

        # 9) 앱 상세 수집 → 본문 조립
        for j, app in enumerate(apps, 1):
            if j > 7:
                break
            try:
                sheet_append_log(ws3, target_row, f"[{j}] 앱 수집 시작 id={app['id']}")
                
                app_info = get_play_app_info(app["id"])
                if not app_info:
                    sheet_append_log(ws3, target_row, f"[{j}] 앱 정보 조회 실패")
                    continue

                app_name = app_info["title"]
                screenshots = app_info["screenshots"]
                src_desc = app_info["description"]

                desc_html = rewrite_app_description(src_desc, app_name, keyword)
                sheet_append_log(ws3, target_row, f"[{j}] {app_name} 설명 리라이트 성공")

                img_group_html = "".join(
                    f'<div class="img-wrap"><img src="{img_url}" alt="{app_name}_{cc+1}"></div>'
                    for cc, img_url in enumerate(screenshots)
                )

                app_url = f"https://play.google.com/store/apps/details?id={app['id']}"

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

                if j == 1 and label_val:
                    encoded_label = urllib.parse.quote(label_val)
                    section_html = f'''
                <div class="ottistMultiRelated">
                  <a class="extL alt" href="{BLOG_URL}search/label/{encoded_label}?&max-results=10">
                    <span style="font-size: medium;"><strong>추천 {label_val} 어플 보러가기</strong></span>
                    <i class="fas fa-link 2xs"></i>
                  </a>
                </div>
                <br /><br /><br />
                ''' + section_html

                if j == 3 and label_val:
                    encoded_label = urllib.parse.quote(label_val)
                    section_html = f'''
                <div class="ottistMultiRelated">
                  <a class="extL alt" href="{BLOG_URL}search/label/{encoded_label}?&max-results=10">
                    <span style="font-size: medium;"><strong>관련 {label_val} 어플도 확인하기</strong></span>
                    <i class="fas fa-link 2xs"></i>
                  </a>
                </div>
                <br /><br /><br />
                ''' + section_html

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

        # 12) 시트 기록 (F 열, J 열)
        ws3.update_cell(target_row, 6, "완")      # F 열 완료 (0-based: 5)
        ws3.update_cell(target_row, 10, post_url)  # J 열 = URL (0-based: 9)
        sheet_append_log(ws3, target_row, f"시트 기록 완료: F='완', J='{post_url}'")

        # 13) 완료
        sheet_append_log(ws3, target_row, "작업 정상 종료")

    except SystemExit:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        row_for_err = target_row if 'target_row' in locals() and target_row else 2
        sheet_append_log(ws3, row_for_err, f"실패: {e}")
        sheet_append_log(ws3, row_for_err, f"Trace: {tb.splitlines()[-1]}")
        print("실패:", e, tb)
