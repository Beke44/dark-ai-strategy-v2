"""
Dark AI Strategy – Railway Worker
===================================
Ez fut Railway-en 24/7:
- FastAPI backend (API végpontok)
- Live Monitor háttérszálon (élő meccsek figyelése)
- Automatikus újraindítás hiba esetén

Railway-en ez az app.py helyett fut ha Procfile-t használunk.
VAGY: ezt töltsd fel app.py névvel a GitHub-ra.

--- MÓDOSÍTVA: modell-nevek anonimizálva a nyilvános API válaszokban ---
"""

import os, sys, json, logging, requests, time, threading
from datetime import datetime, date, timedelta
from pathlib import Path
from dotenv import load_dotenv

# FastAPI
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
SUPABASE_URL     = "https://kvduiliabfncikvesmza.supabase.co"
SUPABASE_KEY     = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2ZHVpbGlhYmZuY2lrdmVzbXphIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU3ODEyMjYsImV4cCI6MjEwMTM1NzIyNn0.9tbY11h4nFDe7IAxqcdcNcZXxcs1r1w9096A2ZlKL_0"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID", "")
API_BASE         = "https://v3.football.api-sports.io"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─── FastAPI ──────────────────────────────────────────────────────────────────
app = FastAPI(title="Dark AI Strategy API", version="4.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

def get_sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def football_api(endpoint: str, params: dict = {}) -> dict:
    try:
        r = requests.get(
            f"{API_BASE}/{endpoint}",
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            params=params, timeout=12
        )
        return r.json()
    except: return {}

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHANNEL, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except: pass

# ─── ÚJ: MODELL-NÉV ANONIMIZÁLÁS ──────────────────────────────────────────────
# A belső modellneveket SOHA nem küldjük ki nyilvánosan (versenytárs-védelem).
# Ha új modellt adsz a rendszerhez, itt vedd fel a saját belső kulcsnevét ->
# megjelenítendő "álnevét".
MODEL_DISPLAY_NAMES = {
    "monte_carlo":     "Szimulációs Modell",
    "elo":             "Rangsor Modell",
    "neural_network":  "Mélytanulási Modell",
    "xgboost":         "Statisztikai Modell",
    "form":            "Forma Modell",
    "h2h":             "Egymás Elleni Modell",
    "goal_stats":      "Gólstatisztikai Modell",
    "trust":           "Megbízhatósági Modell",
    "meta":            "Meta Modell",
    "ensemble":        "Összesített Modell",
}

def _coerce_to_dict(raw):
    """
    Supabase-ből néha JSON-string formában jön vissza egy mező
    (pl. ha mentéskor json.dumps()-szal lett elmentve egy text/jsonb oszlopba).
    Ez a segédfüggvény biztonságosan dict-té alakítja, akármilyen formában jön.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def anonymize_model_dict(raw) -> dict:
    """
    Lecseréli a belső modellkulcsokat (pl. 'xgboost', 'elo') semleges,
    kifelé mutatható elnevezésekre (pl. 'Modell 1', 'Modell 2'),
    vagy a MODEL_DISPLAY_NAMES-ben megadott névre, ha van ilyen.
    Elfogadja dict vagy JSON-string formában is a bemenetet.
    """
    raw = _coerce_to_dict(raw)
    result = {}
    fallback_counter = 1
    for k, v in raw.items():
        display_name = MODEL_DISPLAY_NAMES.get(str(k).lower())
        if display_name is None:
            display_name = f"Modell {fallback_counter}"
            fallback_counter += 1
        result[display_name] = v
    return result


def sanitize_analysis_for_public(analysis: dict) -> dict:
    """
    Egy teljes elemzés-dict-en végigmegy, és minden olyan mezőt
    anonimizál, ami a belső modellarchitektúrára utalhat.
    Nem módosítja az eredeti dict-et, másolattal dolgozik.
    Kezeli azt az esetet is, ha model_votes/models JSON-stringként jött Supabase-ből.
    """
    if not isinstance(analysis, dict):
        return analysis
    safe = dict(analysis)

    if "model_votes" in safe:
        safe["model_votes"] = anonymize_model_dict(safe["model_votes"])

    if "models" in safe:
        safe["models"] = anonymize_model_dict(safe["models"])

    if "raw_probs" in safe:
        safe["raw_probs"] = _coerce_to_dict(safe["raw_probs"])

    # Sosem küldjük ki nyers formában ezeket a mezőket, ha esetleg bekerülnének:
    for forbidden_key in ("model_weights", "model_source_code", "internal_notes"):
        safe.pop(forbidden_key, None)

    return safe

# ─── API VÉGPONTOK ────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status":  "online",
        "name":    "Dark AI Strategy API",
        "version": "4.1",
        "live_monitor": _monitor_status.get("running", False),
        "api_key_set":  bool(FOOTBALL_API_KEY),
    }

@app.get("/api/health")
def health():
    return {
        "status":       "ok",
        "timestamp":    str(datetime.now()),
        "football_api": bool(FOOTBALL_API_KEY),
        "telegram":     bool(TELEGRAM_TOKEN),
        "live_monitor": _monitor_status,
    }

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
        pred_stats = {}
        for pred in ["home","draw","away"]:
            bkt   = [t for t in tips if t.get("prediction") == pred]
            if bkt:
                bkt_w = sum(1 for t in bkt if t["result_status"] == "Win")
                bkt_p = sum(float(t.get("profit") or 0) for t in bkt)
                bkt_s = sum(float(t.get("stake")  or 0) for t in bkt)
                pred_stats[pred] = {
                    "count":    len(bkt),
                    "win_rate": round(bkt_w/len(bkt)*100, 1),
                    "roi":      round(bkt_p/max(bkt_s,1)*100, 2)
                }
        return {
            "total":     len(tips),
            "wins":      wins,
            "losses":    len(tips) - wins,
            "win_rate":  round(wins/len(tips)*100, 1),
            "roi":       round(profit/max(stake,1)*100, 2),
            "profit":    round(profit, 2),
            "double_chance_win_rate": round(dc_w/max(len(dc),1)*100, 1),
            "prediction_breakdown":   pred_stats,
            "verified":  True,
            "last_updated": str(datetime.now())
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/recent")
def recent_tips(limit: int = 20, status: str = "all"):
    try:
        sb = get_sb()
        q  = sb.table("tips").select("*").order("created_at", desc=True)
        if status == "pending":
            q = q.not_.in_("result_status", ["Win","Lost","Void"])
        elif status == "closed":
            q = q.in_("result_status", ["Win","Lost"])
        result = q.limit(limit).execute()
        tips   = result.data or []
        # Duplikáció szűrés
        seen, unique = set(), []
        for t in tips:
            k = f"{t.get('home_team','')}_{t.get('away_team','')}_{str(t.get('created_at',''))[:10]}"
            if k not in seen:
                seen.add(k); unique.append(t)
        return {"tips": unique, "count": len(unique)}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/by-status")
def tips_by_status(status: str = "pending", limit: int = 30):
    try:
        sb = get_sb()
        q  = sb.table("tips").select("*").order("created_at", desc=True)
        if status == "pending":
            q = q.not_.in_("result_status", ["Win","Lost","Void"])
        elif status == "closed":
            q = q.in_("result_status", ["Win","Lost"])
        result = q.limit(limit).execute()
        tips   = result.data or []
        seen, unique = set(), []
        for t in tips:
            k = f"{t.get('home_team','')}_{t.get('away_team','')}_{str(t.get('created_at',''))[:10]}"
            if k not in seen:
                seen.add(k); unique.append(t)
        return {"status": status, "count": len(unique), "tips": unique}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/today")
def tips_today():
    try:
        sb     = get_sb()
        today  = date.today().isoformat()
        result = sb.table("tips").select("*").order(
            "created_at", desc=True).limit(200).execute()
        all_tips = result.data or []
        pending  = [t for t in all_tips
                   if str(t.get("created_at",""))[:10] == today
                   and t.get("result_status") not in ["Win","Lost"]]
        closed   = [t for t in all_tips
                   if t.get("result_status") in ["Win","Lost"]][:20]
        return {
            "date":          today,
            "pending":       pending,
            "closed":        closed,
            "pending_count": len(pending),
            "closed_count":  len(closed),
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/history")
def tip_history(days: int = 30):
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").order(
            "created_at", desc=True).limit(days * 15).execute()
        tips   = result.data or []
        daily  = {}
        for t in tips:
            d = str(t.get("created_at",""))[:10]
            if d not in daily:
                daily[d] = {"wins":0,"losses":0,"profit":0,"count":0}
            daily[d]["count"] += 1
            if t.get("result_status") == "Win":
                daily[d]["wins"]   += 1
                daily[d]["profit"] += float(t.get("profit") or 0)
            if t.get("result_status") == "Lost":
                daily[d]["losses"] += 1
                daily[d]["profit"] += float(t.get("profit") or 0)
        return {"tips": tips, "daily": daily, "total": len(tips)}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/match/{fixture_id}")
def match_detail(fixture_id: int):
    try:
        sb     = get_sb()
        result = sb.table("match_analyses").select("*").eq(
            "fixture_id", fixture_id).order(
            "created_at", desc=True).limit(1).execute()
        if result.data:
            analysis = result.data[0]
            # --- MÓDOSÍTVA: modellnevek anonimizálva, mielőtt kimegy ---
            analysis = sanitize_analysis_for_public(analysis)
            return {"fixture_id": fixture_id, "analysis": analysis, "source": "supabase"}
        # Live API fallback
        fix_data = football_api("fixtures", {"id": fixture_id})
        if not fix_data.get("response"):
            return {"error": "Not found"}
        fix   = fix_data["response"][0]
        teams = fix.get("teams", {})
        venue = fix.get("fixture", {}).get("venue", {})
        odds_data = football_api("odds", {"fixture": fixture_id})
        odds_list = []
        for bk in odds_data.get("response",[{}])[0].get("bookmakers",[])[:5]:
            for bet in bk.get("bets",[]):
                if bet.get("name") == "Match Winner":
                    vals = {v["value"]: float(v["odd"]) for v in bet.get("values",[])}
                    odds_list.append({
                        "bookmaker": bk.get("name"),
                        "home": vals.get("Home",0),
                        "draw": vals.get("Draw",0),
                        "away": vals.get("Away",0)
                    })
        return {
            "fixture_id": fixture_id,
            "fixture":    fix,
            "venue":      venue,
            "home_logo":  teams.get("home",{}).get("logo",""),
            "away_logo":  teams.get("away",{}).get("logo",""),
            "odds":       odds_list,
            "source":     "live_api"
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/match/{fixture_id}/odds")
def match_odds(fixture_id: int):
    data = football_api("odds", {"fixture": fixture_id})
    bookmakers, best_h, best_d, best_a = [], 0.0, 0.0, 0.0
    bk_h = bk_d = bk_a = ""
    for bk in data.get("response",[{}])[0].get("bookmakers",[]):
        bk_data = {"name": bk.get("name"), "bets": {}}
        for bet in bk.get("bets",[]):
            vals = {v["value"]: float(v["odd"]) for v in bet.get("values",[])}
            bk_data["bets"][bet.get("name","")] = vals
            if bet.get("name") == "Match Winner":
                if vals.get("Home",0) > best_h: best_h=vals["Home"]; bk_h=bk["name"]
                if vals.get("Draw",0) > best_d: best_d=vals["Draw"]; bk_d=bk["name"]
                if vals.get("Away",0) > best_a: best_a=vals["Away"]; bk_a=bk["name"]
        bookmakers.append(bk_data)
    return {
        "fixture_id": fixture_id,
        "bookmakers": bookmakers,
        "best_odds": {
            "home": {"odd": best_h, "bookmaker": bk_h},
            "draw": {"odd": best_d, "bookmaker": bk_d},
            "away": {"odd": best_a, "bookmaker": bk_a},
        },
        "arbitrage": {
            "margin": round(1/max(best_h,.01)+1/max(best_d,.01)+1/max(best_a,.01),4),
            "is_arb": (1/max(best_h,.01)+1/max(best_d,.01)+1/max(best_a,.01)) < 1.0
        }
    }

@app.get("/api/match/{fixture_id}/h2h")
def match_h2h(fixture_id: int):
    fix_data = football_api("fixtures", {"id": fixture_id})
    if not fix_data.get("response"):
        return {"error": "Not found"}
    fix     = fix_data["response"][0]
    home_id = fix.get("teams",{}).get("home",{}).get("id",0)
    away_id = fix.get("teams",{}).get("away",{}).get("id",0)
    home_nm = fix.get("teams",{}).get("home",{}).get("name","")
    away_nm = fix.get("teams",{}).get("away",{}).get("name","")
    h2h     = football_api("fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": 20})
    matches, hw, dw, aw, tg = [], 0, 0, 0, 0
    for m in h2h.get("response",[]):
        g  = m.get("goals",{})
        t  = m.get("teams",{})
        gh = g.get("home",0) or 0
        ga = g.get("away",0) or 0
        hid= t.get("home",{}).get("id",0)
        tg+= gh+ga
        if hid==home_id:
            if gh>ga: hw+=1
            elif gh<ga: aw+=1
            else: dw+=1
        else:
            if ga>gh: hw+=1
            elif ga<gh: aw+=1
            else: dw+=1
        matches.append({
            "date": m.get("fixture",{}).get("date","")[:10],
            "home": t.get("home",{}).get("name",""),
            "away": t.get("away",{}).get("name",""),
            "home_goals": gh, "away_goals": ga,
            "league": m.get("league",{}).get("name","")
        })
    total = hw+dw+aw
    return {
        "fixture_id": fixture_id,
        "home_team":  home_nm,
        "away_team":  away_nm,
        "summary": {
            "total": total, "home_wins": hw, "draws": dw, "away_wins": aw,
            "home_win_pct": round(hw/max(total,1)*100,1),
            "draw_pct":     round(dw/max(total,1)*100,1),
            "away_win_pct": round(aw/max(total,1)*100,1),
            "avg_goals":    round(tg/max(total,1),2),
        },
        "matches": matches
    }

# ─── ÚJ: STATISZTIKÁK VÉGPONT (korábban hiányzott!) ───────────────────────────
@app.get("/api/match/{fixture_id}/stats")
def match_stats(fixture_id: int):
    """
    xG, forma, csapat-erő és egyéb bővített statisztikák egy meccshez.
    Ha a meccs élő, megpróbálja lekérni az élő statisztikákat is.
    """
    try:
        fix_data = football_api("fixtures", {"id": fixture_id})
        if not fix_data.get("response"):
            return {"error": "Not found"}
        fix    = fix_data["response"][0]
        status = fix.get("fixture", {}).get("status", {}).get("short", "")

        live_stats = None
        if status in {"1H", "2H", "HT", "ET", "P"}:
            stats_data = football_api("fixtures/statistics", {"fixture": fixture_id})
            live_stats = stats_data.get("response", [])

        # Kiegészítő adatok az elemzésből (Supabase), ha van
        analysis_extra = {}
        _debug_error = None
        try:
            sb = get_sb()
            res = sb.table("match_analyses").select("*").eq(
                "fixture_id", fixture_id).order("created_at", desc=True).limit(1).execute()
            if res.data:
                a = sanitize_analysis_for_public(res.data[0])
                analysis_extra = {
                    "h_power":    a.get("h_power"),
                    "a_power":    a.get("a_power"),
                    "h_form_str": a.get("h_form_str"),
                    "a_form_str": a.get("a_form_str"),
                    "h_xg":       a.get("h_xg"),
                    "a_xg":       a.get("a_xg"),
                    "h_injuries": a.get("h_injuries"),
                    "a_injuries": a.get("a_injuries"),
                }
            else:
                _debug_error = "res.data volt üres (nem talált sort ezzel a fixture_id-vel)"
        except Exception as _dbg_e:
            _debug_error = f"{type(_dbg_e).__name__}: {_dbg_e}"

        return {
            "fixture_id":  fixture_id,
            "status":      status,
            "live_stats":  live_stats,
            "pre_match":   analysis_extra,
            "debug_error": _debug_error,
            "note": None if (live_stats or analysis_extra) else "Élő statisztikák a meccs alatt elérhetők",
        }
    except Exception as e:
        return {"error": str(e)}

# ─── ÚJ: KEZDŐCSAPAT (LINEUP) VÉGPONT (korábban hiányzott!) ───────────────────
@app.get("/api/match/{fixture_id}/lineup")
def match_lineup(fixture_id: int):
    try:
        data = football_api("fixtures/lineups", {"fixture": fixture_id})
        response = data.get("response", [])
        if not response:
            return {
                "fixture_id": fixture_id,
                "available": False,
                "note": "Kezdőcsapat kb. 1 órával a meccs előtt jelenik meg",
            }
        teams = []
        for team_lineup in response:
            teams.append({
                "team":       team_lineup.get("team", {}).get("name", ""),
                "formation":  team_lineup.get("formation", ""),
                "coach":      team_lineup.get("coach", {}).get("name", ""),
                "startXI": [
                    {
                        "name":  p.get("player", {}).get("name", ""),
                        "number": p.get("player", {}).get("number"),
                        "pos":   p.get("player", {}).get("pos", ""),
                    }
                    for p in team_lineup.get("startXI", [])
                ],
                "substitutes": [
                    {
                        "name":  p.get("player", {}).get("name", ""),
                        "number": p.get("player", {}).get("number"),
                        "pos":   p.get("player", {}).get("pos", ""),
                    }
                    for p in team_lineup.get("substitutes", [])
                ],
            })
        return {"fixture_id": fixture_id, "available": True, "teams": teams}
    except Exception as e:
        return {"error": str(e)}

# ─── ÚJ: LIGA TABELLA VÉGPONT (korábban hiányzott!) ───────────────────────────
@app.get("/api/match/{fixture_id}/standings")
def match_standings(fixture_id: int):
    try:
        fix_data = football_api("fixtures", {"id": fixture_id})
        if not fix_data.get("response"):
            return {"error": "Not found"}
        fix        = fix_data["response"][0]
        league_id  = fix.get("league", {}).get("id")
        season     = fix.get("league", {}).get("season")
        if not league_id or not season:
            return {"error": "Missing league/season"}

        data = football_api("standings", {"league": league_id, "season": season})
        response = data.get("response", [])
        if not response:
            return {"fixture_id": fixture_id, "available": False, "standings": []}

        table = response[0].get("league", {}).get("standings", [[]])[0]
        standings = [
            {
                "rank":   row.get("rank"),
                "team":   row.get("team", {}).get("name", ""),
                "played": row.get("all", {}).get("played", 0),
                "win":    row.get("all", {}).get("win", 0),
                "draw":   row.get("all", {}).get("draw", 0),
                "lose":   row.get("all", {}).get("lose", 0),
                "gf":     row.get("all", {}).get("goals", {}).get("for", 0),
                "ga":     row.get("all", {}).get("goals", {}).get("against", 0),
                "points": row.get("points", 0),
                "form":   row.get("form", ""),
            }
            for row in table
        ]
        return {"fixture_id": fixture_id, "available": True, "standings": standings}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/live/fixtures")
def live_fixtures():
    data = football_api("fixtures", {"live": "all"})
    fixtures = []
    for fix in data.get("response",[]):
        f=fix.get("fixture",{}); t=fix.get("teams",{}); g=fix.get("goals",{}); l=fix.get("league",{})
        fixtures.append({
            "id":         f.get("id"),
            "minute":     f.get("status",{}).get("elapsed",0),
            "status":     f.get("status",{}).get("short",""),
            "home_team":  t.get("home",{}).get("name",""),
            "away_team":  t.get("away",{}).get("name",""),
            "home_goals": g.get("home",0),
            "away_goals": g.get("away",0),
            "home_logo":  t.get("home",{}).get("logo",""),
            "away_logo":  t.get("away",{}).get("logo",""),
            "league":     l.get("name",""),
            "country":    l.get("country",""),
        })
    return {
        "count":     len(fixtures),
        "fixtures":  fixtures,
        "timestamp": str(datetime.now())
    }

@app.get("/api/live/status")
def live_status():
    """Live monitor aktuális állapota."""
    return _monitor_status

@app.get("/api/bankroll")
def bankroll():
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").in_(
            "result_status",["Win","Lost"]).execute()
        tips   = result.data or []
        wins   = sum(1 for t in tips if t.get("result_status")=="Win")
        profit = sum(float(t.get("profit") or 0) for t in tips)
        stake  = sum(float(t.get("stake")  or 0) for t in tips)
        closed = [t for t in tips]
        last5  = [t["result_status"] for t in closed[-5:]]
        streak = 0
        if last5:
            s = last5[-1]
            for r in reversed(last5):
                if r==s: streak+=1
                else: break
        return {
            "total":          len(tips),
            "wins":           wins,
            "losses":         len(tips)-wins,
            "win_rate":       round(wins/max(len(tips),1)*100,1),
            "roi":            round(profit/max(stake,1)*100,2),
            "total_profit":   round(profit,2),
            "current_streak": {"type": last5[-1] if last5 else None, "count": streak},
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/arbitrage")
def arbitrage(date_str: str = ""):
    target = date_str or date.today().strftime("%Y-%m-%d")
    data   = football_api("fixtures", {"date": target, "status": "NS"})
    arbs   = []
    for fix in data.get("response",[])[:20]:
        fid     = fix.get("fixture",{}).get("id")
        home_nm = fix.get("teams",{}).get("home",{}).get("name","")
        away_nm = fix.get("teams",{}).get("away",{}).get("name","")
        league  = fix.get("league",{}).get("name","")
        odds_d  = football_api("odds", {"fixture": fid})
        bh=bd=ba=0.0; nbh=nbd=nba=""
        for bk in odds_d.get("response",[{}])[0].get("bookmakers",[]):
            for bet in bk.get("bets",[]):
                if bet.get("name")=="Match Winner":
                    for v in bet.get("values",[]):
                        odd=float(v.get("odd",0))
                        if v["value"]=="Home" and odd>bh: bh=odd; nbh=bk["name"]
                        if v["value"]=="Draw" and odd>bd: bd=odd; nbd=bk["name"]
                        if v["value"]=="Away" and odd>ba: ba=odd; nba=bk["name"]
        if bh>1 and bd>1 and ba>1:
            margin = 1/bh+1/bd+1/ba
            if margin < 1.0:
                arbs.append({
                    "fixture_id": fid, "home_team": home_nm,
                    "away_team": away_nm, "league": league,
                    "profit_pct": round((1-margin)*100,2),
                    "best_odds": {
                        "home":{"odd":bh,"bookmaker":nbh},
                        "draw":{"odd":bd,"bookmaker":nbd},
                        "away":{"odd":ba,"bookmaker":nba},
                    }
                })
    return {"date": target, "count": len(arbs), "opportunities": arbs}

# ─── LIVE MONITOR (háttérszál) ────────────────────────────────────────────────
_monitor_status = {
    "running":      False,
    "last_cycle":   None,
    "api_calls":    0,
    "active_tips":  0,
    "alerts_sent":  0,
}
_sent_alerts = set()
_last_scores = {}

def get_todays_tips_from_supabase() -> dict:
    """Mai pending tippek fixture_id → tip mapping."""
    try:
        sb     = get_sb()
        today  = date.today().isoformat()
        result = sb.table("tips").select("*").not_.in_(
            "result_status", ["Win","Lost","Void"]
        ).execute()
        tip_map = {}
        for tip in (result.data or []):
            fid = tip.get("fixture_id")
            if fid and str(tip.get("created_at",""))[:10] == today:
                tip_map[int(fid)] = tip
        return tip_map
    except Exception as e:
        log.error(f"Supabase tip fetch: {e}")
        return {}

def analyze_situation(tip: dict, fixture: dict, events: list) -> dict:
    """Gyors helyzetelemzés cash out javaslathoz."""
    goals     = fixture.get("goals",{})
    fix_info  = fixture.get("fixture",{})
    teams     = fixture.get("teams",{})
    hg        = goals.get("home",0) or 0
    ag        = goals.get("away",0) or 0
    minute    = fix_info.get("status",{}).get("elapsed",0) or 0
    home_id   = teams.get("home",{}).get("id",0)
    pred_map  = {"home":"home","1":"home","draw":"draw","X":"draw","away":"away","2":"away"}
    pred_key  = pred_map.get(str(tip.get("prediction","")).lower(),"home")

    winning   = (pred_key=="home" and hg>ag) or (pred_key=="away" and ag>hg) or (pred_key=="draw" and hg==ag)
    red_cards = [e for e in events if "Red Card" in e.get("detail","")]
    our_team  = teams.get("home" if pred_key=="home" else "away",{}).get("id",0)
    our_red   = [e for e in red_cards if e.get("team",{}).get("id",0)==our_team]

    if our_red:
        return {"action":"CASH OUT NOW 🚨","urgency":"critical","reason":f"Red card! {our_red[0].get('player',{}).get('name','')}"}
    elif minute>=80 and not winning:
        return {"action":"CASH OUT NOW 🚨","urgency":"critical","reason":f"Min {minute}, losing position"}
    elif minute>=70 and not winning:
        return {"action":"CONSIDER CASH OUT ⚡","urgency":"high","reason":f"Min {minute}, position weak"}
    elif winning and minute>=75:
        return {"action":"HOLD TIGHT 🔒","urgency":"low","reason":f"Min {minute}, winning – stay in!"}
    else:
        return {"action":"HOLD ✅","urgency":"normal","reason":"Normal game flow"}

def live_monitor_loop():
    """Háttérszálon fut Railway-en 24/7."""
    global _monitor_status
    log.info("🔴 Live Monitor indul Railway-en...")
    _monitor_status["running"] = True
    api_calls = 0

    while True:
        try:
            cycle_start = time.time()
            today_tips  = get_todays_tips_from_supabase()
            _monitor_status["active_tips"] = len(today_tips)

            if not today_tips:
                _monitor_status["last_cycle"] = str(datetime.now())
                time.sleep(120)  # 2 perc ha nincs tipp
                continue

            # 1 hívás az összes élő meccshez
            live_data = football_api("fixtures", {"live": "all"})
            api_calls += 1
            _monitor_status["api_calls"] = api_calls

            live_fixtures_map = {}
            for fix in live_data.get("response",[]):
                fid = fix.get("fixture",{}).get("id")
                if fid: live_fixtures_map[int(fid)] = fix

            # Csak tippelt meccsek
            my_live = {fid: fix for fid, fix in live_fixtures_map.items() if fid in today_tips}

            for fid, fixture in my_live.items():
                tip     = today_tips[fid]
                fix_inf = fixture.get("fixture",{})
                minute  = fix_inf.get("status",{}).get("elapsed",0) or 0
                status  = fix_inf.get("status",{}).get("short","")
                goals   = fixture.get("goals",{})
                hg      = goals.get("home",0) or 0
                ag      = goals.get("away",0) or 0
                teams   = fixture.get("teams",{})
                home_nm = teams.get("home",{}).get("name","")
                away_nm = teams.get("away",{}).get("name","")

                # Állás változás detektálása
                last_score = _last_scores.get(fid, (-1,-1))
                if (hg, ag) != last_score:
                    _last_scores[fid] = (hg, ag)

                    # Events lekérése állásváltozáskor
                    ev_data = football_api("fixtures/events", {"fixture": fid})
                    api_calls += 1
                    events  = []
                    for ev in ev_data.get("response",[]):
                        events.append({
                            "type":   ev.get("type",""),
                            "detail": ev.get("detail",""),
                            "minute": ev.get("time",{}).get("elapsed",0),
                            "team":   {"id": ev.get("team",{}).get("id",0),
                                      "name": ev.get("team",{}).get("name","")},
                            "player": {"name": ev.get("player",{}).get("name","")},
                        })

                    # Legújabb esemény
                    goals_ev = [e for e in events if e["type"]=="Goal" and "Normal" in e.get("detail","")]
                    red_ev   = [e for e in events if "Red Card" in e.get("detail","")]

                    situation = analyze_situation(tip, fixture, events)
                    pred      = tip.get("prediction","").upper()
                    orig_odds = float(tip.get("odds") or 0)

                    # Gól értesítő
                    if goals_ev:
                        last_g  = goals_ev[-1]
                        alert_k = f"{fid}_goal_{hg}_{ag}"
                        if alert_k not in _sent_alerts:
                            _sent_alerts.add(alert_k)
                            msg  = f"⚽ <b>GOAL! {home_nm} {hg}–{ag} {away_nm}</b>\n"
                            msg += f"⏱ {last_g['minute']}' | {last_g['team']['name']}: {last_g['player']['name']}\n"
                            msg += f"🎯 Your tip: {pred} @ {orig_odds:.2f}\n"
                            msg += f"\n{situation['action']}\n• {situation['reason']}"
                            send_telegram(msg)
                            _monitor_status["alerts_sent"] = _monitor_status.get("alerts_sent",0)+1
                            log.info(f"🔔 Goal alert: {home_nm} {hg}-{ag} {away_nm}")

                    # Piros lap értesítő
                    if red_ev:
                        for rev in red_ev:
                            alert_k = f"{fid}_red_{rev['minute']}_{rev['player']['name']}"
                            if alert_k not in _sent_alerts:
                                _sent_alerts.add(alert_k)
                                msg  = f"🟥 <b>RED CARD! {rev['minute']}'</b>\n"
                                msg += f"🏟️ {home_nm} vs {away_nm}\n"
                                msg += f"👤 {rev['team']['name']}: {rev['player']['name']}\n"
                                msg += f"\n🚨 <b>{situation['action']}</b>\n• {situation['reason']}"
                                send_telegram(msg)
                                _monitor_status["alerts_sent"] = _monitor_status.get("alerts_sent",0)+1

                # Cash out kritikus javaslat (állásváltozás nélkül is)
                sit = analyze_situation(tip, fixture, [])
                if sit["urgency"] in ["critical","high"]:
                    alert_k = f"{fid}_{sit['action']}_{minute//10}"
                    if alert_k not in _sent_alerts:
                        _sent_alerts.add(alert_k)
                        msg  = f"{sit['action']}\n"
                        msg += f"🏟️ {home_nm} {hg}–{ag} {away_nm} ({minute}')\n"
                        msg += f"🎯 Your tip: {pred} @ {orig_odds:.2f}\n"
                        msg += f"• {sit['reason']}"
                        send_telegram(msg)

                # Meccs vége
                if status in {"FT","AET","PEN"}:
                    alert_k = f"{fid}_final"
                    if alert_k not in _sent_alerts:
                        _sent_alerts.add(alert_k)
                        pred_map = {"home":"home","1":"home","draw":"draw","X":"draw","away":"away","2":"away"}
                        pk = pred_map.get(tip.get("prediction","").lower(),"home")
                        won = (pk=="home" and hg>ag) or (pk=="away" and ag>hg) or (pk=="draw" and hg==ag)
                        msg  = f"🏁 <b>FINAL: {home_nm} {hg}–{ag} {away_nm}</b>\n"
                        msg += f"🎯 Your tip: {pred} → {'✅ WON!' if won else '❌ Lost'}"
                        send_telegram(msg)
                        if fid in today_tips: del today_tips[fid]

            # Polling intervallum
            latest_minute = max(
                (fix.get("fixture",{}).get("status",{}).get("elapsed",0) or 0
                 for fix in my_live.values()), default=0
            )
            interval = 30 if latest_minute >= 75 else 60
            _monitor_status["last_cycle"] = str(datetime.now())

            elapsed  = time.time() - cycle_start
            sleep_t  = max(0, interval - elapsed)
            time.sleep(sleep_t)

        except Exception as e:
            log.error(f"Live monitor ciklus hiba: {e}")
            time.sleep(60)

# ─── STARTUP ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    """FastAPI induláskor elindítja a live monitort háttérszálon."""
    if FOOTBALL_API_KEY:
        t = threading.Thread(target=live_monitor_loop, daemon=True)
        t.start()
        log.info("✅ Live Monitor háttérszál elindítva")
    else:
        log.warning("⚠️ FOOTBALL_API_KEY hiányzik – Live Monitor nem indul!")

# ─── ÚJ: uvicorn induló parancs (korábban hiányzott!) ─────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
