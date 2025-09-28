#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")
import urllib.parse
# =============== Imports ===============
import os, re, json, random, requests, traceback, pickle, glob, textwrap, time
from bs4 import BeautifulSoup

# Google Sheets / Drive / Blogger
import gspread
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# OpenAI (opcional)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# PIL (para miniaturas)
from PIL import Image, ImageDraw, ImageFont

# =============== Variables de entorno y configuración básica ===============
SHEET_ID = os.getenv("SHEET_ID", "1SeQogbinIrDTMKjWhGgWPEQq8xv6ARv5n3I-2BsMrSc")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "YOUR_DRIVE_FOLDER_ID")

# ID / URL del blog (versión México)
BLOG_ID = "8582128276301125850"
BLOG_URL = "https://appmx.appsos.kr/"

# Google Custom Search (opcional)
GCS_API_KEY = os.getenv("GCS_API_KEY", "").strip()
GCS_CX = os.getenv("GCS_CX", "").strip()

# OpenAI API Key (opcional)
OPENAI_API_KEY = ""
if os.path.exists("openai.json"):
    try:
        with open("openai.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            OPENAI_API_KEY = data.get("api_key", "").strip()
    except Exception:
        OPENAI_API_KEY = ""
if not OPENAI_API_KEY:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None

# =============== Google Sheets autenticación (sheet7) ===============
# =============== Google Sheets autenticación (sheet8) ===============
def get_sheet8():
    service_account_file = "sheetapi.json"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = SA_Credentials.from_service_account_file(service_account_file, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws8 = sh.worksheet("sheet8")
    except Exception:
        ws8 = sh.get_worksheet(7)  # 시트8은 인덱스 7 (0부터 시작)
    return ws8

ws8 = get_sheet8()


# =============== Función para generar títulos ===============
def make_post_title(keyword: str) -> str:
    patrones = [
        f"Aplicaciones de {keyword} para iPhone — las mejores opciones",
        f"{keyword} en iOS: aplicaciones recomendadas para ti",
        f"Top aplicaciones de {keyword} para usuarios de iPhone",
        f"{keyword} en iPhone: apps imprescindibles",
        f"Aplicaciones recomendadas de {keyword} para iPhone"
    ]
    return random.choice(patrones)

# =============== Google Drive autenticación ===============
def get_drive_service():
    token_path = "drive_token_2nd.pickle"
    if not os.path.exists(token_path):
        raise RuntimeError("drive_token_2nd.pickle no existe — se necesita el token de usuario de Drive API.")
    with open(token_path, "rb") as f:
        creds = pickle.load(f)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "wb") as f:
            pickle.dump(creds, f)
    return build("drive", "v3", credentials=creds)

# =============== Blogger autenticación ===============
def get_blogger_service():
    if not os.path.exists("blogger_token.json"):
        raise RuntimeError("blogger_token.json no existe — se necesita autenticación de Blogger.")
    with open("blogger_token.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    creds = UserCredentials.from_authorized_user_info(
        data, ["https://www.googleapis.com/auth/blogger"]
    )
    return build("blogger", "v3", credentials=creds)

blog_handler = get_blogger_service()

# =============== Etiquetas del post ===============
def make_post_labels(sheet_row: list) -> list:
    label_val = sheet_row[1].strip() if len(sheet_row) > 1 and sheet_row[1] else ""
    labels = ["Aplicaciones", "iPhone"]
    if label_val:
        labels.append(label_val)
    return labels

# =============== Búsqueda en App Store (iTunes Search API) ===============
def search_app_store_ids(keyword, limit=20, country="mx", eng_keyword=""):
    import requests, urllib.parse
    def fetch(term):
        url = f"https://itunes.apple.com/search?term={urllib.parse.quote(term)}&country={country}&entity=software&limit={limit}"
        try:
            res = requests.get(url, timeout=12)
            res.raise_for_status()
            data = res.json()
            return [{"id": str(app["trackId"]), "name": app["trackName"]}
                    for app in data.get("results", []) if "trackId" in app]
        except Exception as e:
            print("[Error iTunes API]", e)
            return []

    apps = fetch(keyword)
    if len(apps) < 7:
        apps += fetch(f"{keyword} app")
    if len(apps) < 7:
        apps += fetch(f"{keyword} aplicación")
    if len(apps) < 7 and eng_keyword:
        apps += fetch(eng_keyword)

    seen, uniq = set(), []
    for a in apps:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return uniq

# =============== Detalles de la app ===============
def fetch_app_detail(app_id: str, country="mx"):
    import html
    url = f"https://apps.apple.com/{country}/app/id{app_id}"
    name = f"Aplicativo {app_id}"
    desc_html, images = "", []

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.encoding = "utf-8"
        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")

        # Nombre de la app
        h1 = soup.find("h1")
        if h1:
            name = html.unescape(h1.get_text(strip=True))
        else:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                name = html.unescape(og_title["content"])

        # Descripción
        desc_div = soup.find("div", class_=re.compile(r"(section__description|description)"))
        if desc_div:
            ps = desc_div.find_all("p")
            if ps:
                desc_html = "".join(
                    f"<p data-ke-size='size18'>{html.unescape(p.get_text(strip=True))}</p>"
                    for p in ps if p.get_text(strip=True)
                )

        if not desc_html:
            meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
            if meta_desc and meta_desc.get("content"):
                desc_html = f"<p data-ke-size='size18'>{html.unescape(meta_desc['content'].strip())}</p>"

        # Capturas de pantalla (solo índices impares)
        images = []
        screenshot_div = soup.find("div", class_="we-screenshot-viewer__screenshots")
        if screenshot_div:
            sources = screenshot_div.find_all("source")
            for idx, src in enumerate(sources, start=1):
                if len(images) >= 4:
                    break
                if idx % 2 == 1:
                    srcset = src.get("srcset", "")
                    if srcset:
                        img_url = srcset.split(" ")[0]
                        if img_url.startswith("http"):
                            images.append(img_url)

        # fallback
        if not images:
            for img in soup.find_all("img"):
                src = img.get("src") or ""
                if "mzstatic.com" in src and src.startswith("http"):
                    images.append(src)
            images = images[:4]

        return {
            "url": url,
            "name": name,
            "desc_html": desc_html,
            "images": images
        }
    except Exception as e:
        print(f"[Error al obtener detalles] id={app_id}, error={e}")
        return {"url": url, "name": name, "desc_html": "", "images": []}


# =============== Reescribir descripción (OpenAI, fallback) ===============
def rewrite_app_description(original_html: str, app_name: str, keyword: str) -> str:
    from bs4 import BeautifulSoup
    plain = BeautifulSoup(original_html or "", "html.parser").get_text(" ", strip=True)
    if not client:
        return f"<p data-ke-size='size18'>{plain or (app_name + ' Descripción')}</p>"
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Genera un texto promocional natural para un blog en español de México. Coloca los párrafos dentro de <p data-ke-size='size18'>."},
                {"role": "user", "content": plain}
            ],
            temperature=0.7,
            max_tokens=600,
        )
        text = resp.choices[0].message.content.strip()
        if "<p" not in text:
            text = f"<p data-ke-size='size18'>{text}</p>"
        return text
    except Exception as e:
        print("[OpenAI fallo]", e)
        return f"<p data-ke-size='size18'>{plain or (app_name + ' Descripción')}</p>"


