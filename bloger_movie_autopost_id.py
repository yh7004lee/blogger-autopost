#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Excel(MOVIE_ID) → TMDB → Blogger 자동 포스팅 파이프라인
- movies_discover.xlsx 읽기: A=제목, B=MOVIE_ID, C=개봉일, D=평점, E=투표수, F=완료표시
- F열이 "완"인 행은 건너뜨고, 첫 번째 미완료 행(B열의 MOVIE_ID)로 포스팅
- TMDB 상세/출연/이미지/리뷰/추천/예고편 수집
- 랜덤 스피너: 서론(6문장), 섹션 리드(4문장), 마무리(6문장)
- Blogger API로 발행 (blogId=2662415517177573864)
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
POST_COUNT = 1       # 몇 번 포스팅할지 (예: 10 이면 10회 반복)
POST_DELAY_MIN = 1   # 각 포스팅 후 대기 시간 (분 단위, 0 이면 즉시 다음 실행)
# ===============================
# 🔧 환경/경로 설정

BLOG_ID = "2662415517177573864"       # 인도네시아 블로그 ID
CLIENT_SECRET_FILE = r"D:/py/cc.json" # 본인 구글 OAuth 클라이언트 시크릿 JSON 경로
BLOGGER_TOKEN_PICKLE = "blogger_token.pickle"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

# ===============================
# 🈶 TMDB 설정
LANG = "id-ID"   # 인도네시아어 (없으면 영어(en-US) fallback)
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"

# 🔑 TMDB 인증정보 (사용자가 제공한 값 그대로 사용)
BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI1NmY0YTNiY2UwNTEyY2RjMjAxNzFhODMxNTNjMjVkNiIsIm5iZiI6MTc1NjY0NjE4OC40MTI5OTk5LCJzdWIiOiI2OGI0NGIyYzI1NzIyYjIzNDdiNGY0YzQiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.ShX_ZJwMuZ1WffeUR6PloXx2E7pjBJ4nAlQoI4l7nKY"
API_KEY = "56f4a3bce0512cdc20171a83153c25d6"

# ===============================
# 제목 패턴 목록 (인도네시아어)
# ===============================
TITLE_PATTERNS = [
    "{title} {year} sinopsis lengkap & review trailer resmi",
    "Sinopsis dan review film {title} ({year}) pemain & trailer",
    "Trailer resmi {title} {year} + sinopsis & ulasan pemain",
    "Ulasan film {title} {year}: sinopsis, pemain & trailer",
    "Sinopsis film {title} {year} dengan review & pemain",
    "{title} {year} film: sinopsis lengkap, trailer & review",
    "Review {title} {year} + sinopsis + pemain",
    "{title} {year} selengkapnya: sinopsis, pemain, ulasan & trailer",
    "Trailer & sinopsis film {title} ({year}) + review",
    "Pemain & ulasan {title} {year} — sinopsis resmi & trailer"
]

# ===============================
# 시트3 K1 셀 기반 로테이션 함수
# ===============================
def get_next_title_pattern(ws3, title, year):
    # 현재 인덱스 불러오기 (없으면 0으로 초기화)
    try:
        idx_val = ws3.acell("K1").value
        idx = int(idx_val) if idx_val and idx_val.isdigit() else 0
    except Exception:
        idx = 0

    # 패턴 선택
    pattern = TITLE_PATTERNS[idx % len(TITLE_PATTERNS)]
    blog_title = pattern.format(title=title, year=year)

    # 다음 인덱스 저장
    try:
        ws3.update_acell("K1", str(idx + 1))
    except Exception as e:
        print(f"⚠️ K1 셀 업데이트 실패: {e}")

    return blog_title


# 🔑 유튜브 API 인증정보
YOUTUBE_API_KEY = "AIzaSyD92QjYwV12bmLdUpdJU1BpFX3Cg9RwN4o"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# 🏷️ 해시태그 생성 함수
def make_hashtags_from_title(title: str) -> str:
    import re
    # 괄호를 무시하고 단어/숫자만 뽑기
    words = re.findall(r"[가-힣A-Za-zÀ-ÿ0-9]+", title)
    hashtags = ["#" + w for w in words if w.strip()]
    return " ".join(hashtags)


def get_movie_title(movie_id, bearer=None, api_key=None):
    import html, re
    # 1. 인도네시아어
    data_id = tmdb_get(f"/movie/{movie_id}", params={"language": "id-ID"}, bearer=bearer, api_key=api_key)
    title_id = data_id.get("title")

    if title_id and not re.search(r"[ㄱ-ㅎ가-힣]", title_id):
        return html.escape(title_id)

    # 2. 영어 fallback
    data_en = tmdb_get(f"/movie/{movie_id}", params={"language": "en-US"}, bearer=bearer, api_key=api_key)
    title_en = data_en.get("title")

    if title_en:
        return html.escape(title_en)

    # 3. 최후 fallback
    return html.escape(data_id.get("original_title") or "Judul tidak tersedia")


def get_youtube_trailers(title_id, title_en=None, max_results=2):
    """유튜브에서 예고편 검색 (인도네시아어 먼저, 없으면 영어로)"""
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

    # 1차: 인도네시아어 제목 + "trailer resmi"
    if title_id:
        results = search(f"{title_id} trailer resmi")
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
    return gc.open_by_key(SHEET_ID).get_worksheet(2)  # 시트3


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
        print(f"⚠️ Gagal mengambil nama untuk ID {person_id}: {e}")
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
def get_movie_bundle(movie_id, lang="id-ID", bearer=None, api_key=None):
    params = {
        "language": lang,
        "append_to_response": "credits,images",
        "include_image_language": "id,en,null"
    }
    return tmdb_get(f"/movie/{movie_id}", params=params, bearer=bearer, api_key=api_key)


