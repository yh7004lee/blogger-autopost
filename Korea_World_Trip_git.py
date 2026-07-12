#!/usr/bin/env python3
# coding: utf-8

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
from google.oauth2.serviceaccount import Credentials as SACredentials
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

DEBUGMODE = True

def dprint(*args):
    if DEBUGMODE:
        print("DEBUG:", *args)

APIKEYSJSON = os.getenv("APIKEYSJSON")
if not APIKEYSJSON:
    raise RuntimeError("APIKEYSJSON is missing. Set it in GitHub Secrets.")

try:
    secrets = json.loads(APIKEYSJSON)
except Exception as e:
    raise RuntimeError(f"APIKEYSJSON parse failed: {e}")

OPENROUTERAPIKEY = secrets.get("OPENROUTERAPIKEY")
OPENAIAPIKEY = secrets.get("OPENAIAPIKEY")
GEMINIAPIKEY = secrets.get("GEMINIAPIKEY")
SHEETID = secrets.get("SHEETID", "1V6ZVb2NMlqjIobJqV5BBSr9o7bF8WNjSIwMzQekRs")
DRIVEFOLDERID = secrets.get("DRIVEFOLDERID")
GOOGLEMAPSAPIKEY = secrets.get("GOOGLEMAPSAPIKEY")
TOURAPIKEY = secrets.get("TOURAPIKEY")

TARGETGITHUBPAT = os.getenv("TARGETGITHUBPAT")
TARGETREPO = "jm7004lee/jm7004lee.github.io"
TARGETBRANCH = "main"
REPOPATH = os.getenv("TARGETREPOPATH", os.getcwd())
POSTSDIR = "posts"

HISTORYPATH = "processedregionsblogger.json"
SHEETGID = 2131907983

ASSETSBGDIR = "assets/backgrounds"
ASSETSFONTTTF = "assets/fonts/KimNamyun.ttf"
THUMBDIR = "thumbnails"

GITIGNORECONTENT = """2nd.json
2nd.json.b64
bloggertoken.json
cc.json
cc.json.b64
drivetoken2nd.pickle
drivetoken2nd.pickle.b64
openai.json
openai.json.b64
sheetapi.json
sheetapi.json.b64
thumbnails
"""

client = OpenAI(api_key=OPENAIAPIKEY) if OpenAI and OPENAIAPIKEY else None
genaiclient = None
if GEMINIAPIKEY and genai:
    try:
        genaiclient = genai.Client(api_key=GEMINIAPIKEY)
    except Exception as e:
        dprint("genai init failed:", e)
        genaiclient = None


def ensure_gitignore(rep_path):
    path = os.path.join(rep_path, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(GITIGNORECONTENT)


def get_sheet():
    serviceaccountfile = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SACredentials.from_service_account_file(serviceaccountfile, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEETID)
    for ws in sh.worksheets():
        if ws.id == SHEETGID:
            return ws
    raise RuntimeError(f"Worksheet with gid={SHEETGID} not found")


ws4 = get_sheet()


def get_drive_service(tokenpath="drivetoken2nd.pickle"):
    if not os.path.exists(tokenpath):
        raise RuntimeError("Drive token missing")
    with open(tokenpath, "rb") as f:
        creds = pickle.load(f)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tokenpath, "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds)


def ensure_drive_folder(drive_service, foldername):
    q = f"mimeType='application/vnd.google-apps.folder' and name='{foldername}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    foldermeta = {"name": foldername, "mimeType": "application/vnd.google-apps.folder"}
    folder = drive_service.files().create(body=foldermeta, fields="id").execute()
    return folder.get("id")


def upload_to_drive(filepath, filename):
    drive_service = get_drive_service()
    folderid = DRIVEFOLDERID or ""
    if not folderid or folderid == "YOURDRIVEFOLDERID":
        folderid = ensure_drive_folder(drive_service, "blogger")
    media = MediaFileUpload(filepath, mimetype="image/png", resumable=True)
    meta = {"name": filename, "parents": [folderid]}
    try:
        uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    except Exception:
        folderid = ensure_drive_folder(drive_service, "blogger")
        meta = {"name": filename, "parents": [folderid]}
        uploaded = drive_service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_service.permissions().create(
        fileId=uploaded["id"],
        body={"type": "anyone", "role": "reader", "allowFileDiscovery": False},
    ).execute()
    return f"https://lh3.googleusercontent.com/d/{uploaded['id']}"


def load_processed_regions():
    if not os.path.exists(HISTORYPATH):
        return []
    try:
        with open(HISTORYPATH, "r", encoding="utf-8") as f:
            return json.load(f).get("regions", [])
    except Exception as e:
        dprint("load_processed_regions failed:", e)
        return []


def save_processed_region(region, city):
    processed = load_processed_regions()
    key = f"{region} {city}"
    if key not in processed:
        processed.append(key)
        with open(HISTORYPATH, "w", encoding="utf-8") as f:
            json.dump({"regions": processed}, f, ensure_ascii=False, indent=2)


def log_step(row, msg):
    try:
        prev = ws4.cell(row, 16).value or ""
        ws4.update_cell(row, 16, f"{prev} {msg}".strip() if prev else msg)
    except Exception as e:
        print("log_step error:", e)


def pick_random_background():
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join(ASSETSBGDIR, ext)))
    return random.choice(files) if files else None


