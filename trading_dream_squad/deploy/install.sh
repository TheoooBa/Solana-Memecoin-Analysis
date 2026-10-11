#!/bin/zsh
# Installe les deux boucles (crypto 24/7, actions pendant les heures de bourse) comme services launchd.
set -e
cd "$(dirname "$0")"
[ -f "$HOME/.trading_dream_squad.env" ] || { echo "Crée d'abord ~/.trading_dream_squad.env avec: export ANTHROPIC_API_KEY=sk-ant-..."; exit 1; }
for m in crypto stocks; do
  cp com.tradingdreamsquad.$m.plist ~/Library/LaunchAgents/
  launchctl unload ~/Library/LaunchAgents/com.tradingdreamsquad.$m.plist 2>/dev/null || true
  launchctl load ~/Library/LaunchAgents/com.tradingdreamsquad.$m.plist
done
echo "Installé. Vérifie avec: python status.py"
