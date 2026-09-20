# Rapport : disponibilité de l'historique OHLCV pour des pools morts

Généré le 2026-09-20T22:47:31.119134+00:00. Source : GeckoTerminal (attribution requise, voir README).

Définition utilisée UNIQUEMENT pour ce diagnostic ponctuel (pas une définition d'analyse gelée) : un pool est considéré « mort » si sa réserve de liquidité déclarée est ≤ 1.0 $ peu après sa création.

Pools sondés : 5

- Toujours résolvables via l'endpoint multi-pools : 5/5
- Avec au moins une bougie OHLCV disponible : 5/5

| Adresse | Créé le | Réserve $ | Volume 24h $ | Résolvable | Bougies OHLCV | Plage temporelle |
|---|---|---|---|---|---|---|
| 8YffrP1xhkHz16ZNSPpXXpnH2XMjAAaRittYkPWPMGvw | 2026-09-20T22:45:04Z | 0.0 | 1218.4513933863 | oui | 1 | 2026-09-20T22:45:00+00:00 → 2026-09-20T22:45:00+00:00 |
| 7VXDbSKhHCM3eBWdjbU6cTnNRyoRrZYDjTMpfKkeXihH | 2026-09-20T22:44:46Z | 0.0 | 0.009931462191 | oui | 1 | 2026-09-20T22:40:00+00:00 → 2026-09-20T22:40:00+00:00 |
| DghsThSVcSyHyHHZ2ymAs4KFLeV4jrTKbB6mXqRajtKX | 2026-09-20T22:44:40Z | 0.0 | 9376.6150024431 | oui | 1 | 2026-09-20T22:40:00+00:00 → 2026-09-20T22:40:00+00:00 |
| GdYTfqdV5jega9XonxqnztsHT3Xkneab5drNYuzPSLuX | 2026-09-20T22:44:46Z | 0.3529 | 0.2147614293 | oui | 2 | 2026-09-20T22:45:00+00:00 → 2026-09-20T22:40:00+00:00 |
| GFjeUJMaNyvmExQNbhKPh3RXr9jrWdqcyWP9D65HYMbK | 2026-09-20T22:45:18Z | 0.74467083054189 | 133.0567374423 | oui | 1 | 2026-09-20T22:45:00+00:00 → 2026-09-20T22:45:00+00:00 |

**Lecture prudente** : cet échantillon ne couvre que des pools morts *très récemment* (quelques minutes à quelques heures avant le sondage), trouvés sur les premières pages de `new_pools`. Il ne dit rien sur la disponibilité de l'historique pour des pools morts depuis plusieurs semaines ou mois — seul le suivi 48h en conditions réelles (étape collecte GitHub Actions) permettra de vérifier ce point sur la durée.
