"""
Pulse NewsBot — bot d'actualité française.
Génère des tweets engageants avec image PNG, envoyés par email + posté sur X.
"""
import feedparser, anthropic, sqlite3, hashlib, json, time, os, smtplib, random
import socket
socket.setdefaulttimeout(12)   # aucun flux RSS/site mort ne peut geler un run
import urllib.request, urllib.parse, urllib.error, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════
GMAIL_ADDRESS     = os.environ.get("GMAIL_ADDRESS",     "")
GMAIL_APP_PASS    = os.environ.get("GMAIL_APP_PASS",    "")
EMAIL_TO          = os.environ.get("EMAIL_TO",          "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
YOUTUBE_API_KEY   = os.environ.get("YOUTUBE_API_KEY",   "")
UNSPLASH_KEY      = os.environ.get("UNSPLASH_KEY",      "")

TWITTER_API_KEY             = os.environ.get("TWITTER_API_KEY",             "")
TWITTER_API_SECRET          = os.environ.get("TWITTER_API_SECRET",          "")
TWITTER_ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN",        "")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")

FACEBOOK_PAGE_TOKEN = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
FACEBOOK_PAGE_ID    = os.environ.get("FACEBOOK_PAGE_ID",    "")

INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
IMGBB_KEY            = os.environ.get("IMGBB_KEY",            "")

SCORE_MINIMUM = 6
MAX_PAR_PASSE = 1
BREAKING_SCORE = 9        # score minimum (analyse Claude) pour qu'une actu soit publiée en "breaking"
BUZZ_SCORE = 7            # score minimum pour un fast-track "buzz" (multi-sources) — label normal, pas URGENT
BREAKING_SOURCES = 3      # nb de sources distinctes couvrant le même sujet pour déclencher le breaking
BREAKING_GAP_MIN = 25     # délai mini (minutes) entre deux publications breaking (anti-spam)
SPORT_COOLDOWN_MIN = 120  # délai mini (minutes) entre deux posts SPORT (anti-spam sport en direct)

# ═══════════════════════════════════════════════════════════════════════════
# SOURCES RSS — France
# ═══════════════════════════════════════════════════════════════════════════
RSS_FEEDS = [
    # 🇫🇷 Grands quotidiens nationaux
    {"url": "https://www.lemonde.fr/rss/une.xml",                      "source": "Le Monde"},
    {"url": "https://www.lefigaro.fr/rss/figaro_actualites.xml",       "source": "Le Figaro"},
    {"url": "https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml", "source": "Libération"},
    {"url": "https://www.lepoint.fr/rss.xml",                          "source": "Le Point"},
    {"url": "https://www.lexpress.fr/rss/alaune.xml",                  "source": "L'Express"},
    # 📺 Médias temps réel (style breaking)
    {"url": "https://www.bfmtv.com/rss/news-24-7/",                    "source": "BFMTV"},
    {"url": "https://www.francetvinfo.fr/titres.rss",                  "source": "France Info"},
    {"url": "https://www.francebleu.fr/rss/a-la-une.xml",              "source": "France Bleu"},
    {"url": "https://www.cnews.fr/rss/a-la-une.xml",                   "source": "CNEWS"},
    # 📰 Quotidiens populaires + faits divers
    {"url": "https://www.leparisien.fr/rss.xml",                       "source": "Le Parisien"},
    {"url": "https://www.20minutes.fr/feeds/rss-une.xml",              "source": "20 Minutes"},
    {"url": "https://www.ouest-france.fr/rss/une",                     "source": "Ouest-France"},
    {"url": "https://www.sudouest.fr/essentiel/rss.xml",               "source": "Sud Ouest"},
    {"url": "https://www.ladepeche.fr/rss.xml",                        "source": "La Dépêche"},
    {"url": "https://www.midilibre.fr/rss.xml",                        "source": "Midi Libre"},
    {"url": "https://www.nicematin.com/feed/rss/derniere-minute",      "source": "Nice-Matin"},
    {"url": "https://www.estrepublicain.fr/rss/derniere-minute",       "source": "Est Républicain"},
    # 🏛️ Politique / Économie
    {"url": "https://www.lesechos.fr/rss/rss_la_une.xml",              "source": "Les Echos"},
    {"url": "https://www.latribune.fr/rss/rubriques/actualite.html",   "source": "La Tribune"},
    {"url": "https://www.challenges.fr/rss.xml",                       "source": "Challenges"},
    # 💻 Tech / IA / Innovation
    {"url": "https://www.numerama.com/feed/",                          "source": "Numerama"},
    {"url": "https://www.frandroid.com/feed",                          "source": "Frandroid"},
    {"url": "https://www.01net.com/rss/actualites.xml",                "source": "01net"},
    {"url": "https://www.zdnet.fr/feeds/rss/actualites/",              "source": "ZDNet"},
    # 🔬 Science
    {"url": "https://www.sciencesetavenir.fr/rss.xml",                 "source": "Sciences et Avenir"},
    # 🏆 Sport
    {"url": "https://www.lequipe.fr/rss/actu_rss.xml",                 "source": "L'Équipe"},
    {"url": "https://www.eurosport.fr/rss.xml",                        "source": "Eurosport"},
    # ❤️ Positivité / Insolite
    {"url": "https://positivr.fr/feed/",                               "source": "Positivr"},
    # 🎬 Pop culture / Réseaux sociaux / Créateurs / Buzz
    {"url": "https://www.konbini.com/fr/feed/",                        "source": "Konbini"},
    {"url": "https://www.dexerto.fr/feed/",                      "source": "Dexerto"},
    {"url": "https://www.bfmtv.com/rss/people/",                 "source": "BFM People"},
    {"url": "https://www.public.fr/feed",                        "source": "Public"},
    {"url": "https://www.numerama.com/pop-culture/feed/",              "source": "Numerama Pop"},
    {"url": "https://www.melty.fr/feed",                               "source": "Melty"},
    {"url": "https://www.programme-tv.net/rss/actualites.xml",         "source": "Programme TV"},
    {"url": "https://www.premiere.fr/rss/actualite",                   "source": "Première"},
    # 🎮 Geek / Jeux vidéo / YouTubeurs / Créateurs
    {"url": "https://www.journaldugeek.com/feed/",                     "source": "Journal du Geek"},
    {"url": "https://www.begeek.fr/feed",                              "source": "Begeek"},
    {"url": "https://www.jeuxvideo.com/rss/rss.xml",                   "source": "Jeuxvideo.com"},
    {"url": "https://www.gamekult.com/feed.xml",                       "source": "Gamekult"},
    {"url": "https://www.actugaming.net/feed/",                        "source": "ActuGaming"},
    {"url": "https://www.millenium.org/feed",                          "source": "Millenium"},
    {"url": "https://fr.ign.com/feed.xml",                             "source": "IGN France"},
]

# ═══════════════════════════════════════════════════════════════════════════
# DA PULSE — styles, préfixes, labels
# ═══════════════════════════════════════════════════════════════════════════
STYLES = {
    "breaking":      {"color": "#ff6868", "label": "Breaking",      "bar": [(255,32,32),(255,96,48)],     "overlay": (18,3,3)},
    "france":        {"color": "#64b5f6", "label": "France",        "bar": [(33,150,243),(0,184,212)],    "overlay": (3,10,22)},
    "monde":         {"color": "#80d8ff", "label": "Monde",         "bar": [(0,176,255),(0,229,255)],     "overlay": (2,8,18)},
    "politique":     {"color": "#ffd54f", "label": "Politique",     "bar": [(255,193,7),(255,152,0)],     "overlay": (12,10,2)},
    "economie":      {"color": "#69f0ae", "label": "Eco",           "bar": [(0,230,118),(0,191,165)],     "overlay": (2,12,5)},
    "societe":       {"color": "#ce93d8", "label": "Société",       "bar": [(206,147,216),(156,39,176)],  "overlay": (10,4,20)},
    "faitsdivers":   {"color": "#f48fb1", "label": "Faits Divers",  "bar": [(244,143,177),(233,30,99)],   "overlay": (16,4,8)},
    "hommage":       {"color": "#cfd2dd", "label": "Hommage",       "bar": [(176,180,194),(120,124,140)], "overlay": (10,10,14)},
    "histoire":      {"color": "#d4a843", "label": "Histoire",      "bar": [(212,168,67),(160,113,74)],   "overlay": (14,8,2)},
    "culture":       {"color": "#00e5ff", "label": "Culture",       "bar": [(0,229,255),(29,233,182)],    "overlay": (2,12,14)},
    "sport":         {"color": "#82b1ff", "label": "Sport",         "bar": [(68,138,255),(48,79,254)],    "overlay": (2,6,14)},
    "science":       {"color": "#b388ff", "label": "Science",       "bar": [(124,77,255),(101,31,255)],   "overlay": (2,6,16)},
    "sante":         {"color": "#ff8a80", "label": "Santé",         "bar": [(255,138,128),(244,67,54)],   "overlay": (16,4,4)},
    "environnement": {"color": "#80e27e", "label": "Environnement", "bar": [(128,226,126),(76,175,80)],   "overlay": (4,14,4)},
    "tech":          {"color": "#40c4ff", "label": "Tech",          "bar": [(64,196,255),(0,176,255)],    "overlay": (2,8,18)},
    "ia":            {"color": "#e040fb", "label": "IA",            "bar": [(224,64,251),(170,0,255)],    "overlay": (10,2,20)},
    "insolite":      {"color": "#ffd180", "label": "Insolite",      "bar": [(255,209,128),(255,152,0)],   "overlay": (16,10,2)},
    "positivity":    {"color": "#ff80ab", "label": "Positif",       "bar": [(255,128,171),(244,143,177)], "overlay": (20,4,12)},
}

EMOJIS = {
    "breaking": "🚨", "france": "🇫🇷", "monde": "🌍", "politique": "🏛️",
    "economie": "📈", "societe": "👥", "faitsdivers": "🚓", "hommage": "🕊️", "histoire": "📜",
    "culture": "🎭",  "sport": "🏆", "science": "🔬",
    "sante":    "🏥", "environnement": "🌱",
    "tech":     "💻", "ia": "🤖", "insolite": "😲", "positivity": "❤️",
}

LABELS = {
    "breaking": "URGENT", "france": "FRANCE", "monde": "MONDE",
    "politique": "POLITIQUE", "economie": "ECO",
    "societe": "SOCIÉTÉ", "faitsdivers": "FAITS DIVERS", "hommage": "HOMMAGE",
    "histoire": "HISTOIRE",
    "culture": "CULTURE", "sport": "SPORT",
    "science": "SCIENCE", "sante": "SANTÉ",
    "environnement": "ENVIRONNEMENT",
    "tech": "TECH", "ia": "IA",
    "insolite": "INSOLITE", "positivity": "POSITIF",
}

UNSPLASH_FALLBACK = {
    "breaking":      "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=1200&q=95",
    "france":        "https://images.unsplash.com/photo-1502602898657-3e91760cbb34?w=1200&q=95",
    "monde":         "https://images.unsplash.com/photo-1526778548025-fa2f459cd5c1?w=1200&q=95",
    "politique":     "https://images.unsplash.com/photo-1541872703-74c5e44368f9?w=1200&q=95",
    "economie":      "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1200&q=95",
    "societe":       "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=1200&q=95",
    "faitsdivers":   "https://images.unsplash.com/photo-1453873531674-2151bcd01707?w=1200&q=95",
    "hommage":       "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=1200&q=95",
    "histoire":      "https://images.unsplash.com/photo-1461360370896-922624d12aa1?w=1200&q=95",
    "culture":       "https://images.unsplash.com/photo-1499364615650-ec38552f4f34?w=1200&q=95",
    "sport":         "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=1200&q=95",
    "science":       "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=95",
    "sante":         "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=1200&q=95",
    "environnement": "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1200&q=95",
    "tech":          "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200&q=95",
    "ia":            "https://images.unsplash.com/photo-1677442136019-21780ecad995?w=1200&q=95",
    "insolite":      "https://images.unsplash.com/photo-1532009324734-20a7a5813719?w=1200&q=95",
    "positivity":    "https://images.unsplash.com/photo-1518621736915-f3b1c41bfd00?w=1200&q=95",
}

# ═══════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES
# ═══════════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect("seen_articles.db")
    for sql in [
        "CREATE TABLE IF NOT EXISTS seen (hash TEXT PRIMARY KEY, title TEXT, seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS recent_titles (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS category_log (category TEXT PRIMARY KEY, last_sent TEXT DEFAULT '2000-01-01')",
        """CREATE TABLE IF NOT EXISTS analyzed_cache (
            hash TEXT PRIMARY KEY,
            score INTEGER, category TEXT,
            is_duplicate INTEGER, needs_video INTEGER,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # Nouveau : log des mots-clés publiés pour bloquer la répétition
        """CREATE TABLE IF NOT EXISTS keyword_log (
            keyword TEXT PRIMARY KEY,
            last_sent TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # Suivi des threads et sondages (anti-répétition sur 7 jours)
        """CREATE TABLE IF NOT EXISTS special_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            keywords TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]:
        conn.execute(sql)
    conn.execute("DELETE FROM analyzed_cache WHERE analyzed_at < datetime('now', '-24 hours')")
    conn.execute("DELETE FROM keyword_log    WHERE last_sent   < datetime('now', '-12 hours')")
    conn.execute("DELETE FROM special_log    WHERE sent_at     < datetime('now', '-8 days')")
    conn.execute("DELETE FROM seen           WHERE seen_at     < datetime('now', '-45 days')")   # la base reste légère
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
    return [r[0] for r in conn.execute("SELECT title FROM recent_titles ORDER BY added_at DESC LIMIT 50").fetchall()]

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

def get_cached_analysis(conn, url):
    h   = hashlib.md5(url.encode()).hexdigest()
    row = conn.execute("SELECT score, category, is_duplicate, needs_video FROM analyzed_cache WHERE hash=?", (h,)).fetchone()
    if row:
        return {"score": row[0], "category": row[1], "is_duplicate": bool(row[2]), "needs_video": bool(row[3])}
    return None

def cache_analysis(conn, url, analysis):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute(
        "INSERT OR REPLACE INTO analyzed_cache (hash, score, category, is_duplicate, needs_video) VALUES (?,?,?,?,?)",
        (h, int(analysis.get("score", 0)), analysis.get("category", ""),
         1 if analysis.get("is_duplicate") else 0,
         1 if analysis.get("needs_video") else 0)
    )
    conn.commit()

def recent_keywords(conn, hours=12):
    """Retourne les mots-clés majeurs publiés dans les dernières heures."""
    return [r[0] for r in conn.execute(
        "SELECT keyword FROM keyword_log WHERE last_sent > datetime('now', ?)",
        (f"-{hours} hours",)
    ).fetchall()]

def log_keywords(conn, keywords):
    """Enregistre les mots-clés majeurs d'un tweet qui vient d'être publié."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for kw in keywords:
        kw = kw.lower().strip()
        if kw:
            conn.execute(
                "INSERT OR REPLACE INTO keyword_log (keyword, last_sent) VALUES (?, ?)",
                (kw, now)
            )
    conn.commit()

def special_done_today(conn, kind):
    """Vrai si un thread/sondage a déjà été publié aujourd'hui."""
    today = datetime.now().strftime("%Y-%m-%d")
    return conn.execute(
        "SELECT 1 FROM special_log WHERE kind=? AND sent_at LIKE ?",
        (kind, f"{today}%")
    ).fetchone() is not None

def recent_special_topics(conn, kind, days=7):
    """Sujets de threads/sondages des N derniers jours (anti-répétition)."""
    rows = conn.execute(
        "SELECT keywords FROM special_log WHERE kind=? AND sent_at > datetime('now', ?)",
        (kind, f"-{days} days")
    ).fetchall()
    return [r[0] for r in rows]

def log_special(conn, kind, keywords):
    conn.execute(
        "INSERT INTO special_log (kind, keywords) VALUES (?, ?)",
        (kind, ", ".join(keywords))
    )
    conn.commit()

def last_publish_time(conn):
    row = conn.execute("SELECT MAX(last_sent) FROM category_log WHERE last_sent != '2000-01-01'").fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    except:
        return None

# ── Plafond quotidien GLOBAL de publications (toutes sources confondues) ──
DAILY_POST_CAP = 22          # objectif ~20-25 posts/jour : plafond ferme à 22 (breaking+sport+normaux)
DAILY_POST_SOFT = 17         # au-delà, on ne garde QUE le très chaud (breaking/résultats forts)

def posts_today(conn):
    """Nombre de publications déjà faites aujourd'hui (chaque post passe par add_recent)."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM recent_titles WHERE date(added_at) = date('now')").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0

def _paris_hour():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).hour
    except Exception:
        from datetime import timezone, timedelta
        return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=2))).hour

def _cadence_minutes(h):
    """Rythme de publication. Base ~1h30 partout (probabilité croissante jusqu'à 2h30),
    nuit fortement ralentie. PLUS d'accélération prime-time (trop coûteux). Les alertes
    (breaking, résultats sport) restent prioritaires et ne passent pas par ce rythme."""
    if 0 <= h < 7:
        return 180, 300, "nuit (quasi-pause, le breaking passe toujours)"
    return 90, 150, "journée (base 1h30)"

def should_publish_now(conn, min_minutes=None, max_minutes=None):
    if min_minutes is None or max_minutes is None:
        h = _paris_hour()
        min_minutes, max_minutes, mode = _cadence_minutes(h)
        print(f"  🕐 {h}h (Paris) — rythme {mode} : {min_minutes}-{max_minutes} min")
    last = last_publish_time(conn)
    if not last:
        return True
    elapsed = (datetime.now() - last).total_seconds() / 60
    if elapsed < min_minutes:
        print(f"  ⏸️  Dernière publi il y a {int(elapsed)} min — attente.")
        return False
    if elapsed > max_minutes:
        print(f"  ✅ Dernière publi il y a {int(elapsed)} min — on publie.")
        return True
    proba = (elapsed - min_minutes) / (max_minutes - min_minutes)
    publish = random.random() < proba
    if publish:
        print(f"  🎲 {int(elapsed)} min (proba {int(proba*100)}%) → on publie.")
    else:
        print(f"  🎲 {int(elapsed)} min (proba {int(proba*100)}%) → on attend.")
    return publish

# ═══════════════════════════════════════════════════════════════════════════
# CLAUDE API
# ═══════════════════════════════════════════════════════════════════════════
def claude(prompt, max_tokens=600, model="claude-haiku-4-5-20251001"):
    """Appel Claude avec parsing JSON blindé + 1 nouvelle tentative en cas d'erreur réseau/API."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    last_err = None
    for attempt in (1, 2):
        try:
            msg = client.messages.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # Repli : extrait le premier objet JSON même si Claude a ajouté du texte autour
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
                raise
        except (anthropic.APIConnectionError, anthropic.APIStatusError, anthropic.RateLimitError) as e:
            last_err = e
            if attempt == 1:
                time.sleep(3)
                continue
            raise
    raise last_err

def claude_text(prompt, max_tokens=700, model="claude-haiku-4-5-20251001"):
    """Comme claude() mais renvoie du texte brut (pas de JSON)."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()

def analyse_batch(articles, recent, blocked_keywords):
    """Analyse plusieurs articles en un seul appel Claude."""
    if not articles:
        return []

    recent_str  = "\n".join(f"- {t}" for t in recent[:20]) or "Aucun"
    blocked_str = ", ".join(blocked_keywords) if blocked_keywords else "Aucun"
    today       = datetime.now().strftime("%d %B %Y")
    cats        = "|".join(LABELS.keys())

    articles_str = "\n\n".join(
        f"### Article {i+1}\nSource: {a['source']}\nTitre: {a['title']}\nRésumé: {a.get('summary','')[:150]}"
        for i, a in enumerate(articles)
    )

    prompt = f"""Tu es l'éditeur du compte Twitter Pulse, compte d'actualité française.
Aujourd'hui : {today}

Voici {len(articles)} articles à analyser.

Titres récemment publiés (à éviter en doublon sémantique) :
{recent_str}

⚠️ MOTS-CLÉS DÉJÀ PUBLIÉS dans les 12 dernières heures (NE PAS y revenir) :
{blocked_str}

Si un article traite d'un sujet contenant un de ces mots-clés bloqués, mets is_duplicate=true.

Articles :
{articles_str}

Réponds avec ce JSON UNIQUEMENT (un objet par article, dans le MÊME ORDRE) :
{{"analyses":[
  {{"id":1,"score":<0-10>,"category":"<{cats}>","is_duplicate":<true|false>,"needs_video":<true|false>}},
  ...
]}}

Barème = POTENTIEL D'ENGAGEMENT sur X en France (réactions, partages, commentaires). Question clé pour CHAQUE article : "Est-ce que des gens vont COMMENTER, S'INDIGNER, CÉLÉBRER, RIRE ou PARTAGER ?" Une info qui ne provoque AUCUNE émotion (colère, joie, choc, rire, fierté) = MAX 5, même si elle est "importante" sur le papier.

- 9-10 : fait MAJEUR en cours — mort d'une personnalité de premier plan, attentat, catastrophe, France qualifiée/éliminée en Coupe du Monde, démission du gouvernement, verdict d'un procès national. (Jamais : rapport, étude, sondage, classement, prévision → max 7.)
- 8 : ce qui fait halluciner ou vibrer la France. EXEMPLES CALIBRÉS : un arbitre de la Coupe du Monde privé de visa pour les USA = 8 ; l'usine du produit de YouTubeurs très connus (McFly et Carlito...) qui brûle = 8 ; un ministre s'exprime sur une affaire nationale brûlante = 8 ; grosse victoire des Bleus = 8 ; une banque envoie par erreur une notification de test à des millions de clients = 8 (insolite viral national).
- 7 : résultat de match notable, garde à vue d'une personnalité, buzz viral national, sortie d'un jeu très attendu, drama d'influenceur connu, fait divers marquant.
- 6 : insolite sympa, info locale forte, lancement notable grand public.
- 0-5 : le reste. EXEMPLES CALIBRÉS de scores BAS : "Apple ouvre les bundles d'abonnements entre éditeurs sur l'App Store" = 3 (annonce business B2B, tout le monde s'en fiche) ; partenariat entre entreprises = 3 ; mise à jour d'application = 2 ; étude/baromètre = 4 ; "ce qui pourrait changer d'ici 2030" = 3 ; revue de presse / "vu de l'étranger" / édito = 3 ; négociations européennes sur des quotas ou mécanismes = 3.

⛔ PLAFONDS STRICTS :
- Annonce produit/business/tech SANS émotion directe pour le grand public (bundles, partenariats, API, résultats trimestriels, levées de fonds, fonctionnalités) → MAX 4. Test : si la réaction attendue en commentaire est "🥱", c'est MAX 4.
- ⚠️ EXCEPTION : une DÉCISION POLITIQUE/RÉGLEMENTAIRE soudaine et radicale sur une techno grand public (interdiction, suspension, blocage, censure d'un service ou d'une IA connue type ChatGPT/Claude/TikTok) n'est PAS du B2B banal → score 7-8. C'est un coup de tonnerre qui fait réagir (ex : "les États-Unis interdisent tel modèle d'IA hors de leur territoire" = 7).
- FUTUR potentiel ou PROCESSUS technique ("pourrait", "envisage", "d'ici 20XX", négociations, quotas, consultations, projets de loi sans vote) → MAX 5.
- Angle ÉDITORIAL (revue de presse, "vu de l'étranger", tribune, portrait, décryptage d'un autre média) → MAX 5 : on veut le FAIT, pas le commentaire du fait.

🇫🇷 HIÉRARCHIE DE L'ENGAGEMENT en France :
1) Football (Bleus, Mbappé, PSG, OM, Coupe du Monde 2026, Ligue des champions)
2) Drames et faits divers majeurs (fusillade, incendie, disparition, procès médiatique)
3) Politique à CLASH (affaires, gardes à vue, démissions, punchlines — pas les textes techniques)
4) NBA/Wembanyama, Roland-Garros, Tour de France, F1, boxe/MMA
5) Influenceurs/people/télé (Squeezie, McFly et Carlito, Inoxtag, Hanouna...) et gaming (GTA, PlayStation, Nintendo) — une grosse actu ici vaut 7-8, autant que la politique chaude
6) Insolite viral (pannes nationales, bugs cocasses, records absurdes)
Un bon fil = un mix de tout ça. Une info locale marquante peut scorer aussi haut qu'une info internationale.

Catégories possibles (choisis la plus juste) :
breaking, france, monde, politique, economie, societe, faitsdivers, histoire,
culture (cinéma, musique, séries, célébrités, créateurs/influenceurs, YouTubeurs/streamers, jeux vidéo, gaming, esport, buzz réseaux sociaux, produits de célébrités),
sport, science, sante, environnement, tech, ia, insolite, positivity.

