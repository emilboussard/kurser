# Starta en hämtning direkt

GitHub startar schemalagda körningar ibland flera timmar sent. Den dagliga aktiebevakningen skriver därför tiden i
`nyheter.txt` eller `kurser.txt` här och pushar när filen den behöver är gammal. Pushen startar workflowen Nyheter
eller Kurser direkt. Filerna innehåller bara en tidsstämpel.

`morgon.txt`: en tid (HH:MM, svensk tid). En push startar workflowen Morgon, som väntar till den tiden och hämtar både kurser och nyheter (för test eller en hämtning vid en viss tid).
