#!/usr/bin/env python3
"""
Build the DuckDB star schema from the cached OpenF1 JSON.

Layer split:
  - Python does the state-machine work (caution periods) and type coercion.
  - SQL does the joins, derivations and aggregations that make up the star
    schema, plus the data-derived pit-cycle loss estimates.

Output: data/processed/f1.duckdb
"""
from __future__ import annotations

import json
import pathlib

import duckdb
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
DB_PATH = PROC / "f1.duckdb"

# Columns that hold nested JSON arrays / bulky fields and would break the load.
DROP_COLS = [
    "segments_sector_1", "segments_sector_2", "segments_sector_3",
    "headshot_url", "team_colour", "broadcast_name",
]


def session_dirs() -> list[pathlib.Path]:
    return sorted(
        (p for p in RAW.iterdir() if p.is_dir() and p.name.isdigit()),
        key=lambda p: int(p.name),
    )


def load_endpoint(name: str) -> pd.DataFrame:
    frames = []
    for d in session_dirs():
        f = d / f"{name}.json"
        if not f.exists():
            continue
        rows = json.loads(f.read_text(encoding="utf-8"))
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df = df.drop(columns=[c for c in DROP_COLS if c in df.columns])
        df["session_key"] = int(d.name)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def caution_laps_by_session(race_control: pd.DataFrame) -> pd.DataFrame:
    """
    Derive (session_key, lap_number) pairs under a full-course caution.

    A state machine is used rather than keyword matching per message: an SC or
    VSC period spans several laps, and only marking the deployment lap would
    leave most of the caution window in the "clean" data.

    Local yellow flags are deliberately NOT treated as cautions - they affect
    one sector, not the whole lap, and filtering them would discard real
    green-flag racing.
    """
    rows: list[tuple[int, int]] = []
    if race_control.empty:
        return pd.DataFrame(columns=["session_key", "caution_lap"])

    for skey, grp in race_control.groupby("session_key"):
        grp = grp.sort_values("date")
        active = False
        last_lap = 0

        for _, msg in grp.iterrows():
            lap = msg.get("lap_number")
            if pd.isna(lap):
                lap = last_lap
            else:
                lap = int(lap)
                last_lap = lap

            text = str(msg.get("message", "")).upper()
            category = str(msg.get("category", "")).upper()

            starts = (
                "SAFETY CAR DEPLOYED" in text
                or "SAFETY CAR IN THIS LAP" in text
                or "VIRTUAL SAFETY CAR DEPLOYED" in text
                or "RED FLAG" in text
                or category == "SAFETYCAR"
            )
            ends = (
                "SAFETY CAR ENDING" in text
                or "VIRTUAL SAFETY CAR ENDING" in text
                or "TRACK CLEAR" in text
                or "GREEN LIGHT" in text
            )

            if starts and not ends:
                active = True
                rows.append((int(skey), lap))
            elif active:
                rows.append((int(skey), lap))
                if ends:
                    active = False
                    # The restart lap is still compromised (field is bunched
                    # and the leader controls the pace), so keep one more lap.
                    rows.append((int(skey), lap + 1))

    out = pd.DataFrame(rows, columns=["session_key", "caution_lap"]).drop_duplicates()
    return out


