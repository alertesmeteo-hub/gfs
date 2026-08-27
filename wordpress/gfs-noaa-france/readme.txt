=== GFS / NOAA France ===
Contributors: alertesmeteo
Tags: meteo, gfs, noaa, ncep, carte, previsions, avada
Requires at least: 5.8
Requires PHP: 7.4
Stable tag: 1.0.2
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Cartes interactives et prévisions du modèle déterministe NOAA GFS pour 34 746 communes françaises.

== Description ==

Le shortcode [gfs_meteo] affiche dans un seul module :

* une carte NOAA GFS interactive avec zoom, animation et valeur au survol ;
* une recherche par ville ou code postal et la géolocalisation ;
* les prévisions générales jusqu'à +240 h ;
* quatre graphiques et des diagnostics orage/neige ;
* l’outil capture avec copie d’image et téléchargement PNG, plus le diagramme au clic.

Les données proviennent directement de NOAA/NCEP, modèle GFS déterministe à 0,25°.

== Installation ==

1. Téléversez le ZIP dans Extensions > Ajouter une extension.
2. Activez GFS / NOAA France.
3. Vérifiez l'URL dans Réglages > GFS / NOAA.
4. Insérez [gfs_meteo] dans un bloc Avada.

Exemple : [gfs_meteo code="75056" departement="75" ville="Paris" heures="240"]

== Changelog ==

= 1.0.2 =
* Rapport cartographique Web Mercator corrigé pour supprimer l’aplatissement de la France.
* Bouton Zoom interactif remplacé par Outil capture.
* Zone de carte agrandie en hauteur tout en conservant l’échelle géographique.

= 1.0.1 =
* Requête NOAA NOMADS corrigée avec les champs GFS réellement disponibles.
* Unités des précipitations, nuages et données neige corrigées.
* Navigation des onglets WordPress GFS corrigée.
* Libellés résiduels de l’ancien module remplacés par NOAA GFS.

= 1.0.0 =
* Première version indépendante NOAA GFS 0,25°.
* Pipeline GitHub Actions jusqu'à +240 h et publication sur la branche data.
* Cartes lissées, départements précis, isobares, flèches de vent, recherche, tableaux et graphiques.
