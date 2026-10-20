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

def load_stats(mode, force=False):
    season, last_n = {"Last season": (LAST_SEASON, 0), "Last 10 games": (SEASON, 10), "This season": (SEASON, 0)}[mode]
    p = DATA / f"stats_v2_{season}_{last_n}.csv"
    if force or not fresh(p, None if season == LAST_SEASON else 12):
        df = retry(lambda: leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame", last_n_games=last_n, timeout=60).get_data_frames()[0])
        df[["PLAYER_ID", "GP"] + COUNT + SHOOT].to_csv(p, index=False)
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
    return {"roster": [], "lineup": {}, "out": {}}

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

def pct(made, att):
    return made / att if att else 0.0

# ---------- UI ----------
st.set_page_config(page_title="Fantasy NBA Lineup", layout="wide")
state = read_state()

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
    if st.button("Refresh data from NBA"):
        with st.spinner("Fetching…"):
            load_players(True); load_schedule(True); load_stats(mode, True); load_positions(True)
        st.success("Updated")

try:
    players = load_players()
    sched = load_schedule()
    stats = load_stats(mode)
except Exception as e:
    st.error(f"Couldn't load data: {e}")
    st.stop()

slots = load_positions()

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
        state["lineup"] = {k: v for k, v in state["lineup"].items() if v in picked}
        write_state(state)
        st.rerun()

roster = state["roster"]
ids = [p["id"] for p in roster]

with tab_week:
    if not ids:
        st.info("Pick your players in the Roster tab.")
        st.stop()

    wk = week.isoformat()
    out_saved = [i for i in state["out"].get(wk, []) if i in ids]
    out_ids = st.multiselect("Out this week (zeroes their games)", ids, default=out_saved, format_func=label)
    if out_ids != out_saved:
        state["out"] = {wk: out_ids}  # only keep the current week's flags
        write_state(state)

    rows = []
    for i in ids:
        games, b2b = week_info(teams[i], sched, week)
        out = i in out_ids
        n, b = (0, 0) if out else (len(games), b2b)
        s = stats.loc[i] if i in stats.index else None
        row = {"id": i, "Player": names[i], "Team": teams[i], "Pos": slots.get(i, ""), "Games": n, "B2B": b,
               "Days": "OUT" if out else ", ".join(f"{d:%a}" for d in games),
               "GP": int(s["GP"]) if s is not None else 0}
        for c in COUNT + SHOOT:
            row[c] = (s[c] if s is not None else 0.0) * n
        rows.append(row)
    df = pd.DataFrame(rows).set_index("id")
    if (df["GP"] == 0).any():
        st.warning("No '" + mode + "' stats for: " + ", ".join(df[df["GP"] == 0]["Player"]) + " (projected as zeros).")

    st.subheader("Starting lineup")
    cols = st.columns(len(SLOTS))
    lineup = {}
    for col, slot in zip(cols, SLOTS):
        key = f"slot_{slot}"
        if st.session_state.get(key, state["lineup"].get(slot)) not in ids:
            st.session_state[key] = None
        elif key not in st.session_state:
            st.session_state[key] = state["lineup"].get(slot)
        lineup[slot] = col.selectbox(slot, [None] + ids, key=key, format_func=lambda i: "—" if i is None else f"{names[i]} {slots.get(i, '')}")
    chosen = [i for i in lineup.values() if i]
    if len(chosen) != len(set(chosen)):
        st.warning("A player is in more than one slot.")
    if {k: v for k, v in lineup.items() if v} != state["lineup"]:
        state["lineup"] = {k: v for k, v in lineup.items() if v}
        write_state(state)

    def view(d):
        out = d[["Player", "Team", "Pos", "Games", "B2B", "Days"]].copy()
        out["PTS"], out["REB"], out["AST"] = d["PTS"], d["REB"], d["AST"]
        out["STL"], out["BLK"], out["3PM"] = d["STL"], d["BLK"], d["FG3M"]
        out["FG%"] = [pct(m, a) for m, a in zip(d["FGM"], d["FGA"])]
        out["FT%"] = [pct(m, a) for m, a in zip(d["FTM"], d["FTA"])]
        out["FGM/FGA"] = [f"{m:.1f}/{a:.1f}" for m, a in zip(d["FGM"], d["FGA"])]
        out["FTM/FTA"] = [f"{m:.1f}/{a:.1f}" for m, a in zip(d["FTM"], d["FTA"])]
        return out

    cfg = {c: st.column_config.NumberColumn(format="%.1f") for c in ["PTS", "REB", "AST", "STL", "BLK", "3PM"]}
    cfg.update({c: st.column_config.NumberColumn(format="%.3f") for c in ["FG%", "FT%"]})

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
        tv = view(total)
        tv.insert(0, "Slot", "")
        st.dataframe(pd.concat([sv, tv]), hide_index=True, use_container_width=True, column_config=cfg)

    st.subheader(f"All players — {mode} averages × games")
    full = view(df)
    full.insert(0, "Slot", [next((s for s, v in lineup.items() if v == i), "") for i in df.index])
    st.dataframe(full, hide_index=True, use_container_width=True, column_config=cfg)