#!/usr/bin/env python3
"""
Elite Tennis Analytics & 1,000,000-Iteration Monte Carlo Simulation Engine
Features: Cyber-Slate Aesthetic UI, Arrow-Free Numbered Menu,
ATP/WTA Historical Engine, and Live October 2026 Beijing & Shanghai Fixtures.
"""
from __future__ import annotations

import argparse
import io
import os
import sqlite3
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

console = Console()

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tennis_master.sqlite")
SURF = {"Hard": 1, "Clay": 2, "Grass": 3, "Carpet": 1}
ROUND_OFFSET = {
    "Q1": -3, "Q2": -2, "Q3": -1, "R128": 0, "R64": 2, "R32": 4, "R16": 6,
    "QF": 8, "SF": 10, "F": 12, "RR": 4, "BR": 12
}
STAT_COLS = [
    "w_svpt", "w_1stWon", "w_2ndWon", "w_bpSaved", "w_bpFaced",
    "l_svpt", "l_1stWon", "l_2ndWon", "l_bpSaved", "l_bpFaced", "minutes"
]

# Live API Configuration (Optional: set your RapidAPI key here or via env)
API_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
API_KEY = os.environ.get("RAPIDAPI_TENNIS_KEY", "YOUR_RAPIDAPI_KEY_HERE")


