"""Hämtar färska nyheter för bolagen i nyhetsbolag.json och skriver nyheter.json.

Källor per bolag (alla är frivilliga, ett fel i en källa stoppar inte de andra):
- Yahoo Finance RSS (symbol)
- Google News RSS (sökfraser på svenska eller engelska)
- MFN (pressmeddelanden för svenska bolag)

Bara nyheter från de senaste DAGAR dagarna sparas, nyast först, högst MAX_PER_BOLAG per bolag.
Den dagliga aktiebevakningen läser filen via raw.githubusercontent.com.
"""
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


def las_mfn(data, slug):
    """Plockar pressmeddelanden (datum, titel, länk) ur MFN:s bolagssida."""
    text = data.decode("utf-8", "replace")
    ut = []
    # Varje pressmeddelande har en länk /a/<slug>/<meddelande> och ett datum ÅÅÅÅ-MM-DD HH:MM i närheten.
    for m in re.finditer(r'href="((?:https://(?:www\.)?mfn\.se)?/(?:all/)?a/%s/[^"#?]+)"[^>]*>(.*?)</a>' % re.escape(slug), text, re.S):
        lank, titel = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        titel = html.unescape(re.sub(r"\s+", " ", titel)).strip()
        if len(titel) < 8:
            continue
        omrade = text[max(0, m.start() - 600): m.end() + 600]
        d = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", omrade)
        if not d:
            continue
        tid = datetime.datetime.strptime(d.group(1) + " " + d.group(2), "%Y-%m-%d %H:%M")
        tid = tid.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=1)))  # svensk tid, ungefär
        if not lank.startswith("http"):
            lank = "https://mfn.se" + lank
        ut.append({"tid": tid, "titel": titel, "kalla": "MFN (pressmeddelande)", "lank": lank, "fran": "mfn"})
    return ut


def nyckel(titel):
    return re.sub(r"[^a-z0-9åäö]+", "", titel.lower())[:70]


def handlar_om(titel, ord_):
    """Sant om rubriken nämner bolaget (något av orden). Sorterar bort allmänna marknadsnyheter."""
    t = titel.lower()
    return any(o.lower() in t for o in ord_)


def mfn_felsok():
    """Tillfällig felsökning: hur ser MFN:s sidor ut från GitHub? Skriver mfn_debug.json."""
    ut = {}
    try:
        data = hamta("https://mfn.se/all/a/smart-eye").decode("utf-8", "replace")
        d = re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", data)
        ut["datum_kring"] = data[max(0, d.start() - 1500): d.end() + 1500] if d else "inget datum"
        ut["hrefs"] = re.findall(r'href="([^"]*smart-eye/[^"]*)"', data)[:25]
        ut["uuid"] = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", data)[:10]
        ut["feed"] = re.findall(r'(https?://feed\.mfn\.se[^"\' <]*)', data)[:10]
    except Exception as e:  # noqa: BLE001
        ut["fel"] = str(e)[:200]
    for uuid in dict.fromkeys(ut.get("uuid", [])):
        for url in ("https://feed.mfn.se/v1/feed/%s.rss" % uuid, "https://feed.mfn.se/v1/feed/%s.json" % uuid):
            try:
                ut[url] = hamta(url).decode("utf-8", "replace")[:1500]
            except Exception as e:  # noqa: BLE001
                ut[url] = "fel: " + str(e)[:150]
    json.dump(ut, open("mfn_debug.json", "w"), ensure_ascii=False, indent=1)


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
            kallor.append(("mfn", MFN.format(slug=cfg["mfn"]), lambda d, s=cfg["mfn"]: las_mfn(d, s)))
        if cfg.get("yahoo"):
            kallor.append(("yahoo", YAHOO.format(s=urllib.parse.quote(cfg["yahoo"])), lambda d: las_rss(d, "yahoo")))
        for sprak, fras in cfg.get("sok", []):
            q = urllib.parse.quote("%s when:%dd" % (fras, DAGAR))
            kallor.append(("google-" + sprak, GOOGLE[sprak].format(q=q), lambda d, f="google-" + sprak: las_rss(d, f)))
        ord_ = cfg.get("ord") or [cfg.get("namn", bid)]
        for namn, url, tolka in kallor:
            try:
                p = [x for x in tolka(hamta(url)) if x["tid"] >= grans and (namn == "mfn" or handlar_om(x["titel"], ord_))]
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
        ut["bolag"][bid] = {"namn": cfg.get("namn", bid), "antal": antal, "fel": fel, "nyheter": unika}
        print(bid, len(unika), antal, fel or "")
    json.dump(ut, open("nyheter.json", "w"), ensure_ascii=False, indent=1)
    mfn_felsok()


if __name__ == "__main__":
    main()
