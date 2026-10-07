"""
HoopsPass — synthetic NBA ticket-sales dataset generator (season 2025-26).

HoopsPass Inc. is a FICTIONAL primary-ticketing operator that sells a block of
seats ("allocation") for every NBA game on behalf of the home team, runs a resale
marketplace and sells season-ticket packages. Teams and arenas are real public
reference data; every customer, order, ticket, price and score is synthetic.

The dataset is designed for a "badly configured vs well-configured Genie Agent"
demo. Several realistic traps are built in on purpose (see docs/data_dictionary.md):

  T1  coded columns without meaning (channel_code, ticket_type_code, price_tier_code,
      loyalty_tier_code) -> decoded only through ref_codes
  T2  grain / fan-out: orders.total_usd x tickets; games.attendance is arena-wide,
      HoopsPass only sells games.allocated_seats; season orders have game_id NULL
  T3  home vs away: "tickets sold for the Lakers" means Lakers HOME games
  T4  business definitions: tickets sold (no COMP, only COMPLETED), GMV vs net revenue,
      average price (sold_price, no COMP), sell-through (sold / allocated, no resale),
      no-show rate (unscanned among sold)
  T5  dirty data: date_of_birth 1900-01-01 sentinel, ~2% duplicate customer records,
      Toronto is not in the US
  T6  PII: names, email, phone, date_of_birth, zip, card_last4 -> column masks;
      sales_region -> row filter

Usage (local):
    python nba_tickets_generator.py --out ./out --scale 0.05 --format parquet
Usage (Databricks): see databricks/01_generate_nba_tickets.py

Everything is deterministic for a given (seed, scale).
"""
from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Season calendar
# --------------------------------------------------------------------------------------
SEASON = "2025-26"
REG_START = date(2025, 10, 21)
REG_END = date(2026, 4, 12)
ALLSTAR_BREAK = (date(2026, 2, 13), date(2026, 2, 18))
PLAYIN_DATES = [date(2026, 4, 14), date(2026, 4, 15), date(2026, 4, 17)]
PLAYOFF_ROUND_START = {
    "R1": date(2026, 4, 18),
    "R2": date(2026, 5, 4),
    "CONF_FINALS": date(2026, 5, 19),
    "FINALS": date(2026, 6, 4),
}
SEASON_SALES_START = date(2025, 8, 4)   # season packages go on sale
SINGLE_GAME_ONSALE = date(2025, 9, 15)  # single-game tickets go on sale
DOB_SENTINEL = date(1900, 1, 1)         # legacy CRM import marker for unknown DOB

# --------------------------------------------------------------------------------------
# Reference data: 30 NBA teams and their arenas (public facts, capacities approximate)
# columns: code, city, nickname, state, country, conference, division, sales_region,
#          popularity (demand index), price_index (market pricing),
#          arena_name, arena_city, arena_state, capacity, opened_year, timezone
# --------------------------------------------------------------------------------------
TEAM_ROWS = [
    ("ATL", "Atlanta", "Hawks", "GA", "US", "East", "Southeast", "Southeast", 0.72, 0.90, "State Farm Arena", "Atlanta", "GA", 16600, 1999, "America/New_York"),
    ("BOS", "Boston", "Celtics", "MA", "US", "East", "Atlantic", "Northeast", 0.96, 1.30, "TD Garden", "Boston", "MA", 19156, 1995, "America/New_York"),
    ("BKN", "Brooklyn", "Nets", "NY", "US", "East", "Atlantic", "Northeast", 0.65, 1.05, "Barclays Center", "Brooklyn", "NY", 17732, 2012, "America/New_York"),
    ("CHA", "Charlotte", "Hornets", "NC", "US", "East", "Southeast", "Southeast", 0.60, 0.75, "Spectrum Center", "Charlotte", "NC", 19077, 2005, "America/New_York"),
    ("CHI", "Chicago", "Bulls", "IL", "US", "East", "Central", "Midwest", 0.85, 1.10, "United Center", "Chicago", "IL", 20917, 1994, "America/Chicago"),
    ("CLE", "Cleveland", "Cavaliers", "OH", "US", "East", "Central", "Midwest", 0.85, 0.95, "Rocket Arena", "Cleveland", "OH", 19432, 1994, "America/New_York"),
    ("DAL", "Dallas", "Mavericks", "TX", "US", "West", "Southwest", "Southwest", 0.85, 1.10, "American Airlines Center", "Dallas", "TX", 19200, 2001, "America/Chicago"),
    ("DEN", "Denver", "Nuggets", "CO", "US", "West", "Northwest", "West", 0.85, 1.00, "Ball Arena", "Denver", "CO", 19520, 1999, "America/Denver"),
    ("DET", "Detroit", "Pistons", "MI", "US", "East", "Central", "Midwest", 0.75, 0.85, "Little Caesars Arena", "Detroit", "MI", 20332, 2017, "America/Detroit"),
    ("GSW", "Golden State", "Warriors", "CA", "US", "West", "Pacific", "West", 0.97, 1.55, "Chase Center", "San Francisco", "CA", 18064, 2019, "America/Los_Angeles"),
    ("HOU", "Houston", "Rockets", "TX", "US", "West", "Southwest", "Southwest", 0.82, 0.95, "Toyota Center", "Houston", "TX", 18055, 2003, "America/Chicago"),
    ("IND", "Indiana", "Pacers", "IN", "US", "East", "Central", "Midwest", 0.78, 0.85, "Gainbridge Fieldhouse", "Indianapolis", "IN", 17274, 1999, "America/Indiana/Indianapolis"),
    ("LAC", "LA", "Clippers", "CA", "US", "West", "Pacific", "West", 0.78, 1.20, "Intuit Dome", "Inglewood", "CA", 18000, 2024, "America/Los_Angeles"),
    ("LAL", "Los Angeles", "Lakers", "CA", "US", "West", "Pacific", "West", 1.00, 1.60, "Crypto.com Arena", "Los Angeles", "CA", 18997, 1999, "America/Los_Angeles"),
    ("MEM", "Memphis", "Grizzlies", "TN", "US", "West", "Southwest", "Southwest", 0.72, 0.80, "FedExForum", "Memphis", "TN", 17794, 2004, "America/Chicago"),
    ("MIA", "Miami", "Heat", "FL", "US", "East", "Southeast", "Southeast", 0.85, 1.20, "Kaseya Center", "Miami", "FL", 19600, 1999, "America/New_York"),
    ("MIL", "Milwaukee", "Bucks", "WI", "US", "East", "Central", "Midwest", 0.82, 1.00, "Fiserv Forum", "Milwaukee", "WI", 17341, 2018, "America/Chicago"),
    ("MIN", "Minnesota", "Timberwolves", "MN", "US", "West", "Northwest", "Midwest", 0.80, 0.95, "Target Center", "Minneapolis", "MN", 18978, 1990, "America/Chicago"),
    ("NOP", "New Orleans", "Pelicans", "LA", "US", "West", "Southwest", "Southwest", 0.65, 0.80, "Smoothie King Center", "New Orleans", "LA", 16867, 1999, "America/Chicago"),
    ("NYK", "New York", "Knicks", "NY", "US", "East", "Atlantic", "Northeast", 0.95, 1.60, "Madison Square Garden", "New York", "NY", 19812, 1968, "America/New_York"),
    ("OKC", "Oklahoma City", "Thunder", "OK", "US", "West", "Northwest", "Southwest", 0.90, 1.00, "Paycom Center", "Oklahoma City", "OK", 18203, 2002, "America/Chicago"),
    ("ORL", "Orlando", "Magic", "FL", "US", "East", "Southeast", "Southeast", 0.75, 0.90, "Kia Center", "Orlando", "FL", 18846, 2010, "America/New_York"),
    ("PHI", "Philadelphia", "76ers", "PA", "US", "East", "Atlantic", "Northeast", 0.82, 1.10, "Xfinity Mobile Arena", "Philadelphia", "PA", 20478, 1996, "America/New_York"),
    ("PHX", "Phoenix", "Suns", "AZ", "US", "West", "Pacific", "West", 0.78, 1.05, "Mortgage Matchup Center", "Phoenix", "AZ", 17071, 1992, "America/Phoenix"),
    ("POR", "Portland", "Trail Blazers", "OR", "US", "West", "Northwest", "West", 0.65, 0.85, "Moda Center", "Portland", "OR", 19393, 1995, "America/Los_Angeles"),
    ("SAC", "Sacramento", "Kings", "CA", "US", "West", "Pacific", "West", 0.72, 0.95, "Golden 1 Center", "Sacramento", "CA", 17608, 2016, "America/Los_Angeles"),
    ("SAS", "San Antonio", "Spurs", "TX", "US", "West", "Southwest", "Southwest", 0.85, 0.95, "Frost Bank Center", "San Antonio", "TX", 18418, 2002, "America/Chicago"),
    ("TOR", "Toronto", "Raptors", "ON", "CA", "East", "Atlantic", "Northeast", 0.78, 1.10, "Scotiabank Arena", "Toronto", "ON", 19800, 1999, "America/Toronto"),
    ("UTA", "Utah", "Jazz", "UT", "US", "West", "Northwest", "West", 0.60, 0.85, "Delta Center", "Salt Lake City", "UT", 18306, 1991, "America/Denver"),
    ("WAS", "Washington", "Wizards", "DC", "US", "East", "Southeast", "Southeast", 0.58, 0.80, "Capital One Arena", "Washington", "DC", 20356, 1997, "America/New_York"),
]
TEAM_COLS = ["team_code", "city", "nickname", "state", "country", "conference", "division",
             "sales_region", "popularity", "price_index", "arena_name", "arena_city",
             "arena_state", "capacity", "opened_year", "timezone"]

