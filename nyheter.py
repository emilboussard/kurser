"""Hämtar färska nyheter för bolagen i nyhetsbolag.json och skriver nyheter.json.

Källor per bolag (alla är frivilliga, ett fel i en källa stoppar inte de andra):
- Yahoo Finance RSS (symbol)
- Google News RSS (sökfraser på svenska eller engelska)
- MFN (pressmeddelanden för svenska bolag)

Bara nyheter från de senaste DAGAR dagarna sparas, nyast först, högst MAX_PER_BOLAG per bolag.
Google News-länkar (news.google.com/rss/articles/...) avkodas till artikelns riktiga adress (kortare och utan
Googles samtyckessida på telefonen). Den riktiga adressen står i lank och Google-länken i glank. Redan avkodade
länkar återanvänds från förra nyheter.json; högst MAX_AVKODA nya per körning, nyast först.
Den dagliga aktiebevakningen läser filen ur sin klon av repot.
"""
import base64
import datetime
import email.utils
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DAGAR = 5
MAX_PER_BOLAG = 20
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
GOOGLE = {
    "sv": "https://news.google.com/rss/search?q={q}&hl=sv&gl=SE&ceid=SE:sv",
    "en": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
}
YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={s}&region=US&lang=en-US"
MFN = "https://mfn.se/all/a/{slug}"


