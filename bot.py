"""
Pulse NewsBot — bot d'actu internationale en anglais.
Génère des tweets engageants avec image PNG, envoyés par email.
"""
import feedparser, anthropic, sqlite3, hashlib, json, time, os, smtplib, random
import urllib.request, urllib.parse, re
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
UNSPLASH_KEY      = os.environ.get("UNSPLASH_KEY",      "")  # optionnel, pour images contextuelles

SCORE_MINIMUM = 7  # plus exigeant : seules les vraies grosses actus
MAX_PAR_PASSE = 2

# ═══════════════════════════════════════════════════════════════════════════
# SOURCES RSS — international + grandes figures
# ═══════════════════════════════════════════════════════════════════════════
RSS_FEEDS = [
    # 🌍 International majeur
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",            "source": "BBC World"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "source": "NY Times"},
    {"url": "https://www.theguardian.com/world/rss",                  "source": "The Guardian"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",            "source": "Reuters"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",              "source": "Al Jazeera"},
    {"url": "https://feeds.npr.org/1004/rss.xml",                     "source": "NPR World"},
    # 🇺🇸 US (forte portée mondiale)
    {"url": "https://feeds.washingtonpost.com/rss/world",             "source": "Washington Post"},
    {"url": "https://www.politico.com/rss/politicopicks.xml",         "source": "Politico"},
    # 📈 Business / Tech mondial
    {"url": "https://feeds.bloomberg.com/markets/news.rss",           "source": "Bloomberg"},
    {"url": "https://www.ft.com/world?format=rss",                    "source": "Financial Times"},
    {"url": "https://techcrunch.com/feed/",                           "source": "TechCrunch"},
    {"url": "https://www.theverge.com/rss/index.xml",                 "source": "The Verge"},
    # 🔬 Science / Santé
    {"url": "https://www.nature.com/nature.rss",                      "source": "Nature"},
    {"url": "https://www.sciencedaily.com/rss/top/science.xml",       "source": "Science Daily"},
    {"url": "https://www.who.int/rss-feeds/news-english.xml",         "source": "WHO"},
    # 🏆 Sport international
    {"url": "https://www.espn.com/espn/rss/news",                     "source": "ESPN"},
    {"url": "https://www.skysports.com/rss/12040",                    "source": "Sky Sports"},
    # 🌱 Environnement
    {"url": "https://www.theguardian.com/environment/rss",            "source": "Guardian Environment"},
]

# ═══════════════════════════════════════════════════════════════════════════
# DA PULSE — styles, préfixes, labels (EN ANGLAIS maintenant)
# ═══════════════════════════════════════════════════════════════════════════
STYLES = {
    "breaking":      {"color": "#ff6868", "label": "Breaking",      "bar": [(255,32,32),(255,96,48)],     "overlay": (18,3,3)},
    "world":         {"color": "#64b5f6", "label": "World",         "bar": [(33,150,243),(0,184,212)],    "overlay": (3,10,22)},
    "politics":      {"color": "#ffd54f", "label": "Politics",      "bar": [(255,193,7),(255,152,0)],     "overlay": (12,10,2)},
    "business":      {"color": "#69f0ae", "label": "Business",      "bar": [(0,230,118),(0,191,165)],     "overlay": (2,12,5)},
    "society":       {"color": "#ce93d8", "label": "Society",       "bar": [(206,147,216),(156,39,176)],  "overlay": (10,4,20)},
    "history":       {"color": "#d4a843", "label": "History",       "bar": [(212,168,67),(160,113,74)],   "overlay": (14,8,2)},
    "culture":       {"color": "#00e5ff", "label": "Culture",       "bar": [(0,229,255),(29,233,182)],    "overlay": (2,12,14)},
    "sport":         {"color": "#82b1ff", "label": "Sport",         "bar": [(68,138,255),(48,79,254)],    "overlay": (2,6,14)},
    "science":       {"color": "#b388ff", "label": "Science",       "bar": [(124,77,255),(101,31,255)],   "overlay": (2,6,16)},
    "health":        {"color": "#ff8a80", "label": "Health",        "bar": [(255,138,128),(244,67,54)],   "overlay": (16,4,4)},
    "environment":   {"color": "#80e27e", "label": "Environment",   "bar": [(128,226,126),(76,175,80)],   "overlay": (4,14,4)},
}

