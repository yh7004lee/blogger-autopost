#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Excel(MOVIE_ID) → TMDB → Blogger 자동 포스팅 파이프라인
- movies_discover.xlsx 읽기: A=제목, B=MOVIE_ID, C=개봉일, D=평점, E=투표수, F=완료표시
- F열이 "완"인 행은 건너뜨고, 첫 번째 미완료 행(B열의 MOVIE_ID)로 포스팅
- TMDB 상세/출연/이미지/리뷰/추천/예고편 수집
- 랜덤 스피너: 서론(6문장), 섹션 리드(4문장), 마무리(6문장)
- Blogger API로 발행 (blogId=7755804984438912295)
- 성공 시 해당 행 F열에 "완" 기록 후 저장
"""
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
POST_COUNT =1     # 몇 번 포스팅할지 (예: 10 이면 10회 반복)
POST_DELAY_MIN = 1   # 각 포스팅 후 대기 시간 (분 단위, 0 이면 즉시 다음 실행)
# ===============================
# 🔧 환경/경로 설정

BLOG_ID = "1140596789331555981"       # 요청하신 블로그 ID
CLIENT_SECRET_FILE = r"D:/py/cc.json" # 본인 구글 OAuth 클라이언트 시크릿 JSON 경로
BLOGGER_TOKEN_PICKLE = "blogger_token.pickle"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

# ===============================
# 🈶 TMDB 설정 (요청: 키를 가리지 말 것 — 사용자가 제공한 값을 그대로 사용)
LANG = "pt-BR"
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"

# 🔑 TMDB 인증정보 (사용자가 예시로 제공한 값 — 그대로 둠)
BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI1NmY0YTNiY2UwNTEyY2RjMjAxNzFhODMxNTNjMjVkNiIsIm5iZiI6MTc1NjY0NjE4OC40MTI5OTk5LCJzdWIiOiI2OGI0NGIyYzI1NzIyYjIzNDdiNGY0YzQiLCJzY29wZXMiOlsiYXBpX3JlYWQiXSwidmVyc2lvbiI6MX0.ShX_ZJwMuZ1WffeUR6PloXx2E7pjBJ4nAlQoI4l7nKY"
API_KEY = "56f4a3bce0512cdc20171a83153c25d6"

# ===============================
# 제목 패턴 목록
# ===============================
TITLE_PATTERNS = [
    "{title} {year} sinopse elenco crítica trailer",
    "Sinopse do filme {title} {year} crítica elenco trailer",
    "Elenco de {title} {year} sinopse completa crítica",
    "Trailer oficial de {title} {year} sinopse crítica elenco",
    "Crítica e análise do filme {title} {year} elenco sinopse",
    "{year} lançamento {title} sinopse crítica elenco trailer",
    "{title} crítica e sinopse {year} elenco trailer",
    "Filme {title} {year} crítica trailer elenco e sinopse",
    "Sinopse completa de {title} {year} elenco crítica trailer",
    "{title} análise {year} trailer oficial crítica sinopse"
]

# ===============================
# 시트2 K1 셀 기반 로테이션 함수
# ===============================
def get_next_title_pattern(ws2, title, year):
    # 현재 인덱스 불러오기 (없으면 0으로 초기화)
    try:
        idx_val = ws2.acell("K1").value
        idx = int(idx_val) if idx_val and idx_val.isdigit() else 0
    except Exception:
        idx = 0

    # 패턴 선택
    pattern = TITLE_PATTERNS[idx % len(TITLE_PATTERNS)]
    blog_title = pattern.format(title=title, year=year)

    # 다음 인덱스 저장
    try:
        ws2.update_acell("K1", str(idx + 1))
    except Exception as e:
        print(f"⚠️ K1 셀 업데이트 실패: {e}")

    return blog_title


# 🔑 유튜브 API 인증정보
YOUTUBE_API_KEY = "AIzaSyD92QjYwV12bmLdUpdJU1BpFX3Cg9RwN4o"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# 🏷️ 해시태그 생성 함수
def make_hashtags_from_title(title: str) -> str:
    import re
    # 괄호 안 숫자도 분리
    words = re.findall(r"[가-힣A-Za-z0-9]+|\([^)]+\)", title)
    hashtags = ["#" + w.strip() for w in words if w.strip()]
    return " ".join(hashtags)


def get_youtube_trailers(title_pt, title_en=None, max_results=2):
    """유튜브에서 예고편 검색 (포르투갈어 먼저, 없으면 영어로)"""
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

    # 1차: 포르투갈어 제목 + "trailer oficial"
    if title_pt:
        results = search(f"{title_pt} trailer oficial")
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
# Google Sheets 연결 (영화 시트 전용)
# ===============================

def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    creds = ServiceAccountCredentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    SHEET_ID = "10kqYhxmeewG_9-XOdXTbv0RVQG9_-jXjtg0C6ERoGG0"
    return gc.open_by_key(SHEET_ID).get_worksheet(1)





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
        data = tmdb_get(f"/person/{person_id}", params={"language": "en-US"}, bearer=bearer, api_key=api_key)
        name_en = data.get("name", "")
        return name_en
    except Exception as e:
        print(f"⚠️ Falha ao buscar nome para pessoa {person_id}: {e}")
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
def get_movie_bundle(movie_id, lang="pt-BR", bearer=None, api_key=None):
    params = {
        "language": lang,
        "append_to_response": "credits,images",
        "include_image_language": "ko,en,null"
    }
    return tmdb_get(f"/movie/{movie_id}", params=params, bearer=bearer, api_key=api_key)

def get_movie_reviews(movie_id, lang="pt-BR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/reviews", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])

def get_movie_videos(movie_id, lang="pt-BR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/videos", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])

def get_movie_recommendations(movie_id, lang="pt-BR", bearer=None, api_key=None):
    j = tmdb_get(f"/movie/{movie_id}/recommendations", params={"language": lang}, bearer=bearer, api_key=api_key)
    return j.get("results", [])

def get_movie_release_cert(movie_id, bearer=None, api_key=None):
    def map_kr(cert):
        mapping = {
            "ALL": "전체관람가", "G": "전체관람가", "0": "전체관람가",
            "12": "12세 관람가",
            "15": "15세 관람가",
            "18": "청소년 관람불가", "19": "청소년 관람불가", "R": "청소년 관람불가"
        }
        if cert in mapping: return mapping[cert]
        return cert if cert else ""

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

    kr = find_cert("KR")
    if kr: return map_kr(kr)
    us = find_cert("US")
    if us: return us
    return ""

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

    # 6. Impacto
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
# 🎬 아웃트로 (7문장)
# ===============================
def make_outro_6(title, year, genres_str, director_names, keywords):
    year_txt = year if year else "desconhecido"
    director_one = director_names[0] if director_names else ""

    s1 = choose(
        f"Chegamos ao fim desta análise sobre o filme <b>{title}</b> ({year_txt}), que trouxe tantos pontos interessantes para refletirmos.",
        f"Encerramos aqui a apresentação de <b>{title}</b> ({year_txt}), uma obra que certamente merece estar no radar de qualquer amante do cinema.",
        f"Terminamos esta jornada pelo universo de <b>{title}</b> ({year_txt}), destacando os aspectos que o tornam uma produção tão comentada.",
        f"Este foi um mergulho no mundo de <b>{title}</b> ({year_txt}), explorando os elementos que fazem deste filme algo memorável."
    )

    s2 = choose(
        "Ao longo do artigo, revisitamos a sinopse, comentamos sobre o elenco e detalhamos os principais aspectos técnicos e artísticos.",
        "Nesta análise, percorremos a história, falamos dos atores e apontamos os pontos altos que tornam o filme envolvente.",
        "Passamos pela trama, pela direção e pelo impacto cultural que este título trouxe para os espectadores.",
        "Relembramos a narrativa, a ambientação e os personagens que fazem de <b>{title}</b> uma experiência especial."
    )

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

    s4 = choose(
        "As avaliações e notas são apenas guias, mas a verdadeira experiência vem de assistir e sentir cada cena por conta própria.",
        "Os números e críticas importam, mas nada substitui a emoção pessoal de se conectar com a narrativa.",
        "Vale lembrar que opiniões variam, e o melhor é sempre tirar suas próprias conclusões ao assistir.",
        "A nota é apenas uma referência: o impacto real depende do olhar de cada espectador."
    )

    s5 = choose(
        "Ao final, também deixamos recomendações de filmes relacionados que podem enriquecer ainda mais sua jornada cinematográfica.",
        "Para quem gostou desta experiência, indicamos títulos semelhantes que ampliam o repertório e trazem novas descobertas.",
        "Sugerimos ainda obras que dialogam com este filme, permitindo comparações interessantes e novas perspectivas.",
        "Para continuar no clima, oferecemos algumas opções de filmes que seguem a mesma linha temática."
    )

    kw = ", ".join([k for k in (keywords or []) if k][:6]) if keywords else ""
    s6 = choose(
        f"Entre os principais pontos, destacamos palavras-chave como {kw}, que ajudam a compreender melhor o alcance da obra.",
        f"As palavras-chave {kw} sintetizam os elementos centrais do filme e podem servir de guia para novas buscas.",
        f"Destacamos termos como {kw}, que reforçam a importância desta produção dentro de seu gênero.",
        f"Os conceitos de {kw} foram recorrentes e mostram como o filme se posiciona dentro do cenário cinematográfico."
    ) if kw else "Esperamos que as informações acima sirvam como um bom guia para sua próxima sessão de cinema."

    s7 = choose(
        "Muito obrigado por ter acompanhado até aqui, espero que este conteúdo tenha inspirado sua próxima sessão de cinema. 🙂",
        "Agradecemos por sua leitura e desejamos que aproveite ainda mais suas experiências cinematográficas, até a próxima!",
        "Se gostou do artigo, compartilhe com amigos e continue acompanhando nossas próximas análises de grandes filmes.",
        "Foi um prazer trazer esta análise para você, e em breve voltaremos com novos títulos e recomendações especiais."
    )

    return " ".join([s1, s2, s3, s4, s5, s6, s7])



def make_section_lead(name, title, year, genres_str, cert_label, extras=None):
    """Introdução de 4 frases para cada seção (tom amigável e rico, com muitas combinações)"""
    extras = extras or {}
    year_txt = f"{year}년" if year else ""
    genre_phrase = genres_str if genres_str else "gênero"
    cert_txt = cert_label or "classificação desconhecida"
    cast_top = extras.get("cast_top", [])
    who = "·".join(cast_top[:3]) if cast_top else ""
    director_one = extras.get("director_one", "")
    runtime_min = extras.get("runtime_min", None)
    runtime_txt = f"{runtime_min} minutos" if runtime_min else ""

    if name == "줄거리":
        base = [
            choose(
                f"Vou apresentar o enredo de {title}{f'({year_txt})' if year_txt else ''} de forma leve, evitando ao máximo spoilers.",
                f"Para quem ainda não assistiu, vou organizar o enredo principal de {title} de maneira clara.",
                f"O enredo de {title}, sem revelar detalhes, apenas destacando o fluxo central da história.",
                f"Sem perder a diversão, que tal acompanharmos juntos a estrutura da história de <b>{title}</b>?",
                f"Vou explicar de forma simples para que até quem nunca ouviu falar do enredo de {title} consiga entender.",
                f"Sem grandes spoilers, vou destacar apenas os principais acontecimentos de {title}.",
                f"Para que você entenda rapidamente, vou mostrar um pouco do enredo de {title}.",
                f"Vou organizar o enredo passo a passo para que você tenha uma ideia antes de assistir.",
                f"De maneira equilibrada, mas deixando curiosidade, vou compartilhar a história de {title}.",
                f"Sem estragar a surpresa, vamos acompanhar o fio condutor de {title}."
            ),
            choose(
                f"No início, a ambientação é estabelecida naturalmente, no meio {choose('os conflitos se intensificam', 'a tensão aumenta', 'as relações se complicam')} e no final {choose('as emoções explodem', 'as peças do quebra-cabeça se encaixam', 'a mensagem fica clara')}.",
                f"{choose('A primeira cena começa de forma simples', 'Desde o início há tensão', 'A introdução é tranquila')}, em seguida {choose('os personagens entram em ação', 'segredos são revelados', 'os conflitos ficam evidentes')}, aumentando o envolvimento.",
                f"A estrutura geral segue {choose('introdução→conflito→resolução', 'partida→crise→crescimento', 'encontro→conflito→escolha')}, e cada cena tem seu ponto de destaque.",
                f"A partir do meio, a história ganha ritmo e a tensão cresce muito mais.",
                f"No final, as pistas lançadas vão se revelando e a diversão aumenta."
            ),
            choose(
                f"A atmosfera típica de {genre_phrase} se mistura ao desenvolvimento, mantendo o tom {choose('equilibrado', 'sem exageros', 'tranquilo')}.",
                f"Mesmo sem muitas explicações, apenas as cenas já garantem a imersão.",
                f"As grandes reviravoltas ficam para você descobrir, mas vou adiantar um pouco do clima.",
                f"A narrativa não é exagerada, o que facilita acompanhar naturalmente.",
                f"É do tipo que convence mais pelo ritmo e direção do que pelos diálogos, oferecendo uma diversão diferente."
            ),
            choose(
                f"A classificação indicativa é {cert_txt}, e dependendo do gosto {choose('é ótimo para ver em família.', 'é uma boa opção para assistir com amigos.', 'vale a pena assistir sozinho e focado.')}",
                f"Com a classificação {cert_txt}, você pode assistir sem preocupação e aproveitar apenas acompanhando a atmosfera.",
                f"A classificação é {cert_txt}, mas fique à vontade para curtir como preferir.",
                f"Ainda que a classificação seja {cert_txt}, o filme traz temas com os quais qualquer pessoa pode se identificar."
            )
        ]
        if maybe(True, 0.4):
            base.append(
                choose(
                    "A seguir vou organizar com mais detalhes.",
                    "Agora, vamos acompanhar as principais cenas e linhas emocionais.",
                    "Já entendemos a estrutura geral, então vendo os detalhes será ainda mais divertido."
                )
            )



    
   
    elif name == "출연진":
        base = [
            choose(
                f"O elenco desta vez conta com {who} {('e outros' if who else '')}, só de ouvir os nomes já dá para entender por que foi tão comentado.",
                f"O lineup de atores chama atenção desde o início{': ' + who if who else ''}. A presença em cena é marcante.",
                f"Logo nos primeiros créditos aparecem rostos conhecidos{(' — ' + who) if who else ''}.",
                f"Graças a {who} {('e outros' if who else '')}, dá para assistir ao filme com confiança." if who else "Só de ver a formação do elenco já aumenta a expectativa.",
                f"Atores renomados se reuniram e deram ainda mais força à obra.",
                f"Só de olhar a lista de atores já dá a sensação de que ‘vale a pena assistir’.",
                f"O elenco mostra claramente por que a equipe de produção estava tão confiante.",
                f"Cada ator tem uma presença marcante e se destaca.",
                f"Só de ouvir os nomes dos principais papéis já dá vontade de aplaudir.",
                f"O simples fato de contar com atores de confiança já traz uma sensação de empolgação."
            ),
            choose(
                f"O equilíbrio entre protagonistas e coadjuvantes, {choose('a harmonia de tons', 'a sincronia nas falas', 'o entrosamento nas atuações')} fazem com que os personagens ganhem vida naturalmente.",
                f"{choose('Os olhares e gestos', 'O timing das reações', 'O ritmo das falas')} fortalecem as cenas sem exagero, de forma fluida.",
                f"A química entre os atores faz com que a linha emocional {choose('flua naturalmente', 'cresça de forma consistente', 'aumente gradualmente')} e brilhe no clímax.",
                f"O tom das atuações é uniforme, o que facilita a imersão.",
                f"A entrega das falas é natural e convincente, sem exageros.",
                f"O equilíbrio entre protagonistas e coadjuvantes dá vida aos personagens.",
                f"O tom estável das atuações permite que o público mergulhe confortavelmente.",
                f"A sintonia do elenco dá intensidade a cada cena.",
                f"O ritmo entre diálogos e emoções se encaixa perfeitamente.",
                f"Quase não há artificialidade, o que aumenta a sensação de realidade."
            ),
            choose(
                f"Especialmente a {choose('contraposição dos personagens', 'diferença de gerações', 'conflito de valores')} traz uma química interessante.",
                f"O {choose('trabalho em dupla', 'ensaio coletivo', 'trabalho em equipe')} funciona muito bem, deixando as cenas ainda mais divertidas.",
                f"Até as breves participações especiais viram destaque, preste atenção.",
                f"A força dos coadjuvantes enriquece ainda mais a história.",
                f"A sinergia dos atores salta aos olhos em cada cena.",
                f"Algumas combinações inesperadas criam uma tensão intrigante.",
                f"O contraste entre os personagens torna o tema ainda mais claro.",
                f"Mesmo os papéis pequenos cumprem bem sua função, sem lacunas.",
                f"Até os figurantes entregam atuações marcantes.",
                f"Alguns atores conseguem deixar sua marca mesmo em apenas uma cena."
            ),
            choose(
                "A seguir, vou organizar uma breve apresentação dos principais papéis.",
                "Agora, vamos ver que personagem cada ator interpreta.",
                "Na sequência, vou apresentar as informações do elenco uma a uma.",
                "Confira imediatamente qual ator assumiu qual papel.",
                "Vamos dar uma olhada mais detalhada na lista do elenco.",
                "Vou resumir rapidamente os papéis e características de cada ator.",
                "Vou apresentar um a um os personagens interpretados pelos atores.",
                "Aqui está um panorama do elenco por personagem.",
                "Veja abaixo as informações sobre o elenco e seus papéis.",
                "Vou mostrar quais cores cada ator trouxe para o personagem que interpretou."
            )
        ]


 
    elif name == "스틸컷":
        base = [
            choose(
                "Só de olhar os stills já dá para sentir a atmosfera do filme.",
                "Com apenas algumas imagens já é possível perceber o clima da obra.",
                "Poucas fotos já transmitem bem as cores e o tom do filme.",
                "Assim que você vê os stills, já entende qual é o tom da produção.",
                "Basta uma ou duas fotos para captar o mood do filme.",
                "Mesmo sendo breves, os stills já revelam a emoção central da história.",
                "Com apenas algumas cenas é possível sentir claramente a atmosfera.",
                "Os stills podem ser considerados a primeira impressão do filme.",
                "Mesmo em cortes curtos, a atmosfera do filme aparece viva.",
                "Com poucas imagens já dá para imaginar a textura da narrativa."
            ),
            choose(
                f"A {choose('composição do quadro', 'angulação da câmera', 'utilização dos espaços')} é estável e agradável aos olhos.",
                f"A {choose('paleta de cores', 'iluminação', 'contraste')} é {choose('sofisticada', 'suave', 'intensa')}, deixando as cenas marcantes.",
                f"O design de produção é {choose('perfeito para a situação', 'sem exageros', 'alinhado com a emoção')}, dando plenitude às imagens.",
                f"A composição de cena tem equilíbrio e isso torna o visual interessante.",
                f"A forma como luz e cores são trabalhadas é impressionante.",
                f"Até nos pequenos detalhes percebe-se o cuidado da produção.",
                f"A harmonia entre composição e cores faz a cena parecer uma pintura.",
                f"A sensação da câmera em movimento também se reflete nos stills.",
                f"As cores desempenham um papel central na definição da atmosfera.",
                f"A direção de arte transmite claramente o mood do filme."
            ),
            choose(
                "Só de olhar os cortes já dá para sentir a linha emocional.",
                "Mesmo em imagens estáticas, a emoção dos personagens é transmitida.",
                "Os stills despertam curiosidade sobre a próxima cena.",
                "Parece que a história continua só pelas fotos capturadas.",
                "Mesmo paradas, as imagens carregam tensão.",
                "Momentos curtos captados que deixam um longo impacto.",
                "Há muitos detalhes perceptíveis apenas nos stills.",
                "Poucas fotos já ajudam a montar o quebra-cabeça da narrativa.",
                "As expressões dos personagens nos cortes já contam muita coisa.",
                "Mesmo uma cena breve pode representar todo o mood do filme."
            ),
            choose(
                "Veja abaixo as imagens e sinta de antemão a atmosfera do filme.",
                "Ao ver os stills primeiro, a imersão no longa aumenta.",
                "Aproveite as fotos para sentir antes o encanto do filme.",
                "Depois de ver as imagens, os detalhes ficam mais evidentes no longa.",
                "Vale a pena conferir os cortes para identificar pontos-chave da obra.",
                "Os stills funcionam como pequenos trailers dentro do filme.",
                "Ver as fotos antes já prepara você para entrar na história.",
                "Ao reconhecer estas cenas, a experiência durante o longa será ainda melhor.",
                "Captar o clima pelas imagens torna a experiência mais rica.",
                "Veja os stills e escolha quais cenas você mais espera assistir."
            )
        ]


    elif name == "평점 및 인기":
            base = [
                choose(
                    f"A avaliação de {title} é um indicador claro da reação do público.",
                    f"Só de ver a nota desta obra já dá para ter uma ideia da recepção popular.",
                    f"A nota é uma forma rápida de entender a primeira impressão da obra.",
                    f"A pontuação numérica mostra os sentimentos honestos dos espectadores.",
                    f"A avaliação de {title} sugere o nível de popularidade e relevância.",
                    f"A nota também serve como um termômetro de quanto a obra foi querida.",
                    f"A pontuação ajuda a medir as expectativas e a satisfação do público.",
                    f"A nota é o jeito mais simples de ver o desempenho de bilheteria.",
                    f"A pontuação de {title} mostra indiretamente o entusiasmo do público.",
                    f"Com uma avaliação visível, dá para sentir a importância da obra."
                ),
                choose(
                    "O número de votos e a média dizem mais do que simples estatísticas.",
                    "Quanto mais votos, maior a confiabilidade da avaliação.",
                    "Olhar a média junto com o tamanho da amostra dá mais precisão.",
                    "Quando os votos aumentam, fica claro o reconhecimento popular.",
                    "Muitos votos significam que o filme realmente foi comentado.",
                    "O grande número de avaliações mostra o alto interesse pela obra.",
                    "Não só a média, mas também o volume de avaliações é importante.",
                    "O total de votos indica o quão difundida foi a obra.",
                    "Ver a média junto com a participação dá uma noção melhor da posição do filme.",
                    "Os dados de avaliação carregam significados além dos números."
                ),
                choose(
                    "Claro, números não dizem tudo. Assistir é sempre o mais certeiro.",
                    "Uma nota alta não garante diversão, e uma baixa não significa tédio.",
                    "Use a pontuação apenas como referência: seu gosto importa mais.",
                    "A avaliação é só um guia, a decisão final é sua.",
                    "Mesmo notas altas podem não agradar se não for seu estilo.",
                    "Uma nota baixa pode esconder um filme inesquecível para você.",
                    "No fim, o mais importante é se você aproveita a experiência.",
                    "Use a nota como referência leve, sem se preocupar demais.",
                    "A pontuação mostra a voz do público, mas sua opinião é o que conta.",
                    "Use a nota apenas como uma orientação superficial."
                ),
                choose(
                    "Veja abaixo os números apenas como referência leve.",
                    "Considere os dados apenas como guia e siga sua intuição.",
                    "Confira a tabela e perceba a reação geral do público.",
                    "Junto dos números, ver as reações reais é ainda mais divertido.",
                    "Considere os dados como referência, mas encontre sua resposta assistindo.",
                    "Mais interessante que a média é observar a distribuição e o clima.",
                    "Não olhe só os números, leia também as críticas para entender melhor.",
                    "A tabela abaixo resume a reação, encare de forma leve.",
                    "No fim, a escolha é sua, os números são apenas uma pista.",
                    "Veja a tabela e sinta apenas o fluxo geral das reações."
                )
            ]



    
    elif name == "베스트 리뷰":
        base = [
            choose(
                "Mesmo em poucas palavras, as críticas dos espectadores carregam emoções vivas.",
                "Basta ler uma linha de review para sentir a atmosfera da sessão real.",
                "Ao ver os comentários do público, já dá para entender como a obra foi recebida.",
                "Críticas curtas e diretas revelam bem o charme do filme.",
                "As reviews são a voz mais direta dos espectadores, mais do que números.",
                "Até em comentários breves transparecem sentimentos genuínos do público.",
                "Em apenas uma ou duas linhas, muitas vezes está o essencial do filme.",
                "As impressões honestas dos espectadores são mais impactantes que dados frios.",
                "São palavras de quem realmente assistiu, por isso ganham confiança.",
                "As críticas transmitem uma sensação de presença que dá gosto de ler."
            ),
            choose(
                "Dependendo do gosto, opiniões podem divergir — e isso é parte do encanto do cinema.",
                "Elogios ou críticas, todas são interpretações válidas da obra.",
                "O conjunto de reações positivas e negativas compõe o quadro completo.",
                "Quanto mais diversas as opiniões, mais ampla é a dimensão do filme.",
                "Ter elogios e críticas ao mesmo tempo prova que foi muito comentado.",
                "Olhares diferentes revelam as múltiplas camadas do cinema.",
                "Mesmo sobre a mesma cena, interpretações variam e isso é fascinante.",
                "Concordando ou discordando, essa diversidade é a graça da sétima arte.",
                "Para uns é um filme da vida, para outros algo comum — essa variedade é valiosa.",
                "Assim como os gostos variam, as críticas naturalmente são diversas."
            ),
            choose(
                "Abaixo reuni algumas críticas marcantes.",
                "Selecionei comentários breves, evitando ao máximo spoilers.",
                "Críticas representativas dão uma boa ideia da impressão deixada pelo filme.",
                "Um conjunto de reviews curtas e intensas torna a leitura divertida.",
                "Organizei os comentários resumidos para você conferir facilmente.",
                "Preparei uma seleção de críticas para servir de referência.",
                "Mesmo frases curtas já transmitem a atmosfera da obra.",
                "Por serem concisas, as críticas são rápidas de acompanhar.",
                "Separei as frases mais impactantes para apresentar.",
                "Esses breves comentários mostram outra faceta do filme."
            ),
            choose(
                "Ao ler, você percebe naturalmente quais pontos mais gosta.",
                "Se encontrar uma frase que tocar você, releia após assistir.",
                "Quando uma crítica coincide com sua própria emoção, surge uma empatia curiosa.",
                "É interessante revisitar a obra através do olhar de outras pessoas.",
                "Lendo críticas, você pode identificar antecipadamente pontos de atenção.",
                "Mesmo em comentários curtos dá para pensar: ‘Ah, é essa a sensação’.",
                "As reviews podem revelar outro charme escondido do filme.",
                "Às vezes, nos comentários do público estão os pontos secretos da obra.",
                "Lendo opiniões diferentes, sua própria visão pode se aprofundar.",
                "Quando as palavras de uma crítica combinam com seu gosto, a sensação é ótima."
            )
        ]


    elif name == "예고편":
            base = [
                choose(
                    "O trailer é a forma mais rápida de sentir o tom e a atmosfera do filme.",
                    "Em poucos segundos, o trailer já mostra o mood principal da obra.",
                    "Só de assistir ao trailer, dá para captar a essência do filme.",
                    "Curto mas intenso, o trailer aumenta a expectativa pelo longa.",
                    "O trailer funciona como uma janela para espiar a cor da obra antes de assistir.",
                    "É como um cartão de visita, mostrando a primeira impressão do filme.",
                    "Mesmo em poucos segundos, o trailer transmite todo o charme.",
                    "Cenas rápidas já comunicam bem a atmosfera geral.",
                    "O trailer é o recurso que desperta interesse antes da sessão.",
                    "Só o trailer já permite saborear bastante da magia do filme."
                ),
                choose(
                    "Sem medo de spoilers, você pode conferir apenas a atmosfera.",
                    "O trailer relaxa um pouco a tensão mas deixa curiosidade no ar.",
                    "Em pouco tempo já transmite ritmo e emoção da obra.",
                    "Só com cortes e som já dá para sentir a imersão.",
                    "Depois do trailer, a vontade de ver o longa só aumenta.",
                    "A música e a montagem revelam bem a identidade do filme.",
                    "O ritmo e o tempo já antecipam a energia do longa.",
                    "Mesmo curto, o trailer dá pistas sobre a narrativa.",
                    "Som e imagem juntos já fazem pensar: ‘Ah, esse é o estilo do filme’.",
                    "Em poucos segundos, já traz várias cenas memoráveis."
                ),
                choose(
                    f"Assistindo ao trailer {runtime_txt+' inteiro ' if runtime_txt else ''}você capta rapidamente o tom do longa.",
                    "Às vezes, uma única fala no trailer já representa o tom inteiro do filme.",
                    "A primeira e a última cena do trailer podem conter pistas importantes.",
                    "Mesmo curto, o vídeo traz uma mensagem forte.",
                    "Só pelo trailer já dá para sentir a linha emocional que será seguida.",
                    "A beleza visual e a trilha já bastam para transmitir o encanto.",
                    "Uma cena do trailer pode ser o motivo decisivo para ver o filme.",
                    "Mesmo breve, o trailer tem força para gerar imersão.",
                    "É só um teaser, mas deixa um impacto duradouro.",
                    "Algumas cenas já revelam o tema central da obra."
                ),
                choose(
                    "Se possível, veja uma vez de fones e outra em caixas de som, a sensação muda.",
                    "Ative as legendas: você vai captar melhor o tom e a nuance dos diálogos.",
                    "Se mergulhar mesmo em poucos segundos, o encanto do longa é maior.",
                    "Nos primeiros e últimos 10 segundos muitas vezes está a essência do filme.",
                    "Rever várias vezes o trailer revela detalhes escondidos.",
                    "Quando reencontrar no longa as cenas vistas no trailer, a experiência será prazerosa.",
                    "Mais do que passar rápido, é melhor assistir com foco.",
                    "Mesmo em pouco tempo, há muitos detalhes de produção para notar.",
                    "A combinação de som e imagem pode ser tão marcante quanto o próprio longa.",
                    "Embora curto, o trailer mostra fielmente o mood do filme."
                ),
                choose(
                    "Assista ao vídeo abaixo e, se sentir vontade, siga naturalmente para o longa.",
                    "O trailer é um aperitivo e um convite para o filme completo.",
                    "Se este pequeno clipe já tocar você, o longa será ainda mais envolvente.",
                    "Não há melhor forma de confirmar se combina com seu gosto do que pelo trailer.",
                    "Depois de ver o trailer, escolher o filme fica mais fácil.",
                    "Um vídeo já basta para saber se é do seu estilo.",
                    "O trailer é a melhor ferramenta para criar expectativa pelo longa.",
                    "Ao conhecer a obra pela primeira vez, o trailer é o melhor guia.",
                    "Se o trailer já emocionar, seguir para o longa será sem arrependimentos.",
                    "Sinta levemente o encanto do filme através do trailer."
                )
            ]
    
    
        

 
    elif name == "추천 영화":
        base = [
            choose(
                f"Se você já assistiu {title}, vale a pena conferir também os filmes abaixo.",
                f"Se você gostou de {title}, reuni aqui obras com uma atmosfera parecida.",
                f"Preparei recomendações que combinam com {title}, apresentadas em pôsteres.",
                f"Filmes com um mood semelhante estão reunidos em imagens para você.",
                f"Veja nos pôsteres abaixo se encontra algo que combina com seu gosto.",
                f"Conheça outros filmes relacionados a {title} através destes pôsteres."
            ),
            choose(
                "Desta vez as recomendações trazem apenas título e pôster.",
                "Sem descrições detalhadas, preparei apenas imagens objetivas.",
                "Basta rolar a tela e conferir de forma leve e rápida.",
                "Separei pôsteres curtos e diretos para você ver sem esforço.",
                "Sem explicações textuais — aqui mostro só imagens intuitivas."
            ),
            choose(
                "Se algum pôster chamar sua atenção, guarde a dica.",
                "Pode adicionar imediatamente à sua lista o filme que se destacar para você.",
                "Só pelo pôster já dá para sentir o mood da obra.",
                "Comparar todos de uma vez deixa a escolha divertida.",
                "Entre as imagens, escolha aquele que será ‘o filme do dia’."
            ),
            choose(
                "Então vamos dar uma olhada juntos nos pôsteres recomendados.",
                "Veja as imagens abaixo e escolha os filmes que combinam com você.",
                "Mesmo apenas pelos pôsteres já é possível sentir o charme.",
                "Confira rapidamente os filmes listados abaixo.",
                "Aqui estão recomendações leves e divertidas para você aproveitar."
            )
        ]


    else:
        base = [
            choose(
                "Resumi apenas os pontos principais para você conferir rápido e marcar o que precisa.",
                "A estrutura está organizada para ser clara, basta rolar e acompanhar tranquilamente.",
                "Separei os pontos mais importantes — você pode ler só o que interessa."
            ),
            choose(
                "As seções foram organizadas em ordem intuitiva, cada uma com um breve comentário.",
                "A leitura flui naturalmente entre cenas, informações e críticas.",
                "Se preferir, pode marcar nos favoritos e reler com calma depois."
            ),
            choose(
                "Adicionei também algumas dicas pessoais no meio do conteúdo.",
                "Reduzi os exageros e foquei em trazer sugestões práticas.",
                "Mantive o texto em um tamanho leve e agradável para ler."
            ),
            choose(
                "Então, vamos direto ao conteúdo abaixo.",
                "Agora sim, vamos entrar no assunto de verdade."
            )
        ]


    return " ".join(base)





# ===============================
# HTML 빌더
def get_related_posts(blog_id, count=4):
    import feedparser
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    # 랜덤으로 count개 추출
    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    # HTML 박스 생성 (요청하신 스타일 적용)
    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ Posts recomendados</p>
"""


    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box


