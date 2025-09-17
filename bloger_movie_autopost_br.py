
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

"""
Excel(MOVIE_ID) → TMDB → Blogger 자동포스팅 파이프라인 (브라질 포르투갈어 버전)
- Google Sheets에서 대상 영화 ID 읽기
- TMDB 상세/출연/이미지/추천/예고편 수집
- 인트로(7문장) + 섹션 리드 + 본문 섹션 + 아웃트로(7문장)
- Blogger API 발행 후 시트에 "완" 표시
"""

import json, os, html, textwrap, requests, random, time, re
import xml.etree.ElementTree as ET
from googleapiclient.discovery import build
import gspread
from google.oauth2.service_account import Credentials
import google.oauth2.credentials

# ================================
# Google Sheets 인증
# ================================
def get_sheet():
    SERVICE_ACCOUNT_FILE = "sheetapi.json"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    SHEET_ID = "10kqYhxmeewG_9-XOdXTbv0RVQG9_-jXjtg0C6ERoGG0"
    return gc.open_by_key(SHEET_ID).sheet1

# ===============================
# 📝 포스팅 설정
POST_COUNT = 1
POST_DELAY_MIN = 1

# ===============================
# 🔧 환경/경로 설정
BLOG_ID = "1140596789331555981"
RELATED_RSS_URL = f"https://www.blogger.com/feeds/{BLOG_ID}/posts/default?alt=rss"

# ===============================
# 🈶 TMDB 설정
LANG = "pt-BR"
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"

# 🔑 TMDB API Key (V3)
API_KEY = "56f4a3bce0512cdc20171a83153c25d6"

# 🔑 YouTube API
YOUTUBE_API_KEY = "AIzaSyB1-WDPuD1sQX-NDAb2E6QdsTQn-DHFq7Y"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# ===============================
# Blogger 인증
# ===============================
BLOGGER_TOKEN_JSON = "blogger_token.json"
SCOPES = ["https://www.googleapis.com/auth/blogger"]

def get_blogger_service():
    with open(BLOGGER_TOKEN_JSON, "r", encoding="utf-8") as f:
        token_data = json.load(f)
    creds = google.oauth2.credentials.Credentials.from_authorized_user_info(token_data, SCOPES)
    return build("blogger", "v3", credentials=creds)

# ===============================
# TMDB / 공통 유틸
# ===============================
def tmdb_get(path, params=None, api_key=None):
    url = f"{TMDB_V3_BASE}{path}"
    headers = {"Accept": "application/json"}
    if params is None:
        params = {}
    if api_key and "api_key" not in params:
        params["api_key"] = api_key
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def img_url(path, size="w780"):
    return f"{IMG_BASE}/{size}{path}" if path else None

def choose(*options):
    return random.choice(options)

# ===============================
# 🎬 인트로 (7문장)
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

