#!/usr/bin/env python3
"""
Strategy Regret model.

Method (single-driver replay against a fixed field)
---------------------------------------------------
1. Fit a per-race additive pace model on clean green-flag laps:

       lap_time = intercept + driver_effect + degradation(compound) * tyre_age
                  + fuel_term * lap_number

2. Measure the real time lost by a pit stop from the data, separately for
   green-flag stops and stops made under a safety car / VSC.

3. For each classified driver, enumerate alternative 1- and 2-stop strategies
   (pit laps plus compound sequences, respecting the dry two-compound rule).
   Each candidate is scored on the only terms that change with strategy:
   tyre-age degradation and pit loss. A stop taken on a caution lap is scored
   at the cheaper caution pit loss, so "should have pitted under the safety
   car" is a strategy the search can find.

4. Convert the cost difference into positions: re-rank the field by each
   driver's real cumulative race time minus their regret, then compare the
   resulting points with the points actually scored.

Because the driver and fuel terms are identical across strategies for a given
driver, they cancel out of the comparison. That keeps the search exact and
fast, and it is also the model's main limitation: it is a hindsight optimum
for one driver at a time, not a field simulation.

Outputs land in data/processed/ and back in DuckDB.
"""
from __future__ import annotations

import json
import pathlib

import duckdb
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "processed" / "f1.duckdb"
OUT = ROOT / "data" / "processed"

POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
DRY = ("SOFT", "MEDIUM", "HARD")
DEFAULT_GREEN_PIT_LOSS = 22.0
MIN_DEG = 0.005        # floor so a noisy negative slope cannot reward old tyres


# --------------------------------------------------------------------------
# pace model
# --------------------------------------------------------------------------
def fit_pace(laps: pd.DataFrame) -> dict:
    """Fit the additive pace model for one race. Returns coefficients + RMSE."""
    # Laps more than 7% off a driver's own clean median are traffic, mistakes
    # or debris, not pace. Leaving them in inflates the residual and blurs the
    # driver effects.
    med = laps.groupby("driver_number")["lap_duration"].transform("median")
    laps = laps[laps["lap_duration"] <= med * 1.07]

    drivers = sorted(laps["driver_number"].unique())
    if len(drivers) < 2:
        raise ValueError("not enough drivers to fit")

    ref = drivers[0]
    columns: list[np.ndarray] = [np.ones(len(laps))]
    names: list[str] = ["intercept"]

    for d in drivers[1:]:
        columns.append((laps["driver_number"].values == d).astype(float))
        names.append(f"d{d}")

    for comp in DRY:
        columns.append(((laps["compound"].values == comp) * laps["tyre_age"].values).astype(float))
        names.append(f"deg_{comp}")

    columns.append(laps["lap_number"].values.astype(float))
    names.append("fuel")

    X = np.column_stack(columns)
    y = laps["lap_duration"].values.astype(float)

    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    rmse = float(np.sqrt(np.mean(resid ** 2)))

    coef = dict(zip(names, beta))

    # Compounds with too little support get an unstable slope; replace with the
    # median of the well-supported compounds.
    support = laps.groupby("compound").size().to_dict()
    ok = [coef[f"deg_{c}"] for c in DRY if support.get(c, 0) >= 20 and coef[f"deg_{c}"] > 0]
    fallback = float(np.median(ok)) if ok else 0.05
    for c in DRY:
        if support.get(c, 0) < 20 or coef[f"deg_{c}"] <= 0:
            coef[f"deg_{c}"] = fallback
        coef[f"deg_{c}"] = max(float(coef[f"deg_{c}"]), MIN_DEG)

    coef["_ref_driver"] = ref
    coef["_drivers"] = drivers
    coef["_rmse"] = rmse
    return coef


def tyre_term(deg: float, length: int, start_age: float) -> float:
    """Sum of tyre ages over a stint - the only strategy-dependent pace term."""
    return deg * (length * start_age + length * (length - 1) / 2.0)


