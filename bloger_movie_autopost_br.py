#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")
"""
Excel(MOVIE_ID) → TMDB → Blogger 자동포스팅 파이프라인 (브라질 포르투갈어 버전)
- movies_discover.xlsx 읽기: A=제목, B=MOVIE_ID, C=개봉일, D=평점, E=투표수, F=비고, H=완료표시
- H열이 "완"인 행은 건너뛰고, 첫 번째 미완료 행(B열의 MOVIE_ID)로 포스팅
- TMDB 상세/출연/이미지/리뷰/추천/예고편 수집
- 랜덤 인트로(6문장), 섹션 리드(4문장), 아웃트로(6문장)
- Blogger API로 공개 (blogId=브라질 블로그 ID)
- 성공 시 대상 행 H열에 "완" 기록
"""

import json
import urllib.parse
import os, sys, html, textwrap, requests, random, time, pickle

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

import gspread
from google.oauth2.service_account import Credentials

# ================================
# Google Sheets 인증
# ================================
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    gc = gspread.authorize(creds)

    SHEET_ID = "10kqYhxmeewG_9-XOdXTbv0RVQG9_-jXjtg0C6ERoGG0"
    return gc.open_by_key(SHEET_ID).sheet1


# ===============================
# 📝 포스팅 설정
POST_COUNT = 1     
POST_DELAY_MIN = 1  

# ===============================
# 🔧 환경/경로 설정
EXCEL_PATH = "movies_discover.xlsx"
BLOG_ID = "1140596789331555981"   # ★ 브라질용 블로그 ID로 교체

# ===============================
# 🈶 TMDB 설정
LANG = "pt-BR"   # ★ 포르투갈어 (브라질)
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"

# 🔑 TMDB 인증정보
BEARER = "YOUR_TMDB_BEARER"
API_KEY = "56f4a3bce0512cdc20171a83153c25d6"


# 🔑 YouTube API
YOUTUBE_API_KEY = "YOUR_YOUTUBE_KEY"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# ===============================
# TMDB / YouTube API 함수들 (기존 동일, lang만 pt-BR로)
# ===============================
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

def img_url(path, size="w780"):
    if not path:
        return None
    return f"{IMG_BASE}/{size}{path}"

def choose(*options):
    return random.choice(options)

