"""
Pulse NewsBot — bot d'actualité française.
Génère des tweets engageants avec image PNG, envoyés par email + posté sur X.
"""
import feedparser, anthropic, sqlite3, hashlib, json, time, os, smtplib, random
import urllib.request, urllib.parse, urllib.error, re
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
    {"url": "https://www.futura-sciences.com/rss/actualites.xml",      "source": "Futura Sciences"},
    {"url": "https://www.sciencesetavenir.fr/rss.xml",                 "source": "Sciences et Avenir"},
    # 🏆 Sport
    {"url": "https://www.lequipe.fr/rss/actu_rss.xml",                 "source": "L'Équipe"},
    {"url": "https://www.eurosport.fr/rss.xml",                        "source": "Eurosport"},
    # ❤️ Positivité / Insolite
    {"url": "https://positivr.fr/feed/",                               "source": "Positivr"},
    # 🎬 Pop culture / Réseaux sociaux / Créateurs / Buzz
    {"url": "https://www.konbini.com/fr/feed/",                        "source": "Konbini"},
    {"url": "https://www.numerama.com/pop-culture/feed/",              "source": "Numerama Pop"},
    {"url": "https://www.melty.fr/feed",                               "source": "Melty"},
    {"url": "https://www.programme-tv.net/rss/actualites.xml",         "source": "Programme TV"},
    {"url": "https://www.premiere.fr/rss/actualite",                   "source": "Première"},
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
    "economie": "📈", "societe": "👥", "faitsdivers": "🚓", "histoire": "📜",
    "culture": "🎭",  "sport": "🏆", "science": "🔬",
    "sante":    "🏥", "environnement": "🌱",
    "tech":     "💻", "ia": "🤖", "insolite": "😲", "positivity": "❤️",
}

