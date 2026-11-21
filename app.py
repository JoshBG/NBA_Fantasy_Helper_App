"""Fantasy NBA weekly lineup helper.  Run:  streamlit run app.py"""
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from nba_api.stats.endpoints import commonallplayers, leaguedashplayerstats, playerindex, scheduleleaguev2

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
ROSTER_FILE = DATA / "roster.json"
SLOTS = ["PG", "SG", "SF", "PF", "C", "UTIL"]
COUNT = ["PTS", "REB", "AST", "STL", "BLK", "FG3M"]
SHOOT = ["FGM", "FGA", "FTM", "FTA"]
SCHED_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"

today = date.today()
SEASON = f"{today.year}-{str(today.year + 1)[2:]}" if today.month >= 10 else f"{today.year - 1}-{str(today.year)[2:]}"
LAST_SEASON = f"{int(SEASON[:4]) - 1}-{SEASON[2:4]}"

# ---------- cache helpers (files in ./data; auto-refresh when older than max_age hours) ----------
def fresh(path, max_age):
    return path.exists() and (max_age is None or time.time() - path.stat().st_mtime < max_age * 3600)

def retry(fn, tries=3):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))

def load_players(force=False):
    p = DATA / "players.csv"
    if force or not fresh(p, 24):
        df = retry(lambda: commonallplayers.CommonAllPlayers(
            is_only_current_season=1, league_id="00", season=SEASON, timeout=60).get_data_frames()[0])
        df = df[["PERSON_ID", "DISPLAY_FIRST_LAST", "TEAM_ABBREVIATION"]]
        df.columns = ["id", "name", "team"]
        df.to_csv(p, index=False)
    return pd.read_csv(p).fillna("")

# NBA lists coarse positions (G, F, C, G-F, F-C); map them to the likely fantasy slots
SLOT_MAP = {"G": "PG/SG", "F": "SF/PF", "C": "C", "G-F": "SG/SF", "F-G": "SG/SF", "F-C": "PF/C", "C-F": "PF/C"}

def load_positions(force=False):
    p = DATA / "positions.csv"
    try:
        if force or not fresh(p, 24 * 7):
            df = retry(lambda: playerindex.PlayerIndex(league_id="00", season=SEASON, timeout=60).get_data_frames()[0])
            df = df[["PERSON_ID", "POSITION"]]
            df.columns = ["id", "pos"]
            df.to_csv(p, index=False)
    except Exception:
        if not p.exists():
            return {}
    df = pd.read_csv(p).fillna("")
    return {i: SLOT_MAP.get(x, x) for i, x in zip(df["id"], df["pos"])}

STATS_MODES = {"Last season": (LAST_SEASON, 0), "Last 10 games": (SEASON, 10), "This season": (SEASON, 0)}

def stats_path(mode):
    season, last_n = STATS_MODES[mode]
    return DATA / f"stats_v3_{season}_{last_n}.csv"

def pulled(path):
    """When the cache file was last written, i.e. when the app last pulled that data."""
    if not path.exists():
        return "Not yet pulled"
    dt = datetime.fromtimestamp(path.stat().st_mtime)
    return f"{dt:%b} {dt.day}, {dt.hour % 12 or 12}:{dt:%M} {dt:%p}"

