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

import feedparser, json, os, html, textwrap, requests, random, time, re
import xml.etree.ElementTree as ET
from googleapiclient.discovery import build
import gspread
from google.oauth2.service_account import Credentials
import google.oauth2.credentials
import urllib.parse

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
BLOG_BASE = "https://br.appsos.kr"

def blog_search_url(q: str) -> str:
    return f"{BLOG_BASE}/search?q={urllib.parse.quote(q)}"

def blog_label_url(label: str) -> str:
    return f"{BLOG_BASE}/search/label/{urllib.parse.quote(label)}?&max-results=10"

# ===============================
# 🈶 TMDB 설정
LANG = "pt-BR"
CAST_COUNT = 10
STILLS_COUNT = 8
TMDB_V3_BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p"
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
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ TMDB 요청 실패: {e} ({url})")
        return {}

def img_url(path, size="w780"):
    return f"{IMG_BASE}/{size}{path}" if path else None

def choose(*options):
    return random.choice(options)

def post_to_blogger(service, blog_id, title, html_content, labels=None, is_draft=False):
    body = {"kind": "blogger#post", "title": title, "content": html_content}
    if labels:
        body["labels"] = labels
    post = service.posts().insert(blogId=blog_id, body=body, isDraft=is_draft).execute()
    return post

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

    s1 = choose(
        f"Olá, cinéfilos! Hoje vamos mergulhar no universo do filme <b>{title}</b>, {year_txt}, uma obra que merece toda a sua atenção.",
        f"Se você é apaixonado por cinema, vai gostar de conhecer mais sobre <b>{title}</b>, {year_txt}, um título que já conquistou muitos corações.",
        f"Bem-vindo! Hoje o destaque é para <b>{title}</b>, {year_txt}, um longa que desperta emoções intensas e discussões interessantes.",
        f"O cinema nos brinda com várias obras inesquecíveis, e <b>{title}</b>, {year_txt}, é certamente uma delas que vamos explorar juntos."
    )

    s2 = choose(
        f"Este é um filme de {genre_phrase}, que combina emoção e profundidade de maneira envolvente.",
        f"Pertencente ao gênero {genre_phrase}, a produção consegue transmitir sentimentos fortes e momentos inesquecíveis.",
        f"Com características marcantes de {genre_phrase}, o longa prende a atenção do início ao fim.",
        f"Envolvendo-se no gênero {genre_phrase}, a trama se desenrola de forma cativante e instigante."
    )

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

    s5 = choose(
        f"O filme tem duração de {runtime_txt}, o que torna a experiência equilibrada e envolvente.",
        f"Com seus {runtime_txt}, a narrativa consegue manter o ritmo sem se tornar cansativa.",
        f"A duração de {runtime_txt} é ideal para aproveitar cada detalhe da história."
    ) + " " + choose(
        f"A classificação indicativa é {cert_txt}, tornando-o acessível para diferentes públicos.",
        f"Classificado como {cert_txt}, o longa pode ser apreciado por várias faixas etárias.",
        f"A censura é {cert_txt}, o que ajuda o espectador a decidir a melhor ocasião para assistir."
    )

    s6 = choose(
        f"<b>{title}</b> despertou debates e gerou expectativas desde seu lançamento, mostrando sua força cultural.",
        f"Desde sua estreia, <b>{title}</b> chamou a atenção por sua proposta ousada e qualidade técnica.",
        f"O impacto de <b>{title}</b> foi imediato, consolidando-o como um dos grandes destaques do {year_txt}.",
        f"Não é apenas um filme, <b>{title}</b> é uma experiência que permanece viva na memória de quem assiste."
    )

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
# 리드 문구 (섹션 안내)
# ===============================
def make_section_lead(name, title, year, genres_str, cert_label, extras=None):
    extras = extras or {}
    year_txt = f"{year}" if year else ""
    genre_phrase = genres_str if genres_str else "gênero"
    cert_txt = cert_label or "classificação desconhecida"
    who = ", ".join((extras.get("cast_top") or [])[:3]) if extras else ""

    if name == "Sinopse":
        base = [
            choose(
                f"A seguir, apresentamos a sinopse de <b>{title}</b>{' ('+year_txt+')' if year_txt else ''}, cuidadosamente preparada sem spoilers.",
                f"Para quem vai assistir, aqui vai uma visão geral de <b>{title}</b>{' ('+year_txt+')' if year_txt else ''}, destacando os pontos principais.",
                f"Resumimos a história de <b>{title}</b> de maneira clara e envolvente, sem revelar reviravoltas importantes."
            )
        ]
    elif name == "Elenco":
        base = [
            choose(
                f"O elenco é de respeito{f', com {who}' if who else ''}, reunindo talentos que dão vida aos personagens.",
                "A combinação de atores funciona muito bem e sustenta a força da narrativa."
            )
        ]
    elif name == "Fotos":
        base = [
            choose(
                "As imagens a seguir ajudam a sentir a atmosfera do filme antes mesmo de apertar o play.",
                "As fotos revelam escolhas de fotografia e direção de arte que valem atenção."
            )
        ]
    else:
        base = [""]
    return " ".join(base)

