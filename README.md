# NOAA GFS France — cartes et prévisions WordPress

Ce dépôt produit les cartes interactives et les prévisions GFS de la France métropolitaine et de la Corse. Les données GRIB2 publiques du modèle NOAA/NCEP GFS 0,25° sont traitées chaque matin, puis publiées sur la branche `data` pour le module WordPress.

Le module comprend les cartes lissées, les frontières départementales, les isobares et flèches de vent, les tableaux communaux, les diagnostics orage/neige et les cumuls à période personnalisée.

## Mise en service

1. Envoyez le contenu de cette archive à la racine du dépôt `alertesmeteo-hub/gfs`.
2. Dans GitHub, lancez **Actions → Mise à jour GFS France → Run workflow**.
3. Installez dans WordPress le ZIP `gfs-noaa-france-v1.0.1.zip`.
4. Utilisez le shortcode `[gfs_meteo]`.

La source de données configurée par défaut est :

`https://raw.githubusercontent.com/alertesmeteo-hub/gfs/data`

Sources : [NOAA GFS](https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast) et [NOAA NOMADS](https://nomads.ncep.noaa.gov/).
