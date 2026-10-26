# Fantasy NBA Weekly Lineup Helper

A local Streamlit app for setting weekly fantasy NBA lineups in a category league. For any Monday–Sunday week it shows how many games each player has, how many back-to-backs, and projected weekly totals (per-game average × games).

## Features

- Pick up to 13 players from the current NBA player list; the roster is saved between sessions
- Choose any Monday–Sunday week
- Per-player games, back-to-backs, and game days for the week
- Projected PTS, REB, AST, STL, BLK, 3PM, FG%, FT%
  - FG% and FT% are computed from projected makes and attempts, not averaged
- Set your own starters: PG, SG, SF, PF, C, UTIL
- Team total for your six starters: summed counting stats, with FG% and FT% from combined makes and attempts
- Mark players out for the week (zeroes their games)
- Toggle averages between this season, last 10 games, and last season
- Sidebar shows when each data source (stats, schedule, player list, positions) was last pulled
- Likely lineup slots shown next to each player (approximate, based on NBA.com positions)

## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <your-repo-url>
cd fantasy-nba
uv sync
```

## Run

```bash
uv run streamlit run app.py
```

Streamlit opens the app in your browser (default: http://localhost:8501).

## Usage

1. **Roster tab:** choose your team (up to 13 players). It saves automatically.
2. **Week tab:**
   - Use the sidebar to pick the week (◀ / Now / ▶) and the averages source.
   - Mark any injured players under "Out this week".
   - Assign players to the six lineup slots.
   - Read the starters table (with a team total row) and the full-roster table below.
3. Click **Refresh data from NBA** in the sidebar to re-pull players, schedule, and stats. The "Data last pulled" section in the sidebar shows when each source was last fetched (it reflects your app's last pull, not when the NBA updated its data).

## Data

Data comes from NBA.com via the [`nba_api`](https://github.com/swar/nba_api) package, with no API key needed. Everything is cached locally in `data/`:

| File | Contents | Auto-refresh |
|------|----------|--------------|
| `roster.json` | Your roster, lineup slots, and out flags | n/a |
| `players.csv` | Current player list and teams | 24 hours |
| `positions.csv` | NBA.com positions | 7 days |
| `schedule_<season>.json` | Regular-season schedule | 24 hours |
| `stats_v2_<season>_<n>.csv` | Per-game stats | 12 hours (last season never) |

`data/` is gitignored, so your roster stays local.

## Notes

- **Back-to-backs** are counted as the second night of consecutive games, including across the Sunday–Monday week boundary.
- **Positions** are mapped from NBA.com's coarse labels (G → PG/SG, F → SF/PF, C → C, G-F → SG/SF, F-C → PF/C). They are a rough guide, not your league's official eligibility.
- **Early season:** "This season" and "Last 10 games" have no data until games are played, and rookies have no "Last season" stats. The app warns you and projects those players as zeros.
- On Sundays, the week defaults to the upcoming week.

## Project structure

```
fantasy-nba/
├── app.py
├── pyproject.toml
├── uv.lock
├── README.md
├── .gitignore
└── data/        # created automatically, not committed
```

## .gitignore

```
data/
__pycache__/
.venv/
*.pyc
```