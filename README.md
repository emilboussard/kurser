# kurser
Dagliga stängningskurser och nyheter för bevakningssystemet (GitHub Actions).

- `kurser.json`: stängningskurser från Yahoo Finance (`hamta.py`, symbolerna i `symboler.json`). Workflow: Kurser.
- `nyheter.json`: nyheter från de senaste 5 dagarna per bolag, från MFN, Yahoo Finance och Google News (`nyheter.py`, bolagen i `nyhetsbolag.json`). Workflow: Nyheter, vardagar före 06.39.

Nytt bolag: lägg till det i `symboler.json` (kurser) och `nyhetsbolag.json` (nyheter) med samma id som i bevakningsdatabasen.
