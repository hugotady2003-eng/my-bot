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
    # 🇫🇷 France
    {"url": "https://www.lemonde.fr/rss/une.xml",                     "source": "Le Monde"},
    {"url": "https://www.lefigaro.fr/rss/figaro_actualites.xml",      "source": "Le Figaro"},
    {"url": "https://www.liberation.fr/arc/outboundfeeds/rss/",       "source": "Libération"},
    {"url": "https://www.20minutes.fr/feeds/rss/actu",                "source": "20 Minutes"},
    {"url": "https://www.bfmtv.com/rss/news-24-7/",                   "source": "BFMTV"},
    {"url": "https://www.franceinfo.fr/rss/en-direct.rss",            "source": "France Info"},
    # 🌍 International
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",            "source": "BBC World"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "source": "NY Times"},
    {"url": "https://www.theguardian.com/world/rss",                  "source": "The Guardian"},
    {"url": "https://feeds.reuters.com/reuters/topNews",              "source": "Reuters"},
    # 📈 Économie
    {"url": "https://www.lesechos.fr/rss/rss_la_une.xml",             "source": "Les Echos"},
    # 🔬 Science & Tech
    {"url": "https://www.futura-sciences.com/rss/actualites.xml",     "source": "Futura Sciences"},
    # 😲 Insolite
    {"url": "https://www.leparisien.fr/faits-divers/rss.xml",         "source": "Le Parisien"},
    # 🏆 Sport
    {"url": "https://www.lequipe.fr/rss/actu_rss.xml",                "source": "L'Équipe"},
]

# ─────────────────────────────────────────────────────────────────────────────
# DA PULSE — styles + préfixes par catégorie
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_STYLES = {
    "breaking":      {"bar": "linear-gradient(90deg,#ff2020,#ff6030)", "overlay": "rgba(18,3,3,.88)",   "badge_color": "#ff6868",  "label": "🔴 Breaking",      "prefix": "🚨 BREAKING"},
    "international": {"bar": "linear-gradient(90deg,#2196f3,#00b8d4)", "overlay": "rgba(3,10,22,.88)",  "badge_color": "#64b5f6",  "label": "🌍 International", "prefix": "🌍 MONDE"},
    "politique":     {"bar": "linear-gradient(90deg,#ffc107,#ff9800)", "overlay": "rgba(12,10,2,.88)",  "badge_color": "#ffd54f",  "label": "🏛️ Politique",    "prefix": "🏛️ POLITIQUE"},
    "economie":      {"bar": "linear-gradient(90deg,#00e676,#00bfa5)", "overlay": "rgba(2,12,5,.88)",   "badge_color": "#69f0ae",  "label": "📈 Économie",      "prefix": "📈 ÉCO"},
    "societe":       {"bar": "linear-gradient(90deg,#ce93d8,#9c27b0)", "overlay": "rgba(10,4,20,.88)",  "badge_color": "#ce93d8",  "label": "👥 Société",       "prefix": "👥 SOCIÉTÉ"},
    "histoire":      {"bar": "linear-gradient(90deg,#d4a843,#a0714a)", "overlay": "rgba(14,8,2,.88)",   "badge_color": "#d4a843",  "label": "📜 Histoire",      "prefix": "📜 HISTOIRE"},
    "insolite":      {"bar": "linear-gradient(90deg,#00e5ff,#1de9b6)", "overlay": "rgba(2,12,14,.88)",  "badge_color": "#00e5ff",  "label": "😲 Insolite",      "prefix": "😲 INSOLITE"},
    "sport":         {"bar": "linear-gradient(90deg,#448aff,#304ffe)", "overlay": "rgba(2,6,14,.88)",   "badge_color": "#82b1ff",  "label": "🏆 Sport",         "prefix": "🏆 SPORT"},
    "science":       {"bar": "linear-gradient(90deg,#7c4dff,#651fff)", "overlay": "rgba(2,6,16,.88)",   "badge_color": "#b388ff",  "label": "🔬 Science & Tech","prefix": "🔬 SCIENCE"},
}