# ===============================
# 🎬 인트로 생성 (포르투갈어)
# ===============================
def make_intro_6(title, year, genres_str, director_names, main_cast, cert_label, runtime_min, keywords):
    year_txt = f"lançado em {year}" if year else "ano de lançamento desconhecido"
    genre_phrase = genres_str if genres_str else "gênero desconhecido"
    director_one = director_names[0] if director_names else ""
    star_one = main_cast[0] if main_cast else ""
    star_two = main_cast[1] if len(main_cast) > 1 else ""
    runtime_txt = f"{runtime_min} minutos" if runtime_min else "duração desconhecida"
    cert_txt = cert_label or "classificação desconhecida"

    # 1. Abertura
    s1 = choose(
        f"Olá, cinéfilos! Hoje vamos mergulhar no universo do filme <b>{title}</b>, {year_txt}, uma obra que merece toda a sua atenção.",
        f"Se você é apaixonado por cinema, vai gostar de conhecer mais sobre <b>{title}</b>, {year_txt}, um título que já conquistou muitos corações.",
        f"Bem-vindo! Hoje o destaque é para <b>{title}</b>, {year_txt}, um longa que desperta emoções intensas e discussões interessantes.",
        f"O cinema nos brinda com várias obras inesquecíveis, e <b>{title}</b>, {year_txt}, é certamente uma delas que vamos explorar juntos."
    )

    # 2. Gênero
    s2 = choose(
        f"Este é um filme de {genre_phrase}, que combina emoção e profundidade de maneira envolvente.",
        f"Pertencente ao gênero {genre_phrase}, a produção consegue transmitir sentimentos fortes e momentos inesquecíveis.",
        f"Com características marcantes de {genre_phrase}, o longa prende a atenção do início ao fim.",
        f"Envolvendo-se no gênero {genre_phrase}, a trama se desenrola de forma cativante e instigante."
    )

    # 3. Direção
    s3 = (
        choose(
            f"A direção é assinada por {director_one}, que imprime um estilo único e deixa sua marca em cada cena.",
            f"Com {director_one} no comando, a obra se transforma em uma experiência visual e narrativa inesquecível.",
            f"{director_one} conduz a história com sensibilidade e firmeza, criando momentos de grande impacto.",
            f"O olhar criativo de {director_one} faz deste filme algo muito especial e memorável."
        ) if director_one else choose(
            "A direção é equilibrada, com escolhas criativas que mantêm o público imerso.",
            "Mesmo sem grandes exageros, a condução da trama é precisa e muito bem realizada.",
            "A narrativa se beneficia de uma direção clara e consistente, que dá fluidez ao enredo.",
            "A maneira como a história é conduzida garante ritmo e emoção do começo ao fim."
        )
    )

    # 4. Elenco
    s4 = (
        choose(
            f"O elenco brilha com nomes como {star_one}{' e ' + star_two if star_two else ''}, entregando atuações memoráveis.",
            f"Entre os destaques do elenco está {star_one}, cuja performance é digna de aplausos.",
            f"As atuações são sólidas e cheias de emoção, com {star_one} marcando presença em momentos-chave.",
            f"Além de um elenco diversificado, {star_one} se destaca pela entrega em seu papel."
        ) if star_one else choose(
            "O elenco é diversificado e cheio de talentos que enriquecem a narrativa.",
            "Cada integrante do elenco contribui com sua presença marcante.",
            "Os atores entregam interpretações que reforçam a intensidade da história.",
            "O conjunto de atores dá vida a personagens cativantes e bem construídos."
        )
    )

    # 5. Duração e classificação
    s5 = choose(
        f"O filme tem duração de {runtime_txt}, o que torna a experiência equilibrada e envolvente.",
        f"Com seus {runtime_txt}, a narrativa consegue manter o ritmo sem se tornar cansativa.",
        f"A duração de {runtime_txt} é ideal para aproveitar cada detalhe da história."
    ) + " " + choose(
        f"A classificação indicativa é {cert_txt}, tornando-o acessível para diferentes públicos.",
        f"Classificado como {cert_txt}, o longa pode ser apreciado por várias faixas etárias.",
        f"A censura é {cert_txt}, o que ajuda o espectador a decidir a melhor ocasião para assistir."
    )

    # 6. Impacto cultural ou expectativa
    s6 = choose(
        f"<b>{title}</b> despertou debates e gerou expectativas desde seu lançamento, mostrando sua força cultural.",
        f"Desde sua estreia, <b>{title}</b> chamou a atenção por sua proposta ousada e qualidade técnica.",
        f"O impacto de <b>{title}</b> foi imediato, consolidando-o como um dos grandes destaques do {year_txt}.",
        f"Não é apenas um filme, <b>{title}</b> é uma experiência que permanece viva na memória de quem assiste."
    )

    # 7. Encerramento da introdução
    s7 = choose(
        f"Agora, vamos explorar juntos os principais destaques de <b>{title}</b> e entender por que ele merece um lugar especial na sua lista de filmes.",
        f"Nas próximas linhas, você vai conhecer mais sobre a sinopse, o elenco, os bastidores e os pontos fortes de <b>{title}</b>.",
        f"Prepare-se para mergulhar no universo de <b>{title}</b>, analisando detalhes que o tornam uma produção tão relevante.",
        f"Vamos seguir adiante e descobrir o que faz de <b>{title}</b> uma obra tão comentada e aclamada."
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])


# ===============================
# 🎬 아웃트로 생성 (포르투갈어)
# ===============================

