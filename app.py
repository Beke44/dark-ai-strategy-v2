"""
Dark AI Strategy â Railway Worker
===================================
Ez fut Railway-en 24/7:
- FastAPI backend (API vĂŠgpontok)
- Live Monitor hĂĄttĂŠrszĂĄlon (ĂŠlĹ meccsek figyelĂŠse)
- Automatikus ĂşjraindĂ­tĂĄs hiba esetĂŠn

Railway-en ez az app.py helyett fut ha Procfile-t hasznĂĄlunk.
VAGY: ezt tĂśltsd fel app.py nĂŠvvel a GitHub-ra.

--- MĂDOSĂTVA: modell-nevek anonimizĂĄlva a nyilvĂĄnos API vĂĄlaszokban ---
"""

import os, sys, json, logging, requests, time, threading, functools, hmac, html
from datetime import datetime, date, timedelta
from pathlib import Path
from dotenv import load_dotenv

# FastAPI
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
RAILWAY_API_KEY  = os.getenv("RAILWAY_API_KEY", "")
SUPABASE_URL     = "https://kvduiliabfncikvesmza.supabase.co"
SUPABASE_KEY     = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2ZHVpbGlhYmZuY2lrdmVzbXphIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU3ODEyMjYsImV4cCI6MjEwMTM1NzIyNn0.9tbY11h4nFDe7IAxqcdcNcZXxcs1r1w9096A2ZlKL_0"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID", "")
API_BASE         = "https://v3.football.api-sports.io"
TIPS_PAGE_URL    = os.getenv("TIPS_PAGE_URL", "https://darkaistrategy.com/tips")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# âââ FastAPI ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
app = FastAPI(title="Dark AI Strategy API", version="4.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://darkaistrategy.com",
        "https://www.darkaistrategy.com",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type", "X-Railway-API-Key"],
)

PUBLIC_PATHS = {"/", "/api/health", "/api/stats/public"}

@app.middleware("http")
async def require_server_api_key(request: Request, call_next):
    """A fizetĹs Railway API kizĂĄrĂłlag szerverâszerver kulccsal ĂŠrhetĹ el."""
    path = request.url.path.rstrip("/") or "/"
    if (
        request.method == "OPTIONS"
        or path in PUBLIC_PATHS
        or not path.startswith("/api/")
    ):
        return await call_next(request)

    supplied = request.headers.get("x-railway-api-key", "")
    if not RAILWAY_API_KEY:
        log.error("RAILWAY_API_KEY nincs beĂĄllĂ­tva; vĂŠdett kĂŠrĂŠs elutasĂ­tva.")
        return JSONResponse({"error": "service_not_configured"}, status_code=503)
    if not supplied or not hmac.compare_digest(supplied, RAILWAY_API_KEY):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

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

# âââ ĂJ: LAPOZOTT SUPABASE LEKĂRDEZĂS ââââââââââââââââââââââââââââââââââââââââââ
# A Supabase/PostgREST alapĂŠrtelmezetten 1000 sorra vĂĄgja a vĂĄlaszokat, ha
# nincs explicit .range() megadva - emiatt a korĂĄbbi statisztikĂĄk (win rate,
# ROI, profit) hiĂĄnyosan, csak az elsĹ 1000 lezĂĄrt tippbĹl szĂĄmoltak, holott
# 2000+ tipp van a tĂĄblĂĄban. Ez a segĂŠdfĂźggvĂŠny lapozva lekĂŠri AZ ĂSSZES sort.
def fetch_all_tips(status_filter=None, page_size: int = 1000, max_pages: int = 20):
    sb = get_sb()
    all_rows = []
    start = 0
    for _ in range(max_pages):
        q = sb.table("tips").select("*")
        if status_filter:
            q = q.in_("result_status", status_filter)
        q = q.range(start, start + page_size - 1)
        result = q.execute()
        page = result.data or []
        all_rows.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return all_rows
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# âââ ĂJ: EGYSZERĹ° MEMĂRIA-CACHE A KĂLSĹ API HĂVĂSOKHOZ ââââââââââââââââââââââââ
# Ha egyszerre sok felhasznĂĄlĂł nĂŠzi ugyanazt a meccset, nem akarjuk minden
# egyes oldalbetĂśltĂŠsnĂŠl Ăşjra lehĂ­vni ugyanazt a kĂźlsĹ API vĂŠgpontot -
# ehelyett egy rĂśvid ideig (a TTL alatt) a memĂłriĂĄban tĂĄrolt vĂĄlaszt adjuk
# vissza mindenkinek. EgyszerĹą, de hatĂŠkony megoldĂĄs egyetlen Railway
# instance mellett (jelenleg 1 replica fut).
_endpoint_cache = {}
_endpoint_cache_lock = threading.Lock()

def cached_call(key: str, ttl_seconds: int, fn, *args, **kwargs):
    now = time.time()
    with _endpoint_cache_lock:
        cached = _endpoint_cache.get(key)
        if cached and (now - cached["ts"]) < ttl_seconds:
            return cached["data"]
    result = fn(*args, **kwargs)
    with _endpoint_cache_lock:
        _endpoint_cache[key] = {"data": result, "ts": now}
    return result

