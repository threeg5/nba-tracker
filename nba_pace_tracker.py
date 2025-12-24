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
        
        # Open the Sheet
        sheet = client.open("NBA Logs").sheet1 
        return sheet
    except Exception as e:
        st.error(f"Google Sheet Connection Error: {e}")
        return None

def check_and_add_headers(sheet):
    """Adds headers to Google Sheet if it is empty"""
    if sheet:
        try:
            # Check if cell A1 is empty
            if not sheet.acell('A1').value:
                headers = [
                    "Time", "Matchup", "Clock", "Pace", "Home Score", "Away Score", 
                    "DK Total", "Rich Proj", "Proj Rem", "Edge (Rem Diff)", "Rich Adjusted"
                ]
                sheet.append_row(headers)
        except:
            pass # Fail silently if check fails to avoid blocking app

# --- CONFIGURATION ---
st.set_page_config(page_title="The Rich - NBA Data Logger", layout="wide")
st.sidebar.header("⚙️ Logger Settings")

refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", 5, 60, 10)
force_log = st.sidebar.checkbox("⚠️ TEST MODE: Log All Games Now", value=False, help="Check this to ignore the 90-second rule and force logging immediately.")

count = st_autorefresh(interval=refresh_rate * 1000, key="data_refresh")

# Constants
HEADERS_CDN = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"}
TEAM_COLORS = { # Added back for visual flair if needed, or just standard text
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
st.title("📊 The Rich - NBA Data Logger & Dashboard")

season_avg, season_median = get_season_baseline()
games = get_live_games()
live_odds = get_live_odds()
sheet = connect_to_gsheet()

# One-time header check
if 'headers_checked' not in st.session_state:
    check_and_add_headers(sheet)
    st.session_state.headers_checked = True

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
                rich_adjusted_val = 0 # Numeric value for logic
                
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
                
                # Default Text Values
                proj_text = "N/A"
                rich_proj_text = "N/A"
                proj_remaining_text = "N/A"
                implied_remaining_text = "N/A"
                diff_text = "N/A"
                rich_adjusted_text = "N/A"
                ppm_text = "N/A"
                implied_eff = "N/A"
                pace_delta = pace - season_median

                if elapsed_sec > 60:
                    # 1. Pace-Adjusted Proj
                    if dk_total:
                        pace_factor = pace / season_avg
                        proj_text = f"{dk_total * pace_factor:.1f}"

                    # 2. Rich Proj & Remainder
                    points_per_sec = total_current / elapsed_sec
                    
                    if period >= 4:
                         proj_rem = points_per_sec * remaining_sec_in_game
                    else:
                         proj_rem = points_per_sec * (2880 - elapsed_sec)

                    rich_proj = total_current + proj_rem
                    rich_proj_text = f"{rich_proj:.1f}"
                    proj_remaining_text = f"{proj_rem:.1f}"
                    
                    if dk_total:
                        implied_rem = dk_total - total_current
                        implied_remaining_text = f"{implied_rem:.1f}"
                        
                        rem_diff = proj_rem - implied_rem
                        diff_text = f"{rem_diff:.1f}"
                        
                        # 3. Rich Adjusted
                        # Formula: (Rem Diff / Pace Delta) + Rich Proj
                        if abs(pace_delta) > 0.1:
                            rich_adjusted_val = (rem_diff / pace_delta) + rich_proj
                            rich_adjusted_text = f"{rich_adjusted_val:.1f}"

                if elapsed_sec > 30:
                    ppm = total_current / (elapsed_sec / 60)
                    ppm_text = f"{ppm:.1f}"
                
                if pace > 0 and dk_total:
                     implied_eff = f"{(dk_total / season_avg) * 100:.1f}"

                # --- VISUAL DASHBOARD (ALWAYS VISIBLE) ---
                st.subheader(f"{matchup} | {clock}")
                
                # Row 1
                c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
                c1.metric("Clock", clock)
                c2.metric("Pace", f"{pace:.1f}", delta=f"{pace_delta:.1f}")
                c3.metric("Pace-Adj Proj", proj_text)
                c4.metric("DK Total", f"{dk_total if dk_total else 'N/A'}")
                c5.metric("Pts Per Min", ppm_text)
                c6.metric("The Rich Proj", rich_proj_text)
                c7.metric("Implied Eff", implied_eff)
                
                # Row 2
                r2_c1, r2_c2, r2_c3, r2_c4, r2_c5, r2_c6, r2_c7 = st.columns(7)
                r2_c1.metric("Proj Rem Pts", proj_remaining_text)
                r2_c2.metric("Implied Rem Pts", implied_remaining_text)
                r2_c3.metric("Rem Diff", diff_text, delta=diff_text if diff_text != "N/A" else None)
                r2_c4.metric("Rich Adjusted", rich_adjusted_text) # IT IS BACK!
                
                # --- LOGGING TRIGGER ---
                is_logging_time = (period >= 4 and 0 < remaining_sec_in_game <= 90)
                should_log = is_logging_time or force_log

                if should_log:
                    st.caption("🔴 LOGGING TO SHEETS...")
                    log_row = [
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                        matchup,        
                        clock,          
                        f"{pace:.1f}",  
                        h_score,        
                        a_score,        
                        dk_total,       
                        rich_proj_text, 
                        proj_remaining_text,  
                        diff_text,
                        rich_adjusted_text  # ADDED TO LOG
                    ]
                    
                    if sheet:
                        try:
                            sheet.append_row(log_row)
                        except Exception as e:
                            st.error(f"❌ Failed to append to sheet: {e}")
                
                st.divider()

    if active_count == 0:
        st.warning("Games scheduled but none currently active.")