def pit_loss_at(lap: int, green: float, caution: float, caution_laps: set[int]) -> float:
    return caution if lap in caution_laps else green


def best_strategy(n_laps: int, deg: dict[str, float], green_pit: float,
                  caution_pit: float, caution_laps: set[int],
                  start_age: float, start_compound: str,
                  max_stint: int) -> tuple[float, dict]:
    """
    Find the cheapest feasible strategy with a dynamic programme over stint
    boundaries.

    Constraints:
      - the first stint stays on the driver's actual starting compound, because
        the starting tyre is fixed by qualifying rules and tyre allocation,
        neither of which the model observes;
      - consecutive stints must use different dry compounds (the two-compound
        rule);
      - no stint is longer than the longest stint any team actually ran in that
        race, so the search cannot recommend a stint the field itself treated
        as unsafe or illegal;
      - at least one stop.

    Returns (cost, plan). Cost is in seconds and covers only the terms that
    depend on strategy: tyre-age degradation and pit loss.
    """

    c1 = start_compound if start_compound in DRY else "MEDIUM"
    INF = float("inf")
    MAX_STOPS = 6

    def lookup(start: int, compound: str, stops: int):
        if start > n_laps:
            return (0.0, ()) if stops >= 1 else (INF, ())
        return dp.get((start, compound, stops), (INF, ()))

    # dp[(start, compound, stops)] = cheapest (cost, plan) from lap `start`
    # onward. Filled from the end of the race backwards, so every sub-problem
    # is complete before it is used.
    dp: dict[tuple[int, str, int], tuple[float, tuple]] = {}
    for start in range(n_laps, 0, -1):
        for compound in DRY:
            for stops in range(MAX_STOPS, -1, -1):
                best_cost, best_plan = INF, ()
                longest = min(max_stint, n_laps - start + 1)
                for length in range(1, longest + 1):
                    end = start + length - 1
                    sa = start_age if start == 1 else 0.0
                    base = tyre_term(deg[compound], length, sa)

                    if end == n_laps:
                        if stops == 0:
                            continue  # a dry race must include at least one stop
                        if base < best_cost:
                            best_cost, best_plan = base, ((compound, length),)
                        continue

                    if end < 3:
                        continue  # no strategic stop in the first two laps

                    pit = pit_loss_at(end, green_pit, caution_pit, caution_laps)
                    for nxt in DRY:
                        if nxt == compound:
                            continue
                        sub_cost, sub_plan = lookup(end + 1, nxt, stops + 1)
                        if sub_cost == INF:
                            continue
                        total = base + pit + sub_cost
                        if total < best_cost:
                            best_cost, best_plan = total, ((compound, length),) + sub_plan
                dp[(start, compound, stops)] = (best_cost, best_plan)

    cost, plan = dp.get((1, c1, 0), (INF, ()))
    if not plan:
        # No feasible strategy under the observed stint limit.
        return INF, {"stops": 0, "compounds": [], "pit_laps": []}

    pit_laps: list[int] = []
    lap = 1
    for i, (_, length) in enumerate(plan):
        end = lap + length - 1
        if i < len(plan) - 1:
            pit_laps.append(end)
        lap = end + 1

    return cost, {
        "stops": len(plan) - 1,
        "compounds": [c for c, _ in plan],
        "pit_laps": pit_laps,
    }


def actual_strategy_cost(stints: pd.DataFrame, deg: dict[str, float],
                         green_pit: float, caution_pit: float,
                         caution_laps: set[int]) -> float:
    rows = stints.sort_values("stint_number")
    total = 0.0
    fallback_deg = float(np.median(list(deg.values())))
    for i, (_, s) in enumerate(rows.iterrows()):
        length = int(s["lap_end"] - s["lap_start"] + 1)
        start_age = float(s["tyre_age_at_start"]) if i == 0 else 0.0
        total += tyre_term(deg.get(s["compound"], fallback_deg), length, start_age)
        # Stops in the first two laps are red-flag tyre changes or race-start
        # incidents, not strategy calls, so they carry no pit loss.
        if i < len(rows) - 1 and int(s["lap_end"]) >= 3:
            total += pit_loss_at(int(s["lap_end"]), green_pit, caution_pit, caution_laps)
    return total


