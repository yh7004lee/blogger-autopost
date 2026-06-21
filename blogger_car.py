import os
import re
import json
import sys
import glob
import random
import textwrap
import traceback
import pickle
import requests
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import gspread
from google import genai
from openai import OpenAI
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request

from playwright.async_api import async_playwright, TimeoutError
import asyncio

sys.stdout.reconfigure(encoding="utf-8")

DEBUG_MODE = True

def debug(msg: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

def log_step(msg: str):
    try:
        tr = globals().get("target_row", None)
        if tr and "ws" in globals():
            prev = ws.cell(tr, 16).value or ""
            sep = " | " if prev else ""
            ws.update_cell(tr, 16, f"{prev}{sep}{msg}")
    except Exception as e:
        print("⚠️ 로그 기록 실패:", e)
    print(msg)

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
BLOG_ID = os.getenv("BLOG_ID", "5711594645656469839")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
genai_client = None
if GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        debug(f"Gemini client init 실패: {e}")

SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)
ws = sh.worksheet("Sheet2")
debug(f"선택된 탭: {ws.title}")
log_step("1단계: Google Sheets 인증 성공")

ASSETS_BG_DIR = "assets/backgrounds"
ASSETS_FONT_TTF = "assets/fonts/KimNamyun.ttf"
THUMB_DIR = "thumbnails"

def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise FileNotFoundError("blogger_token.json 파일 없음")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data,
        ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()
log_step("2단계: Blogger 인증 성공")

def pick_best_from_srcset(srcset: str):
    if not srcset:
        return None
    candidates = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        items = part.split()
        url = items[0].strip()
        score = 0
        if len(items) > 1:
            size = items[1].strip()
            m = re.match(r"(\d+)(w|x)", size)
            if m:
                val = int(m.group(1))
                unit = m.group(2)
                score = val if unit == "w" else val * 10000
        candidates.append((score, url))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def normalize_url(u):
    if not u:
        return None
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    return u

def is_probably_thumbnail(u: str):
    if not u:
        return True
    bad_keywords = ["thumb", "thumbnail", "small", "mini", "ico_", "btn_", "banner", "logo", "spacer", "icon", "ad", "preview"]
    lower = u.lower()
    return any(k in lower for k in bad_keywords)

def is_valid_car_image_url(u):
    if not u:
        return False
    lower = u.lower()
    if lower.startswith("data:image"):
        return False
    allowed_domains = ("imgauto.naver.net", "ssl.pstatic.net", "phinf.pstatic.net", "nphoto.naver.net", "search.pstatic.net")
    if not any(domain in lower for domain in allowed_domains):
        return False
    if any(x in lower for x in ["ico_", "btn_", "banner", "logo", "spacer", "icon", "ad", "event"]):
        return False
    return True

def get_ext_from_url(u):
    try:
        path = urlparse(u).path
        ext = os.path.splitext(path)[1]
        if ext and len(ext) <= 5:
            return ext
    except Exception:
        pass
    return ".jpg"

def get_car_name_from_url(url):
    parsed_url = urlparse(url)
    params = parse_qs(parsed_url.query)
    if "query" in params:
        return params["query"][0].strip()
    return "확인불가"

def generate_ai_review(prompt, car_name):
    if genai_client:
        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            return response.text.strip()
        except Exception as e:
            debug(f"Gemini Flash 실패: {e}")

        try:
            response = genai_client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            return response.text.strip()
        except Exception as e:
            debug(f"Gemini Flash Lite 실패: {e}")

    if OPENROUTER_API_KEY:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": "openrouter/auto", "messages": [{"role": "user", "content": prompt}]},
                timeout=20
            )
            data = res.json()
            if data.get("choices") and data["choices"][0]["message"].get("content"):
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            debug(f"OpenRouter 실패: {e}")

    if client:
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=650
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            debug(f"GPT 실패: {e}")

    return f"{car_name}은 분석 생성에 실패했지만 기본 정보 기반으로 안정적인 성능을 가진 차량입니다."

def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETS_BG_DIR, ext)))
    return random.choice(files) if files else ""

def make_thumb(save_path: str, var_title: str):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    bg_path = pick_random_background()
    if bg_path and os.path.exists(bg_path):
        bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

    try:
        font = ImageFont.truetype(ASSETS_FONT_TTF, 48)
    except Exception:
        font = ImageFont.load_default()

    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 125), rectangle)
    draw = ImageDraw.Draw(canvas)

    var_title_wrap = textwrap.wrap(var_title, width=12)
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

