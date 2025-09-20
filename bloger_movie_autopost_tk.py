#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Excel(MOVIE_ID) → TMDB → Blogger 자동 포스팅 파이프라인 (터키어 버전)
- movies_discover.xlsx 읽기: A=제목, B=MOVIE_ID, C=개봉일, D=평점, E=투표수, F=완료표시
- F열이 "완"인 행은 건너뛰고, 첫 번째 미완료 행(B열의 MOVIE_ID)로 포스팅
- TMDB 상세/출연/이미지/리뷰/추천/예고편 수집
- 랜덤 스피너: 서론(6문장), 섹션 리드(4문장), 마무리(6문장)
- Blogger API로 발행 (blogId=4734685019625992643)
- 성공 시 해당 행 F열에 "완" 기록 후 저장
"""
import re
import urllib.parse
import os, sys, html, textwrap, requests, random, time, pickle
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
import json
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ===============================
# 📝 포스팅 설정
POST_COUNT = 1       # 몇 번 포스팅할지
POST_DELAY_MIN = 1   # 각 포스팅 후 대기 시간 (분 단위)
# ===============================
# 🔧 환경/경로 설정

BLOG_ID = "4734685019625992643"       # 터키 블로그 ID
CLIENT_SECRET_FILE = r"D:/py/cc.json" # 본인 구글 OAuth 클라이언트 시크릿 JSON 경로
BLOGGER_TOKEN_PICKLE = "blogger_token.pickle"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

# ===============================
# 🈶 TMDB 설정
LANG = "tr-TR"   # 터키어 (없으면 영어(en-US) fallback)
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"

# 🔑 TMDB 인증정보 (사용자 제공 값 그대로)
BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI1NmY0YTNiY2UwNTEyY2RjMjAxNzFhODMxNTNjMjVkNiIsIm5iZiI6MTc1NjY0NjE4OC40MTI5OTk5LCJzdWIiOiI2OGI0NGIyYzI1NzIyYjIzNDdiNGY0YzQiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.ShX_ZJwMuZ1WffeUR6PloXx2E7pjBJ4nAlQoI4l7nKY"
API_KEY = "56f4a3bce0512cdc20171a83153c25d6"

# ===============================
# 제목 패턴 목록 (터키어)
# ===============================
TITLE_PATTERNS = [
    "{title} {year} tam hikaye & resmi fragman yorumu",
    "{title} ({year}) film incelemesi, oyuncular ve fragman",
    "{title} {year} fragman & detaylı inceleme",
    "{title} filmi {year}: özet, oyuncular ve yorumlar",
    "Film {title} ({year}) hakkında her şey: özet, fragman, oyuncular",
    "{title} {year} inceleme: özet, oyuncular ve fragman",
    "{title} {year} resmi fragman + film yorumu",
    "Oyuncular ve inceleme {title} {year} — resmi özet ve fragman",
    "{title} {year} filmi: tam inceleme ve fragman",
    "Fragman & özet {title} ({year}) + detaylı yorum"
]

# ===============================
# 시트4 K1 셀 기반 로테이션 함수
# ===============================
def get_next_title_pattern(ws4, title, year):
    # 현재 인덱스 불러오기 (없으면 0으로 초기화)
    try:
        idx_val = ws4.acell("K1").value
        idx = int(idx_val) if idx_val and idx_val.isdigit() else 0
    except Exception:
        idx = 0

    # 패턴 선택
    pattern = TITLE_PATTERNS[idx % len(TITLE_PATTERNS)]
    blog_title = pattern.format(title=title, year=year)

    # 다음 인덱스 저장
    try:
        ws4.update_acell("K1", str(idx + 1))
    except Exception as e:
        print(f"⚠️ K1 셀 업데이트 실패: {e}")

    return blog_title


# 🔑 유튜브 API 인증정보
YOUTUBE_API_KEY = "AIzaSyD92QjYwV12bmLdUpdJU1BpFX3Cg9RwN4o"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# 🏷️ 해시태그 생성 함수
def make_hashtags_from_title(title: str) -> str:
    import re
    words = re.findall(r"[가-힣A-Za-zÀ-ÿ0-9]+", title)
    hashtags = ["#" + w for w in words if w.strip()]
    return " ".join(hashtags)

def get_movie_overview(movie_id, bearer=None, api_key=None):
    # 1차: 터키어
    data_tr = tmdb_get(f"/movie/{movie_id}", params={"language": "tr-TR"}, bearer=bearer, api_key=api_key)
    overview_tr = data_tr.get("overview")
    if overview_tr:
        return overview_tr

    # 2차: 영어 fallback
    data_en = tmdb_get(f"/movie/{movie_id}", params={"language": "en-US"}, bearer=bearer, api_key=api_key)
    overview_en = data_en.get("overview")
    if overview_en:
        return overview_en

    # 3차: 기본 메시지
    return "Özet bilgisi henüz mevcut değil."

def get_movie_title(movie_id, bearer=None, api_key=None):
    import html, re
    # 1. 터키어
    data_tr = tmdb_get(f"/movie/{movie_id}", params={"language": "tr-TR"}, bearer=bearer, api_key=api_key)
    title_tr = data_tr.get("title")

    if title_tr and not re.search(r"[ㄱ-ㅎ가-힣]", title_tr):
        return html.escape(title_tr)

    # 2. 영어 fallback
    data_en = tmdb_get(f"/movie/{movie_id}", params={"language": "en-US"}, bearer=bearer, api_key=api_key)
    title_en = data_en.get("title")

    if title_en:
        return html.escape(title_en)

    # 3. 최후 fallback
    return html.escape(data_tr.get("original_title") or "Başlık mevcut değil")

def get_youtube_trailers(title_tr, title_en=None, max_results=2):
    """유튜브에서 예고편 검색 (터키어 먼저, 없으면 영어로)"""
    def search(query):
        params = {
            "part": "snippet",
            "q": query,
            "key": YOUTUBE_API_KEY,
            "maxResults": max_results,
            "type": "video",
            "videoEmbeddable": "true"
        }
        try:
            r = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            videos = []
            for item in data.get("items", []):
                vid = item["id"]["videoId"]
                vtitle = item["snippet"]["title"]
                videos.append((vid, vtitle))
            return videos
        except Exception as e:
            print(f"❌ YouTube API 오류: {e}")
            return []

    # 1차: 터키어 제목 + "resmi fragman"
    if title_tr:
        results = search(f"{title_tr} resmi fragman")
        if results:
            return results

    # 2차: 영어 제목 + "trailer"
    if title_en:
        results = search(f"{title_en} trailer")
        if results:
            return results

    return []

# ===============================
# Google Sheets 연결
# ===============================
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    creds = ServiceAccountCredentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    SHEET_ID = "10kqYhxmeewG_9-XOdXTbv0RVQG9_-jXjtg0C6ERoGG0"
    return gc.open_by_key(SHEET_ID).get_worksheet(3)  # 시트4 (터키어 버전)


# ===============================
# 공통 유틸
def tmdb_get(path, params=None, bearer=None, api_key=None):
    url = f"{TMDB_V3_BASE}{path}"
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if params is None:
        params = {}
    if api_key and "api_key" not in params and not bearer:
        params["api_key"] = api_key
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def get_person_name_en(person_id, bearer=None, api_key=None):
    try:
        data = tmdb_get(
            f"/person/{person_id}",
            params={"language": "en-US"},
            bearer=bearer,
            api_key=api_key
        )
        name_en = data.get("name", "")
        return name_en
    except Exception as e:
        print(f"⚠️ Kişi adı alınamadı (ID {person_id}): {e}")
        return ""


def img_url(path, size="w780"):
    if not path:
        return None
    return f"{IMG_BASE}/{size}{path}"


def choose(*options):
    return random.choice(options)


def maybe(value, prob=0.5):
    return value if random.random() < prob else ""


# ===============================
# TMDB 수집기
def get_movie_bundle(movie_id, lang="tr-TR", bearer=None, api_key=None):
    params = {
        "language": lang,
        "append_to_response": "credits,images",
        "include_image_language": "tr,en,null"
    }
    return tmdb_get(f"/movie/{movie_id}", params=params, bearer=bearer, api_key=api_key)


def get_movie_reviews(movie_id, lang="tr-TR", bearer=None, api_key=None):
    """
    TMDB 리뷰 가져오기 (언어 폴백: tr-TR -> en-US -> 언어 미지정)
    """
    for lang_try in (lang, "en-US", None):
        try:
            params = {"language": lang_try} if lang_try else {}
            j = tmdb_get(f"/movie/{movie_id}/reviews", params=params, bearer=bearer, api_key=api_key)
            results = j.get("results", []) or []
            if results:
                return results
        except Exception as e:
            # 디버깅용 로그 (필요시)
            print(f"⚠️ reviews 요청 실패 (lang={lang_try}): {e}")
    return []



def get_movie_videos(movie_id, lang="tr-TR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/videos", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])


def get_movie_recommendations(movie_id, lang="tr-TR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/recommendations", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])


def get_movie_release_cert(movie_id, bearer=None, api_key=None):
    data = tmdb_get(f"/movie/{movie_id}/release_dates", bearer=bearer, api_key=api_key)
    results = data.get("results", [])

    def find_cert(cc):
        for r in results:
            if r.get("iso_3166_1") == cc:
                for d in r.get("release_dates", []):
                    c = (d.get("certification") or "").strip()
                    if c:
                        return c
        return ""

    # 1. 터키 등급
    tr = find_cert("TR")
    if tr:
        return f"Sınıflandırma {tr}"

    # 2. 미국 등급
    us = find_cert("US")
    if us:
        return f"Rated {us}"

    # 3. 한국 fallback
    kr = find_cert("KR")
    if kr:
        return f"Sınıflandırma {kr}"

    return ""

# ===============================
def make_intro_6(title, year, genres_str, director_names, main_cast, cert_label, runtime_min, keywords):
    year_txt = f"{year} yapımı" if year else "yapım yılı bilinmiyor"
    genre_phrase = genres_str if genres_str else "türü bilinmiyor"
    director_one = director_names[0] if director_names else ""
    star_one = main_cast[0] if main_cast else ""
    star_two = main_cast[1] if len(main_cast) > 1 else ""
    runtime_txt = f"{runtime_min} dakika" if runtime_min else "süre bilinmiyor"
    cert_txt = cert_label or "yaş sınırı bilinmiyor"

    # 1. Giriş (selamlama ve açılış)
    s1 = choose(
        f"Merhaba sinemaseverler! Bugün sizlere <b>{title}</b> ({year_txt}) filmini tanıtacağım.",
        f"Eğer sinemayı seviyorsanız, <b>{title}</b> ({year_txt}) kesinlikle ilginizi çekecek.",
        f"Hoş geldiniz! Bugünkü incelememizde <b>{title}</b> ({year_txt}) yer alıyor.",
        f"Sinemanın büyüsünü sevenler için <b>{title}</b>, {year_txt} dikkat çekici yapımlardan biri."
    )

    # 2. Tür açıklaması
    s2 = choose(
        f"Film {genre_phrase} türünde ve izleyiciyi baştan sona sürükleyici bir atmosfere sahip.",
        f"{genre_phrase} kategorisindeki bu yapım, güçlü anlatımıyla öne çıkıyor.",
        f"{genre_phrase} unsurları sayesinde film, duygusal yoğunluğu başarılı şekilde aktarıyor.",
        f"{genre_phrase} türünün en iyi örneklerinden biri olarak görülebilecek bir yapım."
    )

    # 3. Yönetmen açıklaması
    s3 = (
        choose(
            f"Yönetmen koltuğunda {director_one} bulunuyor ve filme kendine özgü bir dokunuş katmış.",
            f"{director_one} imzasıyla çekilen film, hem görselliği hem de hikâyesiyle dikkat çekiyor.",
            f"{director_one}, öyküyü etkileyici bir üslupla aktararak unutulmaz anlar yaratıyor.",
            f"Yaratıcı bakışıyla {director_one}, filmi özel bir noktaya taşıyor."
        ) if director_one else choose(
            "Yönetmenlik dengeli bir şekilde ilerliyor ve seyirciyi hikâyeye bağlı tutuyor.",
            "Abartıdan uzak, sade ama etkili bir anlatım tercih edilmiş.",
            "Anlatım akıcı ve tutarlı, bu sayede film kolayca takip ediliyor.",
            "Yönetim tarzı, filmi baştan sona ritimli ve sürükleyici kılıyor."
        )
    )

    # 4. Oyuncu açıklaması
    s4 = (
        choose(
            f"Başrollerde {star_one}{' ve ' + star_two if star_two else ''} yer alıyor; performansları izleyicilerden tam not alıyor.",
            f"Öne çıkan isimlerden biri {star_one}, oyunculuğuyla büyük beğeni topluyor.",
            f"{star_one}, özellikle duygusal sahnelerde başarılı bir performans sergiliyor.",
            f"Yetenekli kadro arasında {star_one}, rolüyle öne çıkanlardan biri."
        ) if star_one else choose(
            "Film, güçlü bir oyuncu kadrosuna sahip.",
            "Her oyuncu, hikâyeye önemli katkılar sağlıyor.",
            "Oyuncular, karakterleri inandırıcı bir şekilde canlandırıyor.",
            "Ekip performansı, filmin etkisini artırıyor."
        )
    )

    # 5. Süre ve yaş sınırı
    s5 = choose(
        f"Film süresi {runtime_txt}, bu da izleyiciye dengeli bir deneyim sunuyor.",
        f"Yaklaşık {runtime_txt} boyunca film, temposunu koruyarak ilerliyor.",
        f"{runtime_txt} uzunluğuyla film, detaylı bir sinema keyfi yaşatıyor."
    ) + " " + choose(
        f"Yaş sınırı {cert_txt}, bu nedenle farklı izleyici grupları için uygun.",
        f"Film {cert_txt} olarak sınıflandırılmış, bu da geniş kitlelere hitap etmesini sağlıyor.",
        f"{cert_txt} olması sayesinde izleyiciler kendilerine uygun zamanı kolayca seçebilir."
    )

    # 6. Etki açıklaması
    s6 = choose(
        f"<b>{title}</b>, vizyona girdiği andan itibaren dikkatleri üzerine çekti ve kültürel etkisiyle öne çıktı.",
        f"İlk gösteriminden bu yana <b>{title}</b>, kalitesiyle geniş yankı uyandırdı.",
        f"<b>{title}</b>, {year_txt} döneminin en çok konuşulan yapımlarından biri oldu.",
        f"<b>{title}</b>, yalnızca bir film değil; izleyicinin hafızasında yer eden bir deneyim."
    )

    # 7. Giriş bölümü kapanış
    s7 = choose(
        f"Şimdi gelin <b>{title}</b> filminin öne çıkan detaylarına birlikte göz atalım.",
        f"Bir sonraki bölümde <b>{title}</b>’nin özetine, oyuncularına ve güçlü yanlarına değineceğiz.",
        f"Hazırsanız, <b>{title}</b> dünyasını birlikte keşfedelim.",
        f"Gelin birlikte <b>{title}</b>’nin neden bu kadar ilgi gördüğünü inceleyelim."
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])


# ===============================
# 🎬 아웃트로 (7문장)
# ===============================
def make_outro_6(title, year, genres_str, director_names, keywords):
    year_txt = year if year else "bilinmiyor"
    director_one = director_names[0] if director_names else ""

    # 1. 마무리 인사 및 전체 리뷰 종료
    s1 = choose(
        f"<b>{title}</b> ({year_txt}) hakkındaki yazımızın sonuna geldik. Film, izleyicilere düşündürücü ve keyifli anlar sunuyor.",
        f"Böylece <b>{title}</b> ({year_txt}) incelemesini tamamladık. Sinemaseverlerin radarında olması gereken bir yapım.",
        f"<b>{title}</b> ({year_txt}) üzerine yaptığımız yolculuk burada sona eriyor; filmi özel kılan yönlerini ele aldık.",
        f"Bugünkü incelememiz <b>{title}</b> ({year_txt}) ile sona eriyor; dikkat çeken yönleriyle iz bırakan bir film."
    )

    # 2. 글 전체 요약
    s2 = choose(
        "Bu yazıda filmin özetine, oyuncu kadrosuna ve öne çıkan teknik detaylara değindik.",
        "İnceleme boyunca hikâye akışını, oyunculukları ve filmi güçlü kılan noktaları inceledik.",
        "Senaryo, yönetim ve filmin yarattığı etki üzerine önemli noktalara odaklandık.",
        f"Ayrıca <b>{title}</b>’nin atmosferini ve karakterlerini öne çıkaran unsurları konuştuk."
    )

    # 3. 감독에 대한 언급
    s3 = (
        choose(
            f"{director_one}’un yönetmenliği, filmin en güçlü yanlarından biri olarak öne çıkıyor.",
            f"{director_one}, filme kişisel dokunuşunu katarak duygusal ve teknik açıdan dengeli bir iş ortaya koymuş.",
            f"{director_one}’un vizyonu, hikâyeyi unutulmaz bir sinema deneyimine dönüştürüyor.",
            f"Şüphesiz {director_one}, bu yapımı hatırlanır kılan en önemli faktörlerden biri."
        ) if director_one else choose(
            "Yönetmenlik genel olarak dengeli, filmi sonuna kadar akıcı tutmayı başarıyor.",
            "Büyük bir isim olmasa da yönetim sağlam ve tutarlı bir yapı sunuyor.",
            "Yönetim tarzı sayesinde film boyunca izleyici dikkatini koruyor.",
            "Teknik açıdan olgun bir yönetim anlayışı, filmin kalitesini artırıyor."
        )
    )

    # 4. 평점과 비평에 대한 언급
    s4 = choose(
        "Puanlar ve eleştiriler yol gösterici olabilir ama asıl deneyim filmi izlerken yaşanır.",
        "Rakamlar önemli olsa da, filmle kurulan kişisel bağ çok daha değerlidir.",
        "Unutmayın, herkesin yorumu farklı olabilir; en iyisi filmi izleyip kendi kararınızı vermek.",
        "Skorlar sadece bir ölçüttür, gerçek etki her izleyicide farklıdır."
    )

    # 5. 추천 영화 안내
    s5 = choose(
        "Yazının sonunda sizlere benzer birkaç film de öneriyoruz; izleme listenize eklemeyi unutmayın.",
        "Bu filmi sevenler için, keyifle izleyebileceğiniz başka önerilerimiz de var.",
        "Ayrıca benzer türdeki diğer yapımlara da göz atmanızı tavsiye ederiz.",
        "Aynı atmosferi yaşamak isteyenler için birkaç alternatif film önerimiz bulunuyor."
    )

    # 6. 키워드 강조
    kw = ", ".join([k for k in (keywords or []) if k][:6]) if keywords else ""
    s6 = (
        choose(
            f"Öne çıkan anahtar kelimeler arasında {kw} yer alıyor; bu kavramlar filmi daha iyi anlamanıza yardımcı olabilir.",
            f"{kw} gibi anahtar kelimeler, filmin öne çıkan yönlerini özetliyor.",
            f"Bu yazıda {kw} gibi kavramlar üzerinde durarak filmin önemini vurguladık.",
            f"{kw} terimleri, filmin sinema içindeki yerini daha net ortaya koyuyor."
        ) if kw else "Umarız bu inceleme, izleme listeniz için faydalı bir rehber olmuştur."
    )

    # 7. 최종 마무리 인사
    s7 = choose(
        "Bu incelemeyi okuduğunuz için teşekkür ederiz. Bir sonraki film yazısında görüşmek üzere! 🙂",
        "Zaman ayırıp okuduğunuz için teşekkürler, umarız keyifli bir izleme deneyimi yaşarsınız. Hoşça kalın!",
        "Eğer bu yazıyı beğendiyseniz, paylaşabilir ve diğer film incelemelerimizi de takip edebilirsiniz.",
        "Sizlerle bu incelemeyi paylaşmak güzeldi. Çok yakında yeni film önerilerinde buluşalım!"
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])

# ===============================
# 📌 섹션 리드 (각 섹션마다 4문장)
# ===============================
def make_section_lead(name, title, year, genres_str, cert_label, extras=None):
    """각 섹션을 소개하는 4문장 도입부 (친근하고 풍부한 톤, 다양한 조합)"""
    extras = extras or {}
    year_txt = f"{year}" if year else ""
    genre_phrase = genres_str if genres_str else "türü bilinmiyor"
    cert_txt = cert_label or "yaş sınırı bilinmiyor"
    cast_top = extras.get("cast_top", [])
    who = "·".join(cast_top[:3]) if cast_top else ""
    director_one = extras.get("director_one", "")
    runtime_min = extras.get("runtime_min", None)
    runtime_txt = f"{runtime_min} dakika" if runtime_min else ""

    # 🎞️ 줄거리 섹션
    if name == "줄거리":
        base = [
            choose(
                f"<b>{title}</b>{f' ({year_txt})' if year_txt else ''} filminin hikâyesini spoiler vermeden kısaca özetleyelim.",
                f"Henüz izlememiş olanlar için <b>{title}</b>’nin ana konusunu açık ve anlaşılır bir şekilde paylaşacağım.",
                f"<b>{title}</b>’nin olay örgüsünü temel hatlarıyla aktaracağım, sadece ana noktalara odaklanarak.",
                f"Sürprizleri bozmadan, <b>{title}</b> filminin genel akışını birlikte keşfedelim.",
                f"Herkesin kolayca anlayabilmesi için <b>{title}</b>’nin özetini basit bir şekilde aktaracağım."
            ),
            choose(
                f"Hikâye sakin başlarken, {choose('gerilim yükseliyor', 'çatışmalar belirginleşiyor', 'ilişkiler karmaşıklaşıyor')} ve sonunda {choose('duygular doruğa çıkıyor', 'ipucu parçaları birleşiyor', 'mesaj daha da netleşiyor')}.",
                f"{choose('Açılış sahnesi sade başlıyor', 'İlk dakikalardan itibaren gerilim hissediliyor', 'Başlangıç huzurlu görünüyor')}, ardından {choose('karakterler harekete geçiyor', 'gizemler açığa çıkıyor', 'çatışmalar netleşiyor')} ve izleyici daha çok içine çekiliyor.",
                f"Olay örgüsü {choose('giriş→çatışma→çözüm', 'başlangıç→kriz→dönüşüm', 'karşılaşma→gerilim→seçim')} yapısını izliyor, her bölüm kendi vuruculuğunu taşıyor.",
                f"Hikâyenin ortalarında tempo hızlanıyor ve gerilim artıyor.",
                f"Finalde, baştan verilen ipuçları açığa çıkıyor ve heyecan zirveye ulaşıyor."
            ),
            choose(
                f"{genre_phrase} türünün havası, hikâyeye doğal bir şekilde yansıyor.",
                f"Fazla açıklamaya gerek kalmadan sahneler izleyiciyi içine çekiyor.",
                f"Büyük sürprizleri size bırakıyorum ama atmosferi hakkında küçük ipuçları verebilirim.",
                f"Anlatım sade ve akıcı, bu yüzden hikâyeyi takip etmek kolay oluyor.",
                f"Böylesi yapımlar daha çok ritim ve yönetim tarzıyla etkiler, diyaloglardan ziyade."
            ),
            choose(
                f"Yaş sınırı {cert_txt}, bu yüzden {choose('ailece izlemek için uygun.', 'arkadaşlarla keyifle izlenebilir.', 'tek başına odaklanarak izlemek için de ideal.')}",
                f"{cert_txt} sınıflandırmasıyla film, farklı izleyici grupları için rahatça tercih edilebilir.",
                f"Yaş sınırı {cert_txt}, ancak filmin temaları geniş bir kitleye hitap ediyor.",
                f"{cert_txt} olsa da hikâyenin verdiği mesaj herkes tarafından hissedilebilir."
            )
        ]
        if maybe(True, 0.4):
            base.append(
                choose(
                    "Şimdi biraz daha detaylara bakalım.",
                    "Ana sahneleri ve duygusal akışı birlikte inceleyelim.",
                    "Genel yapıyı gördük, şimdi ayrıntılara bakmak daha heyecan verici olacak."
                )
            )

    # 🎭 출연진 섹션
    elif name == "출연진":
        base = [
            choose(
                f"Bu filmde {who} {('ve diğerleri' if who else '')} yer alıyor. Daha isimlerini duyar duymaz neden bu kadar konuşulduğunu anlayabilirsiniz.",
                f"Oyuncu listesi {('şöyle: ' + who) if who else ''}. Daha en baştan dikkat çekiyor.",
                f"Filmin jeneriğinden itibaren tanıdık yüzler ekranda beliriyor{(' — ' + who) if who else ''}.",
                f"{who} {('ve diğer isimler' if who else '')} sayesinde izleyici bu filme güven duyuyor." if who else "Sadece kadroya bakmak bile beklentiyi yükseltiyor.",
                f"Ünlü oyuncular bir araya gelmiş, filme büyük bir enerji katıyor.",
                f"Oyuncu kadrosu bu filmi kesinlikle ‘izlenmeli’ kılıyor.",
                f"Bu güçlü kadro, yapımcıların neden bu kadar iddialı olduğunu gösteriyor.",
                f"Her oyuncu sahnede güçlü bir iz bırakıyor.",
                f"Sadece başrol isimler bile izleyiciyi cezbetmeye yetiyor.",
                f"Güvenilir oyuncuların varlığı izleyicide merak uyandırıyor."
            ),
            choose(
                f"Ana roller ve yan roller arasındaki denge, karakterleri daha canlı kılıyor.",
                f"{choose('Bakışlar ve jestler', 'Diyalog temposu', 'Oyuncular arası uyum')} sahneleri doğal bir şekilde güçlendiriyor.",
                f"Oyuncular arasındaki kimya, hikâyeyi akıcı ve inandırıcı hale getiriyor.",
                f"Performanslar tutarlı, izleyici kolayca içine çekiliyor.",
                f"Diyaloglar doğal ve ikna edici bir şekilde aktarılmış.",
                f"Roller arasındaki denge hikâyeyi daha gerçekçi kılıyor.",
                f"Oyuncuların uyumu sahnelere yoğunluk katıyor.",
                f"Duygu ve diyalogların ritmi mükemmel bir uyum yakalıyor.",
                f"Sahnelerde doğal bir akış hâkim, bu da filmi daha etkileyici kılıyor.",
                f"Hiçbir performans yapay durmuyor, bu da filmi gerçekçi kılıyor."
            ),
            choose(
                f"Özellikle {choose('karakterler arası zıtlıklar', 'kuşak farkları', 'değer çatışmaları')} izleyici için ilgi çekici bir etki yaratıyor.",
                f"{choose('Karakterler arası işbirliği', 'Ekip uyumu', 'Birlikte çalışma')} çok iyi yansıtılmış, sahneleri daha da keyifli hale getiriyor.",
                f"Küçük roller bile izleyicide iz bırakabiliyor, dikkatle bakın.",
                f"Yan roller hikâyeyi daha da zenginleştiriyor.",
                f"Oyuncular arasındaki sinerji her sahnede kendini belli ediyor.",
                f"Beklenmedik oyuncu kombinasyonları sahnelere heyecan katıyor.",
                f"Karakterler arası kontrast, filmin ana temasını güçlendiriyor.",
                f"Küçük roller bile hikâyeye katkı sağlıyor.",
                f"Hatta figüranlar bile sahnelere canlılık katıyor.",
                f"Tek bir sahne bile bazı oyuncular için unutulmaz bir an yaratabiliyor."
            ),
            choose(
                "Şimdi başrol oyuncularını tek tek tanıyalım.",
                "Sıradaki bölümde oyuncuların hayat verdiği karakterlere bakalım.",
                "Birazdan kadroyu daha detaylı inceleyeceğiz.",
                "Hangi aktörün hangi karakteri canlandırdığına göz atalım.",
                "Kadroya daha yakından bakalım.",
                "Her oyuncunun rolünü kısaca açıklayacağım.",
                "Oyuncular ve karakterlerini tek tek tanıtacağım.",
                "İşte filmdeki başlıca oyuncular ve karakterleri.",
                "Aşağıda oyuncular ve rolleri hakkında daha fazla bilgi bulabilirsiniz.",
                "Bakalım her oyuncu karakterine nasıl hayat vermiş."
            )
        ]

    # 🖼️ 스틸컷 섹션

    elif name == "스틸컷":
        base = [
            choose(
                "Sadece birkaç kareye bakarak bile filmin atmosferi hissediliyor.",
                "Birkaç görsel, bu yapımın havasını göstermek için yeterli.",
                "Kısa fotoğraflar bile filmin tonunu ve ruhunu yansıtabiliyor.",
                "Görüntülere bakar bakmaz yapımın gidişatını anlayabiliyorsunuz.",
                "Bir iki kare bile filmin ruhunu hissettiriyor.",
                "Kısa olsa da fotoğraflar hikâyenin duygusal özünü yansıtıyor.",
                "Az sayıda sahne bile atmosferi açıkça hissettiriyor.",
                "Bu görseller, filmle ilgili ilk izlenim olarak görülebilir.",
                "Kısacık görüntüler bile filmin dünyasını net şekilde ortaya koyuyor.",
                "Birkaç kare, hikâyeye dair merakı uyandırmaya yetiyor."
            ),
            choose(
                f"{choose('Sahne kompozisyonu', 'Kamera açıları', 'Mekân kullanımı')} dengeli ve göze hoş görünüyor.",
                f"{choose('Renk paleti', 'Işık kullanımı', 'Kontrastlar')} {choose('zarif', 'yumuşak', 'güçlü')} bir şekilde ayarlanmış ve sahnelere etki katıyor.",
                f"Prodüksiyon tasarımı {choose('ortama uyumlu', 'abartısız', 'duygularla uyumlu')} ve görsellere derinlik katıyor.",
                f"Görsel düzenleme dengeli, bu da filmi estetik açıdan cazip kılıyor.",
                f"Işık ve renk kullanımı oldukça etkileyici.",
                f"Küçük detaylara bile özen gösterildiği anlaşılıyor.",
                f"Kompozisyon ve renk uyumu, sahneleri tablo gibi gösteriyor.",
                f"Kamera hareketleri bile fotoğraflarda hissediliyor.",
                f"Renkler atmosferin oluşmasında önemli bir rol oynuyor.",
                f"Sanatsal yaklaşım, filmin ruhunu net şekilde yansıtıyor."
            ),
            choose(
                "Sadece karelere bakarak bile duygusal yolculuk hissedilebiliyor.",
                "Durağan görüntülerde bile karakterlerin duyguları hissediliyor.",
                "Fotoğraflar, bir sonraki sahneye dair merak uyandırıyor.",
                "Tek bir kare bile hikâyenin akmaya devam ettiğini hissettiriyor.",
                "Durağan olsa da fotoğraflar gerilimi taşıyor.",
                "Kısa bir an bile uzun bir etki bırakabiliyor.",
                "Detayların birçoğu yalnızca bu fotoğraflarda fark ediliyor.",
                "Bazı kareler, hikâyenin parçalarını bir araya getirmeye yardımcı oluyor.",
                "Karakterlerin ifadeleri, fotoğraflarda bile çok şey anlatıyor.",
                "Tek bir görüntü bile filmin genel havasını yansıtabiliyor."
            ),
            choose(
                "Aşağıdaki karelere bakarak filmin atmosferini hissedin.",
                "Bu fotoğrafları görmek, filmi izlerkenki deneyimi daha da artırır.",
                "Görsellere göz atarak filmi izlemeden önce büyüsünü hissedebilirsiniz.",
                "Fotoğrafları gördükten sonra filmdeki detaylar daha kolay fark edilecektir.",
                "Bu karelerde, yapımın önemli noktalarını keşfetmek mümkün.",
                "Görseller, küçük bir fragman gibi işlev görüyor.",
                "Fotoğraflara bakmak, hikâyeye girmeden önce sizi hazırlıyor.",
                "Bu sahneleri önceden görmek, izleme keyfini artırıyor.",
                "Görsellerden atmosferi yakalamak, deneyimi daha zengin kılıyor.",
                "Bu karelere bakın ve en çok hangi sahneyi merak ettiğinizi düşünün."
            )
        ]


    # 🎯 평점 및 인기 섹션
    elif name == "평점 및 인기":
        base = [
            choose(
                f"<b>{title}</b> için verilen puanlar, izleyicilerin tepkisini açıkça gösteriyor.",
                f"Sadece notlara bakarak bile filmin nasıl karşılandığını anlayabiliyoruz.",
                f"Skorlar, bir yapım hakkında ilk izlenimi hızlıca anlamanın yolu.",
                f"Verilen puanlar, izleyicilerin samimi hislerini yansıtıyor.",
                f"<b>{title}</b>’nin aldığı puan, popülaritesini ve önemini ortaya koyuyor.",
                f"Puanlar, filmin ne kadar sevildiğinin bir göstergesi.",
                f"Skorlar, beklenti ve memnuniyet düzeyini ölçmeye yardımcı oluyor.",
                f"Rating, bir filmin performansını görmenin en basit yolu.",
                f"<b>{title}</b>’nin aldığı notlar, izleyicilerin ilgisini dolaylı yoldan gösteriyor.",
                f"Puanlara bakarak bile bu filmin önemini hissedebilirsiniz."
            ),
            choose(
                "Oy sayısı ve ortalama puan, yalnızca istatistikten daha fazlasını ifade ediyor.",
                "Ne kadar çok oy varsa, değerlendirme o kadar güvenilir oluyor.",
                "Ortalama ile birlikte oy sayısına bakmak daha doğru bir tablo sunuyor.",
                "Oy sayısı arttıkça, halkın ilgisi daha net ortaya çıkıyor.",
                "Çok sayıda oy, filmin gerçekten gündemde olduğunu gösteriyor.",
                "Yüksek oy sayısı, izleyicilerin yoğun ilgisini işaret ediyor.",
                "Sadece ortalama değil, oyların çokluğu da önemli.",
                "Toplam oy, yapımın ne kadar geniş kitleye ulaştığını gösteriyor.",
                "Ortalama ile birlikte katılım, filmin konumunu daha net belirliyor.",
                "Veri, sayılardan daha fazlasını anlatıyor."
            ),
            choose(
                "Elbette, sayılar her şey demek değil. Asıl deneyim izleyerek yaşanır.",
                "Yüksek puan her zaman eğlence garantisi değildir, düşük puan da sıkıcı olduğu anlamına gelmez.",
                "Skorları sadece bir referans olarak kullanın: kişisel zevk çok daha önemlidir.",
                "Puanlar yalnızca bir yol gösterici, nihai karar sizin elinizde.",
                "Yüksek not almış olsa da, belki tarzınıza uygun olmayabilir.",
                "Düşük puanlı bir film, sizin için unutulmaz olabilir.",
                "Sonuçta önemli olan, izlerken yaşadığınız kişisel deneyimdir.",
                "Skorları hafif bir ipucu olarak görün, fazla önemsemeyin.",
                "Puanlar genel eğilimi gösterir, ama esas olan sizin görüşünüzdür.",
                "Rating sadece kısa bir rehberdir, gerisini siz deneyimlemelisiniz."
            ),
            choose(
                "Aşağıdaki tabloya sadece hafif bir referans olarak bakın.",
                "Verileri bir yol gösterici gibi düşünün, ama sezgilerinize güvenin.",
                "Tabloya göz atın ve izleyici tepkilerinin genel havasını hissedin.",
                "Sadece rakamlara değil, gerçek tepkilere bakmak da keyifli.",
                "Tablodaki verileri rehber alın, ama asıl cevabı izleyerek bulun.",
                "Ortalamanın ötesinde asıl ilginç olan, dağılım ve atmosferdir.",
                "Sadece sayılara değil, yorumlara da bakın, daha net anlayacaksınız.",
                "Aşağıdaki tablo, izleyici tepkilerini özetliyor, keyifle inceleyin.",
                "Sonuçta seçim size ait, sayılar yalnızca bir işaret.",
                "Tabloya göz atın ve genel izlenimi yakalayın."
            )
        ]


    # 🌟 베스트 리뷰 섹션
    elif name == "베스트 리뷰":
        base = [
            choose(
                "Kısa da olsa izleyici yorumları gerçek duyguları yansıtıyor.",
                "Tek satırlık bir yorum bile filmin havasını hissettirebiliyor.",
                "Halkın yorumlarına bakarak, filmin nasıl karşılandığını görebiliyoruz.",
                "Kısa ve öz yorumlar bile filmin cazibesini net şekilde gösteriyor.",
                "Yorumlar, puanlardan daha samimi ve güvenilir bir gösterge.",
                "Kısa yorumlar bile izleyicilerin içten hislerini yansıtıyor.",
                "Bir iki cümle, çoğu zaman filmin özünü taşıyor.",
                "Samimi izleyici görüşleri, istatistiklerden çok daha etkili.",
                "Bunlar filmi gerçekten izleyenlerin sözleri, bu yüzden daha güvenilir.",
                "Yorumlar, okuması keyifli canlı bir bakış açısı sunuyor."
            ),
            choose(
                "Zevke göre görüşler farklı olabilir — işte sinemanın güzelliği burada.",
                "Övgü ya da eleştiri, hepsi eserin geçerli yorumlarıdır.",
                "Olumlu ve olumsuz tepkilerin birleşimi daha bütünlüklü bir tablo sunuyor.",
                "Ne kadar farklı görüş varsa, film o kadar çok yönlüdür.",
                "Hem övgü hem de eleştiri, filmin gerçekten konuşulduğunu gösteriyor.",
                "Farklı bakış açıları sinemanın katmanlarını ortaya çıkarıyor.",
                "Aynı sahne bile farklı yorumlanabilir — bu da çok ilginçtir.",
                "Katılırsınız ya da katılmazsınız, çeşitlilik filmi özel kılıyor.",
                "Kimine göre başyapıt, kimine göre sıradan — bu çeşitlilik değerlidir.",
                "Tıpkı zevklerin farklı olması gibi, yorumlar da doğal çeşitlilik taşır."
            ),
            choose(
                "İşte sizin için birkaç ilginç yorumu derledim.",
                "Spoiler vermemeye dikkat ederek kısa yorumları seçtim.",
                "Bu örnek yorumlar, film hakkında net bir fikir veriyor.",
                "Kısa yorumları okumak keyifli ve bilgilendirici oluyor.",
                "Seçilmiş yorumlar kolayca kontrol edebilmeniz için derlendi.",
                "Birkaç yorumu referans olarak hazırladım.",
                "Kısa bir cümle bile filmin havasını anlatmaya yetiyor.",
                "Kısa olmaları, yorumları kolay okunur kılıyor.",
                "En dikkat çekici yorumları seçip buraya ekledim.",
                "Bu yorumlar, filmin farklı yönlerini gösteriyor."
            ),
            choose(
                "Okurken, sizin en çok hangi bölümü seveceğinizi fark edeceksiniz.",
                "Eğer sizi etkileyen bir cümle bulursanız, filmden sonra hatırlayın.",
                "Bir yorum duygularınızı yansıtırsa, özel bir empati oluşuyor.",
                "Başkalarının gözünden filmi yeniden görmek ilginçtir.",
                "Yorumları okumak, önemli noktaları önceden sezmenizi sağlıyor.",
                "Kısa bir yorum bile ‘işte böyle hissettiriyor’ dedirtebilir.",
                "Bazı yorumlar filmin gizli yönlerini ortaya çıkarıyor.",
                "Bazen izleyici yorumu, filmin sırlarını açığa çıkarabiliyor.",
                "Çeşitli görüşleri okumak, kendi bakış açınızı derinleştirebilir.",
                "Yorumlar sizin zevkinize uyduğunda çok keyif veriyor."
            )
        ]

    
    # 🎥 예고편 섹션     
    elif name == "예고편":
        base = [
            choose(
                "Fragman, filmin tonunu ve atmosferini hissetmenin en hızlı yoludur.",
                "Saniyeler içinde fragman, yapımın ana havasını ortaya koyar.",
                "Sadece fragmana bakarak bile filmin özünü yakalayabilirsiniz.",
                "Kısa ama yoğun fragman, filme olan merakı artırır.",
                "Fragman, izleme öncesinde filmin havasını keşfetmek için bir penceredir.",
                "Bir kartvizit gibi, fragman filmin ilk izlenimini verir.",
                "Birkaç saniyede bile fragman tüm cazibesini aktarır.",
                "Hızlı kesitler bile genel atmosferi hissettirir.",
                "Fragman, filmi izlemeden önce ilgiyi uyandıran bir araçtır.",
                "Sadece fragmandan bile filmin büyüsünü hissedebilirsiniz."
            ),
            choose(
                "Spoiler endişesi olmadan sadece atmosferi gözlemleyebilirsiniz.",
                "Fragman biraz merakı yatıştırsa da heyecanı artırır.",
                "Kısa sürede fragman, filmin ritmini ve duygusunu gösterir.",
                "Birkaç sahne ve müzikle bile izleyici içine çekilir.",
                "Fragmanı izledikten sonra filmi seyretme isteği daha da artar.",
                "Müzik ve kurgusu filmin kimliğini net şekilde ortaya koyar.",
                "Fragmanın temposu ve ritmi, filmin enerjisini yansıtır.",
                "Kısa olsa da fragman, hikâyeye dair ipuçları verir.",
                "Görüntü ve ses birleşimi izleyiciye ‘işte bu tarz’ dedirtir.",
                "Sadece birkaç saniyede bile unutulmaz sahneler gösterilir."
            ),
            choose(
                f"{runtime_txt+' süresindeki ' if runtime_txt else ''}fragmanı izleyerek filmin havasını hemen yakalayabilirsiniz.",
                "Bazen fragmandaki tek bir diyalog bile tüm filmi özetler.",
                "Fragmanın ilk ve son sahnesi önemli ipuçları barındırabilir.",
                "Kısa olsa da video güçlü bir mesaj taşır.",
                "Sadece fragmanla bile takip edeceğiniz duygusal çizgiyi hissedersiniz.",
                "Görseller ve müzik, filmin büyüsünü yansıtmaya yeter.",
                "Fragmandaki tek bir sahne bile filmi izlemek için sebep olabilir.",
                "Kısa olmasına rağmen fragman sizi içine çekmeyi başarır.",
                "Bir teaser bile uzun süre hafızada kalabilir.",
                "Bazı sahneler, filmin ana temasını göstermek için yeterlidir."
            ),
            choose(
                "Mümkünse bir kez kulaklıkla bir kez hoparlörle izleyin, farklı his verecektir.",
                "Altyazıyı açarak diyalogların tonunu ve havasını daha iyi yakalayabilirsiniz.",
                "Gerçekten kendinizi kaptırırsanız, kısa bir fragman bile etkileyici olur.",
                "İlk 10 saniye ve son 10 saniyede genelde filmin özü gizlidir.",
                "Fragmanı tekrar tekrar izlemek, gizli detayları açığa çıkarır.",
                "Fragmandaki sahneyi filmde görmek, izleyiciye ayrı bir tat verir.",
                "Dikkatle izlemek, yüzeysel bakmaktan çok daha iyidir.",
                "Kısa olsa da yapım detaylarını fark edebilirsiniz.",
                "Görüntü ve ses birleşimi bazen filmin kendisi kadar güçlü olabilir.",
                "Kısa süreli de olsa fragman, filmin havasını dürüstçe gösterir."
            ),
            choose(
                "Aşağıdaki videoyu izleyin, ilginizi çekerse filme devam edin.",
                "Fragman, ana yemeğe davet eden bir başlangıç gibidir.",
                "Eğer bu kısa kesit sizi etkilediyse, film çok daha güçlü gelecektir.",
                "Bir filmi size uygun olup olmadığını anlamanın en iyi yolu fragmandır.",
                "Fragmanı izledikten sonra karar vermek çok daha kolaydır.",
                "Tek bir video bile tarzınıza uyup uymadığını gösterebilir.",
                "Fragman, izleyicide beklenti oluşturmanın en iyi yoludur.",
                "Bir filmle ilk tanışmada fragman en iyi rehberdir.",
                "Eğer fragman bile duygusal hissettirdiyse, film daha da etkileyecektir.",
                "Aşağıdaki fragmanla filmin büyüsünden küçük bir parça hissedin."
            )
        ]


    # 🎥 추천 영화 섹션
    elif name == "추천 영화":
        base = [
            choose(
                f"Eğer <b>{title}</b> filmini izlediyseniz, aşağıdaki yapımlara da göz atmanızı öneririm.",
                f"<b>{title}</b> hoşunuza gittiyse, benzer havaya sahip bazı filmleri derledim.",
                f"<b>{title}</b> ile uyumlu önerileri posterleriyle birlikte sunuyorum.",
                f"Benzer atmosfere sahip filmleri posterlerle sizin için sıraladım.",
                f"Aşağıdaki posterlere göz atın, belki de zevkinize uygun birini bulursunuz.",
                f"<b>{title}</b> ile bağlantılı diğer yapımları bu posterlerde keşfedin."
            ),
            choose(
                "Bu öneriler sadece başlık ve posterlerden oluşuyor.",
                "Uzun açıklamalar yerine sadece görselleri sunuyorum.",
                "Sadece kaydırarak hafifçe göz atabilirsiniz.",
                "Posterleri ayrı tuttum ki kolayca inceleyebilesiniz.",
                "Detaylı açıklama yok — sadece görseller üzerinden fikir alabilirsiniz."
            ),
            choose(
                "İlginizi çeken bir poster olursa, kaydedip sonradan izleme listenize ekleyin.",
                "Beğendiğiniz varsa doğrudan listenize ekleyebilirsiniz.",
                "Posterlerden bile filmin havasını sezebilirsiniz.",
                "Hepsini bir arada görmek, seçim yapmayı daha eğlenceli hale getiriyor.",
                "Görsellerden birini seçip ‘bugünün filmi bu’ diyebilirsiniz."
            ),
            choose(
                "Hadi şimdi aşağıdaki posterlere birlikte bakalım.",
                "Aşağıdaki görselleri inceleyin ve size en uygun filmi seçin.",
                "Sadece posterlere bakarak bile filmlerin cazibesini görebilirsiniz.",
                "Aşağıdaki filmleri hemen kontrol edin.",
                "İşte sizin için birkaç eğlenceli ve keyifli öneri."
            )
        ]


    # 📌 기본 안내 (기타 섹션 처리)
    else:
        base = [
            choose(
                "Özetle sadece en önemli noktaları topladım, böylece hızlıca göz atabilir ve ihtiyacınıza göre işaretleyebilirsiniz.",
                "Yapıyı net bir şekilde düzenledim, yavaşça kaydırarak kolayca takip edebilirsiniz.",
                "En önemli bölümleri ayırdım — sadece ilgilendiğiniz kısımları okuyabilirsiniz."
            ),
            choose(
                "Makalede bölümler sezgisel bir sırayla düzenlendi, her biri kısa açıklamalarla destekleniyor.",
                "Anlatım, hikâye, bilgi ve inceleme arasında doğal bir akış sağlıyor.",
                "İsterseniz favori olarak işaretleyip daha sonra sakin şekilde tekrar okuyabilirsiniz."
            ),
            choose(
                "Araya birkaç kişisel ipucu da ekledim.",
                "Gereksiz kısımları çıkardım ve pratik önerilere odaklandım.",
                "Metin sade, akıcı ve kolay okunur şekilde hazırlandı."
            ),
            choose(
                "Şimdi aşağıdaki ana içeriğe geçelim.",
                "Evet, artık incelemenin asıl kısmına geldik."
            )
        ]
    return " ".join(base)   # ✅ 마지막에 반드시 반환

# ===============================
# HTML 빌더 - 추천 글 박스
def get_related_posts(blog_id, count=4):
    import feedparser
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    # 랜덤으로 count개 추출
    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    # HTML 박스 생성 (스타일 유지, 텍스트 터키어로 변경)
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ Önerilen Yazılar</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box


def build_html(post, title, cast_count=10, stills_count=8):
    esc = html.escape
    

    overview = esc(get_movie_overview(post["id"], bearer=BEARER, api_key=API_KEY))

    release_date = esc(post.get("release_date") or "")
    year = release_date[:4] if release_date else ""
    runtime = post.get("runtime") or 0
    genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
    genres_str = ", ".join(genres_list)
    tagline = esc(post.get("tagline") or "")
    adult_flag = bool(post.get("adult", False))

    # 제작 국가
    countries = [c.get("name","") for c in post.get("production_countries",[]) if c.get("name")]

    country_str = ", ".join(countries) if countries else "Ülke bilgisi yok"

    backdrop = img_url(post.get("backdrop_path"), "w1280")

    credits = post.get("credits", {}) or {}
    cast = credits.get("cast", [])[:cast_count]
    crew = credits.get("crew", [])
    directors = [c for c in crew if c.get("job") == "Director"]
    director_names = [esc(d.get("name","")) for d in directors]
    cast_names = [esc(p.get("name","")) for p in cast]

    backdrops = (post.get("images", {}) or {}).get("backdrops", [])
    backdrops = sorted(backdrops, key=lambda b: (b.get("vote_count",0), b.get("vote_average",0)), reverse=True)[:stills_count]

    cert = get_movie_release_cert(post["id"], bearer=BEARER, api_key=API_KEY)
    if not cert and adult_flag: 
        cert = "Yetişkinlere yönelik içerik"

    # 키워드 생성
    base_keywords = []
    for w in (title.replace(":", " ").replace("-", " ").split()):
        if len(w) > 1:
            base_keywords.append(str(w))
    base_keywords += genres_list + director_names[:2] + cast_names[:3]
    if year: base_keywords.append(str(year))
    if cert: base_keywords.append(str(cert))

    base_keywords += ["İnceleme", "Değerlendirme", "Oyuncular", "Fragman", "Görseller", "Film Önerileri"]

    seen, keywords = set(), []
    for k in base_keywords:
        if isinstance(k, str) and k and k not in seen:
            keywords.append(k)
            seen.add(k)

    intro_6 = make_intro_6(title, year, genres_str, director_names, cast_names, cert, runtime, keywords)

    # 출연진 테이블
    cast_rows = []
    for p in cast:
        name = esc(p.get("name",""))
        if re.search(r"[ㄱ-ㅎ가-힣]", name):
            name_en = get_person_name_en(p.get("id"), bearer=BEARER, api_key=API_KEY)
            if name_en:
                name = esc(name_en)
    
        ch = esc(p.get("character",""))
        prof = img_url(p.get("profile_path"), "w185")
        img_tag = f'<img src="{prof}" alt="{name}" style="width:72px;height:auto;border-radius:8px;">' if prof else ""
        cast_rows.append(
            f'<tr>'
            f'<td style="vertical-align:top;padding:8px 10px;white-space:nowrap;">{img_tag}</td>'
            f'<td style="vertical-align:top;padding:8px 10px;"><b>{name}</b><br><span style="color:#666;">{ch}</span></td>'
            f'</tr>'
        )

    cast_table = (
        '<table style="width:100%;border-collapse:collapse;border:1px solid #eee;">' +
        "".join(cast_rows or ['<tr><td style="padding:10px;">Oyuncu bilgisi bulunmuyor.</td></tr>']) +
        '</table>'
    )

    # 스틸컷
    still_divs = []
    for b in backdrops:
        p = img_url(b.get("file_path"), "w780")
        if not p: continue
        still_divs.append(
            f'<div style="flex:0 0 49%;margin:0.5%;"><img src="{p}" alt="{title} filminden sahne" style="width:100%;height:auto;border-radius:10px;"></div>'
        )
    stills_html = (
        '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;">' +
        "".join(still_divs or ['<div style="padding:10px;">Herhangi bir görsel bulunmuyor.</div>']) +
        '</div>'
    )

    # 평점
    rating_lead = make_section_lead("평점 및 인기", title, year, genres_str, cert)

    vote_avg = post.get("vote_average", 0)
    vote_count = post.get("vote_count", 0)
    popularity = post.get("popularity", 0)

    rating_html = f"""
    <div style="background:linear-gradient(135deg,#f9f9f9,#ececec);
                border:2px solid #ddd;border-radius:15px;
                padding:30px;margin:20px 0;
                box-shadow:0 4px 12px rgba(0,0,0,0.08);
                text-align:center;">
    <div style="font-size:20px;font-weight:bold;margin-bottom:12px;color:#333;">
        ⭐ Değerlendirme & 📊 Popülerlik
    </div>
    <div style="font-size:18px;color:#222;margin:8px 0;">
        <b style="color:#ff9800;">Ortalama Puan:</b> {vote_avg:.1f}/10
    </div>
    <div style="font-size:16px;color:#555;margin:6px 0;">
        Oy sayısı: {vote_count:,}
    </div>
    <div style="font-size:18px;color:#0066cc;margin-top:10px;">
        <b>Popülerlik:</b> {popularity:.1f}
    </div>
    </div>
    """

    # 예고편
    video_html = ""
    video_lead = make_section_lead("예고편", title, year, genres_str, cert)

    videos = get_movie_videos(post["id"], lang=LANG, bearer=BEARER, api_key=API_KEY)
    yt = next((v for v in videos if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")), None)
    if yt:
        yt_key = yt.get("key")
        video_html += f"<p>{video_lead}</p><iframe width='560' height='315' src='https://www.youtube.com/embed/{yt_key}' frameborder='0' allowfullscreen></iframe>"

    # YouTube API 보조 검색
    yt_results = get_youtube_trailers(post.get("title") or "", post.get("original_title") or "", max_results=2)
    if yt_results:
        video_html += "<br /><p>⚠️ Aşağıdaki fragman resmi olmayabilir.</p>"
        for vid, vtitle in yt_results:
            video_html += (
                f"<p><b>{vtitle}</b></p>"
                f"<iframe width='560' height='315' src='https://www.youtube.com/embed/{vid}' "
                f"frameborder='0' allowfullscreen></iframe><br>"
            )

    # 리뷰
    reviews = get_movie_reviews(post["id"], lang=LANG, bearer=BEARER, api_key=API_KEY)
    reviews_html = ""
    if reviews:
        review_blocks = []
        for r in reviews[:5]:
            auth = html.escape(r.get("author",""))
            rating = r.get("author_details",{}).get("rating")
            content = html.escape((r.get("content","") or "").strip())
            if len(content) > 300:
                content = content[:300] + "..."
            review_blocks.append(f"<div style='margin:10px 0;'><b>{auth}</b> ({rating if rating else 'N/A'})<br>{content}</div>")
        reviews_html = "<br /><br /><br />\n<h2>En İyi İncelemeler – "+title+"</h2>" + "".join(review_blocks)

    # 추천 영화
    recs = get_movie_recommendations(post["id"], lang=LANG, bearer=BEARER, api_key=API_KEY)[:6]
    rec_html = ""
    if recs:
        cards = []
        for m in recs:
            mtitle = html.escape(m.get("title", ""))
            year2 = (m.get("release_date") or "")[:4]
            poster2 = img_url(m.get("poster_path"), "w185")
            poster_tag = f"<img src='{poster2}' style='width:100%;border-radius:10px;'>" if poster2 else ""
            query = urllib.parse.quote(f"{mtitle} ({year2})")
            search_url = f"https://cineid.appsos.kr/search?q={query}"
            cards.append(
                f"<div style='flex:0 0 30%;margin:1%;text-align:center;'>"
                f"<a href='{search_url}' target='_blank' style='color:#000;text-decoration:none;'>{poster_tag}<br>{mtitle} ({year2})</a>"
                "</div>"
            )
        rec_lead = make_section_lead("추천 영화", title, year, genres_str, cert)
        rec_html = (
            "<br /><br /><br />\n<h2>Önerilen Filmler</h2>"
            f"<p>{rec_lead}</p>"
            "<div style='display:flex;flex-wrap:wrap;'>"
            + "".join(cards) +
            "</div>"
        )

    outro_6 = make_outro_6(title, year, genres_str, director_names, keywords)
    related_box = get_related_posts(BLOG_ID, count=4)

    blog_title1 = f"Film {title} ({year}) Konusu Oyuncular Fragman"
    hashtags = make_hashtags_from_title(blog_title1)

    html_out = f"""
