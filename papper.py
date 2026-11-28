#!/usr/bin/env python3
"""Pappersportföljen: test av stoppet, ingen regel.

Strategichatten 2026-11-28, claude/Andringar-pappersportfolj-for-bevakningssystemet.md.
Bevakningssidan lägger in en papperspost (papper/{id}_{datum}) när ett bolag får Grönt ljus.
Det här skriptet körs av den dagliga körningen och räknar tre stoppvarianter per post på kursfilen:

  A  Stopptrappan enligt guiden från köpdagen.
  B  Väntar 12 månader: de första 12 månaderna avslutas posten bara om kursen stänger
     under köpkursen x 0,70 (katastrofstopp). Trappan räknas från köpdagen som i A men
     gäller först efter 12 månader.
  C  Som B men med katastrofstopp vid köpkursen x 0,65.

Lika för alla tre: vetobrott ger avslut av allt. Säljzonen ger en tredjedel vid första
stängningen över säljzonen och en tredjedel till om kursen ligger kvar där vid nästa rapport.
Varje papperköp är 10 000 kr. Efter en försäljning följer pengarna MSCI World i kronor
(sidan räknar det). Skriptet ändrar inga riktiga stopp och skapar inga papperposter.

Användning:
  python3 papper.py in.json kurser.json utkatalog

in.json = {
  "idag": "ÅÅÅÅ-MM-DD",
  "papper": [{"id": "<bolag>_<datum>", "version": n, "data": {...}}],
  "dokument": {"<bolagId>": {"samling", "namn", "valuta", "profil",
                "priser": {"malkurs", "saljzon"}, "veto": {"brott"},
                "analysdatum", "senasteRapport": {"datum"}}}
}

Skriver utkatalog/manifest.json = [{"op", "collection", "doc_id", "if_version", "file_path"}]
och en JSON-fil per skrivning (bara ändrade fält), och en kort sammanfattning på stdout.
"""
import datetime
import json
import os
import sys

BELOPP = 10000.0
# Stopptrappan (Masterguiden, Portföljhantering punkt 3): procent, k (gånger ATR14), tak
TRAPPA = {
    "A": {"under": (0.15, 3.0, 0.20), "over": (0.10, 2.0, 0.15), "salj": (0.07, 1.5, 0.10)},
    "B": {"under": (0.25, 4.5, 0.35), "over": (0.18, 3.0, 0.25), "salj": (0.12, 2.0, 0.18)},
}
# Variant -> katastrofgräns de första 12 månaderna (None = trappan från köpdagen)
VARIANTER = [("A", None), ("B", 0.70), ("C", 0.65)]
FX_ID = {"USD": "usdsek", "EUR": "eursek", "NOK": "noksek", "DKK": "dkksek"}


