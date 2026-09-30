# Analyse des memecoins Solana : y a-t-il un avantage mesurable ?

**Ce n'est pas un bot de trading.** C'est un outil de collecte et d'analyse
statistique qui cherche à répondre honnêtement à une question précise :

> Sur les memecoins Solana, existe-t-il des caractéristiques mesurables,
> connues au moment où un token commence à attirer l'attention, qui
> permettent d'entrer plus tôt et de perdre moins, après frais et slippage ?

L'hypothèse de départ est **négative** : sans être insider ou ultra précoce,
il n'y a probablement pas d'avantage exploitable. L'outil cherche à réfuter
cette hypothèse honnêtement, sans la confirmer ni l'infirmer par construction.
Résultat par défaut : **« aucun avantage démontré »**. Un résultat positif ne
sera présenté que s'il tient sur des données gelées, mesurées après coûts.

Aucun wallet, aucune clé privée, aucun code n'envoie de transaction. Aucune
dépendance payante. Aucun compte n'est créé pour toi.

Données : [GeckoTerminal](https://www.geckoterminal.com/) et
[RugCheck.xyz](https://rugcheck.xyz/) (API publiques gratuites). Merci de
conserver cette attribution si tu réutilises ce projet ou son tableau de bord.

## Où en est le projet

Étape 2 sur 4 (voir la feuille de route en bas de page) : **collecte
automatique via GitHub Actions**, en plus de l'étape 1 (collecte locale,
stockage, tests) déjà en place — enrichie depuis le 2026-09-30 d'un schéma
plus riche et d'une vraie détection anti-honeypot (RugCheck.xyz, voir plus
bas). Une première version du tableau de bord (étape 4) existe déjà en local
(`app/`), lisible sur les données déjà collectées, mais le module d'analyse
et son gel de configuration (étape 3) n'ont pas encore commencé — ils
attendent plusieurs semaines de données réunies avant de s'appuyer dessus.

Pour mettre en place la collecte automatique toi-même :
[docs/guide_github_actions.md](docs/guide_github_actions.md).

**Correctif de cadence (2026-09-29)** : le cron interne de GitHub Actions
(`schedule:`) ne tient pas l'intervalle de 5 minutes configuré — mesuré en
production, la cadence réelle tournait autour de 4h de moyenne entre deux
collectes (documentation officielle GitHub : les événements `schedule`
« peuvent être retardés en période de forte charge », contrairement à
`workflow_dispatch`, déclenché via API). Voir
[docs/guide_cron_externe.md](docs/guide_cron_externe.md) pour la mise en
place d'un minuteur externe qui contourne ce problème.

## Univers suivi et biais

Pas de filtre par plateforme de lancement (pump.fun, etc.) : tous les pools
Solana exposés par la source, avec le DEX en colonne. Deux groupes, chacun
avec sa raison de suivi enregistrée :

- **`trending`** : les pools que GeckoTerminal met en avant comme "en
  tendance". C'est une boîte noire (on ne sait pas comment la source calcule
  ce classement), donc l'heure de signal utilisée dans l'analyse n'est
  **jamais** l'heure d'entrée dans cette liste, mais l'heure de **notre**
  première observation.
- **`random_new`** : un échantillon aléatoire de nouveaux pools, tiré avec
  une **graine fixe** et une **probabilité enregistrée** par pool
  (`config/collection.yaml: univers.new_pools_sample_probability`). C'est le
  groupe témoin : sans lui, on ne verrait que des pools qui ont eu un
  minimum de succès (puisque "trending" présuppose déjà une forme de succès),
  et toute statistique serait biaisée par construction.

Le tirage au sort est une **fonction pure** de `(graine, adresse du pool)` —
voir `deterministic_sample_decision` dans
[`src/smc_collector/registry.py`](src/smc_collector/registry.py). Il ne
dépend jamais de l'ordre d'exécution ni d'un état séquentiel : rejouer la
collecte ou manquer des pages à cause du quota ne change jamais qui est
échantillonné.

