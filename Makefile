.PHONY: help up down restart logs logs-app ps build pull shell test test-unit test-integration test-e2e

help:           ## Show this help.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'

up:				## Start services in background
	docker compose up -d

down:			## Stop and remove containers
	docker compose down

restart:		## Restart all services
	docker compose restart

logs:			## View live logs from all containers
	docker compose logs -f

logs-app:		## View live logs from app container
	docker compose logs -f app

ps:				## List status of containers
	docker compose ps

build:			## Build Docker images
	docker compose build

pull:			## Pull latest images
	docker compose pull

shell:			## Open interactive bash shell inside app container
	docker compose exec app bash

test:			## Run all tests in Docker container
	docker compose exec -T app pytest tests/ -q

test-unit:		## Run unit tests in Docker container
	docker compose exec -T app pytest tests/unit/ -q

test-integration:	## Run integration tests in Docker container
	docker compose exec -T app pytest tests/integration/ -q

test-e2e:		## Run end-to-end tests in Docker container
	docker compose exec -T app pytest tests/e2e/ -q