def get_drive_service():
    creds = None
    if os.path.exists("drive_token_2nd.pickle"):
        with open("drive_token_2nd.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError("drive_token_2nd.pickle이 없거나 만료됨.")
        with open("drive_token_2nd.pickle", "wb") as token:
            pickle.dump(creds, token)
    return build("drive", "v3", credentials=creds)

def upload_to_drive(file_path, file_name):
    drive_service = get_drive_service()
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

def read_target_row():
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        status = row[2].strip() if len(row) > 2 and row[2] else ""
        if status != "완":
            return i, row
    return None, None

async def main():
    global target_row
    target_row, row = read_target_row()
    if not target_row:
        log_step("처리할 행이 없습니다.")
        return

    car_name = row[0].strip() if len(row) > 0 and row[0] else ""
    info_url = row[1].strip() if len(row) > 1 and row[1] else ""
    if not car_name and info_url:
        car_name = get_car_name_from_url(info_url)

    if info_url.startswith("?"):
        info_url = "https://search.naver.com/search.naver" + info_url
    elif not info_url.startswith("http"):
        info_url = "https://" + info_url

    spec_url = f"https://search.naver.com/search.naver?where=nexearch&query={car_name.replace(' ', '%20')}+제원"
    photo_url = f"https://search.naver.com/search.naver?where=nexearch&query={car_name.replace(' ', '%20')}+포토"

    summary_items = {}
    extracted_specs = []
    exterior_urls = []
    interior_urls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(info_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        grid_items = page.locator(".grid_item")
        grid_data = {}
        try:
            count = await grid_items.count()
            for i in range(count):
                item = grid_items.nth(i)
                title = (await item.locator(".item_tit").inner_text()).strip()
                info = (await item.locator(".info_text").inner_text()).strip()
                grid_data[title] = info
        except Exception:
            pass

        body_text = await page.locator("body").inner_text()
        price_patterns = [
            r"(\d{1,3},\d{3}\s*~\s*\d{1,3},\d{3}\s*만원)",
            r"(\d{1,3}\s*~\s*\d{1,3}\s*만원)",
            r"(\d[\d,]*\s*만원)"
        ]
        for pattern in price_patterns:
            m = re.search(pattern, body_text)
            if m:
                summary_items["💰 판매 가격"] = m.group(1).strip()
                break

        fuel = grid_data.get("연료", "")
        if fuel:
            summary_items["⛽ 사용 연료"] = fuel
        if "전기" in fuel:
            if grid_data.get("전비"):
                summary_items["⚡ 전비"] = grid_data["전비"]
            if grid_data.get("용량"):
                summary_items["🔋 배터리 용량"] = grid_data["용량"]
        else:
            if grid_data.get("연비"):
                summary_items["⚡ 공인 연비"] = grid_data["연비"]
            if grid_data.get("배기량"):
                summary_items["🧪 엔진 배기량"] = grid_data["배기량"]

        await page.goto(spec_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        try:
            title_loc = page.locator("h2").first
            if await title_loc.count() > 0:
                car_name_local = (await title_loc.inner_text()).strip()
                if car_name_local:
                    car_name = car_name_local
        except Exception:
            pass

        try:
            rows = page.locator("table tbody tr")
            row_count = await rows.count()
            for i in range(row_count):
                rowx = rows.nth(i)
                if await rowx.locator("th").count() > 0 and await rowx.locator("td").count() > 0:
                    raw_k = await rowx.locator("th").first.inner_text()
                    k = raw_k.replace("정보확인 내용 열고 닫기", "").strip()
                    raw_v = await rowx.locator("td").first.inner_text()
                    v = " ".join(raw_v.split())
                    if "배기량" in k:
                        summary_items["🧪 엔진 배기량"] = v
                    if k and v and (k, v) not in extracted_specs and len(extracted_specs) < 14:
                        extracted_specs.append((k, v))
        except Exception:
            pass

        if len(extracted_specs) == 0:
            try:
                groups = page.locator(".table_group")
                count = await groups.count()
                for i in range(count):
                    group = groups.nth(i)
                    title = (await group.locator(".title").inner_text()).strip()
                    rows = group.locator("table.basic_table tbody tr")
                    row_count = await rows.count()
                    for j in range(row_count):
                        r = rows.nth(j)
                        raw_k = await r.locator("th").first.inner_text()
                        k = raw_k.replace("정보확인 내용 열고 닫기", "").strip()
                        v = " ".join((await r.locator("td").first.inner_text()).split())
                        if k and v:
                            extracted_specs.append((f"[{title}] {k}", v))
            except Exception:
                pass

        await browser.close()

    os.makedirs(THUMB_DIR, exist_ok=True)
    title_for_post = f"{car_name} 자동차 상세정보"
    safe_keyword = re.sub(r'[\\/:*?"<>|.]', "_", car_name)

    thumb_path = os.path.join(THUMB_DIR, f"{safe_keyword}.png")
    make_thumb(thumb_path, title_for_post)
    thumb_url = upload_to_drive(thumb_path, f"{safe_keyword}.png")

    first_img_html = f"""
    <div style="text-align:center; margin:30px 0 40px 0; width:100%;">
        <img src="{thumb_url}" style="width:100%; max-width:100%; height:auto; border-radius:4px;" alt="{car_name}">
        <p style="font-size:13px; color:#777; margin-top:10px; font-weight:bold;">▲ {car_name} 썸네일</p>
    </div>
    """

    summary_table_rows = ""
    for title, value in summary_items.items():
        summary_table_rows += f"""
        <tr style="border-bottom:1px solid #eaeaea;">
            <td style="padding:14px 16px; font-size:14px; font-weight:bold; color:#222; background-color:#fcfcfc; width:35%;">{title}</td>
            <td style="padding:14px 16px; font-size:14px; color:#333; font-weight:bold;">{value}</td>
        </tr>"""
    summary_table_html = f"""
    <table style="width:100%; border-collapse:collapse; border-top:2px solid #1a2a40; border-bottom:1px solid #1a2a40; margin:15px 0 25px 0; text-align:left;">
        <tbody>{summary_table_rows}</tbody>
    </table>"""

    spec_table_rows = ""
    for i in range(0, len(extracted_specs), 2):
        item1 = extracted_specs[i]
        item2 = extracted_specs[i + 1] if i + 1 < len(extracted_specs) else ("", "")
        spec_table_rows += f"""
        <tr style="border-bottom:1px solid #eaeaea;">
            <td style="padding:12px; font-size:13px; font-weight:bold; color:#555; background-color:#f9fafb; width:20%; border-right:1px solid #eee;">{item1[0]}</td>
            <td style="padding:12px; font-size:13px; color:#111; width:30%; border-right:1px solid #eee; font-weight:bold;">{item1[1]}</td>
            <td style="padding:12px; font-size:13px; font-weight:bold; color:#555; background-color:#f9fafb; width:20%; border-right:1px solid #eee;">{item2[0]}</td>
            <td style="padding:12px; font-size:13px; color:#111; width:30%; font-weight:bold;">{item2[1]}</td>
        </tr>"""
    spec_table_html = f"""
    <table style="width:100%; border-collapse:collapse; border-top:2px solid #333333; border-bottom:1px solid #333333; margin:15px 0 25px 0; text-align:left;">
        <tbody>{spec_table_rows}</tbody>
    </table>"""

    compact_specs = " ".join([f"{k}: {v}" for k, v in extracted_specs[:10]])
    prompt = f"""
다음 자동차 정보를 보고 종합 평가를 작성해줘.

차량명: {car_name}
판매가격: {summary_items.get('💰 판매 가격', '정보 없음')}
사용연료: {summary_items.get('⛽ 사용 연료', '정보 없음')}
공인연비: {summary_items.get('⚡ 공인 연비', '정보 없음')}
배기량: {summary_items.get('🧪 엔진 배기량', '정보 없음')}

제원:
{compact_specs}

조건
- 5~8문장
- 250자 이상
- 자연스러운 한국어
- 블로그 후기 스타일
- 장점과 아쉬운점 모두 작성
- 마지막은 추천 여부로 마무리
- 문장을 끝까지 완성
- HTML 금지
"""
    ai_review_text = generate_ai_review(prompt, car_name)
    ai_review_html = ai_review_text.replace("\n", "<br>")

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>{title_for_post}</title>
</head>
<body style="font-family:'Malgun Gothic', sans-serif; line-height:1.9; color:#444; background-color:#f5f6f8; padding:20px; margin:0;">
<div style="background-color:#ffffff; padding:40px; border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.03); max-width:850px; margin:0 auto;">
    <p style="font-size:15px; color:#4d4d4d; text-align:justify; margin-bottom:25px;">{title_for_post}</p>
    {first_img_html}
    <h2 style="font-size:21px; color:#1a2a40; border-left:10px solid #1a2a40; padding:15px 20px 5px 20px; background-color:#f7f9fa; font-weight:bold;">1. {car_name} 자동차 주요 스펙 요약</h2>
    <p style="font-size:15px; color:#555; margin-bottom:10px;">차량 검토 시 가장 기본축이 되는 <strong>{car_name}</strong> 사양의 공식 가격 및 요약 데이터 테이블입니다.</p>
    {summary_table_html}
    <h2 style="font-size:21px; color:#1a2a40; border-left:10px solid #1a2a40; padding:15px 20px 5px 20px; background-color:#f7f9fa; font-weight:bold;">2. {car_name} 자동차 상세 제원 데이터 종합 테이블</h2>
    <p style="font-size:15px; color:#555; margin-bottom:10px;">기계적인 엔진 스펙 사양 정보와 트림별 치수 규격을 체계화한 정밀 제원표입니다.</p>
    {spec_table_html}
    <h2 style="font-size:21px; color:#1a2a40; border-left:10px solid #1a2a40; padding:15px 20px 5px 20px; background-color:#f7f9fa; font-weight:bold;">3. {car_name} 자동차 총평</h2>
    <p style="font-size:15px; color:#555; margin-bottom:15px;"></p>
    {ai_review_html}
</div>
</body>
</html>"""

    post_body = {
        "title": title_for_post,
        "content": html_content,
        "labels": ["자동차"],
    }

    res = blog_handler.posts().insert(
        blogId=BLOG_ID,
        body=post_body,
        isDraft=False,
        fetchImages=True
    ).execute()

    if not res or not res.get("url"):
        raise RuntimeError(f"Blogger 응답이 비정상입니다: {res}")

    ws.update_cell(target_row, 3, "완")
    ws.update_cell(target_row, 15, res["url"])
    log_step(f"포스팅 성공: {res['url']}")
    print(f"[완료] 블로그 포스팅: {res['url']}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        tb = traceback.format_exc()
        print("❌ 최종 실패:", e)
        print(tb)
        raise
