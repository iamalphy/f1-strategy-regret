# F1 Strategy Regret Index — Design

**Date:** 2026-11-13
**Author:** Alphy Cheruvallathu Santhosh (with agent assistance)
**Status:** Draft for approval

---

## 1. Intent

Build a public, reproducible data-analytics project whose one-line hook is:

> **"Which F1 team threw away the most points on bad strategy in 2024?"**

The project exists to serve a specific career goal: make the CV's **SQL / Python / Power BI** claims provable. Today the Python work is backed by two public repos; the BI work has no public artifact at all. This project closes that gap while giving an interview-ready story.

Secondary goal: it must be **defensible under questioning**. Every number published must trace back to a stated method and a limitation.

## 2. Why this project (and not another)

Considered alternatives during brainstorming:

| Option | Verdict |
|---|---|
| Generic sales/retail dashboard | Rejected — indistinguishable from thousands of tutorials |
| UAE job-market analysis | Strong, but data access is uncertain and legally messy |
| Data-quality audit tool | Good, but reads as a utility, not an analysis story |
| **F1 strategy analysis** | **Chosen** — rich free data, memorable hook, real analytical depth, locally resonant (Yas Marina) |

Explicitly **not** claimed: global uniqueness. That is unverifiable and not what hiring managers reward. The differentiation is the *question*, not the dataset.

## 3. Goals

1. Produce a per-race, per-driver measure of **strategy regret**: points lost relative to the best strategy available under a stated model.
2. Aggregate to a **team ranking** across the 2024 season.
3. Publish a **browser-viewable dashboard** (a `.pbix` cannot be viewed on GitHub, so it cannot be the artifact).
4. Ship a **fully reproducible pipeline** (Docker + pinned Python) so a reviewer can rerun it.
5. Write a **README a recruiter can absorb in 60 seconds** — question, headline finding, one chart.

## 4. Non-goals

- Predicting race results or recommending live strategy.
- Modelling tyre behaviour beyond what the observed data supports.
- Full traffic/dirty-air simulation of all 20 cars (see §7 limitations).
- Covering multiple seasons. 2024 only, to keep scope tight.

## 5. Anti-goals (what would make this a failure)

- A notebook dump with no conclusion.
- A "predict the winner" ML project (naive, and widely disliked by technical interviewers).
- Publishing a "points lost" number without stating the assumptions behind it.
- An artifact a recruiter cannot open.

## 6. Approaches considered

**A. Single-driver replay against the observed field — CHOSEN.**
Re-run each driver's race with alternative pit laps and stop counts, holding every other driver's actual performance fixed. Pit loss and tyre degradation are measured from the race data itself. Safety-car windows are modelled explicitly, because a stop under a safety car costs a fraction of a green-flag stop.

- *Pro:* genuine modelling, finishable, explainable in an interview.
- *Con:* optimistic — it ignores second-order traffic effects. Stated openly as a limitation rather than papered over with an uncalibrated penalty (see §7.6 and §13).

**B. Full field simulation.** Simulate all 20 cars with interacting traffic and dirty air.
- *Pro:* most realistic.
- *Con:* research-grade. High risk of not shipping. Rejected on scope.

**C. Decision scoring.** Score each pit call against the cheapest possible pit window; no simulation.
- *Pro:* fast and robust.
- *Con:* "points lost" becomes a soft, vague metric. Rejected because the headline number must mean something.

**Decision:** A, chosen by the project owner.

## 7. Method (Approach A)

For each of the 24 races:

1. **Extract** — OpenF1 API session data: laps (sector times, pit-out flags), stints (compound, tyre age), pit stops, positions, race-control messages (safety car / VSC), weather. Requests are throttled and cached; the API enforces 3 requests/second.
2. **Clean** — remove in/out laps, laps under safety car / VSC / red flag, lap 1, and laps with missing or invalid times. Correct for fuel load using a per-lap linear term (documented, not hidden).
3. **Pace model** — fit lap time against tyre age and compound per driver-stint; derive a baseline pace per driver per race.
4. **Pit loss** — measure the time actually lost by a stop, per race: the pit-in lap plus the pit-out lap against the driver's own clean-flag pace. Estimated separately for green-flag stops and stops made under a safety car or VSC.
5. **Replay** — for each driver, evaluate every 1- and 2-stop strategy over the plausible pit-lap range, respecting the dry two-compound rule. A stop on a caution lap is costed at the measured caution pit loss, so "should have pitted under the safety car" is inside the search space. Score = tyre-age degradation + pit losses.
6. **Traffic** — deliberately not modelled. A single-driver replay cannot know how the field would react, and an uncalibrated traffic constant would add false precision to the headline number. The effect is disclosed as a limitation and bounded by the validation test instead.
7. **Regret** — `actual points − best replay points`, floored at zero. Reported per driver, per race, and summed per team.