def simple_cache(ttl_seconds: int):
    """
    DekorĂĄtor a nehezebb (kĂźlsĹ API-t hĂ­vĂł) vĂŠgpontokra. Ha sok
    felhasznĂĄlĂł egyszerre kĂŠri le ugyanazt a meccset, a TTL alatt
    mindenki ugyanazt a cache-elt vĂĄlaszt kapja - nem hĂ­vjuk le
    feleslegesen sokszor ugyanazt a kĂźlsĹ API vĂŠgpontot.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__name__}:{args}:{sorted(kwargs.items())}"
            return cached_call(key, ttl_seconds, fn, *args, **kwargs)
        return wrapper
    return decorator
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# âââ ĂJ: ĂLĹ ESEMĂNYEK ĂS RĂSZLETES STATISZTIKA FELDOLGOZĂSA ââââââââââââââââââ
_STAT_KEY_MAP = {
    "Shots on Goal":     "shots_on_target",
    "Shots off Goal":    "shots_off_target",
    "Total Shots":       "total_shots",
    "Blocked Shots":     "blocked_shots",
    "Shots insidebox":   "shots_inside_box",
    "Shots outsidebox":  "shots_outside_box",
    "Fouls":             "fouls",
    "Corner Kicks":      "corners",
    "Offsides":          "offsides",
    "Ball Possession":   "possession",
    "Yellow Cards":      "yellow_cards",
    "Red Cards":         "red_cards",
    "Goalkeeper Saves":  "goalkeeper_saves",
    "Total passes":      "total_passes",
    "Passes accurate":   "passes_accurate",
    "Passes %":          "passes_percent",
    "expected_goals":    "xg",
}

def get_live_stats_parsed(fixture_id: int):
    """CsapatonkĂŠnti, tisztĂĄn feldolgozott ĂŠlĹ statisztika (lĂśvĂŠsek,
    birtoklĂĄs, szĂśglet, lapok stb.) - nem a nyers API vĂĄlasz."""
    data = football_api("fixtures/statistics", {"fixture": fixture_id})
    response = data.get("response", [])
    if not response:
        return None
    result = {}
    for team_block in response:
        team_name = team_block.get("team", {}).get("name", "")
        stats = {}
        for s in team_block.get("statistics", []):
            key = _STAT_KEY_MAP.get(s.get("type", ""))
            if key:
                stats[key] = s.get("value")
        result[team_name] = stats
    return result


def get_match_events(fixture_id: int):
    """EsemĂŠny-naplĂł: gĂłlok, lapok, cserĂŠk percenkĂŠnti bontĂĄsban."""
    data = football_api("fixtures/events", {"fixture": fixture_id})
    events = []
    for ev in data.get("response", []):
        events.append({
            "minute":       ev.get("time", {}).get("elapsed"),
            "extra_minute": ev.get("time", {}).get("extra"),
            "type":         ev.get("type", ""),      # Goal / Card / subst / Var
            "detail":       ev.get("detail", ""),    # Normal Goal / Yellow Card / Substitution 1 ...
            "team":         ev.get("team", {}).get("name", ""),
            "player":       ev.get("player", {}).get("name", ""),
            "assist":       (ev.get("assist") or {}).get("name"),
        })
    # RĂŠgi esemĂŠnyektĹl az Ăşjabbak felĂŠ rendezve (percek szerint)
    events.sort(key=lambda e: (e.get("minute") or 0, e.get("extra_minute") or 0))
    return events
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# âââ ĂJ: CSAPAT SZEZON-STATISZTIKA (mĂŠlyebb, mint a puszta tabella) âââââââââââ
def get_team_season_stats(team_id: int, league_id: int, season) -> dict:
    """
    Csapat egĂŠsz szezonos statisztikĂĄja: hazai/vendĂŠg gĂłlĂĄtlag,
    clean sheet szĂĄm, gĂłlnĂŠlkĂźli meccsek szĂĄma, legjobb gyĹzelmi sorozat,
    jelenlegi forma-string. Sokkal mĂŠlyebb, mint a tabella egy sora.
    """
    data = football_api("teams/statistics", {
        "team": team_id, "league": league_id, "season": season
    })
    r = data.get("response") or {}
    if not r:
        return None

    fixtures       = r.get("fixtures", {}) or {}
    goals          = r.get("goals", {}) or {}
    biggest        = r.get("biggest", {}) or {}
    clean_sheet    = r.get("clean_sheet", {}) or {}
    failed_to_score = r.get("failed_to_score", {}) or {}

    goals_for_avg     = (goals.get("for", {}) or {}).get("average", {}) or {}
    goals_against_avg = (goals.get("against", {}) or {}).get("average", {}) or {}

    return {
        "team":                    r.get("team", {}).get("name"),
        "form":                    r.get("form"),
        "played_total":            (fixtures.get("played", {}) or {}).get("total"),
        "wins_total":              (fixtures.get("wins", {}) or {}).get("total"),
        "draws_total":             (fixtures.get("draws", {}) or {}).get("total"),
        "loses_total":             (fixtures.get("loses", {}) or {}).get("total"),
        "goals_for_avg_total":     goals_for_avg.get("total"),
        "goals_for_avg_home":      goals_for_avg.get("home"),
        "goals_for_avg_away":      goals_for_avg.get("away"),
        "goals_against_avg_total": goals_against_avg.get("total"),
        "goals_against_avg_home":  goals_against_avg.get("home"),
        "goals_against_avg_away":  goals_against_avg.get("away"),
        "clean_sheets_total":      clean_sheet.get("total"),
        "failed_to_score_total":   failed_to_score.get("total"),
        "biggest_win_streak":      (biggest.get("streak", {}) or {}).get("wins"),
        "biggest_win_home":        (biggest.get("wins", {}) or {}).get("home"),
        "biggest_win_away":        (biggest.get("wins", {}) or {}).get("away"),
    }
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# âââ ĂJ: ODDS-MOZGĂS KĂVETĂSE ("ĂŠles pĂŠnz" jelzĂŠs) ââââââââââââââââââââââââââââ
def record_and_get_odds_movement(fixture_id: int, current_odds: dict):
    """
    Minden lekĂŠrdezĂŠskor elmenti a jelenlegi legjobb odds-okat egy
    historikus (insert-only) Supabase tĂĄblĂĄba, majd Ăśsszeveti a
    legkorĂĄbban rĂśgzĂ­tett ("nyitĂł") ĂŠrtĂŠkkel. Ha egy oldal odds-a
    jelentĹsen (>=7%) csĂśkkent a nyitĂł Ăłta, az klasszikus jele annak,
    hogy nagyobb tĂŠtek ĂŠrkeztek arra az oldalra ("ĂŠles pĂŠnz").
    """
    try:
        sb = get_sb()
        sb.table("odds_snapshots").insert({
            "fixture_id": fixture_id,
            "home_odd":   current_odds.get("home"),
            "draw_odd":   current_odds.get("draw"),
            "away_odd":   current_odds.get("away"),
        }).execute()

        result = sb.table("odds_snapshots").select("*").eq(
            "fixture_id", fixture_id).order("captured_at", desc=False).limit(1).execute()
        if not result.data:
            return None
        opening = result.data[0]

        def pct_change(open_v, curr_v):
            if not open_v or not curr_v:
                return None
            return round((curr_v - open_v) / open_v * 100, 2)

        movement = {
            "home": {"opening": opening.get("home_odd"), "current": current_odds.get("home"),
                      "change_pct": pct_change(opening.get("home_odd"), current_odds.get("home"))},
            "draw": {"opening": opening.get("draw_odd"), "current": current_odds.get("draw"),
                      "change_pct": pct_change(opening.get("draw_odd"), current_odds.get("draw"))},
            "away": {"opening": opening.get("away_odd"), "current": current_odds.get("away"),
                      "change_pct": pct_change(opening.get("away_odd"), current_odds.get("away"))},
        }

        SHARP_THRESHOLD = -7.0  # % - ennĂŠl nagyobb odds-csĂśkkenĂŠs szĂĄmĂ­t jelzĂŠsnek
        sharp_signal = None
        for side in ("home", "draw", "away"):
            chg = movement[side]["change_pct"]
            if chg is not None and chg <= SHARP_THRESHOLD:
                sharp_signal = side
                break
        movement["sharp_signal"] = sharp_signal
        return movement
    except Exception as e:
        log.error(f"Odds movement hiba: {e}")
        return None
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def _tg(value) -> str:
    """FelhasznĂĄlĂłi/API-szĂśveg biztonsĂĄgos Telegram HTML megjelenĂ­tĂŠse."""
    return html.escape(str(value or ""), quote=True)


def send_telegram(
    msg: str,
    category: str = None,
    fixture_id: int = None,
    buttons: list = None,
):
    """
    KĂźld egy Telegram Ăźzenetet, ĂŠs a message_id-t elmenti a Supabase
    telegram_messages tĂĄblĂĄjĂĄba - ez teszi lehetĹvĂŠ a kĂŠsĹbbi (2 nap
    utĂĄni) automatikus tĂśrlĂŠst.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        return None
    try:
        payload = {
            "chat_id": TELEGRAM_CHANNEL,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": text, "url": url}] for text, url in buttons]
            }
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
            timeout=10
        )
        result = r.json()
        if not r.ok or not result.get("ok"):
            log.error(f"Telegram kĂźldĂŠsi hiba ({r.status_code}): {result}")
            return None
        message_id = result.get("result", {}).get("message_id")
        if message_id:
            try:
                sb = get_sb()
                sb.table("telegram_messages").insert({
                    "message_id": message_id,
                    "chat_id": str(TELEGRAM_CHANNEL),
                    "category": category,
                    "fixture_id": fixture_id,
                }).execute()
            except Exception as _log_e:
                log.error(f"telegram_messages log hiba: {_log_e}")
        return message_id
    except Exception as e:
        log.error(f"Telegram kapcsolat hiba: {e}")
        return None

# âââ ĂJ: MODELL-NĂV ANONIMIZĂLĂS ââââââââââââââââââââââââââââââââââââââââââââââ
# A belsĹ modellneveket SOHA nem kĂźldjĂźk ki nyilvĂĄnosan (versenytĂĄrs-vĂŠdelem).
# Ha Ăşj modellt adsz a rendszerhez, itt vedd fel a sajĂĄt belsĹ kulcsnevĂŠt ->
# megjelenĂ­tendĹ "ĂĄlnevĂŠt".
MODEL_DISPLAY_NAMES = {
    "monte_carlo":     "SzimulĂĄciĂłs Modell",
    "elo":             "Rangsor Modell",
    "neural_network":  "MĂŠlytanulĂĄsi Modell",
    "xgboost":         "Statisztikai Modell",
    "form":            "Forma Modell",
    "h2h":             "EgymĂĄs Elleni Modell",
    "goal_stats":      "GĂłlstatisztikai Modell",
    "trust":           "MegbĂ­zhatĂłsĂĄgi Modell",
    "meta":            "Meta Modell",
    "ensemble":        "ĂsszesĂ­tett Modell",
}