def build_html(post, cast_count=10, stills_count=8):
    esc = html.escape
    # Título (pt-BR → fallback em inglês)
    title_pt = esc(post.get("title") or "")
    title_en = esc(post.get("original_title") or "")
    if re.search(r"[ㄱ-ㅎ가-힣]", title_pt):  # Se ainda for coreano
        title = title_en if title_en else title_pt
    else:
        title = title_pt

    
    overview = esc(post.get("overview") or "As informações da sinopse ainda não estão disponíveis.")
    release_date = esc(post.get("release_date") or "")
    year = release_date[:4] if release_date else ""
    runtime = post.get("runtime") or 0
    genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
    genres_str = ", ".join(genres_list)
    tagline = esc(post.get("tagline") or "")
    adult_flag = bool(post.get("adult", False))

    # 제작 국가
    countries = [c.get("name","") for c in post.get("production_countries",[]) if c.get("name")]
    country_str = ", ".join(countries) if countries else "Sem informações de país"

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
        cert = "Conteúdo adulto"

    # 키워드 생성
    base_keywords = []
    for w in (title.replace(":", " ").replace("-", " ").split()):
        if len(w) > 1:
            base_keywords.append(str(w))
    base_keywords += genres_list + director_names[:2] + cast_names[:3]
    if year: base_keywords.append(str(year))
    if cert: base_keywords.append(str(cert))

    base_keywords += ["Crítica", "Avaliação", "Elenco", "Trailer", "Stills", "Filmes Recomendados"]

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
        # 🔑 이름이 한글이면 영어 이름으로 교체 시도
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
        "".join(cast_rows or ['<tr><td style="padding:10px;">Sem informações do elenco.</td></tr>']) +
        '</table>'
    )

    # 스틸컷
    still_divs = []
    for b in backdrops:
        p = img_url(b.get("file_path"), "w780")
        if not p: continue
        still_divs.append(
            f'<div style="flex:0 0 49%;margin:0.5%;"><img src="{p}" alt="Still de {title}" style="width:100%;height:auto;border-radius:10px;"></div>'
        )
    stills_html = (
        '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;">' +
        "".join(still_divs or ['<div style="padding:10px;">Nenhuma imagem de still disponível.</div>']) +
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
        ⭐ Avaliação & 📊 Popularidade
    </div>
    <div style="font-size:18px;color:#222;margin:8px 0;">
        <b style="color:#ff9800;">Nota média:</b> {vote_avg:.1f}/10
    </div>
    <div style="font-size:16px;color:#555;margin:6px 0;">
        Número de votos: {vote_count:,}
    </div>
    <div style="font-size:18px;color:#0066cc;margin-top:10px;">
        <b>Popularidade:</b> {popularity:.1f}
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
        video_html += "<br /><p>⚠️ O trailer abaixo pode não ser o oficial.</p>"
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
        reviews_html = "<br /><br /><br />\n<h2>Melhores críticas de "+title+"</h2>" + "".join(review_blocks)

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
            search_url = f"https://cinebr.appsos.kr/search?q={query}"
            cards.append(
                f"<div style='flex:0 0 30%;margin:1%;text-align:center;'>"
                f"<a href='{search_url}' target='_blank' style='color:#000;text-decoration:none;'>{poster_tag}<br>{mtitle} ({year2})</a>"
                "</div>"
            )
        rec_lead = make_section_lead("추천 영화", title, year, genres_str, cert)
        rec_html = (
            "<br /><br /><br />\n<h2>Filmes recomendados</h2>"
            f"<p>{rec_lead}</p>"
            "<div style='display:flex;flex-wrap:wrap;'>"
            + "".join(cards) +
            "</div>"
        )

    outro_6 = make_outro_6(title, year, genres_str, director_names, keywords)
    related_box = get_related_posts(BLOG_ID, count=4)

    blog_title1 = f"Filme {title} ({year}) Sinopse Elenco Trailer"
    hashtags = make_hashtags_from_title(blog_title1)

    html_out = f"""
<p>{intro_6}</p>
<!--more--><br />
{"<p><img src='"+backdrop+"' style='width:100%;border-radius:12px;'></p>" if backdrop else ""}
{"<p><i>"+html.escape(tagline)+"</i></p>" if tagline else ""}

<br /><br /><br />
<h2>Filme {title} – Sinopse</h2>
<p><b>País:</b> {country_str} | <b>Gênero:</b> {genres_str if genres_str else "Sem informações"}</p>
<p>{make_section_lead("줄거리", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://cinebr.appsos.kr/search/label/{year}?&max-results=10" target="_blank">
    <span style="font-size: medium;"><strong>Filmes recomendados de {year}</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if year else ''}

<div style="background:#fafafa;border:2px solid #ddd;border-radius:12px;padding:10px 18px;">
  <p style="font-weight:bold;">🎬 Sinopse de {title}</p>
  {overview}
</div>
<br />{hashtags}

<br /><br /><br />
<h2>Elenco de {title}</h2>
<p>{make_section_lead("출연진", title, year, genres_str, cert, extras={"cast_top": cast_names})}</p>
{cast_table}
<br />{hashtags}

<br /><br /><br />
<h2>Stills de {title}</h2>
<p>{make_section_lead("스틸컷", title, year, genres_str, cert)}</p>

{f'''<div class="ottistMultiRelated">
  <a class="extL alt" href="https://cinebr.appsos.kr/search/label/{urllib.parse.quote(genres_list[0])}?&max-results=10" target="_blank">
    <span style="font-size: medium;"><strong>Recomendações de filmes de {genres_list[0]}</strong></span>
    <i class="fas fa-link 2xs"></i>
  </a>
</div>''' if genres_list else ''}

{stills_html}
<br />{hashtags}

<br /><br /><br />
<h2>Avaliação e Trailer</h2>
<p>{rating_lead}</p>
{rating_html}{video_html}
{reviews_html}{rec_html}
<br />{hashtags}

<p>{outro_6}</p>
{related_box}
<p style="font-size:12px;">Fonte: <a href="https://www.themoviedb.org/" target="_blank">TMDB</a></p>

"""

    return textwrap.dedent(html_out).strip()



# ===============================
# Blogger 인증/발행
# Blogger 인증용
from google.oauth2.credentials import Credentials as UserCredentials

# Google Sheets 인증용
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

BLOGGER_TOKEN_JSON = "blogger_token.json"  # refresh_token 포함 JSON 파일
SCOPES = ["https://www.googleapis.com/auth/blogger"]

def get_blogger_service():
    with open("blogger_token.json", "r", encoding="utf-8") as f:
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


# ===============================
## 메인 실행부
def main():
    import io, sys, re

    # 로그 버퍼 설정
    log_buffer = io.StringIO()
    class Logger:
        def write(self, msg):
            log_buffer.write(msg)
            sys.__stdout__.write(msg)  # 콘솔에도 그대로 출력
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
            print(f"⚠️ 유효하지 않은 MOVIE_ID: {raw_id} (행 {i}) → 건너뜀")
            continue

        if movie_id and done_flag != "완":
            print(f"👉 대상 행: {i} (MOVIE_ID={movie_id})")

            try:
                # 1) TMDB에서 상세 번들 수집
                post = get_movie_bundle(movie_id, lang=LANG, bearer=BEARER, api_key=API_KEY)

                # 2) HTML 구성
                html_out = build_html(post, cast_count=CAST_COUNT, stills_count=STILLS_COUNT)

                # 3) 포스트 제목
                title = (post.get("title") or post.get("original_title") or f"movie_{movie_id}")
                year = (post.get("release_date") or "")[:4]
                # ws 객체 준비
                ws = get_sheet()   # 이미 sheet2 반환하도록 되어 있으면 ws2 사용
                
                # 블로그 제목 생성
                blog_title = get_next_title_pattern(ws, title, year)


                # 4) Blogger 발행
                genres_list = [g.get("name","") for g in post.get("genres",[]) if g.get("name")]
                labels = ["Filme"] + ([year] if year else []) + genres_list

                res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=labels, is_draft=False)
                print(f"✅ 발행 완료: {res.get('url','(URL 미확인)')}")

                # 5) Google Sheets 업데이트 (완)
                ws.update_cell(i, 6, "완")
                print(f"✅ Google Sheets 업데이트 완료 (행 {i})")

            except Exception as e:
                print(f"❌ 실행 중 오류 발생: {e}")

            finally:
                # 6) 로그 기록 (P열 = 16열, append)
                try:
                    prev = ws.cell(i, 16).value or ""
                    # 줄바꿈 제거 → ' | '로 구분
                    new_log = log_buffer.getvalue().strip().replace("\n", " | ")
                    new_val = (prev + " | " if prev else "") + new_log
                    ws.update_cell(i, 16, new_val)
                    print(f"📌 실행 로그 기록 완료 (행 {i}, P열)")
                except Exception as log_e:
                    sys.__stdout__.write(f"❌ 로그 기록 실패: {log_e}\n")

            break  # ✅ 한 건만 처리 후 종료



# ===============================
# 메인 호출부
# ===============================
if __name__ == "__main__":
    for n in range(POST_COUNT):
        print(f"\n🚀 {n+1}/{POST_COUNT} 번째 포스팅 시작")
        main()

        if n < POST_COUNT - 1 and POST_DELAY_MIN > 0:
            print(f"⏳ {POST_DELAY_MIN}분 대기 후 다음 포스팅...")
            time.sleep(POST_DELAY_MIN * 60)


