def count_strategic_stops(stints: pd.DataFrame) -> int:
    """Stops after lap 2; a red-flag tyre change on lap 1 is not a choice."""
    return int((stints["lap_start"] >= 3).sum())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)

    races = con.execute("SELECT * FROM dim_races ORDER BY round").df()
    clean = con.execute("SELECT * FROM v_clean_laps").df()
    stints = con.execute("SELECT * FROM fact_stints").df()
    results = con.execute("SELECT * FROM fact_results").df()
    drivers = con.execute("SELECT * FROM dim_drivers").df()
    cycles = con.execute("SELECT * FROM fact_pit_cycles").df()
    caution = con.execute("SELECT * FROM dim_caution").df()
    all_laps = con.execute("""
        SELECT session_key, driver_number, lap_number, lap_duration
        FROM fact_laps
    """).df()
    con.close()

    driver_team = drivers.set_index(["session_key", "driver_number"])["team_name"].to_dict()

    race_rows: list[dict] = []
    validation: list[dict] = []
    skipped: list[dict] = []

    for _, race in races.iterrows():
        skey = int(race["session_key"])
        label = f"R{int(race['round']):02d} {race['location']}"

        ldf = clean[clean["session_key"] == skey]
        if ldf.empty:
            skipped.append({"session_key": skey, "location": race["location"],
                            "reason": "no clean laps"})
            continue

        race_stints = stints[stints["session_key"] == skey]
        wet_share = 0.0
        if not race_stints.empty:
            total_len = float(race_stints["stint_length"].sum())
            wet_len = float(race_stints.loc[~race_stints["compound"].isin(DRY), "stint_length"].sum())
            wet_share = wet_len / total_len if total_len else 0.0
        if wet_share > 0.02:
            skipped.append({"session_key": skey, "location": race["location"],
                            "reason": f"wet race ({wet_share:.0%} of laps on wet tyres)"})
            print(f"{label}: skipped (wet)")
            continue

        max_stint = int(race_stints["stint_length"].max()) if not race_stints.empty else 60
        max_stint = max(max_stint, 5)

        try:
            coef = fit_pace(ldf)
        except ValueError as exc:
            skipped.append({"session_key": skey, "location": race["location"], "reason": str(exc)})
            continue

        deg = {c: coef[f"deg_{c}"] for c in DRY}

        # Pit loss, measured two ways for this race.
        rc = cycles[cycles["session_key"] == skey]
        green_rows = rc[~rc["was_caution"]]["cycle_loss_s"].dropna()
        caution_rows = rc[rc["was_caution"]]["green_equiv_loss_s"].dropna()
        green_pit = float(green_rows.median()) if len(green_rows) >= 5 else DEFAULT_GREEN_PIT_LOSS
        if not np.isfinite(green_pit) or green_pit <= 0:
            green_pit = DEFAULT_GREEN_PIT_LOSS
        # Stops under a safety car are costed in green-flag-equivalent seconds
        # and can never be more expensive than a green-flag stop.
        caution_pit = float(caution_rows.median()) if len(caution_rows) >= 3 else green_pit * 0.85
        if not np.isfinite(caution_pit) or caution_pit <= 0:
            caution_pit = green_pit * 0.85
        caution_pit = min(caution_pit, green_pit)
        caution_laps = set(caution[caution["session_key"] == skey]["caution_lap"].astype(int))

        res = results[results["session_key"] == skey]
        n_laps = int(res["race_laps"].max())

        # Data-integrity gate. Some sessions report mid-race stints of one or
        # two laps for most of the field, which means the stint boundaries are
        # not trustworthy and the stop count cannot be reconstructed. Qatar
        # 2024 is the known case; the race is excluded rather than guessed at.
        mid_tiny = race_stints[
            (race_stints["lap_start"] > 2)
            & (race_stints["stint_length"] <= 2)
            & (race_stints["lap_end"] < n_laps)
        ]
        if mid_tiny["driver_number"].nunique() >= 5:
            skipped.append({"session_key": skey, "round": int(race["round"]),
                            "location": race["location"],
                            "reason": f"stint data integrity ({mid_tiny['driver_number'].nunique()} drivers "
                                      f"with 1-2 lap mid-race stints)"})
            print(f"{label}: skipped (unreliable stint data)")
            continue

        lap_totals = all_laps[all_laps["session_key"] == skey].copy()
        lap_totals = lap_totals.dropna(subset=["lap_duration"])
        # A single missing lap time must not make a car look faster than the
        # winner: fill gaps with the driver's own median lap before summing.
        full = all_laps[all_laps["session_key"] == skey].copy()
        full = full.groupby("driver_number").filter(
            lambda g: g["lap_duration"].notna().any()
        )
        full["lap_duration"] = full.groupby("driver_number")["lap_duration"] \
            .transform(lambda s: s.fillna(s.median()))
        real_time = full.groupby("driver_number")["lap_duration"].sum().to_dict()
        timed_laps = lap_totals.groupby("driver_number")["lap_number"].count().to_dict()
        total_laps = full.groupby("driver_number")["lap_number"].count().to_dict()

        race_regret: list[dict] = []
        modelled_time: dict[int, float] = {}

        for _, row in res[res["completed"]].iterrows():
            d = int(row["driver_number"])
            d_stints = stints[(stints["session_key"] == skey) & (stints["driver_number"] == d)]
            if d_stints.empty or d not in real_time:
                continue
            if (total_laps.get(d, 0) < n_laps - 2
                    or timed_laps.get(d, 0) < n_laps - 2):
                continue  # too many timing gaps for the total to be reliable

            start_age = float(d_stints.sort_values("stint_number")["tyre_age_at_start"].iloc[0])
            start_compound = str(d_stints.sort_values("stint_number")["compound"].iloc[0])
            actual_cost = actual_strategy_cost(d_stints, deg, green_pit, caution_pit, caution_laps)
            best_cost, best = best_strategy(
                n_laps, deg, green_pit, caution_pit, caution_laps,
                start_age, start_compound, max_stint,
            )
            if not np.isfinite(best_cost):
                skipped.append({"session_key": skey, "location": race["location"],
                                "reason": f"no feasible strategy under {max_stint}-lap stint limit"})
                continue
            regret_s = max(actual_cost - best_cost, 0.0)

            base = coef["intercept"] + coef.get(f"d{d}", 0.0)
            fuel_sum = coef["fuel"] * n_laps * (n_laps + 1) / 2.0
            modelled_time[d] = base * n_laps + fuel_sum + actual_cost

            race_regret.append({
                "session_key": skey,
                "round": int(race["round"]),
                "location": race["location"],
                "driver_number": d,
                "team": driver_team.get((skey, d)),
                "actual_position": int(row["final_position"]) if pd.notna(row["final_position"]) else None,
                "actual_points": int(row["points"]),
                "actual_stops": count_strategic_stops(d_stints),
                "actual_compounds": "-".join(d_stints.sort_values("stint_number")["compound"]),
                "best_stops": best.get("stops"),
                "best_compounds": "-".join(best.get("compounds", [])),
                "best_pit_laps": ",".join(str(x) for x in best.get("pit_laps", [])),
                "regret_seconds": regret_s,
                "real_time_s": float(real_time[d]),
                "green_pit_loss_s": green_pit,
                "caution_pit_loss_s": caution_pit,
            })

        if not race_regret:
            skipped.append({"session_key": skey, "location": race["location"],
                            "reason": "no replayed drivers"})
            continue

        rr = pd.DataFrame(race_regret)

        # Re-rank by real race time minus the seconds the optimal strategy
        # would have saved.
        rr["adjusted_time"] = rr["real_time_s"] - rr["regret_seconds"]
        rr = rr.sort_values("adjusted_time").reset_index(drop=True)
        rr["modelled_position"] = rr.index + 1
        rr["modelled_points"] = [POINTS[p - 1] if p <= len(POINTS) else 0 for p in rr["modelled_position"]]
        rr["regret_points"] = (rr["modelled_points"] - rr["actual_points"]).clip(lower=0)
        race_rows.extend(rr.to_dict("records"))

        # ---- validation: does the model reproduce the actual order? --------
        order = pd.DataFrame({
            "driver_number": list(modelled_time.keys()),
            "modelled": list(modelled_time.values()),
        }).merge(res[["driver_number", "final_position", "completed"]],
                 on="driver_number", how="left")
        order = order[order["completed"]]
        order["predicted_position"] = order["modelled"].rank(method="first").astype(int)
        spearman = order[["predicted_position", "final_position"]].corr(method="spearman").iloc[0, 1]
        within2 = float((order["predicted_position"] - order["final_position"]).abs().le(2).mean())

        validation.append({
            "session_key": skey,
            "round": int(race["round"]),
            "location": race["location"],
            "rmse_s": coef["_rmse"],
            "spearman": float(spearman) if pd.notna(spearman) else None,
            "within_2_positions": within2,
            "drivers": len(order),
            "green_pit_loss_s": green_pit,
            "caution_pit_loss_s": caution_pit,
        })
        print(f"{label}: rmse={coef['_rmse']:.2f}s within2={within2:.0%} "
              f"regret={rr['regret_points'].sum():.0f}pts")

    if not race_rows:
        raise SystemExit("no races could be modelled")

    driver_race = pd.DataFrame(race_rows)
    driver_race.to_csv(OUT / "driver_race_regret.csv", index=False)

    team_season = (driver_race.groupby("team")
                   .agg(regret_points=("regret_points", "sum"),
                        regret_seconds=("regret_seconds", "sum"),
                        points_scored=("actual_points", "sum"),
                        driver_races=("driver_number", "count"))
                   .reset_index()
                   .sort_values("regret_points", ascending=False))
    team_season["regret_per_point"] = (
        team_season["regret_points"] / team_season["points_scored"].replace(0, np.nan)
    ).round(3)
    team_season.to_csv(OUT / "team_season.csv", index=False)

    val = pd.DataFrame(validation)
    val.to_csv(OUT / "validation.csv", index=False)

    summary = {
        "races_modelled": int(len(val)),
        "races_skipped": len(skipped),
        "skipped": skipped,
        "mean_rmse_s": float(val["rmse_s"].mean()),
        "median_rmse_s": float(val["rmse_s"].median()),
        "mean_spearman": float(val["spearman"].mean()),
        "mean_within_2_positions": float(val["within_2_positions"].mean()),
        "total_regret_points": float(driver_race["regret_points"].sum()),
        "top_team": team_season.iloc[0].to_dict() if not team_season.empty else None,
    }
    (OUT / "validation.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    con = duckdb.connect(str(DB_PATH))
    con.register("res_driver_race", driver_race)
    con.register("res_team_season", team_season)
    con.execute("CREATE OR REPLACE TABLE res_driver_race AS SELECT * FROM res_driver_race")
    con.execute("CREATE OR REPLACE TABLE res_team_season AS SELECT * FROM res_team_season")
    con.close()

    print("\n=== validation ===")
    print(f"  races modelled   : {summary['races_modelled']}")
    print(f"  races skipped    : {summary['races_skipped']}")
    print(f"  mean RMSE        : {summary['mean_rmse_s']:.3f} s")
    print(f"  mean Spearman    : {summary['mean_spearman']:.3f}")
    print(f"  within 2 places  : {summary['mean_within_2_positions']:.1%}")
    print(f"  total regret     : {summary['total_regret_points']:.0f} points")

    print("\n=== teams by points lost to strategy ===")
    print(team_season.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
