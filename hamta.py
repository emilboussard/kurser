"""Hämtar dagliga stängningskurser från Yahoo Finance för symbolerna i symboler.json
och skriver kurser.json. Bara avslutade handelsdagar sparas (pågående dag tas bort)."""
import json, time, datetime, urllib.request, urllib.parse, urllib.error, http.cookiejar

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))
OPENER.addheaders = [("User-Agent", UA), ("Accept", "application/json,text/plain,*/*"), ("Accept-Language", "en-US,en;q=0.9")]
CRUMB = {"v": None}


def get(url, timeout=10):
    with OPENER.open(url, timeout=timeout) as r:
        return r.read()


def skaffa_crumb():
    """Yahoo kräver ibland en cookie och en crumb. Hämtas en gång per körning."""
    if CRUMB["v"] is None:
        for start in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
            try:
                get(start)
            except Exception:  # noqa: BLE001  (fc.yahoo.com svarar 404 men sätter cookien)
                pass
        try:
            CRUMB["v"] = get("https://query1.finance.yahoo.com/v1/test/getcrumb").decode().strip()
        except Exception as e:  # noqa: BLE001
            CRUMB["v"] = ""
            print("crumb-fel:", e)
    return CRUMB["v"]


def hamta_json(symbol):
    fel = []
    bas = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1mo&interval=1d"
    for forsok in range(2):
        for vard in ("query1", "query2"):
            url = bas % (vard, urllib.parse.quote(symbol))
            if forsok == 1:
                c = skaffa_crumb()
                if c:
                    url += "&crumb=" + urllib.parse.quote(c)
            try:
                return json.loads(get(url)), ""
            except urllib.error.HTTPError as e:
                fel.append("%s %s" % (vard, e.code))
            except Exception as e:  # noqa: BLE001
                fel.append("%s %s" % (vard, e))
        time.sleep(2)
    return None, "; ".join(fel)


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
    misslyckade = 0
    for bid, sym in symboler.items():
        if misslyckade >= 3 and not ut["kurser"].get("_ok"):
            data, fel = None, "hoppade över: Yahoo svarade inte för de första symbolerna"
        else:
            data, fel = hamta_json(sym)
            if data is None:
                misslyckade += 1
            else:
                ut["kurser"]["_ok"] = True
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
        time.sleep(0.3)
    ut["kurser"].pop("_ok", None)
    json.dump(ut, open("kurser.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