⚠️ CATÉGORIE "breaking" — TRÈS RESTRICTIVE : réservée aux FAITS urgents en direct (mort d'une personnalité, attentat, catastrophe naturelle, accident/crash grave, fusillade, résultat très attendu). Un rapport, une étude, une analyse, un sondage, un classement, une prévision ou un avis ne doit JAMAIS être catégorisé "breaking" — mets economie, politique, societe, etc. Le label rouge "URGENT" ne doit jamais apparaître sur ce type de contenu.

IMPORTANT : retourne EXACTEMENT {len(articles)} analyses dans le tableau."""

    result   = claude(prompt, max_tokens=max(500, len(articles) * 80))
    analyses = result.get("analyses", [])
    while len(analyses) < len(articles):
        analyses.append({"score": 0, "category": "france", "is_duplicate": False, "needs_video": False})
    return analyses[:len(articles)]

def gen_tweet_complet(title, summary, source, category, video_url=None, article_text=None, correction=None):
    """Génère tweet + titre image + image_query + mots-clés majeurs."""
    today = datetime.now().strftime("%d %B %Y")
    label = LABELS[category]
    video_str = ""
    art_str  = f"\n- EXTRAIT DE L'ARTICLE (fait foi sur les faits et qualifications) : {article_text[:1200]}" if article_text else ""
    corr_str = f"\n\n🚨 CORRECTION OBLIGATOIRE — ta version précédente contenait une ERREUR FACTUELLE : {correction}. Corrige-la impérativement." if correction else ""

    # Style adaptatif selon catégorie — TOUJOURS court et télégraphique (fil d'actu)
    if category == "hommage":
        style_instr = """STYLE HOMMAGE (décès d'une personne) :
- Ton SOBRE, respectueux et factuel — aucun sensationnalisme, aucune formule accrocheuse
- 1 à 2 phrases : qui était la personne, les circonstances si connues
- Pas de mot en MAJUSCULES pour l'emphase, pas de point d'exclamation"""
    elif category in ("breaking", "faitsdivers"):
        style_instr = """STYLE FLASH :
- 1 phrase factuelle et dense : les faits bruts (qui, quoi, où) + le chiffre clé
- Zéro analyse, zéro remplissage"""
    elif category == "positivity":
        style_instr = """STYLE POSITIF :
- 1 à 2 phrases, ton chaleureux mais bref
- Le fait marquant mis en avant, sans pathos"""
    else:
        style_instr = """STYLE INFO (télégraphique) :
- 1 à 2 phrases denses et factuelles : l'essentiel + le chiffre ou le fait clé
- Concis, pas de contexte superflu"""

    result = claude(f"""Tu es community manager de Pulse, compte Twitter d'actualité française.
Aujourd'hui : {today}.

Article à traiter :
- Source : {source}
- Titre  : {title}
- Résumé : {summary}{video_str}{art_str}{corr_str}

Génère QUATRE choses :

1. **headline_court** (max 75 caractères) : titre punchy pour l'image. Pas de hashtag, pas d'emoji.

2. **image_query** (max 5 mots, EN ANGLAIS) : recherche pour trouver une image pertinente.
   Ex: "Emmanuel Macron Elysee speech", "Paris metro station", "Iran flag Tehran protest"

3. **keywords_majeurs** (3 mots-clés en minuscules) : les mots-clés CENTRAUX du sujet, pour anti-répétition.
   Ex pour "Trump impose tarifs Chine" → ["trump", "tarifs", "chine"]
   Ex pour "Incendie 15e arrondissement Paris" → ["incendie", "paris", "15e"]
   Ex pour "Mbappé blessé entraînement" → ["mbappe", "blessure", "real"]

4. **person** : si l'article parle d'UNE personnalité publique précise (politique, sportif, artiste, créateur de contenu, PDG...), donne son nom complet tel qu'il apparaîtrait sur Wikipédia (ex: "Emmanuel Macron", "Kylian Mbappé", "Squeezie"). Sinon mets "".

5. **body** : corps du tweet (sans préfixe — il sera ajouté automatiquement).

{style_instr}

⚖️ RIGUEUR FACTUELLE ABSOLUE (sujets judiciaires, décès, accusations) — PRIORITÉ N°1 :
- Recopie les qualifications juridiques EXACTEMENT comme dans la source : "homicide involontaire" reste INVOLONTAIRE, jamais "meurtre" ni "volontaire". "Meurtre" = uniquement si la source écrit "meurtre". Idem pour assassinat, viol, agression, terrorisme, féminicide.
- Si la qualification n'est pas écrite dans la source, n'en mets AUCUNE (écris "mort de", "décès de", "mis en cause pour").
- Personne mise en cause/suspectée = TOUJOURS "soupçonné de", "présumé" (présomption d'innocence).
- N'invente JAMAIS un chiffre, un âge, un lieu ou une circonstance absents de la source.

RÈGLES STRICTES pour body — FIL D'ACTU COURT (façon CerfiaFR) :
- NE COMMENCE PAS par "{label}" ni aucune catégorie en majuscules ; va DIRECTEMENT à l'info.
- TÉLÉGRAPHIQUE : 1 à 2 phrases MAXIMUM, denses et autonomes, comme une dépêche. Info COMPLÈTE, jamais un teaser.
- Mets en avant le CHIFFRE ou le FAIT clé. Tu peux écrire UN mot ou chiffre important en MAJUSCULES pour l'emphase (avec parcimonie).
- ⛔ INTERDIT : les pavés, les paragraphes "conséquence/enjeu", les ouvertures "Et si...", "Saviez-vous que...", le remplissage.
- Longueur cible COURTE : environ 200 à 330 caractères. Jamais un long pavé.
- 🇫🇷 FRANÇAIS IMPECCABLE : aucun mot ni expression en anglais (traduis tout), aucune faute d'orthographe/grammaire/accord, aucun mot tronqué. RELIS-toi avant de répondre.
- 1 à 2 hashtags INTÉGRÉS DANS LES PHRASES (3 max si vraiment justifié) : colle "#" sur un mot DÉJÀ présent.
- 🎯 CHOIX DU HASHTAG — vise le SUJET, jamais le décor. Le hashtag principal = LE nom propre central de l'actu (entreprise, personne, club, événement, jeu vidéo). Test : "cette actu parle de quoi en UN mot ?" → c'est CE mot qui prend le #. Ex : actu sur l'entrée en Bourse de SpaceX → #SpaceX (PAS #Bourse ni #TimesSquare) ; actu sur Mbappé → #Mbappé (pas #football) ; match des Bleus → #CoupeDuMonde2026 ; sortie de GTA 6 → #GTA6.
- ⛔ Pas de hashtag décoratif ou périphérique : lieux secondaires, mots génériques (#Bourse, #France, #Justice, #Tech) sont INTERDITS sauf s'ils sont précisément LE sujet de l'actu.
- ⛔ INTÉGRATION PROPRE — ne casse JAMAIS le texte : ne DUPLIQUE pas un mot ("à Mexico #Mexico" = INTERDIT), ne mets pas de "#" au milieu d'un mot, n'ajoute pas de mot juste pour caser un hashtag, et NE mets PAS de bloc de hashtags à la fin. Le hashtag doit se lire naturellement dans la phrase.
- RETOUR À LA LIGNE après la 1ʳᵉ phrase (jamais un gros bloc lourd) : phrase d'accroche, puis LIGNE VIDE, puis la 2ᵉ phrase, puis LIGNE VIDE, puis la source. Soit : Phrase 1.\\n\\nPhrase 2.\\n\\n(Source)
- Exemple EXACT du rendu attendu (court, aéré, hashtags intégrés) :
  "À #Mexico, des MILLIERS de manifestants bloquent l'accès au stade à deux jours du match d'ouverture de la #CoupeDuMonde2026.\\n\\nIls réclament une hausse des salaires et l'abrogation d'une loi sur les retraites.\\n\\n(Le Figaro)"
- Dans le JSON, les sauts de ligne s'écrivent \\n

Réponds avec ce JSON UNIQUEMENT :
{{"headline_court":"...","image_query":"...","person":"...","keywords_majeurs":["..","..",".."], "body":"..."}}""", max_tokens=900)

    body = result.get("body", "").strip()
    for label_test in LABELS.values():
        body = re.sub(rf"^{label_test}\s*\|\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(rf"^{label_test}\s*[—-]\s*", "", body, flags=re.IGNORECASE)
    body = re.sub(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s]+", "", body).strip()

    headline_court = result.get("headline_court", title)[:80].strip()
    image_query    = result.get("image_query", category).strip()
    keywords       = result.get("keywords_majeurs", [])
    person         = result.get("person", "").strip()

    return body, headline_court, image_query, keywords, person

# ── GARDE-FOU FACTUEL : vérifie que les termes sensibles du tweet existent bien dans la source ──
SENSITIVE_TOPIC_RX = re.compile(r"mort|morte|décès|décéd|tué|homicide|meurtre|assassin|viol|agress|attentat|terroris|féminicide|procès|mis en examen|garde à vue|empoisonn", re.I)

def _fact_guard(body, source_text):
    """Renvoie la liste des erreurs factuelles détectées (termes sensibles absents de la source)."""
    b, s = body.lower(), source_text.lower()
    issues = []
    if "involontaire" in s and re.search(r"(?<!in)volontaire", b):
        issues.append('tu as écrit "volontaire" alors que la source dit "INVOLONTAIRE"')
    for term in ("meurtre", "assassinat", "viol", "terroriste", "terrorisme", "féminicide", "empoisonnement"):
        if term in b and term not in s:
            issues.append(f'tu as écrit "{term}" alors que ce mot n\'est PAS dans la source')
    return issues

def _fact_hardfix(body, source_text):
    """Dernier recours déterministe si la régénération échoue encore."""
    s = source_text.lower()
    if "involontaire" in s:
        body = re.sub(r"(?<![Ii]n)volontaires?", "involontaire", body)
    if "meurtre" not in s:
        body = re.sub(r"[Mm]eurtres?", "homicide", body)
    return body

def gen_tweet_verified(title, summary, source, category, url=None):
    """gen_tweet_complet + lecture de l'article UNIQUEMENT sur sujets sensibles
    (mort, procès, accusations… où la précision juridique est vitale) + vérification factuelle.
    Sur les sujets non sensibles, le titre + résumé RSS suffisent → coût minimal."""
    src_text = f"{title} {summary}"
    article_text = None
    # Lecture de l'article SEULEMENT si le sujet est sensible (sinon ça triple les tokens pour rien)
    if url and (SENSITIVE_TOPIC_RX.search(title) or SENSITIVE_TOPIC_RX.search(summary or "")):
        try:
            article_text = fetch_article_text(url, max_chars=1000)
            if article_text and len(article_text) > 150:
                src_text += " " + article_text
            else:
                article_text = None
        except Exception:
            article_text = None
    body, headline, image_query, keywords, person = gen_tweet_complet(
        title, summary, source, category, article_text=article_text)
    issues = _fact_guard(body + " " + headline, src_text)
    if issues:
        print(f"  ⚖️ Erreur factuelle détectée ({'; '.join(issues)}) → régénération")
        body, headline, image_query, keywords, person = gen_tweet_complet(
            title, summary, source, category, article_text=article_text, correction="; ".join(issues))
        if _fact_guard(body + " " + headline, src_text):
            body, headline = _fact_hardfix(body, src_text), _fact_hardfix(headline, src_text)
            print("  ⚖️ Correction forcée appliquée")
    return body, headline, image_query, keywords, person

def build_full_tweet(body, category):
    emoji = EMOJIS[category]
    label = LABELS[category]
    return f"{emoji} {label} | {body}"

# ═══════════════════════════════════════════════════════════════════════════
# IMAGES
# ═══════════════════════════════════════════════════════════════════════════
def search_unsplash(query, category):
    if UNSPLASH_KEY:
        try:
            url = f"https://api.unsplash.com/search/photos?query={urllib.parse.quote(query)}&per_page=1&client_id={UNSPLASH_KEY}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            if data.get("results"):
                return data["results"][0]["urls"]["regular"]
        except: pass
    try:
        return f"https://source.unsplash.com/1200x675/?{urllib.parse.quote(query)}"
    except:
        return UNSPLASH_FALLBACK.get(category)

def fetch_img(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception as e:
        print(f"  ⚠️ Fetch image: {e}")
        return None

def fetch_og_image(article_url):
    """Récupère l'image HD (og:image) depuis la page de l'article — bien meilleure que la miniature RSS."""
    if not article_url:
        return None
    try:
        req = urllib.request.Request(article_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(400000).decode("utf-8", errors="ignore")
        # Plusieurs sources possibles, par ordre de préférence
        patterns = [
            r'property=["\']og:image:secure_url["\']',
            r'property=["\']og:image:url["\']',
            r'property=["\']og:image["\']',
            r'name=["\']twitter:image:src["\']',
            r'name=["\']twitter:image["\']',
            r'name=["\']thumbnail["\']',
        ]
        for prop in patterns:
            m = re.search(r'<meta[^>]+' + prop + r'[^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+' + prop, html, re.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith("//"):
                    img = "https:" + img
                if img.startswith("http"):
                    return img
        # Dernier recours : <link rel="image_src">
        m = re.search(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            img = m.group(1).strip()
            if img.startswith("//"):
                img = "https:" + img
            if img.startswith("http"):
                return img
    except Exception as e:
        print(f"  ⚠️ og:image: {e}")
    return None

def fetch_wikipedia_portrait(name):
    """Récupère une photo HD d'une personnalité depuis Wikipedia FR."""
    if not name:
        return None
    try:
        api = ("https://fr.wikipedia.org/w/api.php?action=query&titles="
               + urllib.parse.quote(name)
               + "&prop=pageimages&pithumbsize=1200&format=json&redirects=1")
        req = urllib.request.Request(api, headers={"User-Agent": "PulseBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for _, page in pages.items():
            thumb = page.get("thumbnail", {}).get("source")
            if thumb:
                return thumb
    except Exception as e:
        print(f"  ⚠️ Wikipedia portrait: {e}")
    return None

def img_dimensions_ok(raw, min_w=600, min_h=400):
    """Vérifie qu'une image est assez grande pour ne pas être floue une fois agrandie."""
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        return w >= min_w and h >= min_h
    except:
        return False

def detect_face_center(pil_img):
    """
    Retourne (cx, cy) du centre du plus grand visage détecté, ou None.
    Utilise OpenCV (Haar cascade). Si OpenCV absent ou aucun visage, retourne None
    (on retombe alors sur le cadrage par défaut). Aucun coût API.
    """
    try:
        import cv2, numpy as np
        arr  = np.array(pil_img.convert("RGB"))[:, :, ::-1].copy()  # RGB -> BGR
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(50, 50))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # le plus grand visage
        return (x + w / 2.0, y + h / 2.0)
    except Exception:
        return None

def fetch_article_images(article_url, max_imgs=8):
    """Récupère les VRAIES photos d'un article (og:image + grandes <img> de la page),
    triées par priorité/taille. Filtre logos, icônes, pubs, pixels de tracking, SVG/GIF."""
    if not article_url:
        return []
    try:
        req = urllib.request.Request(article_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            base = r.geturl()
            html = r.read(600000).decode("utf-8", errors="ignore")
    except Exception:
        return []
    cands = []
    for prop in (r'og:image:secure_url', r'og:image:url', r'og:image',
                 r'twitter:image:src', r'twitter:image'):
        for m in re.finditer(r'<meta[^>]+(?:property|name)=["\']' + prop + r'["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
            cands.append((m.group(1).strip(), 10_000_000))
    for im in re.finditer(r'<img[^>]+>', html, re.I):
        tag = im.group(0)
        src = None
        msrc = re.search(r'(?:data-src|data-original|src)=["\']([^"\']+)["\']', tag, re.I)
        if msrc:
            src = msrc.group(1).strip()
        mset = re.search(r'srcset=["\']([^"\']+)["\']', tag, re.I)
        if mset:
            parts = [p.strip().split(" ")[0] for p in mset.group(1).split(",") if p.strip()]
            if parts:
                src = parts[-1]
        if not src:
            continue
        w = h = 0
        mw = re.search(r'width=["\']?(\d+)', tag); mh = re.search(r'height=["\']?(\d+)', tag)
        if mw: w = int(mw.group(1))
        if mh: h = int(mh.group(1))
        cands.append((src, w * h if (w and h) else 1))
    seen, out = set(), []
    BAD = ("logo", "icon", "sprite", "avatar", "placeholder", "pixel", "tracking",
           "/ads/", "advert", "banner", "1x1", "blank.", "spacer", "favicon", ".svg", ".gif",
           "emoji", "share", "btn", "button", "widget")
    for url, score in sorted(cands, key=lambda x: -x[1]):
        if not url or url.startswith("data:"):
            continue
        full = urllib.parse.urljoin(base, url)
        low = full.lower()
        if any(b in low for b in BAD):
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
        if len(out) >= max_imgs:
            break
    return out

def get_best_image(article_url, photo_url, person, image_query, category, allow_stock=False):
    """Choisit la MEILLEURE VRAIE photo en rapport avec l'article. Ordre :
    1. Toutes les images de l'article (og:image + grandes <img>), la plus grande et nette d'abord
    2. Miniature RSS si assez grande
    3. Portrait Wikipedia de la personnalité citée
    Par défaut JAMAIS de stock (allow_stock=False) → renvoie (None, False) si aucune vraie photo,
    pour que l'appelant reporte le post plutôt que publier une image générique.
    Retourne (raw_bytes, is_real_photo)."""
    HQ_W, HQ_H = 500, 320   # seuil raisonnable : accepte les photos de presse standard (640×427, 600×400...)

    # 1. VRAIES photos de l'article : on teste les candidats, on garde la plus grande.
    #    Plancher absolu 380px (en dessous = vignette inutilisable). Entre plancher et seuil idéal,
    #    on accepte quand même : une vraie photo un peu petite vaut mieux qu'une carte sans photo.
    FLOOR_W, FLOOR_H = 380, 240
    best_raw, best_px = None, 0
    for img_url in fetch_article_images(article_url):
        raw = fetch_img(img_url)
        if not raw:
            continue
        try:
            from PIL import Image as _I
            import io as _io
            w, h = _I.open(_io.BytesIO(raw)).size
        except Exception:
            continue
        if w < FLOOR_W or h < FLOOR_H:
            continue
        px = w * h
        if px > best_px:
            best_raw, best_px = raw, px
        if best_px >= 1280 * 720:   # déjà du HD franc → inutile de chercher plus
            break
    if best_raw:
        return best_raw, True

    # 2. Miniature RSS (souvent la photo de l'article) si assez grande
    if photo_url:
        raw = fetch_img(photo_url)
        if raw and img_dimensions_ok(raw, min_w=420, min_h=260):
            return raw, True

    # 3. Portrait Wikipedia de la personnalité citée (vraie photo, pertinente)
    if person:
        portrait = fetch_wikipedia_portrait(person)
        if portrait:
            raw = fetch_img(portrait)
            if raw and img_dimensions_ok(raw, min_w=400, min_h=400):
                print(f"  👤 Photo de {person} (Wikipedia)")
                return raw, True

    # 4. Stock UNIQUEMENT si explicitement autorisé ET hors sport
    #    (une photo stock "sport" est presque toujours hors-sujet : triathlon sur un sujet foot...)
    if allow_stock and image_query and category != "sport":
        u = search_unsplash(image_query, category)
        raw = fetch_img(u)
        if raw and img_dimensions_ok(raw, min_w=800, min_h=400):
            return raw, False

    return None, False

def extract_photo(entry):
    """Cherche une image dans l'article RSS."""
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("type", "").startswith("image"):
                return m.get("url")
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            if "image" in e.get("type", ""):
                return e.get("href")
    return None

def build_png(headline_court, source, category, photo_url=None, image_query=None, article_url=None, person=None, W=1200, H=675, prefetched=None, headline_bottom=False):
    """
    PNG DA Pulse, taille paramétrable (W×H).
    - Paysage 1200×675 pour X/Facebook (défaut)
    - Portrait 1080×1350 (4:5) pour Instagram
    prefetched = (raw_bytes, has_real_photo) pour réutiliser une image déjà téléchargée.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import io

        s = STYLES[category]
        margin = int(W * 0.037)   # marge proportionnelle (~44px en 1200, ~40px en 1080)

        if len(headline_court) > 90:
            headline_court = headline_court[:87].rsplit(" ", 1)[0] + "..."

        # ─── FOND ───
        img = Image.new('RGB', (W, H), (13, 13, 20))

        # Sélection de la meilleure image (ou réutilisation si déjà téléchargée)
        if prefetched is not None:
            raw, has_real_photo = prefetched
        else:
            raw, has_real_photo = get_best_image(article_url, photo_url, person, image_query, category)
        show_text = not has_real_photo

        if raw:
            try:
                photo = Image.open(io.BytesIO(raw)).convert('RGB')
                src_w, src_h = photo.size
                scale = max(W / src_w, H / src_h)
                new_w, new_h = int(src_w * scale + 0.5), int(src_h * scale + 0.5)
                photo = photo.resize((new_w, new_h), Image.LANCZOS)
                if scale < 1:   # on a réduit la photo → légère accentuation pour une netteté parfaite
                    photo = photo.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
                # Cadrage INTELLIGENT : on centre sur le VISAGE s'il y en a un détecté
                # (sinon léger biais vers le haut). Fini les visages coupés.
                face = detect_face_center(photo) if has_real_photo else None
                if face:
                    fcx, fcy = face
                    left = int(fcx - W / 2)
                    top  = int(fcy - H * 0.42)   # visage à ~42% du haut, avec de l'air au-dessus
                else:
                    left = (new_w - W) // 2
                    top  = int((new_h - H) * 0.2)
                # On garde le cadre à l'intérieur de l'image
                left = max(0, min(left, new_w - W))
                top  = max(0, min(top,  new_h - H))
                photo = photo.crop((left, top, left + W, top + H))
                alpha = 1.0 if has_real_photo else 0.80
                img   = Image.blend(Image.new('RGB', (W, H), (13, 13, 20)), photo, alpha=alpha)
            except Exception as e:
                print(f"  ⚠️ Traitement image: {e}")

        # ─── OVERLAY ───
        ov    = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(ov)
        r0, g0, b0 = s["overlay"]
        edge = int(H * 0.15)

        if has_real_photo:
            for y in range(H):
                if y < edge:
                    a = int(190 * (1 - y / edge))
                elif y > H - edge:
                    a = int(190 * ((y - (H - edge)) / edge))
                else:
                    a = 0
                if a > 0:
                    odraw.line([(0, y), (W, y)], fill=(r0, g0, b0, a))
        else:
            for y in range(H):
                a = min(255, 180 + int(y / H * 70))
                odraw.line([(0, y), (W, y)], fill=(r0, g0, b0, a))

        img  = Image.alpha_composite(img.convert('RGBA'), ov).convert('RGB')
        draw = ImageDraw.Draw(img)

        # ─── BARRE COULEUR HAUT ───
        c1, c2 = s["bar"]
        bar_h  = max(10, int(H * 0.018))
        for x in range(W):
            t = x / W
            r = int(c1[0] + t * (c2[0] - c1[0]))
            g = int(c1[1] + t * (c2[1] - c1[1]))
            b = int(c1[2] + t * (c2[2] - c1[2]))
            draw.line([(x, 0), (x, bar_h)], fill=(r, g, b))

        # ─── POLICES ───
        def font(size, bold=True):
            paths = [
                f"/usr/share/fonts/truetype/noto/NotoSans-{'Bold' if bold else 'Regular'}.ttf",
                f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
            ]
            for p in paths:
                try: return ImageFont.truetype(p, size)
                except: continue
            return ImageFont.load_default()

        logo_sz  = int(W * 0.047)
        small_sz = int(W * 0.023)
        f_logo  = font(logo_sz)
        f_badge = font(small_sz, bold=False)
        f_sm    = font(small_sz, bold=False)

        # ─── LOGO PULSE ───
        draw.text((margin, int(H * 0.044)), "Pulse", font=f_logo, fill=(255, 255, 255))

        # ─── BADGE CATÉGORIE ───
        badge_hex = s["color"].lstrip("#")
        badge_rgb = tuple(int(badge_hex[i:i+2], 16) for i in (0, 2, 4))
        cat_text  = s["label"]
        bb = draw.textbbox((0, 0), cat_text, font=f_badge)
        bw = bb[2] - bb[0] + 36
        bh = bb[3] - bb[1] + 18
        bx = W - bw - margin
        by = int(H * 0.039)
        bov   = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bov)
        bdraw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2,
                                fill=(*badge_rgb, 50), outline=(*badge_rgb, 200), width=2)
        img  = Image.alpha_composite(img.convert('RGBA'), bov).convert('RGB')
        draw = ImageDraw.Draw(img)
        draw.text((bx + 18, by + 9), cat_text, font=f_badge, fill=badge_rgb)

        # ─── TITRE EN BAS (style Instagram : toujours visible, même avec photo) ───
        if headline_bottom:

            # Dégradé sombre qui MONTE plus haut (lisibilité du texte placé plus haut)
            grad = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            band = int(H * 0.68)               # couvre les ~2/3 inférieurs
            for i in range(band):
                y = H - band + i
                t = i / band
                a = int(255 * (t ** 0.95))     # assombrit tôt et fort
                gd.line([(0, y), (W, y)], fill=(5, 4, 14, a))
            img = Image.alpha_composite(img.convert('RGBA'), grad).convert('RGB')
            draw = ImageDraw.Draw(img)

            # Titre : on retire les hashtags (inutiles/moches sur une image) et on
            # auto-dimensionne pour que RIEN ne déborde (titre court = très gros).
            clean_title = re.sub(r'#(\w+)', r'\1', headline_court)
            clean_title = re.sub(r'\s{2,}', ' ', clean_title).strip()
            max_w = int(W * 0.90)
            sizes = [int(W * x) for x in (0.092, 0.082, 0.072, 0.063, 0.055, 0.048)]

            def _wrap_words(ft):
                lines, line = [], ""
                for w in clean_title.split():
                    test = (line + " " + w).strip()
                    if draw.textbbox((0, 0), test, font=ft)[2] <= max_w:
                        line = test
                    else:
                        if line: lines.append(line)
                        line = w
                if line: lines.append(line)
                return lines

            def _all_words_fit(ft):
                return all(draw.textbbox((0, 0), w, font=ft)[2] <= max_w for w in clean_title.split())

            chosen_lines, chosen_size = None, sizes[-1]
            for fsize in sizes:
                ft = font(fsize)
                lines = _wrap_words(ft)
                if len(lines) <= 4 and _all_words_fit(ft):
                    chosen_lines, chosen_size = lines, fsize
                    break
            if chosen_lines is None:
                # Dernier recours : plus petite taille + coupe des mots trop longs
                ft = font(sizes[-1]); chosen_size = sizes[-1]
                lines, line = [], ""
                for w in clean_title.split():
                    if draw.textbbox((0, 0), w, font=ft)[2] > max_w:
                        if line: lines.append(line); line = ""
                        chunk = ""
                        for ch in w:
                            if draw.textbbox((0, 0), chunk + ch, font=ft)[2] <= max_w:
                                chunk += ch
                            else:
                                if chunk: lines.append(chunk)
                                chunk = ch
                        if chunk: line = chunk
                    else:
                        test = (line + " " + w).strip()
                        if draw.textbbox((0, 0), test, font=ft)[2] <= max_w:
                            line = test
                        else:
                            if line: lines.append(line)
                            line = w
                if line: lines.append(line)
                chosen_lines = lines[:4]

            ft      = font(chosen_size)
            line_h  = int(chosen_size * 1.14)
            total_h = len(chosen_lines) * line_h
            # Bloc de texte centré vers ~66% de la hauteur → commence bien plus haut
            center_y = int(H * 0.66)
            ty0 = center_y - total_h // 2

            # OMBRE PORTÉE DOUCE (floutée) au lieu d'un contour noir net
            shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            sdraw  = ImageDraw.Draw(shadow)
            ty = ty0
            for ln in chosen_lines:
                sdraw.text((margin + 4, ty + 6), ln, font=ft, fill=(0, 0, 0, 235))
                ty += line_h
            shadow = shadow.filter(ImageFilter.GaussianBlur(12))
            # on densifie l'ombre en la compositant 2 fois (plus lisible)
            img = Image.alpha_composite(img.convert('RGBA'), shadow)
            img = Image.alpha_composite(img, shadow).convert('RGB')
            draw = ImageDraw.Draw(img)

            # Texte blanc net par-dessus (sans contour)
            ty = ty0
            for ln in chosen_lines:
                draw.text((margin, ty), ln, font=ft, fill=(255, 255, 255))
                ty += line_h
            show_text = False  # on n'affiche pas le titre centré en plus

        # ─── TITRE CENTRÉ (seulement si pas de vraie photo et pas en mode bas) ───
        if show_text:
            max_w = int(W * 0.9)
            sizes = [int(W*x) for x in (0.052, 0.045, 0.040, 0.035, 0.030, 0.027)]
            chosen_lines, chosen_size = None, sizes[-1]
            for fsize in sizes:
                ft    = font(fsize)
                words = headline_court.split()
                lines, line = [], ""
                for w in words:
                    test = (line + " " + w).strip()
                    if draw.textbbox((0, 0), test, font=ft)[2] <= max_w:
                        line = test
                    else:
                        if line: lines.append(line)
                        line = w
                if line: lines.append(line)
                if len(lines) <= 4:
                    chosen_lines, chosen_size = lines, fsize
                    break
            if chosen_lines is None:
                chosen_lines = [headline_court[:50] + "..."]
                chosen_size  = sizes[-1]

            ft     = font(chosen_size)
            line_h = chosen_size + 14
            total_h= len(chosen_lines) * line_h
            ty     = (H - total_h) // 2 + 10
            for ln in chosen_lines:
                bb = draw.textbbox((0, 0), ln, font=ft)
                draw.text(((W - (bb[2] - bb[0])) // 2, ty), ln, font=ft, fill=(255, 255, 255))
                ty += line_h

        # ─── SOURCE + DATE EN BAS ───
        mois     = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]
        now      = datetime.now()
        date_str = f"{now.day} {mois[now.month - 1]} {now.year}"
        by2 = int(H - H * 0.077)
        draw.text((margin, by2), source, font=f_sm, fill=(255, 255, 255, 200))
        bb2 = draw.textbbox((0, 0), date_str, font=f_sm)
        draw.text((W - bb2[2] - margin, by2), date_str, font=f_sm, fill=(255, 255, 255, 200))

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue(), f"pulse-{category}-{now.strftime('%d%m%Y-%H%M')}.png"

    except Exception as e:
        print(f"  ⚠️ PNG erreur: {e}")
        return None, None

# ═══════════════════════════════════════════════════════════════════════════
# EMAIL
# ═══════════════════════════════════════════════════════════════════════════
def send_email(subject, tweet_text, title, source, url, video, png_bytes, png_name):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = EMAIL_TO

    mois     = ["jan","fév","mar","avr","mai","juin","juil","août","sep","oct","nov","déc"]
    now      = datetime.now()
    date_str = f"{now.day} {mois[now.month-1]} {now.year} · {now.strftime('%H:%M')}"

    video_section = f"\n\n🎬 Vidéo associée :\n{video['title']}\n{video['url']}" if video else ""
    url_section   = f"\n\n🔗 Article original :\n{url}" if url else ""

    body = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  P U L S E  ·  Insuffler l'actu\n"
        f"  {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📰 Source : {source}\n"
        f"📌 {title}\n\n"
        f"─────────────────────────────────────────\n"
        f"  TWEET — copie ce texte sur X\n"
        f"─────────────────────────────────────────\n\n"
        f"{tweet_text}\n"
        f"{video_section}"
        f"{url_section}\n\n"
        f"─────────────────────────────────────────\n\n"
        f"Pulse × Claude AI\n"
    )

    msg.attach(MIMEText(body, "plain", "utf-8"))

    if png_bytes:
        i = MIMEImage(png_bytes, name=png_name)
        i.add_header("Content-Disposition", "attachment", filename=png_name)
        msg.attach(i)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
        srv.sendmail(GMAIL_ADDRESS, EMAIL_TO, msg.as_string())

# ═══════════════════════════════════════════════════════════════════════════
# POST TWITTER
# ═══════════════════════════════════════════════════════════════════════════
def post_to_twitter(tweet_text, png_bytes=None, video_path=None):
    """Poste sur X avec vidéo MP4 (prioritaire) ou image PNG."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        print("  ⚠️ Twitter API non configurée.")
        return None
    try:
        import tweepy, io, os
        auth   = tweepy.OAuth1UserHandler(TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET)
        api_v1 = tweepy.API(auth)
        client_v2 = tweepy.Client(
            consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        media_ids = None
        if video_path and os.path.exists(video_path):
            try:
                print("  📤 Upload vidéo sur X...")
                media = api_v1.media_upload(filename=video_path, media_category="tweet_video", chunked=True)
                media_ids = [media.media_id]
                print(f"  ✅ Vidéo uploadée")
            except Exception as e:
                print(f"  ⚠️ Upload vidéo échoué : {e} → fallback image")
                video_path = None
        if not video_path and png_bytes:
            try:
                media = api_v1.media_upload(filename="pulse.png", file=io.BytesIO(png_bytes))
                media_ids = [media.media_id]
            except Exception as e:
                print(f"  ⚠️ Upload image X échoué : {e}")
        response = client_v2.create_tweet(text=tweet_text, media_ids=media_ids)
        tweet_id = response.data.get("id")
        url      = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None
        if url:
            media_type = "🎬 vidéo" if video_path else "🖼️ image"
            print(f"  🐦 Posté sur X ({media_type}) : {url}")
        return url
    except Exception as e:
        print(f"  ❌ Post X échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# POST FACEBOOK
# ═══════════════════════════════════════════════════════════════════════════
def post_to_facebook(message, png_bytes=None, video_path=None):
    """Poste sur la Page Facebook : photo + texte, ou vidéo, ou texte seul."""
    if not (FACEBOOK_PAGE_TOKEN and FACEBOOK_PAGE_ID):
        return None
    if meta_backoff_active():
        print("  ⏸️ Facebook sauté (pause Meta en cours)")
        return None
    try:
        import urllib.request, json, os
        # Nettoyage du texte : Facebook n'aime pas les hashtags partout, mais on garde le contenu tel quel
        # (le même que X pour cohérence)

        # 1) Avec vidéo
        if video_path and os.path.exists(video_path):
            try:
                import mimetypes
                url = f"https://graph-video.facebook.com/v21.0/{FACEBOOK_PAGE_ID}/videos"
                with open(video_path, "rb") as f:
                    video_data = f.read()
                boundary = "----PulseBoundary7MA4YWxkTrZu0gW"
                body = b""
                # champ description
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\n{message}\r\n".encode()
                # champ access_token
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{FACEBOOK_PAGE_TOKEN}\r\n".encode()
                # fichier vidéo
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"video.mp4\"\r\nContent-Type: video/mp4\r\n\r\n".encode()
                body += video_data + f"\r\n--{boundary}--\r\n".encode()
                req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    res = json.loads(r.read())
                print(f"  📘 Posté sur Facebook (🎬 vidéo) : {res.get('id','ok')}")
                return res.get("id")
            except Exception as e:
                print(f"  ⚠️ Vidéo FB échouée : {e} → fallback photo")

        # 2) Avec photo
        if png_bytes:
            url = f"https://graph.facebook.com/v21.0/{FACEBOOK_PAGE_ID}/photos"
            boundary = "----PulseBoundary7MA4YWxkTrZu0gW"
            body = b""
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{message}\r\n".encode()
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{FACEBOOK_PAGE_TOKEN}\r\n".encode()
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"pulse.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
            body += png_bytes + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())
            print(f"  📘 Posté sur Facebook (🖼️ photo) : {res.get('post_id', res.get('id','ok'))}")
            return res.get("post_id", res.get("id"))

        # 3) Texte seul
        url  = f"https://graph.facebook.com/v21.0/{FACEBOOK_PAGE_ID}/feed"
        data = urllib.parse.urlencode({"message": message, "access_token": FACEBOOK_PAGE_TOKEN}).encode()
        req  = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        print(f"  📘 Posté sur Facebook (texte) : {res.get('id','ok')}")
        return res.get("id")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except:
            body = ""
        print(f"  ❌ Post Facebook échoué : {e} | détail : {body}")
        if _detect_meta_limit(body):
            record_meta_block(body)
        return None
    except Exception as e:
        print(f"  ❌ Post Facebook échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# POST INSTAGRAM (image 4:5 hébergée sur imgbb)
# ═══════════════════════════════════════════════════════════════════════════
def upload_to_imgbb(png_bytes):
    """Upload une image sur imgbb (gratuit) et retourne l'URL publique."""
    if not IMGBB_KEY:
        return None
    try:
        import base64
        b64  = base64.b64encode(png_bytes).decode()
        data = urllib.parse.urlencode({"key": IMGBB_KEY, "image": b64, "expiration": "86400"}).encode()
        req  = urllib.request.Request("https://api.imgbb.com/1/upload", data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        return res.get("data", {}).get("url")
    except Exception as e:
        print(f"  ⚠️ Upload imgbb échoué : {e}")
        return None

def build_ig_caption(tweet_text, keywords=None):
    """
    Optimise la légende pour Instagram (gratuit, fait en Python) :
    - retire les liens (non cliquables sur Insta)
    - ajoute un appel à l'action vers X
    - ajoute des hashtags pertinents (Insta fonctionne beaucoup par hashtags)
    """
    import re as _re
    text = tweet_text or ""
    # Retire les URLs
    text = _re.sub(r'https?://\S+', '', text).strip()
    # Récupère les hashtags présents pour les regrouper en bas,
    # MAIS garde les mots dans la phrase (on retire juste le "#", sinon on casse le texte)
    existing = _re.findall(r'#(\w+)', text)   # hashtags déjà intégrés dans le texte → on les GARDE tels quels
    # On NE retire PAS le "#" du texte (sinon "#CoupeDuMonde2026" deviendrait "CoupeDuMonde2026" collé = texte cassé)
    text = _re.sub(r'[ \t]{2,}', ' ', text)
    text = _re.sub(r'[ \t]+\n', '\n', text)
    text = _re.sub(r'\n{3,}', '\n\n', text).strip()

    # Bloc de découverte en bas : quelques hashtags EN PLUS, SANS répéter ceux déjà dans le texte
    have = {h.lower() for h in existing}   # mots déjà en hashtag dans le texte (sans le #)
    tags = []
    def add_tag(t):
        t = _re.sub(r'[^0-9A-Za-zÀ-ÿ]', '', t)
        if not t or len(t) <= 2 or t.lower() in have:
            return
        tags.append("#" + t[0].upper() + t[1:])
        have.add(t.lower())
    for kw in (keywords or []):
        add_tag(kw)
    for std in ["Actualité", "France", "Pulse"]:
        add_tag(std)
    extra = " ".join(tags[:6])

    cta = "👉 Plus d'infos sur X : @PULSEactus"
    return f"{text}\n\n{cta}" + (f"\n\n{extra}" if extra else "")

def post_to_instagram(caption, png_bytes=None, video_path=None):
    """
    Poste sur Instagram via l'API Graph (image 4:5 uniquement pour l'instant).
    Process en 2 étapes : créer un conteneur média (avec URL image) puis publier.
    """
    if not (INSTAGRAM_ACCOUNT_ID and FACEBOOK_PAGE_TOKEN):
        return None
    if meta_backoff_active():
        print("  ⏸️ Instagram sauté (pause Meta en cours)")
        return None
    if not png_bytes:
        return None  # Instagram exige une image/vidéo, pas de post texte seul

    # 1) Conversion en JPEG haute qualité (format exigé par l'API Instagram, évite une recompression destructive)
    try:
        import io as _io
        im = Image.open(_io.BytesIO(png_bytes)).convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
        png_bytes = buf.getvalue()
    except Exception:
        pass  # en cas de souci, on envoie l'image telle quelle

    # 2) Héberger l'image (Instagram exige une URL publique)
    image_url = upload_to_imgbb(png_bytes)
    if not image_url:
        print("  ⚠️ Instagram : pas d'URL image (imgbb), skip.")
        return None

    try:
        # 2) Créer le conteneur média
        url1  = f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}/media"
        data1 = urllib.parse.urlencode({
            "image_url":    image_url,
            "caption":      caption,
            "access_token": FACEBOOK_PAGE_TOKEN
        }).encode()
        req1 = urllib.request.Request(url1, data=data1)
        with urllib.request.urlopen(req1, timeout=60) as r:
            res1 = json.loads(r.read())
        creation_id = res1.get("id")
        if not creation_id:
            print(f"  ⚠️ Instagram : conteneur non créé ({res1})")
            return None

        # 3) Publier le conteneur
        time.sleep(3)  # laisser Instagram traiter l'image
        url2  = f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}/media_publish"
        data2 = urllib.parse.urlencode({
            "creation_id":  creation_id,
            "access_token": FACEBOOK_PAGE_TOKEN
        }).encode()
        req2 = urllib.request.Request(url2, data=data2)
        with urllib.request.urlopen(req2, timeout=60) as r:
            res2 = json.loads(r.read())
        post_id = res2.get("id")
        print(f"  📸 Posté sur Instagram : {post_id or 'ok'}")
        return post_id
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except:
            body = ""
        print(f"  ❌ Post Instagram échoué : {e} | détail : {body}")
        if _detect_meta_limit(body):
            record_meta_block(body)
        return None
    except Exception as e:
        print(f"  ❌ Post Instagram échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# HISTOIRE DU JOUR
# ═══════════════════════════════════════════════════════════════════════════
def fetch_wikipedia_onthisday():
    try:
        now = datetime.now()
        url = f"https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/{now.month:02d}/{now.day:02d}"
        req = urllib.request.Request(url, headers={"User-Agent": "PulseBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        events = data.get("events", [])
        clean  = []
        for e in events[:30]:
            year, text = e.get("year"), e.get("text", "")
            pages = e.get("pages", [])
            if year and text and pages:
                clean.append({"year": year, "text": text})
        return clean
    except Exception as e:
        print(f"  ⚠️ Wikipedia: {e}")
        return []

def gen_histoire_du_jour(conn):
    if "histoire" in cats_today(conn):
        return None
    events = fetch_wikipedia_onthisday()
    if not events: return None

    now        = datetime.now()
    today      = now.strftime("%d %B")
    current_yr = now.year

    # On pré-calcule "X ans" en Python pour éviter que Claude se trompe
    events_str = "\n".join(
        f"- En {e['year']} (il y a {current_yr - e['year']} ans) : {e['text']}"
        for e in events[:15]
    )

    try:
        result = claude(f"""Tu écris pour Pulse, compte Twitter français.

Aujourd'hui nous sommes le {today} {current_yr}. Voici les événements historiques VÉRIFIÉS de Wikipédia (le nombre d'années est DÉJÀ calculé, NE PAS le recalculer) :

{events_str}

CHOISIS UN événement qui est MONDIALEMENT CONNU :
- 11 septembre, Apollo 11, chute du Mur, JFK, D-Day, Pearl Harbor, Mandela, Diana, Titanic, Tchernobyl, Mai 68, etc.
- REJETTE les événements obscurs ou trop locaux.

Si rien n'est assez célèbre dans la liste → {{"skip": true}}

Sinon génère le tweet en FRANÇAIS.

⚠️ IMPORTANT : utilise EXACTEMENT le nombre d'années indiqué entre parenthèses dans la liste ci-dessus. Ne recalcule rien. Si la liste dit "il y a 57 ans", écris "il y a 57 ans". Pas 56, pas 58.

Format :
- headline_court (max 75 chars)
- image_query (5 mots en anglais)
- body : commence par "Il y a X ans, [événement]" (X = la valeur exacte de la liste), puis contexte/détails. Hashtags répartis. Fini par "(Source : Wikipédia)"

JSON :
{{"headline_court":"...","image_query":"...","body":"..."}}
OU
{{"skip": true}}""", max_tokens=600)

        if result.get("skip"):
            print("  📜 Aucun événement assez connu — skip.")
            return None

        body = result.get("body", "").strip()
        for lbl in LABELS.values():
            body = re.sub(rf"^{lbl}\s*\|\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s]+", "", body).strip()
        if not body: return None

        return {
            "title":          f"Éphéméride — {today}",
            "source":         "Wikipédia",
            "url":            "",
            "analysis":       {"category": "histoire", "needs_video": False},
            "tweet":          build_full_tweet(body, "histoire"),
            "headline_court": result.get("headline_court", f"Éphéméride {today}")[:75],
            "image_query":    result.get("image_query", "history old"),
            "photo_url":      None,
            "keywords":       [],
        }
    except Exception as e:
        print(f"  ⚠️ Histoire échouée : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# THREADS QUOTIDIENS (basés sur les vrais articles RSS)
# ═══════════════════════════════════════════════════════════════════════════
def gather_all_headlines():
    """Récupère un large échantillon de titres+résumés RSS pour repérer les grands sujets."""
    headlines = []
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                summ  = _strip_html(entry.get("summary", entry.get("description", "")))
                if title:
                    summ = re.sub(r"<[^>]+>", "", summ)  # nettoie le HTML
                    headlines.append(f"[{fi['source']}] {title} — {summ[:200]}")
        except: pass
    return headlines

def post_thread(tweets_list, png_bytes=None):
    """Poste un thread (chaîne de réponses) sur X. Image sur le 1er tweet."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        print("  ⚠️ Twitter API non configurée.")
        return None
    try:
        import tweepy, io
        auth   = tweepy.OAuth1UserHandler(TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET)
        api_v1 = tweepy.API(auth)
        client_v2 = tweepy.Client(
            consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        media_ids = None
        if png_bytes:
            try:
                media = api_v1.media_upload(filename="pulse.png", file=io.BytesIO(png_bytes))
                media_ids = [media.media_id]
            except Exception as e:
                print(f"  ⚠️ Upload image thread échoué : {e}")

        prev_id = None
        first_url = None
        for i, txt in enumerate(tweets_list):
            if i == 0:
                resp = client_v2.create_tweet(text=txt, media_ids=media_ids)
            else:
                resp = client_v2.create_tweet(text=txt, in_reply_to_tweet_id=prev_id)
            prev_id = resp.data.get("id")
            if i == 0:
                first_url = f"https://x.com/i/web/status/{prev_id}"
            time.sleep(2)
        print(f"  🧵 Thread posté ({len(tweets_list)} tweets) : {first_url}")
        return first_url
    except Exception as e:
        print(f"  ❌ Thread X échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# SONDAGES QUOTIDIENS
# ═══════════════════════════════════════════════════════════════════════════
def gen_poll(conn):
    """Génère un sondage basé sur un vrai sujet d'actualité du jour."""
    if special_done_today(conn, "poll"):
        return None
    if datetime.now().hour < 12:   # sondage l'après-midi
        return None

    headlines = gather_all_headlines()
    if len(headlines) < 10:
        return None

    avoid = recent_special_topics(conn, "poll", days=7)
    avoid_str = " ; ".join(avoid) if avoid else "Aucun"
    headlines_str = "\n".join(headlines[:40])
    today = datetime.now().strftime("%d %B %Y")

    try:
        result = claude(f"""Tu animes Pulse, compte Twitter d'actualité française. Aujourd'hui : {today}.

Articles du jour :
{headlines_str}

SONDAGES DÉJÀ FAITS CES 7 DERNIERS JOURS (à éviter) :
{avoid_str}

Crée UN sondage qui va FAIRE RÉAGIR ET VOTER un maximum de monde.

🎯 RÈGLES D'OR D'UN BON SONDAGE :
1. SUJET GRAND PUBLIC ET CHAUD : politique française, sport (foot, JO, équipe de France), faits divers marquants, société, polémiques du moment, décisions du gouvernement, prix/pouvoir d'achat, sécurité, immigration, débats de société. ÉVITE absolument les sujets de niche, intellos, culturels confidentiels ou les personnalités que le grand public ne connaît pas (philosophes, écrivains méconnus, etc.).
2. CLIVANT / QUI DIVISE : la question doit opposer deux camps, toucher une opinion forte, ou demander un pronostic. Les gens votent quand ils ont un AVIS tranché.
3. ULTRA SIMPLE : compréhensible en 2 secondes, même sans avoir lu l'article. Pas de question alambiquée.
4. Options ULTRA COURTES : 1 à 3 mots MAXIMUM, JAMAIS une phrase. Limite stricte de 20 caractères par option (sinon X les coupe en plein milieu !). Privilégie : "Oui" / "Non" / "Pour" / "Contre" / "Ça dépend" / "Aucun" / des noms courts (équipes, personnalités).

EXEMPLES DE BONS SONDAGES (style à imiter, regarde la BRIÈVETÉ des options) :
- "Faut-il interdire les écrans aux moins de 3 ans ? 📱" → "Oui" / "Non"
- "Le PSG va-t-il gagner la Ligue des Champions ? ⚽" → "Oui" / "Non" / "J'y crois pas"
- "Retraite à 64 ans : toujours contre ? 🏛️" → "Pour" / "Contre" / "Ça dépend"
- "Durcir l'assurance-chômage ? 💼" → "Bonne idée" / "Mauvaise idée" / "Ça dépend"

❌ MAUVAIS (options trop longues, seront coupées) :
- "Oui, faut réduire les abus" (trop long !) → écris juste "Oui" ou "Bonne idée"
- "Non, c'est punir les précaires" (trop long !) → écris juste "Non" ou "Injuste"

EXEMPLES DE MAUVAIS SONDAGES (à NE JAMAIS faire) :
- "Edgar Morin incarnait-il l'humanisme républicain ?" (personne ne connaît, trop intello)
- Toute question dont la réponse est factuelle plutôt qu'une opinion

CONTRAINTES TECHNIQUES :
- Question max 200 caractères, avec 1 emoji pertinent (et éventuellement 1 hashtag connu)
- 2 à 4 options, chacune 1-3 mots, 20 caractères MAX (impératif)
- Base-toi sur un vrai sujet présent dans les articles ci-dessus
- FRANÇAIS sans faute

Réponds avec ce JSON UNIQUEMENT :
{{"keywords":["mot1","mot2"],"question":"...","options":["Option 1","Option 2"]}}""", max_tokens=400)

        question = result.get("question", "").strip()

        def clean_option(o, limit=25):
            """Coupe proprement au mot (jamais en plein milieu) si > limite."""
            o = o.strip()
            if len(o) <= limit:
                return o
            cut = o[:limit]
            if " " in cut:
                cut = cut[:cut.rfind(" ")]
            return cut.strip()

        options = [clean_option(o) for o in result.get("options", []) if o.strip()]
        options = [o for o in options if o]  # retire vides
        if not question or len(options) < 2:
            return None
        options = options[:4]  # X autorise max 4 options
        return {
            "question": question[:280],
            "options":  options,
            "keywords": result.get("keywords", []),
        }
    except Exception as e:
        print(f"  ⚠️ Sondage échoué : {e}")
        return None

def post_poll(question, options):
    """Poste un sondage sur X (texte seul, pas d'image possible)."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        return None
    try:
        import tweepy
        client_v2 = tweepy.Client(
            consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        resp = client_v2.create_tweet(
            text=question,
            poll_options=options,
            poll_duration_minutes=1440  # 24h
        )
        tid = resp.data.get("id")
        url = f"https://x.com/i/web/status/{tid}" if tid else None
        print(f"  📊 Sondage posté : {url}")
        return url
    except Exception as e:
        print(f"  ❌ Sondage X échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# CARROUSEL INSTAGRAM (décryptage du jour en plusieurs slides, style média)
# ═══════════════════════════════════════════════════════════════════════════
def _cfont(size, bold=True):
    for p in [f"/usr/share/fonts/truetype/noto/NotoSans-{'Bold' if bold else 'Regular'}.ttf",
              f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"]:
        try: return ImageFont.truetype(p, size)
        except: continue
    return ImageFont.load_default()

def _lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def _neon_bg(W, H):
    """Fond dégradé néon Pulse (navy → violet → magenta), rendu rapide."""
    c_top, c_mid, c_bot = (18, 14, 46), (46, 20, 92), (120, 28, 128)
    col = Image.new('RGB', (1, H))
    for y in range(H):
        t = y / H
        c = _lerp(c_top, c_mid, t / 0.6) if t < 0.6 else _lerp(c_mid, c_bot, (t - 0.6) / 0.4)
        col.putpixel((0, y), c)
    return col.resize((W, H))

def _wrap(draw, text, font, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = (line + " " + w).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            line = test
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    return lines

def build_carousel_slide(title, points, idx, total, is_last=False, accent=(255, 90, 200), bg_photo=None):
    """Génère une slide de contenu (PNG bytes) dans la DA Pulse, avec photo de fond floutée si dispo."""
    import io
    W, H = 1080, 1350
    margin = int(W * 0.07)

    # Fond : photo de l'article floutée + assombrie (plus riche qu'un simple dégradé), sinon dégradé néon
    img = None
    if bg_photo:
        try:
            ph = Image.open(io.BytesIO(bg_photo)).convert('RGB')
            pr, tr = ph.width / ph.height, W / H
            if pr > tr:
                nw = int(ph.height * tr); ph = ph.crop(((ph.width - nw) // 2, 0, (ph.width - nw) // 2 + nw, ph.height))
            else:
                nh = int(ph.width / tr); ph = ph.crop((0, (ph.height - nh) // 2, ph.width, (ph.height - nh) // 2 + nh))
            base = ph.resize((W, H)).filter(ImageFilter.GaussianBlur(18))
            # voile violet sombre Pulse par-dessus pour la lisibilité
            tint = Image.new('RGB', (W, H), (18, 12, 46))
            img = Image.blend(base, tint, 0.78)
        except Exception:
            img = None
    if img is None:
        img = _neon_bg(W, H)
    draw = ImageDraw.Draw(img)

    # barre néon en haut
    for x in range(W):
        draw.line([(x, 0), (x, 10)], fill=_lerp((90, 140, 255), (255, 80, 200), x / W))

    # logo
    draw.text((margin, int(H * 0.045)), "Pulse", font=_cfont(int(W * 0.05)), fill=(255, 255, 255))

    # pastille page i/N
    pl = f"{idx}/{total}"
    fp = _cfont(int(W * 0.028), bold=True)
    bb = draw.textbbox((0, 0), pl, font=fp); pw = bb[2] - bb[0] + 34; ph = bb[3] - bb[1] + 22
    px0 = W - pw - margin; py0 = int(H * 0.045)
    pov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); pdd = ImageDraw.Draw(pov)
    pdd.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=ph // 2,
                          fill=(255, 255, 255, 40), outline=(255, 255, 255, 150), width=2)
    img = Image.alpha_composite(img.convert('RGBA'), pov).convert('RGB'); draw = ImageDraw.Draw(img)
    draw.text((px0 + 17, py0 + 8), pl, font=fp, fill=(255, 255, 255))

    # titre (auto-dimensionné pour tenir)
    y = int(H * 0.15)
    tsize = int(W * 0.072)
    for trysize in (int(W * 0.072), int(W * 0.063), int(W * 0.055)):
        if len(_wrap(draw, title, _cfont(trysize), int(W * 0.86))) <= 3:
            tsize = trysize; break
    f_title = _cfont(tsize)
    for ln in _wrap(draw, title, f_title, int(W * 0.86)):
        draw.text((margin, y), ln, font=f_title, fill=(255, 255, 255)); y += int(tsize * 1.15)

    # trait d'accent
    y += 12
    draw.rounded_rectangle([margin, y, margin + int(W * 0.18), y + 10], radius=5, fill=accent)
    y += int(H * 0.045)

    # points à puces (auto-dimensionnés selon le nombre)
    psize = int(W * 0.044) if len(points) <= 3 else int(W * 0.039)
    f_pt = _cfont(psize, bold=False)
    for pt in points:
        draw.ellipse([margin, y + 13, margin + 18, y + 31], fill=accent)
        for ln in _wrap(draw, pt, f_pt, int(W * 0.80)):
            draw.text((margin + 40, y), ln, font=f_pt, fill=(238, 232, 250)); y += int(psize * 1.32)
        y += int(H * 0.018)

    # CTA sur la dernière slide (flèche → qui s'affiche bien, pas d'emoji couleur)
    if is_last:
        draw.text((margin, int(H * 0.9)), "→ Plus d'infos sur X : @PULSEactus",
                  font=_cfont(int(W * 0.04), bold=True), fill=accent)

    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

def gather_articles_with_urls(limit_per_feed=4):
    """Récupère les articles récents avec leur URL (pour pouvoir lire l'article complet)."""
    arts = []
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:limit_per_feed]:
                title = entry.get("title", "")
                if not title:
                    continue
                summ = re.sub(r"<[^>]+>", "", entry.get("summary", entry.get("description", "")))
                arts.append({
                    "title":   title,
                    "summary": summ[:200],
                    "url":     entry.get("link", ""),
                    "source":  fi["source"],
                })
        except:
            pass
    return arts

def fetch_article_text(url, max_chars=3000):
    """Récupère le texte principal d'un article (paragraphes), pour en extraire les vrais chiffres."""
    if not url:
        return ""
    try:
        import html as _html
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            page = r.read(500000).decode("utf-8", errors="ignore")
        page = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', page, flags=re.DOTALL | re.IGNORECASE)
        paras = re.findall(r'<p[^>]*>(.*?)</p>', page, flags=re.DOTALL | re.IGNORECASE)
        text = " ".join(re.sub(r'<[^>]+>', '', p) for p in paras)
        text = _html.unescape(re.sub(r'\s+', ' ', text)).strip()
        return text[:max_chars]
    except Exception as e:
        print(f"  ⚠️ fetch_article_text: {e}")
        return ""

def recent_thread_topics(conn, days=7, limit=5):
    """Thèmes des derniers décryptages, pour ne jamais répéter le même sujet dans la semaine."""
    try:
        rows = conn.execute(
            "SELECT keywords FROM special_log WHERE kind='thread' AND sent_at > datetime('now', ?) "
            "ORDER BY sent_at DESC LIMIT ?", (f"-{days} days", limit)).fetchall()
        return [r[0] for r in rows if r and r[0]]
    except Exception:
        return []

def gen_carousel(conn):
    """
    Décryptage chiffré : (1) Claude choisit LE sujet de fond du jour,
    (2) on LIT l'article complet, (3) Claude génère des slides avec de vrais chiffres.
    """
    arts = gather_articles_with_urls()
    if len(arts) < 10:
        return None
    arts = arts[:40]
    listing = "\n".join(f"{i}. [{a['source']}] {a['title']} — {a['summary']}" for i, a in enumerate(arts))
    avoid = recent_special_topics(conn, "thread", days=7)
    avoid_str = ", ".join(avoid) if avoid else "(aucun)"
    today = datetime.now().strftime("%d %B %Y")
    try:
        # ÉTAPE 1 : choisir LE sujet de fond du jour
        deja = recent_thread_topics(conn)
        deja_str = ("\n⛔ THÈMES DÉJÀ TRAITÉS cette semaine — choisis un sujet DIFFÉRENT : " + " | ".join(deja)) if deja else ""
        pick = claude(f"""Tu es Pulse, média d'actualité française. Aujourd'hui : {today}.

Articles du jour (numérotés) :
{listing}

Sujets déjà traités ces 7 derniers jours (à éviter) : {avoid_str}

Choisis les sujets qui font PARLER et donnent envie de cliquer : affaire/scandale en cours, polémique, drame marquant, événement sportif majeur, décision qui touche directement le portefeuille ou le quotidien des gens, gros buzz. ⛔ ÉVITE ABSOLUMENT les sujets froids/institutionnels : débats techniques (quotas, tarification, mécanismes européens), réformes "à venir", rapports prospectifs, négociations de procédure. Si un sujet ressemble à un cours d'économie, ne le choisis pas.

{deja_str}

Donne ton TOP 3 par ordre de préférence (le meilleur en premier).

Réponds avec ce JSON UNIQUEMENT :
{{"indices": [<n°1>, <n°2>, <n°3>], "sujet":"<2-4 mots sur le n°1>", "cover_title":"<accroche ≤60 caractères pour le n°1>", "image_query":"<5 mots-clés ANGLAIS décrivant une PHOTO du sujet n°1, ex 'paris police protest night'>", "keywords":["<1 à 2 NOMS PROPRES du sujet n°1 pour hashtag : entreprise/personne/lieu/événement central, ex 'SpaceX' ou 'Nahel'>"]}}""", max_tokens=300)

        # On privilégie le 1er sujet du top 3 qui a une VRAIE photo (og:image).
        # Sinon on garde quand même le meilleur sujet : la couverture utilisera image_query (jamais SANS image).
        indices = pick.get("indices") or ([pick["index"]] if isinstance(pick.get("index"), int) else [])
        valid = [arts[i] for i in indices[:3] if isinstance(i, int) and 0 <= i < len(arts)]
        if not valid:
            print("  ⚠️ Décryptage : aucun sujet exploitable — on retentera au prochain passage.")
            return None
        art, og_bytes = valid[0], None
        for cand in valid:
            try:
                og = fetch_og_image(cand["url"])
            except Exception:
                og = None
            if og:
                art, og_bytes = cand, og
                break

        # ÉTAPE 2 : lire l'article complet (pour les vrais chiffres)
        article_text = fetch_article_text(art["url"], max_chars=4000)
        if len(article_text) < 250:
            article_text = f"{art['title']}. {art['summary']}"  # repli si lecture impossible

        # ÉTAPE 3 : générer les slides chiffrées à partir de l'article
        result = claude(f"""Tu es Pulse, média d'actualité française. Voici un article à décrypter en carrousel pédagogique.

SUJET : {art['title']}
ARTICLE :
{article_text}

Crée un carrousel clair, CONCRET et CHIFFRÉ qui explique le sujet étape par étape.

RÈGLES ABSOLUES :
- Utilise UNIQUEMENT les informations de l'article ci-dessus. N'invente AUCUN chiffre.
- REFORMULE avec tes propres mots, ne recopie jamais des phrases entières (droit d'auteur).
- 📊 LES CHIFFRES D'ABORD : vise AU MOINS 4 données chiffrées sur l'ensemble du carrousel (montants en €, pourcentages, quantités, nombres de personnes, dates, classements...). Fais une slide "Les chiffres clés" si l'article s'y prête.
- ⛔ INTERDIT les phrases vagues et creuses du type "le marché se complexifie", "de plus en plus diverse et imprévisible", "les habitudes changent", "un phénomène croissant". CHAQUE point doit apporter une info CONCRÈTE : un chiffre, un nom propre, un lieu, une date ou un fait précis.
- Si l'article manque de chiffres, mets en avant les faits les plus concrets (noms, pays concernés, décisions précises) — JAMAIS de généralités.
- 🇫🇷 FRANÇAIS IMPECCABLE : zéro mot en anglais, zéro faute. Relis-toi.
- EXACTEMENT 4 slides de contenu. Titre court (≤ 32 caractères) + 2 à 3 points.
- Chaque point : UNE phrase courte et factuelle (≤ 110 caractères), avec un chiffre ou un fait précis. PAS d'emoji dans les points.

Réponds avec ce JSON UNIQUEMENT :
{{"cover_title":"<accroche de couverture ≤60 caractères, percutante>","slides":[{{"titre":"...","points":["...","..."]}},{{"titre":"...","points":["...","..."]}},{{"titre":"...","points":["...","..."]}},{{"titre":"...","points":["...","..."]}}]}}""", max_tokens=1100)

        slides = result.get("slides", [])
        slides = [s for s in slides if s.get("titre") and s.get("points")][:4]
        if len(slides) < 3:
            return None
        return {
            "sujet":       pick.get("sujet", "Décryptage")[:40],
            "cover_title": (result.get("cover_title") or pick.get("cover_title") or art["title"])[:80],
            "image_query": pick.get("image_query", "news france"),
            "keywords":    pick.get("keywords", []),
            "slides":      slides,
            "url":         art["url"],
            "summary":     art["summary"],
            "og_bytes":    og_bytes,   # og:image si trouvée, sinon None → repli image_query à la publication
        }
    except Exception as e:
        print(f"  ⚠️ gen_carousel: {e}")
        return None

def carousel_to_text(carousel):
    """Construit le texte du décryptage (X + Facebook) à partir du carrousel — sans 2e appel Claude."""
    emojis = ["📌", "⚡", "🔍", "🔮", "✅"]
    out = f"🧵 {carousel['cover_title']} — Le décryptage\n\n"
    for i, s in enumerate(carousel["slides"]):
        em = emojis[i] if i < len(emojis) else "•"
        out += f"{em} {s['titre']}\n" + " ".join(s["points"]) + "\n\n"
    return out.strip()

def post_carousel_to_instagram(slides_png, caption):
    if meta_backoff_active():
        print("  ⏸️ Carrousel Instagram sauté (pause Meta en cours)")
        return None
    """Publie un carrousel (2 à 10 images) sur Instagram via l'API Graph."""
    if not (INSTAGRAM_ACCOUNT_ID and FACEBOOK_PAGE_TOKEN):
        return None
    if not slides_png or len(slides_png) < 2:
        return None
    try:
        # 1) Créer un conteneur "item" par image
        child_ids = []
        for png in slides_png:
            image_url = upload_to_imgbb(png)
            if not image_url:
                continue
            data = urllib.parse.urlencode({
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": FACEBOOK_PAGE_TOKEN,
            }).encode()
            req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}/media", data=data)
            with urllib.request.urlopen(req, timeout=60) as r:
                cid = json.loads(r.read()).get("id")
            if cid:
                child_ids.append(cid)
            time.sleep(2)
        if len(child_ids) < 2:
            print(f"  ⚠️ Carrousel : pas assez d'images uploadées ({len(child_ids)})")
            return None

        # 2) Conteneur carrousel parent
        data = urllib.parse.urlencode({
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": FACEBOOK_PAGE_TOKEN,
        }).encode()
        req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}/media", data=data)
        with urllib.request.urlopen(req, timeout=60) as r:
            creation_id = json.loads(r.read()).get("id")
        if not creation_id:
            print("  ⚠️ Carrousel : conteneur parent non créé")
            return None

        # 3) Publier
        time.sleep(5)
        data = urllib.parse.urlencode({"creation_id": creation_id, "access_token": FACEBOOK_PAGE_TOKEN}).encode()
        req = urllib.request.Request(f"https://graph.facebook.com/v21.0/{INSTAGRAM_ACCOUNT_ID}/media_publish", data=data)
        with urllib.request.urlopen(req, timeout=60) as r:
            post_id = json.loads(r.read()).get("id")
        print(f"  🎠 Carrousel Instagram publié ({len(child_ids)} slides) : {post_id or 'ok'}")
        return post_id
    except urllib.error.HTTPError as e:
        try: body = e.read().decode("utf-8", errors="ignore")
        except: body = ""
        print(f"  ❌ Carrousel Instagram échoué : {e} | détail : {body}")
        return None
    except Exception as e:
        print(f"  ❌ Carrousel Instagram échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# CARTE DE VICTOIRE (sport) — photo du match floutée + score + vainqueur
# ═══════════════════════════════════════════════════════════════════════════
# ── DRAPEAUX RONDS pour les matchs internationaux (flagcdn.com, domaine public) ──
COUNTRY_FLAGS = {
    "france": "fr", "mexique": "mx", "afrique du sud": "za", "etats-unis": "us", "usa": "us",
    "canada": "ca", "bresil": "br", "argentine": "ar", "espagne": "es", "angleterre": "gb-eng",
    "allemagne": "de", "italie": "it", "portugal": "pt", "pays-bas": "nl", "belgique": "be",
    "croatie": "hr", "maroc": "ma", "senegal": "sn", "algerie": "dz", "tunisie": "tn",
    "cameroun": "cm", "cote d'ivoire": "ci", "ghana": "gh", "nigeria": "ng", "egypte": "eg",
    "japon": "jp", "coree du sud": "kr", "australie": "au", "arabie saoudite": "sa", "qatar": "qa",
    "iran": "ir", "uruguay": "uy", "colombie": "co", "chili": "cl", "perou": "pe",
    "equateur": "ec", "paraguay": "py", "suisse": "ch", "autriche": "at", "pologne": "pl",
    "danemark": "dk", "suede": "se", "norvege": "no", "ecosse": "gb-sct", "pays de galles": "gb-wls",
    "irlande": "ie", "serbie": "rs", "turquie": "tr", "grece": "gr", "ukraine": "ua",
    "panama": "pa", "costa rica": "cr", "honduras": "hn", "jamaique": "jm", "haiti": "ht",
    "nouvelle-zelande": "nz", "ouzbekistan": "uz", "jordanie": "jo", "cap-vert": "cv", "curacao": "cw",
}
_FLAG_CACHE = {}

def _flag_circle(country, diameter=160):
    """Drapeau rond (bord à dessiner par l'appelant). None si pays inconnu ou réseau KO."""
    try:
        import unicodedata, io as _io, urllib.request
        key = unicodedata.normalize("NFD", (country or "").lower().strip())
        key = "".join(ch for ch in key if unicodedata.category(ch) != "Mn")
        iso = COUNTRY_FLAGS.get(key)
        if not iso:
            return None
        if iso not in _FLAG_CACHE:
            req = urllib.request.Request(f"https://flagcdn.com/w160/{iso}.png",
                                         headers={"User-Agent": "Mozilla/5.0"})
            _FLAG_CACHE[iso] = urllib.request.urlopen(req, timeout=8).read()
        im = Image.open(_io.BytesIO(_FLAG_CACHE[iso])).convert("RGB")
        side = min(im.size)
        im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                      (im.width + side) // 2, (im.height + side) // 2))
        im = im.resize((diameter, diameter), Image.LANCZOS)
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, diameter - 1, diameter - 1], fill=255)
        out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
        out.paste(im, (0, 0), mask)
        return out
    except Exception:
        return None

def extract_sport_result(title, summary):
    """Extrait un résultat sportif TERMINÉ : match (sports co), tennis (sets) ou course (vainqueur seul)."""
    try:
        r = claude(f"""Analyse ce titre/résumé d'actualité sportive.
S'il s'agit d'un RÉSULTAT DÉFINITIF clairement identifiable, extrais les infos. Sinon réponds {{"ok":false}}.

Titre : {title}
Résumé : {summary[:300]}

Types possibles :
- "match"  (sports collectifs : football, basket, rugby, handball...) → team_a, score_a, team_b, score_b
- "tennis" (duel en sets) → player_a, player_b, sets (ex "6-4, 3-6, 7-6"), winner ("A" ou "B")
- "race"   (cyclisme, F1, athlétisme, ski, natation, golf, moto...) → winner_name, detail (ex "Étape 12", "Grand Prix du Canada", sinon vide)

Champs communs : competition (nom court : "Ligue 1", "Roland-Garros", "Tour de France"...), sport (en MAJUSCULES : FOOTBALL, BASKET, TENNIS, RUGBY, CYCLISME, F1...).
⛔ UNIQUEMENT les infos écrites dans le titre/résumé. N'invente RIEN (ni score, ni nom).

Réponds avec ce JSON UNIQUEMENT (un de ces formats) :
{{"ok":true,"type":"match","sport":"FOOTBALL","competition":"Ligue 1","team_a":"PSG","score_a":2,"team_b":"OM","score_b":1}}
{{"ok":true,"type":"tennis","sport":"TENNIS","competition":"Roland-Garros","player_a":"Alcaraz","player_b":"Sinner","sets":"6-4, 3-6, 7-6","winner":"A"}}
{{"ok":true,"type":"race","sport":"CYCLISME","competition":"Tour de France","winner_name":"Pogacar","detail":"Étape 12"}}""", max_tokens=260)
        if not r or not r.get("ok"):
            return None
        t     = r.get("type")
        comp  = str(r.get("competition", "")).strip()[:26]
        sport = str(r.get("sport", "")).strip().upper()[:14]
        if t == "match":
            sa, sb = int(r["score_a"]), int(r["score_b"])
            ta, tb = str(r.get("team_a", "")).strip()[:22], str(r.get("team_b", "")).strip()[:22]
            if not ta or not tb:
                return None
            return {"type": "match", "sport": sport, "competition": comp,
                    "team_a": ta, "score_a": sa, "team_b": tb, "score_b": sb,
                    "winner": "A" if sa > sb else ("B" if sb > sa else "NUL")}
        if t == "tennis":
            pa, pb = str(r.get("player_a", "")).strip()[:22], str(r.get("player_b", "")).strip()[:22]
            win = r.get("winner")
            if not pa or not pb or win not in ("A", "B"):
                return None
            return {"type": "tennis", "sport": sport or "TENNIS", "competition": comp,
                    "player_a": pa, "player_b": pb,
                    "sets": str(r.get("sets", "")).strip()[:30], "winner": win}
        if t == "race":
            wn = str(r.get("winner_name", "")).strip()[:26]
            if not wn:
                return None
            return {"type": "race", "sport": sport, "competition": comp,
                    "winner_name": wn, "detail": str(r.get("detail", "")).strip()[:30]}
        return None
    except Exception as e:
        print(f"  ⚠️ extract_sport_result: {e}")
        return None

def _pulse_brand(img, d, W, H, color=(255, 255, 255), ecg=True):
    """Logo Pulse en italique gras + ligne ECG néon (signature de la marque)."""
    def fnt(px):
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf", int(px))
        except Exception:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(px))
    size = W * 0.040
    x0, y0 = int(W * 0.038), int(H * 0.045)
    f = fnt(size)
    d.text((x0, y0), "PULSE", font=f, fill=color)
    if not ecg:
        return
    # ligne ECG : plat → pic → creux → petit rebond → plat → point (comme le logo officiel)
    bb = d.textbbox((x0, y0), "PULSE", font=f)
    lx, ly = bb[2] + int(W * 0.012), (bb[1] + bb[3]) // 2
    u = max(3, int(W * 0.0075))   # unité d'échelle
    pts = [(lx, ly), (lx + 2*u, ly), (lx + 3*u, ly - 4*u), (lx + 4*u, ly + 3*u),
           (lx + 5*u, ly - u), (lx + 6*u, ly), (lx + 9*u, ly)]
    neon = (255, 80, 200)
    # halo (épais translucide) + trait net
    lay = Image.new('RGBA', img.size, (0, 0, 0, 0)); ld = ImageDraw.Draw(lay)
    ld.line(pts, fill=neon + (110,), width=max(5, int(W * 0.006)), joint="curve")
    img.alpha_composite(lay.filter(ImageFilter.GaussianBlur(4)))
    d2 = ImageDraw.Draw(img)
    d2.line(pts, fill=neon, width=max(2, int(W * 0.0028)), joint="curve")
    r = max(3, int(W * 0.0035))
    d2.ellipse([pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r], fill=neon)

def build_victory_card(raw_photo, res, source, W=1200, H=675):
    """Carte de résultat sportif DA Pulse : photo du match floutée + score/vainqueur selon le sport.
    res = dict renvoyé par extract_sport_result (type match / tennis / race)."""
    import io
    GOLD, WHITE, DIM, SILVER = (255, 210, 74), (255, 255, 255), (225, 220, 240), (220, 224, 235)
    def f(px, bold=True):
        p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
        try: return ImageFont.truetype(p, int(px))
        except Exception: return ImageFont.load_default()
    def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    def fit(d, txt, maxw, start, mins=20):
        s = start
        while s > mins and d.textbbox((0, 0), txt, font=f(s))[2] > maxw: s -= 2
        return f(s)
    def shadow(base, fn, blur=12):
        ly = Image.new('RGBA', base.size, (0, 0, 0, 0)); fn(ImageDraw.Draw(ly))
        base.alpha_composite(ly.filter(ImageFilter.GaussianBlur(blur)))

    # Match entre PAYS (Coupe du Monde...) → style "compte foot pro" : photo nette + drapeaux ronds
    flags = None
    if res.get("type", "match") == "match":
        _D = int(min(W, H) * 0.155)
        _fa = _flag_circle(res.get("team_a", ""), _D)
        _fb = _flag_circle(res.get("team_b", ""), _D)
        if _fa is not None and _fb is not None:
            flags = (_fa, _fb)

    # ── fond : photo floutée (recadrage cover LANCZOS) ou dégradé marque navy→violet→magenta ──
    bg = None
    if raw_photo:
        try:
            ph = Image.open(io.BytesIO(raw_photo)).convert('RGB')
            pr, tr = ph.width / ph.height, W / H
            if pr > tr:
                nw = int(ph.height * tr); ph = ph.crop(((ph.width - nw) // 2, 0, (ph.width - nw) // 2 + nw, ph.height))
            else:
                nh = int(ph.width / tr); ph = ph.crop((0, (ph.height - nh) // 2, ph.width, (ph.height - nh) // 2 + nh))
            bg = ph.resize((W, H), Image.LANCZOS)
            if flags is None:
                bg = bg.filter(ImageFilter.GaussianBlur(7))   # international → photo NETTE plein cadre
        except Exception:
            bg = None
    if bg is None:
        c1, c2, c3 = (18, 14, 62), (74, 28, 160), (226, 59, 167)   # bleu nuit → violet → magenta (DA Pulse)
        col = Image.new('RGB', (1, H))
        for y in range(H):
            t = y / H
            col.putpixel((0, y), lerp(c1, c2, t / 0.55) if t < 0.55 else lerp(c2, c3, (t - 0.55) / 0.45))
        bg = col.resize((W, H))
    img = bg.convert('RGBA')

    # bandes sombres haut/bas (centre laissé visible pour la photo)
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    for y in range(H):
        t = y / H
        a = int(160 * (1 - t / 0.34)) if t < 0.34 else (int(175 * ((t - 0.66) / 0.34)) if t > 0.66 else 0)
        if a > 0: od.line([(0, y), (W, y)], fill=(12, 9, 36, min(190, a)))
    img = Image.alpha_composite(img, ov); d = ImageDraw.Draw(img)

    # barre dégradée marque + logo Pulse italique + ECG
    for x in range(W):
        d.line([(x, 0), (x, max(6, int(H * 0.012)))], fill=lerp((90, 140, 255), (255, 80, 200), x / W))
    _pulse_brand(img, d, W, H); d = ImageDraw.Draw(img)

    # pastille SPORT · COMPÉTITION
    pill = " · ".join(x for x in (res.get("sport", ""), res.get("competition", "")) if x)[:38]
    if pill:
        fc = f(W * 0.019); tw = d.textbbox((0, 0), pill, font=fc)[2]
        x1 = W - tw - int(W * 0.038) - int(W * 0.028); y0 = int(H * 0.055); y1 = y0 + int(H * 0.062)
        d.rounded_rectangle([x1, y0, W - int(W * 0.038), y1], radius=int(H * 0.031), outline=(255, 255, 255, 185), width=2)
        d.text((W - int(W * 0.038) - int(W * 0.014), (y0 + y1) // 2), pill, font=fc, fill=WHITE, anchor="rm")

    typ = res.get("type", "match")
    winner = res.get("winner", "")
    is_draw = (typ == "match" and winner == "NUL")
    if flags is None:
        banner = "MATCH NUL" if is_draw else "★  VICTOIRE  ★"
        bcol = SILVER if is_draw else GOLD
        by = int(H * 0.225)
        shadow(img, lambda l: l.text((W // 2, by), banner, font=f(W * 0.032), fill=(0, 0, 0, 235), anchor="mm"), 10); d = ImageDraw.Draw(img)
        d.text((W // 2, by), banner, font=f(W * 0.032), fill=bcol, anchor="mm")

    cy = int(H * 0.50)

    if typ == "match" and flags is not None:
        # ── style "compte foot pro" : bas assombri, drapeaux ronds, score géant, SCORE FINAL ──
        ov2 = Image.new('RGBA', (W, H), (0, 0, 0, 0)); od2 = ImageDraw.Draw(ov2)
        for y in range(int(H * 0.40), H):
            a = int(220 * ((y - H * 0.40) / (H * 0.60)) ** 1.2)
            od2.line([(0, y), (W, y)], fill=(8, 8, 16, a))
        img = Image.alpha_composite(img, ov2); d = ImageDraw.Draw(img)
        D = flags[0].width
        row_y = int(H * 0.68)
        lax, rax = int(W * 0.20), int(W * 0.80)
        ring_w = max(3, D // 26)
        for (fl, xx, win) in [(flags[0], lax, winner == "A"), (flags[1], rax, winner == "B")]:
            shadow(img, lambda l, xx=xx: l.ellipse([xx - D // 2 - 4, row_y - D // 2 - 4,
                                                    xx + D // 2 + 4, row_y + D // 2 + 4], fill=(0, 0, 0, 210)), 10)
            img.alpha_composite(fl, (xx - D // 2, row_y - D // 2)); d = ImageDraw.Draw(img)
            ring = GOLD if (win and not is_draw) else (255, 255, 255)
            d.ellipse([xx - D // 2, row_y - D // 2, xx + D // 2, row_y + D // 2], outline=ring, width=ring_w)
        score = f"{res.get('score_a', '')} - {res.get('score_b', '')}"
        cf = fit(d, score, int(W * 0.40), W * 0.105, mins=36)
        shadow(img, lambda l, cf=cf: l.text((W // 2, row_y), score, font=cf, fill=(0, 0, 0, 240), anchor="mm"), 14); d = ImageDraw.Draw(img)
        d.text((W // 2, row_y), score, font=cf, fill=WHITE, anchor="mm")
        fy = row_y + D // 2 + int(H * 0.052)
        for (xx, nm) in [(lax, res.get("team_a", "")), (rax, res.get("team_b", ""))]:
            fn = fit(d, nm.upper(), int(W * 0.26), W * 0.021)
            d.text((xx, fy), nm.upper(), font=fn, fill=WHITE, anchor="mm")
        lbl = "MATCH NUL" if is_draw else "SCORE FINAL"
        sf = f(W * 0.016)
        stw = d.textbbox((0, 0), lbl, font=sf)[2]
        d.text((W // 2, fy), lbl, font=sf, fill=GOLD, anchor="mm")
        d.line([(W // 2 - stw // 2 - int(W * 0.065), fy), (W // 2 - stw // 2 - int(W * 0.016), fy)], fill=GOLD, width=2)
        d.line([(W // 2 + stw // 2 + int(W * 0.016), fy), (W // 2 + stw // 2 + int(W * 0.065), fy)], fill=GOLD, width=2)
    elif typ == "race":
        # course / contre-la-montre / GP : le vainqueur en très grand, OR
        name = res.get("winner_name", "").upper()
        fn = fit(d, name, int(W * 0.84), W * 0.085)
        shadow(img, lambda l: l.text((W // 2, cy), name, font=fn, fill=(0, 0, 0, 240), anchor="mm"), 14); d = ImageDraw.Draw(img)
        d.text((W // 2, cy), name, font=fn, fill=GOLD, anchor="mm")
        d.text((W // 2, cy + int(H * 0.085)), "✔ VAINQUEUR", font=f(W * 0.022), fill=GOLD, anchor="mm")
        if res.get("detail"):
            d.text((W // 2, cy - int(H * 0.085)), res["detail"], font=f(W * 0.022, False), fill=DIM, anchor="mm")
    else:
        # match (score) ou tennis (sets) : duel gauche/droite
        if typ == "tennis":
            na, nb = res.get("player_a", ""), res.get("player_b", "")
            center = res.get("sets", "") or "—"
            cf = fit(d, center, int(W * 0.38), W * 0.052, mins=24)
        else:
            na, nb = res.get("team_a", ""), res.get("team_b", "")
            center = f"{res.get('score_a', '')}  -  {res.get('score_b', '')}"
            cf = fit(d, center, int(W * 0.38), W * 0.10, mins=36)   # scores à 3 chiffres (NBA) : auto-réduction
        shadow(img, lambda l: l.text((W // 2, cy), center, font=cf, fill=(0, 0, 0, 240), anchor="mm"), 16); d = ImageDraw.Draw(img)
        d.text((W // 2, cy), center, font=cf, fill=WHITE, anchor="mm")
        lax, rax, maxw = int(W * 0.17), int(W * 0.83), int(W * 0.27)
        for xx, txt, win in [(lax, na.upper(), winner == "A"), (rax, nb.upper(), winner == "B")]:
            ft = fit(d, txt, maxw, W * 0.044)
            shadow(img, lambda l, xx=xx, txt=txt, ft=ft: l.text((xx, cy - int(H * 0.03)), txt, font=ft, fill=(0, 0, 0, 240), anchor="mm"), 11); d = ImageDraw.Draw(img)
            d.text((xx, cy - int(H * 0.03)), txt, font=ft, fill=GOLD if win else WHITE, anchor="mm")
            if win:
                d.text((xx, cy + int(H * 0.05)), "✔ VAINQUEUR", font=f(W * 0.018), fill=GOLD, anchor="mm")

    shadow(img, lambda l: l.text((int(W * 0.038), H - int(H * 0.08)), "Pulse", font=f(W * 0.025), fill=(0, 0, 0, 220)), 6); d = ImageDraw.Draw(img)
    d.text((int(W * 0.038), H - int(H * 0.08)), "Pulse", font=f(W * 0.025), fill=WHITE)
    d.text((W - int(W * 0.038), H - int(H * 0.055)), f"{source} · {datetime.now().strftime('%d/%m/%Y')}",
           font=f(W * 0.018, False), fill=DIM, anchor="rm")

    buf = io.BytesIO(); img.convert('RGB').save(buf, format="PNG"); return buf.getvalue()

# ═══════════════════════════════════════════════════════════════════════════
# CARTE HOMMAGE (décès d'une personnalité) — portrait N&B + nom + dates (sobre)
# ═══════════════════════════════════════════════════════════════════════════
DEATH_MARKERS = (
    "est mort", "est morte", "décès de", "décès d'", "décédé", "décédée", "s'est éteint", "s'est éteinte",
    "meurt", "décède", "mort de ", "mort d'", "mort à l'âge", "morte à l'âge", "nous a quittés",
    "nous a quitté", "disparition de", "à l'âge de", "a perdu la vie", "perd la vie",
    "retrouvé mort", "retrouvée morte", "n'est plus", "tire sa révérence", "carnet noir", "décédait",
)
# Exclusions : bilans collectifs / accidents / expressions (pas un hommage individuel)
DEATH_EXCLUDE = (
    "morts", "tués", "tues", "victimes", "bilan", "peine de mort", "mort de rire",
    "mort cérébrale", "à mort", "mise à mort", "blessés", "ne meurt", "meurt jamais",
    "meurt de rire",
)

# Une AFFAIRE qui mentionne un décès passé n'est PAS un hommage : suites judiciaires,
# enquêtes, procès, commémorations → catégorie normale (justice/faits divers).
OBITUARY_BLOCKERS = (
    "cour de cassation", "cour d'appel", "tribunal", "assises", "procès", "verdict",
    "requalification", "requalifi", "mis en examen", "mise en examen", "meurtrier", "accusé",
    "suspect", "enquête", "instruction", "condamn", "acquitt", "relaxe", "relaxé",
    "non-lieu", "indemnis", "plaignant", "porte plainte", "garde à vue", "interpell",
    "réquisitoire", "plaidoirie", "parquet", "juge", "audience",
    "commémor", "anniversaire", "an après", "ans après", "émeutes", "justice pour",
    "rouvre", "rouvrant", "réouverture", "rebondissement", "révélations sur",
    # ── Personne VIVANTE déclarée morte par erreur (administrative) → surtout PAS un hommage ──
    "déclaré mort", "déclarée morte", "déclaré décédé", "déclarée décédée",
    "mort par erreur", "morte par erreur", "déclaré mort par erreur", "à tort",
    "par erreur", "encore en vie", "toujours en vie", "bien vivant", "bien vivante",
    "n'est pas mort", "n'est pas morte", "pas vraiment mort", "faussement déclaré",
    "erreur administrative", "rayé des vivants", "considéré comme mort", "considérée comme morte",
)

def _is_obituary(title, summary):
    """Vrai UNIQUEMENT si l'article ANNONCE le décès d'une personnalité (pas un bilan
    collectif, pas une suite judiciaire ou commémorative d'un décès passé)."""
    t = (title + " " + summary).lower()
    if any(x in t for x in DEATH_EXCLUDE):
        return False
    if any(x in t for x in OBITUARY_BLOCKERS):
        return False   # affaire / procès / commémoration → pas un hommage
    # décès daté d'une année passée ("tué en juin 2023") = pas une annonce fraîche
    m = re.search(r"(?:tué|tuée|mort|morte|décédé|décédée|disparu|disparue)[^.]{0,25}?\ben\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre\s+)?((?:19|20)\d{2})", t)
    if m and int(m.group(1)) < datetime.now().year:
        return False
    return any(m_ in t for m_ in DEATH_MARKERS)

def extract_obituary(title, summary, url=None):
    """Extrait nom / naissance / âge / métier — UNIQUEMENT depuis l'article (zéro invention).
    L'année du décès est ajoutée par Python (vraie année), jamais devinée par Claude."""
    try:
        text = summary[:400]
        if url:
            art = fetch_article_text(url, max_chars=1800)
            if len(art) > 200:
                text = art
        r = claude(f"""On t'a transmis un article annonçant le décès d'une personnalité.

Titre : {title}
Texte de l'article :
{text}

Extrais UNIQUEMENT les informations EXPLICITEMENT écrites dans le texte ci-dessus.
⛔ RÈGLES ABSOLUES — NE TE TROMPE PAS :
- N'utilise JAMAIS tes connaissances personnelles. Base-toi SEULEMENT sur le texte fourni.
- N'invente JAMAIS et NE CALCULE JAMAIS une date, une année ou un âge.
- "birth_year" : l'année de naissance SEULEMENT si elle est écrite telle quelle dans le texte, sinon null.
- "age" : l'âge au décès SEULEMENT s'il est écrit dans le texte (ex: "mort à 83 ans" → 83), sinon null.
- "desc" : le métier/rôle SEULEMENT s'il est écrit, sinon "".

Réponds avec ce JSON UNIQUEMENT :
{{"ok":true,"name":"<nom complet exact>","birth_year":<entier ou null>,"age":<entier ou null>,"desc":"<métier ou vide>"}}
Si ce n'est pas le décès d'une personne nommée, réponds {{"ok":false}}.""", max_tokens=220)
        if not r or not r.get("ok"):
            return None
        name = str(r.get("name", "")).strip()[:40]
        if not name:
            return None
        # L'année du décès vient de Python (vraie année), pas de Claude
        cur = datetime.now().year
        by, age, dates = r.get("birth_year"), r.get("age"), ""
        try:
            if by and 1850 < int(by) <= cur:
                dates = f"{int(by)} – {cur}"
            elif age and 0 < int(age) < 130:
                dates = f"À {int(age)} ans"
        except (ValueError, TypeError):
            dates = ""
        return {"name": name, "dates": dates, "desc": str(r.get("desc", "")).strip()[:40]}
    except Exception as e:
        print(f"  ⚠️ extract_obituary: {e}")
        return None

def build_hommage_card(raw_photo, name, dates, desc, source, W=1200, H=675):
    """Carte hommage sobre : portrait en noir & blanc + nom + dates (DA Pulse discrète)."""
    import io
    from PIL import ImageOps
    WHITE, GREY, FAINT = (245, 245, 248), (176, 180, 194), (140, 144, 158)
    def f(px, bold=True, serif=False):
        fam = "DejaVuSerif" if serif else "DejaVuSans"
        p = f"/usr/share/fonts/truetype/dejavu/{fam}{'-Bold' if bold else ''}.ttf"
        try: return ImageFont.truetype(p, int(px))
        except: return ImageFont.load_default()
    def fit(d, txt, maxw, start, mins=24, **kw):
        s = start
        while s > mins and d.textbbox((0, 0), txt, font=f(s, **kw))[2] > maxw: s -= 2
        return f(s, **kw)

    # fond : portrait recadré (léger biais vers le haut pour le visage) + NOIR & BLANC
    if raw_photo:
        try:
            ph = Image.open(io.BytesIO(raw_photo)).convert('RGB')
            pr, tr = ph.width / ph.height, W / H
            if pr > tr:
                nw = int(ph.height * tr); ph = ph.crop(((ph.width - nw) // 2, 0, (ph.width - nw) // 2 + nw, ph.height))
            else:
                nh = int(ph.width / tr); top = int((ph.height - nh) * 0.30); ph = ph.crop((0, top, ph.width, top + nh))
            ph = ph.resize((W, H), Image.LANCZOS)
            bw = ImageOps.grayscale(ph).convert('RGB')
            bw = Image.blend(bw, Image.new('RGB', (W, H), (0, 0, 0)), 0.18)
        except Exception:
            bw = Image.new('RGB', (W, H), (28, 28, 34))
    else:
        bw = Image.new('RGB', (W, H), (28, 28, 34))
    img = bw.convert('RGBA')

    # voile sombre : léger en haut, très sombre en bas (pour le texte)
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    for y in range(H):
        t = y / H
        a = int(245 * ((t - 0.42) / 0.58)) if t > 0.42 else int(60 * (1 - t / 0.42))
        od.line([(0, y), (W, y)], fill=(8, 8, 12, min(238, max(0, a))))
    img = Image.alpha_composite(img, ov); d = ImageDraw.Draw(img)

    # fine barre sobre (gris ardoise, pas de néon festif)
    for x in range(W):
        d.line([(x, 0), (x, 5)], fill=(int(70 + 30 * x / W), int(72 + 26 * x / W), int(90 + 40 * x / W)))
    d.text((int(W * 0.038), int(H * 0.05)), "Pulse", font=f(W * 0.034), fill=WHITE)
    d.text((W - int(W * 0.038), int(H * 0.075)), "H O M M A G E", font=f(W * 0.020, True), fill=GREY, anchor="rm")

    # bloc nom + dates en bas
    ny = int(H * 0.66)
    fn = fit(d, name.upper(), int(W * 0.86), W * 0.062, serif=True)
    d.text((int(W * 0.045), ny), name.upper(), font=fn, fill=WHITE)
    y2 = ny + fn.size + int(H * 0.02)
    d.rectangle([int(W * 0.046), y2, int(W * 0.046) + int(W * 0.12), y2 + 3], fill=GREY)
    if dates:
        d.text((int(W * 0.045), y2 + int(H * 0.025)), dates, font=f(W * 0.025, False), fill=GREY)
    if desc:
        d.text((int(W * 0.045), y2 + int(H * 0.085)), desc, font=f(W * 0.021, False), fill=FAINT)
    d.text((W - int(W * 0.038), H - int(H * 0.05)), f"{source}", font=f(W * 0.018, False), fill=FAINT, anchor="rm")

    buf = io.BytesIO(); img.convert('RGB').save(buf, format="PNG"); return buf.getvalue()

# ═══════════════════════════════════════════════════════════════════════════
# VIDÉOS ANIMÉES (motion design Pulse) — 0 appel Claude, rendu local + ffmpeg
# ═══════════════════════════════════════════════════════════════════════════
VIDEO_W, VIDEO_H, VIDEO_FPS, VIDEO_DUR = 1280, 720, 20, 6.5

def _vf(px, bold=True, italic=False, serif=False):
    if serif:
        name = "DejaVuSerif" + ("-Bold" if bold else "")
    else:
        name = "DejaVuSans" + ("-BoldOblique" if (bold and italic) else ("-Bold" if bold else ""))
    return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}.ttf", int(px))

def _vlerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
def _vease(t): t = max(0.0, min(1.0, t)); return 1 - (1 - t) ** 3

def _ease_quint(t):
    """Ease-out quintique : très doux à l'arrivée, mouvement 'premium' qui se pose en douceur."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 5

def _ease_soft(t):
    """Ease-in-out sinusoïdal : démarrage ET arrivée tout en douceur (le plus fluide)."""
    import math as _m
    t = max(0.0, min(1.0, t))
    return 0.5 - 0.5 * _m.cos(_m.pi * t)

def _appear(t, start, dur):
    """Progression d'apparition normalisée [0..1] avec courbe douce, depuis 'start' sur 'dur' secondes."""
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return _ease_quint((t - start) / dur)

def _neon_strip(category, W, h, sober=False):
    """Bande dégradée multi-nuances (couleurs de la catégorie) qui servira de barre néon défilante."""
    if sober:
        a, b = (120, 124, 142), (215, 218, 232)
    else:
        bar = STYLES.get(category, STYLES.get("france", list(STYLES.values())[0]))["bar"]
        a, b = bar[0], bar[1]
    dark = tuple(int(c * 0.45) for c in a)
    period = max(2, W // 2)
    strip = Image.new("RGB", (period, h))
    for x in range(period):
        t = x / period
        tt = 1 - abs(2 * t - 1)                       # sombre → clair → sombre (cycle continu)
        c = _vlerp(dark, b, tt)
        for y in range(h):
            strip.putpixel((x, y), c)
    return strip

def _vease_io(t):
    """Ease-in-out (accélère puis décélère) — plus organique que l'ease-out simple."""
    t = max(0.0, min(1.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2

def _vease_out_back(t, s=1.70158):
    """Léger dépassement à l'arrivée (effet 'pop' premium)."""
    t = max(0.0, min(1.0, t))
    t -= 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2

def _cat_rgb(category, sober=False):
    if sober:
        return (150, 154, 172)
    cc = STYLES.get(category, {}).get("color", "#b060ff").lstrip("#")
    try:
        return tuple(int(cc[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (176, 96, 255)

_PARTICLE_CACHE = {}
def _particles(W, H, n=26, seed=7):
    """Petites particules floues statiques (positions/tailles fixes) — la dérive se fait au compositing."""
    key = (W, H, n, seed)
    if key in _PARTICLE_CACHE:
        return _PARTICLE_CACHE[key]
    import random as _r
    rnd = _r.Random(seed)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    for _ in range(n):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        r = rnd.randint(2, 6)
        a = rnd.randint(40, 120)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))
    base = base.filter(ImageFilter.GaussianBlur(2))
    _PARTICLE_CACHE[key] = base
    return base

_GRAIN_CACHE = {}
def _grain(W, H, seed=11):
    """Grain photographique léger (texture fixe, appliquée en overlay doux)."""
    key = (W, H, seed)
    if key in _GRAIN_CACHE:
        return _GRAIN_CACHE[key]
    import random as _r
    rnd = _r.Random(seed)
    small = Image.new("L", (W // 3, H // 3))
    small.putdata([rnd.randint(108, 148) for _ in range((W // 3) * (H // 3))])
    g = small.resize((W, H)).convert("RGBA")
    g.putalpha(20)
    _GRAIN_CACHE[key] = g
    return g

_VIGNETTE_CACHE = {}
def _vignette(W, H):
    """Vignettage radial sombre (concentre le regard au centre)."""
    if (W, H) in _VIGNETTE_CACHE:
        return _VIGNETTE_CACHE[(W, H)]
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-W * 0.25, -H * 0.25, W * 1.25, H * 1.25], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(W, H) // 6))
    alpha = Image.eval(mask, lambda px: 150 - int(px * 150 / 255))
    dark = Image.new("RGBA", (W, H), (6, 6, 16, 0))
    dark.putalpha(alpha)
    _VIGNETTE_CACHE[(W, H)] = dark
    return dark

def _glass_panel(size, radius, tint=(255, 255, 255), tint_a=18, border_a=70, accent=None):
    """Carte 'verre dépoli' : fond translucide + bord lumineux + liseré d'accent optionnel.
    À coller sur un fond DÉJÀ flouté (le flou de l'arrière-plan donne l'effet glassmorphism)."""
    w, h = size
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=tint + (tint_a,))
    sheen = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    for yy in range(h):
        a = int(26 * (1 - yy / h))
        if a > 0:
            sd.line([(0, yy), (w, yy)], fill=(255, 255, 255, a))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    panel.paste(sheen, (0, 0), mask)
    pd = ImageDraw.Draw(panel)
    pd.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=(255, 255, 255, border_a), width=2)
    if accent is not None:
        pd.rounded_rectangle([1, 1, w - 2, h - 2], radius=radius - 1, outline=accent + (90,), width=1)
    return panel

def _wrap_fit(d, text, maxw, start_px, max_lines=2, min_px=30):
    """Police auto-réduite pour tenir en ≤ max_lines ; tronque avec … en dernier recours."""
    size = start_px
    while size >= min_px:
        font = _vf(size)
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if d.textbbox((0, 0), t, font=font)[2] <= maxw: cur = t
            else: lines.append(cur); cur = w
        lines.append(cur)
        if len(lines) <= max_lines:
            return font, lines
        size -= 4
    font = _vf(min_px)
    lines = lines[:max_lines]
    while d.textbbox((0, 0), lines[-1] + "…", font=font)[2] > maxw and " " in lines[-1]:
        lines[-1] = lines[-1].rsplit(" ", 1)[0]
    lines[-1] += "…"
    return font, lines

def build_video(kind, data, category, raw_photo, source, urgent=False):
    """Génère une vidéo MP4 animée (DA Pulse). kind: "news" | "victory" | "hommage".
    Renvoie le chemin du MP4, ou None si indisponible (le post retombe alors sur l'image)."""
    import io, math, shutil, subprocess, tempfile
    if os.environ.get("PULSE_VIDEO", "1") == "0":
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()   # binaire ffmpeg embarqué via pip
        except Exception:
            print("  ⚠️ ffmpeg introuvable (imageio-ffmpeg manquant dans requirements.txt) → image utilisée")
            return None
    try:
        print(f"  🎬 Génération vidéo ({kind})...")
        W, H, FPS, DUR = VIDEO_W, VIDEO_H, VIDEO_FPS, VIDEO_DUR
        N = int(FPS * DUR)
        sober = (kind == "hommage")
        flags_v = None
        if kind == "victory" and data.get("type") == "match":
            _fa = _flag_circle(data.get("team_a", ""), int(min(W, H) * 0.16))
            _fb = _flag_circle(data.get("team_b", ""), int(min(W, H) * 0.16))
            if _fa is not None and _fb is not None:
                flags_v = (_fa, _fb)
        GOLD, WHITE, DIM = (255, 210, 74), (255, 255, 255), (222, 218, 238)
        NEON, RED = (255, 80, 200), (226, 48, 70)

        # ── couches précalculées ──
        photo_big = None
        if raw_photo:
            try:
                ph = Image.open(io.BytesIO(raw_photo)).convert("RGB")
                SCp = 1.10
                bw_, bh_ = int(W * SCp), int(H * SCp)
                pr, tr = ph.width / ph.height, bw_ / bh_
                if pr > tr:
                    nw = int(ph.height * tr); ph = ph.crop(((ph.width - nw) // 2, 0, (ph.width - nw) // 2 + nw, ph.height))
                else:
                    nh = int(ph.width / tr); ph = ph.crop((0, (ph.height - nh) // 2, ph.width, (ph.height - nh) // 2 + nh))
                ph = ph.resize((bw_, bh_), Image.LANCZOS)
                if sober:
                    from PIL import ImageOps as _IO
                    ph = Image.blend(_IO.grayscale(ph).convert("RGB"), Image.new("RGB", ph.size, (0, 0, 0)), 0.18)
                elif kind == "victory" and flags_v is None:
                    ph = ph.filter(ImageFilter.GaussianBlur(5))
                photo_big = ph
            except Exception:
                photo_big = None
        c1, c2, c3 = (16, 12, 52), (70, 26, 152), (226, 59, 167)
        if sober: c1, c2, c3 = (16, 16, 22), (32, 32, 42), (52, 52, 66)
        col = Image.new("RGB", (1, H))
        for y in range(H):
            t = y / H
            col.putpixel((0, y), _vlerp(c1, c2, t / 0.55) if t < 0.55 else _vlerp(c2, c3, (t - 0.55) / 0.45))
        grad_bg = col.resize((W, H))
        bands = Image.new("RGBA", (W, H), (0, 0, 0, 0)); bd = ImageDraw.Draw(bands)
        for y in range(H):
            t = y / H
            a = int(155 * (1 - t / 0.22)) if t < 0.22 else (int(238 * ((t - 0.44) / 0.56)) if t > 0.44 else 0)
            if a > 0: bd.line([(0, y), (W, y)], fill=(10, 8, 30, min(238, a)))
        intl_grad = None
        if flags_v is not None:
            intl_grad = Image.new("RGBA", (W, H), (0, 0, 0, 0)); _ig = ImageDraw.Draw(intl_grad)
            for y in range(int(H * 0.40), H):
                a = int(220 * ((y - H * 0.40) / (H * 0.60)) ** 1.2)
                _ig.line([(0, y), (W, y)], fill=(8, 8, 16, a))
        BAR_H = 9
        strip = _neon_strip(category, W, BAR_H, sober=sober)
        period = strip.width

        LOGO_F = _vf(W * 0.042, italic=True)
        _t = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        lb = _t.textbbox((int(W * 0.04), int(H * 0.055)), "PULSE", font=LOGO_F)
        ex, ey = lb[2] + int(W * 0.014), (lb[1] + lb[3]) // 2
        u = int(W * 0.008)
        ECG = [(ex, ey), (ex + 2*u, ey), (ex + 3*u, ey - 4*u), (ex + 4*u, ey + 3*u),
               (ex + 5*u, ey - u), (ex + 6*u, ey), (ex + 9*u, ey)]
        seg = [math.dist(ECG[i], ECG[i+1]) for i in range(len(ECG) - 1)]
        TOT = sum(seg)
        def ecg_pts(frac):
            if frac <= 0: return []
            tgt, pts, acc = TOT * min(1.0, frac), [ECG[0]], 0.0
            for i, L in enumerate(seg):
                if acc + L <= tgt: pts.append(ECG[i+1]); acc += L
                else:
                    r = (tgt - acc) / L; a_, b_ = ECG[i], ECG[i+1]
                    pts.append((a_[0] + (b_[0]-a_[0]) * r, a_[1] + (b_[1]-a_[1]) * r)); break
            return pts
        ecg_col = (205, 208, 224) if sober else NEON
        # pastille catégorie NÉON : halo flouté couleur catégorie + anneau net + texte CENTRÉ
        pill_layer = None
        if kind != "hommage" and not urgent:
            _tp = ImageDraw.Draw(Image.new("RGB", (8, 8)))
            lbl_p = LABELS.get(category, category.upper())[:18]
            pf_p = _vf(W * 0.019)
            tw_p = _tp.textbbox((0, 0), lbl_p, font=pf_p)[2]
            cc = STYLES.get(category, {}).get("color", "#e0e0f0").lstrip("#")
            cr = tuple(int(cc[i:i + 2], 16) for i in (0, 2, 4))
            bright = tuple(min(255, int(c + (255 - c) * 0.55)) for c in cr)   # cœur lumineux du néon
            padx, hgt = int(W * 0.022), int(H * 0.064)
            x2p = W - int(W * 0.04); x1p = x2p - (tw_p + padx * 2)
            y0p = int(H * 0.060); y1p = y0p + hgt
            pill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            pld = ImageDraw.Draw(pill_layer)
            pld.rounded_rectangle([x1p, y0p, x2p, y1p], radius=hgt // 2,
                                  outline=cr + (210,), width=max(5, int(W * 0.0055)))
            pill_layer = pill_layer.filter(ImageFilter.GaussianBlur(5))       # halo néon
            pld = ImageDraw.Draw(pill_layer)
            pld.rounded_rectangle([x1p, y0p, x2p, y1p], radius=hgt // 2,
                                  fill=(10, 8, 28, 150), outline=bright + (255,), width=2)
            pld.text(((x1p + x2p) // 2, (y0p + y1p) // 2 + 1), lbl_p, font=pf_p,
                     fill=bright + (255,), anchor="mm")
        glow_full = None
        if not sober:   # halo néon précalculé une seule fois (gros gain de vitesse)
            gl = Image.new('RGBA', (W, H), (0, 0, 0, 0)); gld = ImageDraw.Draw(gl)
            gld.line(ECG, fill=ecg_col + (110,), width=7, joint="curve")
            glow_full = gl.filter(ImageFilter.GaussianBlur(4))

        # ── COUCHES GLASSMORPHISM (précalculées 1× — coût quasi nul par frame) ──
        accent_rgb   = _cat_rgb(category, sober=sober)
        layer_part   = _particles(W, H, n=(16 if sober else 28))
        # vignette + grain fusionnés en UNE seule couche de finition (1 composite/frame au lieu de 2)
        finish = _vignette(W, H).copy()
        finish.alpha_composite(_grain(W, H))
        # fond flouté de base (le verre laisse deviner la photo derrière) :
        if photo_big is not None:
            glass_bg = photo_big.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(26))
            glass_bg = Image.eval(glass_bg, lambda px: int(px * 0.72)).convert("RGBA")  # assombri
        else:
            glass_bg = grad_bg.filter(ImageFilter.GaussianBlur(20)).convert("RGBA")
        # halo d'accent diffus en bas (lumière colorée de la catégorie)
        accent_glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        _ag = ImageDraw.Draw(accent_glow)
        _ag.ellipse([int(W * 0.1), int(H * 0.78), int(W * 0.9), int(H * 1.25)],
                    fill=accent_rgb + (70,))
        accent_glow = accent_glow.filter(ImageFilter.GaussianBlur(min(W, H) // 5))
        # le halo d'accent est STATIQUE → on le fusionne une fois dans le fond flouté d'intro
        glass_bg.alpha_composite(accent_glow)
        # panneau de verre du titre (news) précalculé une seule fois
        glass_title_panel = None
        if kind == "news":
            pass  # construit plus bas une fois HLINES/LH connus

        # ── zones texte (anti-collision : tout est ancré AU-DESSUS du pied de page) ──
        FOOTER_Y = H - int(H * 0.115)            # zone réservée source/date
        tmpd = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        HFONT, HLINES, LH, HY0 = None, [], 0, 0
        if kind == "news":
            headline = re.sub(r'#(\w+)', r'\1', str(data.get("headline", "")))[:90]   # hashtags retirés
            # largeur de texte calée sur l'intérieur du panneau (marges confortables des deux côtés)
            HFONT, HLINES = _wrap_fit(tmpd, headline, int(W * 0.80), int(W * 0.048), max_lines=3)
            LH = int(HFONT.size * 1.26)
            HY0 = FOOTER_Y - int(H * 0.040) - LH * len(HLINES)   # bloc collé au-dessus du pied de page
            # panneau de verre précalculé (construit 1× au lieu de chaque frame)
            _pad_top, _pad_bot = int(H * 0.030), int(H * 0.034)
            _block_h = LH * len(HLINES)
            GP_X0 = int(W * 0.05)
            GP_Y0 = HY0 - _pad_top
            GP_W = W - 2 * GP_X0
            GP_H = _block_h + _pad_top + _pad_bot
            GP_PADTOP, GP_BLOCKH = _pad_top, _block_h
            glass_title_panel = _glass_panel((GP_W, GP_H), radius=int(H * 0.045),
                                             tint_a=22, border_a=80, accent=accent_rgb)

        out_dir = tempfile.mkdtemp(prefix="pulsevid_")
        for n in range(N):
            t = n / FPS
            if photo_big is not None:
                # la photo est là dès la PREMIÈRE frame, avec son zoom lent (Ken Burns)
                z = 1.0 + 0.07 * (t / DUR)
                cw, chh = int(photo_big.width / z), int(photo_big.height / z)
                cx, cy = (photo_big.width - cw) // 2, int((photo_big.height - chh) * 0.45)
                img = photo_big.crop((cx, cy, cx + cw, cy + chh)).resize((W, H), Image.BILINEAR).convert("RGBA")
            else:
                img = grad_bg.copy().convert("RGBA")
            # Intro premium : on part du fond flouté "verre" et la photo nette se révèle (0→1.1s)
            rev = _vease_io(t / 1.1)
            if rev < 1:
                img = Image.blend(glass_bg, img, rev)
            else:
                img.alpha_composite(accent_glow)   # halo d'accent (le fond d'intro l'a déjà fusionné)
            # particules qui dérivent doucement (parallaxe lente vers le haut + oscillation)
            pdx = int(math.sin(t * 0.6) * W * 0.012)
            pdy = int(-(t / DUR) * H * 0.06)
            img.alpha_composite(layer_part, (pdx, pdy))
            img.alpha_composite(bands)
            if intl_grad is not None:
                img.alpha_composite(intl_grad)
            # barre néon défilante (nuances de la catégorie)
            off = int((t * W * 0.22) % period)
            bar = Image.new("RGB", (W, BAR_H))
            xx = -off
            while xx < W:
                bar.paste(strip, (xx, 0)); xx += period
            img.paste(bar, (0, 0))
            d = ImageDraw.Draw(img)
            # logo + ECG (apparition fluide : fondu doux + léger glissement depuis la gauche)
            la = _appear(t, 0.15, 0.9)
            if la > 0:
                lx_off = int((1 - la) * 22)
                d.text((int(W * 0.04) - lx_off, int(H * 0.055)), "PULSE", font=LOGO_F,
                       fill=WHITE + (int(255 * la),))
            frac = _ease_soft((t - 0.4) / 1.1)
            pts = ecg_pts(frac)
            if len(pts) >= 2:
                if glow_full is not None and frac >= 1:
                    img.alpha_composite(glow_full)
                d = ImageDraw.Draw(img)
                d.line(pts, fill=ecg_col, width=3, joint="curve")
                if frac >= 1 and not sober:
                    r = 4 + 1.6 * (1 + math.sin(t * 5.5)) / 2
                    e = ECG[-1]
                    d.ellipse([e[0]-r, e[1]-r, e[0]+r, e[1]+r], fill=ecg_col)
            # pastille haut droite (apparition fluide : fondu + léger glissement depuis la droite)
            sa = _appear(t, 0.7, 0.7)
            px_off = int((1 - sa) * 24)
            if sa > 0:
                if kind == "hommage":
                    d.text((W - int(W * 0.04) + px_off, int(H * 0.085)), "H O M M A G E", font=_vf(W * 0.020),
                           fill=(205, 208, 224, int(255 * sa)), anchor="rm")
                elif urgent:
                    s = 1.0
                    pf = _vf(W * 0.024 * s)
                    txt = "URGENT"; tw = d.textbbox((0, 0), txt, font=pf)[2]
                    cxp, cyp = W - int(W * 0.04) - tw // 2 - int(W * 0.030) + px_off, int(H * 0.095)
                    padx, pady = int(W * 0.022 * s), int(H * 0.022 * s)
                    box = [cxp - tw // 2 - padx, cyp - pady - int(W * 0.012 * s),
                           cxp + tw // 2 + padx + int(W * 0.018 * s), cyp + pady + int(W * 0.012 * s)]
                    d.rounded_rectangle(box, radius=int(H * 0.035), fill=RED + (int(235 * sa),))
                    blink = 0.55 + 0.45 * math.sin(t * 6.0)
                    rr = int(W * 0.0055)
                    d.ellipse([box[0] + padx - rr, cyp - rr, box[0] + padx + rr, cyp + rr],
                              fill=(255, 255, 255, int(255 * sa * blink)))
                    d.text((cxp + int(W * 0.012), cyp), txt, font=pf, fill=WHITE + (int(255 * sa),), anchor="mm")
                elif pill_layer is not None:
                    if sa >= 1:
                        img.alpha_composite(pill_layer)
                    else:
                        tmp_p = pill_layer.copy()
                        tmp_p.putalpha(tmp_p.split()[3].point(lambda px: int(px * sa)))
                        img.alpha_composite(tmp_p)
                    d = ImageDraw.Draw(img)

            # ── contenu central selon le type ──
            if kind == "news":
                # Panneau de verre qui monte en douceur (slide + fade fluide, sans rebond sec)
                panel_in = _ease_quint((t - 1.5) / 1.0)
                if panel_in > 0:
                    slide = int((1 - panel_in) * H * 0.10)
                    pan = glass_title_panel
                    if panel_in < 1:
                        pan = pan.copy()
                        pan.putalpha(pan.split()[3].point(lambda v: int(v * min(1, panel_in))))
                    img.alpha_composite(pan, (GP_X0, GP_Y0 + slide))
                    d = ImageDraw.Draw(img)
                    if panel_in > 0.5:
                        la2 = _ease_quint((panel_in - 0.5) / 0.5)
                        ax = GP_X0 + int(W * 0.018)
                        ah = int(GP_BLOCKH * la2)   # le liseré se "déroule" verticalement
                        d.rounded_rectangle([ax, GP_Y0 + slide + GP_PADTOP,
                                             ax + 5, GP_Y0 + slide + GP_PADTOP + ah],
                                            radius=2, fill=accent_rgb + (230,))
                for i, line in enumerate(HLINES):
                    wa = _appear(t, 1.9 + i * 0.22, 0.85)   # apparition décalée et douce par ligne
                    if wa <= 0: continue
                    dx = int((1 - wa) * 30)        # glissement fluide depuis la gauche
                    y = HY0 + i * LH
                    xline = GP_X0 + int(W * 0.045) + dx
                    d.text((xline + 2, y + 2), line, font=HFONT, fill=(0, 0, 0, int(150 * wa)))
                    d.text((xline, y), line, font=HFONT, fill=WHITE + (int(255 * wa),))
            elif kind == "victory":
                typ, winner = data.get("type", "match"), data.get("winner", "")
                cy = int(H * 0.50)
                if typ == "race":
                    name = data.get("winner_name", "").upper()
                    fz = _vf(min(W * 0.085, W * 0.085))
                    while tmpd.textbbox((0, 0), name, font=fz)[2] > W * 0.84 and fz.size > 30:
                        fz = _vf(fz.size - 4)
                    na_ = _appear(t, 1.5, 0.9)
                    if na_ > 0:
                        sc = 1.08 - 0.08 * na_
                        f2 = _vf(fz.size * sc)
                        d.text((W // 2 + 2, cy + 2), name, font=f2, fill=(0, 0, 0, int(220 * na_)), anchor="mm")
                        d.text((W // 2, cy), name, font=f2, fill=GOLD + (int(255 * na_),), anchor="mm")
                    da_ = _appear(t, 2.3, 0.7)
                    if da_ > 0 and data.get("detail"):
                        d.text((W // 2, cy - int(H * 0.10)), data["detail"], font=_vf(W * 0.022, False),
                               fill=DIM + (int(255 * da_),), anchor="mm")
                elif flags_v is not None:
                    # ── match international : drapeaux ronds + score qui compte (style compte foot pro) ──
                    D = flags_v[0].width
                    row_y = int(H * 0.68)
                    lax, rax = int(W * 0.20), int(W * 0.80)
                    fl_a = _appear(t, 1.2, 0.8)
                    if fl_a > 0:
                        ring_w = max(3, D // 26)
                        for (fl, xx, win_) in [(flags_v[0], lax, winner == "A"), (flags_v[1], rax, winner == "B")]:
                            tmpfl = fl
                            if fl_a < 1:
                                tmpfl = fl.copy()
                                tmpfl.putalpha(tmpfl.split()[3].point(lambda px: int(px * fl_a)))
                            img.alpha_composite(tmpfl, (xx - D // 2, row_y - D // 2))
                            d = ImageDraw.Draw(img)
                            gold_now = win_ and winner != "NUL" and t >= 4.2
                            ring = GOLD if gold_now else (255, 255, 255)
                            d.ellipse([xx - D // 2, row_y - D // 2, xx + D // 2, row_y + D // 2],
                                      outline=ring + (int(255 * fl_a),), width=ring_w)
                        fy = row_y + D // 2 + int(H * 0.052)
                        for (xx, nm) in [(lax, data.get("team_a", "")), (rax, data.get("team_b", ""))]:
                            fn = _vf(W * 0.020)
                            while tmpd.textbbox((0, 0), nm.upper(), font=fn)[2] > W * 0.26 and fn.size > 16:
                                fn = _vf(fn.size - 2)
                            d.text((xx, fy), nm.upper(), font=fn, fill=WHITE + (int(255 * fl_a),), anchor="mm")
                    prog = _ease_soft((t - 2.1) / 1.4)
                    sa_f, sb_f = int(data.get("score_a", 0)), int(data.get("score_b", 0))
                    if prog > 0:
                        final_txt = f"{sa_f} - {sb_f}"
                        cf = _vf(W * 0.105)
                        while tmpd.textbbox((0, 0), final_txt, font=cf)[2] > W * 0.40 and cf.size > 36:
                            cf = _vf(cf.size - 4)
                        cur = f"{int(round(prog * sa_f))} - {int(round(prog * sb_f))}"
                        d.text((W // 2 + 3, row_y + 3), cur, font=cf, fill=(0, 0, 0, 230), anchor="mm")
                        d.text((W // 2, row_y), cur, font=cf, fill=WHITE, anchor="mm")
                    sf_a = _appear(t, 3.9, 0.7)
                    if sf_a > 0:
                        lbl = "MATCH NUL" if winner == "NUL" else "SCORE FINAL"
                        sf = _vf(W * 0.016)
                        stw = tmpd.textbbox((0, 0), lbl, font=sf)[2]
                        fy2 = row_y + D // 2 + int(H * 0.052)
                        d.text((W // 2, fy2), lbl, font=sf, fill=GOLD + (int(255 * sf_a),), anchor="mm")
                        ga = int(255 * sf_a)
                        d.line([(W // 2 - stw // 2 - int(W * 0.065), fy2), (W // 2 - stw // 2 - int(W * 0.016), fy2)], fill=GOLD + (ga,), width=2)
                        d.line([(W // 2 + stw // 2 + int(W * 0.016), fy2), (W // 2 + stw // 2 + int(W * 0.065), fy2)], fill=GOLD + (ga,), width=2)
                else:
                    na, nb = (data.get("player_a"), data.get("player_b")) if typ == "tennis" else (data.get("team_a"), data.get("team_b"))
                    sl = _appear(t, 1.4, 0.95)
                    lax, rax = int(W * 0.17), int(W * 0.83)
                    offx = int((1 - sl) * W * 0.22)
                    for xx_, txt_, win_ in [(lax - offx, (na or "").upper(), winner == "A"),
                                            (rax + offx, (nb or "").upper(), winner == "B")]:
                        if sl <= 0: break
                        ftm = _vf(W * 0.044)
                        while tmpd.textbbox((0, 0), txt_, font=ftm)[2] > W * 0.27 and ftm.size > 22:
                            ftm = _vf(ftm.size - 2)
                        d.text((xx_ + 2, cy - int(H * 0.03) + 2), txt_, font=ftm, fill=(0, 0, 0, int(220 * sl)), anchor="mm")
                        gold_now = win_ and t >= 4.0
                        d.text((xx_, cy - int(H * 0.03)), txt_, font=ftm,
                               fill=(GOLD if gold_now else WHITE) + (int(255 * sl),), anchor="mm")
                        if win_ and _appear(t, 4.2, 0.6) > 0:
                            va = _appear(t, 4.2, 0.6)
                            d.text((xx_, cy + int(H * 0.055)), "✔ VAINQUEUR", font=_vf(W * 0.018),
                                   fill=GOLD + (int(255 * va),), anchor="mm")
                    if typ == "tennis":
                        ca_ = _appear(t, 2.2, 0.85)
                        if ca_ > 0:
                            sets = data.get("sets", "") or "—"
                            cf = _vf(W * 0.05)
                            while tmpd.textbbox((0, 0), sets, font=cf)[2] > W * 0.38 and cf.size > 22:
                                cf = _vf(cf.size - 2)
                            d.text((W // 2 + 2, cy + 2), sets, font=cf, fill=(0, 0, 0, int(220 * ca_)), anchor="mm")
                            d.text((W // 2, cy), sets, font=cf, fill=WHITE + (int(255 * ca_),), anchor="mm")
                    else:
                        prog = _ease_soft((t - 2.2) / 1.4)
                        sa_f, sb_f = int(data.get("score_a", 0)), int(data.get("score_b", 0))
                        va_, vb_ = int(round(prog * sa_f)), int(round(prog * sb_f))
                        if prog > 0:
                            # taille calibrée sur le score FINAL (stable pendant le comptage, jamais sur les équipes)
                            final_txt = f"{sa_f}  -  {sb_f}"
                            cf = _vf(W * 0.10)
                            while tmpd.textbbox((0, 0), final_txt, font=cf)[2] > W * 0.38 and cf.size > 36:
                                cf = _vf(cf.size - 4)
                            d.text((W // 2 + 3, cy + 3), f"{va_}  -  {vb_}", font=cf, fill=(0, 0, 0, 230), anchor="mm")
                            d.text((W // 2, cy), f"{va_}  -  {vb_}", font=cf, fill=WHITE, anchor="mm")
                    st_ = _appear(t, 3.7, 0.6)
                    if st_ > 0 and winner != "NUL":
                        sc = 1.0 + 0.5 * (1 - st_)
                        bf = _vf(W * 0.032 * sc)
                        d.text((W // 2 + 2, int(H * 0.205) + 2), "★  VICTOIRE  ★", font=bf, fill=(0, 0, 0, int(220 * st_)), anchor="mm")
                        d.text((W // 2, int(H * 0.205)), "★  VICTOIRE  ★", font=bf, fill=GOLD + (int(255 * st_),), anchor="mm")
                    elif st_ > 0:
                        d.text((W // 2, int(H * 0.205)), "MATCH NUL", font=_vf(W * 0.032),
                               fill=(220, 224, 235, int(255 * st_)), anchor="mm")
            elif kind == "hommage":
                name = str(data.get("name", ""))
                fz = _vf(W * 0.058, serif=True)
                while tmpd.textbbox((0, 0), name.upper(), font=fz)[2] > W * 0.86 and fz.size > 28:
                    fz = _vf(fz.size - 4, serif=True)
                ny = FOOTER_Y - int(H * 0.205)
                na_ = _appear(t, 1.7, 1.1)
                if na_ > 0:
                    dy = int((1 - na_) * 14)
                    d.text((int(W * 0.045), ny + dy), name.upper(), font=fz, fill=WHITE + (int(255 * na_),))
                fa2 = _appear(t, 2.9, 1.0)
                if fa2 > 0:
                    y2 = ny + fz.size + int(H * 0.022)
                    d.rectangle([int(W * 0.046), y2, int(W * 0.046) + int(W * 0.11), y2 + 3],
                                fill=(176, 180, 194, int(255 * fa2)))
                    if data.get("dates"):
                        d.text((int(W * 0.045), y2 + int(H * 0.022)), data["dates"], font=_vf(W * 0.024, False),
                               fill=(196, 200, 216, int(255 * fa2)))
                fa3 = _appear(t, 3.6, 1.0)
                if fa3 > 0 and data.get("desc"):
                    d.text((int(W * 0.045), ny + fz.size + int(H * 0.085)), data["desc"], font=_vf(W * 0.020, False),
                           fill=(165, 168, 186, int(255 * fa3)))

            # pied de page (zone réservée — rien ne descend dessus)
            fa = _appear(t, DUR - 2.4, 0.9)
            if fa > 0:
                d.text((int(W * 0.04), H - int(H * 0.082)), "Pulse", font=_vf(W * 0.020),
                       fill=WHITE + (int(255 * fa),))
                d.text((W - int(W * 0.04), H - int(H * 0.070)), f"{source} · {datetime.now().strftime('%d/%m/%Y')}",
                       font=_vf(W * 0.016, False), fill=DIM + (int(230 * fa),), anchor="rm")
            # ── finition photographique : vignettage + grain fusionnés (1 seul composite) ──
            img.alpha_composite(finish)
            img.convert("RGB").save(f"{out_dir}/f_{n:03d}.png")

        out_mp4 = os.path.join(out_dir, "pulse_video.mp4")
        subprocess.run([ffmpeg_bin, "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", f"{out_dir}/f_%03d.png", "-c:v", "libx264", "-preset", "veryfast",
                        "-threads", "0", "-pix_fmt", "yuv420p", "-crf", "21",
                        "-movflags", "+faststart", out_mp4], check=True, timeout=300)
        for n in range(N):   # libère le disque : on ne garde que le MP4
            try: os.remove(f"{out_dir}/f_{n:03d}.png")
            except OSError: pass
        print(f"  🎬 Vidéo générée ({kind})")
        return out_mp4
    except Exception as e:
        print(f"  ⚠️ build_video: {e} → image classique")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# CARTES-LISTES (récap du soir, matchs du jour) + MODE COUPE DU MONDE
# ═══════════════════════════════════════════════════════════════════════════
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR  = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]

def _date_fr():
    d = datetime.now()
    return f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month - 1]}"

def build_list_card(title_main, items, W=1200, H=675, accent=(255, 210, 74)):
    """Carte-liste DA Pulse : dégradé marque + titre + lignes numérotées. items = [str, ...]"""
    import io
    WHITE, DIM = (255, 255, 255), (222, 218, 238)
    def f(px, bold=True):
        p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
        return ImageFont.truetype(p, int(px))
    def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    c1, c2, c3 = (18, 14, 62), (62, 24, 138), (160, 40, 130)
    col = Image.new('RGB', (1, H))
    for y in range(H):
        t = y / H
        col.putpixel((0, y), lerp(c1, c2, t / 0.6) if t < 0.6 else lerp(c2, c3, (t - 0.6) / 0.4))
    img = col.resize((W, H)).convert('RGBA')
    d = ImageDraw.Draw(img)
    for x in range(W):
        d.line([(x, 0), (x, max(6, int(H * 0.012)))], fill=lerp((90, 140, 255), (255, 80, 200), x / W))
    _pulse_brand(img, d, W, H); d = ImageDraw.Draw(img)
    d.text((W - int(W * 0.04), int(H * 0.085)), _date_fr().upper(), font=f(W * 0.018),
           fill=DIM, anchor="rm")
    # titre principal
    tf = f(W * 0.040)
    while d.textbbox((0, 0), title_main, font=tf)[2] > W * 0.90 and tf.size > 24:
        tf = f(tf.size - 2)
    ty = int(H * 0.195)
    d.text((int(W * 0.05) + 2, ty + 2), title_main, font=tf, fill=(0, 0, 0, 200))
    d.text((int(W * 0.05), ty), title_main, font=tf, fill=accent)
    # lignes
    items = items[:6]
    top, bottom = int(H * 0.30), H - int(H * 0.10)
    row_h = (bottom - top) // max(1, len(items))
    for i, txt in enumerate(items):
        cy = top + i * row_h + row_h // 2
        r = int(min(W, H) * 0.026)
        cxn = int(W * 0.075)
        d.ellipse([cxn - r, cy - r, cxn + r, cy + r], outline=accent, width=3)
        d.text((cxn, cy + 1), str(i + 1), font=f(r * 1.15), fill=accent, anchor="mm")
        ft = f(W * 0.026)
        maxw = W - cxn - r - int(W * 0.09)
        while d.textbbox((0, 0), txt, font=ft)[2] > maxw and ft.size > 15:
            ft = f(ft.size - 1)
        if d.textbbox((0, 0), txt, font=ft)[2] > maxw:
            while d.textbbox((0, 0), txt + "…", font=ft)[2] > maxw and " " in txt:
                txt = txt.rsplit(" ", 1)[0]
            txt += "…"
        d.text((cxn + r + int(W * 0.025), cy), txt, font=ft, fill=WHITE, anchor="lm")
    d.text((int(W * 0.04), H - int(H * 0.062)), "Pulse", font=f(W * 0.020), fill=WHITE)
    d.text((W - int(W * 0.04), H - int(H * 0.055)), "@PULSEactus", font=f(W * 0.016, False),
           fill=DIM, anchor="rm")
    buf = io.BytesIO(); img.convert('RGB').save(buf, format="PNG"); return buf.getvalue()

def publish_recap(conn):
    """🌙 Récap du soir : les 5 infos qui ont marqué la journée (depuis les publications du jour)."""
    rows = conn.execute(
        "SELECT title FROM recent_titles WHERE date(added_at) = date('now') ORDER BY added_at DESC LIMIT 18"
    ).fetchall()
    titles = [r[0] for r in rows if r and r[0]]
    if len(titles) < 3:
        return False
    arts = "\n".join(f"- {t}" for t in titles)
    r = claude(f"""Voici les actus publiées AUJOURD'HUI par un compte d'actualité français :
{arts}

Choisis les 5 PLUS MARQUANTES (les plus importantes/émotionnelles), de la plus forte à la moins forte.
Pour chacune : reformule en UNE ligne percutante de 65 caractères MAXIMUM (français impeccable, factuel,
rien d'inventé) + un emoji pertinent.

Réponds avec ce JSON UNIQUEMENT :
{{"items":[{{"e":"⚽","t":"..."}},{{"e":"🚨","t":"..."}},{{"e":"..","t":".."}},{{"e":"..","t":".."}},{{"e":"..","t":".."}}]}}""",
        max_tokens=450)
    items = [(str(it.get("e", "•"))[:2], str(it.get("t", "")).strip()[:80])
             for it in (r.get("items") or []) if it.get("t")][:5]
    if len(items) < 3:
        return False
    body = f"🌙 LE RÉCAP | Ce qu'il faut retenir de ce {_date_fr()} :\n\n"
    body += "\n".join(f"{e} {t}" for e, t in items)
    body += "\n\n(Pulse)"
    png    = build_list_card("CE QU'IL FAUT RETENIR", [t for _, t in items], 1200, 675)
    png_ig = build_list_card("CE QU'IL FAUT RETENIR", [t for _, t in items], 1080, 1350)
    try:
        post_to_twitter(body, png)
    except Exception as e:
        print(f"  ❌ X isolé : {e}")
    try:
        post_to_facebook(body, png)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    if ig_allowed(conn):
        post_to_instagram(build_ig_caption(body, []), png_ig)
        log_special(conn, "ig_post", [])
    log_special(conn, "recap", [t for _, t in items][:2])
    print("  🌙 Récap du soir publié")
    return True

# ── MODE COUPE DU MONDE (calendrier fourni via cdm2026.txt à la racine du repo) ──
def load_cdm(path="cdm2026.txt"):
    """Lit le calendrier : lignes 'AAAA-MM-JJ|HH:MM|Équipe A|Équipe B|Phase'. # = commentaire."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = [x.strip() for x in line.split("|")]
                if len(p) >= 4:
                    out.append({"date": p[0], "heure": p[1], "a": p[2], "b": p[3],
                                "phase": p[4] if len(p) > 4 else ""})
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  ⚠️ cdm2026.txt illisible : {e}")
    return out

def publish_cdm_day(conn):
    """🏆 Chaque matin pendant la CDM : les matchs du jour."""
    today = datetime.now().strftime("%Y-%m-%d")
    matchs = [m for m in load_cdm() if m["date"] == today]
    if not matchs:
        return False
    matchs.sort(key=lambda m: m["heure"])
    items = [f"{m['heure']}  {m['a']} – {m['b']}" + (f"  ({m['phase']})" if m["phase"] else "")
             for m in matchs][:6]
    body = f"🏆 #CoupeDuMonde2026 | Les matchs de ce {_date_fr()} :\n\n"
    body += "\n".join(f"⚽ {m['heure']} · {m['a']} – {m['b']}" for m in matchs[:6])
    body += "\n\n(Pulse)"
    png    = build_list_card("LES MATCHS DU JOUR", items, 1200, 675)
    png_ig = build_list_card("LES MATCHS DU JOUR", items, 1080, 1350)
    try:
        post_to_twitter(body, png)
    except Exception as e:
        print(f"  ❌ X isolé : {e}")
    try:
        post_to_facebook(body, png)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    if ig_allowed(conn):
        post_to_instagram(build_ig_caption(body, ["coupedumonde2026"]), png_ig)
        log_special(conn, "ig_post", [])
    log_special(conn, "cdm_jour", [today])
    print(f"  🏆 Matchs du jour publiés ({len(matchs)})")
    return True

def publish_cdm_prono(conn):
    """🔮 La veille d'un match de la France : sondage pronostic natif sur X."""
    from datetime import timedelta
    demain = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for m in load_cdm():
        if m["date"] != demain:
            continue
        if "france" not in (m["a"] + m["b"]).lower():
            continue
        key = f"{m['date']}-{m['a']}-{m['b']}"
        done = conn.execute(
            "SELECT 1 FROM special_log WHERE kind='prono' AND keywords=? AND sent_at > datetime('now','-3 days')",
            (key,)).fetchone()
        if done:
            continue
        adv = m["b"] if m["a"].lower() == "france" else m["a"]
        question = f"🔮 #CoupeDuMonde2026 | Votre prono pour {m['a']} – {m['b']} demain ?"
        options = ["Victoire France 🇫🇷", "Match nul", f"Victoire {adv}"[:25]]
        post_poll(question, options)
        try:
            post_to_facebook(question + "\n\nDites-nous votre pronostic en commentaire 👇")
        except Exception as e:
            print(f"  ❌ Facebook isolé : {e}")
        conn.execute("INSERT INTO special_log (kind, keywords) VALUES ('prono', ?)", (key,))
        conn.commit()
        print(f"  🔮 Prono publié : {m['a']} – {m['b']}")
        return True
    return False

# ═══════════════════════════════════════════════════════════════════════════
# MODE BREAKING — détection multi-sources + publication immédiate
# ═══════════════════════════════════════════════════════════════════════════
BREAKING_STOPWORDS = set("""
alors apres après avec avant aussi autre autres aux avoir bien cela cele celui ces cette
chez comme contre dans depuis deux donc dont elle elles entre etait était etre être eux
fait faire font hier huit pour plus moins très tres sans sous selon sur son sas ses leur
leurs cette ceux dont mais nous vous quand quoi qui que des les une uns deux trois quatre
cinq matin soir jour jours semaine annee année ville france francais français info flash
direct video vidéo photos selon vers tout tous toute toutes encore aujourd hui ans contre
""".split())

def _sig_words(title):
    words = re.findall(r"[0-9A-Za-zÀ-ÿ]+", title.lower())
    return {w for w in words if len(w) >= 4 and w not in BREAKING_STOPWORDS}

# Marqueurs de contenu "mou" (rapport, étude, analyse...) qui ne sont JAMAIS un breaking
BREAKING_EXCLUDE = (
    "rapport", "étude", "etude", "analyse", "sondage", "classement", "baromètre", "barometre",
    "tribune", "chronique", "interview", "portrait", "décryptage", "decryptage", "infographie",
    "vue de l'étranger", "vu de l'étranger", "revue de presse", "édito", "edito",
    "ce qu'il faut retenir", "récap", "recap", "résumé de la", "en images",
    "retour sur", "anniversaire", "commémor", "replay", "podcast", "quiz",
    "horoscope", "programme tv", "diaporama", "il y a 10 ans", "il y a 20 ans",
    "témoignage", "temoignage", "préconise", "preconise", "recommande", "palmarès", "palmares",
    "prévision", "prevision", "selon une étude", "selon un rapport", "avis de", "dossier",
)

def _is_soft_news(title):
    t = title.lower()
    return any(m in t for m in BREAKING_EXCLUDE)

# Marqueurs de SPORT EN DIRECT / résultat chaud (pour un léger coup de pouce, sans spam)
LIVE_SPORT_MARKERS = (
    "en direct", "live", "suivez", "mi-temps", "quart-temps", "quart temps", "prolongation",
    "mène", "mènent", "l'emporte", "s'impose", "bat ", "battu", "victoire", "défaite",
    "qualifié", "qualifie", "élimine", "elimine", "finale", "demi-finale", "demi finale",
    "but de", "égalise", "egalise", "penalty", "carton rouge", "pole position", "grand prix",
    "remporte", "domine", "arrache", "renverse", "points", "sets", "manche décisive",
)

def _is_live_sport(item):
    t = (item.get("title", "") + " " + item.get("summary", "")).lower()
    return any(m in t for m in LIVE_SPORT_MARKERS)

def sport_cooldown_active(conn, minutes=SPORT_COOLDOWN_MIN):
    """Vrai si un post SPORT a déjà été publié dans les N dernières minutes (anti-spam)."""
    return conn.execute(
        "SELECT 1 FROM category_log WHERE category='sport' AND last_sent > datetime('now', ?)",
        (f"-{minutes} minutes",)
    ).fetchone() is not None

# Marqueurs d'un RÉSULTAT FINAL (mots qui indiquent un match terminé)
SPORT_RESULT_MARKERS = (
    "victoire", "défaite", "defaite", "s'impose", "l'emporte", "remporte", "vainqueur",
    "qualifié", "qualifie", "éliminé", "elimine", "élimination", "champion", "sacré",
    "score final", "résultat final", "terminé", "fin du match", "au coup de sifflet final",
    "tenu en échec", "match nul", "domine", "dominé", "fait plier", "fait match nul",
    "arrache", "renverse", "écrase", "humilie", "surclasse", "dispose de", "vient à bout",
    "concède le nul", "partage les points", "chute face", "s'incline", "corrige",
    "succès", "large succès", "victoire de", "déroule", "balaie", "atomise",
    "se qualifie", "valide son billet", "rejoint", "n'a fait qu'une bouchée", "domine",
)
# Indices d'un match EN COURS (ou à venir) → ce n'est PAS un résultat final
SPORT_LIVE_CUES = (
    "en direct", "live", "suivez", "mi-temps", "à la pause", "quart-temps", "quart temps",
    "e période", "1re période", "2e période", "e minute", "en cours", "actuellement",
    "avant-match", "avant match", "compositions", "à suivre", "mène", "mènent",
)

def _is_sport_result(title):
    """Vrai uniquement si le titre annonce un match TERMINÉ (pas un score en cours)."""
    t = title.lower()
    if any(c in t for c in SPORT_LIVE_CUES):      # match en cours/à venir → pas un résultat
        return False
    if any(m in t for m in SPORT_RESULT_MARKERS):
        return True
    # le verbe "battre" conjugué (mais pas "débat", "combat", "bateau"...)
    if re.search(r"\b(bat|battent|battu|battue|battus|battues)\b", t):
        return True
    if re.search(r"\d{1,2}\s?[-:–]\s?\d{1,2}", title):   # score chiffré "2-0", "(1-1)", "4 - 1" → match terminé
        return True
    return False

# Nouveaux développements d'un sujet déjà couvert (déclaration, mise en examen...) → 1 suite autorisée / 4h
FOLLOWUP_MARKERS = (
    "réagit", "s'exprime", "annonce", "révèle", "mis en examen", "mise en examen", "garde à vue",
    "interpell", "démission", "nouveau bilan", "rebondissement", "témoigne", "porte plainte",
    "reconnaît", "condamn", "s'excuse", "répond", "convoqu", "déclare", "promet", "limog",
)
def _is_followup(title):
    t = title.lower()
    return any(m in t for m in FOLLOWUP_MARKERS)

def followup_recent(conn, minutes=240):
    return conn.execute(
        "SELECT 1 FROM special_log WHERE kind='followup' AND sent_at > datetime('now', ?)",
        (f"-{minutes} minutes",)
    ).fetchone() is not None

_META_CONN = None   # connexion DB partagée pour la détection de blocage Meta

def meta_backoff_active(minutes=180):
    """Vrai si Meta a renvoyé 'request limit' récemment → on saute FB/IG le temps que ça se calme."""
    if _META_CONN is None:
        return False
    try:
        return _META_CONN.execute(
            "SELECT 1 FROM special_log WHERE kind='meta_block' AND sent_at > datetime('now', ?)",
            (f"-{minutes} minutes",)).fetchone() is not None
    except Exception:
        return False

def record_meta_block(detail=""):
    if _META_CONN is None:
        return
    try:
        log_special(_META_CONN, "meta_block", [])
        print(f"  🛑 Limite Meta détectée → Facebook/Instagram en pause 3h (auto)")
    except Exception:
        pass

def _detect_meta_limit(err_body):
    b = str(err_body)
    return '"code":4' in b.replace(" ", "") or "request limit" in b.lower() or "2207051" in b

def ig_allowed(conn, minutes=90):
    """Anti-blocage Instagram : espace les publications API (Insta freine les cadences trop élevées)."""
    return conn.execute(
        "SELECT 1 FROM special_log WHERE kind='ig_post' AND sent_at > datetime('now', ?)",
        (f"-{minutes} minutes",)
    ).fetchone() is None

def sport_result_recent(conn, minutes=90):
    """Vrai si un RÉSULTAT sportif a déjà été publié récemment (limite à 1 dérogation / 4h)."""
    return conn.execute(
        "SELECT 1 FROM special_log WHERE kind='sport_result' AND sent_at > datetime('now', ?)",
        (f"-{minutes} minutes",)
    ).fetchone() is not None

def breaking_recent(conn, minutes=BREAKING_GAP_MIN):
    """Vrai si une actu breaking a déjà été publiée dans les N dernières minutes."""
    return conn.execute(
        "SELECT 1 FROM special_log WHERE kind='breaking' AND sent_at > datetime('now', ?)",
        (f"-{minutes} minutes",)
    ).fetchone() is not None

# ── PRÉ-CLASSEMENT GRATUIT (Python) : choisit les candidats qui méritent l'analyse Claude ──
PRERANK_HOT = [
    (5, r"coupe du monde|mondial 2026|équipe de france|les bleus|mbapp|\bpsg\b|\bom\b|wembanyama|roland.garros|tour de france|ligue des champions|huitième de finale|quart de finale|demi-finale|match d'ouverture"),
    (5, r"\bmort\b|\bmorte\b|décès|décéd|\btué|fusillade|attentat|incendie|explosion|enlèvement|disparition|crash|effondr|otage"),
    (4, r"garde à vue|mis en examen|démission|interpell|condamn|verdict|procès|scandale|polémique|visa refus|expuls|suspendu|braquage|prise d'otage|évasion|kidnapping|féminicide"),
    (4, r"squeezie|mcfly|carlito|inoxtag|hanouna|l[ée]na situations|booba|\bjul\b|\bgims\b|ninho|tibo inshape|amixem|michou|domingo|mister v|kameto|zerator|gotaga|maghla"),
    (3, r"clash|\bbuzz\b|viral|historique|inédit|panne (géante|nationale|mondiale)|grève|manifestation|bloqu"),
    (4, r"\binterdit\b|interdiction|interdic|suspendu|suspension|banni|banni|censur|sanction|bloque l'accès|coupe l'accès|piratage|cyberattaque|fuite de données|faille"),
    (3, r"chatgpt|openai|anthropic|\bclaude\b|\bgemini\b|\bgrok\b|\bmeta ai\b|deepseek|nvidia|intelligence artificielle|\bia\b générative"),
    (2, r"victoire|défaite|qualifi|élimin|finale|sacre|remporte"),
    (6, r"(coupe du monde|mondial|cdm).{0,50}(\d{1,2}\s?[-:–]\s?\d{1,2}|succès|s'impose|écrase|bat |élimin|victoire|qualifi)"),
    (6, r"(\d{1,2}\s?[-:–]\s?\d{1,2}|s'impose|écrase|succès).{0,50}(coupe du monde|mondial|cdm)"),
    (4, r"\b\d{1,2}\s?[-:–]\s?\d{1,2}\b"),   # un score chiffré dans le titre = match terminé à pousser
]
PRERANK_COLD = [
    (-4, r"app store|bundle|abonnement|partenariat|trimestriel|levée de fonds|lève des fonds|acquisition|\bapi\b|mise à jour|fonctionnalité|s'associe"),
    (-4, r"vue de l'étranger|revue de presse|édito|tribune|chronique|portrait|ce qu'il faut retenir|récap|décryptage"),
    (-3, r"étude|rapport|sondage|classement|baromètre"),
    (-3, r"pourrait|devrait|envisage|prévoit|à l'horizon|d'ici 20\d\d"),
    (-2, r"comment |pourquoi |voici |conseils|astuces|guide"),
    (-4, r"horoscope|programme tv|replay|podcast|diaporama|quiz|recette|bons plans|promo|soldes|comparatif|notre sélection|que regarder|que faire ce"),
    (-3, r"triathlon|marathon de|championnats? du monde de|coupe du monde de (handball|rugby|natation|judo)|open de|tournoi de"),
]
def prerank_candidates(cands, keep):
    """Classement heuristique gratuit : mots chauds/froids + écho multi-sources.
    Fini le tirage au sort : les articles les plus prometteurs partent en analyse."""
    sigs = [_sig_words(c["title"]) for c in cands]
    scored_idx = []
    for i, c in enumerate(cands):
        t = (c["title"] + " " + (c.get("summary") or "")[:120]).lower()
        s = 0.0
        for w, rx in PRERANK_HOT:
            if re.search(rx, t): s += w
        for w, rx in PRERANK_COLD:
            if re.search(rx, t): s += w
        echo = sum(1 for j in range(len(cands))
                   if j != i and cands[j]["source"] != c["source"] and len(sigs[i] & sigs[j]) >= 2)
        s += min(6, echo * 2)            # repris par plusieurs médias = important
        s += random.random() * 0.5       # micro-aléa pour départager
        scored_idx.append((s, i))
    scored_idx.sort(key=lambda x: -x[0])
    # Dédup intra-lot : un même sujet repris par plusieurs sources n'est analysé qu'UNE fois
    # (≥3 mots significatifs communs = quasi-doublon ; l'écho a déjà boosté son score)
    kept, kept_sigs = [], []
    for s, i in scored_idx:
        if any(len(sigs[i] & ks) >= 3 for ks in kept_sigs):
            continue
        kept.append(cands[i]); kept_sigs.append(sigs[i])
        if len(kept) >= keep:
            break
    return kept

# Mots ULTRA-CHAUDS : une vraie urgence est souvent en ligne avant que 3 médias la reprennent.
# Pour ces sujets, 2 sources concordantes suffisent (au lieu de 3) → détection plus rapide.
# Familles d'événements ULTRA-CHAUDS : 2 sources concordantes suffisent à déclencher le breaking
# (au lieu de 3). Couvre TOUT ce qui, par nature, est une urgence ou un séisme médiatique immédiat.
ULTRA_HOT_RX = re.compile("|".join([
    # ── Mort / violence / terrorisme ──
    r"\bmort\b", r"\bmorte\b", r"\bmorts\b", r"décès", r"décéd", r"\btué", r"\btuée",
    r"assassin", r"attentat", r"fusillade", r"\btirs?\b", r"coups? de feu", r"fusil",
    r"attaque au couteau", r"prise d'otage", r"otages?\b", r"tuerie", r"massacre",
    r"abattu", r"poignardé", r"décapit", r"kamikaze", r"terroris",
    # ── Catastrophes / accidents majeurs ──
    r"explosion", r"déflagration", r"incendie", r"crash", r"accident d'avion", r"déraillement",
    r"effondrement", r"effondre", r"séisme", r"tremblement de terre", r"tsunami", r"inondation",
    r"ouragan", r"tornade", r"éruption", r"naufrage", r"catastrophe", r"évacuation", r"noyade",
    r"carambolage", r"\bnucléaire\b", r"fuite radioactive",
    # ── Disparitions / enlèvements / alertes ──
    r"disparition", r"disparu", r"enlèvement", r"enlevé", r"kidnapp", r"alerte enlèvement",
    r"porté disparu", r"recherche activement",
    # ── Politique / institutionnel majeur (rupture soudaine) ──
    r"démission", r"démissionne", r"limog", r"renvers", r"motion de censure adoptée",
    r"dissolution", r"coup d'état", r"putsch", r"état d'urgence", r"destitution", r"censuré",
    r"déclare la guerre", r"frappe", r"bombarde", r"missile", r"offensive", r"cessez-le-feu",
    r"interpellé", r"arrêté", r"écroué", r"placé en garde à vue",
    # ── Justice retentissante ──
    r"condamné à", r"verdict", r"acquitté", r"relaxé", r"mis en examen", r"inculp",
    # ── Sport : exploits/chocs nationaux instantanés ──
    r"qualifié", r"qualifie", r"éliminé", r"élimine", r"champion", r"sacré", r"finale",
    r"remporte", r"bat\b", r"forfait", r"blessure", r"record du monde", r"démission du sélectionneur",
    # ── Buzz / pop culture / société (déflagration immédiate) ──
    r"choc", r"scandale", r"polémique", r"affaire", r"révélation", r"accusé de", r"accusée de",
    r"plainte", r"clash", r"\bbad buzz\b", r"démasqué", r"dévoile", r"annonce surprise",
    r"séparation", r"rupture", r"divorce", r"enceinte", r"décision historique", r"démission surprise",
    # ── Pannes / cyber d'ampleur ──
    r"panne (géante|nationale|mondiale|massive|g[ée]ante)", r"cyberattaque", r"piratage massif",
    r"fuite de données", r"\brappel\b massif", r"rappel produit",
]), re.I)

def detect_breaking(conn, candidates):
    """
    Détecte une actu 'breaking' = un même sujet repris MAINTENANT par plusieurs sources
    distinctes. Gratuit (analyse de titres uniquement). Renvoie le candidat le plus repris.
    Seuil DYNAMIQUE : 2 sources suffisent si le titre est ultra-chaud, 3 sinon.
    """
    if breaking_recent(conn):
        return None
    enriched = [(c, _sig_words(c["title"])) for c in candidates]
    best, best_count = None, 0
    for i, (c, wi) in enumerate(enriched):
        if len(wi) < 2:
            continue
        if _is_soft_news(c["title"]):      # rapport / étude / revue de presse... → jamais breaking
            continue
        sources = set()
        for j, (d, wj) in enumerate(enriched):
            if i == j or d["source"] == c["source"]:
                continue
            if len(wi & wj) >= 2:          # ≥2 mots significatifs en commun
                sources.add(d["source"])
        required = 2 if ULTRA_HOT_RX.search(c["title"]) else BREAKING_SOURCES
        if len(sources) + 1 >= required and len(sources) >= best_count:
            best, best_count = c, len(sources)
    return best

def publish_breaking(conn, item, cat, urgent=True):
    """Publie vite une actu (X + Facebook + Instagram).
    urgent=True → label rouge 'Breaking'. urgent=False → label normal de la catégorie (buzz/insolite)."""
    add_recent(conn, item["title"])
    if _is_obituary(item.get("title", ""), item.get("summary", "")):
        cat = "hommage"   # décès → ton sobre, même en breaking (le label URGENT reste si urgent=True)
    label_cat = "breaking" if urgent else cat
    body, headline_court, image_query, keywords, person = gen_tweet_verified(
        item["title"], item["summary"], item["source"], cat, url=item.get("url")
    )
    tweet_final = build_full_tweet(body, label_cat)
    photo = extract_photo(item["entry"]) if "entry" in item else None
    raw_src, has_real = get_best_image(item.get("url"), photo, person, image_query, label_cat)
    png_bytes, _ = build_png(headline_court, item["source"], label_cat, photo, image_query,
                             article_url=item.get("url"), person=person, prefetched=(raw_src, has_real))
    vid = build_video("news", {"headline": headline_court}, label_cat, raw_src, item["source"], urgent=urgent)
    try:
        post_to_twitter(tweet_final, png_bytes, vid)
    except Exception as e:
        print(f"  ❌ X isolé : {e}")
    try:
        post_to_facebook(tweet_final, png_bytes, vid)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    png_ig, _ = build_png(headline_court, item["source"], label_cat, photo, image_query,
                          article_url=item.get("url"), person=person, W=1080, H=1350,
                          prefetched=(raw_src, has_real), headline_bottom=True)
    if ig_allowed(conn):
        post_to_instagram(build_ig_caption(tweet_final, keywords), png_ig)
        log_special(conn, "ig_post", [])
    else:
        print("  ⏸️ Instagram en pause (anti-blocage : min 90 min entre posts)")
    if vid and os.path.exists(vid):
        import shutil as _sh
        _sh.rmtree(os.path.dirname(vid), ignore_errors=True)
    mark_cat(conn, label_cat)
    log_keywords(conn, keywords)
    log_special(conn, "breaking", keywords)   # partage l'anti-spam (1 fast-track / 25 min)
    if item.get("url"):
        mark_seen(conn, item["url"], item["title"])
    return keywords

def _strip_html(text):
    """Nettoie un résumé RSS : balises HTML, entités, liens, espaces — Claude reçoit du texte propre."""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)                  # balises
    text = re.sub(r'&[a-zA-Z#0-9]+;', ' ', text)          # entités (&amp; &nbsp; ...)
    text = re.sub(r'https?://\S+', '', text)              # liens bruts
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:400]

def check_feeds(conn):
    global _META_CONN
    _META_CONN = conn
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🔍 Check Pulse...")

    # ── MODE COUPE DU MONDE : matchs du jour (matin) + prono la veille des matchs de la France ──
    try:
        if not special_done_today(conn, "cdm_jour") and _paris_hour() >= 8:
            publish_cdm_day(conn)
        if _paris_hour() >= 18:
            publish_cdm_prono(conn)
    except Exception as e:
        print(f"  ⚠️ Mode CDM : {e}")

    # ── RÉCAP DU SOIR : les 5 infos qui ont marqué la journée (1×/jour, après 21h Paris) ──
    try:
        if not special_done_today(conn, "recap") and _paris_hour() >= 21:
            publish_recap(conn)
    except Exception as e:
        print(f"  ⚠️ Récap du soir : {e}")

    # ── DÉCRYPTAGE QUOTIDIEN : carrousel Instagram + texte X/Facebook (1×/jour) ──
    if not special_done_today(conn, "thread") and datetime.now().hour >= 9:
        carousel = gen_carousel(conn)
        if carousel:
            # Texte X + Facebook (assemblé depuis le carrousel, sans 2e appel Claude)
            body = carousel_to_text(carousel)
            tags = " ".join("#" + re.sub(r'[^0-9A-Za-zÀ-ÿ]', '', k)
                            for k in carousel.get("keywords", [])[:2] if k and k.strip())
            xfb = body + (("\n\n" + tags) if tags else "")

            # Image de couverture : og:image de l'article si dispo, sinon vraie photo via image_query.
            raw_src, has_real = None, False
            if carousel.get("og_bytes"):
                raw_src, has_real = carousel["og_bytes"], True
            if not raw_src:
                try:
                    raw_src, has_real = get_best_image(carousel.get("url"), None, None,
                                                       carousel["image_query"], "monde")
                except Exception:
                    raw_src = None
            if not raw_src:
                print("  ⚠️ Décryptage reporté (aucune image) — le run continue.")
                carousel = None
        if carousel:
            cover_paysage, _ = build_png(carousel["cover_title"][:75], "Pulse", "monde", None,
                                         carousel["image_query"], prefetched=(raw_src, has_real))
            vid_thread = build_video("news", {"headline": carousel["cover_title"][:90]}, "monde", raw_src, "Pulse")
            url = None
            try:
                url = post_to_twitter(xfb, cover_paysage, vid_thread)
            except Exception as e:
                print(f"  ❌ X isolé : {e}")
            try:
                post_to_facebook(xfb, cover_paysage, vid_thread)
            except Exception as e:
                print(f"  ❌ Facebook isolé : {e}")
            if vid_thread and os.path.exists(vid_thread):
                import shutil as _sh
                _sh.rmtree(os.path.dirname(vid_thread), ignore_errors=True)

            # Carrousel Instagram : couverture (4:5) + slides de contenu (fond photo flouté)
            total = len(carousel["slides"]) + 1
            cover_ig, _ = build_png(carousel["cover_title"][:75], "Pulse", "monde", None,
                                    carousel["image_query"], W=1080, H=1350,
                                    prefetched=(raw_src, has_real), headline_bottom=True)
            slides_png = [cover_ig]
            for i, s in enumerate(carousel["slides"], start=2):
                slides_png.append(build_carousel_slide(s["titre"], s["points"], i, total,
                                                       is_last=(i == total), bg_photo=raw_src))
            post_carousel_to_instagram(slides_png, build_ig_caption(body, carousel.get("keywords")))
            log_special(conn, "ig_post", [])   # le carrousel compte dans l'espacement anti-blocage Instagram

            if url:
                log_special(conn, "thread", carousel["keywords"])
                print(f"  🎠 Décryptage du jour publié (carrousel Instagram) [{carousel['sujet']}]")
                return

    # ── SONDAGE QUOTIDIEN (après-midi, 1×/jour) ──
    if not special_done_today(conn, "poll") and datetime.now().hour >= 12:
        poll = gen_poll(conn)
        if poll:
            url = post_poll(poll["question"], poll["options"])
            # Facebook : pas de sondage natif via API → on poste la question + options en texte
            fb_text = poll["question"] + "\n\n" + "\n".join(f"• {o}" for o in poll["options"]) + "\n\n👉 Votez en commentaire !"
            post_to_facebook(fb_text)
            if url:
                log_special(conn, "poll", poll["keywords"])
                print(f"  📊 Sondage du jour publié")
                return  # on s'arrête là pour ce run

    # ── SCAN RSS (à chaque run, gratuit) — sert au MODE BREAKING et à la publi normale ──
    print(f"  → Scan RSS...")
    blocked_kws = recent_keywords(conn, hours=12)
    allow_sport_result = not sport_result_recent(conn)   # autorise UN résultat de match malgré le blocage 12h
    allow_followup     = not followup_recent(conn)        # autorise UNE suite d'affaire malgré le blocage 12h
    candidates  = []
    pre_filtered = 0
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:3]:
                url   = entry.get("link", "")
                title = entry.get("title", "")
                summ  = _strip_html(entry.get("summary", entry.get("description", "")))
                if url and title and not is_seen(conn, url):
                    # Pré-filtre GRATUIT : si le titre contient un mot-clé déjà publié (12h),
                    # on rejette SANS payer Claude — SAUF si c'est le RÉSULTAT d'un match (1 dérogation/4h)
                    title_low = title.lower()
                    is_fu = False
                    if blocked_kws and any(kw in title_low for kw in blocked_kws):
                        if allow_sport_result and _is_sport_result(title):
                            pass  # résultat final d'un match déjà couvert
                        elif allow_followup and _is_followup(title) and not _is_soft_news(title):
                            is_fu = True  # nouveau développement d'un gros sujet (ex: un ministre s'exprime)
                        else:
                            mark_seen(conn, url, title)
                            pre_filtered += 1
                            continue
                    candidates.append({"url": url, "title": title, "summary": summ, "source": fi["source"], "entry": entry, "followup": is_fu})
        except Exception as e:
            print(f"  ❌ RSS {fi['source']}: {e}")

    if pre_filtered:
        print(f"  🚫 {pre_filtered} articles pré-filtrés (mots-clés bloqués, sans coût Claude)")

    recent = get_recent(conn)

    # ── MODE BREAKING : un même sujet repris par plusieurs sources → publication IMMÉDIATE ──
    nb_today = posts_today(conn)
    breaking = detect_breaking(conn, candidates)
    if breaking and nb_today < DAILY_POST_CAP:   # le breaking passe tant qu'on est sous le plafond ferme (24)
        print(f"  🚨 Breaking potentiel (multi-sources) : {breaking['title'][:55]}")
        try:
            a = analyse_batch([breaking], recent, blocked_kws)[0]
            cache_analysis(conn, breaking["url"], a)
        except Exception:
            a = {"score": BREAKING_SCORE, "category": "breaking", "is_duplicate": False}
        if not a.get("is_duplicate") and int(a.get("score", 0)) >= BREAKING_SCORE:
            try:
                # Vrai breaking (drame, urgence) → label rouge URGENT
                kws = publish_breaking(conn, breaking, a.get("category", "breaking"), urgent=True)
                print(f"  🚨 BREAKING publié immédiatement : {breaking['title'][:55]}")
                if kws:
                    print(f"  🔒 Mots-clés bloqués 12h: {', '.join(kws)}")
                return
            except Exception as e:
                print(f"  ❌ Breaking échoué : {e}")
        elif not a.get("is_duplicate") and int(a.get("score", 0)) >= BUZZ_SCORE and nb_today < DAILY_POST_SOFT:
            try:
                # Buzz viral → publié vite (label normal), mais seulement sous le seuil souple (20)
                kws = publish_breaking(conn, breaking, a.get("category", "france"), urgent=False)
                print(f"  ⚡ BUZZ publié rapidement (label normal) : {breaking['title'][:55]}")
                if kws:
                    print(f"  🔒 Mots-clés bloqués 12h: {', '.join(kws)}")
                return
            except Exception as e:
                print(f"  ❌ Buzz échoué : {e}")
        else:
            print(f"  → Sujet pas assez repris pour un fast-track (score {a.get('score')}).")
    elif breaking:
        print(f"  🛑 Plafond quotidien atteint ({nb_today}) — breaking ignoré pour ne pas spammer.")

    # ── PUBLICATION NORMALE (rythme selon l'heure) ──
    # Plafond GLOBAL : au-delà du seuil souple (20), seuls les résultats sport frais peuvent encore passer.
    if nb_today >= DAILY_POST_CAP:
        print(f"  🛑 Plafond quotidien ferme atteint ({nb_today}/{DAILY_POST_CAP}) — stop publications.")
        return
    sport_result_waiting = allow_sport_result and any(
        _is_sport_result(c["title"]) for c in candidates)
    if nb_today >= DAILY_POST_SOFT and not sport_result_waiting:
        print(f"  🛑 Seuil souple atteint ({nb_today}/{DAILY_POST_SOFT}) — on garde la place au chaud (sport/breaking).")
        return
    # Dérogation : un RÉSULTAT sportif frais (ex: match de CDM la nuit) passe malgré le délai,
    # tant qu'aucun résultat n'a été publié depuis 90 min (anti-spam). Sinon il vieillirait trop.
    if not sport_result_waiting and not should_publish_now(conn):
        return
    if sport_result_waiting and not should_publish_now(conn):
        print("  ⚽ Dérogation cadence : un résultat sportif frais est prioritaire")

    if not candidates:
        print("  → Aucun article nouveau.")
        return

    print(f"  → {len(candidates)} articles à analyser...")
    if blocked_kws:
        print(f"  🚫 Mots-clés bloqués (12h) : {', '.join(blocked_kws)}")

    scored      = []
    to_analyse  = []
    for c in candidates:
        cached = get_cached_analysis(conn, c["url"])
        if cached:
            a, score = cached, int(cached.get("score", 0))
            if a.get("is_duplicate"):
                mark_seen(conn, c["url"], c["title"]); continue
            if score < SCORE_MINIMUM:
                mark_seen(conn, c["url"], c["title"]); continue
            scored.append({**c, "analysis": a, "score": score})
        else:
            to_analyse.append(c)

    # 💰 Limite le nombre d'articles ENVOYÉS à Claude par passage (les articles en
    # cache restent gratuits). On garde un échantillon varié pour borner le coût API.
    MAX_ANALYSE = 18
    if len(to_analyse) > MAX_ANALYSE:
        skipped = len(to_analyse) - MAX_ANALYSE
        to_analyse = prerank_candidates(to_analyse, MAX_ANALYSE)
        print(f"  🎯 Pré-classement : {skipped} articles écartés, les {MAX_ANALYSE} plus prometteurs partent en analyse")

    if to_analyse:
        try:
            print(f"  🧠 Batch analyse de {len(to_analyse)} articles...")
            analyses = analyse_batch(to_analyse, recent, blocked_kws)
            for c, a in zip(to_analyse, analyses):
                cache_analysis(conn, c["url"], a)
                score = int(a.get("score", 0))
                if a.get("is_duplicate"):
                    mark_seen(conn, c["url"], c["title"])
                    print(f"  ⏩ Doublon: {c['title'][:55]}")
                    continue
                if score < SCORE_MINIMUM:
                    mark_seen(conn, c["url"], c["title"])
                    print(f"  📉 {score}/10: {c['title'][:55]}")
                    continue
                scored.append({**c, "analysis": a, "score": score})
                print(f"  ✅ {score}/10 [{a.get('category')}]: {c['title'][:55]}")
        except Exception as e:
            print(f"  ❌ Batch analyse: {e}")

    # 🚨 Garde-fou : un rapport / étude / analyse / sondage ne doit JAMAIS porter le label "breaking/URGENT"
    for item in scored:
        if item["analysis"].get("category") == "breaking" and _is_soft_news(item["title"]):
            item["analysis"]["category"] = "france"
            print(f"  ⬇️ 'breaking' déclassé (contenu non urgent) : {item['title'][:50]}")

    # Boost catégorie pas encore vue aujourd'hui
    missing = set(STYLES.keys()) - cats_today(conn)
    for item in scored:
        if item["analysis"]["category"] in missing and item["score"] >= 6:
            item["score"] = min(10, item["score"] + 1)   # la variété départage les BONS sujets, ne sauve pas les mauvais

    # 🏀 Léger coup de pouce au SPORT EN DIRECT (cost-neutral : ne crée pas de post en plus).
    #    Anti-spam : seulement si aucun post sport depuis 2h (+ blocage mots-clés 12h sur le même match).
    if not sport_cooldown_active(conn):
        for item in scored:
            if item["analysis"].get("category") == "sport" and _is_live_sport(item):
                item["score"] = min(10, item["score"] + 2)

    scored.sort(key=lambda x: x["score"], reverse=True)
    top, used = [], set()
    for item in scored:
        cat = item["analysis"]["category"]
        if cat not in used:
            top.append(item); used.add(cat)
        if len(top) >= MAX_PAR_PASSE: break

    # Histoire du jour (1×/jour, vérifié Wikipedia)
    histoire = gen_histoire_du_jour(conn)
    if histoire and "histoire" not in used and len(top) < MAX_PAR_PASSE + 1:
        top.append(histoire); used.add("histoire")

    if not top:
        print("  → Rien à publier.")
        return

    print(f"  → {len(top)} sélectionné(s) [{', '.join(used)}]")

    for item in top:
        try:
            cat = item["analysis"]["category"]
            a   = item["analysis"]
            keywords = []
            # Décès d'une personne → catégorie HOMMAGE (label 🕊️, ton sobre), jamais "faits divers"
            if cat != "breaking" and _is_obituary(item.get("title", ""), item.get("summary", "")):
                cat = "hommage"

            if "tweet" in item:
                tweet_final    = item["tweet"]
                headline_court = item["headline_court"]
                image_query    = item.get("image_query")
                photo          = item.get("photo_url")
                video          = None
                keywords       = item.get("keywords", [])
                person         = item.get("person", "")
            else:
                add_recent(conn, item["title"])
                video = None
                body, headline_court, image_query, keywords, person = gen_tweet_verified(
                    item["title"], item["summary"], item["source"], cat, url=item.get("url")
                )
                tweet_final = build_full_tweet(body, cat)
                photo       = extract_photo(item["entry"])

            # ⚖️ Les vidéos d'articles tiers ne sont JAMAIS republiées (droit d'auteur / risque de strike).
            #    Les seules vidéos publiées sont celles générées par Pulse (build_video).
            video_path = None

            # Image paysage (X + Facebook) — on récupère aussi l'image source pour réutilisation
            raw_src, has_real = get_best_image(item.get("url"), photo, person, image_query, cat)

            # 🏆 Si c'est un RÉSULTAT sportif : carte de victoire (photo floutée + score)
            victory = None
            if cat == "sport" and "tweet" not in item and _is_sport_result(item.get("title", "")):
                victory = extract_sport_result(item["title"], item.get("summary", ""))

            # 🕊️ Si c'est le DÉCÈS d'une personnalité : carte hommage (portrait N&B + nom)
            obituary = None
            if not victory and "tweet" not in item and cat == "hommage":
                obituary = extract_obituary(item["title"], item.get("summary", ""), item.get("url"))

            if victory:
                png_bytes = build_victory_card(raw_src, victory, item["source"], W=1200, H=675)
                png_ig    = build_victory_card(raw_src, victory, item["source"], W=1080, H=1350)
                video_path = build_video("victory", victory, "sport", raw_src, item["source"])
                print(f"  🏆 Carte résultat ({victory['type']}) publiée")
            elif obituary:
                png_bytes = build_hommage_card(raw_src, obituary["name"], obituary["dates"],
                                               obituary["desc"], item["source"], W=1200, H=675)
                png_ig = build_hommage_card(raw_src, obituary["name"], obituary["dates"],
                                            obituary["desc"], item["source"], W=1080, H=1350)
                video_path = build_video("hommage", obituary, "hommage", raw_src, item["source"])
                print(f"  🕊️ Carte hommage : {obituary['name']}")
            else:
                png_bytes, png_nm = build_png(
                    headline_court, item["source"], cat, photo, image_query,
                    article_url=item.get("url"), person=person,
                    prefetched=(raw_src, has_real)
                )
                png_ig, _ = build_png(
                    headline_court, item["source"], cat, photo, image_query,
                    article_url=item.get("url"), person=person,
                    W=1080, H=1350, prefetched=(raw_src, has_real), headline_bottom=True
                )
                if not video_path:   # vidéo animée Pulse sur TOUS les posts (barre néon couleur catégorie)
                    video_path = build_video("news", {"headline": headline_court}, cat, raw_src, item["source"])

            try:
                post_to_twitter(tweet_final, png_bytes, video_path)
            except Exception as e:
                print(f"  ❌ X isolé : {e}")
            try:
                post_to_facebook(tweet_final, png_bytes, video_path)
            except Exception as e:
                print(f"  ❌ Facebook isolé : {e}")
            if ig_allowed(conn):
                post_to_instagram(build_ig_caption(tweet_final, keywords), png_ig)
                log_special(conn, "ig_post", [])
            else:
                print("  ⏸️ Instagram en pause (anti-blocage : min 90 min entre posts)")
            if video_path and os.path.exists(video_path):
                import shutil as _sh
                _sh.rmtree(os.path.dirname(video_path), ignore_errors=True)

            # Nettoyage du fichier vidéo temporaire (après X + Facebook)
            if video_path:
                try:
                    import os as _os
                    if _os.path.exists(video_path):
                        _os.remove(video_path)
                except: pass

            mark_cat(conn, cat)
            log_keywords(conn, keywords)
            if item.get("followup"):
                log_special(conn, "followup", keywords)   # 1 suite d'affaire / 4h
            if cat == "sport" and _is_sport_result(item.get("title", "")):
                log_special(conn, "sport_result", keywords)   # 1 dérogation résultat / 4h
            if item.get("url"):
                mark_seen(conn, item["url"], item["title"])
            print(f"  ✅ Publié [{cat}]: {item['title'][:55]}")
            if keywords:
                print(f"  🔒 Mots-clés bloqués 12h: {', '.join(keywords)}")
            time.sleep(4)
        except Exception as e:
            print(f"  ❌ Envoi '{item['title'][:40]}': {e}")


def main():
    print("🤖 Pulse NewsBot démarré !")
    conn = init_db()
    while True:
        check_feeds(conn)
        time.sleep(7200)

if __name__ == "__main__":
    main()
