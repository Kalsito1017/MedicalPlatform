#!/bin/sh
# Container entrypoint: apply database migrations, then exec the command.
#
# Migrations-on-start is a deliberate demo-scale trade-off (documented in
# docs/DEPLOYMENT.md): one service, no orchestration, always consistent after
# `docker compose up`. For a multi-instance production rollout, run migrations
# as a separate one-shot job instead (`docker compose run --rm app python -m
# alembic -c alembic.ini upgrade head`) and set RUN_MIGRATIONS_ON_START=0.
set -eu

if [ "${RUN_MIGRATIONS_ON_START:-1}" = "1" ]; then
    echo "entrypoint: applying database migrations (alembic upgrade head)"
    python -m alembic -c alembic.ini upgrade head
fi

exec "$@"