def textwrap_kor(text, width):
    if not text:
        return []
    words = text.split()
    if not words:
        return [text[i:i+width] for i in range(0, len(text), width)]
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if len(test) <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_thumb(savepath, var_title):
    os.makedirs(os.path.dirname(savepath), exist_ok=True)
    bgpath = pick_random_background()
    if bgpath and os.path.exists(bgpath):
        bg = Image.open(bgpath).convert("RGBA").resize((500, 500))
    else:
        bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

    try:
        font = ImageFont.truetype(ASSETSFONTTTF, 46)
    except Exception:
        font = ImageFont.load_default()

    canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
    canvas.paste(bg, (0, 0))
    rectangle = Image.new("RGBA", (500, 260), (0, 0, 0, 200))
    canvas.paste(rectangle, (0, 120), rectangle)
    draw = ImageDraw.Draw(canvas)

    lines = textwrap_kor(var_title, 12)
    bbox = font.getbbox("A")
    lineheight = (bbox[3] - bbox[1]) + 10
    total = len(lines) * lineheight
    y = 250 - total // 2

    for line in lines:
        tb = draw.textbbox((0, 0), line, font=font)
        w = tb[2] - tb[0]
        x = 250 - w // 2
        draw.text((x, y), line, fill="#FFEECB", font=font)
        y += lineheight

    canvas = canvas.resize((400, 400))
    canvas.save(savepath, "PNG")


def generate_ai_text(prompt, keyword=""):
    lasterr = None
    if genaiclient:
        try:
            response = genaiclient.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception as e:
            lasterr = e
    if genaiclient:
        try:
            response = genaiclient.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            text = getattr(response, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception as e:
            lasterr = e
    if client:
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1200,
            )
            text = res.choices[0].message.content.strip()
            if text:
                return text
        except Exception as e:
            lasterr = e
    return f"{keyword} {lasterr}"


def google_places_search(query, region, city):
    if not GOOGLEMAPSAPIKEY:
        return []
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": GOOGLEMAPSAPIKEY,
        "language": "ko",
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        data = res.json()
        if data.get("status") in ("ZERO_RESULTS", "INVALID_REQUEST", "OVER_QUERY_LIMIT", "REQUEST_DENIED"):
            return []
        return data.get("results", [])
    except Exception as e:
        dprint("google_places_search failed:", query, e)
        return []


def google_place_details(place_id):
    if not GOOGLEMAPSAPIKEY or not place_id:
        return {}
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_address,rating,user_ratings_total,photos,types,website,editorial_summary",
        "key": GOOGLEMAPSAPIKEY,
        "language": "ko",
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        data = res.json()
        return data.get("result", {}) or {}
    except Exception as e:
        dprint("google_place_details failed:", place_id, e)
        return {}


def google_photo_urls(place):
    urls = []
    photos = place.get("photos", []) or []
    for p in photos[:3]:
        ref = p.get("photo_reference")
        if ref:
            urls.append(
                "https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth=1600&photoreference={ref}&key={GOOGLEMAPSAPIKEY}"
            )
    return urls


def is_valid_image_url(url):
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        if r.status_code != 200:
            return False
        ctype = r.headers.get("content-type", "").lower()
        if "image" not in ctype:
            return False
        return len(r.content or b"") > 100
    except Exception:
        return False


