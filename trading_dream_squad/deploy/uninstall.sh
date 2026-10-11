#!/bin/zsh
for m in crypto stocks; do
  launchctl unload ~/Library/LaunchAgents/com.tradingdreamsquad.$m.plist 2>/dev/null || true
  rm -f ~/Library/LaunchAgents/com.tradingdreamsquad.$m.plist
done
echo "Désinstallé (les journaux dans data/ sont conservés)."