# =============== Registro de miniatura (columna H) ===============
def log_thumb_step(ws, row_idx, message):
    try:
        prev = ws.cell(row_idx, 8).value or ""   # columna H
        new_val = prev + (";" if prev else "") + message
        ws.update_cell(row_idx, 8, new_val)
    except Exception as e:
        print("[Fallo de registro]", e)

# =============== Selección aleatoria de fondo ===============
def pick_random_background() -> str:
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        files.extend(glob.glob(os.path.join("assets", "backgrounds", ext)))
    return random.choice(files) if files else ""

# =============== Generar miniatura ===============
def make_thumb(save_path: str, var_title: str):
    try:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        # Selección aleatoria de fondo
        bg_path = pick_random_background()
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((500, 500))
        else:
            bg = Image.new("RGBA", (500, 500), (255, 255, 255, 255))

        # Fuente (PlusJakartaSans, funciona con español)
        try:
            font = ImageFont.truetype(os.path.join("assets", "fonts", "PlusJakartaSans-SemiBoldItalic.ttf"), 48)
        except Exception:
            font = ImageFont.load_default()

        # Crear lienzo
        canvas = Image.new("RGBA", (500, 500), (255, 255, 255, 0))
        canvas.paste(bg, (0, 0))

        # Fondo de texto
        rectangle = Image.new("RGBA", (500, 250), (0, 0, 0, 200))
        canvas.paste(rectangle, (0, 125), rectangle)

        # Dibujar texto
        draw = ImageDraw.Draw(canvas)

        def wrap_text(text, font, max_width):
            lines, line = [], ""
            for ch in text:
                test_line = line + ch
                text_width = draw.textlength(test_line, font=font)
                if text_width <= max_width:
                    line = test_line
                else:
                    lines.append(line)
                    line = ch
            if line:
                lines.append(line)
            return lines

        var_title_wrap = wrap_text(var_title, font, max_width=460)

        bbox = font.getbbox("A")  # referencia de altura
        line_height = (bbox[3] - bbox[1]) + 12
        total_text_height = len(var_title_wrap) * line_height
        y = 500 / 2 - total_text_height / 2

        for line in var_title_wrap:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            x = (500 - text_width) / 2
            draw.text((x, y), line, "#FFEECB", font=font)
            y += line_height

        canvas = canvas.resize((400, 400))
        canvas.save(save_path, "PNG")
        return True
    except Exception as e:
        print(f"Error: fallo al generar miniatura: {e}")
        return False

