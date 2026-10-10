PY ?= .venv/bin/python

setup:            ## venv, deps, React build, SQLite db, demo data
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
	cd frontend && npm ci --silent && npm run build
	$(PY) manage.py migrate -v 0 && $(PY) manage.py seed_demo

demo:             ## partner retry storm, late reply and forged call against the running app
	$(PY) scripts/partner_demo.py

run:              ## web on :8000 plus the sweeper (no broker needed)
	$(PY) manage.py sweep --loop & .venv/bin/gunicorn config.wsgi -b 127.0.0.1:8000 --threads 16

test:             ## SQLite; add DATABASE_URL=postgres://... to run on Postgres
	$(PY) -m pytest
