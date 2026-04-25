.PHONY: ci lint migrations test security

export DEBUG ?= 1
export SECRET_KEY ?= local-ci-not-secret-8f6a90e4d2b64782a4d4c3b2452dd6d7f7e83bb164f24c86
export FERNET_KEYS ?= local-ci-fernet-key-8f6a90e4d2b64782a4d4c3b2452dd6d7
export DATABASE_ENGINE ?= postgres
export POSTGRES_HOST ?= 127.0.0.1
export POSTGRES_PORT ?= 5432
export POSTGRES_DB ?= perfumex_local
export POSTGRES_USER ?= postgres
export POSTGRES_PASSWORD ?=
export ALLOWED_HOSTS ?= 127.0.0.1,localhost
export CSRF_TRUSTED_ORIGINS ?= https://127.0.0.1,https://localhost
export ASSISTANT_USE_OPENAI ?= false

ci: lint migrations test security

lint:
	ruff check .
	black --check .
	djlint --check prices/templates assistant_core/templates assistant_linking/templates
	npm run lint:js

migrations:
	python manage.py makemigrations --check --dry-run
	python manage.py migrate --plan

test:
	coverage run manage.py test --verbosity=2
	coverage report --fail-under=30

security:
	pip-audit --strict
	python manage.py check --deploy
	bandit -r prices assistant_core assistant_linking catalog