# =============== Subir a Google Drive → devolver URL pública ===============
def upload_to_drive(file_path, file_name):
    try:
        drive_service = get_drive_service()
        folder_id = DRIVE_FOLDER_ID

        if not folder_id or folder_id == "YOUR_DRIVE_FOLDER_ID":
            query = "mimeType='application/vnd.google-apps.folder' and name='blogger' and trashed=false"
            results = drive_service.files().list(q=query, fields="files(id, name)").execute()
            items = results.get("files", [])
            if items:
                folder_id = items[0]["id"]
            else:
                folder_metadata = {"name": "blogger", "mimeType": "application/vnd.google-apps.folder"}
                folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
                folder_id = folder.get("id")

        file_metadata = {"name": file_name, "parents": [folder_id]}
        media = MediaFileUpload(file_path, mimetype="image/png", resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()

        drive_service.permissions().create(
            fileId=file["id"],
            body={"role": "reader", "type": "anyone", "allowFileDiscovery": False}
        ).execute()

        return f"https://lh3.googleusercontent.com/d/{file['id']}"
    except Exception as e:
        print(f"Error: fallo al subir a Google Drive: {e}")
        return ""

# =============== Miniatura + registro + subida ===============
def make_thumb_with_logging(ws, row_idx, save_path, title):
    try:
        log_thumb_step(ws, row_idx, "Inicio miniatura")
        ok = make_thumb(save_path, title)
        if ok:
            log_thumb_step(ws, row_idx, "Miniatura completada")
            url = upload_to_drive(save_path, os.path.basename(save_path))
            if url:
                log_thumb_step(ws, row_idx, f"Subida completada → {url}")
                return url
            else:
                log_thumb_step(ws, row_idx, "Fallo en la subida")
                return ""
        else:
            log_thumb_step(ws, row_idx, "Fallo en la miniatura")
            return ""
    except Exception as e:
        log_thumb_step(ws, row_idx, f"Error:{e}")
        return ""

# =============== Bloque CSS (solo una vez) ===============
def build_css_block() -> str:
    return """
<style>
.img-group {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
}
.img-wrap {
  flex: 0 0 48%;
  margin: 1%;
}
.img-wrap img {
  width: 100%;
  height: auto;
  border-radius: 10px;
}
@media (max-width: 768px) {
  .img-wrap {
    flex: 0 0 100%;
    margin: 5px 0;
  }
}
</style>
"""
# =============== Bloque de introducción ===============
def build_intro_block(title: str, keyword: str) -> str:
    intro_groups = [
        [
            f"Hoy en día, el smartphone no es solo un medio de comunicación, sino una herramienta esencial para la vida moderna.",
            f"Con un dispositivo en la palma de la mano, es posible aprovechar funciones como 『{keyword}』 de manera práctica y rápida.",
            f"En el día a día, las aplicaciones de 『{keyword}』 se han vuelto indispensables para brindar más comodidad.",
            f"Temas como 『{title}』 despiertan el interés de muchos usuarios de tecnología.",
            f"Con la evolución de los smartphones, el uso de aplicaciones relacionadas con 『{keyword}』 crece cada vez más.",
            f"Cualquier persona puede disfrutar de 『{keyword}』 fácilmente a través de aplicaciones dedicadas."
        ],
        [
            f"Existe una gran variedad de apps disponibles y las opciones de 『{keyword}』 aumentan cada día.",
            f"La búsqueda de 『{title}』 demuestra que este tema está en tendencia.",
            f"Trabajo, estudio, ocio e incluso 『{keyword}』 pueden optimizarse mediante aplicaciones.",
            f"Las apps ayudan a ahorrar tiempo y hacen la vida más eficiente.",
            f"Las aplicaciones de 『{keyword}』 ofrecen nuevas experiencias y comodidad a los usuarios.",
            f"Diariamente surgen nuevas apps de 『{keyword}』, aumentando las posibilidades de elección."
        ],
        [
            f"Hay desde aplicaciones de productividad hasta opciones de entretenimiento relacionadas con 『{keyword}』.",
            f"『{title}』 es una de las categorías más populares entre los usuarios.",
            f"Al igual que juegos y streaming, las apps de 『{keyword}』 hacen que el tiempo libre sea más agradable.",
            f"Compras, finanzas y transporte ya dependen de apps, y 『{keyword}』 sigue la misma línea.",
            f"Muchas aplicaciones permiten gestionar contenidos como fotos, videos y 『{keyword}』 de manera sencilla.",
            f"Las apps de 『{keyword}』 destacan cada vez más junto a las de comunicación."
        ],
        [
            f"De esta manera, las aplicaciones de 『{keyword}』 van más allá de su función básica y transforman la vida del usuario.",
            f"Con 『{title}』 se puede mejorar aún más la calidad del día a día.",
            f"Cuando sea necesario, basta abrir la app de 『{keyword}』 para acceder al recurso deseado.",
            f"Más que practicidad, las apps de 『{keyword}』 también brindan nuevas experiencias.",
            f"Muchos ya utilizan aplicaciones de 『{keyword}』 para llevar una rutina más inteligente.",
            f"Una sola app de 『{keyword}』 puede cambiar totalmente el estilo de vida."
        ]
    ]

    intro_sentences = []
    for group in intro_groups:
        intro_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    intro_text = " ".join(intro_sentences)

    first = f'''
<div id="jm">&nbsp;</div>
<p data-ke-size="size18">{intro_text}</p>
<span><!--more--></span>
<p data-ke-size="size18">&nbsp;</p>
'''
    return first

# =============== Bloque de cierre ===============
def build_ending_block(title: str, keyword: str) -> str:
    end_groups = [
        [
            f"Esperamos que las aplicaciones relacionadas con 『{title}』 presentadas aquí mejoren tu vida digital.",
            f"Este artículo reunió apps de 『{title}』 que seguramente serán útiles en tu día a día.",
            f"Las opciones de 『{title}』 mostradas pueden ayudarte a tomar decisiones más inteligentes.",
            f"Si las aplicaciones de 『{title}』 se vuelven indispensables para ti, estaremos muy contentos.",
            f"Para quienes se interesan en 『{title}』, este resumen puede ser una lectura valiosa.",
            f"Al conocer diversas aplicaciones de 『{keyword}』, tu uso del smartphone será más completo."
        ],
        [
            f"Presentamos las características y ventajas de cada app para facilitar la elección de 『{keyword}』.",
            f"Al comparar fortalezas y debilidades, será más fácil decidir qué 『{title}』 descargar.",
            f"Con base en este resumen, podrás encontrar la app de 『{keyword}』 ideal.",
            f"La información reunida aquí es práctica y puede servir como guía de referencia.",
            f"A la hora de elegir una app de 『{keyword}』, este artículo será un aliado confiable.",
            f"La comparación de varias apps ayuda a tomar decisiones más conscientes."
        ],
        [
            "Seguiremos compartiendo novedades e información útil sobre aplicaciones.",
            f"En el futuro, más contenidos sobre 『{keyword}』 y apps recomendadas se publicarán aquí.",
            "Las opiniones de los lectores son importantes para ofrecer artículos más completos.",
            "Pronto presentaremos nuevas apps y funcionalidades destacadas.",
            "Continuaremos actualizando con información práctica que pueda ayudar en el día a día.",
            f"Temas populares como 『{title}』 seguirán siendo explorados en nuestro blog."
        ],
        [
            "Los comentarios y los likes son un gran incentivo — ¡participa con libertad!",
            "Si tienes dudas o sugerencias, compártelas en los comentarios para que podamos mejorar.",
            "Tu feedback es esencial para crear contenidos cada vez más útiles.",
            "Apoya con un like o comentario para seguir ofreciendo información de calidad.",
            "Si hay alguna app de tu interés, déjanos tu sugerencia en los comentarios.",
            f"Tu opinión sobre 『{keyword}』 es muy bienvenida — ¡compártela en los comentarios!"
        ]
    ]

    end_sentences = []
    for group in end_groups:
        end_sentences.extend(random.sample(group, k=random.choice([1, 2])))

    end_text = " ".join(end_sentences)

    last = f"""
<p data-ke-size="size18">&nbsp;</p>
<div style="margin:40px 0px 20px 0px;">
<p data-ke-size="size18">{end_text}</p>
<p data-ke-size="size18">&nbsp;</p>
</div>
"""
    return last
# ================================
# Caja de artículos relacionados (RSS aleatorio 4, versión México)
# ================================
def get_related_posts(blog_id, count=4):
    import feedparser, random
    rss_url = f"https://www.blogger.com/feeds/{blog_id}/posts/default?alt=rss"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return ""

    entries = random.sample(feed.entries, min(count, len(feed.entries)))

    html_box = """
<div style="background: rgb(239, 237, 233); border-radius: 8px; border: 2px dashed rgb(167, 162, 151); 
            box-shadow: rgb(239, 237, 233) 0px 0px 0px 10px; color: #565656; font-weight: bold; 
            margin: 2em 10px; padding: 2em;">
  <p data-ke-size="size16" 
     style="border-bottom: 1px solid rgb(85, 85, 85); color: #555555; font-size: 16px; 
            margin-bottom: 15px; padding-bottom: 5px;">♡♥ Artículos Recomendados</p>
"""

    for entry in entries:
        title = entry.title
        link = entry.link
        html_box += f'<a href="{link}" style="color: #555555; font-weight: normal;">● {title}</a><br>\n'

    html_box += "</div>\n"
    return html_box


# =============== Selección de fila/keyword/label ===============
def pick_target_row(ws):
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], start=2):  # desde la fila 2
        a = row[0].strip() if len(row) > 0 and row[0] else ""  # A = keyword
        d = row[4].strip() if len(row) > 4 and row[4] else ""  # E = flag completado
        if a and d != "OK":
            return i, row
    return None, None


