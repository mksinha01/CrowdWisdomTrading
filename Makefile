.PHONY: bootstrap doctor run offline test lint demo

bootstrap:
	python -m pip install --upgrade pip
	pip install -e . -r requirements.txt -r requirements-dev.txt

doctor:
	cwt doctor

run:
	cwt run --engine hermes

offline:
	cwt run --engine local --offline

test:
	pytest tests/ -q

lint:
	ruff check .
	mypy src

demo:
	cwt run --engine local --offline --record-pacing