def _coerce_to_dict(raw):
    """
    Supabase-bĹl nĂŠha JSON-string formĂĄban jĂśn vissza egy mezĹ
    (pl. ha mentĂŠskor json.dumps()-szal lett elmentve egy text/jsonb oszlopba).
    Ez a segĂŠdfĂźggvĂŠny biztonsĂĄgosan dict-tĂŠ alakĂ­tja, akĂĄrmilyen formĂĄban jĂśn.
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
    LecserĂŠli a belsĹ modellkulcsokat (pl. 'xgboost', 'elo') semleges,
    kifelĂŠ mutathatĂł elnevezĂŠsekre (pl. 'Modell 1', 'Modell 2'),
    vagy a MODEL_DISPLAY_NAMES-ben megadott nĂŠvre, ha van ilyen.
    Elfogadja dict vagy JSON-string formĂĄban is a bemenetet.
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
    Egy teljes elemzĂŠs-dict-en vĂŠgigmegy, ĂŠs minden olyan mezĹt
    anonimizĂĄl, ami a belsĹ modellarchitektĂşrĂĄra utalhat.
    Nem mĂłdosĂ­tja az eredeti dict-et, mĂĄsolattal dolgozik.
    Kezeli azt az esetet is, ha model_votes/models JSON-stringkĂŠnt jĂśtt Supabase-bĹl.
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

    # ĂJ: vĂŠdĹkorlĂĄt a value_edge mezĹre. Egy korĂĄbbi, rĂŠgi adatokban jelenlĂŠvĹ
    # skĂĄlĂĄzĂĄsi hiba (frakciĂł vs. szĂĄzalĂŠk keveredĂŠs a kĂŠt elemzĹ motor kĂśzĂśtt)
    # miatt nĂŠhĂĄny rĂŠgi tippnĂŠl irreĂĄlisan magas (pl. 14580%) ĂŠrtĂŠk szerepelhet.
    # Ha a value_edge 300%-nĂĄl magasabb (ami matematikailag szinte biztosan hibĂĄs
    # adat, nem valĂłs edge), jelezzĂźk gyanĂşskĂŠnt ahelyett, hogy a nyers, megtĂŠvesztĹ
    # szĂĄmot mutatnĂĄnk.
    VALUE_EDGE_SANITY_LIMIT = 300  # % - e fĂślĂśtt valĂłszĂ­nĹąleg hibĂĄs/rĂŠgi adat
    if "value_edge" in safe:
        try:
            ve = float(safe.get("value_edge") or 0)
            if ve > VALUE_EDGE_SANITY_LIMIT:
                safe["value_edge_suspect"] = True
                safe["value_edge_raw"] = ve  # megĹrizzĂźk a nyers ĂŠrtĂŠket debughoz
            else:
                safe["value_edge_suspect"] = False
        except (TypeError, ValueError):
            pass

    # Sosem kĂźldjĂźk ki nyers formĂĄban ezeket a mezĹket, ha esetleg bekerĂźlnĂŠnek:
    for forbidden_key in ("model_weights", "model_source_code", "internal_notes"):
        safe.pop(forbidden_key, None)

    return safe

# âââ API VĂGPONTOK ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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
        # MĂDOSĂTVA: lapozott lekĂŠrdezĂŠs, hogy 1000-nĂŠl tĂśbb lezĂĄrt tippet is
        # helyesen ĂśsszesĂ­tsen (korĂĄbban csendben levĂĄgta 1000-nĂŠl).
        tips   = fetch_all_tips(status_filter=["Win","Lost"])
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

# âââ ĂJ: CSAPATLOGĂ + PONTOS VĂGEREDMĂNY KIEGĂSZĂTĂS TIPPLISTĂKHOZ âââââââââââââ
# A `tips` tĂĄbla sorai nem tartalmaznak logĂł- vagy gĂłlszĂĄm-mezĹt (csak
# csapatnevet ĂŠs Win/Lost eredmĂŠnyt), ezĂŠrt a tipplista-vĂŠgpontok
# (recent/by-status/today) korĂĄbban nem tudtak sem logĂłt, sem pontos
# vĂŠgeredmĂŠnyt (pl. "1:1") visszaadni. Ez a segĂŠdfĂźggvĂŠny fixture_id
# alapjĂĄn, 1 ĂłrĂĄs cache-elĂŠssel adja vissza mindkettĹt EGY API hĂ­vĂĄsbĂłl -
# lezĂĄrt meccseknĂŠl a gĂłlszĂĄm Ăşgysem vĂĄltozik, Ă­gy ez a cache-idĹ biztonsĂĄgos.
@simple_cache(3600)
def _get_fixture_details(fixture_id: int):
    try:
        data = football_api("fixtures", {"id": fixture_id})
        fix  = (data.get("response") or [{}])[0] if data.get("response") else {}
        teams = fix.get("teams", {}) if fix else {}
        goals = fix.get("goals", {}) if fix else {}
        status = fix.get("fixture", {}).get("status", {}).get("short", "") if fix else ""
        return {
            "home_logo":  teams.get("home", {}).get("logo", ""),
            "away_logo":  teams.get("away", {}).get("logo", ""),
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "match_status": status,
        }
    except Exception:
        return {"home_logo": "", "away_logo": "", "home_score": None, "away_score": None, "match_status": ""}


VALUE_EDGE_SANITY_LIMIT = 300  # % - e fĂślĂśtt valĂłszĂ­nĹąleg hibĂĄs/rĂŠgi adat

def _flag_suspect_value_edge(t: dict):
    """Ugyanaz a vĂŠdĹkorlĂĄt, mint sanitize_analysis_for_public()-ben,
    de a tips-lista vĂŠgpontokra alkalmazva (recent/by-status/today) -
    ezek kĂśzvetlenĂźl a `tips` tĂĄblĂĄbĂłl olvasnak, nem mennek ĂĄt azon."""
    try:
        ve = float(t.get("value_edge") or 0)
        if ve > VALUE_EDGE_SANITY_LIMIT:
            t["value_edge_suspect"] = True
            t["value_edge_raw"] = ve
        else:
            t["value_edge_suspect"] = False
    except (TypeError, ValueError):
        pass
    return t


def _enrich_tips_with_logos(tips_list):
    """
    Minden tipphez hozzĂĄadja a home_logo/away_logo mezĹt, lezĂĄrt
    (Win/Lost) tippeknĂŠl a pontos vĂŠgeredmĂŠnyt (home_score/away_score),
    ĂŠs a value_edge gyanĂşs-jelzĂŠst is, fixture_id alapjĂĄn.
    """
    for t in tips_list:
        fid = t.get("fixture_id")
        if fid:
            details = _get_fixture_details(fid)
            t["home_logo"]  = details.get("home_logo", "")
            t["away_logo"]  = details.get("away_logo", "")
            if t.get("result_status") in ("Win", "Lost"):
                t["home_score"] = details.get("home_score")
                t["away_score"] = details.get("away_score")
            else:
                t["home_score"] = None
                t["away_score"] = None
        else:
            t["home_logo"]  = ""
            t["away_logo"]  = ""
            t["home_score"] = None
            t["away_score"] = None
        _flag_suspect_value_edge(t)  # ĂJ: value_edge vĂŠdĹkorlĂĄt itt is
    return tips_list
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ



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
        # DuplikĂĄciĂł szĹąrĂŠs
        seen, unique = set(), []
        for t in tips:
            k = f"{t.get('home_team','')}_{t.get('away_team','')}_{str(t.get('created_at',''))[:10]}"
            if k not in seen:
                seen.add(k); unique.append(t)
        unique = _enrich_tips_with_logos(unique)  # ĂJ: logĂłk hozzĂĄadĂĄsa
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
        unique = _enrich_tips_with_logos(unique)  # ĂJ: logĂłk hozzĂĄadĂĄsa
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
        pending  = _enrich_tips_with_logos(pending)  # ĂJ: logĂłk hozzĂĄadĂĄsa
        closed   = _enrich_tips_with_logos(closed)   # ĂJ: logĂłk hozzĂĄadĂĄsa
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
        # MĂDOSĂTVA: dĂĄtum szerinti szĹąrĂŠs + lapozĂĄs, hogy egy nap ALATTI
        # Ăśsszes tipp bekerĂźljĂśn (korĂĄbban limit(days*15) sok tippet
        # levĂĄghatott egy-egy forgalmasabb napon).
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        sb = get_sb()
        all_rows = []
        start = 0
        page_size = 1000
        for _ in range(20):
            q = sb.table("tips").select("*").gte(
                "created_at", cutoff).order("created_at", desc=True
            ).range(start, start + page_size - 1)
            result = q.execute()
            page = result.data or []
            all_rows.extend(page)
            if len(page) < page_size:
                break
            start += page_size
        tips   = all_rows
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
@simple_cache(30)
def match_detail(fixture_id: int):
    try:
        # MĂDOSĂTVA: mindig lekĂŠrjĂźk az ĂŠlĹ fixture-adatot (csapatnevek,
        # logĂłk, helyszĂ­n, fordulĂł) - korĂĄbban ez csak akkor tĂśrtĂŠnt meg,
        # ha NEM volt Supabase-elemzĂŠs, ezĂŠrt a mentett tippeknĂŠl hiĂĄnyoztak
        # a home_logo/away_logo mezĹk a vĂĄlaszbĂłl.
        fix_data = football_api("fixtures", {"id": fixture_id})
        fix   = (fix_data.get("response") or [{}])[0] if fix_data.get("response") else {}
        teams = fix.get("teams", {}) if fix else {}
        venue = fix.get("fixture", {}).get("venue", {}) if fix else {}
        home_logo = teams.get("home", {}).get("logo", "")
        away_logo = teams.get("away", {}).get("logo", "")
        round_info = fix.get("league", {}).get("round") if fix else None

        sb     = get_sb()
        result = sb.table("match_analyses").select("*").eq(
            "fixture_id", fixture_id).order(
            "created_at", desc=True).limit(1).execute()
        if result.data:
            analysis = result.data[0]
            # --- MĂDOSĂTVA: modellnevek anonimizĂĄlva, mielĹtt kimegy ---
            analysis = sanitize_analysis_for_public(analysis)
            return {
                "fixture_id": fixture_id,
                "analysis":   analysis,
                "fixture":    fix,
                "venue":      venue,
                "round":      round_info,
                "home_logo":  home_logo,
                "away_logo":  away_logo,
                "source":     "supabase",
            }

        # Ha nincs Supabase-elemzĂŠs, csak ĂŠlĹ adat (odds is bekerĂźl)
        if not fix:
            return {"error": "Not found"}
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
            "round":      round_info,
            "home_logo":  home_logo,
            "away_logo":  away_logo,
            "odds":       odds_list,
            "source":     "live_api"
        }
    except Exception as e:
        return {"error": str(e)}

# âââ ĂJ: JĂTĂKOS SZINTĹ° MECCS-STATISZTIKA ("Meccs embere") ââââââââââââââââââââ
@app.get("/api/match/{fixture_id}/player-stats")
@simple_cache(120)
def match_player_stats(fixture_id: int):
    """
    JĂĄtĂŠkosonkĂŠnti meccs-statisztika (ĂŠrtĂŠkelĂŠs, lĂśvĂŠsek, kulcspassz,
    pĂĄrharcok, driblizĂŠs). Csak lezĂĄrt vagy ĂŠppen zajlĂł meccsnĂŠl elĂŠrhetĹ.
    """
    try:
        data = football_api("fixtures/players", {"fixture": fixture_id})
        response = data.get("response", [])
        if not response:
            return {"fixture_id": fixture_id, "available": False, "teams": []}

        teams = []
        rated_players = []
        for team_block in response:
            team_name = team_block.get("team", {}).get("name", "")
            players = []
            for p in team_block.get("players", []):
                player_info = p.get("player", {}) or {}
                stats_list  = p.get("statistics") or [{}]
                s           = stats_list[0] or {}
                games    = s.get("games", {}) or {}
                shots    = s.get("shots", {}) or {}
                passes   = s.get("passes", {}) or {}
                duels    = s.get("duels", {}) or {}
                dribbles = s.get("dribbles", {}) or {}

                rating = None
                try:
                    rating = float(games.get("rating")) if games.get("rating") else None
                except Exception:
                    rating = None

                entry = {
                    "name":             player_info.get("name", ""),
                    "position":         games.get("position"),
                    "minutes":          games.get("minutes"),
                    "rating":           rating,
                    "shots_total":      shots.get("total"),
                    "shots_on":         shots.get("on"),
                    "key_passes":       passes.get("key"),
                    "duels_won":        duels.get("won"),
                    "dribbles_success": dribbles.get("success"),
                }
                players.append(entry)
                if rating:
                    rated_players.append({"team": team_name, **entry})
            teams.append({"team": team_name, "players": players})

        man_of_the_match = None
        if rated_players:
            man_of_the_match = max(rated_players, key=lambda x: x["rating"] or 0)

        return {
            "fixture_id":        fixture_id,
            "available":         True,
            "teams":             teams,
            "man_of_the_match":  man_of_the_match,
        }
    except Exception as e:
        return {"error": str(e)}

# âââ ĂJ: LIGA GĂLLĂVĹ / GĂLPASSZ LISTA ââââââââââââââââââââââââââââââââââââââââ
@app.get("/api/match/{fixture_id}/league-stats")
@simple_cache(21600)
def match_league_stats(fixture_id: int):
    """
    A meccs ligĂĄjĂĄnak gĂłllĂśvĹ- ĂŠs gĂłlpassz-listĂĄja (top 5), plusz a
    fordulĂł szĂĄma - jĂł kiegĂŠszĂ­tĂŠs a Standings fĂźl mellĂŠ.
    """
    try:
        fix_data = football_api("fixtures", {"id": fixture_id})
        if not fix_data.get("response"):
            return {"error": "Not found"}
        fix       = fix_data["response"][0]
        league_id = fix.get("league", {}).get("id")
        season    = fix.get("league", {}).get("season")
        round_info = fix.get("league", {}).get("round")
        if not league_id or not season:
            return {"error": "Missing league/season"}

        scorers_data = football_api("players/topscorers", {"league": league_id, "season": season})
        assists_data = football_api("players/topassists", {"league": league_id, "season": season})

        def parse_top(data, goals_key):
            out = []
            for item in data.get("response", [])[:5]:
                player = item.get("player", {}) or {}
                stats  = (item.get("statistics") or [{}])[0] or {}
                goals_obj = stats.get("goals", {}) or {}
                team = (stats.get("team", {}) or {}).get("name")
                out.append({
                    "player": player.get("name"),
                    "team":   team,
                    "value":  goals_obj.get(goals_key),
                })
            return out

        return {
            "fixture_id":  fixture_id,
            "round":       round_info,
            "top_scorers": parse_top(scorers_data, "total"),
            "top_assists": parse_top(assists_data, "assists"),
        }
    except Exception as e:
        return {"error": str(e)}

# âââ ĂJ: PIACI KONSZENZUS ĂSSZEVETĂS (API-Football sajĂĄt predikciĂłja) âââââââââ
@app.get("/api/match/{fixture_id}/market-consensus")
@simple_cache(21600)
def match_market_consensus(fixture_id: int):
    """
    Az API-Football sajĂĄt, piaci alapĂş predikciĂłja (%-os esĂŠly ĂŠs
    tanĂĄcs) - ez NEM a mi AI-nk, hanem egy kĂźlsĹ referenciapont, amivel
    ĂśsszevethetĹ a sajĂĄt elemzĂŠsĂźnk hitelessĂŠg-erĹsĂ­tĂŠs cĂŠljĂĄbĂłl.
    """
    try:
        data = football_api("predictions", {"fixture": fixture_id})
        response = data.get("response", [])
        if not response:
            return {"fixture_id": fixture_id, "available": False}
        pred    = response[0].get("predictions", {}) or {}
        percent = pred.get("percent", {}) or {}
        winner  = pred.get("winner", {}) or {}
        return {
            "fixture_id":       fixture_id,
            "available":        True,
            "market_percent": {
                "home": percent.get("home"),
                "draw": percent.get("draw"),
                "away": percent.get("away"),
            },
            "market_advice":    pred.get("advice"),
            "market_favorite":  winner.get("name"),
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/match/{fixture_id}/odds")
@simple_cache(60)
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

    # ĂJ: odds-mozgĂĄs rĂśgzĂ­tĂŠse ĂŠs lekĂŠrdezĂŠse (nyitĂł vs jelenlegi)
    movement = record_and_get_odds_movement(fixture_id, {"home": best_h, "draw": best_d, "away": best_a})

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
        },
        "movement": movement,
    }

# âââ ĂJ: CSAPAT SZEZON-STATISZTIKA VĂGPONT ââââââââââââââââââââââââââââââââââââ
@app.get("/api/match/{fixture_id}/team-stats")
@simple_cache(21600)
def match_team_stats(fixture_id: int):
    """
    MindkĂŠt csapat teljes szezonos statisztikĂĄja: gĂłlĂĄtlag hazai/vendĂŠg
    bontĂĄsban, clean sheet szĂĄm, gĂłlnĂŠlkĂźli meccsek, legjobb sorozat.
    Sokkal mĂŠlyebb elemzĂŠsi alap, mint a puszta liga-tabella.
    """
    try:
        fix_data = football_api("fixtures", {"id": fixture_id})
        if not fix_data.get("response"):
            return {"error": "Not found"}
        fix       = fix_data["response"][0]
        league_id = fix.get("league", {}).get("id")
        season    = fix.get("league", {}).get("season")
        home_id   = fix.get("teams", {}).get("home", {}).get("id")
        away_id   = fix.get("teams", {}).get("away", {}).get("id")
        if not league_id or not season:
            return {"error": "Missing league/season"}

        home_stats = get_team_season_stats(home_id, league_id, season)
        away_stats = get_team_season_stats(away_id, league_id, season)
        return {"fixture_id": fixture_id, "home": home_stats, "away": away_stats}
    except Exception as e:
        return {"error": str(e)}

# âââ ĂJ: RĂSZLETES SĂRĂLĂSEK/ELTILTĂSOK VĂGPONT âââââââââââââââââââââââââââââââ
@app.get("/api/match/{fixture_id}/injuries")
@simple_cache(21600)
def match_injuries(fixture_id: int):
    """
    NĂŠv szerinti sĂŠrĂźlt/eltiltott jĂĄtĂŠkos lista mindkĂŠt csapatnĂĄl,
    az ok megjelĂślĂŠsĂŠvel (sĂŠrĂźlĂŠs / eltiltĂĄs / kĂŠtsĂŠges).
    """
    try:
        data = football_api("injuries", {"fixture": fixture_id})
        response = data.get("response", [])
        players = []
        seen = set()
        for item in response:
            player_info = item.get("player", {}) or {}
            name = player_info.get("name", "")
            team = (item.get("team", {}) or {}).get("name", "")
            # Dedupe: az API-Sports.io nĂŠha kĂŠtszer adja vissza ugyanazt a
            # sĂŠrĂźlĂŠst - nĂŠv + csapat + ok alapjĂĄn szĹąrjĂźk ki a duplikĂĄtumot.
            reason = player_info.get("reason", "")
            dedupe_key = (name, team, reason)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            players.append({
                "player": name,
                "team":   team,
                "type":   player_info.get("type", ""),      # pl. "Missing Fixture", "Questionable"
                "reason": reason,                            # pl. "Knee Injury", "Suspended"
            })
        return {"fixture_id": fixture_id, "count": len(players), "players": players}
    except Exception as e:
        return {"error": str(e)}

# âââ ĂJ: KĂNNYĹ° SĂRĂLĂS-ĂSSZEGZĹ (tippkĂĄrtyĂĄhoz, gyors ikon-jelzĂŠshez) ââââââââ
@app.get("/api/match/{fixture_id}/injury-summary")
@simple_cache(21600)
def match_injury_summary(fixture_id: int):
    """
    TĂśmĂśr ĂśsszegzĂŠs a tippkĂĄrtyĂĄkhoz - csak annyi, amennyi egy
    figyelmeztetĹ ikonhoz/tooltiphez kell (nem a teljes lista).
    Ugyanazt a nyers API hĂ­vĂĄst hasznĂĄlja, mint a /injuries vĂŠgpont,
    de csak a lĂŠnyeget adja vissza, hogy a lista-nĂŠzeteknĂŠl gyors legyen.
    """
    try:
        data = football_api("injuries", {"fixture": fixture_id})
        response = data.get("response", [])
        seen = set()
        home_count = 0
        away_count = 0
        top_names = []
        for item in response:
            player_info = item.get("player", {}) or {}
            name   = player_info.get("name", "")
            team   = (item.get("team", {}) or {}).get("name", "")
            reason = player_info.get("reason", "")
            dedupe_key = (name, team, reason)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            if len(top_names) < 3:
                top_names.append(f"{name} ({team})")
        total = len(seen)
        return {
            "fixture_id":  fixture_id,
            "count":       total,
            "has_injuries": total > 0,
            "preview":     top_names,  # max 3 nĂŠv gyors tooltip-hez
        }
    except Exception as e:
        return {"error": str(e)}
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# âââ ĂJ: "HASONLĂ TIPPEK TELJESĂTMĂNYE" KOHORSZ-STATISZTIKA âââââââââââââââââââ
@app.get("/api/tips/cohort-stats")
@simple_cache(600)
def tips_cohort_stats(prediction: str, odds_min: float = 0, odds_max: float = 999):
    """
    Visszaadja, hogy a mĂşltban hasonlĂł jellemzĹjĹą (azonos predikciĂł-tĂ­pus,
    hasonlĂł odds-sĂĄv) lezĂĄrt tippek hogyan teljesĂ­tettek. Ezt a frontend
    a tippkĂĄrtyĂĄn "HasonlĂł tippek eddigi teljesĂ­tmĂŠnye" blokkhoz hĂ­vja.
    PĂŠlda: GET /api/tips/cohort-stats?prediction=draw&odds_min=3&odds_max=4
    """
    try:
        all_tips = fetch_all_tips(status_filter=["Win", "Lost"])
        cohort = [
            t for t in all_tips
            if t.get("prediction") == prediction
            and odds_min <= float(t.get("odds") or 0) <= odds_max
        ]
        if not cohort:
            return {
                "prediction": prediction, "odds_min": odds_min, "odds_max": odds_max,
                "count": 0, "win_rate": None, "message": "Nincs elĂŠg korĂĄbbi adat ehhez a kategĂłriĂĄhoz."
            }
        wins = sum(1 for t in cohort if t.get("result_status") == "Win")
        profit = sum(float(t.get("profit") or 0) for t in cohort)
        return {
            "prediction": prediction,
            "odds_min":   odds_min,
            "odds_max":   odds_max,
            "count":      len(cohort),
            "wins":       wins,
            "losses":     len(cohort) - wins,
            "win_rate":   round(wins / len(cohort) * 100, 1),
            "avg_profit": round(profit / len(cohort), 1),
        }
    except Exception as e:
        return {"error": str(e)}
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.get("/api/match/{fixture_id}/h2h")
@simple_cache(86400)
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

# âââ ĂJ: STATISZTIKĂK VĂGPONT (korĂĄbban hiĂĄnyzott!) âââââââââââââââââââââââââââ
@app.get("/api/match/{fixture_id}/stats")
@simple_cache(20)
def match_stats(fixture_id: int):
    """
    xG, forma, csapat-erĹ ĂŠs egyĂŠb bĹvĂ­tett statisztikĂĄk egy meccshez.
    Ha a meccs ĂŠlĹ vagy vĂŠget ĂŠrt, visszaadja a rĂŠszletes ĂŠlĹ statisztikĂĄt
    (lĂśvĂŠsek, birtoklĂĄs, szĂśglet, lapok) ĂŠs az esemĂŠny-naplĂłt is
    (gĂłlok, lapok, cserĂŠk percenkĂŠnti bontĂĄsban).
    """
    try:
        fix_data = football_api("fixtures", {"id": fixture_id})
        if not fix_data.get("response"):
            return {"error": "Not found"}
        fix    = fix_data["response"][0]
        status = fix.get("fixture", {}).get("status", {}).get("short", "")
        goals  = fix.get("goals", {})
        minute = fix.get("fixture", {}).get("status", {}).get("elapsed")

        # ĂlĹ vagy mĂĄr vĂŠget ĂŠrt meccsnĂŠl lekĂŠrjĂźk a rĂŠszletes statot ĂŠs esemĂŠnyeket
        live_stats   = None
        match_events = None
        if status in {"1H", "2H", "HT", "ET", "P", "FT", "AET", "PEN"}:
            live_stats   = get_live_stats_parsed(fixture_id)
            match_events = get_match_events(fixture_id)

        # KiegĂŠszĂ­tĹ adatok az elemzĂŠsbĹl (Supabase), ha van
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
                _debug_error = "res.data volt Ăźres (nem talĂĄlt sort ezzel a fixture_id-vel)"
        except Exception as _dbg_e:
            _debug_error = f"{type(_dbg_e).__name__}: {_dbg_e}"

        return {
            "fixture_id":     fixture_id,
            "status":         status,
            "minute":         minute,
            "score":          {"home": goals.get("home"), "away": goals.get("away")},
            "live_stats":     live_stats,
            "events":         match_events,
            "pre_match":      analysis_extra,
            "debug_error":    _debug_error,
            "note": None if (live_stats or analysis_extra) else "ĂlĹ statisztikĂĄk a meccs alatt elĂŠrhetĹk",
        }
    except Exception as e:
        return {"error": str(e)}

# âââ ĂJ: KEZDĹCSAPAT (LINEUP) VĂGPONT (korĂĄbban hiĂĄnyzott!) âââââââââââââââââââ
@app.get("/api/match/{fixture_id}/lineup")
@simple_cache(300)
def match_lineup(fixture_id: int):
    try:
        data = football_api("fixtures/lineups", {"fixture": fixture_id})
        response = data.get("response", [])
        if not response:
            return {
                "fixture_id": fixture_id,
                "available": False,
                "note": "KezdĹcsapat kb. 1 ĂłrĂĄval a meccs elĹtt jelenik meg",
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

# âââ ĂJ: LIGA TABELLA VĂGPONT (korĂĄbban hiĂĄnyzott!) âââââââââââââââââââââââââââ
@app.get("/api/match/{fixture_id}/standings")
@simple_cache(3600)
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
@simple_cache(15)
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
    """Live monitor aktuĂĄlis ĂĄllapota."""
    return _monitor_status

# âââ ĂJ: TELEGRAM ELITE HOZZĂFĂRĂS-KEZELĂS ââââââââââââââââââââââââââââââââââââ
# Egyedi, egyszer hasznĂĄlatos, lejĂĄrĂł meghĂ­vĂł linkek + automatikus eltĂĄvolĂ­tĂĄs
# lemondĂĄskor. Ez vĂĄltja fel a statikus t.me/dark_ai_tips linket, ami korĂĄbban
# azt jelentette, hogy egyszeri belĂŠpĂŠs utĂĄn valaki ĂśrĂśkre bent maradt.

@app.post("/api/telegram/create-invite")
def telegram_create_invite(app_user_id: str):
    """
    MeghĂ­vja az Elite elĹfizetĹt: 1x hasznĂĄlatos, 48 ĂłrĂĄn belĂźl lejĂĄrĂł
    linket generĂĄl. Ezt hĂ­vja Lovable, amikor valaki Elite-re fizet elĹ.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        return {"error": "Telegram nincs konfigurĂĄlva"}
    try:
        expire_ts = int(time.time()) + 48 * 3600  # 48 Ăłra
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createChatInviteLink",
            json={
                "chat_id": TELEGRAM_CHANNEL,
                "expire_date": expire_ts,
                "member_limit": 1,
                "name": f"elite-{app_user_id[:8]}",
            },
            timeout=10,
        )
        result = r.json()
        invite_link = result.get("result", {}).get("invite_link")
        if not invite_link:
            return {"error": "Nem sikerĂźlt linket generĂĄlni", "detail": result}

        sb = get_sb()
        sb.table("telegram_invites").insert({
            "app_user_id":  app_user_id,
            "invite_link":  invite_link,
            "used":         False,
        }).execute()
        return {"invite_link": invite_link, "expires_at": expire_ts}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/telegram/revoke-access")
