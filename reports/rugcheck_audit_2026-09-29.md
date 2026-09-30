# Audit : API RugCheck.xyz pour une vraie détection anti-honeypot

Date : 2026-09-29. Contexte : le champ `is_honeypot` de GeckoTerminal renvoie
presque systématiquement "unknown" en pratique (vérifié sur plusieurs tokens
réels lors de l'audit du schéma v2). Recherche documentée : début 2026, une
vague d'arnaques automatisées sur Solana a exploité l'extension Token-2022
"Permanent Delegate" (+50M$ de pertes estimées au T1 2026 selon plusieurs
sources techniques) — un vecteur que ni GeckoTerminal ni notre schéma actuel
ne détectent. Méthode : appels réels à l'API publique RugCheck, aucune
implémentation touchée.

## L'API en résumé

- **Endpoint** : `GET https://api.rugcheck.xyz/v1/tokens/{mint}/report`
- **Authentification** : aucune, testé et confirmé (200 OK sans clé ni en-tête)
- **Débit** : en-tête `x-rate-limit-limit: 15` présent, mais la fenêtre
  temporelle (par minute ? par heure ?) n'est pas précisée. Trois appels
  rapprochés ont fait passer `x-rate-limit-remaining` de 14 à 14 à 13 — pas
  assez de signal pour déduire la fenêtre exacte. **À traiter avec la même
  prudence que GeckoTerminal à l'origine** : débit conservateur côté client,
  reprises avec délai, coupe-circuit en cas d'échecs répétés.
- **Taille de réponse** : ~11,7 Ko sur un token ordinaire sans risque détecté
  — **plus de 2x notre seuil de 5 Ko déjà retenu pour `token_info`**. Garder
  la réponse brute intégrale coûterait donc bien plus cher ici ; voir
  proposition de colonnes ciblées plus bas plutôt qu'un blob brut.

## Champs disponibles, par valeur

### 🔴 Directement ce qu'on cherchait (absent de tout ce qu'on a aujourd'hui)

| Champ | Contenu | Pourquoi c'est important |
|---|---|---|
| `token_extensions.permanentDelegate` | Adresse si présente, sinon `null` | Le créateur peut déplacer/brûler les tokens de n'importe quel portefeuille à tout moment — vecteur d'arnaque dominant en 2026 |
| `token_extensions.transferHook` | Adresse si présente, sinon `null` | Un programme personnalisé s'exécute à chaque transfert et peut faire échouer les ventes à volonté — un honeypot au sens propre |
| `token_extensions.pausableConfig` | Présent/absent | "Interrupteur pause" : le créateur peut geler tous les transferts |
| `token_extensions.defaultAccountState` | Valeur (ex: "frozen") | Si les nouveaux comptes démarrent gelés, c'est un signal fort |
| `transferFee.pct` | Pourcentage de taxe sur chaque transfert | Détecte les taxes cachées excessives |
| `risks` | Liste de risques nommés individuellement | Sortie directe du moteur de 30+ vérifications de RugCheck |
| `score` / `score_normalised` | Score de risque agrégé | Utile comme tri/filtre rapide, jamais comme verdict unique |
| `rugged` | Booléen | Classification "déjà arnaque confirmée" par RugCheck lui-même |
| `graphInsidersDetected` + `insiderNetworks` | Nombre + détail de réseaux de wallets coordonnés | Détecte la distribution artificielle (faux holders liés entre eux) |

### 🟡 Recoupement utile avec ce qu'on a déjà (schéma v2, pas encore déployé)

| Champ | Déjà chez nous via | Valeur ajoutée |
|---|---|---|
| `mintAuthority` / `freezeAuthority` | GeckoTerminal `token_info` | Vérification croisée entre deux sources indépendantes |
| `topHolders` (20 adresses, % chacune, flag "insider") | GeckoTerminal donne des tranches agrégées (top10/11-20/21-40/reste) | RugCheck est plus précis (par adresse) mais coûterait cher à stocker intégralement — proposition : n'en garder qu'un résumé (voir plus bas) |
| `markets[].lp.lpLockedPct` | GeckoTerminal `locked_liquidity_percentage` | Deuxième source pour le même signal — utile si l'une des deux s'avère peu fiable en pratique |
| `creator` / `creatorBalance` | GeckoTerminal `developer_address` / `developer_holding_percentage` | Recoupement |

### ⚪ Faible valeur pour ce projet — non recommandé de stocker

`knownAccounts` (cartographie de comptes de l'écosystème, pas spécifique au
token), `events` (vide dans nos tests), `fileMeta`/`tokenMeta` au-delà de
`mutable`, `price` (redondant), `launchpad`/`deployPlatform` (déjà déductible
du DEX suivi).

## Proposition de colonnes (nouvelle table `rugcheck_info`, même schéma que `token_info` : un appel par pool, à sa découverte)

- `pool_address`, `token_address`, `network`, `requested_at_utc`
- `score`, `score_normalised`
- `rugged` (booléen — c'est un verdict déjà calculé par RugCheck qu'on
  enregistre tel quel, pas une interprétation de notre part)
- `risks_count`, `risks_json` (liste complète, généralement courte)
- `permanent_delegate_present`, `transfer_hook_present`,
  `pausable_present`, `mint_close_authority_present`, `non_transferable`
  (tous booléens)
- `default_account_state` (texte brut, ex: "frozen"/`null`)
- `transfer_fee_pct`
- `lp_locked_pct`, `locker_scan_status`
- `graph_insiders_detected`
- `top_holder_pct` (le plus gros détenteur), `insider_holders_count`,
  `insider_holders_pct_sum` (résumé du réseau d'insiders détecté, sans
  stocker les 20 adresses individuelles — trop coûteux pour la valeur
  ajoutée réelle ici)
- `creator`, `creator_balance`, `token_program` (legacy SPL vs Token-2022 —
  seul Token-2022 peut porter les extensions dangereuses ci-dessus)
- `metadata_mutable`
- **Pas de réponse brute conservée** (contrairement à `token_info`) : la
  taille (~12 Ko) rend le coût disproportionné par rapport à la valeur des
  champs non retenus ci-dessus.

## Coût estimé

- **Appels** : +1 appel par pool nouvellement découvert (même moment que
  `token_info`), donc même ordre de grandeur que ce qu'on avait déjà chiffré
  : ~4-8 appels/run en régime observé. Budget de 60/run largement suffisant
  avec les deux endpoints combinés.
- **Stockage** : ligne d'une vingtaine de colonnes scalaires + une petite
  liste JSON (`risks_json`, généralement vide ou courte) → du même ordre de
  grandeur que `token_info` sans sa réponse brute, soit quelques dizaines de
  Ko/jour au rythme actuel. Négligeable.

## Garde-fous à reprendre (même logique que `token_info`)

- Plafond d'appels par run (paramètre de config séparé, ex:
  `rugcheck.max_calls_per_run`).
