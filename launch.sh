#!/bin/sh
# Runs at BOOT as the non-root app user. GLEE_API_KEY comes from set_env.
set -eu
exec python3 agent.py
