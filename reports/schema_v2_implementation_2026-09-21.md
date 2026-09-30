# Implémentation schéma v2 — rapport court (2026-09-21)

Périmètre : exactement la liste approuvée le 2026-09-21 (voir demande utilisateur).
Rien poussé sur `main` ni `data` par moi-même.

## Fait

- **Instantanés** : `locked_liquidity_pct`, `pool_fee_pct`, `quote_token_price_usd`,
  périodes m15/m30/h6 complètes (transactions + volume), `price_change_pct`
  (6 périodes). Zéro appel supplémentaire, confirmé (calls_made comparable
  avant/après pour la partie instantanés).
- **Rang tendance et nouveaux pools** : nouvelle table `trending_ranks` et
  `new_pool_ranks`, une ligne par pool par exécution, zéro appel
  supplémentaire (déjà récupérées pour la découverte).
- **Info token** : `/tokens/{address}/info` appelé une fois par pool, à sa
  découverte. Score de confiance, tranches de détenteurs, autorités
  mint/freeze, part développeur, `is_honeypot` stocké tel quel (jamais
  interprété — vérifié : "unknown" reste "unknown"), réponse brute conservée
  quand < 5 Ko (mesuré : ~1,4-1,5 Ko en pratique, donc systématiquement
  conservée).
- **Garde-fous** : plafond 10 appels/run, coupure à 3 minutes d'exécution
  écoulée, 3 échecs → indisponible, coupe-circuit sur 401/402/403 ou 3 échecs
  consécutifs — tous testés unitairement avec un faux client.
- **Versionnement** : `schema_version=2` sur les nouvelles lignes. **Bug
  trouvé et corrigé pendant la vérification** (pas dans la liste initiale,
  mais nécessaire pour que le reste fonctionne) : `AppendOnlyCsvWriter`
  ajoutait des lignes au nouveau schéma dans le fichier du jour déjà existant
  (en-tête v1, plus court) sans vérifier la correspondance — ça désalignait
  silencieusement les colonnes. Corrigé : si l'en-tête du fichier existant ne
  correspond plus au schéma courant, un nouveau fichier suffixé
  (`AAAA-MM-JJ_v2.csv`) est créé, sans jamais toucher le fichier existant.
- `build_db.py` : nouvelles tables, types SQLite, testé avec un mélange réel
  de fichiers v1/v2 côte à côte (`tests/test_build_db.py`).

## Pas fait (hors périmètre demandé, non implémenté)

- Second appel info plus tard dans la vie d'un pool.
- Endpoint top holders (confirmé verrouillé, 401 sans clé payante — pas
  tenté de contournement).
- Réseaux sociaux, analyse, tableau de bord.
- Rétro-remplissage de `base_token_address` pour les ~34 pools déjà suivis
  avant aujourd'hui : ils n'auront jamais d'info token sous ce design (voir
  limite ci-dessous).

## Vérifications effectuées

- 76 tests unitaires (dont 31 nouveaux), tous verts.
- Essai réel contre l'univers de production actuel (34 pools, copié
  isolément, jamais écrit sur les vraies données) : succès, `exit_reason`
  toujours `completed`, aucune régression sur le socle prix/liquidité/volume.
- Durée mesurée : **90,5 s** pour un run typique (13 appels, 3 nouvelles
  découvertes, dont 3 appels info-token avec retries 429 absorbés). **Bien
  sous les 3 minutes.**

## Croissance mensuelle — seuil dépassé, décision nécessaire

Mesuré précisément par ligne (pas par estimation globale) :

| Poste | Surcoût mesuré | Projection mensuelle (cadence 5 min, ~4 découvertes/run observées) |
|---|---|---|
| Instantané (+36 octets/ligne, +8%) | mesuré | ~11 Mo/mois |
| `trending_ranks` + `new_pool_ranks` (40 lignes/run fixes) | mesuré | ~31 Mo/mois |
| `token_info` (réponse brute incluse, ~1,5 Ko/ligne) | mesuré | ~62 Mo/mois |
| Registre (`base_token_address` + `schema_version`) | mesuré | ~3,5 Mo/mois |
| **Total estimé** | | **~108 Mo/mois** |

**Ça dépasse ton seuil de 100 Mo/mois.** Comme demandé, je ne l'ai pas fait
passer en douce — je te le signale et je propose une réduction plutôt que
d'agir seul.

Le poste dominant est la réponse brute de `token_info` (~62 Mo/mois, plus de
la moitié du total) : en pratique, chaque réponse fait 1,4-1,5 Ko, donc
systématiquement sous ton seuil de 5 Ko — la condition ne filtre presque
jamais rien, tout est conservé.

**Réduction proposée (non appliquée)** : ne plus conserver la réponse brute
de `token_info` du tout, garder uniquement les champs déjà parsés (score,
tranches, autorités, part développeur, honeypot — donc rien de perdu sur ce
qui a une valeur d'analyse). Ça ramènerait le total estimé à **~58 Mo/mois**,
sous le seuil. Alternative : garder la réponse brute mais accepter le
dépassement (108 Mo/mois reste très loin des limites techniques réelles de
GitHub sur un dépôt public — c'est ton seuil à toi qui est dépassé, pas une
limite de la plateforme).

Cette estimation suppose un rythme de découverte stable (~4 nouveaux
pools/run) — voir hypothèses non vérifiées ci-dessous.

**Je n'ai rien changé à `max_raw_json_bytes` en attendant ta décision.**

## Hypothèses non vérifiées

- Le rythme de découverte de nouveaux pools (~4/run) est extrapolé sur 3
  points de mesure réels (22, 6, 3) qui décroissent encore ; la vraie valeur
  stable pourrait être plus basse (croissance moindre) ou se stabiliser plus
  haut si le renouvellement de `new_pools` reste élevé.
- Le nombre de pools actifs simultanément suivis va croître avec le temps
  (fenêtre de 48h) : c'est une caractéristique du design déjà existant
  (identique en v1), pas quelque chose que le changement d'aujourd'hui
  aggrave en proportion — mais ça amplifiera les chiffres ci-dessus en
  valeur absolue avec le temps, dans les deux schémas.
- Les ~34 pools suivis avant aujourd'hui n'auront jamais d'info token
  (adresse du token de base jamais enregistrée pour eux) — assumé
  acceptable, pas vérifié avec toi explicitement dans la demande initiale.
- `is_honeypot` a valu "unknown" sur les 3 tokens réels testés : le champ
  existe mais semble peu renseigné en pratique sur des tokens très récents.

## Commandes pour toi

```bash
cd ~/Documents/solana-memecoin-analysis
git add config/collection.yaml src/smc_collector/ tests/ reports/
git status   # vérifie ce qui va être committé
git commit -m "Schéma v2 : instantanés enrichis, rangs tendance, info token, versionnement"
git push origin main
```

## Vérifier demain que la branche `data` reçoit bien les nouvelles colonnes

```bash
git fetch origin data
git show origin/data:raw/snapshots/$(date -u +%Y-%m-%d)_v2.csv | head -1
git show origin/data:raw/token_info/$(date -u +%Y-%m-%d).csv 2>/dev/null | head -3
```
(Le nom `_v2.csv` n'apparaîtra que le premier jour de la transition ; les
jours suivants, le fichier journalier normal aura directement le bon en-tête.)

## Retour arrière si les exécutions échouent

```bash
git revert HEAD --no-edit
git push origin main
```
Aucune donnée déjà collectée n'est perdue par ce retour arrière : les
fichiers `_v2.csv` déjà poussés sur `data` restent lisibles (ancien code),
simplement plus alimentés tant que le revert n'est pas lui-même annulé.
