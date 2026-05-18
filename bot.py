import feedparser
import anthropic
import sqlite3
import hashlib
import json
import time
import os
import smtplib
import urllib.request
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS",      "tonmail@gmail.com")
GMAIL_APP_PASS     = os.environ.get("GMAIL_APP_PASS",     "xxxx xxxx xxxx xxxx")
EMAIL_TO           = os.environ.get("EMAIL_TO",           "tonmail@gmail.com")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY",  "TA_CLE_CLAUDE_ICI")
YOUTUBE_API_KEY    = os.environ.get("YOUTUBE_API_KEY",    "")   # optionnel

# Score minimum pour envoyer (0-10)
SCORE_MINIMUM  = 6
# Max articles envoyés par passe
MAX_PAR_PASSE  = 2

# Catégories pour lesquelles une vidéo YouTube boost l'engagement
CATEGORIES_VIDEO = {"breaking", "international", "sport", "science", "insolite", "histoire"}

# ─────────────────────────────────────────────────────────────────────────────
# SOURCES RSS
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # 🇫🇷 France générale
    {"url": "https://www.lemonde.fr/rss/une.xml",                          "source": "Le Monde"},
    {"url": "https://www.lefigaro.fr/rss/figaro_actualites.xml",           "source": "Le Figaro"},
    {"url": "https://www.liberation.fr/arc/outboundfeeds/rss/",            "source": "Libération"},
    {"url": "https://www.20minutes.fr/feeds/rss/actu",                     "source": "20 Minutes"},
    {"url": "https://www.bfmtv.com/rss/news-24-7/",                        "source": "BFMTV"},
    {"url": "https://www.franceinfo.fr/rss/en-direct.rss",                 "source": "France Info"},
    # 🏛️ Politique française spécifique
    {"url": "https://www.lemonde.fr/politique/rss/",                       "source": "Le Monde Politique"},
    {"url": "https://www.lefigaro.fr/rss/figaro_politique.xml",            "source": "Le Figaro Politique"},
    {"url": "https://www.publicsenat.fr/rss/articles.rss",                 "source": "Public Sénat"},
    # 🔐 Numérique / CNIL / Tech française
    {"url": "https://www.cnil.fr/fr/rss.xml",                              "source": "CNIL"},
    {"url": "https://www.numerama.com/feed/",                              "source": "Numerama"},
    # 🌍 International
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",                 "source": "BBC World"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",      "source": "NY Times"},
    {"url": "https://www.theguardian.com/world/rss",                       "source": "The Guardian"},
    {"url": "https://feeds.reuters.com/reuters/topNews",                   "source": "Reuters"},
    # 📈 Économie
    {"url": "https://www.lesechos.fr/rss/rss_la_une.xml",                  "source": "Les Echos"},
    # 🔬 Science & Tech
    {"url": "https://www.futura-sciences.com/rss/actualites.xml",          "source": "Futura Sciences"},
    # 😲 Insolite / Faits divers
    {"url": "https://www.leparisien.fr/faits-divers/rss.xml",              "source": "Le Parisien"},
    # 🏆 Sport
    {"url": "https://www.lequipe.fr/rss/actu_rss.xml",                     "source": "L'Équipe"},
    # 📜 Histoire
    {"url": "https://www.herodote.net/rss.xml",                            "source": "Herodote"},
]

