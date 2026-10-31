"""Hämtar dagliga stängningskurser från Yahoo Finance för symbolerna i symboler.json
och skriver kurser.json. Bara avslutade handelsdagar sparas (pågående dag tas bort)."""
import json, time, datetime, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def hamta_json(symbol):
    fel = ""
    for forsok in range(3):
        for vard in ("query1", "query2"):
            url = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1mo&interval=1d" % (vard, urllib.parse.quote(symbol))
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                    return json.load(r), ""
            except Exception as e:  # noqa: BLE001
                fel = "%s: %s" % (vard, e)
        time.sleep(3 * (forsok + 1))
    return None, fel


def tolka(data, nu):
    res = data["chart"]["result"][0]
    meta = res["meta"]
    off = meta.get("gmtoffset", 0)
    q = res["indicators"]["quote"][0]
    dagar = []
    for i, ts in enumerate(res.get("timestamp") or []):
        k = q["close"][i]
        if k is None:
            continue
        d = datetime.datetime.fromtimestamp(ts + off, datetime.timezone.utc).date().isoformat()
        h, l = q["high"][i], q["low"][i]
        dagar.append({"d": d, "h": round(h, 4) if h is not None else None, "l": round(l, 4) if l is not None else None, "k": round(k, 4)})
    # Pågående handelsdag: ta bort sista dagen om börsen inte har stängt än.
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    slut = reg.get("end")
    if dagar and slut and nu < slut:
        idag = datetime.datetime.fromtimestamp(slut + off, datetime.timezone.utc).date().isoformat()
        if dagar[-1]["d"] == idag:
            dagar.pop()
    # Samma datum två gånger (Yahoo lägger ibland till en extra rad): behåll den sista.
    unika = {}
    for x in dagar:
        unika[x["d"]] = x
    dagar = [unika[d] for d in sorted(unika)]
    return {"valuta": meta.get("currency"), "borsTidszon": meta.get("exchangeTimezoneName"), "dagar": dagar}


def main():
    symboler = json.load(open("symboler.json"))
    nu = time.time()
    ut = {"hamtad": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kalla": "Yahoo Finance v8 chart", "kurser": {}}
    for bid, sym in symboler.items():
        data, fel = hamta_json(sym)
        post = {"symbol": sym}
        if data is None:
            post.update({"fel": fel, "dagar": []})
        else:
            try:
                post.update(tolka(data, nu))
                post["fel"] = ""
            except Exception as e:  # noqa: BLE001
                post.update({"fel": "tolkning: %s" % e, "dagar": []})
        if post["dagar"]:
            post["senaste"] = post["dagar"][-1]
        ut["kurser"][bid] = post
        print(bid, sym, post.get("senaste", {}).get("d"), post.get("senaste", {}).get("k"), post["fel"])
        time.sleep(0.5)
    json.dump(ut, open("kurser.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
