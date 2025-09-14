import re
import json
import requests
import random
import os
import urllib.parse
import sys, traceback
import time
from bs4 import BeautifulSoup
import gspread
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials
from googleapiclient.http import MediaFileUpload
from openai import OpenAI
from module_1 import make_thumb, to_webp

# ================================
# 출력 한글 깨짐 방지
# ================================
sys.stdout.reconfigure(encoding="utf-8")

# ================================
# OpenAI API 키 로드
# ================================
OPENAI_API_KEY = ""
if os.path.exists("openai.json"):
    with open("openai.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        OPENAI_API_KEY = data.get("api_key", "").strip()
if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    print("[ERROR] OpenAI API 키가 없습니다.")
    sys.exit(1)

client = OpenAI(api_key=OPENAI_API_KEY)

# ================================
# Google Sheets 인증
# ================================
SERVICE_ACCOUNT_FILE = "sheetapi.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)

SHEET_ID = os.getenv("SHEET_ID", "1V6ZV_b2NMlqjIobJqV5BBSr9o7_bF8WNjSIwMzQekRs")
ws = gc.open_by_key(SHEET_ID).sheet1

# ================================
# 블로그 ID 로테이션 (O1 셀)
# ================================
BLOG_IDS = ["1271002762142343021", "4265887538424434999", "6159101125292617147"]

try:
    last_index = int(ws.acell("O1").value or "-1")
except:
    last_index = -1
next_index = (last_index + 1) % len(BLOG_IDS)
BLOG_ID = BLOG_IDS[next_index]
ws.update_acell("O1", str(next_index))
print(f"👉 이번 포스팅 블로그 ID: {BLOG_ID}")

# ================================
# URL 가져오기 (E열 URL, G열 상태)
# ================================
target_row, my_url = None, None
rows = ws.get_all_values()
for i, row in enumerate(rows[1:], start=2):
    url_cell = row[4] if len(row) > 4 else ""   # E열
    status_cell = row[6] if len(row) > 6 else "" # G열
    if url_cell and (not status_cell or status_cell.strip() != "완"):
        my_url, target_row = url_cell, i
        break
if not my_url:
    print("🔔 처리할 새로운 URL이 없습니다.")
    sys.exit(0)
print("👉 이번에 처리할 URL:", my_url)

parsed = urllib.parse.urlparse(my_url)
params = urllib.parse.parse_qs(parsed.query)
wlfareInfoId = params.get("wlfareInfoId", [""])[0]
print("wlfareInfoId =", wlfareInfoId)

# ================================
# 복지 서비스 데이터 가져오기
# ================================
def fetch_welfare_info(wlfareInfoId):
    url = f"https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={wlfareInfoId}&wlfareInfoReldBztpCd=01"
    resp = requests.get(url)
    resp.encoding = "utf-8"
    html = resp.text
    outer_match = re.search(r'initParameter\((\{.*?\})\);', html, re.S)
    if not outer_match:
        raise ValueError("initParameter JSON을 찾지 못했습니다.")
    outer_data = json.loads(outer_match.group(1))
    inner_str = outer_data["initValue"]["dmWlfareInfo"]
    return json.loads(inner_str)

def clean_html(raw_html):
    return BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)

# ================================
# GPT 변환
# ================================
def process_with_gpt(section_title: str, raw_text: str, keyword: str) -> str:
    system_msg = (
        "너는 한국어 블로그 글을 쓰는 카피라이터야. "
        "주제는 정부 복지서비스이고, "
        "1) <b>굵게 요약</b>, "
        "2) 이어서 친절하고 풍성한 설명. "
        "3~4 문단, 반드시 <p data-ke-size=\"size18\"> 태그 사용."
    )
    user_msg = f"[섹션 제목] {keyword} {section_title}\n[원문]\n{raw_text}"
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=900,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("[WARN] GPT 실패:", e)
        return f"<p data-ke-size='size18'>{clean_html(raw_text)}</p>"