### Validation (the credibility anchor)

- **Reproduction test:** replaying each driver's *actual* pit laps must approximately reproduce the *actual* finishing order. If it does not, the model is wrong and no results are published. This is the single most important check.
- **Sanity checks:** no regret in races with no strategic choice; regret distribution should be right-skewed (a few big errors, many small ones).
- **Qualitative check:** flagged "biggest regrets" should correspond to strategy calls that were publicly criticised.

### Known limitations (published in the README)

- Single-driver replay ignores second-order traffic interactions.
- Fuel correction is linear and approximate.
- Tyre model uses observed pace, not a physical tyre model.
- Pit loss is a per-circuit constant, not per-team.
- Regret is measured against a model optimum, not a proven optimum.

## 8. Deliverables

```
f1-strategy-regret/
├── README.md              # hook, headline finding, one chart, method, limitations
├── Dockerfile             # pinned Python 3.12 for reproducibility
├── Makefile               # make data / db / model / dashboard / verify
├── pipeline/
│   ├── extract.py         # OpenF1 download + throttle + cache
│   ├── build_db.py        # star schema, caution periods, pit-cycle loss
│   ├── model.py           # pace fit, strategy enumeration, regret scoring
│   ├── dashboard.py       # static Plotly page
│   ├── run_sql.py         # executes sql/queries.sql, writes results
│   └── verify.py          # credibility gate (non-zero exit if it fails)
├── sql/
│   └── queries.sql        # named analytical queries over the star schema
├── dashboard/
│   └── index.html         # generated, committed so GitHub Pages can serve it
├── data/                  # cached JSON + DuckDB (gitignored)
└── docs/
    ├── powerbi-rebuild.md # chart-by-chart Power BI mapping
    └── plans/             # this document + implementation plan
```

## 9. Dashboard content (recruiter-facing)

1. **Headline:** team ranking bar chart — points lost to strategy, 2024.
2. **Heatmap:** regret by team × race.
3. **Case study:** the ten biggest single-race regrets, with actual versus optimal stop counts.
4. **Method box:** three sentences a non-technical reader can follow, including the validation number.

## 10. Technology decisions (made by the builder, not the owner)

| Decision | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | System Python is 3.14 and PEP 668-locked; 3.12 has reliable wheels |
| Runtime | Docker | Reproducible for reviewers; also sidesteps the host Python |
| Data source | OpenF1 API | Free, no authentication, covers laps/stints/pit/positions/race control/weather for 2024 |
| Request handling | Throttled client + on-disk JSON cache | API enforces 3 requests/second; caching keeps reruns fast and offline-capable |
| Storage | DuckDB star schema | SQL is part of the CV claim, so the modelling layer is SQL-first rather than pandas-only |
| Dashboard | Static Plotly HTML | Browser-viewable, no Windows or Power BI licence needed |
| BI evidence | Power BI rebuild documented | Owner may rebuild the same charts in Power BI on Windows; `docs/powerbi-rebuild.md` maps it |

## 11. Success criteria

- Pipeline runs end-to-end from a clean clone with two commands.
- Reproduction test passes within a stated tolerance.
- README answers "what did you find?" in the first screen.
- Every published number has a stated method and limitation.
- Project is linked from the CV and can be discussed for 10 minutes without contradiction.

## 12. Data-availability evidence (verified 2026-11-13)

Before committing to the method, the data source was tested end-to-end.

