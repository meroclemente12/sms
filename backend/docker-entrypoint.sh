#!/bin/sh
# Bring the database schema up to date, optionally load the demo spa, then start.
set -e

echo "Applying database migrations..."
alembic upgrade head

if [ "${SEED_DEMO:-false}" = "true" ]; then
  python -m app.cli seed-demo
fi

exec "$@"