EMOJIS = {
    "breaking": "🚨", "world": "🌍", "politics": "🏛️",
    "business": "📈", "society": "👥", "history": "📜",
    "culture": "🎭",  "sport": "🏆", "science": "🔬",
    "health":   "🏥", "environment": "🌱",
}

LABELS = {
    "breaking": "BREAKING", "world": "WORLD",
    "politics": "POLITICS", "business": "BUSINESS",
    "society": "SOCIETY", "history": "HISTORY",
    "culture": "CULTURE", "sport": "SPORT",
    "science": "SCIENCE", "health": "HEALTH",
    "environment": "ENVIRONMENT",
}

# Images Unsplash de fallback par catégorie
UNSPLASH_FALLBACK = {
    "breaking":    "https://images.unsplash.com/photo-1504711434969-e33886168f5c?w=1200&q=70",
    "world":       "https://images.unsplash.com/photo-1526778548025-fa2f459cd5c1?w=1200&q=70",
    "politics":    "https://images.unsplash.com/photo-1541872703-74c5e44368f9?w=1200&q=70",
    "business":    "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1200&q=70",
    "society":     "https://images.unsplash.com/photo-1529156069898-49953e39b3ac?w=1200&q=70",
    "history":     "https://images.unsplash.com/photo-1461360370896-922624d12aa1?w=1200&q=70",
    "culture":     "https://images.unsplash.com/photo-1499364615650-ec38552f4f34?w=1200&q=70",
    "sport":       "https://images.unsplash.com/photo-1461896836934-ffe607ba8211?w=1200&q=70",
    "science":     "https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1200&q=70",
    "health":      "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=1200&q=70",
    "environment": "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1200&q=70",
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
        # Cache des analyses pour éviter de re-payer Claude sur les mêmes articles
        # qui ont été analysés mais pas publiés (ex: rejetés par sélection top-2)
        """CREATE TABLE IF NOT EXISTS analyzed_cache (
            hash TEXT PRIMARY KEY,
            score INTEGER,
            category TEXT,
            is_duplicate INTEGER,
            needs_video INTEGER,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]:
        conn.execute(sql)
    # Nettoyage : on supprime les analyses de plus de 24h
    conn.execute("DELETE FROM analyzed_cache WHERE analyzed_at < datetime('now', '-24 hours')")
    conn.commit()
    return conn

def get_cached_analysis(conn, url):
    h   = hashlib.md5(url.encode()).hexdigest()
    row = conn.execute(
        "SELECT score, category, is_duplicate, needs_video FROM analyzed_cache WHERE hash=?",
        (h,)
    ).fetchone()
    if row:
        return {
            "score": row[0], "category": row[1],
            "is_duplicate": bool(row[2]), "needs_video": bool(row[3]),
        }
    return None

def cache_analysis(conn, url, analysis):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute(
        """INSERT OR REPLACE INTO analyzed_cache (hash, score, category, is_duplicate, needs_video)
           VALUES (?, ?, ?, ?, ?)""",
        (h, int(analysis.get("score", 0)), analysis.get("category", ""),
         1 if analysis.get("is_duplicate") else 0,
         1 if analysis.get("needs_video") else 0)
    )
    conn.commit()

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

def last_publish_time(conn):
    """Retourne la datetime de la dernière publication, ou None."""
    row = conn.execute("SELECT MAX(last_sent) FROM category_log WHERE last_sent != '2000-01-01'").fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    except:
        return None

def should_publish_now(conn, min_minutes=90, max_minutes=150):
    """
    Décide si on doit publier maintenant.
    Cible un intervalle aléatoire entre min_minutes et max_minutes depuis la dernière publication.
    Logique probabiliste :
    - < min_minutes : on attend toujours
    - > max_minutes : on publie toujours
    - entre les deux : probabilité croissante linéaire
    """
    last = last_publish_time(conn)
    if not last:
        return True  # Première publication

    elapsed = (datetime.now() - last).total_seconds() / 60

    if elapsed < min_minutes:
        wait = int(min_minutes - elapsed)
        print(f"  ⏸️  Dernière publi il y a {int(elapsed)} min — attente min {wait} min.")
        return False
    if elapsed > max_minutes:
        print(f"  ✅ Dernière publi il y a {int(elapsed)} min — au-delà du max, on publie.")
        return True

    # Probabilité croissante : 0% à min_minutes, 100% à max_minutes
    proba = (elapsed - min_minutes) / (max_minutes - min_minutes)
    publish = random.random() < proba
    if publish:
        print(f"  🎲 {int(elapsed)} min depuis dernière publi (proba {int(proba*100)}%) → on publie.")
    else:
        print(f"  🎲 {int(elapsed)} min depuis dernière publi (proba {int(proba*100)}%) → on attend.")
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

def analyse_batch(articles, recent):
    """
    Analyse PLUSIEURS articles en un seul appel Claude (optimisation coût × ~3).
    `articles` : liste de dicts {title, summary, source}.
    Retourne une liste d'analyses dans le même ordre.
    """
    if not articles:
        return []

    recent_str = "\n".join(f"- {t}" for t in recent[:20]) or "None"
    today      = datetime.now().strftime("%d %B %Y")
    cats       = "|".join(LABELS.keys())

    # On numérote chaque article pour pouvoir corréler avec la réponse
    articles_str = "\n\n".join(
        f"### Article {i+1}\nSource: {a['source']}\nTitle: {a['title']}\nSummary: {a.get('summary', '')[:300]}"
        for i, a in enumerate(articles)
    )

    prompt = f"""You are the editor of Pulse, an English-language news Twitter account focused on GLOBAL impact.
Today: {today}

Below are {len(articles)} candidate articles to analyze. For EACH one, return a JSON entry.

Recent published titles (avoid duplicates):
{recent_str}

Articles to analyze:

{articles_str}

Return ONLY this JSON array (one entry per article, in the SAME ORDER):
{{"analyses":[
  {{"id":1,"score":<0-10>,"category":"<{cats}>","is_duplicate":<true|false>,"needs_video":<true|false>}},
  {{"id":2,"score":<0-10>,"category":"<{cats}>","is_duplicate":<true|false>,"needs_video":<true|false>}},
  ...
]}}

Scoring criteria (be STRICT):
- 9-10: major breaking news affecting millions worldwide
- 7-8:  important international story (known figure, big company, world event)
- 5-6:  interesting but limited reach
- 0-4:  local news, fluff, PR, not for international audience

Focus on:
- Globally known politicians (Trump, Macron, Putin, Xi, etc.)
- Major companies (Apple, Tesla, OpenAI, etc.)
- International conflicts, wars, geopolitics
- Global health, environment, science breakthroughs
- World-famous athletes/celebrities doing something newsworthy

is_duplicate=true ONLY if a recent title covers the EXACT same event.
"history" ONLY for verifiable historical facts on today's date.

IMPORTANT: return EXACTLY {len(articles)} entries in the analyses array, in the same order as the articles above."""

    # Le batch peut nécessiter pas mal de tokens en sortie (50 par article ≈)
    result   = claude(prompt, max_tokens=max(500, len(articles) * 80))
    analyses = result.get("analyses", [])

    # Sécurité : si le retour est incomplet, on complète avec des scores nuls
    while len(analyses) < len(articles):
        analyses.append({"score": 0, "category": "world", "is_duplicate": False, "needs_video": False})

    return analyses[:len(articles)]

def gen_tweet_complet(title, summary, source, category, video_url=None):
    """
    Génère le corps du tweet en anglais + un titre court pour l'image
    + une description précise pour chercher l'image contextuelle.
    """
    today = datetime.now().strftime("%d %B %Y")
    label = LABELS[category]
    video_str = f"\nInclude this video link at the end: {video_url}" if video_url else ""

    result = claude(f"""You are the community manager of Pulse, an English-language news Twitter account.
Today: {today}.

Article to cover:
- Source: {source}
- Title:  {title}
- Summary: {summary}{video_str}

Generate THREE things:

1. **headline_court** (max 75 chars): A punchy, attention-grabbing headline for the image background.
   No hashtags, no emoji. Must fit in full without truncation.

2. **image_query** (max 5 words): A specific search query to find a relevant image.
   Example: "Donald Trump speech podium" or "Iran flag protest Tehran" or "Tesla factory production line"

3. **body**: The tweet body (without prefix — it will be added automatically).

STRICT RULES for body:
- DO NOT start with "{label}" or any category in caps
- Go straight to the info — no intro
- ENGLISH only
- Structure (Premium account = 600 chars max):
   • Sentence 1 (the HOOK): SHORT and PUNCHY with the key info. Must make the reader want to keep reading.
   • Line break
   • 1-2 short sentences with details (NO repetition of sentence 1)
   • Line break
   • Source in parentheses at the end: ({source})
- GIVE THE FULL INFO — never tease without delivering
- Max 600 characters total

HASHTAG RULES (very important):
- Use 2-3 hashtags, SPREAD throughout the text (not all clustered at the start or end)
- Put hashtags on the MOST SEARCHED keywords for this topic:
   • Famous person's name → #Trump, #Macron, #Mbappe, #Musk
   • Country → #USA, #China, #Iran, #France
   • Major company → #Tesla, #Apple, #OpenAI
   • Major event/concept → #ClimateChange, #AI, #Election
- Replace a word naturally: write "the #US economy" not "US economy #US"
- Don't hashtag generic words like #news, #today, #breaking

GOOD EXAMPLE:
Body: "#Trump just slapped 50% tariffs on all imports from #China, escalating the trade war.

The move targets electronics and consumer goods, with US store prices expected to surge within weeks. Beijing has promised swift retaliation.

(Bloomberg)"

Return ONLY this JSON:
{{"headline_court":"...","image_query":"...","body":"..."}}""", max_tokens=900)

    body = result.get("body", "").strip()
    # Nettoyage : enlever tout début "LABEL |" si Claude en a mis un
    for label_test in LABELS.values():
        body = re.sub(rf"^{label_test}\s*\|\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(rf"^{label_test}\s*[—-]\s*", "", body, flags=re.IGNORECASE)
    body = re.sub(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s]+", "", body).strip()

    headline_court = result.get("headline_court", title)[:80].strip()
    image_query    = result.get("image_query", category).strip()

    return body, headline_court, image_query

def build_full_tweet(body, category):
    emoji = EMOJIS[category]
    label = LABELS[category]
    return f"{emoji} {label} | {body}"

# ═══════════════════════════════════════════════════════════════════════════
# RECHERCHE IMAGE CONTEXTUELLE
# ═══════════════════════════════════════════════════════════════════════════
def search_unsplash(query, category):
    """
    Cherche une image pertinente via Unsplash (gratuit, sans clé pour les images aléatoires).
    Si pas de match, fallback sur l'image par catégorie.
    """
    if UNSPLASH_KEY:
        try:
            url = f"https://api.unsplash.com/search/photos?query={urllib.parse.quote(query)}&per_page=1&client_id={UNSPLASH_KEY}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            if data.get("results"):
                return data["results"][0]["urls"]["regular"]
        except Exception as e:
            print(f"  ⚠️ Unsplash search: {e}")

    # Fallback : utilise l'URL Unsplash source.unsplash.com (sans clé, gratuit)
    try:
        return f"https://source.unsplash.com/1200x675/?{urllib.parse.quote(query)}"
    except:
        return UNSPLASH_FALLBACK.get(category)

# ═══════════════════════════════════════════════════════════════════════════
# YOUTUBE
# ═══════════════════════════════════════════════════════════════════════════
def find_video(title, summary):
    if not YOUTUBE_API_KEY:
        return None
    try:
        q   = urllib.parse.quote(" ".join(title.split()[:7]))
        url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={q}&type=video&maxResults=5&relevanceLanguage=en&key={YOUTUBE_API_KEY}"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
        videos = [{"title": i["snippet"]["title"], "channel": i["snippet"]["channelTitle"],
                   "url": f"https://youtu.be/{i['id']['videoId']}"}
                  for i in data.get("items", []) if i["id"].get("videoId")]
        if not videos:
            return None
        result = claude(f"""Article: {title}
Summary: {summary}

Candidate videos:
{chr(10).join(f"{i+1}. [{v['channel']}] {v['title']}" for i,v in enumerate(videos))}

JSON: {{"chosen":<1-{len(videos)} or 0>,"reason":"<1 sentence>"}}
Pick only if DIRECTLY related to same event, reliable source.""", max_tokens=100)
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
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception as e:
        print(f"  ⚠️ Fetch image: {e}")
        return None

def build_png(headline_court, source, category, photo_url=None, image_query=None):
    """
    Génère un PNG 1200x675 DA Pulse.
    Si photo_url fourni (article RSS) → utilise. Sinon cherche via image_query.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        W, H = 1200, 675
        s = STYLES[category]

        if len(headline_court) > 80:
            headline_court = headline_court[:77].rsplit(" ", 1)[0] + "..."

        # ─── FOND ───
        img = Image.new('RGB', (W, H), (13, 13, 20))

        # Stratégie image : RSS > Unsplash search > fallback catégorie
        img_url = photo_url
        if not img_url and image_query:
            img_url = search_unsplash(image_query, category)
        if not img_url:
            img_url = UNSPLASH_FALLBACK.get(category)

        raw = fetch_img(img_url)
        # Si fail et image_query existait, retry avec fallback catégorie
        if not raw and image_query:
            raw = fetch_img(UNSPLASH_FALLBACK.get(category))

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
                    if line: lines.append(line)
                    line = w
            if line: lines.append(line)
            if len(lines) <= 3:
                chosen_lines = lines
                chosen_size  = fsize
                break
        if chosen_lines is None:
            chosen_lines = [headline_court[:50] + "..."]
            chosen_size  = 38

        ft      = font(chosen_size)
        line_h  = chosen_size + 14
        total_h = len(chosen_lines) * line_h
        ty      = (H - total_h) // 2 + 10
        for ln in chosen_lines:
            bb = draw.textbbox((0, 0), ln, font=ft)
            draw.text(((W - (bb[2] - bb[0])) // 2, ty), ln, font=ft, fill=(255, 255, 255))
            ty += line_h

        # ─── SOURCE + DATE EN BAS ───
        months   = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        now      = datetime.now()
        date_str = f"{months[now.month - 1]} {now.day}, {now.year}"
        draw.text((44, H - 52), source, font=f_sm, fill=(255, 255, 255, 150))
        bb2 = draw.textbbox((0, 0), date_str, font=f_sm)
        draw.text((W - bb2[2] - 44, H - 52), date_str, font=f_sm, fill=(255, 255, 255, 100))

        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
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

    months   = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    now      = datetime.now()
    date_str = f"{months[now.month-1]} {now.day}, {now.year} · {now.strftime('%H:%M')}"

    video_section = f"\n\n🎬 Related video:\n{video['title']}\n{video['url']}" if video else ""
    url_section   = f"\n\n🔗 Original article:\n{url}" if url else ""

    body = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  P U L S E  ·  The world, decoded\n"
        f"  {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📰 Source: {source}\n"
        f"📌 {title}\n\n"
        f"─────────────────────────────────────────\n"
        f"  TWEET — copy this text to X\n"
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
# HISTOIRE DU JOUR — avec vérification Wikipedia
# ═══════════════════════════════════════════════════════════════════════════
def fetch_wikipedia_onthisday():
    """Récupère les événements du jour depuis Wikipedia (source fiable et vérifiable)."""
    try:
        now = datetime.now()
        url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{now.month:02d}/{now.day:02d}"
        req = urllib.request.Request(url, headers={"User-Agent": "PulseBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        events = data.get("events", [])
        # On garde les events avec un texte et un page Wikipedia
        clean = []
        for e in events[:30]:
            year = e.get("year")
            text = e.get("text", "")
            pages = e.get("pages", [])
            if year and text and pages:
                clean.append({
                    "year": year,
                    "text": text,
                    "wiki_url": pages[0].get("content_urls", {}).get("desktop", {}).get("page", "")
                })
        return clean
    except Exception as e:
        print(f"  ⚠️ Wikipedia: {e}")
        return []

def gen_histoire_du_jour(conn):
    """Génère un tweet historique du jour avec faits 100% vérifiés via Wikipedia."""
    if "history" in cats_today(conn):
        return None

    events = fetch_wikipedia_onthisday()
    if not events:
        print("  ⚠️ Pas d'événements Wikipedia disponibles aujourd'hui.")
        return None

    today = datetime.now().strftime("%d %B")

    # On donne les events vérifiés à Claude pour qu'il choisisse + rédige
    events_str = "\n".join(f"- {e['year']}: {e['text']}" for e in events[:15])

    try:
        result = claude(f"""You are a history writer for Pulse, an English news Twitter account.

Today is {today}. Here are VERIFIED historical events from Wikipedia (100% reliable, do NOT invent facts):

{events_str}

CRITICAL RULE — only choose an event if it is GLOBALLY FAMOUS, meaning:
- The vast majority of people (worldwide) know it or have heard of it
- It's the kind of event taught in history class or referenced in pop culture
- Examples of acceptable events: 9/11 attacks, JFK assassination, Apollo 11 moon landing, fall of the Berlin Wall, D-Day, Pearl Harbor, end of WW2, Hiroshima, fall of the Soviet Union, Mandela freed, MLK's "I have a dream", Princess Diana's death, Titanic sinking, Chernobyl, etc.
- Examples to REJECT: local laws, niche scientific discoveries, regional events, obscure treaties, unknown personalities

If NONE of the events in the list above match this "globally famous" criteria → return {{"skip": true}}.
Be STRICT. Better to skip than to publish a forgettable fact.

If you find a worthy event, generate the tweet using ONLY facts from the list above. Do NOT invent dates, numbers, or details.

FORMAT:
- headline_court (max 75 chars): catchy headline for the image
- image_query (max 5 words): what to search for the image
- body: tweet body (400-500 chars).
  • Start with "X years ago today, [event in one punchy sentence]"
  • Then give 1-2 sentences of context/details (no repetition)
  • End with "(Source: Wikipedia)"
  • 2-3 hashtags SPREAD throughout the text on the most searched keywords (person's name, country, event name) — NOT all at the start or end

Return ONLY this JSON:
{{"headline_court":"...","image_query":"...","body":"..."}}
OR
{{"skip": true}}""", max_tokens=600)

        # Si Claude décide de skip
        if result.get("skip"):
            print(f"  📜 Aucun événement historique mondialement connu aujourd'hui — skip.")
            return None

        body           = result.get("body", "").strip()
        headline_court = result.get("headline_court", f"On this day: {today}")[:75]
        image_query    = result.get("image_query", "history old")

        # Nettoyage anti-doublon "HISTORY |"
        for lbl in LABELS.values():
            body = re.sub(rf"^{lbl}\s*\|\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s]+", "", body).strip()

        if not body:
            return None

        tweet_final = build_full_tweet(body, "history")

        print(f"  📜 History generated: {headline_court}")
        return {
            "title":          f"On this day — {today}",
            "source":         "Wikipedia",
            "url":            "",
            "analysis":       {"category": "history", "needs_video": False},
            "tweet":          tweet_final,
            "headline_court": headline_court,
            "image_query":    image_query,
            "photo_url":      None,
        }
    except Exception as e:
        print(f"  ⚠️ History failed: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════
def check_feeds(conn):
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🔍 Check Pulse...")

    # Décide si c'est le moment de publier (intervalle aléatoire 90-150 min)
    if not should_publish_now(conn):
        return

    print(f"  → RSS scan...")

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
        print("  → No new articles.")
        return

    print(f"  → {len(candidates)} new articles · analyzing...")

    # 2. Analyse — séparation entre les articles cachés et ceux à analyser
    recent       = get_recent(conn)
    scored       = []
    to_analyse   = []   # articles à envoyer à Claude
    to_analyse_idx = []

    for i, c in enumerate(candidates):
        cached = get_cached_analysis(conn, c["url"])
        if cached:
            print(f"  💾 Cache: {c['title'][:55]}")
            a = cached
            score = int(a.get("score", 0))
            if a.get("is_duplicate"):
                mark_seen(conn, c["url"], c["title"])
                continue
            if score < SCORE_MINIMUM:
                mark_seen(conn, c["url"], c["title"])
                continue
            scored.append({**c, "analysis": a, "score": score})
        else:
            to_analyse.append(c)
            to_analyse_idx.append(i)

    # 3. Batch analyse de tous les articles non cachés en UN seul appel Claude
    if to_analyse:
        try:
            print(f"  🧠 Batch analyse de {len(to_analyse)} articles...")
            analyses = analyse_batch(to_analyse, recent)
            for c, a in zip(to_analyse, analyses):
                cache_analysis(conn, c["url"], a)
                score = int(a.get("score", 0))
                if a.get("is_duplicate"):
                    mark_seen(conn, c["url"], c["title"])
                    print(f"  ⏩ Duplicate: {c['title'][:55]}")
                    continue
                if score < SCORE_MINIMUM:
                    mark_seen(conn, c["url"], c["title"])
                    print(f"  📉 {score}/10: {c['title'][:55]}")
                    continue
                scored.append({**c, "analysis": a, "score": score})
                print(f"  ✅ {score}/10 [{a.get('category')}]: {c['title'][:55]}")
        except Exception as e:
            print(f"  ❌ Batch analyse: {e}")

    # 3. Boost catégories pas envoyées aujourd'hui
    missing = set(STYLES.keys()) - cats_today(conn)
    for item in scored:
        if item["analysis"]["category"] in missing:
            item["score"] = min(10, item["score"] + 2)

    # 4. Sélection : MAX_PAR_PASSE catégories différentes
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

    print(f"  → {len(top)} selected [{', '.join(used_cats)}]")

    # 5. Histoire du jour (fait vérifié via Wikipedia)
    histoire = gen_histoire_du_jour(conn)
    if histoire and "history" not in used_cats:
        top.append(histoire)
        used_cats.add("history")

    if not top:
        print("  → Nothing to send.")
        return

    # 6. Générer et envoyer
    for item in top:
        try:
            cat = item["analysis"]["category"]
            a   = item["analysis"]

            if "tweet" in item:
                # Histoire du jour pré-générée
                tweet_final    = item["tweet"]
                headline_court = item["headline_court"]
                image_query    = item.get("image_query")
                photo          = item.get("photo_url")
                video          = None
            else:
                add_recent(conn, item["title"])
                video = None
                if a.get("needs_video") and YOUTUBE_API_KEY:
                    video = find_video(item["title"], item["summary"])
                body, headline_court, image_query = gen_tweet_complet(
                    item["title"], item["summary"], item["source"], cat,
                    video_url=video["url"] if video else None
                )
                tweet_final = build_full_tweet(body, cat)
                photo       = extract_photo(item["entry"])

            # Image
            png_bytes, png_nm = build_png(headline_court, item["source"], cat, photo, image_query)

            now    = datetime.now()
            png_nm = png_nm or f"pulse-{cat}-{now.strftime('%d%m%Y-%H%M')}.png"

            emoji   = EMOJIS[cat]
            subject = f"{emoji} Pulse · {item['source']} · {item['title'][:50]}"
            send_email(subject, tweet_final, item["title"], item["source"],
                       item["url"], video, png_bytes, png_nm)
            mark_cat(conn, cat)
            # On marque l'article comme vu UNIQUEMENT après publication réussie
            if item.get("url"):
                mark_seen(conn, item["url"], item["title"])
            print(f"  📧 Sent [{cat}]: {item['title'][:55]}")
            time.sleep(4)

        except Exception as e:
            print(f"  ❌ Send '{item['title'][:40]}': {e}")


def main():
    print("🤖 Pulse NewsBot started!")
    conn = init_db()
    while True:
        check_feeds(conn)
        print("  💤 Sleep 2h...\n")
        time.sleep(7200)

if __name__ == "__main__":
    main()