def telegram_revoke_access(app_user_id: str):
    """
    KirĂşgja az adott elĹfizetĹt a Telegram csoportbĂłl, amikor lemondja
    az Elite elĹfizetĂŠst vagy lejĂĄr a fizetĂŠse. Ezt hĂ­vja Lovable a
    Stripe "subscription cancelled/expired" esemĂŠnyĂŠnĂŠl.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        return {"error": "Telegram nincs konfigurĂĄlva"}
    try:
        sb = get_sb()
        res = sb.table("telegram_members").select("*").eq(
            "app_user_id", app_user_id).order("joined_at", desc=True).limit(1).execute()
        if not res.data:
            return {"error": "Nincs ismert Telegram-tagsĂĄg ehhez a felhasznĂĄlĂłhoz"}

        telegram_user_id = res.data[0].get("telegram_user_id")

        # KirĂşgĂĄs, majd azonnali "unban", hogy kĂŠsĹbb egy Ăşj meghĂ­vĂłval
        # Ăşjra tudjon csatlakozni, ha Ăşjra elĹfizet.
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/banChatMember",
            json={"chat_id": TELEGRAM_CHANNEL, "user_id": telegram_user_id},
            timeout=10,
        )
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/unbanChatMember",
            json={"chat_id": TELEGRAM_CHANNEL, "user_id": telegram_user_id, "only_if_banned": True},
            timeout=10,
        )
        sb.table("telegram_members").delete().eq("app_user_id", app_user_id).execute()
        return {"status": "removed", "telegram_user_id": telegram_user_id}
    except Exception as e:
        return {"error": str(e)}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Ide kĂźldi a Telegram a csoport-esemĂŠnyeket (ki lĂŠpett be melyik
    meghĂ­vĂł linkkel). EbbĹl tudjuk meg, melyik Telegram-fiĂłk tartozik
    melyik elĹfizetĹhĂśz - enĂŠlkĂźl nem tudnĂĄnk kit kirĂşgni lemondĂĄskor.
    """
    try:
        update = await request.json()
        chat_member_update = update.get("chat_member")
        if not chat_member_update:
            return {"ok": True}

        new_member = chat_member_update.get("new_chat_member", {})
        status     = new_member.get("status")
        invite_link_obj = chat_member_update.get("invite_link", {}) or {}
        used_link  = invite_link_obj.get("invite_link")
        telegram_user_id = new_member.get("user", {}).get("id")

        if status == "member" and used_link and telegram_user_id:
            sb = get_sb()
            invite_res = sb.table("telegram_invites").select("*").eq(
                "invite_link", used_link).eq("used", False).limit(1).execute()
            if invite_res.data:
                app_user_id = invite_res.data[0]["app_user_id"]
                sb.table("telegram_invites").update({"used": True}).eq(
                    "invite_link", used_link).execute()
                sb.table("telegram_members").upsert({
                    "app_user_id":      app_user_id,
                    "telegram_user_id": telegram_user_id,
                }, on_conflict="app_user_id").execute()
        return {"ok": True}
    except Exception as e:
        log.error(f"Telegram webhook hiba: {e}")
        return {"ok": True}
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.get("/api/bankroll")
def bankroll():
    try:
        # MĂDOSĂTVA: lapozott lekĂŠrdezĂŠs, ugyanaz az ok, mint a /api/stats/public-nĂĄl.
        tips   = fetch_all_tips(status_filter=["Win","Lost"])
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
@simple_cache(300)
def arbitrage(date_str: str = ""):
    try:
        target = date_str or date.today().strftime("%Y-%m-%d")
        data   = football_api("fixtures", {"date": target, "status": "NS"})
        arbs   = []
        for fix in data.get("response",[])[:20]:
            fid     = fix.get("fixture",{}).get("id")
            home_nm = fix.get("teams",{}).get("home",{}).get("name","")
            away_nm = fix.get("teams",{}).get("away",{}).get("name","")
            league  = fix.get("league",{}).get("name","")
            odds_d  = football_api("odds", {"fixture": fid})
            # MĂDOSĂTVA: biztonsĂĄgos hozzĂĄfĂŠrĂŠs, ha a "response" Ăźres lista
            # (nincs elĂŠrhetĹ odds ehhez a meccshez) - korĂĄbban ez IndexError-t
            # dobott, ami az egĂŠsz vĂŠgpontot lefagyasztotta.
            odds_response = odds_d.get("response") or [{}]
            bh=bd=ba=0.0; nbh=nbd=nba=""
            for bk in (odds_response[0] or {}).get("bookmakers",[]):
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
    except Exception as e:
        return {"error": str(e), "date": target if 'target' in dir() else date_str, "count": 0, "opportunities": []}

