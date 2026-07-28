"""
Pulse NewsBot — bot d'actualité française.
Génère des tweets engageants avec image PNG, envoyés par email + posté sur X.
"""
import feedparser, anthropic, sqlite3, hashlib, json, time, os, smtplib, random
import requests   # déjà présent dans requirements.txt (inchangé) — sert aux API REST
import unicodedata
import socket
socket.setdefaulttimeout(12)   # aucun flux RSS/site mort ne peut geler un run
import urllib.request, urllib.parse, urllib.error, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# En-têtes proches d'un vrai navigateur → réduit fortement les 403/404 des sites de presse
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",   # un vrai navigateur l'envoie TOUJOURS ; son absence
                                          # est elle-même un signal qui fait flaguer la requête
                                          # comme un bot par certains pare-feux (Cloudflare...).
}

def _read_capped(response, cap=3_000_000, chunk=65536):
    """Lit la réponse HTTP en entier (jusqu'à 'cap' octets), par morceaux.
    Un .read(N) unique sur un flux gzip risquerait de tronquer le flux COMPRESSÉ en plein
    milieu si la page dépasse N octets compressés → décompression impossible, page perdue."""
    parts, total = [], 0
    while total < cap:
        block = response.read(chunk)
        if not block:
            break
        parts.append(block)
        total += len(block)
    return b"".join(parts)

def _decode_html_body(raw_bytes, content_encoding):
    """Décompresse le corps HTTP si besoin (gzip/deflate), puis décode en texte.
    Tolère un flux TRONQUÉ (lecture interrompue) : on récupère alors le maximum de
    contenu décompressable au lieu de tout perdre (le <head>, qui contient
    og:image/JSON-LD, arrive en tout début de flux donc se décompresse en premier)."""
    import zlib
    try:
        if "gzip" in content_encoding:
            try:
                import gzip
                raw_bytes = gzip.decompress(raw_bytes)
            except Exception:
                # flux gzip incomplet/tronqué → décompression partielle tolérante
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw_bytes = d.decompress(raw_bytes)
        elif "deflate" in content_encoding:
            try:
                raw_bytes = zlib.decompress(raw_bytes)
            except zlib.error:
                try:
                    raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
                except zlib.error:
                    d = zlib.decompressobj(-zlib.MAX_WBITS)
                    raw_bytes = d.decompress(raw_bytes)
    except Exception:
        pass   # si la décompression échoue totalement, on retombe sur les octets bruts
    return raw_bytes.decode("utf-8", errors="ignore")
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
# ── Fournisseur de modèle, choisi PAR TÂCHE ──
# Par défaut TOUT passe par le fournisseur gratuit ; Claude reste le SECOURS automatique
# (quota dépassé, panne, réponse illisible) — une publication n'est jamais perdue.
# Sans clé Gemini, tout retombe sur Claude : le comportement d'origine est préservé.
# Pour repasser une tâche sur Claude : LLM_ANALYSE / LLM_REDACTION / LLM_SPECIAUX = claude
PULSE_VERSION = "1.40.1"   # affiché à chaque cycle : permet de vérifier d'un coup d'œil
                           # que le bot.py en ligne est bien le dernier livré.
# ✳️ Hashtags : la charte Pulse en impose un, mais AUCUN des tweets de référence n'en porte.
#    Réglage laissé ouvert : HASHTAGS=0 dans le workflow pour coller aux exemples.
HASHTAGS_ACTIFS = os.environ.get("HASHTAGS", "1").strip() not in ("0", "false", "non")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL",      "gemini-3.5-flash-lite")
LLM_ANALYSE       = os.environ.get("LLM_ANALYSE",       "gemini").strip().lower()
LLM_REDACTION     = os.environ.get("LLM_REDACTION",     "gemini").strip().lower()
LLM_SPECIAUX      = os.environ.get("LLM_SPECIAUX",      "gemini").strip().lower()
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
BUZZ_GAP_MIN = 75         # espacement MINIMUM entre deux buzz non-urgents, le JOUR
BUZZ_GAP_NIGHT_MIN = 150  # la nuit, on espace deux fois plus (cohérent avec la cadence nocturne)
BREAKING_SOURCES = 3      # nb de sources distinctes couvrant le même sujet pour déclencher le breaking
BREAKING_GAP_MIN = 25     # délai mini (minutes) entre deux publications breaking (anti-spam)
STALE_BREAKING_HOURS = 6  # au-delà, un article n'est plus assez frais pour un "breaking" (anti-réchauffé)
STALE_NEWS_HOURS = 24     # au-delà, ce n'est plus une actualité : écarté du fil normal (sauf suivi)
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
    "gta6":          {"color": "#00d17a", "label": "GTA 6",         "bar": [(0,209,122),(255,64,129)],    "overlay": (10,2,16)},
}

EMOJIS = {
    "breaking": "🚨", "france": "🇫🇷", "monde": "🌍", "politique": "🏛️",
    "economie": "📈", "societe": "👥", "faitsdivers": "🚓", "hommage": "🕊️", "histoire": "📜",
    "culture": "🎭",  "sport": "🏆", "science": "🔬",
    "sante":    "🏥", "environnement": "🌱",
    "tech":     "💻", "ia": "🤖", "insolite": "😲", "positivity": "❤️", "gta6": "🎮",
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
    "insolite": "INSOLITE", "positivity": "POSITIF", "gta6": "GTA 6",
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
        # Mémoire PAR SUJET : ce qu'on a déjà publié sur chaque histoire en cours.
        # Permet à un gros sujet de ressortir s'il apporte du NEUF (plafond/jour + écart mini),
        # au lieu du verrou binaire par mot-clé. Source unique du suivi éditorial d'un sujet.
        """CREATE TABLE IF NOT EXISTS topic_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_sig TEXT,
            headline TEXT,
            keywords TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS topic_echo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_key TEXT,
            source TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(topic_key, source)
        )""",
        """CREATE TABLE IF NOT EXISTS topic_echo_alert (
            topic_key TEXT PRIMARY KEY,
            sources_at_alert INTEGER DEFAULT 0,
            alerted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        # Journal des VRAIES publications (1 ligne par post réellement sorti). Sert au compteur
        # quotidien (remis à zéro à minuit HEURE DE PARIS), séparé de recent_titles (anti-doublon).
        """CREATE TABLE IF NOT EXISTS post_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]:
        conn.execute(sql)
    # 🔧 MIGRATIONS : CREATE TABLE IF NOT EXISTS ne modifie PAS une table déjà présente dans un
    # cache restauré. On ajoute donc explicitement les colonnes manquantes sur les bases existantes,
    # sinon toute lecture d'une colonne récente plante ("no such column"). Idempotent et sûr.
    def _ensure_column(table, column, ddl):
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        except Exception:
            pass
    _ensure_column("topic_echo_alert", "sources_at_alert", "sources_at_alert INTEGER DEFAULT 0")
    _ensure_column("topic_echo_alert", "alerted_at", "alerted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    _ensure_column("post_log", "category", "category TEXT")
    _ensure_column("post_log", "posted_at", "posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_cache (
        key TEXT PRIMARY KEY, payload TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    # 🖼️ Articles publiés aujourd'hui (titre + url + catégorie) : sert à illustrer le récap
    #    du soir avec une vraie image liée à chaque actu. Purgé après 2 jours.
    # 🧠 vecteur de sens du sujet (embedding) : permet de reconnaître un même sujet
    #    reformulé. Colonne ajoutée après coup → tolérante si elle existe déjà.
    try:
        conn.execute("ALTER TABLE topic_memory ADD COLUMN vec TEXT")
    except Exception:
        pass
    # 📚 Corps réellement publié : la mémoire éditoriale a besoin de savoir CE QU'ON A DIT,
    #    pas seulement qu'on a parlé du sujet. Sert à ne jamais répéter un fait déjà donné.
    try:
        conn.execute("ALTER TABLE topic_memory ADD COLUMN corps TEXT")
    except Exception:
        pass
    # 🧮 journal des embeddings du jour : sert à respecter le budget du palier gratuit
    conn.execute("""CREATE TABLE IF NOT EXISTS embed_log (
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("DELETE FROM embed_log WHERE created_at < datetime('now', '-2 days')")
    conn.execute("""CREATE TABLE IF NOT EXISTS recap_srcs (
        title TEXT, url TEXT, category TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.execute("DELETE FROM recap_srcs WHERE created_at < datetime('now', '-2 days')")
    conn.commit()
    conn.execute("DELETE FROM daily_cache WHERE created_at < datetime('now', '-2 days')")
    # 💰 Cache d'analyse : gardé 45 JOURS (aligné sur `seen`). Le score/la catégorie d'un article
    #    ne changent jamais → aucune raison de le ré-analyser. Avant, la purge à 24h forçait une
    #    ré-analyse quotidienne de tous les articles encore présents dans les flux (coût API inutile).
    conn.execute("DELETE FROM analyzed_cache WHERE analyzed_at < datetime('now', '-45 days')")
    # 🔥 Écho médiatique : on ne garde que 12h (au-delà, un sujet n'est plus "chaud")
    conn.execute("DELETE FROM topic_echo WHERE first_seen < datetime('now', '-8 hours')")
    conn.execute("DELETE FROM topic_echo_alert WHERE alerted_at < datetime('now', '-8 hours')")
    conn.execute("DELETE FROM keyword_log    WHERE last_sent   < datetime('now', '-2 hours')")
    conn.execute("DELETE FROM special_log    WHERE sent_at     < datetime('now', '-8 days')")
    conn.execute("DELETE FROM seen           WHERE seen_at     < datetime('now', '-45 days')")   # la base reste légère
    conn.execute("DELETE FROM topic_memory    WHERE sent_at     < datetime('now', '-2 days')")
    conn.commit()
    return conn

def is_seen(conn, url):
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM seen WHERE hash=?", (h,)).fetchone() is not None

def remember_recap_src(conn, title, url, category):
    """Mémorise un article publié aujourd'hui pour pouvoir illustrer le récap du soir."""
    if not url:
        return
    try:
        conn.execute("INSERT INTO recap_srcs (title, url, category) VALUES (?,?,?)",
                     (title or "", url, (category or "france")))
        conn.commit()
    except Exception:
        pass

def mark_seen(conn, url, title):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO seen (hash,title) VALUES (?,?)", (h, title))
    conn.commit()

def get_recent(conn):
    # Mémoire anti-doublon montrée à Claude ET utilisée pour ne pas re-"breaker" un sujet déjà publié.
    # 150 titres ≈ ~1 semaine de publications : le bot se souvient plus longtemps de ce qu'il a couvert.
    return [r[0] for r in conn.execute("SELECT title FROM recent_titles ORDER BY added_at DESC LIMIT 150").fetchall()]

def add_recent(conn, title):
    conn.execute("INSERT INTO recent_titles (title) VALUES (?)", (title,))
    conn.execute("DELETE FROM recent_titles WHERE id NOT IN (SELECT id FROM recent_titles ORDER BY added_at DESC LIMIT 300)")
    conn.commit()

def cats_today(conn):
    today = datetime.now().strftime("%Y-%m-%d")
    return {r[0] for r in conn.execute("SELECT category FROM category_log WHERE last_sent LIKE ?", (f"{today}%",)).fetchall()}

def mark_cat(conn, cat):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO category_log (category,last_sent) VALUES (?,?) ON CONFLICT(category) DO UPDATE SET last_sent=excluded.last_sent", (cat, now))
    # Journal des VRAIES publications (une ligne par post réellement sorti) → compteur du jour fiable,
    # séparé de recent_titles (anti-doublon). mark_cat n'est appelé qu'après une publication réussie.
    try:
        conn.execute("INSERT INTO post_log (category) VALUES (?)", (cat,))
    except Exception:
        pass
    conn.commit()
    _touch_publish_time()   # filet de sécurité : horodatage fichier persisté via le workflow Git

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

def recent_keywords(conn, hours=2):
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
    """Heure de la dernière publication = la PLUS RÉCENTE entre la base (category_log)
    et un fichier d'horodatage (last_publish.txt). Le fichier est un filet de sécurité
    si la base seen_articles.db n'est pas persistée entre deux runs GitHub."""
    candidates = []
    row = conn.execute("SELECT MAX(last_sent) FROM category_log WHERE last_sent != '2000-01-01'").fetchone()
    if row and row[0]:
        try:
            candidates.append(datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
    try:
        with open("last_publish.txt", encoding="utf-8") as fh:
            ts = datetime.strptime(fh.read().strip()[:19], "%Y-%m-%d %H:%M:%S")
            # Ignore une date FUTURE (corruption) : sinon elle bloquerait toute publication.
            if ts <= datetime.now() + timedelta(minutes=5):
                candidates.append(ts)
    except Exception:
        pass
    return max(candidates) if candidates else None

def _touch_publish_time():
    """Écrit l'heure de publication dans last_publish.txt (persisté via le workflow Git)."""
    try:
        with open("last_publish.txt", "w", encoding="utf-8") as fh:
            fh.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass

# ── Plafond quotidien GLOBAL de publications (toutes sources confondues) ──
DAILY_POST_CAP = 30          # plafond FERME (une alerte vitale peut seule passer au-delà)
DAILY_POST_SOFT = 24         # au-delà, on ne garde QUE le très chaud (breaking/résultats forts)

# ── Mémoire par sujet : un gros sujet qui ÉVOLUE peut ressortir dans la journée ──
# (ne fait PAS grimper le total quotidien : il PREND la place d'une opportunité plus faible)
TOPIC_MAX_PER_DAY = 3        # nb max de tweets sur un MÊME sujet dans la journée
TOPIC_MIN_GAP_MIN = 60       # écart minimum (minutes) entre deux tweets sur le même sujet

def _paris_today_bounds():
    """Renvoie (début_jour_paris_en_UTC, maintenant_UTC) au format 'YYYY-MM-DD HH:MM:SS'
    pour compter 'aujourd'hui' selon l'heure de PARIS, alors que SQLite stocke en UTC."""
    from datetime import timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        pz = ZoneInfo("Europe/Paris")
    except Exception:
        from datetime import timedelta as _td
        pz = _tz(_td(hours=2))
    now_p = datetime.now(pz)
    start_p = now_p.replace(hour=0, minute=0, second=0, microsecond=0)
    start_u = start_p.astimezone(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    now_u = now_p.astimezone(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    return start_u, now_u

def posts_today(conn):
    """Nombre de publications RÉELLES aujourd'hui (journal post_log), à l'heure de Paris.
    Ne compte QUE les vrais posts sortis (via mark_cat), pas les titres vus/anti-doublon."""
    try:
        start_u, now_u = _paris_today_bounds()
        row = conn.execute(
            "SELECT COUNT(*) FROM post_log WHERE posted_at >= ? AND posted_at <= ?",
            (start_u, now_u)).fetchone()
        return row[0] if row else 0
    except Exception:
        # repli : ancien comptage (ne casse jamais la publication)
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

def _is_night(h=None):
    """Heures de NUIT (minuit-7h, Paris) : le fil se met en quasi-pause. Seules les VRAIES
    alertes vitales (breaking urgent, résultat de match France) passent la nuit.
    Les canaux bonus (buzz, hommage non-urgent, histoire) sont mis en pause.
    La soirée jusqu'à minuit reste ACTIVE : c'est une plage de forte audience."""
    if h is None:
        h = _paris_hour()
    return h < 7

def _cadence_minutes(h):
    """Rythme de publication. Base ~1h30 partout (probabilité croissante jusqu'à 2h30),
    nuit fortement ralentie. PLUS d'accélération prime-time (trop coûteux). Les alertes
    (breaking, résultats sport) restent prioritaires et ne passent pas par ce rythme."""
    if h < 7:
        return 180, 300, "nuit (quasi-pause, seule une alerte vitale passe)"
    return 30, 90, "journée (30 min à 1h30 entre deux actus normales)"

_CADENCE_DECISION = None   # décision de cadence du run courant (un seul tirage par run)

def should_publish_now(conn, min_minutes=None, max_minutes=None):
    # 🎲→1 UNE SEULE décision par run : sans mémoire, chaque appel retirait les dés —
    #    un suivi refusé à 39 % puis une news acceptée 10 s plus tard sur le même tirage
    #    rendait la cadence incohérente à l'intérieur d'un même run.
    global _CADENCE_DECISION
    if _CADENCE_DECISION is not None:
        return _CADENCE_DECISION
    if min_minutes is None or max_minutes is None:
        h = _paris_hour()
        min_minutes, max_minutes, mode = _cadence_minutes(h)
        print(f"  🕐 {h}h (Paris) — rythme {mode} : {min_minutes}-{max_minutes} min")
    last = last_publish_time(conn)
    if not last:
        _CADENCE_DECISION = True
        return True
    elapsed = (datetime.now() - last).total_seconds() / 60
    if elapsed < min_minutes:
        print(f"  ⏸️  Dernière publi il y a {int(elapsed)} min — attente.")
        _CADENCE_DECISION = False
        return False
    if elapsed > max_minutes:
        print(f"  ✅ Dernière publi il y a {int(elapsed)} min — on publie.")
        _CADENCE_DECISION = True
        return True
    proba = (elapsed - min_minutes) / (max_minutes - min_minutes)
    publish = random.random() < proba
    if publish:
        print(f"  🎲 {int(elapsed)} min (proba {int(proba*100)}%) → on publie.")
    else:
        print(f"  🎲 {int(elapsed)} min (proba {int(proba*100)}%) → on attend.")
    _CADENCE_DECISION = publish
    return publish

# ═══════════════════════════════════════════════════════════════════════════
# CLAUDE API
# ═══════════════════════════════════════════════════════════════════════════
_CLAUDE_CALLS = 0   # compteur d'appels Claude PAYÉS sur le run courant (remis à 0 à chaque run)

import atexit as _atexit
@_atexit.register
def _print_claude_meter():
    # S'affiche à la toute fin du processus (donc en fin de run GitHub Actions), quel que soit
    # le chemin de sortie. On n'affiche rien si aucun appel n'a été fait (run en attente de cadence).
    try:
        if not _CLAUDE_CALLS and not any(_USAGE_GEMINI.values()):
            return
        if any(_USAGE_GEMINI.values()):
            print(f"  🆓 Gemini — entrée {_USAGE_GEMINI['in']:,} · sortie {_USAGE_GEMINI['out']:,} tokens"
                  f"  (palier gratuit : 0 ¢)")
        if _CLAUDE_CALLS:
            print(f"  🧮 Claude : {_CLAUDE_CALLS} appel(s) payé(s) ce cycle")
            if any(_USAGE[k] for k in _USAGE):
                print(f"     tokens — entrée {_USAGE['in']:,} · cache écrit {_USAGE['cache_w']:,} · "
                      f"cache relu {_USAGE['cache_r']:,} · sortie {_USAGE['out']:,}")
                print(f"     facture réelle de ce run : {cout_du_run():.3f} ¢")
                if _USAGE["cache_w"] and not _USAGE["cache_r"]:
                    print("     ℹ️ cache écrit mais jamais relu sur ce run — sans effet ici")
        if _LLM_FALLBACKS:
            print(f"  ↩️ {_LLM_FALLBACKS} repli(s) du gratuit vers Claude "
                  f"(à surveiller : si ça se répète, le gratuit n'est pas fiable)")
    except Exception:
        pass

# 💰 MISE EN CACHE DES CONSIGNES — DÉSACTIVÉE PAR DÉFAUT, sur données réelles.
#    Mesuré en production : une écriture de cache coûte 1,25× l'envoi normal, et Pulse ne
#    fait qu'UN appel de rédaction par cycle — le bloc était donc écrit puis jamais relu,
#    soit +20 % sur la facture (0,679 ¢ → 0,814 ¢ par tweet).
#    Le cache ne redevient rentable que si plusieurs appels partagent le même bloc à
#    quelques minutes d'intervalle. Mettre PROMPT_CACHE=1 pour le réactiver.
_PROMPT_CACHE_OK = os.environ.get("PROMPT_CACHE", "0").strip() in ("1", "true", "oui")
# 🧮 Consommation RÉELLE du run (remplie depuis la réponse de l'API) : permet de mesurer la
#    facture au lieu de l'estimer, et de vérifier si la mise en cache rapporte vraiment.
_USAGE = {"in": 0, "cache_w": 0, "cache_r": 0, "out": 0}
_USAGE_GEMINI = {"in": 0, "out": 0}   # consommation du fournisseur gratuit
_LLM_FALLBACKS = 0                    # nb de replis sur Claude (fiabilité du gratuit)
# Tarifs Haiku 4.5, en dollars par million de tokens
_PRIX = {"in": 1.00, "cache_w": 1.25, "cache_r": 0.10, "out": 5.00}

def _note_usage(msg):
    """Enregistre la consommation réelle d'un appel. Silencieux si l'info est absente."""
    try:
        u = getattr(msg, "usage", None)
        if not u:
            return
        _USAGE["in"]      += getattr(u, "input_tokens", 0) or 0
        _USAGE["cache_w"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        _USAGE["cache_r"] += getattr(u, "cache_read_input_tokens", 0) or 0
        _USAGE["out"]     += getattr(u, "output_tokens", 0) or 0
    except Exception:
        pass

def cout_du_run():
    """Coût réel du run, en centimes, à partir des tokens réellement facturés."""
    d = sum(_USAGE[k] * _PRIX[k] for k in _PRIX) / 1_000_000
    return d * 100

def _msg_kwargs(system, model, max_tokens, prompt):
    """Construit les arguments d'appel. Les CONSIGNES FIXES (barème, règles éditoriales)
    partent en bloc `system` marqué pour MISE EN CACHE : relues à 10 % du prix si le même
    bloc resert peu après (ex. analyse groupée + analyse du lot, ou tweet + régénération).
    ⚠️ Durée VOLONTAIREMENT COURTE (5 min, le défaut) : Pulse publie toutes les 110-180 min,
    donc deux runs ne se suivent jamais d'assez près. Un cache d'1 h coûterait le DOUBLE en
    écriture pour n'être jamais relu ; en 5 min l'écriture ne coûte que 1,25× et seuls les
    appels rapprochés d'un même run en profitent.
    Si le cache n'est pas disponible, on renvoie un appel classique — même résultat."""
    kw = {"model": model, "max_tokens": max_tokens,
          "messages": [{"role": "user", "content": prompt}]}
    if not system:
        return kw
    if _PROMPT_CACHE_OK:
        kw["system"] = [{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}]
    else:
        kw["system"] = system
    return kw


def _parse_json_reponse(raw):
    """Parse la réponse d'un modèle en JSON, avec repli si du texte l'entoure.
    Partagé par TOUS les fournisseurs : une seule source de vérité."""
    raw = (raw or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# ⏱️ RÉGULATEUR DE DÉBIT — le palier gratuit limite les requêtes PAR MINUTE, et la
#    limite est propre à chaque famille de modèles (constaté : 3/min sur la synthèse
#    vocale). Or le bot enchaîne des rafales : une narration par slide, c'est 5 appels
#    en quelques secondes. Sans régulation, les derniers sont refusés (429) et la vidéo
#    sort sans voix — ou la carte sans image.
_RATE_LIMITS = {"tts": 3, "image": 5, "texte": 12, "vision": 12}
_RATE_HIST = {}

def _attendre_creneau(famille="texte"):
    """Attend, si nécessaire, qu'un créneau se libère pour cette famille de modèles.
    Simple et sans dépendance : on garde l'horodatage des appels de la dernière minute."""
    import time as _t
    limite = _RATE_LIMITS.get(famille, 12)
    hist = _RATE_HIST.setdefault(famille, [])
    maintenant = _t.time()
    hist[:] = [h for h in hist if maintenant - h < 60]
    if len(hist) >= limite:
        pause = 61 - (maintenant - hist[0])
        if pause > 0:
            print(f"  ⏱️ Débit {famille} atteint ({limite}/min) → pause de {pause:.0f} s")
            _t.sleep(min(pause, 65))
            maintenant = _t.time()
            hist[:] = [h for h in hist if maintenant - h < 60]
    hist.append(maintenant)


def _post_gemini(url, payload, famille="texte", timeout=60, essais=2):
    """Appel POST vers Gemini, régulé et tolérant au 429.
    Un refus pour dépassement de débit n'est pas une panne : on attend et on réessaie."""
    import time as _t
    derniere = None
    for essai in range(essais):
        _attendre_creneau(famille)
        try:
            r = requests.post(url, headers={"x-goog-api-key": GEMINI_API_KEY,
                                            "Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            if getattr(r, "status_code", 200) == 429 and essai + 1 < essais:
                print(f"  ⏱️ Débit {famille} refusé par l'API → nouvelle tentative dans 20 s")
                _t.sleep(20)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            derniere = e
            if essai + 1 >= essais:
                raise
    if derniere:
        raise derniere
    return {}


def _usage_gemini(d):
    """Comptabilise la consommation renvoyée par l'API."""
    try:
        u = (d or {}).get("usageMetadata") or {}
        _USAGE_GEMINI["in"] += u.get("promptTokenCount", 0) or 0
        _USAGE_GEMINI["out"] += u.get("candidatesTokenCount", 0) or 0
    except Exception:
        pass


def _gemini_call(prompt, system, max_tokens, want_json=True):
    """Appel du modèle Gemini en REST — aucune dépendance nouvelle, `requests` suffit,
    donc requirements.txt reste intact. Lève une exception en cas d'échec (le repli
    Claude est géré par _llm_json)."""
    global _USAGE_GEMINI
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    gen = {"maxOutputTokens": int(max_tokens), "temperature": 0.7,
           # pas de « réflexion » : elle est facturée comme de la sortie et ralentit le run
           "thinkingConfig": {"thinkingBudget": 0}}
    if want_json:
        gen["responseMimeType"] = "application/json"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    data = _post_gemini(url, body, famille="texte", timeout=60)
    _usage_gemini(data)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _llm_json(prompt, max_tokens=600, system=None, task="analyse"):
    """Appelle le modèle choisi POUR CETTE TÂCHE et renvoie du JSON.
    🛡️ Claude reste le filet : si le fournisseur gratuit échoue (quota, réseau, réponse
    illisible), on repasse par Claude immédiatement — la publication n'est jamais perdue."""
    global _LLM_FALLBACKS
    fournisseur = {"analyse": LLM_ANALYSE, "redaction": LLM_REDACTION,
                   "special": LLM_SPECIAUX}.get(task, "claude")
    if fournisseur == "gemini" and GEMINI_API_KEY:
        try:
            return _parse_json_reponse(_gemini_call(prompt, system, max_tokens, want_json=True))
        except Exception as e:
            _LLM_FALLBACKS += 1
            print(f"  ⚠️ Gemini indisponible pour « {task} » ({str(e)[:90]}) → repli Claude")
    return claude(prompt, max_tokens=max_tokens, system=system)


def claude(prompt, max_tokens=600, model="claude-haiku-4-5-20251001", system=None):
    """Appel Claude avec parsing JSON blindé + 1 nouvelle tentative en cas d'erreur réseau/API."""
    global _CLAUDE_CALLS, _PROMPT_CACHE_OK
    _CLAUDE_CALLS += 1
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    last_err = None
    for attempt in (1, 2):
        try:
            try:
                msg = client.messages.create(**_msg_kwargs(system, model, max_tokens, prompt))
            except TypeError:
                # SDK trop ancien pour le cache → repli définitif pour ce run
                _PROMPT_CACHE_OK = False
                msg = client.messages.create(**_msg_kwargs(system, model, max_tokens, prompt))
            _note_usage(msg)
            return _parse_json_reponse(msg.content[0].text)
        except (anthropic.APIConnectionError, anthropic.APIStatusError, anthropic.RateLimitError) as e:
            last_err = e
            if _PROMPT_CACHE_OK and system and "cache" in str(e).lower():
                # l'API refuse le cache → on réessaie immédiatement SANS, sans perdre le tweet
                print("  ⚠️ Cache de prompt refusé par l'API → appel classique")
                _PROMPT_CACHE_OK = False
                continue
            if attempt == 1:
                time.sleep(3)
                continue
            raise
    raise last_err

def claude_text(prompt, max_tokens=700, model="claude-haiku-4-5-20251001", system=None):
    """Comme claude() mais renvoie du texte brut (pas de JSON)."""
    global _CLAUDE_CALLS, _PROMPT_CACHE_OK
    _CLAUDE_CALLS += 1
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        msg = client.messages.create(**_msg_kwargs(system, model, max_tokens, prompt))
    except TypeError:
        _PROMPT_CACHE_OK = False
        msg = client.messages.create(**_msg_kwargs(system, model, max_tokens, prompt))
    _note_usage(msg)
    return msg.content[0].text.strip()

_ANALYSE_SYS = None

def _analyse_system():
    """Consignes FIXES de l'analyse (barème, règles éditoriales, catégories).
    💰 Elles sont IDENTIQUES à chaque appel : envoyées en bloc `system` marqué pour mise en
    cache, elles sont facturées ~10× moins cher en lecture. Construites une seule fois pour
    que la chaîne soit rigoureusement identique d'un appel à l'autre (condition du cache)."""
    global _ANALYSE_SYS
    if _ANALYSE_SYS is None:
        cats = "|".join(LABELS.keys())
        _ANALYSE_SYS = f"""Tu es l'éditeur du compte Twitter Pulse, compte d'actualité française.

RÈGLE DOUBLON — LIS ATTENTIVEMENT :
- is_duplicate=true UNIQUEMENT si l'article répète essentiellement les MÊMES FAITS qu'un titre de la liste des DÉJÀ PUBLIÉS fournie ensuite (même information, rien de neuf pour le lecteur).
- is_duplicate=false si l'article apporte un VRAI nouveau développement sur un sujet en cours : une réaction, un recours/un appel, un verdict, un nouveau bilan, une décision officielle, une interpellation, un rebondissement. Un sujet MAJEUR qui évolue MÉRITE une nouvelle publication — ne le bloque PAS juste parce qu'on en a déjà parlé. Note-le alors sur son vrai potentiel d'engagement.

Réponds avec ce JSON UNIQUEMENT (un objet par article, dans le MÊME ORDRE).
Clés compactes : i = numéro d'article, s = score, c = catégorie, d = is_duplicate (1=oui, 0=non), v = needs_video (1=oui, 0=non).
{{"analyses":[
  {{"i":1,"s":<0-10>,"c":"<{cats}>","d":<0|1>,"v":<0|1>}},
  ...
]}}

Barème = la plus HAUTE de DEUX notes. Un bon compte d'actualité fait RÉAGIR **et** rend SERVICE.
  A) ENGAGEMENT : « des gens vont COMMENTER, S'INDIGNER, CÉLÉBRER, RIRE, PARTAGER ? »
  B) IMPACT CONCRET : « est-ce que ça change la vie, la santé, la sécurité, l'argent ou les droits des Français, au point qu'il faut le SAVOIR ou AGIR ? »
score = max(A, B). Une info sans émotion mais très utile monte haut par B. Une info virale sans utilité monte haut par A. Ni l'un ni l'autre = 0-4.

- 9-10 : fait MAJEUR en cours — mort d'une personnalité de premier plan, attentat, catastrophe, France qualifiée/éliminée en Coupe du Monde, démission du gouvernement, verdict d'un procès national. OU alerte VITALE immédiate : tsunami, évacuation, alerte enlèvement, rappel d'un produit dangereux à grande échelle. (Jamais : rapport, étude, sondage, classement, prévision → max 7.)
- 8 : ce qui fait halluciner ou vibrer la France. EXEMPLES CALIBRÉS : un arbitre de la Coupe du Monde privé de visa pour les USA = 8 ; l'usine du produit de YouTubeurs très connus (McFly et Carlito...) qui brûle = 8 ; un ministre s'exprime sur une affaire nationale brûlante = 8 ; grosse victoire des Bleus = 8 ; une banque envoie par erreur une notification de test à des millions de clients = 8. OU une décision ACTÉE qui change la vie des Français : loi définitivement adoptée (fin de vie, retraites, impôts) = 8 ; rappel de produits contaminés (listeria, salmonelle) = 8 ; interdiction nationale d'un produit ou d'un service = 8.
- 7 : résultat de match notable, garde à vue d'une personnalité, buzz viral national, sortie d'un jeu très attendu, drama d'influenceur connu, fait divers marquant. OU info de SERVICE forte : vigilance rouge (canicule, crue, tempête, neige) = 7 ; autorisation européenne d'un traitement contre une maladie majeure (Alzheimer, cancer) = 7 ; décision qui touche le portefeuille de millions de foyers = 7.
- 6 : insolite sympa, info locale forte, lancement notable grand public. OU un chiffre national qui parle au quotidien : inflation, chômage, prix de l'énergie, salaires, pouvoir d'achat = 6 ; étude sur la vie quotidienne avec un chiffre concret et parlant (sommeil, écrans, alimentation) = 6.
- 0-5 : le reste. EXEMPLES CALIBRÉS de scores BAS : "Apple ouvre les bundles d'abonnements entre éditeurs sur l'App Store" = 3 (annonce business B2B, tout le monde s'en fiche) ; partenariat entre entreprises = 3 ; mise à jour d'application = 2 ; "ce qui pourrait changer d'ici 2030" SANS chiffres ni décision actée = 3 ; revue de presse / "vu de l'étranger" / édito = 3 ; négociations européennes sur des quotas ou mécanismes = 3.

⛔ PLAFONDS STRICTS — ils s'appliquent UNIQUEMENT si l'article n'a NI engagement NI impact concret :
- Annonce produit/business/tech SANS conséquence directe pour le grand public (bundles, partenariats, API, résultats trimestriels, levées de fonds, fonctionnalités) → MAX 4. Test : si la réaction attendue en commentaire est "🥱" ET que personne n'a besoin de le savoir, c'est MAX 4.
- ⚠️ EXCEPTION : une DÉCISION POLITIQUE/RÉGLEMENTAIRE soudaine et radicale sur une techno grand public (interdiction, suspension, blocage, censure d'un service ou d'une IA connue type ChatGPT/Claude/TikTok) n'est PAS du B2B banal → score 7-8. C'est un coup de tonnerre qui fait réagir (ex : "les États-Unis interdisent tel modèle d'IA hors de leur territoire" = 7).
- FUTUR potentiel ou PROCESSUS technique ("pourrait", "envisage", "d'ici 20XX", négociations, quotas, consultations, projets de loi sans vote) → MAX 5. ⚠️ EXCEPTION : si le changement est ACTÉ (voté, publié, décrété) et touche DIRECTEMENT la vie, l'argent, la santé ou les droits du lecteur, note-le sur son IMPACT (6-8), même s'il s'applique plus tard.
- Angle ÉDITORIAL (revue de presse, "vu de l'étranger", tribune, portrait, décryptage d'un autre média) → MAX 5 : on veut le FAIT, pas le commentaire du fait.
- CONSEIL PRATIQUE ou MODE D'EMPLOI → MAX 3, ce n'est PAS de l'actualité : comment arroser son potager, entretenir sa voiture, économiser sur ses courses, bien dormir, les astuces de rangement, les gestes à adopter, les erreurs à éviter, les recettes, le bien-être, le jardinage, la déco. Aucun fait nouveau n'est annoncé : rien ne s'est produit. (Ex : "bien arroser son potager face aux étés plus chauds" = 2.)
- 🚫 CONDITION COMMUNE À TOUTES CES EXCEPTIONS : l'article doit LIVRER l'information (chiffres, montants, dates, conditions, décision précise). Un titre-appât qui promet sans donner ("ce qui va changer", "on vous dit tout", "voici pourquoi", "la raison est surprenante") reste MAX 4 : sans les faits, on ne peut pas en écrire un tweet honnête.

🌍 INTERNATIONAL — filtre SÉVÈRE : une actu étrangère ne parle aux Français que si elle est ÉNORME.
- Passent haut (7-9) : attentat, catastrophe naturelle meurtrière de grande ampleur (séisme, tempête, inondation avec un lourd bilan), guerre qui bascule, mort ou scandale d'une STAR mondiale, coup d'éclat d'une GRANDE MARQUE connue de tous (Apple, Netflix, Nintendo, Tesla, Amazon, Disney...).
- Restent BAS (MAX 4) : politique intérieure d'un pays étranger, fait divers local à l'étranger, entreprise étrangère inconnue du grand public, élection ou remaniement local, économie régionale.
- Test simple : « un Français en parlerait-il à un ami ? » Si non → MAX 4.

🚓 FAITS DIVERS — des drames, il y en a TOUS LES JOURS. Seuls les SPECTACULAIRES comptent.
- 7-9 : bilan lourd, circonstances hors du commun, traque nationale, victime ou auteur connu, affaire qui devient un sujet de société.
- MAX 4 : accident de la route ordinaire, incendie d'habitation ou de local sans ampleur, agression isolée, drame local sans retentissement — MÊME avec des blessés, MÊME avec un mort. Un décès ne suffit PAS à faire monter la note : ce qui compte, c'est l'AMPLEUR et le caractère exceptionnel. (Ex : "accident de car scolaire, 3 blessés légers dans le Cantal" = 3.)
- ⚠️ L'AMPLEUR VAUT AUTANT QUE LES VICTIMES : un sinistre de grande échelle vaut 7-8 MÊME SANS MORT — feu de forêt de plusieurs centaines ou milliers d'hectares, évacuations de riverains ou de campings, villages menacés, moyens aériens engagés (Canadair), autoroute ou ville coupée, inondation qui submerge une commune, tempête qui prive des dizaines de milliers de foyers d'électricité. (Ex : "un incendie ravage 3 000 hectares dans le Var, 500 personnes évacuées" = 8, même sans victime.)

🏛️ POLITIQUE LOCALE → MAX 3 : démission d'un maire, conseil municipal, élection ou polémique d'une petite commune (ex : "le maire d'une ville de 8 000 habitants démissionne" = 2). SAUF si l'affaire devient nationale (mise en examen, scandale repris partout).

🇫🇷 HIÉRARCHIE DE L'ENGAGEMENT en France :
1) Football (Bleus, Mbappé, PSG, OM, Coupe du Monde 2026, Ligue des champions)
2) Drames et faits divers majeurs (fusillade, incendie, disparition, procès médiatique)
3) Politique à CLASH (affaires, gardes à vue, démissions, punchlines — pas les textes techniques)
4) NBA/Wembanyama, Roland-Garros, Tour de France, F1, boxe/MMA
5) Influenceurs/people/télé (Squeezie, McFly et Carlito, Inoxtag, Hanouna...) et gaming (GTA, PlayStation, Nintendo) — une grosse actu ici vaut 7-8, autant que la politique chaude
6) Insolite viral (pannes nationales, bugs cocasses, records absurdes)
Un bon fil = un mix de tout ça. Une info locale peut scorer haut UNIQUEMENT si elle est spectaculaire ou hors du commun — un fait divers local banal reste en bas, même dramatique.

Catégories possibles (choisis la plus juste) :
breaking, france, monde, politique, economie, societe, faitsdivers, histoire,
culture (cinéma, musique, séries, célébrités, créateurs/influenceurs, YouTubeurs/streamers, jeux vidéo, gaming, esport, buzz réseaux sociaux, produits de célébrités),
sport, science, sante, environnement, tech, ia, insolite, positivity.

⚠️ CATÉGORIE "breaking" — TRÈS RESTRICTIVE : réservée aux FAITS urgents en direct (mort d'une personnalité, attentat, catastrophe naturelle, accident/crash grave, fusillade, résultat très attendu). Un rapport, une étude, une analyse, un sondage, un classement, une prévision ou un avis ne doit JAMAIS être catégorisé "breaking" — mets economie, politique, societe, etc. Le label rouge "URGENT" ne doit jamais apparaître sur ce type de contenu.
"""
    return _ANALYSE_SYS


def _normalise_analyse(a):
    """Ramène une analyse au format interne, que Claude ait répondu en clés COMPACTES
    (i/s/c/d/v — moins de tokens de sortie, facturés 5× l'entrée) ou en clés longues.
    🛡️ Tolérant : une réponse dans l'ancien format reste parfaitement comprise."""
    if not isinstance(a, dict):
        return {"score": 0, "category": "france", "is_duplicate": False, "needs_video": False}
    def _b(v):
        return v in (True, 1, "1", "true", "True", "oui")
    return {
        "score":        int(a.get("score", a.get("s", 0)) or 0),
        "category":     (a.get("category") or a.get("c") or "france"),
        "is_duplicate": _b(a.get("is_duplicate", a.get("d", False))),
        "needs_video":  _b(a.get("needs_video", a.get("v", False))),
    }


def analyse_batch(articles, recent, blocked_keywords):
    """Analyse plusieurs articles en un seul appel Claude."""
    if not articles:
        return []

    # 12 titres suffisent pour juger un doublon (les plus récents) — 20 gonflaient le prompt
    recent_str  = "\n".join(f"- {t}" for t in recent[:12]) or "Aucun"
    blocked_str = ", ".join(blocked_keywords) if blocked_keywords else "Aucun"
    today       = datetime.now().strftime("%d %B %Y")
    cats        = "|".join(LABELS.keys())

    articles_str = "\n\n".join(
        f"### Article {i+1}\nSource: {a['source']}\nTitre: {a['title']}\nRésumé: {a.get('summary','')[:110]}"
        for i, a in enumerate(articles)
    )

    prompt = f"""Aujourd'hui : {today}

Voici {len(articles)} articles à analyser.

Titres DÉJÀ PUBLIÉS récemment (ta référence pour juger les doublons) :
{recent_str}

Sujets déjà abordés récemment (mots-clés) : {blocked_str}

Articles :
{articles_str}

IMPORTANT : retourne EXACTEMENT {len(articles)} analyses dans le tableau."""

    result   = _llm_json(prompt, max_tokens=max(500, len(articles) * 60),
                         system=_analyse_system(), task="analyse")
    analyses = [_normalise_analyse(a) for a in result.get("analyses", [])]
    while len(analyses) < len(articles):
        analyses.append({"score": 0, "category": "france", "is_duplicate": False, "needs_video": False})
    return analyses[:len(articles)]

def _smart_truncate(s, max_len=80, add_ellipsis=False):
    """Coupe une chaîne sur un mot ENTIER, jamais en plein milieu d'un mot ou d'une phrase.
    Utilisé partout où un texte généré par Claude doit être borné pour l'affichage.
    add_ellipsis=True → ajoute « … » UNIQUEMENT si la chaîne a réellement été coupée."""
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:.-'\u2019")
    cut = cut if cut else s[:max_len].rstrip()
    return (cut + "…") if add_ellipsis else cut

def _recap_line(text, max_len=130):
    """Ligne de récap : on laisse passer la phrase ENTIÈRE (la carte fait le retour à la ligne).
    On ne coupe qu'en tout dernier recours si c'est vraiment démesuré, à une frontière de
    proposition (virgule/point) pour rester lisible, SANS « … » tant que possible."""
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    window = t[:max_len]
    cut = max(window.rfind(", "), window.rfind(". "), window.rfind("; "), window.rfind(" — "), window.rfind(" – "))
    if cut >= 45:                      # coupe nette à une virgule/point → lecture complète, pas de « … »
        return window[:cut].rstrip(" ,;:—–-").strip()
    return _smart_truncate(t, max_len, add_ellipsis=True)   # dernier recours (phrase sans ponctuation)

def _split_long_lead(body, max_lead=90):
    """Anti-pavé : si la 1ʳᵉ ligne (avant le 1er saut de ligne double) dépasse max_lead caractères,
    on la scinde à la 1ʳᵉ frontière de phrase (. ! ?) pour créer une accroche courte + un retour à
    la ligne. Ne touche à rien si l'accroche est déjà courte ou déjà suivie d'un saut de ligne tôt."""
    if not body:
        return body
    parts = body.split("\n\n", 1)
    lead = parts[0].strip()
    rest = parts[1] if len(parts) > 1 else ""
    if len(lead) <= max_lead:
        return body
    # cherche une fin de phrase dans les premiers max_lead caractères
    m = list(re.finditer(r"[.!?…](\s|$)", lead[:max_lead + 40]))
    # ⚠️ NE JAMAIS couper sur le point d'une INITIALE (« E. Jean Carroll », « J. K. Rowling »)
    # ni d'une abréviation courante (« M. Dupont », « etc. ») — sinon on scinde un nom en deux.
    _ABBR = ("m", "mme", "mlle", "dr", "pr", "st", "ste", "etc", "cf", "av", "apr", "jc", "min", "max")
    def _vraie_fin_de_phrase(pos):
        before = lead[:pos]
        if re.search(r"(^|\s)[A-ZÀ-Ÿ]$", before):          # initiale : 1 seule lettre majuscule
            return False
        w = re.search(r"([A-Za-zÀ-ÿ]+)$", before)           # abréviation en fin de mot
        if w and w.group(1).lower() in _ABBR:
            return False
        return True
    m = [x for x in m if _vraie_fin_de_phrase(x.start())]
    if m:
        cut = m[0].end()
        head = lead[:cut].strip()
        tail = lead[cut:].strip()
        new = head
        if tail:
            new += "\n\n" + tail
        if rest:
            new += "\n\n" + rest
        return new.strip()
    return body   # pas de frontière propre trouvée → on laisse tel quel

# ── GARDE-FOU HASHTAG (source unique) ────────────────────────────────────────
# Règle éditoriale : 1 hashtag minimum, qui doit être LE SUJET (nom propre) et se lire
# NATURELLEMENT dans la phrase — jamais un bloc raccroché en fin de tweet, jamais un mot
# générique (#Disparition, #Justice, #France...). On pose donc le « # » sur un mot DÉJÀ
# présent dans le texte. Le repli (ajout en fin) ne sert que si aucun candidat n'y figure.
_GENERIC_TAGS = {
    "actualite", "actualité", "info", "infos", "news", "breaking", "urgent", "alerte",
    "france", "justice", "bourse", "tech", "politique", "economie", "économie", "sport",
    "monde", "societe", "société", "disparition", "enquete", "enquête", "proces", "procès",
    "accident", "incendie", "meteo", "météo", "faitsdivers", "police", "sante", "santé",
}

def _hashtag_candidates(person, keywords):
    """Candidats ordonnés : nom de famille, puis prénom, puis mots-clés majeurs."""
    cands = []
    if person:
        parts = [p for p in str(person).split() if p]
        if parts:
            cands.append(parts[-1])          # le NOM de famille d'abord (#Mbappé, pas #Kylian)
            cands.extend(parts[:-1])
    cands.extend([str(k) for k in (keywords or [])])
    out, seen = [], set()
    for c in cands:
        w = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", c)
        if len(w) >= 3 and w.lower() not in seen:
            seen.add(w.lower()); out.append(w)
    return out

TRENDS_URL   = os.environ.get("TRENDS_URL", "https://getdaytrends.com/france/")
TRENDS_TTL   = 3600            # une heure : les tendances bougent lentement
_TRENDS_CACHE = {"t": 0.0, "v": []}

def _tendances_x():
    """Tendances X France, récupérées sur getdaytrends. Mises en cache 1 h.
    ⚠️ C'est du relevé de page web, pas une API contractuelle : ça peut cesser de
    fonctionner sans prévenir. Tout est donc en repli silencieux — sans tendances, les
    hashtags restent choisis sur les mots du tweet, ce qui marche déjà.
    ⛔ Les tendances ne servent JAMAIS à choisir un sujet : uniquement à départager
    des hashtags déjà pertinents pour l'article traité."""
    import time as _t
    if _TRENDS_CACHE["v"] and (_t.time() - _TRENDS_CACHE["t"]) < TRENDS_TTL:
        return _TRENDS_CACHE["v"]
    try:
        r = requests.get(TRENDS_URL, timeout=12, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PulseBot/1.0)",
            "Accept-Language": "fr-FR,fr;q=0.9"})
        r.raise_for_status()
        html = r.text
        vus, out = set(), []
        # ① liens de tendance : <a href="/france/trend/XXX/">…</a>
        for m in re.finditer(r'href="[^"]*/trend/[^"]*"[^>]*>\s*([^<]{2,60}?)\s*<', html, re.I):
            t = re.sub(r"\s+", " ", m.group(1)).strip()
            if t and t.lower() not in vus:
                vus.add(t.lower()); out.append(t)
        # ② repli : tous les hashtags visibles dans la page
        if len(out) < 5:
            for m in re.finditer(r"#([A-Za-zÀ-ÿ0-9_]{3,40})", html):
                t = "#" + m.group(1)
                if t.lower() not in vus:
                    vus.add(t.lower()); out.append(t)
        out = out[:50]
        if out:
            _TRENDS_CACHE.update({"t": _t.time(), "v": out})
            print(f"  📈 {len(out)} tendances X récupérées")
        return out
    except Exception as e:
        print(f"  ⚠️ Tendances X indisponibles ({str(e)[:60]}) → hashtags sur les mots du tweet")
        return []


def _norm_tag(mot):
    """Normalise un mot en hashtag : accents retirés, casse chameau, sans ponctuation."""
    t = unicodedata.normalize("NFD", str(mot or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    parties = [p for p in re.split(r"[^A-Za-z0-9]+", t) if p]
    if not parties:
        return ""
    return "".join(p[:1].upper() + p[1:] for p in parties)


_TAG_BANNIS = {
    "France", "Info", "Actu", "Actualite", "News", "Video", "Direct", "Urgent",
    "Aujourdhui", "Hier", "Demain", "Nouveau", "Nouvelle", "Selon", "Apres",
    "Pulse", "Twitter", "Breaking",
}

def _hashtags_pertinents(body, keywords=None, person=None, pays=None, maxi=3):
    """Choisit 1 à 3 hashtags à partir des MOTS DU TWEET (personne citée, mots-clés,
    noms propres). Les tendances X servent seulement à PRIORISER parmi ces candidats.
    ⛔ Jamais un hashtag hors-sujet, même s'il est massivement en tendance : le hashtag
    doit décrire le tweet, pas courir après l'audience."""
    txt = str(body or "")
    cands = []
    for src in ([person] if person else []) + list(keywords or []):
        t = _norm_tag(src)
        if t and len(t) >= 3:
            cands.append(t)
    # noms propres du corps (hors début de phrase et hors mots tout en majuscules du style Pulse)
    for m in re.finditer(r"(?<![.!?\n]\s)(?<!^)\b([A-ZÀ-Þ][a-zà-ÿ]{3,})\b", txt):
        t = _norm_tag(m.group(1))
        if t:
            cands.append(t)
    # dédoublonnage en gardant l'ordre
    vus, propres = set(), []
    for t in cands:
        k = t.lower()
        if k in vus or t in _TAG_BANNIS or len(t) < 3:
            continue
        if f"#{k}" in txt.lower():          # déjà présent dans le tweet
            continue
        vus.add(k); propres.append(t)
    if not propres:
        return []
    # priorisation par les tendances : un candidat qui EST en tendance passe devant
    tend = {_norm_tag(t.lstrip("#")).lower() for t in _tendances_x()}
    if tend:
        en_tendance = [t for t in propres if t.lower() in tend]
        autres = [t for t in propres if t.lower() not in tend]
        if en_tendance:
            print(f"  📈 Hashtag(s) en tendance : {', '.join('#' + t for t in en_tendance[:maxi])}")
        propres = en_tendance + autres
    return [f"#{t}" for t in propres[:maxi]]


_ACCENTS = {"a": "àâä", "e": "éèêë", "i": "îï", "o": "ôö", "u": "ùûü", "c": "ç"}

def _poser_hashtags(body, tags, maxi=3):
    """Intègre les hashtags DANS LA PHRASE, en préfixant d'un dièse un mot DÉJÀ PRÉSENT.
    ⚠️ Aucun mot n'est ajouté, aucune tournure modifiée : « en Gironde » devient
    « en #Gironde ». C'est la seule façon d'intégrer un hashtag sans risquer de casser
    la formulation.
    Un candidat absent du texte est ignoré. Si AUCUN ne peut être intégré, on en pose
    au plus deux en fin de corps, avant la source."""
    if not tags:
        return body
    txt = str(body or "")
    # la source finale « (Le Monde) » est une zone protégée : jamais de dièse dedans
    msrc = re.search(r"\n*\([^()]{1,60}\)\s*$", txt)
    core, tail = (txt[:msrc.start()], txt[msrc.start():]) if msrc else (txt, "")
    poses, restants = 0, []
    for tag in tags:
        if poses >= maxi:
            restants.append(tag); continue
        mot = tag.lstrip("#")
        # tolérant aux accents : on cherche le mot tel qu'il apparaît réellement
        motif = "".join(
            f"[{c}{c.upper()}{_ACCENTS.get(c, '')}]" if c.isalpha() else re.escape(c)
            for c in mot.lower())
        m = re.search(rf"(?<![#\w'’])({motif})(?!\w)", core)
        if m and m.start() > 0:              # jamais sur le tout premier mot du tweet
            core = core[:m.start()] + "#" + m.group(1) + core[m.end():]
            poses += 1
        else:
            restants.append(tag)
    if poses == 0 and restants:              # aucun intégrable → repli en fin de corps
        core = core.rstrip() + " " + " ".join(restants[:2])
        if tail and not tail.startswith("\n"):
            tail = "\n\n" + tail.lstrip()
    return core + tail


def _attach_hashtag(body, person, keywords):
    """Garantit UN hashtag, intégré dans la phrase si possible. Ne touche à rien s'il y en a déjà.
    Désactivable par HASHTAGS=0 : les tweets de référence de Pulse n'en portent aucun."""
    if not HASHTAGS_ACTIFS:
        return body
    if not body or "#" in body:
        return body
    cands = _hashtag_candidates(person, keywords)
    if not cands:
        return body
    # la source finale « (Le Monde) » est une zone protégée : on n'y pose jamais de #
    msrc = re.search(r"\n*\([^()]{1,40}\)\s*$", body)
    core, tail = (body[:msrc.start()], body[msrc.start():]) if msrc else (body, "")

    # 1) INTÉGRATION : le premier candidat réellement présent dans le texte reçoit le #
    for w in cands:
        m = re.search(rf"(?<![#\w]){re.escape(w)}(?!\w)", core, re.I)
        if m:
            found = core[m.start():m.end()]                      # garde la casse d'origine
            tagged = "#" + found[0].upper() + found[1:]
            return core[:m.start()] + tagged + core[m.end():] + tail

    # 2) REPLI : aucun candidat dans le texte → on ajoute en fin, en évitant les mots génériques
    pick = next((w for w in cands if w.lower() not in _GENERIC_TAGS), cands[0])
    tag = "#" + pick[0].upper() + pick[1:]
    core = core.rstrip() + " " + tag
    return core + ("\n\n" + tail.lstrip("\n") if tail else "")

_HOOK_INSTR = (
            "\n🎣 L'ACCROCHE (1ʳᵉ phrase) — LA PLUS IMPORTANTE, elle décide si les gens s'arrêtent ou scrollent :\n"
            "La 1ʳᵉ phrase doit AGRIPPER en 2 secondes (comme un bon sondage qui donne envie de voter) :\n"
            "- ⚡ COURTE ET PERCUTANTE : l'accroche fait UNE seule phrase BRÈVE (idéalement 8-15 mots, JAMAIS plus de ~120 caractères). Une accroche qui tient sur 1 ligne à l'écran, pas un pavé. Si ton accroche dépasse une ligne, COUPE : garde le choc, mets le reste dans la 2ᵉ phrase.\n"
            "- COMMENCE PAR CE QUI CHOQUE, SURPREND OU INTRIGUE : le chiffre le plus fort, le mot le plus marquant, le détail le plus inattendu, EN TÊTE de phrase (pas à la fin).\n"
            "- CRÉE UNE ÉMOTION immédiate (indignation, stupeur, admiration, curiosité). Demande-toi : \"en lisant juste cette phrase, aurait-on envie de commenter ou de lire la suite ?\"\n"
            "- CONCRET ET IMAGÉ, jamais abstrait : \"un homme de 92 ans\" plutôt que \"une personne âgée\" ; \"18 MILLIONS d'euros\" plutôt que \"une grosse somme\".\n"
            "- 💥 CHIFFRE-CHOC : si l'article contient un chiffre FORT et surprenant (montant, pourcentage, nombre, record, comparaison), METS-LE EN VEDETTE dès la 1ʳᵉ phrase — c'est ce qui fait le plus réagir/commenter. Écris-le en toutes lettres marquantes (\"3 200 €/mois\", \"+47 % en un an\", \"1 Français sur 4\"). Donne-lui du relief (ce qu'il représente concrètement) SANS jamais l'arrondir à la hausse ni le sortir de son contexte réel. Pas de chiffre → n'en invente pas, garde une autre accroche.\n"
            "- 🎬 LE FAIT EST L'ACCROCHE : n'ajoute PAS une phrase d'ambiance avant l'info (\"La ville retient son souffle.\") — c'est du remplissage. Un fait précis et net accroche mieux qu'une mise en scène. Évite le jargon de journaliste (\"embargo levé\", \"selon nos sources\") : parle comme au grand public.\n"
            "- 💬 CITATIONS-CHOC : si l'article contient de VRAIES citations fortes et courtes (critiques dithyrambiques, phrase-choc d'un témoin ou d'une personnalité), reprends-en 1 ou 2 entre guillemets — c'est très engageant. UNIQUEMENT des citations réellement présentes dans la source, JAMAIS inventées ni reformulées en plus fort. Si l'article n'en contient pas, n'en invente aucune.\n"
            "- Sujets clivants (politique, société, sécurité) : formule le FAIT pour que chacun ait aussitôt un avis — SANS prendre parti ni déformer l'info.\n"
            "- ⛔ JAMAIS AU PRIX DE LA VÉRITÉ : accroche fondée sur un fait RÉEL de la source. Aucune exagération, aucun mot plus fort que la source, aucun teaser trompeur, aucune question racoleuse creuse. Elle rend le vrai fait saillant, elle ne l'invente ni ne l'amplifie."
        )

_TWEET_SYS = None

def _tweet_system(sober=False):
    """Consignes FIXES de rédaction (format de sortie, règles de style, rigueur factuelle).
    💰 Identiques à chaque appel → envoyées en bloc `system` mis en cache, facturées ~10× moins
    cher en lecture. Le style par catégorie et la recette d'accroche restent dans le message
    (ils varient), ainsi que l'article à traiter."""
    global _TWEET_SYS
    key = "sobre" if sober else "standard"
    if _TWEET_SYS is None:
        _TWEET_SYS = {}
    if key not in _TWEET_SYS:
        _TWEET_SYS[key] = f"""Tu es community manager de Pulse, compte Twitter d'actualité française.


Génère QUATRE choses :

1. **headline_court** (max 75 caractères) : titre punchy pour l'image. Pas de hashtag, pas d'emoji.

2. **image_query** (max 5 mots, EN ANGLAIS) : recherche pour trouver une image pertinente.
   Ex: "Emmanuel Macron Elysee speech", "Paris metro station", "Iran flag Tehran protest"

3. **keywords_majeurs** (3 mots-clés en minuscules) : les mots-clés CENTRAUX du sujet, pour anti-répétition.
   Ex pour "Trump impose tarifs Chine" → ["trump", "tarifs", "chine"]
   Ex pour "Incendie 15e arrondissement Paris" → ["incendie", "paris", "15e"]
   Ex pour "Mbappé blessé entraînement" → ["mbappe", "blessure", "real"]

4. **person** : si l'article parle d'UNE personnalité publique précise (politique, sportif, artiste, créateur de contenu, PDG...), donne son nom complet tel qu'il apparaîtrait sur Wikipédia (ex: "Emmanuel Macron", "Kylian Mbappé", "Squeezie"). Sinon mets "".

5. **pays** : le code ISO à 2 lettres du pays PRINCIPALEMENT concerné par l'actu (ex: "FR" France, "ES" Espagne, "US" États-Unis, "UA" Ukraine, "IT" Italie, "DE" Allemagne, "GB" Royaume-Uni, "CH" Suisse). Si l'actu est franco-française → "FR". Si aucun pays précis (sujet mondial, techno générale...) → "".

6. **body** : corps du tweet (sans préfixe — il sera ajouté automatiquement).

🔎 COMPRÉHENSIBLE PAR TOUS (RÈGLE D'OR) : le tweet doit être limpide pour quelqu'un qui n'a JAMAIS suivi le sujet. Tout SIGLE, ORGANISME, INSTITUTION ou terme technique que le grand public ne connaît pas forcément doit être expliqué en 2-4 mots juste après, entre parenthèses.
   Ex : « le FSB (les services secrets russes) » · « la CJUE (la justice de l'UE) » · « le CETA (l'accord commercial UE-Canada) » · « la CNIL (le gendarme des données personnelles) » · « l'AME (l'aide médicale pour étrangers) » · « le HCR (l'agence de l'ONU pour les réfugiés) ».
   Les sigles ULTRA-connus n'ont PAS besoin d'explication (ONU, OTAN, UE, SNCF, PSG, SMIC, RSA, RATP, OMS). Dans le doute, EXPLIQUE : mieux vaut un lecteur qui comprend qu'un lecteur qui décroche.


📐 FORMAT DE RÉFÉRENCE — imite ce style, c'est la ligne éditoriale de Pulse :

« Une vingtaine de climatiseurs commandés par l'État pour l'hôpital d'Avesnes-sur-Helpe sont inutilisables : équipés de fiches italiennes, ils ne peuvent pas être branchés sur les prises françaises. (La Voix du Nord) »

« Les prix des légumes ont bondi de 10% en 1 an et ont plus que doublé en 10 ans. (Familles Rurales) »

« 35% des Français ne laissent pas de pourboire au restaurant, faute de monnaie. (TF1 Info) »

« L'Assemblée nationale APPROUVE l'instauration de la PERPÉTUITÉ pour les auteurs de viols en série commis sur des mineurs de moins de 15 ans. »

« MrBeast s'est marié aujourd'hui. (TMZ) »

« L'actrice américaine Kaylee Hottle, révélée par la franchise Godzilla, est décédée dans un accident de voiture à l'âge de 19 ans. (TMZ) »

Sur un sujet À PLUSIEURS ÉLÉMENTS, une phrase d'ouverture puis une liste aérée :

« L'interdiction des réseaux sociaux aux moins de 15 ans est une PREMIÈRE EN EUROPE.

La vérification de l'âge pourra se faire :
- En insérant sa pièce d'identité
- En se connectant via FranceConnect
- Ou bien grâce à une reconnaissance faciale à partir d'un selfie.

Elle DÉBUTERA :
➡️ le 1er septembre 2026 pour les nouveaux comptes ;
➡️ le 1er janvier 2027 pour les comptes existants. »

CE QUE CES EXEMPLES T'IMPOSENT :
- 🎯 UNE SEULE PHRASE quand le fait y tient. Ne découpe PAS artificiellement en « accroche » puis « détail » : si tout tient en une phrase claire, écris UNE phrase et arrête-toi. La concision EST le style.
- 🔠 1 à 2 mots en MAJUSCULES sur le mot DÉCISIF (APPROUVE, PERPÉTUITÉ, PREMIÈRE EN EUROPE). Jamais plus.
- 🔢 Les CHIFFRES sont mis en avant tels quels (10%, 35%, 4,5M$, 19 ans) — ils portent l'info.
- 📋 Liste UNIQUEMENT si le sujet a vraiment plusieurs éléments distincts : tirets « - » pour une énumération, « ➡️ » pour des échéances ou étapes. Sinon, pas de liste.
- 🚫 AUCUN commentaire, aucune interprétation, aucune question au lecteur, aucun appel à réagir. Le fait, rien que le fait.
- 📰 La source entre parenthèses à la toute fin : (TMZ), (TF1 Info), (La Voix du Nord).

✍️ MISE EN FORME PERCUTANTE (style CerfiaFR) :
- 🔠 MAJUSCULES DE PUNCH : mets 1 à 2 mots-clés FORTS en MAJUSCULES pour créer du relief (ex: "largement REJETÉE", "VIOLENT cambriolage", "RECORD battu"). Maximum 1-2 par tweet, sur le mot qui compte — jamais des phrases entières en majuscules, ça crie.
- 🔸 PUCES pour les actus DENSES : si l'article contient BEAUCOUP de données chiffrées (étude, bilan, rapport avec plusieurs statistiques), structure le corps avec des puces "🔸" (une donnée par puce) pour aérer et rendre lisible. Sinon (actu simple), garde le format phrases + sauts de ligne classique. N'utilise les puces QUE quand il y a vraiment plusieurs chiffres/faits à lister.
- 🔢 CHIFFRES PRÉCIS jamais arrondis mous : "132 députés", "82,4 %", "289 voix nécessaires" — reprends les chiffres EXACTS de l'article, c'est ce qui inspire confiance.

🧠 ADAPTE LE STYLE AU TYPE DE NEWS (c'est ce qui fait un bon compte, pas un moule unique) :
Regarde de quoi parle l'actu et choisis l'angle qui la sert le mieux — base télégraphique façon CerfiaFR, mais modulée :
- 🚨 BREAKING / FAIT DIVERS GRAVE → flash direct, factuel, tendu. Les faits bruts d'abord. Emoji d'alerte (🚨) possible en tête.
- ⚖️ JUDICIAIRE / POLITIQUE SENSIBLE → sobre et précis, zéro sensationnalisme, qualifications exactes (voir rigueur).
- 💰 ÉCONOMIE / SOCIÉTÉ AVEC CHIFFRE → mets le chiffre-choc en avant et réponds à \"pourquoi ça me concerne ?\" (pouvoir d'achat, emploi, factures, impôts) — rends-le concret pour les gens.
- 🔬 SCIENCE / ÉTUDE / RAPPORT → vulgarise : le RÉSULTAT marquant en tête, dis pourquoi c'est important, garde SEULEMENT les 2-3 chiffres les plus parlants (le reste alourdit), zéro jargon.
- ⚽ SPORT → vivant, punchy, l'exploit ou le résultat en avant, emoji du sport (⚽🏀🎾).
- 🎬 CULTURE / INSOLITE / POSITIF → ton plus léger, curiosité ou sourire, on peut jouer sur la surprise.
- 🌍 INTERNATIONAL → clair et pédagogue en une phrase, on situe l'enjeu sans jargon.
Le BON réflexe : demande-toi \"si je voyais passer ça dans mon fil, qu'est-ce qui me ferait m'arrêter ?\" et écris ÇA.

😀 EMOJI DU SUJET (comme dans les sondages) : ajoute UN emoji qui capte le SUJET PRÉCIS ou un élément-clé de l'actu — dans le texte OU à la fin. Choisis-le selon le SENS réel, jamais un décor gratuit.
   Exemples : ⚽ foot/match · 🏀🎾🏉 autres sports · ⚖️ justice/procès/tribunal · 🚔 police/enquête · 🔥 incendie · 🌊 inondation · 🌪️ tempête · 🌡️ canicule · ✈️ aviation · 🚄 SNCF/train · 🚗 route/accident · 💶 budget/dette/prix/euros · 📈 hausse · 📉 baisse · 🗳️ élection/vote · 🏛️ politique/gouvernement · 🏥 santé/hôpital · 💊 médicament · 🎬 cinéma · 🎵 musique · 🚀 espace/tech · 💻 numérique · 🐕 animal · 🌍 international.
   - Sois MALIN sur les mots ambigus : « feu vert » (un accord) n'est PAS 🔥 ; une « vague » de chaleur n'est PAS 🌊. L'emoji suit le vrai sens, pas le mot.
   - ⚠️ Si AUCUN emoji ne colle VRAIMENT au sujet, n'en mets AUCUN. Mieux vaut zéro emoji qu'un emoji générique ou hors-sujet.
   - Tu peux ajouter un 2ᵉ emoji en tête d'accroche s'il renforce (🚨 alerte/breaking). Jamais de guirlande d'emojis, JAMAIS d'emoji sur un hommage ni qui banalise un sujet grave.

⚖️ RIGUEUR FACTUELLE ABSOLUE (sujets judiciaires, décès, accusations) — PRIORITÉ N°1 :
- Recopie les qualifications juridiques EXACTEMENT comme dans la source : "homicide involontaire" reste INVOLONTAIRE, jamais "meurtre" ni "volontaire". "Meurtre" = uniquement si la source écrit "meurtre". Idem pour assassinat, viol, agression, terrorisme, féminicide.
- Si la qualification n'est pas écrite dans la source, n'en mets AUCUNE (écris "mort de", "décès de", "mis en cause pour").
- Personne mise en cause/suspectée = TOUJOURS "soupçonné de", "présumé" (présomption d'innocence).
- N'invente JAMAIS un chiffre, un âge, un lieu ou une circonstance absents de la source.
- ⛔ SUPERLATIFS INTERDITS SANS SOURCE : n'écris JAMAIS « le/la plus [grand·important...] de l'histoire », « jamais vu », « record absolu », « sans précédent », « inédit », « historique » SAUF si la source le dit EXPLICITEMENT. Sinon reste factuel (« un défilé de 6 700 soldats », PAS « le plus imposant jamais organisé »).
- ⛔ NE DÉFORME PAS LE SENS : n'attribue JAMAIS un fait, une origine ou un mérite à la mauvaise culture / personne / pays / groupe. Ex : un haka est une tradition MAORI / du Pacifique — ne le présente JAMAIS comme une « tradition française ». Reste fidèle à QUI fait quoi et à quelle culture/pays appartient quoi.
- ⛔ N'INVENTE JAMAIS LE CONTEXTE D'UN ÉVÉNEMENT SPORTIF OU PROGRAMMÉ : le tour de compétition (quart, demi, finale), l'adversaire, le stade, la date ou l'horaire ne s'écrivent QUE s'ils figurent EXPLICITEMENT dans la source. Si la source n'en parle pas, n'en parle pas — un match, un procès ou une élection dont tu ne connais pas la date ne s'annonce pas.
- ⛔ NE TRANSFORME PAS UN BILAN EN ANNONCE : si la source parle d'un événement DÉJÀ joué, terminé ou d'une élimination (« a échoué », « défaite », « éliminé », « bilan », « retour sur »), écris-le au passé. N'écris JAMAIS « avant le match », « à quelques heures de », « ce soir » pour un événement déjà passé.

RÈGLES STRICTES pour body — FIL D'ACTU COURT (façon CerfiaFR) :
- NE COMMENCE PAS par le libellé de catégorie fourni ci-dessous ni aucune catégorie en majuscules ; va DIRECTEMENT à l'info.
- TÉLÉGRAPHIQUE : 1 à 2 phrases MAXIMUM, denses et autonomes, comme une dépêche. Info COMPLÈTE, jamais un teaser.
- ⛔⛔ INTERDICTION ABSOLUE DU TEASER / RACOLAGE : le tweet DONNE l'information, il ne l'appâte JAMAIS. Bannis totalement : "découvrez si...", "découvrez la suite", "on vous dit tout", "vous n'allez pas croire", "la réponse va vous surprendre", "cliquez pour savoir", "la raison est folle". Si Marine Le Pen peut se présenter → DIS-LE ("elle pourra se présenter" ou "elle est inéligible"). Ne demande jamais au lecteur d'aller chercher l'info ailleurs : elle est DANS le tweet, en clair. Un tweet qui cache le fait pour forcer le clic est un ÉCHEC.
- 🎙️ NE RELAIE JAMAIS LA PUB D'UN AUTRE MÉDIA : beaucoup d'articles (BFMTV, etc.) servent à promouvoir LEUR podcast, émission, dossier ou reportage. IGNORE totalement cette promo. Ne finis JAMAIS par "on en parle dans le podcast", "à écouter dans notre émission", "à retrouver dans notre dossier", "rendez-vous dans…". Ne pose pas non plus de questions creuses qui renvoient à ce contenu ("Pourquoi ce choix ? On en parle dans…"). Extrais UNIQUEMENT le fait d'actualité (le quoi/qui/quand) et donne-le en clair. Si l'article n'a qu'une promo sans réel fait, garde juste le fait vérifiable et rien d'autre.
- Mets en avant le CHIFFRE ou le FAIT clé. Tu peux écrire UN mot ou chiffre important en MAJUSCULES pour l'emphase (avec parcimonie).
- ⛔ INTERDIT : les pavés, les paragraphes "conséquence/enjeu", les ouvertures "Et si...", "Saviez-vous que...", le remplissage.
- Longueur cible : 80 à 200 caractères (source comprise). Les meilleurs tweets de Pulse font ~135 caractères. 250 est un MAXIMUM absolu, réservé aux sujets à plusieurs éléments. Un tweet court et net vaut TOUJOURS mieux qu'un tweet complet et long.
- 🇫🇷 FRANÇAIS IMPECCABLE : aucun mot ni expression en anglais (traduis tout), aucune faute d'orthographe/grammaire/accord, aucun mot tronqué. RELIS-toi avant de répondre.
- 1 à 2 hashtags INTÉGRÉS DANS LES PHRASES (3 max si vraiment justifié) : colle "#" sur un mot DÉJÀ présent.
- 🎯 CHOIX DU HASHTAG — vise le SUJET, jamais le décor. Le hashtag principal = LE nom propre central de l'actu (entreprise, personne, club, événement, jeu vidéo). Test : "cette actu parle de quoi en UN mot ?" → c'est CE mot qui prend le #. Ex : actu sur l'entrée en Bourse de SpaceX → #SpaceX (PAS #Bourse ni #TimesSquare) ; actu sur Mbappé → #Mbappé (pas #football) ; match des Bleus → #CoupeDuMonde2026 ; sortie de GTA 6 → #GTA6.
- ⛔ Pas de hashtag décoratif ou périphérique : lieux secondaires, mots génériques (#Bourse, #France, #Justice, #Tech) sont INTERDITS sauf s'ils sont précisément LE sujet de l'actu.
- ⛔ INTÉGRATION PROPRE — ne casse JAMAIS le texte : ne DUPLIQUE pas un mot ("à Mexico #Mexico" = INTERDIT), ne mets pas de "#" au milieu d'un mot, n'ajoute pas de mot juste pour caser un hashtag, et NE mets PAS de bloc de hashtags à la fin. Le hashtag doit se lire naturellement dans la phrase.
- LONGUEUR — LA CONCISION D'ABORD : si le fait tient en UNE phrase claire, écris UNE phrase puis la source, et arrête-toi. C'est le format le plus fréquent (voir FORMAT DE RÉFÉRENCE). N'ajoute JAMAIS une deuxième phrase pour meubler.
- Si — et seulement si — le sujet l'exige vraiment (contexte indispensable, plusieurs éléments distincts), développe : accroche COURTE puis LIGNE VIDE, puis le détail, puis LIGNE VIDE, puis la source. Structure : Phrase1 courte.\\n\\nPhrase2.\\n\\n(Source). ⛔ Dans ce cas, JAMAIS deux longues phrases avant le 1er saut de ligne — l'accroche tient sur UNE ligne à l'écran.
- Exemple d'un rendu DÉVELOPPÉ (à réserver aux sujets qui le justifient) :
  "🚨 Des MILLIERS de manifestants bloquent le stade à #Mexico.\\n\\nÀ deux jours de l'ouverture de la #CoupeDuMonde2026, ils réclament une hausse des salaires et l'abrogation de la réforme des retraites.\\n\\n(Le Figaro)"
- Dans le JSON, les sauts de ligne s'écrivent \\n

✅ AVANT DE RÉPONDRE, relis-toi en silence et corrige si besoin : (1) l'info principale est visible dès la 1ʳᵉ phrase, (2) l'accroche donne envie SANS teaser, (3) faits, chiffres et qualifications 100 % fidèles à la source, (4) la structure colle au type d'actu, (5) un emoji qui colle au sujet précis (ou AUCUN si rien ne colle vraiment), (6) le hashtag = LE sujet, (7) français impeccable. Ne renvoie que la version corrigée.

Réponds avec ce JSON UNIQUEMENT :
{{"headline_court":"...","image_query":"...","person":"...","keywords_majeurs":["..","..",".."], "body":"..."}}""" + ("" if sober else _HOOK_INSTR)
    return _TWEET_SYS[key]


_SUITE_MARQUEURS = re.compile(
    r"\b(?:toujours|encore|désormais|dorénavant|depuis|après (?:plus de )?\d|"
    r"nouveau|nouvelle|nouvel|finalement|dernier bilan|bilan (?:s'alourdit|grimpe|monte|passe)|"
    r"s'alourdit|grimpe à|monte à|passe à|se poursuit|se poursuivent|poursuit|"
    r"rebondissement|revirement|en cours|ce (?:matin|midi|soir|mardi|mercredi|jeudi|"
    r"vendredi|samedi|dimanche|lundi)|cette nuit|à ce stade|pour l'instant|"
    r"vient d'être|viennent d'être|a finalement|ont finalement)\b",
    re.IGNORECASE)

def _manque_marqueur_suite(texte):
    """Vrai si un tweet de SUITE est écrit comme une découverte, sans aucun signe que
    l'histoire est déjà connue de nos abonnés.
    Republier « Un incendie ravage le Var » alors qu'on l'a déjà annoncé donne
    l'impression d'un compte qui se répète — c'est précisément ce qu'on veut éviter."""
    return not _SUITE_MARQUEURS.search(str(texte or ""))


# 📏 Le plafond dépend du FORMAT : un fait direct doit tenir en deux lignes, un sujet à
#    plusieurs éléments a besoin de sa liste. Repères tirés des tweets de référence :
#    formats direct/chiffre → 38 à 214 caractères (médiane 135) ;
#    formats liste/échéances → 385 à 417 caractères, mais en LIGNES COURTES.
TWEET_LONG_MAX = 230      # plafond des formats DIRECT et CHIFFRE
TWEET_LONG_CIBLE = 170    # cible du resserrage local
TWEET_STRUCT_MAX = 460    # plafond des formats LISTE et ÉCHÉANCES
TWEET_LIGNE_MAX = 110     # aucune LIGNE ne doit être à rallonge, quel que soit le format

def _trop_long(body, structure=False):
    """Vrai si le tweet dépasse la longueur admise pour SON format, source exclue.
    `structure=True` pour les formats liste/échéances, qui ont droit à plus de place —
    mais jamais à des phrases à rallonge (voir _ligne_a_rallonge)."""
    t = re.sub(r"\s*\([^)]{2,40}\)\s*$", "", str(body or "").strip())
    return len(t) > (TWEET_STRUCT_MAX if structure else TWEET_LONG_MAX)


def _ligne_a_rallonge(body):
    """Vrai si une LIGNE du tweet est trop longue. C'est le vrai défaut à traquer dans un
    tweet structuré : une puce doit tenir sur une ligne à l'écran, pas contenir une phrase
    entière. Un tweet peut être long s'il est fait de lignes courtes."""
    for l in str(body or "").splitlines():
        l = l.strip()
        if l.startswith("(") and l.endswith(")"):
            continue                                   # ligne de source
        if len(l) > TWEET_LIGNE_MAX:
            return True
    return False


def _resserre(body, structure=False):
    """Raccourcit un tweet trop long SANS appel payant : on retire les phrases de la fin
    en gardant la source. La première phrase porte le fait, c'est elle qu'on protège.
    ⚠️ Ne touche à RIEN si le tweet est déjà dans la cible — sinon on reformaterait
    inutilement des tweets corrects (et on écraserait leur mise en page)."""
    txt = str(body or "").strip()
    if not _trop_long(txt, structure):
        return txt
    m = re.search(r"(\s*\([^)]{2,40}\)\s*)$", txt)
    source = m.group(1).strip() if m else ""
    corps = txt[:m.start()].strip() if m else txt
    _cible = (TWEET_STRUCT_MAX - 40) if structure else TWEET_LONG_CIBLE
    blocs = [b.strip() for b in corps.split("\n") if b.strip()]
    while blocs and len(" ".join(blocs)) > _cible and len(blocs) > 1:
        blocs.pop()                                  # on sacrifie le dernier bloc
    corps = "\n\n".join(blocs)
    if len(corps) > (TWEET_STRUCT_MAX if structure else TWEET_LONG_MAX):
        # une seule phrase, mais trop longue : on coupe à la dernière ponctuation utile,
        # et à défaut au dernier espace — jamais au milieu d'un mot.
        coupe = max(corps.rfind(". ", 0, _cible), corps.rfind(" ; ", 0, _cible))
        if coupe > 60:
            corps = corps[:coupe + 1].strip()
        else:
            coupe = corps.rfind(" ", 0, _cible)
            if coupe > 60:
                corps = corps[:coupe].rstrip(" ,;:-–—") + "."
    return (corps + ("\n\n" + source if source else "")).strip()


# Données chiffrées : avec unité (10%, 890 M€) OU nombre nu suivi d'un nom
# (4 minutes, 12 rencontres). Les années sont exclues : elles comptent comme des DATES.
_FMT_CHIFFRE_RX = re.compile(
    r"\d[\d  ]*(?:,\d+)?\s?(?:%|€|\$|Md|M€|M\$|milliards?|millions?|milliers?|"
    r"euros?|dollars?)"
    r"|(?<!\d)\d[\d  ]*(?:,\d+)?\s+[a-zà-ÿ]{3,}", re.IGNORECASE)
_FMT_DATE_RX = re.compile(
    r"\b(?:1er|\d{1,2})\s(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)(?:\s\d{4})?\b|\b(?:20|19)\d{2}\b",
    re.IGNORECASE)
_FMT_ENUM_RX = re.compile(
    r"\b(?:d'une part|d'autre part|premièrement|deuxièmement|troisièmement|"
    r"par ailleurs|en outre|également prévu|trois|quatre|cinq|plusieurs mesures|"
    r"les mesures|au programme|notamment|suivants?|comprend|prévoit|examinés?|"
    r"porte sur|il s'agit de)\b",
    re.IGNORECASE)

# Consigne de composition par format. Elle s'ajoute au style commun et dit au rédacteur
# COMMENT bâtir CE tweet-là — au lieu de le laisser appliquer le même moule partout.
_FORMATS = {
    "direct": (
        "🧭 FORMAT IMPOSÉ POUR CE TWEET : **DIRECT**.\n"
        "Le sujet tient en un fait. Écris UNE SEULE phrase qui le donne entièrement, puis la source. "
        "Rien d'autre : pas de contexte, pas de conséquence, pas de deuxième phrase. "
        "Modèle : « MrBeast s'est marié aujourd'hui. (TMZ) »"),
    "chiffre": (
        "🧭 FORMAT IMPOSÉ POUR CE TWEET : **CHIFFRE EN TÊTE**.\n"
        "L'article porte une donnée forte. Construis le tweet AUTOUR d'elle : le chiffre apparaît "
        "dans les premiers mots, écrit tel quel (10%, 890 M€, 2 500 hectares). "
        "Une à deux phrases courtes maximum, puis la source. Si un second chiffre permet une "
        "comparaison parlante (évolution, avant/après), ajoute-le — sinon arrête-toi.\n"
        "Modèle : « Les prix des légumes ont bondi de 10% en 1 an et ont plus que doublé en 10 ans. (Familles Rurales) »"),
    "liste": (
        "🧭 FORMAT IMPOSÉ POUR CE TWEET : **LISTE**.\n"
        "Le sujet comporte PLUSIEURS éléments distincts. Structure ainsi :\n"
        "1) une phrase d'ouverture COURTE qui pose le fait principal ;\n"
        "2) une ligne vide ;\n"
        "3) 2 à 4 puces « – », UNE IDÉE PAR PUCE, chacune sur une ligne, TRÈS COURTES "
        "(pas de phrase à rallonge : un élément, un chiffre, un fait) ;\n"
        "   🎯 ORDRE DES PUCES : commence par celle qui se rattache DIRECTEMENT au fait "
        "annoncé en ouverture. Si le tweet ouvre sur un pompier décédé, la puce sur les "
        "pompiers blessés vient EN PREMIER — pas en dernier. Les autres suivent par "
        "ordre d'importance décroissante ;\n"
        "4) la source à la fin.\n"
        "⛔ N'invente aucun élément pour remplir la liste : s'il n'y a que deux éléments réels, "
        "fais deux puces."),
    "echeances": (
        "🧭 FORMAT IMPOSÉ POUR CE TWEET : **ÉCHÉANCES**.\n"
        "Le sujet comporte des dates ou des étapes. Structure ainsi :\n"
        "1) une phrase d'ouverture COURTE qui pose la décision ;\n"
        "2) une ligne vide ;\n"
        "3) les échéances avec « ➡️ », une par ligne, format « ➡️ le [date] : [ce qui change] », "
        "TRÈS COURTES ;\n"
        "4) la source à la fin.\n"
        "⛔ Aucune date inventée : uniquement celles de l'article."),
}


def choisir_format_tweet(title, summary, article_text=""):
    """Choisit le FORMAT de composition d'un tweet à partir de la matière de l'article.
    Mécanique et gratuit : on compte ce que l'article contient réellement.
    Renvoie (nom_du_format, consigne). L'idée : un compte d'actualité n'écrit pas
    « MrBeast s'est marié » comme il écrit une réforme à trois échéances."""
    src = " ".join(str(x or "") for x in (title, summary, article_text))[:2500]
    chiffres = len({m.group(0).lower().replace(" ", "") for m in _FMT_CHIFFRE_RX.finditer(src)})
    dates    = len({m.group(0).lower() for m in _FMT_DATE_RX.finditer(src)})
    enum     = bool(_FMT_ENUM_RX.search(src))
    # ordre du plus structurant au plus simple
    if dates >= 2 and (chiffres >= 1 or enum):
        nom = "echeances"
    elif enum and chiffres >= 2:
        nom = "liste"
    elif chiffres >= 3:
        nom = "liste"
    elif chiffres >= 1:
        nom = "chiffre"
    else:
        nom = "direct"
    return nom, _FORMATS[nom]


def _normalise_source(body):
    """Place la source « (BFMTV) » sur SA PROPRE LIGNE, après une ligne vide.
    Le modèle le fait la plupart du temps mais pas toujours : la mise en forme d'un
    compte d'actualité doit être constante, pas dépendre de l'humeur du rédacteur."""
    txt = re.sub(r"[ \t]+\n", "\n", str(body or "").strip())
    m = re.search(r"\(\s*([^()]{2,40}?)\s*\)\s*$", txt)
    if not m:
        return txt
    source = f"({m.group(1).strip()})"
    corps = txt[:m.start()].rstrip(" \n\t—-–;,")
    return f"{corps}\n\n{source}" if corps else source


def gen_tweet_complet(title, summary, source, category, video_url=None, article_text=None, prev_angles=None, correction=None, angle_neuf=None, dossier=None):
    """Génère tweet + titre image + image_query + mots-clés majeurs.
    prev_angles = titres déjà publiés par Pulse sur CE sujet (suite = nouvel angle obligatoire)."""
    today = datetime.now().strftime("%d %B %Y")
    label = LABELS[category]
    video_str = ""
    art_str  = f"\n- EXTRAIT DE L'ARTICLE (fait foi sur les faits et qualifications) : {article_text[:1200]}" if article_text else ""
    corr_str = f"\n\n🚨 CORRECTION OBLIGATOIRE — ta version précédente contenait une ERREUR FACTUELLE : {correction}. Corrige-la impérativement." if correction else ""

    # 🔁 SUITE D'UN SUJET DÉJÀ TRAITÉ : on montre à Claude ce que Pulse a DÉJÀ publié
    # sur cette histoire aujourd'hui pour qu'il apporte un NOUVEL angle (jamais une redite).
    prev_str = ""
    if prev_angles:
        prev_list = "\n".join(f"  • {h}" for h in prev_angles[:3] if h)
        if prev_list:
            prev_str = (
                "\n\n🔁 SUJET DÉJÀ COUVERT PAR PULSE AUJOURD'HUI — angle(s) déjà publié(s) :\n" + prev_list +
                "\nCe tweet est une SUITE : nos abonnés CONNAISSENT déjà cette histoire.\n"
                "- ⛔ NE PRÉSENTE PAS le sujet comme une découverte. Écrire « Un incendie ravage le Var » "
                "alors qu'on l'a déjà annoncé fait passer Pulse pour un compte qui se répète.\n"
                "- ✅ ÉCRIS EN CONTINUITÉ, avec un marqueur de suivi dès les premiers mots : "
                "« toujours en cours », « désormais », « après X heures », « le bilan grimpe à », "
                "« nouveau rebondissement », « finalement », « ce mardi soir ». Le lecteur doit "
                "comprendre en une seconde que l'histoire AVANCE.\n"
                "- Mets en avant l'ÉLÉMENT NOUVEAU (réaction, recours/appel, verdict, nouveau bilan, décision officielle, rebondissement).\n"
                "- Rappelle le contexte en QUELQUES MOTS seulement (quelqu'un qui découvre le sujet ici doit comprendre).\n"
                "- Écris une accroche DIFFÉRENTE : ne réutilise ni la même formulation ni le même angle que ci-dessus."
            )
            if angle_neuf:
                # 🆕 L'élément nouveau a DÉJÀ été identifié en amont : on le donne au rédacteur
                #    pour qu'il construise le tweet autour, au lieu de le chercher lui-même.
                prev_str += (f"\n\n🆕 CE QUI EST NOUVEAU (identifié à l'analyse) : {angle_neuf}\n"
                             "C'est le CŒUR de ce tweet : commence par ça. Le reste n'est que contexte.")

    # Style adaptatif selon catégorie — TOUJOURS court et télégraphique (fil d'actu)
    if category == "hommage":
        style_instr = """STYLE HOMMAGE (décès d'une personne) :
- Ton SOBRE, respectueux et factuel — aucun sensationnalisme, aucune formule accrocheuse
- 1 à 2 phrases : qui était la personne, ce qui l'a rendue connue
- Pas de mot en MAJUSCULES pour l'emphase, pas de point d'exclamation
- ⚠️ JAMAIS DE DATE DE DÉCÈS ni de jour ("le 7 mai", "décédée mercredi", "ce lundi", "hier"...).
  Ne mentionne AUCUNE date précise : les articles mélangent souvent la date d'hospitalisation
  et celle du décès, ce qui conduit à des erreurs. Indique UNIQUEMENT l'âge s'il est connu
  ("à 75 ans"). Ne déduis, ne devine, n'infère jamais une date de décès."""
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


    # 🧭 FORMAT DE COMPOSITION choisi d'après la matière de l'article : un fait simple
    #    ne s'écrit pas comme une réforme à plusieurs échéances. Gratuit et déterministe.
    #    Les hommages gardent leur ton sobre : jamais de liste ni de chiffre mis en scène.
    dossier_str = _dossier_en_texte(dossier)
    if category == "hommage":
        _fmt_nom, format_instr = "direct", _FORMATS["direct"]
    else:
        _fmt_nom, format_instr = choisir_format_tweet(title, summary, article_text)
    result = _llm_json(f"""Aujourd'hui : {today}.

Catégorie de ce tweet (libellé à NE PAS reprendre en tête du body) : {label}

Article à traiter :
- Source : {source}
- Titre  : {title}
- Résumé : {summary}{video_str}{art_str}{corr_str}{prev_str}{dossier_str}

{style_instr}
{format_instr}""", max_tokens=900, system=_tweet_system(category == "hommage"), task="redaction")

    body = (result.get("body") or "").strip()
    for label_test in LABELS.values():
        body = re.sub(rf"^{label_test}\s*\|\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(rf"^{label_test}\s*[—-]\s*", "", body, flags=re.IGNORECASE)
    # (On NE retire PLUS l'emoji de tête : il fait désormais partie de l'accroche voulue.)

    # 🩹 GARDE-FOU ANTI-PAVÉ : si la 1ʳᵉ phrase (avant le 1er saut de ligne) est trop longue,
    # on coupe à la première frontière de phrase pour aérer et éviter le bloc lourd.
    body = _split_long_lead(body)

    headline_court = _smart_truncate(result.get("headline_court", title), 80)
    image_query    = result.get("image_query", category).strip()
    keywords       = result.get("keywords_majeurs", [])
    person         = (result.get("person") or "").strip()
    pays           = (result.get("pays") or "").strip()

    # 🏷️ GARDE-FOU HASHTAG : un tweet ne doit jamais partir SANS hashtag (sauf hommage, qui reste sobre).
    if category != "hommage":
        body = _attach_hashtag(body, person, keywords)
    else:
        # 🕊️ GARDE-FOU HOMMAGE : on RETIRE toute date de décès résiduelle (le prompt l'interdit déjà,
        # mais un modèle peut se tromper). On ne garde JAMAIS une date — seul l'âge est autorisé —
        # car les articles confondent souvent date d'hospitalisation et date du décès.
        body = _strip_death_date(body)

    return body, headline_court, image_query, keywords, person, pays

def _strip_death_date(text):
    """Supprime les dates/jours de décès d'un texte d'hommage, en gardant l'âge et le sens.
    Enlève : 'le 7 mai', 'le 7 mai 2026', 'ce mercredi', 'mercredi', 'hier/hier soir/cette nuit'
    quand ils qualifient le décès. Ne touche PAS à l'âge ('à 75 ans') ni aux années de carrière."""
    if not text:
        return text
    mois = r"janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
    jours = r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
    t = text
    # "est décédée le 7 mai (2026)" / "morte le 3 juin" → "est décédée" / "morte"
    t = re.sub(rf"\s+(le\s+)?\d{{1,2}}(?:er)?\s+(?:{mois})(?:\s+\d{{4}})?", "", t, flags=re.I)
    # "décédée ce mercredi" / "morte mercredi soir" / "s'est éteinte hier soir" → retire le repère de jour
    t = re.sub(rf"\s+(ce\s+|ce\s+soir\s+|cette\s+nuit\s+)?(?:{jours})(\s+(?:soir|matin|après-midi))?", "", t, flags=re.I)
    t = re.sub(r"\s+(hier(?:\s+soir|\s+matin)?|cette\s+nuit|ce\s+matin|ce\s+soir|aujourd'hui|la\s+nuit\s+dernière)", "", t, flags=re.I)
    # nettoyage d'espaces/ponctuation laissés par la suppression
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    t = re.sub(r"\(\s*\)", "", t)          # parenthèses vidées
    t = re.sub(r",\s*,", ",", t)
    return t.strip()

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

TEASER_RX = re.compile(
    r"\bdécouvrez\b|"                       # à l'impératif, c'est TOUJOURS un appât à clic
    r"découvrir\s+(si|la suite|pourquoi|comment|qui|ce qui|tout)|"
    r"on vous (dit|explique|donne|livre)\s+(tout|comment|pourquoi|les)|"
    r"vous n'allez pas (le )?croire|"
    r"(voici|les)\s+(les\s+)?(règles d'or|conseils|astuces|secrets|clés|bons gestes|bonnes pratiques)|"
    r"\b(nos|mes|leurs)\s+(conseils|astuces|recettes|solutions)\b|"
    r"voici comment\b|comment (bien|faire|s'y prendre|éviter|choisir|entretenir)\b|"
    r"\b(le|notre)\s+guide\b|mode d'emploi\b|"
    r"tout ce qu'il faut savoir|ce qu'il faut retenir avant|"
    r"la (réponse|raison|vérité|suite)\s+(va|risque de|pourrait)\s+vous|"
    r"cliquez\s+(ici|pour)|"
    r"(voici\s+)?pourquoi\s*\.\.\.|"
    r"la raison est (folle|dingue|incroyable)|"
    r"\ba(\s+)?la\s+fin\b.*surprend|"
    r"vous ne devinerez jamais|"
    r"attendez de (voir|savoir)|"
    r"la suite est|"
    r"réponse (ci-dessous|dans l'article)|"
    # 🎙️ Renvoi vers le contenu d'un autre média (teaser + pub) : « on en parle dans le podcast »…
    r"on (en|vous en|t'en) parle dans|"
    r"dans (le|la|notre|ce|le nouveau|notre nouveau|leur|un nouvel?) (podcast|[ée]mission|reportage|dossier|d[ée]cryptage|num[ée]ro|[ée]pisode)|"
    r"[àa] r[ée][ée]couter|[àa] (r[ée])?[ée]couter (dans|sur|notre|le)|"
    r"[àa] retrouver (dans|sur)|[àa] (re)?voir dans (le|notre|ce)|[àa] lire dans (le|notre)|"
    r"rendez-vous (dans|sur) (le|notre|ce|l')|"
    r"on (d[ée]crypte|d[ée]taille|raconte|explique|revient) .{0,25} dans (le|notre|ce|l')",
    re.IGNORECASE)

def _is_teaser(text):
    """Détecte une formulation racoleuse qui CACHE l'info au lieu de la donner (clickbait)."""
    return bool(TEASER_RX.search(text or ""))

_HEURE_RX = re.compile(r"\b(?:à|dès|vers)\s+(\d{1,2})\s*h(?:\s*(\d{2}))?\b", re.IGNORECASE)
_FUTUR_RX = re.compile(
    r"\b(commence|commencera|débute|debute|débutera|coup d'envoi|donne le coup d'envoi|"
    r"affrontera|affronteront|va affronter|vont affronter|aura lieu|auront lieu|"
    r"est attendu|sont attendus|est prévu|sont prévus|se tiendra|se déroulera|"
    r"rendez-vous|à suivre|s'élance|démarre|démarrera|entre en lice|ouvre ses portes|"
    r"prendra la parole|s'exprimera|doit (commencer|débuter|avoir lieu|s'exprimer))\b",
    re.IGNORECASE)
_JOURS_FR_SEM = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

_ANTICIP_RX = re.compile(
    r"\bavant (?:le|la|les|son|sa|leur|ce|cette) (?:match|rencontre|quart|demi|finale|huitième|"
    r"barrage|élection|scrutin|cérémonie|procès|audience|verdict|conférence|discours|sommet|"
    r"coup d'envoi|début)|"
    r"\b[àa] (?:quelques|moins de) (?:heures|minutes|jours) (?:de|du|d')|"
    r"\b[àa] la veille (?:de|du|d')|"
    r"\bce (?:soir|midi|matin)\b|\bcette nuit\b|\bcet après-midi\b|"
    r"\bprochain match\b|\bva affronter\b|\baffrontera\b|\bva disputer\b|\bdisputera\b|"
    r"\bse prépare [àa] affronter\b|\bdans (?:quelques|moins de) (?:heures|jours)\b",
    re.IGNORECASE)

def _annonce_perimee(text, now=None, pub_ts=None, stale_h=18):
    """Vrai si le tweet annonce comme À VENIR quelque chose qui ne l'est plus.
    Deux cas :
      a) un HORAIRE explicite déjà écoulé aujourd'hui (« commence à 21h » publié à 22h16) ;
      b) une ANTICIPATION d'événement (« avant le quart de finale », « ce soir ») alors que
         l'ARTICLE SOURCE date de plus de stale_h heures — l'événement a donc déjà eu lieu.
    Ne se déclenche pas si l'événement est situé un autre jour, ni sans tournure de futur."""
    t = str(text or "")
    if not t:
        return False
    now = now or datetime.now()
    bas = t.lower()
    autre_jour = bool(re.search(
        r"\b(demain|après-demain|apres-demain|prochaine? (semaine|mois|année)|semaine prochaine|"
        r"le \d{1,2}\s*(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
        r"septembre|octobre|novembre|décembre|decembre))\b", bas))
    autres = [j for i, j in enumerate(_JOURS_FR_SEM) if i != now.weekday()]
    if any(re.search(r"\b" + j + r"\b", bas) for j in autres):
        autre_jour = True

    # (b) article ancien + tournure d'anticipation → l'événement est forcément passé
    if pub_ts and not autre_jour:
        try:
            if (now.timestamp() - float(pub_ts)) / 3600 > stale_h and _ANTICIP_RX.search(t):
                return True
        except (TypeError, ValueError):
            pass

    # (a) horaire explicite déjà écoulé aujourd'hui
    if not _FUTUR_RX.search(t) or autre_jour:
        return False
    for h, m in _HEURE_RX.findall(t):
        try:
            hh, mm = int(h), int(m or 0)
        except ValueError:
            continue
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            continue
        prevu = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        # marge de 20 min : un événement qui vient de démarrer reste annonçable
        if (now - prevu).total_seconds() > 20 * 60:
            return True
    return False

def gen_tweet_verified(title, summary, source, category, url=None, prev_angles=None, pub_ts=None, angle_neuf=None, dossier=None):
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
    body, headline, image_query, keywords, person, pays = gen_tweet_complet(
        title, summary, source, category, article_text=article_text, prev_angles=prev_angles,
        angle_neuf=angle_neuf, dossier=dossier)
    # 💰 UNE SEULE régénération payante par tweet. Les trois garde-fous (fait, teaser,
    #    annonce périmée) pouvaient s'enchaîner : jusqu'à 4 appels facturés pour UN tweet.
    #    Au-delà du quota, on applique la correction LOCALE (gratuite) déjà prévue.
    _regen = [1]
    def _can_regen():
        if _regen[0] <= 0:
            print("  💰 Quota de régénération atteint → correction locale (0 coût)")
            return False
        _regen[0] -= 1
        return True
    issues = _fact_guard(body + " " + headline, src_text)
    if issues and _can_regen():
        print(f"  ⚖️ Erreur factuelle détectée ({'; '.join(issues)}) → régénération")
        body, headline, image_query, keywords, person, pays = gen_tweet_complet(
            title, summary, source, category, article_text=article_text, prev_angles=prev_angles,
            angle_neuf=angle_neuf, dossier=dossier, correction="; ".join(issues))
        if _fact_guard(body + " " + headline, src_text):
            body, headline = _fact_hardfix(body, src_text), _fact_hardfix(headline, src_text)
            print("  ⚖️ Correction forcée appliquée")
    elif issues:
        # quota épuisé : correction locale gratuite, l'exigence factuelle reste tenue
        body, headline = _fact_hardfix(body, src_text), _fact_hardfix(headline, src_text)
        print("  ⚖️ Correction forcée appliquée (sans régénération)")

    # 🚫 GARDE-FOU ANTI-TEASER : un tweet qui CACHE l'info ("découvrez si...", "on vous dit tout")
    # est un échec éditorial. On régénère UNE fois avec une consigne explicite de donner le fait.
    if _is_teaser(body) and _can_regen():
        print(f"  🚫 Teaser/clickbait détecté → régénération (l'info doit être DONNÉE, pas appâtée)")
        anti = ("Ton tweet précédent CACHAIT l'information (formulation racoleuse type 'découvrez si...', "
                "'on vous dit tout') OU relayait la promo d'un autre média ('on en parle dans le podcast', "
                "'à écouter dans notre émission', questions creuses renvoyant à ce contenu). INTERDIT. "
                "DONNE le FAIT d'actualité en clair, directement, dans le tweet — sans aucune promo de podcast/"
                "émission/dossier, sans question creuse. Le lecteur doit connaître le fait en te lisant, sans cliquer ailleurs.")
        body, headline, image_query, keywords, person, pays = gen_tweet_complet(
            title, summary, source, category, article_text=article_text, prev_angles=prev_angles,
            angle_neuf=angle_neuf, dossier=dossier, correction=anti)
    # ✂️ TROP BAVARD — le plafond dépend du format retenu pour ce sujet.
    _fmt = "direct" if category == "hommage" else choisir_format_tweet(title, summary, article_text)[0]
    _struct = _fmt in ("liste", "echeances")
    if (_trop_long(body, _struct) or _ligne_a_rallonge(body)) and _can_regen():
        if _ligne_a_rallonge(body):
            print("  ✂️ Ligne à rallonge détectée → régénération en lignes courtes")
            anti = ("Une de tes lignes est BEAUCOUP trop longue. Chaque ligne doit tenir à l'écran "
                    "sur mobile (100 caractères maximum). Découpe : une idée par ligne, "
                    "des puces COURTES, pas de phrase à rallonge.")
        else:
            print(f"  ✂️ Tweet trop long ({len(body)} car.) → régénération plus concise")
            anti = (f"Ton tweet fait {len(body)} caractères : trop long pour Pulse. "
                    f"Réécris-le en gardant UNIQUEMENT l'essentiel, en lignes COURTES. "
                    f"Supprime le contexte, les conséquences et tout commentaire.")
        body, headline, image_query, keywords, person, pays = gen_tweet_complet(
            title, summary, source, category, article_text=article_text, prev_angles=prev_angles,
            angle_neuf=angle_neuf, dossier=dossier, correction=anti)
    # resserrage LOCAL (gratuit) : s'applique aussi quand le quota est épuisé
    if _trop_long(body, _struct):
        avant = len(body)
        body = _resserre(body, _struct)
        print(f"  ✂️ Tweet resserré localement ({avant} → {len(body)} car.)")

    # 🔁 SUITE écrite comme une découverte : nos abonnés connaissent déjà l'histoire.
    if prev_angles and _manque_marqueur_suite(body) and _can_regen():
        print("  🔁 Suite écrite comme une nouveauté → régénération en mode continuité")
        anti = ("Ce sujet a DÉJÀ été publié par Pulse : ton tweet le présente pourtant comme "
                "une découverte, ce qui donne l'impression d'un compte qui se répète. "
                "Réécris-le en CONTINUITÉ, avec un marqueur de suivi dès les premiers mots "
                "(« toujours en cours », « le bilan grimpe à », « désormais », « après X heures », "
                "« nouveau rebondissement », « ce mardi soir »), et mets l'ÉLÉMENT NOUVEAU en avant.")
        body, headline, image_query, keywords, person, pays = gen_tweet_complet(
            title, summary, source, category, article_text=article_text, prev_angles=prev_angles,
            angle_neuf=angle_neuf, dossier=dossier, correction=anti)

    body = _normalise_source(body)   # source TOUJOURS isolée, sur sa propre ligne

    # Nettoyage LOCAL (gratuit) : s'applique aussi quand le quota de régénération est épuisé.
    if _is_teaser(body):
        body = re.sub(r"\bd[ée]couvr(ez|ir)\s+(si|la suite|pourquoi|comment|qui|ce qui|tout)\b",
                      "", body, flags=re.IGNORECASE).strip()
        print("  🚫 Tournure teaser retirée de force")

    # ⏰ Annonce périmée : le tweet présente comme À VENIR un horaire déjà passé aujourd'hui.
    if _annonce_perimee(body, pub_ts=pub_ts):
        if not _can_regen():
            # quota épuisé : on n'invente pas, on abandonne (0 coût) — jamais d'annonce périmée
            print("  ⛔ Annonce périmée et quota épuisé → sujet abandonné (0 coût)")
            return None, None, None, None, None, None
        print("  ⏰ Annonce périmée détectée (horaire déjà passé) → régénération")
        corr = (f"Il est actuellement {datetime.now().strftime('%Hh%M')}. Ton tweet précédent annonçait "
                "comme À VENIR un événement dont l'heure est DÉJÀ PASSÉE. INTERDIT. "
                "Si l'événement a commencé ou est terminé, écris-le au présent ou au passé "
                "(« a débuté », « est en cours », « s'est achevé ») et donne l'information réellement "
                "nouvelle. N'annonce JAMAIS un horaire déjà écoulé comme un rendez-vous à venir.")
        body2, headline2, iq2, kw2, p2, pays2 = gen_tweet_complet(
            title, summary, source, category, article_text=article_text,
            prev_angles=prev_angles, correction=corr)
        if body2 and not _annonce_perimee(body2, pub_ts=pub_ts):
            body, headline, image_query, keywords, person, pays = body2, headline2, iq2, kw2, p2, pays2
        else:
            print("  ⛔ Toujours périmé après régénération → sujet abandonné")
            return None, None, None, None, None, None
    return body, headline, image_query, keywords, person, pays

def _flag_emoji(country_code):
    """Convertit un code pays ISO ('FR', 'ES', 'US'...) en drapeau emoji. '' ou invalide → ''."""
    if not country_code:
        return ""
    cc = country_code.strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    # Les drapeaux emoji = 2 "Regional Indicator Symbols" (A=U+1F1E6)
    try:
        return chr(0x1F1E6 + ord(cc[0]) - ord('A')) + chr(0x1F1E6 + ord(cc[1]) - ord('A'))
    except Exception:
        return ""

def build_full_tweet(body, category, country=""):
    emoji = EMOJIS[category]
    label = LABELS[category]
    flag = _flag_emoji(country)
    # En-tête : "emoji [drapeau] LABEL | ..." — le drapeau situe le pays, le LABEL (catégorie) est conservé.
    # Emojis COLLÉS puis espace avant le libellé : « 🍅🇫🇷 FLASH | … » (format de référence)
    head = f"{emoji}{flag} {label} |" if flag else f"{emoji} {label} |"
    return f"{head} {body}"

# ═══════════════════════════════════════════════════════════════════════════
# IMAGES
# ═══════════════════════════════════════════════════════════════════════════
def search_unsplash(query, category):
    if UNSPLASH_KEY:
        try:
            url = f"https://api.unsplash.com/search/photos?query={urllib.parse.quote(query)}&per_page=1&client_id={UNSPLASH_KEY}"
            req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
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
    if not url:
        return None
    # En-têtes proches d'un vrai navigateur + Referer = beaucoup moins de 403/404 sur les sites de presse
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        referer = f"{parts.scheme}://{parts.netloc}/"
    except Exception:
        referer = url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Referer": referer,
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read()
    except Exception as e:
        print(f"  ⚠️ Fetch image: {e}")
        return None

def fetch_og_image(article_url):
    """Récupère l'image HD (og:image) depuis la page de l'article — bien meilleure que la miniature RSS."""
    if not article_url:
        return None
    try:
        req = urllib.request.Request(article_url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw_bytes = _read_capped(r, cap=1_500_000)
            enc = (r.headers.get("Content-Encoding") or "").lower()
        html = _decode_html_body(raw_bytes, enc)
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

def _extract_person_name(title, summary=""):
    """Extrait le nom probable d'une personne (Prénom Nom) mentionnée dans un titre/résumé.
    Heuristique gratuite : cherche 2-3 mots capitalisés consécutifs (gère les particules)."""
    txt = title + ". " + (summary or "")
    # Prénom Nom (+ éventuel 2e nom / particule), en évitant les débuts de phrase seuls
    pat = re.compile(r"\b([A-ZÉÈÀÂÎÔÛ][a-zà-ÿ'’-]+(?:\s+(?:de|du|van|von|le|la|el|al)\s+|\s+)"
                     r"[A-ZÉÈÀÂÎÔÛ][a-zà-ÿ'’-]+(?:\s+[A-ZÉÈÀÂÎÔÛ][a-zà-ÿ'’-]+)?)")
    STOP = {"Coupe", "Monde", "France", "Paris", "Ligue", "Cour", "État", "Union", "Assemblée",
            "Conseil", "Nord", "Sud", "Est", "Ouest", "Real", "Premier", "Ministre", "Président"}
    for m in pat.finditer(txt):
        cand = m.group(1).strip()
        first = cand.split()[0]
        if first in STOP:
            continue
        return cand
    return None

def verify_death_wikipedia(name):
    """Vérifie GRATUITEMENT sur Wikipedia FR si une personne est décédée.
    Retourne : 'dead' (décès confirmé, catégorie/date de mort présente),
               'alive' (page existe, AUCUN signe de décès),
               'unknown' (pas de page claire → on ne tranche pas).
    Zéro coût Claude. Appelé UNIQUEMENT sur l'article sélectionné avec mot de décès + nom."""
    if not name:
        return "unknown"
    try:
        # 1) Extrait de page + catégories (les personnes décédées ont une catégorie "Décès en AAAA")
        api = ("https://fr.wikipedia.org/w/api.php?action=query&prop=extracts|categories"
               "&exintro=1&explaintext=1&cllimit=50&format=json&redirects=1&titles="
               + urllib.parse.quote(name))
        req = urllib.request.Request(api, headers={"User-Agent": "PulseBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        pages = data.get("query", {}).get("pages", {})
        for pid, page in pages.items():
            if pid == "-1" or "missing" in page:
                return "unknown"          # pas de page Wikipedia → on ne peut pas trancher
            cats = " ".join(c.get("title", "").lower() for c in page.get("categories", []))
            extract = (page.get("extract", "") or "").lower()
            # Signes FORTS de décès : catégorie "décès en AAAA" ou "mort en AAAA"
            if re.search(r"décès en \d{4}|morts? en \d{4}|décès à", cats):
                return "dead"
            # Dans l'intro : "est un ... mort le" / "était un" (imparfait = souvent décédé)
            if re.search(r"\bmort[e]?\s+le\s+\d|\bdécédé[e]?\s+le\s+\d|"
                         r"\((?:\d{1,2}\s+\w+\s+)?\d{4}\s*[-–]\s*(?:\d{1,2}\s+\w+\s+)?\d{4}\)", extract):
                return "dead"
            # Page existe, personne vivante décrite au présent, pas de marqueur de mort
            if re.search(r"\best\s+un[e]?\b|\best\s+né[e]?\b", extract) and \
               not re.search(r"\bmort|décéd|disparu", extract):
                return "alive"
            return "unknown"
    except Exception as e:
        print(f"  ⚠️ Vérif décès Wikipedia: {e}")
    return "unknown"

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

def _visage_par_ia(pil_img):
    """Centre du visage principal, repéré par l'IA. Renvoie (cx, cy) en pixels, ou None.

    Réservé aux HOMMAGES : c'est le seul visuel où un mauvais cadrage se voit vraiment
    (portrait coupé, tête décentrée sur une carte de deuil). La détection classique par
    OpenCV rate les profils, les visages de trois quarts et les photos anciennes ;
    l'IA les voit. Ailleurs, OpenCV suffit et ne coûte rien.
    🛡️ En cas d'échec : None → OpenCV reprend la main."""
    if not GEMINI_API_KEY or pil_img is None:
        return None
    try:
        import base64 as _b64, io as _io
        buf = _io.BytesIO()
        pil_img.convert("RGB").save(buf, format="JPEG", quality=85)
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{GEMINI_MODEL}:generateContent")
        d = _post_gemini(
            url, famille="vision", timeout=45,
            payload={"contents": [{"parts": [
                      {"inlineData": {"mimeType": "image/jpeg",
                                      "data": _b64.b64encode(buf.getvalue()).decode()}},
                      {"text": "Où se trouve le CENTRE DU VISAGE de la personne principale "
                               "sur cette image ? Donne des coordonnées relatives entre 0 et 1 "
                               "(0,0 = coin haut gauche ; 1,1 = coin bas droit). Vise le milieu "
                               "du visage, entre les yeux et la bouche.\n"
                               'Réponds UNIQUEMENT : {"x": <0-1>, "y": <0-1>} '
                               'ou {"x": null, "y": null} si aucun visage humain.'}]}],
                  "generationConfig": {"maxOutputTokens": 60, "temperature": 0,
                                       "responseMimeType": "application/json",
                                       "thinkingConfig": {"thinkingBudget": 0}}})
        _usage_gemini(d)
        txt = (((d.get("candidates") or [{}])[0].get("content") or {})
               .get("parts") or [{}])[0].get("text", "")
        rep = _parse_json_reponse(txt)
        x, y = rep.get("x"), rep.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                and 0 <= x <= 1 and 0 <= y <= 1:
            w, h = pil_img.size
            print(f"  👤 Visage localisé par l'IA ({x:.2f}, {y:.2f})")
            return (x * w, y * h)
    except Exception as e:
        print(f"  ⚠️ Repérage du visage indisponible ({str(e)[:50]}) → détection classique")
    return None


def detect_face_center(pil_img, par_ia=False):
    """
    Retourne (cx, cy) du centre du plus grand visage détecté, ou None.
    `par_ia=True` (hommages) : on demande d'abord à l'IA, qui voit les profils et les
    photos anciennes que la détection classique rate. Sinon, OpenCV seul — instantané
    et gratuit, largement suffisant pour les cartes d'actualité.
    """
    if par_ia:
        c = _visage_par_ia(pil_img)
        if c:
            return c
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
    """Récupère les VRAIES photos d'un article (og:image, JSON-LD schema.org, image_src,
    + grandes <img> de la page), triées par priorité/taille. Filtre logos, icônes, pubs,
    pixels de tracking, SVG/GIF."""
    if not article_url:
        return []
    try:
        req = urllib.request.Request(article_url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            base = r.geturl()
            raw_bytes = _read_capped(r, cap=2_500_000)
            enc = (r.headers.get("Content-Encoding") or "").lower()
        html = _decode_html_body(raw_bytes, enc)
    except Exception:
        return []
    cands = []
    for prop in (r'og:image:secure_url', r'og:image:url', r'og:image',
                 r'twitter:image:src', r'twitter:image'):
        for m in re.finditer(r'<meta[^>]+(?:property|name)=["\']' + prop + r'["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
            cands.append((m.group(1).strip(), 10_000_000))
    # <link rel="image_src" href="..."> : ancienne convention encore utilisée par certains sites
    for m in re.finditer(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', html, re.I):
        cands.append((m.group(1).strip(), 9_000_000))
    # JSON-LD schema.org (NewsArticle/Article) : "image":"..." ou "image":["...","..."]
    # Très fiable sur les sites de presse modernes, et présent même si l'og:image manque.
    for block in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(block.group(1).strip())
        except Exception:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            img = node.get("image")
            if isinstance(img, str):
                cands.append((img, 8_000_000))
            elif isinstance(img, dict) and img.get("url"):
                cands.append((img["url"], 8_000_000))
            elif isinstance(img, list):
                for it in img:
                    if isinstance(it, str):
                        cands.append((it, 8_000_000))
                    elif isinstance(it, dict) and it.get("url"):
                        cands.append((it["url"], 8_000_000))
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
    Retourne (raw_bytes, is_real_photo). Journalise PRÉCISÉMENT l'étape qui échoue, pour pouvoir
    diagnostiquer un manque d'image sans deviner (page bloquée ? 0 image trouvée ? trop petites ?)."""
    HQ_W, HQ_H = 500, 320

    FLOOR_W, FLOOR_H = 380, 240
    best_raw, best_px = None, 0
    img_urls = fetch_article_images(article_url) if article_url else []
    if article_url and not img_urls:
        print(f"  🖼️ aucune image trouvée dans la page (og:image/JSON-LD/<img> absents ou page bloquée)")
    rejected_small = 0
    for img_url in img_urls:
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
            rejected_small += 1
            continue
        px = w * h
        if px > best_px:
            best_raw, best_px = raw, px
        if best_px >= 1280 * 720:
            break
    if best_raw:
        return best_raw, True
    if img_urls and rejected_small == len(img_urls):
        print(f"  🖼️ {len(img_urls)} image(s) trouvée(s) mais toutes trop petites (<{FLOOR_W}×{FLOOR_H})")
    elif img_urls:
        print(f"  🖼️ {len(img_urls)} image(s) trouvée(s) mais aucune n'a pu être téléchargée")

    # 2. Miniature RSS (souvent la photo de l'article) si assez grande
    if photo_url:
        raw = fetch_img(photo_url)
        if raw and img_dimensions_ok(raw, min_w=420, min_h=260):
            return raw, True
        elif raw:
            print(f"  🖼️ miniature RSS trop petite, écartée")
        else:
            print(f"  🖼️ miniature RSS injoignable")
    else:
        print(f"  🖼️ pas de miniature RSS fournie par le flux")

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

    print(f"  🖼️ Aucune vraie photo exploitable pour cet article")
    return None, False

def extract_video_url(entry):
    """Cherche une VRAIE vidéo téléchargeable (MP4 direct) dans le flux RSS de l'article.
    X n'accepte QUE des fichiers MP4 : on ignore volontairement les lecteurs intégrés
    (YouTube/Dailymotion) et le streaming HLS/m3u8, non ingérables. Retourne une URL .mp4
    ou None. Best-effort : la plupart des médias FR n'exposent pas de MP4 direct → None fréquent."""
    def _is_mp4(url, mime=""):
        if not url:
            return False
        if "video" in (mime or "").lower() and "mp4" in (mime or "").lower():
            return True
        # URL se terminant par .mp4 (éventuel ?query derrière)
        return bool(re.search(r"\.mp4(\?|$)", url, re.I))

    # media:content (souvent utilisé pour la vidéo par les CMS)
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            url, mime = m.get("url", ""), m.get("type", "")
            if m.get("medium") == "video" and _is_mp4(url, mime):
                return url
            if _is_mp4(url, mime):
                return url
    # enclosures (pièces jointes du flux)
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            url, mime = e.get("href", ""), e.get("type", "")
            if _is_mp4(url, mime):
                return url
    return None

def fetch_video_file(video_url, max_mb=50):
    """Télécharge un MP4 distant vers un fichier temporaire, avec plafond de taille
    (X accepte gros mais on reste raisonnable). Retourne le chemin local ou None.
    Vérifie que le contenu est bien une vidéo MP4 et pas une page HTML déguisée."""
    if not video_url:
        return None
    try:
        import tempfile
        req = urllib.request.Request(video_url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            ctype = r.headers.get("Content-Type", "").lower()
            clen = int(r.headers.get("Content-Length", "0") or 0)
            if clen and clen > max_mb * 1024 * 1024:
                print(f"  ⚠️ Vidéo actu trop lourde ({clen // (1024*1024)} Mo) → vidéo Pulse")
                return None
            if "html" in ctype:      # page web, pas un vrai fichier vidéo
                return None
            data = _read_capped(r, max_mb * 1024 * 1024 + 1024)
        # signature MP4 : les octets 4-8 contiennent 'ftyp'
        if len(data) < 12 or data[4:8] != b"ftyp":
            return None
        # 🛡️ Anti-fichier vide : seuil très bas, uniquement pour écarter un fichier tronqué.
        #    (Le POIDS n'est PAS un bon juge : une vraie vidéo de 5 s bien compressée fait 30 Ko.)
        if len(data) < 20 * 1024:
            print(f"  ⚠️ Vidéo d'actu tronquée ({len(data) // 1024} Ko) → ignorée, on garde la carte")
            return None
        fd, path = tempfile.mkstemp(suffix=".mp4", prefix="pulse_actu_")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # 🛡️ ...et trop courte : moins de 1,5 s, ce n'est pas une vidéo éditoriale.
        #    En cas de doute (analyse impossible), on GARDE la vidéo.
        try:
            import subprocess as _sp, imageio_ffmpeg as _iff
            _err = _sp.run([_iff.get_ffmpeg_exe(), "-i", path],
                           capture_output=True, text=True, timeout=20).stderr
            _m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", _err)
            if _m:
                _d = int(_m.group(1)) * 3600 + int(_m.group(2)) * 60 + float(_m.group(3))
                if _d < 1.5:
                    print(f"  ⚠️ Vidéo d'actu trop courte ({_d:.1f}s) → ignorée, on garde la carte")
                    os.remove(path)
                    return None
        except Exception:
            pass
        _sz = f"{len(data) / (1024*1024):.1f} Mo" if len(data) >= 1024 * 1024 else f"{len(data) // 1024} Ko"
        try:
            _dom = urllib.parse.urlparse(video_url).netloc.replace("www.", "")
        except Exception:
            _dom = "?"
        print(f"  🎥 Vidéo d'actu récupérée ({_sz}) chez {_dom} → attachée au tweet")
        return path
    except Exception as e:
        print(f"  ⚠️ Vidéo actu injoignable ({e}) → vidéo Pulse")
        return None

# 🎥 Catégories où une VIDÉO apporte une vraie valeur (grille éditoriale) : faits divers,
# catastrophes, météo, manifs, sport, déclarations, direct, politique. Inutile pour les
# chiffres/études/annonces administratives/infos purement textuelles → on n'y visite pas la page.
# 🎥 Catégories où l'on va chercher la vraie vidéo de l'article. On tente PARTOUT : quand un
# journal expose sa vidéo, c'est le meilleur visuel possible, et le coût est d'une seule requête
# par tweet publié. Seul l'HOMMAGE est exclu : il garde son traitement sobre (portrait fixe).
_VIDEO_WORTHY_CATS = {
    "faitsdivers", "sport", "monde", "politique", "environnement", "societe", "culture",
    "france", "breaking", "insolite", "tech", "ia", "sante", "science", "economie",
    "positivity", "histoire", "gta6",
}
# 🛡️ Diffuseurs TV les plus susceptibles de faire retirer une vidéo (DMCA) : on ne reposte
# JAMAIS leur vidéo, même si elle est accessible (protection du compte, déjà suspendu une fois).
# Les journaux régionaux et sites spécialisés (IGN, etc.) restent autorisés.
_RISKY_VIDEO_SOURCES = ("bfm", "tf1", "lci", "francetv", "france télé", "france tele",
                        "france 2", "france 3", "france 5", "france info", "franceinfo",
                        "m6", "cnews", "canal+", "rmc", "europe 1", "cstar", "tmc")
def _video_source_ok(source):
    """False pour les diffuseurs à risque DMCA : on n'utilise pas leur vidéo."""
    s = (source or "").lower()
    return not any(r in s for r in _RISKY_VIDEO_SOURCES)
def video_worth_searching(category):
    """Décide si chercher une vidéo pour cette catégorie vaut le coup (coût maîtrisé)."""
    return (category or "").lower() in _VIDEO_WORTHY_CATS

def extract_video_from_page(html, base_url=""):
    """🎥 Cherche la VRAIE vidéo ÉDITORIALE dans la page d'un article (og:video, JSON-LD
    VideoObject, puis <video> en dernier recours), en REJETANT les pubs.

    Principe : liste BLANCHE d'emplacements que seuls les CMS de presse utilisent pour la vidéo
    de l'article — pas une liste noire de régies (course perdue). og:video et JSON-LD sont
    structurellement éditoriaux (jamais de pub dans l'aperçu de partage). Le <video> brut n'est
    accepté qu'après filtrage anti-régie. Ne renvoie que des URLs .mp4 franches (X n'accepte pas
    le HLS/.m3u8). Retourne (url_mp4, meta_texte) ou (None, "")."""
    if not html:
        return None, ""

    # Domaines de régies publicitaires / players tiers → rejet immédiat
    AD_DOMAINS = ("doubleclick", "googlesyndication", "googleadservices", "imasdk", "2mdn",
                  "adsystem", "adservice", "teads", "outbrain", "taboola", "smartadserver",
                  "adnxs", "criteo", "moatads", "innovid", "spotx", "springserve", "adform",
                  "/ads/", "/ad/", "advert", "sponsor", "prebid", "ayads", "dailymotion.com/ad")

    def _ok_mp4(url):
        """URL plausible = .mp4 franc, pas une régie, pas du streaming HLS."""
        if not url:
            return False
        u = url.strip()
        if u.startswith("//"):
            u = "https:" + u
        low = u.lower()
        if not low.startswith("http"):
            return False
        if ".m3u8" in low or "manifest" in low:          # streaming : inattachable sur X
            return False
        if any(bad in low for bad in AD_DOMAINS):          # régie pub → rejeté
            return False
        return bool(re.search(r"\.mp4(\?|#|$)", low))

    def _abs(url):
        u = url.strip()
        if u.startswith("//"):
            return "https:" + u
        return u

    # ── 1) og:video — la vidéo OFFICIELLE de partage de l'article (jamais une pub) ──
    for prop in (r'og:video:secure_url', r'og:video:url', r'og:video',
                 r'twitter:player:stream'):
        m = re.search(r'<meta[^>]+property=["\']' + prop + r'["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']' + prop + r'["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+name=["\']' + prop + r'["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m and _ok_mp4(m.group(1)):
            # titre associé (pour la validation de cohérence en aval) — on respecte le guillemet
            # délimiteur pour ne pas tronquer sur une apostrophe française ("Trump promet d'intensifier").
            def _meta_content(html, propname):
                mm = re.search(r'<meta[^>]+property=["\']' + propname + r'["\'][^>]+content=("|\')(.*?)\1', html, re.I)
                return mm.group(2).strip() if mm else ""
            meta = _meta_content(html, "og:video:title") or _meta_content(html, "og:title")
            return _abs(m.group(1)), meta

    # ── 2) JSON-LD VideoObject — bloc structuré schema.org (éditorial par nature) ──
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        if '"VideoObject"' not in block and "'VideoObject'" not in block:
            continue
        for cu in re.findall(r'"contentUrl"\s*:\s*"([^"]+)"', block):
            if _ok_mp4(cu):
                nm = re.search(r'"name"\s*:\s*"([^"]+)"', block)
                return _abs(cu), (nm.group(1).strip() if nm else "")

    # ── 3) <video>/<source> en DERNIER RECOURS — filtré contre les blocs publicitaires ──
    for vm in re.finditer(r'<video\b([^>]*)>(.*?)</video>', html, re.I | re.S):
        attrs, inner = vm.group(1), vm.group(2)
        blob = (attrs + " " + inner[:200]).lower()
        # marqueurs de pub dans les attributs/classe/id → on saute ce lecteur
        if any(w in blob for w in ("ad", "advert", "sponsor", "preroll", "promo", "publicit")):
            # 'ad' seul est trop large ; on ne saute que sur des motifs francs
            if re.search(r'\b(ad|ads|advert|sponsor|preroll|promo|publicit)[-_a-z]*\b', blob):
                continue
        src = re.search(r'src=["\']([^"\']+)["\']', attrs, re.I)
        if not src:
            src = re.search(r'<source[^>]+src=["\']([^"\']+)["\']', inner, re.I)
        if src and _ok_mp4(src.group(1)):
            return _abs(src.group(1)), ""

    # ── 4) Attributs LAZY-LOAD (data-src, data-video-src…) — les CMS chargent souvent la
    #      vidéo en différé : l'URL MP4 est dans un attribut data-*, pas dans src. ──
    for m in re.finditer(r'data-(?:video-)?(?:src|url|mp4|file)=["\']([^"\']+\.mp4[^"\']*)["\']', html, re.I):
        if _ok_mp4(m.group(1)):
            return _abs(m.group(1)), ""

    # ── 5) SCRIPTS DE PLAYERS (Digiteka/Ultimedia, Brightcove, players maison) — la config
    #      JS du lecteur contient les URLs MP4 par qualité. On prend la meilleure (720/1080),
    #      toujours filtrée anti-régie. Gisement majeur de la presse française. ──
    _candidates = []
    for sc in re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.I | re.S):
        if ".mp4" not in sc:
            continue
        low_sc = sc[:400].lower()
        if any(bad in low_sc for bad in ("doubleclick", "adsystem", "teads", "prebid")):
            continue
        for u in re.findall(r'["\'](https?://[^"\']+?\.mp4[^"\']*)["\']', sc.replace("\\/", "/")):
            if _ok_mp4(u):
                _candidates.append(u)
    if _candidates:
        def _quality(u):
            lu = u.lower()
            for q, w in (("2160", 5), ("1080", 4), ("720", 3), ("_hd", 3), ("480", 2), ("360", 1)):
                if q in lu:
                    return w
            return 0
        _candidates.sort(key=lambda u: (_quality(u), len(u)), reverse=True)
        return _abs(_candidates[0]), ""

    return None, ""

def fetch_article_video(article_url, max_mb=50):
    """Best-effort : visite la page de l'article, y cherche une vidéo ÉDITORIALE (jamais pub),
    et la télécharge en MP4 local prêt pour X. Retourne (chemin_local, meta) ou (None, "").
    Robuste : toute erreur réseau/HTML → (None, "") → le bot garde sa vidéo Pulse."""
    if not article_url:
        return None, ""
    try:
        req = urllib.request.Request(article_url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = _read_capped(r, cap=1_800_000)
            enc = (r.headers.get("Content-Encoding") or "").lower()
        html = _decode_html_body(raw, enc)
    except Exception as e:
        print(f"  ⚠️ Page article illisible pour vidéo ({e})")
        return None, ""
    vurl, meta = extract_video_from_page(html, base_url=article_url)
    # 🔁 Repli AMP : la version AMP d'un article expose souvent le MP4 en clair
    #    (<amp-video src=…>) là où la page normale ne charge le player qu'en JS.
    #    Une seule requête de plus, uniquement si la page normale n'a rien donné.
    if not vurl:
        amp = re.search(r'<link[^>]+rel=["\']amphtml["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
        if not amp:
            amp = re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']amphtml["\']', html, re.I)
        if amp:
            try:
                req2 = urllib.request.Request(amp.group(1), headers=_BROWSER_HEADERS)
                with urllib.request.urlopen(req2, timeout=12) as r2:
                    raw2 = _read_capped(r2, cap=1_500_000)
                    enc2 = (r2.headers.get("Content-Encoding") or "").lower()
                html_amp = _decode_html_body(raw2, enc2)
                # <amp-video src=…> puis les mêmes gisements que la page normale
                m = re.search(r'<amp-video[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']', html_amp, re.I)
                if m:
                    vurl, meta = m.group(1), ""
                else:
                    vurl, meta = extract_video_from_page(html_amp, base_url=article_url)
                if vurl:
                    print("  🔁 Vidéo trouvée via la version AMP de l'article")
            except Exception:
                pass
    if not vurl:
        return None, ""
    path = fetch_video_file(vurl, max_mb=max_mb)   # vérifie signature MP4 + plafond taille
    if path:
        print(f"  🎥 Vidéo éditoriale trouvée dans l'article → attachée")
        return path, meta
    return None, ""

def extract_photo(entry):
    """Cherche une image DANS le flux RSS lui-même (gratuit, aucune requête web,
    donc jamais bloqué par un anti-bot). Couvre toutes les formes courantes utilisées
    par les CMS de presse français : media:content, media:thumbnail, enclosure,
    champ image direct, et <img> intégré dans le résumé/contenu HTML du flux."""
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("type", "").startswith("image") or m.get("medium") == "image":
                return m.get("url")
            if m.get("url"):   # certains flux omettent le type mais donnent quand même une image
                return m.get("url")
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            if "image" in e.get("type", "") or re.search(r"\.(jpe?g|png|webp)(\?|$)", e.get("href", ""), re.I):
                return e.get("href")
    if hasattr(entry, "image") and entry.get("image"):
        url = entry.image.get("href") if hasattr(entry.image, "get") else None
        if url:
            return url
    # <img> intégré dans le contenu/résumé HTML du flux (fréquent quand le flux inclut le corps)
    for field in ("content", "summary"):
        raw = entry.get(field)
        if field == "content" and isinstance(raw, list) and raw:
            raw = raw[0].get("value", "")
        if isinstance(raw, str) and "<img" in raw.lower():
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, re.I)
            if m:
                return m.group(1)
    return None

# ─────────────────────────────────────────────────────────────
# LOGO PULSE — image détourée (fond transparent) embarquée en base64.
# Source UNIQUE : paste_pulse_logo() remplace l'ancien texte « Pulse »
# partout où le logo est assez grand (cartes + vidéos). Les rendus
# SOBRES (hommage) gardent volontairement le texte. Repli auto sur le
# texte si le logo échoue → jamais de rendu sans marque.
# ─────────────────────────────────────────────────────────────
_PULSE_LOGO_B64 = "iVBORw0KGgoAAAANSUhEUgAAAggAAAC9CAYAAADWZIWMAABEmUlEQVR42u19d7QlZ3Hnr254YXJUmBnNaKSRNBKKSEISwUggkpAjGAewOdgY1qyP7WPvsY1Z+8AaL5aPMcawNou9ixMYjgGzCBFFEJIQEsoZzYw00mg0QZPTm/fevbf2j69qul6rb3rvdve9t+t3Tp9+76bu/kLVr+qrrwpwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOR3MwM8m55K3hcDgcDofD4XA4HA6HYybUY8DM1zLzA8x8jX3d4XDkA5+ADocjb5Cc3w7gQgDXyv9lbxqHwwmCw+EoLljOpwFoABiT/xveNA6HEwSHw1FEZsBcIqIGM58C4GyRSWc4QXA4nCA4HI5iQ5cX1gM4BcAkgLOZuUpErDsbHA6HEwSHw1FMrBOycAjAMgBVbxKHwwmCw+FwXA7gqBCEYwDme5M4HE4QHA6H4yQA+wDsB1BBFIfgMsrhcILgcDiKBIkvaDBzBcA8ALuFINQRdjQ4HA4nCA6Ho4AgImIAq4QQ7AUwgRCouEg/483kcDhBcDgcxcQSAIsBHAZwBMABAOPyHnvzOBxOEBwOR7Gg3oGNAGoij8YRPAgb5D3PheBw5ISKN4HD4ciZILwIIXvilBCCJQDWMrMuQTgcjhzgHgSHw5E3lgBYCmALgK8COBlhycHhcDhBcDgcRYJ4B2rMXEXY0qgehP0IOxoWA1ion/UWcziyhy8xDIAgNf/GBWWvBGfS76Tl2uX43+5GLiRI+n85whJDGcBmhF0MQEi7fBaAe8xnHQ6HEwTHCSk6U3m6kHQMG1YgLCkQgEcRkiXtFS/Csh4TYYfD4QRhYL0FJbGkSnJUEaK65wFYgJB+dlxe18+qdUVGkLIhE3EPhP5fMr9RMgcQAsXqctbfLiX8fkMOSrgmm882zG/VAUyLpXhAjqOIAtTYnNm9C0PtQQCAi2RMTwCYIqKnmfk58SD4EqjD4QTBIUsJCxCtvS6Q81IEN+xKIQhVo9THAIzKaxV5HUaZl8xrZfnMqPlOVf4eN/8rQTguxzRmbjWryzFp3q8bQoGYx8N+9rj8rSRhF4BnADyPkIN/QsjCiWsz85SThKEmCGcb78E2mQd7Zfw7HA4nCA5RgofkyNprUYp5F2CtflfQjhSxAMF7tJmIDglB2IaQXXHEm8fhcILgmOlJiFtZ8bO10O0ZTd6PExH7fwOzTEbTJoAyyWJMuv/EZ3BSMvRjvC7kdBVC9sR92u/M/BSAlwBY3WZ8OxwOJwiF8yQgLcHYyy1jHkDpmMvYYeZ5CFUcpwHcZ94+iLCcdpaPLYfDCYIjHwLicOSJcUQ1F7aY16cQkid5RUeHI0d4lLDD4chL7pwnRGAKssQgOIgQgLuBmVeIt8G3OjocThAcDseQQ5X9mQg5EHYA2GxIwC6EnS5rAKx3WeVwOEFwOBzFgC5zrQewCMABIjpq5NFTAPYg7GLw7Y4OR07wGASHw5Gt+4CoLt6CK+WlHbGPHAGwHSFQ0WNmHA4nCA6HY+BcAUHRk2yX7ejzEihbAbAOYbnhDuUOcj4OYJN4Dzz2wOHICb7E4HA45uIN4E7JQQynICwvlADsNASiRER1AD9GWGY4Vd/y1nY4nCA4HI7B8SCMMPOZs5A5GxHSiu8C8IS81jAeg+cR0nIv91Z2OJwgOByOwSEGWuPjfQDuY+aV6gFo81UlAKr4d4q3ADFPRE0IxGJvbYfDCYLD4Rg8XINQVOwVXcqUeQiVPPc2WaI4glDueR0zV+FLDA6HEwSHw9H33oOS7EQ4HaEaIxuC0C6osC7nMxESIt0qv6keCSUCB8XLcBGAUyRZkssrhyND+C4Gh8PRLZQEnIKQ6AgATo8RgOQvRqm+T0WIOdjahFjsEgPmVAArECo8DgqB0sJkpRjpQez/eLEzRqie2vAh5nCC4EhbQPXtLSYojNSt3hSfhdooxUaGfcYdWvJN79G0FSeQAgAoMzMDuNY8/8uYeQmAQ+IN4CbtpFscFyEsM+yX61Gsj7TC4zKEtMvo1zEtfaf33pAxzZhllVTzm+pVqacxTwrskfES9k4QCmzeRQJqUAhNRYR/wwjYXrdJmlYZ92GfcY/byv7elPTbRUbpzwcw0aadWb63AsBahCqOdzYpOX5IvAtjiLY69iUpkG2ZdfPeGIAzEJZRFiJkjDxFyM64UfxHAexGSBT1DIDDCPkfnpDxUIsp81IvyYJ7KhxOEIrpQViGUASnhJCuVoW4CjIrkE9YPsZKY/MeGeHeSHivJK9x7DNxa4/Mbx8VBdAgomkiqiVYNj0Thsw8guACPx67NwJQNc9CCc9pLfuSeb8uApzl9Yqcq7E2nCKiza0Ujayvr0aI2J8217Tt30i4Lzb9V5OjYV4vy8FGgZWMgmrI31NEtNXc03r5jv5eCcCo+b26WP8vMY8yCuBXmfke8109T8lRRdi6+EqE2IUpAKuYeVQU5wiAzUQ0QUTHmPlho1z7ZW6VAJRkzNbN+LoawFUAXgtgNYCV0kbd4giAncz8LIBvANgM4FtEdFDnqBDqOc8N6eelZjzoGJo24zw+Fu34byR4rUoJ8z3uHeOYPIr/Pjf5LZhr2/E70uTeGub3quazB4noWZO0y+EEoRDEoEpE0wD+FMA7ZaKMxCaXVeaIEQAkKHUkTGw7aSn2XU5QYnGBMS3Koc7MmwDcBWA/Qka9+4nogBGGZcxyXZaZy2Ld/QyAf5JrlmLPV2rynEntQE3IUClBkNZFIH0HwHUS2NdoQg5GRBmcKd8rtbkuYsLX9m0j1t6lJv0GuVYFwFPMfKEEHl4E4BZDBPQ7ZfNsKphHY+9/0hADjgl0fa46QoZEVUo/FOJWBbAXwIsBTMh7zwuZHO8Tj0FZiEGDmecBeBWA6wBcj+TS1HV0tuRjP7MAwAY5rtZ2YObvAPg6gC8S0aHZEgUz5sYB3ATgLEMcGwmKlRLuPz4Wk8ZW0hhNkiWNhO8lyR9qcm1qYcjYdtHnGwHwaQC/LmOw5prDCUIRyAGJwiUA5yLKY9/vqWpXiUWp2M/M3xbF+iUi2mGIQrfLD/rsV4uSyVLRaNs/2YGCKEs7jGU9bOS+7hAiBQTX/+IOxw4nKI5Kl9cekQMAHiGi5w3RPYAQh9DIeV5pdscaM68F8G4Av4iwjGCfJ07uyrPsE44p6JUAfkGODzLzJwF8goh2x4hwp3OChRicHbvHYY9JUG/YZAdz0gHf5jhUEAt1MYBLEgRNPx7WVT8tj7EUwJsB/B2Ax5n5E8x8HhHVxfKZjdBdYjwXWT2bWtI/aiGM9LXVQg44wcuT5qHt8Yi5p3mx+291UBPlw118197Hvphc2i4ehMkWXq40yUFJUknXmflkZr4BwIMA/ljIgW0nQrTMVJqD8lGLuCy/Z5eI6jJWPgDgQWZ+HzMv0eJXplx2J6R5ifESccGOOw1hcDhBKBQWIQRD0QAcVhBWY5ZYXZ7l3QDuZeaPM/NJIgwr7YSh8aiMiUdFLbqsnk2v9WwL5abz7yrxbjQwc7kii/YnUcLaZm+JvdfqaKaAqIvv2mvdFhPc24QcjImVnJlA1+tJKuk/EGLwB0LANTZDSUHalqiOp7IhJScD+KDMjXcIkekmV8Q5s+izQT90Tt6eB+F0guDoh7481wiSQXShxYVhHWGt+78ipPT9ZQ1qbEMSNABpOULe/yxdihqXsA/A4x0IoxU5WDS6HDCBEP+gOylOzqnPAeCeWFsdkWMBwm4AdGglz4UYkEkE9VKERE43ADjJeAsqOcpOJSU6N9YD+L/M/DlmXiakphMv25UFk486pg4DOObqwglC0aCC83w514fkmWyE9SoAn2bmv0IIGOMOFMYSZL+PXoXR80S0XQLDWm0dXJ/x/VlPzSRCcCCYeTmCCzvreyG5jwOxdplAqOhYRRQQmSY50CWFBjO/D8D3EXZqWGJAfTY3dJnuLQBuZeZzhNy0IwmnoFjQ+Xc7gB1JQcOOAhMEXaNj5tIsD4offfqoqzuwWAcRFSMMfx/Ajcy8qIVbVV+7NgfCpG3/DFq7OPSeXpXDfJwSxXc3ojX+cxG2g9YzvBd11T8MYIvMKxXchxAyKo6KFyFtctBg5jFm/gKC+153XfQTMUga5xqNfx6ArzHzhiSSIES1LjsY8iCl/eBB2C+eMjeOnSDMEMas1sEsD44fffaIqmzOGeK+VWE4DeD1AD4vwq6UQNj0/7U5ECa91g/k3M6aW5hTe1YB/FB2DOSlLLStDsjSUUlJn/w/gbBMdF5a49qQg8UAvgrg5xAFzZYHZG5UhCSsF/K8QshAKWFOnI5oe2NRCII+5/eH1IBKbVAVwXswKkJ4nhxjYpVoUpsyZiYM0sjeulhak3KeNpaXJn853sUWo9S8IyJUxwBcXADyV5W+eA2AjxPRr4u1VI9ZpgCwJkdBuLUDpXQOotLHWd6jxnjsMK+9Kcc+fajJ6yMIQberU/YcnAzgKwAuk7FVHVB5XkOIufl3Zr4OIWeDxuPo+JqHyCNXFCNRn/1OV/tOEJIsz1GE3AAL5bwEYUvdfCEMNhPXtJCCIwhV5Q4gJHM5Lm02hShhTD8x8CUIUdZFgJKEX2Pmh4job3Q/uHGlzhOBn6Xy1bVqRrTE0GwHQ0MI3XxEiZWynBPTiHYOANG+/jysq+80EegNhMDJ5SmSg/kAbhxwcmBl+jTC0tqniOhtCeT5vIJZ0bqkcExkunsQnCAYSUM0gbCnevsQs2NNfjJeoMFfEcF3AzN/k4geFZcqG+tzZU4ehIPGKm4VDDWWk8BSy3GPKMu82opMeyVhUgjCuUr8ekQOCKFA1DiALwO4XKzv6hDMi6o8y1uZ+T+J6AtCErStLy+YntPMn/cjxLl4gGKXQmL4KeTMAMVyl0fTYMU+eTy1PC+WPq2hGGuLZIjAxxJeP0es86ytFSAE1+3p4HMvycmiIkSJiICwdn1FbDxlZdlta0GmdiLkwzhT+7JH806zD/49QpBobcgMJiXKH2bmhZjp7VxaUCv6kBADz6DoBOEFXgQboFjv8mgarNhHDBkIqVNRsAmg7tNXMfMbRQBUjfLVSPSstzjea4LtuEWfXZnDXFQr/A4pAgSEdWnKWGloGzxPRPubtNUhIQbrMLviR0nGQoWIasz8HgBvR3DJD5s3Vcf9OgBvk3ZtSP2GMwsoJ4BQ98PhBKFYMG7XKwvar6rYfiumpJc1sUqzUHqPNrPGTVBpBfkWIpowf1+XQ1spHkvwCmgfTkr/LkS01XHWik3X45l5I4APy/MO61Kr5kl4OzOPiZxYgShxWNHkxO2uLZwgFBKy9n5KUR8foRrkxcz8IiKakvZ4WQ7jXK/VKsWyKrjzEOX0p4zvkQF8z7x2Zov7TbPfAODbLfamH0JYBpmHOXrIYiTk7xHVvxhWS7oh7bcRUdzBEjmK5EHQceUZFJ0gFJIYqIBfWrCJr5gWa3jMkIIq8kmxrEseD7SwyG2RplFkvxtGc9PfbcbQ8hz6TZ/5cAvysA9hF1EJwV0+l/7UqozvQqjwWcPg5DmYDTnQnVcNANcLQXoJoh1YRYDOrS1t5qTDCcJQQoXlixBtlysSQdC8FRBhqJkTlyIEt+VBmI4BeKoDi/ycPDiltMcBREsMJwO4Rv7OMkCxLG31aFxwmzTa+xFVeVwxByJNCGvwiwC8H1GA5LBCc7gAYWvfy8RLs7FgStIm4jraIibI4QRhqAnCyg4U0rAKAE1s1QBwqrz+CoScEFla5zbF8tEOPn9tToQKCAGK28zr8zMmUyfqVQB4TEhBXGlpgp99sXubzT2qYvhdhKW4YU4SxDFDoQbgDEkGtbSgcvJBVxVOEIoIFaqX52Qt94OlBESBZi9m5pOMwqvn0Bf3ENG0JG7iFspxWY4WVc28dimy38GgONzCoiNDuKYQtrN2TYJlCaUh4+J30X8JztKYEzU5N+QYBXA9hjsVe6vxfrPrPCcIRYROgKwzBvbTs6s1OC5KZIVpj1IO9/Nss2ub7H1rEOXDz/Iek1LOXiOv13IgU1+Tdmm1tHEPQpzCilmSPvVEvFss6CKkGK7H2ukIQnzOhQWTE9rPB2ZDLp0gOAZXO0bb5ZYj2sFQNIKgiqaMEJiolQjzIEyq5B6MKcEkBb1KFF4eAYrWosqb3D3TQT9tQkg6tVByGHCnyZJM7MFiAO/E8MceJBEFFgX5coRgVC6InFAiuBPAHS3mpMMJwlD334sQrasWMUCRERXemkZwo+ZRAKlkLN521sqSnNqMpM0mRYHOQ6iMaQlOFuRAiwVtatFWJ0r0InhltMhaV6RNvAdvQKjsmYf3QGMCslJOdTM3tMjcMeSbcyNPHCai/UAIfnW10Tkq3gRDgRVG8Och/JIs1Kyuy+a6ut3xNxBK2mZ9P4QQULe/hdLT+3lDByQiDcVRRkhr/LAhBesybiu91n5ExaIaLfp4hxzjs5BZ+hu/IH9nnedBPRblhNfS6uNpzFxi0LYdyVFG5aGY1WB6VD1OThDcg1BE5BmgSLGjlqEwYKOYa2IVH0VYZ60gn7TB9wHY14EL/PQcBed+ItJ4g/U5GgrTaBH3YIT5LoSEPwsRlX1uO9Yl3qPOzKcg5D0gZJv3gETG7kVISvWoeS1NElhPGFdV5LftN0lOZHFU5HyLjKVhzXnhHgRHS1yU03UbmJmdbAQzI80pg+urMJwyltOCHC2kZzWNslHCqrBIagCMILi7sxbWeo82J/21CG77LIsVaXW92wFMtauuJ+05hpB1cqNRtJ0oJSXQS5Dd8oIS1yMAfg/Al4noeakceQWAfwZwWgqeBDYEoW7Iej02N7NGHSHItBzrt0aHhip3OFc0JknP0whep+05knEnCI7c0BBLdUUOE74M4OsA3oqo7PIKAG8G8DsIyXfSIgkqABqGJEwbglDNQfnqtTa3+Qwj5Gq4QF7L0qpREvAD89r8HISnEoRHTE2KRhMvgO5AGBWvy+ou+lY/81ozZrIiCCUA7yCiLxqiMwHge8z8awhBor1uc50DDcxcZmDp56w9xjre/g7AnyAqQ50FCWZDPg5L+9fhcILQ8Uhq7QamNv9nNcAT183MdrmTjTWatQC4hYgOmP/3A/gQM38awaW6Dumtt1prQV3V04hci1lDn/H7MesoCStEWGYVTW4tqhKCu163Fl6XA1HRZ97VATlRUqXK5rROCI3M7bo84yWI3M5ZkefvEtEXmbmKmS7/MoDvihfnyh6SllpsHig50CWNeabdKOM58T1TNdThBGEw0CZgpd/dUTrJTwdwErKNzta2uV8EcdkIoxEieoaZbwDwCRFYvb6vBqJEMDXjSZgSS7OC7LdyqSLb3UZgNgC8OmZJZ9FfSg42I8RJ6OunZ83LpX+mAXyzAzKlfbhTzhd38B2Z3tRg5pUIiaCyItA6Nz6v/9vlE2aG3NfjPSYISZ40SwhGcuhnLTl9n8iJEnLYZuiBiU4QOvUYaDSxWpkjCIFPY8KwR+WYjxDQs8IcixGVnLXuuqTgmCQBZ9/nJscUgOcQtnRtQ9gjrhHcewAcTVirfSmyL76iSu2YuIhZJiEzs1ZS/BaiwLJeKmu7vDCNmVnjdM0xa6iQ3yp91YxgahuclTEJtfUqdhCRFkc6E9mnWLb3tK9DDwIAPG0IcTdCf53M7yyeUclPTcZ/IpERZVnt8XXZ9HMNUfyBjs28+nkvQlAsM3PDlbUThL7mCEYYqULRcrIHRPHpUTXHiJzjQTZlRHuzx4U86FaskpzL8r7+jrLoSYQAv6PmfBwhsOmQ3M8BRBXZpmLkQO9jY07K8BmE7XIzhKBYR0RETzLzc3J/vczPoMsKcVeqHuM5jSsA2EREByXFctJ6Z13IU5bbCuuGTI0BeMis679CCNx0jxVWu7YihG2Wh7v4npKJEWYeIaKpNp/XeXalaYesllGm0WKrqyjLXq6H14wnLU6i49sss5QTZQD3AzjYaWIrhxOE3CBC0QbJTA442QGANRlbo4rnRBlSkxiJNHL7W29LPcF7UEe01poHtjdT/NJODWZehhDJDqTv8lZFoWRqPoC7TX/lMf+VYN5FRFPMXCWi6Q7GuSrc5UKwNrXZ/WBLaluFlfX8jI+BmuxmuKRHY8CS40bs2ZUgVHMgCPr8TwohatfPDicIfahluwtU7PVnWmWPaxqkKIKmzswLkH0+f72Xx826Yr2JlZSWgqlh5nammiF+lQyt8zi+26JflTAtQuTuTRs1Q6AIIYnUXjPu35jx2LE41OXnD0pfLxClv6lNH2sfLM1wPGgfjyDkl9iLmUWw9O+TEO1iKc1xLtqCTI3Y2NNly4VynTwCFB8289bhBGEgvQodWwH9cttG0KzLiSDckpQT3+yuOB+9d6VzgnVsj0rOHoQnOyCJl4s1l7awjrdPCSGA8gFD4M7OgUzps3/NWMDtCCEAbBGFuxKd1R3R3z0142esyzh8LTPfI8/baDJ/qUf9G19us/JgGtlWNI2P9zv6XJY6OmB5jgHjNXJeK9YK5zBmdjeZ+Pr+BlHWtZSEczxjnOY/yCPVdBkhXqRVQha9rwuNdZ82bJ4IknvcJURumViWeYxdQgjC7Qb7EeJzgDYR+bFlr8U5zc03yT00DInW8yXG+ucuDxuIWG/ymiUMDURBmlnOCUKIMdnp4toJgiMfIfTimJWVxcQvIbh7H2pjGaQhlKxFbO9JycJYDtaK3stmItqqsQYtPrcmI4u2FlNAFYRdFto2VyG46rOs36HX3iljqG1fGUXfQJQ3YUMn3xXFPJqxB0G3/L6YmX9BglXLMXl7GaLAwW7TB+v3ytKnowjLLosRlq90t9VChKWsech2mcWO9YcA7EopHsmRATyT4mAThLMzVohqGewgom0iwBtNlMBbeiyUrKVk11utB2FJjn3yvFEC9QSLti4pll+ZATmPt48Gb95vFO68HMiUut9/SER7W+z2SPQIyK4YAHhJF/feyGl+NgB8mJlvI6Lt8qwapPe3AP4DzZcZmhWV4thcYLxw+7T9fkk8Cf8qbZbVTg69j20y7l+QdtzhBMGRrqAFQiBUlpaBTvytLaqj6f+npnR96zGwglS3puaF2zv4zAKENfS0+ywpaK0MCVAU/FTGY8fiaJfXVtK1Rf4/qVPvAzNP5kCEdIvlagDfZOZriGi3EiIi2mW8IelP2pDKOsu+JuNByLrtZ9M+5PkZnCAMBcx2uUUAzs+JINxhcujX7L2FE48hKpjUSw8CJ3gO9LUxYxXnUQDpkRbXVuvuQrlHzuCe7C6PMkKujYfNZzbkZFkDUTrqTttBCcIelVuSQrnRghio0D+S01TVez4PwLeY+Q1E9Jy571LK40BJymk5GBKKuwZBpjo5aD2IHIMFneTLEAoi5THxmwUelWSyXQjgReht+mcb1FVPIAuakCpTvoZozblVXQF1654v7VFLsc/iybTU23RYSQwzz0cUvJdHQau7Z0nC1AOyHMCypF00CbItz0p+ZenrCwF8l5nXaUyCeBIaaR2IUjyvR4hBaCC7uh+l2Jzod6NrudTMcDhBGBqCcD6y2S4XV4aMyNXbTOiOYuaaaC8tdetJsEcepWxPxGSg9X5vfW1VBkq5EVMG+vchRInBLgJwLrKv30HiyTjSpdLWz2kMwgq0376oz3UgR4IARKmXzxaSsFaSJWXlvV3UYlym2c/bATwrmUPBzKW5HCmQApLzUgDvgRS8S+NaThAceRAE3S5Xz/jah4wF2EzoXJWC0GkkEAWYNliYoyLYR0SHW6xl6r2/MiOr3bZXAyH99O1EdExeq+bQVkpUHgSwucVuj1YEYYuQnJEOCIJ+Z1cfyDolCesRyj2fkyFJeH3GniJt96eJaJ94NOo98IikJUc3iGdqtauW5IHrGEyclpO1vBPN13UppgjTUHh6DVsXg3P0IABRMFai18TEa6S9Fhz3qui1KghVHBXX5zhuD0gMTbkLcqttukMI6soOZJd+5wd9YgxV5HnXA7iFmX+eiG7NIML/rJyMzkuY+d4ejekSgLcT0UNt0mt34z1gSXm9RsbUUllm8N0WThAGEyaXewnZlrC1AvdHsnVpxhY1mXRaXvfyFO/Nbu0qGbIwlkOXaADgj8zzNmJ9pgLtAoS14LSWhDiBKGjbMKIqk0CID8kLt83hu8cRlhlWon2Zam2PzUJoFyD7EuBxKCk6GcA3mPmdRPSZlEiCktK8EkUtQFRvohfoZR0HDfReg7AcOoGwM2aRbL/1XQ19wqods8M4so9MVoXz4/jY0fK1ogjfJgK8jt7uYIjv87aehSryKWer19rTwRw7R0hMPcV7jO/u0L8n1ZIWpbEgh7ZSdB3ZbnYlTCMKkL2wzXcaQqT3INqCWu+Duau7GMYBfJqZf1uXG3pV8dCQ0vWIdjrlkWFUc5bUZ3lozZWdiKp59oo8EaI8MvsRPJBn5zgvnCA4eqaQ1iP7pEAaib9H3MMlZi6LJ4GlMt+LAfwZokjmXio9u23Pjt8SojLaWQvACkIxnEdjJCoJqzIYG3Z5oW6sriNGwG5AiBHhjNtMt/UdmeP403oXp3dyTbEEv9tngr9kFOhHmfkPxINAPSIJ+hsLhYjk4Tkh6TOb+bHbQ72ETwB4vkfLCzomThNCsAlhiXARotoxDicIA91fFyNa08xy61IDwNcl6GhSznWxft4F4JspWPIUIwpKEmwGuTyyAtoyxI+2uL4KtNekrKQsgdLjuPTbPYYgjCL7pUXtr80A7u+ATLUaB3vNc7Trc73GF8UaLffRXLaVUG9g5o8ExwdxDyPpz5hlW/cbdopSL/Vw3r5ajCwNfN0FYDUzzxPvk3sR4DEIg+pByKvG/TRCjvkNiILffgbAFQhb55CStWLjDXSpwRamGc+xT/YS0STQNOGKvnZaygQhqdLlcfGuPGTWuF+Tw9jReztERBOztARPpO+V80kizI81WzM2wZCbAXwdobx11nOmk3FdA/C7ANYx89tlR0xHaajbyIlXD4nce7wXBoBJ2b0GoY7NI0R0QN47gLAzZq25nhMEb4KBggqMjSkrm2YCZxTAF1rcWynFe6KY8qshuPfzWlfW6OrbRcC8QKCb0tdnGFKXVoBifCmmJpbRQcysnHhhDt4Wxb1zaIP4tsW10qab0CbnhiiFv0HYvdFvlqES7WkAPwtgFTNfT0R75kgSgJBQKq++7iV+0OPfU2PmO+a17QjLb2cKQSihP2JWcoUvMQyS+yCyui7Msf+SgonUKstquQOIXMZPI5+c73ZvfrO+sEW1liC9JSFLmrQ/tI0mANxhPpuHt0Xb6ts9IAhb5TwfURwOtZgzdXEXfxfA9/pY8FeFJFwB4GZmXqXLd91ayQDqki3zvAGX87oDZ84ZGY33YFyIwAMANpmlhOcRaoSsY+bqHImZEwRHxhI2yvy1EPkm9UgKJkp7HKmFqDEHNUTu85sRsvNlSRBsVsnNHVz7jIw8GjZAUftmEhJ/wMwnAbjG9GOWVjIQZXKcC8l4DiHuoxv5pUsaH+9zi1r34V8E4PvMfNEsEirpcsvJCNkyB1XOW4/RDjPG54qz5Xduk3bSsblb5sk4pDy2xyE4QRjEvtqIkGp2tpbYQDpPEohIGSGN7lcQ1hOzHs8lsc6/bzwrzfDyDISprXQ5Lcd8hG1++40CWp7x2FHv0j5E+SLmYp0dF2EORFUdO8VrzT31K0nQ4OMzxZPwE7PMurgY6ReESnvcAMAzRLRrrrkJTPDnGQCeheyGUa8sER0RD0IV+RW3coLgmLMVtgZRwpUiEYSKOWpCkv5DrNKTc7ovFSrtrKA0s15qvIFdXmhIu4wB2EFEh40ng3NSGvuJaLsK6zn8zgRC5jsg7ObpRJDr9TSB10ifzx2d3ysA3MjMr++CJKhMv7qHVnee2DlXXWV2haxH8L4+KduyS7H3d8i4OEWzLTpBcDQbVNTkaFZQpNzBEf/Oid/tUBEAISMfCjh4Sdi97qs+DuAfEdI6VzMmTCp07wIwkWTdmLLc84ylm1b8QXwrqJIETRSk+ElESzSZTSUVvl2M9aYWoOzGUJfzWR3MYw0UXScKggF8SMYP9/E8UpJQBfBZZv7JDkkCGTI4DHLi/t6Icy4hJI1qIIpjiSdee07I5xpESzXDos/KsqOnK1T6+IGog0kwW6Eb3xL2AqumxeDgDJ85icWen6KyGQSSMIWQqfFvJNJ7nVHaWa2r67V+LIpLA8yS+u4MY7mnnUGxEfMiMGamNh7NYyrL+aumJsVsCYrm4tAlhvkdjhm1HpeIJ+OPmXkngI9Kv/VrqV87nj/DzL9CRF9qk5pZl29WDfhc5xhB6GjutJChSxA8eU8R0UG71dbIeiUIK8TTsK3d0kYHOSu4yXjkDsYstdB76OK9ugZddrtU07cEoc1DpM7sZKDpUUFw1+qxUAbcAoS1voVyXowoc9kEwhYzXQM+jOCOPo7g/tXzYQATko2QWzynTvwiZ/tihKRIOwF8TPoozboPrRQVjCXLLSbsakTrymkQGFt3QUmIrj3XADwj43keohwI5Yz7DIjKLvcC2+W8XKP2O/iO7vy5TxTs3zLzZQB+pc9JgnrHKgD+nZnfTEQ3NdlWq16r+cgnLqfX5MjuYOhI5jeToZK7BQDuS/o9Qyy2igfhJITtwe220M42n8dcP9OtPns5ACai27shCX1FEMQFMiKTQv8uGcE3Kgqiav6eJ5+ryOsV+X9MlPUC+cy4vF5GtJZdlddG5W973fjv6W+Oms9VupyANcxcL64jRODvYeatCHu6nxNicURIxmH5+ykhBxcN+MSfC+rSl39GRLtlzFycg0dFFewdHRCEa1L2ZDSaXHdULO095v+sg680HfU0gIfNPc+VbDwt51MALCSiQy2Enj7rS+X8sLjqRwC8ByHo93KZk/1oMJWl7xpy/iwz/ywR3dwiT8JitC+H3e+GgJaWb0rCRanbBGoVRNuty0bez0coUPasGBdJRELJ1QGRuRsQMpC28h5U5HNkCGYDMwOGLXkvGcIxjeANBWYWn1OdtEAMz/mid8pGR1ldVY71c8n8lr52FoC3I6Ty/j0i+kinycr6bUI0ECW/UQZZjXW+Nt4ycyyQhpwvCmSBeW0hwraVxfJaNcVBHY+OLsUUeSWhzZcJY12HEGG+FSHi+5h4GfYLi35OhJkKi6IRhJr03beI6J9EOIyiMzdzGsIraS0zSZldkKXjzfw9ihABrgL2dOSTQVAF/V09tI7UqjxN5syhJEvPVD8dR7TlTws3MREdYebrEXIknJeil2eu7afGSENk2OeZ+UoiejxGEjTHw7nS/3lXr5zrHNsjcq+Zpa6G4giivBijMUNuBKHOwhIAd2oa5Rbe2qelDRcy8wgRTSUQA1WuZyJkqxwVPUOYmRtmUv5mI7NLQg4mRNex6d8R0V+qs+yhem2R6LJ5XbbplLTHewB8pFPPR18RBOm0OjNbhTtlJslxEQZxNmU9AjpAxszgGTONP994COxe/qrxFFRMp5Vjvzkv4e8Rcx9xi3dCBsoRUfYHEZYapuX5jgqr3YpQlGSvPLP1NEzJ6+el5YLqc+ia/7MAftMEnr1UrMgsCZMKr02I3OZJiqkusQlpW3I20K4if0/J3zZA8dWI9tlXMm6r3ehNciK7VgzjPWylXDUnwDky5+5WwinKdTcz/6wQhxV9Sr6tlVwXBfEpZn4FpDJhbE//5Ub+DGK23BOluolosoW1WzPnSZGltkCU9TAsxcyMokm6ByKv9wv5XAPgyfj1Ta2G04VQbJbrLcDMbcZ1YzRSrA8bsXFKRhdVjO6pGp00LvprgelXHa92CVwNmIqQjVchWmL7txjJGSgPgnZAHX2Y7UxcSrq0MS5sboHptMXGwp8SMrNfPAJ7xHV1bLbRscx8CooJHci/RERbmHlUBMJJMolqGQp1nZD3Sh2AJDevKqZTkG4J2UaMIFghMw8zU9SWciCX2lZf0wj8FsF13WCvzK1FotR/3KZ9V8q8fBCym0LljNzTE8z8SwBuEoHczx66ssiWKwH8DhF9WJZm7S6eRQNuSOh9f8OM3UaCnlA5q2hVKfSZBDKQ5BmYZuY9Qg4Wx9vRKNZzELy+DxFRX9duEHl5jeie77fwyAwGQUh4QOrApdrNe50MzhmvSRChWvVHe/A8zXZiJF1ft3cVMUBRA8h+i4hui0XBn53jfR00wqsZmT1JyGOarl7rWlelpp61rSrUALws9pksBf3BHpEkG/C4UxThRWL9UxPLu4EoDuRhKRZ1gqgY4nKzkIQvYKZLuN8UpxpPBwD8HjN/AcDT0sc1kS2X5NDXaaDjFMsdbJ8lleMdjK+nEdbt1yEKajSX4hEArxNy8piJheCU5ncn77cKpJxEKFY2PLsYmrh/OlHmWRIV6qLTOPY83Ok1RYiNCatNyxrtR0whuNc+QET/SwW72Vr0hhzaQ691Z8y7kaSYXmr6Ps0lhjjhHBfP1bPG6rwiB6Wh6/kPtmir2eAogkfu7A5Joi7N3ZYkN2RMVYnoi1K2/H8jctP2m5JtCGk+huBO/lMi+jWz7DY2BARB48+2dyrnO1B63eiKnQgeyiozjxPRhPUeMPNVCLFFNxHRAXm9b2s32GDObu/TEyV1SVRiR6PJUZfDvsZNtjJ2qpBWIf2Swf1kKdUMOXh/E/f0aA7toXPm3haCR+/noh4rxlYCzyZIGgewm4i0kFQemQM1avs4oqWOxlznnyzpNBCWGZqOAUOsRxEFij7Q4renZYz9A4Cflt/vt8JOur7dkOd9HsDrZLumtq0Gaw8q9Nn2IcqBkEk2SBlfhLBUsVMMshVKDoz34DUyNu4dIL1Vnw2JcYIwAP0r55UIcQ6DGpncjRDUXSv/PU4OYpnx1mZMELTt1YJrxtbV1Zvm/VETL4JuLdxp3r9axk8jh7EzhZnrxL167n1yXmHGTRKWIwRoTSIK2uQmglSXG26UNtuCKKNhPyhOXeKcMvOkBOA3jOHxSpETjQGXE7uJ6ECH3oEe61NiGV9VhFgDICzzMoBLIdUgiejpToP9BhVOEAYH52fJpnP0GpQRAtDeSkR/LhZjLUFBbBDhn2WKZRVUjyKKiOYkxo6wrSqLZDXxNMvaHjZA8RRE0dNZKjQAuAXAIRGk3MM+0CqaZzHzqIksj8u2y4QwbUGUP6HVeq2ShIeFJDyIKBA2z7mhkfE2l4puIX2FySi6rg1hSpvcz/XQnVtbDOHOuq2BEL8zAmCN3ENDdiVdLh6GbxdB6ThBGBwPwhUF8Ro8CuBlRPQZEdTNBF0eAZuq9J6V7VeVpIQrcl4CKRubkUCLp1p+wnwmzZ0U7e7rKbGwep1fQNenT0cUbZ7UD1eqwJc+K7cjKkISykT0LMIWsbsRFQnLy3vQwAtTadcR4jEWIqoYmmecUrkHhyY3ukd+M684uUPicVoNYJmM4fMQtsw+COBRzVw5zMqnAke/Qy2js4bsuVSh2RwXfw3gfxLR0QTPQRxvzJGs7ezgM5chClZM24Ng4w8IIUDR7vm+PgeDQNvh+XZW+yyxVc6ai8S2BRDyqRCi/d93d3XzYQtkmYj2MvPVAP4ZwJuQTzKluiEE08bS1r/3IAQmfhrA63My/qYQAnfVs6FeDjbtZUu2250idfO3JkS7NUbKsxm0UVGwY8x8DGGZcAVCTIruBPq+eKwGuZy2E4SB16BRbvVlhiAMevyBKrKKERxfR4g3uEeeu5Oo4MU5PsN3Wyg9FXgXGOGeprC2GTw1H8SzkO1ZoiRHcrImGcCNKQn6I4jiLdYj7HMnM2+YmRcgShp0b7fzR0hCiYiOAngzM38IwB+ZPqUc5o1NsqMJgiYBrJUaDPMy7mdNvPUVInpTCgo7z/iPTQgxCGuYeQohSPxJAA8VwXvgBKH/oRbRcgTXVh6WQS8UWDzVqFoKNwP4OBHdJIK9glB5rNGCMNWFMJ2ZA2HSaz3VRpADwfWdxf01zDhRxbVPt2YhBFWtQT7BrYTeFmmypGyfkISFMj+S5s3pCKnMpxElyunK4jOxDSUieq/k6v8LQ8qyTNBlLW6bTK6GsJz1k4iCNrPu623STlrZlBK8Orb9290f51hu2cYh7BUvwjnS/ncpcUQBMto6Qeh/ggAZnBXksy+7gdZb+ZoJXVVI8ZSnWwH8B4DPEtG96jEQYdxujVeF41oR/lkKaL3WQTTZnx1LsXyVsaTTttRriLJ3jiJav4VYPWPINsWyjtMH0fslBv0drU9yDl647q5LO1fKMz+BaM24a4tUvBENiTm5gZknEEpFI4M5yQkKls141AI/UwD+C7Lf6aTP/oi2R4d5XvpWuZrtjrvEGFgB2ToM4OGieA+cIAwOQTg/x0k1V+H3PMKa/ZcRqh9+k4imVaGKZdat0F4dIyFZKb0ygB/L9qZWQmIRws6BLMZHI6YUSwiphxUrchgzeh8PyVpur1Is2zXiI8y8SwjCybH5ovNko5yf1Jz+s51DovRqplT0dgCfEg9G2nEJtvS8TausQX01UWAX5ygf7lQiNTTCN3iPtpl2vrdI3gMnCIODs3IkCI8hREqXjDdBI471tWmEIEN1dz+GsHxwBMD9pqKgWtplBBeirql2S5h+LiPrLQlHYt6MJA/HRQhrwWkTGFsmtmS8Cc8ZAvZLCR6frHAsZeKsmSKr0dBiS5oulfPtCenNZ6s0lCR8gZmfBvAlIaxpeWhsjImttUHGg0Sm/7Psa72nSfR+KalfsAPBW/kcJHFTUbwHThD6GCYTXAWhlnmWE18V724Al0uQ1gvuTwlCu7XCWN32Rg8Cj5bk2DV3tLGm6tJfJaTv1o/XmK+KoL7HWNsLcrIqWZRnmsRW3dqrzH55EqKwyMybJ1SBSqXYeBsmkY8kj4Sedbnhbmb+CYQ98aen2N92/nCMECgp10q0mYopufajAHZJHwybZX1A5vQWqeNBOcZGOEFwvAALEZICIQdr+RnZckhmXQ4mZXTdEIBmFlrDfrYHhGkp8sk1r0LhrhZkTT+zKqN7sm1OCPEHu9SaY+YzEAVzZj12CFFZ5rQEqv7+uboEYMbLpQjLK8cB3JNChVglCU/KNsivATgXvV9uKMU8REreq+Z5phE8VmVkv+xWQpRjomdLSXlDSYDIv+cgiZuKBicIfTxGZbKvRojEzhI68e8z1n/dMmeb4ayb4lM9aI9lCNvaslZ6eq0jLZSeCuzLMvL4WHc6IWz93IQotfFaaa8s9+6rV2MfonTInMJzA1GVyAuY+f8JMVou1zsbUfKtv2Tm58XCrpsxXkZU+VIV7aScbYroqnx3nvES/QkRPSdK8WkhCd9CyLvQ66UvSjisp4ZzkuV6H1uHWhAT3RknDk4Q2lhzCYOkWfniYQE3+Z9TGjw2E1zWleX0OR6wnoMkhp0Dlph7zNpS2oXgTgWS69OzxFdszGgexKPZKwA2m75ZhOxdvqp47yeiZzLKVU8AfqqFjPu5Hl/vCID3KSmUhEq7mfl6hCWoVT2crxzra21ju+WRkG+BpptT9hTlK/gLtqwwZ4IQa6xCNlxGFmsdkbs6S4Kg1ubuPupjDYi8JqaIssQeItqZJDCMIjwTUYrlLDwI6nbWuWzjRd6SI1k/mmIbaNsfi80VjhksJePZ4Q7IeLtxrp6YW2UclHXpQspFb2Pm9wP4ZA/nDMW8CEmvl5B9giR7DzuH3INQWB3XFUGQ0qlLxDJZLOeVCHutT0XYgzuKmVnGbGIMW3XORt/awV/GC91oNkDH/m9/J25VKcvW/2sJAsReK/4djbDXbGXHEPJz70ZwnR5ASGmrx2EAEwAmezSg1BV6YcZeGVum954+Igj6/GfkcE96rR2xsZxEYC4Qay4Lt769D53Lj5n3l+XYX1/OuH/KHRDeXhGybQnvaQXPfwHwgR56EeLLd6VYv2s8QjknOfG8IQiFie53gpAMm+pTq25Ni7VwWMjBYoR1wGqXgj/pdYqRCCSQDxUOpdjgtffZaHGNknk2bqEcJoQIjCLsOR43E1MJyEQPXVoN2cGwMYeJT0KEZpV9bgihz39zq74QBbE+4zZT67mEsGb+I7mXBci+HLZFFkFdF+Q0Pr+TZGXKvJ1i5oeEIKQVe8Exo2YMUT2KrKAevC0Adgx72WMnCJ25WqYRCoPs8abLBCOI0shmXdJ4M6TgTZ+42NSjcnKO97CjRV80REm8MsP+slZkGSFGQoMoNyBs88sj2+QR01Zpjp1zMp4bep2nm5E1SRT0CIDX9ejZ4ztVOGY0NXIkgQCwU5NXubh2D4K1lOKThlIYqNzmtXZrh53cE3d535xwTd0S08t1RxYL6aScrOUfysTPs8xt3KMyirCVDOijmhS6/1v23l+W4f2RseYIIUDuYIxQ5WFV/piIHs8gJe3qjOeF9mmz+cAyZ3pNXHQ3Rj3hfko5eA8sbnY16gQh7knwIMV0oevZGxG5kLMKyFOhtq0P22W+IUx5WExLk3Z0AKgQ0TQzX4eQYjnr/tJlrsfN3Lw2prSzJk0VY1H3/Jllt8iqDB+pXe0ReWwm9D6xWbNARX1tgbmvrOfFk9LX5SZzI23UixxE2JcEwZEZNmQs5Nkom/vNtfuJOI3kdF0A+BUi+qgW7lErTsjBUgB/nrGQtksMBOAr5r1LcyDweq0b006YI96aNbH+ycJDUgKw2OYHkfupyjh4rdxXL3cdMWYGeNukSVkuIcXlxGEA90lfD0WCJIcThEFAPSbksxYAhxEV/Okndk55WMOIPDqXMvN7iehDhjjVmXkNQuGeM5B9fYiakKanIDsYRHktzsHTon3z08y8FlGg8qSRN2Ujd+pGsZTNEQ8ujlvQxxF2TeUVn/OHRPQdANOmEum0VPH8H3jhckCvxmCSJ6GCEDidZTvodUYBfIyZ98sYtDssNHOl3S1mg9zj8ySeMZIwc1dZ3fSB5vxgAO8loh1FzlWQRUc7+hDM/BjCMkNWSkev8wCAl0hEdu4Tz6R6XoqQV38F8nGn6jW/B+A/EWrFXwXg5xGWPvIoHjWBsKPmE0T0m9JeqxGCTMeGaTpg5k6jvIwb7eNPAfgLInpC2vwCAH8F4LUpjAPdJaUZHifNUUG0c6aIOIOInvJdFO5BKAopUGW4SqykLImcLdM7ZRLB9AuOIey7zosgqHV0tRxJbZeHd4MB/FvsPvMkB822DMctcergPUaUArluvAoLhBjl5U16B4C3MPNT8vrZYkWnMQ4ogSApRnKek/WcrlkG8AMAW4e0SJQTBEdTAVQHcD6CmziPgb89di/9QpwmmflJhJ0MeQkEwkx3py23nIciHgHwEIC7DaG7BDPTMOcxhnv9eyTW87RY06U+mKPzZZ5mQRLVVV/DzJ1U47GxmDXKOfVBGcBuTW/eZ4bMUCkjR39BJ/kKw5Yp4/FwvxF4+TdIWOJQMtsPyZt0rVzX1PNaqlOl8HdENGmsyWtjRGYY5FTFKOYp5B8UV8YLs6+WUu5rm05aiWkVxcVdri6cIBQNqvg25jge7u0DJdwMVR8iM6zVpwF8WoLlak3I5jCggqjyIiOqtJg3mS9hZgBhmnOzhJlJkiqGFBYxnuxHLgacIBSVILw444mv192LUGei3wiC3ssjyM+d2m8EgQB8hIiOAChLJP0YgFcP6fxWz80I2sc5DKNcsCRB22G0gPJRvTcH+9iQcYLgSGcCiDW4PmOCoMsJjwHYm1PSk07u7xvIb7tjv3kPtgH4B+krXU4YQ6goOYxWpS7rVApoNds0yzr2ixhDpmTgGUT1PpwgOEEoBDMoyXr76YiSwGTtQdgkAT/lPttXrFnqtiNsdbSkoagE4c+J6BiinQxAqK46rIpTlWMJIVixSIFpFHt+FNB7MIMgENFBIzMdThAKIQSAkEJ2UcbMuJ9TLGugYpmIDgG4BVGAWNFQF8vxfgCfEm9Tw1iT14viqA0xUViA4On6wwIRRU0gZD0oNxbYet7h6sIJQhZmKTFzSY7yLI5SCu74s4wyyDo72g9jTL0frYd/x8xS3UWynjSa/T1ENBW40wwLal4f918voGvvEwD+D0J+hFIBFKVmIZxG2F75LYTiXMO0W6UbGXCL6zAnCJlYpkTUkKM+i6ORgovripjSzmLSqdu2n9f1GmIx34kQrEgF8yKo9+AGIrojtv+7LkT10iGf2ycqWBLRAQC3oRjeJCWGEwjltD+IsJyEgs0B7f9nXH2nj0InSpKI76UAlgBYJsw8bq1UzCSMJykhAEcB7ASwQ9aDe4G1OQgfArALwL5+JQhad56IjjHzRwD8Y4GEY03G4jcBvF+KRWmhIC2HXUFI/TzMBEHH5R1CiP4F0a6NYccxhOWVvyaiTVIYSuVUUbwHZWmHJwtIjnJjY0UlCOMIwYBnI+QdWIWwfqt7rm0SHLVW2Vgs0whrYT8CcBsRHZ7DvWiK5TGEPATnIrv0vZq69HYAPyHKuNGnfabBWqMI6/AbhlwhwoyDZwBcQUQ7bY0MM3YWIhRtWo7h3QqqtSd+moi+LM/8AEJgLw/xOJiQ53sCwJXynDsRxSoVQZbrPHgCwEYPTnQPQtoW6QSATXLc1AdkjRHqL6zOicBtEUu03Md9pl6ECWb+UwCfRf6pd7MQikcBvEXIQTy1rGYYvBrBE9YY4vaYknmxX8bDYWa+AcAnhtiaVOJzDMC7JeX4+gLKbyUEmmLZKzimDA9SjIIUy3M4etmOywEszMkqeGBAiF1dlOTnAHxJBOUw1qOvG3LwBiK6s0neebv7ZZiD1k5sxUWoPwGZe/8sr5WH8NnVWzkK4J1EdJd40a5GCEitozieYO3/211/OUHIzCKdQ4DiiUDFHnkQgLDUkXXwnV77vsHidkwAfhOhwmNlyJSDLvscEXJwKzNX2hSlWRbrz2H0powCOEZEB3S5iYiOA/idIVWIDRkH/42IvsjM42I1LzNtUjQPwuNDPs6dIDia4pIcJl0JYbvYrthE7Gdi1wBQIqKdAN4E4DiiUryDjmlRCs8CuNaQg2ZeEt3B8FMFmNejADYbcqDepK8B+AqiiofDQIY0MdIfEdGHJQh1Ut6/sIAyXJc+9wyKnHKC4Og11ufEyp8GsNko376HKIcKEd0K4M2I1t4HlSRoEGwVwK0AXiHLCq3IgXrBGGF5atgtSAJwlzzvCfklhOHvEe32GGRvksbUHAPwq0R0g8QF1SVGiAC8tGBWtPb3QURFmnwHgxOEAoz8EGxTlx0MZ2U88ZUg7CCiqR7HU2RBEmqiQG8SkqCehEGyItkoBQLw1+I52CrWca3V2JHzSQAWF2TK7IkTKyEMjwH4KMLOIvUkDJKVqaWjKwiR+q8kon/VpSUTkFdBlBCrMGJSzgcQlhQdThAKAyUDKxC2a+VhGTw5qBaJIQlfAvAahHTRakX2s5WhyW+0dO8WANcR0e8rWWsTcwBEbteXAzgJUWDjMCoIDUZ9MqY0FIcB3A3gcoRsmxUMRtAmm34rAfgkgJcQ0d2WIBryfhlCkqQiVjV9ChJ/5DsYnCAUDasQEqFkOfF1kt05qAQhRhJuQ3C/fh5RYZt+syQtMSgj7HH/SwCXENHXZGcMdbnUU2miNIfNgtyDkP8CCeTvGIAniWg7Ef0ygN+W18p9ShbVc6Tj4FEA1xPRu00honqCvD4LYRmqSDsYtO/u0Losri6cIBStH17aRPBlce2tA++GCSShTETPEtHPA3grwvY3tSRrOSuJRkwhsBCZq4joD2VPfznmTu5Ucb5xkAleFzgguxa0gJc9HwNwj9ZWIaKPAXglgJtjZDFvomDHQUU8Hx8AcCkR3aT1XVoQxEVDTgZbYburi+xQ8SboK5wuVkFW6+fqqTiAaOvQQG8VlFiOkvz9GWa+USzJ94iHRgW0PnvaJLlhBLkqqcMAPgfgU0T0AwCQILRGB0sKzQjCRum7Yc2BoMGH2l6lJAVq2088SncDeA0zvwuh+uMZOYyBZuPgEIB/A/AxInpcx0GLMaDP+zrpZ0YxCjWxGQMP5GBEFRa+j7QfRn9QaAzge5BUxxljCxFtGMJ2PSFsmXklgLcBeJco07jg1ihpmsO84JjiibtBt4hC+AwRPWH6flY7R0wV0VGEAL3TCzBd3ktEf8HMVSKa7nRuSea9RQDeAeDdCKnMk/qt1APCwDElFichTwH4DIB/IqLNMYLIrfpbnmMbQor4omEKwArxtHkMghOEwpGEqxEKRrHpHz04wfJH7HNxAYUEYWd/b0pY+UEiumNI25UQ8iUoUaggBPS9Vc4b2yj7eHsmzZ9Si7l0P0JZ3i8irJ8eNQqB57KlVK1oZl4L4COI6oiUWlifSfc/GznACeO017EzDfPbmjjqIxqn0Y23JUYWdQz8IkJxqwvbkL1OZSm1IJhPyTj4HIDvENGR2YwDGc//COA0FCdIUXf4bAXwW7KU6ATBCYLDkQ5RkNfGRTm8CsCLxKrcgGiNt1uFuQ8h0Gwzgiv8TgCPxq45Z2Lg6OkYqAK4CMB1AC4G8GKEaqpzkY11hK2W98p4+CqAB4nooI8DhxMEx2wFWF6RuYURVKokgJnr1cayXImwx3wjwnr1EoTqgVVEKbAnxfsyhRC/8TCA5xBSAO9q0a+NtKyefi6w1WPMuQ3bjIElCCXg1wphWCrjYQyRp0h3RagX5ZiMg10yFnYDOERE+9IYBwXq6xcqrO5jdBxOEPpG8eTZxu2uk5Y7jttMaO7zPjuxPNAqIVGXv1sxyoPdFdr381bHAfdSAYki16WXho8DhxMEh2PwlYXODUqYL9yEJOnrTgiGYwzE+z8e45MU8+PjwOEEwTHDQqgAGEFwQev/9ii1UDbWetH0qWPye1YZ6eeqeGHQon53FDOD5cpyaIDXNEIa4klENQuq5n6mEQVlHUcoMWwTkqh7vS7v1xAFOU7LWZPRNMzfeu1pX291OByOwYHnQZg7wbIKXo+qHKOi7EsxsqAFeebJZ6pCDPTzlZhy12juSgK5UFIyjij5Tt0oe/t3DVGOhbJcK74lqyEKnc09KOmoCcE4hrCX/wDCXu6DCNkA6+bQe2fMbeugw+FwOBwOh8PhcDj6xQJ2zBFNAhRpiMcGt/l/5g/5WqzD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOFKDp1p2nECTlNG9H3SeetnhcDgcDofD4XA4Bg/uQSg4xGtQNoeOizKistW9wCSAKUTlp+sAGu5NcDgcDicIjv4jBosBnApgjTmWAFgAYCWAkwEsAlDqwSX3AtgGYDuApwA8I//vAHCIiCa9VxwOh8MJgqM/SEJJxkDJjAdLBlis/V6gLL+tvznj7J4Eh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XA4HA6Hw+FwOBwOh8PhcDgcDofD4XDMDv8f+9v2d7P7t9QAAAAASUVORK5CYII="
_pulse_logo_cache = {}

def _pulse_logo(target_h):
    """Logo PULSE (RGBA) redimensionné à la hauteur target_h (px), mis en cache."""
    target_h = max(1, int(target_h))
    logo = _pulse_logo_cache.get(target_h)
    if logo is None:
        import io, base64
        from PIL import Image
        base = Image.open(io.BytesIO(base64.b64decode(_PULSE_LOGO_B64))).convert("RGBA")
        w = max(1, round(base.width * target_h / base.height))
        logo = base.resize((w, target_h), Image.LANCZOS)
        _pulse_logo_cache[target_h] = logo
    return logo

def paste_pulse_logo(img, x, y, target_h, opacity=1.0):
    """Colle le logo PULSE sur `img` à (x, y), à la hauteur target_h.
    Renvoie la largeur du logo posé, ou 0 si échec (le caller retombe alors
    sur le texte). `opacity` (0..1) sert au fondu dans les vidéos."""
    try:
        logo = _pulse_logo(target_h)
        if opacity < 0.999:
            op = max(0.0, min(1.0, opacity))
            a = logo.getchannel("A").point(lambda p: int(p * op))
            tmp = logo.copy(); tmp.putalpha(a); logo = tmp
        if img.mode == "RGBA":
            img.alpha_composite(logo, (int(x), int(y)))
        else:
            img.paste(logo, (int(x), int(y)), logo)
        return logo.width
    except Exception:
        return 0

def _feather_paste(bg, fg, x, y, frac=0.10):
    """Colle fg sur bg avec un FONDU UNIFORME sur les QUATRE bords (pas seulement les coins).
    Chaque bord de la photo se dissout de la même façon dans le fond → plus de bordure nette,
    et pas d'effet « coins flous / bords nets » (l'ancien flou de rectangle effaçait trop les coins)."""
    import numpy as _np
    fw, fh = fg.size
    feather = max(30, int(min(fw, fh) * frac))
    ys, xs = _np.mgrid[0:fh, 0:fw]
    # distance de chaque pixel au bord le plus proche → dégradé identique sur les 4 côtés
    dist = _np.minimum(_np.minimum(xs, fw - 1 - xs), _np.minimum(ys, fh - 1 - ys)).astype("float32")
    alpha = _np.clip(dist / feather, 0.0, 1.0)          # 0 au bord → 1 à 'feather' vers l'intérieur
    mask = Image.fromarray((alpha * 255.0).astype("uint8"), "L")
    mask = mask.filter(ImageFilter.GaussianBlur(feather * 0.35))   # léger adoucissement du dégradé
    bg.paste(fg, (x, y), mask)
    return bg

# ── PILULES-CATÉGORIES PRÉ-DESSINÉES (image fournie par l'utilisateur) ──────────
# Le bot découpe la pilule dans 'pulse_pills.png' (une seule image à uploader dans le repo).
# Coordonnées de chaque pilule dans l'image. Catégories absentes → le bot dessine la sienne.
_PILL_COORDS = {
    "politique": (79, 66, 507, 172),    "science": (548, 66, 967, 172),     "faitsdivers": (1004, 66, 1446, 172),
    "culture": (79, 207, 507, 313),     "environnement": (548, 207, 966, 313), "sport": (1005, 207, 1445, 313),
    "positivity": (79, 347, 506, 451),  "positif": (79, 347, 506, 451),
    "economie": (548, 347, 967, 451),   "tech": (1005, 347, 1445, 451),     "technologie": (1005, 347, 1445, 451),
    "breaking": (79, 486, 505, 591),    "france": (548, 486, 966, 591),     "monde": (1005, 486, 1446, 591),
    "societe": (78, 622, 505, 725),     "hommage": (549, 622, 966, 725),    "histoire": (1005, 622, 1445, 725),
    "sante": (79, 756, 505, 858),       "ia": (549, 756, 966, 858),         "insolite": (1005, 756, 1445, 858),
    "gta6": (549, 885, 966, 984),
}
# ── PILULES ANIMÉES (GIF) — utilisées sur les VIDÉOS ────────────────────────────
# Chaque catégorie a son GIF (660×135, 26 images, ~3,15 s, fond transparent), déposé dans
# 'pills/'. Sur une vidéo, la pilule STATIQUE n'est pas dessinée : le GIF est superposé par
# ffmpeg au même emplacement, en boucle. Si le GIF manque, on retombe sur la pilule statique.
_PILL_GIF_MAP = {
    "politique": "politique.gif",   "science": "science.gif",       "faitsdivers": "faits-divers.gif",
    "culture": "culture.gif",       "environnement": "environnement.gif", "sport": "sport.gif",
    "positivity": "positif.gif",    "positif": "positif.gif",       "economie": "economie.gif",
    "tech": "technologie.gif",      "technologie": "technologie.gif",
    "breaking": "urgent.gif",       "urgent": "urgent.gif",         "france": "france.gif",
    "monde": "monde.gif",           "societe": "societe.gif",       "hommage": "hommage.gif",
    "histoire": "histoire.gif",     "sante": "sante.gif",           "ia": "ia.gif",
    "insolite": "insolite.gif",     "gta6": "gta-6.gif",
}
_PILL_GIF_DIRS = ("pills", "assets/pills", "assets", ".")

def _pill_gif_path(category):
    """Chemin du GIF de la catégorie, ou None. Jamais d'erreur."""
    try:
        name = _PILL_GIF_MAP.get((category or "").lower())
        if not name:
            return None
        for d in _PILL_GIF_DIRS:
            fp = os.path.join(d, name)
            if os.path.exists(fp):
                return fp
    except Exception:
        pass
    return None

def _overlay_animated_pill(video_path, category, W, H, tmpdir, until=None):
    """Superpose la pilule ANIMÉE sur une vidéo déjà rendue, au même emplacement que la
    pilule statique (haut-droite, hauteur 0.052×H). `until` = secondes pendant lesquelles
    elle reste affichée (None = toute la vidéo).
    🛡️ Tolérant : au moindre problème, renvoie la vidéo d'origine — jamais d'échec de publication."""
    gif = _pill_gif_path(category)
    if not gif or not video_path or not os.path.exists(video_path):
        return video_path
    try:
        with Image.open(gif) as g:
            gw, gh = g.size
        ph = max(2, int(H * 0.052))
        pw = max(2, int(round(ph * gw / gh)))
        pw += pw % 2; ph += ph % 2                      # dimensions paires (exigence h264)
        margin = int(W * 0.037)
        x, y = W - pw - margin, int(H * 0.048)
        out = os.path.join(tmpdir, "pill_" + os.path.basename(video_path))
        enable = f":enable='lte(t,{float(until):.2f})'" if until else ""
        import imageio_ffmpeg as _iff, subprocess as _sp
        ff = _iff.get_ffmpeg_exe()
        # ⚠️ Le GIF est joué UNE FOIS (animation d'entrée) puis sa dernière image est FIGÉE
        #    (tpad clone). En boucle, la pilule disparaîtrait et se redessinerait toutes les
        #    3,15 s en plein milieu de la vidéo — effet parasite.
        r = _sp.run(
            [ff, "-y", "-loglevel", "error", "-i", video_path, "-i", gif,
             "-filter_complex",
             f"[1:v]scale={pw}:{ph}:flags=lanczos,tpad=stop_mode=clone:stop_duration=120[p];"
             f"[0:v][p]overlay={x}:{y}:shortest=1{enable}",
             "-map", "0:a?", "-c:a", "copy",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
            capture_output=True, timeout=300)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 10_000:
            print(f"  ✨ Pilule animée : {os.path.basename(gif)}")
            return out
        print(f"  ⚠️ Pilule animée ignorée (ffmpeg {r.returncode}) → pilule fixe conservée")
    except Exception as e:
        print(f"  ⚠️ Pilule animée ignorée : {e}")
    return video_path

_LOGO_GIF_NAME = "pulse-logo-animated.gif"

def _logo_gif_path():
    """Chemin du logo PULSE animé, ou None. Jamais d'erreur."""
    try:
        for d in _PILL_GIF_DIRS:
            fp = os.path.join(d, _LOGO_GIF_NAME)
            if os.path.exists(fp):
                return fp
    except Exception:
        pass
    return None

def _overlay_animated_logo(video_path, W, H, tmpdir, x, y, target_h, until=None):
    """Superpose le LOGO PULSE animé au même emplacement que le logo fixe.
    Le GIF est joué UNE FOIS puis figé (pas de rebouclage en plein milieu).
    🛡️ Tolérant : au moindre problème, renvoie la vidéo d'origine."""
    gif = _logo_gif_path()
    if not gif or not video_path or not os.path.exists(video_path):
        return video_path
    try:
        with Image.open(gif) as g:
            gw, gh = g.size
        lh = max(2, int(target_h))
        lw = max(2, int(round(lh * gw / gh)))
        lw += lw % 2; lh += lh % 2
        out = os.path.join(tmpdir, "logo_" + os.path.basename(video_path))
        enable = f":enable='lte(t,{float(until):.2f})'" if until else ""
        import imageio_ffmpeg as _iff, subprocess as _sp
        ff = _iff.get_ffmpeg_exe()
        r = _sp.run(
            [ff, "-y", "-loglevel", "error", "-i", video_path, "-i", gif,
             "-filter_complex",
             f"[1:v]scale={lw}:{lh}:flags=lanczos,tpad=stop_mode=clone:stop_duration=120[l];"
             f"[0:v][l]overlay={int(x)}:{int(y)}:shortest=1{enable}",
             "-map", "0:a?", "-c:a", "copy",
             "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
            capture_output=True, timeout=300)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 10_000:
            print("  ✨ Logo Pulse animé")
            return out
        print(f"  ⚠️ Logo animé ignoré (ffmpeg {r.returncode}) → logo fixe conservé")
    except Exception as e:
        print(f"  ⚠️ Logo animé ignoré : {e}")
    return video_path

_PILL_SHEET = None
_PILL_SHEET_TRIED = False
_PILL_PNG_CACHE = {}
def _category_pill(category, target_h):
    """Pilule pré-dessinée (RGBA) pour la catégorie, à la hauteur target_h — ou None.
    ① PNG individuel haute définition (pills/<cat>.png, 1320×222) — la meilleure qualité ;
    ② repli sur la planche historique pulse_pills.png ;
    ③ sinon None → le bot dessine sa propre pastille. Tolérant : jamais d'erreur."""
    global _PILL_SHEET, _PILL_SHEET_TRIED
    # ① PNG individuel (même nommage que les GIF animés : une seule table de vérité)
    try:
        name = _PILL_GIF_MAP.get((category or "").lower())
        if name:
            png = name[:-4] + ".png"
            key = png
            # ⚠️ On ne met en cache QUE les succès : mémoriser un échec désactiverait la
            #    pastille pour tout le run si le dossier était momentanément illisible.
            src = _PILL_PNG_CACHE.get(key)
            if src is None:
                for d in _PILL_GIF_DIRS:
                    fp = os.path.join(d, png)
                    if os.path.exists(fp):
                        src = Image.open(fp).convert("RGBA")
                        _PILL_PNG_CACHE[key] = src
                        break
            if src is not None:
                w, h = src.size
                return src.resize((max(1, int(target_h * w / h)), int(target_h)), Image.LANCZOS)
    except Exception:
        pass
    box = _PILL_COORDS.get((category or "").lower())
    if not box:
        return None
    if not _PILL_SHEET_TRIED:
        _PILL_SHEET_TRIED = True
        for p in ("pulse_pills.png", "assets/pulse_pills.png", "pills_src.png"):
            try:
                _PILL_SHEET = Image.open(p).convert("RGBA"); break
            except Exception:
                _PILL_SHEET = None
    if _PILL_SHEET is None:
        return None
    try:
        x0, y0, x1, y1 = box
        pad = 10
        pill = _PILL_SHEET.crop((max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad))
        w, h = pill.size
        return pill.resize((max(1, int(target_h * w / h)), int(target_h)), Image.LANCZOS)
    except Exception:
        return None

_EMOJI_RX = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\U00002600-\U000027BF"
    "\U0001F900-\U0001F9FF\U00002190-\U000021FF\U00002B00-\U00002BFF\uFE0F\u200D\u20E3]"
)
_EMOJI_FONT_PATH = "__unset__"
def _emoji_image(ch, size):
    """Rend un emoji en image COULEUR (RGBA), hauteur ~size px. None si indisponible ou vide.
    Tolérant : ne lève jamais d'erreur (fallback = pas d'emoji, le reste de la carte est intact)."""
    global _EMOJI_FONT_PATH
    try:
        from PIL import Image, ImageDraw, ImageFont
        ch = (ch or "").strip()
        if not ch:
            return None
        if _EMOJI_FONT_PATH == "__unset__":
            import glob
            hits = (glob.glob("/usr/share/fonts/**/NotoColorEmoji.ttf", recursive=True) or
                    glob.glob("/usr/share/fonts/**/*ColorEmoji*.ttf", recursive=True) or
                    glob.glob("/usr/share/fonts/**/*emoji*.ttf", recursive=True))
            _EMOJI_FONT_PATH = hits[0] if hits else None
        if not _EMOJI_FONT_PATH:
            return None
        f = ImageFont.truetype(_EMOJI_FONT_PATH, 109)   # NotoColorEmoji : taille bitmap native
        canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((80, 80), ch, font=f, embedded_color=True, anchor="mm")
        bb = canvas.getbbox()
        if not bb:
            return None
        crop = canvas.crop(bb)
        sc = size / crop.height
        return crop.resize((max(1, int(crop.width * sc)), int(size)), Image.LANCZOS)
    except Exception:
        return None

def _paste_rounded_shadow(bg, fg, x, y, radius=None, shadow_blur=None, shadow_alpha=175):
    """Colle fg sur bg avec COINS ARRONDIS + ombre portée douce (look 'carte' moderne).
    L'ombre floue fond naturellement la photo dans le fond → plus de bordure nette visible."""
    try:
        fw, fh = fg.size
        if radius is None:
            radius = max(14, int(min(fw, fh) * 0.055))
        if shadow_blur is None:
            shadow_blur = max(12, int(min(fw, fh) * 0.05))
        pad = shadow_blur * 2 + 10
        sh = Image.new("RGBA", (fw + pad * 2, fh + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([pad, pad, pad + fw - 1, pad + fh - 1],
                                             radius=radius, fill=(0, 0, 0, shadow_alpha))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))
        bg = bg.convert("RGBA")
        bg.alpha_composite(sh, (x - pad, y - pad + int(shadow_blur * 0.35)))  # ombre décalée vers le bas
        mask = Image.new("L", (fw, fh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, fw - 1, fh - 1], radius=radius, fill=255)
        bg.paste(fg.convert("RGBA"), (x, y), mask)
        return bg.convert("RGB")
    except Exception:
        bg.paste(fg, (x, y))
        return bg.convert("RGB") if bg.mode != "RGB" else bg

CARD_MARGIN = 48   # 🔒 marge de sécurité CONSTANTE (px à l'échelle finale) sur tous les bords
_IMG_SS = 2   # super-résolution des cartes : 2× = texte/graphismes nets ("4K-like"), résiste à la compression X

def build_png(headline_court, source, category, photo_url=None, image_query=None, article_url=None, person=None, W=1200, H=675, prefetched=None, headline_bottom=False, reveal=1.0, ss=None, as_image=False, no_pill=False, no_logo=False):
    """
    PNG DA Pulse, taille paramétrable (W×H).
    - Paysage 1200×675 pour X/Facebook (défaut)
    - Portrait 1080×1350 (4:5) pour Instagram
    prefetched = (raw_bytes, has_real_photo) pour réutiliser une image déjà téléchargée.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import io

        _ss = _IMG_SS if ss is None else max(1, int(ss))
        W, H = int(W * _ss), int(H * _ss)   # super-résolution (1× pour les images de vidéo)
        s = STYLES[category]
        margin = CARD_MARGIN * _ss   # marge de sécurité CONSTANTE : 48 px sur tous les bords

        if len(headline_court) > 120:
            headline_court = headline_court[:118].rsplit(" ", 1)[0]

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
                src_ratio = src_w / src_h
                dst_ratio = W / H

                # Cadrage SÛR : on ne coupe JAMAIS le sujet. On teste d'abord un recadrage
                # "plein cadre" centré sur le visage ; on ne l'utilise QUE s'il ne risque pas
                # de manger la tête. Sinon, on affiche la photo ENTIÈRE sur un fond DA flouté
                # (dérivé de la photo) → aucune tête coupée, jamais, quel que soit le format.
                cover_scale = max(W / src_w, H / src_h)
                # À quel point faut-il agrandir pour remplir ? Au-delà d'un certain zoom sur une
                # photo de format très différent du cadre, le "plein cadre" coupe forcément trop.
                ratio_gap = max(dst_ratio / src_ratio, src_ratio / dst_ratio)
                face = None
                if has_real_photo:
                    try:
                        probe = photo.resize((int(src_w * cover_scale + 0.5),
                                              int(src_h * cover_scale + 0.5)), Image.LANCZOS)
                        face = detect_face_center(probe, par_ia=(category == "hommage"))
                    except Exception:
                        face = None

                use_cover = (ratio_gap <= 1.5) or (face is not None and ratio_gap <= 2.2)

                if use_cover:
                    # Plein cadre (comme avant) mais en s'appuyant sur le visage s'il est connu.
                    scale = cover_scale
                    new_w, new_h = int(src_w * scale + 0.5), int(src_h * scale + 0.5)
                    ph = photo.resize((new_w, new_h), Image.LANCZOS)
                    if scale < 1:
                        ph = ph.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
                    if face:
                        fcx, fcy = face
                        left = int(fcx - W / 2)
                        # 🙂 Le visage doit rester dans les 2/3 SUPÉRIEURS : le titre est en bas
                        #    et ne doit JAMAIS le recouvrir. On vise le visage vers 30 % de la
                        #    hauteur et on l'empêche de descendre dans la zone du titre.
                        top = int(fcy - H * 0.30)
                        top = max(top, int(fcy - H * 0.60))
                    else:
                        left = (new_w - W) // 2
                        if dst_ratio < 1 and src_ratio > 1.2:
                            top = int((new_h - H) * (0.28 if headline_bottom else 0.42))
                        else:
                            top = int((new_h - H) * 0.2)
                    left = max(0, min(left, new_w - W))
                    top  = max(0, min(top,  new_h - H))
                    ph = ph.crop((left, top, left + W, top + H))
                    alpha = 1.0 if has_real_photo else 0.80
                    img = Image.blend(Image.new('RGB', (W, H), (13, 13, 20)), ph, alpha=alpha)
                else:
                    # PHOTO ENTIÈRE sur fond flouté : on adapte le CADRE à la photo, pas l'inverse.
                    # 1) fond = la photo agrandie pour couvrir, très floutée + assombrie (DA Pulse)
                    bg = photo.resize((int(src_w * cover_scale + 0.5),
                                       int(src_h * cover_scale + 0.5)), Image.LANCZOS)
                    bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2,
                                  (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
                    bg = bg.filter(ImageFilter.GaussianBlur(28))
                    bg = Image.blend(bg, Image.new('RGB', (W, H), (13, 11, 30)), 0.5)
                    # 2) photo entière (contain) placée par-dessus, jamais rognée
                    #    (un peu plus haut si le titre est en bas, pour ne pas la coller au texte)
                    fit_scale = min(W / src_w, H / src_h) * 0.94
                    fw, fh = int(src_w * fit_scale), int(src_h * fit_scale)
                    fitted = photo.resize((fw, fh), Image.LANCZOS)
                    fx = (W - fw) // 2
                    fy = int((H - fh) * (0.34 if headline_bottom else 0.5))
                    img = _paste_rounded_shadow(bg, fitted, fx, fy)   # coins arrondis + ombre douce → transition naturelle
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
        if no_logo:
            pass    # un logo ANIMÉ sera superposé par ffmpeg au même emplacement
        elif category == "hommage" or paste_pulse_logo(img, margin, int(H * 0.044), int(H * 0.062)) == 0:
            draw.text((margin, int(H * 0.044)), "Pulse", font=f_logo, fill=(255, 255, 255))

        # ─── BADGE CATÉGORIE : pilule pré-dessinée si fournie, sinon dessin maison ───
        # no_pill : une pilule ANIMÉE sera superposée par ffmpeg → on ne dessine pas la fixe
        _pill = None if no_pill else _category_pill(category, int(H * 0.052))
        if no_pill:
            pass
        elif _pill is not None:
            px = W - _pill.width - margin
            py = int(H * 0.048)
            img = img.convert("RGBA")
            img.alpha_composite(_pill, (px, py))
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
        else:
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

            # 🌑 Dégradé NOIR du bas vers le haut : 0 % en haut de la bande → 75 % en bas.
            #    Garantit la lisibilité du titre sur n'importe quelle photo, sans écraser
            #    l'image (75 % laisse la photo respirer, contrairement à un aplat opaque).
            grad = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            band = int(H * 0.68)               # couvre les ~2/3 inférieurs
            for i in range(band):
                y = H - band + i
                t = i / band
                a = int(0.75 * 255 * (t ** 0.95))
                gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
            img = Image.alpha_composite(img.convert('RGBA'), grad).convert('RGB')
            draw = ImageDraw.Draw(img)

            # Titre : on retire les hashtags (inutiles/moches sur une image) et on
            # auto-dimensionne pour que RIEN ne déborde (titre court = très gros).
            clean_title = re.sub(r'#(\w+)', r'\1', headline_court)
            clean_title = _EMOJI_RX.sub('', clean_title)        # retire les emojis du TEXTE (plus de « tofu »)
            clean_title = re.sub(r'\s{2,}', ' ', clean_title).strip()
            # Largeur de la colonne de texte : pleine largeur en portrait ; en format LARGE (16:9),
            # on limite à ~74 % pour éviter des lignes interminables et garder la photo respirante.
            max_w = int(W * (0.74 if W / max(1, H) > 1.2 else 0.90))
            # ⚠️ La taille du titre se calcule sur la HAUTEUR, pas la largeur : sinon un cadre large
            #    (16:9) produit un texte énorme sur un cadre court. Ces fractions reproduisent
            #    EXACTEMENT les tailles historiques du portrait 1080×1350 (aucune régression).
            sizes = [int(H * x) for x in (0.0672, 0.0600, 0.0528, 0.0464, 0.0408, 0.0352)]

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
                # 🛡️ le bloc de titre ne doit JAMAIS occuper plus d'un tiers de la hauteur,
                #    quel que soit le format de la carte (portrait, carré ou 16:9).
                if len(lines) <= 3 and _all_words_fit(ft) and len(lines) * fsize * 1.14 <= H * 0.34:
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
            # 📐 Le bloc de titre est ANCRÉ PAR LE BAS, juste au-dessus de la source/date
            #    (le pied est à H*0.923). Ainsi la dernière ligne ne chevauche jamais le pied,
            #    quel que soit le nombre de lignes, et le titre reste toujours en bas de l'image.
            _foot_y = int(H - H * 0.077)             # même repère que la source/date
            _title_bottom = _foot_y - int(H * 0.035)  # marge de sécurité au-dessus du pied
            ty0 = _title_bottom - total_h
            # garde-fou haut : ne pas remonter au point de manger la moitié de l'image
            ty0 = max(ty0, int(H * 0.30))

            # ✍️ Animation : la mise en page est calculée sur le titre COMPLET (rien ne bouge),
            #    on n'affiche que les `reveal` premiers mots. reveal = 1.0 → carte normale.
            #    🌊 FLUIDITÉ : le mot en cours d'apparition entre en FONDU (alpha fractionnaire)
            #    au lieu de surgir d'un coup — la fraction décimale de (reveal × mots) = son alpha.
            _lines = chosen_lines
            _fade_word, _fade_alpha, _fade_line_idx = None, 0.0, -1
            if reveal < 1.0:
                _wtot = sum(len(l.split()) for l in chosen_lines)
                _prog = _wtot * max(0.0, reveal)
                _show = int(_prog)
                _fade_alpha = _prog - _show          # 0 → mot pas commencé ; 0.5 → à moitié fondu
                _lines, _n = [], 0
                for li, l in enumerate(chosen_lines):
                    _w = l.split()
                    _take = max(0, min(len(_w), _show - _n))
                    _lines.append(" ".join(_w[:_take]))
                    # le mot suivant (celui qui fond) est sur cette ligne ?
                    if _fade_word is None and _fade_alpha > 0.02 and _take < len(_w) and _n + _take == _show:
                        _fade_word, _fade_line_idx = _w[_take], li
                    _n += len(_w)

            # OMBRE PORTÉE DOUCE (floutée) au lieu d'un contour noir net
            shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            sdraw  = ImageDraw.Draw(shadow)
            ty = ty0
            for li, ln in enumerate(_lines):
                sdraw.text((margin + 4, ty + 6), ln, font=ft, fill=(0, 0, 0, 235))
                if li == _fade_line_idx and _fade_word:
                    _pre = ln + (" " if ln else "")
                    _fx = margin + 4 + int(sdraw.textlength(_pre, font=ft))
                    sdraw.text((_fx, ty + 6), _fade_word, font=ft,
                               fill=(0, 0, 0, int(235 * _fade_alpha)))
                ty += line_h
            shadow = shadow.filter(ImageFilter.GaussianBlur(12))
            # on densifie l'ombre en la compositant 2 fois (plus lisible)
            img = Image.alpha_composite(img.convert('RGBA'), shadow)
            img = Image.alpha_composite(img, shadow).convert('RGB')
            draw = ImageDraw.Draw(img)

            # Texte blanc net par-dessus (sans contour) — le mot en cours en alpha fractionnaire
            _txt_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            _tdraw = ImageDraw.Draw(_txt_layer)
            ty = ty0
            last_y = ty0
            for li, ln in enumerate(_lines):
                _tdraw.text((margin, ty), ln, font=ft, fill=(255, 255, 255, 255))
                if li == _fade_line_idx and _fade_word:
                    _pre = ln + (" " if ln else "")
                    _fx = margin + int(_tdraw.textlength(_pre, font=ft))
                    _tdraw.text((_fx, ty), _fade_word, font=ft,
                                fill=(255, 255, 255, int(255 * _fade_alpha)))
                last_y = ty
                ty += line_h
            img = Image.alpha_composite(img.convert('RGBA'), _txt_layer).convert('RGB')
            draw = ImageDraw.Draw(img)
            # (Plus d'emoji en fin de titre : la pastille de catégorie porte déjà son icône.)
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
                if len(lines) <= 3:
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

        if as_image:
            return img.convert('RGB')
        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=95, optimize=True, progressive=True)
        return buf.getvalue(), f"pulse-{category}-{now.strftime('%d%m%Y-%H%M')}.jpg"

    except Exception as e:
        print(f"  ⚠️ PNG erreur: {e}")
        return None if as_image else (None, None)

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
def post_to_twitter(tweet_text, png_bytes=None, video_path=None, reply_to_id=None, png_list=None):
    """Poste sur X avec vidéo MP4 (prioritaire), plusieurs images, ou une seule.
    `png_list` : jusqu'à 4 images publiées ENSEMBLE (carrousel). X n'en accepte pas plus.
    reply_to_id → poste en réponse (fil)."""
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
        if not video_path and png_list:
            # 🖼️ Carrousel : X accepte 4 médias maximum par publication.
            try:
                ids = []
                for i, p in enumerate(png_list[:4]):
                    if not p:
                        continue
                    m = api_v1.media_upload(filename=f"pulse_{i+1}.png", file=io.BytesIO(p))
                    ids.append(m.media_id)
                if ids:
                    media_ids = ids
                    print(f"  🖼️ Carrousel : {len(ids)} images publiées ensemble")
            except Exception as e:
                print(f"  ⚠️ Carrousel échoué ({str(e)[:70]}) → image seule")
                media_ids = None
        if not video_path and not media_ids and png_bytes:
            try:
                media = api_v1.media_upload(filename="pulse.png", file=io.BytesIO(png_bytes))
                media_ids = [media.media_id]
            except Exception as e:
                print(f"  ⚠️ Upload image X échoué : {e}")
        response = client_v2.create_tweet(text=tweet_text, media_ids=media_ids, in_reply_to_tweet_id=reply_to_id) if reply_to_id \
                   else client_v2.create_tweet(text=tweet_text, media_ids=media_ids)
        tweet_id = response.data.get("id")
        url      = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None
        if url:
            media_type = "🎬 vidéo" if video_path else ("🖼️ image" if png_bytes else "📝 texte seul")
            print(f"  🐦 Posté sur X ({media_type}) : {url}")
        return url
    except Exception as e:
        print(f"  ❌ Post X échoué : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# 📊 DATA CARDS — 2ᵉ tweet avec un graphique de vraie donnée, en rapport avec l'article
#    Pilote : ÉCONOMIE. Données VÉRIFIÉES et FIGÉES (rafraîchies ~1×/an), chacune sourcée.
#    Éteint par défaut : passer STAT_CARDS_ENABLED à True APRÈS avoir vérifié les chiffres.
# ═══════════════════════════════════════════════════════════════════════════
STAT_CARDS_ENABLED = True           # ✅ activé — données vérifiées contre l'INSEE (juillet 2026)
STAT_COOLDOWN_DAYS = 7              # un même graphique ne ressort pas avant N jours

# Sujets SENSIBLES : jamais de graphique auto (sobriété + éthique). Filet de sécurité réutilisable.
_STAT_EXCLUDE_RX = re.compile(
    r"viol|agression sexuelle|p[ée]docrimin|p[ée]dophil|inceste|mineur|enfant|"
    r"f[ée]minicide|attentat|terroris|meurtre|homicide|tuerie|fusillade|d[ée]c[èe]s|mort",
    re.I)

# Chaque série : vraie donnée annuelle officielle. 'dec' = décimales à afficher, 'unit' = suffixe.
STAT_SERIES = {
    "chomage": {
        "kicker": "ÉCONOMIE",
        "title": "Le taux de chômage en France depuis 2015",
        "years":  [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
        "values": [10.4, 10.1,  9.4,  9.0,  8.4,  8.0,  7.9,  7.3,  7.4,  7.4],
        "unit": "%", "dec": 1,
        "source": "INSEE, enquête Emploi (taux de chômage BIT, moyenne annuelle)",
        "caption": "Le chômage en France sur dix ans, en données officielles.",
        "hashtag": "#chômage",
        "match": re.compile(r"\bch[ôo]mage\b|ch[ôo]meur|demandeur[s]? d.emploi|"
                            r"france travail|p[ôo]le emploi|plein[- ]emploi|taux d.emploi", re.I),
    },
    "inflation": {
        "kicker": "ÉCONOMIE",
        "title": "L'inflation en France depuis 2015",
        "years":  [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
        "values": [ 0.0,  0.2,  1.0,  1.8,  1.1,  0.5,  1.6,  5.2,  4.9,  2.0],
        "unit": "%", "dec": 1,
        "source": "INSEE, indice des prix à la consommation (moyenne annuelle)",
        "caption": "L'inflation en France sur dix ans, en données officielles.",
        "hashtag": "#inflation",
        "match": re.compile(r"\binflation\b|prix à la consommation|hausse des prix|"
                            r"indice des prix|pouvoir d.achat|vie ch[èe]re|co[ûu]t de la vie", re.I),
    },
    "dette": {
        "kicker": "ÉCONOMIE",
        "title": "La dette publique de la France (% du PIB)",
        "years":  [2015, 2016, 2017, 2018, 2019, 2020,  2021,  2022,  2023,  2024],
        "values": [95.6, 98.0, 98.4, 98.0, 97.4, 114.7, 112.8, 111.4, 109.5, 112.6],
        "unit": "%", "dec": 0,
        "source": "INSEE / Eurostat, dette publique au sens de Maastricht (% du PIB)",
        "caption": "La dette publique française, en % du PIB.",
        "hashtag": "#dettepublique",
        "match": re.compile(r"dette publique|dette de la france|dette de l.[ée]tat|"
                            r"endettement|milliards de dette|3\s?000 milliards", re.I),
    },
}

def match_stat_topic(title, summary):
    """Renvoie la clé de série éco pertinente pour cet article, ou None.
    Conservateur : mieux vaut aucun graphique qu'un graphique hors-sujet.
    Aucun graphique sur un sujet sensible (filtre d'exclusion)."""
    t = ((title or "") + " " + (summary or "")).strip()
    if not t:
        return None
    if _STAT_EXCLUDE_RX.search(t):
        return None
    for key in ("dette", "inflation", "chomage"):   # ordre de priorité
        if STAT_SERIES[key]["match"].search(t):
            return key
    return None

def _nice_bounds(vmin, vmax):
    """Bornes + pas d'axe 'ronds' pour ~4-6 lignes de grille."""
    span = max(vmax - vmin, 1e-6)
    raw = span / 4.0
    import math as _m
    mag = 10 ** _m.floor(_m.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag; break
    else:
        step = 10 * mag
    lo = _m.floor((vmin - step * 0.4) / step) * step
    hi = _m.ceil((vmax + step * 0.4) / step) * step
    return lo, hi, step

def render_stat_chart(key):
    """Génère le PNG (bytes) d'une data card à partir d'une série vérifiée. Rendu pro (sur-échantillonnage ×3)."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as _np, io as _io, math as _m
    s = STAT_SERIES[key]
    years, values = s["years"], s["values"]
    S = 3; W = H = 1080; CW, CH = W*S, H*S
    TL, BR = (43,12,82), (7,20,66)
    CA, CB = (176,38,255), (255,45,149)
    WHITE = (255,255,255)

    def fnt(px, bold=True):
        for p in (f"/usr/share/fonts/truetype/noto/NotoSans-{'Bold' if bold else 'Regular'}.ttf",
                  f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"):
            try: return ImageFont.truetype(p, px)
            except Exception: pass
        return ImageFont.load_default()

    def fmt(v):
        return (f"{v:.{s['dec']}f}".replace(".", ",")) + (" " + s["unit"] if s["unit"] else "")

    # fond dégradé diagonal (numpy = rapide et propre)
    xs = _np.linspace(0, 1, CW); ys = _np.linspace(0, 1, CH)
    tx, ty = _np.meshgrid(xs, ys); tt = (tx + ty) / 2.0
    arr = _np.stack([TL[i] + (BR[i]-TL[i]) * tt for i in range(3)], axis=-1).astype("uint8")
    img = Image.fromarray(arr, "RGB").convert("RGBA")
    d = ImageDraw.Draw(img)

    m_left, m_right, m_top, m_bot = 150*S, 185*S, 430*S, 200*S
    px0, py0, px1, py1 = m_left, CH-m_bot, CW-m_right, m_top
    vmin, vmax, step = _nice_bounds(min(values), max(values))
    def X(i): return px0 + (px1-px0) * i/(len(years)-1)
    def Y(v): return py0 + (py1-py0) * (v-vmin)/(vmax-vmin)

    # grille + labels Y
    fg = fnt(30*S, bold=False)
    g = vmin
    while g <= vmax + 1e-6:
        yy = Y(g)
        d.line([(px0, yy), (px1, yy)], fill=(255,255,255,32), width=max(1, S))
        d.text((px0-18*S, yy), fmt(g), font=fg, fill=(255,255,255,150), anchor="rm")
        g += step

    # courbe lissée (Catmull-Rom) + halo
    pts = [(X(i), Y(v)) for i, v in enumerate(values)]
    p = _np.array(pts, float); p = _np.vstack([p[0], p, p[-1]]); curve = []
    for i in range(1, len(p)-2):
        p0, p1, p2, p3 = p[i-1], p[i], p[i+1], p[i+2]
        for u in range(30):
            t = u/30.0; t2, t3 = t*t, t*t*t
            curve.append((
                0.5*((2*p1[0])+(-p0[0]+p2[0])*t+(2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2+(-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3),
                0.5*((2*p1[1])+(-p0[1]+p2[1])*t+(2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2+(-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)))
    curve.append(tuple(p[-2]))
    wpx = 10*S
    glow = Image.new("RGBA", img.size, (0,0,0,0)); gd = ImageDraw.Draw(glow)
    for i in range(len(curve)-1):
        gd.line([curve[i], curve[i+1]], fill=(255,80,180,120), width=wpx*3)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(wpx*1.5)))
    r = wpx//2
    for i in range(len(curve)-1):
        c = tuple(int(CA[j] + (CB[j]-CA[j]) * i/(len(curve)-1)) for j in range(3)) + (255,)
        d.line([curve[i], curve[i+1]], fill=c, width=wpx)
        d.ellipse([curve[i][0]-r, curve[i][1]-r, curve[i][0]+r, curve[i][1]+r], fill=c)

    # labels X (années)
    fx = fnt(30*S, bold=True)
    for i, yr in enumerate(years):
        d.text((X(i), py0+26*S), str(yr), font=fx, fill=(255,255,255,190), anchor="mt")

    # points début / fin + valeurs
    x0, y0v = X(0), Y(values[0]); xN, yN = X(len(years)-1), Y(values[-1])
    d.ellipse([x0-12*S, y0v-12*S, x0+12*S, y0v+12*S], fill=WHITE)
    d.text((x0, y0v-26*S), fmt(values[0]), font=fnt(38*S), fill=(255,255,255,230), anchor="mb")
    d.ellipse([xN-15*S, yN-15*S, xN+15*S, yN+15*S], fill=WHITE)
    d.text((xN+26*S, yN), fmt(values[-1]), font=fnt(54*S), fill=WHITE, anchor="lm")

    # titre (kicker + titre auto-ajusté sur la largeur)
    d.text((150*S, 130*S), s["kicker"], font=fnt(34*S), fill=(255,90,170,255))
    tf = fnt(60*S); avail = CW - 150*S - 70*S; words = s["title"].split(); lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=tf) <= avail: cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    yy = 190*S
    for line in lines:
        d.text((150*S, yy), line, font=tf, fill=WHITE); yy += 76*S

    # pied : source + handle
    d.text((150*S, CH-118*S), "Source : " + s["source"], font=fnt(24*S, bold=False), fill=(255,255,255,140))
    d.text((CW-90*S, CH-70*S), "@PULSEactus", font=fnt(40*S), fill=(255,255,255,230), anchor="rm")

    out = _io.BytesIO()
    img.convert("RGB").resize((W, H), Image.LANCZOS).save(out, format="PNG", quality=95)
    return out.getvalue()

def _stat_ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS stat_posted (series TEXT, posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

def stat_recently_posted(conn, key, days=STAT_COOLDOWN_DAYS):
    _stat_ensure(conn)
    row = conn.execute("SELECT COUNT(*) FROM stat_posted WHERE series=? AND posted_at >= datetime('now', ?)",
                       (key, f"-{days} days")).fetchone()
    return bool(row and row[0])

def stat_mark_posted(conn, key):
    _stat_ensure(conn)
    conn.execute("INSERT INTO stat_posted (series) VALUES (?)", (key,))
    conn.commit()

def _tweet_id_from_url(url):
    try:
        tid = str(url).rstrip("/").split("/")[-1]
        return tid if tid.isdigit() else None
    except Exception:
        return None

def post_stat_followup(conn, item, main_x_url):
    """Si l'article touche un thème éco couvert, poste un 2ᵉ tweet (graphique) EN RÉPONSE au tweet principal.
    Entièrement isolé : n'interrompt jamais le flux principal. Éteint tant que STAT_CARDS_ENABLED est False."""
    try:
        if not STAT_CARDS_ENABLED:
            return
        tid = _tweet_id_from_url(main_x_url)
        if not tid:
            return
        key = match_stat_topic(item.get("title", ""), item.get("summary", ""))
        if not key:
            return
        if stat_recently_posted(conn, key):
            print(f"  📊 Graphique '{key}' déjà posté récemment → pas de doublon")
            return
        s = STAT_SERIES[key]
        png = render_stat_chart(key)
        caption = f"📊 Le contexte en données 👇\n\n{s['caption']}\n\nSource : {s['source'].split('(')[0].strip()} {s['hashtag']}"
        url = post_to_twitter(caption[:275], png_bytes=png, reply_to_id=tid)
        if url:
            stat_mark_posted(conn, key)
            print(f"  📊 Graphique éco publié en réponse ({key}) : {url}")
    except Exception as e:
        print(f"  ⚠️ Data card ignorée (isolée) : {e}")

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
                # 🖼️ Image de la page Wikipédia de l'événement (libre de droits) : la vraie
                #    illustration d'époque, jamais une image de stock hors-sujet.
                img = None
                titre_page = None
                for pg in pages:
                    src = (pg.get("originalimage") or {}).get("source") or \
                          (pg.get("thumbnail") or {}).get("source")
                    if src and img is None:
                        img = src
                    if titre_page is None:
                        titre_page = pg.get("normalizedtitle") or pg.get("title")
                    if img:
                        break
                clean.append({"year": year, "text": text, "img": img, "page": titre_page})
        return clean
    except Exception as e:
        print(f"  ⚠️ Wikipedia: {e}")
        return []

# ═══════════════════════════════════════════════════════════════════════════
# 🎮 RUBRIQUE GTA VI — mini-rédaction spécialisée (temporaire, jusqu'à fin déc. 2026)
# Principe : on ne PUBLIE QUE ce qui vient d'un VRAI article identifiable (jamais d'invention).
# Chaque info reçoit un NIVEAU DE FIABILITÉ (1 officiel → 4 spéculation, rejetée) et une
# CATÉGORIE (map, gameplay, personnages...) pour varier les sujets jour après jour.
# ═══════════════════════════════════════════════════════════════════════════
GTA6_RELEASE = datetime(2026, 11, 19)
GTA6_END     = datetime(2027, 1, 1)     # rubrique désactivée automatiquement après fin déc. 2026

GTA6_RX = re.compile(r"\bgta\s?(6|vi)\b|grand theft auto\s*(6|vi)\b", re.I)

# Niveau 1 : annonce officielle Rockstar (priorité maximale)
_GTA6_L1_RX = re.compile(
    r"rockstar\s*(games)?\s*(annonce|confirme|dévoile|devoile|publie|officialise|présente)"
    r"|communiqué officiel|officialise|bande[-\s]?annonce officielle|nouveau trailer|trailer\s*\d"
    r"|date de sortie (officielle|confirmée)|report(é|e)\b|repouss|sortie repouss", re.I)
# Niveau 2 : information fortement crédible (fichiers, recoupements, déclarations confirmées)
_GTA6_L2_RX = re.compile(
    r"datamin|fichiers? du jeu|code du jeu|dataminer|recoup|plusieurs médias|confirmé par"
    r"|acteur|actrice|doubleur|doubleuse|fiche (produit|store)|page (store|steam|psn)"
    r"|précommande|precommande|configuration requise|specs?\b", re.I)
# Niveau 3 : rumeur crédible (à étiqueter comme telle)
_GTA6_L3_RX = re.compile(
    r"rumeur|leak|fuite|insider|selon (des|les|un)|théorie|theorie|spécul|specul"
    r"|aurait|serait|pourrait|d'après (des|les)", re.I)
# Contenus sans valeur éditoriale (guides, récaps) → écartés
_GTA6_SKIP_RX = re.compile(r"tout (ce qu'on sait|savoir)|on fait le point|récap|recap|\bguide\b|soldes|promo", re.I)

# Catégories de la rubrique (clé, emoji, libellé, détection) — sert à VARIER les sujets
GTA6_CATEGORIES = [
    ("map",        "🏙️", "Carte et environnement", r"\bmap\b|carte|leonida|monde ouvert|open world|ville|zone|easter egg"),
    ("viceCity",   "🌴", "Vice City",              r"vice ?city"),
    ("vehicules",  "🚗", "Véhicules",              r"véhicule|vehicule|voiture|bagnole|moto|avion|bateau|conduite"),
    ("gameplay",   "🎮", "Gameplay",               r"gameplay|jouabilité|mécanique|missions?|braquage|activités|pnj|\bia\b"),
    ("perso",      "👥", "Personnages",            r"personnage|protagoniste|jason|lucia|héros|antagoniste"),
    ("histoire",   "📖", "Histoire",               r"histoire|scénario|scenario|intrigue|narration"),
    ("economie",   "💰", "Économie du jeu",        r"économie|argent in-?game|microtransaction|shark card|monnaie"),
    ("armes",      "🔫", "Armes",                  r"\barmes?\b|fusil|pistolet|arsenal"),
    ("rockstar",   "🏢", "Rockstar Games",         r"rockstar|take[-\s]?two|houser"),
    ("date",       "📅", "Date de sortie",         r"date de sortie|sortie du jeu|report|repouss|lancement"),
    ("dev",        "🛠️", "Développement",          r"développement|developpement|studio|crunch|production"),
    ("screens",    "📸", "Nouveaux screenshots",   r"screenshot|capture|image officielle|artwork"),
    ("trailer",    "🎬", "Trailers",               r"trailer|bande[-\s]?annonce"),
    ("bo",         "🎵", "Bande-son",              r"bande[-\s]?son|musique|radio|soundtrack|ost"),
    ("communaute", "🔍", "Découvertes communauté", r"communauté|fans|joueurs ont|découverte|décrypt"),
    ("technique",  "⚙️", "Aspects techniques",     r"technique|fps|60 ?fps|résolution|moteur|rage\b|performances?"),
    ("graphismes", "🎨", "Graphismes",             r"graphisme|réalisme|realisme|visuel|ray[-\s]?tracing"),
    ("editions",   "📦", "Éditions du jeu",        r"édition|edition collector|bonus de précommande|steelbook"),
]

def _gta6_level(title, summary, source):
    """Niveau de fiabilité : 1 = officiel Rockstar, 2 = fortement crédible, 3 = rumeur crédible,
    4 = spéculation sans élément solide (NE DOIT PAS être publiée)."""
    t = f"{title} {summary}"
    if _GTA6_SKIP_RX.search(title or ""):
        return 4
    if "rockstar" in (source or "").lower() or _GTA6_L1_RX.search(t):
        return 1
    if _GTA6_L2_RX.search(t):
        return 2
    if _GTA6_L3_RX.search(t):
        return 3
    return 4

def _gta6_category(title, summary):
    """Classe l'info dans une catégorie de la rubrique (pour varier les sujets)."""
    t = f"{title} {summary}".lower()
    for key, emoji, label, rx in GTA6_CATEGORIES:
        if re.search(rx, t):
            return key, emoji, label
    return "actu", "🎮", "Actualité GTA 6"

def _gta6_recent_cats(conn, days=2):
    """Catégories GTA 6 déjà publiées ces derniers jours (pour ne pas se répéter)."""
    rows = conn.execute(
        "SELECT keywords FROM special_log WHERE kind='gta6' AND sent_at > datetime('now', ?)",
        (f"-{days} days",)).fetchall()
    cats = set()
    for r in rows:
        m = re.search(r"cat:(\w+)", r[0] or "")
        if m:
            cats.add(m.group(1))
    return cats

def gen_gta6_hype(conn, candidates=None):
    """🎮 Rubrique GTA VI : mini-rédaction spécialisée, ancrée sur de VRAIS articles.
    Sélectionne l'info la plus fiable/variée du moment (niveaux 1-3 ; niveau 4 rejeté),
    la rédige comme un journaliste spécialisé, et garantit une image. Max 2/jour.
    Une annonce OFFICIELLE Rockstar (niveau 1) est prioritaire et passe hors cadence.
    Renvoie un item spécial ou None (le silence vaut mieux qu'une info sans valeur)."""
    KIND = "gta6"
    MAX_PAR_JOUR = 2
    if datetime.now() >= GTA6_END:          # rubrique désactivée automatiquement après fin déc. 2026
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    deja = conn.execute("SELECT COUNT(*) FROM special_log WHERE kind=? AND sent_at LIKE ?",
                        (KIND, f"{today}%")).fetchone()[0]
    if deja >= MAX_PAR_JOUR:
        return None

    # ── 1. On ne travaille QUE sur de vrais articles GTA 6 des flux (jamais d'invention) ──
    pool = []
    for c in (candidates or []):
        blob = f"{c.get('title','')} {c.get('summary','')}"
        if not GTA6_RX.search(blob):
            continue
        lvl = _gta6_level(c.get("title", ""), c.get("summary", ""), c.get("source", ""))
        if lvl >= 4:                        # spéculation sans élément solide → jamais publiée
            continue
        key, emoji, label = _gta6_category(c.get("title", ""), c.get("summary", ""))
        pool.append({"c": c, "level": lvl, "cat": key, "emoji": emoji, "label": label})
    if not pool:
        return None                          # rien de solide aujourd'hui → on ne publie rien

    # ── 2. Anti-répétition : on écarte un sujet déjà traité récemment ──
    recents = recent_special_topics(conn, KIND, days=10)
    recent_sigs = [_sig_words(t) for t in recents if t]
    fresh = []
    for p in pool:
        sw = _sig_words(p["c"].get("title", ""))
        if len(sw) >= 2 and any(len(sw & rs) >= 2 for rs in recent_sigs):
            continue
        fresh.append(p)
    pool = fresh or pool

    # ── 3. Priorité : fiabilité d'abord, puis VARIÉTÉ (catégorie pas vue ces 2 jours) ──
    recent_cats = _gta6_recent_cats(conn, days=2)
    pool.sort(key=lambda p: (p["level"], 1 if p["cat"] in recent_cats else 0))
    best = pool[0]
    level, art = best["level"], best["c"]

    # ── 4. Cadence : l'officiel (niveau 1) est prioritaire et passe hors cadence ──
    if level > 1:
        last_gta = conn.execute("SELECT sent_at FROM special_log WHERE kind=? ORDER BY sent_at DESC LIMIT 1",
                                (KIND,)).fetchone()
        if last_gta:
            try:
                from datetime import datetime as _dt
                if (datetime.now() - _dt.fromisoformat(last_gta[0])).total_seconds() / 3600 < 5:
                    return None              # au moins 5h entre deux tweets GTA 6
            except Exception:
                pass
        import random as _rr
        if _rr.random() > 0.35:              # parcimonie : on ne tente pas à chaque run (coût API)
            return None

    # ── 5. Conscience de la date (avant / semaine de sortie / après) ──
    jours = (GTA6_RELEASE - datetime.now()).days
    if jours > 7:
        temporalite = (f"Nous sommes à J-{jours} de la sortie (19 nov. 2026). Parle au FUTUR : "
                       f"la hype monte, les fans attendent.")
    elif jours >= 0:
        temporalite = f"C'EST LA SEMAINE DE SORTIE (J-{jours} !). Ton d'excitation, la sortie est imminente."
    else:
        temporalite = (f"Le jeu est SORTI depuis {abs(jours)} jour(s). Parle au PASSÉ/PRÉSENT : ce que les "
                       f"joueurs DÉCOUVRENT. NE dis JAMAIS que le jeu 'va sortir' : il est déjà là.")

    # ── 6. Consignes de rédaction adaptées au NIVEAU DE FIABILITÉ ──
    if level == 1:
        fiabilite = ("NIVEAU 1 — ANNONCE OFFICIELLE ROCKSTAR. C'est un FAIT confirmé. Écris-le comme une "
                     "news factuelle et forte. N'emploie AUCUN mot de rumeur (pas de 'théorie', "
                     "'rumeur', 'il se pourrait'). Ne mets AUCUN avertissement de non-confirmation.")
        longueur = "Longueur : 200 à 400 caractères. Direct, percutant."
    elif level == 2:
        fiabilite = ("NIVEAU 2 — INFORMATION FORTEMENT CRÉDIBLE (fichiers du jeu, recoupements, déclaration "
                     "confirmée). Présente-la comme sérieuse mais NON officielle : précise d'où elle vient "
                     "(ex : 'repéré dans les fichiers du jeu', 'confirmé par l'acteur'). Pas de 'Rockstar confirme' "
                     "si Rockstar n'a rien dit.")
        longueur = "Longueur : 250 à 450 caractères."
    else:
        fiabilite = ("NIVEAU 3 — RUMEUR CRÉDIBLE. Elle DOIT être présentée comme une rumeur non confirmée. "
                     "Marqueurs obligatoires : 'Rumeur :', 'Selon...', 'Ce ne serait qu'une théorie', "
                     "'non confirmé'. N'affirme JAMAIS la rumeur comme un fait. Ne prétends jamais que "
                     "Rockstar a confirmé quoi que ce soit.")
        longueur = "Longueur : 350 à 550 caractères (explique la rumeur : d'où elle vient, ce qu'elle avance, pourquoi les fans y croient)."

    try:
        result = _llm_json(f"""Tu es le journaliste de Pulse spécialisé GTA VI (sortie le 19 NOVEMBRE 2026, PS5 / Xbox Series X|S).
Tu écris en français, pour une communauté de fans passionnés.

⏰ CONTEXTE TEMPOREL : {temporalite}

📰 L'ARTICLE (ta SEULE source — n'invente RIEN, n'ajoute aucun fait absent d'ici) :
- Titre : {art.get('title','')}
- Résumé : {art.get('summary','')}
- Média : {art.get('source','')}
- Catégorie de la rubrique : {best['emoji']} {best['label']}

🔎 FIABILITÉ — {fiabilite}

MISSION : ne résume pas l'article, réponds à « pourquoi cette info intéresse les fans de GTA VI ? ».
Apporte du contexte, un rappel ou une conséquence quand c'est pertinent et VRAI.

STYLE :
- 🎮 (ou {best['emoji']}) en tête. 1ʳᵉ ligne = accroche COURTE qui donne l'info (jamais de teaser type "vous n'allez pas croire").
- LIGNE VIDE, puis le détail : une idée par ligne, phrases courtes, JAMAIS un pavé.
- Termine par le média entre parenthèses : ({art.get('source','')})
- {longueur}
- 2-3 emojis max, bien choisis (🎮 🌴 👀 🔍 🚗). Le hashtag #GTA6 doit apparaître, intégré naturellement.
- Faits, chiffres et citations STRICTEMENT fidèles à l'article. Aucune fausse source.

Réponds en JSON UNIQUEMENT :
{{"headline_court":"... (max 70 char, pour l'image)","image_query":"GTA 6 ... (anglais, décrit une image du sujet)","body":"🎮 ...","keywords":["GTA6","..",".."]}}""", max_tokens=700, task="special")
    except Exception as e:
        print(f"  ⚠️ GTA6: {e}")
        return None

    body = (result.get("body") or "").strip()
    if not body or len(body) < 20:
        return None
    body = _split_long_lead(body)

    # Garde-fou rumeur : UNIQUEMENT pour les niveaux 2-3 (jamais sur une annonce officielle !)
    if level >= 3 and not re.search(r"th[ée]orie|rumeur|sp[ée]cul|selon|non confirm|la communaut[ée]", body, re.I):
        body += "\n\n⚠️ Rumeur non confirmée."
    if "#gta6" not in body.lower():
        body = _attach_hashtag(body, "", ["GTA6"])

    headline = _smart_truncate(result.get("headline_court", "GTA 6"), 80)
    return {
        "url": art.get("url"),                       # VRAI article → og:image réelle + anti-doublon
        "title": art.get("title", headline),
        "headline_court": headline,
        "summary": art.get("summary", ""),
        "source": art.get("source", ""),
        "entry": art.get("entry"),
        "photo_url": extract_photo(art["entry"]) if art.get("entry") else None,
        "analysis": {"category": "gta6", "needs_video": False},
        "tweet": build_full_tweet(body, "gta6"),
        "keywords": result.get("keywords", ["GTA6"]),
        "image_query": result.get("image_query", "GTA 6 Vice City neon"),
        "person": "",
        "_special_kind": "gta6",
        "_gta6_cat": best["cat"],
        "_gta6_level": level,
    }

def _wiki_page_image(page_title):
    """Vignette de la page Wikipédia `page_title` via l'API REST summary.
    Beaucoup d'événements 'onthisday' n'embarquent pas d'image, alors que leur page
    en a une. Renvoie une URL ou None. Jamais d'erreur."""
    if not page_title:
        return None
    try:
        import urllib.parse
        t = urllib.parse.quote(page_title.replace(" ", "_"))
        url = f"https://fr.wikipedia.org/api/rest_v1/page/summary/{t}"
        req = urllib.request.Request(url, headers={"User-Agent": "PulseBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        return (data.get("originalimage") or {}).get("source") \
            or (data.get("thumbnail") or {}).get("source")
    except Exception:
        return None


# 🎨 Modèle d'illustration. Nano Banana par défaut : ~500 images/jour sur le palier
#    gratuit, et c'est la voie recommandée par Google. Un modèle de SECOURS peut être
#    indiqué si le premier n'est pas ouvert sur le compte (ex. IMAGE_MODEL_SECOURS=
#    imagen-4.0-ultra-generate-001) — mais les points d'accès Imagen ferment le
#    17 août 2026, ce n'est donc qu'un dépannage temporaire.
IMAGE_MODEL         = os.environ.get("IMAGE_MODEL", "gemini-2.5-flash-image")
IMAGE_MODEL_SECOURS = os.environ.get("IMAGE_MODEL_SECOURS", "")

def _gemini_image(description, libelle="Illustration"):
    """Génère une image d'illustration.
    Deux familles de modèles cohabitent et n'ont PAS le même point d'accès :
      • Nano Banana (gemini-*-flash-image) → :generateContent, ~500 images/jour gratuites ;
      • Imagen (imagen-*) → :predict, format différent. ⚠️ Ces points d'accès sont
        DÉPRÉCIÉS et ferment le 17 août 2026 — à n'utiliser qu'en dépannage.
    Le bot choisit le format d'après le nom du modèle, et bascule sur l'autre si le
    premier échoue. Renvoie les octets de l'image, ou None.
    ⚠️ Une actualité n'est illustrée ainsi qu'en DERNIER RECOURS, et le tweet porte
    alors la mention correspondante."""
    if not description or not GEMINI_API_KEY:
        return None
    import base64 as _b64
    base = "https://generativelanguage.googleapis.com/v1beta/models"

    def _via_generate(modele, consigne):
        d = _post_gemini(f"{base}/{modele}:generateContent",
                         {"contents": [{"parts": [{"text": consigne}]}]},
                         famille="image", timeout=90)
        for p in (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []):
            inl = p.get("inlineData") or p.get("inline_data") or {}
            mime = (inl.get("mimeType") or inl.get("mime_type") or "")
            if inl.get("data") and mime.startswith("image/"):
                return _b64.b64decode(inl["data"])
        return None

    def _via_predict(modele, consigne):
        d = _post_gemini(f"{base}/{modele}:predict",
                         {"instances": [{"prompt": consigne}],
                          "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}},
                         famille="image", timeout=90)
        for pr in (d.get("predictions") or []):
            b64 = pr.get("bytesBase64Encoded") or pr.get("image", {}).get("bytesBase64Encoded")
            if b64:
                return _b64.b64decode(b64)
        return None

    consigne = description[:600]
    modeles = [IMAGE_MODEL] + [m for m in (IMAGE_MODEL_SECOURS,) if m and m != IMAGE_MODEL]
    for modele in modeles:
        appel = _via_predict if modele.lower().startswith("imagen") else _via_generate
        try:
            brut = appel(modele, consigne)
            if brut and len(brut) > 2000:
                print(f"  🎨 {libelle} générée par {modele} (mention obligatoire ajoutée)")
                return brut
        except Exception as e:
            print(f"  ⚠️ {modele} indisponible ({str(e)[:60]})")
    return None


def _histoire_img(events, idx):
    """Image de l'événement n° idx choisi par Claude.
    ① image fournie par le flux onthisday, sinon ② image de la PAGE Wikipédia liée.
    ⚠️ JAMAIS l'image d'un AUTRE événement : mieux vaut pas d'image qu'un hors-sujet."""
    try:
        i = int(idx)
        if 0 <= i < len(events):
            ev = events[i]
            if ev.get("img"):
                return ev["img"]
            # repli RACCORD : la vignette de la page Wikipédia de CET événement précis
            img = _wiki_page_image(ev.get("page"))
            if img:
                return img
    except (TypeError, ValueError):
        pass
    return None

def gen_histoire_du_jour(conn):
    if special_done_today(conn, "histoire") or "histoire" in cats_today(conn):
        return None
    events = fetch_wikipedia_onthisday()
    if not events: return None

    now        = datetime.now()
    today      = now.strftime("%d %B")
    current_yr = now.year

    # On pré-calcule "X ans" en Python pour éviter que Claude se trompe.
    # Liste NUMÉROTÉE : Claude renvoie l'index de l'événement choisi → on récupère
    # l'image Wikipédia de CET événement précis (raccord garanti).
    events = events[:15]
    events_str = "\n".join(
        f"{i}. En {e['year']} (il y a {current_yr - e['year']} ans){' 🖼️' if e.get('img') or e.get('page') else ''} : {e['text']}"
        for i, e in enumerate(events)
    )

    try:
        result = _llm_json(f"""Tu écris pour Pulse, compte Twitter français.

Aujourd'hui nous sommes le {today} {current_yr}. Voici les événements historiques VÉRIFIÉS de Wikipédia (le nombre d'années est DÉJÀ calculé, NE PAS le recalculer) :

{events_str}

ÉTAPE 1 — CHOISIS L'événement le plus FORT du jour, dans cet ordre :
1. Un fait SURPRENANT et RELATABLE, du genre « quoi, c'était il y a si peu ?! » : un droit de société acquis étonnamment tard, une « première fois » du quotidien, un chiffre hallucinant. Ex : « il y a 61 ans, les femmes mariées obtenaient enfin le droit d'ouvrir un compte en banque sans l'accord de leur mari ».
2. OU un événement MONDIALEMENT connu ET marquant (11-Septembre, Apollo 11, chute du Mur, D-Day…), s'il a un angle fort.
3. OU une anecdote / un détail fou que peu de gens connaissent.
REJETTE l'obscur, l'administratif, le « déjà vu mille fois sans angle neuf ».
À FORCE ÉGALE, préfère un événement marqué 🖼️ (il a une illustration d'époque) — mais ne sacrifie JAMAIS la force du sujet juste pour l'image.
⚠️ SOIS TRÈS EXIGEANT : si le jour n'offre RIEN de vraiment marquant ou surprenant, réponds {{"skip": true}}. Mieux vaut NE RIEN publier qu'un éphéméride banal — c'est un choix assumé, pas un échec.

ÉTAPE 2 — ÉCRIS UN TWEET COURT, FACTUEL, PERCUTANT (registre sobre, PAS une dissertation) :
- 2 à 4 phrases MAXIMUM. Concis, chaque mot compte. Va droit au fait.
- Commence par le fait le plus fort/surprenant, puis « il y a X ans » et le contexte essentiel — pas plus.
- CONCRET et FACTUEL. ⛔ INTERDIT le lyrisme et les envolées : pas de « cathédrale de la solidarité », « acte de rébellion contre l'indifférence », « la planète bat au même rythme », « moment suspendu », « cathédrales »… On informe, on ne fait pas de poésie.
- ⛔ RIGUEUR : n'invente aucun fait/date/chiffre. Utilise EXACTEMENT le nombre d'années donné (« il y a 57 ans » → écris 57, pas 56 ni 58).

Format :
- index : le NUMÉRO de l'événement choisi dans la liste ci-dessus (entier)
- headline_court (max 75 chars)
- image_query (5 mots en anglais)
- body : le fait fort d'emblée, puis le contexte court. 1-2 hashtags pertinents max. Fini par « (Source : Wikipédia) »

JSON :
{{"index":0,"headline_court":"...","image_query":"...","body":"..."}}
OU
{{"skip": true}}""", max_tokens=450, task="special")

        if result.get("skip"):
            print("  📜 Aucun événement assez connu — skip.")
            return None

        body = (result.get("body") or "").strip()
        for lbl in LABELS.values():
            body = re.sub(rf"^{lbl}\s*\|\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s]+", "", body).strip()
        if not body: return None

        # 🖼️ Image : d'abord la vraie photo Wikipédia de l'événement choisi. Si elle
        #    n'existe pas, une ILLUSTRATION générée — et dans ce cas SEULEMENT, le tweet
        #    porte la mention « image représentative », pour ne jamais laisser croire
        #    qu'il s'agit d'un document d'époque authentique.
        _photo = _histoire_img(events, result.get("index"))
        _brut, _generee = None, False
        if not _photo:
            _brut = _gemini_image(_prompt_historique(result.get("headline_court") or body[:200]),
                                  libelle="Illustration historique")
            _generee = _brut is not None
        _corps = build_full_tweet(body, "histoire")
        if _generee and "représentative" not in _corps.lower():
            _corps = _corps.rstrip() + "\n\n(Illustration représentative, image générée)"

        return {
            "title":          f"Éphéméride — {today}",
            "source":         "Wikipédia",
            "url":            "",
            "analysis":       {"category": "histoire", "needs_video": False},
            "tweet":          _corps,
            "headline_court": _smart_truncate(result.get("headline_court", f"Éphéméride {today}"), 75),
            "image_query":    result.get("image_query", "history old"),
            "photo_url":      _photo,
            "raw_image":      _brut,          # illustration générée, si aucune photo réelle
            "image_generee":  _generee,
            "keywords":       [],
        }
    except Exception as e:
        print(f"  ⚠️ Histoire échouée : {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# THREADS QUOTIDIENS (basés sur les vrais articles RSS)
# ═══════════════════════════════════════════════════════════════════════════
def gather_all_headlines(resume_max=80):
    """Récupère un large échantillon de titres RSS pour repérer les grands sujets.
    💰 `resume_max` borne le résumé joint à chaque titre : pour CHOISIR un sujet, le titre
    porte l'essentiel. Un résumé de 200 caractères par article faisait gonfler le prompt
    du sondage à ~4 800 tokens pour un seul tweet."""
    headlines = []
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:5]:
                title = _titre_propre(entry.get("title", ""))
                summ  = _strip_html(entry.get("summary", entry.get("description", "")))
                if title:
                    summ = re.sub(r"<[^>]+>", "", summ)  # nettoie le HTML
                    if resume_max > 0:
                        headlines.append(f"[{fi['source']}] {title} — {summ[:resume_max]}")
                    else:
                        headlines.append(f"[{fi['source']}] {title}")
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
    if _paris_hour() < 12:   # sondage l'après-midi (heure de PARIS, pas du serveur)
        return None

    # 💰 titres seuls (sans résumé) : choisir un SUJET de sondage ne demande pas le détail
    headlines = gather_all_headlines(resume_max=0)
    if len(headlines) < 10:
        return None

    avoid = recent_special_topics(conn, "poll", days=7)
    avoid_str = " ; ".join(avoid) if avoid else "Aucun"
    headlines_str = "\n".join(headlines[:30])
    today = datetime.now().strftime("%d %B %Y")

    try:
        result = _llm_json(f"""Tu animes Pulse, compte Twitter d'actualité française. Aujourd'hui : {today}.

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
{{"keywords":["mot1","mot2"],"question":"...","options":["Option 1","Option 2"]}}""", max_tokens=400, task="special")

        question = (result.get("question") or "").strip()

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

# ═══════════════════════════════════════════════════════════════════════════
# 🎵 MOTEUR SONORE PULSE — chaque famille de catégorie a une VRAIE identité sonore,
# construite avec des ÉLÉMENTS différents (pas six variations de la même nappe) :
#   tension  → riser + IMPACT SOUDAIN + tic-tac urgent + drone grave   (breaking)
#   energie  → kick 4/4 + charley + basse + plucks lumineux            (sport)
#   sobre    → nappe posée + note grave espacée                        (politique...)
#   tech     → arpège 16e avec écho, aérien                            (tech/science/GTA)
#   leger    → plucks façon marimba, majeur, sautillant                (culture/insolite)
#   solennel → notes de piano rares et graves, très discret            (hommage)
# 100 % composé par le bot (numpy) : libre de droits, 0 €, 0 dépendance.
# Plusieurs variantes par ambiance + anti-répétition persistante en base.
# ═══════════════════════════════════════════════════════════════════════════
_A, _C, _D, _E, _F, _G, _B = 110.0, 130.81, 146.83, 164.81, 174.61, 196.0, 123.47
SOUND_MOODS = {   # (accords, bpm)
    "tension": [([(_A/2, _A, _E), (_G/2, _G, _D), (_A/2, _A, _E), (_F/2, _F, _C)], 104),
                ([(_E/2, _E, _B), (_D/2, _D, _A), (_E/2, _E, _B), (_C/2, _C, _G)], 112)],
    "energie": [([(_C, _E, _G), (_F, _A, _C*2), (_G, _B, _D*2), (_C, _E, _G)], 124),
                ([(_D, _F*1.0595, _A), (_G, _B, _D*2), (_A, _C*2, _E*2), (_D, _F*1.0595, _A)], 128),
                ([(_F, _A, _C*2), (_C, _E, _G), (_G, _B, _D*2), (_F, _A, _C*2)], 120)],
    "sobre":   [([(_C, _E, _G), (_D, _F, _A), (_B, _D, _F*1.0595), (_C, _E, _G)], 70),
                ([(_A, _C*2, _E*2), (_F, _A, _C*2), (_G, _B, _D*2), (_A, _C*2, _E*2)], 64)],
    "tech":    [([(_C, _G, _E*2), (_A, _E*2, _C*2), (_F, _C*2, _A), (_G, _D*2, _B)], 100),
                ([(_E, _B, _G*2/1.0595), (_C, _G, _E*2), (_D, _A, _F*2), (_E, _B, _G*2/1.0595)], 108)],
    "leger":   [([(_C, _E, _G), (_F, _A, _C*2), (_C, _E, _G), (_G, _B, _D*2)], 96),
                ([(_G, _B, _D*2), (_C, _E, _G), (_D, _F*1.0595, _A), (_G, _B, _D*2)], 92)],
    "solennel":[([(_A/2, _C, _E), (_F/2, _A/2, _C), (_G/2, _B/2, _D), (_A/2, _C, _E)], 50),
                ([(_D/2, _F/2, _A/2), (_A/2, _C, _E), (_G/2, _B/2, _D), (_D/2, _F/2, _A/2)], 46)],
}
_CAT_TO_MOOD = {
    "breaking": "tension", "urgent": "tension", "faitsdivers": "tension",
    "sport": "energie",
    "politique": "sobre", "justice": "sobre", "monde": "sobre", "economie": "sobre",
    "france": "sobre", "societe": "sobre", "environnement": "sobre", "sante": "sobre",
    "tech": "tech", "science": "tech", "gta6": "tech",
    "culture": "leger", "insolite": "leger", "positif": "leger", "people": "leger",
    "hommage": "solennel",
}

# ── petits instruments (numpy) ────────────────────────────────────────────────
def _snd_add(buf, sr, at_s, sig):
    i = int(at_s * sr)
    if i < 0 or i >= len(buf): return
    j = min(len(buf), i + len(sig)); buf[i:j] += sig[:j - i]

def _snd_kick(np, sr, amp=1.0):
    """Grosse caisse : sinus qui plonge 130→46 Hz, décroissance rapide."""
    n = int(sr * 0.30); t = np.arange(n) / sr
    f = 46 + 84 * np.exp(-t * 26)
    return amp * np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 10)

def _snd_tick(np, sr, amp=0.5, seed=3, dur=0.045):
    """Tic bref (bruit aigu très court) : tic-tac d'urgence / charley."""
    n = int(sr * dur)
    no = np.diff(np.random.RandomState(seed).randn(n), prepend=0.0)
    no /= (np.max(np.abs(no)) + 1e-9)
    return amp * no * np.exp(-np.arange(n) / (sr * 0.012))

def _snd_pluck(np, sr, freq, dur=0.7, amp=0.5, bright=0.5):
    """Note pincée (marimba/pluck) : harmoniques + décroissance."""
    n = int(sr * dur); t = np.arange(n) / sr
    sig = (np.sin(2 * np.pi * freq * t) + bright * 0.6 * np.sin(2 * np.pi * freq * 2 * t)
           + bright * 0.25 * np.sin(2 * np.pi * freq * 3 * t))
    return amp * sig * np.exp(-t * (4.5 / dur))

def _snd_boom(np, sr, amp=1.0):
    """IMPACT : boum grave + souffle, longue traîne — l'entrée 'soudaine' du breaking."""
    n = int(sr * 1.8); t = np.arange(n) / sr
    f = 40 + 46 * np.exp(-t * 7)
    core = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 2.0)
    no = np.random.RandomState(7).randn(n) * np.exp(-t * 5.5) * 0.5
    k = max(2, int(sr * 0.002)); no = np.convolve(no, np.ones(k) / k, mode="same")
    return amp * (core + no)

def _snd_riser(np, sr, dur=0.9, amp=0.7):
    """Montée (bruit + sinus qui grimpe) : la demi-seconde qui annonce l'impact."""
    n = int(sr * dur); t = np.arange(n) / sr
    f = 180 + 1500 * (t / dur) ** 2
    no = np.random.RandomState(5).randn(n) * 0.5
    k = max(2, int(sr * 0.0012)); no = np.convolve(no, np.ones(k) / k, mode="same")
    return amp * (t / dur) ** 2 * (0.55 * np.sin(2 * np.pi * np.cumsum(f) / sr) + no)

def _snd_pad(np, sr, buf, chords, dur, vol=1.0, dark=False):
    """Nappe d'accords (fond harmonique), répartie sur toute la durée."""
    n_total = len(buf); t = np.arange(n_total) / sr
    seg = n_total // len(chords)
    for ci, chord in enumerate(chords):
        a, b = ci * seg, ((ci + 1) * seg if ci < len(chords) - 1 else n_total)
        tt = t[a:b]
        for f in chord:
            buf[a:b] += vol * np.sin(2 * np.pi * f * tt + 0.15 * np.sin(2 * np.pi * 0.3 * tt))
            if not dark:
                buf[a:b] += vol * 0.3 * np.sin(2 * np.pi * f * 2 * tt)
        fade = min(int(sr * 0.6), (b - a) // 2)
        buf[a:a + fade] *= np.linspace(0.3, 1.0, fade)
        buf[b - fade:b] *= np.linspace(1.0, 0.3, fade)

def _last_sound_variant():
    try:
        c = sqlite3.connect("seen_articles.db")
        row = c.execute("SELECT keywords FROM special_log WHERE kind='sound' ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
        return (row[0] or "") if row else ""
    except Exception:
        return ""

def _log_sound_variant(tag):
    try:
        c = sqlite3.connect("seen_articles.db")
        c.execute("INSERT INTO special_log (kind, keywords) VALUES ('sound', ?)", (tag,))
        c.commit(); c.close()
    except Exception:
        pass

def build_soundtrack(path_wav, duration, category="actu", sujet=""):
    """Bande-son de la vidéo selon la CATÉGORIE : chaque ambiance a ses propres éléments
    (impact, kick, tic-tac, arpège, piano rare...). Variante tirée au sort en évitant la
    dernière jouée (mémoire en base). Un sujet dramatique assombrit un thème léger."""
    import wave
    import numpy as np
    mood = _CAT_TO_MOOD.get((category or "").lower(), "sobre")
    s = (sujet or "").lower()
    if mood in ("leger", "tech") and any(w in s for w in
            ("mort", "guerre", "attentat", "crise", "drame", "violence",
             "accident", "crash", "explosion", "meurtre", "conflit")):
        mood = "tension"
    variants = SOUND_MOODS[mood]
    last = _last_sound_variant()
    pool = [i for i in range(len(variants)) if f"{mood}:{i}" != last] or list(range(len(variants)))
    vi = random.choice(pool)
    chords, bpm = variants[vi]
    _log_sound_variant(f"{mood}:{vi}")

    sr = 44100
    n_total = max(sr, int(sr * duration))
    buf = np.zeros(n_total)
    beat = 60.0 / bpm

    if mood == "tension":
        # 🚨 SOUDAIN : montée (0.9 s) → IMPACT → drone grave + tic-tac urgent + pouls
        _snd_add(buf, sr, 0.0, _snd_riser(np, sr, dur=0.9, amp=1.1))
        _snd_add(buf, sr, 0.9, _snd_boom(np, sr, amp=2.6))
        pad = np.zeros(n_total); _snd_pad(np, sr, pad, chords, duration, vol=0.28, dark=True)
        ramp = np.clip((np.arange(n_total) / sr - 0.9) / 0.4, 0, 1)   # le fond n'arrive qu'APRÈS l'impact
        buf += pad * ramp
        tk = 1.1
        while tk < duration - 0.3:
            _snd_add(buf, sr, tk, _snd_tick(np, sr, amp=1.3))         # tic-tac d'urgence, 2/temps
            tk += beat / 2
        kk = 1.1
        while kk < duration - 0.5:
            _snd_add(buf, sr, kk, _snd_kick(np, sr, amp=1.5))         # pouls sourd
            kk += beat
        vol, fade_in = 0.22, False
    elif mood == "energie":
        # ⚽ kick 4/4 + charley en contretemps + basse + plucks de l'accord
        pad = np.zeros(n_total); _snd_pad(np, sr, pad, chords, duration, vol=0.16); buf += pad
        seg = duration / len(chords)
        tt = 0.0; bi = 0
        while tt < duration - 0.3:
            _snd_add(buf, sr, tt, _snd_kick(np, sr, amp=2.0))
            _snd_add(buf, sr, tt + beat / 2, _snd_tick(np, sr, amp=0.7, seed=11))
            ch = chords[min(int(tt / seg), len(chords) - 1)]
            _snd_add(buf, sr, tt, _snd_pluck(np, sr, ch[0] / 2, dur=0.35, amp=0.9, bright=0.2))   # basse
            if bi % 2 == 1:
                _snd_add(buf, sr, tt, _snd_pluck(np, sr, ch[(bi // 2) % 3] * 2, dur=0.5, amp=0.75, bright=0.7))
            tt += beat; bi += 1
        vol, fade_in = 0.21, True
    elif mood == "tech":
        # 💻 arpège en doubles-croches + écho, nappe aérienne
        pad = np.zeros(n_total); _snd_pad(np, sr, pad, chords, duration, vol=0.20); buf += pad
        seg = duration / len(chords); step = beat / 4
        tt, k = 0.4, 0
        while tt < duration - 0.4:
            ch = chords[min(int(tt / seg), len(chords) - 1)]
            f = ch[k % 3] * 2
            _snd_add(buf, sr, tt, _snd_pluck(np, sr, f, dur=0.30, amp=0.85, bright=0.8))
            _snd_add(buf, sr, tt + step * 1.5, _snd_pluck(np, sr, f, dur=0.30, amp=0.38, bright=0.8))  # écho
            tt += step; k += 1
        vol, fade_in = 0.18, True
    elif mood == "leger":
        # 🎭 marimba sautillante (fond-tierce-quinte-tierce), nappe chaude
        pad = np.zeros(n_total); _snd_pad(np, sr, pad, chords, duration, vol=0.22); buf += pad
        seg = duration / len(chords); pattern = [0, 1, 2, 1]
        tt, k = 0.2, 0
        while tt < duration - 0.3:
            ch = chords[min(int(tt / seg), len(chords) - 1)]
            _snd_add(buf, sr, tt, _snd_pluck(np, sr, ch[pattern[k % 4]] * 2, dur=0.45, amp=0.95, bright=0.35))
            tt += beat / 2; k += 1
        vol, fade_in = 0.18, True
    elif mood == "solennel":
        # 🕯️ notes de piano rares et graves sur nappe très douce — sobriété
        pad = np.zeros(n_total); _snd_pad(np, sr, pad, chords, duration, vol=0.5, dark=True); buf += pad
        seg = duration / len(chords)
        tt, k = 0.8, 0
        while tt < duration - 1.0:
            ch = chords[min(int(tt / seg), len(chords) - 1)]
            _snd_add(buf, sr, tt, _snd_pluck(np, sr, ch[k % 2], dur=2.6, amp=0.9, bright=0.15))
            tt += beat * 2; k += 1
        vol, fade_in = 0.11, True
    else:  # sobre
        # 🏛️ nappe posée + note grave espacée
        pad = np.zeros(n_total); _snd_pad(np, sr, pad, chords, duration, vol=0.6); buf += pad
        seg = duration / len(chords)
        tt = 0.5
        while tt < duration - 0.8:
            ch = chords[min(int(tt / seg), len(chords) - 1)]
            _snd_add(buf, sr, tt, _snd_pluck(np, sr, ch[0] / 2, dur=1.6, amp=0.4, bright=0.2))
            tt += beat * 2
        vol, fade_in = 0.16, True

    # Compression douce (tanh) : un impact fort (breaking) ne doit pas écraser tout le reste
    # à la normalisation — le boum reste puissant, le tic-tac et le drone restent audibles derrière.
    ref = np.percentile(np.abs(buf), 99.0) + 1e-9
    buf = np.tanh(buf / (ref * 1.4))
    buf = buf / (np.max(np.abs(buf)) + 1e-9) * vol
    gf = int(sr * 1.0)
    if fade_in:
        buf[:gf] *= np.linspace(0, 1, gf)       # tension garde son départ SOUDAIN (pas de fondu d'entrée)
    buf[-gf:] *= np.linspace(1, 0, gf)
    pcm = (buf * 32767).astype(np.int16)
    with wave.open(path_wav, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return f"{mood}:{vi}"

def _decrypt_soundtrack(path_wav, duration, sujet=""):
    """Compat : la nappe du décryptage passe désormais par le moteur unique."""
    return build_soundtrack(path_wav, duration, category="sobre-decrypt", sujet=sujet)

def _texte_pour_voix(cover_title, slides, duree_video, mots_par_sec=2.3):
    """Construit le texte lu à voix haute en le BORNANT à la durée réelle de la vidéo.
    ⚠️ Sans cette borne, la narration dépasse systématiquement de 20 à 40 s et se retrouve
    coupée en plein milieu d'une phrase. On s'arrête donc à une frontière de phrase, dans
    l'ordre d'apparition à l'écran : titre de couverture, puis chaque intertitre et ses points.
    Rien n'est inventé : uniquement ce qui est déjà affiché."""
    budget = max(8, int(duree_video * mots_par_sec) - 5)   # marge de sécurité
    morceaux = [cover_title or ""]
    for s in (slides or []):
        morceaux.append(s.get("titre", ""))
        for p in (s.get("points") or [])[:2]:
            morceaux.append(p)
    retenu, total = [], 0
    for m in morceaux:
        m = re.sub(r"\s+", " ", (m or "")).strip().rstrip(".")
        if not m:
            continue
        n = len(m.split())
        if total + n > budget:
            break            # on s'arrête AVANT de dépasser : jamais de phrase tronquée
        retenu.append(m)
        total += n
    return ". ".join(retenu) + ("." if retenu else "")


def build_decrypt_video(cover_png, slides_data, sujet="", bg_photo=None, accent=(255, 90, 200), decrypt_cat="monde", voice_text=None):
    """🎬 Vidéo DÉCRYPTAGE (X/Facebook) — portrait 1080×1350.
    Les slides SONT la vidéo (plein cadre, pas de fond ajouté). Le texte S'ÉCRIT mot par mot,
    et la durée de chaque slide est calculée sur sa QUANTITÉ DE TEXTE (temps de lecture garanti,
    quel que soit le nombre de caractères). Se termine par une slide d'abonnement.
    slides_data = liste de {"titre":..., "points":[...]} (les données, pas des PNG)."""
    import io, shutil, subprocess, tempfile
    if os.environ.get("PULSE_VIDEO", "1") == "0" or not slides_data:
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    try:
        print(f"  🎬 Génération vidéo décryptage ({len(slides_data)} slides)...")
        W, H, FPS = VIDEO_W, VIDEO_H, 18
        XFADE = 0.45
        frames_fade = int(FPS * XFADE)
        total_n = len(slides_data) + 1   # pastilles : cover = 1/N, slides = 2..N

        # ── ⏱️ DURÉE INTELLIGENTE : chaque slide reste le temps d'être LUE ──
        # Vitesse de lecture confortable ≈ 15 caractères/seconde. La durée = écriture du texte
        # (≈ 8 mots/s) + temps de lecture complet, bornée [3,5 s ; 11 s] pour rester rythmée.
        def slide_duration(titre, points):
            chars = len(titre or "") + sum(len(p) for p in points)
            words = len((titre or "").split()) + sum(len(p.split()) for p in points)
            write_t = words / 8.0                       # phase d'écriture
            read_t  = chars / 15.0                      # relecture complète
            return max(3.5, min(11.0, 1.0 + max(write_t, read_t))), words

        # ── pré-rendu des ÉTATS de chaque slide (1 image par mot, fond mis en cache) ──
        # La mise en page vient de build_carousel_slide (source unique) via reveal=k/mots.
        seq = []   # liste de (frames de la slide) sous forme (states, frames_total, frames_write)
        # 1) COVER : la carte (déjà rendue) plein cadre, statique
        cov = Image.open(io.BytesIO(cover_png)).convert("RGB")
        if cov.size != (W, H):
            cov = cov.resize((W, H), Image.LANCZOS)
        seq.append(([cov], int(FPS * 2.8), 0))
        # 2) SLIDES DE CONTENU : états mot par mot
        for i, sd in enumerate(slides_data, start=2):
            titre, points = sd.get("titre", ""), sd.get("points") or []
            dur, words = slide_duration(titre, points)
            bg = build_carousel_bg(i, total_n, accent=accent, bg_photo=bg_photo, W=W, H=H)
            # 🌊 DEUX sous-états par mot (demi-fondu puis complet) : l'écriture est fluide,
            #    chaque mot entre en fondu au lieu de surgir. Plafonné pour garder le rendu léger.
            _n_states = min(2 * words, 40)
            states = [build_carousel_slide(titre, points, i, total_n,
                                           is_last=(i == total_n), accent=accent,
                                           reveal=(k / max(1, _n_states)), as_image=True, bg_cache=bg,
                                           W=W, H=H)
                      for k in range(0, _n_states + 1)]
            frames_total = int(FPS * dur)
            frames_write = int(FPS * (words / 8.0))
            seq.append((states, frames_total, frames_write))
        # 3) SLIDE FINALE : abonnement (statique, ~3 s)
        seq.append(([build_follow_slide(accent=accent, W=W, H=H)], int(FPS * 3.0), 0))

        # ⏱️ Plafond de durée : sous 60 s, X relance la vidéo en boucle dans le fil (watch time).
        total_dur = sum(ft for _, ft, _ in seq) / FPS
        if total_dur > VIDEO_MAX_DUR and len(seq) > 2:
            fixed   = seq[0][1] + seq[-1][1]                        # cover + slide d'abonnement
            budget  = max(1, int(FPS * VIDEO_MAX_DUR) - fixed)      # frames restantes pour le contenu
            content = sum(ft for _, ft, _ in seq[1:-1]) or 1
            k = budget / content
            seq = ([seq[0]]
                   + [(s, max(int(FPS * 2.2), int(ft * k)), int(fw * k)) for s, ft, fw in seq[1:-1]]
                   + [seq[-1]])
            total_dur = sum(ft for _, ft, _ in seq) / FPS
            print(f"  ⏱️ Décryptage ramené à {total_dur:.0f}s (boucle auto sous 60s)")

        tmpdir = tempfile.mkdtemp(prefix="pulse_decrypt_")
        raw_mp4 = os.path.join(tmpdir, "video_raw.mp4")
        proc = subprocess.Popen(
            [ffmpeg_bin, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", raw_mp4],
            stdin=subprocess.PIPE)

        def state_at(states, f, frames_write):
            """État (image) à la frame f : le texte s'écrit pendant frames_write, puis reste complet."""
            if len(states) == 1 or frames_write <= 0:
                return states[-1]
            k = min(len(states) - 1, int(len(states) * f / max(1, frames_write)))
            return states[k]

        for si, (states, frames_total, frames_write) in enumerate(seq):
            for f in range(frames_total):
                frame = state_at(states, f, frames_write)
                # fondu enchaîné vers la slide suivante (qui démarre texte vide)
                if si < len(seq) - 1 and f >= frames_total - frames_fade:
                    alpha = (f - (frames_total - frames_fade)) / frames_fade
                    nxt = seq[si + 1][0][0]
                    frame = Image.blend(frame, nxt, alpha)
                proc.stdin.write(frame.tobytes())
        proc.stdin.close(); proc.wait()
        if proc.returncode != 0 or not os.path.exists(raw_mp4):
            shutil.rmtree(tmpdir, ignore_errors=True); return None

        # ✨ pilule ANIMÉE sur la COUVERTURE uniquement (2,8 s) : les slides ont déjà leur
        #    compteur "n/N" en haut à droite, on ne le recouvre pas.
        raw_mp4 = _overlay_animated_pill(raw_mp4, decrypt_cat, W, H, tmpdir, until=2.8)
        raw_mp4 = _overlay_animated_logo(raw_mp4, W, H, tmpdir,
                                         int(W * 0.037), int(H * 0.044), int(H * 0.062), until=2.8)

        out_mp4 = os.path.join(tmpdir, "video.mp4")
        try:
            wav = os.path.join(tmpdir, "pad.wav")
            # 🔊 Bande son : VOIX de synthèse lisant le décryptage + MUSIQUE en fond.
            #    La voix prime largement (musique à -16 dB + atténuation quand ça parle).
            #    Si la voix échoue → musique seule ; si tout échoue → nappe d'origine.
            # texte borné à la durée réelle de la vidéo (jamais de phrase coupée)
            _lu = _texte_pour_voix(voice_text, slides_data, total_dur) if voice_text else ""
            voix = _gemini_tts(_lu, os.path.join(tmpdir, "voix.wav")) if _lu else None
            piste = _piste_musicale()
            mixe = _melange_voix_musique(voix, piste, os.path.join(tmpdir, "mix.m4a"),
                                         total_dur) if (voix or piste) else None
            if mixe:
                wav = mixe
            else:
                _decrypt_soundtrack(wav, total_dur, sujet)
            r = subprocess.run([ffmpeg_bin, "-y", "-loglevel", "error", "-i", raw_mp4, "-i", wav,
                                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", out_mp4],
                               capture_output=True)
            if r.returncode != 0 or not os.path.exists(out_mp4):
                out_mp4 = raw_mp4      # vidéo muette plutôt que pas de vidéo
        except Exception:
            out_mp4 = raw_mp4
        print(f"  🎬 Vidéo décryptage générée ({len(slides_data)} slides + abonnement, ~{total_dur:.0f}s)")
        return out_mp4
    except Exception as e:
        print(f"  ⚠️ Vidéo décryptage échouée : {e}")
        return None

TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "Kore")
_MUSIC_DIRS = ("music", "assets/music", ".")

def _gemini_tts(texte, wav_out):
    """Voix de synthèse via l'API Gemini. Le service renvoie du PCM brut 16 bits / 24 kHz
    encodé en base64 : on le convertit en WAV avec ffmpeg.
    Renvoie le chemin du WAV, ou None. 🛡️ Jamais d'erreur remontée : sans voix, la vidéo
    sort quand même avec sa seule musique."""
    if not texte or not GEMINI_API_KEY:
        return None
    try:
        import base64 as _b64, imageio_ffmpeg as _iff, subprocess as _sp
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{TTS_MODEL}:generateContent")
        # le modèle ne parle que si on le lui demande explicitement ("Lis…")
        d = _post_gemini(url,
                         {"contents": [{"parts": [{"text":
                              "Lis ce texte d'un ton posé, clair et journalistique, "
                              "sans emphase excessive : " + texte[:1200]}]}],
                          "generationConfig": {
                              "responseModalities": ["AUDIO"],
                              "speechConfig": {"voiceConfig": {
                                  "prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}}}},
                         famille="tts", timeout=90)
        parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        b64 = None
        for p in parts:
            inl = p.get("inlineData") or p.get("inline_data") or {}
            if inl.get("data"):
                b64 = inl["data"]; break
        if not b64:
            return None
        pcm = os.path.splitext(wav_out)[0] + ".pcm"
        with open(pcm, "wb") as f:
            f.write(_b64.b64decode(b64))
        ff = _iff.get_ffmpeg_exe()
        rr = _sp.run([ff, "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000",
                      "-ac", "1", "-i", pcm, wav_out], capture_output=True, timeout=120)
        if rr.returncode == 0 and os.path.exists(wav_out) and os.path.getsize(wav_out) > 2000:
            print(f"  🔊 Voix de synthèse générée ({TTS_VOICE})")
            return wav_out
    except Exception as e:
        print(f"  ⚠️ Voix de synthèse indisponible ({str(e)[:70]}) → vidéo sans voix")
    return None


def _piste_musicale():
    """Piste musicale au hasard dans le dossier `music/` (déposé dans le dépôt, comme
    `pills/`). Des morceaux ORIGINAUX générés une fois : aucun ayant droit, aucun risque
    de signalement, et zéro coût à chaque vidéo. None si le dossier est absent."""
    try:
        exts = (".mp3", ".m4a", ".wav", ".ogg")
        for d in _MUSIC_DIRS:
            if not os.path.isdir(d):
                continue
            pistes = [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.lower().endswith(exts)]
            if pistes:
                return random.choice(pistes)
    except Exception:
        pass
    return None


def _melange_voix_musique(voix_wav, musique, sortie, duree, voix_db=3.0, musique_db=-18.0):
    """Mixe la VOIX et la MUSIQUE en gardant la voix NETTEMENT au-dessus.
    `musique_db=-16` place la musique environ six fois moins forte que la voix, et un
    `sidechaincompress` la fait automatiquement baisser quand la voix parle.
    Renvoie le chemin du mixage, ou la voix seule, ou la musique seule — dans cet ordre
    de préférence : la parole prime toujours sur l'ambiance."""
    try:
        import imageio_ffmpeg as _iff, subprocess as _sp
        ff = _iff.get_ffmpeg_exe()
        if voix_wav and musique:
            # ⚠️ Un label ffmpeg ne se consomme qu'UNE fois : la voix est dupliquée (asplit)
            #    car elle sert deux fois — comme déclencheur d'atténuation et dans le mixage.
            #    La musique est bouclée par l'entrée (-stream_loop), pas par un filtre.
            filtre = (
                f"[0:a]volume={voix_db}dB,apad,asplit=2[v1][v2];"
                f"[1:a]volume={musique_db}dB[m];"
                f"[m][v1]sidechaincompress=threshold=0.02:ratio=12:attack=8:release=320[duck];"
                f"[duck][v2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"aresample=44100[a]"
            )
            r = _sp.run([ff, "-y", "-loglevel", "error",
                         "-i", voix_wav, "-stream_loop", "-1", "-i", musique,
                         "-filter_complex", filtre, "-map", "[a]",
                         "-t", f"{duree:.2f}", sortie],
                        capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(sortie) and os.path.getsize(sortie) > 2000:
                print("  🎚️ Voix et musique mixées (voix dominante)")
                return sortie
            print(f"  ⚠️ Mixage impossible (ffmpeg {r.returncode}) → voix seule")
        if voix_wav:
            return voix_wav
        if musique:
            r = _sp.run([ff, "-y", "-loglevel", "error", "-i", musique,
                         "-filter:a", f"volume={musique_db}dB,atrim=0:{duree:.2f}",
                         "-ar", "44100", sortie], capture_output=True, timeout=120)
            if r.returncode == 0 and os.path.exists(sortie):
                return sortie
    except Exception as e:
        print(f"  ⚠️ Mixage audio ignoré ({str(e)[:70]})")
    return voix_wav or None


# ════════════════════════════════════════════════════════════════════════════════
#  CARROUSEL — gabarit repris du modèle fourni (1080×1350), rendu en Pillow.
#  Le gabarit d'origine passait par un navigateur (Playwright + Chromium, ~300 Mo
#  à installer à chaque cycle) : impossible ici, et inutile — toutes les valeurs
#  du modèle (tailles, couleurs, marges, dégradés) sont reproduites fidèlement.
# ════════════════════════════════════════════════════════════════════════════════
CARR_W, CARR_H = 1080, 1350
CARR_ACCENT    = (185, 166, 230)      # #b9a6e6 — surlignage lavande
CARR_HL_TEXTE  = (43, 34, 71)         # #2b2247 — texte sur surlignage
CARR_STROKE    = (25, 20, 38)         # #191426 — contour des titres

_CARR_FONT_DIRS = ("fonts", "assets/fonts", "/usr/share/fonts/truetype/google-fonts",
                   "/usr/share/fonts/truetype/dejavu", ".")
# Les cinq polices de titre du modèle, dans l'ordre de repli. Elles sont livrées dans
# le dossier `fonts/` (comme `pills/`) : sans elles, Poppins Bold prend le relais —
# lisible, mais nettement moins percutant que les graisses lourdes prévues.
_CARR_TITRES = {
    "Poppins Gras":  ("Poppins-ExtraBold.ttf", "Poppins-Black.ttf", "Poppins-Bold.ttf"),
    "Archivo Black": ("ArchivoBlack-Regular.ttf", "Poppins-ExtraBold.ttf"),
    "Anton":         ("Anton-Regular.ttf", "Poppins-ExtraBold.ttf"),
    "Bebas Neue":    ("BebasNeue-Regular.ttf", "Poppins-ExtraBold.ttf"),
    "Oswald":        ("Oswald.ttf", "Poppins-ExtraBold.ttf"),
}
CARR_TITLE_FONT = os.environ.get("CARR_TITLE_FONT", "Poppins Gras")

def _carr_charge(noms, px):
    for n in noms:
        for d in _CARR_FONT_DIRS:
            try:
                return ImageFont.truetype(os.path.join(d, n), max(8, int(px)))
            except Exception:
                continue
    return None

def _carr_font(px, gras=True, titre=False):
    """Police du gabarit. `titre=True` → police d'affichage lourde (réglable par
    CARR_TITLE_FONT) ; sinon Poppins Medium pour le corps de texte."""
    if titre:
        f = _carr_charge(_CARR_TITRES.get(CARR_TITLE_FONT, _CARR_TITRES["Poppins Gras"]), px)
        if f is not None:
            return f
    f = _carr_charge(("Poppins-Bold.ttf",) if gras else ("Poppins-Medium.ttf", "Poppins-Regular.ttf"), px)
    if f is not None:
        return f
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(8, int(px)))


def _carr_accent(categorie=None):
    """Couleur de surlignage. Sans catégorie → lavande du gabarit (décryptage).
    Avec catégorie → couleur de la charte Pulse, pour que le récap parle le même
    langage visuel que les pastilles et les badges."""
    if not categorie:
        return CARR_ACCENT
    try:
        st = STYLES.get(str(categorie).lower())
        coul = st.get("color") if isinstance(st, dict) else getattr(st, "color", None)
        if coul:
            return _hex_rgb(coul)
    except Exception:
        pass
    return CARR_ACCENT


def _carr_texte_sur_accent(accent):
    """Texte sombre sur un surlignage clair, blanc sur un surlignage foncé —
    la charte compte des couleurs très claires (jaune, vert d'eau) et d'autres soutenues."""
    lum = 0.299 * accent[0] + 0.587 * accent[1] + 0.114 * accent[2]
    return CARR_HL_TEXTE if lum > 140 else (255, 255, 255)


_HL_MOTIFS = [
    # dates complètes et périodes
    r"\b\d{1,2}(?:er)? (?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)(?: \d{4})?\b",
    r"\b(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|"
    r"octobre|novembre|décembre|decembre) \d{4}\b",
    r"\b(?:début|debut|fin|mi)-?(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|"
    r"août|aout|septembre|octobre|novembre|décembre|decembre)(?: \d{4})?\b",
    # montants et pourcentages — les symboles ne sont PAS des caractères de mot,
    # une limite de mot après eux ne fonctionnerait pas
    r"\b\d[\d  ]*(?:,\d+)? ?(?:Md|[MmKk])?[%€$]",
    r"\b\d[\d  ]*(?:,\d+)? ?(?:euros?|milliards?|millions?|milliers?)\b",
    r"\b\d[\d  ]*(?:,\d+)? ?(?:hectares?|kilomètres?|km|mètres?|tonnes?|habitants?|"
    r"morts?|blessés?|victimes?|ans?|mois|jours?|heures?)\b",
    # années seules et grands nombres
    r"\b(?:19|20)\d{2}\b",
    r"\b\d{2,}(?: \d{3})+\b",
]
_HL_RX = re.compile("|".join(_HL_MOTIFS), re.IGNORECASE)
# décisions et bascules : ce sont elles qui portent l'information d'un décryptage
_HL_DECISION = re.compile(
    r"\b(?:interdit|interdite|interdiction|autorisé|autorisée|adopté|adoptée|approuvé|"
    r"approuvée|rejeté|rejetée|suspendu|suspendue|annulé|annulée|reporté|reportée|"
    r"fermé|fermée|rouvre|rouvert|obligatoire|supprimé|supprimée|doublé|doublée|"
    r"condamné|condamnée|relaxé|relaxée|démission|démissionne)\b", re.IGNORECASE)


def _carr_surligne(texte, maxi=4):
    """Découpe un texte en segments pour le gabarit, en surlignant les éléments qui
    PORTENT l'information : dates, chiffres, montants, décisions.
    Le modèle n'est pas sollicité — c'est déterministe, gratuit et reproductible.
    Renvoie une liste de segments : "texte" ou {"hl": "…"}, au plus `maxi` surlignages."""
    t = re.sub(r"\s+", " ", str(texte or "")).strip()
    if not t:
        return []
    zones = []
    for rx in (_HL_RX, _HL_DECISION):
        for m in rx.finditer(t):
            zones.append((m.start(), m.end()))
    if not zones:
        return [t]
    # fusionner les chevauchements, garder l'ordre, limiter le nombre
    zones.sort()
    fusion = [zones[0]]
    for a, b in zones[1:]:
        if a <= fusion[-1][1] + 1:
            fusion[-1] = (fusion[-1][0], max(fusion[-1][1], b))
        else:
            fusion.append((a, b))
    fusion = fusion[:max(1, maxi)]
    segs, pos = [], 0
    for a, b in fusion:
        if a > pos:
            segs.append(t[pos:a])
        segs.append({"hl": t[a:b].strip()})
        pos = b
    if pos < len(t):
        segs.append(t[pos:])
    return [s for s in segs if s != ""]


def _carr_lignes_titre(texte, maxi=3):
    """Coupe un titre en 1 à 3 lignes équilibrées, pour le gabarit."""
    mots = re.sub(r"\s+", " ", str(texte or "")).strip().split()
    if not mots:
        return []
    n = 1 if len(mots) <= 3 else (2 if len(mots) <= 7 else min(maxi, 3))
    taille = max(1, (len(mots) + n - 1) // n)
    return [" ".join(mots[i:i + taille]) for i in range(0, len(mots), taille)][:maxi]


def _carr_numerote(slides):
    """Pose la pastille « k/total » sur chaque slide."""
    total = len(slides)
    for i, s in enumerate(slides):
        s["pageLabel"] = f"{i + 1}/{total}"
    return slides


def carrousel_recap(items, date_txt=""):
    """Transforme le récap du soir en carrousel d'images.
    `items` = [(emoji, texte, categorie, raw_image), …] — même entrée que la carte récap.
    Renvoie (slides, accents, photos) — une couleur et une photo PAR slide, la couleur
    étant celle de la catégorie de l'actu (le décryptage, lui, garde le lavande)."""
    slides = [{"kind": "recapCover", "subjectTag": (date_txt or _date_fr()).upper(),
               "titleLines": ["Ce qu'il faut", "retenir"]}]
    accents = [CARR_ACCENT]
    photos  = [None]
    for i, it in enumerate(items or []):
        emo, texte, cat, raw = (list(it) + [None, None, None, None])[:4]
        cat = (cat or "france").lower()
        slides.append({
            "kind": "info",
            "titleLines": [f"{i + 1} — {(STYLES.get(cat, {}) or {}).get('label', cat).upper()}"],
            "paras": [_carr_surligne(texte)],
        })
        accents.append(_carr_accent(cat))
        photos.append(raw)
    # la couverture reprend la photo de la première actu, à défaut de la sienne
    if len(photos) > 1 and photos[1]:
        photos[0] = photos[1]
    return _carr_numerote(slides), accents, photos


def carrousel_decryptage(carousel, raw_photo=None, categorie="monde"):
    """Transforme le décryptage du jour en carrousel : couverture → une info par
    slide → appel à s'abonner. Le lavande d'origine est conservé (demande explicite)."""
    slides = [{"kind": "cover",
               "category": (STYLES.get(categorie, {}) or {}).get("label", categorie),
               "titleLines": _carr_lignes_titre(carousel.get("cover_title", ""))}]
    for s in (carousel.get("slides") or []):
        paras = [_carr_surligne(p) for p in (s.get("points") or []) if str(p).strip()]
        slides.append({"kind": "info",
                       "titleLines": _carr_lignes_titre(s.get("titre", ""), maxi=2),
                       "paras": paras})
    slides.append({"kind": "cta",
                   "ctaLines": _carr_lignes_titre("Pulse décrypte l'actualité chaque jour", maxi=2),
                   "ctaSub": "L'info vérifiée, sans détour",
                   "ctaBig": "Abonnez-vous"})
    n = len(slides)
    return _carr_numerote(slides), [CARR_ACCENT] * n, [raw_photo] * n


def rendre_carrousel(slides, accents, photos, watermark="@PULSEactus", maxi=None):
    """Rend un carrousel en liste de PNG. Une couleur et une photo par slide.
    🛡️ Une slide qui échoue est simplement omise : le carrousel sort quand même."""
    out = []
    for i, s in enumerate(slides or []):
        if maxi and len(out) >= maxi:
            break
        png = build_carousel_png(
            s, watermark=watermark,
            accent=(accents[i] if i < len(accents) else CARR_ACCENT),
            raw_photo=(photos[i] if i < len(photos) else None))
        if png:
            out.append(png)
    return out


def _carr_duree_slide(slide):
    """Temps d'affichage d'une slide, calé sur le temps de LECTURE de son contenu."""
    mots = 0
    for l in (slide.get("titleLines") or []):
        mots += len(str(l).split())
    for p in (slide.get("paras") or []):
        for seg in p:
            mots += len(str(seg.get("hl") if isinstance(seg, dict) else seg).split())
    for c in ("kicker", "ctaSub", "ctaBig"):
        mots += len(str(slide.get(c) or "").split())
    for l in (slide.get("ctaLines") or []):
        mots += len(str(l).split())
    # ~2,6 mots/seconde en lecture confortable, plus un temps d'accroche
    return max(3.0, min(11.0, 1.6 + mots / 2.6))


def build_video_carrousel(pngs, slides, voice_text="", cat="monde", voice_parts=None):
    """Assemble un carrousel d'images en VIDÉO verticale, avec la voix de synthèse
    par-dessus une musique de fond (voix dominante).

    🔑 SYNCHRONISATION : quand `voice_parts` est fourni (un texte par slide), la voix est
    générée SLIDE PAR SLIDE et chaque image reste à l'écran exactement le temps de son
    propre audio. Le son ne peut donc PAS dériver — il se recale à chaque slide.
    (Avant, durée d'affichage et durée de parole étaient calculées par deux formules
    différentes : le décalage s'accumulait et la voix finissait plusieurs secondes après
    l'image.) Sans `voice_parts`, on retombe sur l'estimation par le texte.
    🛡️ Tolérant : renvoie None en cas d'échec — l'appelant retombe alors sur les images."""
    if not pngs:
        return None
    tmpdir = None
    try:
        import imageio_ffmpeg as _iff, subprocess as _sp, tempfile as _tf
        ff = _iff.get_ffmpeg_exe()
        tmpdir = _tf.mkdtemp(prefix="pulse_carr_")

        # ── ① Voix slide par slide : c'est elle qui donne le tempo ──
        voix_slides, durees = [], []
        if voice_parts and GEMINI_API_KEY:
            for i in range(len(pngs)):
                txt = (voice_parts[i] if i < len(voice_parts) else "") or ""
                w = None
                if txt.strip():
                    w = _gemini_tts(txt, os.path.join(tmpdir, f"v{i:02d}.wav"))
                d = _duree_audio(w) if w else 0.0
                voix_slides.append(w)
                # la slide dure le temps de sa narration + une respiration, bornée
                durees.append(max(2.5, min(14.0, d + 0.9)) if d > 0
                              else _carr_duree_slide(slides[i] if i < len(slides) else {}))
        if not durees:
            durees = [_carr_duree_slide(slides[i] if i < len(slides) else {})
                      for i in range(len(pngs))]
        total = sum(durees)

        # liste de montage : une image, sa durée. La dernière est répétée — sans quoi
        # ffmpeg tronque le plan final (particularité du démultiplexeur concat).
        chemins = []
        for i, p in enumerate(pngs):
            c = os.path.join(tmpdir, f"s{i:02d}.png")
            with open(c, "wb") as f:
                f.write(p)
            chemins.append(c)
        liste = os.path.join(tmpdir, "montage.txt")
        with open(liste, "w", encoding="utf-8") as f:
            for c, d in zip(chemins, durees):
                f.write(f"file '{c}'\nduration {d:.2f}\n")
            f.write(f"file '{chemins[-1]}'\n")

        muet = os.path.join(tmpdir, "muet.mp4")
        r = _sp.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                     "-i", liste, "-vf", "fps=25,format=yuv420p",
                     "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                     "-movflags", "+faststart", muet],
                    capture_output=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(muet):
            print(f"  ⚠️ Montage vidéo impossible (ffmpeg {r.returncode})")
            return None

        # ── ② Bande voix : chaque narration placée AU DÉBUT de sa slide ──
        voix = None
        if any(voix_slides):
            morceaux, depart = [], 0.0
            for i, w in enumerate(voix_slides):
                if w:
                    morceaux.append((depart, w))
                depart += durees[i]
            if morceaux:
                entrees, filtres, labels = [], [], []
                for k, (t0, w) in enumerate(morceaux):
                    entrees += ["-i", w]
                    filtres.append(f"[{k}:a]adelay={int(t0*1000)}|{int(t0*1000)},"
                                   f"aresample=44100[d{k}]")
                    labels.append(f"[d{k}]")
                voix = os.path.join(tmpdir, "voix.wav")
                fc = ";".join(filtres) + ";" + "".join(labels) + \
                     f"amix=inputs={len(morceaux)}:normalize=0[a]"
                rv = _sp.run([ff, "-y", "-loglevel", "error"] + entrees +
                             ["-filter_complex", fc, "-map", "[a]",
                              "-t", f"{total:.2f}", voix],
                             capture_output=True, timeout=300)
                if rv.returncode != 0 or not os.path.exists(voix):
                    print(f"  ⚠️ Assemblage de la voix impossible → voix omise")
                    voix = None
                else:
                    print(f"  🔊 Voix calée slide par slide ({len(morceaux)} segments)")
        elif voice_text:
            lu = _texte_pour_voix(voice_text, [], total)
            voix = _gemini_tts(lu, os.path.join(tmpdir, "voix.wav")) if lu else None

        piste = _piste_musicale()
        son = (_melange_voix_musique(voix, piste, os.path.join(tmpdir, "mix.m4a"), total)
               if (voix or piste) else None)
        if not son:
            print(f"  🎬 Carrousel vidéo ({total:.0f}s, {len(pngs)} slides, sans son)")
            return muet

        final = os.path.join(tmpdir, "carrousel.mp4")
        r2 = _sp.run([ff, "-y", "-loglevel", "error", "-i", muet, "-i", son,
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                      "-movflags", "+faststart", final],
                     capture_output=True, timeout=600)
        if r2.returncode == 0 and os.path.exists(final):
            print(f"  🎬 Carrousel vidéo ({total:.0f}s, {len(pngs)} slides, avec son)")
            return final
        return muet
    except Exception as e:
        print(f"  ⚠️ Carrousel vidéo indisponible ({str(e)[:80]})")
        return None


def _carr_narration_slides(carousel):
    """Texte à lire POUR CHAQUE SLIDE du décryptage, dans l'ordre du carrousel.
    ⚠️ Les INTERTITRES ne sont PAS lus : ce sont des repères visuels (« Ce qui va
    changer », « Pourquoi ces travaux »), les entendre à voix haute casse le fil du
    récit. Seuls le titre principal et les points d'information sont narrés.
    Renvoie une liste alignée sur les slides : [couverture, info…, abonnement]."""
    parts = [str(carousel.get("cover_title") or "").strip()]
    for s in (carousel.get("slides") or []):
        pts = [str(p).strip() for p in (s.get("points") or []) if str(p).strip()]
        parts.append(" ".join(pts))          # les points seulement, jamais le titre
    parts.append("")                          # slide d'abonnement : rien à lire
    return parts


def _duree_audio(chemin):
    """Durée d'un fichier audio en secondes, mesurée par ffmpeg. 0 si illisible."""
    try:
        import imageio_ffmpeg as _iff, subprocess as _sp, re as _re
        r = _sp.run([_iff.get_ffmpeg_exe(), "-hide_banner", "-i", chemin],
                    capture_output=True, text=True, timeout=60)
        m = _re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def _carr_texte_narration(carousel):
    """Texte lu sur la vidéo de décryptage : titre puis chaque point, dans l'ordre
    d'apparition. Rien d'inventé — uniquement ce qui est écrit à l'écran."""
    lu = [carousel.get("cover_title", "")]
    for s in (carousel.get("slides") or []):
        lu.append(s.get("titre", ""))
        for p in (s.get("points") or [])[:2]:
            lu.append(p)
    return ". ".join(x.strip() for x in lu if x and str(x).strip())


def _carr_segments(paragraphe):
    """Normalise un paragraphe du modèle en liste de (texte, surligné).
    Format d'entrée : ["texte ", {"hl": "mot clé"}, " suite"]."""
    out = []
    for seg in (paragraphe or []):
        if isinstance(seg, dict) and "hl" in seg:
            out.append((str(seg["hl"]), True))
        else:
            out.append((str(seg), False))
    return out


def _carr_wrap(draw, segments, font, largeur_max):
    """Découpe des segments en lignes, en conservant le surlignage AU MOT PRÈS.
    Renvoie une liste de lignes, chaque ligne étant une liste de (mot, surligné)."""
    mots = []
    for texte, hl in segments:
        # on garde les espaces : ils appartiennent au segment normal qui les porte
        for i, m in enumerate(texte.split(" ")):
            if m == "":
                continue
            mots.append((m, hl))
    lignes, courante, largeur = [], [], 0
    espace = draw.textlength(" ", font=font)
    for mot, hl in mots:
        w = draw.textlength(mot, font=font)
        supp = w + (espace if courante else 0)
        if courante and largeur + supp > largeur_max:
            lignes.append(courante)
            courante, largeur = [(mot, hl)], w
        else:
            courante.append((mot, hl))
            largeur += supp
    if courante:
        lignes.append(courante)
    return lignes


_CARR_INK = {}
def _carr_zone_encre(draw, font):
    """Hauteur d'encre RÉELLE de la police (capitales → jambages), mesurée une fois.
    ⚠️ Pillow ancre le texte sur le haut de l'ascendante, pas sur la ligne de base :
    estimer la position du fond de surlignage le décalait de plus de 10 px vers le haut.
    Renvoie (haut, bas) relatifs au point d'ancrage du texte."""
    cle = (getattr(font, "path", ""), font.size)
    if cle not in _CARR_INK:
        bb = draw.textbbox((0, 0), "HxpgÉÀ", font=font)   # capitales, jambages, accents
        _CARR_INK[cle] = (bb[1], bb[3])
    return _CARR_INK[cle]


def _carr_dessine_paragraphe(img, draw, segments, font, x, y, largeur_max, accent):
    """Dessine un paragraphe avec ses surlignages (fond arrondi à la couleur d'accent).
    Renvoie la hauteur consommée."""
    espace = draw.textlength(" ", font=font)
    pad_x, pad_y, rayon = 8, 3, 6              # padding du gabarit ; rayon 6px
    haut, bas = _carr_zone_encre(draw, font)
    # ↕️ L'interligne doit laisser respirer les surlignages : la chaîne de référence
    #    inclut les capitales accentuées (É, À), qui montent haut — sans cette marge,
    #    les fonds de deux lignes successives se touchent et forment un pavé continu.
    ECART_MINI = 10
    interligne = max(int(font.size * 1.5), (bas - haut) + 2 * pad_y + ECART_MINI)
    coul_txt = _carr_texte_sur_accent(accent)
    for ligne in _carr_wrap(draw, segments, font, largeur_max):
        cx = x
        for mot, hl in ligne:
            w = draw.textlength(mot, font=font)
            if hl:
                # fond à la couleur d'accent, calé sur l'encre → identique pour tous les
                # mots d'une même ligne, qu'ils aient ou non un jambage
                cal = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(cal).rounded_rectangle(
                    [cx - pad_x, y + haut - pad_y, cx + w + pad_x, y + bas + pad_y],
                    radius=rayon, fill=tuple(accent) + (255,))
                img.alpha_composite(cal)
                draw = ImageDraw.Draw(img)
                draw.text((cx, y), mot, font=font, fill=coul_txt)
            else:
                # texte blanc avec ombre portée (text-shadow du gabarit)
                draw.text((cx + 2, y + 2), mot, font=font, fill=(0, 0, 0, 170))
                draw.text((cx, y), mot, font=font, fill=(255, 255, 255))
            cx += w + espace
        y += interligne
    return y


def _carr_font_ajustee(draw, lignes, px, largeur_max, mini=0.55):
    """Choisit la plus grande taille de titre qui TIENNE dans la largeur, en repliant
    les lignes trop longues si nécessaire.
    Indispensable : les polices d'affichage n'ont pas la même largeur à taille égale
    (Archivo Black déborde là où Bebas Neue laisse du vide), et un titre bavard — ou un
    seul mot très long — déborderait quelle que soit la police.
    Renvoie (police, lignes_ajustées) et garantit qu'AUCUNE ligne ne dépasse."""
    src = [str(l).upper() for l in (lignes or []) if str(l).strip()]
    if not src:
        return _carr_font(px, titre=True), []

    def _replie(f, lignes_src):
        out = []
        for l in lignes_src:
            if draw.textlength(l, font=f) <= largeur_max:
                out.append(l); continue
            cur = ""
            for mot in l.split():
                essai = (cur + " " + mot).strip()
                if cur and draw.textlength(essai, font=f) > largeur_max:
                    out.append(cur); cur = mot
                else:
                    cur = essai
            if cur:
                out.append(cur)
        return out

    confort = max(16, int(px * mini))
    # ① taille maximale où tout tient SANS replier (rendu le plus proche du modèle)
    t = int(px)
    while t >= confort:
        f = _carr_font(t, titre=True)
        if all(draw.textlength(l, font=f) <= largeur_max for l in src):
            return f, src
        t -= 2
    # ② sinon on replie, en réduisant jusqu'à ce que même les mots isolés tiennent
    t = confort
    while t >= 18:
        f = _carr_font(t, titre=True)
        out = _replie(f, src)
        if all(draw.textlength(l, font=f) <= largeur_max for l in out):
            return f, out
        t -= 2
    f = _carr_font(18, titre=True)
    return f, _replie(f, src)


def _carr_titre(draw, lignes, font, x, y, centre=False, largeur=None, stroke=5):
    """Titre en CAPITALES, blanc contouré de sombre, interligne serré (0.94)."""
    interligne = int(font.size * 0.94)
    for l in (lignes or []):
        t = str(l).upper()
        px = x
        if centre and largeur:
            px = x + (largeur - draw.textlength(t, font=font)) // 2
        draw.text((px, y), t, font=font, fill=(255, 255, 255),
                  stroke_width=stroke, stroke_fill=CARR_STROKE)
        y += interligne
    return y


def _carr_fond(img, raw_photo):
    """Fond : photo de l'article recadrée en couverture, sinon dégradé sobre.
    ⚠️ Jamais d'image de stock : la charte Pulse l'interdit."""
    try:
        if raw_photo:
            import io as _io
            ph = Image.open(_io.BytesIO(raw_photo)).convert("RGB")
            sw, sh = ph.size
            k = max(CARR_W / sw, CARR_H / sh)
            nw, nh = int(sw * k + 0.5), int(sh * k + 0.5)
            ph = ph.resize((nw, nh), Image.LANCZOS)
            fx, fy = (nw // 2, int(nh * 0.38))
            try:
                f = detect_face_center(ph)
                if f:
                    fx, fy = f
            except Exception:
                pass
            left = max(0, min(int(fx - CARR_W / 2), nw - CARR_W))
            top  = max(0, min(int(fy - CARR_H / 2), nh - CARR_H))
            img.paste(ph.crop((left, top, left + CARR_W, top + CARR_H)).convert("RGBA"), (0, 0))
            return True
    except Exception:
        pass
    d = ImageDraw.Draw(img)
    for y in range(CARR_H):
        t = y / CARR_H
        d.line([(0, y), (CARR_W, y)],
               fill=(int(58 + 40 * t), int(44 + 26 * t), int(96 + 40 * t), 255))
    return False


def _carr_voiles(img, sombre):
    """Dégradé violet du haut + voile général (classes .grad et .scrim du gabarit)."""
    cal = Image.new("RGBA", (CARR_W, CARR_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(cal)
    # .grad : violet 72 % en haut → 18 % à 24 % → transparent à 48 %
    for y in range(int(CARR_H * 0.48)):
        t = y / (CARR_H * 0.48)
        a = 0.72 - 0.54 * min(1.0, t / 0.5) if t < 0.5 else 0.18 * (1 - (t - 0.5) / 0.5)
        d.line([(0, y), (CARR_W, y)], fill=(96, 66, 150, int(max(0, a) * 255)))
    img.alpha_composite(cal)
    if sombre:
        # .scrim : 40 % en haut → 52 % en bas, pour que le texte reste lisible
        cal2 = Image.new("RGBA", (CARR_W, CARR_H), (0, 0, 0, 0))
        d2 = ImageDraw.Draw(cal2)
        for y in range(CARR_H):
            t = y / CARR_H
            d2.line([(0, y), (CARR_W, y)],
                    fill=(int(42 - 28 * t), int(28 - 17 * t), int(74 - 46 * t),
                          int((0.40 + 0.12 * t) * 255)))
        img.alpha_composite(cal2)


def build_carousel_png(slide, watermark="@PULSEactus", accent=CARR_ACCENT, raw_photo=None):
    """Rend UNE slide de carrousel au format du gabarit fourni (1080×1350).
    `slide` : {"kind": cover|recapCover|info|cta, ...} — voir le modèle.
    Renvoie les octets PNG, ou None."""
    try:
        import io as _io
        img = Image.new("RGBA", (CARR_W, CARR_H), (201, 199, 210, 255))
        kind = slide.get("kind", "info")
        _carr_fond(img, raw_photo)
        _carr_voiles(img, kind in ("info", "recapCover"))
        d = ImageDraw.Draw(img)

        # pastille « k/total » en haut à droite
        if slide.get("pageLabel"):
            f = _carr_font(30)
            t = str(slide["pageLabel"])
            tw = d.textlength(t, font=f)
            x1, y1 = CARR_W - 34, 34
            x0, y0 = x1 - tw - 44, y1
            cal = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(cal).rounded_rectangle([x0, y0, x1, y0 + f.size + 16],
                                                  radius=24, fill=(28, 23, 42, 153))
            img.alpha_composite(cal); d = ImageDraw.Draw(img)
            d.text((x0 + 22, y0 + 8), t, font=f, fill=(255, 255, 255))

        M = 64
        larg = CARR_W - 2 * M

        if kind == "cover":
            # badge de catégorie en haut, titre calé en bas
            if slide.get("category"):
                f = _carr_font(30)
                t = str(slide["category"]).upper()
                tw = d.textlength(t, font=f)
                cal = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(cal).rounded_rectangle([M, 64, M + tw + 40, 64 + f.size + 16],
                                                      radius=8, fill=(242, 240, 245, 255))
                img.alpha_composite(cal); d = ImageDraw.Draw(img)
                d.text((M + 20, 72), t, font=f, fill=(32, 32, 42))
            f, tl = _carr_font_ajustee(d, slide.get("titleLines"), 98, larg)
            _carr_titre(d, tl, f, M, CARR_H - 120 - int(f.size * 0.94) * max(1, len(tl)))

        elif kind == "recapCover":
            if slide.get("subjectTag"):
                f = _carr_font(28)
                t = str(slide["subjectTag"]).upper()
                tw = d.textlength(t, font=f)
                x0 = (CARR_W - tw - 48) // 2
                cal = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(cal).rounded_rectangle(
                    [x0, CARR_H // 2 - 220, x0 + tw + 48, CARR_H // 2 - 220 + f.size + 16],
                    radius=24, fill=(28, 23, 42, 140))
                img.alpha_composite(cal); d = ImageDraw.Draw(img)
                d.text((x0 + 24, CARR_H // 2 - 212), t, font=f, fill=(255, 255, 255))
            f, tl = _carr_font_ajustee(d, slide.get("titleLines"), 112, larg)
            _carr_titre(d, tl, f, M,
                        CARR_H // 2 - int(f.size * 0.94) * max(1, len(tl)) // 2 + 20,
                        centre=True, largeur=larg)

        elif kind == "cta":
            y = 80
            f, tl = _carr_font_ajustee(d, slide.get("ctaLines"), 72, larg)
            y = _carr_titre(d, tl, f, M, y)
            if slide.get("ctaSub"):
                fs = _carr_font(36, titre=True)
                d.text((M, y + 20), str(slide["ctaSub"]).upper(), font=fs,
                       fill=(255, 255, 255), stroke_width=2, stroke_fill=CARR_STROKE)
            if slide.get("ctaBig"):
                fb, _tb = _carr_font_ajustee(d, [slide["ctaBig"]], 92, larg)
                t = (_tb[0] if _tb else str(slide["ctaBig"]).upper())
                tw = d.textlength(t, font=fb)
                d.text(((CARR_W - tw) // 2, CARR_H - 300), t, font=fb,
                       fill=(255, 255, 255), stroke_width=6, stroke_fill=CARR_STROKE)

        else:  # info
            y = 70
            if slide.get("titleLines"):
                f, tl = _carr_font_ajustee(d, slide["titleLines"], 76, larg)
                y = _carr_titre(d, tl, f, M, y) + 44
            fp = _carr_font(33, gras=False)
            for p in (slide.get("paras") or []):
                y = _carr_dessine_paragraphe(img, d, _carr_segments(p), fp, M, y, larg, accent)
                d = ImageDraw.Draw(img)
                y += 24                      # margin-bottom du gabarit
            if slide.get("kicker"):
                fk, tk = _carr_font_ajustee(d, [slide["kicker"]], 78, larg)
                _carr_titre(d, tk, fk, M, CARR_H - 260 - int(fk.size * 0.94) * (len(tk) - 1),
                            centre=True, largeur=larg)

        # filigrane centré en bas
        if watermark:
            fw = _carr_font(22)
            t = " ".join(str(watermark).upper())      # letter-spacing: 6px
            tw = d.textlength(t, font=fw)
            d.text(((CARR_W - tw) // 2, CARR_H - 44 - fw.size), t, font=fw,
                   fill=(255, 255, 255, 209))

        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"  ⚠️ Slide carrousel non rendue ({str(e)[:80]})")
        return None


def build_carousel_slide(title, points, idx, total, is_last=False, accent=(255, 90, 200), bg_photo=None,
                         reveal=1.0, as_image=False, bg_cache=None, W=1080, H=1350):
    """Génère une slide de contenu dans la DA Pulse.
    reveal ∈ [0,1] : fraction des MOTS affichés (titre d'abord, puis les puces) — permet à la vidéo
    de faire « s'écrire » le texte au fur et à mesure SANS dupliquer la mise en page (source unique).
    as_image=True renvoie l'image PIL (pour la vidéo) au lieu des bytes PNG.
    bg_cache : fond pré-calculé à réutiliser (perf vidéo : le fond ne change pas entre les états)."""
    import io
    margin = int(W * 0.07)

    if bg_cache is not None:
        img = bg_cache.copy()
        draw = ImageDraw.Draw(img)
    else:
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
        if paste_pulse_logo(img, margin, int(H * 0.045), int(H * 0.045)) == 0:
            draw.text((margin, int(H * 0.045)), "Pulse", font=_cfont(int(W * 0.05)), fill=(255, 255, 255))

        # pastille page i/N
        pl = f"{idx}/{total}"
        fp = _cfont(int(W * 0.028), bold=True)
        bb = draw.textbbox((0, 0), pl, font=fp); pw = bb[2] - bb[0] + 34; ph2 = bb[3] - bb[1] + 22
        px0 = W - pw - margin; py0 = int(H * 0.045)
        pov = Image.new('RGBA', (W, H), (0, 0, 0, 0)); pdd = ImageDraw.Draw(pov)
        pdd.rounded_rectangle([px0, py0, px0 + pw, py0 + ph2], radius=ph2 // 2,
                              fill=(255, 255, 255, 40), outline=(255, 255, 255, 150), width=2)
        img = Image.alpha_composite(img.convert('RGBA'), pov).convert('RGB'); draw = ImageDraw.Draw(img)
        draw.text((px0 + 17, py0 + 8), pl, font=fp, fill=(255, 255, 255))

    # ── TEXTE : compteur global de mots (titre puis puces) ; on ne dessine que les `visible` premiers ──
    title_wc = len(title.split())
    total_wc = title_wc + sum(len(p.split()) for p in points)
    # 🌊 FLUIDITÉ : le mot suivant entre en FONDU (alpha = fraction décimale de la progression).
    _prog   = total_wc if reveal >= 1.0 else max(0.0, min(1.0, reveal)) * total_wc
    visible = int(_prog)
    _fade_a = (_prog - visible) if reveal < 1.0 else 0.0
    widx = 0   # index global du prochain mot

    def _draw_words(x0, y, line, font, fill):
        """Dessine les mots d'une ligne un par un ; le mot en cours entre en FONDU.
        ⚠️ Pillow ignore l'alpha d'un fill sur un canvas RGB : le mot fondu passe donc
        par un CALQUE RGBA composité — seul moyen d'obtenir une vraie transparence."""
        nonlocal widx, img, draw
        x = x0
        for word in line.split(" "):
            if not word:
                continue
            if widx < visible:
                draw.text((x, y), word, font=font, fill=fill)
            elif widx == visible and _fade_a > 0.04:
                _wl = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(_wl).text((x, y), word, font=font,
                                         fill=(fill[0], fill[1], fill[2], int(255 * _fade_a)))
                img = Image.alpha_composite(img.convert("RGBA"), _wl).convert("RGB")
                draw = ImageDraw.Draw(img)
            widx += 1
            x += draw.textlength(word + " ", font=font)

    # titre (auto-dimensionné pour tenir)
    # ⚠️ Les tailles se calculent sur la HAUTEUR, pas la largeur : sinon un cadre large (16:9)
    #    produit un texte énorme sur un cadre court. Ces fractions reproduisent EXACTEMENT
    #    les tailles historiques du portrait 1080×1350. La colonne de texte est resserrée
    #    en format large pour éviter des lignes interminables.
    _large = (W / max(1, H)) > 1.2
    col_t = int(W * (0.70 if _large else 0.86))
    col_p = int(W * (0.66 if _large else 0.80))
    y = int(H * 0.15)
    tsize = int(H * 0.0576)
    for trysize in (int(H * 0.0576), int(H * 0.0504), int(H * 0.0440)):
        if len(_wrap(draw, title, _cfont(trysize), col_t)) <= 3:
            tsize = trysize; break
    f_title = _cfont(tsize)
    for ln in _wrap(draw, title, f_title, col_t):
        _draw_words(margin, y, ln, f_title, (255, 255, 255)); y += int(tsize * 1.15)

    # trait d'accent : apparaît quand le TITRE est entièrement écrit
    y += 12
    if title and visible >= title_wc:
        draw.rounded_rectangle([margin, y, margin + int(W * 0.18), y + 10], radius=5, fill=accent)
    y += int(H * 0.045)

    # points à puces (auto-dimensionnés selon le nombre) — la puce apparaît avec son 1er mot
    psize = int(H * 0.0352) if len(points) <= 3 else int(H * 0.0312)
    f_pt = _cfont(psize, bold=False)
    _bas = H - int(H * (VIDEO_SAFE_BOTTOM if H > W else 0.10))   # plancher : on n'écrit pas dessous
    for pt in points:
        if y + psize * 1.4 > _bas:      # 🛡️ plus de place : on s'arrête proprement
            break
        if widx < visible:
            draw.ellipse([margin, y + 13, margin + 18, y + 31], fill=accent)
        for ln in _wrap(draw, pt, f_pt, col_p):
            if y + psize * 1.4 > _bas:
                break
            _draw_words(margin + 40, y, ln, f_pt, (238, 232, 250)); y += int(psize * 1.32)
        y += int(H * 0.018)

    # CTA sur la dernière slide : quand tout le texte est écrit
    if is_last and visible >= total_wc:
        # en 9:16 (vidéo X), on remonte le CTA au-dessus de l'interface immersive ;
        # en 4:5 (carrousel Instagram), position inchangée.
        _cta_y = int(H * 0.9) if (H / max(1, W)) < 1.4 else H - int(H * VIDEO_SAFE_BOTTOM)
        draw.text((margin, _cta_y), "→ Plus d'infos sur X : @PULSEactus",
                  font=_cfont(int(W * 0.04), bold=True), fill=accent)

    if as_image:
        return img
    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

def build_carousel_bg(idx, total, accent=(255, 90, 200), bg_photo=None, W=1080, H=1350):
    """Fond « chrome » d'une slide (dégradé/photo + barre + logo + pastille), SANS texte —
    pré-calculé une fois par slide pour l'animation vidéo (perf)."""
    return build_carousel_slide("", [], idx, total, accent=accent, bg_photo=bg_photo,
                                reveal=0.0, as_image=True, W=W, H=H)

def build_follow_slide(accent=(255, 90, 200), W=1080, H=1350):
    """Slide de FIN de la vidéo décryptage : appel à s'abonner, dans la DA Pulse."""
    img = _neon_bg(W, H)
    draw = ImageDraw.Draw(img)
    for x in range(W):
        draw.line([(x, 0), (x, 10)], fill=_lerp((90, 140, 255), (255, 80, 200), x / W))
    # grand logo centré
    lw = paste_pulse_logo(img, 0, 0, int(H * 0.075))
    if lw:
        img2 = _neon_bg(W, H); d2 = ImageDraw.Draw(img2)
        for x in range(W):
            d2.line([(x, 0), (x, 10)], fill=_lerp((90, 140, 255), (255, 80, 200), x / W))
        paste_pulse_logo(img2, (W - lw) // 2, int(H * 0.34), int(H * 0.075))
        img, draw = img2, d2
    else:
        draw.text((W // 2, int(H * 0.37)), "PULSE", font=_cfont(int(W * 0.09)), fill=(255, 255, 255), anchor="mm")
    draw.text((W // 2, int(H * 0.50)), "Suis toute l'actu en temps réel",
              font=_cfont(int(W * 0.045)), fill=(255, 255, 255), anchor="mm")
    draw.text((W // 2, int(H * 0.575)), "Abonne-toi → @PULSEactus",
              font=_cfont(int(W * 0.05), bold=True), fill=accent, anchor="mm")
    return img

def gather_articles_with_urls(limit_per_feed=4):
    """Récupère les articles récents avec leur URL (pour pouvoir lire l'article complet)."""
    arts = []
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:limit_per_feed]:
                title = _titre_propre(entry.get("title", ""))
                if not title:
                    continue
                summ = re.sub(r"<[^>]+>", "", entry.get("summary", entry.get("description", "")))
                # Date de publication (epoch) pour juger la fraîcheur — clé pour le suivi live des matchs
                pub_ts = None
                for fld in ("published_parsed", "updated_parsed"):
                    val = entry.get(fld)
                    if val:
                        try:
                            pub_ts = time.mktime(val); break
                        except Exception:
                            pass
                arts.append({
                    "title":   title,
                    "summary": summ[:200],
                    "url":     entry.get("link", ""),
                    "source":  fi["source"],
                    "pub_ts":  pub_ts,
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
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw_bytes = _read_capped(r, cap=1_500_000)
            enc = (r.headers.get("Content-Encoding") or "").lower()
        page = _decode_html_body(raw_bytes, enc)
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
    💰 Le résultat est mis en CACHE pour la journée : si la publication échoue ensuite
    (ex : aucune image exploitable), les runs suivants ne re-paient PAS Claude.
    """
    _ck = "__carousel__" + datetime.now().strftime("%Y-%m-%d")
    try:
        row = conn.execute("SELECT payload FROM daily_cache WHERE key=?", (_ck,)).fetchone()
        if row:
            print("  💾 Décryptage réutilisé (déjà généré aujourd'hui, 0 coût)")
            return json.loads(row[0])
    except Exception:
        pass
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
        pick = _llm_json(f"""Tu es Pulse, média d'actualité française. Aujourd'hui : {today}.

Articles du jour (numérotés) :
{listing}

Sujets déjà traités ces 7 derniers jours (à éviter) : {avoid_str}

Choisis les sujets qui font PARLER et donnent envie de cliquer : affaire/scandale en cours, polémique, drame marquant, événement sportif majeur, décision qui touche directement le portefeuille ou le quotidien des gens, gros buzz. ⛔ ÉVITE ABSOLUMENT les sujets froids/institutionnels : débats techniques (quotas, tarification, mécanismes européens), réformes "à venir", rapports prospectifs, négociations de procédure. Si un sujet ressemble à un cours d'économie, ne le choisis pas.

{deja_str}

Donne ton TOP 3 par ordre de préférence (le meilleur en premier).

Réponds avec ce JSON UNIQUEMENT :
{{"indices": [<n°1>, <n°2>, <n°3>], "sujet":"<2-4 mots sur le n°1>", "cover_title":"<accroche ≤60 caractères pour le n°1>", "image_query":"<5 mots-clés ANGLAIS décrivant une PHOTO du sujet n°1, ex 'paris police protest night'>", "keywords":["<1 à 2 NOMS PROPRES du sujet n°1 pour hashtag : entreprise/personne/lieu/événement central, ex 'SpaceX' ou 'Nahel'>"]}}""", max_tokens=300, task="special")

        # On privilégie le 1er sujet du top 3 qui a une VRAIE photo (og:image).
        # Sinon on garde quand même le meilleur sujet : la couverture utilisera image_query (jamais SANS image).
        indices = pick.get("indices") or ([pick["index"]] if isinstance(pick.get("index"), int) else [])
        valid = [arts[i] for i in indices[:3] if isinstance(i, int) and 0 <= i < len(arts)]
        if not valid:
            print("  ⚠️ Décryptage : aucun sujet exploitable — on retentera au prochain passage.")
            return None
        art, og_bytes = valid[0], None
        for cand in valid:
            # Cascade COMPLÈTE (og:image + autres images de l'article + Wikipedia), pas juste og:image.
            try:
                raw, real = get_best_image(cand.get("url"), cand.get("photo_url"),
                                           cand.get("person"), None, "actu")
            except Exception:
                raw, real = None, False
            if raw:
                art, og_bytes = cand, raw
                break
        # Si aucun des 3 sujets n'a de vraie photo → couverture sur fond DA (géré à la publication),
        # jamais de stock générique hors-sujet.

        # ÉTAPE 2 : lire l'article complet (pour les vrais chiffres)
        article_text = fetch_article_text(art["url"], max_chars=4000)
        if len(article_text) < 250:
            article_text = f"{art['title']}. {art['summary']}"  # repli si lecture impossible

        # ÉTAPE 3 : générer les slides chiffrées à partir de l'article
        result = _llm_json(f"""Tu es Pulse, média d'actualité française. Voici un article à décrypter en carrousel pédagogique.

SUJET : {art['title']}
ARTICLE :
{article_text}

Crée un carrousel clair, CONCRET et CHIFFRÉ qui explique le sujet étape par étape.

RÈGLES ABSOLUES :
- Utilise UNIQUEMENT les informations de l'article ci-dessus. N'invente AUCUN chiffre.
- ⛔ SUPERLATIFS INTERDITS SANS SOURCE : n'écris JAMAIS « le/la plus [grand·imposant·important...] de l'histoire », « jamais vu/organisé », « du jamais-vu », « record absolu », « sans précédent », « inédit », « historique » SAUF si l'article le dit EXPLICITEMENT. Sinon reste factuel (« un défilé de 6 700 soldats », PAS « le défilé le plus imposant jamais organisé »).
- REFORMULE avec tes propres mots, ne recopie jamais des phrases entières (droit d'auteur).
- ⛔ NE DÉFORME PAS LE SENS en reformulant : n'attribue JAMAIS un fait, une origine ou un mérite à la mauvaise culture / personne / pays / groupe. Ex : un haka est une tradition MAORI / du Pacifique exécutée ici par des soldats ultramarins d'Océanie — ne le transforme JAMAIS en « tradition guerrière française ». Reste fidèle à QUI fait quoi et à quelle culture/pays appartient quoi.
- 📊 LES CHIFFRES D'ABORD : vise AU MOINS 4 données chiffrées sur l'ensemble du carrousel (montants en €, pourcentages, quantités, nombres de personnes, dates, classements...). Fais une slide "Les chiffres clés" si l'article s'y prête.
- ⛔ INTERDIT les phrases vagues et creuses du type "le marché se complexifie", "de plus en plus diverse et imprévisible", "les habitudes changent", "un phénomène croissant". CHAQUE point doit apporter une info CONCRÈTE : un chiffre, un nom propre, un lieu, une date ou un fait précis.
- Si l'article manque de chiffres, mets en avant les faits les plus concrets (noms, pays concernés, décisions précises) — JAMAIS de généralités.
- 🇫🇷 FRANÇAIS IMPECCABLE : zéro mot en anglais, zéro faute. Relis-toi.
- EXACTEMENT 4 slides de contenu. Titre court (≤ 32 caractères) + 2 à 3 points.
- Chaque point : UNE phrase courte et factuelle (≤ 110 caractères), avec un chiffre ou un fait précis. PAS d'emoji dans les points.

EN PLUS des slides, écris le TEASER pour X ("tweet_points") : 3 puces AUTONOMES qui résument le sujet
pour quelqu'un qui ne verra JAMAIS les slides. Chaque puce = une phrase COMPLÈTE et compréhensible SEULE
(qui/quoi/où), un fait ou un chiffre précis, ≤ 120 caractères, zéro jargon. Lues à la suite, les 3 puces
doivent raconter l'histoire en entier. Relis-les : si une puce n'est pas comprise sans contexte, réécris-la.
Donne aussi "keywords" : 1 à 2 NOMS PROPRES centraux de CET article (entreprise/personne/lieu/événement),
pour le hashtag du tweet.

Réponds avec ce JSON UNIQUEMENT :
{{"cover_title":"<accroche de couverture ≤60 caractères, percutante>","tweet_points":["...","...","..."],"keywords":["..."],"slides":[{{"titre":"...","points":["...","..."]}},{{"titre":"...","points":["...","..."]}},{{"titre":"...","points":["...","..."]}},{{"titre":"...","points":["...","..."]}}]}}""", max_tokens=1300, task="special")

        slides = result.get("slides", [])
        slides = [s for s in slides if s.get("titre") and s.get("points")][:4]
        if len(slides) < 3:
            return None
        _out = {
            "sujet":       pick.get("sujet", "Décryptage")[:40],
            "cover_title": (result.get("cover_title") or pick.get("cover_title") or art["title"])[:80],
            "image_query": pick.get("image_query", "news france"),
            # keywords de l'étape 3 (qui a LU l'article réellement décrypté) prioritaires : l'étape 1
            # décrivait le sujet n°1 du top 3, or on peut avoir basculé sur le n°2/n°3 (photo) →
            # c'est ce décalage qui produisait des hashtags HORS SUJET.
            "keywords":    (result.get("keywords") or pick.get("keywords", []))[:2],
            "tweet_points": [str(p).strip() for p in (result.get("tweet_points") or []) if str(p).strip()][:3],
            "slides":      slides,
            "url":         art["url"],
            "summary":     art["summary"],
            "og_bytes":    og_bytes,   # og:image si trouvée, sinon None → repli image_query à la publication
        }
        try:
            conn.execute("INSERT OR REPLACE INTO daily_cache (key, payload) VALUES (?,?)",
                         (_ck, json.dumps(_out, ensure_ascii=False)))
            conn.commit()
        except Exception:
            pass
        return _out
    except Exception as e:
        print(f"  ⚠️ gen_carousel: {e}")
        return None

def carousel_to_text(carousel):
    """Construit le texte du décryptage (X + Facebook).
    Priorité aux "tweet_points" : 3 puces écrites POUR le tweet, compréhensibles SEULES.
    Repli (ancien comportement) : collage du meilleur point de chaque slide — moins lisible,
    car ces points ont été écrits pour être lus SOUS le titre de leur slide."""
    out = f"🧵 {carousel['cover_title']}\n\n"
    pts = carousel.get("tweet_points") or []
    if len(pts) >= 2:
        for p in pts:
            out += f"▸ {p}\n"
    else:
        for s in carousel["slides"]:
            spts = s.get("points") or []
            if not spts:
                continue
            best = max(spts, key=lambda p: (bool(re.search(r"\d", p)), -len(p)))
            out += f"▸ {best}\n"
    out += "\n\nLe décryptage complet en vidéo 👇"
    return out.strip()

def post_carousel_to_instagram(slides_png, caption):
    """Publie un carrousel (2 à 10 images) sur Instagram via l'API Graph."""
    if meta_backoff_active():
        print("  ⏸️ Carrousel Instagram sauté (pause Meta en cours)")
        return None
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
        r = _llm_json(f"""Analyse ce titre/résumé d'actualité sportive.
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
{{"ok":true,"type":"race","sport":"CYCLISME","competition":"Tour de France","winner_name":"Pogacar","detail":"Étape 12"}}""", max_tokens=260, task="analyse")
        if not r or not r.get("ok"):
            return None
        t     = r.get("type")
        comp  = _smart_truncate(str(r.get("competition", "")), 26)
        sport = str(r.get("sport", "")).strip().upper()[:14]
        if t == "match":
            sa, sb = int(r["score_a"]), int(r["score_b"])
            ta, tb = _smart_truncate(str(r.get("team_a", "")), 22), _smart_truncate(str(r.get("team_b", "")), 22)
            if not ta or not tb:
                return None
            return {"type": "match", "sport": sport, "competition": comp,
                    "team_a": ta, "score_a": sa, "team_b": tb, "score_b": sb,
                    "winner": "A" if sa > sb else ("B" if sb > sa else "NUL")}
        if t == "tennis":
            pa, pb = _smart_truncate(str(r.get("player_a", "")), 22), _smart_truncate(str(r.get("player_b", "")), 22)
            win = r.get("winner")
            if not pa or not pb or win not in ("A", "B"):
                return None
            return {"type": "tennis", "sport": sport or "TENNIS", "competition": comp,
                    "player_a": pa, "player_b": pb,
                    "sets": _smart_truncate(str(r.get("sets", "")), 30), "winner": win}
        if t == "race":
            wn = _smart_truncate(str(r.get("winner_name", "")), 26)
            if not wn:
                return None
            return {"type": "race", "sport": sport, "competition": comp,
                    "winner_name": wn, "detail": _smart_truncate(str(r.get("detail", "")), 30)}
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

def _fetch_crest_png(crest_url):
    """Récupère un blason/drapeau d'équipe depuis l'API football-data et le convertit en
    image PIL. Gère le cas SVG (non lisible par PIL) en le sautant proprement."""
    if not crest_url:
        return None
    try:
        if crest_url.lower().endswith(".svg"):
            return None      # PIL ne lit pas le SVG et on n'ajoute pas de dépendance
        raw = fetch_img(crest_url)
        if not raw:
            return None
        import io
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None

def build_france_match_bg(live, W=1200, H=675):
    """Fond FIABLE pour un post de match des Bleus : dégradé DA Pulse + les deux blasons
    d'équipe (fournis par l'API football-data, donc toujours pertinents pour LA rencontre).
    Si les blasons sont indisponibles (SVG/erreur), renvoie None → carte score sur fond DA."""
    try:
        import io
        crest_a = _fetch_crest_png(live.get("crest_a"))
        crest_b = _fetch_crest_png(live.get("crest_b"))
        if not crest_a and not crest_b:
            return None      # aucun blason exploitable → on laisse la carte score gérer le fond
        # Dégradé nocturne DA Pulse
        bg = Image.new("RGB", (W, H), (14, 11, 32))
        top, bot = (30, 16, 70), (90, 20, 90)
        px = bg.load()
        for y in range(H):
            t = y / H
            r = int(top[0] + (bot[0] - top[0]) * t)
            g = int(top[1] + (bot[1] - top[1]) * t)
            b = int(top[2] + (bot[2] - top[2]) * t)
            for x in range(W):
                px[x, y] = (r, g, b)
        # Blasons : un à gauche, un à droite, taille raisonnable
        sz = int(H * 0.42)
        def place(crest, cx):
            if not crest:
                return
            c = crest.copy()
            c.thumbnail((sz, sz), Image.LANCZOS)
            bg.paste(c, (cx - c.width // 2, H // 2 - c.height // 2 - 40), c)
        place(crest_a, int(W * 0.27))
        place(crest_b, int(W * 0.73))
        buf = io.BytesIO()
        bg.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return None

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
    "meurt", "décède", "mort à l'âge", "morte à l'âge", "nous a quittés",
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
    "coupable", "reconnu coupable", "plaide coupable", "tueur", "tueuse", "criminel",
    "serial killer", "violeur", "agresseur", "kidnappeur", "ravisseur", "auteur présumé",
    "auteur du meurtre", "responsable de la mort", "à perpétuité", "réclusion", "prison à vie",
    "torture", "viol", "agression sexuelle", "pédocriminel", "terroriste",
    "commémor", "anniversaire", "an après", "ans après", "émeutes", "justice pour",
    "rouvre", "rouvrant", "réouverture", "rebondissement", "révélations sur",
    # ── Personne VIVANTE déclarée morte par erreur (administrative) → surtout PAS un hommage ──
    "déclaré mort", "déclarée morte", "déclaré décédé", "déclarée décédée",
    "mort par erreur", "morte par erreur", "déclaré mort par erreur", "à tort",
    "par erreur", "encore en vie", "toujours en vie", "bien vivant", "bien vivante",
    "n'est pas mort", "n'est pas morte", "pas vraiment mort", "faussement déclaré",
    "erreur administrative", "rayé des vivants", "considéré comme mort", "considérée comme morte",
)

def _is_urgent_alert(title, summary):
    """Détecte une alerte de DANGER IMMINENT qui doit passer coûte que coûte (contourne la cadence) :
    tsunami, évacuation, alerte rouge, attentat/fusillade en cours, séisme de forte magnitude.
    Conçu pour éviter les faux positifs (mots-clés de danger physique réel, pas métaphoriques)."""
    t = (str(title or "") + " " + str(summary or "")).lower()
    strong = ("tsunami", "alerte rouge", "évacuation", "évacuer", "attentat",
              "fusillade", "prise d'otage", "mettez-vous à l'abri", "se mettre à l'abri",
              "immédiatement en hauteur", "alerte enlèvement")
    if any(k in t for k in strong):
        return True
    if "séisme" in t or "tremblement de terre" in t or "magnitude" in t:
        m = re.search(r"magnitude\s*(\d[\.,]?\d?)", t)
        if m:
            try:
                return float(m.group(1).replace(",", ".")) >= 5.5
            except Exception:
                return False
    return False

def _is_obituary(title, summary):
    """Vrai UNIQUEMENT si l'article ANNONCE le décès d'une PERSONNE (personnalité).
    Approche robuste (pas une simple liste de mots) :
      1. exclusions dures (bilans collectifs, criminels, affaires, mort métaphorique, erreur admin)
      2. un marqueur de décès doit matcher en MOT ENTIER (jamais en sous-chaîne)
      3. les tournures 'mort de X' ne comptent que si X n'est PAS une émotion/chose abstraite.
    """
    t = (title + " " + summary).lower()

    # ── 1a. Exclusions DURES par mots entiers (bilans, expressions, négations) ──
    if re.search(r"\b(morts|tués|tues|victimes|bilan|blessés|disparus|disparues)\b", t):
        return False
    if re.search(r"\bpeine de mort\b|\bmise à mort\b|\bà mort\b|\bmort cérébrale\b|"
                 r"\bmort clinique\b|\bmort subite\b|\bne meurt (jamais|pas)\b|\bpour mourir\b", t):
        return False
    # 'mort de rire/peur/faim/fatigue...' = expression idiomatique, jamais un décès réel
    if re.search(r"\bmort[e]?\s+d[e']\s*(?:l[ae'] )?"
                 r"(rire|peur|faim|fatigue|honte|trouille|ennui|épuisement|epuisement|"
                 r"chaud|froid|soif|jalousie|stress|vieillesse)\b", t):
        return False
    # 'pas mort d'homme' / 'y'a pas mort d'homme' = expression française signifiant 'rien de grave'
    # 'mort de sa belle mort' = expression signifiant 'décès naturel pacifique' (pas une annonce)
    if re.search(r"\bpas mort d[e']|y.?a pas mort|mort de sa belle mort|"
                 r"faire le mort|jouer le mort|joue le mort|"
                 r"se faire passer pour mort|plus mort que vif|à moitié mort|à demi mort\b", t):
        return False

    # ── 1b. Contexte CRIMINEL / JUDICIAIRE → jamais un hommage (victime ou criminel) ──
    #    (un tueur condamné, un procès, une enquête sur un meurtre ne sont PAS un hommage)
    if re.search(r"\b(coupable|meurtri\w+|tueur|tueuse|criminel\w*|violeur|violeurs|agresseur|"
                 r"kidnappeur|ravisseur|assassin\w*|accusé\w*|suspect\w*|soup[çc]onn\w+|inculp\w+|"
                 r"condamn\w+|acquitt\w+|relax\w+|mis en examen|mise en examen|"
                 r"garde à vue|interpell\w+|écrou\w+|réclusion|perpétuité|prison à vie|"
                 r"tentative d[e']\s*(assassinat|meurtre|homicide)|attentat|attaque (armée|au couteau|visait)|"
                 r"procès|verdict|réquisitoire|plaidoirie|assises|tribunal|parquet|"
                 r"cour de cassation|cour d'appel|non-lieu|instruction judiciaire|"
                 r"enquête|enquete|torture|\bviol\b|\bviols\b|agression sexuelle|"
                 r"pédocrimin\w+|terroriste|terrorisme|auteur (présumé|du meurtre|des faits))\b", t):
        return False

    # ── 1c. Commémoration / suite / anniversaire d'un décès PASSÉ → pas une annonce ──
    if re.search(r"\b(commémor\w+|hommage rendu|anniversaire|un an après|\d+ ans? après|"
                 r"émeutes|justice pour|rouvre|rouvr\w+|réouverture|rebondissement|"
                 r"révélations sur|il y a un an|il y a \d+ ans)\b", t):
        return False

    # ── 1d. Personne VIVANTE déclarée morte par erreur → surtout PAS un hommage ──
    if re.search(r"\b(déclaré[e]? mort[e]?|déclaré[e]? décédé[e]?|mort[e]? par erreur|"
                 r"à tort|par erreur|encore en vie|toujours en vie|bien vivant[e]?|"
                 r"n'est pas mort[e]?|pas vraiment mort[e]?|faussement déclaré|"
                 r"erreur administrative|rayé des vivants|considéré[e]? comme mort[e]?)\b", t):
        return False

    # ── 1e. Mort MÉTAPHORIQUE : 'la mort de/du/des [chose abstraite]' ──
    if re.search(r"\bmort\s+(?:de\s+la|du|des|d'une|de\s+l')\s+"
                 r"(presse|cinéma|cinema|télé|télévision|television|radio|musique|culture|"
                 r"démocratie|democratie|gauche|droite|industrie|économie|economie|monnaie|euro|"
                 r"vérité|verite|époque|epoque|innocence|vie privée|liberté|liberte|dollar|"
                 r"french touch|french tech|presse écrite|presse papier|jeu|saga|série|serie|"
                 r"2g|3g|4g|diesel|essence|voiture thermique)\b", t):
        return False

    # ── 1f. Décès daté d'une année PASSÉE ('tué en 2023') = pas une annonce fraîche ──
    m = re.search(r"(?:tué|tuée|mort|morte|décédé|décédée|disparu|disparue)[^.]{0,25}?"
                  r"\ben\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|"
                  r"octobre|novembre|décembre\s+)?((?:19|20)\d{2})", t)
    if m and int(m.group(1)) < datetime.now().year:
        return False

    # ── 1g. NOMINATION / SUCCESSION POLITIQUE → jamais un hommage : quelqu'un qui PREND ses
    #    fonctions est vivant. Ex : « Burnham succède à Starmer, il prendra ses fonctions » n'est
    #    PAS un décès. (Ciblé sur la prise de poste, pas les mentions biographiques d'un défunt.) ──
    if re.search(r"succède à|succèdent à|prend(ra|rait|nent)? ses fonctions|prise de fonctions?|"
                 r"investiture|prête serment|prestation de serment|remaniement ministériel|"
                 r"nouveau premier ministre|nouvelle première ministre|accède au pouvoir|"
                 r"forme (un|son) (nouveau )?gouvernement", t):
        return False

    # ── 2. Marqueurs de décès, TOUS testés en MOT ENTIER (regex) ──
    #    'meurt' ne matchera donc jamais dans 'meurtre'. 'mort de' est traité à part (étape 3).
    death_patterns = [
        r"est mort[e]?\b", r"\bdécès d[e']", r"\bdécédé[e]?\b", r"\bdécède\b", r"\bdécédait\b",
        r"s'est éteint[e]?\b", r"\bmeurt\b", r"mort[e]? à l'âge", r"nous a quitté[s]?\b",
        r"\bà l'âge de\b", r"a perdu la vie", r"\bperd la vie\b", r"retrouvé[e]? mort[e]?\b",
        r"n'est plus de ce monde", r"tire sa révérence", r"\bcarnet noir\b",
        r"s'en est allé[e]?\b", r"emporté[e]? par (un|une|le|la|à)",
    ]
    for pat in death_patterns:
        if re.search(pat, t):
            return True

    # ── 3. 'mort de/d' X' : décès SEULEMENT si X n'est pas une émotion / cause abstraite ──
    mde = re.search(r"\bmort[e]?\s+d[e']\s*(?:l[ae'] )?(\w+)", t)
    if mde:
        suite = mde.group(1)
        FAUSSES_CAUSES = {"rire", "peur", "faim", "fatigue", "honte", "trouille", "ennui",
                          "épuisement", "epuisement", "chaud", "froid", "soif", "jalousie",
                          "stress", "vieillesse", "homme", "femme", "gens", "monde", "sens",
                          "cause", "suite", "naturelle", "naturel", "lui", "elle", "ça",
                          "quoi", "rien", "envie", "honte"}
        if suite not in FAUSSES_CAUSES:
            return True

    # ── 3b. 'Mort du / de la / de l' / des + [personne]' (tournure de titre de presse) ──
    #    Ex : « Mort du chanteur Johnny Hallyday », « Mort de l'actrice Jeanne Moreau ».
    #    On exige un mot désignant une personne pour éviter « mort du diesel/cinéma » (déjà exclus en 1e).
    if re.search(r"\bmort[e]?\s+(?:du|de la|de l'|des)\s+"
                 r"(chanteu\w+|chanteuse|acteur|actrice|com[ée]dien\w*|artiste|musicien\w*|"
                 r"r[ée]alisateur\w*|r[ée]alisatrice|[ée]crivain\w*|auteur\w*|autrice|"
                 r"rappeur\w*|rappeuse|animateur\w*|animatrice|journaliste|pr[ée]sentateur\w*|"
                 r"pr[ée]sentatrice|humoriste|dessinateur\w*|peintre|sculpteur\w*|photographe|"
                 r"cin[ée]aste|producteur\w*|productrice|danseur\w*|danseuse|pianiste|guitariste|"
                 r"violoniste|compositeur\w*|parolier\w*|footballeur\w*|sportif\w*|sportive|"
                 r"champion\w*|l[ée]gende|ic[ôo]ne|star|vedette|ministre|d[ée]put[ée]\w*|"
                 r"s[ée]nateur\w*|maire|pr[ée]sident\w*|philosophe|[ée]conomiste|scientifique|"
                 r"chercheur\w*|prix nobel|acad[ée]micien\w*)\b", t):
        return True

    return False

def extract_obituary(title, summary, url=None):
    """Extrait nom / naissance / âge / métier — UNIQUEMENT depuis l'article (zéro invention).
    L'année du décès est ajoutée par Python (vraie année), jamais devinée par Claude."""
    try:
        text = summary[:400]
        if url:
            art = fetch_article_text(url, max_chars=1800)
            if len(art) > 200:
                text = art
        r = _llm_json(f"""On t'a transmis un article annonçant le décès d'une personnalité.

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
Si ce n'est pas le décès d'une personne nommée, réponds {{"ok":false}}.""", max_tokens=220, task="analyse")
        if not r or not r.get("ok"):
            return None
        name = _smart_truncate(str(r.get("name", "")), 40)
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
        return {"name": name, "dates": dates, "desc": _smart_truncate(str(r.get("desc", "")), 40)}
    except Exception as e:
        print(f"  ⚠️ extract_obituary: {e}")
        return None

def build_hommage_card(raw_photo, name, dates, desc, source, W=1080, H=1350):
    """Carte hommage sobre en PORTRAIT : portrait de la personne cadré sur son VISAGE,
    en noir & blanc, + nom + dates (DA Pulse discrète). Le visage est détecté pour ne jamais
    le couper ; à défaut, léger biais vers le haut (le visage est rarement en bas d'un portrait)."""
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

    # fond : portrait recadré SUR LE VISAGE (detect_face_center) + NOIR & BLANC
    if raw_photo:
        try:
            ph = Image.open(io.BytesIO(raw_photo)).convert('RGB')
            tr = W / H
            # on agrandit pour couvrir le cadre portrait, puis on recadre autour du visage
            scale = max(W / ph.width, H / ph.height)
            big = ph.resize((int(ph.width * scale + 0.5), int(ph.height * scale + 0.5)), Image.LANCZOS)
            face = None
            try:
                face = detect_face_center(big, par_ia=True)
            except Exception:
                face = None
            if face:
                fcx, fcy = face
                left = int(fcx - W / 2)
                # visage placé dans le tiers supérieur (portrait digne), jamais coupé en haut
                top = int(fcy - H * 0.34)
            else:
                left = (big.width - W) // 2
                top = int((big.height - H) * 0.18)   # léger biais vers le haut (visage rarement en bas)
            left = max(0, min(left, big.width - W))
            top = max(0, min(top, big.height - H))
            ph = big.crop((left, top, left + W, top + H))
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
# 🎬 TOUTES les vidéos Pulse partagent le MÊME format : 16:9 (1920×1080), comme la carte d'actu.
# X n'accepte que trois ratios (16:9, 1:1, 9:16) et ajoute des bandes noires aux autres.
# Le 16:9 est le format du fil, lisible sans plein écran — cohérence sur hommage,
# décryptage, victoire sportive et actualité.
VIDEO_W, VIDEO_H = 1920, 1080
PORTRAIT_W, PORTRAIT_H = VIDEO_W, VIDEO_H     # alias historique
# ⚠️ Zone basse réservée à l'interface de X en lecture immersive (texte du post + boutons) :
# ~400 px sur 1920 → aucun contenu critique (titre, source) ne doit y descendre.
VIDEO_SAFE_BOTTOM = 0.21
VIDEO_MAX_DUR = 58.0          # < 60 s : la vidéo boucle automatiquement dans le fil
# 🎬 Vidéo « carte animée » des actus : 16:9 plein cadre (aucune bande noire).
# Les images sont calculées en 2× (3840×2160) puis réduites en 1920×1080 → texte très net.
CARD_VIDEO_W, CARD_VIDEO_H = 1920, 1080
CARD_VIDEO_SS = 2
VIDEO_MIX_RATIO = 0.5         # alternance vidéo animée / carte fixe : le fil reste varié et naturel
NEWS_VIDEO_W, NEWS_VIDEO_H, NEWS_VIDEO_DUR = VIDEO_W, VIDEO_H, 7.5

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

def _avg_hash(raw, hash_size=8):
    """Empreinte perceptuelle simple (average-hash) avec PIL seul, sans dépendance.
    Deux versions de la MÊME photo (compressions/tailles différentes) donnent la même
    empreinte → permet un vrai dédoublonnage par contenu, pas par taille de fichier."""
    try:
        from PIL import Image as _I
        import io as _io
        im = _I.open(_io.BytesIO(raw)).convert("L").resize((hash_size, hash_size), _I.LANCZOS)
        px = list(im.getdata())
        avg = sum(px) / len(px)
        bits = 0
        for i, p in enumerate(px):
            if p >= avg:
                bits |= (1 << i)
        return bits
    except Exception:
        return None

def _hamming(a, b):
    return bin(a ^ b).count("1") if (a is not None and b is not None) else 99

def get_article_photos(article_url, max_photos=4, min_w=380, min_h=240, primary=None):
    """Récupère PLUSIEURS vraies photos d'un article (diaporama vidéo), dédoublonnées PAR CONTENU,
    en écartant logos/habillage (BFMTV, etc.). `primary` = photo principale déjà obtenue (og:image) :
    si fournie, elle est placée EN PREMIER (c'est la plus fiable pour le sujet du tweet).
    Best-effort : renvoie [] si la page est bloquée ou sans image assez grande."""
    out, hashes = [], []

    def _consider(raw):
        """Ajoute une image si elle est assez grande, pas un logo, et pas un doublon de contenu."""
        if not raw or len(out) >= max_photos:
            return
        try:
            from PIL import Image as _I
            import io as _io
            w, h = _I.open(_io.BytesIO(raw)).size
        except Exception:
            return
        if w < min_w or h < min_h:
            return
        ratio = w / h
        # Un logo/bandeau est souvent très allongé ou minuscule → on l'écarte
        if ratio > 3.0 or ratio < 0.33:
            return
        hsh = _avg_hash(raw)
        if hsh is not None and any(_hamming(hsh, prev) <= 6 for prev in hashes):
            return          # trop proche d'une image déjà retenue → doublon de contenu
        hashes.append(hsh)
        out.append(raw)

    # 1) la photo principale (og:image) d'abord : c'est le sujet du tweet, la plus sûre
    if primary:
        _consider(primary)
    # 2) les autres images de l'article
    try:
        img_urls = fetch_article_images(article_url) if article_url else []
    except Exception:
        img_urls = []
    BRAND = ("/logo", "-logo", "logo-", "logo.", "watermark", "brand", "signature",
             "/default", "placeholder", "vignette-defaut", "sprite", "favicon",
             "chaine-", "channel-logo", "habillage")
    for img_url in img_urls:
        if len(out) >= max_photos:
            break
        low = img_url.lower()
        if any(b in low for b in BRAND):
            continue        # URL d'un logo/habillage → ignorée (on ne filtre PAS sur le nom du média)
        _consider(fetch_img(img_url))
    return out

def build_news_slideshow_video(photos, headline, category, source):
    """🎬 Vidéo d'actu = DIAPORAMA des vraies photos de l'article (plusieurs images qui défilent),
    Ken Burns doux + fondus enchaînés, bandeau titre en bas dans la DA Pulse. PAS d'effet 'glass'
    (pas de flou/verre dépoli sur la photo principale) : les photos sont nettes, plein cadre.
    Repli : si 0 photo exploitable, renvoie None → l'appelant utilise l'ancienne vidéo animée."""
    import io, shutil, subprocess, tempfile
    if os.environ.get("PULSE_VIDEO", "1") == "0" or not photos:
        return None
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        try:
            import imageio_ffmpeg
            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    try:
        W, H, FPS = VIDEO_W, VIDEO_H, VIDEO_FPS
        PER, XFADE = 2.6, 0.5
        frames_slide = int(FPS * PER)
        frames_fade = int(FPS * XFADE)
        accent = _cat_rgb(category)

        def f(px, bold=True):
            p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
            try: return ImageFont.truetype(p, int(px))
            except Exception: return ImageFont.load_default()

        # Prépare chaque photo : remplit le cadre 16:9 SANS flou (net, plein cadre, recadrage centré)
        prepared = []
        for raw in photos:
            try:
                ph = Image.open(io.BytesIO(raw)).convert("RGB")
            except Exception:
                continue
            pr, tr = ph.width / ph.height, W / H
            if pr > tr:      # photo plus large → on rogne les côtés
                nw = int(ph.height * tr)
                ph = ph.crop(((ph.width - nw) // 2, 0, (ph.width - nw) // 2 + nw, ph.height))
            else:            # photo plus haute → on rogne haut/bas
                nh = int(ph.width / tr)
                ph = ph.crop((0, (ph.height - nh) // 2, ph.width, (ph.height - nh) // 2 + nh))
            # léger sur-cadre pour le Ken Burns (zoom lent)
            ph = ph.resize((int(W * 1.12), int(H * 1.12)), Image.LANCZOS)
            prepared.append(ph)
        if not prepared:
            return None

        # Bandeau titre + source (dessiné une fois, collé sur chaque frame)
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(int(H * 0.52), H):       # dégradé sombre en bas pour lisibilité du titre
            t = (y - H * 0.58) / (H * 0.42)
            od.line([(0, y), (W, y)], fill=(8, 6, 24, int(230 * t)))
        od.rectangle([0, H - 6, W, H], fill=accent + (255,))    # liseré couleur catégorie
        # titre multi-lignes
        title = headline[:120]
        words, lines, cur = title.split(), [], ""
        fnt = f(38)
        for w_ in words:
            test = (cur + " " + w_).strip()
            if od.textbbox((0, 0), test, font=fnt)[2] > W - 90 and cur:
                lines.append(cur); cur = w_
            else:
                cur = test
        lines.append(cur)
        lines = lines[:3]
        y0 = H - 88 - len(lines) * 46
        for ln in lines:
            od.text((46, y0), ln, font=fnt, fill=(255, 255, 255, 255))
            y0 += 46
        if source:
            od.text((46, H - 78), source, font=f(22, bold=False), fill=(210, 205, 230, 235))

        def compose(idx, prog):
            """prog ∈ [0,1] : avancement dans la slide (pour le zoom Ken Burns)."""
            ph = prepared[idx]
            zoom = 1.0 + 0.06 * prog
            cw, ch = int(W * (1.12 / zoom)), int(H * (1.12 / zoom))
            x = (ph.width - cw) // 2
            y = (ph.height - ch) // 2
            frame = ph.crop((x, y, x + cw, y + ch)).resize((W, H), Image.LANCZOS)
            frame = frame.convert("RGBA")
            frame.alpha_composite(overlay)
            return frame.convert("RGB")

        tmpdir = tempfile.mkdtemp(prefix="pulse_news_")
        out_mp4 = os.path.join(tmpdir, "video.mp4")
        proc = subprocess.Popen(
            [ffmpeg_bin, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_mp4],
            stdin=subprocess.PIPE)
        n = len(prepared)
        for i in range(n):
            for fr in range(frames_slide):
                prog = fr / frames_slide
                frame = compose(i, prog)
                if i < n - 1 and fr >= frames_slide - frames_fade:
                    alpha = (fr - (frames_slide - frames_fade)) / frames_fade
                    frame = Image.blend(frame, compose(i + 1, 0.0), alpha)
                proc.stdin.write(frame.tobytes())
        # si une seule photo, on tient un peu plus longtemps dessus
        if n == 1:
            for _ in range(int(FPS * 1.4)):
                proc.stdin.write(compose(0, 1.0).tobytes())
        proc.stdin.close(); proc.wait()
        if proc.returncode != 0 or not os.path.exists(out_mp4):
            shutil.rmtree(tmpdir, ignore_errors=True); return None
        print(f"  🎬 Vidéo actu diaporama générée ({n} photo{'s' if n > 1 else ''})")
        return out_mp4
    except Exception as e:
        print(f"  ⚠️ Diaporama actu échoué : {e}")
        return None

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
        W, H, FPS, DUR = VIDEO_W, VIDEO_H, VIDEO_FPS, VIDEO_DUR   # 16:9, comme la carte d'actu
        if kind == "news":
            DUR = NEWS_VIDEO_DUR      # un peu plus long : le titre s'écrit mot par mot
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
        _anim_pill = _pill_gif_path(category) is not None   # GIF dispo → pilule dessinée omise
        _anim_logo = (_logo_gif_path() is not None) and not sober
        if kind != "hommage" and not urgent and not _anim_pill:
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
        _safe = VIDEO_SAFE_BOTTOM if H > W else 0.085   # zone d'interface : verticale uniquement
        FOOTER_Y = H - int(H * _safe)             # source/date
        tmpd = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        HFONT, HLINES, LH, HY0 = None, [], 0, 0
        if kind == "news":
            headline = re.sub(r'#(\w+)', r'\1', str(data.get("headline", "")))[:120]   # hashtags retirés
            # Portrait : titre large, gros, jusqu'à 5 lignes (style « carte info »)
            HFONT, HLINES = _wrap_fit(tmpd, headline, int(W * 0.86), int(W * 0.062), max_lines=5)
            LH = int(HFONT.size * 1.24)
            HY0 = FOOTER_Y - int(H * 0.035) - LH * len(HLINES)   # bloc collé au-dessus du pied de page

            # 🌑 Dégradé noir montant du bas : la photo reste nette en haut, le texte est lisible en bas.
            # Montée douce au-dessus du titre, puis noir DENSE dès la 1ʳᵉ ligne (contraste type "carte info").
            _y_soft = max(0, HY0 - int(H * 0.20))    # début de l'assombrissement
            _y_text = max(_y_soft + 1, HY0 - int(H * 0.02))
            _col_s = Image.new("RGBA", (1, H), (0, 0, 0, 0))
            for y in range(H):
                if y <= _y_soft:
                    a = 0
                elif y < _y_text:
                    a = int(208 * (((y - _y_soft) / (_y_text - _y_soft)) ** 1.25))
                else:
                    a = 208 + int(44 * (y - _y_text) / max(1, H - _y_text))
                _col_s.putpixel((0, y), (0, 0, 0, min(252, a)))
            news_scrim = _col_s.resize((W, H))

            # ✍️ Texte découpé en MOTS : chacun apparaît l'un après l'autre (effet « qui s'écrit »)
            HWORDS, _x0 = [], int(W * 0.07)
            for i, line in enumerate(HLINES):
                x = _x0
                for word in line.split(" "):
                    if word:
                        HWORDS.append((x, HY0 + i * LH, word))
                        x += int(tmpd.textlength(word + " ", font=HFONT))
            # cadence adaptative : l'écriture s'étale sur ~4 s et se termine ~1,5 s avant la fin,
            # que le titre fasse 8 mots ou 25 (sinon un titre court s'écrit d'un bloc).
            WSTEP = min(0.20, max(0.06, 4.0 / max(1, len(HWORDS) - 1)))
            glass_title_panel = None

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
            if kind != "news":
                img.alpha_composite(bands)   # news : remplacé par le dégradé noir du bas
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
            # 🔷 EN-TÊTE : le VRAI logo PULSE détouré, en fondu doux (il embarque déjà son battement).
            # Hommage : texte sobre conservé. Repli : si le logo échoue, on retombe sur texte + ECG animé.
            la = _appear(t, 0.15, 0.9)
            _logo_drawn = True if _anim_logo else False   # animé → aucun logo fixe dessiné
            if la > 0 and not sober and not _anim_logo:
                if paste_pulse_logo(img, int(W * 0.04), int(H * 0.030), int(H * 0.042), opacity=la) > 0:
                    _logo_drawn = True
                    d = ImageDraw.Draw(img)
            if la > 0 and not _logo_drawn:
                lx_off = int((1 - la) * 22)
                d.text((int(W * 0.04) - lx_off, int(H * 0.055)), "PULSE", font=LOGO_F,
                       fill=WHITE + (int(255 * la),))
            if not _logo_drawn:
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
                # 🌑 Dégradé noir sur le bas de la photo (monte doucement dans la 1ʳᵉ seconde)
                sc_in = _vease_io(t / 0.9)
                sc = news_scrim
                if sc_in < 1:
                    sc = sc.copy()
                    sc.putalpha(sc.split()[3].point(lambda v: int(v * sc_in)))
                img.alpha_composite(sc)
                d = ImageDraw.Draw(img)

                # ✍️ Le titre s'écrit MOT PAR MOT
                bar_in = _ease_quint((t - 0.9) / 0.6)
                if bar_in > 0:                       # petit liseré d'accent qui se déroule
                    bh = int(LH * len(HLINES) * bar_in)
                    bx = int(W * 0.035)
                    d.rounded_rectangle([bx, HY0, bx + 6, HY0 + bh], radius=3, fill=accent_rgb + (235,))
                for k, (wx, wy, word) in enumerate(HWORDS):
                    wa = _appear(t, 1.15 + k * WSTEP, 0.34)   # chaque mot arrive juste après le précédent
                    if wa <= 0: continue
                    dy = int((1 - wa) * 14)                   # léger glissement vers le haut
                    d.text((wx + 2, wy + dy + 3), word, font=HFONT, fill=(0, 0, 0, int(170 * wa)))
                    d.text((wx, wy + dy), word, font=HFONT, fill=WHITE + (int(255 * wa),))
            elif kind == "victory":
                typ, winner = data.get("type", "match"), data.get("winner", "")
                cy = int(H * 0.70)   # portrait : bloc score dans le bas, sur la zone assombrie
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
                        d.text((W // 2 + 2, int(H * 0.605) + 2), "★  VICTOIRE  ★", font=bf, fill=(0, 0, 0, int(220 * st_)), anchor="mm")
                        d.text((W // 2, int(H * 0.605)), "★  VICTOIRE  ★", font=bf, fill=GOLD + (int(255 * st_),), anchor="mm")
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
                # Marque : uniquement l'en-tête animé "PULSE" en haut à gauche.
                # (On ne la répète PAS en pied de page — doublon visuel inutile.)
                d.text((W - int(W * 0.04), H - int(H * (VIDEO_SAFE_BOTTOM + 0.028))),
                       f"{source} · {datetime.now().strftime('%d/%m/%Y')}",
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
        # ✨ pilule + logo ANIMÉS (remplacent leurs versions dessinées, omises plus haut)
        out_mp4 = _overlay_animated_pill(out_mp4, category, W, H, out_dir)
        if _anim_logo:
            out_mp4 = _overlay_animated_logo(out_mp4, W, H, out_dir,
                                             int(W * 0.04), int(H * 0.030), int(H * 0.042))
        # 🎵 Nappe d'ambiance selon la catégorie (hommage = solennelle très discrète).
        # En cas de pépin audio : vidéo muette plutôt que pas de vidéo.
        try:
            wav = os.path.join(out_dir, "pad.wav")
            snd_cat = "hommage" if sober else ("sport" if kind == "victory" else category)
            tag = build_soundtrack(wav, DUR, category=snd_cat,
                                   sujet=str(data.get("headline", "") or data.get("name", "")))
            withsnd = os.path.join(out_dir, "pulse_video_snd.mp4")
            r = subprocess.run([ffmpeg_bin, "-y", "-loglevel", "error", "-i", out_mp4, "-i", wav,
                                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", withsnd],
                               capture_output=True, timeout=120)
            if r.returncode == 0 and os.path.exists(withsnd):
                out_mp4 = withsnd
                print(f"  🎵 Ambiance sonore : {tag}")
        except Exception as e:
            print(f"  ⚠️ Ambiance sonore ignorée : {e}")
        print(f"  🎬 Vidéo générée ({kind})")
        return out_mp4
    except Exception as e:
        print(f"  ⚠️ build_video: {e} → image classique")
        return None

def build_card_video(headline, source, category, raw_photo, photo_url=None, image_query=None,
                     article_url=None, person=None, dur=7.0, fps=25):
    """Vidéo 16:9 (1920×1080) PLEIN CADRE montrant la carte Pulse — même photo, mêmes coins
    arrondis, même pastille, même emoji — avec le titre qui s'ÉCRIT mot à mot.
    Les images viennent de build_png : aucune mise en page dupliquée. Elles sont calculées en 2×
    (3840×2160) puis réduites en 1920×1080 → texte net, sans aucune bande noire.
    Renvoie le chemin du MP4, ou None → l'appelant publie alors simplement la carte fixe."""
    try:
        import subprocess, tempfile, imageio_ffmpeg
        W, H = CARD_VIDEO_W, CARD_VIDEO_H

        # 🌊 DEUX sous-états par mot (demi-fondu puis mot complet, max 26 images) : chaque mot
        #    ENTRE EN FONDU au lieu de surgir — l'écriture devient fluide. Le nombre d'images
        #    reste borné pour que le rendu tienne en moins d'une minute.
        words = max(1, len(str(headline or "").split()))
        n     = max(3, min(2 * words + 1, 26))
        tmpdir = tempfile.mkdtemp(prefix="pulse_card_")
        _anim_pill = _pill_gif_path(category) is not None   # GIF dispo → pilule fixe omise
        _anim_logo = (_logo_gif_path() is not None) and category != "hommage"
        for i in range(n):
            c = build_png(headline, source, category, photo_url, image_query,
                          article_url=article_url, person=person, W=W, H=H,
                          prefetched=(raw_photo, True), headline_bottom=True,
                          reveal=i / (n - 1), ss=CARD_VIDEO_SS, as_image=True,
                          no_pill=_anim_pill, no_logo=_anim_logo)
            if c is None:
                return None
            if c.size != (W, H):
                c = c.resize((W, H), Image.LANCZOS)     # 2× → 1920×1080 : texte lissé, très net
            c.save(f"{tmpdir}/s_{i:03d}.png")

        write_dur = min(3.4, dur * 0.5)              # phase d'écriture
        step      = write_dur / max(1, n - 1)
        hold      = max(1.5, dur - write_dur)        # la carte complète reste affichée
        lst = f"{tmpdir}/list.txt"
        with open(lst, "w") as fh:
            for i in range(n):
                fh.write(f"file 's_{i:03d}.png'\nduration {step if i < n - 1 else hold:.3f}\n")
            fh.write(f"file 's_{n-1:03d}.png'\n")     # dernière image répétée (exigence de ffmpeg)

        ff  = imageio_ffmpeg.get_ffmpeg_exe()
        out = f"{tmpdir}/pulse_card.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                        "-r", str(fps), "-c:v", "libx264", "-preset", "slow", "-tune", "stillimage",
                        "-pix_fmt", "yuv420p", "-crf", "17", "-movflags", "+faststart", out],
                       check=True, timeout=300)
        for i in range(n):
            try: os.remove(f"{tmpdir}/s_{i:03d}.png")
            except OSError: pass

        # ✨ pilule ANIMÉE puis LOGO animé (remplacent leurs versions fixes, omises plus haut)
        out = _overlay_animated_pill(out, category, W, H, tmpdir)
        if _anim_logo:
            _m = int(W * 0.037)
            out = _overlay_animated_logo(out, W, H, tmpdir, _m, int(H * 0.044), int(H * 0.062))

        # nappe sonore discrète (si elle échoue : vidéo muette, jamais d'échec de publication)
        try:
            wav = f"{tmpdir}/pad.wav"
            tag = build_soundtrack(wav, write_dur + hold, category=category, sujet=str(headline or ""))
            snd = f"{tmpdir}/pulse_card_snd.mp4"
            r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", out, "-i", wav,
                                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", snd],
                               capture_output=True, timeout=120)
            if r.returncode == 0 and os.path.exists(snd):
                out = snd
                print(f"  🎵 Ambiance sonore : {tag}")
        except Exception as e:
            print(f"  ⚠️ Ambiance sonore ignorée : {e}")
        print(f"  🎬 Carte animée générée ({write_dur + hold:.1f}s, 16:9 {W}×{H})")
        return out
    except Exception as e:
        print(f"  ⚠️ build_card_video: {e} → carte fixe")
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

def _liquid_glass_bg(W, H):
    """Fond « Liquid Glass » Pulse : dégradé profond + bulles FLOUES à dégradé de couleur (chaque
    bulle passe d'une couleur Pulse à une autre en son sein). Robuste : renvoie None si erreur."""
    try:
        import numpy as np
        ys, xs = np.mgrid[0:H, 0:W].astype("float32")
        t = np.clip(xs / W * 0.55 + ys / H * 0.55, 0, 1)          # diagonale
        c1 = np.array([15, 11, 46], "float32")
        c2 = np.array([52, 22, 112], "float32")
        c3 = np.array([126, 38, 116], "float32")
        rgb = np.empty((H, W, 3), "float32")
        m = t < 0.55
        rgb[m] = c1 + (c2 - c1) * (t[m] / 0.55)[:, None]
        rgb[~m] = c2 + (c3 - c2) * ((t[~m] - 0.55) / 0.45)[:, None]
        S = float(min(W, H))
        # bulles : (cx, cy, rayon, couleur_centre, couleur_bord) — dégradé DANS la bulle
        bubbles = [
            (0.26*W, 0.28*H, 0.46*S, (255,122,212), (98,112,255)),
            (0.82*W, 0.56*H, 0.40*S, (132,94,255),  (255,150,232)),
            (0.58*W, 0.10*H, 0.26*S, (255,184,120), (255,92,182)),
            (0.10*W, 0.84*H, 0.34*S, (98,182,255),  (152,92,255)),
            (0.92*W, 0.14*H, 0.22*S, (255,122,202), (122,122,255)),
            (0.46*W, 0.72*H, 0.32*S, (152,92,255),  (98,152,255)),
        ]
        for cx, cy, r, cA, cB in bubbles:
            dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / r
            falloff = np.clip(1.0 - dist, 0, 1) ** 1.5
            cA = np.array(cA, "float32"); cB = np.array(cB, "float32")
            grad = cA[None, None, :] + (cB - cA)[None, None, :] * np.clip(dist, 0, 1)[:, :, None]
            a = (falloff * 0.48)[:, :, None]
            rgb = rgb * (1 - a) + grad * a
        # léger voile sombre = garde le texte blanc lisible par-dessus les bulles
        rgb *= 0.88
        img = Image.fromarray(np.clip(rgb, 0, 255).astype("uint8"), "RGB")
        img = img.filter(ImageFilter.GaussianBlur(S * 0.028))     # flou = effet verre liquide
        return img.convert("RGBA")
    except Exception:
        return None

def _gradient_text_layer(text, font, colors, pad=8):
    """Renvoie un calque RGBA du `text` rempli d'un DÉGRADÉ horizontal (liste de couleurs RGB),
    rogné au plus juste. None en cas d'erreur (l'appelant garde le texte plat)."""
    try:
        import numpy as np
        big = Image.new("L", (2600, 520), 0)
        ImageDraw.Draw(big).text((pad, pad), text, font=font, fill=255)
        bb = big.getbbox()
        if not bb:
            return None
        mask = big.crop(bb)
        w, h = mask.size
        n = len(colors)
        arr = np.zeros((h, w, 4), "uint8")
        for x in range(w):
            u = (x / (w - 1)) * (n - 1) if w > 1 else 0
            i0 = int(u); i1 = min(i0 + 1, n - 1); fr = u - i0
            arr[:, x, 0] = int(colors[i0][0] + (colors[i1][0] - colors[i0][0]) * fr)
            arr[:, x, 1] = int(colors[i0][1] + (colors[i1][1] - colors[i0][1]) * fr)
            arr[:, x, 2] = int(colors[i0][2] + (colors[i1][2] - colors[i0][2]) * fr)
        layer = Image.fromarray(arr, "RGBA")
        layer.putalpha(mask)
        return layer
    except Exception:
        return None

def _hex_rgb(h):
    """'#rrggbb' → (r, g, b). Tolérant : gris clair par défaut."""
    try:
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return (200, 200, 210)


def _recap_thumb(raw_bytes, tw, th, radius_left=0):
    """Vignette recadrée (cover, centrée sur le visage si présent) pour une carte récap.
    radius_left = rayon des coins GAUCHE (la vignette est collée au bord gauche de la carte,
    coins droits carrés). Renvoie une image RGBA, ou None."""
    try:
        import io as _io
        photo = Image.open(_io.BytesIO(raw_bytes)).convert("RGB")
        sw, sh = photo.size
        scale = max(tw / sw, th / sh)
        nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
        ph = photo.resize((nw, nh), Image.LANCZOS)
        face = None
        try:
            face = detect_face_center(ph)
        except Exception:
            face = None
        if face:
            fx, fy = face
            left = int(fx - tw / 2); top = int(fy - th / 2)
        else:
            left = (nw - tw) // 2; top = int((nh - th) * 0.32)
        left = max(0, min(left, nw - tw)); top = max(0, min(top, nh - th))
        crop = ph.crop((left, top, left + tw, top + th)).convert("RGBA")
        if radius_left > 0:
            m = Image.new("L", (tw, th), 0)
            md = ImageDraw.Draw(m)
            md.rounded_rectangle([0, 0, tw - 1, th - 1], radius=radius_left, fill=255)
            md.rectangle([tw // 2, 0, tw - 1, th - 1], fill=255)   # côté droit carré
            crop.putalpha(m)
        return crop
    except Exception:
        return None


_RECAP_CAT_HINTS = [
    ("faitsdivers", ("incendie", "feu", "mort", "tué", "tue", "meurtre", "agress", "accident",
                     "victime", "police", "gendarm", "pompier", "disparu", "corps", "enquête",
                     "enquete", "interpel", "braquage", "vol ", "crime", "noyé", "noye", "blessé")),
    ("politique", ("loi", "assemblée", "assemblee", "sénat", "senat", "parlement", "ministre",
                   "gouvernement", "président", "president", "élection", "election", "député",
                   "depute", "réforme", "reforme", "vote", "motion", "décret", "decret", "élysée")),
    ("economie", ("euros", "milliard", "million", "inflation", "bourse", "entreprise", "emploi",
                  "chômage", "chomage", "budget", "impôt", "impot", "banque", "prix", "croissance",
                  "salaire", "grève", "greve", "marché", "marche")),
    ("tech", ("chatgpt", " ia ", "intelligence artificielle", "openai", "google", "apple", "iphone",
              "logiciel", "application", "pirat", "cyber", "données", "donnees", "internet",
              "robot", "smartphone", "réseau social", "reseau social")),
    ("sante", ("santé", "sante", "hôpital", "hopital", "maladie", "virus", "vaccin", "médecin",
               "medecin", "cancer", "épidémie", "epidemie", "patient", "soins")),
    ("environnement", ("climat", "canicule", "réchauffement", "rechauffement", "pollution",
                       "écologie", "ecologie", "biodiversité", "sécheresse", "secheresse",
                       "inondation", "tempête", "tempete", "carbone", "énergie", "energie")),
    ("culture", ("film", "cinéma", "cinema", "musique", "album", "concert", "festival", "livre",
                 "artiste", "acteur", "actrice", "chanteur", "exposition", "musée", "musee",
                 "série", "serie", "théâtre", "theatre")),
    ("sport", ("match", "but ", "victoire", "défaite", "defaite", "équipe", "equipe", "joueur",
               "champion", "finale", "coupe", "ligue", "tournoi", "jeux olympiques", "médaille",
               "medaille", "psg", "bleus")),
    ("monde", ("états-unis", "etats-unis", "chine", "russie", "ukraine", "gaza", "israël", "israel",
               "guerre", "international", "washington", "moscou", "pékin", "pekin", "onu", "trump")),
    ("societe", ("réseaux sociaux", "reseaux sociaux", "école", "ecole", "famille", "jeunes",
                 "manifestation", "société", "societe", "discrimination", "logement")),
]

def _recap_guess_cat(text):
    """Déduit la catégorie d'une actu depuis son texte (repli quand aucun article source
    du jour ne correspond). Renvoie 'france' si rien ne matche."""
    low = " " + (text or "").lower() + " "
    for cat, hints in _RECAP_CAT_HINTS:
        if any(h in low for h in hints):
            return cat
    return "france"


def build_recap_card(items, W=1080, H=1350):
    """🌙 Récap du jour, format maquette : cartes en VERRE DÉPOLI, chacune avec une VIGNETTE
    IMAGE liée à l'actu, un numéro, une barre + un badge de catégorie, et le titre.
    items = [(emoji, texte, categorie, raw_image_bytes_ou_None), ...] (3 à 6).
    Repli intégral : si le rendu échoue, on retombe sur build_list_card (jamais d'échec)."""
    try:
        import io as _io
        _ss = _IMG_SS
        Wf, Hf = int(W * _ss), int(H * _ss)
        M = 48 * _ss                                   # marge de sécurité constante
        WHITE, DIM, ROSE = (255, 255, 255), (214, 210, 232), (247, 71, 212)
        def f(px, bold=True):
            p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
            return ImageFont.truetype(p, max(8, int(px)))

        img = _liquid_glass_bg(Wf, Hf) or Image.new("RGBA", (Wf, Hf), (26, 16, 48, 255))
        d = ImageDraw.Draw(img)

        # ── EN-TÊTE : logo Pulse + date ──
        _pulse_brand(img, d, Wf, Hf); d = ImageDraw.Draw(img)
        d.text((Wf - M, int(Hf * 0.052)), _date_fr().upper(), font=f(Wf * 0.020), fill=DIM, anchor="rm")

        # ── TITRE : surtitre + « Ce qu'il faut retenir » + barre dégradée ──
        d.text((M, int(Hf * 0.130)), "L'ESSENTIEL DU JOUR", font=f(Wf * 0.021), fill=(236, 150, 236))
        tf = f(Wf * 0.058)
        ty = int(Hf * 0.158)
        _tl = _gradient_text_layer("Ce qu'il faut retenir", tf, [(255, 255, 255), (236, 180, 255)])
        if _tl is not None:
            img.alpha_composite(_tl, (M, ty)); tw_, th_ = _tl.width, _tl.height
        else:
            d.text((M, ty), "Ce qu'il faut retenir", font=tf, fill=WHITE)
            bb = d.textbbox((0, 0), "Ce qu'il faut retenir", font=tf); tw_, th_ = bb[2], bb[3]
        d = ImageDraw.Draw(img)
        try:
            import numpy as np
            bw, bh = int(Wf * 0.30), max(5, int(Hf * 0.006))
            ba = np.zeros((bh, bw, 4), "uint8")
            for x in range(bw):
                u = x / (bw - 1)
                ba[:, x] = [int(236 + (122 - 236) * u), int(72 + (108 - 72) * u), int(212 + (255 - 212) * u), 255]
            bar = Image.fromarray(ba, "RGBA")
            bm = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(bm).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=bh // 2, fill=255)
            bar.putalpha(bm)
            img.alpha_composite(bar, (M, ty + th_ + int(Hf * 0.018)))
        except Exception:
            pass

        # ── CARTES ──
        items = items[:6]
        n = len(items)
        top = int(Hf * 0.275)
        bottom = Hf - int(Hf * 0.068)
        gap = int(Hf * 0.020)
        card_h = (bottom - top - gap * (n - 1)) // n
        card_w = Wf - 2 * M
        thumb_w = int(card_w * 0.235)
        rad = int(18 * _ss)

        for i, it in enumerate(items):
            emoji_ch, txt, cat, raw = (list(it) + [None, None, None, None])[:4]
            cy = top + i * (card_h + gap)
            cat = (cat or "france").lower()
            st = STYLES.get(cat, STYLES.get("france", {}))
            ccol = _hex_rgb(st.get("color", "#82b1ff"))
            clabel = st.get("label", cat.upper()).upper()

            # fond carte : VERRE DÉPOLI (blanc à 7 %) — coins arrondis, bordure claire fine.
            # ⚠️ putalpha() ÉCRASERAIT l'alpha 7 % par le masque plein → panneau blanc opaque
            #    (bug vécu). On COMPOSE les deux alphas : opacité 7 % × forme arrondie.
            panel = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 0))
            pd = ImageDraw.Draw(panel)
            pd.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=rad, fill=(255, 255, 255, 18))
            pd.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=rad,
                                 outline=(255, 255, 255, 33), width=max(1, _ss))   # bordure ~13 %
            img.alpha_composite(panel, (M, cy))
            d = ImageDraw.Draw(img)

            # ── VIGNETTE : vraie photo, PLEINE HAUTEUR, largeur ~180 px, sans marge intérieure ──
            tw = int(180 * _ss)
            th = card_h
            tx, tyv = M, cy
            thumb = _recap_thumb(raw, tw, th, radius_left=rad) if raw else None
            if thumb is None:
                # repli : pastille de catégorie sur un fond SOMBRE translucide (jamais un
                # rectangle blanc ni un emoji). ⚠️ putalpha() écraserait l'alpha → fond blanc
                # opaque (bug vécu). On dessine directement sur un calque transparent avec la
                # forme voulue (coins arrondis à gauche, droits à droite).
                base = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
                bd = ImageDraw.Draw(base)
                bd.rounded_rectangle([0, 0, tw - 1, th - 1], radius=rad, fill=(255, 255, 255, 20))
                bd.rectangle([tw // 2, 0, tw - 1, th - 1], fill=(255, 255, 255, 20))
                pill = _category_pill(cat, int(th * 0.34))
                if pill is not None and pill.width > tw - 16 * _ss:
                    r2 = (tw - 16 * _ss) / pill.width
                    pill = pill.resize((tw - 16 * _ss, int(pill.height * r2)), Image.LANCZOS)
                if pill is not None:
                    base.alpha_composite(pill, ((tw - pill.width) // 2, (th - pill.height) // 2))
                thumb = base
            img.alpha_composite(thumb, (tx, tyv))
            d = ImageDraw.Draw(img)
            # barre catégorie 6 px à l'extrême gauche — dessinée APRÈS la vignette pour être
            # TOUJOURS visible (une photo opaque la cachait, un repli translucide la laissait voir :
            # d'où l'incohérence entre cartes). Désormais elle passe devant dans tous les cas.
            d.rounded_rectangle([M, cy, M + 6 * _ss, cy + card_h - 1], radius=3 * _ss, fill=ccol)
            # numéro d'ordre EN SURIMPRESSION sur la photo, coin haut-gauche.
            # Une simple OMBRE PORTÉE floutée derrière le chiffre suffit à le rendre lisible
            # sur n'importe quel fond — pas de rectangle.
            _num = f"{i + 1:02d}"
            nf = f(card_h * 0.17)
            _nx, _ny = tx + int(tw * 0.10), tyv + int(th * 0.07)
            _nsh = Image.new("RGBA", (img.width, img.height), (0, 0, 0, 0))
            ImageDraw.Draw(_nsh).text((_nx + 2 * _ss, _ny + 2 * _ss), _num, font=nf, fill=(0, 0, 0, 200))
            _nsh = _nsh.filter(ImageFilter.GaussianBlur(3 * _ss))
            img = Image.alpha_composite(img, _nsh)
            d = ImageDraw.Draw(img)
            d.text((_nx, _ny), _num, font=nf, fill=WHITE)

            # ── zone texte à DROITE de la vignette : badge + TITRE (2-3 lignes, blanc gras) ──
            tex = tx + tw + int(card_w * 0.030)
            tew = M + card_w - tex - int(card_w * 0.030)
            bf = f(card_h * 0.125)
            bb = d.textbbox((0, 0), clabel, font=bf)
            bw2, bh2 = bb[2] - bb[0] + int(card_h * 0.16), bb[3] - bb[1] + int(card_h * 0.12)
            by = cy + int(card_h * 0.13)
            d.rounded_rectangle([tex, by, tex + bw2, by + bh2], radius=bh2 // 2, fill=ccol)
            _lum = 0.299 * ccol[0] + 0.587 * ccol[1] + 0.114 * ccol[2]
            _btxt = (20, 16, 30) if _lum > 150 else WHITE
            d.text((tex + bw2 // 2, by + bh2 // 2), clabel, font=bf, fill=_btxt, anchor="mm")
            # TITRE de l'actu : blanc gras, 2-3 lignes, centré verticalement dans l'espace restant
            body_f = f(card_h * 0.150)
            lh = int(body_f.size * 1.16)
            words = str(txt or "").split(); lines = []; cur = ""
            for w in words:
                tt = (cur + " " + w).strip()
                if d.textbbox((0, 0), tt, font=body_f)[2] <= tew:
                    cur = tt
                else:
                    if cur: lines.append(cur)
                    cur = w
                    if len(lines) >= 3: cur = ""; break
            if cur and len(lines) < 3: lines.append(cur)
            lines = lines[:3]
            block_h = len(lines) * lh
            tblock = by + bh2 + int((card_h - (by - cy) - bh2 - block_h) * 0.5)
            for ln in lines:
                d.text((tex, tblock), ln, font=body_f, fill=WHITE); tblock += lh

        # pied
        d.text((Wf // 2, Hf - int(Hf * 0.038)), "@PULSEactus", font=f(Wf * 0.020),
               fill=(236, 150, 236), anchor="mm")
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=95, optimize=True, progressive=True)
        return buf.getvalue()
    except Exception as e:
        print(f"  ⚠️ Récap illustré indisponible ({e}) → carte simple")
        return build_list_card("CE QU'IL FAUT RETENIR",
                               [(e2, t2) for (e2, t2, *_r) in items], W, H)


def build_list_card(title_main, items, W=1200, H=675, accent=(255, 210, 74)):
    """Carte-liste DA Pulse : dégradé marque + titre + lignes numérotées. items = [str, ...]"""
    import io
    W, H = int(W * _IMG_SS), int(H * _IMG_SS)   # super-résolution 2× → rendu net
    WHITE, DIM = (255, 255, 255), (222, 218, 238)
    def f(px, bold=True):
        p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
        return ImageFont.truetype(p, int(px))
    def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    c1, c2, c3 = (18, 14, 62), (62, 24, 138), (160, 40, 130)
    img = _liquid_glass_bg(W, H)
    if img is None:
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
    # ── titre principal : STYLE personnalisé (dégradé Pulse + ombre douce + barre d'accent) ──
    tf = f(W * 0.045)
    while d.textbbox((0, 0), title_main, font=tf)[2] > W * 0.90 and tf.size > 24:
        tf = f(tf.size - 2)
    tx, ty = int(W * 0.05), int(H * 0.180)
    grad_cols = [(255, 214, 92), (255, 128, 196), (150, 158, 255)]   # or → rose → bleu (thème Pulse)
    _tl = _gradient_text_layer(title_main, tf, grad_cols)
    if _tl is not None:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))               # ombre douce (glow sombre)
        sh.paste((0, 0, 0, 190), (tx + 3, ty + 5), _tl.split()[3])
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        img.alpha_composite(sh)
        img.alpha_composite(_tl, (tx, ty))
        title_w, title_h = _tl.width, _tl.height
        d = ImageDraw.Draw(img)
    else:
        d.text((tx + 2, ty + 2), title_main, font=tf, fill=(0, 0, 0, 200))
        d.text((tx, ty), title_main, font=tf, fill=accent)
        bb = d.textbbox((0, 0), title_main, font=tf); title_w, title_h = bb[2] - bb[0], bb[3] - bb[1]
    # barre d'accent dégradée arrondie sous le titre
    try:
        import numpy as np
        bar_w = max(60, int(title_w * 0.42)); bar_h = max(6, int(H * 0.0085))
        c0, c1 = (255, 128, 196), (120, 150, 255)
        ba = np.zeros((bar_h, bar_w, 4), "uint8")
        for x in range(bar_w):
            u = x / (bar_w - 1)
            ba[:, x] = [int(c0[0] + (c1[0] - c0[0]) * u), int(c0[1] + (c1[1] - c0[1]) * u),
                        int(c0[2] + (c1[2] - c0[2]) * u), 255]
        bar = Image.fromarray(ba, "RGBA")
        bm = Image.new("L", (bar_w, bar_h), 0)
        ImageDraw.Draw(bm).rounded_rectangle([0, 0, bar_w - 1, bar_h - 1], radius=bar_h // 2, fill=255)
        bar.putalpha(bm)
        img.alpha_composite(bar, (tx, ty + title_h + int(H * 0.020)))
        d = ImageDraw.Draw(img)
    except Exception:
        pass
    # ── lignes : emoji COULEUR par item (fallback numéro) + retour à la ligne intelligent sur
    #    3 lignes max (fini le « de… » coupé), police fixe et grande ──
    items = items[:6]
    top, bottom = int(H * 0.285), H - int(H * 0.085)
    n = max(1, len(items))
    slot_h = (bottom - top) // n
    r = int(min(W, H) * 0.024)
    cxn = int(W * 0.074)
    text_x = cxn + r + int(W * 0.026)
    maxw = W - text_x - int(W * 0.045)
    body_f = f(W * 0.0332)                          # police fixe, grande
    line_h = int(body_f.size * 1.2)
    def _wrap(text, font, mw, max_lines=3):
        words = (text or "").split(); lines = []; cur = ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textbbox((0, 0), t, font=font)[2] <= mw:
                cur = t
            else:
                if cur: lines.append(cur)
                cur = w
                if len(lines) >= max_lines:
                    cur = ""; break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        # si le texte dépasse le max de lignes (très rare) : « … » propre sur la dernière ligne
        if len(" ".join(lines).split()) < len(words) and lines:
            last = lines[-1]
            while d.textbbox((0, 0), last + "…", font=font)[2] > mw and " " in last:
                last = last.rsplit(" ", 1)[0]
            lines[-1] = last + "…"
        return lines[:max_lines]
    for i, it in enumerate(items):
        emoji_ch, txt = (it if isinstance(it, (tuple, list)) else ("", it))
        cy = top + i * slot_h + slot_h // 2
        lines = _wrap(str(txt), body_f, maxw, 3)
        block_h = len(lines) * line_h
        # puce : emoji COULEUR si dispo, sinon pastille numérotée (fallback)
        _em = _emoji_image(emoji_ch, int(r * 2.05)) if emoji_ch else None
        if _em is not None:
            img.alpha_composite(_em, (cxn - _em.width // 2, cy - _em.height // 2))
            d = ImageDraw.Draw(img)
        else:
            d.ellipse([cxn - r, cy - r, cxn + r, cy + r], outline=accent, width=3)
            d.text((cxn, cy + 1), str(i + 1), font=f(r * 1.15), fill=accent, anchor="mm")
        ly = cy - block_h // 2 + int(line_h * 0.08)
        for ln in lines:
            d.text((text_x, ly), ln, font=body_f, fill=WHITE)
            ly += line_h
    d.text((int(W * 0.04), H - int(H * 0.062)), "Pulse", font=f(W * 0.020), fill=WHITE)
    d.text((W - int(W * 0.04), H - int(H * 0.055)), "@PULSEactus", font=f(W * 0.016, False),
           fill=DIM, anchor="rm")
    buf = io.BytesIO(); img.convert('RGB').save(buf, format="JPEG", quality=95, optimize=True, progressive=True); return buf.getvalue()

def publish_recap(conn):
    """🌙 Récap du soir : les 5 infos qui ont marqué la journée (état FINAL, pas les titres du matin)."""
    rows = conn.execute(
        "SELECT title FROM recent_titles WHERE date(added_at) = date('now') ORDER BY id DESC LIMIT 18"
    ).fetchall()
    titles = [r[0] for r in rows if r and r[0]]
    if len(titles) < 3:
        return False
    titles = list(reversed(titles))   # du MATIN au SOIR : la dernière mention d'un sujet = son état final
    # 💰 Cache du jour : si la publication du récap échoue (réseau…), les runs suivants
    #    réutilisent le contenu déjà généré au lieu de re-payer Claude.
    _rk = "__recap__" + datetime.now().strftime("%Y-%m-%d")
    _cached_items = None
    try:
        row = conn.execute("SELECT payload FROM daily_cache WHERE key=?", (_rk,)).fetchone()
        if row:
            _cached_items = [(e, t) for e, t in json.loads(row[0])]
            print("  💾 Récap réutilisé (déjà généré aujourd'hui, 0 coût)")
    except Exception:
        pass
    # Date + heure de Paris : indispensable pour corriger les formulations devenues obsolètes.
    try:
        from zoneinfo import ZoneInfo
        now_p = datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        now_p = datetime.now()
    now_str = f"{JOURS_FR[now_p.weekday()]} {now_p.day} {MOIS_FR[now_p.month - 1]} {now_p.year}, {now_p.hour}h{now_p.minute:02d}"
    arts = "\n".join(f"- {t}" for t in titles)
    if _cached_items is not None:
        items = _cached_items
    else:
        r = _llm_json(f"""Tu es le rédacteur en chef de Pulse, compte d'actualité français. Tu écris LE RÉCAP DU SOIR.
Nous sommes {now_str}. Tu as suivi TOUTE la journée.

Actus publiées aujourd'hui, du MATIN au SOIR (l'ordre compte) :
{arts}

Objectif : donner à un lecteur pressé LES infos à retenir de la journée, comme un journaliste qui a suivi l'actu — pas une IA qui recolle des titres.

RÈGLES :
1. 🕐 INTELLIGENCE TEMPORELLE : nous sommes {now_str}. Reformule tout ce qui est devenu FAUX. Un titre qui ANNONÇAIT un événement à venir ("verdict mardi", "ce soir", "demain") alors qu'il est déjà PASSÉ doit être réécrit à l'état ACCOMPLI ("verdict rendu", "condamné"). N'emploie "aujourd'hui/ce soir/mardi/demain" QUE si c'est encore exact maintenant.
2. 🧵 UN SUJET = UNE LIGNE, À L'ÉTAT FINAL : si un sujet revient plusieurs fois (il a évolué), fusionne-le en UNE seule ligne reflétant sa DERNIÈRE évolution. Ex : enquête ouverte → suspect interpellé → mis en examen ⇒ une seule ligne « mis en examen ». JAMAIS deux lignes sur la même histoire.
   ⚠️ MAIS NE CONFONDS JAMAIS DEUX ÉVÉNEMENTS DIFFÉRENTS : garde chaque fait avec les BONS acteurs. Ex : si l'Espagne bat la France ET que l'Argentine bat l'Angleterre, ce sont DEUX matchs distincts — n'écris SURTOUT PAS « l'Espagne bat l'Angleterre ». Chaque équipe/personne/pays avec son vrai adversaire, son vrai résultat, sa vraie affaire. Ne mélange jamais deux histoires en une seule phrase fausse.
3. 🎯 SÉLECTION : garde les 5 infos les plus MARQUANTES de la journée (importance, impact, mémorisation), pas les 5 dernières. De la plus forte à la moins forte.
4. ✍️ CHAQUE LIGNE = UNE PHRASE COURTE ET COMPLÈTE (c'est LA règle la plus importante, tu t'es trompé dessus avant) :
   - MODÈLE à imiter EXACTEMENT : « Mort de Sam Neill : l'acteur de Jurassic Park s'est éteint à 78 ans. » → courte, ENTIÈRE, on comprend tout d'un coup d'œil.
   - 80 CARACTÈRES MAXIMUM, idéalement 55-70. Compte-les. Une ligne trop longue devient minuscule et illisible sur l'image.
   - La phrase est ENTIÈRE : « Sujet : le fait », terminée. JAMAIS coupée, JAMAIS en suspens, JAMAIS de « … ». Interdit de finir par « ravagés par les », « face à une crise de », « l'ambassadeur russe ».
   - Si l'info est trop riche pour tenir court, garde SEULEMENT le fait principal et SUPPRIME les détails secondaires (chiffres en trop, précisions, causes). Ex : au lieu de « Incendies en Île-de-France : plus de 800 hectares de la forêt de Fontainebleau ravagés par les flammes, pompiers toujours mobilisés » → « Incendies : 800 hectares de la forêt de Fontainebleau partis en fumée. »
   - Compréhensible SANS avoir suivi l'actu. Un emoji pertinent en tête (jamais festif sur un drame).
5. ✅ AVANT DE RÉPONDRE, vérifie chaque ligne : ≤ 80 caractères ET une phrase COMPLÈTE (jamais de « … » ni de fin en suspens) ? encore vraie à {now_str} ? compréhensible seule ? mérite le top 5 ? formulation naturelle ? Corrige sinon.

Réponds avec ce JSON UNIQUEMENT :
{{"items":[{{"e":"⚖️","t":"Sujet : info essentielle"}},{{"e":"🚨","t":".."}},{{"e":"..","t":".."}},{{"e":"..","t":".."}},{{"e":"..","t":".."}}]}}""",
            max_tokens=500, task="special")
        # Filet anti-doublon : même si Claude sort 2 lignes sur le même sujet, on ne garde que la 1ʳᵉ
        # (≥2 mots saillants communs = même histoire) → « un sujet = une ligne » garanti mécaniquement.
        raw = [(str(it.get("e", "•"))[:2], _recap_line(str(it.get("t", ""))))
               for it in (r.get("items") or []) if it.get("t")]
        items, seen_sigs = [], []
        for e, t in raw:
            sw = _sig_words(t)
            if len(sw) >= 2 and any(len(sw & ps) >= 2 for ps in seen_sigs):
                continue
            items.append((e, t)); seen_sigs.append(sw)
            if len(items) >= 5:
                break
        if len(items) < 3:
            return False
        try:
            conn.execute("INSERT OR REPLACE INTO daily_cache (key, payload) VALUES (?,?)",
                         (_rk, json.dumps(items, ensure_ascii=False)))
            conn.commit()
        except Exception:
            pass
    # 🖼️ Pour chaque ligne, on relie une CATÉGORIE et une IMAGE d'un article du jour traitant
    #    le même sujet (≥2 mots saillants communs). Aucune image de stock, aucun emoji générique :
    #    si rien de fiable, la carte affichera la pastille de catégorie. Coût : 0 appel Claude.
    # Le repli de catégorie (quand aucun article source du jour ne correspond) est déduit
    # du texte par _recap_guess_cat (défini au niveau module, testé).
    def _enrich(items):
        try:
            rows2 = conn.execute(
                "SELECT title, url, category FROM recap_srcs WHERE date(created_at) = date('now') ORDER BY created_at DESC"
            ).fetchall()
        except Exception:
            rows2 = []
        out = []
        for e, t in items:
            sig = _sig_words(t)
            best = None
            for (atitle, aurl, acat) in rows2:
                if len(sig & _sig_words(atitle or "")) >= 2:
                    best = (aurl, acat); break
            # catégorie : celle de l'article source si trouvé, sinon déduite du texte de l'actu
            cat = (best[1] if best and best[1] else None) or _recap_guess_cat(t)
            raw = None
            if best and best[0]:
                try:
                    raw, ok = get_best_image(best[0], None, None, None, cat)
                    if not ok:
                        raw = None
                except Exception:
                    raw = None
            out.append((e, t, cat, raw))
        return out

    body = f"🌙 LE RÉCAP | Ce qu'il faut retenir de ce {_date_fr()} :\n\n"
    body += "\n".join(f"{e} {t}" for e, t in items)
    body += "\n\n(Pulse)"
    # 📱 Récap VERTICAL illustré (1080×1350) : une vignette liée à chaque actu.
    _items = _enrich(items)
    # 🎠 RÉCAP EN CARROUSEL : couverture « Ce qu'il faut retenir » puis une actu par
    #    image, surlignages à la couleur de sa catégorie. X accepte 4 médias : on garde
    #    la couverture et les 3 actus en tête. La carte unique reste le repli.
    png_list = []
    try:
        _sl, _ac, _ph = carrousel_recap(_items)
        png_list = rendre_carrousel(_sl, _ac, _ph, maxi=4)
        if len(png_list) > 1:
            print(f"  🎠 Récap en carrousel : {len(png_list)} images")
    except Exception as e:
        print(f"  ⚠️ Carrousel récap indisponible ({str(e)[:70]}) → carte unique")
        png_list = []
    png = png_list[0] if png_list else build_recap_card(_items, 1080, 1350)
    _x = _fb = _ig = None
    try:
        _x = post_to_twitter(body, png, png_list=(png_list if len(png_list) > 1 else None))
    except Exception as e:
        print(f"  ❌ X isolé : {e}")
    try:
        _fb = post_to_facebook(body, png)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    if ig_allowed(conn):
        _ig = post_to_instagram(build_ig_caption(body, []), png)
        if _ig:
            log_special(conn, "ig_post", [])
    if not (_x or _fb or _ig):
        print("  🛑 Aucune plateforme n'a publié → le récap retentera au prochain run (contenu en cache)")
        return False
    log_special(conn, "recap", [t for _, t in items][:2])
    print("  🌙 Récap du soir publié")
    return True

# ── MODE COUPE DU MONDE (calendrier fourni via cdm2026.txt à la racine du repo) ──
def _france_match_today():
    """Renvoie le match de la France prévu aujourd'hui (dict du calendrier) ou None."""
    today = datetime.now().strftime("%Y-%m-%d")
    for m in load_cdm():
        if m["date"] == today and "france" in (m["a"] + m["b"]).lower():
            return m
    return None

def _detect_france_match(candidates):
    """Détecte AUTOMATIQUEMENT dans les flux RSS un match de l'équipe de France de foot
    (sans calendrier). Renvoie (adversaire, articles_du_match) ou (None, []).
    Cherche les titres qui parlent du match des Bleus : 'France', un adversaire, et un
    contexte de match (mi-temps / score / verbe de résultat)."""
    # Marqueurs qui prouvent qu'on parle d'un MATCH de foot des Bleus (pas d'un autre sujet France)
    FOOT_CONTEXT = ("équipe de france", "equipe de france", "les bleus", "bleus",
                    "coupe du monde", "mondial", "didier deschamps", "deschamps",
                    "mbappé", "mbappe")
    MATCH_CUES = ("mi-temps", "mi temps", "à la pause", "score final", "coup d'envoi",
                  "s'impose", "l'emporte", "battu", "victoire", "défaite", "defaite",
                  "match nul", "tenu en échec", "élimin", "elimin", "qualifi", "but de",
                  "ouvre le score", "égalise", "egalise", "mène", "remporte", "affronte",
                  "face à", "contre", "vs", "-")
    score_rx = re.compile(r"\b\d{1,2}\s?[-:–]\s?\d{1,2}\b")
    # Pays adversaires plausibles en CDM : leur présence + un score/résultat suffit à prouver
    # qu'on parle d'un MATCH (même si le titre ne répète pas "Bleus"/"Coupe du monde").
    PAYS = ("sénégal", "senegal", "irak", "norvège", "norvege", "brésil", "bresil", "argentine",
            "espagne", "allemagne", "angleterre", "portugal", "maroc", "belgique", "croatie",
            "pays-bas", "italie", "danemark", "suisse", "pologne", "mexique", "états-unis",
            "etats-unis", "usa", "canada", "japon", "corée", "coree", "australie", "tunisie",
            "ghana", "nigéria", "nigeria", "cameroun", "égypte", "egypte", "uruguay", "colombie",
            "autriche", "écosse", "ecosse", "turquie", "grèce", "grece", "serbie", "ukraine",
            "côte d'ivoire", "cote d'ivoire", "curaçao", "curacao", "afrique du sud", "iran",
            "qatar", "équateur", "equateur", "venezuela", "paraguay", "chili", "panama", "jamaïque")

    # ── FRAÎCHEUR : un vrai post mi-temps/score ne vient QUE d'un article publié à l'instant.
    #    On exige une date de publication < 3h. Sans date fiable, on REJETTE (évite les
    #    articles "retour sur", récaps, ou matchs d'hier qui rouvrent un faux live). ──
    FRESH_SECONDS = 3 * 3600
    now_ts = time.time()
    # marqueurs qui trahissent un article rétrospectif (jamais un live)
    RETRO_CUES = ("retour sur", "il y a un an", "il y a 1 an", "l'an dernier", "rétro",
                  "retro", "anniversaire", "revivez", "ce jour-là", "ce jour la",
                  "souvenez", "à l'époque", "a l'epoque", "archive", "il y a deux ans",
                  "rediffusion", "replay", "best of", "résumé de la soirée d'hier", "hier soir")

    # Contextes d'AUTRES sports → ce n'est PAS un match de foot des Bleus (ex: rugby "All Blacks
    # 32-34", basket, hand...). On les exclut explicitement pour ne jamais coller un ⚽ dessus.
    OTHER_SPORT = ("rugby", "xv de france", "all blacks", "quinze de france", "essai", "mêlée",
                   "melee", "tournoi des six nations", "six nations", "top 14", "ovalie",
                   "basket", "handball", "hand-ball", "volley", "水球", "water-polo",
                   "roland-garros", "tennis", "jeu, set", "rugby à xiii", "treiziste",
                   "nba", "euroligue", "waterpolo", "hockey")
    # Un score de FOOTBALL ne dépasse jamais ~2 chiffres bas. 32-34 = rugby, 87-85 = basket...
    def _plausible_foot_score(txt):
        for mm in re.finditer(r"\b(\d{1,2})\s?[-:–]\s?(\d{1,2})\b", txt):
            a, b = int(mm.group(1)), int(mm.group(2))
            if a <= 15 and b <= 15:      # score de foot réaliste (borne large mais élimine rugby/basket)
                return True
        return False

    match_arts = []
    for c in candidates:
        t = (c.get("title", "") + " " + c.get("summary", "")).lower()
        if "france" not in t and "bleus" not in t:
            continue
        # ⛔ autre sport détecté → on ignore (jamais de faux "SCORE FINAL foot" sur du rugby)
        if any(s in t for s in OTHER_SPORT):
            continue
        # ⛔ si un score est présent mais qu'il est IMPOSSIBLE au foot (ex: 32-34) → on ignore
        if score_rx.search(t) and not _plausible_foot_score(t):
            continue
        has_foot_ctx = any(ctx in t for ctx in FOOT_CONTEXT)
        has_country_score = any(p in t for p in PAYS) and score_rx.search(t)
        # Soit le contexte foot explicite (Bleus/CDM/Deschamps/Mbappé) est présent,
        # soit le titre contient France + un pays adversaire + un score → match plausible
        # même si le titre est court et factuel ("France-Norvège : victoire 2-0").
        if not has_foot_ctx and not has_country_score:
            continue
        if not (score_rx.search(t) or any(cue in t for cue in MATCH_CUES)):
            continue
        # filtre rétrospectif
        if any(r in t for r in RETRO_CUES):
            continue
        # filtre fraîcheur : article publié dans les 3 dernières heures uniquement
        pub = c.get("pub_ts")
        if pub is None or (now_ts - pub) > FRESH_SECONDS or (now_ts - pub) < -1800:
            continue
        match_arts.append(c)

    if not match_arts:
        return None, []

    # Devine l'adversaire : le pays cité le plus souvent (hors France) dans les titres du match
    from collections import Counter
    cnt = Counter()
    for art in match_arts:
        t = (art.get("title", "") + " " + art.get("summary", "")).lower()
        for p in PAYS:
            if p in t:
                cnt[p] += 1
    adversaire = cnt.most_common(1)[0][0].capitalize() if cnt else "l'adversaire"
    return adversaire, match_arts

def fetch_france_match_live():
    """Interroge l'API football-data.org pour connaître l'état RÉEL d'un match de la France
    aujourd'hui (source fiable, pas d'ambiguïté 'analyse vs live' comme avec les articles RSS).
    Nécessite la variable d'environnement FOOTBALL_DATA_TOKEN (clé gratuite football-data.org).
    Retourne un dict {status, phase, team_a, team_b, score_a, score_b, adversaire, home} ou None.
      - phase 'half'  → match à la pause (score de mi-temps disponible)
      - phase 'final' → match terminé (score final)
      - phase None    → match en cours mais ni pause ni fin (rien à publier maintenant)
    """
    token = os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()
    if not token:
        print("  ⚠️ FOOTBALL_DATA_TOKEN ABSENT → pas de suivi live fiable du match (ajoute la clé dans les secrets GitHub).")
        return None
    try:
        # Fenêtre aujourd'hui → demain (UTC) : les matchs de Coupe du Monde aux horaires américains
        # tombent souvent la nuit / au petit matin en Europe, donc parfois sur la date UTC du lendemain.
        today = datetime.utcnow().strftime("%Y-%m-%d")
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        url = f"https://api.football-data.org/v4/matches?dateFrom={today}&dateTo={tomorrow}"
        req = urllib.request.Request(url, headers={"X-Auth-Token": token, "User-Agent": "PulseBot/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  ⚠️ API football-data (clé invalide ou quota dépassé ?) : {e}")
        return None

    matches = data.get("matches", [])
    fr_matches = [m for m in matches
                  if ((m.get("homeTeam") or {}).get("name") == "France"
                      or (m.get("awayTeam") or {}).get("name") == "France")]
    print(f"  ⚽ API football-data : {len(matches)} match(s) sur 48h, dont France : {len(fr_matches)}")
    for m in matches:
        home = (m.get("homeTeam") or {}).get("name", "") or ""
        away = (m.get("awayTeam") or {}).get("name", "") or ""
        crest_home = (m.get("homeTeam") or {}).get("crest", "") or ""
        crest_away = (m.get("awayTeam") or {}).get("crest", "") or ""
        # On ne suit QUE l'équipe de France (masculine A). "France" apparaît tel quel dans l'API.
        if home != "France" and away != "France":
            continue
        status = m.get("status", "")
        print(f"  ⚽🇫🇷 Match France détecté : {home} vs {away} — statut API = {status}")
        score = m.get("score", {}) or {}
        ft = score.get("fullTime", {}) or {}
        ht = score.get("halfTime", {}) or {}
        france_home = (home == "France")
        adversaire = away if france_home else home

        base = {"status": status, "team_a": home, "team_b": away,
                "home": france_home, "adversaire": adversaire,
                "crest_a": crest_home, "crest_b": crest_away}
        if status == "FINISHED":
            base.update({"phase": "final",
                         "score_a": ft.get("home", 0) or 0, "score_b": ft.get("away", 0) or 0})
            return base
        if status == "PAUSED":     # mi-temps : le score halfTime est renseigné
            base.update({"phase": "half",
                         "score_a": ht.get("home", 0) or 0, "score_b": ht.get("away", 0) or 0})
            return base
        if status == "IN_PLAY":    # match en cours hors pause → rien à publier maintenant
            base.update({"phase": None,
                         "score_a": ft.get("home", 0) or 0, "score_b": ft.get("away", 0) or 0})
            return base
    return None

def publish_france_live(conn, candidates):
    """⚽🇫🇷 Suit le match de la France : poste la MI-TEMPS puis le SCORE FINAL (2 posts max).
    SOURCE PRIORITAIRE = API football-data.org (statut réel du match : fiable, aucun faux positif
    type 'France 0-0 Maroc' alors qu'aucun match n'a lieu). SECOURS = flux RSS si pas de clé API.
    Ne compte PAS dans la cadence des autres actus (canal bonus)."""
    live = fetch_france_match_live()
    if live is not None:
        phase = live.get("phase")
        if phase not in ("half", "final"):
            return False      # match pas à la pause / pas fini → rien à publier ce run
        adversaire = live["adversaire"]
        match_key = f"{datetime.now().strftime('%Y')}-France-{adversaire.lower().strip()}"
        kind = "fr_final" if phase == "final" else "fr_half"
        if conn.execute("SELECT 1 FROM special_log WHERE kind=? AND keywords=?",
                        (kind, match_key)).fetchone():
            return False      # déjà publié pour ce match
        ta, tb = live["team_a"], live["team_b"]
        sa, sb = live["score_a"], live["score_b"]
        try:
            result = {"type": "match", "team_a": ta, "team_b": tb, "score_a": sa, "score_b": sb,
                      "winner": "A" if sa > sb else ("B" if sb > sa else None),
                      "sport": "FOOT", "competition": "Coupe du Monde 2026"}
            raw = build_france_match_bg(live)   # fond fiable : blasons des 2 équipes (API)
            if phase == "final":
                card = build_victory_card(raw, result, "", 1200, 675)
                video = build_video("victory", result, "sport", raw, "")
                txt = f"⚽ 🇫🇷 SCORE FINAL | {ta} {sa}-{sb} {tb}\n\n#CoupeDuMonde2026 #France"
                label = "SCORE FINAL"
            else:
                data = {"headline": f"Mi-temps : {ta} {sa}-{sb} {tb}"}
                video = build_video("news", data, "sport", raw, "")
                card, _ = build_png(f"⏸️ MI-TEMPS — {ta} {sa}-{sb} {tb}", "", "sport",
                                    prefetched=(raw, raw is not None))
                txt = f"⏸️ 🇫🇷 MI-TEMPS | {ta} {sa}-{sb} {tb}\n\n#CoupeDuMonde2026 #France"
                label = "MI-TEMPS"
            if not _post_all_platforms(conn, txt, card, video, "sport"):
                print("  🛑 Aucune plateforme n'a publié → le match repassera au prochain run")
                return False
            conn.execute("INSERT INTO special_log (kind, keywords) VALUES (?, ?)", (kind, match_key))
            conn.commit()
            print(f"  ⚽🇫🇷 {label} publié (API) : {ta} {sa}-{sb} {tb}")
            return True
        except Exception as e:
            print(f"  ❌ Publication France live (API) échouée : {e}")
            return False
    # Secours RSS UNIQUEMENT si aucune clé API n'est configurée. Si la clé API EST présente,
    # elle fait autorité : quand elle ne voit aucun match France aujourd'hui (live is None),
    # on NE publie PAS de score via RSS — c'est ce qui évitait un vieux "France-Paraguay"
    # republié depuis un article traînant dans les flux plusieurs jours après le match.
    if os.environ.get("FOOTBALL_DATA_TOKEN", "").strip():
        return False
    return _publish_france_live_rss(conn, candidates)

def _publish_france_live_rss(conn, candidates):
    """Secours (sans clé API) : détecte le match France dans les flux RSS.
    Moins fiable que l'API mais évite de ne rien publier si FOOTBALL_DATA_TOKEN absent.
    Retourne True si un post a été fait."""
    adversaire, match_articles = _detect_france_match(candidates)
    if not match_articles:
        return False

    # Clé anti-doublon : le jour + l'adversaire (un seul match France/jour en pratique)
    match_key = f"{datetime.now().strftime('%Y')}-France-{adversaire.lower().strip()}"

    # ── 1) SCORE FINAL (prioritaire) : article avec marqueur de fin de match ──
    FINAL_CUES = ("score final", "terminé", "termine", "fin du match", "coup de sifflet final",
                  "s'impose", "l'emporte", "battu", "battue", "victoire", "défaite", "defaite",
                  "élimin", "elimin", "qualifi", "match nul", "tenu en échec", "se quitte",
                  "remporte", "écrase", "ecrase", "domine")
    HALF_CUES = ("mi-temps", "mi temps", "à la pause", "a la pause", "première période",
                 "premiere periode", "1re période", "45e minute", "à la mi-temps")

    already_final = conn.execute(
        "SELECT 1 FROM special_log WHERE kind='fr_final' AND keywords=?", (match_key,)).fetchone()
    already_half = conn.execute(
        "SELECT 1 FROM special_log WHERE kind='fr_half' AND keywords=?", (match_key,)).fetchone()

    for art in match_articles:
        t = (art.get("title", "") + " " + art.get("summary", "")).lower()
        # SCORE FINAL
        if not already_final and any(cue in t for cue in FINAL_CUES) and re.search(r"\d{1,2}\s?[-:–]\s?\d{1,2}", t):
            try:
                result = extract_sport_result(art.get("title", ""), art.get("summary", ""))   # score précis
                if result and result.get("type") == "match":
                    raw, _ = get_best_image(art.get("url"), art.get("photo_url"), None, None, "sport")
                    card = build_victory_card(raw, result, art.get("source", ""), 1200, 675)
                    video = build_video("victory", result, "sport", raw, art.get("source", ""))
                    sa, sb = result.get("score_a"), result.get("score_b")
                    ta, tb = result.get("team_a"), result.get("team_b")
                    txt = f"⚽ 🇫🇷 SCORE FINAL | {ta} {sa}-{sb} {tb}\n\n#CoupeDuMonde2026 #France"
                    if not _post_all_platforms(conn, txt, card, video, "sport"):
                        print("  🛑 Aucune plateforme n'a publié → le score repassera au prochain run")
                        return False
                    conn.execute("INSERT INTO special_log (kind, keywords) VALUES ('fr_final', ?)", (match_key,))
                    conn.commit()
                    # PAS de mark_cat ici : le suivi France est un canal BONUS qui ne doit pas
                    # retarder le rythme des autres actus (mark_cat réinitialiserait leur minuteur).
                    # En revanche on marque l'article vu + on logue le texte publié, pour éviter
                    # qu'il ne soit RE-publié (sous une autre formulation) par le flux normal.
                    mark_seen(conn, art.get("url"), art.get("title", ""))
                    add_recent(conn, art.get("title", f"France {sa}-{sb} {tb} score final"))
                    print(f"  ⚽🇫🇷 SCORE FINAL publié : {ta} {sa}-{sb} {tb}")
                    return True
            except Exception as e:
                print(f"  ❌ Score final France échoué : {e}")
        # MI-TEMPS
        if not already_half and any(cue in t for cue in HALF_CUES):
            sc = re.search(r"(\d{1,2})\s?[-:–]\s?(\d{1,2})", t)
            if sc:
                score_str = f"{sc.group(1)}-{sc.group(2)}"
                try:
                    raw, _ = get_best_image(art.get("url"), art.get("photo_url"), None, None, "sport")
                    data = {"headline": f"Mi-temps : France {score_str} {adversaire}"}
                    video = build_video("news", data, "sport", raw, art.get("source", ""))
                    card, _ = build_png(f"⏸️ MI-TEMPS — France {score_str} {adversaire}",
                                        art.get("source", ""), "sport",
                                        article_url=art.get("url"), prefetched=(raw, raw is not None))
                    txt = f"⏸️ 🇫🇷 MI-TEMPS | France {score_str} {adversaire}\n\n#CoupeDuMonde2026 #France"
                    if not _post_all_platforms(conn, txt, card, video, "sport"):
                        print("  🛑 Aucune plateforme n'a publié → la mi-temps repassera au prochain run")
                        return False
                    conn.execute("INSERT INTO special_log (kind, keywords) VALUES ('fr_half', ?)", (match_key,))
                    conn.commit()
                    # PAS de mark_cat ici : le suivi France est un canal BONUS qui ne doit pas
                    # retarder le rythme des autres actus (mark_cat réinitialiserait leur minuteur).
                    # En revanche on marque l'article vu + on logue le texte publié, pour éviter
                    # qu'il ne soit RE-publié (sous une autre formulation) par le flux normal.
                    mark_seen(conn, art.get("url"), art.get("title", ""))
                    add_recent(conn, art.get("title", f"France {score_str} {adversaire} mi-temps"))
                    print(f"  ⏸️🇫🇷 MI-TEMPS publiée : France {score_str} {adversaire}")
                    return True
                except Exception as e:
                    print(f"  ❌ Mi-temps France échouée : {e}")
    return False

def _post_all_platforms(conn, text, image_bytes, video_path, category):
    """Publie sur X + Facebook + Instagram, chaque plateforme isolée.
    Renvoie True si AU MOINS UNE plateforme a réellement publié — pour que l'appelant
    ne consomme jamais un sujet resté totalement inédit (règle absolue)."""
    _x = _fb = _ig = None
    try:
        _x = post_to_twitter(text, png_bytes=image_bytes, video_path=video_path)
    except Exception as e:
        print(f"  ❌ X isolé : {e}")
    try:
        _fb = post_to_facebook(text, png_bytes=image_bytes, video_path=video_path)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    try:
        if ig_allowed(conn) and image_bytes:
            _ig = post_to_instagram(text, png_bytes=image_bytes)
    except Exception as e:
        print(f"  ❌ Instagram isolé : {e}")
    return (_x is not None) or (_fb is not None) or (_ig is not None)

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
    _x = _fb = _ig = None
    try:
        _x = post_to_twitter(body, png)
    except Exception as e:
        print(f"  ❌ X isolé : {e}")
    try:
        _fb = post_to_facebook(body, png)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    if ig_allowed(conn):
        _ig = post_to_instagram(build_ig_caption(body, ["coupedumonde2026"]), png_ig)
        if _ig:
            log_special(conn, "ig_post", [])
    if not (_x or _fb or _ig):
        print("  🛑 Aucune plateforme n'a publié → les matchs du jour retenteront au prochain run")
        return False
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
        _purl = post_poll(question, options)
        try:
            post_to_facebook(question + "\n\nDites-nous votre pronostic en commentaire 👇")
        except Exception as e:
            print(f"  ❌ Facebook isolé : {e}")
        if not _purl:
            print("  🛑 Le sondage n'est pas parti → le prono retentera au prochain run")
            return False
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
    words = re.findall(r"[0-9A-Za-zÀ-ÿ]+", (title or "").lower())
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
    # Un match REPORTÉ / ANNULÉ / À VENIR n'est pas un résultat (même avec une date type "7-1")
    if any(w in t for w in ("reporté", "reportée", "annulé", "annulée", "programmé", "programmée",
                            "aura lieu", "se jouera", "prévu le", "à venir", "fixé au", "décalé")):
        return False
    # Score chiffré "2-0", "(1-1)", "4 - 1" → match terminé. MAIS on écarte les DATES :
    #   "7-1-2026" (jj-mm-aaaa), ou un nombre collé à une année ("au 7-1 2026").
    m = re.search(r"(?<!\d)(\d{1,2})\s?[-:–]\s?(\d{1,2})(?!\s?[-/.]\s?\d)(?!\s?\d{2,})", title)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        # un vrai score de sport a des valeurs plausibles (0–30 hors exceptions) ; une date a
        # un 2e nombre ≤ 12 (mois) souvent suivi d'année — déjà exclu par le lookahead ci-dessus.
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

# ── MÉMOIRE PAR SUJET (intelligence éditoriale) ───────────────────────────────
# Un sujet = une histoire qui peut évoluer. On regroupe les articles par SIGNATURE
# (mots saillants communs), on retient combien de fois on en a parlé aujourd'hui et
# quand, puis on autorise une SUITE uniquement si : sous le plafond du jour (TOPIC_MAX_PER_DAY)
# ET assez de temps écoulé (TOPIC_MIN_GAP_MIN). La VALEUR AJOUTÉE, elle, est jugée par
# Claude dans l'appel d'analyse existant (aucun appel API supplémentaire).
def _topic_sig_words(title):
    """Mots saillants d'un titre (réutilise _sig_words) : sert à reconnaître un même sujet."""
    return _sig_words(title)

def log_topic(conn, title, keywords, corps=None):
    """Enregistre un sujet qu'on vient de publier (signature + titre + angle + heure)."""
    try:
        sig = " ".join(sorted(_topic_sig_words(title)))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        v = _embed(title, conn, essentiel=True)   # mémoriser un sujet publié : prioritaire
        conn.execute(
            "INSERT INTO topic_memory (topic_sig, headline, keywords, sent_at, vec, corps) "
            "VALUES (?,?,?,?,?,?)",
            (sig, title or "", ", ".join(keywords or []), now,
             json.dumps(v) if v else None, (corps or "")[:400])
        )
        conn.commit()
    except Exception as e:
        print(f"  ⚠️ log_topic: {e}")

EMBED_MODEL = os.environ.get("EMBED_MODEL", "gemini-embedding-001")
EMBED_SEUIL = 0.82        # au-delà, deux titres parlent du MÊME sujet (calibré prudemment)
# 🛡️ Le palier gratuit plafonne à ~1 000 requêtes/jour, TOUTES tâches confondues. Le bot
#    tourne 288 fois par jour : sans borne, les embeddings épuiseraient le quota à eux seuls
#    et feraient basculer analyse et rédaction sur Claude (payant). D'où ce budget, avec une
#    RÉSERVE pour les usages essentiels (mémoriser un sujet publié).
EMBED_BUDGET_JOUR = int(os.environ.get("EMBED_BUDGET_JOUR", "300"))
EMBED_RESERVE     = 60    # au-delà du budget courant, seuls les appels essentiels passent
_EMBED_CACHE = {}         # titre → vecteur, pour ne jamais payer deux fois dans un run
_EMBED_CONN  = None       # base ouverte, pour compter la consommation du jour


def _embed_budget_restant(conn):
    """Nombre d'embeddings encore autorisés aujourd'hui. Tolérant : en cas de souci,
    on renvoie 0 (repli mots-clés) plutôt que de risquer d'épuiser le quota."""
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM embed_log WHERE date(created_at) = date('now')"
        ).fetchone()[0]
        return max(0, EMBED_BUDGET_JOUR - n)
    except Exception:
        return 0


def _embed(texte, conn=None, essentiel=False):
    """Vecteur de SENS d'un titre, via l'API d'embeddings (gratuite dans nos volumes).
    `essentiel=True` pour les usages qu'on ne veut jamais perdre (mémoriser un sujet publié) :
    ceux-là puisent dans la réserve. Renvoie None si indisponible ou hors budget — la
    comparaison par mots-clés reprend alors la main, sans rien casser."""
    if not texte or not GEMINI_API_KEY:
        return None
    cle = texte.strip().lower()[:300]
    if cle in _EMBED_CACHE:
        return _EMBED_CACHE[cle]
    c = conn or _EMBED_CONN
    if c is not None:
        restant = _embed_budget_restant(c)
        if restant <= 0 or (restant <= EMBED_RESERVE and not essentiel):
            _EMBED_CACHE[cle] = None
            return None
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{EMBED_MODEL}:embedContent")
        _d = _post_gemini(url, {"model": f"models/{EMBED_MODEL}",
                                "content": {"parts": [{"text": texte[:2000]}]},
                                "outputDimensionality": 768},
                          famille="texte", timeout=20)
        try:
            _USAGE_GEMINI["in"] += ((_d.get("usageMetadata") or {}).get("promptTokenCount")
                                    or max(1, len(texte) // 4))
        except Exception:
            pass
        if c is not None:
            try:
                c.execute("INSERT INTO embed_log (created_at) VALUES (CURRENT_TIMESTAMP)")
                c.commit()
            except Exception:
                pass
        v = (_d.get("embedding") or {}).get("values")
        if v:
            _EMBED_CACHE[cle] = v
            return v
    except Exception as e:
        print(f"  ⚠️ Embedding indisponible ({str(e)[:70]}) → comparaison par mots-clés")
    _EMBED_CACHE[cle] = None
    return None


def _cos(a, b):
    """Similarité cosinus entre deux vecteurs : 1 = sens identique, 0 = sans rapport."""
    try:
        import math
        num = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return num / (na * nb) if na and nb else 0.0
    except Exception:
        return 0.0


def _meme_sujet(titre_a, titre_b, vec_a=None, vec_b=None, min_overlap=2):
    """Deux titres parlent-ils du MÊME sujet ?
    ① Comparaison par le SENS si les vecteurs sont disponibles (attrape « le feu ravage
       2500 ha » vs « 2500 hectares partis en fumée », que les mots communs rataient).
    ② Sinon, repli sur les mots saillants communs — le comportement historique."""
    if vec_a is not None and vec_b is not None:
        return _cos(vec_a, vec_b) >= EMBED_SEUIL
    return len(_sig_words(titre_a) & _sig_words(titre_b)) >= min_overlap


DOSSIER_JOURS = 14        # mémoire ÉDITORIALE : une grosse affaire se suit deux semaines

def dossier_sujet(conn, title, jours=DOSSIER_JOURS, maxi=8):
    """Tout ce que Pulse a DÉJÀ PUBLIÉ sur ce sujet, sur les `jours` derniers jours.

    ⚠️ À ne pas confondre avec topic_history(), qui compte les publications des dernières
    24 h pour appliquer la CADENCE (3 max/jour). Ici c'est la mémoire ÉDITORIALE : sur une
    affaire suivie deux semaines — un incendie, un procès —, le rédacteur doit savoir ce
    qui a déjà été dit pour ne pas le redire.

    Renvoie une liste chronologique de (âge_lisible, corps_publié), du plus ancien au plus
    récent. Vide si le sujet est neuf."""
    sig = _topic_sig_words(title)
    if not sig:
        return []
    depuis = (datetime.now() - timedelta(days=jours)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = conn.execute(
            "SELECT topic_sig, headline, corps, sent_at, vec FROM topic_memory "
            "WHERE sent_at >= ? ORDER BY sent_at ASC", (depuis,)
        ).fetchall()
    except Exception:
        return []
    vec_courant = _embed(title, conn)
    out = []
    for topic_sig, head, corps, sent_at, vec_txt in rows:
        autre = None
        if vec_courant is not None and vec_txt:
            try:
                autre = json.loads(vec_txt)
            except Exception:
                autre = None
        meme = (_cos(vec_courant, autre) >= EMBED_SEUIL) if autre is not None \
            else (len(sig & set((topic_sig or "").split())) >= 2)
        if not meme:
            continue
        try:
            dt = datetime.strptime((sent_at or "")[:19], "%Y-%m-%d %H:%M:%S")
            h = (datetime.now() - dt).total_seconds() / 3600
            age = (f"il y a {int(h * 60)} min" if h < 1.5 else
                   f"il y a {int(h)} h" if h < 36 else
                   f"il y a {int(h / 24)} jours")
        except Exception:
            age = "récemment"
        texte = re.sub(r"\s+", " ", (corps or head or "")).strip()
        if texte:
            out.append((age, texte[:200]))
    return out[-maxi:]           # les plus récents priment si le dossier est épais


def _dossier_en_texte(dossier):
    """Met le dossier en forme pour le rédacteur, du plus ancien au plus récent."""
    if not dossier:
        return ""
    lignes = "\n".join(f"• [{age}] {txt}" for age, txt in dossier)
    return (
        f"\n\n📚 CE QUE PULSE A DÉJÀ PUBLIÉ SUR CETTE AFFAIRE ({len(dossier)} publication(s)) :\n"
        + lignes +
        "\n\n⛔ NE RÉPÈTE AUCUN de ces faits : nos abonnés les connaissent. Un chiffre, un bilan "
        "ou une circonstance déjà donnés plus haut ne doivent PAS être redits comme une nouveauté.\n"
        "✅ Compare l'article à cet historique et ne garde QUE ce qui a changé depuis. "
        "Si un bilan a évolué, dis explicitement l'évolution (« le bilan passe de X à Y »).\n"
        "✅ Écris en CONTINUITÉ : le lecteur doit sentir que l'histoire avance."
    )


def topic_history(conn, title, min_overlap=2):
    """Publications d'AUJOURD'HUI portant sur le même sujet que `title`.
    Renvoie (nb, dernier_datetime|None, [titres_déjà_publiés]).
    'Même sujet' = au moins `min_overlap` mots saillants en commun."""
    sig = _topic_sig_words(title)
    if not sig:
        return 0, None, []
    # ⏱️ Fenêtre GLISSANTE de 24 h, et non le jour calendaire : sinon, à minuit, le bot oublie
    #    d'un coup ce qu'il vient de publier et le plafond anti-répétition se réinitialise.
    depuis = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = conn.execute(
            "SELECT topic_sig, headline, sent_at, vec FROM topic_memory WHERE sent_at >= ?",
            (depuis,)
        ).fetchall()
    except Exception:
        return 0, None, []
    vec_courant = _embed(title, conn)    # None si hors budget ou API absente → repli mots-clés
    n, last, heads = 0, None, []
    for topic_sig, head, sent_at, vec_txt in rows:
        autre_vec = None
        if vec_courant is not None and vec_txt:
            try:
                autre_vec = json.loads(vec_txt)
            except Exception:
                autre_vec = None
        if autre_vec is not None:
            meme = _cos(vec_courant, autre_vec) >= EMBED_SEUIL
        else:
            meme = len(sig & set((topic_sig or "").split())) >= min_overlap
        if meme:
            n += 1
            if head:
                heads.append(head)
            try:
                dt = datetime.strptime((sent_at or "")[:19], "%Y-%m-%d %H:%M:%S")
                if last is None or dt > last:
                    last = dt
            except Exception:
                pass
    return n, last, heads

_DERNIER_ANGLE = {"v": ""}   # élément neuf du dernier sujet jugé (passé au rédacteur)

def _suite_apporte_du_neuf(titre, resume, deja_publies):
    """Une SUITE de sujet mérite-t-elle une publication ?
    Le juge par mots-clés ne voit pas la différence entre « le feu est fixé » (vrai
    développement) et « les flammes progressent toujours » (même fait reformulé).
    L'analyse étant gratuite, on demande au modèle de trancher — et on lui fait dire
    LEQUEL est le développement, ce qui donne l'angle du tweet.
    Renvoie (bool, angle). 🛡️ En cas d'échec : (None, "") → le juge par mots-clés décide."""
    if not deja_publies:
        return True, ""
    try:
        deja = "\n".join(f"- {h}" for h in deja_publies[:4])
        r = _llm_json(f"""Un compte d'actualité a DÉJÀ publié sur ce sujet :
{deja}

Nouvel article :
Titre : {titre}
Résumé : {(resume or "")[:400]}

Ce nouvel article apporte-t-il un DÉVELOPPEMENT RÉEL par rapport à ce qui est déjà publié ?
- OUI si un fait NOUVEAU est intervenu : bilan qui change, interpellation, décision de
  justice, réaction officielle, fin ou aggravation de l'événement, nouvelle victime, recours.
- NON si c'est le MÊME fait raconté autrement, un simple rappel, un angle décoratif,
  ou un chiffre déjà connu reformulé.

Sois SÉVÈRE : dans le doute, réponds non. Republier deux fois la même chose décrédibilise.

Réponds UNIQUEMENT :
{{"neuf": true|false, "angle": "<en 6 mots max, ce qui est nouveau ; vide si rien>"}}""",
                       max_tokens=120, task="analyse")
        if isinstance(r, dict) and "neuf" in r:
            return bool(r.get("neuf")), str(r.get("angle") or "")[:80]
    except Exception as e:
        print(f"  ⚠️ Évaluation de suite indisponible ({str(e)[:60]}) → règle par mots-clés")
    return None, ""


def topic_gate(conn, title, resume=None, juge_modele=False):
    """Décide si un sujet DÉJÀ traité aujourd'hui a le droit de ressortir MAINTENANT.
    Renvoie (autorisé: bool, code: str, titres_déjà_publiés: list).
    code ∈ {'new','followup','cap','too_soon','stale'}.
    juge_modele=True : la NOUVEAUTÉ est évaluée par le modèle (plus fin que les mots-clés),
    avec repli automatique sur la règle par mots-clés si l'évaluation échoue."""
    n, last, heads = topic_history(conn, title)
    if n == 0:
        return True, "new", heads
    if n >= TOPIC_MAX_PER_DAY:
        return False, "cap", heads
    if last is not None:
        gap_min = (datetime.now() - last).total_seconds() / 60.0
        if gap_min < TOPIC_MIN_GAP_MIN:
            return False, "too_soon", heads
    # 🆕 EXIGENCE DE NOUVEAUTÉ : une "suite" doit apporter des mots SIGNIFICATIFS absents des
    #    titres déjà publiés sur ce sujet. Sinon c'est le même fait reformulé par un autre média
    #    (vécu : incendie du Var tweeté en URGENT puis re-tweeté en FAITS DIVERS 1h après).
    if juge_modele:
        neuf, angle = _suite_apporte_du_neuf(title, resume, heads)
        _DERNIER_ANGLE["v"] = angle if neuf else ""
        if neuf is True:
            if angle:
                print(f"  🆕 Développement réel : {angle}")
            return True, "followup", heads
        if neuf is False:
            print("  ⏭️  Même fait reformulé (jugé par le modèle) → pas de republication")
            return False, "stale", heads
        # neuf is None → l'évaluation a échoué, on retombe sur la règle par mots-clés
    new_words = _sig_words(title)
    for h in heads:
        new_words -= _sig_words(h)
    # on ignore les mots "génériques de gravité" qui ne portent aucune info neuve
    new_words -= _FOLLOWUP_GENERIC
    if not new_words:
        return False, "stale", heads
    return True, "followup", heads

# Mots trop génériques pour à eux seuls justifier une "suite" : présents dans presque
# toutes les reformulations d'un même drame, ils n'apportent aucune information nouvelle.
_FOLLOWUP_GENERIC = {
    "incendie", "feu", "flammes", "fumée", "fumee", "brasier", "hectares", "pompiers", "sapeurs",
    "mobilisés", "mobilises", "mobilisation", "ravage", "ravages", "ravagés", "ravagees",
    "partis", "partie", "détruits", "detruits", "brûlés", "brules", "brûlée", "brulee",
    "toujours", "actif", "active", "encore", "matinée", "matinee", "soirée", "soiree", "cours",
    "morts", "mort", "décès", "deces", "blessés", "blesses", "victimes", "bilan", "lourd",
    "selon", "après", "apres", "contre", "dans", "pour", "avec", "plus", "cette", "leur",
    "être", "sont", "reste", "restait", "restent", "situation", "important", "importante",
    "police", "gendarmes", "enquête", "enquete", "ouverte", "france", "région", "region",
    "département", "departement", "secteur", "zone", "zones", "sinistrées", "sinistrees",
}

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

def buzz_recent(conn):
    """🚰 Frein du canal BUZZ (score 7-8, non urgent) : hors cadence des news, mais jamais
    plus d'un buzz par fenêtre (75 min le jour, 150 la nuit), ni juste après un breaking.
    Les HOMMAGES ne comptent pas ici : un décès n'arme jamais ce frein."""
    _h = _paris_hour()
    _gap = BUZZ_GAP_MIN if 7 <= _h < 23 else BUZZ_GAP_NIGHT_MIN
    if conn.execute("SELECT 1 FROM special_log WHERE kind='buzz' AND sent_at > datetime('now', ?)",
                    (f"-{_gap} minutes",)).fetchone() is not None:
        return True
    return breaking_recent(conn)

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

    # ── ⚖️ LOI & INSTITUTIONS (angle mort historique : une loi qui change la vie du pays) ──
    (4, r"\bloi\b|projet de loi|proposition de loi|adopt(e|é|ée)\b|\bvot(e|é|ée)\b|promulg|décret|ordonnance"
        r"|sénat|assemblée nationale|conseil constitutionnel|cour de cassation|conseil d'état|cour suprême"
        r"|référendum|motion de censure|\b49\.?3\b|remaniement|constitution|amendement|abrog"),
    # ── 🚓 POLICE & USAGE DE LA FORCE ──
    (4, r"policier|policière|gendarme|\bcrs\b|\bigpn\b|arme létale|légitime défense|refus d'obtempérer"
        r"|violences policières|bavure|présomption d'innocence|garde des sceaux|ministre de l'intérieur"
        r"|tir(s|e)? (mortel|sur)|coup de feu"),
    # ── 🌍 GUERRE & INTERNATIONAL ──
    (4, r"\bguerre\b|frappes?|bombard|missile|drone|offensive|invasion|cessez-le-feu|trêve|représailles"
        r"|\botan\b|\bonu\b|sanctions? contre|embargo|\bgaza\b|ukraine|\biran\b|détroit d'ormuz|corée du nord"),
    # ── 🏥 SANTÉ PUBLIQUE & ALERTES SANITAIRES ──
    (4, r"épidémie|pandémie|contamination|intoxication|listeria|salmonell|\be\.? ?coli\b|rappel (produit|massif|de lots?)"
        r"|pénurie de médicaments|scandale sanitaire|\bvirus\b|grippe aviaire|empoisonn"),
    # ── 💊 AVANCÉES MÉDICALES & VIE QUOTIDIENNE (utiles, même sans buzz) ──
    (3, r"traitement contre|nouveau traitement|médicament (contre|autorisé)|autorisation de mise sur le marché"
        r"|\bvaccin\b|essai clinique|alzheimer|parkinson|cancer|diabète|remboursé par (la )?sécu|haute autorité de santé"),
    (3, r"sommeil|dorm(ent|ir|ent-ils)|temps d'écran|\bécrans\b|espérance de vie"
        r"|alimentation des français|santé mentale|les français (consomment|mangent|boivent|travaillent|gagnent)"
        r"|pouvoir d'achat des ménages|budget des familles"),
    # ── 🌡️ CLIMAT & CATASTROPHES NATURELLES ──
    (4, r"vigilance rouge|alerte rouge|canicule|séisme|tremblement de terre|tsunami|inondation|crue"
        r"|tempête|ouragan|tornade|éruption|évacuation|sécheresse historique|feu de forêt"),
    # ── 💶 ÉCONOMIE DU QUOTIDIEN (« en quoi ça me concerne ? ») ──
    (3, r"pouvoir d'achat|inflation|hausse des prix|carburant|prix de l'essence|facture d'électricité|prix du gaz"
        r"|\bimpôt|nouvelle taxe|\bsmic\b|retraites?\b|chômage|licenciement|plan social|faillite|pénurie"
        r"|augmentation des prix|gel des prix"),
    # ── 🚆 TRANSPORTS & SERVICES PUBLICS ──
    (3, r"\bsncf\b|\bratp\b|aéroport|trafic interrompu|coupure (de courant|d'électricité)|panne géante"
        r"|grève des|vol annulé|circulation interrompue"),
    # ── 👤 GRANDES PERSONNALITÉS & POUVOIR ──
    (3, r"macron|premier ministre|président de la république|élysée|matignon|\bpape\b|\btrump\b|poutine"
        r"|zelensky|netanyahu|von der leyen"),
    # ── 🎓 ÉDUCATION & JEUNESSE ──
    (2, r"harcèlement scolaire|réforme (du|des) (bac|lycée|collège)|professeur agressé|fermeture d'école"),
    # ── 🏆 GRANDS RENDEZ-VOUS CULTURELS ──
    (2, r"césars?\b|oscars?\b|palme d'or|prix goncourt|eurovision|jeux olympiques|\bjo 20\d\d\b"),
]
PRERANK_COLD = [
    (-4, r"app store|bundle|abonnement|partenariat|trimestriel|levée de fonds|lève des fonds|acquisition|\bapi\b|mise à jour|fonctionnalité|s'associe"),
    (-4, r"vue de l'étranger|revue de presse|édito|tribune|chronique|portrait|ce qu'il faut retenir|récap|décryptage"),
    (-3, r"étude|rapport|sondage|classement|baromètre"),
    (-3, r"pourrait|devrait|envisage|prévoit|à l'horizon|d'ici 20\d\d"),
    (-2, r"comment |pourquoi |voici |conseils|astuces|guide"),
    (-4, r"horoscope|programme tv|replay|podcast|diaporama|quiz|recette|bons plans|promo|soldes|comparatif|notre sélection|que regarder|que faire ce"),
    (-3, r"triathlon|marathon de|championnats? du monde de|coupe du monde de (handball|rugby|natation|judo)|open de|tournoi de"),
    # 🛒 CONTENU PRODUIT / BANC D'ESSAI — jamais une actualité, souvent de l'affiliation.
    #    (vécu : « EcoFlow RIVER 2, l'une des plus légères stations portables » partait en
    #     analyse payante ; « Galaxy Watch : Samsung teste déjà One UI 9 » aussi.)
    (-5, r"on a test|nous avons test|notre test|à l'essai|prise en main|unboxing|"
         r"rapport qualité.?prix|meilleur prix|prix cassé|au meilleur prix|"
         r"stations? portables?|batteries? externes?|écouteurs|casques? audio|aspirateurs?|"
         r"trottinettes?|montres? connectées?|objets? connectés?|"
         r"chargeurs? (?:sans fil|rapides?|à induction|usb)|box internet|forfaits? mobiles?|"
         r"\bone ui\b|\bandroid \d|\bios \d\d"),
    (-4, r"\b(pourquoi|comment) (choisir|acheter)\b|à moins de \d+ ?(€|euros)|"
         r"-\d{2} ?% sur|code promo|black friday|french days|vente flash"),
    # 🎣 TOURNURES D'APPÂT — le titre cache l'info au lieu de la donner.
    (-4, r"oubliez |vous ne devinerez|la vraie révolution|ce détail qui change|"
         r"personne n'avait remarqué|voici pourquoi|la raison est|et ce n'est pas"),
    # 💋 INTIMITÉ / POTINS — hors ligne éditoriale d'un compte d'actualité.
    (-5, r"au plumard|sous la couette|sa vie sexuelle|ses ébats|confidences intimes|"
         r"nuit torride|en couple avec|son ex |sa nouvelle compagne|son nouveau compagnon"),
]
PRERANK_WILDCARD = 4     # places réservées : un sujet majeur au vocabulaire imprévu doit atteindre Claude

# ── 📣 ÉCHO MÉDIATIQUE : combien de MÉDIAS DIFFÉRENTS couvrent le même sujet ? ──
# Le meilleur signal d'importance réelle d'une actu : quand toute la presse en parle,
# c'est que ça compte. À l'inverse, un sujet repris par un seul média reste une info isolée.
# Sert AU CLASSEMENT FINAL (pas seulement à choisir qui part en analyse) : une actu couverte
# par 5 médias doit passer devant une curiosité scientifique publiée par un seul.
def source_echo(cands):
    """{url: nombre de médias distincts couvrant ce sujet} (≥2 mots saillants communs = même sujet)."""
    sigs = [_sig_words(c.get("title", "")) for c in cands]
    echo = {}
    for i, c in enumerate(cands):
        srcs = {c.get("source")}
        for j, d in enumerate(cands):
            if i != j and len(sigs[i] & sigs[j]) >= 2:
                srcs.add(d.get("source"))
        echo[c.get("url")] = len(srcs)
    return echo

def echo_bonus(n_sources):
    """Bonus de score selon la couverture médiatique.
    Plafonné à +2 : l'écho DÉPARTAGE deux sujets proches, il ne doit jamais faire passer une info
    moyenne devant un scoop majeur (un breaking exclusif à 9/10 reste devant un 6/10 repris partout).
    Bonus SEULEMENT, jamais de malus : une exclusivité ou un breaking sorti en premier est encore
    seul par nature — on ne le punit pas."""
    if n_sources >= 3: return 2      # largement repris : toute la presse en parle
    if n_sources == 2: return 1      # confirmé par un second média
    return 0                         # source unique → ni bonus, ni pénalité

def _hot_prescore(title):
    """Pré-score GRATUIT d'un sujet chaud (aucun appel Claude).
    Source de vérité unique : utilisé pour décider si un sujet mérite d'être analysé."""
    low = (title or "").lower()
    return sum(w for w, rx in PRERANK_HOT if re.search(rx, low)) + \
           sum(w for w, rx in PRERANK_COLD if re.search(rx, low))


def prerank_candidates(cands, keep, wildcard=PRERANK_WILDCARD):
    """Classement heuristique gratuit : mots chauds/froids + écho multi-sources.
    ⚠️ Une liste de mots-clés ne sera JAMAIS exhaustive : un sujet majeur peut arriver avec un
    vocabulaire imprévu (ex : une loi sur le tir des policiers = 0 mot chaud). On réserve donc
    `wildcard` places aux articles les plus RÉCENTS non retenus au score — hors contenus
    manifestement froids (horoscope, promos...). Le classement final reste fait par Claude."""
    sigs = [_sig_words(c["title"]) for c in cands]
    scored_idx, cold_of = [], {}
    for i, c in enumerate(cands):
        t = (c["title"] + " " + (c.get("summary") or "")[:120]).lower()
        s, cold = 0.0, 0.0
        for w, rx in PRERANK_HOT:
            if re.search(rx, t): s += w
        for w, rx in PRERANK_COLD:
            if re.search(rx, t): cold += w
        s += cold
        cold_of[i] = cold
        echo = sum(1 for j in range(len(cands))
                   if j != i and cands[j]["source"] != c["source"] and len(sigs[i] & sigs[j]) >= 2)
        s += min(6, echo * 2)            # repris par plusieurs médias = important
        s += random.random() * 0.5       # micro-aléa pour départager
        scored_idx.append((s, i))
    scored_idx.sort(key=lambda x: -x[0])

    kept, kept_sigs, kept_idx = [], [], set()

    def _take(i):
        # Dédup intra-lot : un même sujet repris par plusieurs sources n'est analysé qu'UNE fois
        if any(len(sigs[i] & ks) >= 3 for ks in kept_sigs):
            return False
        kept.append(cands[i]); kept_sigs.append(sigs[i]); kept_idx.add(i)
        return True

    # 1) Le gros du lot : les meilleurs scores (les favoris ne sont jamais évincés)
    par_score = max(1, keep - max(0, wildcard))
    for s, i in scored_idx:
        if len(kept) >= par_score: break
        _take(i)

    # 2) Places JOKER : les articles les plus récents non retenus (anti-angle-mort du vocabulaire)
    if wildcard > 0 and len(kept) < keep:
        rest = [i for i in range(len(cands)) if i not in kept_idx and cold_of[i] > -3]
        rest.sort(key=lambda i: -(cands[i].get("pub_ts") or 0))
        for i in rest:
            if len(kept) >= keep: break
            _take(i)

    # 3) S'il reste des places (peu de candidats récents), on complète par score
    for s, i in scored_idx:
        if len(kept) >= keep: break
        if i in kept_idx: continue
        _take(i)
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

def _topic_key(title):
    """Clé de regroupement d'un sujet : les mots significatifs triés (stable d'un média à l'autre).
    Deux titres du même événement partagent la même clé même s'ils sont formulés différemment."""
    return " ".join(sorted(_sig_words(title)))

def topic_echo_add(conn, title, source):
    """Enregistre qu'un MÉDIA parle d'un sujet (mémoire 12h), appelé sur CHAQUE article frais du flux
    (avant tout pré-filtre), pour que même un sujet déjà tweeté accumule ses médias. Ne compte jamais
    deux fois le même média (UNIQUE topic_key+source). Renvoie le sujet canonique, ou None si trop court.
    Regroupe par CHEVAUCHEMENT (≥2 mots communs) : deux formulations du même événement = un seul sujet."""
    sig = _sig_words(title)
    if len(sig) < 2:
        return None
    rows = conn.execute(
        "SELECT topic_key, source FROM topic_echo WHERE first_seen > datetime('now','-6 hours')"
    ).fetchall()
    seen_keys = {}
    for k, src in rows:
        seen_keys.setdefault(k, set()).add(src)
    canon = None
    for k in seen_keys:
        if len(sig & set(k.split())) >= 2:
            canon = k
            break
    if canon is None:
        canon = " ".join(sorted(sig))
    conn.execute("INSERT OR IGNORE INTO topic_echo (topic_key, source) VALUES (?,?)", (canon, source))
    conn.commit()
    return canon

def topic_echo_status(conn, title):
    """État d'écho d'un sujet : (n_medias_distincts_12h, deja_alerte, sources_au_moment_alerte, canon).
    Sert à décider : nouveau sujet chaud (breaking) OU suivi (si un NOUVEAU média est apparu depuis)."""
    sig = _sig_words(title)
    if len(sig) < 2:
        return 0, False, 0, None
    rows = conn.execute(
        "SELECT topic_key, source FROM topic_echo WHERE first_seen > datetime('now','-6 hours')"
    ).fetchall()
    seen_keys = {}
    for k, src in rows:
        seen_keys.setdefault(k, set()).add(src)
    canon = None
    for k in seen_keys:
        if len(sig & set(k.split())) >= 2:
            canon = k
            break
    if canon is None:
        return 0, False, 0, " ".join(sorted(sig))
    n = len(seen_keys.get(canon, set()))
    row = conn.execute("SELECT sources_at_alert FROM topic_echo_alert WHERE topic_key=?", (canon,)).fetchone()
    alerted = row is not None
    at_alert = row[0] if row else 0
    return n, alerted, at_alert, canon

def topic_echo_mark_alerted(conn, canon, n_sources):
    """Pose le drapeau anti-spam ET mémorise combien de médias parlaient du sujet à ce moment,
    pour ne re-déclencher un SUIVI que si un NOUVEAU média rejoint le sujet ensuite."""
    conn.execute(
        "INSERT INTO topic_echo_alert (topic_key, sources_at_alert) VALUES (?,?) "
        "ON CONFLICT(topic_key) DO UPDATE SET sources_at_alert=excluded.sources_at_alert",
        (canon, n_sources))
    conn.commit()

def detect_breaking(conn, candidates, return_all=False):
    """🔥 Détecte les sujets chauds via l'ÉCHO MÉDIATIQUE PERSISTANT (12h) : ≥ BREAKING_SOURCES
    médias DISTINCTS sur 12h (le comptage est alimenté en amont, sur chaque article frais du flux,
    même les sujets déjà tweetés). Renvoie des candidats annotés :
      • _echo_kind='breaking' : sujet jamais alerté qui franchit le seuil → alerte.
      • _echo_kind='followup' : sujet déjà alerté MAIS un NOUVEAU média est apparu depuis → suite si du neuf.
    return_all=True → liste triée (nb médias décroissant). Anti-spam et valeur éditoriale gérés en aval."""
    if breaking_recent(conn) and not return_all:
        return None
    published_sigs = [_sig_words(t) for t in get_recent(conn)]
    hot = {}   # canon -> (candidat, n_sources)
    for c in candidates:
        wi = _sig_words(c["title"])
        if len(wi) < 2:
            continue
        if _is_soft_news(c["title"]):
            continue
        n_sources, already_alerted, at_alert, canon = topic_echo_status(conn, c["title"])
        # ⚡ Seuil d'écho requis, du plus exigeant au plus rapide :
        #    • sujet ordinaire      → BREAKING_SOURCES médias distincts
        #    • sujet ultra-chaud    → 2 médias
        #    • ALERTE VITALE        → 1 seul média suffit. Sur un attentat, un tsunami ou une
        #      évacuation, attendre confirmation coûte de longues minutes ; le vocabulaire de
        #      danger physique est assez étroit pour que le risque de fausse alerte reste faible.
        if _is_urgent_alert(c["title"], c.get("summary", "")):
            _seuil = 1
        elif ULTRA_HOT_RX.search(c["title"]):
            _seuil = 2
        else:
            _seuil = BREAKING_SOURCES
        if not canon or n_sources < _seuil:
            continue
        if already_alerted:
            # suivi possible UNIQUEMENT si un NOUVEAU média a rejoint le sujet depuis l'alerte
            if n_sources <= at_alert:
                continue
            # 🆕 …ET si le titre apporte des mots SIGNIFICATIFS neufs (pas une reformulation).
            #    Sans ça, un 2e média qui redit la même chose relançait le sujet (incendie du Var).
            _allowed, _code, _heads = topic_gate(conn, c["title"],
                                                 resume=c.get("summary"), juge_modele=True)
            if not _allowed and _code in ("stale", "too_soon", "cap"):
                continue
            # 🆕 l'élément nouveau identifié voyage avec le candidat jusqu'à la rédaction
            c["_angle_neuf"] = _DERNIER_ANGLE.get("v", "")
            kind = "followup"
        else:
            # nouveau sujet chaud : pas déjà couvert récemment par Pulse
            if any(len(wi & ps) >= 2 for ps in published_sigs):
                continue
            kind = "breaking"
        c["_topic_canon"] = canon
        c["_echo_kind"] = kind
        c["_echo_n"] = n_sources
        if canon not in hot or n_sources > hot[canon][1]:
            hot[canon] = (c, n_sources)
    ordered = [c for c, n in sorted(hot.values(), key=lambda x: -x[1])]
    if return_all:
        return ordered
    if breaking_recent(conn):
        return None
    return ordered[0] if ordered else None

def publish_breaking(conn, item, cat, urgent=True, bump_cadence=None, candidates=None):
    _angle = item.get("_angle_neuf") or ""
    """Publie vite une actu (X + Facebook + Instagram).
    urgent=True → label rouge 'Breaking'. urgent=False → label normal de la catégorie (buzz/insolite).
    bump_cadence : repousse-t-il le minuteur de cadence des news normales ? Par défaut, un vrai
    breaking (urgent=True) ne le repousse PAS ; un buzz/suivi (urgent=False) le repousse."""
    if bump_cadence is None:
        bump_cadence = not urgent
    _pts = item.get("pub_ts")
    if _pts:
        _ah = (time.time() - _pts) / 3600
        _q = "frais ✅" if _ah <= 6 else ("récent" if _ah <= 24 else "ANCIEN ⚠️")
        print(f"  🕒 Âge de l'article : {_ah:.1f}h ({_q})")
    # (add_recent est appelé plus bas, UNIQUEMENT si une plateforme a réellement publié)
    if _is_obituary(item.get("title", ""), item.get("summary", "")):
        cat = "hommage"   # décès → ton sobre, même en breaking (le label URGENT reste si urgent=True)
    _bn, _bl, _prev_heads = topic_history(conn, item.get("title", ""))
    _dossier = dossier_sujet(conn, item.get("title", ""))     # mémoire éditoriale 14 jours
    if _dossier:
        print(f"  📚 Affaire suivie : {len(_dossier)} publication(s) en mémoire")
    # 🚨→📰 ANTI-RÉCHAUFFÉ DU LABEL : "URGENT" est réservé à ce qui VIENT d'arriver.
    #    Un résultat sportif dont l'événement date (article > 3h) ou dont le sujet a DÉJÀ été
    #    couvert (suivi, défilé du lendemain…) redescend en label normal : l'info reste
    #    publiable, mais on n'écrit plus "URGENT" sur ce que la planète sait depuis hier.
    if urgent:
        _age_ok = (item.get("pub_ts") is None) or ((time.time() - item["pub_ts"]) / 3600 <= 3)
        if _is_sport_result(item.get("title", "")) and (_bn > 0 or not _age_ok):
            print("  ⬇️ Label URGENT retiré (résultat déjà connu / sujet déjà couvert) → label normal")
            urgent = False
    label_cat = "breaking" if urgent else cat
    body, headline_court, image_query, keywords, person, pays = gen_tweet_verified(
        item["title"], item["summary"], item["source"], cat, url=item.get("url"),
        prev_angles=_prev_heads, pub_ts=item.get("pub_ts"), angle_neuf=_angle,
        dossier=_dossier
    )
    if not body:
        print(f"  ⛔ Breaking abandonné (génération vide ou annonce périmée) : {item['title'][:50]}")
        return None
    tweet_final = build_full_tweet(body, label_cat, country=pays)
    photo = extract_photo(item["entry"]) if item.get("entry") else None
    raw_src, has_real, _generee = _meilleure_image(item, candidates, photo, person, image_query, label_cat)
    if _generee:
        body = _mention_illustration(body)
    if has_real:
        png_bytes, _ = build_png(headline_court, item["source"], label_cat, photo, image_query,
                                 article_url=item.get("url"), person=person, W=1080, H=1350,
                                 prefetched=(raw_src, has_real), headline_bottom=True)
        # Vidéo : d'abord une VRAIE vidéo de l'article (MP4 du flux, sinon vidéo éditoriale de la
        # page) ; à défaut, vidéo Pulse 9:16 construite sur la VRAIE photo de l'article.
        vid = None
        rv = extract_video_url(item.get("entry")) if item.get("entry") else None
        if rv:
            vid = fetch_video_file(rv)
        if not vid and video_worth_searching(label_cat) and _video_source_ok(item.get("source", "")):
            vp, _m = fetch_article_video(item.get("url"))
            if vp:
                vid = vp
        if not vid and raw_src and label_cat != "hommage" and random.random() < VIDEO_MIX_RATIO:
            vid = build_card_video(headline_court, item["source"], label_cat, raw_src,
                                   photo_url=photo, image_query=image_query,
                                   article_url=item.get("url"), person=person)
    else:
        # 🚫 Aucune vraie photo → breaking publié SANS image (texte seul), pas de vidéo dégradée.
        png_bytes, vid = None, None
        print("  🚫 Aucune vraie photo → breaking SANS image (texte seul)")
    try:
        _xurl = post_to_twitter(tweet_final, png_bytes, vid)
    except Exception as e:
        _xurl = None
        print(f"  ❌ X isolé : {e}")
    try:
        post_stat_followup(conn, item, _xurl)   # 📊 2ᵉ tweet graphique si thème éco (isolé)
    except Exception as e:
        print(f"  ⚠️ Data card isolée : {e}")
    _fb_id = None
    try:
        _fb_id = post_to_facebook(tweet_final, png_bytes, vid)
    except Exception as e:
        print(f"  ❌ Facebook isolé : {e}")
    png_ig, _ig_id = None, None
    if has_real:
        png_ig, _ = build_png(headline_court, item["source"], label_cat, photo, image_query,
                              article_url=item.get("url"), person=person, W=1080, H=1350,
                              prefetched=(raw_src, has_real), headline_bottom=True)
    if png_ig is None:
        print("  ⏸️ Instagram sauté (pas d'image, texte seul)")
    elif ig_allowed(conn):
        _ig_id = post_to_instagram(build_ig_caption(tweet_final, keywords), png_ig)
        if _ig_id:
            log_special(conn, "ig_post", [])
    else:
        print("  ⏸️ Instagram en pause (anti-blocage : min 90 min entre posts)")
    if vid and os.path.exists(vid):
        import shutil as _sh
        _sh.rmtree(os.path.dirname(vid), ignore_errors=True)
    # 🛡️ RÈGLE ABSOLUE : si AUCUNE plateforme n'a publié (X + FB + IG tous en échec), le sujet
    #    n'est PAS consommé — ni marqué vu, ni mémorisé, ni compté. Il repassera au run suivant.
    posted_ok = (_xurl is not None) or (_fb_id is not None) or (_ig_id is not None)
    if not posted_ok:
        print("  🛑 Aucune plateforme n'a publié → sujet NON consommé (repassera au prochain run)")
        return None
    add_recent(conn, item["title"])
    remember_recap_src(conn, item.get("title", ""), item.get("url"), label_cat)
    # Cadence : un VRAI breaking (urgent) NE réinitialise PAS le minuteur des news normales
    # (il est un bonus qui ne doit pas voler le créneau). Un suivi/buzz, lui, repousse la cadence.
    if bump_cadence:
        mark_cat(conn, label_cat)
    # 🔥 Anti-spam écho : ce sujet a déclenché SON alerte → mémorise le nb de médias à cet instant,
    # pour ne re-déclencher un suivi que si un NOUVEAU média rejoint le sujet ensuite.
    if item.get("_topic_canon"):
        topic_echo_mark_alerted(conn, item["_topic_canon"], item.get("_echo_n", BREAKING_SOURCES))
    log_keywords(conn, keywords)
    log_topic(conn, item.get("title", ""), keywords, corps=body)   # mémoire par sujet (suivi éditorial)
    # 🏷️ Un canal = un registre : l'hommage n'arme jamais le frein des buzz,
    #    et chaque canal garde son propre anti-rafale.
    _kind = "hommage" if label_cat == "hommage" else ("breaking" if urgent else "buzz")
    log_special(conn, _kind, keywords)
    if not bump_cadence:
        # 🧮 Toute publication de ce chemin (urgent, buzz, hommage) COMPTE dans le plafond
        #    quotidien (post_log), mais ne touche NI le minuteur de cadence (category_log)
        #    NI last_publish.txt. (Si bump_cadence est vrai, mark_cat l'a déjà comptée.)
        try:
            conn.execute("INSERT INTO post_log (category) VALUES (?)", (label_cat,))
            conn.commit()
        except Exception:
            pass
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

_SOURCES_PLATEAU = ("bfmtv", "bfm tv")

def _image_plateau_probable(source):
    """Vrai si la source illustre systématiquement ses articles par des captures de
    PLATEAU TÉLÉ (présentateurs, décor de studio) plutôt que par une photo du sujet.
    ⚠️ On ne cherche PAS à reconnaître un plateau sur l'image : une photo de studio a des
    visages, une vraie photo d'actualité aussi — la détection visuelle serait peu fiable.
    La règle par source est déterministe. Limitée à BFMTV, le cas constaté."""
    s = str(source or "").lower()
    return any(m in s for m in _SOURCES_PLATEAU)


def _image_pertinente(raw, titre, resume=""):
    """Vérifie EN REGARDANT l'image qu'elle illustre bien le sujet de l'article.

    Aucune règle par source ne peut attraper tous les cas : un média peut illustrer une
    nomination de sélectionneur par un mème sans rapport, une brève people par une photo
    de plateau. Le seul contrôle fiable est de regarder l'image.

    Renvoie True (garder), False (écarter), ou None si le contrôle n'a pas pu se faire —
    dans ce cas on GARDE l'image : on ne bloque jamais une publication sur un doute."""
    if not raw or not GEMINI_API_KEY:
        return None
    try:
        import base64 as _b64
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{GEMINI_MODEL}:generateContent")
        sujet = re.sub(r"\s+", " ", f"{titre}. {resume}").strip()[:300]
        question = (
            "Voici l'image qu'un compte d'actualité s'apprête à publier avec cette info :\n"
            f"« {sujet} »\n\n"
            "Cette image est-elle une illustration ACCEPTABLE de cette information ?\n"
            "Réponds NON si : c'est un mème ou un détournement humoristique ; c'est une "
            "personne DIFFÉRENTE de celle dont parle l'info ; c'est un plateau de télévision "
            "ou des présentateurs alors que l'info n'y a aucun rapport ; c'est une capture "
            "d'écran de réseau social ; l'image n'a visiblement AUCUN lien avec le sujet.\n"
            "Réponds NON ÉGALEMENT si l'image est CHOQUANTE et impubliable telle quelle : "
            "corps, sang, blessures visibles, cadavre, scène de violence explicite, "
            "détresse humaine crue. Un compte d'actualité ne publie pas ces images.\n"
            "Réponds OUI si l'image montre le sujet, la personne concernée, le lieu, "
            "l'événement, ou un visuel générique cohérent avec le thème.\n"
            'Réponds UNIQUEMENT : {"ok": true|false, "raison": "<5 mots max>"}')
        d = _post_gemini(
            url, {"contents": [{"parts": [
                      {"inlineData": {"mimeType": "image/jpeg",
                                      "data": _b64.b64encode(raw).decode()}},
                      {"text": question}]}],
                  "generationConfig": {"maxOutputTokens": 80, "temperature": 0,
                                       "responseMimeType": "application/json",
                                       "thinkingConfig": {"thinkingBudget": 0}}},
            famille="vision", timeout=45)
        _usage_gemini(d)
        txt = (((d.get("candidates") or [{}])[0].get("content") or {})
               .get("parts") or [{}])[0].get("text", "")
        rep = _parse_json_reponse(txt)
        if isinstance(rep, dict) and "ok" in rep:
            if not rep["ok"]:
                print(f"  👁️ Image écartée : {str(rep.get('raison') or 'hors-sujet')[:40]}")
            return bool(rep["ok"])
    except Exception as e:
        print(f"  ⚠️ Contrôle visuel indisponible ({str(e)[:60]}) → image conservée")
    return None


def _prompt_historique(sujet):
    """Consigne de génération pour une ÉVOCATION HISTORIQUE : style d'époque assumé,
    jamais confondable avec un document authentique. Le tweet porte la mention
    « image représentative »."""
    return ("Illustration d'évocation historique, style peinture documentaire sobre, "
            "atmosphère d'époque, sans texte ni logo, sans visage reconnaissable de "
            "personnalité réelle, cadrage large. Sujet : "
            + re.sub(r"\s+", " ", str(sujet or "")).strip()[:400])


def _prompt_illustration(titre, categorie=""):
    """Consigne de génération d'une IMAGE D'ILLUSTRATION pour une actualité.
    ⚠️ L'image ne doit JAMAIS prétendre montrer l'événement réel : pas de visage
    reconnaissable, pas de scène documentaire, pas de logo ni de texte. Une évocation
    du contexte, rien de plus — et le tweet portera la mention « illustration »."""
    sujet = re.sub(r"\s+", " ", str(titre or "")).strip()[:200]
    return ("Image d'illustration éditoriale, photographie d'ambiance sobre et réaliste, "
            "lumière naturelle, cadrage large, AUCUN texte, AUCUN logo, AUCUN visage "
            "reconnaissable, aucune personne identifiable, pas de scène de reportage. "
            "Évoque simplement le CONTEXTE de ce sujet : " + sujet)


def _meilleure_image(item, candidates, photo, person, image_query, cat):
    """Choisit la meilleure image disponible pour un article.
    ① Si la source est BFMTV, on tente d'abord la photo d'un autre média couvrant le même
       sujet : leurs illustrations sont des plateaux, sans rapport avec l'actualité.
    ② Sinon, la photo de l'article, CONTRÔLÉE VISUELLEMENT : un média peut illustrer une
       nomination par un mème (vécu : Brad Pitt bandé sur une info Zidane).
    ③ Image écartée → photo d'un autre média, puis illustration générée.
    Renvoie (octets, trouvée, générée)."""
    src = item.get("source", "")
    titre, resume = item.get("title", ""), item.get("summary", "")

    def _valide(raw):
        return raw if _image_pertinente(raw, titre, resume) is not False else None

    if _image_plateau_probable(src):
        raw, ok = _photo_secours_jumeau(item, candidates)
        if ok and _valide(raw):
            print(f"  📺 {src} illustre en plateau → photo d'un autre média retenue")
            return raw, True, False
    else:
        raw, ok = get_best_image(item.get("url"), photo, person, image_query, cat)
        if ok and _valide(raw):
            return raw, True, False
        if ok:
            # l'image de l'article a été écartée : on cherche chez un confrère
            raw2, ok2 = _photo_secours_jumeau(item, candidates)
            if ok2 and _valide(raw2):
                print("  🤝 Image d'origine hors-sujet → photo d'un autre média retenue")
                return raw2, True, False

    brut = _gemini_image(_prompt_illustration(titre, cat),
                         libelle="Illustration d'actualité")
    if brut:
        print(f"  🎨 Aucune photo exploitable → illustration générée (mention ajoutée)")
        return brut, True, True

    # dernier recours : l'image d'origine, même imparfaite, plutôt que rien
    raw, ok = get_best_image(item.get("url"), photo, person, image_query, cat)
    if not ok:
        raw, ok = _photo_secours_jumeau(item, candidates)
    return raw, ok, False


def _mention_illustration(body):
    """Ajoute la mention obligatoire quand l'image accompagnant le tweet est générée.
    Sans elle, un lecteur pourrait croire à une photo de l'événement — c'est
    précisément ce qu'un compte d'actualité ne doit jamais laisser penser."""
    txt = str(body or "").strip()
    if "illustration" in txt.lower():
        return txt
    m = re.search(r"\n\n\(\s*([^()]{2,40}?)\s*\)\s*$", txt)
    if m:      # fusionner avec la source : une seule ligne de pied
        return txt[:m.start()] + f"\n\n({m.group(1).strip()} · illustration générée)"
    return txt + "\n\n(Illustration générée)"


def _photo_secours_jumeau(item, candidates):
    """🖼️🤝 L'article sélectionné n'a pas d'image exploitable (403, paywall, flux nu) ?
    On tente celle d'un article JUMEAU : même sujet (≥2 mots saillants communs) chez un
    AUTRE média. C'est la même actu, la photo reste donc parfaitement raccord.
    Renvoie (raw_bytes, True) ou (None, False). Coût : 0 appel Claude."""
    try:
        sig = _sig_words(item.get("title", ""))
        if len(sig) < 2:
            return None, False
        for c in candidates or []:
            if c.get("url") == item.get("url"):
                continue
            if len(sig & _sig_words(c.get("title", ""))) < 2:
                continue
            # 1) miniature du flux RSS du jumeau (gratuit, jamais bloqué)
            ph = extract_photo(c["entry"]) if c.get("entry") else None
            raw, ok = get_best_image(c.get("url"), ph, None, None, "france")
            if ok and raw:
                print(f"  🖼️🤝 Image récupérée chez un média jumeau ({c.get('source','?')}) — même sujet")
                return raw, True
        return None, False
    except Exception:
        return None, False

_PREFIXES = (r"vid[ée]os?|photos?|images?|en\s+images?|infographies?|cartes?|"
             r"direct|en\s+direct|live|podcasts?|reportages?|d[ée]cryptages?|analyses?|"
             r"t[ée]moignages?|enqu[êe]tes?|portraits?|interviews?|entretiens?|r[ée]cits?|"
             r"tribunes?|[ée]ditos?|exclusif|exclusivit[ée]|à\s+la\s+une|le\s+fil")
_PREFIXE_RX = re.compile(
    # ① entre crochets/parenthèses/guillemets : le séparateur qui suit est facultatif
    rf"^\s*[\[\(«]\s*(?:{_PREFIXES})\s*[\]\)»]\s*[.:•\-–—]?\s*"
    # ② à nu : un séparateur est OBLIGATOIRE, pour ne pas amputer « Vidéosurveillance : … »
    rf"|^\s*(?:{_PREFIXES})\s*[.:•\-–—]\s*",
    re.IGNORECASE)

def _titre_propre(titre):
    """Retire les préfixes de rédaction d'un titre RSS (« Vidéo. », « EN IMAGES : »,
    « DIRECT — »…). Ce ne sont pas de l'information : ils polluent le tweet, brouillent
    la reconnaissance des doublons et alourdissent les prompts.
    Retire jusqu'à deux préfixes empilés (« Vidéo. Reportage. Titre »)."""
    t = (titre or "").strip()
    for _ in range(2):
        nouveau = _PREFIXE_RX.sub("", t, count=1).strip()
        if nouveau == t or not nouveau:
            break
        t = nouveau
    return t or (titre or "").strip()


def check_feeds(conn):
    global _META_CONN, _CLAUDE_CALLS, _CADENCE_DECISION, _EMBED_CONN
    _EMBED_CONN = conn
    _META_CONN = conn
    _CLAUDE_CALLS = 0
    _CADENCE_DECISION = None
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🔍 Check Pulse — version {PULSE_VERSION}")
    # 🔧 Diagnostic de configuration : dit NOIR SUR BLANC quel moteur est réellement actif.
    #    (Piège vécu : une clé rangée dans les secrets GitHub mais non transmise par le
    #    workflow reste invisible pour le bot — sans ce message, ça passe inaperçu.)
    try:
        _souhaits = {"analyse": LLM_ANALYSE, "rédaction": LLM_REDACTION,
                     "spéciaux": LLM_SPECIAUX}
        _actifs = " · ".join(f"{k}={v}" for k, v in _souhaits.items())
        if not any(v == "gemini" for v in _souhaits.values()):
            print(f"  🔧 Moteur : {_actifs}")
        elif GEMINI_API_KEY:
            print(f"  🔧 Moteur : {_actifs} (clé Gemini détectée ✅)")
        elif any(os.environ.get(v, "").strip().lower() == "gemini"
                 for v in ("LLM_ANALYSE", "LLM_REDACTION", "LLM_SPECIAUX")):
            # réglage EXPLICITE sur gemini mais aucune clé : c'est une erreur de configuration
            print("  ⚠️ GEMINI demandé mais AUCUNE clé reçue → tout reste sur Claude.")
            print("     La clé est-elle bien transmise par le workflow (bloc env:) ?")
        else:
            # cas normal sans clé : le repli Claude fait le travail, pas d'alarme inutile
            print("  🔧 Moteur : Claude (aucune clé Gemini fournie)")
    except Exception:
        pass

    # ── MODE COUPE DU MONDE : matchs du jour (matin) + prono la veille des matchs de la France ──
    # Chaque rendez-vous fixe qui publie fait 'return' → UN SEUL post par run (pas de rafale).
    try:
        if not special_done_today(conn, "cdm_jour") and _paris_hour() >= 8:
            if publish_cdm_day(conn):
                return
        if _paris_hour() >= 18:
            if publish_cdm_prono(conn):
                return
    except Exception as e:
        print(f"  ⚠️ Mode CDM : {e}")

    # ── RÉCAP DU SOIR : les 5 infos qui ont marqué la journée (1×/jour, après 21h Paris) ──
    try:
        if not special_done_today(conn, "recap") and _paris_hour() >= 21:
            if publish_recap(conn):
                return
    except Exception as e:
        print(f"  ⚠️ Récap du soir : {e}")

    # ── DÉCRYPTAGE QUOTIDIEN : carrousel Instagram + texte X/Facebook (1×/jour) ──
    if not special_done_today(conn, "thread") and _paris_hour() >= 9:
        carousel = gen_carousel(conn)
        if carousel:
            # Texte X + Facebook (assemblé depuis le carrousel, sans 2e appel Claude).
            # Hashtag INTÉGRÉ dans la phrase via la source unique _attach_hashtag (posé sur un nom
            # propre déjà présent) — fini les "#Mot1 #Mot2" empilés en fin de tweet.
            body = carousel_to_text(carousel)
            xfb = _attach_hashtag(body, "", carousel.get("keywords", []))

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

            # Carrousel Instagram : couverture (4:5) + slides de contenu (fond photo flouté)
            total = len(carousel["slides"]) + 1
            cover_ig, _ = build_png(carousel["cover_title"][:75], "Pulse", "monde", None,
                                    carousel["image_query"], W=1080, H=1350,
                                    prefetched=(raw_src, has_real), headline_bottom=True)
            slides_png = [cover_ig]
            for i, s in enumerate(carousel["slides"], start=2):
                slides_png.append(build_carousel_slide(s["titre"], s["points"], i, total,
                                                       is_last=(i == total), bg_photo=raw_src))

            # 🎬 Vidéo décryptage pour X/FB : les slides SONT la vidéo, texte qui s'écrit,
            # durée adaptée à la quantité de texte, slide d'abonnement en fin.
            # ⚠️ La vidéo est en 16:9 : on lui donne une couverture 16:9 NATIVE. Étirer la
            #    couverture 4:5 d'Instagram déformait toute la première scène (visages écrasés).
            cover_vid, _ = build_png(carousel["cover_title"][:75], "Pulse", "monde", None,
                                     carousel["image_query"], W=VIDEO_W, H=VIDEO_H,
                                     prefetched=(raw_src, has_real), headline_bottom=True, ss=1,
                                     no_pill=_pill_gif_path("monde") is not None,
                                     no_logo=_logo_gif_path() is not None)
            # 🔊 La narration est construite DANS build_decrypt_video, une fois la durée
            #    de la vidéo connue, pour être bornée et jamais tronquée. On passe ici le
            #    titre de couverture ; les intertitres et points viennent de `slides`.
            # 🎠🎬 DÉCRYPTAGE EN VIDÉO CARROUSEL : les slides du gabarit, assemblées en
            #    vidéo verticale avec la voix de synthèse par-dessus la musique.
            #    L'ancienne vidéo reste le repli si le montage échoue.
            vid_thread = None
            try:
                _sl, _ac, _ph = carrousel_decryptage(carousel, raw_photo=raw_src, categorie="monde")
                _pngs = rendre_carrousel(_sl, _ac, _ph)
                if _pngs:
                    _lu = _carr_texte_narration(carousel)
                    vid_thread = build_video_carrousel(
                        _pngs, _sl, voice_text=_lu, cat="monde",
                        voice_parts=_carr_narration_slides(carousel))
                    if _pngs:
                        cover_paysage = _pngs[0]      # l'aperçu devient la couverture du carrousel
            except Exception as e:
                print(f"  ⚠️ Carrousel décryptage indisponible ({str(e)[:70]})")
            if not vid_thread:
                # repli : l'ancienne vidéo de décryptage, si le carrousel n'a pas abouti
                vid_thread = build_decrypt_video(cover_vid or cover_paysage, carousel["slides"],
                                                 carousel.get("sujet", ""), bg_photo=raw_src,
                                                 decrypt_cat="monde",
                                                 voice_text=carousel.get("cover_title", ""))
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

            if url:
                # Instagram UNIQUEMENT après un X réussi : sinon, un échec X ferait re-poster
                # le carrousel Instagram à chaque nouvelle tentative (doublons visibles).
                try:
                    post_carousel_to_instagram(slides_png, build_ig_caption(body, carousel.get("keywords")))
                    log_special(conn, "ig_post", [])   # espacement anti-blocage Instagram
                except Exception as e:
                    print(f"  ❌ Instagram isolé : {e}")
                log_special(conn, "thread", carousel["keywords"])
                print(f"  🎠 Décryptage du jour publié (carrousel Instagram) [{carousel['sujet']}]")
                return
            else:
                print("  🛑 X n'a pas publié → le décryptage retentera au prochain run (contenu en cache)")

    # ── SONDAGE QUOTIDIEN (après-midi, 1×/jour) ──
    if not special_done_today(conn, "poll") and _paris_hour() >= 12:
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
    blocked_kws = recent_keywords(conn, hours=2)
    allow_sport_result = not sport_result_recent(conn)   # autorise UN résultat de match malgré le blocage
    candidates  = []
    pre_filtered = 0
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:3]:
                url   = entry.get("link", "")
                title = _titre_propre(entry.get("title", ""))
                summ  = _strip_html(entry.get("summary", entry.get("description", "")))
                # Date de publication (epoch) — nécessaire pour le suivi live des matchs France
                # (sans ça, _detect_france_match rejette TOUT par prudence : aucune date = pas de live)
                pub_ts = None
                for fld in ("published_parsed", "updated_parsed"):
                    val = entry.get(fld)
                    if val:
                        try:
                            pub_ts = time.mktime(val); break
                        except Exception:
                            pass
                if url and title and not is_seen(conn, url):
                    # 🔥 ÉCHO : on compte ce média sur le sujet AVANT tout pré-filtre, pour qu'un
                    # sujet chaud accumule ses médias même s'il a déjà été tweeté (→ suivi possible).
                    # Ne double jamais un même média sur un sujet (UNIQUE) ni un même article (is_seen).
                    try:
                        topic_echo_add(conn, title, fi["source"])
                    except Exception:
                        pass
                    # Pré-filtre GRATUIT (sans coût Claude) : un titre qui touche un sujet déjà
                    # traité aujourd'hui n'est PLUS jeté d'office. La mémoire par sujet décide si
                    # une SUITE est permise maintenant (plafond/jour + écart mini) ; Claude jugera
                    # ensuite la valeur ajoutée. SAUF résultat de match (dérogation dédiée).
                    title_low = title.lower()
                    is_fu = False
                    if blocked_kws and any(kw in title_low for kw in blocked_kws):
                        if allow_sport_result and _is_sport_result(title):
                            pass  # résultat final d'un match déjà couvert (1 dérogation/4h)
                        else:
                            allowed, code, _heads = topic_gate(conn, title)
                            if allowed:
                                is_fu = True   # développement d'un sujet en cours → part en analyse
                            elif code == "too_soon":
                                # trop tôt sur ce sujet : on le REVERRA au prochain run (pas marqué vu)
                                pre_filtered += 1
                                continue
                            else:  # 'cap' : plafond du sujet atteint pour aujourd'hui
                                mark_seen(conn, url, title)
                                pre_filtered += 1
                                continue
                    candidates.append({"url": url, "title": title, "summary": summ, "source": fi["source"], "entry": entry, "followup": is_fu, "pub_ts": pub_ts})
        except Exception as e:
            print(f"  ❌ RSS {fi['source']}: {e}")

    if pre_filtered:
        print(f"  🚫 {pre_filtered} articles pré-filtrés (mots-clés bloqués, sans coût Claude)")

    recent = get_recent(conn)

    # ── MODE BREAKING : sujets chauds (écho 12h) → publication immédiate, PLUSIEURS sujets/run ──
    # On parcourt tous les sujets chauds (triés par nb de médias). Pour chacun :
    #   • jamais tweeté dessus → BREAKING (contourne la cadence, ne la réinitialise pas)
    #   • déjà tweeté → Claude/topic_gate jugent s'il y a du NEUF → suivi (soumis à la cadence) ;
    #     sinon on passe au sujet chaud suivant.
    # Le 1er qui publie arrête le run (1 post/run). Si aucun ne publie → on continue vers les news.
    nb_today = posts_today(conn)
    hot_topics = detect_breaking(conn, candidates, return_all=True)
    if hot_topics and not breaking_recent(conn):
        # 💰 UN SEUL appel d'analyse pour TOUS les sujets chauds du run. Le prompt d'analyse
        #    pèse ~2 400 tokens : l'envoyer une fois par sujet était le principal gaspillage
        #    (3 sujets chauds = 3 × 2 400 tokens de consigne identique). On pré-analyse ici
        #    en un seul lot ; la boucle ci-dessous lira le résultat dans le cache (0 coût).
        _hot_batch = []
        for _h in hot_topics:
            if get_cached_analysis(conn, _h.get("url")):
                continue                                   # déjà analysé à un run passé
            _ts = _h.get("pub_ts")
            if _ts and (time.time() - _ts) > STALE_BREAKING_HOURS * 3600:
                continue                                   # trop vieux pour un breaking
            if _hot_prescore(_h.get("title", "")) < 3:
                continue                                   # banal → jamais payé
            if topic_gate(conn, _h.get("title", ""))[1] in ("cap", "too_soon", "stale"):
                continue                                   # déjà traité aujourd'hui
            _hot_batch.append(_h)
        if len(_hot_batch) > 1:
            try:
                for _h, _a in zip(_hot_batch, analyse_batch(_hot_batch, recent, blocked_kws)):
                    cache_analysis(conn, _h["url"], _a)
                print(f"  💰 {len(_hot_batch)} sujets chauds analysés en UN seul appel (au lieu de {len(_hot_batch)})")
            except Exception:
                pass                                       # la boucle analysera au cas par cas
        for hot in hot_topics:
            if nb_today >= DAILY_POST_CAP and not _is_urgent_alert(hot.get("title", ""), hot.get("summary", "")):
                print(f"  🛑 Plafond quotidien atteint ({nb_today}) — sujet chaud ignoré (une ALERTE VITALE passerait).")
                continue
            # 🕒 GARDE-FOU FRAÎCHEUR : un article dont la publication remonte à plus de
            # STALE_BREAKING_HOURS n'est plus assez frais pour un "breaking" (pas de réchauffé).
            # Il reste éligible à une publication normale plus bas. pub_ts absent → on ne bloque pas.
            pub_ts = hot.get("pub_ts")
            if pub_ts and (time.time() - pub_ts) > STALE_BREAKING_HOURS * 3600:
                age_h = int((time.time() - pub_ts) / 3600)
                print(f"  🕒 Sujet chaud mais article ancien ({age_h}h) → pas de breaking sur du réchauffé → suivant")
                continue
            # garde-fou gratuit : un sujet "chaud" mais éditorialement banal ne paie pas Claude
            pre_score = _hot_prescore(hot["title"])
            if pre_score < 3:
                print(f"  ⚪ Sujet chaud mais banal (pré-classement {pre_score}) → suivant")
                continue
            # a-t-on DÉJÀ tweeté sur ce sujet ? (mémoire par sujet + signal d'écho)
            allowed, code, prev_heads = topic_gate(conn, hot["title"])
            echo_kind = hot.get("_echo_kind", "breaking")
            is_followup = (code == "followup") or (echo_kind == "followup")
            if code == "cap":
                print(f"  ⏭️  Sujet chaud déjà traité (plafond du jour) → suivant")
                continue
            if code == "too_soon":
                print(f"  ⏭️  Sujet chaud déjà traité (trop tôt, anti-spam) → suivant")
                continue
            print(f"  🚨 Sujet chaud ({echo_kind}, {hot.get('_echo_n','?')} médias) : {hot['title'][:50]}")
            # Mémoire d'analyse : ne PAS re-payer Claude si cet article a déjà été analysé à un run passé.
            a = get_cached_analysis(conn, hot["url"])
            if a is None:
                try:
                    a = analyse_batch([hot], recent, blocked_kws)[0]
                    cache_analysis(conn, hot["url"], a)
                except Exception:
                    a = {"score": BREAKING_SCORE, "category": "breaking", "is_duplicate": False}
            else:
                print("  💾 Analyse réutilisée (déjà vue à un run précédent, 0 coût)")
            # Claude juge : doublon = rien de neuf par rapport à nos tweets → on passe au sujet suivant
            if a.get("is_duplicate"):
                print("  ⏭️  Rien de neuf sur ce sujet chaud (doublon) → suivant")
                continue
            score = int(a.get("score", 0))
            if is_followup:
                if score >= BREAKING_SCORE:
                    # 🚨➕ SUIVI D'UN VRAI BREAKING (événement majeur EN COURS) : c'est À PART.
                    # Il passe TOUT DE SUITE — sans tenir compte du délai de cadence entre tweets —
                    # et il ne remet PAS le compteur à zéro (les actus normales ne sont pas retardées).
                    # Anti-spam assuré en amont : un suivi n'existe QUE si un NOUVEAU média a couvert
                    # le développement (jamais deux fois la même info).
                    try:
                        if publish_breaking(conn, hot, a.get("category", "breaking"), urgent=True, bump_cadence=False, candidates=candidates) is not None:
                            print(f"  🚨➕ Suivi de BREAKING publié immédiatement : {hot['title'][:50]}")
                            return
                        continue          # abandonné (annonce périmée) → sujet suivant
                    except Exception as e:
                        print(f"  ❌ Suivi breaking échoué : {e}")
                elif score >= BUZZ_SCORE:
                    # Suivi d'un sujet chaud : CANAL BONUS — contourne la cadence et ne la
                    # réinitialise pas. Frein propre : 1 buzz max / BUZZ_GAP_MIN.
                    if buzz_recent(conn):
                        print(f"  🚰 Buzz déjà publié il y a moins de {BUZZ_GAP_MIN} min → on espace")
                        continue
                    try:
                        if publish_breaking(conn, hot, a.get("category", "france"), urgent=False, bump_cadence=False, candidates=candidates) is not None:
                            print(f"  ➕ Suivi publié (info complémentaire) : {hot['title'][:50]}")
                            return
                        continue          # abandonné → sujet suivant
                    except Exception as e:
                        print(f"  ❌ Suivi échoué : {e}")
                else:
                    print(f"  → Pas assez d'info neuve pour un suivi (score {score}) → suivant")
                    continue
            else:
                # NOUVEAU sujet chaud.
                is_obit = _is_obituary(hot.get("title", ""), hot.get("summary", ""))
                urgent_alert = _is_urgent_alert(hot.get("title", ""), hot.get("summary", ""))
                # Un DÉCÈS de personnalité (obituaire avéré) est traité comme un vrai breaking dès un
                # score ≥ BUZZ_SCORE : la valeur d'un hommage est dans l'immédiateté. Le score continue
                # de filtrer les décès mineurs (personnalité peu connue → score bas → rythme normal).
                # Une ALERTE DE DANGER IMMINENT (tsunami, évacuation, séisme fort, attentat…) contourne
                # AUSSI la cadence dès score ≥ BUZZ_SCORE : ces infos ne doivent JAMAIS attendre.
                # 🌙 La NUIT (23h-7h), le fil se met en quasi-pause : seuls passent ce qui ne
                #    peut VRAIMENT pas attendre — alerte de danger imminent (tsunami, attentat…)
                #    et décès marquant. Un "breaking" ordinaire (gros score sans danger) attend
                #    le matin plutôt que de réveiller le fil à 3h pour une actu non vitale.
                _night = _is_night()
                _vital = urgent_alert or is_obit
                breaking_immediat = (urgent_alert and score >= BUZZ_SCORE) \
                                    or (is_obit and score >= BUZZ_SCORE) \
                                    or (score >= BREAKING_SCORE and not (_night and not _vital))
                if breaking_immediat:
                    # VRAI breaking (attentat, catastrophe, décès marquant) → contourne la cadence,
                    # ne la réinitialise pas. Un décès garde le label sobre (géré dans publish_breaking).
                    try:
                        if publish_breaking(conn, hot, a.get("category", "breaking"),
                                            urgent=not is_obit, bump_cadence=False, candidates=candidates) is not None:
                            tag = "🕊️ Hommage" if is_obit else ("🚨🌊 ALERTE URGENTE" if urgent_alert else "🚨 BREAKING")
                            print(f"  {tag} publié immédiatement (contourne la cadence) : {hot['title'][:55]}")
                            return
                        continue          # abandonné → sujet suivant
                    except Exception as e:
                        print(f"  ❌ Breaking échoué : {e}")
                elif score >= BUZZ_SCORE and nb_today < DAILY_POST_SOFT and not _night:
                    # BUZZ : CANAL BONUS — contourne la cadence, ne la réinitialise pas.
                    # 🌙 Fermé la NUIT : un buzz viral non vital attend le matin.
                    # Frein propre le jour : 1 buzz max / BUZZ_GAP_MIN.
                    if buzz_recent(conn):
                        print(f"  🚰 Buzz déjà publié il y a moins de {BUZZ_GAP_MIN} min → on espace")
                        continue
                    try:
                        if publish_breaking(conn, hot, a.get("category", "france"), urgent=False, bump_cadence=False, candidates=candidates) is not None:
                            print(f"  ⚡ Sujet chaud publié (dans le rythme) : {hot['title'][:55]}")
                            return
                        continue          # abandonné → sujet suivant
                    except Exception as e:
                        print(f"  ❌ Publication sujet chaud échouée : {e}")
                else:
                    print(f"  → Sujet chaud pas assez fort (score {score}) → suivant")
                    continue

    # ── MATCH DE LA FRANCE : mi-temps + score final (prioritaire, contourne la cadence) ──
    try:
        if publish_france_live(conn, candidates):
            return
    except Exception as e:
        print(f"  ⚠️ Suivi match France : {e}")

    # ── PUBLICATION NORMALE (rythme selon l'heure) ──
    # Plafond GLOBAL : au-delà du seuil souple (20), on garde la place au chaud (breaking/France live).
    if nb_today >= DAILY_POST_CAP:
        print(f"  🛑 Plafond quotidien ferme atteint ({nb_today}/{DAILY_POST_CAP}) — stop publications.")
        return
    if nb_today >= DAILY_POST_SOFT:
        print(f"  🛑 Seuil souple atteint ({nb_today}/{DAILY_POST_SOFT}) — on garde la place au chaud (breaking/France live).")
        return
    # Le SEUL contenu qui contourne la cadence est le suivi des matchs de la France
    # (mi-temps/score final, géré par publish_france_live ci-dessus, déjà traité et limité à
    # 2 posts/match). Tout le reste — y compris les résultats sportifs d'autres équipes —
    # respecte la cadence normale comme n'importe quelle actu, pour ne pas monopoliser le rythme
    # de publication pendant les périodes riches en sport (ex: Coupe du Monde).
    # 🎯 La cadence ne gate que les NEWS NORMALES. Les canaux bonus (histoire, GTA 6)
    #    sont tentés à chaque run — ils ne prennent pas le créneau des news et ne le décalent pas.
    # 🌙 La NUIT (23h-7h), les actualités NORMALES sont suspendues : seules les alertes
    #    vitales et les décès marquants passent, par le chemin « sujet chaud » ci-dessus.
    #    Conséquence : aucune analyse payée la nuit — on ne paie pas pour trier des articles
    #    qu'on ne publiera pas.
    _night_now = _is_night()
    cadence_ok = (not _night_now) and should_publish_now(conn)
    if _night_now:
        print("  🌙 Nuit : actualités normales suspendues (seules les alertes vitales passent)")
    elif not cadence_ok:
        print("  ⏸️  Cadence pas prête → seuls les canaux bonus (histoire, GTA 6) sont tentés")

    if not candidates:
        print("  → Aucun article nouveau.")
        return

    print(f"  → {len(candidates)} articles à analyser...")
    if blocked_kws:
        print(f"  🚫 Mots-clés bloqués (2h) : {', '.join(blocked_kws)}")

    # ── Anti-doublon RENFORCÉ : on écarte tout article trop proche d'un titre DÉJÀ publié
    #    récemment (≥2 mots significatifs communs). Évite de re-tweeter le même sujet sous
    #    une formulation légèrement différente (ex: deux articles "Coupe du monde 48 équipes"). ──
    published_sigs = [_sig_words(t) for t in recent]
    # ── Filtre ARTICLE-GUIDE / marronnier périmé : un papier "découvrez le calendrier",
    #    "tout savoir", "débute bientôt"... n'est pas une actu, et renvoie dans le vide sur X. ──
    GUIDE_RX = re.compile(
        r"(tout savoir sur|tout ce qu'il faut savoir|on vous explique|"
        r"calendrier complet|guide complet|à quelle heure (et où |voir |suivre)|"
        r"où (et comment |regarder|voir) (le|la|les|ce|cette)|comment (voir|suivre|regarder) (le|la|les|en direct)|"
        r"débute (bientôt|ce|demain|la semaine)|va (commencer|débuter) (bientôt|ce|demain|le)|"
        r"c'est (bientôt|parti pour le)|notre dossier|le programme complet (de|du|des)|"
        r"toutes les infos pratiques|mode d'emploi)", re.I)

    filtered_candidates = []
    for c in candidates:
        title = c.get("title", "")
        # marronnier / article-guide → écarté (sans coût Claude)
        if GUIDE_RX.search(title) or GUIDE_RX.search(c.get("summary", "")):
            mark_seen(conn, c["url"], title); continue
        # doublon avec un sujet déjà publié récemment
        # (on NE l'applique PAS aux développements d'un sujet en cours : la mémoire par sujet
        #  les a déjà validés, et Claude jugera ensuite s'ils apportent réellement du neuf)
        sw = _sig_words(title)
        if not c.get("followup") and len(sw) >= 2 and any(len(sw & ps) >= 2 for ps in published_sigs):
            print(f"  ♻️ Doublon d'un sujet déjà publié, écarté : {title[:50]}")
            mark_seen(conn, c["url"], title); continue
        filtered_candidates.append(c)
    candidates = filtered_candidates

    scored      = []
    to_analyse  = []
    for c in (candidates if cadence_ok else []):
        cached = get_cached_analysis(conn, c["url"])
        if cached:
            a, score = cached, int(cached.get("score", 0))
            if a.get("is_duplicate"):
                mark_seen(conn, c["url"], c["title"]); continue
            if score < SCORE_MINIMUM:
                mark_seen(conn, c["url"], c["title"]); continue
            scored.append({**c, "analysis": a, "score": score})
            print(f"  ♻️ {score}/10 [{a.get('category')}] (déjà analysé, cache) : {c['title'][:50]}")
        else:
            to_analyse.append(c)

    # 💰 Limite le nombre d'articles ENVOYÉS à Claude par passage (les articles en
    # cache restent gratuits). On garde un échantillon varié pour borner le coût API.
    # 📈 L'analyse est passée sur le fournisseur GRATUIT : on peut donc en examiner
    #    beaucoup plus sans surcoût. Plus de candidats analysés = moins de sujets ratés,
    #    et un meilleur choix final. (Le pré-classement gratuit filtre toujours en amont.)
    MAX_ANALYSE = 20 if (LLM_ANALYSE == "gemini" and GEMINI_API_KEY) else 8
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

    # 📣 ÉCHO MÉDIATIQUE — le signal d'importance le plus fiable : combien de médias en parlent ?
    # Appliqué à TOUS les candidats retenus (batch ET cache), avant les autres bonus, pour qu'une
    # actu largement reprise passe devant une info isolée notée pareil par Claude.
    _echo = source_echo(candidates)
    for item in scored:
        n = _echo.get(item.get("url"), 1)
        item["_echo"] = n
        b = echo_bonus(n)
        if b:
            item["score"] = min(10, item["score"] + b)

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

    # ⏱️ PRIORITÉ À LA FRAÎCHEUR : à intérêt comparable, la plus RÉCENTE passe devant.
    #    Le score lui-même n'est pas modifié (il sert aux seuils breaking/buzz) : seul l'ORDRE change.
    _now_ts = time.time()
    def _freshness(it):
        ts = it.get("pub_ts")
        if not ts:
            return 0.0                      # date inconnue → ni bonus ni malus
        h = (_now_ts - ts) / 3600
        if h <= 0.5: return 2.6              # moins de 30 min : l'actu vient de tomber
        if h <= 1:  return 2.0
        if h <= 3:  return 1.2
        if h <= 6:  return 0.5
        if h <= 12: return 0.0
        if h <= 24: return -1.5
        return -3.0                          # au-delà d'un jour : ce n'est plus une actualité
    def _age_h(it):
        ts = it.get("pub_ts")
        return None if not ts else (_now_ts - ts) / 3600
    scored.sort(key=lambda x: (x["score"] + _freshness(x), x["score"]), reverse=True)

    if scored:
        print("  🏁 Classement final (score · médias · âge) :")
        for it in scored[:5]:
            a = _age_h(it)
            age = "âge ?" if a is None else (f"{a:.1f}h" + (" ⚠️" if a > 24 else ""))
            print(f"     {it['score']}/10 · {it.get('_echo', 1)} média(s) · {age} — {it['title'][:44]}")

    top, used = [], set()
    for item in scored:
        # 🗞️ Anti-réchauffé : une actu de plus de STALE_NEWS_HOURS n'est plus une nouvelle,
        #    sauf si c'est un vrai développement (suivi) d'un sujet en cours.
        a = _age_h(item)
        if a is not None and a > STALE_NEWS_HOURS and not item.get("followup"):
            print(f"     ⏳ Écarté (trop ancien, {a:.0f}h) : {item['title'][:44]}")
            continue
        cat = item["analysis"]["category"]
        if cat not in used:
            top.append(item); used.add(cat)
        if len(top) >= MAX_PAR_PASSE: break

    # Histoire du jour (1×/jour, vérifié Wikipedia) — 🌙 jamais la nuit (canal bonus)
    histoire = None if _is_night() else gen_histoire_du_jour(conn)
    if histoire and "histoire" not in used and len(top) < MAX_PAR_PASSE + 1:
        top.append(histoire); used.add("histoire")

    # 🎮 GTA 6 — rubrique spécialisée, ancrée sur de VRAIS articles (max 2/jour, hors cadence)
    gta = gen_gta6_hype(conn, candidates)
    if gta and "gta6" not in used:
        # Anti-doublon : si le même article est déjà pris par le flux normal, la rubrique
        # spécialisée l'emporte (traitement éditorial dédié) et on retire la version générique.
        if gta.get("url"):
            top = [it for it in top if it.get("url") != gta["url"]]
        top.append(gta); used.add("gta6")

    if not top:
        print("  → Rien à publier.")
        return

    print(f"  → {len(top)} sélectionné(s) [{', '.join(used)}]")

    for item in top:
        try:
            cat = item["analysis"]["category"]
            a   = item["analysis"]
            keywords = []
            title_s, summary_s = item.get("title", ""), item.get("summary", "")

            # ── VÉRIFICATION FINALE DÉCÈS (gratuite, Wikipedia) ──
            # Déclenchée UNIQUEMENT sur l'article sélectionné (le plus intéressant), et seulement
            # s'il contient un mot de décès ET un nom de personne → quelques appels/jour maximum.
            # Corrige dans les DEUX sens : rattrape un vrai décès mal classé (ex: chef cuisinier
            # en "culture"), et bloque un faux hommage (personne bien vivante).
            DEATH_HINT = re.compile(r"\b(mort|morte|décès|décédé|décédée|disparition|"
                                    r"s'est éteint|nous a quitté|meurt|obsèques|funérailles|"
                                    r"in memoriam|hommage)\b", re.I)
            already_obit = _is_obituary(title_s, summary_s)
            if cat != "breaking" and DEATH_HINT.search(title_s + " " + summary_s):
                person_name = _extract_person_name(title_s, summary_s)
                if person_name:
                    verdict = verify_death_wikipedia(person_name)
                    if verdict == "dead":
                        cat = "hommage"           # décès confirmé → hommage (même si classé culture/sport)
                        print(f"  ✅ Décès CONFIRMÉ sur Wikipedia ({person_name}) → hommage")
                    elif verdict == "alive" and already_obit:
                        # les mots-clés criaient à l'hommage, mais Wikipedia dit la personne vivante
                        print(f"  🚫 Faux décès bloqué : {person_name} est vivant(e) selon Wikipedia")
                        continue                  # on n'publie pas ce faux hommage
                    elif already_obit:
                        cat = "hommage"           # Wikipedia incertain → on garde la décision mots-clés
                elif already_obit:
                    cat = "hommage"
            elif cat != "breaking" and already_obit:
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
                video = None
                _hn, _hl, _prev_heads = topic_history(conn, item["title"])
                _dossier = dossier_sujet(conn, item["title"])
                if _dossier:
                    print(f"  📚 Affaire suivie : {len(_dossier)} publication(s) en mémoire")
                body, headline_court, image_query, keywords, person, pays = gen_tweet_verified(
                    item["title"], item["summary"], item["source"], cat, url=item.get("url"),
                    prev_angles=_prev_heads, pub_ts=item.get("pub_ts"),
                    angle_neuf=item.get("_angle_neuf") or "", dossier=_dossier
                )
                if not body:
                    print(f"  ⛔ Sujet abandonné (génération vide ou annonce périmée) : {item['title'][:50]}")
                    continue
                tweet_final = build_full_tweet(body, cat, country=pays)
                photo       = extract_photo(item["entry"])

            # ⚖️ Les vidéos d'articles tiers ne sont JAMAIS republiées (droit d'auteur / risque de strike).
            #    Les seules vidéos publiées sont celles générées par Pulse (build_video).
            video_path = None

            # Image paysage (X + Facebook) — on récupère aussi l'image source pour réutilisation
            raw_src, has_real, _generee = _meilleure_image(item, candidates, photo, person, image_query, cat)
            if _generee:
                body = _mention_illustration(body)
            if not has_real and item.get("raw_image"):
                # 🎨 uniquement l'histoire du jour : illustration générée, tweet déjà annoté
                raw_src, has_real = item["raw_image"], True

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
                                               obituary["desc"], item["source"], W=1080, H=1350)
                png_ig = build_hommage_card(raw_src, obituary["name"], obituary["dates"],
                                            obituary["desc"], item["source"], W=1080, H=1350)
                video_path = build_video("hommage", obituary, "hommage", raw_src, item["source"])
                print(f"  🕊️ Carte hommage : {obituary['name']}")
            elif not has_real and cat != "gta6":
                # 🚫 Aucune vraie photo → on publie SANS visuel généré (ni carte, ni vidéo sur
                # fond dégradé). On garde UNIQUEMENT une VRAIE vidéo de l'article si le média l'expose.
                # Exception : la rubrique GTA 6 exige TOUJOURS une image (carte Pulse à défaut de photo).
                png_bytes = png_ig = png_nm = None
                if not video_path:
                    real_vid_url = extract_video_url(item.get("entry")) if item.get("entry") else None
                    if real_vid_url:
                        video_path = fetch_video_file(real_vid_url)
                    if not video_path and video_worth_searching(cat) and _video_source_ok(item.get("source", "")):
                        vp, _vmeta = fetch_article_video(item.get("url"))
                        if vp:
                            video_path = vp
                if video_path:
                    print("  🚫 Pas de photo mais vidéo éditoriale trouvée → tweet avec vidéo")
                else:
                    print("  🚫 Aucune vraie photo → publication SANS image (texte seul)")
            else:
                png_bytes, png_nm = build_png(
                    headline_court, item["source"], cat, photo, image_query,
                    article_url=item.get("url"), person=person,
                    W=1080, H=1350, prefetched=(raw_src, has_real), headline_bottom=True
                )
                png_ig, _ = build_png(
                    headline_court, item["source"], cat, photo, image_query,
                    article_url=item.get("url"), person=person,
                    W=1080, H=1350, prefetched=(raw_src, has_real), headline_bottom=True
                )
                if not video_path:
                    # 1) vraie vidéo de l'article (MP4 direct) si le flux RSS l'expose
                    real_vid_url = extract_video_url(item.get("entry")) if item.get("entry") else None
                    if real_vid_url:
                        video_path = fetch_video_file(real_vid_url)
                    # 2) sinon, si une vidéo a une vraie valeur ici (needs_video), on cherche la vidéo
                    #    ÉDITORIALE dans la PAGE de l'article (og:video/JSON-LD, jamais une pub).
                    if not video_path and video_worth_searching(cat) and _video_source_ok(item.get("source", "")):
                        vp, _vmeta = fetch_article_video(item.get("url"))
                        if vp:
                            video_path = vp
                    # 3) sinon : une fois sur deux, la CARTE PULSE animée en 16:9 (le titre s'écrit) ;
                    #    l'autre fois, la carte fixe → le fil alterne et ne devient pas monotone.
                    #    L'image reste attachée : si l'envoi de la vidéo échoue, le tweet sort en carte.
                    if not video_path and has_real and raw_src and random.random() < VIDEO_MIX_RATIO:
                        video_path = build_card_video(headline_court, item["source"], cat, raw_src,
                                                      photo_url=photo, image_query=image_query,
                                                      article_url=item.get("url"), person=person)

            posted_ok = False
            res_x = None
            _pub_ts = item.get("pub_ts")
            if _pub_ts:
                _age_h = (time.time() - _pub_ts) / 3600
                _frais = "frais ✅" if _age_h <= 6 else ("récent" if _age_h <= 24 else "ANCIEN ⚠️")
                print(f"  🕒 Âge de l'article à la publication : {_age_h:.1f}h ({_frais})")
            try:
                res_x = post_to_twitter(tweet_final, png_bytes, video_path)
                posted_ok = posted_ok or (res_x is not False and res_x is not None)
            except Exception as e:
                print(f"  ❌ X isolé : {e}")
            try:
                post_stat_followup(conn, item, res_x)   # 📊 2ᵉ tweet graphique si thème éco (isolé)
            except Exception as e:
                print(f"  ⚠️ Data card isolée : {e}")
            try:
                res_fb = post_to_facebook(tweet_final, png_bytes, video_path)
                posted_ok = posted_ok or (res_fb is not False and res_fb is not None)
            except Exception as e:
                print(f"  ❌ Facebook isolé : {e}")
            if png_ig is None:
                print("  ⏸️ Instagram sauté pour ce post (pas d'image, texte seul)")
            elif ig_allowed(conn):
                try:
                    res_ig = post_to_instagram(build_ig_caption(tweet_final, keywords), png_ig)
                    if res_ig is not False and res_ig is not None:
                        posted_ok = True
                    log_special(conn, "ig_post", [])
                except Exception as e:
                    print(f"  ❌ Instagram isolé : {e}")
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

            # ⚠️ On ne "consomme" le sujet (blocage mots-clés 12h, marquage vu, cadence) QUE si
            # au moins une plateforme a VRAIMENT publié. Sinon un échec réseau/403 ferait perdre
            # le sujet pour 12h (cas Jubillar : X en 403 → sujet chaud verrouillé sans jamais sortir).
            if not posted_ok:
                print(f"  ⚠️ Aucune plateforme n'a publié — sujet NON consommé, réessai au prochain run : {item['title'][:55]}")
                time.sleep(2)
                return
            add_recent(conn, item["title"])   # mémoire anti-doublon : UNIQUEMENT après publication réelle
            remember_recap_src(conn, item.get("title", ""), item.get("url"), cat)

            # 🎮 GTA 6 = canal bonus : on logue pour le compteur (max 2/jour) mais on NE touche PAS
            # à mark_cat → il ne retarde pas le rythme des autres actus (comme le suivi France live).
            if item.get("_special_kind") == "gta6":
                # on mémorise la CATÉGORIE + le sujet : sert à varier les thèmes et à éviter les redites
                log_special(conn, "gta6", [f"cat:{item.get('_gta6_cat','actu')}", item.get("title", "")])
                if item.get("url"):
                    mark_seen(conn, item["url"], item["title"])
                print(f"  🎮 GTA 6 (niveau {item.get('_gta6_level','?')} · {item.get('_gta6_cat','')}) : {item['title'][:45]}")
                time.sleep(4)
                continue

            # 🕊️📜 CANAUX BONUS (histoire, hommage) : ils NE réinitialisent PAS la cadence
            #    (pas de mark_cat) → ils ne volent jamais le créneau des news normales.
            if cat == "histoire":
                log_special(conn, "histoire", [])
                if item.get("url"):
                    mark_seen(conn, item["url"], item["title"])
                print(f"  📜 Histoire du jour publiée (canal bonus, cadence intacte)")
                time.sleep(4)
                continue
            if cat == "hommage":
                log_keywords(conn, keywords)
                log_topic(conn, item.get("title", ""), keywords, corps=body)
                if item.get("url"):
                    mark_seen(conn, item["url"], item["title"])
                print(f"  🕊️ Hommage publié (canal bonus, cadence intacte) : {item['title'][:50]}")
                time.sleep(4)
                continue
            mark_cat(conn, cat)
            log_keywords(conn, keywords)
            log_topic(conn, item.get("title", ""), keywords, corps=body)   # mémoire par sujet (suivi éditorial)
            if cat == "sport" and _is_sport_result(item.get("title", "")):
                log_special(conn, "sport_result", keywords)   # 1 dérogation résultat / 4h
            if item.get("url"):
                mark_seen(conn, item["url"], item["title"])
            print(f"  ✅ Publié [{cat}]: {item['title'][:55]}")
            if keywords:
                print(f"  🔒 Mots-clés bloqués 2h: {', '.join(keywords)}")
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