# ─────────────────────────────────────────────────────────────────────────────
# DA PULSE — styles + préfixes par catégorie
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_STYLES = {
    "breaking":      {"bar": "linear-gradient(90deg,#ff2020,#ff6030)", "overlay": "rgba(18,3,3,.88)",   "badge_color": "#ff6868",  "label": "🔴 Breaking",      "prefix": "BREAKING"},
    "international": {"bar": "linear-gradient(90deg,#2196f3,#00b8d4)", "overlay": "rgba(3,10,22,.88)",  "badge_color": "#64b5f6",  "label": "🌍 International", "prefix": "MONDE"},
    "politique":     {"bar": "linear-gradient(90deg,#ffc107,#ff9800)", "overlay": "rgba(12,10,2,.88)",  "badge_color": "#ffd54f",  "label": "🏛️ Politique",    "prefix": "POLITIQUE"},
    "economie":      {"bar": "linear-gradient(90deg,#00e676,#00bfa5)", "overlay": "rgba(2,12,5,.88)",   "badge_color": "#69f0ae",  "label": "📈 Économie",      "prefix": "ECO"},
    "societe":       {"bar": "linear-gradient(90deg,#ce93d8,#9c27b0)", "overlay": "rgba(10,4,20,.88)",  "badge_color": "#ce93d8",  "label": "👥 Société",       "prefix": "SOCIETE"},
    "histoire":      {"bar": "linear-gradient(90deg,#d4a843,#a0714a)", "overlay": "rgba(14,8,2,.88)",   "badge_color": "#d4a843",  "label": "📜 Histoire",      "prefix": "HISTOIRE"},
    "insolite":      {"bar": "linear-gradient(90deg,#00e5ff,#1de9b6)", "overlay": "rgba(2,12,14,.88)",  "badge_color": "#00e5ff",  "label": "😲 Insolite",      "prefix": "INSOLITE"},
    "sport":         {"bar": "linear-gradient(90deg,#448aff,#304ffe)", "overlay": "rgba(2,6,14,.88)",   "badge_color": "#82b1ff",  "label": "🏆 Sport",         "prefix": "SPORT"},
    "science":       {"bar": "linear-gradient(90deg,#7c4dff,#651fff)", "overlay": "rgba(2,6,16,.88)",   "badge_color": "#b388ff",  "label": "🔬 Science & Tech","prefix": "SCIENCE"},
}

TWEET_PREFIXES = {
    "breaking":      "🚨 BREAKING",
    "international": "🌍 MONDE",
    "politique":     "🏛️ POLITIQUE",
    "economie":      "📈 ECO",
    "societe":       "👥 SOCIETE",
    "histoire":      "📜 HISTOIRE",
    "insolite":      "😲 INSOLITE",
    "sport":         "🏆 SPORT",
    "science":       "🔬 SCIENCE",
}

# ─────────────────────────────────────────────────────────────────────────────
# BASE DE DONNÉES
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("seen_articles.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS seen (
        hash TEXT PRIMARY KEY, title TEXT,
        seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS recent_titles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS category_log (
        category TEXT PRIMARY KEY,
        last_sent TIMESTAMP DEFAULT '2000-01-01')""")
    conn.commit()
    return conn

def get_categories_sent_today(conn):
    """Retourne les catégories déjà envoyées aujourd'hui."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows  = conn.execute(
        "SELECT category FROM category_log WHERE last_sent LIKE ?", (f"{today}%",)
    ).fetchall()
    return {r[0] for r in rows}

