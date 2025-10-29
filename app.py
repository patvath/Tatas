# app.py — Single-player BallDontLie Streamlit app with auth fallbacks
# -------------------------------------------------------------------
# 🔑 PASTE YOUR KEY HERE (keep quotes)
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

import os
import time
import math
import datetime as dt
from typing import List, Dict, Optional, Tuple
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NBA Projections — Single Player (BALLDONTLIE)",
                   page_icon="🏀", layout="wide")

# -------------------- Key (manual paste supported) --------------------
def resolve_api_key() -> str:
    hardcoded = (API_KEY_DEFAULT or "").strip()
    if hardcoded and hardcoded != "PASTE_YOUR_KEY_HERE":
        return hardcoded
    # optional sidebar override
    sidebar_k = st.sidebar.text_input("BALLDONTLIE API Key (optional)", value="", type="password")
    if sidebar_k.strip():
        return sidebar_k.strip()
    # optional secrets/env
    try:
        s1 = st.secrets.get("BALLDONTLIE_API_KEY", "")
        s2 = st.secrets.get("api", {}).get("api_key", "") if hasattr(st, "secrets") else ""
        sec = str(s1 or s2).strip()
        if sec:
            return sec
    except Exception:
        pass
    env = os.getenv("BALLDONTLIE_API_KEY", "").strip()
    if env:
        return env
    return ""

API_KEY = resolve_api_key()

# Preferred and fallback bases in order
BASES = [
    "https://api.balldontlie.io/nba/v1",
    "https://api.balldontlie.io/v1",
]

# global diagnostics
if "auth_style" not in st.session_state:
    st.session_state.auth_style = "unknown"
if "last_status" not in st.session_state:
    st.session_state.last_status = None
if "last_url" not in st.session_state:
    st.session_state.last_url = None

def _headers(style: str) -> Dict[str,str]:
    """style = 'raw' or 'bearer'"""
    if style == "bearer":
        return {"Authorization": f"Bearer {API_KEY}"}
    return {"Authorization": API_KEY}

# --------------------------- HTTP helper ---------------------------------
def http_get(full_url: str, params: dict = None, timeout: int = 20, retries: int = 2):
    """
    Tries raw-token header first; on 401 retries with Bearer.
    Handles 429 with small backoff. Returns (json, None) or (None, (status, preview)).
    """
    if not API_KEY:
        st.error("No API key detected. Paste it into API_KEY_DEFAULT at the top (or sidebar).")
        st.stop()
    params = params or {}

    # Try raw first
    for style in ["raw", "bearer"]:
        headers = _headers(style)
        for attempt in range(retries + 1):
            r = requests.get(full_url, headers=headers, params=params, timeout=timeout)
            st.session_state.last_status = r.status_code
            st.session_state.last_url = full_url

            # 429 backoff
            if r.status_code == 429 and attempt < retries:
                wait = float(r.headers.get("Retry-After", 1 + attempt))
                time.sleep(wait)
                continue

            # 401: if raw failed, try bearer next; if bearer failed too, bubble error
            if r.status_code == 401:
                break  # move to next style (or exit if already bearer)

            if r.status_code != 200:
                preview = (r.text or "")[:400]
                return None, (r.status_code, preview)

            try:
                st.session_state.auth_style = style
                return r.json(), None
            except ValueError:
                preview = (r.text or "")[:400]
                return None, (999, f"Non-JSON response: {preview}")
        # if we got here due to 401 on this style, loop continues to alternate style

    return None, (401, "Unauthorized after trying both raw and Bearer Authorization headers.")

# ----------------------------- API helpers -------------------------------
@st.cache_data(ttl=600)
def search_player_any_route(name: str) -> Tuple[pd.DataFrame, str, dict | None]:
    """Try multiple search routes; return (df, route_used, error_info)"""
    routes = [
        (BASES[0] + "/players", {"search": name, "per_page": 10}),
        (BASES[1] + "/players", {"search": name, "per_page": 10}),
        (BASES[0] + "/players/search", {"query": name, "per_page": 10}),
        (BASES[1] + "/players/search", {"query": name, "per_page": 10}),
    ]
    last_err = None
    for url, params in routes:
        payload, err = http_get(url, params)
        if payload and isinstance(payload, dict) and payload.get("data"):
            return pd.json_normalize(payload["data"]), url, None
        last_err = {"url": url, "err": err or (200, "empty data[]")}
    return pd.DataFrame(), "", last_err

@st.cache_data(ttl=600)
def get_season_averages_any(player_id: int, season: int) -> Tuple[dict, str, dict | None]:
    url1 = BASES[0] + "/season_averages/general"
    params1 = {"season": season, "type": "base", "season_type": "regular", "player_ids[]": player_id}
    payload, err = http_get(url1, params1)
    if payload and payload.get("data"):
        return payload["data"][0], url1, None

    url2 = BASES[1] + "/season_averages"
    params2 = {"season": season, "player_ids[]": player_id}
    payload2, err2 = http_get(url2, params2)
    if payload2 and payload2.get("data"):
        return payload2["data"][0], url2, None
    return {}, url2, {"first": {"url": url1, "err": err}, "second": {"url": url2, "err": err2}}

@st.cache_data(ttl=600)
def get_recent_stats_any(player_id: int, start_date: str, end_date: str) -> Tuple[List[dict], str, dict | None]:
    d0 = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = []
    cur = d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)

    rows: List[dict] = []
    route_used = ""
    last_err = None
    for base in BASES:
        ok = True
        tmp_rows: List[dict] = []
        for i in range(0, len(dates), 10):
            chunk = dates[i:i+10]
            params = [("player_ids[]", player_id)]
            for d in chunk:
                params.append(("dates[]", d))
            payload, err = http_get(base + "/stats", dict(params))
            if not payload or "data" not in payload:
                ok = False
                last_err = {"url": base + "/stats", "err": err}
                break
            tmp_rows.extend(payload["data"])
        if ok:
            rows = tmp_rows
            route_used = base + "/stats"
            break
    return rows, route_used, last_err