# ===============================
# 리드 문구 (섹션 안내, 확장판)
# ===============================
def make_section_lead(name, title, year, genres_str, cert_label, extras=None):
    extras = extras or {}
    year_txt = f"{year}" if year else ""
    genre_phrase = genres_str if genres_str else "gênero"
    cert_txt = cert_label or "classificação desconhecida"
    who = ", ".join((extras.get("cast_top") or [])[:3]) if extras else ""
    runtime_min = extras.get("runtime_min") if extras else None
    runtime_txt = f"{runtime_min} minutos" if runtime_min else ""

    if name == "Sinopse":
        base = [
            choose(
                f"A seguir, apresentamos a sinopse de <b>{title}</b>{' ('+year_txt+')' if year_txt else ''}, cuidadosamente preparada sem spoilers, para que você possa sentir o clima geral da obra antes de assistir.",
                f"Para quem vai assistir, aqui vai uma visão geral de <b>{title}</b>{' ('+year_txt+')' if year_txt else ''}, destacando os pontos principais da trama e preparando sua expectativa de forma equilibrada.",
                f"Resumimos a história de <b>{title}</b> de maneira clara e envolvente, evitando revelar reviravoltas importantes, mas transmitindo o tom emocional do filme.",
                f"Nesta seção você encontrará uma introdução objetiva sobre <b>{title}</b>, ajudando a entender a proposta da obra e seu posicionamento dentro do gênero {genre_phrase}.",
                f"A sinopse de <b>{title}</b> serve como um convite para mergulhar no enredo, oferecendo apenas o suficiente para despertar curiosidade sem comprometer a surpresa."
            ),
            choose(
                f"O tom acompanha o melhor do {genre_phrase}, ora mais contido, ora mais intenso, mas sempre envolvente, criando uma experiência que mantém a atenção do público.",
                "A narrativa busca equilíbrio entre emoção e ritmo, mantendo o espectador conectado ao longo de toda a projeção e criando momentos memoráveis.",
                "A linguagem visual e a trilha sonora desempenham papel importante, reforçando o clima da história e ajudando a transmitir a essência do filme.",
                "A atmosfera do filme foi construída com cuidado, utilizando o melhor do gênero para envolver o público em cada cena e diálogo.",
                "Combinando direção de arte, diálogos e trilha, a narrativa mostra consistência que mantém o público imerso até o final."
            ),
            choose(
                f"A classificação etária é {cert_txt}, o que auxilia o espectador a decidir o momento adequado para assistir sem preocupações.",
                "Sem indicação restritiva, o filme pode ser apreciado por diferentes públicos, tornando-se uma experiência inclusiva.",
                "Vale ressaltar que a classificação é apenas um guia; a experiência completa deve ser vivida pessoalmente.",
                "Independentemente da classificação, a obra convida o espectador a refletir e sentir cada detalhe da história.",
                "A classificação indica a faixa recomendada, mas a verdadeira intensidade do filme só pode ser avaliada ao assistir."
            ),
            choose(
                "Confira abaixo um resumo detalhado antes de seguir para os pontos centrais da análise.",
                "Vamos ao panorama geral da história, que vai preparar o terreno para as próximas seções.",
                "Com essa visão inicial, você terá base suficiente para compreender melhor os destaques apresentados a seguir.",
                "Este resumo funciona como um guia introdutório, antes de explorarmos aspectos técnicos e artísticos mais profundos.",
                "Agora, acompanhe esta introdução curta e direta, que antecede uma análise mais completa do filme."
            )
        ]

    elif name == "Elenco":
        base = [
            choose(
                f"O elenco é de respeito{f', com {who}' if who else ''}, reunindo talentos capazes de dar vida e profundidade aos personagens principais e secundários.",
                "A combinação de atores escolhidos funciona de forma harmoniosa, sustentando a força narrativa e transmitindo autenticidade em cada cena.",
                "As performances se destacam pela naturalidade e pelo comprometimento, tornando cada personagem memorável e essencial à trama.",
                "Este é um elenco diversificado que adiciona camadas à narrativa, trazendo diferentes estilos de interpretação que enriquecem a experiência.",
                "Além de nomes conhecidos, novos talentos aparecem em destaque, reforçando a qualidade e a originalidade do filme."
            ),
            choose(
                "A seguir, listamos os principais nomes e seus papéis, destacando como cada ator contribui para a construção da história.",
                "Veja abaixo os destaques do elenco e as personagens correspondentes, para que você conheça melhor os rostos por trás da trama.",
                "Os nomes apresentados a seguir ajudam a compor a força dramática da produção, cada um desempenhando um papel marcante.",
                "Ao explorar o elenco, é possível perceber como as diferentes atuações se complementam e elevam o impacto da obra.",
                "Conhecer os principais atores ajuda a compreender melhor o tom do filme e o tipo de performance que o espectador pode esperar."
            )
        ]

    elif name == "Fotos":
        base = [
            choose(
                "As imagens a seguir ajudam a sentir a atmosfera do filme antes mesmo de apertar o play, oferecendo um vislumbre da estética e da fotografia escolhida.",
                "As fotos revelam escolhas de fotografia e direção de arte que valem atenção, destacando cores, cenários e enquadramentos marcantes.",
                "Cada imagem transporta o espectador para dentro da narrativa, funcionando quase como uma janela para o universo do filme.",
                "Essas fotos são capazes de transmitir emoção por si só, reforçando a intensidade de determinadas cenas sem a necessidade de palavras.",
                "A seleção de stills mostra a qualidade artística da obra e serve como complemento à sinopse e ao elenco apresentados anteriormente."
            ),
            choose(
                "Observe atentamente a composição, o jogo de luz e sombra e como todos os elementos reforçam o tom da história de forma coerente.",
                "Cada still captura um instante que diz muito sobre o universo do filme, ajudando o espectador a imaginar a experiência completa.",
                "A iluminação, a direção de arte e os detalhes de cenário se destacam em cada imagem, mostrando o cuidado estético da produção.",
                "As fotos também permitem notar detalhes que podem passar despercebidos durante a exibição, enriquecendo ainda mais a análise.",
                "Esse conjunto de imagens ajuda a entender a atmosfera criada e complementa a narrativa visual proposta pelo diretor."
            )
        ]

    else:
        base = [""]

    return " ".join(base)



