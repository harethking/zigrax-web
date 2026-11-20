#!/usr/bin/env python3
"""ZIGRAX rankings: Chess.com (per Arab country, Arab, world) and FIDE (same scopes).
Writes small JSON files to rank/ that the app reads from GitHub Pages.
  python3 scripts/rank.py fide            # monthly FIDE list
  python3 scripts/rank.py cc  [seconds]   # Chess.com, incremental, time budget
"""
import sys, os, zlib, json, time, gzip, io, zipfile, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

ARAB = ["IQ", "SA", "EG", "AE", "KW", "QA", "BH", "OM", "YE", "JO", "SY", "LB", "PS",
        "DZ", "MA", "TN", "LY", "SD", "SO", "DJ", "KM", "MR"]
FIDE_FED = {"IRQ": "IQ", "KSA": "SA", "EGY": "EG", "UAE": "AE", "KUW": "KW", "QAT": "QA", "BRN": "BH",
            "OMA": "OM", "YEM": "YE", "JOR": "JO", "SYR": "SY", "LBN": "LB", "PLE": "PS", "ALG": "DZ",
            "MAR": "MA", "TUN": "TN", "LBA": "LY", "SUD": "SD", "SOM": "SO", "DJI": "DJ", "COM": "KM", "MTN": "MR"}
TOP = 200
OUT = os.path.join(os.path.dirname(__file__), "..", "rank")
UA = {"User-Agent": "ZIGRAX rankings (zigrax.com; github.com/harethking/zigrax-web)"}


def save(rel, obj):
    p = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def get(url, tries=6):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            time.sleep(2 + k * 3 if e.code == 429 else 1 + k)
        except Exception:
            time.sleep(1 + k)
    return None


# ---------------------------------------------------------------- FIDE
def fide():
    src = sys.argv[2] if len(sys.argv) > 2 else ""
    raw = open(src, "rb").read() if src and os.path.exists(src) else get("https://ratings.fide.com/download/players_list.zip", tries=4)
    if not raw:
        sys.exit("no FIDE list")
    z = zipfile.ZipFile(io.BytesIO(raw))
    txt = z.read(z.namelist()[0]).decode("latin-1").splitlines()
    head = txt[0]
    names = ["ID Number", "Name", "Fed", "Sex", "Tit", "WTit", "OTit", "FOA", "SRtng", "SGm", "SK",
             "RRtng", "RGm", "Rk", "BRtng", "BGm", "BK", "B-day", "Flag"]
    pos = []
    for n in names:
        i = head.find(n if n != "Tit" else " Tit") + (1 if n == "Tit" else 0)
        pos.append(i)
    cols = {}
    order = sorted(range(len(names)), key=lambda j: pos[j])
    for a, j in enumerate(order):
        end = pos[order[a + 1]] if a + 1 < len(order) else None
        cols[names[j]] = (pos[j], end)
    def f(line, n):
        s, e = cols[n]
        return line[s:e].strip()
    cats = {"std": "SRtng", "rapid": "RRtng", "blitz": "BRtng"}
    world = {c: [] for c in cats}
    per = {cc: {c: [] for c in cats} for cc in ARAB}
    count = 0
    for line in txt[1:]:
        if len(line) < 60:
            continue
        flag = f(line, "Flag")
        if "i" in flag.lower():           # inactive
            continue
        fed = f(line, "Fed")
        cc = FIDE_FED.get(fed)
        nm = f(line, "Name")
        tit = f(line, "Tit") or f(line, "WTit")
        by = f(line, "B-day")
        pid = f(line, "ID Number")
        count += 1
        for c, col in cats.items():
            r = f(line, col)
            if not r.isdigit() or int(r) == 0:
                continue
            row = [nm, tit, int(r), pid, by, fed]
            world[c].append(row)
            if cc:
                per[cc][c].append(row)
    top = lambda L: sorted(L, key=lambda x: -x[2])[:TOP]
    stamp = time.strftime("%Y-%m-%d")
    save("fide/world.json", {"updated": stamp, "cats": {c: top(L) for c, L in world.items()}})
    arab = {c: [] for c in cats}
    for cc in ARAB:
        save(f"fide/{cc}.json", {"updated": stamp, "n": {c: len(L) for c, L in per[cc].items()},
                                 "cats": {c: top(L) for c, L in per[cc].items()}})
        for c in cats:
            arab[c] += per[cc][c]
    save("fide/arab.json", {"updated": stamp, "n": {c: len(L) for c, L in arab.items()},
                            "cats": {c: top(L) for c, L in arab.items()}})
    print("FIDE active players", count, {cc: len(per[cc]["std"]) for cc in ARAB})


# ---------------------------------------------------------------- Chess.com
CACHE = os.environ.get("RANK_CACHE", "rank_cache.json.gz")
CCATS = ["rapid", "blitz", "bullet"]
ACTIVE_DAYS = 120
MIN_GAMES = 10