CATEGORY_EMOJIS = {
    "breaking": "🚨", "international": "🌍", "politique": "🏛️",
    "economie": "📈", "societe": "👥",       "histoire": "📜",
    "insolite": "😲", "sport": "🏆",         "science": "🔬",
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
    conn.commit()
    return conn

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
    prefix = CATEGORY_STYLES.get(category, {}).get("prefix", "📰")

    extras = {
        "histoire": "Commence par '📜 Il y a X ans...' et rends-le fascinant.",
        "insolite": "Rends-le fun et surprenant. Emoji 😲 ou 🤯 bienvenu.",
        "breaking": "Urgence et clarté avant tout.",
        "science":  "Explique simplement, sans jargon.",
    }
    extra = extras.get(category, "")

    video_instruction = ""
    if video_url:
        video_instruction = f"""
IMPORTANT : intègre ce lien vidéo YouTube de façon naturelle dans le tweet ou à la fin.
URL vidéo : {video_url}
Formulation suggérée : "📹 Voir en vidéo : {video_url}" ou intégré dans le texte."""

    prompt = f"""Tu es community manager de Pulse, compte Twitter actu France & monde.
Aujourd'hui : {today}. Catégorie : {category}. {extra}

Article :
Source : {source}
Titre : {title}
Résumé : {summary}
{video_instruction}

RÈGLE IMPORTANTE sur le format :
- Si PAS de vidéo et PAS d'image : commence OBLIGATOIREMENT par "{prefix} —" pour identifier la catégorie
- Si image ou vidéo présente : pas besoin du préfixe, l'image/vidéo identifie déjà
- Tweet unique si ≤ 280 caractères (lien vidéo compris)
- Thread de 3-5 tweets si le sujet est riche

Réponds UNIQUEMENT avec ce JSON :
Tweet : {{"type":"tweet","content":"..."}}
Thread : {{"type":"thread","content":["1/N...","2/N..."]}}

Règles : vrai et fidèle · ton direct · max 3 emojis/tweet
· 📰 {source} à la fin · max 2 hashtags"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE TWEET — DA Pulse
# ─────────────────────────────────────────────────────────────────────────────

def build_tweet_image_html(headline, source, category, photo_url=None):
    s        = CATEGORY_STYLES.get(category, CATEGORY_STYLES["international"])
    # Date en français
    mois = ["jan","fév","mar","avr","mai","juin","juil","août","sep","oct","nov","déc"]
    now  = datetime.now()
    date_str = f"{now.day} {mois[now.month-1]} {now.year}"

    # Tronquer le titre
    h = headline if len(headline) <= 80 else headline[:77] + "..."
    # Taille de police adaptée à la longueur
    font_size = "22px" if len(h) > 60 else "26px"

    photo_style = ""
    if photo_url:
        photo_style = f"background-image:url('{photo_url}');background-size:cover;background-position:center;"

    return f"""<table width="580" cellpadding="0" cellspacing="0" style="border-radius:14px;overflow:hidden;margin:0 auto;" bgcolor="#0d0d14">
  <tr><td style="height:9px;background:{s['bar']};font-size:0;">&nbsp;</td></tr>
  <tr>
    <td style="padding:0;position:relative;">
      <table width="580" cellpadding="0" cellspacing="0">
        <tr>
          <td width="580" height="290" style="{photo_style}opacity:1;font-size:0;" bgcolor="#0d0d14">
            <table width="580" cellpadding="0" cellspacing="0" style="background:rgba(0,0,0,0);">
              <tr><td style="background:{s['overlay']};padding:18px 22px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-family:Georgia,serif;font-style:italic;font-weight:900;font-size:20px;color:#fff;letter-spacing:-1px;">Pulse</td>
                    <td align="right" style="font-family:Arial,sans-serif;font-size:10px;font-weight:bold;color:{s['badge_color']};border:1px solid {s['badge_color']};border-radius:20px;padding:3px 10px;white-space:nowrap;">{s['label']}</td>
                  </tr>
                </table>
                <div style="height:20px;"></div>
                <p style="font-family:Georgia,serif;font-style:italic;font-weight:900;font-size:{font_size};color:#fff;line-height:1.25;margin:0;padding:0;">{h}</p>
                <div style="height:20px;"></div>
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-family:Arial;font-size:11px;color:rgba(255,255,255,.4);">📰 {source}</td>
                    <td align="right" style="font-family:Arial;font-size:11px;color:rgba(255,255,255,.22);">{date_str}</td>
                  </tr>
                </table>
              </td></tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""
# ─────────────────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def build_email(tweet_result, analysis, title, url, source, score, image_html, video):
    cat       = analysis.get("category", "international")
    emoji     = CATEGORY_EMOJIS.get(cat, "📰")
    s         = CATEGORY_STYLES.get(cat, CATEGORY_STYLES["international"])
    badge_col = s["badge_color"]

    subject = f"{emoji} Pulse · {source} · {score}/10 · {title[:52]}..."

    # Bloc vidéo
    video_html = ""
    if video:
        video_html = f"""
        <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:14px 16px;margin-top:16px;display:flex;align-items:center;gap:14px;">
          <span style="font-size:28px;">🎬</span>
          <div>
            <p style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;margin:0 0 4px;font-family:Arial;">Vidéo YouTube associée</p>
            <p style="font-size:14px;font-weight:bold;color:#eee;margin:0 0 4px;font-family:Arial;">{video['title'][:70]}</p>
            <p style="font-size:12px;color:#666;margin:0 0 6px;font-family:Arial;">{video['channel']}</p>
            <a href="{video['url']}" style="font-size:13px;color:{badge_col};text-decoration:none;font-family:Arial;">▶ {video['url']}</a>
          </div>
        </div>"""

    # Tweets
    if tweet_result["type"] == "tweet":
        tweets_html = f"""
        <p style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px;font-family:Arial;">Tweet — copie et poste sur X</p>
        <div style="background:#f7f7f9;border:1px solid #e0e0e8;border-radius:14px;padding:18px;font-size:16px;line-height:1.7;color:#111;white-space:pre-wrap;font-family:Arial;">{tweet_result['content']}</div>"""
    else:
        n = len(tweet_result["content"])
        tweets_html = f'<p style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px;font-family:Arial;">Thread · {n} tweets</p>'
        for i, t in enumerate(tweet_result["content"], 1):
            tweets_html += f"""
            <p style="font-size:10px;color:#aaa;margin:12px 0 4px;font-family:Arial;">— Tweet {i}/{n} —</p>
            <div style="background:#f7f7f9;border:1px solid #e0e0e8;border-radius:14px;padding:16px;font-size:15px;line-height:1.7;color:#111;white-space:pre-wrap;font-family:Arial;">{t}</div>"""

    score_color = "#ff4444" if score < 5 else "#ffc107" if score < 7 else "#00c853"
    score_pct   = score * 10

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0c0c14;">
<div style="max-width:640px;margin:0 auto;padding:28px 16px;">

  <div style="background:linear-gradient(135deg,#1c0860 0%,#6020cc 55%,#c44dff 100%);border-radius:16px 16px 0 0;padding:22px 28px 18px;">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <div>
        <span style="font-family:Georgia,serif;font-style:italic;font-weight:900;font-size:30px;color:#fff;letter-spacing:-1.5px;">Pulse</span>
        <span style="font-size:11px;color:rgba(255,255,255,.45);margin-left:10px;letter-spacing:2px;font-family:Arial;">INSUFFLER L'ACTU.</span>
      </div>
      <span style="font-size:12px;color:rgba(255,255,255,.4);font-family:Arial;">{datetime.now().strftime('%d/%m/%Y · %H:%M')}</span>
    </div>
  </div>

  <div style="background:#16161f;padding:24px 28px;border-radius:0 0 16px 16px;border:1px solid rgba(255,255,255,.05);">

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:10px;">
      <span style="background:rgba(255,255,255,.07);border:1px solid {badge_col}55;border-radius:100px;padding:6px 16px;font-size:12px;font-weight:bold;color:{badge_col};font-family:Arial;">{s['label']}</span>
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:11px;color:#444;font-family:Arial;text-transform:uppercase;letter-spacing:1px;">Intérêt</span>
        <div style="background:#222230;border-radius:100px;width:90px;height:5px;overflow:hidden;">
          <div style="background:{score_color};width:{score_pct}%;height:100%;border-radius:100px;"></div>
        </div>
        <span style="font-size:13px;font-weight:bold;color:{score_color};font-family:Arial;">{score}/10</span>
      </div>
    </div>

    <div style="background:rgba(255,255,255,.03);border-left:3px solid {badge_col};padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:20px;">
      <p style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;margin:0 0 5px;font-family:Arial;">📰 {source}</p>
      <p style="font-size:16px;font-weight:bold;color:#eee;margin:0;line-height:1.4;font-family:Arial;">{title}</p>
      <p style="font-size:11px;color:#444;margin:7px 0 0;font-style:italic;font-family:Arial;">{analysis.get('reason','')}</p>
    </div>

    {'<p style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;margin:0 0 10px;font-family:Arial;">Image pour X</p>' + image_html if image_html else ''}

    {video_html}

    <div style="margin-top:22px;">{tweets_html}</div>

    <div style="margin-top:22px;padding-top:18px;border-top:1px solid rgba(255,255,255,.05);">
      <a href="{url}" style="display:inline-block;background:linear-gradient(135deg,#3b1fff,#9c27b0);color:#fff;padding:11px 24px;border-radius:100px;text-decoration:none;font-size:13px;font-weight:bold;font-family:Arial;">🔗 Lire l'article original</a>
    </div>

    <p style="margin-top:18px;font-size:11px;color:#2a2a35;text-align:center;font-family:Arial;">Pulse × Claude AI — copie le tweet et poste-le sur X ✨</p>
  </div>
</div>
</body></html>"""

    return subject, html


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html_body, "html", "utf-8"))
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

    # 3. Meilleurs en tête
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:MAX_PAR_PASSE]
    print(f"  → {len(top)} sélectionné(s).")

    # 4. Générer + envoyer
    for item in top:
        try:
            add_recent_title(conn, item["title"])
            cat      = item["analysis"]["category"]
            a        = item["analysis"]

            # Image article
            photo    = extract_image(item["entry"])
            img_html = ""
            if a.get("needs_image") or photo:
                img_html = build_tweet_image_html(item["title"], item["source"], cat, photo)

            # Vidéo YouTube (seulement si Claude le juge utile)
            video = None
            if a.get("needs_video") and YOUTUBE_API_KEY:
                video = find_relevant_video(item["title"], item["summary"], cat)

            # Tweet (avec ou sans vidéo, avec ou sans image → préfixe adapté)
            has_visual = bool(img_html or video)
            tweet = generate_tweet_content(
                item["title"], item["summary"], item["source"], cat,
                video_url=video["url"] if video else None
            )

            # Email
            subject, html = build_email(
                tweet, a, item["title"], item["url"],
                item["source"], item["score"], img_html, video
            )
            send_email(subject, html)
            print(f"  📧 Envoyé {'📹' if video else '🖼️' if img_html else '📝'} : {item['title'][:50]}...")
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
