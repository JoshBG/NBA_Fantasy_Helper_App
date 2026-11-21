# Fantasy NBA Weekly Lineup Helper

A local Streamlit app for setting weekly fantasy NBA lineups in a category league. For any Monday–Sunday week it shows how many games each player has, how many back-to-backs, and projected weekly totals (per-game average × games).

## Features

- Pick up to 13 players from the current NBA player list; the roster is saved between sessions
- Choose any Monday–Sunday week
- Per-player games, back-to-backs, and game days for the week
- Projected PTS, REB, AST, STL, BLK, 3PM, FG%, FT%
  - FG% and FT% are computed from projected makes and attempts, not averaged
- Set your own starters: PG, SG, SF, PF, C, UTIL, in up to three saved lineups you can copy between and compare side by side
- Team total for your six starters: summed counting stats, with FG% and FT% from combined makes and attempts
- Color-shaded stat tables: each projected stat is shaded green or red against one typical starter's weekly numbers
- Roster ranking for the week: category z-scores on projected totals (average × games), summed into a score
- Compare lineups: side-by-side table of games, back-to-backs, and every category for each saved lineup, with the best lineup highlighted in each row and a count of categories above the typical-starter benchmark
- Lineup vs typical starters: per-category comparison (table and bar chart) against a benchmark built from NBA API stats, flagging strong and weak categories
- Override how many games each player will play that week (e.g., 4 scheduled, expecting 3); set 0 for an injured player
- Toggle averages between this season, last 10 games, and last season
- Games played this season (GP) and the team's games played so far (Team GP) next to each player, regardless of the averages toggle
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
   - Adjust the **Playing** column in "Games this week" for anyone you expect to miss games (0 = out). Projections use that number.
   - Pick Lineup 1, 2 or 3 and assign players to the six lineup slots (use "Copy this lineup to" to clone it into another slot, replacing that lineup).
   - Read the starters table (with a team total row) and the full-roster table below.
   - Check "Lineup vs typical starters" to see which categories are strong or weak. Set your league size in the sidebar.
   - Fill in up to three lineups, then use **Compare lineups** to see them side by side (it appears once at least one lineup has players). Use **Roster ranking** to see who projects best this week.
3. Click **Refresh data from NBA** in the sidebar to re-pull players, schedule, and stats. The "Data last pulled" section in the sidebar shows when each source was last fetched (it reflects your app's last pull, not when the NBA updated its data).

## Data

Data comes from NBA.com via the [`nba_api`](https://github.com/swar/nba_api) package, with no API key needed. Everything is cached locally in `data/`:

| File | Contents | Auto-refresh |
|------|----------|--------------|
| `roster.json` | Your roster, up to three lineups, weekly game overrides, and settings | n/a |
| `players.csv` | Current player list and teams | 24 hours |
| `positions.csv` | NBA.com positions | 7 days |
| `schedule_<season>.json` | Regular-season schedule | 24 hours |
| `stats_v3_<season>_<n>.csv` | Per-game stats | 12 hours (last season never) |

`data/` is gitignored, so your roster stays local.

## Notes

- **Benchmark:** the average of the top (league teams × 6) players by minutes, times the average games a team plays that week, for as many starters as you've selected. It follows the averages toggle. Minutes are only a rough proxy for fantasy value, and the comparison ignores your actual opponent and position scarcity.
- **Comparing lineups:** the same player can be in more than one lineup. Your Playing numbers apply to every lineup, and each lineup is measured against a benchmark scaled to its own number of starters. Rows are highlighted for the best value, except back-to-backs, which aren't ranked as good or bad.
- **Ranking and shading:** the ranking sums category z-scores against the top (league teams × 13) players by minutes, each on their real schedule that week. FG% and FT% are volume-weighted (makes above what the typical percentage would give on those attempts). Shading uses the same typical-starter benchmark. Positions and your opponent are ignored.
- **GP / Team GP:** GP is the player's games this season across all teams. Team GP counts the current team's scheduled games dated before today, so traded or recently signed players can look off.
- **Game overrides** are saved per week and cleared automatically after a couple of weeks. The B2B column keeps the scheduled value unless a player is set to 0 games.
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