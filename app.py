"""
Dark AI Strategy – FastAPI Backend LIVE verzió
=================================================
Ez FELVÁLTJA az app.py-t!
Másold: C:/darkaistrategy/uj/app.py (replace!)
GitHub-ra is töltsd fel ugyanide.

Tartalmazza:
- /api/stats/public         → nyilvános statisztikák
- /api/tips/recent          → legutóbbi tippek
- /api/live/fixtures        → élő meccsek
- /api/live/analyze/{id}    → élő meccs elemzés
- /api/live/my-tips/{uid}   → felhasználó aktív tippjeinek live elemzése
- /api/bankroll/{uid}       → bankroll védelem
"""

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import os, json, requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = "https://kvduiliabfncikvesmza.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2ZHVpbGlhYmZuY2lrdmVzbXphIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU3ODEyMjYsImV4cCI6MjEwMTM1NzIyNn0.9tbY11h4nFDe7IAxqcdcNcZXxcs1r1w9096A2ZlKL_0"
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
API_BASE = "https://v3.football.api-sports.io"

app = FastAPI(title="Dark AI Strategy API", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Helper függvények ────────────────────────────────────────────────────────
def get_sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def football_api(endpoint: str, params: dict = {}) -> dict:
    try:
        r = requests.get(
            f"{API_BASE}/{endpoint}",
            headers={"x-apisports-key": FOOTBALL_API_KEY},
            params=params,
            timeout=10
        )
        return r.json()
    except: return {}

# ─── ALAP VÉGPONTOK ──────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "online", "name": "Dark AI Strategy API", "version": "3.0"}

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "football_api": bool(FOOTBALL_API_KEY),
        "timestamp": str(datetime.now())
    }

