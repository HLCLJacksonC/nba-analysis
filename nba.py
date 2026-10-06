#!/usr/bin/env python3
"""Daily NBA log + Elo ratings. Data: ESPN public scoreboard API (no key, no deps).

  python3 nba.py              # fetch yesterday (US Eastern), update games.csv + README.md
  python3 nba.py 20261003     # fetch a specific date
  python3 nba.py --since 20260930
  python3 nba.py --test
"""
import csv, json, sys, tempfile, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
CSV = HERE / "games.csv"
SNAP = HERE / "elo_history.csv"
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"
FIELDS = ["game_id", "date", "season_type", "home", "home_pts", "away", "away_pts"]
SNAP_FIELDS = ["date", "team", "elo", "w", "l"]
ET = timezone(timedelta(hours=-5))  # ponytail: fixed offset, fine since we only derive a game date

K, HOME_EDGE, BASE = 20.0, 100.0, 1500.0


def fetch(yyyymmdd):
    """Final games on an NBA date, as CSV rows."""
    req = urllib.request.Request(API.format(yyyymmdd), headers={"User-Agent": "nba-analysis"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    out = []
    for e in data.get("events", []):
        c = e["competitions"][0]
        if c["status"]["type"]["name"] != "STATUS_FINAL":
            continue
        side = {t["homeAway"]: t for t in c["competitors"]}
        out.append({
            "game_id": e["id"],
            "date": yyyymmdd,
            "season_type": e.get("season", {}).get("slug", "?"),
            "home": side["home"]["team"]["abbreviation"],
            "home_pts": int(side["home"]["score"]),
            "away": side["away"]["team"]["abbreviation"],
            "away_pts": int(side["away"]["score"]),
        })
    return out


def load():
    if not CSV.exists():
        return []
    with CSV.open() as f:
        return [{**r, "home_pts": int(r["home_pts"]), "away_pts": int(r["away_pts"])}
                for r in csv.DictReader(f)]


def save(rows):
    rows.sort(key=lambda r: (r["date"], r["game_id"]))
    with CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


def snapshot(rows, today):
    """Append today's ratings to the history file (idempotent per day). Returns all history."""
    hist = []
    if SNAP.exists():
        with SNAP.open() as f:
            hist = [r for r in csv.DictReader(f) if r["date"] != today]
    r, t = elo(rows), table(rows)
    hist += [{"date": today, "team": k, "elo": f"{r[k]:.1f}", "w": t[k]["w"], "l": t[k]["l"]}
             for k in sorted(r)]
    hist.sort(key=lambda x: (x["date"], x["team"]))
    with SNAP.open("w", newline="") as f:
        w = csv.DictWriter(f, SNAP_FIELDS)
        w.writeheader()
        w.writerows(hist)
    return hist


def deltas(hist, today, days=7):
    """Elo change per team vs the newest snapshot at least `days` old (empty early on)."""
    if not hist:
        return {}
    cutoff = (datetime.strptime(today, "%Y%m%d") - timedelta(days)).strftime("%Y%m%d")
    old = [d for d in {h["date"] for h in hist} if d <= cutoff]
    if not old:
        return {}
    then = max(old)
    was = {h["team"]: float(h["elo"]) for h in hist if h["date"] == then}
    now = {h["team"]: float(h["elo"]) for h in hist if h["date"] == today}
    return {k: now[k] - was[k] for k in now if k in was}


def elo(rows):
    """Elo after each game, chronologically. Returns {team: rating}."""
    r = {}
    for g in rows:
        h, a = r.setdefault(g["home"], BASE), r.setdefault(g["away"], BASE)
        exp_h = 1 / (1 + 10 ** ((a - h - HOME_EDGE) / 400))
        won_h = 1.0 if g["home_pts"] > g["away_pts"] else 0.0
        r[g["home"]] = h + K * (won_h - exp_h)
        r[g["away"]] = a + K * (exp_h - won_h)
    return r


def table(rows):
    t = {}
    for g in rows:
        for me, them in ((g["home"], g["away"]), (g["away"], g["home"])):
            t.setdefault(me, {"w": 0, "l": 0, "pf": 0, "pa": 0})
        hp, ap = g["home_pts"], g["away_pts"]
        t[g["home"]]["w" if hp > ap else "l"] += 1
        t[g["away"]]["w" if ap > hp else "l"] += 1
        t[g["home"]]["pf"] += hp; t[g["home"]]["pa"] += ap
        t[g["away"]]["pf"] += ap; t[g["away"]]["pa"] += hp
    return t


def report(all_rows, hist=(), today=""):
    reg = [g for g in all_rows if g["season_type"] == "regular-season"]
    rows = reg or all_rows
    label = "Regular season" if reg else "Preseason"
    t, r = table(rows), elo(rows)
    rank = sorted(t, key=lambda k: -r[k])
    d = deltas(hist, today)

    out = ["# NBA Elo Tracker", "",
           f"_{label} · {len(rows)} games through {rows[-1]['date'] if rows else 'n/a'} · "
           f"updated {datetime.now(timezone.utc):%Y-%m-%d %H:%MZ}_", "",
           "Elo: K=20, home-court +100, everyone starts at 1500. "
           "Data from ESPN's public scoreboard API. "
           "Game log: [games.csv](games.csv) · daily rating snapshots: "
           "[elo_history.csv](elo_history.csv).", "",
           "| # | Team | Elo | 7d | W-L | PPG | OPP PPG | Diff |",
           "|--:|:--|--:|--:|:--:|--:|--:|--:|"]
    for i, k in enumerate(rank, 1):
        s, n = t[k], t[k]["w"] + t[k]["l"]
        out.append(f"| {i} | {k} | {r[k]:.0f} | {d[k]:+.0f}" if k in d else
                   f"| {i} | {k} | {r[k]:.0f} | –")
        out[-1] += (f" | {s['w']}-{s['l']} | {s['pf']/n:.1f} | "
                    f"{s['pa']/n:.1f} | {(s['pf']-s['pa'])/n:+.1f} |")
    if d:
        mv = sorted(d.items(), key=lambda kv: -kv[1])
        hot = ", ".join(f"{k} {v:+.0f}" for k, v in mv[:3] if v > 0) or "nobody"
        cold = ", ".join(f"{k} {v:+.0f}" for k, v in mv[-3:] if v < 0) or "nobody"
        out += ["", f"**Rising (7d):** {hot} · **Falling (7d):** {cold}"]

    out += ["", "## Last 10 games", "", "| Date | Matchup | Score | Margin |", "|:--|:--|:--|--:|"]
    for g in all_rows[-10:][::-1]:
        win, lose = ((g["home"], g["away"]) if g["home_pts"] > g["away_pts"]
                     else (g["away"], g["home"]))
        m = abs(g["home_pts"] - g["away_pts"])
        out.append(f"| {g['date']} | {g['away']} @ {g['home']} | "
                   f"{g['away_pts']}-{g['home_pts']} | {win} by {m} |")
    return "\n".join(out) + "\n"


def test():
    rows = [{"game_id": "1", "date": "20260101", "season_type": "regular-season",
             "home": "AAA", "home_pts": 110, "away": "BBB", "away_pts": 100}]
    r = elo(rows)
    assert r["AAA"] > BASE > r["BBB"], r
    assert abs((r["AAA"] - BASE) + (r["BBB"] - BASE)) < 1e-9, "elo must be zero-sum"
    assert r["AAA"] - BASE < K / 2, "beating a weaker-by-home-edge team earns little"
    t = table(rows)
    assert t["AAA"] == {"w": 1, "l": 0, "pf": 110, "pa": 100}, t
    assert t["BBB"] == {"w": 0, "l": 1, "pf": 100, "pa": 110}, t
    assert "AAA" in report(rows) and "BBB by" not in report(rows)

    global SNAP
    SNAP, real = Path(tempfile.mkdtemp()) / "h.csv", SNAP
    try:
        h1 = snapshot(rows, "20260101")
        h2 = snapshot(rows, "20260101")
        assert h1 == h2 and len(h2) == 2, "same-day re-run must not duplicate"
        assert deltas(h2, "20260101") == {}, "no 7-day-old snapshot yet"
        h3 = snapshot(rows, "20260110")
        assert len(h3) == 4, h3
        assert deltas(h3, "20260110") == {"AAA": 0.0, "BBB": 0.0}, "no new games, no drift"
        assert "7d" in report(rows, h3, "20260110")
    finally:
        SNAP = real
    live = fetch("20261003")
    assert live and all(set(g) == set(FIELDS) for g in live), live
    print("ok")


def main(argv):
    if "--test" in argv:
        return test()
    today = datetime.now(ET).date()
    if "--since" in argv:
        start = datetime.strptime(argv[argv.index("--since") + 1], "%Y%m%d").date()
        days = [start + timedelta(d) for d in range((today - start).days + 1)]
    elif argv:
        days = [datetime.strptime(argv[0], "%Y%m%d").date()]
    else:
        days = [today - timedelta(1)]

    rows = load()
    seen = {g["game_id"] for g in rows}
    new = [g for d in days for g in fetch(d.strftime("%Y%m%d")) if g["game_id"] not in seen]
    rows += new
    if not rows:
        print("no final games yet")
        return
    save(rows)
    rows = load()
    stamp = today.strftime("%Y%m%d")
    reg = [g for g in rows if g["season_type"] == "regular-season"]
    hist = snapshot(reg or rows, stamp)
    (HERE / "README.md").write_text(report(rows, hist, stamp))
    print(f"+{len(new)} games ({len(rows)} total)")
    for g in new:
        print(f"  {g['date']} {g['away']} {g['away_pts']} @ {g['home']} {g['home_pts']}")


if __name__ == "__main__":
    main(sys.argv[1:])