def make_outro_6(title, year, genres_str, director_names, keywords):
    year_txt = year if year else "desconhecido"
    director_one = director_names[0] if director_names else ""

    # 1. Encerramento inicial
    s1 = choose(
        f"Chegamos ao fim desta análise sobre o filme <b>{title}</b> ({year_txt}), que trouxe tantos pontos interessantes para refletirmos.",
        f"Encerramos aqui a apresentação de <b>{title}</b> ({year_txt}), uma obra que certamente merece estar no radar de qualquer amante do cinema.",
        f"Terminamos esta jornada pelo universo de <b>{title}</b> ({year_txt}), destacando os aspectos que o tornam uma produção tão comentada.",
        f"Este foi um mergulho no mundo de <b>{title}</b> ({year_txt}), explorando os elementos que fazem deste filme algo memorável."
    )

    # 2. Resumo do que foi abordado
    s2 = choose(
        "Ao longo do artigo, revisitamos a sinopse, comentamos sobre o elenco e detalhamos os principais aspectos técnicos e artísticos.",
        "Nesta análise, percorremos a história, falamos dos atores e apontamos os pontos altos que tornam o filme envolvente.",
        "Passamos pela trama, pela direção e pelo impacto cultural que este título trouxe para os espectadores.",
        "Relembramos a narrativa, a ambientação e os personagens que fazem de <b>{title}</b> uma experiência especial."
    )

    # 3. Reflexão sobre a direção
    s3 = (
        choose(
            f"A condução de {director_one} foi um dos pontos mais fortes, mostrando criatividade e sensibilidade em cada cena.",
            f"{director_one} conseguiu imprimir sua marca pessoal no filme, equilibrando emoção e técnica de maneira única.",
            f"O olhar artístico de {director_one} deixou claro como a direção pode transformar uma história em algo grandioso.",
            f"Não podemos deixar de destacar a visão de {director_one}, que fez deste trabalho uma obra marcante."
        ) if director_one else choose(
            "A direção em geral mostrou equilíbrio e clareza, garantindo ritmo e impacto narrativo até o fim.",
            "Mesmo sem um nome amplamente conhecido na direção, a condução foi sólida e bem estruturada.",
            "A forma como o enredo foi dirigido manteve o público conectado e interessado até os últimos momentos.",
            "A direção mostrou maturidade e domínio técnico, elevando a qualidade da obra."
        )
    )

    # 4. Reflexão sobre avaliação e experiência pessoal
    s4 = choose(
        "As avaliações e notas são apenas guias, mas a verdadeira experiência vem de assistir e sentir cada cena por conta própria.",
        "Os números e críticas importam, mas nada substitui a emoção pessoal de se conectar com a narrativa.",
        "Vale lembrar que opiniões variam, e o melhor é sempre tirar suas próprias conclusões ao assistir.",
        "A nota é apenas uma referência: o impacto real depende do olhar de cada espectador."
    )

    # 5. Recomendação de filmes relacionados
    s5 = choose(
        "Ao final, também deixamos recomendações de filmes relacionados que podem enriquecer ainda mais sua jornada cinematográfica.",
        "Para quem gostou desta experiência, indicamos títulos semelhantes que ampliam o repertório e trazem novas descobertas.",
        "Sugerimos ainda obras que dialogam com este filme, permitindo comparações interessantes e novas perspectivas.",
        "Para continuar no clima, oferecemos algumas opções de filmes que seguem a mesma linha temática."
    )

    # 6. Palavras-chave e importância
    s6 = choose(
        f"Entre os principais pontos, destacamos palavras-chave como {', '.join(keywords[:6])}, que ajudam a compreender melhor o alcance da obra.",
        f"As palavras-chave {', '.join(keywords[:6])} sintetizam os elementos centrais do filme e podem servir de guia para novas buscas.",
        f"Destacamos termos como {', '.join(keywords[:6])}, que reforçam a importância desta produção dentro de seu gênero.",
        f"Os conceitos de {', '.join(keywords[:6])} foram recorrentes e mostram como o filme se posiciona dentro do cenário cinematográfico."
    )

    # 7. Despedida final
    s7 = choose(
        "Muito obrigado por ter acompanhado até aqui, espero que este conteúdo tenha inspirado sua próxima sessão de cinema. 🙂",
        "Agradecemos por sua leitura e desejamos que aproveite ainda mais suas experiências cinematográficas, até a próxima!",
        "Se gostou do artigo, compartilhe com amigos e continue acompanhando nossas próximas análises de grandes filmes.",
        "Foi um prazer trazer esta análise para você, e em breve voltaremos com novos títulos e recomendações especiais."
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])


