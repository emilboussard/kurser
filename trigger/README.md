# Starta en hämtning direkt

GitHub startar schemalagda körningar ibland flera timmar sent. Den dagliga aktiebevakningen skriver därför tiden i
`nyheter.txt` eller `kurser.txt` här och pushar när filen den behöver är gammal. Pushen startar workflowen Nyheter
eller Kurser direkt. Filerna innehåller bara en tidsstämpel.
