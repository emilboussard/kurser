"""Sållning: hittar nya kandidater till bevakningslistan (Masterguide v4.8, Steg 0 och Fas 1).

Marknader: Sverige, Norge, Danmark och Finland (börsvärde över 2 mdr kr) och USA (NYSE och
Nasdaq, börsvärde över 2 mdr USD). Hämtar bolagen från Yahoo Finance, räknar de kriterier i
Fas 1 som går att räkna ur siffror och skriver sallning.json (och en kopia per månad i arkiv/).
Snabbpoängen väljer vilka bolag som är värda en hel analys. Den ersätter aldrig analysen,
som räknar om allt enligt guiden.

Sifferkriterier (max 12 p): Profil A 1, 2, 5, 6, 7, 10. Profil B 1, 2, 3, 5, 7, 10.
Kriterierna 3/6 (kundkoncentration), 8 (guidning) och 9 (insiders) kräver läsning (6 p).
Ett bolag behöver minst 8 av 12 för att kunna nå 14 av 18 och grönt ljus.

Körning:
  python3 sallning.py --marknad se     en marknad, skriver delar/se.json
  python3 sallning.py --sla-ihop       slår ihop delar/*.json till sallning.json och arkiv/
  python3 sallning.py                  alla marknader efter varandra och sedan ihopslagning
"""
import glob, json, os, re, sys, time, datetime, urllib.request, urllib.parse, urllib.error, http.cookiejar

MIN_POANG = 8            # minsta möjliga snabbpoäng för att bli kandidat
SKATT = 0.79             # ROIC = EBIT × 0,79 / (eget kapital + räntebärande nettoskuld)
GRANS_NORDEN_SEK = 2e9   # minsta börsvärde i Norden, i kronor
ANTAL_FORSLAG = 5        # förslag per månad, de bästa oavsett marknad

USA_BORSER = ["NYQ", "NMS", "NGM", "NCM", "ASE"]  # NYSE, Nasdaq (tre nivåer), NYSE American. Inte OTC.
MARKNADER = {
    "se": {"land": "Sverige", "region": "se", "borser": ["STO"], "suffix": ".ST", "valuta": "SEK"},
    "no": {"land": "Norge", "region": "no", "borser": ["OSL"], "suffix": ".OL", "valuta": "NOK"},
    "dk": {"land": "Danmark", "region": "dk", "borser": ["CPH"], "suffix": ".CO", "valuta": "DKK"},
    "fi": {"land": "Finland", "region": "fi", "borser": ["HEL"], "suffix": ".HE", "valuta": "EUR"},
    "us-stora": {"land": "USA", "region": "us", "borser": USA_BORSER, "suffix": "", "valuta": "USD", "min": 10e9},
    "us-mellan": {"land": "USA", "region": "us", "borser": USA_BORSER, "suffix": "", "valuta": "USD", "min": 2e9, "max": 10e9},
}
ORDNING = ["se", "no", "dk", "fi", "us-stora", "us-mellan"]  # vid dubbelnotering behålls den första (lägre valutakostnad)
VALUTA_RESERV = {"SEK": 1.0, "NOK": 0.93, "DKK": 1.47, "EUR": 11.0, "USD": 9.5}  # bara om kurserna inte går att hämta


def uteslut(sektor, bransch):
    """Bolag som guidens nyckeltal inte passar för. Returnerar orsaken eller None."""
    bransch = bransch or ""
    if sektor == "Real Estate":
        return "fastighetsbolag (räknas inte med guidens nyckeltal)"
    if bransch.startswith("Banks") or bransch == "Mortgage Finance":
        return "bank (egen modul i guiden)"
    if bransch.startswith("Insurance -"):
        return "försäkringsbolag (guidens nyckeltal passar inte)"
    if bransch == "Asset Management":
        return "investmentbolag eller kapitalförvaltare (investmentbolag har egen modul)"
    if bransch == "Shell Companies":
        return "SPAC eller skalbolag"
    return None


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