def get_movie_reviews(movie_id, lang="id-ID", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/reviews", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])


def get_movie_videos(movie_id, lang="id-ID", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/videos", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])


def get_movie_recommendations(movie_id, lang="id-ID", bearer=None, api_key=None):
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

    # 1. 인도네시아 등급
    idn = find_cert("ID")
    if idn:
        return f"Klasifikasi {idn}"

    # 2. 미국 등급
    us = find_cert("US")
    if us:
        return f"Rated {us}"

    # 3. 한국 fallback
    kr = find_cert("KR")
    if kr:
        return f"Klasifikasi {kr}"

    return ""

# ===============================
def make_intro_6(title, year, genres_str, director_names, main_cast, cert_label, runtime_min, keywords):
    year_txt = f"dirilis pada {year}" if year else "tahun rilis tidak diketahui"
    genre_phrase = genres_str if genres_str else "genre tidak diketahui"
    director_one = director_names[0] if director_names else ""
    star_one = main_cast[0] if main_cast else ""
    star_two = main_cast[1] if len(main_cast) > 1 else ""
    runtime_txt = f"{runtime_min} menit" if runtime_min else "durasi tidak diketahui"
    cert_txt = cert_label or "klasifikasi tidak diketahui"

    # 1. 서론 (인사 및 도입부)
    s1 = choose(
        f"Halo pecinta film! Kali ini kita akan menyelami dunia <b>{title}</b>, {year_txt}, sebuah karya yang layak mendapat perhatian Anda.",
        f"Jika Anda menyukai dunia perfilman, Anda pasti akan tertarik mengenal lebih jauh tentang <b>{title}</b>, {year_txt}, sebuah judul yang sudah memikat banyak hati.",
        f"Selamat datang! Hari ini sorotan kita jatuh pada <b>{title}</b>, {year_txt}, sebuah film yang membangkitkan emosi dan diskusi menarik.",
        f"Sinema selalu menghadirkan karya tak terlupakan, dan <b>{title}</b>, {year_txt}, jelas salah satunya yang akan kita bahas bersama."
    )

    # 2. 장르 설명
    s2 = choose(
        f"Film ini bergenre {genre_phrase}, menghadirkan emosi dan kedalaman dengan cara yang menarik.",
        f"Termasuk dalam kategori {genre_phrase}, karya ini mampu menyampaikan perasaan kuat dan momen berkesan.",
        f"Dengan ciri khas {genre_phrase}, film ini berhasil menarik perhatian dari awal hingga akhir.",
        f"Menyuguhkan nuansa {genre_phrase}, kisahnya berkembang dengan cara yang memikat."
    )

    # 3. 감독 설명
    s3 = (
        choose(
            f"Disutradarai oleh {director_one}, yang memberikan gaya unik dan meninggalkan jejak di setiap adegan.",
            f"Dengan {director_one} sebagai sutradara, karya ini menjadi pengalaman visual dan naratif yang tak terlupakan.",
            f"{director_one} membawakan cerita dengan penuh kepekaan dan ketegasan, menciptakan momen berkesan.",
            f"Sentuhan kreatif dari {director_one} menjadikan film ini sesuatu yang sangat spesial."
        ) if director_one else choose(
            "Arahannya seimbang, dengan pilihan kreatif yang membuat penonton tetap terhanyut.",
            "Tanpa berlebihan, penggarapan cerita dilakukan dengan rapi dan efektif.",
            "Narasi berkembang dengan alur yang jelas dan konsisten, membuat cerita mudah diikuti.",
            "Cara penyutradaraan membuat kisah ini tetap penuh ritme dan emosi dari awal sampai akhir."
        )
    )

    # 4. 출연진 설명
    s4 = (
        choose(
            f"Pemeran utama bersinar dengan nama seperti {star_one}{' dan ' + star_two if star_two else ''}, menghadirkan akting yang berkesan.",
            f"Salah satu sorotan pemeran adalah {star_one}, dengan penampilan yang layak diapresiasi.",
            f"Akting yang solid penuh emosi ditampilkan, dengan {star_one} menonjol di momen penting.",
            f"Selain deretan aktor berbakat, {star_one} tampil menawan dengan perannya."
        ) if star_one else choose(
            "Film ini dipenuhi aktor berbakat yang memperkaya jalan cerita.",
            "Setiap anggota pemeran memberikan kontribusi berarti dalam cerita.",
            "Para aktor berhasil menghadirkan interpretasi yang memperkuat intensitas kisah.",
            "Kehadiran para pemain membuat karakter-karakter terasa hidup dan meyakinkan."
        )
    )

    # 5. 상영시간 및 등급 설명
    s5 = choose(
        f"Film ini berdurasi {runtime_txt}, sehingga memberikan pengalaman menonton yang seimbang dan menarik.",
        f"Dengan durasi {runtime_txt}, alur cerita mampu menjaga ritme tanpa terasa membosankan.",
        f"Durasi {runtime_txt} terasa pas untuk menikmati setiap detail kisah."
    ) + " " + choose(
        f"Klasifikasi usia adalah {cert_txt}, sehingga bisa dinikmati oleh berbagai kalangan.",
        f"Diklasifikasikan sebagai {cert_txt}, film ini cocok untuk beragam penonton.",
        f"Tingkat sensor {cert_txt}, membantu penonton memilih waktu terbaik untuk menonton."
    )

    # 6. 임팩트 설명
    s6 = choose(
        f"<b>{title}</b> memicu diskusi dan ekspektasi sejak dirilis, menunjukkan kekuatan budayanya.",
        f"Sejak pemutaran perdananya, <b>{title}</b> menarik perhatian berkat kualitas dan keberaniannya.",
        f"Dampak dari <b>{title}</b> terasa langsung, menjadikannya salah satu sorotan besar di {year_txt}.",
        f"Tidak hanya sebuah film, <b>{title}</b> adalah pengalaman yang terus hidup dalam ingatan penontonnya."
    )

    # 7. 도입부 마무리
    s7 = choose(
        f"Sekarang, mari kita jelajahi bersama poin-poin utama dari <b>{title}</b> dan pahami mengapa film ini layak ditonton.",
        f"Pada bagian berikut, Anda akan mengenal lebih jauh tentang sinopsis, pemeran, serta kekuatan utama <b>{title}</b>.",
        f"Bersiaplah menyelami dunia <b>{title}</b>, dengan detail yang membuatnya begitu relevan.",
        f"Ayo kita lanjutkan dan temukan apa yang menjadikan <b>{title}</b> begitu populer dan diperbincangkan."
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])


# ===============================
# 🎬 아웃트로 (7문장)
# ===============================
def make_outro_6(title, year, genres_str, director_names, keywords):
    year_txt = year if year else "tidak diketahui"
    director_one = director_names[0] if director_names else ""

    # 1. 마무리 인사 및 전체 리뷰 종료
    s1 = choose(
        f"Kita sampai di akhir pembahasan tentang film <b>{title}</b> ({year_txt}), yang menghadirkan banyak hal menarik untuk direnungkan.",
        f"Demikianlah ulasan tentang <b>{title}</b> ({year_txt}), sebuah karya yang pantas ada di radar setiap pecinta film.",
        f"Perjalanan kita menjelajahi dunia <b>{title}</b> ({year_txt}) berakhir di sini, menyoroti aspek-aspek yang membuatnya begitu diperbincangkan.",
        f"Inilah penelusuran tentang <b>{title}</b> ({year_txt}), membahas elemen-elemen yang menjadikannya sebuah film berkesan."
    )

    # 2. 글 전체 요약
    s2 = choose(
        "Sepanjang artikel ini, kita membahas sinopsis, para pemeran, serta aspek teknis dan artistik utama.",
        "Dalam ulasan ini, kita menelusuri cerita, membicarakan aktor, dan menyoroti bagian penting yang membuat film ini menarik.",
        "Kita sudah membahas alur cerita, penyutradaraan, dan dampak budaya yang dibawa film ini kepada penonton.",
        f"Kita juga menyinggung narasi, suasana, serta karakter-karakter yang menjadikan <b>{title}</b> pengalaman istimewa."
    )

    # 3. 감독에 대한 언급
    s3 = (
        choose(
            f"Arah dari {director_one} menjadi salah satu kekuatan utama, menunjukkan kreativitas dan kepekaan dalam setiap adegan.",
            f"{director_one} berhasil memberikan sentuhan pribadi pada film ini, menyeimbangkan emosi dan teknik dengan cara unik.",
            f"Pandangan artistik {director_one} menunjukkan bagaimana penyutradaraan dapat mengubah cerita menjadi sesuatu yang megah.",
            f"Tidak bisa dipungkiri, visi {director_one} menjadikan karya ini begitu berkesan."
        ) if director_one else choose(
            "Penyutradaraan secara umum seimbang dan jelas, menjaga ritme serta kekuatan naratif hingga akhir.",
            "Meski tanpa nama besar di kursi sutradara, penggarapannya tetap solid dan terstruktur dengan baik.",
            "Cara penyutradaraan membuat penonton tetap terhubung dan tertarik sampai akhir cerita.",
            "Penyutradaraan menunjukkan kedewasaan dan penguasaan teknis, meningkatkan kualitas film ini."
        )
    )

    # 4. 평점과 비평에 대한 언급
    s4 = choose(
        "Nilai dan ulasan hanyalah panduan, pengalaman nyata datang dari menonton dan merasakan setiap adegan.",
        "Angka dan kritik memang penting, tetapi tidak bisa menggantikan emosi pribadi saat terhubung dengan cerita.",
        "Perlu diingat, pendapat bisa berbeda-beda, yang terbaik adalah menonton sendiri dan menarik kesimpulan.",
        "Skor hanyalah acuan: dampak sesungguhnya tergantung pada setiap penonton."
    )

    # 5. 추천 영화 안내
    s5 = choose(
        "Di akhir ulasan ini, kami juga memberikan rekomendasi film terkait untuk memperkaya pengalaman menonton Anda.",
        "Bagi yang menyukai film ini, kami sarankan beberapa judul serupa yang bisa menambah wawasan baru.",
        "Kami juga merekomendasikan karya lain yang sejalan dengan film ini, memberi kesempatan untuk perbandingan menarik.",
        "Untuk tetap dalam suasana yang sama, ada beberapa pilihan film lain dengan tema serupa."
    )

    # 6. 키워드 강조
    kw = ", ".join([k for k in (keywords or []) if k][:6]) if keywords else ""
    s6 = choose(
        f"Beberapa kata kunci penting yang dapat dicatat antara lain {kw}, membantu memahami cakupan film ini dengan lebih baik.",
        f"Kata kunci {kw} merangkum elemen inti film ini dan bisa menjadi panduan untuk eksplorasi lebih lanjut.",
        f"Kami menyoroti istilah seperti {kw}, yang mempertegas pentingnya film ini dalam genrenya.",
        f"Konsep {kw} sering muncul, menunjukkan bagaimana film ini menempatkan diri dalam dunia perfilman."
    ) if kw else "Semoga informasi di atas bisa menjadi panduan yang bermanfaat untuk sesi menonton Anda berikutnya."

    # 7. 최종 마무리 인사
    s7 = choose(
        "Terima kasih sudah mengikuti ulasan ini sampai akhir, semoga bisa menginspirasi sesi menonton Anda berikutnya. 🙂",
        "Kami berterima kasih atas waktu Anda membaca, semoga pengalaman menonton film Anda semakin menyenangkan. Sampai jumpa!",
        "Jika Anda menyukai artikel ini, silakan bagikan kepada teman dan ikuti ulasan film menarik lainnya di sini.",
        "Senang bisa menghadirkan ulasan ini untuk Anda, nantikan segera judul baru dan rekomendasi menarik lainnya."
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])




