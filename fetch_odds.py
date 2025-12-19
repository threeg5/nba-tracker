

import requests
import json
import time
import os
from datetime import datetime

# --- CONFIGURATION ---
# We will set this in Render's "Environment" tab later to keep it safe
API_KEY = os.getenv('ODDS_API_KEY', '624e9d20721bec2374f998f1a727bfa3') 
REGION = 'us'
MARKETS = 'totals'
BOOKMAKERS = 'draftkings'
REFRESH_RATE = 300  # 5 minutes (Safe for free tier)

# Map Odds-API names to NBA-API Tricodes
TEAM_MAP = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN', 'Charlotte Hornets': 'CHA',
    'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE', 'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN',
    'Detroit Pistons': 'DET', 'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
    'Los Angeles Clippers': 'LAC', 'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM', 'Miami Heat': 'MIA',
    'Milwaukee Bucks': 'MIL', 'Minnesota Timberwolves': 'MIN', 'New Orleans Pelicans': 'NOP', 'New York Knicks': 'NYK',
    'Oklahoma City Thunder': 'OKC', 'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI', 'Phoenix Suns': 'PHX',
    'Portland Trail Blazers': 'POR', 'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SAS', 'Toronto Raptors': 'TOR',
    'Utah Jazz': 'UTA', 'Washington Wizards': 'WAS'
}

def fetch_draftkings_odds():
    if not API_KEY or "PASTE" in API_KEY:
        print("Error: API Key not found.")
        return

    url = f'https://api.the-odds-api.com/v4/sports/basketball_nba/odds'
    params = {
        'api_key': API_KEY,
        'regions': REGION,
        'markets': MARKETS,
        'bookmakers': BOOKMAKERS,
        'oddsFormat': 'american',
    }

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching Odds...")
    
    try:
        response = requests.get(url, params=params)
        if response.status_code != 200:
            print(f"API Error: {response.status_code}")
            return

        data = response.json()
        odds_dict = {}
        
        for game in data:
            home_code = TEAM_MAP.get(game['home_team'])
            away_code = TEAM_MAP.get(game['away_team'])
            
            if not home_code or not away_code: continue

            dk_book = next((b for b in game['bookmakers'] if b['key'] == 'draftkings'), None)
            if dk_book:
                totals = next((m for m in dk_book['markets'] if m['key'] == 'totals'), None)
                if totals:
                    over = next((o for o in totals['outcomes'] if o['name'] == 'Over'), None)
                    if over:
                        matchup_key = f"{away_code} @ {home_code}"
                        odds_dict[matchup_key] = {"Over": over.get('point')}
                        print(f"   {matchup_key}: {over.get('point')}")

        with open("nba_odds.json", "w") as f:
            json.dump(odds_dict, f)
        print("   Saved to nba_odds.json")

    except Exception as e:
        print(f"   Error: {e}")

if __name__ == "__main__":
    # Fetch immediately on start
    fetch_draftkings_odds()
    while True:
        time.sleep(REFRESH_RATE)
        fetch_draftkings_odds()
