# Guide : cron externe pour déclencher la collecte toutes les 5 minutes

## Pourquoi

Vérifié dans la documentation officielle GitHub (2026-09-29) : l'événement
`schedule` (notre cron actuel, `*/5 * * * *`) porte un avertissement
explicite — *"peut être retardé en période de forte charge"*. L'événement
`workflow_dispatch` (déclenchement via API) n'a **aucun** avertissement de ce
type. Mesuré sur notre propre dépôt : le cron interne tourne en moyenne
toutes les **~4h**, pas toutes les 5 minutes.

Le principe : un service de minuteur externe (pas GitHub) appelle l'API
GitHub à intervalle réel de 5 minutes pour déclencher le workflow via
`workflow_dispatch`, qui n'est pas soumis au même best-effort.

**Aucune modification du code n'est nécessaire** : `.github/workflows/collect.yml`
supporte déjà `workflow_dispatch` (ajouté dès la création du workflow, pour
les tests manuels). On garde aussi le `schedule:` existant en secours — s'il
se déclenche en plus, ça ne fait qu'ajouter une collecte de plus, sans
conflit (le `concurrency` du workflow gère déjà ça).

## Ce que tu dois faire toi-même (compte + jeton — je ne peux pas le faire à ta place)

### 1. Créer un jeton d'accès GitHub, avec le minimum de droits nécessaire

Va sur [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new)
(jeton **fine-grained**, plus sûr qu'un jeton classique — limité à un seul
dépôt et à une seule permission) :

- **Resource owner** : ton compte (TheoooBa)
- **Repository access** : "Only select repositories" → choisis
  `Solana-Memecoin-Analysis`
- **Permissions** → **Repository permissions** → trouve **"Actions"** →
  mets-la sur **"Read and write"** (c'est la seule permission nécessaire,
  vérifiée dans la doc officielle GitHub — pas besoin de toucher à autre chose)
- **Expiration** : à toi de voir (90 jours par défaut ; au-delà, il faudra le
  renouveler et remettre à jour cron-job.org)
- Clique **Generate token**, et **copie-le immédiatement** (GitHub ne le
  raffiche plus jamais après).

### 2. Créer un compte sur cron-job.org (gratuit)

[console.cron-job.org](https://console.cron-job.org/signup) — inscription
standard, aucune carte bancaire.

### 3. Créer le job planifié

Dans le tableau de bord cron-job.org, **Create cronjob** :

- **Title** : `Solana Memecoin Analysis - collecte`
- **URL** :
  ```
  https://api.github.com/repos/TheoooBa/Solana-Memecoin-Analysis/actions/workflows/collect.yml/dispatches
  ```
- **Schedule** : toutes les 5 minutes
- **Request method** : `POST`
- **Headers** (section "Advanced" ou "Headers") — ajoute ces trois lignes :
  | Nom | Valeur |
  |---|---|
  | `Authorization` | `Bearer <colle ton jeton ici>` |
  | `Accept` | `application/vnd.github+json` |
  | `X-GitHub-Api-Version` | `2022-11-28` |
- **Request body** (section "Body" / "Payload"), type JSON :
  ```json
  {"ref": "main"}
  ```

Sauvegarde et active le job.

### 4. Vérifier que ça marche

Sur cron-job.org, force une exécution manuelle du job ("Execute now" ou
équivalent) et regarde le code de réponse : **204** = succès (déclenché sans
contenu de retour, comportement normal de cet endpoint). Une erreur 401
signifie un jeton invalide/mal copié ; une 404 signifie un mauvais nom de
dépôt ou de fichier de workflow.

Ensuite va sur l'onglet [Actions](https://github.com/TheoooBa/Solana-Memecoin-Analysis/actions)
de GitHub : un nouveau run "Collecte GeckoTerminal" doit apparaître, déclenché
`via workflow_dispatch` (visible dans le détail du run) plutôt que
`Scheduled`.

## Comment vérifier, dans quelques jours, que la cadence s'est vraiment améliorée

```bash
cd ~/Documents/solana-memecoin-analysis
git fetch origin data
git show origin/data:logs/runs/$(date -u +%Y-%m-%d).csv | tail -20
```

Regarde l'écart entre les colonnes `started_at_utc` de deux lignes
consécutives : ça doit se rapprocher de 5 minutes au lieu des ~4h observées
jusqu'ici.

## Limites et risques connus

- **Le jeton expire** (90 jours par défaut si tu ne changes rien) : au-delà,
  cron-job.org recevra des 401 et arrêtera de déclencher la collecte,
  silencieusement de ton côté (aucune alerte automatique n'est en place). À
  surveiller manuellement, ou on pourra construire une alerte plus tard.
- **cron-job.org reste un service tiers gratuit** : pas de garantie contractuelle
  de disponibilité. S'il tombe, on retombe sur le `schedule:` natif (donc
  jamais pire qu'aujourd'hui, juste pas mieux temporairement).
- **Le budget d'appels par run (60) et le débit (8/min) n'ont pas changé** :
  passer à des runs toutes les 5 minutes ne change rien à ces limites, juste
  à la fréquence à laquelle on les sollicite.
