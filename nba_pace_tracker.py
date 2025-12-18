import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import re
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# --- CONFIGURATION ---
st.set_page_config(page_title="The Rich NBA Real-Time Pace Tracker", layout="wide")
st.sidebar.header("⚙️ Dashboard Settings")

# Auto-Update
refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", 1, 120, 30)
count = st_autorefresh(interval=refresh_rate * 1000, key="data_refresh")

# Indicators
st.sidebar.subheader("Bollinger Bands")
bb_length = st.sidebar.number_input("BB Length", 1, value=12)
bb_std = st.sidebar.number_input("BB StdDev", 0.1, value=2.0)

st.sidebar.subheader("Keltner Channels")
kc_length = st.sidebar.number_input("KC Length", 1, value=12)
kc_mult = st.sidebar.number_input("KC Multiplier", 0.1, value=2.0)

# DEBUG SECTION
st.sidebar.divider()
st.sidebar.subheader("🕵️ Odds")
show_debug = st.sidebar.checkbox("Show Odds Data")

# Constants
HEADERS_CDN = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"}
HEADERS_STATS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com"}

TEAM_COLORS = {
    'ATL': '#E03A3E', 'BOS': '#007A33', 'BKN': '#000000', 'CHA': '#1D1160',
    'CHI': '#CE1141', 'CLE': '#860038', 'DAL': '#00538C', 'DEN': '#0E2240',
    'DET': '#C8102E', 'GSW': '#1D428A', 'HOU': '#CE1141', 'IND': '#002D62',
    'LAC': '#C8102E', 'LAL': '#552583', 'MEM': '#5D76A9', 'MIA': '#98002E',
    'MIL': '#00471B', 'MIN': '#0C2340', 'NOP': '#0C2340', 'NYK': '#006BB6',
    'OKC': '#007AC1', 'ORL': '#0077C0', 'PHI': '#006BB6', 'PHX': '#1D1160',
    'POR': '#E03A3E', 'SAC': '#5A2D81', 'SAS': '#C4CED4', 'TOR': '#CE1141',
    'UTA': '#002B5C', 'WAS': '#002B5C'
}

# --- DATA FUNCTIONS ---

@st.cache_data(ttl=3600)
def get_season_baseline():
    try:
        resp = requests.get("https://stats.nba.com/stats/teamgamelogs", headers=HEADERS_STATS, 
                          params={"Season": "2024-25", "SeasonType": "Regular Season", "MeasureType": "Advanced", "PerMode": "Totals", "LeagueID": "00"}, timeout=5)
        if resp.status_code == 200:
            df = pd.DataFrame(resp.json()['resultSets'][0]['rowSet'], columns=resp.json()['resultSets'][0]['headers'])
            unique = df.drop_duplicates(subset=['GAME_ID'])
            return unique['PACE'].mean(), unique['PACE'].median()
    except: pass
    return 99.5, 98.0

def get_live_games():
    try:
        return requests.get("https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json", headers=HEADERS_CDN, timeout=5).json()['scoreboard']['games']
    except: return []

def get_live_odds():
    try:
        with open("nba_odds.json", "r") as f:
            return json.load(f)
    except: return {}

def parse_game_clock(clock_str, period):
    try:
        if "Final" in clock_str: return period * 12.0
        if "Half" in clock_str: return 24.0
        if "Start" in clock_str or period == 0: return 0.0
        match = re.search(r'Q\d\s+(:?\d{0,2}:?\d{2}(\.\d+)?)', clock_str)
        minutes_remaining = 12.0
        if match:
            time_part = match.group(1)
            if ":" in time_part:
                parts = time_part.split(":")
                mins = int(parts[0]) if parts[0] else 0
                secs = float(parts[1])
                minutes_remaining = mins + (secs / 60.0)
            else: minutes_remaining = float(time_part) / 60.0
        past_quarters = period - 1
        elapsed = (past_quarters * 12.0) + (12.0 - minutes_remaining)
        return elapsed
    except Exception: return (period * 12.0) - 6.0 

