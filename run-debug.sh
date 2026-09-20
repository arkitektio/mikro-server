#!/bin/bash
# Abort on the first failing step. Without this the boot ran `ensureadmin` -- a
# management command that is not installed -- on every start, printed the error,
# and carried on to serve traffic. A boot script that continues past a failed
# migration is a boot script that cannot tell you the deploy is broken: the
# embedding system checks, for one, refuse `migrate` when a vector column's width
# is not EMBEDDINGS.DIMENSIONS, and that refusal must stop the boot.
set -euo pipefail
echo "=> Waiting for DB to be online"
python manage.py wait_for_database -s 2

echo "=> Performing database migrations..."
python manage.py migrate


# Start the first process
echo "=> Starting Server"
python manage.py runserver 0.0.0.0:80