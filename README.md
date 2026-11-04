# kurser
Dagliga stängningskurser och nyheter för bevakningssystemet (GitHub Actions).

- `kurser.json`: stängningskurser från Yahoo Finance (`hamta.py`, symbolerna i `symboler.json`). Workflow: Kurser.
- `nyheter.json`: nyheter från de senaste 5 dagarna per bolag, från MFN, Yahoo Finance och Google News (`nyheter.py`, bolagen i `nyhetsbolag.json`). Workflow: Nyheter, vardagar före 06.39.

- `sallning.json`: månadens sållning efter nya bolag (`sallning.py`). Alla svenska aktier med börsvärde över 2 mdr kr från Yahoo Finance, poängsatta på Masterguidens sifferkriterier (max 12 p, kandidat från 8 p utan veto 1–2). En kopia per månad sparas i `arkiv/`. Workflow: Sallning, den 1:a varje månad.

Nytt bolag: lägg till det i `symboler.json` (kurser) och `nyhetsbolag.json` (nyheter) med samma id som i bevakningsdatabasen.
