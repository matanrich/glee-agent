#!/bin/sh
# Runs at BUILD time as root; output is baked into the image.
set -eu
uv pip install --system glee-sdk
