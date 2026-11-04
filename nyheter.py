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
    return list(per_grupp.values()), flode.get("items", [])[:1]


def nyckel(titel):
    return re.sub(r"[^a-z0-9åäö]+", "", titel.lower())[:70]


def handlar_om(titel, ord_):
    """Sant om rubriken nämner bolaget (något av orden). Sorterar bort allmänna marknadsnyheter."""
    t = titel.lower()
    return any(o.lower() in t for o in ord_)


def mfn_felsok(exempel):
    """Tillfällig felsökning: sparar ett rått MFN-objekt i mfn_debug.json så att fälten kan kontrolleras."""
    json.dump(exempel, open("mfn_debug.json", "w"), ensure_ascii=False, indent=1)


def main():
    bolag = json.load(open("nyhetsbolag.json"))
    bolag.pop("_info", None)
    nu = datetime.datetime.now(datetime.timezone.utc)
    grans = nu - datetime.timedelta(days=DAGAR)
    ut = {"hamtad": iso(nu), "dagar": DAGAR, "kallor": "MFN, Yahoo Finance RSS, Google News RSS", "bolag": {}}
    felsok = []
    for bid, cfg in bolag.items():
        poster, fel, antal = [], {}, {}
        kallor = []
        if cfg.get("mfn"):
            def fran_mfn(s=cfg["mfn"], bid=bid):
                poster_mfn, exempel = hamta_mfn(s)
                if bid == "smart-eye":
                    felsok.extend(exempel)
                return poster_mfn
            kallor.append(("mfn", fran_mfn))
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
    json.dump(ut, open("nyheter.json", "w"), ensure_ascii=False, indent=1)
    mfn_felsok(felsok)


if __name__ == "__main__":
    main()