def tal(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def serie(kurser, key, cache):
    if key not in cache:
        k = (kurser.get("kurser") or {}).get(key) or {}
        cache[key] = sorted(
            [d for d in (k.get("dagar") or []) if isinstance(d, dict) and d.get("d") and tal(d.get("k"))],
            key=lambda d: d["d"])
    return cache[key]


def senast_fore(dagar, datum):
    """Senaste stängningen på eller före datum."""
    v = None
    for d in dagar:
        if d["d"] <= datum:
            v = d
        else:
            break
    return v


def fx(kurser, valuta, datum, cache):
    if not valuta or valuta == "SEK":
        return 1.0
    key = FX_ID.get(valuta)
    if not key:
        return None
    d = senast_fore(serie(kurser, key, cache), datum)
    return d["k"] if d else None


def index_sek(kurser, datum, cache):
    """MSCI World i kronor (URTH x USD/SEK), som Mot index."""
    u = senast_fore(serie(kurser, "msci-world-urth", cache), datum)
    f = senast_fore(serie(kurser, "usdsek", cache), datum)
    if not u or not f:
        return None
    return {"datum": u["d"], "urth": u["k"], "usdsek": f["k"], "sek": round(u["k"] * f["k"], 4)}


def plus12(datum):
    y, m, d = (int(x) for x in datum[:10].split("-"))
    try:
        return datetime.date(y + 1, m, d).isoformat()
    except ValueError:
        return datetime.date(y + 1, m, 28).isoformat()


def atr14(dagar, i):
    """Snittet av de 14 senaste true range t.o.m. dag i. None om det saknas data."""
    if i is None or i < 14:
        return None
    tr = []
    for j in range(i - 13, i + 1):
        h, lo, pk = dagar[j].get("h"), dagar[j].get("l"), dagar[j - 1].get("k")
        if not (tal(h) and tal(lo) and tal(pk)):
            return None
        tr.append(max(h - lo, abs(h - pk), abs(lo - pk)))
    return sum(tr) / 14.0


def lage(k, malkurs, saljzon):
    if tal(saljzon) and k > saljzon:
        return "salj"
    if tal(malkurs) and k > malkurs:
        return "over"
    return "under"


def trappans_niva(topp, k, atr, profil, malkurs, saljzon, kopkurs):
    """Samma formel som den dagliga körningens STOPP c), med köpkursen som inköpspris."""
    lg = lage(k, malkurs, saljzon)
    p, kk, tak = TRAPPA["B" if profil == "B" else "A"][lg]
    if atr is None or not tal(malkurs) or not tal(saljzon):
        n = topp * (1 - p)
    else:
        n = max(min(topp * (1 - p), topp - kk * atr), topp * (1 - tak))
    if lg in ("over", "salj") and k > kopkurs:
        n = max(n, kopkurs)
    return round(n, 4), lg


def salj(v, datum, k, andel, orsak, kop_sek, fx_d, ix, ix_kop):
    andel = min(andel, v["andelKvar"])
    k_sek = k * fx_d if (fx_d is not None) else None
    varde = andel * BELOPP * k_sek / kop_sek if (k_sek is not None and kop_sek) else None
    v["salj"].append({
        "datum": datum, "kurs": k, "andel": round(andel, 6), "orsak": orsak,
        "varde": round(varde, 2) if varde is not None else None,
        "index": ix["sek"] if ix else None,
    })
    v["andelKvar"] = round(v["andelKvar"] - andel, 6)
    if v["andelKvar"] <= 1e-6:
        v["andelKvar"] = 0.0
        v["status"] = "avslutad"
        varden = [s["varde"] for s in v["salj"]]
        avk = round(sum(varden) / BELOPP - 1, 4) if all(x is not None for x in varden) else None
        idx_avk = round(ix["sek"] / ix_kop - 1, 4) if (ix and ix_kop) else None
        v["avslut"] = {"datum": datum, "kurs": k, "orsak": orsak, "avkastning": avk, "indexAvk": idx_avk}


def kor_post(data, dok, kurser, idag, cache):
    """Returnerar (ändrade fält, kort rad)."""
    bid = data.get("bolagId")
    dagar = serie(kurser, bid, cache)
    if not dagar:
        return None, "%s: saknas i kursfilen" % bid
    dok = dok or {}
    upd = {}
    kop = str(data.get("datum") or "")[:10]
    kk = data.get("kurs")
    if not kop or not tal(kk):
        return None, "%s: posten saknar datum eller kurs" % bid
    valuta = dok.get("valuta") or data.get("valuta") or "SEK"

    # Köpkursen i kronor och index vid köpdagen (sidan kan bara uppskatta valutan)
    kop_sek = data.get("kursSek")
    if data.get("fxUppskattad") or not tal(kop_sek):
        f = fx(kurser, valuta, kop, cache)
        if f is not None:
            upd["fx"] = f
            upd["kursSek"] = round(kk * f, 4)
            upd["fxUppskattad"] = False
            kop_sek = upd["kursSek"]
    ix_kop_obj = data.get("index") if isinstance(data.get("index"), dict) else None
    if not ix_kop_obj or not tal(ix_kop_obj.get("sek")):
        ix_kop_obj = index_sek(kurser, kop, cache)
        if ix_kop_obj:
            upd["index"] = ix_kop_obj
    ix_kop = ix_kop_obj["sek"] if ix_kop_obj else None

    priser = dok.get("priser") or {}
    malkurs = priser.get("malkurs") if tal(priser.get("malkurs")) else data.get("malkurs")
    saljzon = priser.get("saljzon") if tal(priser.get("saljzon")) else data.get("saljzon")
    profil = dok.get("profil") or data.get("profil") or "A"
    veto = bool((dok.get("veto") or {}).get("brott"))
    veto_fran = str(dok.get("analysdatum") or "")[:10]
    rapport = str((dok.get("senasteRapport") or {}).get("datum") or "")[:10]
    tolv = plus12(kop)
    kontr = data.get("kontrolleradTill")

    i_kop = None
    for i, d in enumerate(dagar):
        if d["d"] == kop:
            i_kop = i
    nya = [(i, d) for i, d in enumerate(dagar) if d["d"] > kop and d["d"] <= idag and (not kontr or d["d"] > kontr)]

    var = json.loads(json.dumps(data.get("varianter") or {}))
    for vk, katastrof in VARIANTER:
        v = var.get(vk) or {}
        if v.get("status") == "avslutad":
            var[vk] = v
            continue
        if not tal(v.get("topp")):
            v = {"status": "öppen", "topp": kk, "andelKvar": 1.0, "salj": []}
            v["niva"], v["lage"] = trappans_niva(kk, kk, atr14(dagar, i_kop), profil, malkurs, saljzon, kk)
        v.setdefault("salj", [])
        v.setdefault("andelKvar", 1.0)
        for i, d in nya:
            if v["status"] == "avslutad":
                break
            dd, k = d["d"], d["k"]
            v["topp"] = max(v["topp"], k)
            n, lg = trappans_niva(v["topp"], k, atr14(dagar, i), profil, malkurs, saljzon, kk)
            v["niva"] = max(v["niva"], n)
            v["lage"] = lg
            fx_d = fx(kurser, valuta, dd, cache)
            ix = index_sek(kurser, dd, cache)
            if veto and dd >= veto_fran:
                salj(v, dd, k, v["andelKvar"], "veto", kop_sek, fx_d, ix, ix_kop)
                continue
            if katastrof is None or dd >= tolv:
                if k <= v["niva"]:
                    salj(v, dd, k, v["andelKvar"], "stopp", kop_sek, fx_d, ix, ix_kop)
                    continue
            elif k < kk * katastrof:
                salj(v, dd, k, v["andelKvar"], "katastrofstopp", kop_sek, fx_d, ix, ix_kop)
                continue
            over = tal(saljzon) and k > saljzon
            if not v.get("forstaSaljzon"):
                if over:
                    salj(v, dd, k, 1.0 / 3, "säljzon", kop_sek, fx_d, ix, ix_kop)
                    v["forstaSaljzon"] = dd
            elif not v.get("andraSaljzon") and rapport and rapport > v["forstaSaljzon"] \
                    and dd >= rapport and v.get("rapportKollad") != rapport:
                v["rapportKollad"] = rapport
                if over:
                    salj(v, dd, k, 1.0 / 3, "säljzon", kop_sek, fx_d, ix, ix_kop)
                    v["andraSaljzon"] = dd
        var[vk] = v

    if var != (data.get("varianter") or {}):
        upd["varianter"] = var
    if nya:
        sista = nya[-1][1]
        upd["kontrolleradTill"] = sista["d"]
        f = fx(kurser, valuta, sista["d"], cache)
        upd["senaste"] = {"datum": sista["d"], "kurs": sista["k"],
                          "kursSek": round(sista["k"] * f, 4) if f is not None else None}
    elif not data.get("senaste"):
        f = fx(kurser, valuta, kop, cache)
        upd["senaste"] = {"datum": kop, "kurs": kk, "kursSek": round(kk * f, 4) if f is not None else None}
    status = " ".join("%s:%s" % (vk, (var.get(vk) or {}).get("status", "?")) for vk, _ in VARIANTER)
    return upd, "%s %s: %d nya dagar, %s" % (bid, kop, len(nya), status)


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    inp = json.load(open(sys.argv[1], encoding="utf-8"))
    kurser = json.load(open(sys.argv[2], encoding="utf-8"))
    ut = sys.argv[3]
    os.makedirs(ut, exist_ok=True)
    idag = str(inp.get("idag") or datetime.date.today().isoformat())[:10]
    dokument = inp.get("dokument") or {}
    cache = {}
    manifest, rader = [], []
    for n, post in enumerate(inp.get("papper") or []):
        data = post.get("data") if isinstance(post.get("data"), dict) else post
        doc_id = str(post.get("id") or data.get("id") or "").split("/")[-1]
        if not doc_id or not data.get("bolagId"):
            rader.append("hoppade över en post utan id eller bolagId")
            continue
        upd, rad = kor_post(data, dokument.get(data["bolagId"]), kurser, idag, cache)
        rader.append(rad)
        if upd:
            fp = os.path.join(ut, "papper_%d.json" % n)
            json.dump(upd, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            manifest.append({"op": "update", "collection": "papper", "doc_id": doc_id,
                             "if_version": post.get("version"), "file_path": fp})
    meta = {"datum": idag, "index": index_sek(kurser, idag, cache), "fel": "",
            "antal": len(inp.get("papper") or []),
            "kalla": "papper.py i emilboussard/kurser, kurser.json",
            "uppdaterad": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"}
    fp = os.path.join(ut, "meta_papper.json")
    json.dump(meta, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    manifest.append({"op": "set", "collection": "meta", "doc_id": "papper", "if_version": None, "file_path": fp})
    json.dump(manifest, open(os.path.join(ut, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Pappersportföljen: %d poster, %d skrivningar" % (len(inp.get("papper") or []), len(manifest)))
    for r in rader:
        print(" ", r)


if __name__ == "__main__":
    main()
