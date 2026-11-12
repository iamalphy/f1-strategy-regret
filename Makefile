IMAGE := f1-strategy-regret
RUN   := docker run --rm -v $(PWD):/app -w /app $(IMAGE)

.PHONY: all build data db model dashboard verify clean

all: build data db model dashboard verify

build:
	docker build -t $(IMAGE) .

## Download and cache all 2024 race sessions (resumable).
data:
	$(RUN) python pipeline/extract.py

## Build the DuckDB star schema from the cache.
db:
	$(RUN) python pipeline/build_db.py

## Fit the pace model, replay strategies, score regret.
model:
	$(RUN) python pipeline/model.py

## Render the static dashboard.
dashboard:
	$(RUN) python pipeline/dashboard.py

## Run the named SQL queries and write results to data/processed/sql_results.md.
sql:
	$(RUN) python pipeline/run_sql.py

## Print the validation summary and fail loudly if the model is not credible.
verify:
	$(RUN) python pipeline/verify.py

clean:
	rm -f data/processed/*