# âââ LIVE MONITOR (hĂĄttĂŠrszĂĄl) ââââââââââââââââââââââââââââââââââââââââââââââââ
_monitor_status = {
    "running":      False,
    "last_cycle":   None,
    "api_calls":    0,
    "active_tips":  0,
    "alerts_sent":  0,
}
_sent_alerts = set()
_last_scores = {}
_last_decision_state = {}  # fixture_id -> utoljĂĄra kikĂźldĂśtt dĂśntĂŠsi szint
_last_daily_maintenance_date = None  # dĂĄtum, amikor utoljĂĄra lefutott a napi Telegram-karbantartĂĄs (tĂśrlĂŠs)
_last_daily_recap_date = None        # dĂĄtum, amikor utoljĂĄra lefutott a "tegnapi eredmĂŠnyek" ĂśsszesĂ­tĹ
_last_new_tip_check = 0              # timestamp, mikor nĂŠztĂźk utoljĂĄra az Ăşj tippeket

NEW_TIP_CHECK_INTERVAL_SEC = 120  # ennyi mĂĄsodpercenkĂŠnt nĂŠzzĂźk meg, van-e Ăşj, be nem jelentett tipp

TELEGRAM_RETENTION_DAYS = 2  # ennyi napig maradnak meg az egyedi Telegram Ăźzenetek


