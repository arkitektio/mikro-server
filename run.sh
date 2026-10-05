#!/bin/bash
# Serve, and nothing else.
#
# The database is brought to this release before the service is started, by whoever starts
# it: `python -m arkitekt_service migrate` waits for the database, applies the migrations
# and runs the service's setup. Konstruktor runs it once per build — before a hub's first
# start and before an update's — so a container that merely restarts does none of it.
# `run-debug.sh` does both in one go, for development.
set -euo pipefail
exec daphne -b 0.0.0.0 -p 80 --websocket_timeout -1 mikro_server.asgi:application