# =============== Registro acumulativo en columna H ===============
def sheet_append_log(ws, row_idx, message, tries=3, delay=2):
    """Concatena timestamp + mensaje en columna H (8)"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    line = f"[{ts}] {message}"
    for t in range(1, tries+1):
        try:
            prev = ws.cell(row_idx, 8).value or ""   # columna H
            new_val = (prev + (";" if prev else "") + line)
            ws.update_cell(row_idx, 8, new_val)
            print(f"[LOG:H{row_idx}] {line}")
            return True
        except Exception as e:
            print(f"[WARN] Reintento registro {t}/{tries}: {e}")
            time.sleep(delay * t)
    print(f"[FAIL] Registro fallido: {line}")
    return False


# =============== Ejecución principal ===============
if __name__ == "__main__":
    try:
        # 1) Obtener fila/registro objetivo
        target_row, row = pick_target_row(ws8)
        if not target_row or not row:
            sheet_append_log(ws8, 2, "No hay keywords para procesar (columna A)")
            raise SystemExit(0)

        keyword = row[0].strip()   # columna A
        label_val = row[1].strip() if len(row) > 1 else ""  # columna B

        sheet_append_log(ws8, target_row, f"Fila objetivo={target_row}, Keyword='{keyword}', Label='{label_val}'")

        # 2) Generar título
        title = make_post_title(keyword)
        sheet_append_log(ws8, target_row, f"Título='{title}'")

        # 3) Generar y subir thumbnail
        thumb_dir = "thumbnails"
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{keyword}.png")
        sheet_append_log(ws8, target_row, "Inicio generación thumbnail")
        thumb_url = make_thumb_with_logging(ws8, target_row, thumb_path, title)
        sheet_append_log(ws8, target_row, f"Resultado thumbnail: {thumb_url or 'Fallido'}")

        # 4) Buscar IDs de apps
        sheet_append_log(ws8, target_row, "Inicio búsqueda de IDs de apps")
        eng_keyword = row[3].strip() if len(row) > 3 else ""  # columna D
        apps = search_app_store_ids(keyword, limit=20, eng_keyword=eng_keyword)

        if not apps:
            sheet_append_log(ws8, target_row, "No se encontraron apps → salir")
            ws8.update_cell(target_row, 5, "OK")      # columna E
            ws8.update_cell(target_row, 7, "")        # columna G
            sheet_append_log(ws8, target_row, "Registro hoja completado: E='OK', G='' (sin resultados)")
            raise SystemExit(0)

        if len(apps) < 3:
            sheet_append_log(ws8, target_row, "Menos de 3 apps → marcar como completado")
            ws8.update_cell(target_row, 5, "OK")
            ws8.update_cell(target_row, 7, "")
            sheet_append_log(ws8, target_row, "Registro hoja completado: E='OK', G='' (pocas apps)")
            raise SystemExit(0)

        sheet_append_log(ws8, target_row, f"IDs de apps={[(a['id'], a['name']) for a in apps]}")

        # 5) Introducción
        html_full = build_css_block()
        html_full += build_intro_block(title, keyword)
        html_full += """
        <div class="mbtTOC"><button>Contenido</button>
        <ul data-ke-list-type="disc" id="mbtTOC" style="list-style-type: disc;"></ul>
        </div>
        <p>&nbsp;</p>
        """
        sheet_append_log(ws8, target_row, "Introducción generada")

        # 6) Insertar thumbnail en el cuerpo
        if thumb_url:
            html_full += f"""
