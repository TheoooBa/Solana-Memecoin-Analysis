# Conception de l'étape 3 : module d'analyse, gel, test de permutation

Date : 2026-10-14. Contexte : 23 jours de collecte, 10 815 pools avec fenêtre
de suivi 48h complète (10 592 témoins, 223 tendance), couverture RugCheck
87%. Le volume n'est plus le facteur limitant (voir discussion du
2026-10-14). Ce rapport fixe les définitions avant d'écrire une ligne de
code d'analyse — approuvé point par point avec l'utilisateur avant rédaction.
Aucune implémentation n'a été faite.

## 1. Signal : le point d'entrée de référence

Pas de nouveau mécanisme de détection de seuil (ça ajouterait de la
complexité, et serait lui-même une règle à tester, pas une définition
stable). Le signal est **le moment de la découverte** :
`pool_registry.discovered_at_utc`, déjà horodaté, zéro code nouveau.

Ça sépare proprement deux questions :
- **Quand regarder** : fixe, simple, pas sujet à ajustement après coup.
- **Quoi vérifier à ce moment-là** : liquidité verrouillée, concentration
  des détenteurs, drapeaux RugCheck — c'est le terrain des règles testées
  (section 6).

## 2. Horizons d'évaluation

`h1`, `h6`, `h24` après la découverte. `m5`/`m15`/`m30` écartés : trop
bruités une fois le coût (section 3) appliqué — un mouvement de quelques
pourcents sur 5 minutes ne veut rien dire après un abattement de 3%.

Pour chaque pool et chaque horizon, le prix de référence est celui du
premier instantané disponible à `discovered_at_utc + horizon` ou après
(tolérance : jusqu'à l'instantané suivant si aucun n'existe pile à l'heure,
jamais interpolé). Un pool sans instantané disponible à cet horizon
(suivi interrompu, erreur de collecte) est exclu de ce couple
règle/horizon — jamais une valeur devinée.

## 3. Modèle de coûts

Abattement forfaitaire de **3% (aller-retour)** sur le rendement brut —
`rendement_net = (prix_horizon / prix_découverte - 1) - 0.03`.
Volontairement conservateur (plutôt que le point bas de la fourchette
2-3% évoquée) : un coût plus élevé rend plus difficile pour une règle de
« battre les coûts », cohérent avec l'hypothèse de départ négative du
projet. Affinable plus tard avec un modèle dépendant de la taille du trade
via `reserve_usd`, mais pas pour ce premier passage.

## 4. Mécanisme de gel

`config/analysis.yaml` contient les définitions exactes (règles, horizons,
coûts, seuils). Geler = calculer un hash SHA-256 du contenu du fichier et
écrire `config/analysis_freeze.json` : `{"hash": "...", "frozen_at_utc":
"..."}`. Commande dédiée (`analyze freeze`), rien d'automatique.

Après le gel :
- Données avec `discovered_at_utc` **avant** le gel : exploration
  uniquement (concevoir des règles, regarder des graphiques), jamais
  utilisées pour un verdict officiel.
- Données **après** le gel : seules celles-ci comptent pour l'évaluation
  d'une règle.
- `analyze run` recalcule le hash de `analysis.yaml` à chaque exécution et
  le compare à celui enregistré. S'ils ne correspondent pas (fichier
  modifié depuis le gel), l'outil **refuse de tourner** — oblige à regeler
  explicitly plutôt que d'évaluer silencieusement contre une règle
  retouchée après avoir vu des résultats.

## 5. Test de permutation

**Comparaison au sein du groupe témoin uniquement**, jamais tendance vs
témoin. Tendance présuppose déjà un succès (biais de sélection de
GeckoTerminal) : y tester une règle répondrait à « aide-t-elle à choisir
parmi des pools déjà en vue », pas à l'hypothèse de départ (« avant que
l'attention soit large »).

Pour une règle et un horizon donnés :
1. Sélectionner les pools témoins découverts après le gel, avec un
   instantané disponible à l'horizon visé.
2. Séparer en groupe A (respecte la règle) / groupe B (ne la respecte pas).
3. Statistique observée : écart de **médiane** du rendement net entre A et
   B (jamais la seule moyenne — voir aussi quantiles et taux de perte en
   sortie, comme déjà acté pour ce projet).
4. **Permutation par bloc de jour** : les étiquettes A/B sont mélangées
   uniquement à l'intérieur de chaque jour de découverte (pas entre jours)
   — des pools découverts le même jour partagent souvent le même régime de
   marché (prix du SOL, humeur générale), les traiter comme indépendants
   sous-estimerait l'incertitude réelle. 10 000 permutations, graine fixe
   (42, cohérence avec `univers.sampling_seed`).
5. p-value = proportion des statistiques permutées aussi extrêmes que
   l'observée (bilatérale).
