.PHONY: setup migrate run bot worker test lint check
setup:
	uv sync --extra dev
	cp -n .env.example .env || true
migrate:
	uv run python manage.py migrate
run:
	uv run python manage.py runserver
bot:
	uv run python manage.py runbot
worker:
	uv run celery -A config worker -B -l INFO
test:
	uv run pytest
lint:
	uv run ruff check .
	uv run mypy apps config
check:
	uv run python manage.py check --deploy