# ------------------------ Projection helpers -----------------------------
def compute_recent_avgs(rows: List[dict]) -> Optional[dict]:
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

def blend_projection(season_avg: Optional[dict], recent_avg: Optional[dict],
                     season_weight: float, recent_weight: float) -> dict:
    out = {}
    for k in ["PTS", "AST", "REB", "3PM"]:
        s = (season_avg or {}).get(k, math.nan)
        r = (recent_avg or {}).get(k, math.nan)
        if not math.isnan(s) and not math.isnan(r):
            out[k] = season_weight * s + recent_weight * r
        elif not math.isnan(r):
            out[k] = r
        elif not math.isnan(s):
            out[k] = s
        else:
            out[k] = math.nan
    return out

def r2(v):
    try:
        return None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 2)
    except Exception:
        return None

# ---------------------------------- UI -----------------------------------
st.title("NBA Projections — Single Player (BALLDONTLIE)")
st.caption("Paste your API key at the very top. The app tries both raw and Bearer auth automatically and falls back across routes.")

show_diag = st.sidebar.checkbox("Show diagnostics", value=True)
st.sidebar.code(f"Key length: {len(API_KEY) if API_KEY else 0}\nAuth style used: {st.session_state.auth_style}\nLast status: {st.session_state.last_status}\nLast URL: {st.session_state.last_url}")

with st.form("player_form"):
    player_query = st.text_input("Player name", placeholder="e.g., Stephen Curry")
    col1, col2 = st.columns(2)
    with col1:
        recent_days = st.slider("Recent window (days)", 7, 60, 30)
    with col2:
        season_weight = st.slider("Season weight", 0.0, 1.0, 0.4, 0.05)
    submitted = st.form_submit_button("Get Projection")

if submitted:
    if not API_KEY:
        st.error("No API key found. Paste into API_KEY_DEFAULT at the top or use the sidebar.")
        st.stop()
    if not player_query.strip():
        st.warning("Enter a player name.")
        st.stop()

    # ---- Single-player search, multi-route ----
    df_players, used_route, search_err = search_player_any_route(player_query.strip())
    if show_diag:
        st.sidebar.write("Search route used:", used_route or "none")
        if search_err:
            st.sidebar.write("Search route err:", search_err)

    if df_players.empty:
        st.error("No player found from any supported route. Try a different spelling.")
        st.stop()

    df_players["full_name"] = df_players["first_name"].str.strip() + " " + df_players["last_name"].str.strip()
    exact = df_players[df_players["full_name"].str.lower() == player_query.strip().lower()]
    selected = (exact.iloc[0] if not exact.empty else df_players.iloc[0])

    pid = int(selected["id"])
    st.subheader(f"{selected['full_name']}  (ID {pid})")
    st.write(f"Team: {selected.get('team.full_name', 'N/A')} — Pos: {selected.get('position') or 'N/A'}")

    # ---- Season averages ----
    season_num = dt.date.today().year
    srow, season_route, season_err = get_season_averages_any(pid, season_num)
    season_avg = {
        "PTS": srow.get("pts"),
        "AST": srow.get("ast"),
        "REB": srow.get("reb"),
        "3PM": srow.get("fg3m"),
        "MIN": srow.get("min"),
    }
    if show_diag:
        st.sidebar.write("Season route:", season_route or "none")
        if season_err:
            st.sidebar.write("Season err:", season_err)

    # ---- Recent stats ----
    start_date = (dt.date.today() - dt.timedelta(days=recent_days)).strftime("%Y-%m-%d")
    end_date = dt.date.today().strftime("%Y-%m-%d")
    rec_rows, stats_route, stats_err = get_recent_stats_any(pid, start_date, end_date)
    recent_avg = compute_recent_avgs(rec_rows) or {}
    if show_diag:
        st.sidebar.write("Stats route:", stats_route or "none")
        if stats_err:
            st.sidebar.write("Stats err:", stats_err)

    # ---- Projection ----
    proj = blend_projection(season_avg, recent_avg, season_weight, 1 - season_weight)

    left, right = st.columns(2)
    with left:
        st.markdown("**Season Averages**")
        st.metric("PTS", r2(season_avg.get("PTS")))
        st.metric("AST", r2(season_avg.get("AST")))
        st.metric("REB", r2(season_avg.get("REB")))
        st.metric("3PM", r2(season_avg.get("3PM")))
    with right:
        st.markdown(f"**Recent ({recent_days} days)**")
        st.metric("PTS", r2(recent_avg.get("PTS")))
        st.metric("AST", r2(recent_avg.get("AST")))
        st.metric("REB", r2(recent_avg.get("REB")))
        st.metric("3PM", r2(recent_avg.get("3PM")))

    st.markdown("### Projection")
    st.dataframe(pd.DataFrame([{
        "Player": selected["full_name"],
        "Team": selected.get("team.abbreviation"),
        "PTS_proj": r2(proj.get("PTS")),
        "AST_proj": r2(proj.get("AST")),
        "REB_proj": r2(proj.get("REB")),
        "3PM_proj": r2(proj.get("3PM")),
        "Blend": f"{season_weight:.2f} season + {1-season_weight:.2f} recent"
    }]))
else:
    st.info("Enter a player name and click **Get Projection**. Use **Show diagnostics** if auth/route issues persist.")
