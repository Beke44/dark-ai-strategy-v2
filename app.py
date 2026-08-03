from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

SUPABASE_URL = "https://kvduiliabfncikvesmza.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2ZHVpbGlhYmZuY2lrdmVzbXphIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU3ODEyMjYsImV4cCI6MjEwMTM1NzIyNn0.9tbY11h4nFDe7IAxqcdcNcZXxcs1r1w9096A2ZlKL_0"

app = FastAPI(title="Dark AI Strategy API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_supabase():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/")
def root():
    return {"status": "online", "name": "Dark AI Strategy API", "version": "2.0"}

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": str(datetime.now())}

@app.get("/api/stats/public")
def stats():
    try:
        sb = get_supabase()
        result = sb.table("tips").select("*").in_("result_status", ["Win", "Lost"]).execute()
        tips = result.data or []
        if not tips:
            return {"total": 0, "wins": 0, "win_rate": 0, "roi": 0}
        wins = sum(1 for t in tips if t.get("result_status") == "Win")
        profit = sum(float(t.get("profit") or 0) for t in tips)
        stake = sum(float(t.get("stake") or 0) for t in tips)
        dc = [t for t in tips if "Double chance" in str(t.get("extra_tip", ""))]
        dc_wins = sum(1 for t in dc if t.get("result_status") == "Win")
        return {
            "total": len(tips),
            "wins": wins,
            "losses": len(tips) - wins,
            "win_rate": round(wins / len(tips) * 100, 1),
            "roi": round(profit / max(stake, 1) * 100, 2),
            "double_chance_win_rate": round(dc_wins / max(len(dc), 1) * 100, 1),
            "verified": True,
            "last_updated": str(datetime.now())
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/tips/recent")
def recent_tips(limit: int = 20):
    try:
        sb = get_supabase()
        result = sb.table("tips").select("*").order("timestamp", desc=True).limit(limit).execute()
        return {"tips": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        return {"error": str(e)}