# ===============================
# 📌 섹션 리드 (각 섹션마다 4문장)
# ===============================
def make_section_lead(name, title, year, genres_str, cert_label, extras=None):
    """각 섹션을 소개하는 4문장 도입부 (친근하고 풍부한 톤, 다양한 조합)"""
    extras = extras or {}
    year_txt = f"{year}" if year else ""
    genre_phrase = genres_str if genres_str else "genre tidak diketahui"
    cert_txt = cert_label or "klasifikasi tidak diketahui"
    cast_top = extras.get("cast_top", [])
    who = "·".join(cast_top[:3]) if cast_top else ""
    director_one = extras.get("director_one", "")
    runtime_min = extras.get("runtime_min", None)
    runtime_txt = f"{runtime_min} menit" if runtime_min else ""

    # 🎞️ 줄거리 섹션
    if name == "줄거리":
        base = [
            choose(
                f"Saya akan memperkenalkan alur cerita <b>{title}</b>{f' ({year_txt})' if year_txt else ''} secara ringan, tanpa banyak spoiler.",
                f"Bagi yang belum menonton, saya akan menyusun ringkasan cerita utama <b>{title}</b> dengan jelas.",
                f"Alur cerita <b>{title}</b> akan dijelaskan secara singkat, hanya menyoroti garis besar kisahnya.",
                f"Tanpa mengurangi keseruan, mari kita ikuti struktur cerita <b>{title}</b> bersama-sama.",
                f"Saya akan menjelaskan dengan sederhana agar siapa pun bisa memahami jalan cerita <b>{title}</b>.",
                f"Tanpa membuka banyak rahasia, saya akan menyoroti peristiwa utama dalam <b>{title}</b>.",
                f"Agar cepat dipahami, mari kita lihat sedikit ringkasan alur <b>{title}</b>.",
                f"Saya akan menyusun alur cerita langkah demi langkah untuk memberi gambaran sebelum menonton.",
                f"Dengan cara seimbang namun tetap membangkitkan rasa penasaran, saya akan berbagi kisah <b>{title}</b>.",
                f"Tanpa merusak kejutan, mari kita ikuti alur utama dari <b>{title}</b>."
            ),
            choose(
                f"Awalnya cerita berjalan tenang, di tengah {choose('konflik mulai memanas', 'ketegangan meningkat', 'hubungan menjadi rumit')} dan pada akhirnya {choose('emosi mencapai puncak', 'potongan cerita terhubung', 'pesan cerita semakin jelas')}.",
                f"{choose('Adegan pembuka berjalan sederhana', 'Sejak awal sudah terasa tegang', 'Pengantar terasa damai')}, lalu {choose('tokoh-tokoh mulai beraksi', 'rahasia mulai terungkap', 'konflik semakin jelas')}, membuat keterlibatan semakin besar.",
                f"Struktur cerita mengikuti pola {choose('pengenalan→konflik→resolusi', 'awal→krisis→pertumbuhan', 'pertemuan→konflik→pilihan')}, dengan setiap bagian memiliki sorotan tersendiri.",
                f"Memasuki pertengahan, ritme cerita semakin cepat dan ketegangan meningkat.",
                f"Pada akhirnya, petunjuk yang ditabur sejak awal mulai terungkap dan keseruan bertambah."
            ),
            choose(
                f"Nuansa khas {genre_phrase} terasa menyatu dengan perkembangan cerita, menjaga nada {choose('seimbang', 'natural', 'tenang')}.",
                f"Tanpa banyak penjelasan, adegan-adegan saja sudah cukup membuat penonton larut.",
                f"Plot twist besar biar jadi kejutan Anda, tapi saya akan memberikan sedikit gambaran suasana.",
                f"Narasinya tidak berlebihan sehingga mudah diikuti secara alami.",
                f"Jenis film seperti ini lebih meyakinkan lewat ritme dan arahan daripada dialog, memberikan pengalaman berbeda."
            ),
            choose(
                f"Klasifikasi usia adalah {cert_txt}, sehingga {choose('cocok ditonton bersama keluarga.', 'asik ditonton bersama teman.', 'juga bisa dinikmati sendirian dengan fokus penuh.')}",
                f"Dengan klasifikasi {cert_txt}, Anda bisa menontonnya dengan santai sambil menikmati suasananya.",
                f"Klasifikasinya {cert_txt}, tetapi silakan nikmati sesuai selera masing-masing.",
                f"Meskipun klasifikasi {cert_txt}, tema film ini bisa dirasakan oleh siapa saja."
            )
        ]
        if maybe(True, 0.4):
            base.append(
                choose(
                    "Selanjutnya saya akan menyusunnya lebih detail.",
                    "Mari kita ikuti adegan utama dan garis emosionalnya.",
                    "Kita sudah paham struktur umumnya, sekarang melihat detailnya akan lebih seru."
                )
            )

    # 🎭 출연진 섹션
    elif name == "출연진":
        base = [
            choose(
                f"Kali ini jajaran pemain mencakup {who} {('dan lainnya' if who else '')}, hanya dengan mendengar namanya saja sudah jelas mengapa film ini ramai dibicarakan.",
                f"Line-up aktor langsung menarik perhatian sejak awal{': ' + who if who else ''}. Kehadiran mereka terasa kuat.",
                f"Sejak kredit awal, wajah-wajah terkenal muncul{(' — ' + who) if who else ''}.",
                f"Berkat {who} {('dan lainnya' if who else '')}, penonton bisa menaruh kepercayaan penuh pada film ini." if who else "Hanya dengan melihat jajaran aktor saja sudah menambah ekspektasi.",
                f"Aktor-aktor ternama berkumpul, memberi energi besar pada karya ini.",
                f"Melihat daftar aktor saja sudah membuat film ini terasa ‘wajib tonton’.",
                f"Formasi pemain menunjukkan jelas mengapa tim produksi begitu percaya diri.",
                f"Setiap aktor hadir dengan penampilan menonjol.",
                f"Cukup dengan nama-nama utama saja sudah membuat orang ingin bertepuk tangan.",
                f"Fakta bahwa film ini melibatkan aktor terpercaya membuat penonton semakin bersemangat."
            ),
            choose(
                f"Keseimbangan antara peran utama dan pendukung, {choose('keserasian ekspresi', 'sinkronisasi dialog', 'kekompakan akting')} membuat karakter terasa hidup.",
                f"{choose('Tatapan dan gestur', 'Timing reaksi', 'Ritme dialog')} memperkuat adegan secara natural tanpa berlebihan.",
                f"Chemistry antar aktor membuat alur emosional {choose('mengalir alami', 'tumbuh konsisten', 'semakin meningkat')} dan bersinar di puncaknya.",
                f"Tonalitas akting konsisten sehingga mudah untuk terhanyut.",
                f"Penyampaian dialog terasa natural dan meyakinkan, tanpa berlebihan.",
                f"Keseimbangan antara peran utama dan pendukung membuat karakter terasa nyata.",
                f"Tonalitas stabil dari akting membuat penonton nyaman larut dalam cerita.",
                f"Kekompakan para pemain menambah intensitas setiap adegan.",
                f"Ritme antara dialog dan emosi terasa menyatu sempurna.",
                f"Hampir tidak ada kesan artifisial, membuat cerita semakin realistis."
            ),
            choose(
                f"Terutama {choose('kontras antar karakter', 'perbedaan generasi', 'benturan nilai')} membawa chemistry yang menarik.",
                f"{choose('Kerja sama antar tokoh', 'Latihan bersama', 'Kolaborasi tim')} berjalan sangat baik, membuat adegan makin seru.",
                f"Bahkan penampilan singkat bisa jadi sorotan, jadi perhatikan baik-baik.",
                f"Kekuatan peran pendukung semakin memperkaya cerita.",
                f"Sinergi para pemain terlihat jelas di setiap adegan.",
                f"Kombinasi tak terduga menciptakan ketegangan menarik.",
                f"Kontras antar karakter memperjelas tema utama.",
                f"Bahkan peran kecil pun berfungsi baik tanpa terasa kosong.",
                f"Bahkan figuran memberikan kesan yang berbekas.",
                f"Beberapa aktor mampu meninggalkan jejak hanya dengan satu adegan."
            ),
            choose(
                "Selanjutnya, saya akan memperkenalkan peran utama satu per satu.",
                "Sekarang mari kita lihat karakter yang dimainkan oleh tiap aktor.",
                "Berikutnya saya akan menyajikan informasi pemain secara rinci.",
                "Cek langsung siapa yang memerankan tokoh utama dalam film ini.",
                "Mari kita lihat lebih detail daftar pemerannya.",
                "Saya akan merangkum peran dan ciri setiap aktor secara singkat.",
                "Aktor dan karakter yang diperankan akan saya jelaskan satu per satu.",
                "Berikut gambaran umum pemeran dan karakter dalam film ini.",
                "Lihat daftar di bawah untuk informasi pemeran dan perannya.",
                "Mari kita lihat bagaimana setiap aktor memberi warna pada karakternya."
            )
        ]

    # 🖼️ 스틸컷 섹션
    elif name == "스틸컷":
        base = [
            choose(
                "Hanya dengan melihat stills saja, suasana film sudah terasa.",
                "Beberapa gambar saja sudah cukup menunjukkan nuansa karya ini.",
                "Foto-foto singkat bisa langsung memperlihatkan warna dan nada film.",
                "Begitu melihat stills, Anda langsung paham arah produksi ini.",
                "Cukup satu-dua foto untuk merasakan mood film ini.",
                "Meski singkat, stills sudah bisa menampilkan inti emosional cerita.",
                "Dengan sedikit adegan saja, atmosfer sudah terasa jelas.",
                "Stills bisa dianggap sebagai kesan pertama dari film ini.",
                "Bahkan dari potongan singkat, suasana film sudah terlihat nyata.",
                "Beberapa gambar saja sudah bisa membangkitkan imajinasi tentang jalan cerita."
            ),
            choose(
                f"{choose('Komposisi adegan', 'Sudut kamera', 'Penggunaan ruang')} terasa seimbang dan enak dipandang.",
                f"{choose('Palet warna', 'Pencahayaan', 'Kontras')} dibuat {choose('elegan', 'halus', 'kuat')}, membuat adegan lebih berkesan.",
                f"Desain produksi {choose('pas dengan situasi', 'tanpa berlebihan', 'selaras dengan emosi')}, menambah kedalaman gambar.",
                f"Komposisi visual terasa seimbang, menjadikan tampilan menarik.",
                f"Cara pengolahan cahaya dan warna begitu menawan.",
                f"Hingga detail kecil pun terlihat diperhatikan oleh produksi.",
                f"Harmoni antara komposisi dan warna membuat adegan seperti lukisan.",
                f"Pergerakan kamera juga terasa dalam stills yang ditampilkan.",
                f"Warna memainkan peran penting dalam menentukan suasana.",
                f"Arah artistik menyampaikan mood film dengan jelas."
            ),
            choose(
                "Hanya dengan potongan gambar, jalur emosional sudah terasa.",
                "Bahkan dalam gambar statis, emosi karakter bisa dirasakan.",
                "Stills membangkitkan rasa penasaran akan adegan berikutnya.",
                "Seolah cerita terus berjalan hanya dari foto yang ditangkap.",
                "Meskipun diam, gambar membawa ketegangan.",
                "Momen singkat yang terekam memberi kesan panjang.",
                "Ada banyak detail yang hanya terlihat pada stills.",
                "Beberapa foto membantu menyusun puzzle cerita.",
                "Ekspresi tokoh dalam stills sudah menceritakan banyak hal.",
                "Bahkan satu adegan singkat bisa mewakili keseluruhan mood film."
            ),
            choose(
                "Lihat gambar-gambar berikut untuk merasakan suasana film ini.",
                "Melihat stills lebih dulu meningkatkan imersi saat menonton.",
                "Nikmati foto-foto ini untuk merasakan daya tarik film sebelum menonton.",
                "Setelah melihat gambar, detail dalam film akan lebih mudah dikenali.",
                "Layak dilihat untuk menemukan titik penting dari karya ini.",
                "Stills berfungsi seperti trailer kecil di dalam film.",
                "Melihat foto lebih dulu mempersiapkan Anda masuk ke dalam cerita.",
                "Saat mengenali adegan ini, pengalaman menonton jadi lebih menyenangkan.",
                "Menangkap suasana lewat gambar membuat pengalaman lebih kaya.",
                "Lihat stills ini dan tentukan adegan mana yang paling Anda tunggu."
            )
        ]

    return base



    # 🎯 평점 및 인기 섹션
    elif name == "평점 및 인기":
        base = [
            choose(
                f"Penilaian untuk <b>{title}</b> menjadi indikator jelas dari reaksi penonton.",
                f"Hanya dengan melihat nilai, kita bisa tahu gambaran penerimaan film ini.",
                f"Skor adalah cara cepat untuk memahami kesan pertama dari sebuah karya.",
                f"Angka penilaian mencerminkan perasaan jujur para penonton.",
                f"Rating <b>{title}</b> menunjukkan tingkat popularitas dan relevansinya.",
                f"Nilai juga berfungsi sebagai termometer seberapa dicintainya film ini.",
                f"Skor membantu mengukur ekspektasi serta kepuasan penonton.",
                f"Rating adalah cara paling sederhana untuk melihat performa film.",
                f"Skor <b>{title}</b> memperlihatkan antusiasme publik secara tidak langsung.",
                f"Dengan penilaian yang terlihat, kita bisa merasakan pentingnya film ini."
            ),
            choose(
                "Jumlah suara dan rata-rata memberi arti lebih dari sekadar angka statistik.",
                "Semakin banyak suara, semakin dapat dipercaya penilaiannya.",
                "Melihat rata-rata bersama jumlah suara membuat hasil lebih akurat.",
                "Ketika jumlah suara meningkat, terlihat jelas pengakuan publik.",
                "Banyaknya suara berarti film ini benar-benar ramai dibicarakan.",
                "Jumlah penilaian yang besar menunjukkan tingginya minat penonton.",
                "Bukan hanya rata-rata, tapi juga volume suara yang penting.",
                "Total suara memberi gambaran seberapa luas karya ini dikenal.",
                "Rata-rata bersama partisipasi penonton memberi posisi yang lebih jelas.",
                "Data penilaian menyimpan makna lebih dari sekadar angka."
            ),
            choose(
                "Tentu saja, angka bukan segalanya. Menonton langsung tetap yang paling tepat.",
                "Nilai tinggi tidak selalu menjamin keseruan, dan nilai rendah tidak selalu berarti membosankan.",
                "Gunakan skor hanya sebagai referensi: selera pribadi jauh lebih penting.",
                "Penilaian hanyalah panduan, keputusan akhir tetap ada di tangan Anda.",
                "Meski rating tinggi, mungkin saja tidak sesuai dengan gaya Anda.",
                "Rating rendah bisa saja menyembunyikan film yang berkesan untuk Anda.",
                "Pada akhirnya, yang terpenting adalah pengalaman Anda sendiri saat menonton.",
                "Anggap skor sebagai acuan ringan saja, jangan terlalu dipikirkan.",
                "Skor mencerminkan suara publik, tapi opini pribadi Anda yang utama.",
                "Gunakan rating hanya sebagai panduan singkat."
            ),
            choose(
                "Lihat tabel di bawah hanya sebagai referensi ringan.",
                "Anggap data sebagai panduan, lalu ikuti intuisi Anda.",
                "Cek tabel dan rasakan gambaran umum reaksi penonton.",
                "Selain angka, melihat reaksi nyata juga menyenangkan.",
                "Gunakan data sebagai acuan, tapi temukan jawaban sendiri dengan menonton.",
                "Lebih menarik dari rata-rata adalah melihat distribusi dan suasananya.",
                "Jangan hanya melihat angka, bacalah juga ulasan untuk pemahaman lebih baik.",
                "Tabel di bawah merangkum reaksi penonton, simak dengan ringan.",
                "Pada akhirnya, pilihan tetap milik Anda, angka hanyalah petunjuk.",
                "Lihat tabel ini dan tangkap suasana umum dari reaksi penonton."
            )
        ]
    
    # 🌟 베스트 리뷰 섹션
    elif name == "베스트 리뷰":
        base = [
            choose(
                "Meski singkat, ulasan penonton membawa emosi yang nyata.",
                "Cukup membaca satu baris review, sudah terasa suasana aslinya.",
                "Melihat komentar publik, kita bisa paham bagaimana film diterima.",
                "Ulasan singkat dan padat bisa menunjukkan pesona film dengan jelas.",
                "Review adalah suara paling jujur dari penonton, lebih dari angka.",
                "Bahkan komentar pendek menyampaikan perasaan tulus penonton.",
                "Dalam satu-dua kalimat sering kali tersimpan inti dari film.",
                "Kesan jujur penonton lebih kuat daripada data statistik.",
                "Ini adalah kata-kata dari mereka yang benar-benar menonton, sehingga lebih dipercaya.",
                "Review menghadirkan rasa kehadiran nyata yang menyenangkan untuk dibaca."
            ),
            choose(
                "Tergantung selera, opini bisa berbeda — inilah keindahan sinema.",
                "Pujian atau kritik, semua adalah interpretasi yang sah dari karya.",
                "Gabungan reaksi positif dan negatif memberi gambaran lengkap.",
                "Semakin beragam opininya, semakin luas dimensi film tersebut.",
                "Adanya pujian dan kritik sekaligus membuktikan film ramai diperbincangkan.",
                "Pandangan berbeda menyingkap banyak lapisan sinema.",
                "Bahkan pada adegan sama, tafsiran bisa berbeda — dan itu menarik.",
                "Setuju atau tidak, keragaman inilah yang membuat film istimewa.",
                "Bagi sebagian orang film ini masterpiece, bagi yang lain biasa saja — variasi itu berharga.",
                "Seperti selera yang beragam, ulasan juga hadir dengan perbedaan alami."
            ),
            choose(
                "Berikut saya kumpulkan beberapa review yang menarik.",
                "Saya pilih komentar singkat dengan menghindari spoiler sebisa mungkin.",
                "Ulasan representatif ini memberi gambaran baik tentang kesan film.",
                "Kumpulan review pendek ini membuat membaca jadi menyenangkan.",
                "Komentar yang dipilih dirangkum agar mudah untuk dicek.",
                "Saya siapkan beberapa ulasan sebagai referensi.",
                "Kalimat singkat saja sudah cukup menyampaikan suasana film.",
                "Karena ringkas, review ini mudah untuk diikuti.",
                "Saya pisahkan komentar paling berkesan untuk ditampilkan.",
                "Komentar singkat ini memperlihatkan sisi lain dari film."
            ),
            choose(
                "Saat membaca, Anda akan tahu bagian mana yang paling Anda sukai.",
                "Jika menemukan kalimat yang menyentuh, coba ingat lagi setelah menonton.",
                "Ketika sebuah review sesuai dengan emosi Anda, tercipta empati unik.",
                "Menarik rasanya meninjau kembali film lewat pandangan orang lain.",
                "Membaca ulasan membantu Anda mengantisipasi poin penting.",
                "Bahkan dari komentar singkat, kita bisa berpikir: ‘Oh, jadi itu rasanya’.",
                "Review kadang menyingkap pesona tersembunyi film.",
                "Ada kalanya komentar penonton justru menunjukkan sisi rahasia karya.",
                "Dengan membaca opini beragam, perspektif pribadi bisa lebih dalam.",
                "Saat kata-kata review cocok dengan selera Anda, rasanya menyenangkan."
            )
        ]
    
    # 🎥 예고편 섹션
    elif name == "예고편":
        base = [
            choose(
                "Trailer adalah cara tercepat untuk merasakan nada dan atmosfer film.",
                "Dalam hitungan detik, trailer sudah menunjukkan mood utama karya ini.",
                "Hanya dengan menonton trailer, Anda bisa menangkap esensi film.",
                "Pendek tapi intens, trailer meningkatkan rasa penasaran terhadap film.",
                "Trailer berfungsi sebagai jendela untuk mengintip nuansa sebelum menonton.",
                "Seperti kartu nama, trailer memberi kesan pertama tentang film.",
                "Bahkan dalam beberapa detik, trailer menyampaikan seluruh pesonanya.",
                "Cuplikan cepat sudah cukup menggambarkan suasana keseluruhan.",
                "Trailer adalah alat yang membangkitkan minat sebelum menonton.",
                "Hanya dari trailer, sudah bisa merasakan sebagian besar magis film."
            ),
            choose(
                "Tanpa takut spoiler, Anda bisa cek hanya suasananya saja.",
                "Trailer sedikit meredakan ketegangan tapi menyisakan rasa penasaran.",
                "Dalam waktu singkat, trailer sudah menampilkan ritme dan emosi film.",
                "Hanya dengan potongan adegan dan musik, imersi sudah terasa.",
                "Setelah melihat trailer, keinginan menonton film semakin besar.",
                "Musik dan editing menunjukkan identitas film dengan jelas.",
                "Tempo dan irama trailer memberi gambaran energi film.",
                "Meski singkat, trailer memberi petunjuk tentang jalan cerita.",
                "Suara dan gambar berpadu membuat penonton berkata: ‘Ya, ini gayanya’.",
                "Dalam detik singkat, sudah ada adegan-adegan berkesan."
            ),
            choose(
                f"Dengan menonton trailer {runtime_txt+' penuh ' if runtime_txt else ''}Anda cepat menangkap nada film.",
                "Kadang satu dialog dalam trailer sudah cukup mewakili keseluruhan film.",
                "Adegan pertama dan terakhir trailer bisa menyimpan petunjuk penting.",
                "Meski singkat, cuplikan video membawa pesan yang kuat.",
                "Hanya lewat trailer, sudah terasa garis emosional yang akan diikuti.",
                "Visual indah dan musik latar cukup untuk menyampaikan pesona film.",
                "Satu adegan dalam trailer bisa jadi alasan utama untuk menonton.",
                "Walau singkat, trailer punya kekuatan menciptakan imersi.",
                "Meskipun hanya teaser, kesannya bisa bertahan lama.",
                "Beberapa adegan sudah cukup memperlihatkan tema utama film."
            ),
            choose(
                "Kalau bisa, coba tonton sekali dengan headset dan sekali dengan speaker, rasanya berbeda.",
                "Aktifkan subtitle agar bisa menangkap nada dan nuansa dialog lebih baik.",
                "Kalau benar-benar larut, bahkan trailer singkat jadi lebih berkesan.",
                "Dalam 10 detik awal dan akhir sering tersembunyi inti film.",
                "Menonton ulang trailer beberapa kali bisa membuka detail tersembunyi.",
                "Ketika menemukan adegan trailer saat menonton film, rasanya memuaskan.",
                "Lebih baik menonton fokus daripada sekadar sekilas.",
                "Meski sebentar, ada banyak detail produksi yang bisa diperhatikan.",
                "Kombinasi gambar dan suara kadang sama kuatnya dengan film penuh.",
                "Meski singkat, trailer memperlihatkan mood film secara jujur."
            ),
            choose(
                "Tonton video di bawah dan jika tertarik, lanjutkan ke film lengkapnya.",
                "Trailer adalah hidangan pembuka sekaligus undangan untuk film utama.",
                "Kalau cuplikan singkat ini sudah menyentuh Anda, filmnya pasti lebih seru.",
                "Tidak ada cara lebih baik untuk tahu cocok atau tidak selain dari trailer.",
                "Setelah melihat trailer, memilih film jadi lebih mudah.",
                "Satu video saja cukup untuk tahu apakah sesuai gaya Anda.",
                "Trailer adalah alat terbaik untuk membangun ekspektasi penonton.",
                "Untuk perkenalan pertama dengan film, trailer adalah pemandu terbaik.",
                "Kalau trailer saja sudah emosional, filmnya pasti lebih berkesan.",
                "Rasakan sedikit pesona film lewat trailer berikut."
            )
        ]

    
        


    # 🎥 추천 영화 섹션
    elif name == "추천 영화":
        base = [
            choose(
                f"Kalau kamu sudah menonton <b>{title}</b>, ada baiknya juga cek film-film di bawah ini.",
                f"Jika kamu menyukai <b>{title}</b>, saya kumpulkan beberapa karya dengan suasana mirip.",
                f"Saya siapkan rekomendasi yang cocok dengan <b>{title}</b>, ditampilkan lewat poster.",
                f"Film dengan nuansa serupa saya rangkum dalam gambar poster untuk kamu lihat.",
                f"Coba perhatikan poster di bawah, siapa tahu ada yang sesuai dengan selera kamu.",
                f"Kenali film lain yang berhubungan dengan <b>{title}</b> lewat kumpulan poster ini."
            ),
            choose(
                "Rekomendasi kali ini hanya berupa judul dan poster.",
                "Tanpa deskripsi panjang, saya tampilkan hanya gambar singkat.",
                "Cukup gulir layar dan lihat secara ringan.",
                "Poster singkat saya pisahkan agar mudah dilihat tanpa repot.",
                "Tidak ada penjelasan detail — hanya visual poster yang intuitif."
            ),
            choose(
                "Kalau ada poster yang menarik perhatianmu, simpan sebagai referensi.",
                "Bisa langsung tambahkan ke daftar tontonan kalau ada yang cocok.",
                "Hanya dari poster, sudah bisa terasa suasana filmnya.",
                "Membandingkan semua sekaligus membuat pilihan jadi lebih seru.",
                "Dari gambar-gambar ini, pilih yang akan jadi ‘film hari ini’."
            ),
            choose(
                "Yuk, kita lihat bersama poster-poster rekomendasi di bawah.",
                "Perhatikan gambar-gambar berikut dan pilih film yang cocok untukmu.",
                "Bahkan hanya dari poster sudah terlihat pesonanya.",
                "Langsung saja cek film yang ada di bawah ini.",
                "Berikut beberapa rekomendasi ringan dan seru untuk kamu nikmati."
            )
        ]
    
    # 📌 기본 안내 (기타 섹션 처리)
    else:
        base = [
            choose(
                "Saya rangkum hanya poin-poin penting agar bisa kamu lihat cepat dan tandai sesuai kebutuhan.",
                "Struktur sudah saya susun jelas, cukup gulir perlahan untuk mengikutinya.",
                "Saya pisahkan bagian terpenting — kamu bisa baca hanya yang menarik."
            ),
            choose(
                "Bagian-bagian artikel diatur urutannya secara intuitif, masing-masing dengan catatan singkat.",
                "Bacaan mengalir alami antara cerita, informasi, dan ulasan.",
                "Kalau mau, bisa tandai sebagai favorit dan baca ulang dengan tenang nanti."
            ),
            choose(
                "Saya tambahkan juga beberapa tips pribadi di tengah konten.",
                "Saya kurangi bagian berlebihan dan fokus ke saran praktis.",
                "Teks dibuat ringan dan enak dibaca, tidak terlalu panjang."
            ),
            choose(
                "Sekarang mari langsung masuk ke isi utama di bawah ini.",
                "Nah, sekarang kita masuk ke bagian inti pembahasan."
            )
        ]
    
    return " ".join(base)




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

    # HTML 박스 생성 (스타일 유지, 텍스트 인도네시아어로 변경)
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ Rekomendasi Postingan</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box


