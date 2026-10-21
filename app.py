"""
Reset Plate — an agent that finds healthy food you'll actually love, powered by Qloo's taste graph.

How the agent works (perceive → plan → act → explain):
  1. RESOLVE  each thing the user loves (a dish, a restaurant, a singer, a movie) into Qloo entities
  2. EXPAND   those tastes through Qloo Insights into restaurants in the user's city
  3. FILTER   with simple, sustainable "Reset" rules (one balanced plate, kitchen closes at night)
  4. PLAN     a 7-day plan that mixes the best healthy-leaning matches, and explains every pick

Run:
  pip install flask requests
  export QLOO_API_KEY=...            # without a key it runs in DEMO mode with sample data
  python3 app.py                     # http://0.0.0.0:8080
"""
import html
import os
import re

import requests
from flask import Flask, request

QLOO_BASE = os.environ.get("QLOO_BASE", "https://hackathon.api.qloo.com")
QLOO_KEY = os.environ.get("QLOO_API_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))

SEARCH_TYPES = ["urn:entity:place", "urn:entity:artist", "urn:entity:movie", "urn:entity:book",
                "urn:entity:tv_show", "urn:entity:brand"]

HEALTHY_WORDS = {
    "salad": 3, "healthy": 4, "vegetarian": 3, "vegan": 3, "grill": 2, "grilled": 2, "seafood": 2,
    "fish": 2, "poke": 3, "mediterranean": 3, "greek": 2, "japanese": 2, "sushi": 2, "peruvian": 2,
    "ceviche": 3, "juice": 2, "smoothie": 2, "organic": 3, "farm": 2, "fresh": 2, "bowl": 2,
    "vegetable": 2, "lebanese": 2, "thai": 1, "vietnamese": 2, "soup": 1, "breakfast": 1,
}
HEAVY_WORDS = {"fried": -2, "buffet": -3, "fast food": -3, "burger": -2, "pizza": -1, "dessert": -2,
               "donut": -3, "bakery": -1, "bar": -1, "pub": -2, "wings": -2, "ice cream": -2}

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

app = Flask(__name__)


# ───────────────────────────── Qloo client ─────────────────────────────

def qloo(path, params):
    r = requests.get(QLOO_BASE + path, params=params,
                     headers={"X-Api-Key": QLOO_KEY, "accept": "application/json"}, timeout=25)
    r.raise_for_status()
    return r.json()


def entities_of(data):
    res = data.get("results", data)
    if isinstance(res, dict):
        return res.get("entities", [])
    return res if isinstance(res, list) else []


def resolve(term):
    """Turn a free-text taste ('mangu', 'Juan Luis Guerra', 'Rocky') into a Qloo entity."""
    data = qloo("/search", {"query": term, "types": ",".join(SEARCH_TYPES), "take": 1})
    ents = entities_of(data)
    return ents[0] if ents else None


def recommend_places(entity_ids, city, take=20):
    params = {"filter.type": "urn:entity:place", "filter.location.query": city,
              "filter.tags": "urn:tag:genre:place:restaurant", "take": take}
    if entity_ids:
        params["signal.interests.entities"] = ",".join(entity_ids)
    return entities_of(qloo("/v2/insights", params))


# ───────────────────────────── the agent ─────────────────────────────

def text_of(place):
    props = place.get("properties") or {}
    parts = [place.get("name", ""), props.get("description", "") or ""]
    parts += [t.get("name", "") for t in (place.get("tags") or []) if isinstance(t, dict)]
    parts += [str(k) for k in (props.get("keywords") or []) if isinstance(k, (str, dict))]
    return " ".join(p if isinstance(p, str) else str(p) for p in parts).lower()


def health_score(place):
    t = text_of(place)
    score, why = 0, []
    for w, pts in {**HEALTHY_WORDS, **HEAVY_WORDS}.items():
        if re.search(r"\b" + re.escape(w) + r"\b", t):
            score += pts
            if pts > 0:
                why.append(w)
    return score, why


def taste_score(place):
    q = place.get("query") or {}
    for k in ("affinity", "score"):
        if isinstance(q.get(k), (int, float)):
            return float(q[k])
    return 0.5


def plate_tip(why):
    if any(w in why for w in ("salad", "vegetable", "bowl", "vegan", "vegetarian")):
        return "Build the plate around the vegetables; add one palm of protein."
    if any(w in why for w in ("fish", "seafood", "sushi", "poke", "ceviche")):
        return "Fish or seafood + vegetables. Skip the second side of rice."
    if any(w in why for w in ("grill", "grilled")):
        return "Grilled protein, half the plate vegetables, a quarter rice or plantain."
    return "One plate: ½ vegetables, ¼ protein, ¼ rice/plantain. No seconds for 15 min."


def run_agent(loves, city):
    log, resolved = [], []
    for term in loves:
        try:
            e = resolve(term)
        except Exception as ex:
            log.append(f"Could not look up “{term}” ({ex.__class__.__name__}).")
            continue
        if e:
            resolved.append(e)
            log.append(f"Understood “{term}” as {e.get('name')} ({(e.get('types') or [e.get('type', '')])[0]}).")
        else:
            log.append(f"No match for “{term}” — skipped.")
    ids = [e["entity_id"] for e in resolved if e.get("entity_id")]
    places = recommend_places(ids, city)
    log.append(f"Qloo returned {len(places)} restaurants in {city} that fit those tastes.")

    ranked = []
    for p in places:
        h, why = health_score(p)
        ranked.append(dict(place=p, health=h, why=why, taste=taste_score(p),
                           total=taste_score(p) * 10 + h))
    healthy = [r for r in ranked if r["health"] > 0] or ranked
    healthy.sort(key=lambda r: -r["total"])
    log.append(f"Kept {len(healthy)} that pass the Reset rules (healthy-leaning, not buffets or fast food).")

    plan = []
    for i, day in enumerate(DAYS):
        if not healthy:
            break
        if day in ("Wednesday", "Sunday"):
            plan.append(dict(day=day, home=True))
            continue
        r = healthy[i % len(healthy)]
        plan.append(dict(day=day, home=False, **r))
    return dict(log=log, resolved=resolved, plan=plan, top=healthy[:8])


# ───────────────────────────── demo data (no key yet) ─────────────────────────────

def demo(loves, city):
    fake = [
        ("Verde Bowl", "Fresh bowls, salads and grilled chicken", ["healthy", "salad", "bowl"]),
        ("Mar y Limón", "Ceviche and grilled fish, Peruvian style", ["peruvian", "ceviche", "seafood"]),
        ("Sakura Zen", "Sushi and Japanese soups", ["japanese", "sushi"]),
        ("La Parrilla Criolla", "Dominican grill, grilled meats and vegetables", ["grill", "dominican"]),
        ("Olivo", "Mediterranean and Greek plates", ["mediterranean", "greek"]),
        ("Jugo Vivo", "Fresh juices, smoothies and breakfast", ["juice", "smoothie", "breakfast"]),
    ]
    places = [dict(name=n, entity_id=f"demo{i}", properties=dict(description=d,
                   address=f"{city}"), tags=[dict(name=t) for t in tags], query=dict(affinity=0.9 - i * 0.05))
              for i, (n, d, tags) in enumerate(fake)]
    global recommend_places, resolve
    orig_r, orig_s = recommend_places, resolve
    recommend_places = lambda ids, c, take=20: places
    resolve = lambda term: dict(name=term.title(), entity_id="demo", types=["demo"])
    try:
        out = run_agent(loves, city)
    finally:
        recommend_places, resolve = orig_r, orig_s
    out["log"].insert(0, "DEMO MODE — no Qloo API key set yet, using sample restaurants.")
    return out


# ───────────────────────────── web page ─────────────────────────────

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Reset Plate</title>
<style>
:root{{--bg:#0f1720;--card:#16212c;--ink:#f2eee6;--mut:#a9a294;--acc:#e0a33a;--ok:#4fb3a9}}
@media (prefers-color-scheme:light){{:root{{--bg:#f7f4ee;--card:#fff;--ink:#1d2329;--mut:#5e6670;--acc:#b5761b;--ok:#2c8a80}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}}
main{{max-width:880px;margin:auto;padding:28px 16px 60px}}h1{{font-size:40px;margin:0}}h1 span{{color:var(--acc)}}
.sub{{color:var(--mut);margin:4px 0 22px}}form,.card{{background:var(--card);border-radius:14px;padding:18px;margin-bottom:16px}}
label{{display:block;font-weight:600;margin:8px 0 4px}}input{{width:100%;padding:11px;border-radius:9px;border:1px solid #3a4652;background:transparent;color:var(--ink);font-size:16px}}
button{{margin-top:14px;background:var(--acc);color:#111;border:0;border-radius:9px;padding:12px 20px;font-weight:700;font-size:16px;cursor:pointer}}
.day{{display:grid;grid-template-columns:110px 1fr;gap:12px;padding:12px 0;border-top:1px solid #2a3540}}
.day b{{color:var(--acc)}}.why{{color:var(--ok);font-size:14px}}.tip{{color:var(--mut);font-size:14px}}
ul.log{{color:var(--mut);font-size:14px;padding-left:18px}}.rules li{{margin:4px 0}}
</style></head><body><main>
<h1>Reset <span>Plate</span></h1>
<p class=sub>Eat healthier without giving up the food — and the culture — you love. Powered by Qloo's taste graph.</p>
<form method=post>
<label>Things you love (food, places, music, movies — comma separated)</label>
<input name=loves value="{loves}" placeholder="mangú, sushi, Juan Luis Guerra, Rocky">
<label>Your city</label><input name=city value="{city}" placeholder="Santo Domingo">
<button>Build my week</button></form>
{result}
<div class=card><b>The Reset rules</b><ul class=rules>
<li>🍽️ One plate: ½ vegetables, ¼ protein, ¼ rice or plantain. No seconds for 15 minutes.</li>
<li>🌙 The kitchen closes at night. Brush your teeth after the last meal.</li>
<li>🚶 10 minutes walking after the biggest meal.</li></ul></div>
</main></body></html>"""


@app.route("/", methods=["GET", "POST"])
def home():
    loves_raw = request.form.get("loves", "")
    city = request.form.get("city", "") or "Santo Domingo"
    result = ""
    if request.method == "POST" and loves_raw.strip():
        loves = [x.strip() for x in loves_raw.split(",") if x.strip()][:6]
        try:
            out = run_agent(loves, city) if QLOO_KEY else demo(loves, city)
            rows = []
            for d in out["plan"]:
                if d["home"]:
                    rows.append(f"<div class=day><b>{d['day']}</b><div>🏠 Home-cooked day — "
                                f"same one-plate rule.</div></div>")
                    continue
                p = d["place"]
                addr = html.escape(str((p.get("properties") or {}).get("address", "")))
                why = ", ".join(d["why"]) or "strong taste match"
                rows.append(f"<div class=day><b>{d['day']}</b><div><b>{html.escape(p.get('name', ''))}</b>"
                            f"<div class=tip>{addr}</div><div class=why>Why: matches your taste · {html.escape(why)}</div>"
                            f"<div class=tip>Plate: {html.escape(plate_tip(d['why']))}</div></div></div>")
            logs = "".join(f"<li>{html.escape(l)}</li>" for l in out["log"])
            result = (f"<div class=card><b>Your week</b>{''.join(rows) or '<p>No matches found — try other tastes.</p>'}</div>"
                      f"<div class=card><b>How the agent decided</b><ul class=log>{logs}</ul></div>")
        except Exception as ex:
            result = f"<div class=card>Something went wrong talking to Qloo: {html.escape(str(ex))}</div>"
    return PAGE.format(loves=html.escape(loves_raw), city=html.escape(city), result=result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