# ===============================
# TMDB 보조 함수들
# ===============================
def get_movie_release_cert(movie_id):
    try:
        data = tmdb_get(f"/movie/{movie_id}/release_dates", api_key=API_KEY)
        for r in data.get("results", []):
            if r.get("iso_3166_1") == "BR" and r.get("release_dates"):
                return r["release_dates"][0].get("certification") or None
    except:
        return None
    return None

def get_movie_videos_all(movie_id):
    try:
        data = tmdb_get(f"/movie/{movie_id}/videos", api_key=API_KEY)
        return data.get("results", [])
    except:
        return []

def get_youtube_trailers(title, year=None, max_results=2):
    if not YOUTUBE_API_KEY:
        return []
    q = f"{title} trailer"
    if year: q += f" {year}"
    params = {"part":"snippet","q":q,"type":"video","key":YOUTUBE_API_KEY,"maxResults":max_results}
    try:
        r = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=20)
        r.raise_for_status()
        items = r.json().get("items", [])
        vids = []
        for it in items:
            vid = it["id"]["videoId"]
            title = it["snippet"]["title"]
            vids.append((title, f"https://www.youtube.com/watch?v={vid}"))
        return vids
    except:
        return []

def get_movie_recommendations(movie_id, max_results=4):
    try:
        data = tmdb_get(f"/movie/{movie_id}/recommendations", api_key=API_KEY)
        results = data.get("results", [])
        return results[:max_results]
    except:
        return []

def make_hashtags_from_title(title, year, genres):
    tags = []
    if year: tags.append(f"#{year}")
    if genres: tags.extend([f"#{g}" for g in genres.split(",")[:3]])
    tags.append(f"#{title.replace(' ','')}")
    return " ".join(tags)