# ===============================
# TMDB 보조 함수들
# ===============================
def get_movie_images(movie_id, api_key=None):
    try:
        params = {"include_image_language": "null,en"}
        j = tmdb_get(f"/movie/{movie_id}/images", params=params, api_key=api_key)
        return j.get("backdrops", []), j.get("posters", [])
    except:
        return [], []

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
        return [(it["id"]["videoId"], it["snippet"]["title"]) for it in items]
    except:
        return []

def get_movie_recommendations(movie_id, lang="pt-BR", api_key=None):
    try:
        params = {"language": lang}
        j = tmdb_get(f"/movie/{movie_id}/recommendations", params=params, api_key=api_key)
        return j.get("results", [])
    except:
        return []

def make_hashtags_from_title(title, year=None, genres=None):
    tags = []
    if year: tags.append(f"#{year}")
    if genres: tags.extend([f"#{g.strip()}" for g in genres.split(",") if g.strip()][:3])
    if title: tags.append(f"#{title.replace(' ', '')}")
    return " ".join(tags)

# ===============================
# HTML 빌더
# ===============================
def build_html(post, cast_count=10, stills_count=8):
    esc = html.escape
    title = esc(post.get("title") or post.get("original_title") or "Título indisponível")
    release_date = esc(post.get("release_date") or "")
    year = release_date[:4] if release_date else ""
    runtime = int(post.get("runtime") or 0)
    genres_list = [g.get("name","") for g in (post.get("genres") or []) if g.get("name")]
    genres_str = " · ".join(genres_list)
    tagline = esc(post.get("tagline") or "")
    adult_flag = bool(post.get("adult", False))
    countries = [c.get("name","") for c in (post.get("production_countries") or []) if c.get("name")]
    country_str = ", ".join(countries) if countries else "—"
    backdrop = img_url(post.get("backdrop_path"), "w1280")

    credits = post.get("credits") or {}
    cast = (credits.get("cast") or [])[:cast_count]
    crew = credits.get("crew") or []
    directors = [c for c in crew if c.get("job") == "Director"]
    director_names = [esc(d.get("name","")) for d in directors]
    cast_names = [esc(p.get("name","")) for p in cast]

    backdrops, posters = get_movie_images(post["id"], api_key=API_KEY)
    backdrops = sorted(backdrops, key=lambda b: (b.get("vote_count",0), b.get("vote_average",0)), reverse=True)[:stills_count]

    cert = get_movie_release_cert(post["id"])
    if not cert and adult_flag: cert = "18+"

    base_keywords = [title] + genres_list + director_names[:2] + cast_names[:3]
    if year: base_keywords.append(year)
    if cert: base_keywords.append(cert)
    base_keywords += ["resenha","avaliação","elenco","trailer","fotos","filmes recomendados"]
    seen=set(); keywords=[]
    for k in base_keywords:
        if k and k not in seen: keywords.append(k); seen.add(k)

    intro_6 = make_intro_6(title, year, genres_str, director_names, cast_names, cert, runtime, keywords)

    # === Elenco 테이블 ===
    cast_rows = []
    for p in cast:
        name = esc(p.get("name",""))
        ch = esc(p.get("character",""))
        prof = img_url(p.get("profile_path"), "w185")
        img_tag = f'<img src="{prof}" alt="{name}" style="width:72px;border-radius:8px;">' if prof else ""
        cast_rows.append(f"<tr><td>{img_tag}</td><td><b>{name}</b><br><span style='color:#666'>{ch}</span></td></tr>")
    cast_table = "<table style='width:100%;border:1px solid #ccc;border-collapse:collapse;'>" + "".join(cast_rows or ["<tr><td>Sem elenco.</td></tr>"]) + "</table>"

    # === Stills 갤러리 ===
    still_divs=[]
    for b in backdrops:
        p=img_url(b.get("file_path"),"w780")
        if p: still_divs.append(f"<div style='flex:0 0 49%;margin:0.5%'><img src='{p}' style='width:100%;border-radius:8px;'></div>")
    stills_html="<div style='display:flex;flex-wrap:wrap'>" + "".join(still_divs or ["<div>Sem fotos.</div>"]) + "</div>"

    # === 평가/인기 ===
    vote_avg=float(post.get("vote_average") or 0)
    vote_count=int(post.get("vote_count") or 0)
    popularity=float(post.get("popularity") or 0)
    rating_html=f"""
    <div style="background:#fafafa;border:2px solid #ddd;border-radius:10px;padding:20px;margin:20px 0;text-align:center">
      <b>⭐ Nota média:</b> {vote_avg:.1f}/10<br>
      <b>📊 Votos:</b> {vote_count}<br>
      <b>🔥 Popularidade:</b> {popularity:.1f}
    </div>
    """

    # === Trailer ===
    videos=get_movie_videos_all(post["id"])
    video_html=""
    if videos:
        for v in videos:
            yt_key=v.get("key")
            yt_name=esc(v.get("name") or "Trailer")
            if yt_key: video_html+=f"<p><b>{yt_name}</b></p><iframe width='560' height='315' src='https://www.youtube.com/embed/{yt_key}' frameborder='0' allowfullscreen></iframe><br>"
    else:
        video_html="<p>Trailer não disponível.</p>"

    # === 추천 영화 ===
    recs=get_movie_recommendations(post["id"],lang=LANG,api_key=API_KEY)
    if recs:
        rec_cards=[]
        for r in recs[:6]:
            rtitle=esc(r.get("title") or r.get("original_title") or "")
            poster=img_url(r.get("poster_path"),"w185")
            blog_link=blog_search_url(rtitle)
            rec_cards.append(f"<div style='flex:0 0 32%;text-align:center'><a href='{blog_link}'><img src='{poster}' style='width:100%;border-radius:8px;'></a><br><a href='{blog_link}'>{rtitle}</a></div>")
        recs_html=f"<h2>Filmes recomendados de “{title}”</h2><div style='display:flex;flex-wrap:wrap'>{''.join(rec_cards)}</div>"
    else:
        recs_html="<p>Não há recomendações disponíveis.</p>"

    # === RSS 관련글 박스 ===
    def build_related_block(rss_url,count=5):
        links=[]
        try:
            r=requests.get(rss_url,timeout=10)
            root=ET.fromstring(r.content)
            for item in root.findall(".//item")[:count]:
                link=item.findtext("link") or ""
                t=item.findtext("title") or "Sem título"
                if link: links.append((link,t))
        except: pass
        links_html="".join([f"<a href='{href}'>● {esc(t)}</a><br>" for href,t in links])
        return f"<div style='background:#efede9;border:2px dashed #aaa;padding:15px;margin:20px 0;border-radius:8px'><p><b>♡♥ Leia também</b></p>{links_html}</div>"

    related_block=build_related_block(RELATED_RSS_URL,5)
    outro_6=make_outro_6(title,year,genres_str,director_names,keywords)

    # === 장르 링크 ===
    genre_links=" ".join([f"<a href='{blog_label_url(g)}'>#{g}</a>" for g in genres_list])

    overview=esc(post.get("overview") or "Sinopse não disponível.")
    html_out=f"""
<p>{intro_6}</p><br>
{f"<img src='{backdrop}' style='width:100%;border-radius:12px'>" if backdrop else ""}
{f"<p><i>{tagline}</i></p>" if tagline else ""}

{related_block}

<h2>Sinopse do filme “{title}”</h2>
<p><b>País:</b> {country_str} | <b>Gênero:</b> {genre_links if genre_links else "—"}</p>
<div style="background:#fafafa;border:1px solid #ddd;border-radius:10px;padding:15px;margin:15px 0;line-height:1.6">{overview}</div>

<h2>Elenco de “{title}”</h2>{cast_table}
<h2>Fotos de “{title}”</h2>{stills_html}
<h2>Avaliação & Trailer de “{title}”</h2>{rating_html}{video_html}
{recs_html}
<p>{outro_6}</p>
"""
    return textwrap.dedent(html_out).strip()


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
    blog_title = f"{year} Filme {title} sinopse elenco trailer" if year else f"Filme {title} sinopse elenco trailer"
    html_out = build_html(post, cast_count=CAST_COUNT, stills_count=STILLS_COUNT)

    res = post_to_blogger(service, BLOG_ID, blog_title, html_out, labels=["Filme", year] if year else ["Filme"])
    print(f"✅ 발행 완료: {res.get('url','(URL 미확인)')}")
    ws.update_cell(target_row, 8, "완")
    return True

if __name__ == "__main__":
    for i in range(POST_COUNT):
        print(f"\n🚀 {i+1}/{POST_COUNT} 번째 포스팅 시작")
        ok = main_once()
        if not ok: break
        if i < POST_COUNT-1 and POST_DELAY_MIN>0:
            time.sleep(POST_DELAY_MIN*60)