LABELS = {
    "breaking": "URGENT", "france": "FRANCE", "monde": "MONDE",
    "politique": "POLITIQUE", "economie": "ECO",
    "societe": "SOCIÉTÉ", "faitsdivers": "FAITS DIVERS",
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

def should_publish_now(conn, min_minutes=60, max_minutes=180):
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
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

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

Barème score :
- 9-10 : breaking news majeure
- 7-8  : info importante (politique, économie, sport, gros buzz réseaux sociaux/pop culture)
- 6    : info intéressante du quotidien (insolite, fait divers marquant, info locale forte, lancement de produit d'une célébrité)
- 0-5  : trop banal pour un compte d'actu

⚖️ ÉQUILIBRE ÉDITORIAL IMPORTANT :
- NE FAVORISE PAS systématiquement la politique et les mêmes sujets (Trump, Iran, Macron, Chine...).
- VALORISE autant la POP CULTURE et les RÉSEAUX SOCIAUX : créateurs de contenu (Squeezie, McFly & Carlito, Inoxtag, Lena Situations...), lancements de marques/produits par des célébrités, buzz viraux, cinéma, séries, musique, télé-réalité, sorties culturelles.
- Ces sujets "légers" intéressent ÉNORMÉMENT le grand public et méritent des scores élevés (7-8) quand c'est un gros événement (ex: Squeezie lance une boisson, McFly & Carlito sortent des chips = score 7-8).
- Un bon mix = politique + société + pop culture + sport + insolite, PAS que de la politique.
- Une info locale marquante peut scorer aussi haut qu'une info internationale.

Catégories :
- "breaking" : breaking news urgente, à diffuser vite
- "france"   : actu nationale française (général)
- "monde"    : actu internationale
- "politique" : politique française ou internationale
- "economie" : économie, entreprises, marchés
- "societe"  : société, social, vie quotidienne
- "faitsdivers" : faits divers, justice
- "culture"  : cinéma, musique, art, littérature, séries, célébrités, créateurs de contenu, buzz réseaux sociaux, lancements de produits par des personnalités
- "sport"    : sport
- "science"  : science, recherche
- "sante"    : santé publique, médecine
- "environnement" : climat, écologie
- "tech"     : technologie, gadgets
- "ia"       : intelligence artificielle
- "insolite" : insolite, faits étonnants
- "positivity" : feel-good, actes de bonté, histoires positives
- "histoire" : événement historique vérifiable lié à la date du jour

IMPORTANT : retourne EXACTEMENT {len(articles)} analyses dans le tableau."""

    result   = claude(prompt, max_tokens=max(500, len(articles) * 80))
    analyses = result.get("analyses", [])
    while len(analyses) < len(articles):
        analyses.append({"score": 0, "category": "france", "is_duplicate": False, "needs_video": False})
    return analyses[:len(articles)]

def gen_tweet_complet(title, summary, source, category, video_url=None):
    """Génère tweet + titre image + image_query + mots-clés majeurs."""
    today = datetime.now().strftime("%d %B %Y")
    label = LABELS[category]
    video_str = f"\nIntègre ce lien à la fin du tweet : {video_url}" if video_url else ""

    # Style adaptatif selon catégorie
    if category in ("breaking", "faitsdivers"):
        style_instr = """STYLE BREAKING/URGENT :
- Phrase 1 : très courte, percutante, factuelle (qui, quoi, où)
- Pas d'analyse, juste les faits bruts
- Ton journaliste de terrain BFM"""
    elif category == "positivity":
        style_instr = """STYLE NARRATIF/STORYTELLING :
- Phrase 1 : accroche émotionnelle qui donne envie de lire
- Paragraphes développés avec noms, dates, citations
- Ton chaleureux et humain"""
    else:
        style_instr = """STYLE INFO POSÉE :
- Phrase 1 : accroche claire avec l'info principale
- 1-2 phrases courtes de contexte/conséquences (sans répéter)
- Ton informatif et professionnel"""

    result = claude(f"""Tu es community manager de Pulse, compte Twitter d'actualité française.
Aujourd'hui : {today}.

Article à traiter :
- Source : {source}
- Titre  : {title}
- Résumé : {summary}{video_str}

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

RÈGLES STRICTES pour body :
- NE COMMENCE PAS par "{label}" ni aucune catégorie en majuscules
- Va directement à l'info
- FRANÇAIS obligatoire
- Compte Premium = 600 caractères max
- Info COMPLÈTE, jamais teaser
- 2-3 hashtags RÉPARTIS dans le texte sur les mots les plus recherchés (#Macron, #Paris, #PSG, pas #news)
- Hashtags intégrés naturellement : "la #France" pas "France #France"
- Termine par la source entre parenthèses : ({source})

⚠️ MISE EN FORME OBLIGATOIRE — AÉRER LE TEXTE :
- Sépare le tweet en 2 ou 3 paragraphes courts
- METS UN DOUBLE SAUT DE LIGNE (\\n\\n) entre chaque paragraphe
- La source à la fin doit être précédée d'un double saut de ligne, seule sur sa ligne
- Exemple de structure EXACTE attendue (avec les \\n\\n) :
  "Phrase d'accroche qui pose l'info principale.\\n\\nDeuxième paragraphe avec le contexte et les détails.\\n\\nTroisième paragraphe avec la conséquence ou la question soulevée.\\n\\n(Source)"
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
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(200000).decode("utf-8", errors="ignore")
        # Cherche og:image puis twitter:image
        for prop in ('property=["\']og:image(?::url)?["\']', 'name=["\']twitter:image["\']'):
            m = re.search(r'<meta[^>]+' + prop + r'[^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+' + prop, html, re.IGNORECASE)
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

def get_best_image(article_url, photo_url, person, image_query, category):
    """
    Choisit la MEILLEURE image dispo, par ordre de priorité :
    1. og:image de l'article (HD, en rapport avec le sujet)
    2. photo de la personnalité (Wikipedia) si l'article parle de quelqu'un
    3. miniature RSS si assez grande
    4. Unsplash (recherche par mots-clés)
    5. Unsplash fallback catégorie
    Retourne (raw_bytes, is_real_photo).
    """
    # 1. og:image de l'article
    og = fetch_og_image(article_url)
    if og:
        raw = fetch_img(og)
        if raw and img_dimensions_ok(raw):
            return raw, True

    # 2. Photo de la personnalité
    if person:
        portrait = fetch_wikipedia_portrait(person)
        if portrait:
            raw = fetch_img(portrait)
            if raw and img_dimensions_ok(raw):
                print(f"  👤 Photo de {person} (Wikipedia)")
                return raw, True

    # 3. Miniature RSS si assez grande
    if photo_url:
        raw = fetch_img(photo_url)
        if raw and img_dimensions_ok(raw):
            return raw, True

    # 4. Unsplash recherche
    if image_query:
        u = search_unsplash(image_query, category)
        raw = fetch_img(u)
        if raw and img_dimensions_ok(raw, min_w=800, min_h=400):
            return raw, False

    # 5. Fallback catégorie
    raw = fetch_img(UNSPLASH_FALLBACK.get(category))
    return (raw, False) if raw else (None, False)

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

def extract_video(entry):
    """Cherche une vidéo dans l'article RSS (MP4, HLS, WebM...)."""
    VIDEO_EXTS  = (".mp4", ".mov", ".webm", ".m3u8", ".avi", ".mkv")
    VIDEO_TYPES = ("video/", "application/x-mpegurl", "application/vnd.apple.mpegurl")
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            t, url = m.get("type", "").lower(), m.get("url", "")
            if any(t.startswith(vt) for vt in VIDEO_TYPES): return url
            if any(url.lower().endswith(ext) for ext in VIDEO_EXTS): return url
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            t   = e.get("type", "").lower()
            url = e.get("href", "") or e.get("url", "")
            if any(t.startswith(vt) for vt in VIDEO_TYPES): return url
            if any(url.lower().endswith(ext) for ext in VIDEO_EXTS): return url
    content = ""
    if hasattr(entry, "content") and entry.content:
        content = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        content = entry.summary or ""
    import re as _re
    matches = _re.findall(r'https?://[^\s"\'<>]+\.(?:mp4|m3u8|mov|webm)', content, _re.IGNORECASE)
    return matches[0] if matches else None

def extract_video_from_page(article_url):
    """Cherche une vidéo (og:video) sur la page de l'article."""
    if not article_url:
        return None
    try:
        req = urllib.request.Request(article_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(300000).decode("utf-8", errors="ignore")
        # og:video:url ou og:video
        for prop in ('property=["\']og:video(?::url|:secure_url)?["\']',):
            m = re.search(r'<meta[^>]+' + prop + r'[^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if not m:
                m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+' + prop, html, re.IGNORECASE)
            if m:
                v = m.group(1).strip()
                # On ignore les embeds YouTube/Dailymotion (copyright + pas téléchargeables simplement)
                if any(x in v.lower() for x in ("youtube.com", "youtu.be", "dailymotion", "/embed/")):
                    return None
                if v.startswith("//"):
                    v = "https:" + v
                if v.startswith("http") and any(v.lower().split("?")[0].endswith(e) for e in (".mp4", ".m3u8", ".mov", ".webm")):
                    return v
    except Exception as e:
        print(f"  ⚠️ og:video: {e}")
    return None

def download_and_convert_video(video_url, max_duration=90):
    """Télécharge et convertit une vidéo en MP4 720p compatible X via FFmpeg."""
    import subprocess, tempfile, os
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  ⚠️ FFmpeg non disponible.")
        return None
    try:
        out_path = tempfile.mktemp(suffix=".mp4")
        cmd = [
            "ffmpeg", "-y", "-i", video_url,
            "-t", str(max_duration),
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-max_muxing_queue_size", "1024", out_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            print(f"  ⚠️ FFmpeg erreur: {result.stderr.decode()[-200:]}")
            return None
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        if size_mb > 512:
            print(f"  ⚠️ Vidéo trop lourde ({size_mb:.0f} MB).")
            os.remove(out_path); return None
        print(f"  🎬 Vidéo convertie ({size_mb:.1f} MB)")
        return out_path
    except subprocess.TimeoutExpired:
        print("  ⚠️ FFmpeg timeout."); return None
    except Exception as e:
        print(f"  ⚠️ Vidéo erreur: {e}"); return None

def build_png(headline_court, source, category, photo_url=None, image_query=None, article_url=None, person=None, W=1200, H=675, prefetched=None, headline_bottom=False):
    """
    PNG DA Pulse, taille paramétrable (W×H).
    - Paysage 1200×675 pour X/Facebook (défaut)
    - Portrait 1080×1350 (4:5) pour Instagram
    prefetched = (raw_bytes, has_real_photo) pour réutiliser une image déjà téléchargée.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
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
                left  = (new_w - W) // 2
                top   = (new_h - H) // 2
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
            # Dégradé sombre renforcé en bas pour lisibilité du titre
            grad = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            band = int(H * 0.42)
            for i in range(band):
                y = H - band + i
                a = int(225 * (i / band) ** 1.3)
                gd.line([(0, y), (W, y)], fill=(8, 6, 20, a))
            img = Image.alpha_composite(img.convert('RGBA'), grad).convert('RGB')
            draw = ImageDraw.Draw(img)

            # Titre wrappé, posé juste au-dessus de la source
            max_w = int(W * 0.90)
            sizes = [int(W * x) for x in (0.058, 0.052, 0.046, 0.040, 0.035)]
            chosen_lines, chosen_size = None, sizes[-1]
            for fsize in sizes:
                ft = font(fsize)
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
                chosen_lines = [headline_court[:60] + "..."]
                chosen_size = sizes[-1]

            ft = font(chosen_size)
            line_h = chosen_size + 12
            total_h = len(chosen_lines) * line_h
            ty = int(H - H * 0.10) - total_h
            for ln in chosen_lines:
                # ombre + texte pour lisibilité
                draw.text((margin + 2, ty + 2), ln, font=ft, fill=(0, 0, 0))
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
    # Retire les hashtags déjà présents pour les regrouper proprement en bas
    existing = _re.findall(r'#\w+', text)
    text = _re.sub(r'#\w+', '', text).strip()
    text = _re.sub(r'[ \t]+\n', '\n', text)
    text = _re.sub(r'\n{3,}', '\n\n', text).strip()

    # Construit une liste de hashtags : mots-clés + existants + standards
    tags = []
    def add_tag(t):
        t = _re.sub(r'[^0-9A-Za-zÀ-ÿ]', '', t)
        if t and len(t) > 2:
            h = "#" + t[0].upper() + t[1:]
            if h.lower() not in [x.lower() for x in tags]:
                tags.append(h)
    for kw in (keywords or []):
        add_tag(kw)
    for h in existing:
        add_tag(h[1:])
    for std in ["Actualité", "Info", "France", "News", "Pulse"]:
        add_tag(std)
    hashtags = " ".join(tags[:12])

    cta = "👉 Plus d'infos sur X : @PULSEactus"
    return f"{text}\n\n{cta}\n\n{hashtags}"

def post_to_instagram(caption, png_bytes=None, video_path=None):
    """
    Poste sur Instagram via l'API Graph (image 4:5 uniquement pour l'instant).
    Process en 2 étapes : créer un conteneur média (avec URL image) puis publier.
    """
    if not (INSTAGRAM_ACCOUNT_ID and FACEBOOK_PAGE_TOKEN):
        return None
    if not png_bytes:
        return None  # Instagram exige une image/vidéo, pas de post texte seul

    # 1) Héberger l'image (Instagram exige une URL publique)
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
                summ  = entry.get("summary", entry.get("description", ""))
                if title:
                    summ = re.sub(r"<[^>]+>", "", summ)  # nettoie le HTML
                    headlines.append(f"[{fi['source']}] {title} — {summ[:200]}")
        except: pass
    return headlines

def gen_thread(conn):
    """Génère un thread explicatif sur le sujet majeur du jour, basé UNIQUEMENT sur les articles RSS."""
    if special_done_today(conn, "thread"):
        return None
    if datetime.now().hour < 9:   # pas de thread avant 9h
        return None

    headlines = gather_all_headlines()
    if len(headlines) < 10:
        print("  ⚠️ Pas assez d'articles pour un thread.")
        return None

    avoid = recent_special_topics(conn, "thread", days=7)
    avoid_str = " ; ".join(avoid) if avoid else "Aucun"

    headlines_str = "\n".join(headlines[:50])
    today = datetime.now().strftime("%d %B %Y")

    try:
        result = claude(f"""Tu es journaliste pour Pulse, compte Twitter d'actualité française. Aujourd'hui : {today}.

Voici les titres et résumés des articles d'actualité du jour :

{headlines_str}

SUJETS DÉJÀ TRAITÉS CES 7 DERNIERS JOURS (à ÉVITER) :
{avoid_str}

Ta mission : identifier LE sujet majeur du jour (différent de ceux déjà traités) et écrire un DÉCRYPTAGE complet en UN SEUL tweet long (compte Premium).

RÈGLES ABSOLUES :
- Base-toi UNIQUEMENT sur les informations présentes dans les articles ci-dessus.
- N'INVENTE AUCUN fait, chiffre, date ou citation qui ne serait pas dans les articles.
- Si tu n'es pas sûr d'un détail, reste général plutôt que d'inventer.
- FRANÇAIS, ton clair et pédagogique.

Format du tweet (700 à 1000 caractères) :
- Ligne 1 : accroche forte qui pose le sujet, suivie de "— Le décryptage 🧵"
- DOUBLE SAUT DE LIGNE
- 2-3 paragraphes courts séparés par des doubles sauts de ligne : contexte, enjeux, ce qu'il faut comprendre
- DOUBLE SAUT DE LIGNE
- Une phrase de conclusion / ce qu'il faut retenir
- 2-3 hashtags répartis dans le texte
- Les sauts de ligne s'écrivent \\n dans le JSON

Réponds avec ce JSON UNIQUEMENT :
{{"sujet":"<2-4 mots>","keywords":["mot1","mot2","mot3"],"image_query":"<5 mots anglais>","body":"Accroche — Le décryptage 🧵\\n\\nParagraphe 1...\\n\\nParagraphe 2...\\n\\nÀ retenir : ..."}}""", max_tokens=1000)

        body = result.get("body", "").strip()
        if not body or len(body) < 100:
            print("  ⚠️ Thread invalide.")
            return None

        # Nettoyage préfixe éventuel
        for lbl in LABELS.values():
            body = re.sub(rf"^{lbl}\s*\|\s*", "", body, flags=re.IGNORECASE)

        return {
            "body":        body,
            "keywords":    result.get("keywords", []),
            "sujet":       result.get("sujet", "actu"),
            "image_query": result.get("image_query", "world news"),
        }
    except Exception as e:
        print(f"  ⚠️ Thread échoué : {e}")
        return None

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
4. Options TRÈS courtes et tranchées (max 25 caractères) : souvent "Oui / Non", "Pour / Contre", "D'accord / Pas d'accord", ou des choix concrets (noms d'équipes, de personnalités connues...).

EXEMPLES DE BONS SONDAGES (style à imiter) :
- "Faut-il interdire les écrans aux moins de 3 ans ? 📱" → Oui / Non
- "Le PSG va-t-il gagner la Ligue des Champions ? ⚽" → Oui / Non / J'y crois moyen
- "Retraite à 64 ans : toujours contre ? 🏛️" → Pour / Contre / Ça dépend
- "Couvre-feu pour les mineurs : bonne idée ? 🌙" → Oui / Non

EXEMPLES DE MAUVAIS SONDAGES (à NE JAMAIS faire) :
- "Edgar Morin incarnait-il l'humanisme républicain ?" (personne ne connaît, trop intello)
- Toute question dont la réponse est factuelle plutôt qu'une opinion

CONTRAINTES TECHNIQUES :
- Question max 200 caractères, avec 1 emoji pertinent (et éventuellement 1 hashtag connu)
- 2 à 4 options, chacune max 25 caractères
- Base-toi sur un vrai sujet présent dans les articles ci-dessus
- FRANÇAIS

Réponds avec ce JSON UNIQUEMENT :
{{"keywords":["mot1","mot2"],"question":"...","options":["Option 1","Option 2"]}}""", max_tokens=400)

        question = result.get("question", "").strip()
        options  = [o.strip()[:25] for o in result.get("options", []) if o.strip()]
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
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════
def check_feeds(conn):
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🔍 Check Pulse...")

    # ── DÉCRYPTAGE QUOTIDIEN (matin, 1×/jour, prioritaire) ──
    if not special_done_today(conn, "thread") and datetime.now().hour >= 9:
        thread = gen_thread(conn)
        if thread:
            raw_src, has_real = get_best_image(None, None, None, thread["image_query"], "monde")
            png_bytes, _ = build_png(thread["sujet"][:75], "Pulse", "monde", None, thread["image_query"], prefetched=(raw_src, has_real))
            url = post_to_twitter(thread["body"], png_bytes)
            post_to_facebook(thread["body"], png_bytes)
            png_ig, _ = build_png(thread["sujet"][:75], "Pulse", "monde", None, thread["image_query"], W=1080, H=1350, prefetched=(raw_src, has_real), headline_bottom=True)
            post_to_instagram(build_ig_caption(thread["body"], thread.get("keywords")), png_ig)
            if url:
                log_special(conn, "thread", thread["keywords"])
                print(f"  🧵 Décryptage du jour publié [{thread['sujet']}]")
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

    # ── PUBLICATION NORMALE (rythme aléatoire) ──
    if not should_publish_now(conn):
        return

    print(f"  → Scan RSS...")
    blocked_kws = recent_keywords(conn, hours=12)
    candidates  = []
    pre_filtered = 0
    for fi in RSS_FEEDS:
        try:
            feed = feedparser.parse(fi["url"])
            for entry in feed.entries[:2]:
                url   = entry.get("link", "")
                title = entry.get("title", "")
                summ  = entry.get("summary", entry.get("description", ""))
                if url and title and not is_seen(conn, url):
                    # Pré-filtre GRATUIT : si le titre contient un mot-clé déjà publié (12h),
                    # on rejette SANS payer Claude
                    title_low = title.lower()
                    if blocked_kws and any(kw in title_low for kw in blocked_kws):
                        mark_seen(conn, url, title)
                        pre_filtered += 1
                        continue
                    candidates.append({"url": url, "title": title, "summary": summ, "source": fi["source"], "entry": entry})
        except Exception as e:
            print(f"  ❌ RSS {fi['source']}: {e}")

    if pre_filtered:
        print(f"  🚫 {pre_filtered} articles pré-filtrés (mots-clés bloqués, sans coût Claude)")

    if not candidates:
        print("  → Aucun article nouveau.")
        return

    print(f"  → {len(candidates)} articles à analyser...")
    recent = get_recent(conn)
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

    # Boost catégorie pas encore vue aujourd'hui
    missing = set(STYLES.keys()) - cats_today(conn)
    for item in scored:
        if item["analysis"]["category"] in missing:
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
                body, headline_court, image_query, keywords, person = gen_tweet_complet(
                    item["title"], item["summary"], item["source"], cat
                )
                tweet_final = build_full_tweet(body, cat)
                photo       = extract_photo(item["entry"])

            # Cherche une vidéo dans l'article RSS, puis sur la page de l'article
            video_path = None
            if "entry" in item:
                video_url = extract_video(item["entry"])
                if not video_url:
                    video_url = extract_video_from_page(item.get("url"))
                if video_url:
                    print(f"  🎬 Vidéo trouvée : {video_url[:60]}...")
                    video_path = download_and_convert_video(video_url)

            # Image paysage (X + Facebook) — on récupère aussi l'image source pour réutilisation
            raw_src, has_real = get_best_image(item.get("url"), photo, person, image_query, cat)
            png_bytes, png_nm = build_png(
                headline_court, item["source"], cat, photo, image_query,
                article_url=item.get("url"), person=person,
                prefetched=(raw_src, has_real)
            )
            post_to_twitter(tweet_final, png_bytes, video_path)
            post_to_facebook(tweet_final, png_bytes, video_path)

            # Image portrait 4:5 (Instagram) — titre écrit sur l'image (style Insta)
            png_ig, _ = build_png(
                headline_court, item["source"], cat, photo, image_query,
                article_url=item.get("url"), person=person,
                W=1080, H=1350, prefetched=(raw_src, has_real), headline_bottom=True
            )
            post_to_instagram(build_ig_caption(tweet_final, keywords), png_ig)

            # Nettoyage du fichier vidéo temporaire (après X + Facebook)
            if video_path:
                try:
                    import os as _os
                    if _os.path.exists(video_path):
                        _os.remove(video_path)
                except: pass

            mark_cat(conn, cat)
            log_keywords(conn, keywords)
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