@app.get("/api/stats/public")
def public_stats():
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").in_("result_status", ["Win","Lost"]).execute()
        tips   = result.data or []
        if not tips:
            return {"total": 0, "wins": 0, "win_rate": 0, "roi": 0}
        wins   = sum(1 for t in tips if t.get("result_status") == "Win")
        profit = sum(float(t.get("profit") or 0) for t in tips)
        stake  = sum(float(t.get("stake")  or 0) for t in tips)
        dc     = [t for t in tips if "Double chance" in str(t.get("extra_tip",""))]
        dc_w   = sum(1 for t in dc if t.get("result_status") == "Win")
        return {
            "total":    len(tips),
            "wins":     wins,
            "losses":   len(tips) - wins,
            "win_rate": round(wins/len(tips)*100,1),
            "roi":      round(profit/max(stake,1)*100,2),
            "double_chance_win_rate": round(dc_w/max(len(dc),1)*100,1),
            "verified": True,
            "last_updated": str(datetime.now())
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/recent")
def recent_tips(limit: int = 20):
    try:
        sb     = get_sb()
        result = sb.table("tips").select("*").order("timestamp", desc=True).limit(limit).execute()
        return {"tips": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        return {"error": str(e)}

# ─── ÉLŐMECCS VÉGPONTOK ──────────────────────────────────────────────────────
@app.get("/api/live/fixtures")
def live_fixtures():
    """Összes élő meccs."""
    data     = football_api("fixtures", {"live": "all"})
    fixtures = []
    for fix in data.get("response", []):
        f = fix.get("fixture", {})
        t = fix.get("teams",   {})
        g = fix.get("goals",   {})
        l = fix.get("league",  {})
        fixtures.append({
            "id":         f.get("id"),
            "minute":     f.get("status", {}).get("elapsed", 0),
            "status":     f.get("status", {}).get("short"),
            "home_team":  t.get("home", {}).get("name"),
            "away_team":  t.get("away", {}).get("name"),
            "home_goals": g.get("home", 0),
            "away_goals": g.get("away", 0),
            "league":     l.get("name"),
        })
    return {"count": len(fixtures), "fixtures": fixtures, "timestamp": str(datetime.now())}

@app.get("/api/live/analyze/{fixture_id}")
def live_analyze(fixture_id: int, prediction: str = "home",
                 odds: float = 2.0, stake: float = 1000,
                 risk_profile: str = "normal"):
    """
    Élő meccs AI elemzése és javaslat.
    prediction: home/draw/away
    odds: az eredeti fogadás oddsa
    stake: tét összege
    risk_profile: conservative/normal/aggressive
    """
    try:
        from live_betting_ai import get_fixture_live_data, analyze_live_tip, set_api_key
        set_api_key(FOOTBALL_API_KEY)
        live_data = get_fixture_live_data(fixture_id)
        tip       = {
            "prediction": prediction,
            "odds":       odds,
            "stake":      stake,
            "fixture":    live_data.get("fixture", {})
        }
        analysis = analyze_live_tip(tip, live_data, risk_profile)
        return {"fixture_id": fixture_id, "analysis": analysis}
    except Exception as e:
        return {"error": str(e), "fixture_id": fixture_id}

@app.get("/api/live/my-tips/{user_id}")
def live_my_tips(user_id: str, risk_profile: str = "normal"):
    """
    A felhasználó összes aktív (Pending) tippjének élő elemzése.
    Ez a Lovable Live Dashboard főadata.
    """
    try:
        from live_betting_ai import get_fixture_live_data, analyze_live_tip, set_api_key
        set_api_key(FOOTBALL_API_KEY)

        sb      = get_sb()
        result  = sb.table("tips").select("*").eq(
            "result_status", "Pending"
        ).execute()
        pending = result.data or []

        # Csak a mai tippek
        today   = datetime.now().strftime("%Y-%m-%d")
        today_tips = [
            t for t in pending
            if str(t.get("timestamp",""))[:10] == today
        ]

        analyzed = []
        for tip in today_tips[:10]:  # max 10 tipp egyszerre
            try:
                # Meccs ID kell
                fixture_id = tip.get("fixture_id")
                if not fixture_id:
                    analyzed.append({**tip, "live_analysis": None, "error": "Nincs fixture_id"})
                    continue

                live_data = get_fixture_live_data(int(fixture_id))
                analysis  = analyze_live_tip(tip, live_data, risk_profile)
                analyzed.append({**tip, "live_analysis": analysis})
            except Exception as te:
                analyzed.append({**tip, "live_analysis": None, "error": str(te)})

        # Urgency szerint rendezés (critical előre)
        urgency_order = {"critical": 0, "high": 1, "medium": 2, "normal": 3, "low": 4}
        analyzed.sort(key=lambda x: urgency_order.get(
            x.get("live_analysis", {}).get("urgency", "normal") if x.get("live_analysis") else "normal", 3
        ))

        return {
            "user_id":      user_id,
            "total":        len(analyzed),
            "tips":         analyzed,
            "risk_profile": risk_profile,
            "timestamp":    str(datetime.now())
        }

    except Exception as e:
        return {"error": str(e), "user_id": user_id}

@app.get("/api/bankroll/{user_id}")
def bankroll_check(user_id: str, bankroll: float = 10000):
    """Bankroll védelem elemzése."""
    try:
        from live_betting_ai import check_bankroll_protection
        sb     = get_sb()
        result = sb.table("tips").select("*").execute()
        tips   = result.data or []
        check  = check_bankroll_protection(tips, bankroll)
        return {"user_id": user_id, "bankroll": bankroll, "protection": check}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/live/parlay")
def live_parlay(fixture_ids: str = "", predictions: str = "",
                odds_list: str = "", stake: float = 1000,
                risk_profile: str = "normal"):
    """
    Kombináció (parlay) élő elemzése.
    fixture_ids: vesszővel elválasztva pl. "12345,67890"
    predictions: "home,away"
    odds_list: "2.1,3.5"
    """
    try:
        from live_betting_ai import get_fixture_live_data, analyze_parlay, set_api_key
        set_api_key(FOOTBALL_API_KEY)

        ids   = [int(x) for x in fixture_ids.split(",") if x.strip()]
        preds = predictions.split(",")
        odds  = [float(x) for x in odds_list.split(",") if x.strip()]

        tips = []
        live_data_map = {}

        for i, fid in enumerate(ids):
            tip = {
                "prediction": preds[i] if i < len(preds) else "home",
                "odds":       odds[i]  if i < len(odds)  else 2.0,
                "stake":      stake,
                "fixture":    {"id": fid}
            }
            tips.append(tip)
            try:
                live_data_map[fid] = get_fixture_live_data(fid)
            except: pass

        result = analyze_parlay(tips, live_data_map, risk_profile)
        return result
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/telegram/send-daily-tips")
def trigger_daily_tips(api_key: str = ""):
    """Manuálisan kiküldi a napi tippeket Telegram-ra."""
    if api_key != "dark-ai-secret-2026":
        return {"error": "Unauthorized"}
    try:
        from telegram_bot import send_daily_tips
        send_daily_tips()
        return {"status": "ok", "message": "Napi tippek elküldve"}
    except Exception as e:
        return {"error": str(e)}