<p>{intro_6}</p>
<!--more--><br />
{"<p><img src='"+backdrop+"' style='width:100%;border-radius:12px;'></p>" if backdrop else ""}
{"<p><i>"+html.escape(tagline)+"</i></p>" if tagline else ""}

<br /><br /><br />
<h2>Film {title} – Konu</h2>
<p><b>Ülke:</b> {country_str} | <b>Tür:</b> {genres_str if genres_str else "Bilgi yok"}</p>
<p>{make_section_lead("줄거리", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://cineid.appsos.kr/search/label/{year}?&max-results=10" target="_blank">
    <span style="font-size: medium;"><strong>{year} yılına ait önerilen filmleri keşfedin</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if year else ''}

<div style="background:#fafafa;border:2px solid #ddd;border-radius:12px;padding:10px 18px;">
  <p style="font-weight:bold;">🎬 {title} – Konu Özeti</p>
  {overview}
</div>
<br />{hashtags}

<br /><br /><br />
<h2>{title} Oyuncuları</h2>
<p>{make_section_lead("출연진", title, year, genres_str, cert, extras={"cast_top": cast_names})}</p>
{cast_table}
<br />{hashtags}

<br /><br /><br />
<h2>{title} Filminden Kareler</h2>
<p>{make_section_lead("스틸컷", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://cineid.appsos.kr/search/label/{urllib.parse.quote(genres_list[0])}?&max-results=10" target="_blank">
    <span style="font-size: medium;"><strong>{genres_list[0]} türündeki filmleri keşfedin</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if genres_list else ''}

{stills_html}
<br />{hashtags}

<br /><br /><br />
<h2>Değerlendirme ve Fragman</h2>
<p>{rating_lead}</p>
{rating_html}{video_html}
{reviews_html}{rec_html}
<br />{hashtags}

<p>{outro_6}</p>
{related_box}
<p style="font-size:12px;">Kaynak: <a href="https://www.themoviedb.org/" target="_blank">TMDB</a></p>

"""

    return textwrap.dedent(html_out).strip()

# ===============================
# Blogger 인증/발행
# ===============================
from google.oauth2.credentials import Credentials as UserCredentials
#from google.oauth2.service_account import Credentials as ServiceAccountCredentials 중복

BLOGGER_TOKEN_JSON = "blogger_token.json"  # refresh_token 포함 JSON 파일
SCOPES = ["https://www.googleapis.com/auth/blogger"]

def get_blogger_service():
    with open(BLOGGER_TOKEN_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data, ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)


def post_to_blogger(service, blog_id, title, html_content, labels=None, is_draft=False):
    body = {
        "title": title,
        "content": html_content
    }
    if labels:
        body["labels"] = labels
    posts = service.posts()
    res = posts.insert(blogId=blog_id, body=body, isDraft=is_draft, fetchImages=True).execute()
    return res


# ===============================
# 메인 실행부
def main():
    import io, sys, re

    # 로그 버퍼 설정
    log_buffer = io.StringIO()
    class Logger:
        def write(self, msg):
            log_buffer.write(msg)
            sys.__stdout__.write(msg)  # 콘솔에도 출력
        def flush(self):
            sys.__stdout__.flush()

    sys.stdout = Logger()
    sys.stderr = Logger()

    ws3 = get_sheet()
    service = get_blogger_service()

    rows = ws3.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        raw_id = row[1].strip() if len(row) > 1 else ""  # 원본 값
        movie_id = re.sub(r"\D", "", raw_id)            # 숫자만 추출
        done_flag = row[5].strip() if len(row) > 5 else ""

        if not movie_id:
            print(f"⚠️ Geçersiz MOVIE_ID: {raw_id} (satır {i}) → atlandı")
            continue

        if movie_id and done_flag != "완":
            print(f"👉 Hedef satır: {i} (MOVIE_ID={movie_id})")

            try:
                # 1) TMDB 상세 번들 수집
                post = get_movie_bundle(movie_id, lang=LANG, bearer=BEARER, api_key=API_KEY)

                # 2) 제목 가져오기
                title = get_movie_title(movie_id, bearer=BEARER, api_key=API_KEY)
                year = (post.get("release_date") or "")[:4]

                # 3) HTML 구성
                html_out = build_html(post, title, cast_count=CAST_COUNT, stills_count=STILLS_COUNT)

                # 4) 블로그 제목 생성
                blog_title = get_next_title_pattern(ws3, title, year)

                # 5) Blogger 발행
                genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
                labels = ["Film"] + ([year] if year else []) + genres_list

                res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=labels, is_draft=False)
                print(f"✅ Yayın tamamlandı: {res.get('url','(URL bilinmiyor)')}")

                # 6) Google Sheets 업데이트 (완)
                ws3.update_cell(i, 6, "완")
                print(f"✅ Google Sheets güncellendi (satır {i})")

            except Exception as e:
                print(f"❌ Yürütme sırasında hata oluştu: {e}")

            finally:
                # 7) 로그 기록 (P열 = 16열, append)
                try:
                    prev = ws3.cell(i, 16).value or ""
                    new_log = log_buffer.getvalue().strip().replace("\n", " | ")
                    new_val = (prev + " | " if prev else "") + new_log
                    ws3.update_cell(i, 16, new_val)
                    print(f"📌 Çalışma günlüğü kaydedildi (satır {i}, sütun P)")
                except Exception as log_e:
                    sys.__stdout__.write(f"❌ Günlük kaydedilemedi: {log_e}\n")

            break  # ✅ 한 건만 처리 후 종료


# ===============================
# 메인 호출부
# ===============================
if __name__ == "__main__":
    for n in range(POST_COUNT):
        print(f"\n🚀 {n+1}/{POST_COUNT} gönderi başlatılıyor")
        main()

        if n < POST_COUNT - 1 and POST_DELAY_MIN > 0:
            print(f"⏳ Sonraki gönderiden önce {POST_DELAY_MIN} dakika bekleniyor...")
            time.sleep(POST_DELAY_MIN * 60)