<p style="text-align:center;">
  <img src="{thumb_url}" alt="{keyword} thumbnail" style="max-width:100%; height:auto; border-radius:10px;">
</p><br /><br />
"""
            sheet_append_log(ws8, target_row, "Thumbnail insertado en el cuerpo")
        else:
            sheet_append_log(ws8, target_row, "No hay thumbnail")

        # 7) Hashtags
        tag_items = title.split()
        tag_str = " ".join([f"#{t}" for t in tag_items]) + " #AppStore"
        sheet_append_log(ws8, target_row, f"Hashtags='{tag_str}'")

        # 8) Recolectar detalles de apps → armar contenido
        for j, app in enumerate(apps, 1):
            if j > 7:  # máximo 7 apps
                break
            try:
                sheet_append_log(ws8, target_row, f"[{j}] Inicio recolección app id={app['id']}")
                detail = fetch_app_detail(app["id"], country="mx")
                app_url = detail["url"]
                app_name = detail["name"]
                src_html = detail["desc_html"]
                images = detail["images"]

                desc_html = rewrite_app_description(src_html, app_name, keyword)
                sheet_append_log(ws8, target_row, f"[{j}] {app_name} descripción reescrita")

                img_group_html = "".join(
                    f'<div class="img-wrap"><img src="{img_url}" alt="{app_name}_{cc}"></div>'
                    for cc, img_url in enumerate(images, 1)
                )

                section_html = f"""
                <h2 data-ke-size="size26">{j}. {app_name} — Presentación de App</h2>
                <br />
                {desc_html}
                <p data-ke-size="size18"><b>Pantallas de {app_name}</b></p>
                <div class="img-group">{img_group_html}</div>
                <br />
                <p data-ke-size="size18" style="text-align:center;">
                  <a href="{app_url}" class="myButton">Descargar {app_name}</a>
                </p>
                <br />
                <p data-ke-size="size18">{tag_str}</p>
                <br /><br />
                """

                html_full += section_html
                sheet_append_log(ws8, target_row, f"[{j}] Sección {app_name} completada")
            except Exception as e_each:
                sheet_append_log(ws8, target_row, f"[{j}] Fallo al procesar app: {e_each}")

        # 9) Cierre
        html_full += build_ending_block(title, keyword)
        sheet_append_log(ws8, target_row, "Cierre generado")
        related_box = get_related_posts(BLOG_ID, count=6)
        html_full += related_box
        html_full += "<script>mbtTOC();</script><br /><br />"

        # 10) Subir post
        try:
            labels = make_post_labels(row)
            post_body = {"content": html_full, "title": title, "labels": labels}
            res = blog_handler.posts().insert(blogId=BLOG_ID, body=post_body,
                                              isDraft=False, fetchImages=True).execute()
            post_url = res.get("url", "")
            sheet_append_log(ws8, target_row, f"Subida exitosa: {post_url}")
        except Exception as up_e:
            sheet_append_log(ws8, target_row, f"Fallo al subir: {up_e}")
            raise

        # 11) Registro en hoja
        ws8.update_cell(target_row, 5, "OK")
        ws8.update_cell(target_row, 7, post_url)
        sheet_append_log(ws8, target_row, f"Registro hoja completado: E='OK', G='{post_url}'")

        # 12) Finalizado
        sheet_append_log(ws8, target_row, "Finalización exitosa")

    except SystemExit:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        row_for_err = target_row if 'target_row' in locals() and target_row else 2
        sheet_append_log(ws8, row_for_err, f"Fallo: {e}")
        sheet_append_log(ws8, row_for_err, f"Trace: {tb.splitlines()[-1]}")
        print("Fallo:", e, tb)