| Check | Result |
|---|---|
| FastF1 session load | **Blocked** — timing endpoints now require an F1 account login; only the driver list loads, `laps` is empty |
| OpenF1 `/sessions?year=2024&session_name=Race` | 24 race sessions returned |
| OpenF1 `/laps` | Lap duration, three sector times, pit-out flag |
| OpenF1 `/stints` | Compound, tyre age at start, lap range |
| OpenF1 `/pit` | Pit stop records |
| OpenF1 `/position` | Running order |
| OpenF1 `/race_control` | 71 messages for Bahrain, including flags and categories |
| OpenF1 `/weather` | Air temperature, humidity, pressure, wind |
| Rate limit | 3 requests/second — the client must throttle |

**Consequence:** the pipeline is built on OpenF1, and the FastF1 attempt is documented in the README as a deliberate, evidenced choice rather than an omission.

## 13. Builder-check resolutions (decisions made by the builder)

The design was reviewed cold by an independent builder. These gaps were resolved without owner input:

| Gap raised | Resolution |
|---|---|
| **No SQL anywhere, despite SQL being a CV goal** | **Added.** A DuckDB SQL layer is now first-class: raw extracts land in staging tables, a star schema (fact_laps, dim_drivers, dim_races, fact_stints) is built in SQL, and the pace/replay inputs are produced by SQL queries. Python orchestrates; SQL does the modelling work. |
| Can an alternative strategy use an actual safety-car window? | **Yes.** "Did not pit under the safety car" is the most-criticised error in the sport; excluding it would systematically understate regret and fail the project's own qualitative check. |
| Ex-post or ex-ante optimum? | **Ex-post (hindsight optimum), stated plainly.** Labelled as such in the README and in the limitation section. Ex-ante would need a decision policy and uncertainty treatment — out of scope, and pretending otherwise would be worse. |
| Sprint weekends in scope? | **No.** Grands Prix only: 24 races, 24 results. Sprint points excluded to keep the ranking clean. |
| Fastest-lap point (2024) | **Removed from both sides** so it cannot create phantom regret. |
| Retirements, penalties, pit-lane starts | Retired drivers excluded from the regret ranking and from the re-rank field; grid and time penalties are inherent in the actual race time used as the re-ranking base; a pit-lane start is treated as last on the grid. |
| Substitute drivers (Bearman, Colapinto) | Points attributed to the team for the season total; shown as separate drivers in per-driver views. |
| Publishing derived data | Aggregates, charts and a small illustrative sample are published; the full lap-level cache stays local (keeps the repo clean of bulk data and avoids trademark concerns). |
| Backmarker bias in an absolute-points headline | **Added a secondary normalised metric:** regret per point scored, shown alongside the absolute ranking so lower-half teams are represented fairly. |

### 13.1 Decisions forced by the data (recorded after the build)

| Issue found | Resolution |
|---|---|
| The enumeration could recommend strategies that break the two-compound rule or run an impossible stint length | Strategy search replaced with a **dynamic programme over stint boundaries**, enforcing the two-compound rule, at least one stop, no stint longer than the longest stint any team actually ran in that race, and the driver's real starting compound. |
| The model recommended dry strategies in wet races | Wet share is now computed over **all stints**, not just clean laps; any race above 2% wet-tyre laps is excluded. |
| A missing lap time made a P17 car look faster than the winner (Shanghai) | Missing lap durations are **imputed with the driver's own median** before summing; drivers with more than two gaps are excluded. |
| Red-flag tyre changes were counted as strategic stops | Stops ending in the first two laps carry **no pit loss** and are not counted in the stop count. |
| Qatar's stint data reports 1–2 lap mid-race stints for 18 drivers | **Data-integrity gate:** a race is excluded when five or more drivers show that pattern. Qatar 2024 is excluded. |
| Caution pit loss measured against green pace gave 86 s | Pit loss under caution is measured against the pace of **cars that stayed out**, then expressed in green-flag-equivalent seconds (~17 s versus ~24 s green). |

## 14. Open questions for the owner

1. Repo name and public/private — suggest `f1-strategy-regret`, public.
2. Should the Power BI version be built by the owner on a Windows machine, or is the Plotly dashboard sufficient?
3. Willingness to publish a short write-up (LinkedIn post) linking the project — improves reach with recruiters.