def valutakurser():
    """Kronor per enhet av varje valuta, från Yahoo (senaste stängning)."""
    kurser, fel = dict(VALUTA_RESERV), []
    for val in ("NOK", "DKK", "EUR", "USD"):
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%sSEK=X?range=5d&interval=1d" % val
        data, f = hamta_json(url, forsok=2)
        try:
            kurser[val] = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
        except Exception:  # noqa: BLE001
            fel.append("%s: %s" % (val, f or "saknas"))
    return kurser, fel


def screener(villkor, minsta, storsta=None):
    crumb = skaffa_crumb()
    storlek = [{"operator": "gt", "operands": ["intradaymarketcap", minsta]}]
    if storsta:
        storlek.append({"operator": "lt", "operands": ["intradaymarketcap", storsta]})
    alla, offset, total = [], 0, None
    while total is None or offset < total:
        kropp = {
            "size": 250, "offset": offset, "sortField": "intradaymarketcap", "sortType": "DESC",
            "quoteType": "EQUITY", "userId": "", "userIdType": "guid",
            "query": {"operator": "AND", "operands": [villkor] + storlek},
        }
        url = "https://query2.finance.yahoo.com/v1/finance/screener?formatted=false&lang=en-US&region=US&crumb=" + urllib.parse.quote(crumb)
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


def hamta_citat(symboler):
    """Kursdata för enstaka symboler (bevakade bolag som screenern missar)."""
    if not symboler:
        return []
    url = ("https://query2.finance.yahoo.com/v7/finance/quote?symbols=%s&crumb=%s"
           % (urllib.parse.quote(",".join(symboler)), urllib.parse.quote(skaffa_crumb())))
    data, fel = hamta_json(url, forsok=2)
    if data is None:
        print("quote-fel:", fel)
    return ((data or {}).get("quoteResponse") or {}).get("result") or []


def hor_till(m, q):
    """Hör aktien till marknaden? Rätt börs, inga preferensaktier, warranter eller utländska noteringar."""
    sym = q.get("symbol") or ""
    namn = (q.get("longName") or q.get("shortName") or "").lower()
    if "PREF" in sym.upper() or "pref" in namn:
        return False
    if m["suffix"]:
        return sym.endswith(m["suffix"])
    # USA: vanliga aktier (BRK-B går bra, BAC-PL och liknande gör det inte) på NYSE eller Nasdaq
    return bool(re.fullmatch(r"[A-Z]{1,5}(-[A-D])?", sym)) and q.get("exchange") in m["borser"]


def hamta_univers(m, minsta, storsta, extra, diagnos):
    """Alla aktier på marknaden mellan minsta och största börsvärde. Två frågor (region och börs),
    eftersom Yahoo saknar region eller börs för vissa bolag, plus bevakade bolag som ändå saknas."""
    per_sym, fel = {}, []
    borsvillkor = {"operator": "or", "operands": [{"operator": "eq", "operands": ["exchange", b]} for b in m["borser"]]}
    for namn, villkor in (("region", {"operator": "eq", "operands": ["region", m["region"]]}), ("borser", borsvillkor)):
        try:
            svar = screener(villkor, minsta, storsta)
            diagnos[namn] = len(svar)
            for q in svar:
                if hor_till(m, q):
                    per_sym.setdefault(q.get("symbol"), q)
        except Exception as e:  # noqa: BLE001
            diagnos[namn] = "fel: %s" % e
            fel.append(str(e))
    if not per_sym:
        raise RuntimeError("; ".join(fel) or "screenern gav inga bolag")
    saknas = [s for s in extra if s not in per_sym]
    citat = hamta_citat(saknas)
    diagnos["bevakadeSomSaknades"] = saknas
    for q in citat:
        # Bevakade bolag tas med även när Yahoo saknar börsvärde, så att de markeras som redan bevakade.
        if q.get("quoteType") not in (None, "EQUITY"):
            continue  # t.ex. indexfonden URTH
        mc = q.get("marketCap")
        if mc is None or (mc >= minsta and (storsta is None or mc < storsta)):
            per_sym[q["symbol"]] = q
    return list(per_sym.values())


