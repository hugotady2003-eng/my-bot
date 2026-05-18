# 🤖 NewsBot — Tweets par Email

Bot qui surveille les journaux, génère des tweets via Claude, et te les envoie par email en HTML.

---

## 🚀 Installation (15 minutes, tout gratuit)

### Étape 1 — Mot de passe d'application Gmail

> ⚠️ Tu ne vas PAS utiliser ton vrai mot de passe Gmail.
> Google propose des "mots de passe d'application" spéciaux pour ça.

1. Va sur [myaccount.google.com](https://myaccount.google.com)
2. **Sécurité** → **Validation en deux étapes** (active-la si pas encore fait)
3. Retourne dans **Sécurité** → cherche **"Mots de passe des applications"**
4. Choisis "Application : Autre" → tape "NewsBot" → clique **Générer**
5. Copie le mot de passe à 16 caractères (ex: `abcd efgh ijkl mnop`)

---

### Étape 2 — Clé Claude (Anthropic)

1. Va sur [console.anthropic.com](https://console.anthropic.com)
2. Crée un compte gratuit
3. **API Keys** → **Create Key** → copie la clé
4. Ajoute ~5€ de crédit (dure plusieurs mois pour ce bot)

---

### Étape 3 — GitHub Actions (hébergement gratuit)

1. Crée un compte [GitHub](https://github.com) si pas encore fait
2. Crée un **nouveau repo privé** (ex: `mon-newsbot`)
3. Upload tous les fichiers du projet dans ce repo
4. Va dans **Settings → Secrets and variables → Actions**
5. Clique **New repository secret** et ajoute ces 4 secrets :

| Nom du secret | Valeur |
|---|---|
| `GMAIL_ADDRESS` | ton.adresse@gmail.com |
| `GMAIL_APP_PASS` | le mot de passe à 16 caractères |
| `EMAIL_TO` | l'adresse où tu veux recevoir (peut être la même) |
| `ANTHROPIC_API_KEY` | ta clé Claude (sk-ant-...) |

6. Va dans l'onglet **Actions** → clique **"I understand my workflows, enable them"**
7. ✅ C'est parti ! Le bot tourne toutes les 30 minutes automatiquement.

---

## 📧 Ce que tu reçois

Un email HTML bien formaté avec :
- La **source** et le **titre** de l'article
- Une **photo d'illustration** si disponible
- Le **tweet prêt à copier-coller** (ou le thread complet)
- Un bouton pour lire l'article original

Tu n'as plus qu'à copier le tweet et le poster sur X ! ✨

---

## ⚙️ Personnaliser les sources

Dans `bot.py`, modifie la liste `RSS_FEEDS` pour ajouter tes journaux préférés.
Exemple pour ajouter L'Équipe :
```python
{"url": "https://www.lequipe.fr/rss/actu_rss.xml", "source": "L'Équipe"},
```

---

## 💰 Coût réel

| Service | Coût |
|---|---|
| GitHub Actions | **Gratuit** |
| Gmail SMTP | **Gratuit** |
| Claude API | ~0,50€/mois |
| **Total** | **~0,50€/mois** |
