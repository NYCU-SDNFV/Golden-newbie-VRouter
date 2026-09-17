SHELL := /usr/bin/env bash
COMPOSE ?= docker compose
CONTAINER ?= lab3
PLANE ?= ovs
GROUPS := a1 a2 a3 b0 b1 b2 b3 b4 b5 b6 b7 b8 b9 b10
export CONTAINER
.NOTPARALLEL:

.PHONY: help build up down deploy status shell logs clean policy test test-offline check-update update a b report git $(GROUPS)

help:
	@printf '%s\n' \
	  'make up                       Start the supplied course container' \
	  'make deploy PLANE=frr|ovs      Deploy the Part A or Part B topology' \
	  'make a / make b                Run Part A / Part B checks' \
	  'make test                     Check release compatibility, then all checks' \
	  'make test-offline              Run all checks without an online version check' \
	  'make status / make shell       Inspect the running lab' \
	  'make clean                    Remove only this lab container and topology'

build:
	$(COMPOSE) build

up:
	@mkdir -p results captures
	$(COMPOSE) up -d --build
	@sh tests/wait_ready.sh

down:
	$(COMPOSE) down --remove-orphans

deploy: up
	docker exec $(CONTAINER) python3 harness/lab3.py deploy --plane $(PLANE)

status:
	docker exec $(CONTAINER) python3 harness/lab3.py status --json

policy:
	@bash .github/policy/00_layout.sh
	@bash .github/policy/01_integrity.sh

check-update:
	python3 .github/release/upgrade.py check

update:
	python3 .github/release/upgrade.py update

test: check-update
	@$(MAKE) test-offline

test-offline: policy up
	@sh tests/00_env.sh
	@for group in $(GROUPS); do python3 tests/grade.py "$$group" || exit $$?; done
	@python3 tests/grade.py report
	@sh tests/80_git.sh
	@printf '\nAll Lab 3 checks passed.\n'

$(GROUPS):
	@python3 tests/grade.py $@

a:
	@for group in a1 a2 a3; do python3 tests/grade.py "$$group" || exit $$?; done

b:
	@for group in b0 b1 b2 b3 b4 b5 b6 b7 b8 b9 b10; do python3 tests/grade.py "$$group" || exit $$?; done

report:
	@python3 tests/grade.py report

git:
	@sh tests/80_git.sh

shell:
	docker exec -it $(CONTAINER) bash

logs:
	$(COMPOSE) logs --no-color --tail=100
	@printf '%s\n' 'Routing/controller evidence is also under results/ and captures/.'

clean:
	@sh tests/clean.sh
