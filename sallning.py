"""Sållning: hittar nya kandidater till bevakningslistan (Masterguide v4.8, Steg 0 och Fas 1).

Hämtar alla svenska aktier med börsvärde över GRANS_SEK från Yahoo Finance, räknar de
kriterier i Fas 1 som går att räkna ur siffror och skriver sallning.json (och en kopia
per månad i arkiv/). Poängen är en snabbpoäng för att välja vilka bolag som är värda en
hel analys. Den ersätter aldrig analysen, som räknar om allt enligt guiden.

Sifferkriterier (max 12 p): Profil A 1, 2, 5, 6, 7, 10. Profil B 1, 2, 3, 5, 7, 10.
Kriterierna 3/6 (kundkoncentration), 8 (guidning) och 9 (insiders) kräver läsning (6 p).
Ett bolag behöver minst 8 av 12 för att kunna nå 14 av 18 och grönt ljus.
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.parse, urllib.error, http.cookiejar

GRANS_SEK = 2e9          # minsta börsvärde i kronor
MIN_POANG = 8            # minsta möjliga snabbpoäng för att bli kandidat
SKATT = 0.79             # ROIC = EBIT × 0,79 / (eget kapital + räntebärande nettoskuld)
UTESLUTNA_SEKTORER = {
    "Financial Services": "bank, finans eller investmentbolag (egen modul i guiden)",
    "Real Estate": "fastighetsbolag (räknas inte med guidens nyckeltal)",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))
OPENER.addheaders = [("User-Agent", UA), ("Accept", "application/json,text/plain,*/*"), ("Accept-Language", "en-US,en;q=0.9")]
CRUMB = {"v": None}


# ---------------------------------------------------------------- hämtning

def get(url, data=None, timeout=20):
    req = urllib.request.Request(url, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with OPENER.open(req, timeout=timeout) as r:
        return r.read()


def skaffa_crumb():
    """Yahoo kräver en cookie och en crumb för screener och quoteSummary."""
    if CRUMB["v"] is None:
        for start in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
            try:
                get(start)
            except Exception:  # noqa: BLE001  (fc.yahoo.com svarar 404 men sätter cookien)
                pass
        for vard in ("query1", "query2"):
            try:
                CRUMB["v"] = get("https://%s.finance.yahoo.com/v1/test/getcrumb" % vard).decode().strip()
                break
            except Exception as e:  # noqa: BLE001
                print("crumb-fel:", vard, e)
        CRUMB["v"] = CRUMB["v"] or ""
    return CRUMB["v"]


def hamta_json(url, data=None, forsok=4):
    """Hämtar JSON med omförsök. 429 (för många anrop) ger längre paus."""
    fel = ""
    for i in range(forsok):
        try:
            return json.loads(get(url, data)), ""
        except urllib.error.HTTPError as e:
            fel = "HTTP %s" % e.code
            if e.code == 404:
                break
            if e.code in (401, 403):
                CRUMB["v"] = None
            time.sleep(10 * (i + 1) if e.code == 429 else 2)
        except Exception as e:  # noqa: BLE001
            fel = str(e)
            time.sleep(2)
    return None, fel


def hamta_univers():
    """Alla aktier i regionen Sverige med börsvärde över gränsen, största först."""
    crumb = skaffa_crumb()
    alla, offset, total = [], 0, None
    while total is None or offset < total:
        kropp = {
            "size": 250, "offset": offset, "sortField": "intradaymarketcap", "sortType": "DESC",
            "quoteType": "EQUITY", "userId": "", "userIdType": "guid",
            "query": {"operator": "AND", "operands": [
                {"operator": "eq", "operands": ["region", "se"]},
                {"operator": "gt", "operands": ["intradaymarketcap", GRANS_SEK]},
            ]},
        }
        url = "https://query2.finance.yahoo.com/v1/finance/screener?formatted=false&lang=en-US&region=SE&crumb=" + urllib.parse.quote(crumb)
        data, fel = hamta_json(url, json.dumps(kropp).encode())
        if data is None:
            raise RuntimeError("screenern svarade inte: " + fel)
        res = data["finance"]["result"][0]
        total = res.get("total", 0)
        citat = res.get("quotes") or []
        if not citat:
            break
        alla += citat
        offset += len(citat)
        time.sleep(1)
    return alla


def rensa_univers(citat):
    """Bara .ST-aktier, inga preferensaktier, och en aktieserie per bolag (den mest handlade)."""
    bast = {}
    for q in citat:
        sym = q.get("symbol", "")
        namn = q.get("longName") or q.get("shortName") or sym
        if not sym.endswith(".ST") or "PREF" in sym.upper() or "pref" in namn.lower():
            continue
        nyckel = re.sub(r"\s+(ser\.?\s*[A-D]|class\s+[A-D]|[A-D])$", "", namn.strip(), flags=re.I).lower()
        nyckel = re.sub(r"[^a-z0-9åäö]", "", nyckel)
        vol = q.get("averageDailyVolume3Month") or 0
        if nyckel not in bast or vol > (bast[nyckel].get("averageDailyVolume3Month") or 0):
            bast[nyckel] = q
    return sorted(bast.values(), key=lambda q: -(q.get("marketCap") or 0))


TYPER = [
    "annualTotalRevenue", "annualGrossProfit", "annualOperatingIncome", "annualEBIT", "annualEBITDA",
    "annualNetIncomeCommonStockholders", "annualOperatingCashFlow", "annualCapitalExpenditure",
    "annualStockholdersEquity", "annualTotalDebt", "annualCashCashEquivalentsAndShortTermInvestments",
    "annualOrdinarySharesNumber", "annualDilutedAverageShares",
    "quarterlyTotalRevenue", "quarterlyTotalDebt", "quarterlyCashCashEquivalentsAndShortTermInvestments",
    "trailingTotalRevenue", "trailingGrossProfit", "trailingOperatingIncome", "trailingEBITDA",
    "trailingNetIncomeCommonStockholders", "trailingOperatingCashFlow", "trailingCapitalExpenditure",
]


def hamta_bokslut(sym):
    nu = int(time.time())
    url = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
           "?symbol=%s&type=%s&period1=%d&period2=%d&merge=false&padTimeSeries=false&lang=en-US&region=US"
           % (urllib.parse.quote(sym), urllib.parse.quote(sym), ",".join(TYPER), nu - 8 * 365 * 86400, nu))
    data, fel = hamta_json(url)
    if data is None:
        return None, fel
    ut = {}
    for r in (data.get("timeseries") or {}).get("result") or []:
        typ = (r.get("meta") or {}).get("type", [None])[0]
        varden = {}
        for x in r.get(typ) or []:
            rv = (x or {}).get("reportedValue") or {}
            if rv.get("raw") is not None:
                varden[x["asOfDate"]] = rv["raw"]
                ut.setdefault("_valuta", x.get("currencyCode"))
        if typ:
            ut[typ] = varden
    return ut, ""


def hamta_profil(sym):
    url = ("https://query2.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=assetProfile,earningsTrend&crumb=%s"
           % (urllib.parse.quote(sym), urllib.parse.quote(skaffa_crumb())))
    data, fel = hamta_json(url, forsok=2)
    if data is None:
        return {}, fel
    res = ((data.get("quoteSummary") or {}).get("result") or [{}])[0]
    ap = res.get("assetProfile") or {}
    ut = {"sektor": ap.get("sector"), "bransch": ap.get("industry")}
    for t in ((res.get("earningsTrend") or {}).get("trend") or []):
        if t.get("period") == "0y":
            et = t.get("epsTrend") or {}
            nu, fore = (et.get("current") or {}).get("raw"), (et.get("90daysAgo") or {}).get("raw")
            if nu is not None and fore not in (None, 0):
                ut["estimatAndring3m"] = (nu - fore) / abs(fore)
    return ut, ""


# ---------------------------------------------------------------- beräkning

def ar_serie(b, *typer):
    for t in typer:
        if b.get(t):
            return b[t]
    return {}


def cagr(forst, sist, ar):
    if forst is None or sist is None or forst <= 0 or sist <= 0 or ar <= 0:
        return None
    return (sist / forst) ** (1 / ar) - 1


def pct(x):
    return None if x is None else round(100 * x, 1)


def rakna(b):
    """Räknar nyckeltalen ur bokslutet. Returnerar (nyckeltal, notering) eller (None, orsak)."""
    oms = ar_serie(b, "annualTotalRevenue")
    datum = sorted(oms)[-5:]
    if len(datum) < 3:
        return None, "för få års bokslut hos Yahoo (%d)" % len(datum)

    def rad(typer):
        s = ar_serie(b, *typer)
        return [s.get(d) for d in datum]

    R = [oms[d] for d in datum]
    EBIT = rad(["annualEBIT", "annualOperatingIncome"])
    EBITDA = rad(["annualEBITDA"])
    NI = rad(["annualNetIncomeCommonStockholders"])
    OCF = rad(["annualOperatingCashFlow"])
    CAPEX = rad(["annualCapitalExpenditure"])
    EK = rad(["annualStockholdersEquity"])
    SKULD = rad(["annualTotalDebt"])
    KASSA = rad(["annualCashCashEquivalentsAndShortTermInvestments"])
    AKTIER = rad(["annualOrdinarySharesNumber", "annualDilutedAverageShares"])
    BRUTTO = rad(["annualGrossProfit"])

    n = {"ar": [d[:4] for d in datum]}
    ar_spann = (datetime.date.fromisoformat(datum[-1]) - datetime.date.fromisoformat(datum[0])).days / 365.25

    # Omsättningstillväxt per år (CAGR över boksluten) och senaste kvartalet mot samma kvartal i fjol
    n["tillvaxt"] = cagr(R[0], R[-1], ar_spann)
    kv = ar_serie(b, "quarterlyTotalRevenue")
    kd = sorted(kv)
    n["tillvaxtKvartal"] = (kv[kd[-1]] / kv[kd[-5]] - 1) if len(kd) >= 5 and kv[kd[-5]] > 0 else None

    # ROIC per år och snitt
    roic = []
    for e, ek, s, k in zip(EBIT, EK, SKULD, KASSA):
        if None in (e, ek):
            continue
        kap = ek + (s or 0) - (k or 0)
        if kap > 0:
            roic.append(e * SKATT / kap)
    n["roic"] = sum(roic) / len(roic) if roic else None
    n["roicAr"] = len(roic)

    # Nettoskuld/EBITDA: senaste kvartalets balansräkning och rullande EBITDA (annars senaste året)
    qs, qk = ar_serie(b, "quarterlyTotalDebt"), ar_serie(b, "quarterlyCashCashEquivalentsAndShortTermInvestments")
    if qs and qk and max(qs) == max(qk):
        skuld, kassa = qs[max(qs)], qk[max(qk)]
    else:
        skuld, kassa = SKULD[-1] or 0, KASSA[-1] or 0
    ebitda_ttm = list(ar_serie(b, "trailingEBITDA").values())
    ebitda = ebitda_ttm[-1] if ebitda_ttm else EBITDA[-1]
    netto = skuld - kassa
    n["nettoskuld"] = netto
    if netto <= 0:
        n["nettoskuldEbitda"] = round(netto / ebitda, 2) if ebitda and ebitda > 0 else 0.0
    elif ebitda and ebitda > 0:
        n["nettoskuldEbitda"] = round(netto / ebitda, 2)
    else:
        n["nettoskuldEbitda"] = 99.0

    # EBIT-marginal: rullande 12 månader (annars senaste året), stigande = över snittet av åren före
    marg = [e / r for e, r in zip(EBIT, R) if e is not None and r]
    t_oms, t_ebit = list(ar_serie(b, "trailingTotalRevenue").values()), list(ar_serie(b, "trailingOperatingIncome").values())
    n["ebitMarginal"] = (t_ebit[-1] / t_oms[-1]) if t_oms and t_ebit and t_oms[-1] else (marg[-1] if marg else None)
    n["marginalStigande"] = bool(marg[:-1]) and n["ebitMarginal"] is not None and n["ebitMarginal"] > sum(marg[:-1]) / len(marg[:-1])

    # Bruttomarginal (Profil B), rullande om det finns
    t_brutto = list(ar_serie(b, "trailingGrossProfit").values())
    if t_brutto and t_oms and t_oms[-1]:
        n["bruttomarginal"] = t_brutto[-1] / t_oms[-1]
    else:
        bm = [g / r for g, r in zip(BRUTTO, R) if g is not None and r]
        n["bruttomarginal"] = bm[-1] if bm else None

    # FCF = löpande kassaflöde − investeringar (Yahoo, före leasingamortering). Konvertering över alla år.
    fcf = [o + c for o, c in zip(OCF, CAPEX) if o is not None and c is not None]
    ni = [x for x, o, c in zip(NI, OCF, CAPEX) if x is not None and o is not None and c is not None]
    n["fcfKonvertering"] = (sum(fcf) / sum(ni)) if ni and sum(ni) > 0 else None
    t_ocf, t_capex = list(ar_serie(b, "trailingOperatingCashFlow").values()), list(ar_serie(b, "trailingCapitalExpenditure").values())
    fcf_ttm = (t_ocf[-1] + t_capex[-1]) if t_ocf and t_capex else (fcf[-1] if fcf else None)
    n["fcfTtm"] = fcf_ttm
    oms_ttm = t_oms[-1] if t_oms else R[-1]
    n["fcfMarginal"] = (fcf_ttm / oms_ttm) if fcf_ttm is not None and oms_ttm else None
    n["kassaManader"] = (12 * kassa / -fcf_ttm) if fcf_ttm is not None and fcf_ttm < 0 else None

    # Utspädning: förändring i antal aktier per år, upp till tre år bakåt
    ak = [(d, a) for d, a in zip(datum, AKTIER) if a]
    if len(ak) >= 2:
        d0, a0 = ak[max(0, len(ak) - 4)]
        d1, a1 = ak[-1]
        ar_u = (datetime.date.fromisoformat(d1) - datetime.date.fromisoformat(d0)).days / 365.25
        n["utspadning"] = cagr(a0, a1, ar_u)
        rps0 = R[datum.index(d0)] / a0
        rps1 = R[datum.index(d1)] / a1
        n["omsPerAktieTillvaxt"] = cagr(rps0, rps1, ar_u)
    else:
        n["utspadning"] = n["omsPerAktieTillvaxt"] = None

    # Cykeltestet (Del 4) med de år som finns: EBIT föll mer än 30 % från topp till botten
    fall = 0.0
    for i, e in enumerate(EBIT):
        if e and e > 0:
            for f in EBIT[i + 1:]:
                if f is not None:
                    fall = max(fall, (e - f) / e)
    n["ebitFall"] = fall
    return n, ""


def poang_a(n):
    k = {}
    t = n["tillvaxt"]
    k[1] = (1 if t is not None and t >= 0.05 else 0, "omsättning %s %% per år%s" % (pct(t), " (2p om intäkterna är återkommande)" if t and t > 0.12 else ""))
    r = n["roic"]
    k[2] = (2 if r is not None and r > 0.15 else 1 if r is not None and r >= 0.10 else 0, "ROIC %s %% (snitt %d år)" % (pct(r), n["roicAr"]))
    s = n["nettoskuldEbitda"]
    k[5] = (2 if s < 1.5 else 1 if s <= 3 else 0, "nettoskuld/EBITDA %s" % s)
    m = n["ebitMarginal"]
    k[6] = (2 if m is not None and m > 0.20 and n["marginalStigande"] else 1 if m is not None and m >= 0.10 else 0,
            "EBIT-marginal %s %%%s" % (pct(m), ", stigande" if n["marginalStigande"] else ""))
    f = n["fcfKonvertering"]
    k[7] = (2 if f is not None and f > 0.90 else 1 if f is not None and f >= 0.60 else 0, "FCF/nettovinst %s %%" % pct(f))
    u = n["utspadning"]
    k[10] = (2 if u is not None and u < 0.01 else 1 if u is not None and u <= 0.03 else 0, "aktier %s %% per år" % pct(u))
    osakra = 1 if t and t > 0.12 else 0
    return k, osakra


def poang_b(n, profil):
    k = {}
    t = n["tillvaxtKvartal"] if n["tillvaxtKvartal"] is not None else n["tillvaxt"]
    k[1] = (2 if t is not None and t > 0.20 else 1 if t is not None and t >= 0.10 else 0, "omsättning %s %% (senaste kvartalet mot i fjol)" % pct(t))
    g = n["bruttomarginal"]
    k[2] = (2 if g is not None and g > 0.60 else 1 if g is not None and g >= 0.40 else 0, "bruttomarginal %s %% (ROIC gäller kontraktstillverkare)" % pct(g))
    r40 = (t + n["fcfMarginal"]) if t is not None and n["fcfMarginal"] is not None else None
    k[3] = (2 if r40 is not None and r40 >= 0.40 else 1 if r40 is not None and r40 >= 0.20 else 0, "Rule of 40: %s" % pct(r40))
    osakra = 0
    if n["nettoskuld"] <= 0:
        k[5] = (2, "nettokassa")
    elif n["kassaManader"] is not None:
        mn = n["kassaManader"]
        k[5] = (2 if mn > 36 else 1 if mn >= 18 else 0, "kassan räcker %d månader" % mn)
    else:
        k[5] = (1, "nettoskuld men positivt FCF (bedöms i analysen)")
        osakra += 1
    e = profil.get("estimatAndring3m")
    if e is None:
        k[7] = (1, "estimatändring saknas (räknat som oförändrat)")
    else:
        k[7] = (2 if e > 0.05 else 0 if e < -0.05 else 1, "vinstestimat %+.1f %% senaste 3 mån" % (100 * e))
    u, rps = n["utspadning"], n["omsPerAktieTillvaxt"]
    if u is not None and u < 0.015:
        k[10] = (2, "aktier %s %% per år" % pct(u))
    elif u is not None and u <= 0.05 and rps is not None and rps > 0.15:
        k[10] = (1, "aktier %s %% per år, omsättning per aktie %s %%" % (pct(u), pct(rps)))
    else:
        k[10] = (0, "aktier %s %% per år" % pct(u))
    return k, osakra


def veton(n):
    v = []
    if n["nettoskuldEbitda"] > 4:
        v.append("veto 1: nettoskuld/EBITDA %s" % n["nettoskuldEbitda"])
    if n["kassaManader"] is not None and n["kassaManader"] < 12:
        v.append("veto 2: kassan räcker %d månader" % n["kassaManader"])
    if n["utspadning"] is not None and n["utspadning"] > 0.05:
        v.append("veto 3 (villkorligt): aktierna ökar %s %% per år" % pct(n["utspadning"]))
    return v


# ---------------------------------------------------------------- huvudprogram

def main():
    start = time.time()
    idag = datetime.date.today()
    redan = {}
    try:
        for bid, sym in json.load(open("symboler.json")).items():
            redan[sym.upper()] = bid
    except Exception:  # noqa: BLE001
        pass

    ut = {
        "hamtad": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system": "Masterguide v4.8", "kalla": "Yahoo Finance (screener, fundamentals-timeseries, quoteSummary)",
        "regler": {"borsvardeMinSEK": GRANS_SEK, "minSnabbpoang": MIN_POANG, "maxSnabbpoang": 12},
        "status": "ok", "fel": [], "antal": {}, "kandidater": [], "ovriga": [], "uteslutna": [], "dataSaknas": [],
    }
    try:
        univers = rensa_univers(hamta_univers())
    except Exception as e:  # noqa: BLE001
        # Behåll förra listan och skriv bara in felet, så att en misslyckad körning inte tömmer filen.
        try:
            ut = json.load(open("sallning.json"))
        except Exception:  # noqa: BLE001
            pass
        ut["status"] = "fel"
        ut["fel"] = ["%s: universum kunde inte hämtas: %s" % (idag.isoformat(), e)]
        json.dump(ut, open("sallning.json", "w"), ensure_ascii=False, indent=1)
        print("Universum kunde inte hämtas:", e)
        return
    print("Universum:", len(univers), "bolag")

    for q in univers:
        sym = q["symbol"]
        bas = {
            "symbol": sym, "namn": q.get("longName") or q.get("shortName"),
            "borsvardeMdr": round((q.get("marketCap") or 0) / 1e9, 1), "kurs": q.get("regularMarketPrice"),
            "valuta": q.get("currency"), "pe": round(q["trailingPE"], 1) if q.get("trailingPE") else None,
            "finnsRedan": redan.get(sym.upper()),
        }
        profil, pfel = hamta_profil(sym)
        bas["sektor"], bas["bransch"] = profil.get("sektor"), profil.get("bransch")
        if bas["sektor"] in UTESLUTNA_SEKTORER:
            bas["orsak"] = UTESLUTNA_SEKTORER[bas["sektor"]]
            ut["uteslutna"].append(bas)
            time.sleep(0.4)
            continue
        b, bfel = hamta_bokslut(sym)
        if b is None:
            bas["orsak"] = "bokslutet kunde inte hämtas: " + bfel
            ut["dataSaknas"].append(bas)
            continue
        n, orsak = rakna(b)
        if n is None:
            bas["orsak"] = orsak
            ut["dataSaknas"].append(bas)
            continue

        profil_bokstav = "B" if (n["tillvaxt"] or 0) >= 0.15 else "A"
        k, osakra = poang_a(n) if profil_bokstav == "A" else poang_b(n, profil)
        poang = sum(p for p, _ in k.values())
        v = veton(n)
        flaggor = []
        if n["ebitFall"] > 0.30:
            flaggor.append("möjligen cykliskt: EBIT föll %d %% under åren %s–%s (cykeltestet kräver 15 år)" % (100 * n["ebitFall"], n["ar"][0], n["ar"][-1]))
        if pfel:
            flaggor.append("bransch och estimat saknas (%s)" % pfel)
        bas.update({
            "profil": profil_bokstav, "snabbpoang": poang, "mojligPoang": poang + osakra,
            "kriterier": {str(nr): {"poang": p, "varde": txt} for nr, (p, txt) in sorted(k.items())},
            "veto": v, "flaggor": flaggor, "bokslutAr": n["ar"], "redovisningsvaluta": b.get("_valuta"),
            "roic": pct(n["roic"]), "ebitMarginal": pct(n["ebitMarginal"]), "tillvaxt": pct(n["tillvaxt"]),
            "nettoskuldEbitda": n["nettoskuldEbitda"], "fcfKonvertering": pct(n["fcfKonvertering"]),
        })
        ar_veto = any(not x.startswith("veto 3") for x in v)
        if bas["mojligPoang"] >= MIN_POANG and not ar_veto:
            ut["kandidater"].append(bas)
        else:
            ut["ovriga"].append(bas)
        print("%-14s %s %2dp %s" % (sym, profil_bokstav, poang, "; ".join(v)))
        time.sleep(0.4)

    nyckel = lambda x: (-x["snabbpoang"], -(x["roic"] or -999))  # noqa: E731
    ut["kandidater"].sort(key=nyckel)
    ut["ovriga"].sort(key=nyckel)
    ut["antal"] = {
        "univers": len(univers), "kandidater": len(ut["kandidater"]),
        "nyaKandidater": sum(1 for x in ut["kandidater"] if not x["finnsRedan"]),
        "ovriga": len(ut["ovriga"]), "uteslutna": len(ut["uteslutna"]), "dataSaknas": len(ut["dataSaknas"]),
    }
    if len(ut["dataSaknas"]) > len(univers) / 2:
        ut["status"] = "ofullständig"
        ut["fel"].append("bokslut saknas för mer än hälften av bolagen")
    ut["sekunder"] = round(time.time() - start)
    json.dump(ut, open("sallning.json", "w"), ensure_ascii=False, indent=1)
    os.makedirs("arkiv", exist_ok=True)
    json.dump(ut, open("arkiv/sallning-%s.json" % idag.strftime("%Y-%m"), "w"), ensure_ascii=False, indent=1)
    print("Klart:", ut["antal"])


if __name__ == "__main__":
    sys.exit(main())