SUFFIX = re.compile(r"\b(publ|ab|asa|as|oyj|abp|inc|incorporated|corp|corporation|co|company|plc|ltd|limited|sa|nv|se|ag|"
                    r"ser|class|sdb|adr|ads|the)\b")


def namnnyckel(namn):
    """Bolagsnamn utan bolagsform och aktieserie, för att hitta samma bolag på flera börser."""
    s = (namn or "").lower().replace("a/s", " ").replace("&", " and ")
    s = re.sub(r"[^a-z0-9åäöæø ]", " ", s)
    s = SUFFIX.sub(" ", s)
    s = re.sub(r"\s+[a-d]\s*$", " ", s)  # aktieserie sist (ser. B, Class A)
    return re.sub(r"\s+", "", s)


def rot(sym):
    """Symbol utan börs och aktieserie: INVE-A.ST och INVE-B.ST blir INVE, BRK-B blir BRK."""
    s = re.sub(r"\.(ST|OL|CO|HE)$", "", sym.upper())
    return re.sub(r"-[A-D]$", "", s)


def rensa_univers(citat):
    """En aktieserie per bolag (den mest handlade), största bolaget först."""
    bast = {}
    for q in citat:
        nyckel = namnnyckel(q.get("longName") or q.get("shortName") or q.get("symbol"))
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
    n["roicUppblast"] = bool(EK[-1]) and (SKULD[-1] or 0) - (KASSA[-1] or 0) < -0.5 * EK[-1]
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


# ---------------------------------------------------------------- en marknad

def bevakade():
    """Bevakade bolag (symboler.json): rot → id, och Yahoo-symbolerna."""
    try:
        sym = json.load(open("symboler.json"))
    except Exception:  # noqa: BLE001
        sym = {}
    sym = {bid: s for bid, s in sym.items() if not s.startswith("^") and "=" not in s}
    return {rot(s): bid for bid, s in sym.items()}, list(sym.values())


