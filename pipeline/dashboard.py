#!/usr/bin/env python3
"""
Build the static, browser-viewable dashboard from the model outputs.

A .pbix cannot be opened on GitHub, so the published artifact is a single
self-contained HTML page rendered with Plotly. The Power BI rebuild mapping is
documented in docs/powerbi-rebuild.md.
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd
import plotly.graph_objects as go

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
OUT = ROOT / "dashboard" / "index.html"

TEAM_COLOURS = {
    "Red Bull Racing": "#3671C6",
    "Ferrari": "#E8002D",
    "McLaren": "#FF8000",
    "Mercedes": "#27F4D2",
    "Aston Martin": "#229971",
    "Alpine": "#FF87BC",
    "Williams": "#64C4FF",
    "RB": "#6692FF",
    "Kick Sauber": "#52E252",
    "Haas F1 Team": "#B6BABD",
}

CONFIG = {"displayModeBar": False, "responsive": True}


def ranking_figure(team_season: pd.DataFrame) -> go.Figure:
    colours = [TEAM_COLOURS.get(t, "#888888") for t in team_season["team"]]
    fig = go.Figure(go.Bar(
        x=team_season["regret_points"],
        y=team_season["team"],
        orientation="h",
        marker=dict(color=colours),
        text=[f"{v:.0f}" for v in team_season["regret_points"]],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>%{x:.0f} points lost<extra></extra>",
    ))
    fig.update_layout(
        xaxis_title="Points lost (hindsight optimum)",
        template="plotly_white",
        height=420,
        margin=dict(l=150, r=50, t=20, b=50),
        showlegend=False,
        font=dict(family="Arial, Helvetica, sans-serif"),
    )
    return fig


def heatmap_figure(driver_race: pd.DataFrame, team_order: list[str]) -> go.Figure:
    pivot = (driver_race.groupby(["team", "location"])["regret_points"].sum()
             .reset_index()
             .pivot(index="team", columns="location", values="regret_points")
             .fillna(0))
    race_order = (driver_race[["round", "location"]].drop_duplicates()
                  .sort_values("round")["location"].tolist())
    pivot = pivot[[c for c in race_order if c in pivot.columns]]
    pivot = pivot.reindex(team_order[::-1])

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=pivot.columns.tolist(),
        y=pivot.index.tolist(),
        colorscale="YlOrRd",
        colorbar=dict(title="pts"),
        hovertemplate="<b>%{y}</b><br>%{x}<br>%{z:.0f} points lost<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white",
        height=460,
        margin=dict(l=150, r=40, t=20, b=120),
        xaxis=dict(tickangle=-45),
        font=dict(family="Arial, Helvetica, sans-serif"),
    )
    return fig


def biggest_figure(driver_race: pd.DataFrame) -> go.Figure:
    top = driver_race.sort_values("regret_points", ascending=False).head(10).copy()
    top["label"] = (top["driver_number"].astype(str) + " \u2014 " + top["location"])
    fig = go.Figure(go.Bar(
        x=top["regret_points"],
        y=top["label"],
        orientation="h",
        marker=dict(color=[TEAM_COLOURS.get(t, "#888888") for t in top["team"]]),
        customdata=top[["actual_stops", "best_stops"]].values,
        hovertemplate="<b>%{y}</b><br>%{x:.0f} points lost<br>"
                      "actual stops: %{customdata[0]}<br>"
                      "model best: %{customdata[1]} stops<extra></extra>",
    ))
    fig.update_layout(
        xaxis_title="Points lost",
        template="plotly_white",
        height=430,
        margin=dict(l=40, r=40, t=20, b=50),
        yaxis=dict(autorange="reversed"),
        font=dict(family="Arial, Helvetica, sans-serif"),
    )
    return fig


def main() -> int:
    driver_race = pd.read_csv(PROC / "driver_race_regret.csv")
    team_season = pd.read_csv(PROC / "team_season.csv")
    summary = json.loads((PROC / "validation.json").read_text(encoding="utf-8"))

    team_season = team_season.sort_values("regret_points", ascending=False)
    team_order = team_season["team"].tolist()
    team_season = team_season.sort_values("regret_points", ascending=True)

    ranking = ranking_figure(team_season)
    heat = heatmap_figure(driver_race, team_order)
    biggest = biggest_figure(driver_race)

    top = summary["top_team"]
    headline = (f"{top['team']} lost the most points to strategy "
                f"({top['regret_points']:.0f} points) across "
                f"{summary['races_modelled']} modelled races")

    def block(fig: go.Figure, title: str) -> str:
        return (
            f'<section><h2>{title}</h2>'
            + fig.to_html(full_html=False, include_plotlyjs=False, config=CONFIG)
            + "</section>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>F1 Strategy Regret Index</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #ffffff; color: #1a1a1a;
         font-family: Arial, Helvetica, sans-serif; }}
  header {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 4px; }}
  h1 {{ margin: 0 0 6px; font-size: 27px; letter-spacing: -0.3px; }}
  .question {{ margin: 0 0 14px; font-size: 16px; color: #555; }}
  .headline {{ margin: 0 0 14px; font-size: 17px; }}
  .method {{ margin: 0; font-size: 13px; line-height: 1.55; color: #666;
             border-left: 3px solid #ddd; padding-left: 12px; }}
  main {{ max-width: 1080px; margin: 0 auto; padding: 8px 20px 40px; }}
  section {{ margin-top: 26px; }}
  h2 {{ font-size: 15px; font-weight: 600; text-transform: uppercase;
        letter-spacing: 0.06em; color: #333; margin: 0 0 6px; }}
  footer {{ max-width: 1080px; margin: 0 auto; padding: 0 20px 50px;
            font-size: 12px; color: #777; }}
  a {{ color: #0b62c4; }}
</style>
</head>
<body>
<header>
  <h1>F1 Strategy Regret Index</h1>
  <p class="question">Which team threw away the most points on strategy in 2024?</p>
  <p class="headline"><b>{headline}.</b></p>
  <p class="method">
    Each driver's race is replayed under alternative 1- and 2-stop strategies,
    holding the rest of the field fixed. Tyre degradation and pit loss are
    estimated from the race data itself, separately for green-flag and
    safety-car stops. The best available strategy sets the benchmark;
    regret is the points difference between that benchmark and what the driver
    actually scored. The replay reproduces the real finishing order with a mean
    Spearman rank correlation of {summary['mean_spearman']:.2f}.
    It is a hindsight benchmark, not a prediction - see the README for
    limitations.
  </p>
</header>
<main>
{block(ranking, "Points lost to strategy, 2024 season")}
{block(heat, "Where the points were lost")}
{block(biggest, "Biggest single-race regrets")}
</main>
<footer>
  Data: OpenF1 (laps, stints, pit stops, positions, race control) &middot;
  {summary['races_modelled']} races modelled, {summary['races_skipped']} skipped &middot;
  Full method and code in the repository README.
</footer>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