def build_html(post, title, cast_count=10, stills_count=8):
    esc = html.escape
    
    overview = esc(post.get("overview") or "Informasi sinopsis belum tersedia.")
    release_date = esc(post.get("release_date") or "")
    year = release_date[:4] if release_date else ""
    runtime = post.get("runtime") or 0
    genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
    genres_str = ", ".join(genres_list)
    tagline = esc(post.get("tagline") or "")
    adult_flag = bool(post.get("adult", False))

    # 제작 국가
    countries = [c.get("name","") for c in post.get("production_countries",[]) if c.get("name")]
    country_str = ", ".join(countries) if countries else "Tidak ada informasi negara"

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
        cert = "Konten dewasa"

    # 키워드 생성
    base_keywords = []
    for w in (title.replace(":", " ").replace("-", " ").split()):
        if len(w) > 1:
            base_keywords.append(str(w))
    base_keywords += genres_list + director_names[:2] + cast_names[:3]
    if year: base_keywords.append(str(year))
    if cert: base_keywords.append(str(cert))

    base_keywords += ["Ulasan", "Penilaian", "Pemeran", "Trailer", "Stills", "Rekomendasi Film"]

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
        "".join(cast_rows or ['<tr><td style="padding:10px;">Tidak ada informasi pemeran.</td></tr>']) +
        '</table>'
    )

    # 스틸컷
    still_divs = []
    for b in backdrops:
        p = img_url(b.get("file_path"), "w780")
        if not p: continue
        still_divs.append(
            f'<div style="flex:0 0 49%;margin:0.5%;"><img src="{p}" alt="Still dari {title}" style="width:100%;height:auto;border-radius:10px;"></div>'
        )
    stills_html = (
        '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;">' +
        "".join(still_divs or ['<div style="padding:10px;">Tidak ada gambar still tersedia.</div>']) +
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
        ⭐ Penilaian & 📊 Popularitas
    </div>
    <div style="font-size:18px;color:#222;margin:8px 0;">
        <b style="color:#ff9800;">Nilai rata-rata:</b> {vote_avg:.1f}/10
    </div>
    <div style="font-size:16px;color:#555;margin:6px 0;">
        Jumlah suara: {vote_count:,}
    </div>
    <div style="font-size:18px;color:#0066cc;margin-top:10px;">
        <b>Popularitas:</b> {popularity:.1f}
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
        video_html += "<br /><p>⚠️ Trailer di bawah ini mungkin bukan yang resmi.</p>"
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
        reviews_html = "<br /><br /><br />\n<h2>Ulasan terbaik untuk "+title+"</h2>" + "".join(review_blocks)

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
            "<br /><br /><br />\n<h2>Rekomendasi Film</h2>"
            f"<p>{rec_lead}</p>"
            "<div style='display:flex;flex-wrap:wrap;'>"
            + "".join(cards) +
            "</div>"
        )

    outro_6 = make_outro_6(title, year, genres_str, director_names, keywords)
    related_box = get_related_posts(BLOG_ID, count=4)

    blog_title1 = f"Film {title} ({year}) Sinopsis Pemeran Trailer"
    hashtags = make_hashtags_from_title(blog_title1)

    html_out = f"""
<p>{intro_6}</p>
<!--more--><br />
{"<p><img src='"+backdrop+"' style='width:100%;border-radius:12px;'></p>" if backdrop else ""}
{"<p><i>"+html.escape(tagline)+"</i></p>" if tagline else ""}

<br /><br /><br />
<h2>Film {title} – Sinopsis</h2>
<p><b>Negara:</b> {country_str} | <b>Genre:</b> {genres_str if genres_str else "Tidak ada informasi"}</p>
<p>{make_section_lead("줄거리", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://cineid.appsos.kr/search/label/{year}?&max-results=10" target="_blank">
    <span style="font-size: medium;"><strong>Rekomendasi film dari tahun {year}</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if year else ''}

<div style="background:#fafafa;border:2px solid #ddd;border-radius:12px;padding:10px 18px;">
  <p style="font-weight:bold;">🎬 Sinopsis {title}</p>
  {overview}
</div>
<br />{hashtags}

<br /><br /><br />
<h2>Pemeran {title}</h2>
<p>{make_section_lead("출연진", title, year, genres_str, cert, extras={"cast_top": cast_names})}</p>
{cast_table}
<br />{hashtags}

<br /><br /><br />
<h2>Stills dari {title}</h2>
<p>{make_section_lead("스틸컷", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://cineid.appsos.kr/search/label/{urllib.parse.quote(genres_list[0])}?&max-results=10" target="_blank">
    <span style="font-size: medium;"><strong>Rekomendasi film bergenre {genres_list[0]}</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if genres_list else ''}

{stills_html}
<br />{hashtags}

<br /><br /><br />
<h2>Penilaian dan Trailer</h2>
<p>{rating_lead}</p>
{rating_html}{video_html}
{reviews_html}{rec_html}
<br />{hashtags}

<p>{outro_6}</p>
{related_box}
<p style="font-size:12px;">Sumber: <a href="https://www.themoviedb.org/" target="_blank">TMDB</a></p>

"""

    return textwrap.dedent(html_out).strip()


