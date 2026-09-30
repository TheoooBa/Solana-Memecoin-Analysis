# Audit du schéma de stockage face aux réponses API réelles

Date : 2026-09-21. Méthode : appels réels sur `api.geckoterminal.com/api/v2`
(la surface gratuite qu'on utilise déjà), comparés champ par champ au code
actuel (`src/smc_collector/models.py`, `parsing.py`, `collect.py`).
**Aucun code n'a été modifié pour produire cet audit.**

Légende priorité :
- 🔴 **Irrécupérable** : donnée éphémère liée à l'instant de la requête ; si on
  ne la capture pas maintenant, elle est perdue pour toujours (aucune API de
  la source ne permet de revenir en arrière).
- 🟠 **Dans la réponse mais ignorée** : on appelle déjà cet endpoint, la donnée
  arrive déjà dans le JSON, on la jette au parsing. Coût d'ajout nul en
  appels, juste du parsing. Devient irrécupérable dès qu'on ne l'ajoute pas
  avant le prochain run (car liée à cet instant précis).
- 🟡 **Reconstructible plus tard, à un coût** : récupérable via un appel
  supplémentaire, tant que le pool/token existe encore côté source.
- ⚪ **Aucune perte réelle** : dérivable par calcul à partir d'autres champs
  déjà stockés, ou valeur cosmétique sans usage analytique.

---

## 1. `trending_pools` et `new_pools` (endpoints déjà appelés chaque run)

Champs de `attributes` (identiques sur les deux endpoints) :

| Champ API | Stocké ? | Statut | Détail |
|---|---|---|---|
| `address` | ✅ `pool_address` | — | |
| `pool_created_at` | ✅ | — | |
| `base_token_price_usd` | ✅ `price_usd` | — | |
| `fdv_usd` | ✅ | — | |
| `market_cap_usd` | ✅ | — | |
| `reserve_in_usd` | ✅ `reserve_usd` | — | |
| `name` (ex: "PEPENOM / SOL") | ❌ | ⚪ | Reconstructible par concaténation `base_token_symbol / quote_token_symbol`. Aucune action. |
| `quote_token_price_usd` | ❌ | 🟠🔴 | **Le prix du SOL en USD à l'instant du snapshot.** Absent de toute table. Or l'étape 3 prévoit explicitement de "découper par niveau du prix du SOL" — sans cette colonne, cette stratification devra s'appuyer sur une source externe non certifiée par notre propre collecte. Ajout : 1 colonne, 0 appel supplémentaire (déjà dans le JSON reçu). |
| `base_token_price_native_currency`, `base_token_price_quote_token`, `quote_token_price_base_token` | ❌ | ⚪ | Ratios dérivables de `price_usd` / `quote_token_price_usd` une fois ce dernier ajouté. Aucune action nécessaire. |
| `price_change_percentage` {m5,m15,m30,h1,h6,h24} | ❌ (aucune des 6 périodes) | 🟠 | Complètement absent du schéma actuel. Pré-calculé par la source sur sa propre fréquence interne (potentiellement plus fine que notre cadence de 5 min) — pas strictement redérivable à l'identique depuis nos seuls snapshots. 0 appel supplémentaire, 6 colonnes. |
| `transactions` {m5,m15,**m30**,h1,**h6**,h24} × {buys,sells,buyers,sellers} | ✅ m5, h1, h24 seulement | 🟠 | **m15, m30 et h6 sont dans la réponse reçue à chaque run et jetés au parsing.** 0 appel supplémentaire, 12 colonnes (3 périodes × 4 champs). |
| `volume_usd` {m5,m15,**m30**,h1,**h6**,h24} | ✅ m5, h1, h24 seulement | 🟠 | Même remarque : m15, m30, h6 ignorés. 0 appel, 3 colonnes. |

## 2. `pools/multi/{addresses}` (endpoint déjà appelé chaque run pour les instantanés)

Mêmes champs que ci-dessus, **plus deux champs exclusifs à cet endpoint**
(absents de `trending_pools`/`new_pools`, vérifié empiriquement) :

| Champ API | Stocké ? | Statut | Détail |
|---|---|---|---|
| `locked_liquidity_percentage` | ❌ | 🔴🟠 | **Indicateur de sécurité anti-rug direct** (part de la liquidité verrouillée). Déjà dans la réponse de l'endpoint qu'on appelle à *chaque* run pour les instantanés. C'est un état vivant qui peut changer (déverrouillage) — le rater à un run, c'est le perdre pour cet instant précis. 0 appel supplémentaire, 1 colonne. **Priorité la plus haute de tout cet audit avec le rang tendance (section 4).** |
| `pool_fee_percentage` | ❌ | 🟠 | Directement utile pour le futur simulateur de coûts (étape 3 : modélisation des frais). 0 appel, 1 colonne. |
| `pool_name` | ❌ | ⚪ | Alias de `name`, même remarque qu'en section 1. |

## 3. `pools/{address}/ohlcv/{timeframe}` (backfill historique)

| Champ API | Stocké ? | Statut | Détail |
|---|---|---|---|
| `data.attributes.ohlcv_list` (timestamp, o, h, l, c, v) | ✅ intégral | — | |
| `meta.base.{symbol,address}` et `meta.quote.{symbol,address}` | ❌ | 🟠 | **Bug de parsing, pas une vraie limite d'API** : `parse_ohlcv_candles` ignore complètement le bloc `meta` de la réponse. Résultat concret : dans `collect.py`, les colonnes `base_token_symbol`/`base_token_address`/`quote_token_symbol`/`quote_token_address` sont mises en dur à `None` pour *toutes* les lignes `source="ohlcv_backfill"` (~9 300 des ~9 350 lignes de la démo actuelle !), alors que l'info est présente dans chaque réponse déjà reçue. 0 appel, remplit des colonnes qui existent déjà mais restent vides. |

## 4. Rang dans la liste "tendance" — non capturé du tout, à chaque run

Le rang (`trending_rank_N`) n'est aujourd'hui écrit qu'**une seule fois**, au
moment de la toute première découverte d'un pool (`reason` dans
`pool_registry`). Si ce pool reste en tendance (ou y revient) lors des runs
suivants, son rang à *ces* instants-là n'est stocké nulle part.