def cc(budget):
    t0 = time.time()
    try:
        with gzip.open(CACHE, "rt") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {"users": {}, "lists": {}, "prof": {}}
    users, prof = cache["users"], cache["prof"]
    # 1. the member lists, refreshed every few days
    for c in ARAB:
        L = cache["lists"].get(c)
        if L and time.time() - L["t"] < 3 * 86400:
            continue
        raw = get(f"https://api.chess.com/pub/country/{c}/players")
        if raw:
            cache["lists"][c] = {"t": time.time(), "u": json.loads(raw).get("players", [])}
    sizes = {c: len(cache["lists"].get(c, {}).get("u", [])) for c in ARAB}
    print("country sizes", sizes, "total", sum(sizes.values()))
    # 2. stats: never-seen first, then the oldest (spread over the countries)
    want = []
    for c in ARAB:
        for u in cache["lists"].get(c, {}).get("u", []):
            e = users.get(u)
            want.append((e[0] if e else 0, zlib.crc32(u.encode()), u, c))
    want.sort()
    want = [(a, u, c) for a, _, u, c in want]   # unseen first, in a mixed order across countries
    lock = threading.Lock()
    done = [0]

    def one(item):
        if time.time() - t0 > budget:
            return
        _, u, c = item
        raw = get(f"https://api.chess.com/pub/player/{u}/stats")
        now = int(time.time())
        rec = [now, c]
        if raw:
            s = json.loads(raw)
            for k in CCATS:
                d = s.get("chess_" + k) or {}
                last, r = d.get("last") or {}, d.get("record") or {}
                n = (r.get("win") or 0) + (r.get("loss") or 0) + (r.get("draw") or 0)
                rec += [last.get("rating") or 0, last.get("date") or 0, n]
        else:
            rec += [0, 0, 0] * 3
        with lock:
            users[u] = rec
            done[0] += 1

    with ThreadPoolExecutor(12) as ex:
        list(ex.map(one, want))
    print("stats fetched", done[0], "cached", len(users), "in", int(time.time() - t0), "s")
    # 3. rankings
    now = time.time()
    lists = {c: {k: [] for k in CCATS} for c in ARAB}
    for c in ARAB:
        for u in cache["lists"].get(c, {}).get("u", []):
            e = users.get(u)
            if not e or e[1] != c:
                continue
            for j, k in enumerate(CCATS):
                r, d, n = e[2 + j * 3: 5 + j * 3]
                if r and n >= MIN_GAMES and now - d < ACTIVE_DAYS * 86400:
                    lists[c][k].append([u, r, n])
    # profile (name, title, avatar) only for the players who are shown
    shown = set()
    for c in ARAB:
        for k in CCATS:
            lists[c][k].sort(key=lambda x: -x[1])
            for row in lists[c][k][:TOP]:
                shown.add(row[0])

    def pf(u):
        p = prof.get(u)
        if p and now - p[0] < 14 * 86400:
            return
        raw = get(f"https://api.chess.com/pub/player/{u}")
        if raw:
            j = json.loads(raw)
            prof[u] = [int(now), j.get("name") or "", j.get("title") or "", j.get("avatar") or ""]
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(pf, sorted(shown)))
    def rows(L):
        out = []
        for u, r, n in L[:TOP]:
            p = prof.get(u) or [0, "", "", ""]
            out.append([u, p[1], p[2], r, n, p[3]])
        return out
    stamp = time.strftime("%Y-%m-%d")
    arab = {k: [] for k in CCATS}
    for c in ARAB:
        tot = sizes[c]
        seen = sum(1 for u in cache["lists"].get(c, {}).get("u", []) if u in users)
        save(f"cc/{c}.json", {"updated": stamp, "members": tot, "scanned": seen,
                              "n": {k: len(lists[c][k]) for k in CCATS},
                              "cats": {k: rows(lists[c][k]) for k in CCATS}})
        for k in CCATS:
            arab[k] += [x + [c] for x in lists[c][k][:TOP]]
    for k in CCATS:
        arab[k].sort(key=lambda x: -x[1])
    save("cc/arab.json", {"updated": stamp, "n": {k: sum(len(lists[c][k]) for c in ARAB) for k in CCATS},
                          "cats": {k: [rows([x[:3]])[0] + [x[3]] for x in arab[k][:TOP]] for k in CCATS}})
    # world: the Chess.com leaderboards
    raw = get("https://api.chess.com/pub/leaderboards")
    if raw:
        lb = json.loads(raw)
        w = {}
        for k in CCATS:
            w[k] = [[p.get("username"), p.get("name") or "", p.get("title") or "", p.get("score"), 0,
                     p.get("avatar") or "", (p.get("country") or "").rsplit("/", 1)[-1]]
                    for p in lb.get("live_" + k, [])]
        save("cc/world.json", {"updated": stamp, "cats": w})
    with gzip.open(CACHE, "wt") as fh:
        json.dump(cache, fh, separators=(",", ":"))


if __name__ == "__main__":
    if sys.argv[1] == "fide":
        fide()
    else:
        cc(float(sys.argv[2]) if len(sys.argv) > 2 else 3600)