def _is_recommended_tip(t: dict) -> bool:
    """Ugyanaz a logika, mint a Lovable frontend szĹąrĹje:
    egy tipp akkor szĂĄmĂ­t 'tĂŠt ajĂĄnlĂĄsosnak', ha van Kelly-tĂŠt,
    value bet jelzĂŠs, vagy smart_pro elfogadĂĄs."""
    try:
        return bool(
            (t.get("rec_stake") and float(t.get("rec_stake") or 0) > 0) or
            (t.get("kelly_fraction") and float(t.get("kelly_fraction") or 0) > 0) or
            t.get("is_value_bet") is True or
            t.get("smart_pro") is True
        )
    except Exception:
        return False


def _summarize_tip_group(tips_subset):
    """KĂśzĂśs ĂśsszesĂ­tĹ logika: darabszĂĄm, gyĹzelem/vesztĂŠs, win rate, profit."""
    closed = [t for t in tips_subset if t.get("result_status") in ("Win", "Lost")]
    wins = sum(1 for t in closed if t.get("result_status") == "Win")
    profit = sum(float(t.get("profit") or 0) for t in closed)
    return {
        "total": len(tips_subset),
        "closed": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate": round(wins / max(len(closed), 1) * 100, 1),
        "profit": round(profit, 0),
    }


def build_daily_summary_message(target_date: str) -> str:
    """
    ĂsszeĂĄllĂ­t egy napi ĂśsszesĂ­tĹ Telegram Ăźzenetet a megadott napra
    (YYYY-MM-DD), kĂźlĂśn szekciĂłval a tĂŠt ajĂĄnlĂĄsos ĂŠs a tĂŠt ajĂĄnlĂĄs
    nĂŠlkĂźli tippekre. Ezt a tĂśrlĂŠs ELĹTT kĂźldjĂźk ki, arra a napra,
    aminek az Ăźzenetei ĂŠpp most vĂĄlnak rĂŠgivĂŠ.
    """
    try:
        # MĂDOSĂTVA: lapozott lekĂŠrdezĂŠs (lĂĄsd fetch_all_tips), hogy ez se
        # essen bele az 1000-es alapĂŠrtelmezett Supabase limitbe.
        all_tips = fetch_all_tips()
    except Exception as e:
        log.error(f"Napi ĂśsszesĂ­tĹ - tips lekĂŠrĂŠs hiba: {e}")
        return None

    day_tips = [t for t in all_tips if str(t.get("created_at", ""))[:10] == target_date]
    if not day_tips:
        return None

    recommended = [t for t in day_tips if _is_recommended_tip(t)]
    normal      = [t for t in day_tips if not _is_recommended_tip(t)]

    r = _summarize_tip_group(recommended)
    n = _summarize_tip_group(normal)

    msg  = f"đ <b>Daily Summary â {target_date}</b>\n\n"
    msg += f"đ <b>Stake-recommended tips</b> ({r['total']})\n"
    msg += f"  â {r['wins']} won / â {r['losses']} lost (win rate: {r['win_rate']}%)\n"
    msg += f"  đ° Total profit: {r['profit']:+,.0f} coin\n\n"
    msg += f"đ <b>Non-recommended tips</b> ({n['total']})\n"
    msg += f"  â {n['wins']} won / â {n['losses']} lost (win rate: {n['win_rate']}%)\n"
    msg += f"  đ° Total profit: {n['profit']:+,.0f} coin\n\n"
    msg += f"<i>This day's detailed live alerts will now be removed "
    msg += f"from the channel ({TELEGRAM_RETENTION_DAYS}-day retention policy).</i>"
    return msg


