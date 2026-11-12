-- Named analytical queries over the star schema and model results.
-- Each block is separated by a `-- name: <slug>` marker and is executed by
-- pipeline/run_sql.py. Keep one statement per block.

-- name: team_ranking
-- The headline table: points lost to strategy, plus the share of the total.
SELECT
    team,
    CAST(SUM(regret_points) AS INTEGER)            AS points_lost,
    ROUND(SUM(regret_seconds), 1)                  AS seconds_lost,
    CAST(SUM(actual_points) AS INTEGER)             AS points_scored,
    ROUND(SUM(regret_points) / NULLIF(SUM(actual_points), 0), 3) AS lost_per_point_scored,
    COUNT(*)                                       AS driver_races
FROM res_driver_race
GROUP BY team
ORDER BY points_lost DESC;

-- name: race_ranking
-- Which rounds produced the most regret across the field.
SELECT
    round,
    location,
    CAST(SUM(regret_points) AS INTEGER) AS points_lost,
    COUNT(*)                            AS drivers,
    ROUND(AVG(green_pit_loss_s), 1)     AS avg_green_pit_loss_s
FROM res_driver_race
GROUP BY round, location
ORDER BY points_lost DESC;

-- name: worst_individual_calls
-- The case studies: biggest single-driver regrets, with the strategy the
-- model preferred.
SELECT
    round,
    location,
    driver_number,
    team,
    actual_position,
    actual_points,
    actual_compounds,
    best_compounds,
    best_pit_laps,
    CAST(regret_points AS INTEGER) AS points_lost,
    ROUND(regret_seconds, 1)       AS seconds_lost
FROM res_driver_race
ORDER BY regret_points DESC, regret_seconds DESC
LIMIT 15;

-- name: stop_count_errors
-- Did the regret come from the wrong number of stops or the wrong timing?
SELECT
    CASE
        WHEN best_stops > actual_stops THEN 'stopped too few times'
        WHEN best_stops < actual_stops THEN 'stopped too many times'
        ELSE 'right stop count, wrong window'
    END AS error_type,
    COUNT(*)                            AS driver_races,
    CAST(SUM(regret_points) AS INTEGER) AS points_lost,
    ROUND(AVG(regret_seconds), 1)       AS avg_seconds_lost
FROM res_driver_race
GROUP BY 1
ORDER BY points_lost DESC;

-- name: pit_loss_by_race
-- The measured cost of a stop, green flag versus safety car.
SELECT
    r.round,
    r.location,
    COUNT(*) FILTER (WHERE NOT c.was_caution) AS green_stops,
    ROUND(MEDIAN(c.cycle_loss_s) FILTER (WHERE NOT c.was_caution), 1) AS green_loss_s,
    COUNT(*) FILTER (WHERE c.was_caution)     AS caution_stops,
    ROUND(MEDIAN(c.cycle_loss_s) FILTER (WHERE c.was_caution), 1)     AS caution_loss_s
FROM fact_pit_cycles c
JOIN dim_races r USING (session_key)
GROUP BY r.round, r.location
ORDER BY r.round;

-- name: data_coverage
-- Integrity check: what the pipeline actually ingested.
SELECT
    (SELECT COUNT(*) FROM dim_races)        AS races,
    (SELECT COUNT(*) FROM fact_laps)        AS laps,
    (SELECT COUNT(*) FROM v_clean_laps)     AS clean_laps,
    (SELECT COUNT(*) FROM fact_pit_cycles)  AS measured_pit_cycles,
    (SELECT COUNT(*) FROM res_driver_race)  AS modelled_driver_races
;