# ===============================
# Blogger 인증
# ===============================
import google.oauth2.credentials
CLIENT_SECRET_FILE = r"cc.json"
BLOGGER_TOKEN_JSON = "blogger_token.json"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

def get_blogger_service():
    with open(BLOGGER_TOKEN_JSON, "r", encoding="utf-8") as f:
        token_data = json.load(f)
    creds = google.oauth2.credentials.Credentials.from_authorized_user_info(token_data, SCOPES)
    return build("blogger", "v3", credentials=creds)

# ===============================
# Excel 헬퍼
# ===============================
DONE_COL = 8       # H열
DONE_MARK = "완"   

def find_next_row(ws):
    rows = ws.get_all_values()
    for idx, row in enumerate(rows[1:], start=2):
        done_val = row[7].strip() if len(row) > 7 else ""   # H열
        movie_raw = row[1].strip() if len(row) > 1 else ""
        if done_val == DONE_MARK:
            continue
        if not movie_raw.isdigit():
            continue
        return idx, int(movie_raw)
    return None, None

def mark_done(ws, row_idx):
    ws.update_cell(row_idx, DONE_COL, DONE_MARK)

# ===============================
# Blogger 포스트
# ===============================
def post_to_blogger(service, blog_id, title, html_content, labels=None, is_draft=False):
    body = {"kind": "blogger#post", "title": title, "content": html_content}
    if labels:
        body["labels"] = labels
    post = service.posts().insert(blogId=blog_id, body=body, isDraft=is_draft).execute()
    return post

# ===============================
# 메인 실행
# ===============================
def main_once():
    ws = get_sheet()
    service = get_blogger_service()

    target_row, movie_id = find_next_row(ws)
    if not movie_id:
        print("📌 처리할 행이 없습니다. (모든 행이 '완')")
        return False

    print(f"👉 대상 행: {target_row} (MOVIE_ID={movie_id})")

    post = tmdb_get(f"/movie/{movie_id}", params={"language": LANG, "append_to_response": "credits,images"}, bearer=None, api_key=API_KEY)

    title = post.get("title") or post.get("original_title") or f"movie_{movie_id}"
    year = (post.get("release_date") or "")[:4]
    blog_title = f"Filme {title} ({year}) sinopse elenco trailer"

    html_out = f"<p>{make_intro_6(title, year, '', [], [], '', 0, [title])}</p><br><p>{make_outro_6(title, year, '', [], [title])}</p>"

    res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=["Filme", year] if year else ["Filme"])
    print(f"✅ 발행 완료: {res.get('url', '(URL 미확인)')}")

    mark_done(ws, target_row)
    print(f"✅ Google Sheets 완료 표시 (행 {target_row}, H열)")

    return True

if __name__ == "__main__":
    for i in range(POST_COUNT):
        print(f"\n🚀 {i+1}/{POST_COUNT} 번째 포스팅 시작")
        ok = main_once()
        if not ok:
            print("📌 더 이상 처리할 데이터가 없어 종료합니다.")
            break
        if i < POST_COUNT - 1 and POST_DELAY_MIN > 0:
            time.sleep(POST_DELAY_MIN * 60)