**Aucun pool n'est jamais supprimé.** Un pool mort reste dans les fichiers,
avec un événement qui documente pourquoi et quand son suivi s'est arrêté
(voir `data/raw/pool_status/`).

## Architecture des données

Trois familles de fichiers CSV append-only, un fichier par jour, jamais
réécrits ni élagués :

- `data/raw/pool_registry/AAAA-MM-JJ.csv` — une ligne par pool la première
  fois qu'on décide de le suivre (groupe, raison, probabilité
  d'échantillonnage, jusqu'à quand on le suit).
- `data/raw/snapshots/AAAA-MM-JJ.csv` — une ligne par observation. Deux
  provenances possibles, marquées dans la colonne `source` :
  - `snapshot` : instantané pris par nos propres requêtes pendant la fenêtre
    de suivi (48h par défaut) ;
  - `ohlcv_backfill` : bougie OHLCV reconstituée pour compléter l'historique
    **avant** notre première observation d'un pool nouvellement découvert.
- `data/raw/pool_status/AAAA-MM-JJ.csv` — événements de cycle de vie
  (fin de fenêtre de suivi, pool absent d'une réponse groupée, etc.).
- `data/raw/trending_ranks/AAAA-MM-JJ.csv` et `data/raw/new_pool_ranks/AAAA-MM-JJ.csv`
  — le rang d'un pool dans chacune de ces deux listes, à **chaque** exécution
  où il y apparaît (pas seulement à sa découverte) : une boîte noire sans
  historique côté source, irrécupérable après coup si non captée sur le moment.
- `data/raw/token_info/AAAA-MM-JJ.csv` — un appel `/tokens/{adresse}/info`
  (GeckoTerminal) par pool, à sa découverte : score de confiance,
  concentration des détenteurs, autorités mint/freeze, part développeur,
  indicateur honeypot brut (souvent "unknown" en pratique — voir la section
  RugCheck ci-dessous). `data/raw/token_info_status/` journalise les échecs et
  les pools définitivement marqués indisponibles.
- `data/raw/rugcheck_info/AAAA-MM-JJ.csv` et `data/raw/rugcheck_status/AAAA-MM-JJ.csv`
  — détection anti-honeypot réelle via RugCheck.xyz, même principe (un appel
  par pool à sa découverte). Voir section dédiée ci-dessous.
- `data/logs/runs/AAAA-MM-JJ.csv` — une ligne par exécution de `collect`
  (appels effectués, erreurs, pools vus), pour repérer les trous de collecte.

La commande `build-db` charge tout ça dans une base SQLite locale
(`data/smc.sqlite3`), reconstruite à chaque exécution — les CSV restent la
seule source de vérité versionnée.

Le dépôt Git a deux branches : `main` (code, config, tests) et `data` (les
CSV ci-dessus, à sa racine — pas de sous-dossier `data/` imbriqué sur cette
branche). Voir [docs/guide_github_actions.md](docs/guide_github_actions.md)
pour la mise en place.

## Détection anti-honeypot réelle (RugCheck.xyz)

Le champ `is_honeypot` de GeckoTerminal renvoie presque systématiquement
"unknown" en pratique — inutilisable comme signal. RugCheck.xyz (API
publique, gratuite, sans clé, vérifiée par appels réels le 2026-09-29 puis
reconfirmée le 2026-09-30, y compris sur un pool créé quelques secondes plus
tôt) détecte de vrais vecteurs d'arnaque : extensions Token-2022 dangereuses
("permanent delegate" pouvant déplacer les tokens de n'importe quel
portefeuille, "transfer hook" pouvant bloquer les ventes à volonté, compte
gelable par défaut), taxe de transfert cachée, verrouillage réel de la
liquidité, et détection de réseaux d'insiders coordonnés parmi les
détenteurs. Un appel par pool, à sa découverte, jamais réinterrogé.

Deux différences volontaires avec `token_info` :

- **Aucune réponse brute conservée** : ~12 Ko par appel, plus de 2x le seuil
  déjà retenu pour `token_info`, pour une valeur ajoutée jugée insuffisante
  par l'audit (voir `reports/rugcheck_audit_2026-09-29.md`) — seuls les
  champs ciblés sont stockés.
- **Un 404 ("pas encore indexé") n'est jamais compté comme un échec** :
  contrairement à un vrai échec (500, timeout...), il ne fait pas avancer le
  compteur d'un pool vers "indisponible" et ne déclenche jamais le
  coupe-circuit — le pool reste éligible indéfiniment, retenté au run suivant.

Même prudence que pour GeckoTerminal à l'origine sur le débit : l'en-tête
observé (`x-rate-limit-limit: 15`) ne précise pas la fenêtre temporelle, donc
`config/collection.yaml: rugcheck.calls_per_minute` démarre conservateur (8,
comme GeckoTerminal) plutôt que de présumer une limite favorable.

## Cache brut sur GitHub Actions : une limite assumée

`data/cache/` archive le JSON brut de chaque appel HTTP, utile pour
déboguer en local. Sur GitHub Actions, chaque run tourne sur une machine
neuve et éphémère : ce cache ne peut survivre d'un run à l'autre que s'il
est commité sur la branche `data` — ce qui représenterait plusieurs
centaines de Mo à quelques Go par mois sur un dépôt public, pour une valeur
d'usage limitée puisque les CSV (`raw/`) restent déjà la source de vérité
complète. **Décision : le cache n'est jamais versionné** (`.gitignore` sur la
branche `data`). Il reste local et utile quand tu lances `collect`
toi-même, et est simplement recréé (et perdu) à chaque run automatique.