6. Intervalle de confiance à 95% par **bootstrap au niveau du jour** (on
   rééchantillonne des jours entiers avec remise, pas des événements
   individuels) — même logique de clustering que la permutation.

**Limite connue, assumée** : de nombreuses règles × horizons seront testés
au total ; aucune correction formelle pour tests multiples (Bonferroni,
FDR) n'est appliquée dans ce premier passage. Le registre (section 6) rend
visible le nombre total de tests effectués, ce qui permet au moins de
juger un résultat positif isolé avec la prudence qui s'impose — une vraie
correction pourra être ajoutée si ça devient un problème concret.

## 6. Registre de toutes les règles testées

Une ligne par couple (règle, horizon) évalué, **jamais effacée**, qu'elle
ait « marché » ou non :

| Colonne | Contenu |
|---|---|
| `rule_id` | identifiant de la règle dans `analysis.yaml` |
| `horizon_hours` | 1, 6 ou 24 |
| `n_events_a`, `n_events_b` | effectifs des deux groupes |
| `n_distinct_days` | jours de découverte distincts couverts (groupe A) |
| `median_net_return_a`, `median_net_return_b` | rendements nets médians |
| `win_rate_a`, `win_rate_b` | part de rendements nets positifs |
| `p_value` | test de permutation par bloc de jour |
| `ci_low`, `ci_high` | IC95% par bootstrap journalier |
| `verdict` | `donnees_insuffisantes` \| `ne_bat_pas_le_hasard` \| `bat_le_hasard` |
| `frozen_config_hash` | traçabilité vers la version gelée évaluée |
| `evaluated_at_utc` | horodatage de l'évaluation |

## 7. Seuil minimum

`min_events: 100` **ET** `min_distinct_days: 10` pour le groupe A (règle
respectée). En dessous de l'un ou l'autre : `verdict = donnees_insuffisantes`,
quoi que dise la statistique — empêche un résultat porté par la vague d'un
seul jour exceptionnel d'être présenté comme un signal.

## Ébauche de `config/analysis.yaml`

```yaml
signal:
  reference: "discovered_at_utc"

horizons_hours: [1, 6, 24]

costs:
  round_trip_pct: 3.0

permutation_test:
  n_permutations: 10000
  cluster_by: "day"
  random_seed: 42
  confidence_level: 0.95

minimum_sample:
  min_events: 100
  min_distinct_days: 10

rules:
  - id: "liquidite_verrouillee_90"
    description: "Liquidité verrouillée > 90% à la découverte"
    type: "technique"
    field: "locked_liquidity_pct"
    condition: "> 90"

  - id: "rugcheck_sain"
    description: "RugCheck : non signalé rugged, pas de permanent delegate, pas de transfer hook"
    type: "securite"
    condition: "rugcheck.rugged == false and rugcheck.permanent_delegate_present == false and rugcheck.transfer_hook_present == false"

  - id: "rugcheck_score_faible"
    description: "Score de risque RugCheck normalisé < 30"
    type: "securite"
    field: "rugcheck.score_normalised"
    condition: "< 30"

  - id: "faible_concentration_top10"
    description: "Détenteurs — top 10 < 50% (RugCheck top_holder_pct cumulé, à défaut token_info)"
    type: "technique"
    field: "rugcheck.top_holder_pct"
    condition: "< 50"
```

Point d'attention honnête : les règles qui dépendent de `token_info`
(GeckoTerminal) auront un effectif très réduit (1 088 pools couverts sur
10 815, contrainte de budget documentée) — probablement
`donnees_insuffisantes` pour un moment. Les règles RugCheck (87% de
couverture) sont les seules avec assez de volume pour un verdict
exploitable dès ce premier gel.

## Ce que ça donne comme code (pour la prochaine étape, pas encore écrit)

- `config/analysis.yaml` (ébauche ci-dessus, à affiner/étendre avant le gel)
- Nouveau paquet `src/smc_analysis/` (séparé de `smc_collector`, même
  logique de séparation des responsabilités que le reste du projet) :
  `freeze.py` (hash + écriture de `analysis_freeze.json`), `rules.py`
  (évaluation d'une condition sur une ligne de données jointe
  snapshot+rugcheck+token_info), `permutation.py` (test par bloc de jour,
  bootstrap journalier), `evaluate.py` (orchestration : sélection des
  pools, calcul des rendements nets, écriture du registre).
- Deux sous-commandes CLI : `analyze freeze` et `analyze run`.
- Tests unitaires sur chaque brique (évaluation de condition, permutation
  par bloc, bootstrap, seuils minimums) avant tout calcul sur les vraies
  données — même rigueur que `token_info.py`/`rugcheck.py`.

En attente de ton accord sur ce document avant d'écrire le code.
