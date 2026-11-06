"""Hämtar dagliga stängningskurser från Yahoo Finance för symbolerna i symboler.json
och skriver kurser.json. Bara avslutade handelsdagar sparas (pågående dag tas bort).
Sedan 2026-11-06 hämtas ett år, så att MA200 (snittet av de 200 senaste stängningarna,
Masterguide Steg 3) kan räknas här. Filen sparar fortfarande bara de senaste dagarna."""
import json, time, datetime, urllib.request, urllib.parse, urllib.error, http.cookiejar

DAGAR_SPARA = 23   # så många handelsdagar sparas i kurser.json (stopp, topp och ATR14 räcker med 16)
MA_DAGAR = 200

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
    bas = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d"
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
    # Yahoo lägger ibland inte in dagsstapeln för senaste handelsdagen förrän långt efter stängning.
    # Finns en nyare, avslutad handelsdag i meta (regularMarketTime/-Price): lägg till den som stängningskurs.
    rmt, rmp = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if rmt and rmp is not None:
        start = reg.get("start")
        stangd = (slut is not None and nu >= slut + 300) or (start is not None and rmt < start)
        d_rmt = datetime.datetime.fromtimestamp(rmt + off, datetime.timezone.utc).date().isoformat()
        if stangd and (not dagar or d_rmt > dagar[-1]["d"]):
            hh, ll = meta.get("regularMarketDayHigh"), meta.get("regularMarketDayLow")
            dagar.append({"d": d_rmt, "h": round(hh, 4) if hh is not None else None, "l": round(ll, 4) if ll is not None else None, "k": round(rmp, 4)})
    # Samma datum två gånger (Yahoo lägger ibland till en extra rad): behåll den sista.
    unika = {}
    for x in dagar:
        unika[x["d"]] = x
    dagar = [unika[d] for d in sorted(unika)]
    # MA200: snittet av de 200 senaste stängningarna. Färre än 200 dagar (t.ex. nyligen noterat): null.
    ma200 = None
    if len(dagar) >= MA_DAGAR:
        sista = dagar[-MA_DAGAR:]
        ma200 = {"varde": round(sum(x["k"] for x in sista) / MA_DAGAR, 4), "datum": sista[-1]["d"]}
    return {"valuta": meta.get("currency"), "borsTidszon": meta.get("exchangeTimezoneName"), "dagar": dagar[-DAGAR_SPARA:], "ma200": ma200}


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