# ===============================
# Blogger 인증/발행
# ===============================
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

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

    ws = get_sheet()
    service = get_blogger_service()

    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # 2행부터
        raw_id = row[1].strip() if len(row) > 1 else ""  # 원본 값
        movie_id = re.sub(r"\D", "", raw_id)            # 숫자만 추출
        done_flag = row[5].strip() if len(row) > 5 else ""

        if not movie_id:
            print(f"⚠️ MOVIE_ID tidak valid: {raw_id} (baris {i}) → dilewati")
            continue

        if movie_id and done_flag != "완":
            print(f"👉 Target baris: {i} (MOVIE_ID={movie_id})")

            try:
                # 1) TMDB 상세 번들 수집
                post = get_movie_bundle(movie_id, lang=LANG, bearer=BEARER, api_key=API_KEY)

                # 2) 제목 가져오기
                title = get_movie_title(movie_id, bearer=BEARER, api_key=API_KEY)
                year = (post.get("release_date") or "")[:4]

                # 3) HTML 구성
                html_out = build_html(post, title, cast_count=CAST_COUNT, stills_count=STILLS_COUNT)

                # 4) 블로그 제목 생성
                blog_title = get_next_title_pattern(ws, title, year)

                # 5) Blogger 발행
                genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
                labels = ["Film"] + ([year] if year else []) + genres_list

                res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=labels, is_draft=False)
                print(f"✅ Publikasi selesai: {res.get('url','(URL tidak diketahui)')}")

                # 6) Google Sheets 업데이트 (완)
                ws.update_cell(i, 6, "완")
                print(f"✅ Google Sheets diperbarui (baris {i})")

            except Exception as e:
                print(f"❌ Terjadi kesalahan saat eksekusi: {e}")

            finally:
                # 7) 로그 기록 (P열 = 16열, append)
                try:
                    prev = ws.cell(i, 16).value or ""
                    new_log = log_buffer.getvalue().strip().replace("\n", " | ")
                    new_val = (prev + " | " if prev else "") + new_log
                    ws.update_cell(i, 16, new_val)
                    print(f"📌 Log eksekusi disimpan (baris {i}, kolom P)")
                except Exception as log_e:
                    sys.__stdout__.write(f"❌ Gagal menyimpan log: {log_e}\n")

            break  # ✅ 한 건만 처리 후 종료


# ===============================
# 메인 호출부
# ===============================
if __name__ == "__main__":
    for n in range(POST_COUNT):
        print(f"\n🚀 Posting {n+1}/{POST_COUNT} dimulai")
        main()

        if n < POST_COUNT - 1 and POST_DELAY_MIN > 0:
            print(f"⏳ Tunggu {POST_DELAY_MIN} menit sebelum posting berikutnya...")
            time.sleep(POST_DELAY_MIN * 60)
























