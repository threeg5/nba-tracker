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
        creds_json_str = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
        if not creds_json_str:
            return None

        creds_dict = json.loads(creds_json_str)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # Open the Sheet (Make sure your sheet is named EXACTLY 'NBA Logs')
        sheet = client.open("NBA Logs").sheet1 
        return sheet
    except Exception as e:
        st.error(f"Google Sheet Connection Error: {e}")
        return None

# --- CONFIGURATION ---
st.set_page_config(page_title="NBA Data Logger", layout="wide")
st.sidebar.header("⚙️ Logger Settings")

refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", 5, 60, 10)
force_log = st.sidebar.checkbox("⚠️ TEST MODE: Log All Games Now", value=False, help="Check this to ignore the 90-second rule and force logging immediately.")

count = st_autorefresh(interval=refresh_rate * 1000, key="data_refresh")

# Constants
HEADERS_CDN = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"}

# --- DATA FUNCTIONS ---

@st.cache_data(ttl=3600)
def get_season_baseline():
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

season_avg, season_median = get_season_baseline()
games = get_live_games()
live_odds = get_live_odds()
sheet = connect_to_gsheet()

if not games:
    st.info("No games active.")
else:
    active_count = 0
    for game in games:
        if game['gameStatus'] >= 2: # In Progress
            active_count += 1
            res = calculate_pace(game['gameId'])
            if res:
                pace, home, away, h_score, a_score, period, clock, mins_elapsed = res
                
                # --- CALCULATIONS ---
                total_current = h_score + a_score
                elapsed_sec = mins_elapsed * 60
                
                proj_rem = 0
                rich_proj = total_current
                rem_diff = 0
                
                # Logic to determine "Time Left"
                remaining_sec_in_game = 0
                if period >= 4:
                     end_of_period_sec = period * 12 * 60
                     remaining_sec_in_game = end_of_period_sec - elapsed_sec
                else:
                     remaining_sec_in_game = 9999 
                
                # --- ODDS & PROJECTIONS ---
                matchup = f"{away} @ {home}"
                odds_data = live_odds.get(matchup, {})
                dk_total = odds_data.get('Over', 0)
                
                if elapsed_sec > 60:
                    points_per_sec = total_current / elapsed_sec
                    # If Q4, project remaining. If early game, project full 48m.
                    if period >= 4:
                         proj_rem = points_per_sec * remaining_sec_in_game
                    else:
                         proj_rem = points_per_sec * (2880 - elapsed_sec)

                    rich_proj = total_current + proj_rem
                    
                    if dk_total:
                        implied_rem = dk_total - total_current
                        rem_diff = proj_rem - implied_rem
                
                # --- LOGGING TRIGGER ---
                # Log if: (In 4th Qtr AND <90s left) OR (Test Mode is ON)
                is_logging_time = (period >= 4 and 0 < remaining_sec_in_game <= 90)
                should_log = is_logging_time or force_log

                # --- PREPARE DATA ---
                log_row = [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    matchup,        
                    clock,          
                    f"{pace:.1f}",  
                    h_score,        
                    a_score,        
                    dk_total,       
                    f"{rich_proj:.1f}", 
                    f"{proj_rem:.1f}",  
                    f"{rem_diff:.1f}"   
                ]
                
                # --- VISUAL DISPLAY (ALWAYS SHOW THIS) ---
                # Status Badge
                if should_log:
                    st.success(f"🔴 LOGGING ACTIVE: {matchup} (Writing to Sheets...)")
                else:
                    st.info(f"👀 MONITORING: {matchup} (Waiting for last 90s...)")
                
                # Data Table
                df_display = pd.DataFrame([log_row], columns=["Time", "Matchup", "Clock", "Pace", "Home", "Away", "DK", "Rich Proj", "Proj Rem", "Edge"])
                st.table(df_display)
                
                # --- WRITE TO SHEET (ONLY IF TRIGGERED) ---
                if should_log and sheet:
                    try:
                        sheet.append_row(log_row)
                    except Exception as e:
                        st.error(f"❌ Failed to append to sheet: {e}")
                
                st.divider()

    if active_count == 0:
        st.warning("Games scheduled but none currently active.")