# ===============================
# HTML 빌더
# ===============================
def build_html(post, cast_count=10, stills_count=8):
    title = post.get("title") or post.get("original_title")
    year = (post.get("release_date") or "")[:4]
    genres = ", ".join([g["name"] for g in post.get("genres", [])]) if post.get("genres") else ""
    runtime = post.get("runtime") or 0
    cert = get_movie_release_cert(post["id"])
    directors = [c["name"] for c in post.get("credits", {}).get("crew", []) if c.get("job")=="Director"]
    cast_list = [c for c in post.get("credits", {}).get("cast", [])][:cast_count]
    cast_top = [c.get("name") for c in cast_list]

    html_parts = []

    # 인트로
    html_parts.append(f"<p>{make_intro_6(title, year, genres, directors, cast_top, cert, runtime, [title])}</p>")

    # Sinopse
    overview = post.get("overview") or "Sinopse não disponível."
    html_parts.append(f"<h2>Sinopse</h2><p>{make_section_lead('Sinopse', title, year, genres, cert)}</p><p>{overview}</p>")

    # Elenco
    html_parts.append("<h2>Elenco</h2>")
    html_parts.append(f"<p>{make_section_lead('Elenco', title, year, genres, cert, {'cast_top':cast_top})}</p>")
    html_parts.append("<ul>")
    for c in cast_list:
        html_parts.append(f"<li>{c.get('name')} como {c.get('character')}</li>")
    html_parts.append("</ul>")

    # Fotos
    stills = post.get("images", {}).get("backdrops", [])[:stills_count]
    if stills:
        html_parts.append("<h2>Fotos</h2>")
        html_parts.append(f"<p>{make_section_lead('Fotos', title, year, genres, cert)}</p>")
        for s in stills:
            u = img_url(s.get("file_path"), "w500")
            if u: html_parts.append(f'<img src="{u}" alt="still">')

    # Avaliação
    vote_avg = post.get("vote_average")
    vote_cnt = post.get("vote_count")
    popularity = post.get("popularity")
    html_parts.append("<h2>Avaliação & Popularidade</h2>")
    html_parts.append(f"<p>Nota média: {vote_avg} (com {vote_cnt} votos)</p>")
    html_parts.append(f"<p>Popularidade: {popularity}</p>")

    # Trailer
    html_parts.append("<h2>Trailer</h2>")
    vids = get_movie_videos_all(post["id"])
    yt = [v for v in vids if v.get("site")=="YouTube" and v.get("type")=="Trailer"]
    if yt:
        vid = yt[0]["key"]
        html_parts.append(f'<iframe width="560" height="315" src="https://www.youtube.com/embed/{vid}" frameborder="0" allowfullscreen></iframe>')
    else:
        alts = get_youtube_trailers(title, year)
        if alts:
            html_parts.append(f'<a href="{alts[0][1]}" target="_blank">{alts[0][0]}</a>')
        else:
            html_parts.append("<p>Trailer não disponível.</p>")

    # Recomendados
    recs = get_movie_recommendations(post["id"])
    if recs:
        html_parts.append("<h2>Filmes recomendados</h2><ul>")
        for r in recs:
            html_parts.append(f"<li>{r.get('title')} ({(r.get('release_date') or '')[:4]})</li>")
        html_parts.append("</ul>")

    # Outro
    html_parts.append(f"<p>{make_outro_6(title, year, genres, directors, [title])}</p>")

    # Hashtags
    html_parts.append(f"<p>{make_hashtags_from_title(title, year, genres)}</p>")

    return "\n".join(html_parts)

# ===============================
# 메인 실행
# ===============================
def main_once():
    ws = get_sheet()
    service = get_blogger_service()
    rows = ws.get_all_values()
    target_row, movie_id = None, None
    for idx, row in enumerate(rows[1:], start=2):
        done_val = row[7].strip() if len(row) > 7 else ""
        movie_raw = row[1].strip() if len(row) > 1 else ""
        if done_val == "완": continue
        if not movie_raw.isdigit(): continue
        target_row, movie_id = idx, int(movie_raw)
        break
    if not movie_id:
        print("📌 처리할 행이 없습니다.")
        return False

    post = tmdb_get(f"/movie/{movie_id}", params={"language": LANG, "append_to_response": "credits,images"}, api_key=API_KEY)
    title = post.get("title") or post.get("original_title") or f"movie_{movie_id}"
    year = (post.get("release_date") or "")[:4]
    blog_title = f"Filme {title} ({year}) sinopse elenco trailer"
    html_out = build_html(post, cast_count=CAST_COUNT, stills_count=STILLS_COUNT)

    res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=["Filme", year] if year else ["Filme"])
    print(f"✅ 발행 완료: {res.get('url','(URL 미확인)')}")
    ws.update_cell(target_row, 8, "완")
    print(f"✅ 완료 표시 (행 {target_row}, H열)")
    return True

if __name__ == "__main__":
    for i in range(POST_COUNT):
        print(f"\n🚀 {i+1}/{POST_COUNT} 번째 포스팅 시작")
        ok = main_once()
        if not ok: break
        if i < POST_COUNT-1 and POST_DELAY_MIN>0:
            time.sleep(POST_DELAY_MIN*60)
