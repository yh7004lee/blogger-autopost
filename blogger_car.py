import os
import re
import json
import sys
import random
import traceback
import requests
import urllib.request
from urllib.parse import urlparse, parse_qs

import gspread
from google import genai
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
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
SHEET_ID = "1u9dWxBc-JDITIn4S0c_wvkswWImwgnPSPuAX-qJywaw"
BLOG_ID = "5711594645656469839"

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
genai_client = None
if GEMINI_API_KEY:
    try:
        genai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        debug(f"Gemini client init 실패: {e}")

SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)
debug(f"스프레드시트 제목: {sh.title}")

all_sheets = sh.worksheets()
debug(f"탭 목록: {[s.title for s in all_sheets]}")

ws = sh.worksheet("시트1")
debug(f"선택된 탭: {ws.title}")
log_step("1 단계: Google Sheets 인증 성공")

IMAGE_SAVE_DIR = "car_images"
os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)

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
log_step("2 단계: Blogger 인증 성공")

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

def clean_car_name(name: str):
    if not name:
        return ""
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\bai\s*브리핑\b", "", name, flags=re.IGNORECASE).strip()
    return name

def make_clean_title(car_name):
    car_name = clean_car_name(car_name)
    parts = {
        "prefix": ["상세", "완전", "팩트", "핵심", "최신", "정밀", "꼼꼼한"],
        "mid": ["제원", "색상", "디자인", "스펙", "옵션", "트림"],
        "suffix": ["총평", "분석", "리포트", "가이드", "정보", "한눈에", "모든 것"]
    }
    return f"{car_name} {random.choice(parts['prefix'])} {random.choice(parts['mid'])} {random.choice(parts['suffix'])}"