- Coupure si le run dépasse un temps total donné.
- 3 échecs pour un pool → "indisponible", jamais bloqué indéfiniment.
- Coupe-circuit si erreurs d'authentification/quota en série — avec cette
  API, il faut aussi gérer explicitement le cas où **le token n'est pas
  encore indexé** par RugCheck (probable sur des pools très récents,
  contrairement à `token_info` qui a mieux répondu sur des tokens jeunes lors
  des tests) : ce cas ne doit pas compter comme un "échec" au sens strict,
  mais être distingué (ex: 404 → à retenter au run suivant, pas
  définitivement indisponible après 3 essais si c'est juste "pas encore
  indexé").
- Respect du débit observé (15 requêtes, fenêtre non confirmée) — démarrer
  prudent (ex: même 8/min que GeckoTerminal) plutôt que de présumer une
  fenêtre favorable.

## Ce qui reste incertain

- La fenêtre exacte du rate limit (15 par minute ? par heure ?) — pas
  déterminable depuis l'extérieur sans consommer tout le quota pour observer
  la réinitialisation, ce que je n'ai pas fait pour ne pas gaspiller le quota
  avant l'implémentation réelle.
- La fiabilité de RugCheck sur des tokens très récents (< quelques minutes)
  n'a pas été testée spécifiquement — nos deux exemples testés avaient déjà
  quelques jours d'existence.
- Aucune garantie que RugCheck reste gratuit indéfiniment (même réserve que
  pour `token_info` sur CoinGecko) — même stratégie de coupe-circuit
  recommandée.

Aucune implémentation n'a été faite. En attente de ton accord sur les
colonnes proposées avant de coder.
