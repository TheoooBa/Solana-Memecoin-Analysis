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

Données : [GeckoTerminal](https://www.geckoterminal.com/) (API publique
gratuite). Merci de conserver cette attribution si tu réutilises ce projet ou
son tableau de bord.

## Où en est le projet

Étape 1 sur 4 (voir la feuille de route ci-dessous) : **collecte, stockage,
journalisation, tests, démonstration locale**. Le module d'analyse, le
simulateur, le gel de configuration et le tableau de bord viennent après,
seulement une fois plusieurs semaines de données réunies.

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
- `data/logs/runs/AAAA-MM-JJ.csv` — une ligne par exécution de `collect`
  (appels effectués, erreurs, pools vus), pour repérer les trous de collecte.

La commande `build-db` charge tout ça dans une base SQLite locale
(`data/smc.sqlite3`), reconstruite à chaque exécution — les CSV restent la
seule source de vérité versionnée.

## Utilisation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Une collecte unique, idempotente (peut être relancée à la main ou par cron/Actions)
python -m smc_collector.cli collect

# Recharge tous les CSV dans une base SQLite locale pour exploration
python -m smc_collector.cli build-db

# Diagnostic ponctuel : l'historique OHLCV existe-t-il encore pour des pools déjà morts ?
python -m smc_collector.cli diagnose-dead-pools
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

1. ~~Collecte, stockage, journalisation, tests, démonstration locale~~ (cette étape)
2. Workflow GitHub Actions (collecte toutes les 5 minutes) + guide de mise en place
3. Une fois plusieurs semaines de données réunies : module d'analyse,
   simulateur, gel de configuration
4. Tableau de bord Streamlit