## Utilisation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Pas de packaging pour ce projet (voir tests/conftest.py) : PYTHONPATH=src
# est nécessaire pour toutes les commandes ci-dessous.

# Une collecte unique, idempotente (peut être relancée à la main ou par cron/Actions)
PYTHONPATH=src python -m smc_collector.cli collect

# Recharge tous les CSV dans une base SQLite locale pour exploration
PYTHONPATH=src python -m smc_collector.cli build-db

# Diagnostic ponctuel : l'historique OHLCV existe-t-il encore pour des pools déjà morts ?
PYTHONPATH=src python -m smc_collector.cli diagnose-dead-pools

# Tableau de bord local, en lecture seule sur data/smc.sqlite3 (construit
# avec build-db ci-dessus) — voir section "Tableau de bord" plus bas.
streamlit run app/Home.py
```

Une clé "Demo" CoinGecko est optionnelle (voir `.env.example`) : la collecte
fonctionne sans, via la surface API `api.geckoterminal.com/api/v2` qui ne
demande aucun compte.

## Débit observé (2026-09-21)

La documentation officielle se contredit sur le débit gratuit sans clé
(certaines pages disent ~10 appels/minute, d'autres ~30). Faute de source
faisant autorité unique, la config par défaut est **volontairement
conservatrice** (`api.calls_per_minute: 8`), avec un coupe-circuit dur par
exécution (`api.max_calls_per_run: 60`) et des reprises à délai exponentiel
qui respectent l'en-tête `Retry-After` en cas de 429.

**Mesure empirique lors de la démonstration locale** : même à 8 appels/minute
(un appel toutes les 7,5 s), l'API a renvoyé des `429` sur environ un tiers
des appels OHLCV consécutifs — donc le débit réellement toléré en rafale est
**inférieur** à 8/min, au moins par intermittence. Le mécanisme de reprise
(délai exponentiel, `Retry-After` respecté) a systématiquement récupéré au
essai suivant, et le run s'est terminé sans perte de données
(`exit_reason=completed`). Ne descends pas `calls_per_minute` en dessous de
cette valeur sans revoir aussi `max_retries` en conséquence. Ajuste
`calls_per_minute` à la hausse si tu observes empiriquement plus de marge.

## Tableau de bord (étape 4, première version)

`app/` — application Streamlit, cinq pages : Accueil, Collecte, Explorateur
de pools, Analyse, Réseaux. **Lecture seule** : aucune collecte, aucun appel
API et aucune transaction depuis l'app elle-même, uniquement des requêtes SQL
sur `data/smc.sqlite3` (reconstruite par `build-db`, jamais la source de
vérité).

- **Accueil** : santé du collecteur, taille des groupes tendance/témoin,
  variations de prix les plus marquées sur la fenêtre de données la plus récente.
- **Collecte** : cadence réelle entre exécutions, budget d'appels, erreurs.
- **Explorateur** : tous les pools suivis, filtrables par groupe, avec le
  détail (historique de prix, sécurité GeckoTerminal/RugCheck si déjà
  interrogé) d'un pool choisi dans notre propre jeu de données — pas une
  redite d'une fiche token que GeckoTerminal ou RugCheck proposent déjà
  chacun en direct.
- **Analyse** : affiche honnêtement « aucun avantage démontré » tant que
  l'étape 3 (module d'analyse, gel de configuration) n'a pas commencé —
  aucune règle fabriquée n'y apparaît.
- **Réseaux** : état vide assumé (pas de scraping X/Telegram par conception).

Déployé sur Streamlit Community Cloud, accès restreint (voir
[docs/guide_deploiement_dashboard.md](docs/guide_deploiement_dashboard.md)).

**Auto-synchronisation au démarrage** : Streamlit Community Cloud ne clone
que la branche `main`, jamais `data` (où vivent les vrais CSV) — sans rien de
plus, l'app déployée n'aurait donc aucune donnée. `app/db.py` clone la
branche `data` (dépôt public, lecture seule, aucune authentification, aucun
appel à GeckoTerminal ni RugCheck) dans un dossier temporaire au premier
chargement, reconstruit `data/smc.sqlite3`, et rafraîchit toutes les 15
minutes tant que l'app reste active. En local avec `data/` déjà rempli à la
main, ce comportement peut être désactivé (`SMC_SKIP_SYNC=1`) pour travailler
hors-ligne sur un jeu de données figé.

## Rapport : historique OHLCV pour des pools morts

Voir [`reports/dead_pool_history_probe.md`](reports/dead_pool_history_probe.md),
généré par `diagnose-dead-pools`. Résumé : sur l'échantillon sondé (pools
créés dans les minutes précédant le sondage, avec une réserve de liquidité
quasi nulle), **l'historique OHLCV reste accessible** et l'endpoint
multi-pools continue de résoudre l'adresse. Cet échantillon ne couvre que des
pools morts très récemment — seul le suivi 48h en conditions réelles dira si
c'est encore vrai plusieurs semaines après la mort d'un pool.

## Configuration

`config/collection.yaml` contient uniquement des réglages d'ingénierie
(débit, budget d'appels, durée de suivi, graine et probabilité
d'échantillonnage). Aucun seuil d'analyse n'y vit : les définitions comme "un
démarrage", "un rug", une taille de trade, etc. viendront dans
`config/analysis.yaml` à l'étape 3, avec leur propre mécanisme de gel.

## Hors périmètre

Trading réel ou simulé en direct, wallet, bots, agents ou LLM pour découvrir
des patterns, API payantes (dont Dune — jamais appelée), scraping de X ou
Telegram, choix des seuils d'analyse à la place de l'utilisateur.

## Prochaines étapes

1. ~~Collecte, stockage, journalisation, tests, démonstration locale~~
2. ~~Workflow GitHub Actions (collecte toutes les 5 minutes) + guide de mise en place~~,
   ~~schéma enrichi + détection anti-honeypot réelle (RugCheck.xyz)~~ (cette étape)
3. Une fois plusieurs semaines de données réunies : module d'analyse,
   simulateur, gel de configuration
4. ~~Première version du tableau de bord (Streamlit, lecture seule)~~ —
   reste à faire : déploiement sur Streamlit Community Cloud (accès
   restreint), et la page Analyse une fois l'étape 3 commencée