def main() -> int:
    PROC.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))

    races = pd.DataFrame([
        {
            "session_key": int(s["session_key"]),
            "meeting_key": int(s["meeting_key"]),
            "country": s.get("country_name"),
            "location": s.get("location"),
            "date_start": s.get("date_start"),
        }
        for s in manifest
    ])
    # OpenF1 does not expose a championship round number, so derive it from
    # the calendar order.
    races = races.sort_values("date_start").reset_index(drop=True)
    races["round"] = races.index + 1

    drivers = load_endpoint("drivers")
    laps = load_endpoint("laps")
    stints = load_endpoint("stints")
    pit = load_endpoint("pit")
    position = load_endpoint("position")
    race_control = load_endpoint("race_control")
    weather = load_endpoint("weather")

    if laps.empty:
        raise SystemExit("no lap data cached - run pipeline/extract.py first")

    for df in (laps, stints, pit, position, race_control, weather, drivers, races):
        if "session_key" in df.columns:
            df["session_key"] = df["session_key"].astype("int64")
    for df in (laps, stints, pit, position, drivers):
        if "driver_number" in df.columns:
            df["driver_number"] = df["driver_number"].astype("int64")

    laps["lap_number"] = laps["lap_number"].astype("int64")
    stints["lap_start"] = stints["lap_start"].astype("int64")
    stints["lap_end"] = stints["lap_end"].astype("int64")

    caution = caution_laps_by_session(race_control)

    con = duckdb.connect(str(DB_PATH))
    con.register("stg_races", races)
    con.register("stg_drivers", drivers)
    con.register("stg_laps", laps)
    con.register("stg_stints", stints)
    con.register("stg_pit", pit)
    con.register("stg_position", position)
    con.register("stg_caution", caution)
    con.register("stg_weather", weather)

    con.execute("""
        ------------------------------------------------------------------
        -- dimensions
        ------------------------------------------------------------------
        CREATE TABLE dim_races AS
        SELECT session_key, meeting_key, round, country, location,
               CAST(date_start AS VARCHAR) AS date_start
        FROM stg_races;

        CREATE TABLE dim_drivers AS
        SELECT DISTINCT session_key, driver_number, full_name, name_acronym, team_name
        FROM stg_drivers;

        CREATE TABLE dim_caution AS SELECT DISTINCT session_key, caution_lap
        FROM stg_caution;

        ------------------------------------------------------------------
        -- fact_laps: one row per driver per lap, with tyre state and flags
        ------------------------------------------------------------------
        CREATE TABLE fact_laps AS
        WITH joined AS (
            SELECT
                l.session_key,
                l.driver_number,
                l.lap_number,
                l.lap_duration,
                COALESCE(l.is_pit_out_lap, FALSE) AS is_pit_out_lap,
                s.stint_number,
                s.compound,
                s.tyre_age_at_start,
                s.tyre_age_at_start + (l.lap_number - s.lap_start) AS tyre_age
            FROM stg_laps l
            LEFT JOIN stg_stints s
              ON  s.session_key   = l.session_key
              AND s.driver_number = l.driver_number
              AND l.lap_number BETWEEN s.lap_start AND s.lap_end
        ),
        pit_in AS (
            SELECT DISTINCT session_key, driver_number, lap_number
            FROM stg_pit
        )
        SELECT
            j.*,
            (p.lap_number IS NOT NULL) AS is_pit_in_lap,
            (c.caution_lap IS NOT NULL) AS is_caution_lap
        FROM joined j
        LEFT JOIN pit_in p
          ON  p.session_key = j.session_key
          AND p.driver_number = j.driver_number
          AND p.lap_number = j.lap_number
        LEFT JOIN stg_caution c
          ON  c.session_key = j.session_key
          AND c.caution_lap = j.lap_number;

        ------------------------------------------------------------------
        -- fact_stints
        ------------------------------------------------------------------
        CREATE TABLE fact_stints AS
        SELECT session_key, driver_number, stint_number, lap_start, lap_end,
               compound, tyre_age_at_start,
               (lap_end - lap_start + 1) AS stint_length
        FROM stg_stints;

        ------------------------------------------------------------------
        -- fact_pit: stop time and lane time
        ------------------------------------------------------------------
        CREATE TABLE fact_pit AS
        SELECT session_key, driver_number, lap_number,
               pit_duration, lane_duration, stop_duration
        FROM stg_pit;

        ------------------------------------------------------------------
        -- fact_pit_cycles: the *time actually lost* by a stop
        --
        -- For each observed stop, take the pit-in lap and the pit-out lap and
        -- compare their combined duration with the driver's own clean-flag lap
        -- pace. This measures loss against staying out, which is what the
        -- replay model needs, rather than raw pit-lane time.
        ------------------------------------------------------------------
        CREATE TABLE fact_clean_pace AS
        SELECT session_key, driver_number,
               MEDIAN(lap_duration) AS clean_lap_s,
               COUNT(*)             AS clean_laps
        FROM fact_laps
        WHERE lap_duration IS NOT NULL
          AND lap_number > 1
          AND NOT is_pit_out_lap
          AND NOT is_pit_in_lap
          AND NOT is_caution_lap
          AND compound IN ('SOFT','MEDIUM','HARD')
          AND tyre_age BETWEEN 0 AND 45
        GROUP BY 1, 2;

        CREATE TABLE fact_pit_cycles AS
        WITH sc_pace AS (
            -- Pace of cars that stayed out during the caution. A car pitting
            -- under a safety car is losing time relative to *this*, not
            -- relative to green-flag pace.
            SELECT session_key, lap_number,
                   MEDIAN(lap_duration) AS sc_lap_s
            FROM fact_laps
            WHERE is_caution_lap
              AND NOT is_pit_out_lap
              AND NOT is_pit_in_lap
              AND lap_duration IS NOT NULL
              AND lap_duration < 400
            GROUP BY 1, 2
        ),
        inbound AS (
            SELECT p.session_key, p.driver_number, p.lap_number AS pit_lap,
                   d1.lap_duration AS in_lap_s,
                   d1.is_caution_lap AS was_caution
            FROM fact_pit p
            LEFT JOIN fact_laps d1
              ON d1.session_key = p.session_key
             AND d1.driver_number = p.driver_number
             AND d1.lap_number = p.lap_number
        ),
        outbound AS (
            SELECT i.*, d2.lap_duration AS out_lap_s
            FROM inbound i
            LEFT JOIN fact_laps d2
              ON d2.session_key = i.session_key
             AND d2.driver_number = i.driver_number
             AND d2.lap_number = i.pit_lap + 1
        ),
        computed AS (
        SELECT o.session_key, o.driver_number, o.pit_lap, o.was_caution,
               o.in_lap_s, o.out_lap_s, cp.clean_lap_s,
               sc.sc_lap_s,
               (o.in_lap_s + o.out_lap_s)
                 - 2 * COALESCE(sc.sc_lap_s, cp.clean_lap_s) AS cycle_loss_s,
               -- Cost expressed in green-flag-equivalent seconds: the same
               -- fraction of a lap lost, scaled to green-flag lap pace. This
               -- keeps caution stops comparable with green stops when the
               -- model re-ranks by race time.
               CASE WHEN sc.sc_lap_s > 0
                    THEN ((o.in_lap_s + o.out_lap_s) - 2 * sc.sc_lap_s)
                         * cp.clean_lap_s / sc.sc_lap_s
                    ELSE (o.in_lap_s + o.out_lap_s) - 2 * cp.clean_lap_s
               END AS green_equiv_loss_s
        FROM outbound o
        JOIN fact_clean_pace cp
          ON cp.session_key = o.session_key
         AND cp.driver_number = o.driver_number
        LEFT JOIN sc_pace sc
          ON sc.session_key = o.session_key
         AND sc.lap_number = o.pit_lap
        WHERE cp.clean_laps >= 5
          AND o.in_lap_s IS NOT NULL
          AND o.out_lap_s IS NOT NULL
          AND o.pit_lap > 1
          -- discard stops corrupted by red flags / timing gaps
          AND o.in_lap_s < 400 AND o.out_lap_s < 400
        )
        SELECT * FROM computed
        WHERE cycle_loss_s BETWEEN -10 AND 60;

        ------------------------------------------------------------------
        -- fact_positions: final classified position per driver
        ------------------------------------------------------------------
        CREATE TABLE fact_positions AS
        WITH ranked AS (
            SELECT session_key, driver_number, position,
                   ROW_NUMBER() OVER (
                       PARTITION BY session_key, driver_number
                       ORDER BY date DESC
                   ) AS rn
            FROM stg_position
        )
        SELECT session_key, driver_number, position AS final_position
        FROM ranked WHERE rn = 1;

        ------------------------------------------------------------------
        -- fact_results: race distance, retirement status, points
        ------------------------------------------------------------------
        CREATE TABLE fact_results AS
        WITH dist AS (
            SELECT session_key, MAX(lap_number) AS race_laps
            FROM fact_laps GROUP BY session_key
        ),
        drv AS (
            SELECT session_key, driver_number, MAX(lap_number) AS last_lap
            FROM fact_laps GROUP BY session_key, driver_number
        ),
        merged AS (
            SELECT d.session_key, d.driver_number, d.last_lap, x.race_laps,
                   (d.last_lap >= x.race_laps) AS completed
            FROM drv d JOIN dist x USING (session_key)
        )
        SELECT
            m.session_key, m.driver_number, m.last_lap, m.race_laps, m.completed,
            p.final_position,
            CASE
                WHEN NOT m.completed THEN 0
                WHEN p.final_position IS NULL THEN 0
                WHEN p.final_position BETWEEN 1 AND 10
                    THEN (SELECT points FROM (VALUES
                            (1,25),(2,18),(3,15),(4,12),(5,10),
                            (6,8),(7,6),(8,4),(9,2),(10,1)
                         ) AS t(pos, points) WHERE pos = p.final_position)
                ELSE 0
            END AS points
        FROM merged m
        LEFT JOIN fact_positions p
          ON p.session_key = m.session_key AND p.driver_number = m.driver_number;

        ------------------------------------------------------------------
        -- fact_weather: track temperature per session
        ------------------------------------------------------------------
        CREATE TABLE fact_weather AS
        SELECT session_key,
               AVG(track_temperature) AS track_temp_c,
               AVG(air_temperature)   AS air_temp_c,
               MAX(rainfall)          AS rainfall
        FROM stg_weather GROUP BY session_key;

        ------------------------------------------------------------------
        -- analysis view: clean green-flag racing laps only
        ------------------------------------------------------------------
        CREATE VIEW v_clean_laps AS
        SELECT *
        FROM fact_laps
        WHERE lap_duration IS NOT NULL
          AND lap_number > 1
          AND NOT is_pit_out_lap
          AND NOT is_pit_in_lap
          AND NOT is_caution_lap
          AND compound IN ('SOFT','MEDIUM','HARD')
          AND tyre_age BETWEEN 0 AND 45;
    """)

    counts = con.execute("""
        SELECT 'races', COUNT(*) FROM dim_races
        UNION ALL SELECT 'drivers', COUNT(*) FROM dim_drivers
        UNION ALL SELECT 'laps', COUNT(*) FROM fact_laps
        UNION ALL SELECT 'clean_laps', COUNT(*) FROM v_clean_laps
        UNION ALL SELECT 'stints', COUNT(*) FROM fact_stints
        UNION ALL SELECT 'pit_stops', COUNT(*) FROM fact_pit
        UNION ALL SELECT 'pit_cycles', COUNT(*) FROM fact_pit_cycles
        UNION ALL SELECT 'caution_laps', COUNT(*) FROM dim_caution
        UNION ALL SELECT 'results', COUNT(*) FROM fact_results
    """).fetchall()

    print("=== star schema built ===")
    for name, n in counts:
        print(f"  {name:14s} {n}")

    print("\n=== pit-cycle loss by flag state ===")
    print(con.execute("""
        SELECT CASE WHEN was_caution THEN 'caution' ELSE 'green' END AS state,
               COUNT(*) AS stops,
               ROUND(MEDIAN(cycle_loss_s), 2) AS median_loss_s,
               ROUND(MEDIAN(green_equiv_loss_s), 2) AS green_equiv_s
        FROM fact_pit_cycles GROUP BY 1 ORDER BY 1
    """).df().to_string(index=False))

    con.close()
    print(f"\nwrote {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
