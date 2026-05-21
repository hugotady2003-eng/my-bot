"""
Script de test isolé : vérifie la connexion X et essaie de poster un tweet.
Affiche TOUS les détails d'erreur pour identifier le problème.
"""
import os, sys

# Lecture des credentials
API_KEY       = os.environ.get("TWITTER_API_KEY",             "")
API_SECRET    = os.environ.get("TWITTER_API_SECRET",          "")
ACCESS_TOKEN  = os.environ.get("TWITTER_ACCESS_TOKEN",        "")
ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")

print("=" * 60)
print("TEST TWITTER / X API")
print("=" * 60)

# 1. Vérification des credentials
print("\n1️⃣  Vérification des credentials...")
for name, val in [
    ("TWITTER_API_KEY",             API_KEY),
    ("TWITTER_API_SECRET",          API_SECRET),
    ("TWITTER_ACCESS_TOKEN",        ACCESS_TOKEN),
    ("TWITTER_ACCESS_TOKEN_SECRET", ACCESS_SECRET),
]:
    if val:
        # Affiche les 4 premiers et 4 derniers caractères pour vérification sans exposer
        masked = f"{val[:4]}...{val[-4:]} ({len(val)} chars)"
        print(f"   ✅ {name}: {masked}")
    else:
        print(f"   ❌ {name}: MANQUANT")
        sys.exit(1)

# 2. Import tweepy
print("\n2️⃣  Import tweepy...")
try:
    import tweepy
    print(f"   ✅ tweepy version: {tweepy.__version__}")
except ImportError:
    print("   ❌ tweepy non installé. Fais: pip install tweepy")
    sys.exit(1)

# 3. Auth OAuth 1.0a
print("\n3️⃣  Création de l'auth OAuth 1.0a...")
try:
    auth = tweepy.OAuth1UserHandler(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)
    api_v1 = tweepy.API(auth)
    print("   ✅ Auth créée")
except Exception as e:
    print(f"   ❌ {type(e).__name__}: {e}")
    sys.exit(1)

# 4. Test verify_credentials (lecture simple)
print("\n4️⃣  Vérification des credentials avec verify_credentials...")
try:
    me = api_v1.verify_credentials()
    print(f"   ✅ Connecté en tant que: @{me.screen_name} ({me.name})")
    print(f"   📊 Followers: {me.followers_count}")
except Exception as e:
    print(f"   ❌ {type(e).__name__}: {e}")
    print("   → Les credentials sont INVALIDES.")
    print("   → Cause probable: tokens en read-only, ou plan X désactivé.")
    sys.exit(1)

# 5. Test client v2
print("\n5️⃣  Création du client v2...")
try:
    client_v2 = tweepy.Client(
        consumer_key=API_KEY, consumer_secret=API_SECRET,
        access_token=ACCESS_TOKEN, access_token_secret=ACCESS_SECRET
    )
    print("   ✅ Client v2 créé")
except Exception as e:
    print(f"   ❌ {type(e).__name__}: {e}")
    sys.exit(1)

# 6. POST D'UN VRAI TWEET DE TEST
print("\n6️⃣  Tentative de poster un tweet de test...")
from datetime import datetime
test_text = f"🧪 Pulse bot test — {datetime.now().strftime('%H:%M:%S')}"

try:
    response = client_v2.create_tweet(text=test_text)
    tweet_id = response.data.get("id")
    print(f"   ✅ TWEET POSTÉ ! ID: {tweet_id}")
    print(f"   🔗 https://x.com/i/web/status/{tweet_id}")
    print("\n🎉 TOUT FONCTIONNE ! Le bot peut maintenant poster.")
except Exception as e:
    print(f"   ❌ {type(e).__name__}: {e}")
    # Détails supplémentaires si disponibles
    if hasattr(e, "response") and e.response is not None:
        print(f"\n   📋 Status code: {e.response.status_code}")
        print(f"   📋 Response body: {e.response.text[:500]}")
    print("\n💡 Causes possibles :")
    print("   1. Pay-per-use pas activé sur ton compte X dev")
    print("   2. Pas de crédit/dépôt minimum sur ton compte X dev")
    print("   3. Permissions de l'app pas sur 'Read and Write'")
    print("   4. Tokens générés avant changement permissions")
    sys.exit(1)