# --------------------------------------------------------------------------------------
# Code tables (the meaning lives ONLY here and in ref_codes — on purpose)
# --------------------------------------------------------------------------------------
REF_CODES = [
    ("channel_code", "1", "BOX_OFFICE", "Arena box office window"),
    ("channel_code", "2", "WEB", "hoopspass.example (desktop web)"),
    ("channel_code", "3", "APP", "HoopsPass mobile app"),
    ("channel_code", "4", "PHONE", "Call center / phone sales"),
    ("channel_code", "5", "RESALE", "HoopsPass Resale marketplace (fan-to-fan)"),
    ("channel_code", "6", "SEASON", "Season ticket sales team (packages)"),
    ("ticket_type_code", "STD", "Standard", "Standard single-game ticket sold at face value or with a promo"),
    ("ticket_type_code", "SSN", "Season package", "Game included in a season-ticket package (one SEASON order covers all 41 home games)"),
    ("ticket_type_code", "GRP", "Group", "Group sales, 10+ seats in one order, group discount"),
    ("ticket_type_code", "COMP", "Complimentary", "Complimentary ticket, sold_price_usd = 0, NOT a sale"),
    ("ticket_type_code", "RSL", "Resale", "Ticket resold by a fan on the Resale marketplace; HoopsPass earns fee + commission only"),
    ("price_tier_code", "P1", "Courtside", "Courtside / floor seats"),
    ("price_tier_code", "P2", "Lower center", "Lower bowl, center sections"),
    ("price_tier_code", "P3", "Lower ends", "Lower bowl, corners and baselines"),
    ("price_tier_code", "P4", "Club", "Club level with lounge access"),
    ("price_tier_code", "P5", "Upper center", "Upper bowl, center sections"),
    ("price_tier_code", "P6", "Upper ends", "Upper bowl, corners and ends"),
    ("loyalty_tier_code", "0", "None", "No HoopsPass Rewards membership"),
    ("loyalty_tier_code", "1", "Rookie", "HoopsPass Rewards - entry tier"),
    ("loyalty_tier_code", "2", "Starter", "HoopsPass Rewards - 5+ games per season"),
    ("loyalty_tier_code", "3", "All-Star", "HoopsPass Rewards - 15+ games or season ticket holder"),
    ("loyalty_tier_code", "4", "MVP", "HoopsPass Rewards - invitation only, top 1% spenders"),
    ("order_status", "COMPLETED", "Completed", "Paid and fulfilled; the only status that counts as a sale"),
    ("order_status", "REFUNDED", "Refunded", "Fully refunded after purchase; tickets voided"),
    ("order_status", "CANCELLED", "Cancelled", "Cancelled before payment settled; tickets voided"),
    ("order_status", "CHARGEBACK", "Chargeback", "Payment disputed and reversed by the bank; tickets voided"),
    ("order_type", "SINGLE", "Single game", "Order for one specific game (orders.game_id is set)"),
    ("order_type", "SEASON", "Season package", "Season-ticket package; orders.game_id is NULL, game is on each ticket"),
    ("order_type", "GROUP", "Group", "Group sales order for one game"),
    ("order_type", "RESALE", "Resale", "Purchase on the Resale marketplace"),
    ("game_type", "REG", "Regular season", "Regular season game (82 per team)"),
    ("game_type", "PLAYIN", "Play-in", "Play-in tournament game"),
    ("game_type", "PLAYOFF", "Playoffs", "Playoff game (rounds R1, R2, CONF_FINALS, FINALS)"),
]

TIERS = ["P1", "P2", "P3", "P4", "P5", "P6"]
TIER_SHARE = np.array([0.006, 0.14, 0.18, 0.12, 0.25, 0.304])     # share of arena seats
TIER_BASE_PRICE = np.array([1200.0, 260.0, 160.0, 190.0, 75.0, 42.0])  # USD at price_index 1.0
TIER_SECTIONS = {
    "P1": [f"CS{i}" for i in range(1, 5)],
    "P2": [str(s) for s in range(101, 113)],
    "P3": [str(s) for s in range(113, 125)],
    "P4": [f"C{s}" for s in range(201, 217)],
    "P5": [str(s) for s in range(301, 313)],
    "P6": [str(s) for s in range(313, 331)],
}
TIER_SEATS_PER_ROW = {"P1": 20, "P2": 22, "P3": 22, "P4": 18, "P5": 24, "P6": 24}
ROW_LABELS = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + ["AA", "BB", "CC", "DD"]
NO_SHOW_RATE = {"STD": 0.07, "SSN": 0.14, "GRP": 0.10, "COMP": 0.25, "RSL": 0.06}
PLAYOFF_PRICE_MULT = {"PLAYIN": 1.4, "R1": 1.6, "R2": 2.0, "CONF_FINALS": 2.6, "FINALS": 3.5}
PROMO_CODES = ["EARLYBIRD", "FAMILY4", "STUDENT10", "REWARDS15", "FLASH20", "MILITARY10"]

FIRST_NAMES = """James Mary Robert Patricia John Jennifer Michael Linda David Elizabeth William Barbara
Richard Susan Joseph Jessica Thomas Sarah Christopher Karen Charles Lisa Daniel Nancy Matthew Betty
Anthony Sandra Mark Margaret Donald Ashley Steven Kimberly Andrew Emily Paul Donna Joshua Michelle
Kenneth Carol Kevin Amanda Brian Melissa George Deborah Timothy Stephanie Ronald Dorothy Jason Rebecca
Edward Sharon Jeffrey Laura Ryan Cynthia Jacob Amy Gary Kathleen Nicholas Angela Eric Shirley Jonathan
Brenda Stephen Emma Larry Anna Justin Pamela Scott Nicole Brandon Samantha Benjamin Katherine Samuel
Christine Gregory Helen Alexander Debra Patrick Rachel Frank Carolyn Raymond Janet Jack Maria Dennis
Catherine Jerry Heather Tyler Diane Aaron Olivia Jose Julie Adam Joyce Nathan Victoria Henry Ruth
Zachary Virginia Douglas Lauren Peter Kelly Kyle Christina Noah Joan Ethan Evelyn Jeremy Judith
Walter Andrea Christian Hannah Keith Megan Roger Cheryl Terry Jacqueline Austin Martha Sean Madison
Gerald Teresa Carl Gloria Dylan Sara Harold Janice Jordan Ann Jesse Kathryn Bryan Abigail Lawrence
Sophia Arthur Frances Gabriel Jean Bruce Alice Logan Judy Albert Isabella Willie Julia Alan Grace
Eugene Amber Russell Denise Vincent Danielle Elijah Marilyn Louis Beverly Bobby Charlotte Philip
Natalie Johnny Theresa Diego Priya Wei Mateo Aisha Kenji Fatima Luis Mei Omar Ingrid Tomasz Zofia
Rahul Chloe Mohammed Sofia Hiroshi Elena Carlos Ana Jamal Leah Marcus Nia Andre Camila Darius""".split()

