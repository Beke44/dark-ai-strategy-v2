"""
Dark AI Strategy – FastAPI Backend TELJES verzió
=================================================
Minden adat elérhető amit a Streamlit mutat:
H2H, team power, forma, sérülések, odds,
modell szavazatok, extra piacok, bankroll

Ez váltja le az app.py-t!
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, date
import os, json, requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL     = "https://kvduiliabfncikvesmza.supabase.co"
SUPABASE_KEY     = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2ZHVpbGlhYmZuY2lrdmVzbXphIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU3ODEyMjYsImV4cCI6MjEwMTM1NzIyNn0.9tbY11h4nFDe7IAxqcdcNcZXxcs1r1w9096A2ZlKL_0"
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
API_BASE         = "https://v3.football.api-sports.io"

app = FastAPI(title="Dark AI Strategy API", version="4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

def get_sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def football_api(endpoint: str, params: dict = {}) -> dict:
    try:
        r = requests.get(f"{API_BASE}/{endpoint}",
                        headers={"x-apisports-key": FOOTBALL_API_KEY},
                        params=params, timeout=12)
        return r.json()
    except: return {}

# ─── ALAP ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "online", "name": "Dark AI Strategy API", "version": "4.0",
            "endpoints": ["/api/stats/public", "/api/tips/recent",
                         "/api/match/{id}", "/api/match/{id}/h2h",
                         "/api/match/{id}/odds", "/api/live/fixtures",
                         "/api/bankroll", "/api/models/performance"]}

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": str(datetime.now()),
            "football_api": bool(FOOTBALL_API_KEY)}

# ─── STATISZTIKÁK ────────────────────────────────────────────────────────────

@app.get("/api/stats/public")
def public_stats():
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").in_(
            "result_status", ["Win","Lost"]).execute()
        tips   = result.data or []
        if not tips:
            return {"total": 0, "wins": 0, "win_rate": 0, "roi": 0}
        wins   = sum(1 for t in tips if t.get("result_status") == "Win")
        profit = sum(float(t.get("profit") or 0) for t in tips)
        stake  = sum(float(t.get("stake")  or 0) for t in tips)
        dc     = [t for t in tips if "Double chance" in str(t.get("extra_tip",""))]
        dc_w   = sum(1 for t in dc if t.get("result_status") == "Win")

        # Prediction bontás
        pred_stats = {}
        for pred in ["home","draw","away"]:
            bkt = [t for t in tips if t.get("prediction") == pred]
            if bkt:
                bkt_w = sum(1 for t in bkt if t["result_status"] == "Win")
                bkt_p = sum(float(t.get("profit") or 0) for t in bkt)
                bkt_s = sum(float(t.get("stake")  or 0) for t in bkt)
                pred_stats[pred] = {
                    "count": len(bkt),
                    "win_rate": round(bkt_w/len(bkt)*100, 1),
                    "roi": round(bkt_p/max(bkt_s,1)*100, 2)
                }

        return {
            "total":     len(tips),
            "wins":      wins,
            "losses":    len(tips) - wins,
            "win_rate":  round(wins/len(tips)*100, 1),
            "roi":       round(profit/max(stake,1)*100, 2),
            "profit":    round(profit, 2),
            "double_chance_win_rate": round(dc_w/max(len(dc),1)*100, 1),
            "prediction_breakdown": pred_stats,
            "verified":  True,
            "last_updated": str(datetime.now())
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/models/performance")
def models_performance():
    """Modell teljesítmény statisztikák a Lovable dashboard-hoz."""
    try:
        sb     = get_sb()
        result = sb.table("match_analyses").select(
            "models,model_votes,prediction,created_at").limit(500).execute()
        data   = result.data or []

        model_stats = {}
        for row in data:
            votes = row.get("model_votes") or {}
            if isinstance(votes, str):
                try: votes = json.loads(votes)
                except: votes = {}
            for mname, mdata in votes.items():
                if mname not in model_stats:
                    model_stats[mname] = {"count": 0, "weight": 0}
                model_stats[mname]["count"] += 1
                if isinstance(mdata, dict):
                    model_stats[mname]["weight"] = mdata.get("weight", 0)

        return {"models": model_stats, "total_analyses": len(data)}
    except Exception as e:
        return {"error": str(e)}

# ─── TIPPEK ──────────────────────────────────────────────────────────────────

@app.get("/api/tips/recent")
def recent_tips(limit: int = 20, status: str = "all"):
    try:
        sb = get_sb()
        q  = sb.table("tips").select("*").order("created_at", desc=True)
        if status != "all":
            q = q.eq("result_status", status)
        result = q.limit(limit).execute()
        return {"tips": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/today")
def todays_tips():
    """Mai tippek teljes elemzéssel."""
    try:
        sb     = get_sb()
        today  = date.today().isoformat()
        result = sb.table("match_analyses").select("*").eq(
            "date", today).order("confidence", desc=True).execute()
        return {
            "date":  today,
            "count": len(result.data or []),
            "tips":  result.data or []
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/history")
def tip_history(days: int = 30):
    """Historikus tippek és teljesítmény."""
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").order(
            "created_at", desc=True).limit(days * 15).execute()
        tips   = result.data or []

        # Napi bontás
        daily  = {}
        for t in tips:
            d = str(t.get("created_at",""))[:10]
            if d not in daily:
                daily[d] = {"wins":0,"losses":0,"profit":0,"count":0}
            daily[d]["count"] += 1
            if t.get("result_status") == "Win":   daily[d]["wins"]   += 1
            if t.get("result_status") == "Lost":  daily[d]["losses"] += 1
            daily[d]["profit"] += float(t.get("profit") or 0)

        return {
            "tips":  tips,
            "daily": daily,
            "total": len(tips)
        }
    except Exception as e:
        return {"error": str(e)}

# ─── MECCS RÉSZLETEK ─────────────────────────────────────────────────────────

@app.get("/api/match/{fixture_id}")
def match_detail(fixture_id: int):
    """
    Teljes meccs elemzés – MINDEN adat:
    team power, forma, modellek, értékbecslés, extra piacok
    """
    try:
        sb     = get_sb()
        result = sb.table("match_analyses").select("*").eq(
            "fixture_id", fixture_id).order(
            "created_at", desc=True).limit(1).execute()

        if result.data:
            analysis = result.data[0]
            # JSON mezők visszaalakítása
            for field in ["raw_probs","calibrated_probs","model_votes",
                         "models","extra_predictions"]:
                if analysis.get(field) and isinstance(analysis[field], str):
                    try: analysis[field] = json.loads(analysis[field])
                    except: pass
            return {"fixture_id": fixture_id, "analysis": analysis, "source": "supabase"}
        else:
            # Valós idejű lekérés az API-ból ha nincs DB-ben
            return get_live_match_detail(fixture_id)

    except Exception as e:
        return {"error": str(e)}

def get_live_match_detail(fixture_id: int) -> dict:
    """Valós idejű meccs adatok ha nincs az adatbázisban."""
    fix_data  = football_api("fixtures", {"id": fixture_id})
    if not fix_data.get("response"):
        return {"error": "Meccs nem található"}

    fix       = fix_data["response"][0]
    teams     = fix.get("teams", {})
    league    = fix.get("league", {})
    goals     = fix.get("goals", {})
    home_id   = teams.get("home",{}).get("id",0)
    away_id   = teams.get("away",{}).get("id",0)

    # Odds lekérés
    odds_data = football_api("odds", {"fixture": fixture_id})
    odds_list = []
    for bk in odds_data.get("response",[{}])[0].get("bookmakers",[])[:5]:
        for bet in bk.get("bets",[]):
            if bet.get("name") == "Match Winner":
                vals = {v["value"]: float(v["odd"]) for v in bet.get("values",[])}
                odds_list.append({
                    "bookmaker": bk.get("name"),
                    "home":      vals.get("Home",0),
                    "draw":      vals.get("Draw",0),
                    "away":      vals.get("Away",0)
                })

    # H2H
    h2h_data = football_api("fixtures/headtohead", {
        "h2h": f"{home_id}-{away_id}", "last": 10
    })
    h2h = []
    for hm in h2h_data.get("response",[])[:10]:
        g = hm.get("goals",{})
        t = hm.get("teams",{})
        h2h.append({
            "date":       hm.get("fixture",{}).get("date","")[:10],
            "home":       t.get("home",{}).get("name",""),
            "away":       t.get("away",{}).get("name",""),
            "home_goals": g.get("home",0),
            "away_goals": g.get("away",0),
            "league":     hm.get("league",{}).get("name","")
        })

    return {
        "fixture_id": fixture_id,
        "fixture":    fix,
        "odds":       odds_list,
        "h2h":        h2h,
        "source":     "live_api"
    }

@app.get("/api/match/{fixture_id}/h2h")
def match_h2h(fixture_id: int):
    """H2H előzmények és statisztikák."""
    fix_data = football_api("fixtures", {"id": fixture_id})
    if not fix_data.get("response"):
        return {"error": "Meccs nem található"}

    fix     = fix_data["response"][0]
    home_id = fix.get("teams",{}).get("home",{}).get("id",0)
    away_id = fix.get("teams",{}).get("away",{}).get("id",0)
    home_nm = fix.get("teams",{}).get("home",{}).get("name","")
    away_nm = fix.get("teams",{}).get("away",{}).get("name","")

    h2h_data = football_api("fixtures/headtohead",
                            {"h2h": f"{home_id}-{away_id}", "last": 20})
    matches  = []
    home_w = draw_w = away_w = 0
    total_home_goals = total_away_goals = 0

    for hm in h2h_data.get("response",[]):
        g  = hm.get("goals",{})
        t  = hm.get("teams",{})
        gh = g.get("home",0) or 0
        ga = g.get("away",0) or 0
        hid = t.get("home",{}).get("id",0)

        if hid == home_id:
            total_home_goals += gh
            total_away_goals += ga
            if gh > ga:   home_w += 1
            elif gh < ga: away_w += 1
            else:         draw_w += 1
        else:
            total_home_goals += ga
            total_away_goals += gh
            if ga > gh:   home_w += 1
            elif ga < gh: away_w += 1
            else:         draw_w += 1

        matches.append({
            "date":        hm.get("fixture",{}).get("date","")[:10],
            "home_team":   t.get("home",{}).get("name",""),
            "away_team":   t.get("away",{}).get("name",""),
            "home_goals":  gh,
            "away_goals":  ga,
            "league":      hm.get("league",{}).get("name",""),
            "season":      str(hm.get("league",{}).get("season",""))
        })

    total = home_w + draw_w + away_w
    return {
        "fixture_id":  fixture_id,
        "home_team":   home_nm,
        "away_team":   away_nm,
        "summary": {
            "total_matches":    total,
            "home_wins":        home_w,
            "draws":            draw_w,
            "away_wins":        away_w,
            "home_win_pct":     round(home_w/max(total,1)*100, 1),
            "draw_pct":         round(draw_w/max(total,1)*100, 1),
            "away_win_pct":     round(away_w/max(total,1)*100, 1),
            "avg_home_goals":   round(total_home_goals/max(total,1), 2),
            "avg_away_goals":   round(total_away_goals/max(total,1), 2),
            "avg_total_goals":  round((total_home_goals+total_away_goals)/max(total,1), 2),
        },
        "matches": matches
    }

@app.get("/api/match/{fixture_id}/odds")
def match_odds(fixture_id: int):
    """Összes fogadóiroda odds összehasonlítása."""
    odds_data = football_api("odds", {"fixture": fixture_id})
    bookmakers = []

    for bk in odds_data.get("response",[{}])[0].get("bookmakers",[]):
        bk_data = {"name": bk.get("name"), "bets": {}}
        for bet in bk.get("bets",[]):
            bet_name = bet.get("name","")
            vals     = {v["value"]: float(v["odd"]) for v in bet.get("values",[])}
            bk_data["bets"][bet_name] = vals
        bookmakers.append(bk_data)

    # Legjobb odds meghatározása
    best_home = best_draw = best_away = 0.0
    best_bk_h = best_bk_d = best_bk_a = ""
    for bk in bookmakers:
        mw = bk["bets"].get("Match Winner", {})
        if mw.get("Home",0) > best_home:
            best_home = mw["Home"]; best_bk_h = bk["name"]
        if mw.get("Draw",0) > best_draw:
            best_draw = mw["Draw"]; best_bk_d = bk["name"]
        if mw.get("Away",0) > best_away:
            best_away = mw["Away"]; best_bk_a = bk["name"]

    return {
        "fixture_id":  fixture_id,
        "bookmakers":  bookmakers,
        "best_odds": {
            "home": {"odd": best_home, "bookmaker": best_bk_h},
            "draw": {"odd": best_draw, "bookmaker": best_bk_d},
            "away": {"odd": best_away, "bookmaker": best_bk_a},
        },
        "arbitrage_check": {
            "margin": round(1/max(best_home,0.01) + 1/max(best_draw,0.01) + 1/max(best_away,0.01), 4),
            "is_arbitrage": (1/max(best_home,0.01) + 1/max(best_draw,0.01) + 1/max(best_away,0.01)) < 1.0
        }
    }

@app.get("/api/match/{fixture_id}/stats")
def match_stats(fixture_id: int):
    """Meccs statisztikák ha már elkezdődött."""
    stats_data = football_api("fixtures/statistics", {"fixture": fixture_id})
    events_data = football_api("fixtures/events", {"fixture": fixture_id})

    team_stats = {}
    for ts in stats_data.get("response",[]):
        team_name = ts.get("team",{}).get("name","")
        stats     = {s["type"]: s["value"] for s in ts.get("statistics",[])}
        team_stats[team_name] = stats

    events = []
    for ev in events_data.get("response",[]):
        events.append({
            "minute": ev.get("time",{}).get("elapsed",0),
            "team":   ev.get("team",{}).get("name",""),
            "player": ev.get("player",{}).get("name",""),
            "type":   ev.get("type",""),
            "detail": ev.get("detail","")
        })

    return {
        "fixture_id": fixture_id,
        "team_stats": team_stats,
        "events":     events
    }

@app.get("/api/team/{team_id}/form")
def team_form(team_id: int, last: int = 10):
    """Csapat forma az utolsó N meccsből."""
    data   = football_api("fixtures", {"team": team_id, "last": last, "status": "FT"})
    games  = []
    wins   = draws = losses = goals_f = goals_a = 0

    for fix in data.get("response",[]):
        t  = fix.get("teams",{})
        g  = fix.get("goals",{})
        gh = g.get("home",0) or 0
        ga = g.get("away",0) or 0
        is_home = t.get("home",{}).get("id") == team_id

        if is_home:
            gf, gcm = gh, ga
            opp = t.get("away",{}).get("name","")
        else:
            gf, gcm = ga, gh
            opp = t.get("home",{}).get("name","")

        goals_f += gf; goals_a += gcm
        if gf > gcm:   wins   += 1; result = "W"
        elif gf < gcm: losses += 1; result = "L"
        else:          draws  += 1; result = "D"

        games.append({
            "date":     fix.get("fixture",{}).get("date","")[:10],
            "opponent": opp,
            "is_home":  is_home,
            "gf": gf, "ga": gcm,
            "result": result,
            "league": fix.get("league",{}).get("name","")
        })

    total = len(games)
    return {
        "team_id": team_id,
        "games":   games,
        "summary": {
            "played":       total,
            "wins":         wins,
            "draws":        draws,
            "losses":       losses,
            "goals_for":    goals_f,
            "goals_against":goals_a,
            "points":       wins*3 + draws,
            "form_string":  "".join(g["result"] for g in games),
            "form_value":   round((wins*3+draws)/(total*3)*100, 1) if total else 0,
        }
    }

@app.get("/api/team/{team_id}/injuries")
def team_injuries(team_id: int):
    """Csapat sérülések és eltiltások."""
    data  = football_api("injuries", {"team": team_id})
    inj   = []
    for p in data.get("response",[]):
        inj.append({
            "player":   p.get("player",{}).get("name",""),
            "type":     p.get("player",{}).get("type",""),
            "reason":   p.get("player",{}).get("reason",""),
            "fixture":  p.get("fixture",{}).get("date","")
        })
    return {"team_id": team_id, "injuries": inj, "count": len(inj)}

# ─── BANKROLL ────────────────────────────────────────────────────────────────

@app.get("/api/bankroll")
def bankroll_stats():
    """Bankroll elemzés és javaslatok."""
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").order(
            "created_at", desc=True).limit(200).execute()
        tips   = result.data or []

        closed  = [t for t in tips if t.get("result_status") in ["Win","Lost"]]
        profit  = sum(float(t.get("profit") or 0) for t in closed)
        stake   = sum(float(t.get("stake")  or 0) for t in closed)
        wins    = sum(1 for t in closed if t["result_status"] == "Win")

        # Utolsó 7 nap
        from datetime import timedelta
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        recent   = [t for t in closed if str(t.get("created_at","")) >= week_ago]
        recent_w = sum(1 for t in recent if t["result_status"]=="Win")
        recent_l = len(recent) - recent_w

        # Sorozat vizsgálat
        last_5  = [t["result_status"] for t in closed[:5]]
        streak  = 0
        if last_5:
            streak_type = last_5[0]
            for r in last_5:
                if r == streak_type: streak += 1
                else: break

        # Ajánlott tét (Kelly kritérium)
        win_rate = wins/max(len(closed),1)
        avg_odds = sum(float(t.get("odds") or 2) for t in closed)/max(len(closed),1)
        kelly    = max(0, (win_rate * avg_odds - 1) / (avg_odds - 1)) if avg_odds > 1 else 0
        kelly_25 = kelly * 0.25  # Negyede (biztonságos)

        warnings = []
        if recent_l >= 3:
            warnings.append(f"⚠️ {recent_l} vesztes az utolsó 7 napban")
        if streak >= 3 and last_5[0] == "Lost":
            warnings.append(f"⚠️ {streak} egymást követő vesztes tipp!")

        return {
            "total_closed":  len(closed),
            "wins":          wins,
            "losses":        len(closed)-wins,
            "win_rate":      round(win_rate*100, 1),
            "roi":           round(profit/max(stake,1)*100, 2),
            "total_profit":  round(profit, 2),
            "weekly": {
                "count":  len(recent),
                "wins":   recent_w,
                "losses": recent_l
            },
            "kelly_criterion": round(kelly_25*100, 2),
            "recommended_stake_pct": round(min(kelly_25*100, 5), 1),
            "current_streak": {"type": last_5[0] if last_5 else None, "count": streak},
            "warnings": warnings,
        }
    except Exception as e:
        return {"error": str(e)}

# ─── ARBITRÁZS SCANNER ───────────────────────────────────────────────────────

@app.get("/api/arbitrage")
def scan_arbitrage(date_str: str = ""):
    """Arbitrázs lehetőségek keresése az aznapi meccseken."""
    target = date_str or date.today().strftime("%Y-%m-%d")
    data   = football_api("fixtures", {"date": target, "status": "NS"})
    arbs   = []

    for fix in data.get("response", [])[:20]:
        fid      = fix.get("fixture",{}).get("id")
        home_nm  = fix.get("teams",{}).get("home",{}).get("name","")
        away_nm  = fix.get("teams",{}).get("away",{}).get("name","")
        league   = fix.get("league",{}).get("name","")

        odds_data = football_api("odds", {"fixture": fid})
        best_h = best_d = best_a = 0.0
        bk_h = bk_d = bk_a = ""

        for bk in odds_data.get("response",[{}])[0].get("bookmakers",[]):
            for bet in bk.get("bets",[]):
                if bet.get("name") == "Match Winner":
                    for v in bet.get("values",[]):
                        odd = float(v.get("odd",0))
                        if v["value"] == "Home" and odd > best_h:
                            best_h = odd; bk_h = bk["name"]
                        if v["value"] == "Draw" and odd > best_d:
                            best_d = odd; bk_d = bk["name"]
                        if v["value"] == "Away" and odd > best_a:
                            best_a = odd; bk_a = bk["name"]

        if best_h > 1 and best_d > 1 and best_a > 1:
            margin = 1/best_h + 1/best_d + 1/best_a
            if margin < 1.0:
                profit_pct = round((1 - margin) * 100, 2)
                arbs.append({
                    "fixture_id": fid,
                    "home_team":  home_nm,
                    "away_team":  away_nm,
                    "league":     league,
                    "margin":     round(margin, 4),
                    "profit_pct": profit_pct,
                    "best_odds": {
                        "home": {"odd": best_h, "bookmaker": bk_h},
                        "draw": {"odd": best_d, "bookmaker": bk_d},
                        "away": {"odd": best_a, "bookmaker": bk_a},
                    }
                })

    arbs.sort(key=lambda x: -x["profit_pct"])
    return {"date": target, "arbitrage_count": len(arbs), "opportunities": arbs}

# ─── ÉLŐ MECCSEK ─────────────────────────────────────────────────────────────

@app.get("/api/live/fixtures")
def live_fixtures():
    data     = football_api("fixtures", {"live": "all"})
    fixtures = []
    for fix in data.get("response",[]):
        f = fix.get("fixture",{})
        t = fix.get("teams",{})
        g = fix.get("goals",{})
        l = fix.get("league",{})
        fixtures.append({
            "id":         f.get("id"),
            "minute":     f.get("status",{}).get("elapsed",0),
            "status":     f.get("status",{}).get("short",""),
            "home_team":  t.get("home",{}).get("name",""),
            "away_team":  t.get("away",{}).get("name",""),
            "home_goals": g.get("home",0),
            "away_goals": g.get("away",0),
            "league":     l.get("name",""),
            "country":    l.get("country",""),
        })
    return {"count": len(fixtures), "fixtures": fixtures,
            "timestamp": str(datetime.now())}

@app.get("/api/live/analyze/{fixture_id}")
def live_analyze(fixture_id: int, prediction: str = "home",
                odds: float = 2.0, stake: float = 1000,
                risk_profile: str = "normal"):
    try:
        from live_betting_ai import get_fixture_live_data, analyze_live_tip, set_api_key
        set_api_key(FOOTBALL_API_KEY)
        live_data = get_fixture_live_data(fixture_id)
        tip       = {"prediction": prediction, "odds": odds,
                    "stake": stake, "fixture": live_data.get("fixture",{})}
        analysis  = analyze_live_tip(tip, live_data, risk_profile)
        return {"fixture_id": fixture_id, "analysis": analysis}
    except Exception as e:
        return {"error": str(e)}
