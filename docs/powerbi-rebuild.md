# Rebuilding the dashboard in Power BI

The published dashboard is a static Plotly page because a `.pbix` cannot be
opened from GitHub. This document maps it to a Power BI rebuild so the same
analysis can be reproduced on Windows.

## 1. Get the data

Either connect Power BI directly to DuckDB (via the ODBC driver) or export the
two model tables to CSV:

```bash
docker run --rm -v "$PWD":/app -w /app f1-strategy-regret python -c "
import duckdb
con = duckdb.connect('data/processed/f1.duckdb', read_only=True)
con.execute(\"COPY res_driver_race TO 'data/processed/res_driver_race.csv' (HEADER)\")
con.execute(\"COPY res_team_season TO 'data/processed/res_team_season.csv' (HEADER)\")
"
```

Load `res_driver_race.csv` as the fact table. `res_team_season.csv` is a
ready-made aggregate; it is easier to rebuild it as a measure (below) so the
report stays dynamic.

## 2. Data model

| Model table | Power BI table | Grain |
|---|---|---|
| `res_driver_race` | `Fact Regret` | one row per driver per race |
| `dim_races` | `Dim Race` | one row per race, joined on `session_key` |
| `dim_drivers` | `Dim Driver` | one row per driver per race, joined on `session_key` + `driver_number` |

Create the relationships:

- `Fact Regret[session_key]` → `Dim Race[session_key]` (many-to-one)
- `Fact Regret[session_key]` + `[driver_number]` → `Dim Driver` (many-to-one)

Mark `Dim Race` as a date table using `date_start`, and sort `Dim Race[location]`
by `round`.

## 3. Measures (DAX)

```dax
Points Lost := SUM ( 'Fact Regret'[regret_points] )

Seconds Lost := SUM ( 'Fact Regret'[regret_seconds] )

Points Scored := SUM ( 'Fact Regret'[actual_points] )

Lost per Point Scored :=
DIVIDE ( [Points Lost], [Points Scored] )

Driver Races := COUNTROWS ( 'Fact Regret' )

Regret Rank :=
RANKX ( ALLSELECTED ( 'Dim Driver'[team_name] ), [Points Lost], , DESC )
```

## 4. Visuals, matching the published page

| Published chart | Power BI visual | Fields |
|---|---|---|
| Team ranking | Clustered bar chart (horizontal) | Axis `Dim Driver[team_name]`, values `[Points Lost]`, sorted descending |
| Team × race heatmap | Matrix with conditional formatting (background colour scale) | Rows `team_name`, columns `Dim Race[location]` sorted by `round`, values `[Points Lost]` |
| Biggest single-race regrets | Bar chart (top N filter, N = 10) | Axis `driver_number` + `location`, values `[Points Lost]` |
| Headline card | Card visual | `[Points Lost]` filtered to the top team |

Colour the bars with each team's official colour (`team_colour` in the raw API
data) using a conditional-formatting rule on `team_name`.

## 5. Validation card

Add a text box (or card) reading:

> Mean Spearman rank correlation between modelled and actual finishing order:
> **{value from `validation.json`}** across **{races}** races.

This is the honesty check a technical interviewer will look for. Do not remove
it from the report.

## 6. Publishing

Publish to a Power BI workspace and screenshot the report page for the CV or
portfolio. The screenshot should show the team ranking and the validation card
together, because the second is what makes the first credible.
