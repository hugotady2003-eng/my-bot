"""
Pulse NewsBot — bot d'actu Twitter
Génère des tweets avec image PNG et PDF, envoyés par email.
"""
import feedparser, anthropic, sqlite3, hashlib, json, time, os, smtplib
import urllib.request, urllib.parse, re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════
GMAIL_ADDRESS     = os.environ.get("GMAIL_ADDRESS",     "")
GMAIL_APP_PASS    = os.environ.get("GMAIL_APP_PASS",    "")
EMAIL_TO          = os.environ.get("EMAIL_TO",          "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY   = os.environ.get("YOUTUBE_API_KEY",   "")

SCORE_MINIMUM = 6
MAX_PAR_PASSE = 2

# ═══════════════════════════════════════════════════════════════════════════
# SOURCES RSS
# ═══════════════════════════════════════════════════════════════════════════
RSS_FEEDS = [
    {"url": "https://www.lemonde.fr/rss/une.xml",                     "source": "Le Monde"},
    {"url": "https://www.lemonde.fr/politique/rss/",                  "source": "Le Monde"},
    {"url": "https://www.lefigaro.fr/rss/figaro_actualites.xml",      "source": "Le Figaro"},
    {"url": "https://www.20minutes.fr/feeds/rss/actu",                "source": "20 Minutes"},
    {"url": "https://www.bfmtv.com/rss/news-24-7/",                   "source": "BFMTV"},
    {"url": "https://www.franceinfo.fr/rss/en-direct.rss",            "source": "France Info"},
    {"url": "https://www.publicsenat.fr/rss/articles.rss",            "source": "Public Sénat"},
    {"url": "https://www.cnil.fr/fr/rss.xml",                         "source": "CNIL"},
    {"url": "https://www.numerama.com/feed/",                         "source": "Numerama"},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",            "source": "BBC World"},
    {"url": "https://feeds.reuters.com/reuters/topNews",              "source": "Reuters"},
    {"url": "https://www.theguardian.com/world/rss",                  "source": "The Guardian"},
    {"url": "https://www.lesechos.fr/rss/rss_la_une.xml",             "source": "Les Echos"},
    {"url": "https://www.futura-sciences.com/rss/actualites.xml",     "source": "Futura Sciences"},
    {"url": "https://www.leparisien.fr/faits-divers/rss.xml",         "source": "Le Parisien"},
    {"url": "https://www.lequipe.fr/rss/actu_rss.xml",                "source": "L'Équipe"},
]

# ═══════════════════════════════════════════════════════════════════════════
# DA PULSE — styles, préfixes, labels
# ═══════════════════════════════════════════════════════════════════════════
STYLES = {
    "breaking":      {"color": "#ff6868", "label": "Breaking",       "bar": [(255,32,32),(255,96,48)],     "overlay": (18,3,3)},
    "international": {"color": "#64b5f6", "label": "International",  "bar": [(33,150,243),(0,184,212)],    "overlay": (3,10,22)},
    "politique":     {"color": "#ffd54f", "label": "Politique",      "bar": [(255,193,7),(255,152,0)],     "overlay": (12,10,2)},
    "economie":      {"color": "#69f0ae", "label": "Economie",       "bar": [(0,230,118),(0,191,165)],     "overlay": (2,12,5)},
    "societe":       {"color": "#ce93d8", "label": "Societe",        "bar": [(206,147,216),(156,39,176)],  "overlay": (10,4,20)},
    "histoire":      {"color": "#d4a843", "label": "Histoire",       "bar": [(212,168,67),(160,113,74)],   "overlay": (14,8,2)},
    "insolite":      {"color": "#00e5ff", "label": "Insolite",       "bar": [(0,229,255),(29,233,182)],    "overlay": (2,12,14)},
    "sport":         {"color": "#82b1ff", "label": "Sport",          "bar": [(68,138,255),(48,79,254)],    "overlay": (2,6,14)},
    "science":       {"color": "#b388ff", "label": "Science & Tech", "bar": [(124,77,255),(101,31,255)],   "overlay": (2,6,16)},
}

# Emojis utilisés UNIQUEMENT en Python (jamais demandés à Claude)
EMOJIS = {
    "breaking": "🚨", "international": "🌍", "politique": "🏛️",
    "economie": "📈", "societe": "👥",        "histoire": "📜",
    "insolite": "😲", "sport": "🏆",          "science": "🔬",
}

# Labels en MAJUSCULES (sans accents pour éviter les bugs)
LABELS = {
    "breaking": "BREAKING",      "international": "MONDE",
    "politique": "POLITIQUE",    "economie": "ECO",
    "societe": "SOCIETE",        "histoire": "HISTOIRE",
    "insolite": "INSOLITE",      "sport": "SPORT",
    "science": "SCIENCE",
}

# Images Unsplash par catégorie (fallback quand pas de photo dans l'article)
UNSPLASH = {
    "breaking":      "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=1200&q=70",
    "international": "https://images.unsplash.com/photo-1526778548025-fa2f459cd5c1?w=1200&q=70",
    "politique":     "https://images.unsplash.com/photo-1541872703-74c5e44368f9?w=1200&q=70",
    "economie":      "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1200&q=70",
    "societe":       "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=1200&q=70",
    "histoire":      "https://images.unsplash.com/photo-1461360370896-922624d12aa1?w=1200&q=70",
    "insolite":      "https://images.unsplash.com/photo-1437622368342-7a3d73a34c8f?w=1200&q=70",
    "sport":         "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=1200&q=70",
    "science":       "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=70",
}

# Mots-clés qui indiquent une photo de personne (on ne met pas de texte par-dessus)
PERSON_KEYWORDS = [
    "macron", "trump", "biden", "poutine", "bardella", "le pen",
    "melenchon", "attal", "weil", "zemmour", "zelensky", "netanyahu",
    "borne", "philippe", "darmanin", "castets", "schiappa",
]

# ═══════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES
# ═══════════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect("seen_articles.db")
    for sql in [
        "CREATE TABLE IF NOT EXISTS seen (hash TEXT PRIMARY KEY, title TEXT, seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS recent_titles (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS category_log (category TEXT PRIMARY KEY, last_sent TEXT DEFAULT '2000-01-01')",
    ]:
        conn.execute(sql)
    conn.commit()
    return conn

def is_seen(conn, url):
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM seen WHERE hash=?", (h,)).fetchone() is not None

def mark_seen(conn, url, title):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO seen (hash,title) VALUES (?,?)", (h, title))
    conn.commit()

def get_recent(conn):
    return [r[0] for r in conn.execute("SELECT title FROM recent_titles ORDER BY added_at DESC LIMIT 40").fetchall()]

def add_recent(conn, title):
    conn.execute("INSERT INTO recent_titles (title) VALUES (?)", (title,))
    conn.execute("DELETE FROM recent_titles WHERE id NOT IN (SELECT id FROM recent_titles ORDER BY added_at DESC LIMIT 200)")
    conn.commit()

def cats_today(conn):
    today = datetime.now().strftime("%Y-%m-%d")
    return {r[0] for r in conn.execute("SELECT category FROM category_log WHERE last_sent LIKE ?", (f"{today}%",)).fetchall()}

def mark_cat(conn, cat):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO category_log (category,last_sent) VALUES (?,?) ON CONFLICT(category) DO UPDATE SET last_sent=excluded.last_sent", (cat, now))
    conn.commit()

# ═══════════════════════════════════════════════════════════════════════════
# CLAUDE API
# ═══════════════════════════════════════════════════════════════════════════
def claude(prompt, max_tokens=500):
    """Appel Claude qui retourne du JSON parsé."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def analyse(title, summary, source, recent):
    """Analyse un article : score + catégorie + doublon + besoin vidéo."""
    recent_str = "\n".join(f"- {t}" for t in recent[:20]) or "Aucun"
    today      = datetime.now().strftime("%d %B %Y")
    return claude(f"""Éditeur du compte Twitter Pulse. Aujourd'hui : {today}

Article :
- Source : {source}
- Titre  : {title}
- Résumé : {summary}

Titres récemment publiés (anti-doublon) :
{recent_str}

Réponds avec ce JSON UNIQUEMENT :
{{"score":<0-10>,"category":"<breaking|international|politique|economie|societe|histoire|insolite|sport|science>","is_duplicate":<true|false>,"needs_video":<true|false>,"reason":"<1 phrase>"}}

Barème score :
- 9-10 : breaking news majeure
- 7-8  : info importante avec impact
- 5-6  : info intéressante mais niche
- 0-4  : banal, communiqué publicitaire

is_duplicate=true uniquement si un titre récent traite EXACTEMENT du même événement.
"histoire" UNIQUEMENT si fait historique lié à la date du jour.""", max_tokens=200)

def gen_tweet_et_titre(title, summary, source, category, video_url=None):
    """
    Génère le corps du tweet (sans préfixe, ajouté en Python ensuite)
    + un titre court pour l'image PNG.
    """
    today = datetime.now().strftime("%d %B %Y")
    label = LABELS[category]
    video_str = f"\nIntègre ce lien à la fin du tweet : {video_url}" if video_url else ""

    result = claude(f"""Tu es community manager de Pulse, compte Twitter d'actualité française.
Aujourd'hui : {today}.

Article à traiter :
- Source : {source}
- Titre  : {title}
- Résumé : {summary}{video_str}

Tu dois générer DEUX choses :

1. **headline_court** : un titre court de MAX 75 caractères qui résume l'info, pour mettre sur une image.
   Pas de hashtag, pas d'emoji. Doit tenir entier sans être tronqué.

2. **body** : le CORPS du tweet (sans préfixe — il sera ajouté automatiquement).

RÈGLES STRICTES pour body :
- NE COMMENCE PAS par "{label}" ni aucune autre catégorie en majuscules
- Va directement à l'info, sans intro
- TOUJOURS en français
- Structure recommandée (compte Premium = 600 caractères max) :
   • Phrase 1 : accroche percutante avec L'INFO CLÉ (la "punchline")
   • Saut de ligne
   • 1 ou 2 phrases courtes avec les DÉTAILS qui complètent SANS RÉPÉTER
   • Saut de ligne
   • Source à la fin entre parenthèses : ({source})
- DONNE L'INFO COMPLÈTE — ne teaser pas. Si tu dis "la méthode la plus négligée", DIS LAQUELLE.
- 2-3 hashtags INTÉGRÉS dans le texte (remplacent un mot : "la #Chine" pas "Chine #Chine")
- Maximum 600 caractères au total

EXEMPLE CORRECT :
Body : "3 secondes d'exercice excentrique par jour suffisent pour gagner 12,8% de force musculaire en 4 semaines.

L'étude japonaise montre que la phase #excentrique du mouvement — la descente lente du poids — est la plus efficace, bien plus que la phase concentrique. Une révolution pour la #musculation moderne.

(Futura Sciences)"

Réponds avec ce JSON UNIQUEMENT :
{{"headline_court":"...","body":"..."}}""", max_tokens=800)

    # Nettoyage agressif du body : on enlève tout début "LABEL |" si Claude en a mis un
    body = result.get("body", "").strip()

    # Patterns à supprimer en début de body (au cas où Claude n'a pas suivi les règles)
    for label_test in LABELS.values():
        # Cas 1 : "LABEL | "
        pattern = rf"^{label_test}\s*\|\s*"
        body = re.sub(pattern, "", body, flags=re.IGNORECASE)
        # Cas 2 : "LABEL — " ou "LABEL - "
        pattern = rf"^{label_test}\s*[—-]\s*"
        body = re.sub(pattern, "", body, flags=re.IGNORECASE)

    # Aussi enlever d'éventuels emojis au début
    body = re.sub(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s]+", "", body).strip()

    headline_court = result.get("headline_court", title)[:80].strip()

    return body, headline_court

def build_full_tweet(body, category):
    """Construit le tweet final : emoji + LABEL + | + body."""
    emoji = EMOJIS[category]
    label = LABELS[category]
    return f"{emoji} {label} | {body}"

# ═══════════════════════════════════════════════════════════════════════════
# YOUTUBE
# ═══════════════════════════════════════════════════════════════════════════
def find_video(title, summary):
    if not YOUTUBE_API_KEY:
        return None
    try:
        q   = urllib.parse.quote(" ".join(title.split()[:7]))
        url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={q}&type=video&maxResults=5&relevanceLanguage=fr&key={YOUTUBE_API_KEY}"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
        videos = [{"title": i["snippet"]["title"], "channel": i["snippet"]["channelTitle"],
                   "url": f"https://youtu.be/{i['id']['videoId']}"}
                  for i in data.get("items", []) if i["id"].get("videoId")]
        if not videos:
            return None
        result = claude(f"""Article : {title}
Résumé : {summary}

Vidéos candidates :
{chr(10).join(f"{i+1}. [{v['channel']}] {v['title']}" for i,v in enumerate(videos))}

JSON : {{"chosen":<1-{len(videos)} ou 0>,"reason":"<1 phrase>"}}
Choisis seulement si la vidéo est DIRECTEMENT liée au même événement, source fiable.""", max_tokens=100)
        idx = int(result.get("chosen", 0))
        return videos[idx-1] if 0 < idx <= len(videos) else None
    except Exception as e:
        print(f"  ⚠️ YouTube: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# IMAGE PNG — DA Pulse
# ═══════════════════════════════════════════════════════════════════════════
def fetch_img(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read()
    except:
        return None

def is_person(title):
    """Détecte si le titre concerne une personne identifiable (photo = portrait)."""
    t = title.lower()
    return any(k in t for k in PERSON_KEYWORDS)

def build_png(headline_court, source, category, photo_url=None):
    """
    Génère un PNG 1200x675 DA Pulse.
    headline_court : MAX 80 caractères, sera affiché en entier ou tronqué proprement.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        W, H = 1200, 675
        s = STYLES[category]

        # Limite stricte : 80 chars max pour garantir que ça tienne
        if len(headline_court) > 80:
            headline_court = headline_court[:77].rsplit(" ", 1)[0] + "..."

        # ─── FOND ───
        img = Image.new('RGB', (W, H), (13, 13, 20))
        raw = fetch_img(photo_url or UNSPLASH.get(category))
        if raw:
            try:
                photo = Image.open(io.BytesIO(raw)).convert('RGB').resize((W, H), Image.LANCZOS)
                img   = Image.blend(Image.new('RGB', (W, H), (13, 13, 20)), photo, alpha=0.80)
            except:
                pass

        # ─── OVERLAY SOMBRE ───
        ov    = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(ov)
        r0, g0, b0 = s["overlay"]
        for y in range(H):
            a = min(255, 180 + int(y / H * 70))
            odraw.line([(0, y), (W, y)], fill=(r0, g0, b0, a))
        img  = Image.alpha_composite(img.convert('RGBA'), ov).convert('RGB')
        draw = ImageDraw.Draw(img)

        # ─── BARRE COULEUR HAUT ───
        c1, c2 = s["bar"]
        for x in range(W):
            t = x / W
            r = int(c1[0] + t * (c2[0] - c1[0]))
            g = int(c1[1] + t * (c2[1] - c1[1]))
            b = int(c1[2] + t * (c2[2] - c1[2]))
            draw.line([(x, 0), (x, 12)], fill=(r, g, b))

        # ─── POLICES ───
        def font(size, bold=True):
            paths = [
                f"/usr/share/fonts/truetype/noto/NotoSans-{'Bold' if bold else 'Regular'}.ttf",
                f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
            ]
            for p in paths:
                try:
                    return ImageFont.truetype(p, size)
                except:
                    continue
            return ImageFont.load_default()

        f_logo  = font(56)
        f_badge = font(28, bold=False)
        f_sm    = font(28, bold=False)

        # ─── LOGO PULSE ───
        draw.text((44, 30), "Pulse", font=f_logo, fill=(255, 255, 255))

        # ─── BADGE CATÉGORIE ───
        badge_hex = s["color"].lstrip("#")
        badge_rgb = tuple(int(badge_hex[i:i+2], 16) for i in (0, 2, 4))
        cat_text  = s["label"]
        bb = draw.textbbox((0, 0), cat_text, font=f_badge)
        bw = bb[2] - bb[0] + 36
        bh = bb[3] - bb[1] + 18
        bx = W - bw - 44
        by = 26
        bov   = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bov)
        bdraw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2,
                                  fill=(*badge_rgb, 50), outline=(*badge_rgb, 200), width=2)
        img  = Image.alpha_composite(img.convert('RGBA'), bov).convert('RGB')
        draw = ImageDraw.Draw(img)
        draw.text((bx + 18, by + 9), cat_text, font=f_badge, fill=badge_rgb)

        # ─── TITRE CENTRÉ ───
        # On affiche le texte SAUF si le titre concerne une personne identifiable
        # ET qu'il y a une vraie photo de l'article (pas l'image Unsplash)
        skip_text = is_person(headline_court) and photo_url is not None
        if not skip_text:
            # Cherche la plus grande taille de police qui tient en 3 lignes max
            chosen_lines = None
            chosen_size  = 32
            for fsize in [62, 54, 48, 42, 36, 32]:
                ft    = font(fsize)
                words = headline_court.split()
                lines, line = [], ""
                for w in words:
                    test = (line + " " + w).strip()
                    if draw.textbbox((0, 0), test, font=ft)[2] <= 1080:
                        line = test
                    else:
                        if line:
                            lines.append(line)
                        line = w
                if line:
                    lines.append(line)
                if len(lines) <= 3:
                    chosen_lines = lines
                    chosen_size  = fsize
                    break
            # Sécurité : si même à 32px ça déborde, on tronque à 3 lignes
            if chosen_lines is None:
                ft = font(32)
                words = headline_court.split()
                lines, line = [], ""
                for w in words:
                    test = (line + " " + w).strip()
                    if draw.textbbox((0, 0), test, font=ft)[2] <= 1080:
                        line = test
                    else:
                        if line:
                            lines.append(line)
                        line = w
                if line:
                    lines.append(line)
                chosen_lines = lines[:3]
                chosen_size  = 32
                # Ajouter "..." si tronqué
                if len(lines) > 3:
                    chosen_lines[-1] = chosen_lines[-1].rstrip() + "..."

            ft      = font(chosen_size)
            line_h  = chosen_size + 14
            total_h = len(chosen_lines) * line_h
            ty      = (H - total_h) // 2 + 10
            for ln in chosen_lines:
                bb = draw.textbbox((0, 0), ln, font=ft)
                draw.text(((W - (bb[2] - bb[0])) // 2, ty), ln, font=ft, fill=(255, 255, 255))
                ty += line_h

        # ─── SOURCE + DATE EN BAS ───
        mois     = ["jan", "fev", "mar", "avr", "mai", "juin", "juil", "aout", "sep", "oct", "nov", "dec"]
        now      = datetime.now()
        date_str = f"{now.day} {mois[now.month - 1]} {now.year}"
        draw.text((44, H - 52), source, font=f_sm, fill=(255, 255, 255, 150))
        bb2 = draw.textbbox((0, 0), date_str, font=f_sm)
        draw.text((W - bb2[2] - 44, H - 52), date_str, font=f_sm, fill=(255, 255, 255, 100))

        # ─── EXPORT ───
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        return buf.getvalue(), f"pulse-{category}-{now.strftime('%d%m%Y-%H%M')}.png"

    except Exception as e:
        print(f"  ⚠️ PNG erreur: {e}")
        return None, None

# ═══════════════════════════════════════════════════════════════════════════
# PDF
# ═══════════════════════════════════════════════════════════════════════════
def build_pdf(tweet_text, title, source, url, category, video=None):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib.enums import TA_CENTER
        import io

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        now      = datetime.now()
        mois     = ["jan","fév","mar","avr","mai","juin","juil","août","sep","oct","nov","déc"]
        date_str = f"{now.day} {mois[now.month-1]} {now.year} · {now.strftime('%H:%M')}"
        cat_label= STYLES[category]["label"]

        sH = ParagraphStyle("h",  fontSize=22, fontName="Helvetica-Bold",  textColor=colors.HexColor("#1a0060"), spaceAfter=4)
        sS = ParagraphStyle("s",  fontSize=10, fontName="Helvetica",        textColor=colors.HexColor("#888888"), spaceAfter=6)
        sC = ParagraphStyle("c",  fontSize=11, fontName="Helvetica-Bold",   textColor=colors.HexColor("#7b2fff"), spaceAfter=4)
        sT = ParagraphStyle("t",  fontSize=13, fontName="Helvetica-Bold",   textColor=colors.HexColor("#111111"), leading=18, spaceAfter=4)
        sL = ParagraphStyle("l",  fontSize=9,  fontName="Helvetica",        textColor=colors.HexColor("#aaaaaa"), spaceBefore=14, spaceAfter=4)
        sTw= ParagraphStyle("tw", fontSize=13, fontName="Helvetica",        textColor=colors.HexColor("#000000"), leading=20,
                             spaceAfter=8, borderPadding=12, backColor=colors.HexColor("#f5f5f7"),
                             borderColor=colors.HexColor("#e0e0e8"), borderWidth=1, borderRadius=8)
        sU = ParagraphStyle("u",  fontSize=10, fontName="Helvetica",        textColor=colors.HexColor("#3b1fff"), spaceAfter=4)
        sN = ParagraphStyle("n",  fontSize=9,  fontName="Helvetica-Oblique",textColor=colors.HexColor("#bbbbbb"), alignment=TA_CENTER)

        # Le texte du tweet peut contenir des sauts de ligne → on remplace par <br/>
        tweet_html = tweet_text.replace("\n", "<br/>")

        story = [
            Paragraph("Pulse", sH),
            Paragraph(f"Insuffler l'actu · {date_str}", sS),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e0e0e8")),
            Spacer(1, 0.3*cm),
            Paragraph(f"{cat_label} · {source}", sC),
            Paragraph(title, sT),
            HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")),
            Paragraph("TWEET — copie ce texte tel quel sur X :", sL),
            Paragraph(tweet_html, sTw),
        ]
        if video:
            story += [
                Paragraph("VIDÉO ASSOCIÉE :", sL),
                Paragraph(video["title"], sT),
                Paragraph(video["url"], sU),
            ]
        story += [
            Spacer(1, 0.3*cm),
            HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")),
            Paragraph(f"Article source : {url}", sU),
            Spacer(1, 0.2*cm),
            Paragraph("Pulse × Claude AI", sN),
        ]
        doc.build(story)
        return buf.getvalue()
    except Exception as e:
        print(f"  ⚠️ PDF erreur: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# EMAIL
# ═══════════════════════════════════════════════════════════════════════════
def send_email(subject, pdf_bytes, pdf_name, png_bytes, png_name):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(
        "Pulse — Nouvelle actu\n\n"
        "📎 PDF : tweet à copier sur X\n"
        "🖼️ PNG : image à joindre au tweet\n\n"
        "Pulse × Claude AI",
        "plain", "utf-8"
    ))
    if pdf_bytes:
        p = MIMEApplication(pdf_bytes, _subtype="pdf", name=pdf_name)
        p.add_header("Content-Disposition", "attachment", filename=pdf_name)
        msg.attach(p)
    if png_bytes:
        i = MIMEImage(png_bytes, name=png_name)
        i.add_header("Content-Disposition", "attachment", filename=png_name)
        msg.attach(i)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        srv.sendmail(GMAIL_ADDRESS, EMAIL_TO, msg.as_string())

# ═══════════════════════════════════════════════════════════════════════════
# EXTRACTION IMAGE RSS
# ═══════════════════════════════════════════════════════════════════════════
def extract_photo(entry):
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("type", "").startswith("image"):
                return m.get("url")
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            if "image" in e.get("type", ""):
                return e.get("href")
    return None

# ═══════════════════════════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════
def check_feeds(conn):
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🔍 Scan RSS...")

    # 1. Collecter nouveaux articles
    candidates = []
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:3]:
                url   = entry.get("link", "")
                title = entry.get("title", "")
                summ  = entry.get("summary", entry.get("description", ""))
                if url and title and not is_seen(conn, url):
                    candidates.append({"url": url, "title": title, "summary": summ,
                                       "source": fi["source"], "entry": entry})
        except Exception as e:
            print(f"  ❌ RSS {fi['source']}: {e}")

    if not candidates:
        print("  → Aucun nouvel article.")
        return

    print(f"  → {len(candidates)} articles nouveaux · analyse en cours...")

    # 2. Analyser chaque article
    recent = get_recent(conn)
    scored = []
    for c in candidates:
        try:
            a     = analyse(c["title"], c["summary"], c["source"], recent)
            score = int(a.get("score", 0))
            mark_seen(conn, c["url"], c["title"])
            if a.get("is_duplicate"):
                print(f"  ⏩ Doublon : {c['title'][:55]}")
                continue
            if score < SCORE_MINIMUM:
                print(f"  📉 {score}/10 : {c['title'][:55]}")
                continue
            scored.append({**c, "analysis": a, "score": score})
            print(f"  ✅ {score}/10 [{a.get('category')}] : {c['title'][:55]}")
        except Exception as e:
            print(f"  ❌ Analyse '{c['title'][:40]}': {e}")

    if not scored:
        print("  → Aucun article retenu.")
        return

    # 3. Boost catégories non encore envoyées aujourd'hui
    missing = set(STYLES.keys()) - cats_today(conn)
    for item in scored:
        if item["analysis"]["category"] in missing:
            item["score"] = min(10, item["score"] + 2)

    # 4. Sélection : MAX_PAR_PASSE articles de CATÉGORIES DIFFÉRENTES
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = []
    used_cats = set()
    for item in scored:
        cat = item["analysis"]["category"]
        if cat not in used_cats:
            top.append(item)
            used_cats.add(cat)
        if len(top) >= MAX_PAR_PASSE:
            break

    print(f"  → {len(top)} sélectionné(s) [{', '.join(used_cats)}] sur {len(scored)} éligibles.")

    # 5. Générer et envoyer
    for item in top:
        try:
            cat = item["analysis"]["category"]
            a   = item["analysis"]
            add_recent(conn, item["title"])

            # Vidéo YouTube
            video = None
            if a.get("needs_video") and YOUTUBE_API_KEY:
                video = find_video(item["title"], item["summary"])

            # Tweet + titre court
            body, headline_court = gen_tweet_et_titre(
                item["title"], item["summary"], item["source"], cat,
                video_url=video["url"] if video else None
            )
            tweet_final = build_full_tweet(body, cat)

            # Image PNG
            photo            = extract_photo(item["entry"])
            png_bytes, png_nm= build_png(headline_court, item["source"], cat, photo)

            # PDF
            now       = datetime.now()
            pdf_bytes = build_pdf(tweet_final, item["title"], item["source"], item["url"], cat, video)
            pdf_nm    = f"pulse-{cat}-{now.strftime('%d%m%Y-%H%M')}.pdf"
            png_nm    = png_nm or f"pulse-{cat}-{now.strftime('%d%m%Y-%H%M')}.png"

            # Envoi
            emoji   = EMOJIS[cat]
            subject = f"{emoji} Pulse · {item['source']} · {item['title'][:50]}"
            send_email(subject, pdf_bytes, pdf_nm, png_bytes, png_nm)
            mark_cat(conn, cat)
            print(f"  📧 Envoyé [{cat}] : {item['title'][:55]}")
            time.sleep(4)

        except Exception as e:
            print(f"  ❌ Envoi '{item['title'][:40]}': {e}")


def main():
    print("🤖 Pulse NewsBot démarré !")
    conn = init_db()
    while True:
        check_feeds(conn)
        print("  💤 Pause 2h...\n")
        time.sleep(7200)

if __name__ == "__main__":
    main()