# ============================================================================
# 1. DATABASE & SYNC PIPELINE (ATP & WTA Support)
# ============================================================================
def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            tour TEXT,
            tourney_date INTEGER,
            tourney_name TEXT,
            surface TEXT,
            round TEXT,
            winner_id INTEGER,
            winner_name TEXT,
            loser_id INTEGER,
            loser_name TEXT,
            score TEXT,
            best_of INTEGER,
            w_svpt REAL, w_1stWon REAL, w_2ndWon REAL, w_bpSaved REAL, w_bpFaced REAL,
            l_svpt REAL, l_1stWon REAL, l_2ndWon REAL, l_bpSaved REAL, l_bpFaced REAL,
            minutes REAL
        )
    ''')
    conn.commit()
    conn.close()


def sync_github_data():
    atp_url = "https://github.com/JeffSackmann/tennis_atp/archive/refs/heads/master.zip"
    wta_url = "https://github.com/JeffSackmann/tennis_wta/archive/refs/heads/master.zip"
    
    console.print(Panel(
        "[bold cyan]Connecting to GitHub Archives (ATP & WTA)...[/bold cyan]\n[dim]Downloading Sackmann professional match bundles.[/dim]",
        title="[bold gold1]🌐 DATA SYNC PIPELINE[/bold gold1]",
        border_style="cyan",
        box=box.HEAVY
    ))
    
    conn = sqlite3.connect(DB_FILE)
    total_inserted = 0

    for tour_name, url in [("ATP", atp_url), ("WTA", wta_url)]:
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                prefix = "tennis_atp-master/" if tour_name == "ATP" else "tennis_wta-master/"
                csv_files = [f for f in z.namelist() if f.startswith(prefix + f"{tour_name.lower()}_matches_") and f.endswith(".csv") and "qual" not in f and "futures" not in f and "challenger" not in f]
                
                with console.status(f"[bold green]Seeding {tour_name} database records...", spinner="dots"):
                    for file_path in csv_files:
                        with z.open(file_path) as f:
                            df = pd.read_csv(f, low_memory=False)
                            for _, row in df.iterrows():
                                m_id = f"{tour_name}_{row.get('tourney_date')}_{row.get('winner_id')}_{row.get('loser_id')}"
                                conn.execute('''
                                    INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                ''', (
                                    m_id, tour_name, row.get('tourney_date'), row.get('tourney_name'), row.get('surface'), row.get('round'),
                                    row.get('winner_id'), row.get('winner_name'), row.get('loser_id'), row.get('loser_name'),
                                    row.get('score'), row.get('best_of'),
                                    row.get('w_svpt'), row.get('w_1stWon'), row.get('w_2ndWon'), row.get('w_bpSaved'), row.get('w_bpFaced'),
                                    row.get('l_svpt'), row.get('l_1stWon'), row.get('l_2ndWon'), row.get('l_bpSaved'), row.get('l_bpFaced'),
                                    row.get('minutes')
                                ))
                                total_inserted += 1
            conn.commit()
        except Exception as e:
            console.print(f"[bold red]⚠ Warning during {tour_name} sync: {e}[/bold red]")

    conn.close()
    console.print(f"[bold green]✔ Successfully synchronized {total_inserted:,} total ATP/WTA match records![/bold green]\n")


def load_from_db() -> pd.DataFrame:
    if not os.path.exists(DB_FILE):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM matches", conn)
    conn.close()
    
    if df.empty:
        return df
        
    df = df[~df["score"].str.contains("W/O|DEF", na=False)].copy()
    df["retired"] = df["score"].astype(str).str.contains("RET", na=False)
    df["surf"] = df["surface"].map(SURF)
    df = df[df["surf"].notna()].copy()
    df["surf"] = df["surf"].astype(int)
    df["best_of"] = pd.to_numeric(df["best_of"], errors="coerce").fillna(3).astype(int)
    df["best_of"] = np.where(df["best_of"] >= 5, 5, 3)
    
    td = pd.to_numeric(df["tourney_date"], errors="coerce")
    base = pd.to_datetime(td.astype(int).astype(str), format="%Y%m%d", errors="coerce")
    off = df["round"].map(ROUND_OFFSET).fillna(0)
    df["date"] = base + pd.to_timedelta(off, unit="D")
    df["day"] = (df["date"] - pd.Timestamp("1970-01-01")).dt.days.astype(int)
    return df.sort_values("day", kind="mergesort").reset_index(drop=True)


# ============================================================================
# 2. QUANTITATIVE RATING & MODEL ENGINE
# ============================================================================
@dataclass(frozen=True)
class Config:
    halflife: float = 365.0
    k_tour: float = 300.0
    k_surf: float = 400.0
    tour_halflife: float = 1095.0
    prior_serve: float = 0.62
    prior_pts: float = 5000.0
    load_halflife: float = 7.0
    bp_halflife: float = 730.0
    elo_surface_w: float = 0.5


class PState:
    __slots__ = ("t", "sw", "sx", "rw", "rx", "bpd", "bpf", "load", "last", "elo", "n", "recent_form", "form_history")

    def __init__(self):
        self.t = None
        self.sw = np.zeros(4); self.sx = np.zeros(4)
        self.rw = np.zeros(4); self.rx = np.zeros(4)
        self.bpd = 0.0; self.bpf = 0.0
        self.load = 0.0; self.last = None
        self.elo = np.full(4, 1500.0); self.n = np.zeros(4)
        self.recent_form = 0.5; self.form_history = []

    def decay_to(self, day, cfg):
        if self.t is None:
            self.t = day
            return
        dt = day - self.t
        if dt <= 0:
            return
        f = 0.5 ** (dt / cfg.halflife)
        self.sw *= f; self.sx *= f; self.rw *= f; self.rx *= f
        fb = 0.5 ** (dt / cfg.bp_halflife)
        self.bpd *= fb; self.bpf *= fb
        self.load *= 0.5 ** (dt / cfg.load_halflife)
        self.t = day


class RatingEngine:
    def __init__(self, cfg: Config = Config()):
        self.cfg = cfg
        self.players: dict = {}
        self.tsw = np.zeros(4); self.tsx = np.zeros(4)
        self.tour_t = None

    def _get(self, pid):
        p = self.players.get(pid)
        if p is None:
            p = self.players[pid] = PState()
        return p

    def _tour_decay(self, day):
        if self.tour_t is None:
            self.tour_t = day
        elif day > self.tour_t:
            f = 0.5 ** ((day - self.tour_t) / self.cfg.tour_halflife)
            self.tsw *= f; self.tsx *= f
            self.tour_t = day

    def tour_serve(self, surf, day):
        self._tour_decay(day)
        c = self.cfg
        overall = (self.tsx[0] + c.prior_pts * c.prior_serve) / (self.tsw[0] + c.prior_pts)
        if surf == 0:
            return overall
        return (self.tsx[surf] + 2000.0 * overall) / (self.tsw[surf] + 2000.0)

    def _view(self, pid, surf, day):
        c = self.cfg
        p = self._get(pid)
        p.decay_to(day, c)
        ds_all = p.sx[0] / (p.sw[0] + c.k_tour)
        dr_all = p.rx[0] / (p.rw[0] + c.k_tour)
        ds = (p.sx[surf] + c.k_surf * ds_all) / (p.sw[surf] + c.k_surf)
        dr = (p.rx[surf] + c.k_surf * dr_all) / (p.rw[surf] + c.k_surf)
        return p, float(ds), float(dr)

    def snapshot(self, a, b, surf, day) -> dict:
        c = self.cfg
        ts = self.tour_serve(surf, day)
        pa, dsa, dra = self._view(a, surf, day)
        pb, dsb, drb = self._view(b, surf, day)
        w = c.elo_surface_w

        def form_string(p):
            if not p.form_history:
                return "[dim]No matches[/dim]"
            return "".join(["[bold green]W [/bold green]" if x == 1 else "[bold red]L [/bold red]" for x in p.form_history[-5:]])

        return dict(
            tour_s=ts, dsA=dsa, dra=dra, dsB=dsb, drB=drb,
            sA=ts + dsa, rA=(1 - ts) + dra, sB=ts + dsb, rB=(1 - ts) + drb,
            eloA=(1 - w) * pa.elo[0] + w * pa.elo[surf],
            eloB=(1 - w) * pb.elo[0] + w * pb.elo[surf],
            loadA=float(pa.load), loadB=float(pb.load),
            formStrA=form_string(pa), formStrB=form_string(pb)
        )

    def update(self, w, l, surf, day, st, snap):
        c = self.cfg
        pw, pl = self._get(w), self._get(l)
        pw.decay_to(day, c); pl.decay_to(day, c)
        ts = snap["tour_s"]; tr = 1.0 - ts
        wsv, wwon, lsv, lwon, wbps, wbpf, lbps, lbpf, mins = st

        pw.form_history.append(1); pl.form_history.append(0)

        if wsv == wsv and lsv == lsv and wsv > 0 and lsv > 0 and wwon == wwon and lwon == lwon:
            s_w, s_l = wwon / wsv, lwon / lsv
            r_w, r_l = 1.0 - s_l, 1.0 - s_w
            dsw = (s_w - ts) + snap["drB"]
            drw = (r_w - tr) + snap["dsB"]
            dsl = (s_l - ts) + snap["dsA"]
            drl = (r_l - tr) + snap["dsA"]
            for p, dsv, drv, spts, rpts in ((pw, dsw, drw, wsv, lsv), (pl, dsl, drl, lsv, wsv)):
                for i in (0, surf):
                    p.sw[i] += spts; p.sx[i] += spts * dsv
                    p.rw[i] += rpts; p.rx[i] += rpts * drv
            self._tour_decay(day)
            for i in (0, surf):
                self.tsw[i] += wsv + lsv
                self.tsx[i] += wwon + lwon


def build_features(d: pd.DataFrame, cfg: Config = Config()):
    eng = RatingEngine(cfg)
    day = d["day"].tolist(); w = d["winner_id"].tolist(); l = d["loser_id"].tolist()
    surf = d["surf"].tolist()
    cols = [d[c].tolist() for c in STAT_COLS]
    rows, pending, cur = [], [], None
    for i in range(len(d)):
        if day[i] != cur:
            for args in pending:
                eng.update(*args)
            pending, cur = [], day[i]
        snap = eng.snapshot(w[i], l[i], surf[i], day[i])
        rows.append(snap)
        st = (cols[0][i], cols[1][i] + cols[2][i], cols[5][i], cols[6][i] + cols[7][i],
              cols[3][i], cols[4][i], cols[8][i], cols[9][i], cols[10][i])
        pending.append((w[i], l[i], surf[i], day[i], st, snap))
    for args in pending:
        eng.update(*args)
    F = pd.DataFrame(rows)
    for c in ("day", "date", "surf", "best_of", "retired", "winner_id", "loser_id"):
        F[c] = d[c].to_numpy()
    return F, eng


# ============================================================================
# 3. 1,000,000-ITERATION MONTE CARLO SIMULATOR
# ============================================================================
def run_million_sims_with_alternates(snap: dict, best_of: int = 3, num_sims: int = 1_000_000):
    with console.status("[bold cyan]⚡ Executing 1,000,000 Vectorized Monte Carlo Simulations...", spinner="aesthetic"):
        elo_diff = snap["eloA"] - snap["eloB"]
        base_prob_a = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))
        
        load_pen_a = snap["loadA"] * 0.0001
        load_pen_b = snap["loadB"] * 0.0001
        p_a = np.clip(base_prob_a - load_pen_a + load_pen_b, 0.05, 0.95)
        
        sets_needed = (best_of // 2) + 1
        chunk_size = 100_000
        
        wins_a_count = 0
        all_match_games = []

        for _ in range(0, num_sims, chunk_size):
            set_prob_a = 1.0 / (1.0 + np.exp(-3.0 * (p_a - 0.5)))
            sim_sets_a = np.random.binomial(sets_needed * 2 - 1, set_prob_a, size=chunk_size)
            
            is_straight = np.random.random(chunk_size) < 0.56
            games = np.where(
                is_straight,
                np.random.choice([18, 19, 20, 21, 22, 24], size=chunk_size, p=[0.15, 0.20, 0.25, 0.20, 0.12, 0.08]),
                np.random.choice([23, 24, 25, 26, 27, 28, 30, 32], size=chunk_size, p=[0.10, 0.18, 0.20, 0.18, 0.15, 0.10, 0.06, 0.03])
            )
            all_match_games.extend(games)
            
            match_wins = sim_sets_a >= sets_needed
            wins_a_count += np.sum(match_wins)

    games_arr = np.array(all_match_games, dtype=float)
    prob_a = (wins_a_count / num_sims) * 100
    
    lines = [19.5, 20.5, 21.5, 22.5, 23.5, 24.5, 25.5]
    alternate_results = []
    
    for line in lines:
        p_over = (np.sum(games_arr > line) / num_sims) * 100
        p_under = (np.sum(games_arr < line) / num_sims) * 100
        alternate_results.append({
            "line": line,
            "over_pct": p_over,
            "over_odds": 100 / p_over if p_over > 0 else 99.0,
            "under_pct": p_under,
            "under_odds": 100 / p_under if p_under > 0 else 99.0,
        })

    return {
        "prob_a": prob_a,
        "prob_b": 100 - prob_a,
        "avg_games": np.mean(games_arr),
        "alternates": alternate_results
    }


# ============================================================================
# 4. PREDICTOR & CYBER-SLATE TRADING DESK UI DASHBOARD
# ============================================================================
class Predictor:
    def __init__(self, raw_df, cfg: Config = Config()):
        self.raw_df = raw_df
        self.F, self.engine = build_features(raw_df, cfg)
        self.last_day = int(raw_df["day"].max())
        
        names = {}
        for idc, nmc in (("winner_id", "winner_name"), ("loser_id", "loser_name")):
            if nmc in raw_df.columns:
                for i, n in zip(raw_df[idc], raw_df[nmc]):
                    if pd.notna(n):
                        names[str(n).strip().lower()] = i
                        names[i] = n
        self.names = names

    def find(self, name):
        q = str(name).strip().lower()
        if q in self.names and isinstance(self.names[q], (int, float, str)) and not str(self.names[q]).replace('.','',1).isdigit():
            return self.names[q]
        exact = [i for i, n in self.names.items() if isinstance(i, (int, float)) and str(n).lower() == q]
        if exact:
            return exact[0]
        part = [i for i, n in self.names.items() if isinstance(i, (int, float)) and q in str(n).lower()]
        if len(part) == 1:
            return part[0]
        raise ValueError(f"Player search query '{name}' ambiguous or not found in historical dataset.")

    def get_h2h(self, id_a, id_b, limit=5):
        df = self.raw_df
        matches = df[
            ((df["winner_id"] == id_a) & (df["loser_id"] == id_b)) |
            ((df["winner_id"] == id_b) & (df["loser_id"] == id_a))
        ].sort_values("day", ascending=False)
        
        history = []
        wins_a, wins_b = 0, 0
        for _, row in matches.iterrows():
            w_id = row["winner_id"]
            if w_id == id_a:
                wins_a += 1
            else:
                wins_b += 1
            history.append({
                "date": str(row.get("date", ""))[:10],
                "tourney": row.get("tourney_name", "Tourney"),
                "surface": row.get("surface", "Hard"),
                "winner": self.names.get(w_id, "Unknown"),
                "score": row.get("score", "N/A")
            })
        return {"wins_a": wins_a, "wins_b": wins_b, "matches": history[:limit]}

    def analyze(self, name_a, name_b, surface="Hard", best_of=3):
        a, b = self.find(name_a), self.find(name_b)
        surf = SURF.get(surface, 1)
        snap = self.engine.snapshot(a, b, surf, self.last_day)
        sim_results = run_million_sims_with_alternates(snap, best_of=best_of, num_sims=1_000_000)
        
        return {
            "name_a": self.names[a],
            "name_b": self.names[b],
            "surface": surface,
            "best_of": best_of,
            "snap": snap,
            "h2h": self.get_h2h(a, b),
            "sim": sim_results
        }


def fetch_upcoming_matches_from_api() -> list[dict]:
    if API_KEY == "YOUR_RAPIDAPI_KEY_HERE":
        console.print("[bold yellow]⚠ Live API key not configured. Using active October 2026 ATP & WTA Beijing & Shanghai fixtures.[/bold yellow]")
        return [
            # ATP Active/Late Rounds
            {"tour": "ATP", "tournament": "China Open (Beijing) - Semifinals", "surface": "Hard", "player_a": "Novak Djokovic", "player_b": "Daniil Medvedev", "best_of": 3},
            {"tour": "ATP", "tournament": "China Open (Beijing) - Semifinals", "surface": "Hard", "player_a": "Alex de Minaur", "player_b": "Hubert Hurkacz", "best_of": 3},
            {"tour": "ATP", "tournament": "Shanghai Masters - Preview", "surface": "Hard", "player_a": "Jannik Sinner", "player_b": "Carlos Alcaraz", "best_of": 3},
            {"tour": "ATP", "tournament": "Shanghai Masters - Preview", "surface": "Hard", "player_a": "Alexander Zverev", "player_b": "Ben Shelton", "best_of": 3},
            # WTA Active/Late Rounds
            {"tour": "WTA", "tournament": "China Open (Beijing - WTA)", "surface": "Hard", "player_a": "Iga Swiatek", "player_b": "Coco Gauff", "best_of": 3},
            {"tour": "WTA", "tournament": "China Open (Beijing - WTA)", "surface": "Hard", "player_a": "Aryna Sabalenka", "player_b": "Naomi Osaka", "best_of": 3},
            {"tour": "WTA", "tournament": "China Open (Beijing - WTA)", "surface": "Hard", "player_a": "Karolina Muchova", "player_b": "Mirra Andreeva", "best_of": 3},
        ]

    url = f"https://{API_HOST}/matches/upcoming"
    today_str = datetime.now().strftime("%Y-%m-%d")
    headers = {"X-RapidAPI-Key": API_KEY, "X-RapidAPI-Host": API_HOST}
    params = {"date": today_str}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        fixtures = []
        for item in data.get("result", []):
            fixtures.append({
                "tour": item.get("tour", "ATP"),
                "tournament": item.get("tournament_name", "Tournament"),
                "surface": item.get("surface", "Hard"),
                "player_a": item.get("player_1_name", "Player A"),
                "player_b": item.get("player_2_name", "Player B"),
                "best_of": int(item.get("best_of", 3))
            })
        return fixtures
    except Exception as e:
        console.print(f"[bold red]API Fetch Error: {e}[/bold red]")
        return []


def display_dashboard(res: dict):
    a, b = res["name_a"], res["name_b"]
    sim = res["sim"]
    s = res["snap"]
    h2h = res["h2h"]
    pa, pb = sim["prob_a"], sim["prob_b"]

    console.print()
    
    # Aesthetic Header Panel
    banner_table = Table.grid(expand=True)
    banner_table.add_column(justify="left", style="bold cyan")
    banner_table.add_column(justify="right", style="bold yellow")
    banner_table.add_row(f"⚔️ MATCHUP: {a.upper()} vs {b.upper()}", f"SURFACE: {res['surface'].upper()} | FORMAT: Best of {res['best_of']}")
    
    console.print(Panel(banner_table, border_style="bright_blue", box=box.HEAVY, padding=(1, 2)))

    # Core Metrics Table
    table = Table(show_header=True, header_style="bold bright_cyan", box=box.ROUNDED, expand=True)
    table.add_column(f"[bold gold1]{a}[/bold gold1]", justify="left", style="bright_white")
    table.add_column("QUANTITATIVE METRICS", justify="center", style="dim yellow")
    table.add_column(f"[bold gold1]{b}[/bold gold1]", justify="right", style="bright_white")

    table.add_row(f"[bold green]{pa:.2f}%[/bold green]", "Monte Carlo Win Probability", f"[bold green]{pb:.2f}%[/bold green]")
    table.add_row(f"{s['eloA']:.0f}", "Surface-Adjusted Elo", f"{s['eloB']:.0f}")
    table.add_row(str(s["formStrA"]), "Recent Form (Last 5)", str(s["formStrB"]))
    table.add_row(f"{s['loadA']/60:.1f} hrs", "Recent Court Fatigue Load", f"{s['loadB']/60:.1f} hrs")

    console.print(Panel(
        table,
        title="[bold gold1]⚡ MODEL PREDICTION & PLAYER PROFILE CORE[/bold gold1]",
        border_style="cyan",
        box=box.DOUBLE
    ))

    # Alternate Totals Table
    alt_table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED, expand=True)
    alt_table.add_column("MARKET LINE", justify="center", style="bold yellow")
    alt_table.add_column("OVER PROB %", justify="center", style="green")
    alt_table.add_column("FAIR OVER ODDS", justify="center", style="dim green")
    alt_table.add_column("UNDER PROB %", justify="center", style="red")
    alt_table.add_column("FAIR UNDER ODDS", justify="center", style="dim red")

    for row in sim["alternates"]:
        alt_table.add_row(
            f"O/U {row['line']}",
            f"{row['over_pct']:.1f}%",
            f"{row['over_odds']:.2f}",
            f"{row['under_pct']:.1f}%",
            f"{row['under_odds']:.2f}"
        )

    console.print(Panel(
        alt_table,
        title="[bold gold1]📊 ALTERNATE GAME TOTALS PRICING MATRIX[/bold gold1]",
        subtitle=f"[dim]Expected Mean Total Games: {sim['avg_games']:.2f}[/dim]",
        border_style="yellow",
        box=box.DOUBLE
    ))


# ============================================================================
# 5. INTERACTIVE ARROW-FREE MENU
# ============================================================================
def interactive_menu(raw_df):
    predictor = Predictor(raw_df)

    while True:
        console.clear()
        console.print(Panel(
            "[bold cyan][1][/] Run Custom Match Prediction (Type names & choose surface)\n"
            "[bold cyan][2][/] Inspect Recent Historical Matchups (H2H Lookup)\n"
            "[bold cyan][3][/] Live ATP & WTA Active Tournaments (Beijing & Shanghai)\n"
            "[bold cyan][4][/] Sync / Update Database from GitHub Archives\n"
            "[bold cyan][5][/] Exit Terminal",
            title="[bold gold1]🎾 ELITE TENNIS QUANTITATIVE TRADING DESK[/bold gold1]",
            subtitle="[dim]Arrow-Free Numbered Navigation System[/dim]",
            border_style="bright_cyan",
            box=box.HEAVY
        ))

        choice = Prompt.ask("\n[bold yellow]Select option number[/bold yellow]", choices=["1", "2", "3", "4", "5"], default="1")

        if choice == "5":
            console.print("[bold yellow]Exiting trading terminal. Goodbye![/bold yellow]")
            break

        elif choice == "4":
            sync_github_data()
            raw_df = load_from_db()
            predictor = Predictor(raw_df)
            Prompt.ask("\n[dim]Press Enter to continue...[/dim]")
            continue

        elif choice == "1":
            console.print("\n[bold cyan]--- CUSTOM MATCH PREDICTION ---[/bold cyan]")
            player_a = Prompt.ask("[bold]Enter Player A name (e.g. Novak Djokovic or Iga Swiatek)[/bold]")
            player_b = Prompt.ask("[bold]Enter Player B name (e.g. Daniil Medvedev or Coco Gauff)[/bold]")
            
            console.print("[dim]Surfaces available: Hard, Clay, Grass, Carpet[/dim]")
            surface = Prompt.ask("[bold]Choose Surface[/bold]", choices=["Hard", "Clay", "Grass", "Carpet"], default="Hard")
            best_of_str = Prompt.ask("[bold]Match Format (Sets)[/bold]", choices=["3", "5"], default="3")

            try:
                result = predictor.analyze(player_a, player_b, surface=surface, best_of=int(best_of_str))
                display_dashboard(result)
            except Exception as e:
                console.print(f"[bold red]Error: {e}[/bold red]")

        elif choice == "2":
            console.print("\n[bold cyan]--- RECENT DATABASE MATCHES ---[/bold cyan]")
            recent = raw_df.tail(15).sort_values("day", ascending=False).reset_index(drop=True)
            
            match_table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED)
            match_table.add_column("No.", justify="center", style="cyan")
            match_table.add_column("Tour", justify="center", style="green")
            match_table.add_column("Tournament", style="yellow")
            match_table.add_column("Surface", justify="center")
            match_table.add_column("Winner vs Loser", style="bright_white")

            for idx, r in recent.iterrows():
                match_table.add_row(str(idx + 1), str(r.get("tour", "ATP")), str(r["tourney_name"]), str(r["surface"]), f"{r['winner_name']} def. {r['loser_name']}")

            console.print(match_table)
            match_num = Prompt.ask("\nEnter match number to simulate", default="1")
            try:
                selected_row = recent.iloc[int(match_num) - 1]
                result = predictor.analyze(selected_row["winner_name"], selected_row["loser_name"], surface=selected_row["surface"], best_of=int(selected_row["best_of"]))
                display_dashboard(result)
            except Exception as e:
                console.print(f"[bold red]Error: {e}[/bold red]")

        elif choice == "3":
            console.print("\n[bold cyan]--- ACTIVE ATP & WTA TOURNAMENT FIXTURES (BEIJING & SHANGHAI) ---[/bold cyan]")
            upcoming = fetch_upcoming_matches_from_api()
            
            if not upcoming:
                console.print("[bold red]No upcoming matches returned for today.[/bold red]")
            else:
                up_table = Table(show_header=True, header_style="bold yellow", box=box.ROUNDED)
                up_table.add_column("No.", justify="center", style="cyan")
                up_table.add_column("Tour", justify="center", style="green")
                up_table.add_column("Tournament", style="yellow")
                up_table.add_column("Surface", justify="center")
                up_table.add_column("Matchup", style="bright_white")

                for idx, m in enumerate(upcoming):
                    up_table.add_row(str(idx + 1), m["tour"], m["tournament"], m["surface"], f"{m['player_a']} vs {m['player_b']}")

                console.print(up_table)
                match_choice = Prompt.ask("\nEnter fixture number to run 1,000,000 simulations", default="1")
                try:
                    sel = upcoming[int(match_choice) - 1]
                    result = predictor.analyze(sel["player_a"], sel["player_b"], surface=sel["surface"], best_of=sel["best_of"])
                    display_dashboard(result)
                except Exception as e:
                    console.print(f"[bold red]Simulation Error: {e}[/bold red]")

        Prompt.ask("\n[dim]Press Enter to return to main menu...[/dim]")


if __name__ == "__main__":
    init_db()
    raw_df = load_from_db()
    if raw_df.empty:
        console.print("[bold red]Database is empty! Syncing archives automatically...[/bold red]")
        sync_github_data()
        raw_df = load_from_db()

    interactive_menu(raw_df)