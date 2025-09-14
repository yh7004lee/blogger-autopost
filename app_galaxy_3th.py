import os
import re
import time
import random
import urllib.request
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager
from openai import OpenAI
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.http import MediaFileUpload

# ✅ OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Google Sheets API 인증 (서비스 계정 JSON → base64 decode 해서 넣어둬야 함)
SHEET_ID = os.getenv("SHEET_ID")
SHEET_RANGE = "시트1!A2:D1000"
creds = Credentials.from_service_account_file("sheetapi.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
sheets_service = build("sheets", "v4", credentials=creds)

# ✅ Blogger API 인증
creds_blogger = Credentials.from_authorized_user_file("blogger_token.json", scopes=["https://www.googleapis.com/auth/blogger"])
if not creds_blogger.valid:
    creds_blogger.refresh(Request())
blog_service = build("blogger", "v3", credentials=creds_blogger)

# ✅ Drive API 인증
creds_drive = Credentials.from_authorized_user_file("blogger_token.json", scopes=["https://www.googleapis.com/auth/drive.file"])
if not creds_drive.valid:
    creds_drive.refresh(Request())
drive_service = build("drive", "v3", credentials=creds_drive)

# =================================================================
# 구글시트에서 키워드 가져오기
# =================================================================
def get_next_keyword():
    result = sheets_service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=SHEET_RANGE).execute()
    rows = result.get("values", [])
    for idx, row in enumerate(rows, start=2):
        if len(row) >= 4 and row[3].strip() == "완":
            continue
        if len(row) >= 3:
            return idx, row[0], row[1], row[2]
    return None, None, None, None

row_idx, prefix, keyword, suffix = get_next_keyword()
if not keyword:
    print("✅ 처리할 새로운 키워드 없음")
    exit()

title = f"{prefix} {keyword} {suffix}"
print("👉 이번 포스팅 타이틀:", title)

# =================================================================
# 썸네일 생성 → 구글드라이브 업로드
# =================================================================
def upload_to_drive(file_path, file_name, folder_name="blogger"):
    q = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id,name)").execute()
    files = res.get("files", [])
    if files:
        folder_id = files[0]["id"]
    else:
        folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
        folder_id = folder["id"]

    media = MediaFileUpload(file_path, mimetype="image/webp", resumable=True)
    file = drive_service.files().create(body={"name": file_name, "parents": [folder_id]}, media_body=media, fields="id").execute()
    file_id = file["id"]

    drive_service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}).execute()
    return f"https://lh3.googleusercontent.com/d/{file_id}"

# =================================================================
# Selenium (headless 모드)
# =================================================================
options = webdriver.FirefoxOptions()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
service = Service(executable_path=GeckoDriverManager().install())
driver = webdriver.Firefox(service=service, options=options)

url = f"https://play.google.com/store/search?q={keyword}&c=apps"
driver.get(url)
time.sleep(2)

soup = BeautifulSoup(driver.page_source, "html.parser")
source = soup.find_all(class_="ULeU3b")

app_links = []
for k, s in enumerate(source):
    if k == 15:
        break
    link = "https://play.google.com" + s.find("a")["href"]
    app_links.append(link)

del app_links[:3]
print(f"[총 앱 링크] {len(app_links)}개")

# =================================================================
# ChatGPT 재작성 함수
# =================================================================
def rewrite_app_description(original_html: str, app_name: str, keyword_str: str) -> str:
    compact = BeautifulSoup(original_html, "html.parser").get_text(separator=" ", strip=True)
    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "사실은 유지하되 문장과 구성을 완전히 새로 쓰고, "
        "자연스럽고 따뜻한 톤으로 풀어줘. "
        "마크다운 금지, <p data-ke-size=\"size18\"> 문단만 사용."
    )
    user_msg = f"[앱명] {app_name}\n[키워드] {keyword_str}\n[원문]\n{compact}"

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
        temperature=0.7,
        max_tokens=600,
    )
    rewritten = resp.choices[0].message.content.strip()
    if "<p" not in rewritten:
        rewritten = f'<p data-ke-size="size18">{rewritten}</p>'
    return rewritten

# =================================================================
# HTML 본문 생성
# =================================================================
html_content = f"<h1>{title}</h1><br />"
for j, app_url in enumerate(app_links, 1):
    if j > 5:
        break
    driver.get(app_url)
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    try:
        app_name = soup.find("h1").text.strip()
    except:
        app_name = f"앱 {j}"
    desc_div = soup.find("div", class_="fysCi")
    if desc_div:
        rewritten = rewrite_app_description(str(desc_div), app_name, keyword)
    else:
        rewritten = "<p data-ke-size='size18'>설명을 가져오지 못했습니다.</p>"
    html_content += f"<h2>{j}. {app_name} 어플 소개</h2>{rewritten}<br />"

driver.quit()

# =================================================================
# Blogger 포스팅
# =================================================================
BLOG_IDS = ["1271002762142343021", "4265887538424434999", "6159101125292617147"]
last_index_file = "last_blog_index.txt"

if os.path.exists(last_index_file):
    with open(last_index_file, "r") as f:
        last_index = int(f.read().strip())
else:
    last_index = -1

next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]

post = {
    "title": title,
    "content": html_content,
    "labels": ["앱", "추천", keyword],
}
res = blog_service.posts().insert(blogId=BLOG_ID, body=post, isDraft=False, fetchImages=True).execute()
print("✅ 포스팅 완료:", res["url"])

with open(last_index_file, "w") as f:
    f.write(str(next_index))

# =================================================================
# 구글시트 업데이트 (D열 → "완")
# =================================================================
sheets_service.spreadsheets().values().update(
    spreadsheetId=SHEET_ID,
    range=f"시트1!D{row_idx}",
    valueInputOption="RAW",
    body={"values": [["완"]]}
).execute()

print("✅ 구글시트 업데이트 완료")
