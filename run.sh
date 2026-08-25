#!/bin/zsh
# Launch the Equilibrist GLEE agent in the background, logging to agent.log.
cd "$(dirname "$0")"
set -a; source .env; set +a
nohup .venv/bin/python agent.py >> agent.log 2>&1 &
echo "started pid $!"