def sall_marknad(kod):
    start = time.time()
    m = MARKNADER[kod]
    redan, bev_sym = bevakade()
    del_ut = {"marknad": kod, "land": m["land"], "status": "ok", "fel": [], "diagnos": {},
              "kandidater": [], "ovriga": [], "uteslutna": [], "dataSaknas": []}
    fx, fxfel = valutakurser()
    del_ut["diagnos"]["valutor"] = fx
    if fxfel:
        del_ut["diagnos"]["valutafel"] = fxfel
    minsta = m.get("min") or GRANS_NORDEN_SEK / fx[m["valuta"]]
    storsta = m.get("max")
    del_ut["diagnos"]["borsvarde"] = {"min": round(minsta), "max": storsta, "valuta": m["valuta"]}
    extra = [s for s in bev_sym if hor_till(m, {"symbol": s, "exchange": m["borser"][0]})]
    try:
        univers = rensa_univers(hamta_univers(m, minsta, storsta, extra, del_ut["diagnos"]))
    except Exception as e:  # noqa: BLE001
        del_ut["status"] = "fel"
        del_ut["fel"].append("%s: urvalet kunde inte hämtas: %s" % (m["land"], e))
        return del_ut
    print(kod, "urval:", len(univers), "bolag")

    for q in univers:
        sym = q["symbol"]
        mc, val = q.get("marketCap") or 0, q.get("currency")
        bas = {
            "symbol": sym, "namn": q.get("longName") or q.get("shortName"), "marknad": m["land"], "marknadKod": kod,
            "borsvardeMdr": round(mc / 1e9, 1), "borsvardeMdrSEK": round(mc * fx[val] / 1e9, 1) if val in fx else None,
            "kurs": q.get("regularMarketPrice"), "valuta": val,
            "pe": round(q["trailingPE"], 1) if q.get("trailingPE") else None,
            "finnsRedan": redan.get(rot(sym)), "sektor": None, "bransch": None,
        }
        b, bfel = hamta_bokslut(sym)
        if b is None:
            bas["orsak"] = "bokslutet kunde inte hämtas: " + bfel
            del_ut["dataSaknas"].append(bas)
            continue
        n, orsak = rakna(b)
        if n is None:
            bas["orsak"] = orsak
            del_ut["dataSaknas"].append(bas)
            time.sleep(0.2)
            continue

        profil_bokstav = "B" if (n["tillvaxt"] or 0) >= 0.15 else "A"
        k, osakra = poang_a(n) if profil_bokstav == "A" else poang_b(n, {})
        v = veton(n)
        hart_veto = any(not x.startswith("veto 3") for x in v)
        poang = sum(p for p, _ in k.values())
        # Bransch och estimat hämtas bara för bolag som kan bli kandidater (estimatet kan ge Profil B 1p till).
        pfel = ""
        if poang + osakra + (1 if profil_bokstav == "B" else 0) >= MIN_POANG and not hart_veto:
            profil, pfel = hamta_profil(sym)
            bas["sektor"], bas["bransch"] = profil.get("sektor"), profil.get("bransch")
            orsak = uteslut(bas["sektor"], bas["bransch"])
            if orsak:
                bas["orsak"] = orsak
                del_ut["uteslutna"].append(bas)
                time.sleep(0.2)
                continue
            if profil_bokstav == "B":
                k, osakra = poang_b(n, profil)
                poang = sum(p for p, _ in k.values())

        flaggor = []
        if n["ebitFall"] > 0.30:
            hur = "gick från vinst till förlust" if n["ebitFall"] >= 1 else "föll %d %%" % (100 * n["ebitFall"])
            flaggor.append("möjligen cykliskt: EBIT %s under åren %s–%s (cykeltestet kräver 15 år)" % (hur, n["ar"][0], n["ar"][-1]))
        if n["roicUppblast"]:
            flaggor.append("ROIC blir hög för att nettokassan drar ner kapitalet (guidens formel)")
        if pfel:
            flaggor.append("bransch och estimat saknas (%s)" % pfel)
        bas.update({
            "profil": profil_bokstav, "snabbpoang": poang, "mojligPoang": poang + osakra,
            "kriterier": {str(nr): {"poang": p, "varde": txt} for nr, (p, txt) in sorted(k.items())},
            "veto": v, "flaggor": flaggor, "bokslutAr": n["ar"], "redovisningsvaluta": b.get("_valuta"),
            "roic": pct(n["roic"]), "ebitMarginal": pct(n["ebitMarginal"]), "tillvaxt": pct(n["tillvaxt"]),
            "nettoskuldEbitda": n["nettoskuldEbitda"], "fcfKonvertering": pct(n["fcfKonvertering"]),
        })
        if bas["mojligPoang"] >= MIN_POANG and not hart_veto:
            del_ut["kandidater"].append(bas)
        else:
            del_ut["ovriga"].append(bas)
        print("%-12s %s %2dp %s" % (sym, profil_bokstav, poang, "; ".join(v)))
        time.sleep(0.2)

    if len(del_ut["dataSaknas"]) > len(univers) / 2:
        del_ut["status"] = "ofullständig"
        del_ut["fel"].append("%s: bokslut saknas för mer än hälften av bolagen" % m["land"])
    del_ut["antal"] = {"urval": len(univers), **{k: len(del_ut[k]) for k in ("kandidater", "ovriga", "uteslutna", "dataSaknas")}}
    del_ut["sekunder"] = round(time.time() - start)
    return del_ut


# ---------------------------------------------------------------- ihopslagning

LISTOR = ("kandidater", "ovriga", "uteslutna", "dataSaknas")