def send_yesterday_recap():
    """
    ĂJ: minden nap egyszer elkĂźldi a TEGNAPI nap eredmĂŠnyeit
    (tĂŠt ajĂĄnlĂĄsos / nem-ajĂĄnlĂĄsos bontĂĄsban) - ez FĂGGETLEN a
    2 napos tĂśrlĂŠsi logikĂĄtĂłl, csak informatĂ­v, semmit nem tĂśrĂśl.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    try:
        all_tips = fetch_all_tips()
    except Exception as e:
        log.error(f"Tegnapi ĂśsszesĂ­tĹ - tips lekĂŠrĂŠs hiba: {e}")
        return

    day_tips = [t for t in all_tips if str(t.get("created_at", ""))[:10] == yesterday]
    if not day_tips:
        return  # nem kĂźldĂźnk Ăźres Ăźzenetet, ha tegnap nem volt tipp

    recommended = [t for t in day_tips if _is_recommended_tip(t)]
    normal      = [t for t in day_tips if not _is_recommended_tip(t)]

    r = _summarize_tip_group(recommended)
    n = _summarize_tip_group(normal)

    msg  = f"đ <b>Yesterday's Results â {yesterday}</b>\n\n"
    msg += f"đ <b>Stake-recommended tips</b> ({r['total']})\n"
    msg += f"  â {r['wins']} won / â {r['losses']} lost (win rate: {r['win_rate']}%)\n"
    msg += f"  đ° Total profit: {r['profit']:+,.0f} coin\n\n"
    msg += f"đ <b>Non-recommended tips</b> ({n['total']})\n"
    msg += f"  â {n['wins']} won / â {n['losses']} lost (win rate: {n['win_rate']}%)\n"
    msg += f"  đ° Total profit: {n['profit']:+,.0f} coin"
    send_telegram(msg, category="yesterday_recap")


def check_and_notify_new_tips():
    """
    MegnĂŠzi, van-e olyan tipp a Supabase-ben, amirĹl mĂŠg nem kĂźldtĂźnk
    Telegram-ĂŠrtesĂ­tĂŠst (telegram_notified = false). Ha 1-3 Ăşj tipp
    van, mindegyikrĹl kĂźlĂśn, rĂŠszletes Ăźzenetet kĂźld. Ha ennĂŠl TĂBB
    (pl. egy nagyobb elemzĂŠsi kĂśr egyszerre 15+ meccset mentett),
    EGYETLEN ĂśsszefoglalĂł Ăźzenetbe gyĹąjti Ĺket - Ă­gy egyszerre sok Ăşj
    tipp sem ĂĄrasztja el a csatornĂĄt kĂźlĂśn-kĂźlĂśn Ăźzenetekkel.
    """
    try:
        sb = get_sb()
        result = sb.table("tips").select("*").eq(
            "telegram_notified", False
        ).order("created_at", desc=False).limit(50).execute()
        new_tips = result.data or []
    except Exception as e:
        log.error(f"Ăj tipp ellenĹrzĂŠs hiba: {e}")
        return

    if not new_tips:
        return

    pred_label_map = {"home": "Home", "draw": "Draw", "away": "Away"}

    def _tip_ids(tips_list):
        return [t.get("id") for t in tips_list]

    def _mark_notified(tips_list):
        ids = _tip_ids(tips_list)
        if ids:
            try:
                sb.table("tips").update({"telegram_notified": True}).in_("id", ids).execute()
            except Exception as e:
                log.error(f"BejelentettkĂŠnt jelĂślĂŠs hiba: {e}")

    NOTIFY_INDIVIDUAL_THRESHOLD = 3  # ennĂŠl kevesebb Ăşj tippnĂŠl mĂŠg egyenkĂŠnt kĂźldĂźnk

    if len(new_tips) <= NOTIFY_INDIVIDUAL_THRESHOLD:
        for tip in new_tips:
            try:
                pred_label = pred_label_map.get(tip.get("prediction"), tip.get("prediction", ""))
                recommended = _is_recommended_tip(tip)
                msg  = "đ <b>PREMIUM PICK</b>\n" if recommended else "â˝ <b>NEW DARK AI PICK</b>\n"
                msg += "ââââââââââââââââââ\n"
                msg += f"đ <b>{_tg(tip.get('home_team'))} â {_tg(tip.get('away_team'))}</b>\n"
                msg += f"đ {_tg(tip.get('league') or 'Unknown league')}\n\n"
                msg += f"đŻ <b>{_tg(pred_label)}</b>  â˘  Odds: <b>{float(tip.get('odds') or 0):.2f}</b>\n"
                msg += f"đ§  AI confidence: <b>{float(tip.get('confidence') or 0):.0f}%</b>"
                value_edge = float(tip.get('value_edge') or 0)
                if value_edge:
                    msg += f"\nđ Value edge: <b>+{value_edge:.1f}%</b>"
                if recommended:
                    msg += f"\nđ° Recommended stake: <b>{float(tip.get('rec_stake') or 0):,.0f} coin</b>"
                msg += "\n\n<i>For information only. 18+ Âˇ Gamble responsibly.</i>"
                send_telegram(
                    msg,
                    category="new_tip",
                    fixture_id=tip.get("fixture_id"),
                    buttons=[("đ VIEW FULL ANALYSIS", TIPS_PAGE_URL)],
                )
            except Exception as e:
                log.error(f"New tip alert error (id={tip.get('id')}): {e}")
        _mark_notified(new_tips)
    else:
        # Many new tips at once -> single digest message
        try:
            recommended = [t for t in new_tips if _is_recommended_tip(t)]
            observation = [t for t in new_tips if not _is_recommended_tip(t)]
            msg  = "â˝ <b>DARK AI STRATEGY</b>\n"
            msg += f"<b>{len(new_tips)} NEW PICKS</b>\n"
            msg += "ââââââââââââââââââ\n"
            msg += f"đ Stake recommended: <b>{len(recommended)}</b>\n"
            msg += f"đ Watchlist: <b>{len(observation)}</b>\n"

            def append_group(title, icon, rows, limit):
                nonlocal msg
                if not rows:
                    return
                msg += f"\n<b>{icon} {title}</b>\n\n"
                for idx, tip in enumerate(rows[:limit], 1):
                    pred_label = pred_label_map.get(tip.get("prediction"), tip.get("prediction", ""))
                    msg += (
                        f"{idx}. <b>{_tg(tip.get('home_team'))} â {_tg(tip.get('away_team'))}</b>\n"
                        f"   đŻ {_tg(pred_label)}  â˘  <b>{float(tip.get('odds') or 0):.2f}</b>\n"
                    )
                if len(rows) > limit:
                    msg += f"   <i>+{len(rows) - limit} more picks on the website</i>\n"

            append_group("STAKE-RECOMMENDED PICKS", "đ", recommended, 10)
            append_group("WATCHLIST", "đ", observation, 8)
            msg += "\nââââââââââââââââââ\n"
            msg += "<i>18+ Âˇ Gamble responsibly. Picks are for information only.</i>"
            send_telegram(
                msg,
                category="new_tip_digest",
                buttons=[("đ ALL PICKS & ANALYSIS", TIPS_PAGE_URL)],
            )
        except Exception as e:
            log.error(f"ĂsszesĂ­tett Ăşj tipp ĂŠrtesĂ­tĂŠs hiba: {e}")
        _mark_notified(new_tips)


def delete_old_telegram_messages(older_than_date: str):
    """
    TĂśrĂśl minden Telegram Ăźzenetet a csatornĂĄbĂłl, ami `older_than_date`
    (YYYY-MM-DD) napon vagy azelĹtt lett elkĂźldve, majd tĂśrli a
    hozzĂĄjuk tartozĂł nyilvĂĄntartĂĄsi sorokat is a Supabase-bĹl.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        return
    try:
        sb = get_sb()
        result = sb.table("telegram_messages").select("*").lte(
            "sent_at", f"{older_than_date}T23:59:59"
        ).execute()
        old_messages = result.data or []
    except Exception as e:
        log.error(f"telegram_messages lekĂŠrĂŠs hiba: {e}")
        return

    deleted_count = 0
    for row in old_messages:
        msg_id = row.get("message_id")
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
                json={"chat_id": TELEGRAM_CHANNEL, "message_id": msg_id},
                timeout=10
            )
            deleted_count += 1
        except Exception as e:
            log.error(f"Telegram Ăźzenet tĂśrlĂŠs hiba (message_id={msg_id}): {e}")
        try:
            sb.table("telegram_messages").delete().eq("id", row.get("id")).execute()
        except Exception:
            pass

    log.info(f"đ§š Telegram karbantartĂĄs: {deleted_count} rĂŠgi Ăźzenet tĂśrĂślve ({older_than_date} ĂŠs korĂĄbbi).")


def run_daily_telegram_maintenance():
    """
    Naponta egyszer lefutĂł karbantartĂĄs:
    1. ElkĂźldi az ĂśsszesĂ­tĹt arra a napra, ami most vĂĄlik "rĂŠgivĂŠ"
       (a megĹrzĂŠsi hatĂĄr napja).
    2. TĂśrli a csatornĂĄbĂłl az annĂĄl a napnĂĄl rĂŠgebbi egyedi Ăźzeneteket.
    """
    purge_date = (date.today() - timedelta(days=TELEGRAM_RETENTION_DAYS)).isoformat()

    summary_msg = build_daily_summary_message(purge_date)
    if summary_msg:
        send_telegram(summary_msg, category="daily_summary")

    delete_old_telegram_messages(purge_date)



def get_todays_tips_from_supabase() -> dict:
    """Mai pending tippek fixture_id â tip mapping."""
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
    """Gyors helyzetelemzĂŠs cash out javaslathoz."""
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
    # DĂśntetlen tippnĂŠl nincs egyetlen "sajĂĄt csapat", ezĂŠrt egy piros lap
    # ĂśnmagĂĄban nem jelent automatikus kiszĂĄllĂĄst.
    our_team = 0
    if pred_key in {"home", "away"}:
        our_team = teams.get(pred_key, {}).get("id", 0)
    our_red = [
        e for e in red_cards
        if our_team and e.get("team", {}).get("id", 0) == our_team
    ]

    if our_red:
        player = our_red[0].get('player',{}).get('name') or 'unknown player'
        return {"action":"CASH OUT NOW","urgency":"critical","reason":f"Red card for the selected team: {player}"}
    elif minute>=80 and not winning:
        return {"action":"CASH OUT NOW","urgency":"critical","reason":f"The pick is losing at minute {minute}"}
    elif minute>=70 and not winning:
        return {"action":"CONSIDER CASH OUT","urgency":"high","reason":f"The pick is not winning at minute {minute}"}
    elif winning and minute>=75:
        return {"action":"HOLD","urgency":"low","reason":f"The pick is winning at minute {minute}"}
    else:
        return {"action":"HOLD","urgency":"normal","reason":"The current match state does not require action"}


def _prediction_hu(prediction) -> str:
    return {
        "home": "Home win", "1": "Home win",
        "draw": "Draw", "x": "Draw",
        "away": "Away win", "2": "Away win",
    }.get(str(prediction or "").lower(), str(prediction or "â"))


def _urgency_view(situation: dict) -> tuple:
    return {
        "critical": ("đ¨", "CRITICAL DECISION ALERT"),
        "high":     ("â ď¸", "DECISION WARNING"),
        "low":      ("đ", "POSITION STABLE"),
        "normal":   ("â", "LIVE MATCH UPDATE"),
    }.get(situation.get("urgency"), ("âšď¸", "LIVE MATCH UPDATE"))


def _live_message(
    event_title: str, tip: dict, fixture_id: int, home_nm: str, away_nm: str,
    hg: int, ag: int, minute: int, situation: dict, event_detail: str = "",
) -> str:
    icon, status_title = _urgency_view(situation)
    odds = float(tip.get("odds") or 0)
    msg  = f"{icon} <b>{status_title}</b>\n"
    msg += "ââââââââââââââââââ\n"
    msg += f"đ´ <b>LIVE Âˇ {int(minute or 0)}'</b>\n"
    msg += f"đ <b>{_tg(home_nm)} {hg}â{ag} {_tg(away_nm)}</b>\n"
    if event_title:
        msg += f"\n{event_title}\n"
    if event_detail:
        msg += f"{_tg(event_detail)}\n"
    msg += f"\nđŻ Your pick: <b>{_tg(_prediction_hu(tip.get('prediction')))}</b>"
    if odds > 1:
        msg += f" @ <b>{odds:.2f}</b>"
    msg += f"\n\n{icon} Recommendation: <b>{_tg(situation.get('action'))}</b>\n"
    msg += f"đĄ {_tg(situation.get('reason'))}\n"
    msg += f"\n<i>Updated: {datetime.now().strftime('%H:%M')} Âˇ Automated decision support</i>"
    return msg