def get_best_place_image(place):
    candidates = []
    for u in google_photo_urls(place):
        if u and u not in candidates:
            candidates.append(u)
    fallback = "https://via.placeholder.com/800x500?text=No+Image"
    verified = []
    for u in candidates:
        if is_valid_image_url(u):
            verified.append(u)
        if len(verified) >= 3:
            break
    while len(verified) < 3:
        verified.append(fallback)
    return verified[:3]


def clean_place_title(title, region, city):
    t = str(title or "").strip()
    if not t:
        return t
    t = re.sub(r"\s+", " ", t)
    return t


def generate_random_title(region, city):
    mids = ["핫플레이스", "인기", "필수"]
    suffixes = ["관광명소 TOP10", "여행코스 BEST10"]
    mid = random.choice(mids)
    suffix = random.choice(suffixes)
    return f"{region} {city} 가볼만한곳 {mid} {suffix}"


def fetch_overseas_places(region, city):
    places = []
    seen = set()

    queries = [
        f"{city} {region} tourist attractions",
        f"{city} {region} top attractions",
        f"{city} {region} must visit places",
        f"{city} {region} sightseeing",
        f"{city} {region} famous places",
    ]

    for q in queries:
        results = google_places_search(q, region, city)
        for r in results:
            name = (r.get("name") or "").strip()
            pid = (r.get("place_id") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)

            detail = google_place_details(pid) if pid else {}
            item = {
                "title": detail.get("name") or name,
                "addr": detail.get("formatted_address") or r.get("formatted_address", ""),
                "overview": (detail.get("editorial_summary") or {}).get("overview", ""),
                "rating": detail.get("rating", r.get("rating", 0)),
                "reviews": detail.get("user_ratings_total", r.get("user_ratings_total", 0)),
                "images": google_photo_urls(detail) if detail else [],
                "raw": detail or r,
            }
            places.append(item)
            if len(places) >= 10:
                break
        if len(places) >= 10:
            break

    if len(places) < 10:
        # fallback: duplicates less likely, but keep content flowing
        extra_q = f"{city} {region} landmarks"
        results = google_places_search(extra_q, region, city)
        for r in results:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            item = {
                "title": name,
                "addr": r.get("formatted_address", ""),
                "overview": "",
                "rating": r.get("rating", 0),
                "reviews": r.get("user_ratings_total", 0),
                "images": [],
                "raw": r,
            }
            places.append(item)
            if len(places) >= 10:
                break

    return places[:10]


def get_overview_from_place(place):
    return place.get("overview", "") or ""


def make_intro_prompt(region, city, title, placenames=None):
    placehint = ""
    if placenames:
        if isinstance(placenames, list):
            placehint = ", ".join(placenames[:10])
        else:
            placehint = str(placenames)
    return f"""
{region} {city} 여행 포스팅 소개문을 한국어로 작성해줘.
제목: {title}
장소 힌트: {placehint}
조건:
- 5~7문장
- 자연스럽고 블로그 스타일
- 과장된 광고 문구는 줄이고 정보성 있게
- HTML <p> 태그 사용
"""


def make_section_prompt(region, city, place_title, addr, overview):
    return f"""
다음 해외 여행 명소에 대한 본문을 한국어로 작성해줘.
지역: {region} {city}
명소명: {place_title}
주소: {addr}
개요: {overview}
조건:
- 4~6문장
- HTML <p> 태그 사용
- 여행 블로그 스타일
- 꼭 가보면 좋은 이유, 분위기, 방문 팁을 자연스럽게 포함
"""


def build_markdown_post(region, city, title, places, thumburl, datestr):
    placenames = [clean_place_title(p.get("title"), region, city) for p in places]
    intro = generate_ai_text(make_intro_prompt(region, city, title, placenames), title)

    sections = []
    for idx, item in enumerate(places, start=1):
        cleantitle = clean_place_title(item.get("title"), region, city)
        sectiontitle = f"{idx}. {cleantitle}"
        images = item.get("images", [])
        overview = get_overview_from_place(item)
        body = generate_ai_text(
            make_section_prompt(region, city, cleantitle, item.get("addr", ""), overview),
            cleantitle,
        )
        sec = []
        sec.append(f"## {sectiontitle}")
        for img in images[:1]:
            sec.append(f'<p><img src="{img}" alt="{cleantitle}" /></p>')
            break
        if item.get("addr"):
            sec.append(f"<p><strong>주소:</strong> {item['addr']}</p>")
        if body:
            sec.append(body)
        sections.append("\n".join(sec))

        time.sleep(0.2)

    joined_sections = "\n\n".join(sections)

    md = f"""---
    title: "{title}"
    date: {datestr}
    categories: [travel]
    tags: [{region}, {city}, overseas, travel]
    image: {thumburl}
    ---
    
    {intro}
    
    {joined_sections}
    """
    return md