🔴 **Irrécupérable par nature** : la liste "tendance" est une boîte noire
recalculée en continu par la source, sans API d'historique. Un rang non capturé
à l'instant T est perdu pour toujours — c'est exactement le point que tu
soulèves, et c'est le plus urgent de cet audit à mes yeux avec
`locked_liquidity_percentage`.

**Coût d'ajout** : 0 appel supplémentaire (`trending_pools` est déjà appelé
chaque run). Nouvelle table `trending_observations` (une ligne par pool
présent dans la liste tendance, à chaque run) : `request_timestamp_utc`,
`pool_address`, `network`, `rank`. Avec `trending_pool_count=20` et un run
toutes les 5 min : ~20 lignes × 288 runs/jour ≈ 5 760 lignes/jour, ~60-80
octets/ligne → **~350-450 Ko/jour, ~10-13 Mo/mois**. Négligeable.

## 5. `tokens/{address}/info` — endpoint gratuit non appelé actuellement

**Découverte de cet audit** : la documentation CoinGecko présente cet
endpoint comme réservé au plan payant (`pro-api.coingecko.com/.../info`),
mais il est en réalité accessible **gratuitement, sans clé**, sur la même
surface `api.geckoterminal.com/api/v2` qu'on utilise déjà. Vérifié par un
appel réel (200 OK) le 2026-09-21.

Répond directement à ta question sur la concentration des détenteurs et les
indicateurs de sécurité :

| Champ | Contenu | Exemple observé |
|---|---|---|
| `gt_score` + `gt_score_details` | Score de confiance GeckoTerminal (0-100), détaillé par pool/transaction/creation/info/holders | `77.2`, détail par composante |
| `gt_verified` | Token vérifié par GeckoTerminal ou non | `true` |
| `holders.count` | Nombre de détenteurs uniques | `1932` |
| `holders.distribution_percentage` | **Concentration** : part détenue par le top 10, 11-20, 21-40, le reste | top_10 = `59.6%` |
| `holders.last_updated` | Fraîcheur de la donnée détenteurs | ~1x/jour observé |
| `mint_authority`, `freeze_authority` | Le créateur peut-il encore créer des tokens / geler des comptes | `"no"` / `"no"` |
| `is_honeypot` | Détection honeypot | `"unknown"` dans notre exemple — **peu fiable en pratique, souvent non renseigné** |
| `developer_address`, `developer_holding_percentage` | Part détenue par le développeur | `41.85%` |
| `launchpad_details` | Migration pump.fun-like : % de graduation, date de complétion, pool de destination | utile comme signal de "démarrage" indépendant du DEX |

Testé aussi : `top_holders` (liste adresse par adresse) et `holders_chart`
(historique) renvoient **401 Unauthorized** sans clé payante — ceux-là sont
réellement verrouillés, contrairement à `/info`.

**Coût en appels** : ce n'est PAS un endpoint à appeler à chaque run (ces
données évoluent lentement — `last_updated` observé à ~1x/jour). Proposition :
1 appel par pool **nouvellement découvert** (à la découverte), donc le même
ordre de grandeur que le backfill OHLCV actuel. En régime de croisière observé
sur le run réel du 21/09 (0 nouveauté tendance, 6 nouveautés témoin), ça
representerait **+5 à +10 appels/run**, ce qui allonge la durée du run
(chaque appel ~7-15s avec les 429 déjà observés → **+1 à +2 min/run**) sans
remettre en cause le budget de 60 appels/run.