# ================================
# 서론·마무리 문구 (7문단 랜덤)
# ================================
synonyms = {
    "도움": ["도움", "지원", "혜택", "보탬", "이익", "유익", "거들음", "뒷받침", "보호", "후원", "안정망"],
    "안내": ["안내", "소개", "정리", "가이드", "설명", "풀이", "길잡이", "해설", "안내서", "알림"],
    "중요한": ["중요한", "핵심적인", "필수적인", "꼭 알아야 할", "가장 큰 의미가 있는", "생활에 필요한", "본질적인", "절대적인", "핵심 포인트가 되는"],
    "쉽게": ["쉽게", "간단히", "수월하게", "편리하게", "한결 수월하게", "부담 없이", "빠르게", "효율적으로", "신속하게"],
    "정보": ["정보", "내용", "자료", "소식", "데이터", "소식지", "알림", "소식거리", "핵심 요약", "필요한 지식"],
    "살펴보겠습니다": ["살펴보겠습니다", "알아보겠습니다", "정리하겠습니다", "확인해 보겠습니다", "차근차근 풀어보겠습니다", "하나씩 짚어보겠습니다", "꼼꼼히 다뤄보겠습니다"],
}
def choice(word): return random.choice(synonyms.get(word, [word]))

def make_intro(keyword):
    parts = [
        [f"{keyword}은 많은 분들이 관심을 갖는 {choice('중요한')} 제도입니다.",
         f"{keyword} 제도는 {choice('중요한')} 복지 서비스 중 하나입니다."],
        ["정부는 이를 통해 생활의 어려움을 덜어주고자 합니다.",
         "이 제도는 경제적 부담을 줄이는 데 큰 역할을 합니다."],
        [f"제도를 잘 이해하면 혜택을 더욱 {choice('쉽게')} 받을 수 있습니다.",
         "신청 과정을 정확히 알면 시행착오를 줄일 수 있습니다."],
        [f"오늘은 {keyword}의 개요부터 신청 방법까지 꼼꼼히 {choice('살펴보겠습니다')}.",
         f"이번 글에서는 {keyword}에 대해 전반적으로 {choice('안내')}합니다."],
        ["실제 생활에서 어떻게 활용되는지 사례를 통해 설명드리겠습니다.",
         "현장에서 유용하게 쓰이는 방안들도 함께 알려드리겠습니다."],
        ["끝까지 읽으시면 제도를 이해하는 데 큰 보탬이 되실 겁니다.",
         "여러분께 꼭 필요한 지식과 혜택을 전해드리겠습니다."],
        ["이 글은 복지 정책을 이해하는 데 실질적인 길잡이가 될 것입니다.",
         "궁금했던 부분들이 해소되도록 알차게 정리했습니다."]
    ]
    return " ".join(random.choice(p) for p in parts)

def make_last(keyword):
    parts = [
        [f"오늘은 {keyword} 제도를 {choice('안내')}했습니다.",
         f"이번 글에서 {keyword}의 핵심 내용을 다뤘습니다."],
        [f"이 {choice('정보')}를 참고하셔서 실제 신청에 {choice('도움')}이 되시길 바랍니다.",
         "꼭 필요한 분들이 혜택을 누리시길 바랍니다."],
        [f"앞으로도 다양한 복지 {choice('정보')}를 전해드리겠습니다.",
         "생활 속에서 꼭 필요한 정보를 전달드리겠습니다."],
        ["댓글과 의견도 남겨주시면 큰 힘이 됩니다.",
         "궁금한 점이 있으면 자유롭게 남겨주세요."],
        ["앞으로 다룰 주제에 대한 의견도 기다리겠습니다.",
         "관심 있는 다른 복지 제도도 차례차례 다룰 예정입니다."],
        ["읽어주셔서 감사합니다. 다음 글에서 다시 찾아뵙겠습니다.",
         "끝까지 읽어주셔서 감사드리며, 다음 글도 기대해 주세요."],
        ["여러분의 생활이 더욱 든든해지기를 바랍니다.",
         "복지 제도를 통해 삶이 한층 나아지시길 기원합니다."]
    ]
    return " ".join(random.choice(p) for p in parts)