def find_next_row_sheet4():
    values = ws4.get_all_values()
    for i, row in enumerate(values, start=1):
        if i == 1:
            continue
        region = row[0].strip() if len(row) > 0 and row[0].strip() else ""
        city = row[1].strip() if len(row) > 1 and row[1].strip() else ""
        status = row[3].strip() if len(row) > 3 and row[3].strip() else ""
        if region and city and status != "완":
            return i, region, city
    return None, None, None


def git_run(cmd, cwd=None, env=None):
    result = subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    return result


def push_post_to_github(filepath, repopath):
    if not TARGETGITHUBPAT:
        raise RuntimeError("TARGETGITHUBPAT missing")
    if not os.path.exists(os.path.join(repopath, ".git")):
        raise RuntimeError("Git repo not found")
    ensure_gitignore(repopath)

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    relpath = os.path.relpath(filepath, repopath)

    git_run(["git", "config", "user.name", "github-actions[bot]"], cwd=repopath, env=env)
    git_run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=repopath, env=env)
    remoteurl = f"https://x-access-token:{TARGETGITHUBPAT}@github.com/{TARGETREPO}.git"
    git_run(["git", "remote", "set-url", "origin", remoteurl], cwd=repopath, env=env)
    git_run(["git", "fetch", "origin", TARGETBRANCH], cwd=repopath, env=env)
    git_run(["git", "switch", "main"], cwd=repopath, env=env)
    git_run(["git", "reset", "--hard", f"origin/{TARGETBRANCH}"], cwd=repopath, env=env)
    git_run(["git", "add", relpath], cwd=repopath, env=env)

    status = subprocess.run(["git", "status", "--porcelain"], cwd=repopath, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env).stdout.strip()
    if not status:
        return "no changes"

    git_run(["git", "commit", "-m", f"Add post {os.path.basename(filepath)}"], cwd=repopath, env=env)
    git_run(["git", "push", "origin", TARGETBRANCH], cwd=repopath, env=env)
    return f"pushed to {TARGETBRANCH}"


def main():
    rowidx, region, city = find_next_row_sheet4()
    if not rowidx:
        print("No pending rows found.")
        return

    log_step(rowidx, "1")
    title = generate_random_title(region, city)
    log_step(rowidx, "2")

    places = fetch_overseas_places(region, city)
    if not places:
        raise RuntimeError(f"No places found for {region} {city}")

    for p in places:
        p["title"] = clean_place_title(p.get("title"), region, city)
        p["images"] = p.get("images") or []
        p["overview"] = p.get("overview") or ""

    time.sleep(0.3)

    safetitle = re.sub(r'[\\/:*?"<>|]', "", title).strip()
    datestr = datetime.now().strftime("%Y-%m-%d %H:%M:%S +0900")
    postfilename = f"{datetime.now().strftime('%Y-%m-%d')}-{safetitle}.md"
    postpath = os.path.join(REPOPATH, POSTSDIR, postfilename)
    os.makedirs(os.path.dirname(postpath), exist_ok=True)

    thumbpath = os.path.join(THUMBDIR, f"{safetitle}.png")
    make_thumb(thumbpath, title)
    log_step(rowidx, "3")

    thumburl = upload_to_drive(thumbpath, f"{safetitle}.png")
    log_step(rowidx, "4")

    markdowncontent = build_markdown_post(region, city, title, places, thumburl, datestr)
    with open(postpath, "w", encoding="utf-8") as f:
        f.write(markdowncontent)
    log_step(rowidx, "5")

    push_post_to_github(postpath, REPOPATH)
    ws4.update_cell(rowidx, 4, "완")
    ws4.update_cell(rowidx, 15, f"https://github.com/{TARGETREPO}/blob/{TARGETBRANCH}/{POSTSDIR}/{postfilename}")
    save_processed_region(region, city)
    log_step(rowidx, "6")

    print(postpath)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
