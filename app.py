# app.py — Single-player BallDontLie Streamlit app
# ---------------------------------------------------------
# 🔑 PASTE YOUR KEY HERE (keep quotes)
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

import os
import time
import math
import datetime as dt
from typing import List, Dict, Optional
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NBA Projections — Single Player (BALLDONTLIE)", page_icon="🏀", layout="wide")

def resolve_api_key() -> str:
    hardcoded = (API_KEY_DEFAULT or "").strip()
    if hardcoded and hardcoded != "PASTE_YOUR_KEY_HERE":
        return hardcoded
    return ""

API_KEY = resolve_api_key()
BASE = "https://api.balldontlie.io/nba/v1"
HEADERS = {"Authorization": API_KEY} if API_KEY else {}

def http_get(path: str, params: dict = None, timeout: int = 20, retries: int = 2):
    if not API_KEY:
        st.error("No API key detected. Paste your key into API_KEY_DEFAULT at the top of this file.")
        st.stop()
    url = f"{BASE}{path}"
    for attempt in range(retries + 1):
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=timeout)
        if r.status_code == 429 and attempt < retries:
            time.sleep(1 + attempt)
            continue
        if r.status_code != 200:
            st.error(f"HTTP {r.status_code} — {r.text[:300]}")
            st.stop()
        return r.json()
    return None

@st.cache_data(ttl=600)
def search_player_by_name(name: str) -> pd.DataFrame:
    payload = http_get("/players", params={"search": name, "per_page": 10})
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return pd.json_normalize(data) if data else pd.DataFrame()

@st.cache_data(ttl=600)
def get_season_averages(player_id: int, season: int) -> Dict:
    payload = http_get("/season_averages/general", params={"season": season, "type": "base", "player_ids[]": player_id})
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return rows[0] if rows else {}

@st.cache_data(ttl=600)
def get_recent_stats(player_id: int, start_date: str, end_date: str) -> List[Dict]:
    d0 = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = []
    cur = d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)
    rows: List[Dict] = []
    for i in range(0, len(dates), 10):
        chunk = dates[i:i+10]
        params = {"player_ids[]": player_id}
        for d in chunk:
            params[f"dates[{d}]"] = d
        payload = http_get("/stats", params=params)
        rows.extend(payload.get("data", []) if isinstance(payload, dict) else [])
    return rows

def compute_recent_avgs(rows: List[Dict]) -> Optional[Dict]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ["pts", "ast", "reb", "fg3m", "min"]:
        if col not in df.columns:
            df[col] = 0
    df["min"] = pd.to_numeric(df["min"], errors="coerce")
    return {
        "GP": len(df),
        "PTS": df["pts"].mean(),
        "AST": df["ast"].mean(),
        "REB": df["reb"].mean(),
        "3PM": df["fg3m"].mean(),
        "MIN": df["min"].mean(skipna=True),
    }

def blend_projection(season_avg, recent_avg, s_w, r_w):
    out = {}
    for k in ["PTS", "AST", "REB", "3PM"]:
        s = (season_avg or {}).get(k, math.nan)
        r = (recent_avg or {}).get(k, math.nan)
        if not math.isnan(s) and not math.isnan(r):
            out[k] = s_w * s + r_w * r
        elif not math.isnan(r):
            out[k] = r
        elif not math.isnan(s):
            out[k] = s
        else:
            out[k] = math.nan
    return out

def round_or_none(v):
    try:
        return None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 2)
    except Exception:
        return None

st.title("NBA Projections — Single Player (BALLDONTLIE)")
st.caption("Paste your API key at the top. This app only queries one player per search to avoid rate limits.")

with st.form("player_form"):
    player_query = st.text_input("Player name", placeholder="e.g., Stephen Curry")
    col1, col2 = st.columns(2)
    with col1:
        recent_days = st.slider("Recent window (days)", 7, 60, 30)
    with col2:
        season_weight = st.slider("Season weight", 0.0, 1.0, 0.4, 0.05)
    submitted = st.form_submit_button("Get Projection")

if submitted:
    if not player_query.strip():
        st.warning("Enter a player name.")
        st.stop()

    df_players = search_player_by_name(player_query.strip())
    if df_players.empty:
        st.error("No player found.")
        st.stop()

    df_players["full_name"] = df_players["first_name"] + " " + df_players["last_name"]
    player = df_players.iloc[0]
    pid = int(player["id"])

    st.subheader(f"{player['full_name']} ({player['team.full_name']})")

    season_num = dt.date.today().year
    srow = get_season_averages(pid, season_num)
    season_avg = {"PTS": srow.get("pts"), "AST": srow.get("ast"), "REB": srow.get("reb"), "3PM": srow.get("fg3m")}

    start_date = (dt.date.today() - dt.timedelta(days=recent_days)).strftime("%Y-%m-%d")
    end_date = dt.date.today().strftime("%Y-%m-%d")
    rec_rows = get_recent_stats(pid, start_date, end_date)
    recent_avg = compute_recent_avgs(rec_rows) if rec_rows else {}

    proj = blend_projection(season_avg, recent_avg, season_weight, 1 - season_weight)

    st.dataframe(pd.DataFrame([{
        "PTS_proj": round_or_none(proj.get("PTS")),
        "AST_proj": round_or_none(proj.get("AST")),
        "REB_proj": round_or_none(proj.get("REB")),
        "3PM_proj": round_or_none(proj.get("3PM")),
        "Blend": f"{season_weight:.2f} season + {1-season_weight:.2f} recent"
    }]))
