import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="NBA Real-Time Pace Tracker", layout="wide")

# --- SIDEBAR SETTINGS (Must be at top for auto-refresh to work) ---
st.sidebar.header("⚙️ Dashboard Settings")

# 1. Auto-Update Settings
st.sidebar.subheader("Auto-Update")
refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", min_value=1, max_value=120, value=30)

# Initialize Auto-Refresh
count = st_autorefresh(interval=refresh_rate * 1000, key="data_refresh")

# 2. Indicator Settings
st.sidebar.subheader("Bollinger Bands")
bb_length = st.sidebar.number_input("BB Length (SMA)", min_value=1, value=5)
bb_std = st.sidebar.number_input("BB StdDev", min_value=0.1, value=2.0)

st.sidebar.subheader("Keltner Channels")
kc_length = st.sidebar.number_input("KC Length (EMA)", min_value=1, value=5)
kc_mult = st.sidebar.number_input("KC Multiplier", min_value=0.1, value=2.0)

# --- CONSTANTS & HEADERS ---
HEADERS_CDN = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://www.nba.com/"
}

HEADERS_STATS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
}

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

@st.cache_data(ttl=3600)  # Cache this for 1 hour
def get_season_baseline():
    """Fetches the 2024-25 Season Average and Median Pace."""
    url = "https://stats.nba.com/stats/teamgamelogs"
    params = {
        "Season": "2024-25",
        "SeasonType": "Regular Season",
        "MeasureType": "Advanced", 
        "PerMode": "Totals",
        "LeagueID": "00"
    }
    
    try:
        resp = requests.get(url, headers=HEADERS_STATS, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            headers = data['resultSets'][0]['headers']
            rows = data['resultSets'][0]['rowSet']
            df = pd.DataFrame(rows, columns=headers)
            unique_games = df.drop_duplicates(subset=['GAME_ID'])
            return unique_games['PACE'].mean(), unique_games['PACE'].median()
    except Exception:
        pass 
    
    return 99.5, 98.0  # Fallback defaults

def get_live_games():
    """Fetches list of live games directly from NBA CDN."""
    url = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
    try:
        response = requests.get(url, headers=HEADERS_CDN, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data['scoreboard']['games']
    except Exception:
        pass
    return []

def calculate_pace(game_id):
    """
    Fetches boxscore directly and extracts Pace + Scores + Clock.
    """
    url = f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
    try:
        response = requests.get(url, headers=HEADERS_CDN, timeout=5)
        if response.status_code != 200:
            return None, None, None, 0, 0, 0, ""
            
        data = response.json()['game']
        home = data['homeTeam']
        away = data['awayTeam']
        period = data['period']
        
        # Extract Scores & Clock
        home_score = home['score']
        away_score = away['score']
        clock_text = data.get('gameStatusText', 'Unknown') # e.g. "Q4 9:04"
        
        # If game hasn't started
        if period == 0: 
            return 0, home['teamTricode'], away['teamTricode'], 0, 0, 0, clock_text
        
        # Calculate Possessions
        def get_poss(team):
            stats = team['statistics']
            fga = stats['fieldGoalsAttempted']
            fta = stats['freeThrowsAttempted']
            orb = stats['reboundsOffensive']
            tov = stats['turnovers']
            return fga + 0.44 * fta - orb + tov

        home_poss = get_poss(home)
        away_poss = get_poss(away)
        avg_poss = (home_poss + away_poss) / 2
        
        # Time Estimate
        minutes_elapsed = period * 12
        
        if minutes_elapsed == 0: 
            return 0, home['teamTricode'], away['teamTricode'], 0, 0, 0, clock_text
        
        pace = (avg_poss / minutes_elapsed) * 48
        
        return pace, home['teamTricode'], away['teamTricode'], home_score, away_score, period, clock_text
        
    except Exception:
        return None, None, None, 0, 0, 0, ""

# --- MAIN APPLICATION ---

st.title("🏀 Live NBA Pace Tracker")
st.caption(f"Auto-updating every {refresh_rate} seconds.")

# 1. Get Historical Baseline
season_avg, season_median = get_season_baseline()

# 2. Get Live Games
games = get_live_games()

if not games:
    st.info("No games found on the scoreboard.")
else:
    # Initialize Session State
    if 'pace_history' not in st.session_state:
        st.session_state.pace_history = {} 

    # Process Active Games
    active_games_count = 0
    for game in games:
        if game['gameStatus'] >= 2: 
            active_games_count += 1
            game_id = game['gameId']
            # Unpack the new score & clock variables
            pace, home, away, h_score, a_score, period, clock = calculate_pace(game_id)
            
            if pace and pace > 0:
                matchup = f"{away} @ {home}"
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                if matchup not in st.session_state.pace_history:
                    st.session_state.pace_history[matchup] = []
                
                history = st.session_state.pace_history[matchup]
                
                # Update logic: Append if new timestamp
                if not history or history[-1]['Time'] != timestamp:
                    history.append({
                        "Time": timestamp,
                        "Pace": pace,
                        "Home": home,
                        "Away": away,
                        "HomeScore": h_score,
                        "AwayScore": a_score,
                        "Clock": clock
                    })

    if active_games_count == 0:
        st.warning("Games are scheduled but none are currently active.")

    # 3. Render Charts
    for matchup, data in st.session_state.pace_history.items():
        if len(data) > 1:
            df = pd.DataFrame(data)
            
            # --- INDICATOR MATH ---
            df['BB_Mid'] = df['Pace'].rolling(window=bb_length).mean()
            df['BB_Std'] = df['Pace'].rolling(window=bb_length).std()
            df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * bb_std)
            df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * bb_std)

            df['KC_Mid'] = df['Pace'].ewm(span=kc_length, adjust=False).mean()
            df['KC_Vol'] = df['Pace'].rolling(window=kc_length).std()
            df['KC_Upper'] = df['KC_Mid'] + (df['KC_Vol'] * kc_mult)
            df['KC_Lower'] = df['KC_Mid'] - (df['KC_Vol'] * kc_mult)

            # --- PLOTLY CHART ---
            fig = go.Figure()

            # 1. The "Glow" Layer (Wider, transparent red line underneath)
            fig.add_trace(go.Scatter(
                x=df['Time'], y=df['Pace'],
                mode='lines',
                name='Pace Glow',
                line=dict(color='rgba(211, 47, 47, 0.2)', width=12), # Red, Low Opacity, Wide
                hoverinfo='skip', showlegend=False
            ))

            # 2. The Main Line (Solid dull red on top)
            dull_red = '#D32F2F' # "Material Red 700"
            fig.add_trace(go.Scatter(
                x=df['Time'], y=df['Pace'],
                mode='lines+markers',
                name='Live Pace',
                line=dict(color=dull_red, width=3),
                marker=dict(color=dull_red, size=6)
            ))

            # Bands
            if not df['BB_Upper'].isnull().all():
                fig.add_trace(go.Scatter(x=df['Time'], y=df['BB_Upper'], line=dict(width=0), showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=df['Time'], y=df['BB_Lower'], fill='tonexty', fillcolor='rgba(0, 255, 255, 0.1)', line=dict(width=0), name='Bollinger Band', hoverinfo='skip'))

            if not df['KC_Upper'].isnull().all():
                # CHANGED: Removed dash='dot' to make lines solid
                fig.add_trace(go.Scatter(x=df['Time'], y=df['KC_Upper'], mode='lines', name='Keltner Upper', line=dict(color='orange')))
                fig.add_trace(go.Scatter(x=df['Time'], y=df['KC_Lower'], mode='lines', name='Keltner Lower', line=dict(color='orange')))

            # Reference Lines
            fig.add_hline(y=season_avg, line_dash="dash", line_color="#00FF00", annotation_text=f"Season Avg ({season_avg:.1f})", annotation_position="bottom right")
            fig.add_hline(y=season_median, line_dash="dot", line_color="#FFFF00", annotation_text=f"Season Med ({season_median:.1f})", annotation_position="top right")

            # Dynamic Title with SCORES & CLOCK
            latest = df.iloc[-1]
            home_team_code = latest['Home']
            away_team_code = latest['Away']
            home_score = latest['HomeScore']
            away_score = latest['AwayScore']
            clock_status = latest['Clock']
            
            # Format: "GSW 102 @ LAL 99 (Q4 5:00)"
            title_text = f"{away_team_code} {away_score} @ {home_team_code} {home_score} ({clock_status})"
            title_color = TEAM_COLORS.get(home_team_code, '#FFFFFF')
            
            fig.update_layout(
                title=dict(text=title_text, font=dict(color=title_color, size=20)),
                xaxis_title="Time",
                yaxis_title="Pace (Poss/48m)",
                template="plotly_dark",
                height=500,
                margin=dict(l=20, r=20, t=50, b=20),
                legend=dict(orientation="h", y=1.1)
            )
            
            st.plotly_chart(fig, use_container_width=True)

            # Stats Table
            current_pace = df['Pace'].iloc[-1]
            diff_from_avg = current_pace - season_avg
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Current Pace", f"{current_pace:.1f}", delta=f"{diff_from_avg:.1f} vs Avg")
            col2.metric("Game Clock", f"{clock_status}")
            col3.metric("League Median", f"{season_median:.1f}")
            col4.metric("League Average", f"{season_avg:.1f}")
            
            st.divider()