.PHONY: run install update build test

PROGRAM_NAME := "qamposer-backend"

prepare:
	cp .env.example .env

run:
	uv run uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload

install:
	uv sync
	uv run pre-commit install

update:
	uv lock --upgrade
	uv sync
	uv run pre-commit autoupdate

build:
	uv build

test:
	uv run pytest ./tests -p no:warnings

docker-build:
	docker build . -t ${PROGRAM_NAME}

docker-build-prod:
	docker build . -t ${PROGRAM_NAME}-prod --platform=linux/amd64

docker-run:
	docker run -it -p 8080:8080 ${PROGRAM_NAME}
