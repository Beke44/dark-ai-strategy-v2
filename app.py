from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
from datetime import datetime

app = FastAPI(title="Dark AI Strategy API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    return {"status": "online", "name": "Dark AI Strategy API"}

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": str(datetime.now())}

@app.get("/api/stats/public")
def stats():
    try:
        with open("statistics_data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        tips = data.get("saved_tips", [])
        closed = [t for t in tips if t.get("result_status") in ["Win", "Lost"]]
        wins = sum(1 for t in closed if t["result_status"] == "Win")
        profit = sum(float(t.get("profit", 0)) for t in closed)
        stake = sum(float(t.get("stake", 0)) for t in closed)
        dc = [t for t in closed if "Double chance" in str(t.get("extra_tip", ""))]
        dc_wins = sum(1 for t in dc if t["result_status"] == "Win")
        return {
            "total": len(closed),
            "wins": wins,
            "losses": len(closed) - wins,
            "win_rate": round(wins / max(len(closed), 1) * 100, 1),
            "roi": round(profit / max(stake, 1) * 100, 2),
            "double_chance_win_rate": round(dc_wins / max(len(dc), 1) * 100, 1),
            "last_updated": str(datetime.now())
        }
    except Exception as e:
        return {"error": str(e)}