def load_stats(mode, force=False):
    season, last_n = STATS_MODES[mode]
    p = stats_path(mode)
    if force or not fresh(p, None if season == LAST_SEASON else 12):
        df = retry(lambda: leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame", last_n_games=last_n, timeout=60).get_data_frames()[0])
        df[["PLAYER_ID", "GP", "MIN"] + COUNT + SHOOT].to_csv(p, index=False)
    return pd.read_csv(p).set_index("PLAYER_ID")

def load_schedule(force=False):
    """Returns {team_tricode: set of game dates} for regular-season games."""
    p = DATA / f"schedule_{SEASON}.json"
    if force or not fresh(p, 24):
        try:
            r = requests.get(SCHED_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            raw = r.json()
        except Exception:
            raw = retry(lambda: scheduleleaguev2.ScheduleLeagueV2(season=SEASON, timeout=60).get_dict())
        p.write_text(json.dumps(raw))
    raw = json.loads(p.read_text())
    games = {}
    for day in raw["leagueSchedule"]["gameDates"]:
        for g in day["games"]:
            if not str(g["gameId"]).startswith("002"):  # regular season only
                continue
            d = datetime.strptime(g["gameDateEst"][:10], "%Y-%m-%d").date()
            for side in ("homeTeam", "awayTeam"):
                games.setdefault(g[side]["teamTricode"], set()).add(d)
    return games

# ---------- roster persistence ----------
def read_state():
    if ROSTER_FILE.exists():
        return json.loads(ROSTER_FILE.read_text())
    return {"roster": [], "lineups": {}, "overrides": {}}

def write_state(s):
    ROSTER_FILE.write_text(json.dumps(s, indent=2))

# ---------- calculations ----------
def week_info(team, sched, start):
    days = [start + timedelta(d) for d in range(7)]
    played = sched.get(team, set())
    games = [d for d in days if d in played]
    b2b = sum(1 for d in games if d - timedelta(1) in played)  # 2nd night of a back-to-back
    return games, b2b

def default_week():
    mon = today - timedelta(days=today.weekday())
    return mon + timedelta(7) if today.weekday() == 6 else mon  # on Sunday, plan next week

def team_games_played(team, sched):
    """Regular-season games on the schedule dated before today (tonight's game not counted)."""
    return sum(1 for d in sched.get(team, set()) if d < today)

def top_pool(stats, size):
    """Top players by minutes, ignoring players with very few games."""
    elig = stats[stats["GP"] >= max(1, 0.3 * stats["GP"].max())]
    return elig.nlargest(size, "MIN")

def benchmark(stats, sched, week, pool_size, n_starters):
    """Weekly totals for n_starters 'typical starters': average of the top players by minutes
    (ignoring players with very few games), times the average games per team that week."""
    if stats.empty:
        return None
    pool = top_pool(stats, pool_size)
    if pool.empty:
        return None
    team_games = [len(week_info(t, sched, week)[0]) for t in sched]
    avg_games = sum(team_games) / len(team_games) if team_games else 0
    return pool[COUNT + SHOOT].mean() * avg_games * n_starters, len(pool), avg_games

def pct(made, att):
    return made / att if att else 0.0

SHADE_COLS = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "3PM": "FG3M"}
PCT_COLS = [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]
CATS = list(SHADE_COLS) + [p[0] for p in PCT_COLS]

def cat_values(tot):
    v = {lab: tot[col] for lab, col in SHADE_COLS.items()}
    for lab, mk, at in PCT_COLS:
        v[lab] = pct(tot[mk], tot[at])
    return v

def shade(x, scale):
    """Green above / red below, strength grows until |x| reaches scale."""
    x = max(-1.0, min(1.0, x / scale))
    rgb = "46,160,67" if x > 0 else "215,58,73"
    return f"background-color: rgba({rgb},{abs(x) * 0.55:.2f})"

def styled(v, d, bm1):
    """Style a table: stat cells shaded vs one typical starter's weekly numbers (bm1).
    Rows 0..len(d)-1 of v line up with the rows of d; extra rows (e.g. a total) stay unshaded."""
    v = v.reset_index(drop=True)
    css = pd.DataFrame("", index=v.index, columns=v.columns)
    if bm1 is not None:
        for pos, (_, r) in enumerate(d.iterrows()):
            for lab, col in SHADE_COLS.items():
                if bm1[col]:
                    css.iloc[pos, css.columns.get_loc(lab)] = shade(r[col] / bm1[col] - 1, 0.5)
            for lab, mk, at in PCT_COLS:  # makes above/below what the typical shooting % would give on these attempts
                if bm1[mk]:
                    css.iloc[pos, css.columns.get_loc(lab)] = shade((r[mk] - pct(bm1[mk], bm1[at]) * r[at]) / bm1[mk], 0.15)
    fmt = {**{c: "{:.1f}" for c in SHADE_COLS}, "FG%": "{:.3f}", "FT%": "{:.3f}",
           **{c: "{:.0f}" for c in ["GP", "Team GP", "Games", "B2B"]}}
    return v.style.apply(lambda _: css, axis=None).format(fmt, na_rep="")

def z_scores(stats, sched, week, teams, pool_size, roster_w):
    """Category z-scores of weekly projections vs a pool of top players on their real schedules."""
    if stats.empty:
        return None
    pool = top_pool(stats, pool_size)
    if pool.empty:
        return None
    games = pd.Series([len(week_info(teams.get(i, ""), sched, week)[0]) for i in pool.index], index=pool.index)
    pw = pool[COUNT + SHOOT].mul(games, axis=0)
    z = pd.DataFrame(index=roster_w.index)
    for lab, col in SHADE_COLS.items():
        sd = pw[col].std()
        z[lab] = (roster_w[col] - pw[col].mean()) / sd if sd else 0.0
    for lab, mk, at in PCT_COLS:  # volume-weighted: makes above expected at the pool's percentage
        p = pct(pw[mk].sum(), pw[at].sum())
        impact = pw[mk] - p * pw[at]
        sd = impact.std()
        z[lab] = ((roster_w[mk] - p * roster_w[at]) - impact.mean()) / sd if sd else 0.0
    z["Score"] = z.sum(axis=1)
    return z

# ---------- UI ----------
st.set_page_config(page_title="Fantasy NBA Lineup", layout="wide")
state = read_state()
state.setdefault("overrides", {})
state.setdefault("settings", {})
state.setdefault("lineups", {})
if "lineup" in state:  # migrate the old single lineup into Lineup 1
    state["lineups"].setdefault("1", state.pop("lineup"))
for old_wk, old_ids in state.pop("out", {}).items():  # migrate old "out" flags to 0-game overrides
    state["overrides"].setdefault(old_wk, {}).update({str(i): 0 for i in old_ids})

with st.sidebar:
    st.header("Settings")
    if "week" not in st.session_state:
        st.session_state.week = default_week()
    c1, c2, c3 = st.columns(3)
    if c1.button("◀"):
        st.session_state.week -= timedelta(7)
    if c2.button("Now"):
        st.session_state.week = default_week()
    if c3.button("▶"):
        st.session_state.week += timedelta(7)
    week = st.session_state.week
    st.write(f"**{week:%a %b %d} – {week + timedelta(6):%a %b %d, %Y}**")
    mode = st.radio("Averages", ["This season", "Last 10 games", "Last season"])
    teams_n = int(st.number_input("League teams (for benchmark)", 4, 30, int(state["settings"].get("teams", 10))))
    if teams_n != state["settings"].get("teams"):
        state["settings"]["teams"] = teams_n
        write_state(state)
    if st.button("Refresh data from NBA"):
        with st.spinner("Fetching…"):
            load_players(True); load_schedule(True); load_stats(mode, True); load_positions(True)
        st.success("Updated")

try:
    players = load_players()
    sched = load_schedule()
    stats = load_stats(mode)
    season_stats = load_stats("This season")  # GP column always uses this season
except Exception as e:
    st.error(f"Couldn't load data: {e}")
    st.stop()

slots = load_positions()

with st.sidebar:  # drawn after loading so first-run pulls show up immediately
    st.subheader("Data last pulled")
    sched_path = DATA / f"schedule_{SEASON}.json"
    st.caption(f"Player stats ({mode}): {pulled(stats_path(mode))}")
    st.caption(f"Schedule: {pulled(sched_path)}")
    st.caption(f"Player list: {pulled(DATA / 'players.csv')}")
    st.caption(f"Positions: {pulled(DATA / 'positions.csv')}")

if stats.empty:
    st.warning(f"No stats yet for '{mode}'. Try 'Last season'.")

names = dict(zip(players["id"], players["name"]))
teams = dict(zip(players["id"], players["team"]))
for pl in state["roster"]:  # keep saved players even if missing from the current list
    names.setdefault(pl["id"], pl["name"]); teams.setdefault(pl["id"], "")
label = lambda i: f"{names[i]} ({teams[i] or '—'}) {slots.get(i, '')}"

tab_week, tab_roster = st.tabs(["Week", "Roster"])

with tab_roster:
    saved_ids = [p["id"] for p in state["roster"]]
    picked = st.multiselect("Your team (up to 13)", sorted(names, key=names.get), default=saved_ids,
                            format_func=label, max_selections=13)
    if picked != saved_ids:
        state["roster"] = [{"id": i, "name": names[i]} for i in picked]
        state["lineups"] = {n: {k: v for k, v in l.items() if v in picked} for n, l in state["lineups"].items()}
        write_state(state)
        st.rerun()

roster = state["roster"]
ids = [p["id"] for p in roster]

with tab_week:
    if not ids:
        st.info("Pick your players in the Roster tab.")
        st.stop()

    wk = week.isoformat()
    sched_info = {i: week_info(teams[i], sched, week) for i in ids}
    saved = state["overrides"].get(wk, {})
    edit_df = pd.DataFrame({
        "Player": [names[i] for i in ids],
        "Days": [", ".join(f"{d:%a}" for d in sched_info[i][0]) for i in ids],
        "Scheduled": [len(sched_info[i][0]) for i in ids],
        "B2B": [sched_info[i][1] for i in ids],
        "Playing": [min(int(saved.get(str(i), len(sched_info[i][0]))), len(sched_info[i][0])) for i in ids],
    }, index=ids)
    st.subheader("Games this week")
    st.caption("Edit **Playing** to adjust a player's expected games (0 = out). Saved for this week only.")
    edited = st.data_editor(
        edit_df, hide_index=True, use_container_width=True, key=f"games_{wk}",
        disabled=["Player", "Days", "Scheduled", "B2B"],
        column_config={"Playing": st.column_config.NumberColumn(min_value=0, max_value=7, step=1)})
    playing = {i: int(min(max(v, 0), edit_df.loc[i, "Scheduled"])) if pd.notna(v) else int(edit_df.loc[i, "Scheduled"])
               for i, v in edited["Playing"].items()}
    new_over = {str(i): n for i, n in playing.items() if n != edit_df.loc[i, "Scheduled"]}
    if new_over != saved:
        state["overrides"][wk] = new_over
        cutoff = (today - timedelta(14)).isoformat()  # drop old weeks
        state["overrides"] = {k: v for k, v in state["overrides"].items() if k >= cutoff and v}
        write_state(state)

    rows = []
    for i in ids:
        games, b2b = sched_info[i]
        n = playing[i]
        b = 0 if n == 0 else b2b  # B2B shows the scheduled value unless the player is out
        days = ", ".join(f"{d:%a}" for d in games)
        s = stats.loc[i] if i in stats.index else None
        row = {"id": i, "Player": names[i], "Team": teams[i], "Pos": slots.get(i, ""), "Games": n, "B2B": b,
               "Days": "OUT" if n == 0 else days + (f" (playing {n})" if n < len(games) else ""),
               "ModeGP": int(s["GP"]) if s is not None else 0,
               "Season GP": int(season_stats.loc[i, "GP"]) if i in season_stats.index else 0,
               "Team GP": team_games_played(teams[i], sched)}
        for c in COUNT + SHOOT:
            row[c] = (s[c] if s is not None else 0.0) * n
        rows.append(row)
    df = pd.DataFrame(rows).set_index("id")
    if (df["ModeGP"] == 0).any():
        st.warning("No '" + mode + "' stats for: " + ", ".join(df[df["ModeGP"] == 0]["Player"]) + " (projected as zeros).")

    st.subheader("Starting lineup")
    active = st.radio("Editing", ["1", "2", "3"], horizontal=True, format_func=lambda n: f"Lineup {n}", key="active_lineup")
    saved_l = state["lineups"].get(active, {})

    cols = st.columns(len(SLOTS))
    lineup = {}
    for col, slot in zip(cols, SLOTS):
        key = f"slot_{active}_{slot}"
        if st.session_state.get(key, saved_l.get(slot)) not in ids:
            st.session_state[key] = None
        elif key not in st.session_state:
            st.session_state[key] = saved_l.get(slot)
        lineup[slot] = col.selectbox(slot, [None] + ids, key=key, format_func=lambda i: "—" if i is None else f"{names[i]} {slots.get(i, '')}")
    chosen = [i for i in lineup.values() if i]
    if len(chosen) != len(set(chosen)):
        st.warning("A player is in more than one slot.")
    if {k: v for k, v in lineup.items() if v} != saved_l:
        state["lineups"][active] = {k: v for k, v in lineup.items() if v}
        write_state(state)

    def copy_lineup(src, dst):
        state["lineups"][dst] = dict(state["lineups"].get(src, {}))
        for slot in SLOTS:  # drop stale widget values so the target reloads from the saved copy
            st.session_state.pop(f"slot_{dst}_{slot}", None)
        write_state(state)
        st.session_state["active_lineup"] = dst  # jump to the copy

    cc1, cc2 = st.columns([3, 1])
    dst = cc1.selectbox("Copy this lineup to", [n for n in ["1", "2", "3"] if n != active], key=f"copy_dst_{active}",
                        format_func=lambda n: f"Lineup {n}" + (" (has players, will be replaced)" if state["lineups"].get(n) else " (empty)"))
    cc2.button("Copy", on_click=copy_lineup, args=(active, dst), key=f"copy_{active}", disabled=not chosen)

    def view(d):
        out = d[["Player", "Team", "Pos", "Season GP", "Team GP", "Games", "B2B", "Days"]].rename(columns={"Season GP": "GP"})
        out["PTS"], out["REB"], out["AST"] = d["PTS"], d["REB"], d["AST"]
        out["STL"], out["BLK"], out["3PM"] = d["STL"], d["BLK"], d["FG3M"]
        out["FG%"] = [pct(m, a) for m, a in zip(d["FGM"], d["FGA"])]
        out["FT%"] = [pct(m, a) for m, a in zip(d["FTM"], d["FTA"])]
        out["FGM/FGA"] = [f"{m:.1f}/{a:.1f}" for m, a in zip(d["FGM"], d["FGA"])]
        out["FTM/FTA"] = [f"{m:.1f}/{a:.1f}" for m, a in zip(d["FTM"], d["FTA"])]
        return out

    cfg = {c: st.column_config.NumberColumn(format="%.1f") for c in ["PTS", "REB", "AST", "STL", "BLK", "3PM"]}
    cfg.update({c: st.column_config.NumberColumn(format="%.3f") for c in ["FG%", "FT%"]})
    cfg.update({c: st.column_config.NumberColumn(format="%d") for c in ["GP", "Team GP"]})
    b1 = benchmark(stats, sched, week, teams_n * len(SLOTS), 1)  # one typical starter, for shading
    bm1 = b1[0] if b1 else None

    start = df.loc[chosen] if chosen else df.iloc[0:0]
    tot = start[COUNT + SHOOT].sum()
    st.subheader("Projected weekly totals (starters)")
    m = st.columns(10)
    m[0].metric("Games", int(start["Games"].sum()))
    m[1].metric("B2Bs", int(start["B2B"].sum()))
    for c, (lab, v) in zip(m[2:], [("PTS", tot["PTS"]), ("REB", tot["REB"]), ("AST", tot["AST"]), ("STL", tot["STL"]),
                                    ("BLK", tot["BLK"]), ("3PM", tot["FG3M"])]):
        c.metric(lab, f"{v:.1f}")
    m[8].metric("FG%", f"{pct(tot['FGM'], tot['FGA']):.3f}")
    m[9].metric("FT%", f"{pct(tot['FTM'], tot['FTA']):.3f}")

    if chosen:
        st.subheader("Starters and team total")
        sv = view(start)
        sv.insert(0, "Slot", [next(sl for sl, v in lineup.items() if v == i) for i in start.index])
        total = start.sum(numeric_only=True).to_frame().T
        total[["Player", "Team", "Pos", "Days"]] = ["TEAM TOTAL", "", "", ""]
        total["Season GP"] = float("nan")
        total["Team GP"] = float("nan")
        tv = view(total)
        tv.insert(0, "Slot", "")
        st.dataframe(styled(pd.concat([sv, tv]), start, bm1), hide_index=True, use_container_width=True)

    if chosen:
        st.subheader("Lineup vs typical starters")
        bench = benchmark(stats, sched, week, teams_n * len(SLOTS), len(chosen))
        if bench is None:
            st.info("No stats for this averages setting yet, so there is nothing to compare against.")
        else:
            bm, pool_n, avg_games = bench
            comp = []
            for lab, col in [("PTS", "PTS"), ("REB", "REB"), ("AST", "AST"), ("STL", "STL"), ("BLK", "BLK"), ("3PM", "FG3M")]:
                comp.append((lab, tot[col], bm[col], f"{tot[col]:.1f}", f"{bm[col]:.1f}"))
            for lab, mk, at in [("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")]:
                mine, ref = pct(tot[mk], tot[at]), pct(bm[mk], bm[at])
                comp.append((lab, mine, ref, f"{mine:.3f}", f"{ref:.3f}"))
            cdf = pd.DataFrame(comp, columns=["Category", "m", "r", "Your lineup", "Typical starters"])
            cdf["vs benchmark %"] = [(m / r - 1) * 100 if r else 0.0 for m, r in zip(cdf["m"], cdf["r"])]
            cdf["Verdict"] = ["Strong" if v > (3 if c.endswith("%") else 10) else
                              "Weak" if v < -(3 if c.endswith("%") else 10) else "Even"
                              for c, v in zip(cdf["Category"], cdf["vs benchmark %"])]
            st.dataframe(cdf[["Category", "Your lineup", "Typical starters", "vs benchmark %", "Verdict"]],
                         hide_index=True, use_container_width=True,
                         column_config={"vs benchmark %": st.column_config.NumberColumn(format="%+.1f")})
            st.bar_chart(cdf.set_index("Category")["vs benchmark %"])
            st.caption(f"Benchmark: average of the top {pool_n} players by minutes ({mode}), over {avg_games:.1f} games "
                       f"(the average team's games this week), for {len(chosen)} starters. "
                       "Strong/Weak = more than 10% above/below (3% for FG% and FT%).")

    filled = {n: [i for i in l.values() if i in df.index] for n, l in sorted(state["lineups"].items())}
    filled = {n: c for n, c in filled.items() if c}
    if filled:
        st.subheader("Compare lineups")
        table = {}
        for n, c in filled.items():
            vals = cat_values(df.loc[c, COUNT + SHOOT].sum())
            bn = benchmark(stats, sched, week, teams_n * len(SLOTS), len(c))
            above = sum(vals[k] > v for k, v in cat_values(bn[0]).items()) if bn else float("nan")
            table[f"Lineup {n}"] = {"Games": df.loc[c, "Games"].sum(), "B2B": df.loc[c, "B2B"].sum(), **vals,
                                    "Categories above benchmark": above}
        comp_df = pd.DataFrame(table)
        sty = (comp_df.style
               .format("{:.1f}", subset=pd.IndexSlice[list(SHADE_COLS), :])
               .format("{:.3f}", subset=pd.IndexSlice[["FG%", "FT%"], :])
               .format("{:.0f}", subset=pd.IndexSlice[["Games", "B2B", "Categories above benchmark"], :], na_rep="–"))
        if len(filled) > 1:
            sty = sty.highlight_max(axis=1, color="rgba(46,160,67,0.35)",
                                    subset=pd.IndexSlice[["Games"] + CATS + ["Categories above benchmark"], :])
        st.dataframe(sty, use_container_width=True)
        st.caption("Green = best lineup in that row. 'Categories above benchmark' counts categories where the lineup beats "
                   "the typical-starter benchmark. Your Playing numbers apply to every lineup.")

    st.subheader("Roster ranking for the week")
    z = z_scores(stats, sched, week, teams, teams_n * 13, df[COUNT + SHOOT])
    if z is None:
        st.info("No stats for this averages setting yet, so players can't be ranked.")
    else:
        rk = z.copy()
        rk.insert(0, "Player", df["Player"])
        rk.insert(1, "Pos", df["Pos"])
        rk.insert(2, "Games", df["Games"])
        rk.insert(3, "In lineup", [next((sl for sl, v in lineup.items() if v == i), "") for i in rk.index])
        rk = rk.sort_values("Score", ascending=False)
        rk.insert(0, "Rank", range(1, len(rk) + 1))
        rk = rk.reset_index(drop=True)
        zcols = CATS + ["Score"]
        css = pd.DataFrame("", index=rk.index, columns=rk.columns)
        for c in zcols:
            css[c] = [shade(v, 6.0 if c == "Score" else 2.0) for v in rk[c]]
        fmt = {**{c: "{:+.1f}" for c in zcols}, "Games": "{:.0f}"}
        st.dataframe(rk.style.apply(lambda _: css, axis=None).format(fmt), hide_index=True, use_container_width=True)
        st.caption(f"Score = sum of category z-scores for this week's projection (average x games) against the top "
                   f"{teams_n * 13} players by minutes on their real schedules. FG% and FT% are volume-weighted. "
                   "Positions and your opponent are not considered. 'In lineup' shows the lineup you're editing.")

    st.subheader(f"All players — {mode} averages × games")
    full = view(df)
    full.insert(0, "Slot", [next((s for s, v in lineup.items() if v == i), "") for i in df.index])
    st.dataframe(styled(full, df, bm1), hide_index=True, use_container_width=True)
    st.caption("Shading: each projected stat vs one typical starter over the same week (green above, red below). "
               "FG% and FT% shading weighs attempts, so volume matters.")