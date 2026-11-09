# kurser
Dagliga stängningskurser och nyheter för bevakningssystemet (GitHub Actions).

- `kurser.json`: stängningskurser från Yahoo Finance (`hamta.py`, symbolerna i `symboler.json`). Workflow: Kurser.
- `nyheter.json`: nyheter från de senaste 5 dagarna per bolag, från MFN, Yahoo Finance och Google News (`nyheter.py`, bolagen i `nyhetsbolag.json`). Google News-länkar avkodas till artikelns riktiga adress (`lank`; Google-länken i `glank`). Workflow: Nyheter.
- Workflow **Morgon**: startar på kvällen eller natten (GitHub startar schemalagda körningar 4–9 timmar sent) och väntar till 06.10 svensk tid, hämtar sedan både kurser och nyheter, så att filerna är färska när den dagliga aktiebevakningen startar 06.39. Test: skriv en tid (HH:MM) i `trigger/morgon.txt` och pusha.

- `sallning.json`: månadens sållning efter nya bolag (`sallning.py`). Sverige, Norge, Danmark och Finland (börsvärde över 2 mdr kr) och USA (NYSE och Nasdaq, över 2 mdr USD), från Yahoo Finance. Poängsatta på Masterguidens sifferkriterier (max 12 p, kandidat från 8 p utan veto 1–2). `forslag` = de fem bästa nya kandidaterna. En kopia per månad sparas i `arkiv/`. Workflow: Sallning, den 1:a varje månad, en marknad per jobb.

Nytt bolag: lägg till det i `symboler.json` (kurser) och `nyhetsbolag.json` (nyheter) med samma id som i bevakningsdatabasen.