def calculate_pace(game_id):
    try:
        data = requests.get(f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json", headers=HEADERS_CDN, timeout=5).json()['game']
        home, away, period = data['homeTeam'], data['awayTeam'], data['period']
        clock_text = data.get('gameStatusText', '')
        if period == 0: return 0, home['teamTricode'], away['teamTricode'], 0, 0, 0, clock_text
        def get_poss(t): 
            s = t['statistics']
            return s['fieldGoalsAttempted'] + 0.44 * s['freeThrowsAttempted'] - s['reboundsOffensive'] + s['turnovers']
        avg_poss = (get_poss(home) + get_poss(away)) / 2
        minutes_elapsed = parse_game_clock(clock_text, period)
        if minutes_elapsed <= 0: minutes_elapsed = 1
        pace = (avg_poss / minutes_elapsed) * 48
        return pace, home['teamTricode'], away['teamTricode'], home['score'], away['score'], period, clock_text
    except: return None, None, None, 0, 0, 0, ""

# --- MAIN ---
st.title("🏀 The Rich NBA Pace Tracker - Live")
st.caption(f"Auto-updating every {refresh_rate} seconds.")

season_avg, season_median = get_season_baseline()
games = get_live_games()
live_odds = get_live_odds()

if show_debug:
    st.warning("⚠️ DEBUG MODE ACTIVE")
    st.write(f"**Loaded Odds Data:** {len(live_odds)} games found")
    st.json(live_odds)
    if not live_odds:
        st.error("❌ The odds file is empty. Check your Render Logs and API Key!")

if not games:
    st.info("No games found.")
else:
    if 'pace_history' not in st.session_state: st.session_state.pace_history = {} 
    
    active = 0
    for game in games:
        if game['gameStatus'] >= 2: 
            active += 1
            pace, home, away, h_score, a_score, period, clock = calculate_pace(game['gameId'])
            if pace and pace > 0:
                matchup = f"{away} @ {home}"
                now = datetime.now().strftime("%H:%M:%S")
                if matchup not in st.session_state.pace_history: st.session_state.pace_history[matchup] = []
                hist = st.session_state.pace_history[matchup]
                if not hist or hist[-1]['Time'] != now:
                    hist.append({"Time": now, "Pace": pace, "Home": home, "Away": away, "HomeScore": h_score, "AwayScore": a_score, "Clock": clock})

    if active == 0: st.warning("Games scheduled but none active.")

    for matchup, data in st.session_state.pace_history.items():
        if len(data) > 1:
            df = pd.DataFrame(data)
            
            # Indicators
            df['BB_Mid'] = df['Pace'].rolling(bb_length).mean()
            df['BB_Std'] = df['Pace'].rolling(bb_length).std()
            df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * bb_std)
            df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * bb_std)
            df['KC_Mid'] = df['Pace'].ewm(span=kc_length, adjust=False).mean()
            df['KC_Vol'] = df['Pace'].rolling(kc_length).std()
            df['KC_Upper'] = df['KC_Mid'] + (df['KC_Vol'] * kc_mult)
            df['KC_Lower'] = df['KC_Mid'] - (df['KC_Vol'] * kc_mult)

            # Visuals
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df['Time'], y=df['Pace'], mode='lines', line=dict(color='rgba(211, 47, 47, 0.2)', width=12), hoverinfo='skip', showlegend=False))
            fig.add_trace(go.Scatter(x=df['Time'], y=df['Pace'], mode='lines+markers', name='Live Pace', line=dict(color='#D32F2F', width=3), marker=dict(color='#D32F2F', size=6)))
            
            if not df['BB_Upper'].isnull().all():
                fig.add_trace(go.Scatter(x=df['Time'], y=df['BB_Upper'], line=dict(width=0), showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=df['Time'], y=df['BB_Lower'], fill='tonexty', fillcolor='rgba(0, 255, 255, 0.1)', line=dict(width=0), name='Bollinger Band', hoverinfo='skip'))
            if not df['KC_Upper'].isnull().all():
                fig.add_trace(go.Scatter(x=df['Time'], y=df['KC_Upper'], mode='lines', name='Keltner Upper', line=dict(color='orange')))
                fig.add_trace(go.Scatter(x=df['Time'], y=df['KC_Lower'], mode='lines', name='Keltner Lower', line=dict(color='orange')))

            fig.add_hline(y=season_avg, line_dash="dash", line_color="#00FF00", annotation_text=f"Season Avg ({season_avg:.1f})", annotation_position="bottom right")
            fig.add_hline(y=season_median, line_dash="dot", line_color="#FFFF00", annotation_text=f"Season Med ({season_median:.1f})", annotation_position="top right")

            # --- PROJECTION LOGIC ---
            latest = df.iloc[-1]
            odds_display = live_odds.get(matchup, {})
            dk_total = odds_display.get('Over', None)
            
            proj_text = "N/A"
            eff_text = "N/A"
            
            if dk_total and season_avg > 0:
                # 1. Pace-Adjusted Projection
                pace_factor = latest['Pace'] / season_avg
                projected_score = dk_total * pace_factor
                proj_text = f"{projected_score:.1f}"
                
                # 2. Implied Efficiency (Pace-Neutral)
                # Formula: (Projected Score / Current Pace) * 100
                if latest['Pace'] > 0:
                    implied_eff = (projected_score / latest['Pace']) * 100
                    eff_text = f"{implied_eff:.1f}"
            
            title_text = f"{latest['Away']} {latest['AwayScore']} @ {latest['Home']} {latest['HomeScore']} ({latest['Clock']})  |  DK: {dk_total if dk_total else 'N/A'}"
            
            fig.update_layout(title=dict(text=title_text, font=dict(color=TEAM_COLORS.get(latest['Home'], '#FFFFFF'), size=20)),
                              xaxis_title="Time", yaxis_title="Pace", template="plotly_dark", height=500, margin=dict(t=50), legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Current Pace", f"{latest['Pace']:.1f}", delta=f"{latest['Pace'] - season_avg:.1f}")
            c2.metric("Clock", latest['Clock'])
            c3.metric("DraftKings Total", f"{dk_total if dk_total else 'N/A'}")
            c4.metric("Pace-Adj Proj", proj_text, delta="Implied Final" if proj_text != "N/A" else None)
            
            # REPLACED 'Lg Avg Pace' WITH NEW METRIC
            c5.metric("Implied Eff (Pts/100)", eff_text)
            
            st.divider()