# ================================
# Blogger 인증
# ================================
def get_blogger_service():
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data, ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# ================================
# 추천글 박스
# ================================
def get_related_posts(blog_id, count=4):
    import feedparser
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)
    if not feed.entries: return ""
    entries = random.sample(feed.entries, min(count, len(feed.entries)))
    box = """
<div style="background:#efede9;border-radius:8px;border:2px dashed #a7a297;
            box-shadow:#efede9 0px 0px 0px 10px;color:#565656;font-weight:bold;
            margin:2em 10px;padding:2em;">
  <p data-ke-size="size16"
     style="border-bottom:1px solid #555;color:#555;font-size:16px;
            margin-bottom:15px;padding-bottom:5px;">♡♥ 같이 보면 좋은글</p>
"""
    for entry in entries:
        box += f'<a href="{entry.link}" style="color:#555;font-weight:normal;">● {entry.title}</a><br>\n'
    return box + "</div>\n"

# ================================
# 본문 생성
# ================================
data = fetch_welfare_info(wlfareInfoId)
keyword = clean_html(data.get("wlfareInfoNm", "복지 서비스"))
title = f"2025 {keyword} 지원 대상 신청방법 총정리"

# 썸네일 (업로드 로직 단순화 예시)
safe_keyword = re.sub(r'[\\/:*?"<>|.]', "_", keyword)
folder = f"thumbs/{safe_keyword}"
os.makedirs(folder, exist_ok=True)
png_path = os.path.join(folder, f"{safe_keyword}.png")
make_thumb(png_path, title, 1)
to_webp(png_path)
img_url = f"https://lh3.googleusercontent.com/d/{safe_keyword}"  # 단순화

intro = make_intro(keyword)
last = make_last(keyword)

html = f"""
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{intro}</p><br />
<p style="text-align:center;">
    <img src="{img_url}" alt="{keyword} 썸네일" style="max-width:100%;height:auto;border-radius:10px;">
</p>
<span><!--more--></span><br />
"""

fields = {
    "개요": "wlfareInfoOutlCn",
    "지원대상": "wlfareSprtTrgtCn",
    "서비스내용": "wlfareSprtBnftCn",
    "신청방법": "aplyMtdDc",
    "추가정보": "etct"
}
for title_k, key in fields.items():
    value = data.get(key, "")
    if not value or value.strip() in ["", "정보 없음"]:
        continue
    processed = process_with_gpt(title_k, clean_html(value), keyword)
    html += f"<br /><h2 data-ke-size='size26'>{keyword} {title_k}</h2><br />{processed}<br /><br />"

related_box = get_related_posts(BLOG_ID)
html += f"""
<div style="margin:40px 0px 20px 0px;">
<p style="text-align:center;" data-ke-size="size18"><a class="myButton" href="{my_url}"> {keyword} </a></p><br />
<p data-ke-size="size18">{last}</p>
</div>
{related_box}
"""

# ================================
# 블로그 업로드
# ================================
post_body = {
    "content": html,
    "title": title,
    "labels": ["복지", "정부지원", "복지서비스"],
    "blog": {"id": BLOG_ID},
}
res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body, isDraft=False, fetchImages=True).execute()
print(f"[완료] 블로그 포스팅: {res['url']}")

# ================================
# ✅ 구글시트 업데이트 (G열/O열)
# ================================
ws.update_cell(target_row, 7, "완")        # G열
ws.update_cell(target_row, 15, res['url']) # O열
print("✅ 구글시트 업데이트 완료 (G열 '완' + O열 URL 기록)")
print(title)
