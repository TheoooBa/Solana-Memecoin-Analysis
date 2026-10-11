# Trading squad : 4 agents Claude en groupchat, en paper trading

Un scout, un news bot, un chart bot et un skeptic se passent la parole sur chaque actif qui bouge fort. Le skeptic tranche buy, wait ou skip. Un desk en code (pas un LLM) applique les règles de risque dures. Tout est simulé : aucun ordre réel n'existe dans ce projet, et il ne faut pas en ajouter avant d'avoir des mois de résultats mesurés.

Le vrai sujet n'est pas de savoir si ça fait de beaux messages, mais si ça fait mieux que le hasard. Tout est construit pour répondre à ça : chaque plan est journalisé, rejoué sur les vraies bougies qui suivent, et comparé à une règle bête sans LLM qui tourne sur les mêmes données.

## Installation

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python test_core.py
```

Python 3.9 ou plus. Les tests tournent sans clé ni réseau.

## Démo sans clé ni réseau (2 minutes)

```
python run.py --market mock --dry-run --replay 60 --step-hours 3 --fresh
python evaluate.py --market mock
```

Le marché mock est synthétique : chocs de prix, titres qui arrivent avant ou après le choc, régimes de momentum ou de retour à la moyenne. Ça valide la tuyauterie, pas une stratégie. Les résultats du mock ne disent rien sur un vrai marché.

## Avec Claude

Teste d'abord les vrais prompts sur le mock, ça coûte quelques centimes :

```
python run.py --market mock --replay 8 --step-hours 6 --fresh
```

Puis en réel, un cycle à la fois :

```
python run.py --market crypto --once
python run.py --market stocks --once --ignore-hours
```

Puis en boucle 24/7 (crypto) ou pendant les heures de bourse US (actions) :

```
nohup python run.py --market crypto > crypto.log 2>&1 &
nohup python run.py --market stocks > stocks.log 2>&1 &
```

Les actions US sont ouvertes de 21h30 à 04h00 heure de Singapour. Le programme dort tout seul quand le marché est fermé (jours fériés non gérés).

## Ce qui tourne à chaque cycle

Un script classe les actifs par taille anormale du mouvement des 4 dernières heures (en écarts-types de l'historique de l'actif) et par volume. Le scout choisit au plus 3 actifs dans le haut du classement. Pour chacun, le news bot lit les titres Google News avec leur âge, le chart bot propose un plan long (entrée, stop, cible), le code vérifie mécaniquement ce plan, puis le skeptic donne son verdict en voyant tout le fil. Le desk refuse si le portefeuille est plein (5 positions). Un actif analysé n'est pas ré-analysé pendant 6 h (8 h pour les actions).

Si un agent répond n'importe quoi, le système échoue du côté prudent : pas de plan, ou skip. Une panne réseau sur un actif n'arrête pas la boucle.

## Deux variantes sur les mêmes données

La variante claude fait tourner les quatre agents LLM. La variante rules est une règle de 20 lignes sans LLM (achat sur repli d'un demi ATR après une hausse, validation si le volume confirme). Elle tourne à côté, gratuitement, sur les mêmes bougies. Si Claude ne la bat pas nettement, l'escouade ne sert à rien, et c'est une information utile. Désactivable avec `--no-baseline`.

Par défaut les trois premiers bots utilisent `claude-haiku-5-5` et le skeptic `claude-sonnet-5-5`, parce qu'un skeptic du même modèle que ceux qu'il challenge partage leurs angles morts. Modifiable avec `SQUAD_MODEL` et `SQUAD_SKEPTIC_MODEL`.

## Lire les résultats

```
python evaluate.py --market crypto --horizon 24
```

Les R sont des multiples du risque initial, frais inclus : +1R veut dire que tu as gagné ce que tu risquais. Le tableau compare quatre groupes par variante : tous les plans (comme s'il n'y avait pas de skeptic), ceux que le skeptic valide, ceux qu'il refuse, et le portefeuille réel du desk. La question clé est la ligne du skeptic : si les plans validés ne font pas mieux que les refusés, ce bot est décoratif. Le tableau donne aussi la statistique t de la comparaison. Sous 2 en valeur absolue, l'écart est compatible avec le hasard. En dessous de 30 plans par groupe, ne conclus rien.

`--csv fichier.csv` exporte chaque plan avec son résultat. Le coût LLM réel (appels et tokens par cycle) est dans le journal et affiché en bas du rapport.

## Règles de la simulation

Seules les bougies qui commencent après la décision comptent. Entrée au marché si le plan est à moins de 0,1 % du prix, sinon ordre limite ou stop valable 12 h en crypto et 8 h en actions. Si le stop et la cible sont touchés dans la même bougie, le stop passe en premier. Les gaps sont subis au prix d'ouverture. Frais et slippage aller-retour : 0,2 % en crypto, 0,06 % en actions. Sortie forcée au bout de 7 jours. Tout se règle dans `config.py`.

## Limites à garder en tête

Rien ne prouve qu'un LLM lisant des titres et des bougies horaires batte le marché : quand le scout voit le mouvement, il a déjà eu lieu, et le news bot est très bon pour fabriquer une explication après coup. Quelques semaines de résultats sont du bruit : avec 50 plans, une moyenne de +0,3R reste compatible avec zéro. La crypto est ouverte 24h/24 mais les titres Google News sont peu fiables sur les petites pièces. yfinance n'est pas une API officielle et peut renvoyer des trous. Le plafond de 1500 appels LLM par 24 h (`--max-calls-day`) est là pour éviter une facture surprise si la boucle s'emballe.

Les connexions réelles à Binance et à yfinance n'ont pas pu être testées depuis l'environnement où le code a été écrit (pas de réseau externe) : le parsing est testé sur des réponses simulées au bon format, mais le premier `--once` réel est le vrai test. S'il échoue, le message d'erreur est affiché et le cycle suivant réessaie.

## Fichiers

`run.py` lance, `evaluate.py` juge, `config.py` règle. `agents.py` contient les quatre prompts. `squad.py` orchestre un cycle et applique les règles du desk. `paper.py` simule les trades. `providers.py` gère les trois sources de données. `rules.py` est la règle bête. `journal.py` est la base SQLite dans `data/`.