def live_monitor_loop():
    """HĂĄttĂŠrszĂĄlon fut Railway-en 24/7."""
    global _monitor_status
    log.info("đ´ Live Monitor indul Railway-en...")
    _monitor_status["running"] = True
    api_calls = 0

    while True:
        try:
            cycle_start = time.time()

            # âââ ĂJ: napi Telegram karbantartĂĄs (ĂśsszesĂ­tĹ + rĂŠgi Ăźzenetek tĂśrlĂŠse) âââ
            global _last_daily_maintenance_date
            today_iso = date.today().isoformat()
            if _last_daily_maintenance_date != today_iso:
                try:
                    run_daily_telegram_maintenance()
                except Exception as _maint_e:
                    log.error(f"Napi Telegram karbantartĂĄs hiba: {_maint_e}")
                _last_daily_maintenance_date = today_iso
            # ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

            # âââ ĂJ: "Tegnapi eredmĂŠnyek" napi ĂśsszesĂ­tĹ (nem tĂśrĂśl semmit) âââââââââââ
            global _last_daily_recap_date
            if _last_daily_recap_date != today_iso:
                try:
                    send_yesterday_recap()
                except Exception as _recap_e:
                    log.error(f"Tegnapi ĂśsszesĂ­tĹ hiba: {_recap_e}")
                _last_daily_recap_date = today_iso
            # ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

            # âââ ĂJ: "Ăj tipp" azonnali ĂŠrtesĂ­tĂŠs ellenĹrzĂŠse âââââââââââââââââââââââââ
            global _last_new_tip_check
            now_ts = time.time()
            if now_ts - _last_new_tip_check >= NEW_TIP_CHECK_INTERVAL_SEC:
                try:
                    check_and_notify_new_tips()
                except Exception as _tip_e:
                    log.error(f"Ăj tipp ĂŠrtesĂ­tĂŠs ellenĹrzĂŠs hiba: {_tip_e}")
                _last_new_tip_check = now_ts
            # ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

            today_tips  = get_todays_tips_from_supabase()
            _monitor_status["active_tips"] = len(today_tips)

            if not today_tips:
                _monitor_status["last_cycle"] = str(datetime.now())
                time.sleep(120)  # 2 perc ha nincs tipp
                continue

            # 1 hĂ­vĂĄs az Ăśsszes ĂŠlĹ meccshez
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

                # FONTOS: minden meccsnĂŠl, minden ciklusban Ăşjra kell szĂĄmolni.
                # KorĂĄbban ez csak eredmĂŠnyvĂĄltozĂĄskor tĂśrtĂŠnt, ezĂŠrt a cash-out
                # Ăźzenet ĂĄtvehette az elĹzĹ meccs tippjĂŠt ĂŠs oddsĂĄt.
                pred      = tip.get("prediction", "")
                orig_odds = float(tip.get("odds") or 0)

                # ĂllĂĄs vĂĄltozĂĄs detektĂĄlĂĄsa
                last_score = _last_scores.get(fid, (-1,-1))
                if (hg, ag) != last_score:
                    # Az API idĹnkĂŠnt egy rĂŠgebbi snapshotot ad vissza. Ne kĂźldjĂźnk
                    # olyan riasztĂĄst, amelyben az ĂśsszgĂłlszĂĄm visszafelĂŠ vĂĄltozik.
                    if last_score != (-1, -1) and (hg + ag) < sum(last_score):
                        log.warning(
                            f"Elavult ĂŠlĹ ĂĄllĂĄs kihagyva ({fid}): "
                            f"{last_score[0]}-{last_score[1]} -> {hg}-{ag}"
                        )
                        continue
                    _last_scores[fid] = (hg, ag)

                    # Railway deploy/restart utĂĄn az elsĹ lĂĄtott ĂĄllĂĄs csak
                    # kiindulĂĄsi pont. Ne kĂźldjĂźk ki Ăşjra a meccs korĂĄbbi gĂłljĂĄt.
                    # A kĂśvetkezĹ ciklustĂłl minden valĂłdi vĂĄltozĂĄs riasztĂĄst kap.
                    if last_score == (-1, -1):
                        continue

                    # Events lekĂŠrĂŠse ĂĄllĂĄsvĂĄltozĂĄskor
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

                    # LegĂşjabb esemĂŠny
                    goals_ev = [e for e in events if e["type"]=="Goal"]
                    red_ev   = [e for e in events if "Red Card" in e.get("detail","")]

                    situation = analyze_situation(tip, fixture, events)

                    # GĂłl ĂŠrtesĂ­tĹ
                    if goals_ev:
                        last_g  = max(goals_ev, key=lambda e: e.get("minute", 0) or 0)
                        alert_k = f"{fid}_goal_{hg}_{ag}"
                        if alert_k not in _sent_alerts:
                            _sent_alerts.add(alert_k)
                            scorer = last_g['player']['name'] or "Scorer unavailable"
                            event_detail = f"{last_g['team']['name']}: {scorer}"
                            msg = _live_message(
                                "â˝ <b>GOAL</b>", tip, fid, home_nm, away_nm,
                                hg, ag, minute, situation, event_detail,
                            )
                            send_telegram(
                                msg, category="live_goal", fixture_id=fid,
                                buttons=[("đ PICKS & ANALYSIS", TIPS_PAGE_URL)],
                            )
                            _monitor_status["alerts_sent"] = _monitor_status.get("alerts_sent",0)+1
                            log.info(f"đ Goal alert: {home_nm} {hg}-{ag} {away_nm}")

                    # Piros lap ĂŠrtesĂ­tĹ
                    if red_ev:
                        for rev in red_ev:
                            alert_k = f"{fid}_red_{rev['minute']}_{rev['player']['name']}"
                            if alert_k not in _sent_alerts:
                                _sent_alerts.add(alert_k)
                                player = rev['player']['name'] or "Player unavailable"
                                event_detail = f"{rev['team']['name']}: {player}"
                                msg = _live_message(
                                    f"đĽ <b>RED CARD Âˇ {rev['minute']}'</b>",
                                    tip, fid, home_nm, away_nm, hg, ag, minute,
                                    situation, event_detail,
                                )
                                send_telegram(
                                    msg, category="live_red_card", fixture_id=fid,
                                    buttons=[("đ PICKS & ANALYSIS", TIPS_PAGE_URL)],
                                )
                                _monitor_status["alerts_sent"] = _monitor_status.get("alerts_sent",0)+1

                # DĂśntĂŠstĂĄmogatĂł jelzĂŠs: ugyanazt a szintet meccsenkĂŠnt csak egyszer
                # kĂźldjĂźk. Ăgy 70' kĂśrĂźl lehet egy figyelmeztetĂŠs, 80' utĂĄn pedig
                # legfeljebb egy kritikus jelzĂŠs, nem kĂźlĂśn Ăźzenet 80/85/90 percnĂŠl.
                sit = analyze_situation(tip, fixture, [])
                previous_decision = _last_decision_state.get(fid)
                if sit["urgency"] in {"high", "critical"} and previous_decision != sit["urgency"]:
                    msg = _live_message(
                        "", tip, fid, home_nm, away_nm, hg, ag, minute, sit,
                    )
                    send_telegram(
                        msg, category="live_decision", fixture_id=fid,
                        buttons=[("đ PICKS & ANALYSIS", TIPS_PAGE_URL)],
                    )
                    _monitor_status["alerts_sent"] = _monitor_status.get("alerts_sent",0)+1
                _last_decision_state[fid] = sit["urgency"]

                # Meccs vĂŠge
                if status in {"FT","AET","PEN"}:
                    alert_k = f"{fid}_final"
                    if alert_k not in _sent_alerts:
                        _sent_alerts.add(alert_k)
                        pred_map = {"home":"home","1":"home","draw":"draw","x":"draw","away":"away","2":"away"}
                        pk = pred_map.get(tip.get("prediction","").lower(),"home")
                        won = (pk=="home" and hg>ag) or (pk=="away" and ag>hg) or (pk=="draw" and hg==ag)
                        result_icon = "â" if won else "â"
                        result_text = "WON" if won else "LOST"
                        msg  = "đ <b>FULL TIME</b>\n"
                        msg += "ââââââââââââââââââ\n"
                        msg += f"đ <b>{_tg(home_nm)} {hg}â{ag} {_tg(away_nm)}</b>\n\n"
                        msg += f"đŻ Your pick: <b>{_tg(_prediction_hu(pred))}</b>"
                        if orig_odds > 1:
                            msg += f" @ <b>{orig_odds:.2f}</b>"
                        msg += f"\n{result_icon} Result: <b>{result_text}</b>"
                        send_telegram(
                            msg, category="live_final", fixture_id=fid,
                            buttons=[("đ VIEW RESULTS", TIPS_PAGE_URL)],
                        )
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

# âââ STARTUP ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
@app.on_event("startup")
async def startup_event():
    """FastAPI indulĂĄskor elindĂ­tja a live monitort hĂĄttĂŠrszĂĄlon."""
    if FOOTBALL_API_KEY:
        t = threading.Thread(target=live_monitor_loop, daemon=True)
        t.start()
        log.info("â Live Monitor hĂĄttĂŠrszĂĄl elindĂ­tva")
    else:
        log.warning("â ď¸ FOOTBALL_API_KEY hiĂĄnyzik â Live Monitor nem indul!")

# âââ ĂJ: uvicorn indulĂł parancs (korĂĄbban hiĂĄnyzott!) âââââââââââââââââââââââââ
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
