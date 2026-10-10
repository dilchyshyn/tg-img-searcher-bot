#!/usr/bin/env bash
# Перевіряє GitHub і, якщо з'явились нові коміти, оновлює код та перезбирає бота.
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch --quiet origin

# Нових комітів немає (або локальна гілка попереду GitHub)
if git merge-base --is-ancestor '@{u}' HEAD; then
    exit 0
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Є незакомічені зміни в коді — оновлення пропущено"
    exit 0
fi

old=$(git rev-parse --short HEAD)
git merge --ff-only --quiet '@{u}'
echo "Оновлено $old -> $(git rev-parse --short HEAD), перезбираю бота"

docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null
