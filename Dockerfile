FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Лінтер і тести: якщо падають — збірка (а з нею й деплой) зупиняється,
# старий контейнер працює далі
FROM base AS test

COPY requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY src ./src
COPY tests ./tests
RUN ruff check --no-cache . \
    && python -m pytest -q -p no:cacheprovider \
    && touch /tests-passed


FROM base

# Фінальний образ залежить від test, інакше BuildKit пропустив би цей stage
COPY --from=test /tests-passed /tmp/tests-passed
COPY src ./src

CMD ["python", "-m", "src.main"]
