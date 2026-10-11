#!/bin/zsh
# Lancé par launchd : charge la clé depuis ~/.trading_dream_squad.env puis démarre la boucle d'un marché.
cd "$(dirname "$0")/.."
[ -f "$HOME/.trading_dream_squad.env" ] && source "$HOME/.trading_dream_squad.env"
mkdir -p logs
exec caffeinate -i .venv/bin/python -W ignore run.py --market "$1" >> "logs/$1.log" 2>&1