**Coût en stockage** : nouvelle table `token_info`, une ligne par
récupération (jamais réécrite ni supprimée, donc une ré-interrogation en fin
de fenêtre de 48h pour voir l'évolution de la concentration serait une ligne
de plus, pas un écrasement). ~15-20 colonnes, mais peu de lignes (1 par pool
suivi, pas par run) → quelques dizaines de Ko/jour au régime observé.

## 6. Champs à faible valeur, non recommandés

`decimals`, `image_url`, `banner_image_url`, `coingecko_coin_id`, `websites`,
`discord_url`, `telegram_handle`, `twitter_handle`, `description`,
`categories` : purement cosmétiques/informatifs pour cet usage d'analyse
statistique, aucun impact sur le simulateur ou les métriques prévues. 🟡
reconstructibles plus tard via un appel `/tokens/{address}/info` tant que le
token est encore indexé côté source — je ne recommande pas de les stocker
maintenant.

---

## Récapitulatif des propositions, par priorité

| # | Proposition | Appels/run (régime croisière) | Stockage | Irrécupérable si ignoré |
|---|---|---|---|---|
| 1 | `locked_liquidity_percentage` + `pool_fee_percentage` (déjà dans la réponse multi-pools) | +0 | +1 colonne/run, négligeable | **Oui** (état vivant) |
| 2 | Rang tendance à chaque run (nouvelle table `trending_observations`) | +0 | ~10-13 Mo/mois | **Oui** (boîte noire sans historique) |
| 3 | `quote_token_price_usd` (prix SOL/USD à l'instant du snapshot) | +0 | +1 colonne/run, négligeable | **Oui**, et bloque la stratification par prix du SOL prévue à l'étape 3 |
| 4 | Périodes manquantes m15/m30/h6 (transactions + volume) + `price_change_percentage` complet | +0 | +21 colonnes, ~130 Mo/mois en régime de croisière estimé | Oui (agrégats liés à l'instant) |
| 5 | Corriger le parsing OHLCV pour remplir `meta.base`/`meta.quote` sur les lignes de backfill | +0 | 0 (remplit des colonnes existantes) | Oui, mais c'est un bug, pas une limite |
| 6 | `tokens/{address}/info` : score de confiance, concentration détenteurs, mint/freeze authority, honeypot, dev holding | +5 à +10 (nouvelle table) | Négligeable | Partiellement — ces champs évoluent, une lecture tardive donne un état différent de celui du moment de démarrage |

Les points 1, 2 et 3 sont, à mon avis, ceux qui correspondent le mieux à ta
consigne "priorité à ce qui est irrécupérable" : coût nul en appels, et
perdus définitivement à chaque run où ils ne sont pas capturés.

---

## Versionnement du schéma (`schema_version`)

Proposition, à valider avant implémentation :

- Une constante unique `SCHEMA_VERSION` dans `models.py`, avec un
  commentaire-journal listant ce que chaque version a changé (ex : `# v2 :
  ajout locked_liquidity_percentage, pool_fee_percentage, quote_token_price_usd`).
- Un champ `schema_version: int` ajouté **en dernière colonne** de chaque
  dataclass (`PoolSnapshot`, `PoolDiscovery`, `PoolStatusEvent`, `RunLog`),
  rempli avec la constante courante à l'écriture.
- Compatibilité déjà existante à exploiter : `build_db.py` charge déjà
  chaque ligne avec `row.get(name, "")` — un fichier CSV d'un jour antérieur
  qui n'a pas encore la colonne `schema_version` (ou les nouvelles colonnes
  métier) continue de se charger sans erreur, avec ces champs vides. Le
  fichier journalier lui-même garde son ancien en-tête (plus court) tant
  qu'il n'est pas réécrit — cohérent avec le principe "jamais réécrit".
- Pourquoi une colonne explicite plutôt que déduire la version depuis les
  colonnes présentes : une colonne vide peut vouloir dire "l'API a renvoyé
  null" OU "cette version du schéma ne capturait pas encore ce champ" —
  `schema_version` lève l'ambiguïté sans avoir à connaître la date exacte de
  chaque changement de code.
- Toute analyse à l'étape 3 pourra alors filtrer ou traiter différemment les
  lignes `schema_version=1` (avant cet audit) et `schema_version=2` (après),
  ou simplement les exclure des métriques qui dépendent d'un champ absent en
  v1 (ex : la stratification par prix du SOL ne pourra logiquement démarrer
  qu'à partir des lignes où `quote_token_price_usd` existe).

Aucun de ces changements n'a été implémenté — je n'ai touché ni au code ni
au workflow, conformément à la demande.