def mark_category_sent(conn, category):
    conn.execute("""INSERT INTO category_log (category, last_sent) VALUES (?,?)
        ON CONFLICT(category) DO UPDATE SET last_sent=excluded.last_sent""",
        (category, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()

def is_url_seen(conn, url):
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM seen WHERE hash=?", (h,)).fetchone() is not None

def mark_url_seen(conn, url, title):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO seen (hash,title) VALUES (?,?)", (h, title))
    conn.commit()

def get_recent_titles(conn, limit=50):
    rows = conn.execute(
        "SELECT title FROM recent_titles ORDER BY added_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [r[0] for r in rows]

def add_recent_title(conn, title):
    conn.execute("INSERT INTO recent_titles (title) VALUES (?)", (title,))
    conn.execute("""DELETE FROM recent_titles WHERE id NOT IN (
        SELECT id FROM recent_titles ORDER BY added_at DESC LIMIT 200)""")
    conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
# YOUTUBE — recherche + validation de pertinence
# ─────────────────────────────────────────────────────────────────────────────

def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """
    Cherche des vidéos YouTube via l'API Data v3.
    Retourne une liste de {title, url, channel, description}.
    """
    if not YOUTUBE_API_KEY:
        return []
    try:
        q = urllib.parse.quote(query)
        api_url = (
            f"https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&q={q}&type=video&maxResults={max_results}"
            f"&relevanceLanguage=fr&order=relevance&key={YOUTUBE_API_KEY}"
        )
        with urllib.request.urlopen(api_url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        results = []
        for item in data.get("items", []):
            vid_id = item["id"].get("videoId", "")
            snip   = item.get("snippet", {})
            if vid_id:
                results.append({
                    "title":       snip.get("title", ""),
                    "channel":     snip.get("channelTitle", ""),
                    "description": snip.get("description", "")[:200],
                    "url":         f"https://youtu.be/{vid_id}",
                })
        return results
    except Exception as e:
        print(f"  ⚠️  YouTube search error: {e}")
        return []


def validate_youtube_video(article_title: str, article_summary: str, videos: list[dict]) -> dict | None:
    """
    Claude choisit la vidéo la plus pertinente parmi les résultats.
    Retourne la vidéo choisie ou None si aucune n'est assez pertinente.
    """
    if not videos:
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    videos_str = "\n".join(
        f"{i+1}. [{v['channel']}] {v['title']} — {v['description']}"
        for i, v in enumerate(videos)
    )

    prompt = f"""Tu dois choisir si une vidéo YouTube est assez pertinente pour accompagner un tweet d'actualité.

Article :
Titre : {article_title}
Résumé : {article_summary}

Vidéos candidates :
{videos_str}

Réponds UNIQUEMENT avec ce JSON :
{{
  "chosen": <1-{len(videos)} ou 0 si aucune ne convient>,
  "reason": "<pourquoi en 1 phrase>"
}}

Critères stricts pour choisir (chosen > 0) :
- La vidéo traite DIRECTEMENT du même événement ou sujet précis
- Elle est d'une source fiable (media officiel, chaîne institutionnelle)
- Elle n'est PAS hors-sujet, même légèrement
- En cas de doute → chosen = 0 (vaut mieux pas de vidéo qu'une mauvaise)"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
    result = json.loads(raw)

    idx = int(result.get("chosen", 0))
    if idx > 0 and idx <= len(videos):
        chosen = videos[idx - 1]
        print(f"  🎬 Vidéo validée : {chosen['title'][:60]}... ({result.get('reason','')})")
        return chosen
    else:
        print(f"  ⏩ Aucune vidéo retenue : {result.get('reason','')}")
        return None


def find_relevant_video(title: str, summary: str, category: str) -> dict | None:
    """
    Point d'entrée : cherche et valide une vidéo pour cet article.
    Ne tente la recherche que pour les catégories qui le méritent.
    """
    if not YOUTUBE_API_KEY or category not in CATEGORIES_VIDEO:
        return None

    # Construire une requête de recherche ciblée
    # On utilise les 8 premiers mots du titre + la catégorie pour affiner
    words  = title.split()[:8]
    query  = " ".join(words)

    print(f"  🔍 Recherche YouTube : {query}")
    videos = search_youtube(query, max_results=5)

    if not videos:
        return None

    return validate_youtube_video(title, summary, videos)


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSE ARTICLE (score + catégorie + anti-doublon + needs_video)
# ─────────────────────────────────────────────────────────────────────────────

def analyse_article(title, summary, source, recent_titles):
    client     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today      = datetime.now().strftime("%d %B %Y")
    recent_str = "\n".join(f"- {t}" for t in recent_titles[:30]) or "Aucun"

    prompt = f"""Tu es l'éditeur en chef du compte Twitter Pulse, actu France & monde.
Aujourd'hui : {today}

Article :
Source : {source}
Titre : {title}
Résumé : {summary}

Titres déjà publiés récemment (anti-doublon) :
{recent_str}

Réponds UNIQUEMENT avec ce JSON :
{{
  "score": <0-10>,
  "category": "<breaking|international|politique|economie|societe|histoire|insolite|sport|science>",
  "is_duplicate": <true|false>,
  "needs_image": <true|false>,
  "needs_video": <true|false>,
  "reason": "<1 phrase>"
}}

Barème score :
9-10 → Breaking majeur / info impacte tout le monde
7-8  → Info importante, audience large
5-6  → Intéressant mais niche ou peu urgent
0-4  → Banal, communiqué, pub, fait divers mineur

is_duplicate : true si un titre récent traite du MÊME événement.
needs_image  : true si l'actu est visuelle (lieu connu, personnalité, catastrophe...).
needs_video  : true si une vidéo (discours, match, phénomène naturel, découverte...) rendrait le tweet plus impactant.
Catégorie "histoire" : uniquement si l'article parle d'un fait historique lié à la date du jour."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# GÉNÉRATION TWEET / THREAD
# ─────────────────────────────────────────────────────────────────────────────

def generate_tweet_content(title, summary, source, category, video_url=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now().strftime("%d %B %Y")
    prefix = TWEET_PREFIXES.get(category, "📰")
    cat_label = {
        "breaking": "BREAKING", "international": "MONDE",
        "politique": "POLITIQUE", "economie": "ECO",
        "societe": "SOCIETE", "histoire": "HISTOIRE",
        "insolite": "INSOLITE", "sport": "SPORT", "science": "SCIENCE",
    }.get(category, category.upper())
    extras = {
        "histoire": "Style 'Il y a X ans...'",
        "insolite": "Rends-le fun et surprenant.",
        "breaking": "Urgence et clarté.",
        "science":  "Simplifie, rends accessible.",
    }
    extra = extras.get(category, "")
    video_instruction = f"\nIntègre ce lien à la fin : {video_url}" if video_url else ""

    prompt = f"""Tu es community manager de Pulse, compte Twitter d'actu française.
Aujourd'hui : {today}. {extra}

Article :
Source : {source}
Titre : {title}
Résumé : {summary}{video_instruction}

FORMAT OBLIGATOIRE :
{prefix} {cat_label} | info directe #hashtag1 #hashtag2 (Source)

Règles :
- TOUJOURS en FRANÇAIS
- Commence par "{prefix} {cat_label} |"
- Info brute après le |, zéro remplissage
- 2-3 hashtags pertinents
- Source entre parenthèses : ({source})
- Max 280 caractères

Exemple : 🌍 MONDE | Le Danemark refuse de négocier le Groenland malgré Trump #Groenland #Trump #Géopolitique (Le Monde)

Réponds UNIQUEMENT avec ce JSON :
{{"tweet": "le tweet complet"}}"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
    return json.loads(raw)


def analyse_image_type(photo_url, title):
    """
    Détermine si l'image est une photo de personne identifiable.
    Retourne True si personne (pas de texte au milieu), False sinon.
    """
    if not photo_url:
        return False
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = f"""Le titre de l'article est : "{title}"
L'image vient de cette URL : {photo_url}

Sans voir l'image, en te basant uniquement sur le titre et le contexte :
Cette image montre-t-elle probablement une ou plusieurs personnes identifiables (politiciens, célébrités, sportifs...) ?

Réponds UNIQUEMENT avec : {{"is_person": true}} ou {{"is_person": false}}"""
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        return json.loads(raw).get("is_person", False)
    except:
        return False

def build_tweet_image_png(headline, source, category, photo_url=None, is_person=False):
    """
    Génère un PNG 1200x675 DA Pulse.
    - Police Noto Sans (propre, lisible, style Apple)
    - Taille texte adaptative selon longueur du titre
    - Pas d'emoji dans les textes rendus (problème PIL)
    - Badge catégorie sans emoji
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io, urllib.request

        W, H = 1200, 675

        # ── Fond de base ──
        img  = Image.new('RGB', (W, H), (13, 13, 20))

        # ── Photo de fond — article ou fallback Unsplash ──
        UNSPLASH_FALLBACK = {
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
        img_url = photo_url or UNSPLASH_FALLBACK.get(category)
        if img_url:
            try:
                req   = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    raw = r.read()
                photo = Image.open(io.BytesIO(raw)).convert('RGB').resize((W, H), Image.LANCZOS)
                bg    = Image.new('RGB', (W, H), (13, 13, 20))
                img   = Image.blend(bg, photo, alpha=0.80)
            except:
                pass

        # ── Overlay dégradé sombre ──
        s       = CATEGORY_STYLES.get(category, CATEGORY_STYLES["international"])
        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        odraw   = ImageDraw.Draw(overlay)
        for y in range(H):
            a = int(140 + (y / H) * 100)
            odraw.line([(0, y), (W, y)], fill=(10, 10, 18, a))
        img  = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(img)

        # ── Barre couleur haut ──
        bar_colors = {
            "breaking":      [(255,32,32),   (255,96,48)],
            "international": [(33,150,243),  (0,184,212)],
            "politique":     [(255,193,7),   (255,152,0)],
            "economie":      [(0,230,118),   (0,191,165)],
            "societe":       [(206,147,216), (156,39,176)],
            "histoire":      [(212,168,67),  (160,113,74)],
            "insolite":      [(0,229,255),   (29,233,182)],
            "sport":         [(68,138,255),  (48,79,254)],
            "science":       [(124,77,255),  (101,31,255)],
        }
        c1, c2 = bar_colors.get(category, [(33,150,243),(0,184,212)])
        for x in range(W):
            t = x / W
            r = int(c1[0] + t*(c2[0]-c1[0]))
            g = int(c1[1] + t*(c2[1]-c1[1]))
            b = int(c1[2] + t*(c2[2]-c1[2]))
            draw.line([(x,0),(x,12)], fill=(r,g,b))

        # ── Polices — Noto Sans (propre, sans serif, style Apple) ──
        font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        def load_font(size, bold=False):
            candidates = [p for p in font_paths if ("Bold" in p or not bold)]
            for p in candidates:
                try:
                    return ImageFont.truetype(p, size)
                except:
                    continue
            return ImageFont.load_default()

        font_logo  = load_font(56, bold=True)
        font_badge = load_font(28)
        font_sm    = load_font(30)

        # ── Taille titre adaptative ──
        # On choisit la taille pour que le texte tienne en max 3 lignes dans 1100px de large
        title_clean = headline  # pas d'emoji dans le titre image
        for font_size in [72, 60, 50, 42, 36]:
            font_title = load_font(font_size, bold=True)
            # Découper en lignes
            words = title_clean.split()
            lines, line = [], ""
            for w in words:
                test = (line + " " + w).strip()
                bbox = draw.textbbox((0,0), test, font=font_title)
                if bbox[2] - bbox[0] <= 1100:
                    line = test
                else:
                    if line: lines.append(line)
                    line = w
            if line: lines.append(line)
            if len(lines) <= 3:
                break  # bonne taille trouvée

        lines = lines[:3]

        # ── Logo PULSE (haut gauche) — sans emoji ──
        draw.text((44, 32), "Pulse", font=font_logo, fill=(255, 255, 255))

        # ── Badge catégorie (haut droit) — couleur de la catégorie ──
        cat_labels_clean = {
            "breaking":      "Breaking",
            "international": "International",
            "politique":     "Politique",
            "economie":      "Economie",
            "societe":       "Societe",
            "histoire":      "Histoire",
            "insolite":      "Insolite",
            "sport":         "Sport",
            "science":       "Science & Tech",
        }
        cat_text  = cat_labels_clean.get(category, category.capitalize())
        # Couleur du badge = couleur de la catégorie
        badge_rgb = tuple(int(s["badge_color"].lstrip("#")[i:i+2], 16) for i in (0,2,4))

        bbox = draw.textbbox((0,0), cat_text, font=font_badge)
        tw_b = bbox[2] - bbox[0]
        th_b = bbox[3] - bbox[1]
        pad_x, pad_y = 20, 10
        bw = tw_b + pad_x * 2
        bh = th_b + pad_y * 2
        bx = W - bw - 44
        by = 28

        # Fond coloré semi-transparent du badge
        badge_overlay = Image.new('RGBA', (W, H), (0,0,0,0))
        bdraw = ImageDraw.Draw(badge_overlay)
        bdraw.rounded_rectangle(
            [bx, by, bx+bw, by+bh], radius=bh//2,
            fill=(*badge_rgb, 50),
            outline=(*badge_rgb, 180), width=2
        )
        img  = Image.alpha_composite(img.convert('RGBA'), badge_overlay).convert('RGB')
        draw = ImageDraw.Draw(img)
        # Texte centré dans le badge
        draw.text((bx + pad_x, by + pad_y), cat_text, font=font_badge, fill=badge_rgb)

        # ── Titre centré verticalement — seulement si pas une photo de personne ──
        if not is_person:
            line_h     = font_size + 16
            total_h    = len(lines) * line_h
            ty         = (H - total_h) // 2 + 10
            for ln in lines:
                bbox = draw.textbbox((0,0), ln, font=font_title)
                lw   = bbox[2] - bbox[0]
                draw.text(((W - lw) // 2, ty), ln, font=font_title, fill=(255, 255, 255))
                ty  += line_h

        # ── Source + date (bas) ──
        mois = ["jan","fev","mar","avr","mai","juin","juil","aout","sep","oct","nov","dec"]
        now  = datetime.now()
        date_str = f"{now.day} {mois[now.month-1]} {now.year}"
        draw.text((44, H - 55), source, font=font_sm, fill=(255,255,255,120))
        bbox = draw.textbbox((0,0), date_str, font=font_sm)
        draw.text((W - bbox[2] - 44, H - 55), date_str, font=font_sm, fill=(255,255,255,80))

        # ── Export ──
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        filename = f"pulse-{category}-{now.strftime('%d%m%Y-%H%M')}.png"
        return buf.getvalue(), filename

    except Exception as e:
        print(f"  ⚠️  PNG echoue : {e}")
        return None, None
# ─────────────────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def build_pdf(tweet_result, title, source, url, category, video=None):
    """Génère un PDF avec le texte exact à copier-coller sur X."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        import io

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)

        styles = getSampleStyleSheet()
        cat_label = CATEGORY_STYLES.get(category, {}).get("label", "")
        now = datetime.now()
        mois = ["jan","fév","mar","avr","mai","juin","juil","août","sep","oct","nov","déc"]
        date_str = f"{now.day} {mois[now.month-1]} {now.year} · {now.strftime('%H:%M')}"

        # Styles custom
        s_header = ParagraphStyle("header", fontSize=22, fontName="Helvetica-Bold",
                                   textColor=colors.HexColor("#1a0060"), spaceAfter=4)
        s_sub    = ParagraphStyle("sub", fontSize=10, fontName="Helvetica",
                                   textColor=colors.HexColor("#888888"), spaceAfter=2)
        s_cat    = ParagraphStyle("cat", fontSize=11, fontName="Helvetica-Bold",
                                   textColor=colors.HexColor("#7b2fff"), spaceAfter=6)
        s_title  = ParagraphStyle("title", fontSize=13, fontName="Helvetica-Bold",
                                   textColor=colors.HexColor("#111111"), spaceAfter=4,
                                   leading=18)
        s_label  = ParagraphStyle("label", fontSize=9, fontName="Helvetica",
                                   textColor=colors.HexColor("#aaaaaa"), spaceAfter=4,
                                   spaceBefore=16)
        s_tweet  = ParagraphStyle("tweet", fontSize=14, fontName="Helvetica",
                                   textColor=colors.HexColor("#000000"), leading=22,
                                   spaceAfter=8, borderPadding=12,
                                   backColor=colors.HexColor("#f5f5f7"),
                                   borderColor=colors.HexColor("#e0e0e8"),
                                   borderWidth=1, borderRadius=8)
        s_url    = ParagraphStyle("url", fontSize=10, fontName="Helvetica",
                                   textColor=colors.HexColor("#3b1fff"), spaceAfter=4)
        s_note   = ParagraphStyle("note", fontSize=9, fontName="Helvetica-Oblique",
                                   textColor=colors.HexColor("#bbbbbb"), spaceAfter=4,
                                   alignment=TA_CENTER)

        story = []

        # En-tête
        story.append(Paragraph("Pulse", s_header))
        story.append(Paragraph(f"Insuffler l'actu · {date_str}", s_sub))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e0e0e8")))
        story.append(Spacer(1, 0.3*cm))

        # Catégorie + source
        story.append(Paragraph(f"{cat_label} · {source}", s_cat))
        story.append(Paragraph(title, s_title))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")))

        # Tweet — texte à copier
        story.append(Paragraph("TWEET — copie ce texte tel quel sur X :", s_label))
        story.append(Paragraph(tweet_result.get("tweet", tweet_result.get("proposition1", "")), s_tweet))

        # Vidéo
        if video:
            story.append(Paragraph("VIDÉO ASSOCIÉE :", s_label))
            story.append(Paragraph(f"{video['title']}", s_title))
            story.append(Paragraph(video["url"], s_url))

        # Lien article
        story.append(Spacer(1, 0.4*cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")))
        story.append(Paragraph(f"Source : {url}", s_url))
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph("Pulse × Claude AI — généré automatiquement", s_note))

        doc.build(story)
        return buf.getvalue()

    except Exception as e:
        print(f"  ⚠️  PDF échoué : {e}")
        return None


def send_email(subject, pdf_bytes=None, png_bytes=None, png_filename="pulse-image.png", pdf_filename="pulse-tweet.pdf"):
    """Email minimaliste avec PDF + PNG en pièces jointes."""
    from email.mime.application import MIMEApplication
    from email.mime.image import MIMEImage

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = EMAIL_TO

    # Corps texte ultra simple
    body = MIMEText(
        f"Pulse — Nouvelle actu détectée\n\n"
        f"📎 {pdf_filename} — texte à copier-coller sur X\n"
        f"{'🖼️ ' + png_filename + ' — image à poster avec le tweet' if png_bytes else ''}\n\n"
        f"Pulse × Claude AI",
        "plain", "utf-8"
    )
    msg.attach(body)

    # Pièce jointe PDF
    if pdf_bytes:
        pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf", name=pdf_filename)
        pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
        msg.attach(pdf_part)

    # Pièce jointe PNG
    if png_bytes:
        img_part = MIMEImage(png_bytes, name=png_filename)
        img_part.add_header("Content-Disposition", "attachment", filename=png_filename)
        msg.attach(img_part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        srv.sendmail(GMAIL_ADDRESS, EMAIL_TO, msg.as_string())


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION IMAGE ARTICLE
# ─────────────────────────────────────────────────────────────────────────────

def extract_image(entry):
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("type","").startswith("image"):
                return m.get("url")
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            if "image" in e.get("type",""):
                return e.get("href")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# BOUCLE PRINCIPALE
# ─────────────────────────────────────────────────────────────────────────────

def check_feeds(conn):
    # ── Filtre horaire : pas d'envoi entre 23h et 8h ──
    heure = datetime.now().hour
    if heure >= 23 or heure < 8:
        print(f"  😴 Hors plage horaire ({heure}h) — on attend 8h.")
        return

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔍 Scan RSS...")

    # 1. Collecter
    candidates = []
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:5]:
                url   = entry.get("link","")
                title = entry.get("title","")
                summ  = entry.get("summary", entry.get("description",""))
                if url and title and not is_url_seen(conn, url):
                    candidates.append({"url":url,"title":title,"summary":summ,"source":fi["source"],"entry":entry})
        except Exception as e:
            print(f"  ❌ {fi['source']}: {e}")

    if not candidates:
        print("  → Rien de nouveau.")
        return

    print(f"  → {len(candidates)} nouveaux articles · analyse...")

    # 2. Analyser
    recent_titles = get_recent_titles(conn)
    scored = []
    for c in candidates:
        try:
            a     = analyse_article(c["title"], c["summary"], c["source"], recent_titles)
            score = int(a.get("score", 0))
            mark_url_seen(conn, c["url"], c["title"])

            if a.get("is_duplicate"):
                print(f"  ⏩ Doublon : {c['title'][:50]}...")
                continue
            if score < SCORE_MINIMUM:
                print(f"  📉 {score}/10 : {c['title'][:50]}...")
                continue

            scored.append({**c, "analysis": a, "score": score})
            print(f"  ✅ {score}/10 [{a.get('category')}] : {c['title'][:50]}...")
        except Exception as e:
            print(f"  ❌ Analyse : {e}")

    # 3. Boost catégories pas encore envoyées aujourd'hui + tri par score
    cats_today = get_categories_sent_today(conn)
    all_cats   = set(CATEGORY_STYLES.keys())
    missing    = all_cats - cats_today
    if missing:
        print(f"  📊 Catégories manquantes aujourd'hui : {', '.join(missing)}")

    # Boost +2 pour les catégories pas encore vues aujourd'hui
    for item in scored:
        if item["analysis"]["category"] in missing:
            item["score"] = min(10, item["score"] + 2)

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:MAX_PAR_PASSE]
    print(f"  → {len(top)} sélectionné(s).")

    # 4. Générer + envoyer
    for item in top:
        try:
            add_recent_title(conn, item["title"])
            cat = item["analysis"]["category"]
            a   = item["analysis"]

            # Image PNG
            photo    = extract_image(item["entry"])
            png_bytes, png_filename = None, None
            is_person = False
            if photo:
                is_person = analyse_image_type(photo, item["title"])
            if a.get("needs_image") or photo:
                png_bytes, png_filename = build_tweet_image_png(
                    item["title"], item["source"], cat, photo, is_person=is_person
                )

            # Vidéo YouTube
            video = None
            if a.get("needs_video") and YOUTUBE_API_KEY:
                video = find_relevant_video(item["title"], item["summary"], cat)

            # Tweet — 1 seule proposition par article
            tweet = generate_tweet_content(
                item["title"], item["summary"], item["source"], cat,
                video_url=video["url"] if video else None
            )

            now      = datetime.now()
            emoji    = TWEET_PREFIXES.get(cat, "📰").split()[0]
            subject  = f"{emoji} Pulse · {item['source']} · {item['title'][:45]}..."
            pdf_name = f"pulse-{cat}-{now.strftime('%d%m%Y-%H%M')}.pdf"
            pdf_bytes = build_pdf(tweet, item["title"], item["source"], item["url"], cat, video)

            send_email(
                subject,
                pdf_bytes=pdf_bytes, pdf_filename=pdf_name,
                png_bytes=png_bytes, png_filename=png_filename or "pulse-image.png"
            )
            mark_category_sent(conn, cat)
            print(f"  📧 Envoyé : {item['title'][:50]}...")
            time.sleep(4)
            mark_category_sent(conn, cat)
            print(f"  📧 Envoyé {'📎PDF' if pdf_bytes else ''} {'🖼️PNG' if png_bytes else ''} {'📹' if video else ''} : {item['title'][:50]}...")
            time.sleep(4)

        except Exception as e:
            print(f"  ❌ Envoi : {e}")


def main():
    print("🤖 Pulse NewsBot démarré !")
    print(f"   Score min : {SCORE_MINIMUM}/10 · Max/passe : {MAX_PAR_PASSE}")
    print(f"   YouTube : {'✅ activé' if YOUTUBE_API_KEY else '⚠️  désactivé (pas de clé)'}\n")
    conn = init_db()
    while True:
        check_feeds(conn)
        print("   💤 Pause 30 min...\n")
        time.sleep(1800)

if __name__ == "__main__":
    main()
