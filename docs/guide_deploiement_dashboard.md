# Guide : déployer le tableau de bord sur Streamlit Community Cloud

## Pourquoi cette plateforme

Gratuite, sans carte bancaire, pensée pour exactement ce genre d'app
(Streamlit). Accès restreignable à une liste de personnes précises (pas de
lien public ouvert à tous), et un cold-start de quelques dizaines de secondes
à chaque réveil est acceptable pour ce projet (pas un service temps réel).

**Aucune modification du code n'est nécessaire** : `app/db.py` synchronise
lui-même les données au démarrage (voir README, section "Tableau de bord") —
il suffit de pointer Streamlit Community Cloud vers ce dépôt.

## Ce que tu dois faire toi-même (compte GitHub/Streamlit — je ne peux pas le faire à ta place)

### 1. Créer un compte sur share.streamlit.io

Va sur [share.streamlit.io](https://share.streamlit.io) et connecte-toi avec
ton compte GitHub (TheoooBa) — c'est la méthode d'authentification standard
de la plateforme, aucun mot de passe séparé à retenir.

### 2. Créer l'app

**Create app** → **Deploy a public app from GitHub** (le dépôt source reste
public sur GitHub même si l'accès à l'app est restreint ensuite — ce sont
deux réglages différents) :

- **Repository** : `TheoooBa/Solana-Memecoin-Analysis`
- **Branch** : `main`
- **Main file path** : `app/Home.py`
- Laisse les réglages Python par défaut (le dépôt n'a pas de `runtime.txt` :
  Streamlit Cloud choisit une version récente compatible avec
  `requirements.txt`)

Clique **Deploy**. Le premier démarrage prend plus longtemps que les
suivants : l'app clone la branche `data` et reconstruit la base SQLite avant
d'afficher quoi que ce soit (voir README) — plusieurs dizaines de secondes à
quelques minutes selon la taille du jeu de données à ce moment-là.

### 3. Restreindre l'accès

Dans les paramètres de l'app (icône ⚙️ ou **Settings** depuis le tableau de
bord Streamlit Cloud) → section **Sharing** :

- Passe l'app en **"Only specific people can view this app"**
- Ajoute les adresses email des personnes autorisées (la tienne d'abord)
- Chaque visiteur autorisé devra se connecter avec un compte Google
  correspondant à l'email ajouté — c'est le mécanisme d'authentification de
  Streamlit Cloud, pas quelque chose que ce projet gère lui-même

## Vérifier que ça marche

Ouvre l'URL de l'app (format `https://<nom-app>.streamlit.app`) :

- La page Accueil doit afficher de vrais chiffres (nombre de pools, dernier
  run) plutôt que "Aucune base locale trouvée"
- Le panneau "Santé du collecteur" doit refléter l'état réel de la collecte
  au moment de la visite (pas une valeur figée)

Si l'app affiche une erreur de synchronisation dans la barre latérale
("Synchronisation des données impossible : ...") au lieu des données : le
clonage de la branche `data` ou la reconstruction de la base a échoué — le
message donne la raison (réseau, format de fichier inattendu, etc.).

## Limites et risques connus

- **Cold start** : après une période d'inactivité, Streamlit Cloud met l'app
  en veille ; la prochaine visite relance tout depuis zéro, resynchronisation
  comprise. Accepté dès le départ pour ce projet (voir README).
- **Synchronisation toutes les 15 minutes tant que l'app reste active**
  (`ttl=900` dans `app/db.py`) : entre deux syncs, l'app peut afficher des
  données légèrement en retard sur la collecte réelle — jamais plus de 15
  minutes en pratique.
- **Dépôt public** : même avec l'accès à l'app restreint, le code et
  l'historique Git (dont la branche `data`) restent visibles par quiconque
  sur GitHub. Rien de sensible n'y vit (pas de clé, pas de wallet), mais ce
  n'est pas une confidentialité des données elles-mêmes, seulement de
  l'interface.
- **Streamlit Community Cloud reste un service tiers gratuit** : pas de
  garantie contractuelle de disponibilité, comme cron-job.org pour la
  collecte (voir `docs/guide_cron_externe.md`).