async def generate_title_from_spec_page(page):
    selectors = [
        '#root > section > div.top_wrap > div.top_area > div > h2',
        'xpath=//*[@id="root"]/section/div[1]/div[1]/div/h2',
        'h2'
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                text = (await loc.inner_text()).strip()
                text = clean_car_name(text)
                if text:
                    return text
        except Exception:
            pass
    return None

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

async def close_popup(page):
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
    except Exception:
        pass
    try:
        close_btn = page.locator("button.civ__close, .civ__close_btn, button[class*='close']").first
        if await close_btn.count() > 0:
            await close_btn.click(timeout=500)
            await page.wait_for_timeout(200)
    except Exception:
        pass

async def get_preview_big_image_url(page):
    selectors = [
        "body > div.civ.civ_ko.civ__full_screen img",
        "body > div.civ.civ_ko.civ__full_screen img[src]",
        "#root img",
        "img[alt]",
    ]
    for sel in selectors:
        try:
            img = page.locator(sel).first
            await img.wait_for(state="visible", timeout=800)
            current_src = await img.evaluate("el => el.currentSrc || ''")
            src = await img.get_attribute("src")
            srcset = await img.get_attribute("srcset")
            data_src = await img.get_attribute("data-src")
            data_original = await img.get_attribute("data-original")
            data_lazy_src = await img.get_attribute("data-lazy-src")
            data_srcset = await img.get_attribute("data-srcset")
            candidates = [
                current_src,
                pick_best_from_srcset(srcset),
                pick_best_from_srcset(data_srcset),
                data_original,
                data_src,
                data_lazy_src,
                src
            ]
            for c in candidates:
                c = normalize_url(c)
                if c and is_valid_car_image_url(c) and not is_probably_thumbnail(c):
                    try:
                        size = await img.evaluate("""
                            el => ({
                                nw: el.naturalWidth || 0,
                                nh: el.naturalHeight || 0
                            })
                        """)
                        if max(size["nw"], size["nh"]) >= 800:
                            return c
                    except Exception:
                        return c
        except Exception:
            continue
    return None

async def extract_images_from_section(page, section_name, section_index, max_count, photo_url):
    extracted_urls = []
    try:
        await page.goto(photo_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(800)
        if section_index == 2:
            try:
                tab_buttons = page.locator("div.tab_menu > ul > li, .tab_menu li, button.tab")
                tab_count = await tab_buttons.count()
                if tab_count >= 2:
                    interior_tab = tab_buttons.nth(1)
                    await interior_tab.click(timeout=800)
                    await page.wait_for_timeout(800)
            except Exception:
                pass
        section_div = page.locator(f"#root > section > div.content_wrap > div > div > div:nth-child({section_index})")
        await section_div.wait_for(state="visible", timeout=1500)
        await page.wait_for_timeout(300)
        base_selector = f"#root > section > div.content_wrap > div > div > div:nth-child({section_index}) > div.photo_list_wrap > div > li"
        li_elements = await page.locator(base_selector).all()
        li_count = len(li_elements)
        if li_count == 0:
            return extracted_urls
        actual_max = min(li_count, max_count)
        extracted_count = 0
        for i in range(actual_max):
            try:
                img_locator = page.locator(f"{base_selector}:nth-child({i+1}) > a > img")
                await img_locator.wait_for(state="visible", timeout=1500)
                await img_locator.click(timeout=800)
                await page.wait_for_timeout(300)
                for attempt in range(30):
                    try:
                        big_url = await get_preview_big_image_url(page)
                        if big_url and is_valid_car_image_url(big_url) and not is_probably_thumbnail(big_url):
                            if big_url not in extracted_urls:
                                ext = get_ext_from_url(big_url)
                                file_name = f"{section_name}_photo_{extracted_count + 1}{ext}"
                                file_path = os.path.join(IMAGE_SAVE_DIR, file_name)
                                urllib.request.urlretrieve(big_url, file_path)
                                extracted_urls.append(big_url)
                                extracted_count += 1
                                break
                    except Exception:
                        pass
                    break
                await close_popup(page)
                await page.wait_for_timeout(300)
            except TimeoutError:
                await close_popup(page)
                break
            except Exception:
                await close_popup(page)
                break
    except Exception:
        pass
    return extracted_urls

async def main():
    global target_row
    target_row, row = None, None
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):
        status = row[2].strip() if len(row) > 2 and row[2] else ""
        if status != "완":
            target_row, row = i, row
            break

    if target_row is None:
        log_step("처리할 행이 없습니다.")
        return

    info_url = row[1].strip() if len(row) > 1 and row[1] else ""
    if info_url.startswith("?"):
        info_url = "https://search.naver.com/search.naver" + info_url
    elif not info_url.startswith("http"):
        info_url = "https://" + info_url

    car_name = ""

    spec_url = ""
    photo_url = ""

    summary_items = {}
    extracted_specs = []
    exterior_urls = []
    interior_urls = []
    web_image_urls = []

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

        try:
            page_car_name = await generate_title_from_spec_page(page)
            if page_car_name:
                car_name = page_car_name
        except Exception:
            pass

        if not car_name:
            try:
                car_name = get_car_name_from_url(info_url)
            except Exception:
                car_name = ""

        if not car_name:
            car_name = "확인불가"

        spec_url = f"https://search.naver.com/search.naver?where=nexearch&query={car_name.replace(' ', '%20')}+제원"
        photo_url = f"https://search.naver.com/search.naver?where=nexearch&query={car_name.replace(' ', '%20')}+포토"

        await page.goto(spec_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        page_car_name = await generate_title_from_spec_page(page)
        if page_car_name:
            car_name = page_car_name

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

        MAX_IMAGES_PER_SECTION = 10
        try:
            exterior_urls = await extract_images_from_section(page, "익스테리어", 1, MAX_IMAGES_PER_SECTION, photo_url)
        except Exception:
            pass
        try:
            interior_urls = await extract_images_from_section(page, "인테리어", 2, MAX_IMAGES_PER_SECTION, photo_url)
        except Exception:
            pass

        web_image_urls = exterior_urls + interior_urls
        await browser.close()

    if not summary_items:
        summary_items["💰 판매 가격"] = "정보 없음"
    if not extracted_specs:
        extracted_specs = [("정보", "추출 실패")]

    title_for_post = make_clean_title(car_name)

    first_img_html = ""
    if len(web_image_urls) > 0:
        first_img_html = f"""
        <div style="text-align: center; margin: 30px 0 40px 0; width: 100%;">
            <img src="{web_image_urls[0]}" style="width: 100%; max-width: 100%; height: auto; border-radius: 4px;" alt="{car_name}">
            <p style="font-size: 13px; color: #777; margin-top: 10px; font-weight: bold;">▲ {car_name} 전면부 메인 디자인 공식 포토</p>
        </div>
        """

    gallery_exterior_html = ""
    for i, url in enumerate(exterior_urls, start=1):
        gallery_exterior_html += f"""
        <div style="text-align: center; margin-bottom: 35px; width: 100%;">
            <img src="{url}" style="width: 100%; max-width: 100%; height: auto; border-radius: 4px;" alt="{car_name} 익스테리어">
            <p style="font-size: 13px; color: #666; margin-top: 10px; font-weight: bold;">▲ {car_name} 외관 디자인 사진 {i}</p>
        </div>"""

    gallery_interior_html = ""
    for i, url in enumerate(interior_urls, start=1):
        gallery_interior_html += f"""
        <div style="text-align: center; margin-bottom: 35px; width: 100%;">
            <img src="{url}" style="width: 100%; max-width: 100%; height: auto; border-radius: 4px;" alt="{car_name} 인테리어">
            <p style="font-size: 13px; color: #666; margin-top: 10px; font-weight: bold;">▲ {car_name} 실내 인테리어 사진 {i}</p>
        </div>"""

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
- 5~8 문장
- 250 자 이상
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
</head>
<body>
    <p style="font-size:18px;">{title_for_post}</p>
    {first_img_html}
    <h2 style="font-size:20px;">1. {car_name} 자동차 주요 스펙 요약</h2>
    <p style="font-size:15px;">차량 검토 시 가장 기본축이 되는 {car_name} 사양의 공식 가격 및 요약 데이터 테이블입니다.</p>
    {summary_table_html}
    <h2 style="font-size:20px;">2. {car_name} 자동차 상세 제원 데이터 종합 테이블</h2>
    <p style="font-size:15px;">기계적인 엔진 스펙 사양 정보와 트림별 치수 규격을 체계화한 정밀 제원표입니다.</p>
    {spec_table_html}
    <br><br>
    <div style="margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; text-align: center;">
        <span style="font-size: 20px; font-weight: bold;">
            🔗 <a href="{info_url}" target="_blank">더 자세한 내용은 여기를 클릭하세요!!</a>
        </span>
    </div>
    <br><br>
    <h2 style="font-size:20px;">3. {car_name} 자동차 실물 디자인 외관 사진</h2>
    <p style="font-size:15px;">외관의 강인한 디자인과 디테일을 생생하게 확인할 수 있는 {car_name} 공식 익스테리어 포토 갤러리입니다.</p>
    <div style="margin: 20px 0; width: 100%;">
        {gallery_exterior_html if exterior_urls else "<p>익스테리어 이미지를 불러오지 못했습니다.</p>"}
    </div>
    <br><br>
    <h2 style="font-size:20px;">4. {car_name} 자동차 실물 디자인 내부 사진</h2>
    <p style="font-size:15px;">실내의 고급스러운 인테리어와 편의사양을 자세히 살펴볼 수 있는 {car_name} 공식 인테리어 포토 갤러리입니다.</p>
    <div style="margin: 20px 0; width: 100%;">
        {gallery_interior_html if interior_urls else "<p>인테리어 이미지를 불러오지 못했습니다.</p>"}
    </div>
    <br><br>
    <h2 style="font-size:20px;">5. {car_name} 자동차 총평</h2>
    <p style="font-size:15px;"></p>
    {ai_review_html}
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
        raise RuntimeError(f"Blogger 응답이 비정상적입니다: {res}")

    ws.update_cell(target_row, 3, "완")
    ws.update_cell(target_row, 15, res["url"])
    log_step(f"포스팅 성공: {res['url']}")
    print(f"[완료] 블로그 포스팅: {res['url']}")
    print(f"📸 이미지: 익스테리어 {len(exterior_urls)}개 + 인테리어 {len(interior_urls)}개 = 총 {len(web_image_urls)}개")
    print(f"📝 제목: {title_for_post}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        tb = traceback.format_exc()
        print("❌ 최종 실패:", e)
        print(tb)
        raise
