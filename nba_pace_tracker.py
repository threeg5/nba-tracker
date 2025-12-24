import streamlit as st
import pandas as pd
import requests
import json
import re
import os
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# --- GOOGLE SHEETS SETUP ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def connect_to_gsheet():
    """Connects to Google Sheets using Render Environment Variables"""
    try:
        # Load credentials from Render Environment Variable
        creds_json_str = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
        if not creds_json_str:
            st.error("❌ Missing 'GOOGLE_SHEETS_CREDENTIALS' in Render Environment.")
            return None

        creds_dict = json.loads(creds_json_str)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # Open the Sheet (Make sure your sheet is named EXACTLY 'NBA Logs' or change this line)
        sheet = client.open("NBA Logs").sheet1 
        return sheet
    except Exception as e:
        st.error(f"Google Sheet Error: {e}")
        return None

# --- CONFIGURATION ---
st.set_page_config(page_title="NBA Data Logger", layout="wide")
st.sidebar.header("⚙️ Logger Settings")

# Set this to 10 seconds for your "Every 10s" requirement
refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", 5, 60, 10)
count = st_autorefresh(interval=refresh_rate * 1000, key="data_refresh")

# Constants
HEADERS_CDN = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"}

# --- DATA FUNCTIONS ---

@st.cache_data(ttl=3600)
def get_season_baseline():
    # Hardcoded fallback for speed/reliability in logger mode
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
        
        if period == 0: return None
        
        def get_poss(t): 
            s = t['statistics']
            return s['fieldGoalsAttempted'] + 0.44 * s['freeThrowsAttempted'] - s['reboundsOffensive'] + s['turnovers']
        
        avg_poss = (get_poss(home) + get_poss(away)) / 2
        minutes_elapsed = parse_game_clock(clock_text, period)
        
        if minutes_elapsed <= 0: minutes_elapsed = 1
        pace = (avg_poss / minutes_elapsed) * 48
        
        return pace, home['teamTricode'], away['teamTricode'], home['score'], away['score'], period, clock_text, minutes_elapsed
    except: return None

# --- MAIN LOOP ---
st.title("📊 NBA Data Logger (G-Sheets)")
st.write(f"Auto-logging games in the last 90 seconds. Refreshing every {refresh_rate}s.")

season_avg, season_median = get_season_baseline()
games = get_live_games()
live_odds = get_live_odds()
sheet = connect_to_gsheet()

if not games:
    st.info("No games active.")
else:
    active_games = []
    for game in games:
        if game['gameStatus'] >= 2: # In Progress
            res = calculate_pace(game['gameId'])
            if res:
                pace, home, away, h_score, a_score, period, clock, mins_elapsed = res
                
                # --- CALCULATIONS ---
                total_current = h_score + a_score
                elapsed_sec = mins_elapsed * 60
                
                # Default "End of Game" values
                proj_rem = 0
                rich_proj = total_current
                rem_diff = 0
                
                # Determine Seconds Remaining in Regulation (48m = 2880s)
                # If OT (Period > 4), logic handles it by elapsed_sec increasing
                total_regulation_sec = 2880
                # If in OT, we might be over 2880, so remaining is based on OT period end
                # But typically "Last 1:30" means end of whatever period is the final one.
                # Simplification: Calculate remaining relative to current period end if Q4+
                
                remaining_sec_in_game = 0
                if period >= 4:
                     # Calculate when this period ends
                     end_of_period_sec = period * 12 * 60
                     remaining_sec_in_game = end_of_period_sec - elapsed_sec
                else:
                     remaining_sec_in_game = 9999 # Not end of game
                
                # --- ODDS & PROJECTIONS ---
                matchup = f"{away} @ {home}"
                odds_data = live_odds.get(matchup, {})
                dk_total = odds_data.get('Over', 0)
                
                if elapsed_sec > 60:
                    points_per_sec = total_current / elapsed_sec
                    # For projection, we project to end of current period if Q4+
                    proj_rem = points_per_sec * remaining_sec_in_game
                    rich_proj = total_current + proj_rem
                    
                    if dk_total:
                        implied_rem = dk_total - total_current
                        rem_diff = proj_rem - implied_rem
                
                # --- LOGGING CONDITION ---
                # "Last 1:30 seconds" -> Remaining <= 90 seconds
                # And we must be in Q4 or OT
                if period >= 4 and 0 < remaining_sec_in_game <= 90:
                    
                    log_row = [
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), # Timestamp
                        matchup,        # Game
                        clock,          # Clock
                        f"{pace:.1f}",  # Pace
                        h_score,        # Home Score
                        a_score,        # Away Score
                        dk_total,       # DK Total
                        f"{rich_proj:.1f}", # Rich Proj
                        f"{proj_rem:.1f}",  # Proj Rem Pts
                        f"{rem_diff:.1f}"   # Rem Diff (Edge)
                    ]
                    
                    # VISUAL DISPLAY
                    st.success(f"LOGGING: {matchup} | Clock: {clock} | Edge: {rem_diff:.1f}")
                    st.table(pd.DataFrame([log_row], columns=["Time", "Matchup", "Clock", "Pace", "Home", "Away", "DK", "Rich Proj", "Proj Rem", "Edge"]))
                    
                    # SEND TO GOOGLE SHEET
                    if sheet:
                        try:
                            sheet.append_row(log_row)
                        except Exception as e:
                            st.error(f"Failed to append to sheet: {e}")
                
                else:
                    # Just show basic info if not logging
                    st.text(f"{matchup}: {clock} (Not logging yet - {remaining_sec_in_game:.0f}s left)")