def sla_ihop():
    idag = datetime.date.today()
    try:
        forra = json.load(open("sallning.json"))
    except Exception:  # noqa: BLE001
        forra = {}
    ut = {
        "hamtad": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system": "Masterguide v4.8", "kalla": "Yahoo Finance (screener, fundamentals-timeseries, quoteSummary)",
        "regler": {"borsvardeMinNordenSEK": GRANS_NORDEN_SEK, "borsvardeMinUSA_USD": MARKNADER["us-mellan"]["min"],
                   "marknader": sorted({m["land"] for m in MARKNADER.values()}),
                   "minSnabbpoang": MIN_POANG, "maxSnabbpoang": 12, "antalForslag": ANTAL_FORSLAG},
        "status": "ok", "fel": [], "diagnos": {}, "antal": {}, "forslag": [], **{k: [] for k in LISTOR},
    }
    lyckade = 0
    poster = []  # (lista, post) i ORDNING, så att dubbelnoterade bolag behåller den första marknaden
    for kod in ORDNING:
        try:
            d = json.load(open("delar/%s.json" % kod))
        except Exception:  # noqa: BLE001
            d = {"status": "fel", "fel": ["%s: ingen körning hittades" % MARKNADER[kod]["land"]]}
        ut["diagnos"][kod] = {"status": d.get("status"), "antal": d.get("antal"), "sekunder": d.get("sekunder"),
                              **(d.get("diagnos") or {})}
        ut["fel"] += d.get("fel") or []
        if d.get("status") == "fel":
            # Behåll marknadens bolag från förra körningen, så att ett tillfälligt fel inte tömmer listan.
            for lista in LISTOR:
                poster += [(lista, x) for x in forra.get(lista, []) if x.get("marknadKod") == kod]
            continue
        lyckade += 1
        for lista in LISTOR:
            poster += [(lista, x) for x in d.get(lista, [])]

    if lyckade == 0:
        forra["status"] = "fel"
        forra["fel"] = ["%s: ingen marknad kunde hämtas" % idag.isoformat()] + ut["fel"]
        json.dump(forra or ut, open("sallning.json", "w"), ensure_ascii=False, indent=1)
        print("Ingen marknad kunde hämtas")
        return
    if lyckade < len(ORDNING):
        ut["status"] = "delvis"

    sedda_sym, sedda_namn = set(), {}
    for lista, x in poster:
        if x["symbol"] in sedda_sym:
            continue
        nyckel = namnnyckel(x.get("namn")) or x["symbol"]
        if nyckel in sedda_namn:
            sedda_namn[nyckel].setdefault("ocksaNoterad", []).append(x["symbol"])
            if x.get("finnsRedan") and not sedda_namn[nyckel].get("finnsRedan"):
                sedda_namn[nyckel]["finnsRedan"] = x["finnsRedan"]
            continue
        sedda_sym.add(x["symbol"])
        sedda_namn[nyckel] = x
        ut[lista].append(x)

    nyckel = lambda x: (-x.get("snabbpoang", 0), -(x.get("roic") or -999))  # noqa: E731
    ut["kandidater"].sort(key=nyckel)
    ut["ovriga"].sort(key=nyckel)
    ut["forslag"] = [x["symbol"] for x in ut["kandidater"] if not x["finnsRedan"] and not x["veto"]][:ANTAL_FORSLAG]
    ut["antal"] = {k: len(ut[k]) for k in LISTOR}
    ut["antal"]["nyaKandidater"] = sum(1 for x in ut["kandidater"] if not x["finnsRedan"])
    ut["antal"]["kandidaterPerMarknad"] = {}
    for x in ut["kandidater"]:
        ut["antal"]["kandidaterPerMarknad"][x["marknad"]] = ut["antal"]["kandidaterPerMarknad"].get(x["marknad"], 0) + 1
    json.dump(ut, open("sallning.json", "w"), ensure_ascii=False, indent=1)
    os.makedirs("arkiv", exist_ok=True)
    json.dump(ut, open("arkiv/sallning-%s.json" % idag.strftime("%Y-%m"), "w"), ensure_ascii=False, indent=1)
    print("Klart:", ut["status"], ut["antal"], ut["forslag"])


def main(argv):
    if "--sla-ihop" in argv:
        sla_ihop()
        return
    koder = [argv[argv.index("--marknad") + 1]] if "--marknad" in argv else ORDNING
    os.makedirs("delar", exist_ok=True)
    for kod in koder:
        d = sall_marknad(kod)
        json.dump(d, open("delar/%s.json" % kod, "w"), ensure_ascii=False, indent=1)
        print(kod, d["status"], d.get("antal"), d["fel"])
    if "--marknad" not in argv:
        sla_ihop()


if __name__ == "__main__":
    main(sys.argv[1:])
