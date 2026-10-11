#!/usr/bin/env bash
# Перевіряє GitHub і, якщо з'явились нові коміти, оновлює код та перезбирає бота.
# Якщо збірка або запуск не пройшли — повертає попередній образ (тег :prev).
set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER=telegram-img-bot
MAX_ATTEMPTS=3
state_file="$(git rev-parse --git-dir)/auto-update-state"
state_sha=""
state_tries=0
if [ -f "$state_file" ]; then
    read -r state_sha state_tries < "$state_file"
fi

# $2 = final: більше не повторювати цей коміт
record_failure() {
    local sha tries=0
    sha=$(git rev-parse HEAD)
    if [ "$state_sha" = "$sha" ]; then
        tries=$state_tries
    fi
    tries=$((tries + 1))
    if [ "${2:-}" = final ]; then
        tries=$MAX_ATTEMPTS
    fi
    echo "$sha $tries" > "$state_file"
    echo "$1" >&2
    exit 1
}

git fetch --quiet origin

if git merge-base --is-ancestor '@{u}' HEAD; then
    # Нових комітів немає. Повторюємо збірку, лише якщо попередня спроба для цього коміту не вдалась
    if [ "$state_sha" = "$(git rev-parse HEAD)" ] && [ "$state_tries" -gt 0 ] && [ "$state_tries" -lt "$MAX_ATTEMPTS" ]; then
        echo "Повторна спроба збірки $(git rev-parse --short HEAD) ($((state_tries + 1))/$MAX_ATTEMPTS)"
    else
        exit 0
    fi
else
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        echo "Є незакомічені зміни в коді — оновлення пропущено"
        exit 0
    fi
    old=$(git rev-parse --short HEAD)
    git merge --ff-only --quiet '@{u}'
    echo "Оновлено $old -> $(git rev-parse --short HEAD), перезбираю бота"
fi

# Запам'ятовуємо образ, що зараз працює, щоб мати куди відкотитись
has_prev=0
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker tag "$(docker inspect -f '{{.Image}}' "$CONTAINER")" "$CONTAINER:prev"
    has_prev=1
fi

# Збірка включає ruff і тести (stage test у Dockerfile); якщо вони падають,
# контейнер не перестворюється і далі працює попередня версія
if ! docker compose up -d --build --remove-orphans; then
    record_failure "Збірка $(git rev-parse --short HEAD) не вдалась (ймовірно, тести) — контейнер не змінено"
fi

# Перевірка запуску: restart-політика перезапускатиме контейнер, що падає, тож дивимось RestartCount
sleep 15
read -r running restarts < <(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$CONTAINER")
# Збій на старті повторювати не варто: образ зібрався, тож упаде знову,
# а кожна спроба на ~15 с замінює робочий бот зламаним
if [ "$running" != true ] || [ "$restarts" != 0 ]; then
    if [ "$has_prev" = 1 ]; then
        docker tag "$CONTAINER:prev" "$CONTAINER:latest"
        docker compose up -d --no-build --remove-orphans
        record_failure "Контейнер $(git rev-parse --short HEAD) не запустився — повернуто попередню версію" final
    fi
    record_failure "Контейнер $(git rev-parse --short HEAD) не запустився, попередньої версії немає" final
fi

rm -f "$state_file"
docker image prune -f >/dev/null