def hamta(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def iso(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def las_rss(data, fran):
    """Läser en RSS-fil och returnerar poster {tid, titel, kalla, lank, fran}."""
    rot = ET.fromstring(data)
    ut = []
    for item in rot.iter("item"):
        titel = html.unescape((item.findtext("title") or "").strip())
        lank = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        if not titel or not lank or not pub:
            continue
        try:
            tid = email.utils.parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            continue
        if tid.tzinfo is None:
            tid = tid.replace(tzinfo=datetime.timezone.utc)
        kalla_el = item.find("source")
        kalla = (kalla_el.text or "").strip() if kalla_el is not None and kalla_el.text else ""
        if fran.startswith("google") and kalla and titel.endswith(" - " + kalla):
            titel = titel[: -len(" - " + kalla)]
        if fran == "yahoo" and not kalla:
            kalla = "Yahoo Finance"
        ut.append({"tid": tid, "titel": titel, "kalla": kalla, "lank": lank, "fran": fran})
    return ut


def tolka_tid(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            t = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def hamta_mfn(slug):
    """Pressmeddelanden från MFN:s JSON-flöde. Bolagets id (entity_id) står på bolagssidan mfn.se/all/a/<slug>."""
    sida = hamta(MFN.format(slug=slug)).decode("utf-8", "replace")
    m = re.search(r"subscribe-tray-v2-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", sida) or \
        re.search(r'"entity_id"\s*:\s*"([0-9a-f-]{36})"', sida)
    if not m:
        raise ValueError("hittade inget entity_id på mfn.se/all/a/%s" % slug)
    flode = json.loads(hamta("https://feed.mfn.se/v1/feed/%s.json?limit=30" % m.group(1)))
    per_grupp = {}
    for it in flode.get("items", []):
        c = it.get("content") or {}
        p = it.get("properties") or {}
        titel = (c.get("title") or it.get("title") or "").strip()
        tid = tolka_tid(c.get("publish_date") or it.get("date_published") or it.get("publish_date"))
        if not titel or not tid:
            continue
        cslug = c.get("slug")
        lank = "https://mfn.se/a/%s/%s" % (slug, cslug) if cslug else (it.get("url") or "")
        sprak = p.get("lang") or c.get("lang") or ""
        post = {"tid": tid, "titel": html.unescape(titel), "kalla": "MFN (pressmeddelande)", "lank": lank, "fran": "mfn", "sprak": sprak}
        # Samma pressmeddelande på svenska och engelska har samma group_id: behåll den svenska.
        g = it.get("group_id") or it.get("news_id") or lank
        if g not in per_grupp or (sprak == "sv" and per_grupp[g].get("sprak") != "sv"):
            per_grupp[g] = post
    return list(per_grupp.values())


MAX_AVKODA = 120
GN_RE = re.compile(r"^https://news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_-]+)")
GN_BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def gn_id(lank):
    m = GN_RE.match(lank or "")
    return m.group(1) if m else None


def gn_gammalt_format(gid):
    """Äldre Google-id:n har adressen direkt i base64. Nya (AU_yqL...) kräver ett anrop."""
    try:
        b = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except (ValueError, TypeError):
        return None
    if b"AU_yqL" in b:
        return None
    m = re.search(rb"https?://[\x21-\x7e]+", b)
    return m.group(0).decode() if m else None


def gn_avkoda(gid):
    """Artikelns riktiga adress för ett Google News-id, eller None."""
    url = gn_gammalt_format(gid)
    if url:
        return url
    sida = hamta("https://news.google.com/rss/articles/%s" % gid).decode("utf-8", "replace")
    sg = re.search(r'data-n-a-sg="([^"]+)"', sida)
    ts = re.search(r'data-n-a-ts="([^"]+)"', sida)
    if not sg or not ts:
        raise ValueError("ingen signatur på artikelsidan")
    inre = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
            '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (gid, ts.group(1), sg.group(1)))
    data = ("f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inre]]]))).encode()
    req = urllib.request.Request(GN_BATCH, data=data, headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        svar = r.read().decode("utf-8", "replace")
    for rad in json.loads(svar.split("\n\n", 1)[1]):
        if isinstance(rad, list) and len(rad) > 2 and rad[0] == "wrb.fr" and rad[2]:
            res = json.loads(rad[2])
            if isinstance(res, list) and len(res) > 1 and str(res[1]).startswith("http"):
                return res[1]
    raise ValueError("inget svar från Google")


def avkoda_lankar(ut):
    """Byter Google News-länkar mot riktiga adresser. Återanvänder förra filens avkodningar."""
    cache = {}
    try:
        for b in json.load(open("nyheter.json")).get("bolag", {}).values():
            for x in b.get("nyheter", []):
                g = gn_id(x.get("glank"))
                if g and x.get("lank") and not gn_id(x["lank"]):
                    cache[g] = x["lank"]
    except (OSError, ValueError):
        pass
    alla = [x for b in ut["bolag"].values() for x in b["nyheter"] if gn_id(x["lank"])]
    alla.sort(key=lambda x: x["tid"], reverse=True)
    nya = fel_i_rad = fran_cache = 0
    for x in alla:
        gid = gn_id(x["lank"])
        url = cache.get(gid)
        if url:
            fran_cache += 1
        elif nya < MAX_AVKODA and fel_i_rad < 5:
            nya += 1
            try:
                url = gn_avkoda(gid)
                cache[gid] = url
                fel_i_rad = 0
            except Exception as e:  # noqa: BLE001
                fel_i_rad += 1
                print("avkodning misslyckades:", str(e)[:120])
            time.sleep(0.3)
        if url:
            x["glank"] = x["lank"]
            x["lank"] = url
    kvar = sum(1 for x in alla if gn_id(x["lank"]))
    print("Google-länkar: %d från förra filen, %d nya försök, %d kvar som Google-länk" % (fran_cache, nya, kvar))
    ut["googleKvar"] = kvar


def nyckel(titel):
    return re.sub(r"[^a-z0-9åäö]+", "", titel.lower())[:70]


def handlar_om(titel, ord_):
    """Sant om rubriken nämner bolaget (något av orden). Sorterar bort allmänna marknadsnyheter."""
    t = titel.lower()
    return any(o.lower() in t for o in ord_)


def main():
    bolag = json.load(open("nyhetsbolag.json"))
    bolag.pop("_info", None)
    nu = datetime.datetime.now(datetime.timezone.utc)
    grans = nu - datetime.timedelta(days=DAGAR)
    ut = {"hamtad": iso(nu), "dagar": DAGAR, "kallor": "MFN, Yahoo Finance RSS, Google News RSS", "bolag": {}}
    for bid, cfg in bolag.items():
        poster, fel, antal = [], {}, {}
        kallor = []
        if cfg.get("mfn"):
            kallor.append(("mfn", lambda s=cfg["mfn"]: hamta_mfn(s)))
        if cfg.get("yahoo"):
            url = YAHOO.format(s=urllib.parse.quote(cfg["yahoo"]))
            kallor.append(("yahoo", lambda u=url: las_rss(hamta(u), "yahoo")))
        for sprak, fras in cfg.get("sok", []):
            q = urllib.parse.quote("%s when:%dd" % (fras, DAGAR))
            url = GOOGLE[sprak].format(q=q)
            kallor.append(("google-" + sprak, lambda u=url, f="google-" + sprak: las_rss(hamta(u), f)))
        ord_ = cfg.get("ord") or [cfg.get("namn", bid)]
        for namn, hamtare in kallor:
            try:
                p = [x for x in hamtare() if x["tid"] >= grans and (namn == "mfn" or handlar_om(x["titel"], ord_))]
                antal[namn] = antal.get(namn, 0) + len(p)
                poster += p
            except Exception as e:  # noqa: BLE001
                fel[namn] = str(e)[:200]
            time.sleep(0.5)
        # Samma nyhet från flera källor: behåll den första (MFN, sedan Yahoo, sedan Google).
        sett, unika = set(), []
        for x in poster:
            k = nyckel(x["titel"])
            if k in sett:
                continue
            sett.add(k)
            unika.append(x)
        unika.sort(key=lambda x: x["tid"], reverse=True)
        unika = unika[:MAX_PER_BOLAG]
        for x in unika:
            x["tid"] = iso(x["tid"])
            x.pop("sprak", None)
        ut["bolag"][bid] = {"namn": cfg.get("namn", bid), "antal": antal, "fel": fel, "nyheter": unika}
        print(bid, len(unika), antal, fel or "")
    try:
        avkoda_lankar(ut)
    except Exception as e:  # noqa: BLE001  Länkarna är en bonus; filen sparas ändå.
        print("avkodningen avbröts:", str(e)[:200])
    json.dump(ut, open("nyheter.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
