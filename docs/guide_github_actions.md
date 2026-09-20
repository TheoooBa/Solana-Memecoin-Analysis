# Guide : mettre en place la collecte automatique sur GitHub Actions

Ce guide est à suivre **par toi-même**, à la main. Rien n'est créé ou poussé
automatiquement à ta place : ni compte, ni dépôt, ni push. Chaque commande
est à copier-coller dans ton terminal, depuis le dossier du projet
(`~/Documents/solana-memecoin-analysis`).

## 0. Pourquoi une branche `data` séparée ?

Le code (Python, config, tests) vit sur la branche `main`. Les données
collectées vivent sur une branche `data` distincte, dont la racine contient
directement `raw/`, `logs/` (pas de sous-dossier `data/` imbriqué — cette
branche EST le dossier data). Ça évite de polluer l'historique du code avec
des milliers de commits automatiques, et ça permet de cloner uniquement le
code sans télécharger des mois de CSV.

## 1. Créer le dépôt GitHub (à la main)

Sur [github.com/new](https://github.com/new) : crée un dépôt **public**,
sans README ni .gitignore générés automatiquement (le projet en a déjà).
Note son URL, par exemple `https://github.com/<ton-compte>/solana-memecoin-analysis.git`.

## 2. Pousser le code sur `main`

```bash
cd ~/Documents/solana-memecoin-analysis
git remote add origin https://github.com/<ton-compte>/solana-memecoin-analysis.git
git push -u origin main
```

## 3. Créer la branche `data` (une seule fois, à la main)

```bash
git checkout --orphan data
git rm -rf . > /dev/null
mkdir -p raw logs
touch raw/.gitkeep logs/.gitkeep
echo "cache/" > .gitignore
git add .
git commit -m "Initialisation de la branche de données"
git push -u origin data
git checkout main
```

Après ça, ton dépôt a deux branches : `main` (le code) et `data` (vide pour
l'instant, prête à recevoir les CSV).

## 4. Autoriser les workflows à pousser des commits

Sur GitHub : **Settings → Actions → General → Workflow permissions**, coche
**"Read and write permissions"**, puis Sauvegarder. Le fichier
`.github/workflows/collect.yml` déclare déjà `permissions: contents: write`,
mais ce réglage du dépôt doit aussi l'autoriser — sans lui, le `git push`
échoue avec une erreur de permission.

## 5. (Optionnel) Ajouter une clé Demo CoinGecko

Complètement facultatif : la collecte fonctionne sans, via la surface API
publique `api.geckoterminal.com/api/v2` qui ne demande aucun compte. Si tu
veux quand même une clé Demo (gratuite, limites plus hautes) :

1. Crée un compte CoinGecko et récupère une clé Demo depuis leur tableau de
   bord développeur (aucune carte bancaire requise pour le plan Demo).
2. Sur GitHub : **Settings → Secrets and variables → Actions → New
   repository secret**, nom `GECKOTERMINAL_API_KEY`, colle la clé.
3. Le workflow la lit automatiquement (`secrets.GECKOTERMINAL_API_KEY`) et la
   passe à `collect` via la variable d'environnement du même nom.

## 6. Déclencher un premier run manuel (pour vérifier avant d'attendre le cron)

Sur GitHub : onglet **Actions → Collecte GeckoTerminal → Run workflow**.
Suis les logs en direct. Si tout va bien, tu dois voir un nouveau commit
apparaître sur la branche `data` dans les minutes qui suivent.

Pour le vérifier en local :

```bash
git fetch origin data
git log origin/data --oneline -5
```

## 7. Le cron `*/5 * * * *` prendra ensuite le relais automatiquement

Aucune autre action n'est nécessaire. GitHub déclenchera le workflow toutes
les 5 minutes tant que le dépôt reste actif (voir risque ci-dessous).

## Risques connus pour la fiabilité (vérifiés dans la doc officielle GitHub, 2026-09-21)

1. **Désactivation automatique après 60 jours d'inactivité — le risque #1.**
   GitHub désactive silencieusement les workflows planifiés d'un dépôt public
   si la **branche par défaut** (`main`) ne reçoit **aucun commit** pendant
   60 jours. **Les commits automatiques sur la branche `data` ne comptent
   PAS** pour cette règle — seule une activité sur `main` la reset. Comme la
   collecte n'écrit jamais sur `main`, elle s'arrêtera d'elle-même après 60
   jours si tu ne fais rien. **Mitigation manuelle** : pousse n'importe quel
   petit commit sur `main` de temps en temps (par exemple une mise à jour du
   README), au moins une fois tous les 60 jours. Mets-toi un rappel.
2. **Le cron n'est pas garanti à la seconde près.** GitHub documente que les
   workflows planifiés peuvent être retardés, en particulier en cas de forte
   charge sur leur infrastructure ou pile d'événements planifiés. Certaines
   exécutions à `*/5 * * * *` peuvent donc être sautées ou décalées de
   quelques minutes. C'est pour ça que `data/logs/runs/` journalise chaque
   run : `build-db` puis une requête sur `collector_runs` permettent de
   repérer les trous réels plutôt que de les supposer.
3. **Un run peut dépasser 5 minutes.** Avec un débit volontairement prudent
   (voir README, "Débit observé"), une collecte avec beaucoup de nouvelles
   découvertes peut prendre plus de 5 minutes. Le paramètre `concurrency`
   du workflow met alors le run suivant en file au lieu de le lancer en
   parallèle (ce qui casserait les commits sur `data`) — pas de perte de
   données, juste un intervalle réel parfois supérieur à 5 minutes.
4. **Minutes gratuites** : aucune limite ni coût sur un dépôt **public**
   avec les runners hébergés par GitHub. Si tu passais un jour ce dépôt en
   privé, la limite gratuite est de 2 000 minutes Linux/mois (plan Free) —
   largement dépassée par un run toutes les 5 minutes en continu.

## Vérifier que ça tourne, dans la durée

```bash
git fetch origin data
git checkout data
python -m smc_collector.cli build-db --db-path /tmp/verif.sqlite3
sqlite3 /tmp/verif.sqlite3 "SELECT started_at_utc, exit_reason, calls_made, errors_count FROM collector_runs ORDER BY started_at_utc DESC LIMIT 10;"
git checkout main
```