LAST_NAMES = """Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Hernandez Lopez
Gonzalez Wilson Anderson Thomas Taylor Moore Jackson Martin Lee Perez Thompson White Harris Sanchez
Clark Ramirez Lewis Robinson Walker Young Allen King Wright Scott Torres Nguyen Hill Flores Green
Adams Nelson Baker Hall Rivera Campbell Mitchell Carter Roberts Gomez Phillips Evans Turner Diaz
Parker Cruz Edwards Collins Reyes Stewart Morris Morales Murphy Cook Rogers Gutierrez Ortiz Morgan
Cooper Peterson Bailey Reed Kelly Howard Ramos Kim Cox Ward Richardson Watson Brooks Chavez Wood
James Bennett Gray Mendoza Ruiz Hughes Price Alvarez Castillo Sanders Patel Myers Long Ross Foster
Jimenez Powell Jenkins Perry Russell Sullivan Bell Coleman Butler Henderson Barnes Gonzales Fisher
Vasquez Simmons Romero Jordan Patterson Alexander Hamilton Graham Reynolds Griffin Wallace Moreno
West Cole Hayes Bryant Herrera Gibson Ellis Tran Medina Aguilar Stevens Murray Ford Castro Marshall
Owens Harrison Fernandez McDonald Woods Washington Kennedy Wells Vargas Henry Chen Freeman Webb
Tucker Guzman Burns Crawford Olson Simpson Porter Hunter Gordon Mendez Silva Shaw Snyder Mason
Dixon Munoz Hunt Hicks Holmes Palmer Wagner Black Robertson Boyd Rose Stone Salazar Fox Warren
Mills Meyer Rice Schmidt Garza Daniels Ferguson Nichols Stephens Soto Weaver Ryan Gardner Payne
Grant Dunn Kelley Spencer Hawkins Arnold Pierce Vazquez Hansen Peters Santos Hart Bradley Knight
Elliott Cunningham Duncan Armstrong Hudson Carroll Lane Riley Andrews Alvarado Ray Delgado Berry
Perkins Hoffman Johnston Matthews Pena Richards Contreras Willis Carpenter Lawrence Sandoval Guerrero
Kowalski Nowak Okafor Singh Yamamoto Haddad Novak Lindqvist Oyelaran Nakamura Dubois Rossi""".split()

# US states + DC (+ a few Canadian provinces) with a rough population weight (millions)
STATE_POP = {
    "CA": 39.0, "TX": 30.5, "FL": 22.6, "NY": 19.6, "PA": 13.0, "IL": 12.5, "OH": 11.8, "GA": 11.0,
    "NC": 10.8, "MI": 10.0, "NJ": 9.3, "VA": 8.7, "WA": 7.8, "AZ": 7.4, "MA": 7.0, "TN": 7.1,
    "IN": 6.9, "MD": 6.2, "MO": 6.2, "WI": 5.9, "CO": 5.9, "MN": 5.7, "SC": 5.4, "AL": 5.1,
    "LA": 4.6, "KY": 4.5, "OR": 4.2, "OK": 4.1, "CT": 3.6, "UT": 3.4, "IA": 3.2, "NV": 3.2,
    "AR": 3.1, "MS": 2.9, "KS": 2.9, "NM": 2.1, "NE": 2.0, "ID": 2.0, "WV": 1.8, "HI": 1.4,
    "NH": 1.4, "ME": 1.4, "MT": 1.1, "RI": 1.1, "DE": 1.0, "SD": 0.9, "ND": 0.8, "AK": 0.7,
    "DC": 0.7, "VT": 0.6, "WY": 0.6,
    "ON": 15.5, "QC": 8.9, "BC": 5.5,
}
CANADA = {"ON", "QC", "BC"}


# --------------------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------------------
def build_teams_and_arenas() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.DataFrame(TEAM_ROWS, columns=TEAM_COLS)
    df = df.sort_values("team_code").reset_index(drop=True)
    df["team_id"] = np.arange(1, len(df) + 1)
    df["arena_id"] = np.arange(101, 101 + len(df))
    df["team_name"] = df["city"] + " " + df["nickname"]
    teams = df[["team_id", "team_code", "team_name", "city", "nickname", "state", "country",
                "conference", "division", "sales_region", "arena_id"]].copy()
    arenas = df[["arena_id", "arena_name", "arena_city", "arena_state", "country", "capacity",
                 "opened_year", "timezone"]].rename(columns={"arena_city": "city", "arena_state": "state"}).copy()
    return teams, arenas


def build_ref_codes() -> pd.DataFrame:
    return pd.DataFrame(REF_CODES, columns=["code_type", "code", "label", "description"])


def _team_meta() -> pd.DataFrame:
    df = pd.DataFrame(TEAM_ROWS, columns=TEAM_COLS).sort_values("team_code").reset_index(drop=True)
    df["team_id"] = np.arange(1, len(df) + 1)
    df["arena_id"] = np.arange(101, 101 + len(df))
    return df.set_index("team_id")


# --------------------------------------------------------------------------------------
# Schedule: 82 games per team, 41 home, NBA-like matchup formula
# --------------------------------------------------------------------------------------
def _matchups(meta: pd.DataFrame) -> list[tuple[int, int]]:
    """Returns (home_team_id, away_team_id) pairs for the regular season: 1230 games."""
    games: list[tuple[int, int]] = []
    conferences = {c: meta[meta.conference == c] for c in ("East", "West")}
    for conf, cdf in conferences.items():
        divisions = sorted(cdf.division.unique())
        div_teams = {d: sorted(cdf[cdf.division == d].index.tolist()) for d in divisions}
        # 1) inside the division: 4 games, 2 home each
        for d, ids in div_teams.items():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    games += [(ids[i], ids[j])] * 2 + [(ids[j], ids[i])] * 2
        # 2) other divisions in the conference: 4 games for most pairs, 3 games for a symmetric
        #    subset (team i of division X vs teams i and i+1 of division X+1; the "outgoing" team hosts 2)
        three_game = set()
        for k, d in enumerate(divisions):
            nxt = divisions[(k + 1) % 3]
            a, b = div_teams[d], div_teams[nxt]
            for i in range(5):
                for j in (i, (i + 1) % 5):
                    three_game.add((a[i], b[j]))  # a[i] hosts 2, b[j] hosts 1
        for k, d in enumerate(divisions):
            for d2 in divisions[k + 1:]:
                for x in div_teams[d]:
                    for y in div_teams[d2]:
                        if (x, y) in three_game:
                            games += [(x, y)] * 2 + [(y, x)]
                        elif (y, x) in three_game:
                            games += [(y, x)] * 2 + [(x, y)]
                        else:
                            games += [(x, y)] * 2 + [(y, x)] * 2
    # 3) inter-conference: 2 games, 1 home each
    for x in conferences["East"].index:
        for y in conferences["West"].index:
            games += [(x, y), (y, x)]
    return games


def _target_games_per_day(day: date, rng: np.random.Generator) -> int:
    if day == REG_START:
        return 2
    if day == REG_START + timedelta(days=1):
        return 12
    if day == date(2025, 12, 25):
        return 5
    if day == date(2025, 12, 24):
        return 0
    wd = day.weekday()  # 0 = Monday
    mean = {0: 7, 1: 7.5, 2: 8, 3: 6, 4: 9, 5: 8.5, 6: 5.5}[wd]
    return int(np.clip(rng.normal(mean, 1.6), 2, 14))


def _tipoff(day: date, tz: str, rng: np.random.Generator, game_type: str) -> datetime:
    wd = day.weekday()
    if day == date(2025, 12, 25):
        hour, minute = rng.choice([12, 14, 17, 20]), 0
    elif game_type != "REG":
        hour, minute = rng.choice([18, 19, 20]), rng.choice([0, 30])
    elif wd >= 5:
        hour, minute = rng.choice([13, 15, 17, 18, 19]), rng.choice([0, 30])
    else:
        hour, minute = (19, rng.choice([0, 30])) if "Los_Angeles" not in tz and "Phoenix" not in tz else (19, 30)
        if rng.random() < 0.15:
            hour = 20
    return datetime(day.year, day.month, day.day, int(hour), int(minute))


def build_schedule(meta: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Regular season + play-in + playoffs. Returns the games table (without sales columns)."""
    matchups = _matchups(meta)
    assert len(matchups) == 1230, len(matchups)
    remaining = [matchups[i] for i in rng.permutation(len(matchups))]
    days = []
    d = REG_START
    while d <= REG_END:
        if not (ALLSTAR_BREAK[0] <= d <= ALLSTAR_BREAK[1]):
            days.append(d)
        d += timedelta(days=1)

    scheduled: list[tuple[date, int, int]] = []
    last_played: dict[int, date] = {}
    for di, day in enumerate(days):
        target = _target_games_per_day(day, rng)
        # make sure we do not run out of days: force the pace in the last stretch
        days_left = len(days) - di
        min_needed = math.ceil(len(remaining) / days_left) if days_left else len(remaining)
        target = max(target, min_needed)
        busy: set[int] = set()
        picked = []
        for g in remaining:
            if len(picked) >= target:
                break
            h, a = g
            if h in busy or a in busy:
                continue
            # soft rule: avoid three games in three nights
            def tired(t: int) -> bool:
                lp = last_played.get(t)
                return lp is not None and (day - lp).days == 1 and rng.random() < 0.55
            if (tired(h) or tired(a)) and days_left > 3:
                continue
            picked.append(g)
            busy.update(g)
        for g in picked:
            remaining.remove(g)
            scheduled.append((day, g[0], g[1]))
            last_played[g[0]] = day
            last_played[g[1]] = day
    assert not remaining, f"{len(remaining)} games could not be scheduled"

    rows = []
    for seq, (day, h, a) in enumerate(sorted(scheduled, key=lambda r: (r[0], r[1])), start=1):
        rows.append({
            "game_id": 22500000 + seq, "season": SEASON, "game_type": "REG", "playoff_round": None,
            "game_date": day, "home_team_id": h, "away_team_id": a, "arena_id": int(meta.loc[h, "arena_id"]),
            "series_game_no": None,
        })
    games = pd.DataFrame(rows)

    # ---- standings (synthetic): strength = popularity + noise -------------------------------
    strength = meta["popularity"] * 0.7 + rng.normal(0, 0.12, len(meta))
    strength = pd.Series(np.asarray(strength), index=meta.index)
    seeds = {}
    for conf in ("East", "West"):
        ids = meta[meta.conference == conf].index
        order = strength.loc[ids].sort_values(ascending=False).index.tolist()
        seeds[conf] = order  # position 0 = seed 1

    # ---- play-in ------------------------------------------------------------------------
    playin_rows, seq = [], 0
    playoff_field = {}
    for conf in ("East", "West"):
        s = seeds[conf]
        s7, s8, s9, s10 = s[6], s[7], s[8], s[9]
        def play(home, away, day, no):
            nonlocal seq
            seq += 1
            p_home = 0.55 + 0.5 * (strength[home] - strength[away])
            winner = home if rng.random() < np.clip(p_home, 0.2, 0.8) else away
            playin_rows.append({"game_id": 52500000 + seq, "season": SEASON, "game_type": "PLAYIN",
                                "playoff_round": "PLAYIN", "game_date": day, "home_team_id": home,
                                "away_team_id": away, "arena_id": int(meta.loc[home, "arena_id"]),
                                "series_game_no": no, "_winner": winner})
            return winner
        w78 = play(s7, s8, PLAYIN_DATES[0], 1)
        l78 = s8 if w78 == s7 else s7
        w910 = play(s9, s10, PLAYIN_DATES[1], 1)
        w8 = play(l78, w910, PLAYIN_DATES[2], 1)
        playoff_field[conf] = s[:6] + [w78, w8]
    playin = pd.DataFrame(playin_rows)

    # ---- playoffs -----------------------------------------------------------------------
    po_rows, seq = [], 0

    def series(hi, lo, rnd, start, series_id):
        """Best-of-seven: pick the winner first, then place the loser's wins among the first n-1 games."""
        nonlocal seq
        n_games = int(rng.choice([4, 5, 6, 7], p=[0.12, 0.25, 0.33, 0.30]))
        p_hi = float(np.clip(0.5 + 0.6 * (strength[hi] - strength[lo]), 0.3, 0.75))
        winner = hi if rng.random() < p_hi else lo
        loser = lo if winner == hi else hi
        loser_wins = set(rng.choice(n_games - 1, size=n_games - 4, replace=False).tolist()) if n_games > 4 else set()
        pattern = [hi, hi, lo, lo, hi, lo, hi]  # 2-2-1-1-1 home-court format
        day = start
        for g in range(1, n_games + 1):
            home = pattern[g - 1]
            away = lo if home == hi else hi
            game_winner = loser if (g - 1) in loser_wins else winner
            seq += 1
            po_rows.append({"game_id": 42500000 + series_id * 10 + g, "season": SEASON, "game_type": "PLAYOFF",
                            "playoff_round": rnd, "game_date": day, "home_team_id": home, "away_team_id": away,
                            "arena_id": int(meta.loc[home, "arena_id"]), "series_game_no": g, "_winner": game_winner})
            day = day + timedelta(days=2 if g not in (2, 4) else 3)
        round_end[rnd] = max(round_end.get(rnd, day), day)
        return winner

    # rounds are scheduled one after another: the next round starts 2 days after the previous one ends
    round_end: dict[str, date] = {}
    r1_winners, r2_winners, conf_champs = {}, {}, {}
    sid = 0
    for conf in ("East", "West"):
        f = playoff_field[conf]
        pairs = [(f[0], f[7]), (f[3], f[4]), (f[2], f[5]), (f[1], f[6])]
        r1 = []
        for i, (hi, lo) in enumerate(pairs):
            sid += 1
            r1.append(series(hi, lo, "R1", PLAYOFF_ROUND_START["R1"] + timedelta(days=i % 2), sid))
        r1_winners[conf] = r1
    r2_start = max(round_end["R1"] + timedelta(days=2), PLAYOFF_ROUND_START["R2"])
    for conf in ("East", "West"):
        f, r1 = playoff_field[conf], r1_winners[conf]
        r2 = []
        for i, (x, y) in enumerate([(r1[0], r1[1]), (r1[2], r1[3])]):
            hi, lo = (x, y) if f.index(x) < f.index(y) else (y, x)
            sid += 1
            r2.append(series(hi, lo, "R2", r2_start + timedelta(days=i % 2), sid))
        r2_winners[conf] = r2
    cf_start = max(round_end["R2"] + timedelta(days=2), PLAYOFF_ROUND_START["CONF_FINALS"])
    for i, conf in enumerate(("East", "West")):
        f, r2 = playoff_field[conf], r2_winners[conf]
        hi, lo = (r2[0], r2[1]) if f.index(r2[0]) < f.index(r2[1]) else (r2[1], r2[0])
        sid += 1
        conf_champs[conf] = series(hi, lo, "CONF_FINALS", cf_start + timedelta(days=i % 2), sid)
    e, w = conf_champs["East"], conf_champs["West"]
    hi, lo = (e, w) if strength[e] >= strength[w] else (w, e)
    sid += 1
    series(hi, lo, "FINALS", max(round_end["CONF_FINALS"] + timedelta(days=3), PLAYOFF_ROUND_START["FINALS"]), sid)
    playoffs = pd.DataFrame(po_rows)

    games = pd.concat([games.assign(_winner=None), playin, playoffs], ignore_index=True)

    # ---- enrich: tipoff, tv, scores, attendance, allocation ------------------------------
    tz = meta["timezone"]
    games["tipoff_local"] = [_tipoff(d, tz[h], rng, t) for d, h, t in zip(games.game_date, games.home_team_id, games.game_type)]
    pop_h = meta.loc[games.home_team_id, "popularity"].to_numpy()
    pop_a = meta.loc[games.away_team_id, "popularity"].to_numpy()
    games["is_national_tv"] = (rng.random(len(games)) < np.clip((pop_h + pop_a) / 2 - 0.55, 0.02, 0.9)) | (games.game_type != "REG")
    # scores: winner consistent with play-in/playoff outcomes
    home_pts = rng.normal(113, 11, len(games)).round().astype(int)
    margin = np.abs(rng.normal(9, 6, len(games))).round().astype(int) + 1
    home_wins = rng.random(len(games)) < np.clip(0.54 + 0.5 * (pop_h - pop_a), 0.25, 0.8)
    forced = games["_winner"].notna().to_numpy()
    home_wins = np.where(forced, games["_winner"].to_numpy() == games["home_team_id"].to_numpy(), home_wins)
    away_pts = np.where(home_wins, home_pts - margin, home_pts + margin)
    games["home_points"] = home_pts
    games["away_points"] = away_pts.astype(int)
    cap = meta.loc[games.home_team_id, "capacity"].to_numpy()
    demand = _demand_index(games, pop_h, pop_a)
    games["attendance"] = (cap * np.clip(0.80 + 0.22 * demand + rng.normal(0, 0.02, len(games)), 0.72, 1.0)).astype(int)
    # HoopsPass allocation is a per-team contract (stable share of the arena, mildly higher for big markets)
    team_share = pd.Series(np.clip(0.20 + 0.12 * meta["popularity"].to_numpy() + rng.normal(0, 0.03, len(meta)), 0.18, 0.40), index=meta.index)
    games["allocated_seats"] = (cap * team_share.loc[games.home_team_id].to_numpy() * rng.uniform(0.95, 1.05, len(games))).astype(int)
    games["_demand"] = demand
    games = games.drop(columns=["_winner"])
    games = games.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    return games


def _demand_index(games: pd.DataFrame, pop_h: np.ndarray, pop_a: np.ndarray) -> np.ndarray:
    """0..1 demand index used for sell-through and pricing."""
    base = (0.6 * pop_h + 0.4 * pop_a) ** 1.5
    wd = pd.to_datetime(games.game_date).dt.weekday.to_numpy()
    weekend = np.isin(wd, [4, 5]) * 0.08 + (wd == 6) * 0.03
    special = (pd.to_datetime(games.game_date) == pd.Timestamp("2025-12-25")).to_numpy() * 0.2
    opening = (pd.to_datetime(games.game_date) <= pd.Timestamp(REG_START + timedelta(days=1))).to_numpy() * 0.15
    po = games.game_type.map({"REG": 0.0, "PLAYIN": 0.35, "PLAYOFF": 0.5}).to_numpy()
    return np.clip(base + weekend + special + opening + po, 0, 1)


# --------------------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------------------
def build_customers(n: int, meta: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    states = list(STATE_POP)
    w = np.array([STATE_POP[s] for s in states], dtype=float)
    # fans cluster around team markets
    for s in meta["state"]:
        if s in STATE_POP:
            w[states.index(s)] *= 1.6
    w = w / w.sum()
    state = rng.choice(states, size=n, p=w)
    first = rng.choice(FIRST_NAMES, size=n)
    last = rng.choice(LAST_NAMES, size=n)
    cid = np.arange(100001, 100001 + n)
    email_num = rng.integers(1, 9999, n)
    domains = rng.choice(["example.com", "example.net", "example.org"], size=n, p=[0.6, 0.25, 0.15])
    email = np.char.lower(np.char.add(np.char.add(np.char.add(first.astype(str), "."), last.astype(str)),
                                      np.char.add(np.char.add(email_num.astype(str), "@"), domains.astype(str))))
    area = rng.integers(201, 989, n)
    phone = np.array([f"({a}) 555-{x:04d}" for a, x in zip(area, rng.integers(100, 200, n))])  # 555-01xx fictional range
    birth_year = rng.integers(1950, 2008, n)
    dob = pd.to_datetime(pd.DataFrame({"year": birth_year, "month": rng.integers(1, 13, n), "day": rng.integers(1, 29, n)}))
    sentinel = rng.random(n) < 0.12  # legacy CRM import: unknown DOB stored as 1900-01-01
    dob = dob.where(~sentinel, pd.Timestamp(DOB_SENTINEL))
    loyalty = rng.choice([0, 1, 2, 3, 4], size=n, p=[0.55, 0.22, 0.13, 0.08, 0.02])
    signup = pd.Series(pd.Timestamp("2017-01-01") + pd.to_timedelta(rng.integers(0, (date(2026, 6, 15) - date(2017, 1, 1)).days, n), unit="D"))
    team_ids = meta.index.to_numpy()
    team_state = meta["state"].to_numpy()
    r = rng.random(n)
    fav = rng.choice(team_ids, size=n).astype(float)
    for s in np.unique(team_state):
        ids = team_ids[team_state == s]
        m = (state == s) & (r >= 0.12) & (r < 0.75)
        if m.any():
            fav[m] = rng.choice(ids, size=int(m.sum()))
    fav[r < 0.12] = np.nan
    is_ca = np.isin(state, list(CANADA))
    zip_code = np.char.zfill(rng.integers(1000, 99999, n).astype(str), 5).astype(object)
    n_ca = int(is_ca.sum())
    if n_ca:
        letters = np.array([chr(65 + i) for i in range(26)])
        zip_code[is_ca] = [f"M{a}{b} {c}{d}{e}" for a, b, c, d, e in zip(
            rng.integers(1, 10, n_ca), letters[rng.integers(0, 26, n_ca)], rng.integers(1, 10, n_ca),
            letters[rng.integers(0, 26, n_ca)], rng.integers(1, 10, n_ca))]
    customers = pd.DataFrame({
        "customer_id": cid, "first_name": first, "last_name": last, "email": email, "phone": phone,
        "date_of_birth": dob.dt.date, "home_state": state,
        "home_country": np.where(np.isin(state, list(CANADA)), "CA", "US"), "home_zip": zip_code,
        "loyalty_tier_code": loyalty.astype(int), "favorite_team_id": pd.array(fav, dtype="Int64"),
        "signup_date": signup.dt.date, "marketing_opt_in": rng.random(n) < 0.62,
        "created_at": signup + pd.to_timedelta(rng.integers(0, 86400, n), unit="s"),
    })
    # ~2% duplicate records (same person imported twice: different id, name casing, same email)
    n_dup = int(n * 0.02)
    dup = customers.sample(n=n_dup, random_state=int(rng.integers(0, 2**31 - 1))).copy()
    dup["customer_id"] = np.arange(cid.max() + 1, cid.max() + 1 + n_dup)
    dup["first_name"] = dup["first_name"].str.upper()
    dup["last_name"] = dup["last_name"].str.upper()
    dup["email"] = dup["email"].str.upper()
    dup["date_of_birth"] = DOB_SENTINEL
    dup["loyalty_tier_code"] = 0
    dup["created_at"] = dup["created_at"] + pd.Timedelta(days=400)
    dup["signup_date"] = pd.to_datetime(dup["created_at"]).dt.date
    customers = pd.concat([customers, dup], ignore_index=True)
    return customers


# --------------------------------------------------------------------------------------
# Sales generation
# --------------------------------------------------------------------------------------
@dataclass
class Ctx:
    meta: pd.DataFrame
    games: pd.DataFrame
    customers: pd.DataFrame
    rng: np.random.Generator
    scale: float
    local_pool: dict[str, np.ndarray]          # state -> customer ids
    all_ids: np.ndarray
    season_holders: dict[int, pd.DataFrame]    # team_id -> holders (customer_id, tier, seat_idx)
    next_order_id: int = 5_000_001
    next_ticket_id: int = 90_000_001


def _seat_from_index(tier: str, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    spr = TIER_SEATS_PER_ROW[tier]
    sections = np.array(TIER_SECTIONS[tier])
    rows_per_section = 20 if tier != "P1" else 2
    seats_per_section = spr * rows_per_section
    sec = sections[(idx // seats_per_section) % len(sections)]
    row = np.array(ROW_LABELS)[(idx % seats_per_section) // spr]
    seat = idx % spr + 1
    return sec, row, seat


def _pick_customers(ctx: Ctx, n: int, home_state: str, local_share: float = 0.65) -> np.ndarray:
    local = ctx.local_pool.get(home_state)
    if local is None or len(local) == 0:
        return ctx.rng.choice(ctx.all_ids, size=n)
    is_local = ctx.rng.random(n) < local_share
    out = ctx.rng.choice(ctx.all_ids, size=n)
    k = int(is_local.sum())
    if k:
        out[is_local] = ctx.rng.choice(local, size=k)
    return out


def _order_sizes(ctx: Ctx, n_tickets: int, kind: str) -> np.ndarray:
    if n_tickets <= 0:
        return np.array([], dtype=int)
    if kind == "GRP":
        sizes = ctx.rng.integers(10, 41, size=max(1, n_tickets // 10 + 2))
    elif kind == "COMP":
        sizes = ctx.rng.choice([1, 2, 3, 4], size=n_tickets, p=[0.4, 0.4, 0.1, 0.1])
    elif kind == "RSL":
        sizes = ctx.rng.choice([1, 2, 3, 4], size=n_tickets, p=[0.25, 0.55, 0.1, 0.1])
    else:
        sizes = ctx.rng.choice([1, 2, 3, 4, 5, 6, 8], size=n_tickets, p=[0.18, 0.50, 0.12, 0.13, 0.03, 0.02, 0.02])
    cum = np.cumsum(sizes)
    keep = cum <= n_tickets
    sizes = sizes[keep]
    rest = n_tickets - (cum[keep][-1] if keep.any() else 0)
    if rest > 0:
        sizes = np.append(sizes, rest)
    return sizes.astype(int)


def _order_timestamps(ctx: Ctx, n: int, game_date: date, game_type: str, tipoff: datetime) -> pd.Series:
    """Purchase timestamps: skewed towards the last two weeks before the game, never after tip-off."""
    rng = ctx.rng
    tip_secs = tipoff.hour * 3600 + tipoff.minute * 60
    onsale = SINGLE_GAME_ONSALE if game_type == "REG" else game_date - timedelta(days=6 if game_type == "PLAYOFF" else 3)
    max_days = max(1, (game_date - onsale).days)
    bucket = rng.random(n)
    hi = max_days + 1
    days = np.where(bucket < 0.45, rng.integers(0, min(8, hi), n),
             np.where(bucket < 0.75, rng.integers(min(8, max_days), hi, n), rng.integers(min(31, max_days), hi, n)))
    secs = rng.integers(0, 86400, n)
    secs = np.where(rng.random(n) < 0.5, rng.integers(17 * 3600, 23 * 3600, n), secs)  # evening bias
    secs = np.where(days == 0, rng.integers(6 * 3600, max(6 * 3600 + 1, tip_secs - 900), n), secs)  # game day: before tip-off
    ts = pd.Timestamp(game_date) - pd.to_timedelta(days, unit="D") + pd.to_timedelta(secs, unit="s")
    return ts


def _statuses(ctx: Ctx, n: int, kind: str) -> np.ndarray:
    if kind == "COMP":
        return np.array(["COMPLETED"] * n)
    if kind == "SEASON":
        return ctx.rng.choice(["COMPLETED", "CANCELLED"], size=n, p=[0.98, 0.02])
    return ctx.rng.choice(["COMPLETED", "REFUNDED", "CANCELLED", "CHARGEBACK"], size=n, p=[0.945, 0.03, 0.02, 0.005])


def _channels(ctx: Ctx, n: int, kind: str) -> np.ndarray:
    if kind == "GRP":
        return ctx.rng.choice([4, 2], size=n, p=[0.6, 0.4])
    if kind == "COMP":
        return np.full(n, 1)
    if kind == "RSL":
        return np.full(n, 5)
    if kind == "SEASON":
        return np.full(n, 6)
    return ctx.rng.choice([2, 3, 1, 4], size=n, p=[0.45, 0.35, 0.10, 0.10])


def _payments(ctx: Ctx, n: int, kind: str) -> tuple[np.ndarray, np.ndarray]:
    if kind == "COMP":
        return np.array(["NONE"] * n), np.array([None] * n, dtype=object)
    pm = ctx.rng.choice(["CARD", "APPLE_PAY", "PAYPAL", "GIFT_CARD"], size=n, p=[0.70, 0.15, 0.10, 0.05])
    last4 = np.where(np.isin(pm, ["CARD", "APPLE_PAY"]), np.char.zfill(ctx.rng.integers(0, 10000, n).astype(str), 4), None)
    return pm, last4.astype(object)


def init_context(meta, games, customers, rng, scale) -> Ctx:
    local_pool = {s: g["customer_id"].to_numpy() for s, g in customers.groupby("home_state")}
    ctx = Ctx(meta=meta, games=games, customers=customers, rng=rng, scale=scale,
              local_pool=local_pool, all_ids=customers["customer_id"].to_numpy(), season_holders={})
    # season ticket holders per team: ~28% of the mean allocation, fixed seats, tiers P2-P6
    reg = games[games.game_type == "REG"]
    mean_alloc = reg.groupby("home_team_id")["allocated_seats"].mean()
    for team_id, alloc in mean_alloc.items():
        k = int(round(alloc * 0.28 * scale))
        tier_counts = np.floor(np.array([0.0, 0.16, 0.20, 0.12, 0.24, 0.28]) * k).astype(int)
        tier_idx = np.repeat(np.arange(len(TIERS)), tier_counts)
        seat_idx = np.concatenate([np.arange(c) for c in tier_counts]) if tier_counts.sum() else np.array([], dtype=int)
        holders = pd.DataFrame({"tier_idx": tier_idx, "seat_idx": seat_idx})
        holders["customer_id"] = _pick_customers(ctx, len(holders), meta.loc[team_id, "state"], local_share=0.85)
        ctx.season_holders[team_id] = holders
    return ctx


def generate_season_orders(ctx: Ctx) -> pd.DataFrame:
    """One SEASON order per season-ticket holder (orders.game_id is NULL on purpose)."""
    rows = []
    for team_id, holders in ctx.season_holders.items():
        n = len(holders)
        ids = np.arange(ctx.next_order_id, ctx.next_order_id + n)
        ctx.next_order_id += n
        holders["order_id"] = ids
        holders["status"] = _statuses(ctx, n, "SEASON")
        pm, last4 = _payments(ctx, n, "SEASON")
        ts = pd.Timestamp(SEASON_SALES_START) + pd.to_timedelta(ctx.rng.integers(0, 60, n), unit="D") + pd.to_timedelta(ctx.rng.integers(8 * 3600, 21 * 3600, n), unit="s")
        # package price = 41 games x face x 0.85, computed later from tickets; placeholder filled in finalize
        rows.append(pd.DataFrame({
            "order_id": ids, "customer_id": holders["customer_id"].to_numpy(), "game_id": pd.array([pd.NA] * n, dtype="Int64"),
            "order_type": "SEASON", "order_ts": ts, "channel_code": 6, "status": holders["status"].to_numpy(),
            "payment_method": pm, "card_last4": last4, "promo_code": None, "ticket_count": 41,
            "subtotal_usd": 0.0, "fees_usd": 0.0, "total_usd": 0.0, "_team_id": team_id,
        }))
    return pd.concat(rows, ignore_index=True)


def generate_game(ctx: Ctx, g: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Orders and tickets for one game (numpy-first; one DataFrame per table per game)."""
    rng, meta = ctx.rng, ctx.meta
    home = int(g.home_team_id)
    home_state = meta.loc[home, "state"]
    price_index = float(meta.loc[home, "price_index"])
    pop_a = float(meta.loc[int(g.away_team_id), "popularity"])
    demand = float(g._demand)
    alloc = int(round(g.allocated_seats * ctx.scale))
    cap = int(meta.loc[home, "capacity"])
    is_reg = g.game_type == "REG"

    sellthrough = float(np.clip(0.50 + 0.52 * demand + rng.normal(0, 0.03), 0.48, 1.0))
    if not is_reg:
        sellthrough = float(np.clip(0.96 + rng.normal(0, 0.015), 0.9, 1.0))
    sold_primary = int(alloc * sellthrough)

    price_mult = 1.0 if is_reg else PLAYOFF_PRICE_MULT[g.playoff_round]
    price_mult *= 1 + 0.30 * (pop_a - 0.7) + (0.06 if demand > 0.75 else 0)

    tier_alloc = np.floor(TIER_SHARE * alloc).astype(int)
    tier_cap = np.floor(TIER_SHARE * cap).astype(int)
    tier_sold = np.floor(TIER_SHARE * sold_primary).astype(int)

    # column accumulators (numpy arrays per block)
    acc: dict[str, list] = {k: [] for k in ("order_id", "section", "row_label", "seat_number", "tier_idx",
                                            "kind", "face", "sold", "fee", "comm", "status", "promo")}

    def add(order_id, sec, row, seat, tier_idx, kind, face, sold, fee, comm, status, promo):
        n = len(face)
        acc["order_id"].append(np.broadcast_to(np.asarray(order_id, dtype=np.int64), (n,)))
        acc["section"].append(np.asarray(sec, dtype="<U5"))
        acc["row_label"].append(np.asarray(row, dtype="<U2"))
        acc["seat_number"].append(np.asarray(seat, dtype=np.int64))
        acc["tier_idx"].append(np.broadcast_to(np.asarray(tier_idx, dtype=np.int64), (n,)))
        acc["kind"].append(np.broadcast_to(np.asarray(kind, dtype="<U4"), (n,)))
        acc["face"].append(np.asarray(face, dtype=np.float64))
        acc["sold"].append(np.asarray(sold, dtype=np.float64))
        acc["fee"].append(np.asarray(fee, dtype=np.float64))
        acc["comm"].append(np.broadcast_to(np.asarray(comm, dtype=np.float64), (n,)))
        acc["status"].append(np.broadcast_to(np.asarray(status, dtype="<U10"), (n,)))
        acc["promo"].append(np.broadcast_to(np.asarray(promo, dtype=bool), (n,)))

    # ---- season package tickets (REG only) --------------------------------------------
    ssn_by_tier: dict[int, int] = {}
    if is_reg:
        h = ctx.season_holders[home]
        if len(h):
            tier_idx_h = h["tier_idx"].to_numpy()
            for ti in np.unique(tier_idx_h):
                ssn_by_tier[int(ti)] = int((tier_idx_h == ti).sum())
            sec, row, seat = _seats_from_index(tier_idx_h, h["seat_idx"].to_numpy())
            face = _face_value(tier_idx_h, price_index, price_mult, rng)
            sold = np.round(face * 0.85, 2)
            add(h["order_id"].to_numpy(), sec, row, seat, tier_idx_h, "SSN", face, sold,
                np.round(sold * 0.08, 2), 0.0, h["status"].to_numpy(), False)

    # ---- single-game, group and comp tickets inside the allocation ----------------------
    for ti in range(len(TIERS)):
        n_ssn = ssn_by_tier.get(ti, 0)
        n_left = max(0, int(tier_sold[ti]) - n_ssn)
        if n_left == 0:
            continue
        n_comp = int(round(tier_alloc[ti] * (0.012 if is_reg else 0.005)))
        n_grp = int(round(n_left * 0.06)) if (is_reg and ti in (2, 4, 5)) else 0
        n_std = max(0, n_left - n_comp - n_grp)
        pool_lo, pool_hi = n_ssn, max(n_ssn + 1, int(tier_alloc[ti]))
        n_pool = pool_hi - pool_lo
        n_take = min(n_pool, n_std + n_grp + n_comp)
        idx = pool_lo + rng.choice(n_pool, size=n_take, replace=False)
        kinds = np.array(["STD"] * min(n_std, n_take) + ["GRP"] * max(0, min(n_grp, n_take - min(n_std, n_take))), dtype="<U4")
        kinds = np.concatenate([kinds, np.array(["COMP"] * max(0, n_take - len(kinds)), dtype="<U4")])
        tier_arr = np.full(n_take, ti)
        sec, row, seat = _seats_from_index(tier_arr, idx)
        face = _face_value(tier_arr, price_index, price_mult, rng)
        promo = (rng.random(n_take) < 0.15) & (kinds == "STD")
        sold = np.where(promo, face * rng.uniform(0.85, 0.95, n_take), face)
        sold = np.where(kinds == "GRP", face * 0.80, sold)
        sold = np.where(kinds == "COMP", 0.0, sold)
        sold = np.round(sold, 2)
        fee = np.round(np.where(kinds == "COMP", 0.0, sold * 0.12 + 1.50), 2)
        add(-1, sec, row, seat, tier_arr, kinds, face, sold, fee, 0.0, "", promo)

    # ---- resale marketplace tickets (seats OUTSIDE our allocation) ---------------------
    n_rsl = int(sold_primary * np.clip(rng.uniform(0.05, 0.13) * (0.6 + demand), 0.02, 0.2))
    if n_rsl > 0:
        tier_rsl = np.floor(TIER_SHARE * n_rsl).astype(int)
        for ti in range(len(TIERS)):
            n = int(tier_rsl[ti])
            n_pool = int(tier_cap[ti] - tier_alloc[ti])
            if n == 0 or n_pool <= 0:
                continue
            n = min(n, n_pool)
            idx = int(tier_alloc[ti]) + rng.choice(n_pool, size=n, replace=False)
            tier_arr = np.full(n, ti)
            sec, row, seat = _seats_from_index(tier_arr, idx)
            face = _face_value(tier_arr, price_index, price_mult, rng)
            mult = np.clip(rng.lognormal(mean=np.log(1.0 + 0.5 * demand), sigma=0.35, size=n), 0.45, 4.0)
            sold = np.round(face * mult, 2)
            add(-1, sec, row, seat, tier_arr, "RSL", face, sold, np.round(sold * 0.10, 2), np.round(sold * 0.10, 2), "", False)

    if not acc["face"]:
        return pd.DataFrame(), pd.DataFrame()
    A = {k: np.concatenate(v) for k, v in acc.items()}
    n = len(A["face"])
    order_id = A["order_id"].copy()
    status = A["status"].copy()
    local_order = np.full(n, -1, dtype=np.int64)   # index into this game's orders (-1 = season order)

    # ---- group tickets into orders -------------------------------------------------------
    o: dict[str, list] = {k: [] for k in ("order_id", "customer_id", "order_type", "order_ts", "channel_code",
                                          "status", "payment_method", "card_last4", "promo_code", "ticket_count")}
    n_orders_total = 0
    for kind, order_type in (("STD", "SINGLE"), ("GRP", "GROUP"), ("COMP", "SINGLE"), ("RSL", "RESALE")):
        pos = np.flatnonzero(A["kind"] == kind)
        n_t = len(pos)
        if n_t == 0:
            continue
        sizes = _order_sizes(ctx, n_t, kind)
        n_o = len(sizes)
        ids = np.arange(ctx.next_order_id, ctx.next_order_id + n_o)
        ctx.next_order_id += n_o
        rng.shuffle(pos)
        rep = np.repeat(np.arange(n_o), sizes)
        order_id[pos] = ids[rep]
        st = _statuses(ctx, n_o, kind)
        status[pos] = st[rep]
        local_order[pos] = n_orders_total + rep
        pm, last4 = _payments(ctx, n_o, kind)
        promo_any = np.bincount(rep, weights=A["promo"][pos].astype(float), minlength=n_o) > 0
        promo = np.where(promo_any, rng.choice(PROMO_CODES, size=n_o), None).astype(object)
        o["order_id"].append(ids)
        o["customer_id"].append(_pick_customers(ctx, n_o, home_state, local_share=0.70 if kind != "RSL" else 0.55))
        o["order_type"].append(np.full(n_o, order_type, dtype="<U7"))
        o["order_ts"].append(_order_timestamps(ctx, n_o, g.game_date, g.game_type, pd.Timestamp(g.tipoff_local)))
        o["channel_code"].append(_channels(ctx, n_o, kind))
        o["status"].append(st)
        o["payment_method"].append(pm)
        o["card_last4"].append(last4)
        o["promo_code"].append(promo)
        o["ticket_count"].append(sizes)
        n_orders_total += n_o

    orders = pd.DataFrame()
    if n_orders_total:
        O = {k: np.concatenate(v) for k, v in o.items() if k != "order_ts"}
        O["order_ts"] = pd.DatetimeIndex(np.concatenate([np.asarray(x) for x in o["order_ts"]]))
        m = local_order >= 0
        subtotal = np.bincount(local_order[m], weights=A["sold"][m], minlength=n_orders_total)
        fees = np.bincount(local_order[m], weights=A["fee"][m], minlength=n_orders_total)
        processing = np.where(np.isin(O["order_type"], ["SINGLE", "GROUP"]) & (subtotal > 0), 2.50, 0.0)
        orders = pd.DataFrame({
            "order_id": O["order_id"], "customer_id": O["customer_id"],
            "game_id": pd.array(np.full(n_orders_total, int(g.game_id)), dtype="Int64"),
            "order_type": O["order_type"], "order_ts": O["order_ts"], "channel_code": O["channel_code"].astype(int),
            "status": O["status"], "payment_method": O["payment_method"], "card_last4": O["card_last4"],
            "promo_code": O["promo_code"], "ticket_count": O["ticket_count"].astype(int),
            "subtotal_usd": np.round(subtotal, 2), "fees_usd": np.round(fees + processing, 2),
            "total_usd": np.round(subtotal + fees + processing, 2),
        })

    # ---- ticket ids, scans ---------------------------------------------------------------
    ticket_id = np.arange(ctx.next_ticket_id, ctx.next_ticket_id + n)
    ctx.next_ticket_id += n
    no_show = np.array([NO_SHOW_RATE[k] for k in ("STD", "SSN", "GRP", "COMP", "RSL")])
    kind_idx = np.searchsorted(np.array(["COMP", "GRP", "RSL", "SSN", "STD"]), A["kind"])
    no_show_sorted = np.array([NO_SHOW_RATE[k] for k in ("COMP", "GRP", "RSL", "SSN", "STD")])
    completed = status == "COMPLETED"
    scanned = completed & (rng.random(n) >= no_show_sorted[kind_idx])
    tip = pd.Timestamp(g.tipoff_local)
    offsets = rng.integers(-75 * 60, 20 * 60, n)
    late = rng.random(n) < 0.03
    offsets = np.where(late, rng.integers(20 * 60, 70 * 60, n), offsets)
    scan_ts = pd.Series(tip + pd.to_timedelta(offsets, unit="s"))
    scan_ts = scan_ts.where(scanned, pd.NaT)

    tickets = pd.DataFrame({
        "ticket_id": ticket_id, "order_id": order_id, "game_id": int(g.game_id), "home_team_id": home,
        "section": A["section"], "row_label": A["row_label"], "seat_number": A["seat_number"],
        "price_tier_code": np.array(TIERS)[A["tier_idx"]], "ticket_type_code": A["kind"],
        "face_value_usd": A["face"], "sold_price_usd": A["sold"], "service_fee_usd": A["fee"],
        "commission_usd": A["comm"], "scanned_at": scan_ts.to_numpy(),
    })
    return orders, tickets


def _seats_from_index(tier_idx: np.ndarray, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised seat labels for arrays of (tier index, seat index within tier)."""
    sec = np.empty(len(idx), dtype="<U5")
    row = np.empty(len(idx), dtype="<U2")
    seat = np.empty(len(idx), dtype=np.int64)
    for ti in np.unique(tier_idx):
        m = tier_idx == ti
        s, r, st = _seat_from_index(TIERS[int(ti)], idx[m])
        sec[m], row[m], seat[m] = s, r, st
    return sec, row, seat


def _face_value(tier_idx: np.ndarray, price_index: float, price_mult: float, rng: np.random.Generator) -> np.ndarray:
    base = TIER_BASE_PRICE[np.asarray(tier_idx, dtype=int)]
    noise = rng.uniform(0.95, 1.05, len(base))
    return np.round(base * price_index * price_mult * noise, 0)


def finalize_season_orders(season_orders: pd.DataFrame, ssn_money: pd.DataFrame) -> pd.DataFrame:
    """Fill season package totals from the generated SSN tickets."""
    so = season_orders.merge(ssn_money, left_on="order_id", right_index=True, how="left")
    so["subtotal_usd"] = so["sold_price_usd"].fillna(0).round(2)
    so["fees_usd"] = so["service_fee_usd"].fillna(0).round(2)
    so["total_usd"] = (so["subtotal_usd"] + so["fees_usd"]).round(2)
    so["ticket_count"] = so["n"].fillna(0).astype(int)
    so = so.drop(columns=["sold_price_usd", "service_fee_usd", "n", "_team_id"])
    return so


ORDER_COLS = ["order_id", "customer_id", "game_id", "order_type", "order_ts", "channel_code", "status",
              "payment_method", "card_last4", "promo_code", "ticket_count", "subtotal_usd", "fees_usd", "total_usd"]


def generate_all(scale: float = 1.0, seed: int = 2026, n_customers: int | None = None,
                 batch_games: int = 40, progress=print) -> Iterator[tuple[str, pd.DataFrame]]:
    """
    Yields ("table_name", DataFrame) in dependency order. Fact tables come in batches:
    ("orders", df) / ("tickets", df) may be yielded many times -> append.
    """
    rng = np.random.default_rng(seed)
    meta = _team_meta()
    teams, arenas = build_teams_and_arenas()
    yield "teams", teams
    yield "arenas", arenas
    yield "ref_codes", build_ref_codes()

    games = build_schedule(meta, rng)
    games_out = games.drop(columns=["_demand"]).copy()
    games_out["series_game_no"] = games_out["series_game_no"].astype("Int64")
    yield "games", games_out

    n_cust = n_customers or max(3000, int(300_000 * scale))
    customers = build_customers(n_cust, meta, rng)
    yield "customers", customers

    ctx = init_context(meta, games, customers, rng, scale)
    season_orders = generate_season_orders(ctx)

    ssn_money_parts = []
    n_games = len(games)
    for start in range(0, n_games, batch_games):
        chunk = games.iloc[start:start + batch_games]
        o_parts, t_parts = [], []
        for _, g in chunk.iterrows():
            o, t = generate_game(ctx, g)
            if not o.empty:
                o_parts.append(o)
            if not t.empty:
                t_parts.append(t)
        if t_parts:
            t_all = pd.concat(t_parts, ignore_index=True)
            ssn = t_all[t_all.ticket_type_code == "SSN"]
            if len(ssn):
                m = ssn.groupby("order_id").agg(sold_price_usd=("sold_price_usd", "sum"),
                                                service_fee_usd=("service_fee_usd", "sum"), n=("ticket_id", "size"))
                ssn_money_parts.append(m)
            yield "tickets", t_all
        if o_parts:
            yield "orders", pd.concat(o_parts, ignore_index=True)[ORDER_COLS]
        progress(f"games {min(start + batch_games, n_games)}/{n_games} | tickets so far {ctx.next_ticket_id - 90_000_001:,}")

    ssn_money = pd.concat(ssn_money_parts).groupby(level=0).sum() if ssn_money_parts else pd.DataFrame(columns=["sold_price_usd", "service_fee_usd", "n"])
    yield "orders", finalize_season_orders(season_orders, ssn_money)[ORDER_COLS]


# --------------------------------------------------------------------------------------
# Local CLI
# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--scale", type=float, default=1.0, help="allocation scale (1.0 = full season, ~6.5M tickets)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    ap.add_argument("--customers", type=int, default=None, help="override number of customers")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    part_no: dict[str, int] = {}
    for name, df in generate_all(scale=args.scale, seed=args.seed, n_customers=args.customers):
        d = os.path.join(args.out, name)
        os.makedirs(d, exist_ok=True)
        part_no[name] = part_no.get(name, 0) + 1
        path = os.path.join(d, f"part-{part_no[name]:04d}.{args.format}")
        if args.format == "parquet":
            df.to_parquet(path, index=False, coerce_timestamps="us", allow_truncated_timestamps=True)
        else:
            df.to_csv(path, index=False)
    print("done:", {k: v for k, v in part_no.items()})


if __name__ == "__main__":
    main()
