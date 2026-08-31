#!/usr/bin/env python3
"""Construit les cartes et prévisions NOAA GFS 0,25° pour la France.

La chaîne lit les données GRIB2 publiques NOAA/NCEP. Les fichiers nationaux
sont découpés par département pour WordPress et les cartes restent calculées
depuis la grille GFS, jamais depuis les seules communes.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import requests
from eccodes import (
    codes_get,
    codes_get_double_array,
    codes_get_double_elements,
    codes_grib_new_from_file,
    codes_release,
)
from scipy.ndimage import map_coordinates

from gfs_maps import DEFAULT_BOUNDS, GFSMapRenderer, LAYER_SPECS


LOGGER = logging.getLogger("gfs.france")
PIPELINE_VERSION = "1.2.1"
DATASET_PAGE = "https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast"
DEFAULT_CURRENT_METADATA_URL = (
    "https://raw.githubusercontent.com/alertesmeteo-hub/"
    "gfs/data/index.json"
)
USER_AGENT = "alertes-meteo.com/gfs-noaa-france/1.2.1"

# Grille mondiale régulière GFS 0,25°.
GFS_NI = 1440
GFS_NJ = 721
GFS_LAT_FIRST = 90.0
GFS_LON_FIRST = 0.0
GFS_STEP = 0.25

# Rapport Web Mercator réel des limites -12/18° et 38/57° : environ 1,05.
# Utiliser le même nombre de pixels par unité projetée sur les deux axes évite
# d'aplatir visuellement la France.
MAP_WIDTH = 2100
MAP_HEIGHT = 2000

# Format compact partagé avec le JavaScript. Les diagnostics explicitement
# dérivés sont conservés car ils servent aux tableaux orages et neige.
VALUE_COLUMNS = (
    "temperature_c",
    "humidity_pct",
    "precipitation_mm",
    "cloud_cover_pct",
    "wind_speed_kmh",
    "wind_direction_deg",
    "wind_gust_kmh",
    "pressure_hpa",
    "visibility_km",
    "condition_code",
    "pressure_surface_hpa",
    "dewpoint_c",
    "precipitation_total_mm",
    "cloud_low_pct",
    "cloud_mid_pct",
    "cloud_high_pct",
    "cape_jkg",
    "precipitation_rate_mmh",
    "reflectivity_dbz",
    "graupel_mm",
    "thunder_risk_code",
    "lcl_m",
    "lightning_score",
    "hail_risk_code",
    "convective_precipitation_mm",
    "storm_type_code",
    "snow_risk_code",
    "snowfall_mm",
    "snow_fresh_cm",
    "snow_depth_cm",
    "snow_water_equivalent_mm",
    "snow_stick_risk_code",
    "snow_phase_code",
    "snowfall_total_mm",
)

INTEGER_COLUMNS = {
    "humidity_pct",
    "cloud_cover_pct",
    "wind_speed_kmh",
    "wind_direction_deg",
    "wind_gust_kmh",
    "pressure_hpa",
    "condition_code",
    "pressure_surface_hpa",
    "cloud_low_pct",
    "cloud_mid_pct",
    "cloud_high_pct",
    "cape_jkg",
    "reflectivity_dbz",
    "thunder_risk_code",
    "lcl_m",
    "lightning_score",
    "hail_risk_code",
    "storm_type_code",
    "snow_risk_code",
    "snow_stick_risk_code",
    "snow_phase_code",
}

MAP_FIELDS = {spec.field for spec in LAYER_SPECS} | {
    "pressure_hpa",
    "wind_u_kmh", "wind_v_kmh",
    "storm_motion_u_kmh", "storm_motion_v_kmh",
    *{
        f"wind_{component}_{level}_kmh"
        for component in ("u", "v")
        for level in (100, 200, 300, 500, 700, 850, 925, 950)
    },
}

CONDITION_CODES = {
    0: "unknown",
    1: "clear",
    2: "partly_cloudy",
    3: "cloudy",
    4: "overcast",
    5: "rain",
    6: "heavy_rain",
    7: "snow",
    8: "fog",
    9: "windy",
}

@dataclass
class DepartmentData:
    code: str
    global_point_ids: np.ndarray
    points: list[list[Any]]
    communes: list[list[Any]]


@dataclass
class NationalCatalog:
    version: str
    model_indexes: list[int]
    point_latitudes: np.ndarray
    point_longitudes: np.ndarray
    point_departments: list[str]
    departments: dict[str, DepartmentData]
    commune_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        default="config/communes-france.json",
        help="Catalogue officiel des communes de France",
    )
    parser.add_argument(
        "--output-dir",
        default="build/gfs-national",
        help="Dossier de publication à produire",
    )
    parser.add_argument(
        "--forecast-hours",
        type=int,
        default=360,
        help="Dernière échéance GFS, entre 3 et 360 heures",
    )
    parser.add_argument(
        "--current-metadata-url",
        default=DEFAULT_CURRENT_METADATA_URL,
        help="index.json actuellement publié, pour éviter un run identique",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force la reconstruction même si ce run est déjà publié",
    )
    return parser.parse_args()


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_get(gid: int, key: str, default: Any = None) -> Any:
    try:
        return codes_get(gid, key)
    except Exception:
        return default


def grib_datetime(gid: int, date_key: str, time_key: str) -> datetime | None:
    date_value = safe_get(gid, date_key)
    time_value = safe_get(gid, time_key)
    if date_value is None or time_value is None:
        return None
    try:
        return datetime.strptime(
            f"{int(date_value):08d}{int(time_value):04d}", "%Y%m%d%H%M"
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def grid_index(latitude: float, longitude: float) -> tuple[int, float, float]:
    row = int(round((GFS_LAT_FIRST - latitude) / GFS_STEP))
    column = int(round(((longitude - GFS_LON_FIRST) % 360.0) / GFS_STEP)) % GFS_NI
    row = max(0, min(GFS_NJ - 1, row))
    column = max(0, min(GFS_NI - 1, column))
    index = row * GFS_NI + column
    model_latitude = GFS_LAT_FIRST - row * GFS_STEP
    model_longitude = GFS_LON_FIRST + column * GFS_STEP
    return index, model_latitude, model_longitude


def rain_nearby_points(latitude: float, longitude: float) -> dict[int, tuple[float, float]]:
    """Nœuds réels de la grille 0,25° à 10 km maximum, même hors département."""
    _index, center_lat, center_lon = grid_index(latitude, longitude)
    result = {}
    for dy in (-0.25, 0.0, 0.25):
        for dx in (-0.25, 0.0, 0.25):
            index, lat, lon = grid_index(center_lat + dy, center_lon + dx)
            dlat = math.radians(lat - latitude)
            dlon = math.radians(lon - longitude)
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(latitude)) * math.cos(math.radians(lat)) * math.sin(dlon / 2) ** 2
            distance = 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))
            if distance <= 10.0 + 1e-7:
                result[index] = (lat, lon)
    return result


def load_catalog(path: Path) -> NationalCatalog:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    raw_communes = payload.get("communes") or []
    if len(raw_communes) < 34_000:
        raise RuntimeError("Le catalogue communal France est incomplet")

    mapped: list[tuple[list[Any], int, float, float]] = []
    point_coordinates: dict[int, tuple[float, float]] = {}
    rain_department_points: dict[str, set[int]] = defaultdict(set)
    for commune in raw_communes:
        if not isinstance(commune, list) or len(commune) < 7:
            raise RuntimeError("Entrée communale invalide dans le catalogue")
        latitude = float(commune[5])
        longitude = float(commune[6])
        model_index, model_latitude, model_longitude = grid_index(
            latitude, longitude
        )
        mapped.append((commune, model_index, model_latitude, model_longitude))
        point_coordinates[model_index] = (model_latitude, model_longitude)
        neighbors = rain_nearby_points(latitude, longitude)
        point_coordinates.update(neighbors)
        rain_department_points[str(commune[2]).upper()].update(neighbors)

    model_indexes = sorted(point_coordinates)
    global_identifier = {
        model_index: position for position, model_index in enumerate(model_indexes)
    }
    point_latitudes = np.asarray(
        [point_coordinates[index][0] for index in model_indexes], dtype=np.float64
    )
    point_longitudes = np.asarray(
        [point_coordinates[index][1] for index in model_indexes], dtype=np.float64
    )

    department_votes: dict[int, Counter[str]] = defaultdict(Counter)
    by_department: dict[str, list[tuple[list[Any], int]]] = defaultdict(list)
    for commune, model_index, _latitude, _longitude in mapped:
        department = str(commune[2]).upper()
        global_id = global_identifier[model_index]
        department_votes[global_id][department] += 1
        by_department[department].append((commune, global_id))

    for department, indexes in rain_department_points.items():
        for index in indexes:
            department_votes[global_identifier[index]][department] += 1

    point_departments = [
        department_votes[position].most_common(1)[0][0]
        if department_votes[position]
        else ""
        for position in range(len(model_indexes))
    ]

    departments: dict[str, DepartmentData] = {}
    for department, entries in sorted(by_department.items()):
        global_ids = sorted({global_id for _commune, global_id in entries} |
                            {global_identifier[index] for index in rain_department_points[department]})
        local_identifier = {
            global_id: position for position, global_id in enumerate(global_ids)
        }
        compact_communes = [
            [
                str(commune[0]),
                str(commune[1]),
                list(commune[3]),
                int(commune[4]),
                float(commune[5]),
                float(commune[6]),
                local_identifier[global_id],
            ]
            for commune, global_id in entries
        ]
        compact_points = [
            [
                model_indexes[global_id],
                round(float(point_latitudes[global_id]), 5),
                round(float(point_longitudes[global_id]), 5),
            ]
            for global_id in global_ids
        ]
        departments[department] = DepartmentData(
            code=department,
            global_point_ids=np.asarray(global_ids, dtype=np.int64),
            points=compact_points,
            communes=compact_communes,
        )

    if len(departments) != 96:
        raise RuntimeError(
            f"Nombre inattendu de départements métropolitains : {len(departments)}"
        )
    LOGGER.info(
        "Catalogue GFS : %s communes, %s points GFS 0,25°, %s départements",
        len(raw_communes),
        len(model_indexes),
        len(departments),
    )
    return NationalCatalog(
        version=f"{payload.get('catalog_version', '1')}-gfs001",
        model_indexes=model_indexes,
        point_latitudes=point_latitudes,
        point_longitudes=point_longitudes,
        point_departments=point_departments,
        departments=departments,
        commune_count=len(raw_communes),
    )


def already_published(url: str, run_time: datetime | None) -> bool:
    if not url or run_time is None:
        return False
    try:
        response = requests.get(
            url,
            timeout=(10, 30),
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        model = payload.get("model") or {}
        return (
            payload.get("status") == "ok"
            and str(model.get("run_time") or "") >= iso_utc(run_time)
            and model.get("pipeline_version") == PIPELINE_VERSION
        )
    except (requests.RequestException, ValueError, TypeError):
        return False


def mask_missing(values: np.ndarray, missing_value: Any) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64)
    invalid = ~np.isfinite(result) | (np.abs(result) > 1.0e20)
    try:
        missing = float(missing_value)
    except (TypeError, ValueError):
        missing = math.nan
    if math.isfinite(missing):
        invalid |= np.isclose(result, missing, rtol=0.0, atol=1.0e-9)
    result[invalid] = np.nan
    return result


def _metadata_integer(gid: int, key: str, default: int = -1) -> int:
    try:
        return int(round(float(safe_get(gid, key, default))))
    except (TypeError, ValueError):
        return default


def message_field(gid: int) -> str | None:
    """Associe sans ambiguïté un message GFS à un champ interne.

    Plusieurs paramètres NOAA partagent le même ``shortName``. Le niveau GRIB
    est donc indispensable : sans ce contrôle, le dernier CAPE, vent, RH ou
    géopotentiel rencontré écraserait silencieusement les autres.
    """
    short_name = str(safe_get(gid, "shortName", "")).lower()
    level_type = str(safe_get(gid, "typeOfLevel", ""))
    level_type_lower = level_type.lower()
    level = _metadata_integer(gid, "level")
    pressure_layer_depth = (
        int(round(level / 100.0)) if level >= 1000 else level
    )
    step_type = str(safe_get(gid, "stepType", "")).lower()
    name = str(safe_get(gid, "name", "")).lower()
    is_pressure = level_type_lower in {"isobaricinhpa", "isobaricinhpa"}
    is_height = level_type_lower == "heightaboveground"
    is_pressure_layer = "pressurefromgroundlayer" in level_type_lower

    if short_name in {"z", "gh", "hgt"}:
        if level_type_lower == "surface":
            return (
                "surface_geopotential"
                if short_name == "z"
                else "surface_altitude_m"
            )
        if is_pressure and level in {500, 850}:
            return f"geopotential_{level}_m"
        if level_type_lower == "cloudceiling":
            return "cloud_ceiling_m"
        if "isothermzero" in level_type_lower or "0c isotherm" in name:
            return "freezing_level_m"
        if "highesttroposphericfreezing" in level_type_lower:
            return "highest_freezing_level_m"
        if level_type_lower == "potentialvorticity" and level >= 0:
            return "pv_surface_height_m"

    if short_name in {"2t", "tmp", "t"}:
        if (short_name == "2t") or (is_height and level == 2):
            return "temperature_k"
        if level_type_lower == "surface":
            return "surface_temperature_k"
        if "depthbelowlandlayer" in level_type_lower and level in {0, 10}:
            return "soil_temperature_k"
        if is_pressure and level in {10, 500, 700, 850}:
            return f"temperature_{level}_k"
    if short_name in {"tmax", "mx2t", "mx2t3", "mx2t6"}:
        return "temperature_max_k" if is_height and level == 2 else None
    if short_name in {"tmin", "mn2t", "mn2t3", "mn2t6"}:
        return "temperature_min_k" if is_height and level == 2 else None
    if short_name in {"2d", "dpt", "d2m"}:
        return "dewpoint_k" if short_name == "2d" or (is_height and level == 2) else None

    if short_name in {"10u", "10v", "u", "v", "ugrd", "vgrd"}:
        component = "u" if short_name in {"10u", "u", "ugrd"} else "v"
        if short_name in {"10u", "10v"} or (is_height and level == 10):
            return f"wind_{component}_ms"
        if is_height and level == 100:
            return f"wind_{component}_100_ms"
        if is_pressure and level in {200, 300, 500, 700, 850, 925, 950}:
            return f"wind_{component}_{level}_ms"
    if short_name in {"ustm", "vstm"}:
        return "storm_motion_u_ms" if short_name == "ustm" else "storm_motion_v_ms"

    if short_name in {"rh", "r", "2r"}:
        if is_height and level == 2:
            return "humidity_2m_pct"
        if is_pressure and level in {700, 850}:
            return f"humidity_{level}_pct"
        if "entireatmosphere" in level_type_lower:
            return "humidity_atmosphere_pct"

    if short_name in {"cape", "mucape"}:
        if short_name == "mucape" or (
            is_pressure_layer and pressure_layer_depth == 255
        ):
            return "mucape_jkg"
        if is_pressure_layer and pressure_layer_depth == 180:
            return "mlcape_jkg"
        if is_pressure_layer and pressure_layer_depth == 90:
            return "cape_90_jkg"
        if level_type_lower == "surface":
            return "sbcape_jkg"
    if short_name in {"cin", "mucin"}:
        if short_name == "mucin" or (
            is_pressure_layer and pressure_layer_depth == 255
        ):
            return "mucin_jkg"
        if is_pressure_layer and pressure_layer_depth == 180:
            return "mlcin_jkg"
        if level_type_lower == "surface":
            return "sbcin_jkg"

    if short_name in {"sp", "pres"}:
        if level_type_lower == "tropopause":
            return "tropopause_pressure_pa"
        if level_type_lower == "surface":
            return "surface_pressure_pa"
    if short_name == "plpl":
        return "parcel_lift_pressure_pa"
    if short_name == "trpp" and level_type_lower == "tropopause":
        return "tropopause_pressure_pa"

    if short_name in {"tcc", "lcc", "mcc", "hcc", "lcdc", "mcdc", "hcdc"}:
        if step_type == "avg":
            return None
        if short_name in {"lcc", "lcdc"}:
            return "cloud_low_pct"
        if short_name in {"mcc", "mcdc"}:
            return "cloud_mid_pct"
        if short_name in {"hcc", "hcdc"}:
            return "cloud_high_pct"
        if "convectivecloudlayer" in level_type_lower:
            return "cloud_convective_pct"
        return "cloud_total_fraction" if "atmosphere" in level_type_lower else None

    direct = {
        "10fg": "gust_speed_ms", "fg10": "gust_speed_ms",
        "gust": "gust_speed_ms",
        "msl": "mean_sea_pressure_pa", "prmsl": "mean_sea_pressure_pa",
        "tp": "precipitation_total_m", "apcp": "precipitation_total_m",
        "tprate": "precipitation_rate_kgm2s", "prate": "precipitation_rate_kgm2s",
        "cprat": "convective_rate_kgm2s", "acpcp": "convective_precipitation_mm",
        "pwat": "precipitable_water_mm", "watr": "runoff_total_mm",
        "sf": "snow_total_m", "sdwe": "snow_total_m",
        "sd": "snow_depth_m", "snod": "snow_depth_m", "sde": "snow_depth_m",
        "orog": "surface_altitude_m", "vis": "visibility_m",
        "refc": "reflectivity_dbz", "vrate": "ventilation_rate_m2s",
        "hlcy": "srh_0_3_m2s2", "hindex": "haines_index",
        "cwork": "cloud_work_jkg", "lftx": "surface_lifted_index_k",
        "4lftx": "best_lifted_index_k", "absv": f"absolute_vorticity_{level}_s"
        if is_pressure and level in {500, 900} else None,
        "vvel": "vertical_velocity_700_pas" if is_pressure and level == 700 else None,
        "w": "vertical_velocity_700_pas" if is_pressure and level == 700 else None,
        "tsoil": "soil_temperature_k" if "depthbelowlandlayer" in level_type_lower and level in {0, 10} else None,
        "st": "soil_temperature_k" if "depthbelowlandlayer" in level_type_lower and level in {0, 10} else None,
        "soilw": "soil_moisture_vol_fraction" if "depthbelowlandlayer" in level_type_lower and level in {0, 10} else None,
        "soill": "soil_moisture_liquid_fraction" if "depthbelowlandlayer" in level_type_lower and level in {0, 10} else None,
        "shtfl": "sensible_heat_flux_wm2", "avg_ishf": "sensible_heat_flux_wm2",
        "lhtfl": "latent_heat_flux_wm2", "avg_slhtf": "latent_heat_flux_wm2",
        "dswrf": "downward_shortwave_wm2", "sdswrf": "downward_shortwave_wm2",
        "dlwrf": "downward_longwave_wm2", "sdlwrf": "downward_longwave_wm2",
        "hpbl": "mixed_layer_depth_m",
    }
    if short_name in direct and direct[short_name] is not None:
        return direct[short_name]
    if short_name == "cpr" and step_type != "avg":
        return "convective_rate_kgm2s"
    if short_name == "refd" and is_height and level in {1000, 4000}:
        return f"reflectivity_{level}_dbz"
    if (
        int(safe_get(gid, "discipline", -1)) == 0
        and int(safe_get(gid, "parameterCategory", -1)) == 16
        and int(safe_get(gid, "parameterNumber", -1)) == 193
    ):
        return "reflectivity_dbz"
    return None


class NationalGrid:
    def __init__(self, catalog: NationalCatalog) -> None:
        self.catalog = catalog
        self.validated = False
        self.ni = 0
        self.nj = 0
        self.latitude_first = 0.0
        self.longitude_first = 0.0
        self.step = GFS_STEP
        self.latitude_step = -GFS_STEP
        self.point_indexes: list[int] = []

    def validate(self, gid: int) -> None:
        if self.validated:
            return
        ni = int(safe_get(gid, "Ni", 0))
        nj = int(safe_get(gid, "Nj", 0))
        lat_first = float(safe_get(gid, "latitudeOfFirstGridPointInDegrees", 0))
        lat_last = float(safe_get(gid, "latitudeOfLastGridPointInDegrees", 0))
        lon_first = float(safe_get(gid, "longitudeOfFirstGridPointInDegrees", 0))
        step = abs(float(safe_get(gid, "iDirectionIncrementInDegrees", GFS_STEP)))
        latitude_step = abs(
            float(safe_get(gid, "jDirectionIncrementInDegrees", GFS_STEP))
        )
        if lat_last < lat_first:
            latitude_step = -latitude_step
        if ni <= 1 or nj <= 1 or not math.isclose(step, GFS_STEP, abs_tol=1.0e-6):
            raise RuntimeError(
                "La grille reçue n'est pas une extraction GFS 0,25° valide "
                f"({ni} × {nj}, premier point {lat_first}/{lon_first})"
            )
        rows = np.rint(
            (self.catalog.point_latitudes - lat_first) / latitude_step
        ).astype(int)
        columns = np.rint(
            ((self.catalog.point_longitudes - lon_first) % 360.0) / step
        ).astype(int)
        if np.any(rows < 0) or np.any(rows >= nj) or np.any(columns < 0) or np.any(columns >= ni):
            raise RuntimeError("L'extraction GFS ne couvre pas toutes les communes françaises")
        self.ni, self.nj = ni, nj
        self.latitude_first, self.longitude_first, self.step = lat_first, lon_first, step
        self.latitude_step = latitude_step
        self.point_indexes = (rows * ni + columns).tolist()
        self.validated = True

    def extract(self, gid: int) -> np.ndarray:
        self.validate(gid)
        values = codes_get_double_elements(gid, "values", self.point_indexes)
        return mask_missing(values, safe_get(gid, "missingValue"))


def mercator(latitude: np.ndarray) -> np.ndarray:
    radians = np.radians(np.clip(latitude, -85.0, 85.0))
    return np.log(np.tan(np.pi / 4.0 + radians / 2.0))


def inverse_mercator(value: np.ndarray) -> np.ndarray:
    return np.degrees(2.0 * np.arctan(np.exp(value)) - np.pi / 2.0)


class MapSampler:
    """Rééchantillonne la grille GFS par spline bicubique sur Web Mercator."""

    def __init__(self, width: int, height: int) -> None:
        self.width = int(width)
        self.height = int(height)
        bounds = DEFAULT_BOUNDS
        target_latitudes = inverse_mercator(
            np.linspace(
                mercator(np.asarray(float(bounds["north"]))),
                mercator(np.asarray(float(bounds["south"]))),
                self.height,
            )
        )
        target_longitudes = np.linspace(
            float(bounds["west"]), float(bounds["east"]), self.width
        )
        self.target_latitudes = target_latitudes
        self.target_longitudes = target_longitudes

    def extract(self, gid: int, validator: NationalGrid) -> np.ndarray:
        validator.validate(gid)
        values = mask_missing(
            codes_get_double_array(gid, "values"),
            safe_get(gid, "missingValue"),
        ).reshape(validator.nj, validator.ni)
        rows = (
            self.target_latitudes - validator.latitude_first
        ) / validator.latitude_step
        columns = ((self.target_longitudes - validator.longitude_first) % 360.0) / validator.step
        row_grid = np.broadcast_to(rows[:, None], (self.height, self.width))
        column_grid = np.broadcast_to(columns[None, :], (self.height, self.width))
        coverage = (
            (row_grid >= 0) & (row_grid <= validator.nj - 1)
            & (column_grid >= 0) & (column_grid <= validator.ni - 1)
        )
        interpolation_order = 3 if np.all(np.isfinite(values)) else 1
        sampled = map_coordinates(
            values,
            [row_grid, column_grid],
            order=interpolation_order,
            mode="constant",
            cval=np.nan,
            prefilter=interpolation_order > 1,
        ).astype(np.float32, copy=False)
        sampled[~coverage] = np.nan
        return sampled


def normalize_gfs_units(
    field: str,
    point_field: np.ndarray,
    map_field: np.ndarray,
    units: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Normalise les unités GRIB selon les conventions NOAA GFS."""
    normalized_units = str(units).lower()
    if field in {"precipitation_total_m", "snow_total_m"}:
        # APCP/WEASD GFS : kg m-2 = mm d'eau. Seules des valeurs en mètres
        # nécessitent une conversion.
        if normalized_units in {"m", "metre", "meter"}:
            return point_field * 1000.0, map_field * 1000.0
    elif field == "snow_depth_m":
        # SNOD est une hauteur en mètres ; l'état interne utilise des mm.
        if normalized_units in {"m", "metre", "meter"}:
            return point_field * 1000.0, map_field * 1000.0
    elif field in {
        "cloud_total_fraction", "cloud_low_pct", "cloud_mid_pct", "cloud_high_pct"
    }:
        # TCDC/LCDC/MCDC/HCDC GFS sont directement exprimés en pourcentage.
        if normalized_units not in {"%", "percent", "percentage"}:
            return point_field * 100.0, map_field * 100.0
    elif field == "surface_geopotential":
        return point_field / 9.80665, map_field / 9.80665
    return point_field, map_field


def parse_grib_files(
    paths: Iterable[Path],
    grid: NationalGrid,
    map_sampler: MapSampler | None,
    lead_hour: int,
) -> dict[str, Any]:
    point_values: dict[str, np.ndarray] = {}
    map_values: dict[str, np.ndarray] = {}
    run_time: datetime | None = None
    valid_time: datetime | None = None
    observed_lead: int | None = None

    for path in paths:
        with path.open("rb") as handle:
            while True:
                gid = codes_grib_new_from_file(handle)
                if gid is None:
                    break
                try:
                    field = message_field(gid)
                    if field is None:
                        continue
                    run_time = run_time or grib_datetime(gid, "dataDate", "dataTime")
                    valid_time = valid_time or grib_datetime(
                        gid, "validityDate", "validityTime"
                    )
                    end_step = safe_get(gid, "endStep")
                    if end_step is not None:
                        observed_lead = int(end_step)
                    point_field = grid.extract(gid)
                    map_field = map_sampler.extract(gid, grid) if map_sampler else np.empty(0)
                    point_field, map_field = normalize_gfs_units(
                        field,
                        point_field,
                        map_field,
                        str(safe_get(gid, "units", "")),
                    )
                    point_values[field] = point_field
                    if map_sampler is not None:
                        map_values[field] = map_field
                finally:
                    codes_release(gid)

    if "temperature_k" not in point_values:
        raise RuntimeError(f"Température à 2 m absente de l'échéance +{lead_hour:02d} h")
    if observed_lead is not None and observed_lead != lead_hour:
        raise RuntimeError(
            f"Échéance GRIB incohérente : +{observed_lead} h au lieu de +{lead_hour} h"
        )
    if valid_time is None and run_time is not None:
        valid_time = run_time + timedelta(hours=lead_hour)
    if valid_time is None:
        raise RuntimeError(f"Date de validité absente à +{lead_hour:02d} h")
    return {
        "lead_hour": lead_hour,
        "run_time": run_time,
        "valid_time": valid_time,
        "values": point_values,
        "map_values": map_values,
    }


def array_like(
    raw: dict[str, np.ndarray], name: str, shape: tuple[int, ...]
) -> np.ndarray:
    values = raw.get(name)
    if values is None:
        return np.full(shape, np.nan, dtype=np.float64)
    # Les cartes 0,25° n'ont pas besoin de float64. Le float32 divise presque
    # par deux le pic mémoire lorsque plus de 80 paramètres sont traités.
    result = np.asarray(values, dtype=np.float32)
    if result.shape != shape:
        raise RuntimeError(f"Forme inattendue pour le champ {name} : {result.shape}")
    return result


def accumulation(
    raw: dict[str, np.ndarray],
    name: str,
    shape: tuple[int, ...],
    previous: np.ndarray | None,
    lead_hour: int,
) -> tuple[np.ndarray, np.ndarray]:
    total = array_like(raw, name, shape)
    if not np.any(np.isfinite(total)) and lead_hour == 0:
        total = np.zeros(shape, dtype=np.float64)
    total = np.where(np.isfinite(total), np.maximum(total, 0.0), np.nan)
    if previous is None:
        hourly = total.copy()
    else:
        hourly = np.maximum(total - previous, 0.0)
        hourly[~np.isfinite(total)] = np.nan
    return hourly, total.copy()


def rounded(values: np.ndarray, decimals: int) -> np.ndarray:
    return np.round(values, decimals)


def rolling_extreme(
    values: np.ndarray,
    history: list[tuple[int, np.ndarray]] | None,
    lead_hour: int,
    *,
    maximum: bool,
    window_hours: int = 12,
) -> tuple[np.ndarray, list[tuple[int, np.ndarray]]]:
    """Maximum ou minimum glissant, sans empiler les grandes grilles en RAM."""
    compact = np.asarray(values, dtype=np.float32).copy()
    kept = [
        (int(history_lead), np.asarray(history_values, dtype=np.float32))
        for history_lead, history_values in (history or [])
        if lead_hour - int(history_lead) <= window_hours
    ]
    kept.append((int(lead_hour), compact))
    result = kept[0][1].copy()
    combine = np.fmax if maximum else np.fmin
    for _history_lead, history_values in kept[1:]:
        result = combine(result, history_values)
    return result, kept


def theta_e_850(temperature_c: np.ndarray, humidity_pct: np.ndarray) -> np.ndarray:
    """Température potentielle équivalente à 850 hPa (formule de Bolton)."""
    temperature_k = temperature_c + 273.15
    relative = np.clip(humidity_pct / 100.0, 0.01, 1.0)
    saturation = 6.112 * np.exp(17.67 * temperature_c / (temperature_c + 243.5))
    vapour = relative * saturation
    mixing_ratio = 0.622 * vapour / np.maximum(850.0 - vapour, 1.0)
    dewpoint_c = 243.5 * np.log(np.maximum(vapour, 0.01) / 6.112) / (
        17.67 - np.log(np.maximum(vapour, 0.01) / 6.112)
    )
    dewpoint_k = dewpoint_c + 273.15
    tlcl = 1.0 / (
        1.0 / np.maximum(dewpoint_k - 56.0, 1.0)
        + np.log(np.maximum(temperature_k / dewpoint_k, 0.1)) / 800.0
    ) + 56.0
    theta = temperature_k * np.power(
        1000.0 / 850.0,
        0.2854 * (1.0 - 0.28 * mixing_ratio),
    )
    result = theta * np.exp(
        (3376.0 / np.maximum(tlcl, 150.0) - 2.54)
        * mixing_ratio
        * (1.0 + 0.81 * mixing_ratio)
    )
    valid = np.isfinite(temperature_c) & np.isfinite(humidity_pct)
    return np.where(valid, np.clip(result, 180.0, 450.0), np.nan)


def storm_diagnostics(
    cape: np.ndarray,
    precipitation: np.ndarray,
    precipitation_rate: np.ndarray,
    humidity: np.ndarray,
    reflectivity: np.ndarray,
    graupel: np.ndarray,
    gust_speed: np.ndarray,
    step_hours: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Diagnostic convectif indicatif à partir des champs GFS disponibles.

    La MUCAPE décrit un potentiel d'instabilité, pas la présence d'un orage.
    Un niveau de risque positif exige donc aussi un signal convectif actif :
    précipitations, taux de précipitation, réflectivité ou graupel.
    """
    cape_value = np.nan_to_num(cape, nan=0.0, posinf=0.0, neginf=0.0)
    rain_value = np.nan_to_num(
        precipitation, nan=0.0, posinf=0.0, neginf=0.0
    )
    direct_rate = np.nan_to_num(
        precipitation_rate, nan=0.0, posinf=0.0, neginf=0.0
    )
    effective_rate = np.maximum(
        direct_rate,
        rain_value / max(float(step_hours), 1.0),
    )
    humidity_value = np.nan_to_num(humidity, nan=0.0)
    reflectivity_value = np.nan_to_num(reflectivity, nan=0.0)
    graupel_value = np.nan_to_num(graupel, nan=0.0)
    gust_value = np.nan_to_num(gust_speed, nan=0.0)

    moist = (
        (humidity_value >= 45.0)
        | (effective_rate >= 0.5)
        | (reflectivity_value >= 35.0)
        | (graupel_value >= 0.1)
    )
    active = (
        (effective_rate >= 0.10)
        | (reflectivity_value >= 30.0)
        | (graupel_value >= 0.05)
    )
    weak = moist & active & (cape_value >= 100.0)
    moderate = moist & (cape_value >= 500.0) & (
        (effective_rate >= 0.8)
        | (reflectivity_value >= 40.0)
        | (graupel_value >= 0.2)
    )
    strong = moist & (cape_value >= 1000.0) & (
        (effective_rate >= 2.5)
        | (reflectivity_value >= 48.0)
        | (graupel_value >= 0.8)
    )
    severe = moist & (cape_value >= 1800.0) & (
        (effective_rate >= 7.0)
        | (reflectivity_value >= 56.0)
        | (graupel_value >= 2.0)
    )
    severe |= strong & (cape_value >= 1500.0) & (gust_value >= 100.0)

    thunder = np.zeros(cape.shape, dtype=np.int16)
    thunder[weak] = 1
    thunder[moderate] = 2
    thunder[strong] = 3
    thunder[severe] = 4

    lightning_raw = np.clip(
        cape_value / 45.0
        + effective_rate * 6.0
        + np.maximum(reflectivity_value - 25.0, 0.0) * 1.8
        + graupel_value * 8.0,
        0.0,
        100.0,
    )
    lightning = np.where(weak, lightning_raw, 0.0)
    lightning = np.where(thunder >= 2, np.maximum(lightning, 25.0), lightning)
    lightning = np.where(thunder >= 3, np.maximum(lightning, 50.0), lightning)
    lightning = np.where(thunder >= 4, np.maximum(lightning, 75.0), lightning)

    hail = np.zeros(cape.shape, dtype=np.int16)
    hail[(thunder >= 2) & (cape_value >= 700.0) & (
        (reflectivity_value >= 44.0) | (graupel_value >= 0.2)
    )] = 1
    hail[(thunder >= 3) & (cape_value >= 1400.0) & (
        (reflectivity_value >= 50.0) | (graupel_value >= 0.8)
    )] = 2
    hail[(thunder >= 4) & (
        (reflectivity_value >= 56.0) | (graupel_value >= 2.0)
    )] = 3

    cape_fraction = np.clip((cape_value - 100.0) / 1400.0, 0.0, 1.0)
    rate_fraction = np.clip((effective_rate - 0.1) / 4.0, 0.0, 1.0)
    convective_fraction = np.where(
        weak,
        np.clip(0.15 + 0.55 * cape_fraction + 0.30 * rate_fraction, 0.0, 1.0),
        0.0,
    )
    convective_precipitation = rain_value * convective_fraction

    storm_type = thunder.copy()
    return thunder, lightning, hail, convective_precipitation, storm_type


def transform_step(
    raw: dict[str, np.ndarray],
    altitude: np.ndarray,
    previous: dict[str, Any],
    lead_hour: int,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    shape = altitude.shape
    temperature = array_like(raw, "temperature_k", shape) - 273.15
    surface_temperature = array_like(raw, "surface_temperature_k", shape) - 273.15
    soil_temperature = array_like(raw, "soil_temperature_k", shape) - 273.15
    temperature_850 = array_like(raw, "temperature_850_k", shape) - 273.15
    temperature_700 = array_like(raw, "temperature_700_k", shape) - 273.15
    temperature_500 = array_like(raw, "temperature_500_k", shape) - 273.15
    temperature_10 = array_like(raw, "temperature_10_k", shape) - 273.15
    direct_dewpoint = array_like(raw, "dewpoint_k", shape) - 273.15
    temp_gamma = 17.625 * temperature / (243.04 + temperature)
    dew_gamma = 17.625 * direct_dewpoint / (243.04 + direct_dewpoint)
    humidity_derived = np.clip(100.0 * np.exp(dew_gamma - temp_gamma), 0, 100)
    humidity_direct = np.clip(array_like(raw, "humidity_2m_pct", shape), 0, 100)
    humidity = np.where(np.isfinite(humidity_direct), humidity_direct, humidity_derived)
    humidity_850 = np.clip(array_like(raw, "humidity_850_pct", shape), 0, 100)
    humidity_700 = np.clip(array_like(raw, "humidity_700_pct", shape), 0, 100)
    u_wind = array_like(raw, "wind_u_ms", shape)
    v_wind = array_like(raw, "wind_v_ms", shape)
    wind_components = {
        level: (
            array_like(raw, f"wind_u_{level}_ms", shape),
            array_like(raw, f"wind_v_{level}_ms", shape),
        )
        for level in (100, 200, 300, 500, 700, 850, 925, 950)
    }
    gust_scalar = array_like(raw, "gust_speed_ms", shape)
    surface_pressure = array_like(raw, "surface_pressure_pa", shape) / 100.0
    sbcape = np.maximum(array_like(raw, "sbcape_jkg", shape), 0.0)
    mlcape = np.maximum(array_like(raw, "mlcape_jkg", shape), 0.0)
    mucape = np.maximum(array_like(raw, "mucape_jkg", shape), 0.0)
    cape = np.where(np.isfinite(mucape), mucape, sbcape)
    sbcin = array_like(raw, "sbcin_jkg", shape)
    mlcin = array_like(raw, "mlcin_jkg", shape)
    mucin = array_like(raw, "mucin_jkg", shape)
    precipitation_rate = np.maximum(
        array_like(raw, "precipitation_rate_kgm2s", shape) * 3600.0,
        0.0,
    )
    reflectivity = np.clip(array_like(raw, "reflectivity_dbz", shape), 0, 80)
    cloud_total = np.clip(array_like(raw, "cloud_total_fraction", shape), 0, 100)
    cloud_low = np.clip(array_like(raw, "cloud_low_pct", shape), 0, 100)
    cloud_mid = np.clip(array_like(raw, "cloud_mid_pct", shape), 0, 100)
    cloud_high = np.clip(array_like(raw, "cloud_high_pct", shape), 0, 100)
    cloud_convective = np.clip(array_like(raw, "cloud_convective_pct", shape), 0, 100)

    precipitation, rain_total = accumulation(
        raw,
        "precipitation_total_m",
        shape,
        previous.get("rain_total"),
        lead_hour,
    )
    snow, snow_total = accumulation(
        raw, "snow_total_m", shape, previous.get("snow_total"), lead_hour
    )
    graupel, graupel_total = accumulation(
        raw, "graupel_total_mm", shape, previous.get("graupel_total"), lead_hour
    )
    _runoff_increment, runoff_total = accumulation(
        raw, "runoff_total_mm", shape, previous.get("runoff_total"), lead_hour
    )

    wind_speed = np.hypot(u_wind, v_wind) * 3.6
    wind_direction = np.degrees(np.arctan2(-u_wind, -v_wind)) % 360.0
    gust_speed = gust_scalar * 3.6
    wind_speeds = {
        level: np.hypot(components[0], components[1]) * 3.6
        for level, components in wind_components.items()
    }
    storm_motion_u = array_like(raw, "storm_motion_u_ms", shape)
    storm_motion_v = array_like(raw, "storm_motion_v_ms", shape)
    storm_motion_speed = np.hypot(storm_motion_u, storm_motion_v) * 3.6

    maximum_source = array_like(raw, "temperature_max_k", shape) - 273.15
    minimum_source = array_like(raw, "temperature_min_k", shape) - 273.15
    maximum_source = np.where(np.isfinite(maximum_source), maximum_source, temperature)
    minimum_source = np.where(np.isfinite(minimum_source), minimum_source, temperature)
    temperature_max_12h, maximum_history = rolling_extreme(
        maximum_source,
        previous.get("temperature_max_history"),
        lead_hour,
        maximum=True,
    )
    temperature_min_12h, minimum_history = rolling_extreme(
        minimum_source,
        previous.get("temperature_min_history"),
        lead_hour,
        maximum=False,
    )

    relative = np.clip(humidity / 100.0, 0.01, 1.0)
    gamma = np.log(relative) + 17.625 * temperature / (243.04 + temperature)
    dewpoint = np.where(np.isfinite(direct_dewpoint), direct_dewpoint,
                        243.04 * gamma / (17.625 - gamma))
    dewpoint[~np.isfinite(temperature) | ~np.isfinite(humidity)] = np.nan
    lcl = np.clip(125.0 * (temperature - dewpoint), 0, 5000)

    dewpoint_kelvin = np.clip(dewpoint + 273.15, 173.15, 333.15)
    vapour_pressure = 6.11 * np.exp(
        5417.7530 * (1.0 / 273.16 - 1.0 / dewpoint_kelvin)
    )
    humidex = temperature + 0.5555 * (vapour_pressure - 10.0)

    wind_chill = temperature.copy()
    chill_valid = (
        np.isfinite(temperature)
        & np.isfinite(wind_speed)
        & (temperature <= 10)
        & (wind_speed >= 4.8)
    )
    wind_factor = np.power(np.maximum(wind_speed, 0), 0.16)
    wind_chill[chill_valid] = (
        13.12
        + 0.6215 * temperature[chill_valid]
        - 11.37 * wind_factor[chill_valid]
        + 0.3965 * temperature[chill_valid] * wind_factor[chill_valid]
    )

    cloud = 100.0 * (
        1.0
        - (1.0 - cloud_low / 100.0)
        * (1.0 - cloud_mid / 100.0)
        * (1.0 - cloud_high / 100.0)
    )
    cloud[
        ~np.isfinite(cloud_low)
        | ~np.isfinite(cloud_mid)
        | ~np.isfinite(cloud_high)
    ] = np.nan
    cloud = np.where(np.isfinite(cloud_total), cloud_total, cloud)

    temperature_kelvin = np.maximum(temperature + 273.15, 180.0)
    pressure = surface_pressure * np.exp(
        9.80665 * np.maximum(altitude, -500.0)
        / (287.05 * (temperature_kelvin + 0.00325 * np.maximum(altitude, 0.0)))
    )
    pressure[~np.isfinite(surface_pressure) | ~np.isfinite(temperature)] = np.nan
    pressure = np.clip(pressure, 850, 1085)
    mean_sea_pressure = array_like(raw, "mean_sea_pressure_pa", shape) / 100.0
    pressure = np.where(np.isfinite(mean_sea_pressure), mean_sea_pressure, pressure)

    condition = np.zeros(shape, dtype=np.int16)
    condition[np.isfinite(cloud) & (cloud <= 20)] = 1
    condition[np.isfinite(cloud) & (cloud > 20) & (cloud <= 55)] = 2
    condition[np.isfinite(cloud) & (cloud > 55) & (cloud <= 85)] = 3
    condition[np.isfinite(cloud) & (cloud > 85)] = 4
    condition[np.isfinite(gust_speed) & (gust_speed >= 70)] = 9
    condition[np.isfinite(precipitation) & (precipitation >= 0.1)] = 5
    condition[np.isfinite(precipitation) & (precipitation >= 5)] = 6
    condition[np.isfinite(snow) & (snow >= 0.1)] = 7

    step_hours = 3.0 if lead_hour <= 144 else 6.0
    thunder, lightning, hail, diagnosed_convective_precipitation, storm_type = (
        storm_diagnostics(
            cape,
            precipitation,
            precipitation_rate,
            humidity,
            reflectivity,
            graupel,
            gust_speed,
            step_hours,
        )
    )
    direct_convective_precipitation = array_like(
        raw, "convective_precipitation_mm", shape
    )
    convective_precipitation = np.where(
        np.isfinite(direct_convective_precipitation),
        np.maximum(direct_convective_precipitation, 0.0),
        diagnosed_convective_precipitation,
    )

    cloud_composition = (
        (np.nan_to_num(cloud_low, nan=0.0) >= 35.0).astype(np.int16)
        + 2 * (np.nan_to_num(cloud_mid, nan=0.0) >= 35.0).astype(np.int16)
        + 4 * (np.nan_to_num(cloud_high, nan=0.0) >= 35.0).astype(np.int16)
    )
    cloud_composition[
        ~np.isfinite(cloud_low) & ~np.isfinite(cloud_mid) & ~np.isfinite(cloud_high)
    ] = 0

    snow_ratio = np.select(
        [temperature <= -10, temperature <= -5, temperature <= 0, temperature <= 1.5],
        [15.0, 12.0, 10.0, 6.0],
        default=2.0,
    )
    snow_fresh = np.maximum(snow, 0.0) * snow_ratio / 10.0
    previous_fresh = previous.get("fresh_snow")
    if previous_fresh is None:
        estimated_snow_depth = snow_fresh.copy()
    else:
        estimated_snow_depth = np.nan_to_num(previous_fresh, nan=0.0) + np.nan_to_num(
            snow_fresh, nan=0.0
        )
        estimated_snow_depth[
            ~np.isfinite(snow_fresh) & ~np.isfinite(previous_fresh)
        ] = np.nan
    direct_snow_depth_cm = array_like(raw, "snow_depth_m", shape) / 10.0
    snow_depth = np.where(
        np.isfinite(direct_snow_depth_cm),
        np.maximum(direct_snow_depth_cm, 0.0),
        estimated_snow_depth,
    )

    snow_phase = np.zeros(shape, dtype=np.int16)
    snow_phase[np.isfinite(precipitation) & (precipitation >= 0.1)] = 1
    snow_phase[(snow >= 0.03) & (temperature > 0.5)] = 2
    snow_phase[(snow >= 0.03) & (temperature <= 0.5)] = 3
    snow_stick = np.zeros(shape, dtype=np.int16)
    snow_stick[(snow_fresh >= 0.05) & (temperature <= 2.0)] = 1
    snow_stick[(snow_fresh >= 0.2) & (temperature <= 1.0)] = 2
    snow_stick[(snow_fresh >= 0.5) & (temperature <= 0.0)] = 3
    snow_risk = np.zeros(shape, dtype=np.int16)
    snow_risk[(snow >= 0.03) | ((precipitation >= 0.2) & (temperature <= 1.5))] = 1
    snow_risk[(snow_fresh >= 0.3) | ((precipitation >= 1) & (temperature <= 0.5))] = 2
    snow_risk[(snow_fresh >= 1.0) | ((precipitation >= 3) & (temperature <= 0))] = 3
    snow_risk[(snow_fresh >= 3.0) | ((precipitation >= 8) & (temperature <= -1))] = 4

    result = {
        "temperature_c": rounded(temperature, 1),
        "temperature_max_12h_c": rounded(temperature_max_12h, 1),
        "temperature_min_12h_c": rounded(temperature_min_12h, 1),
        "wind_chill_c": rounded(wind_chill, 1),
        "dewpoint_c": rounded(dewpoint, 1),
        "humidex": rounded(humidex, 1),
        "surface_temperature_c": rounded(surface_temperature, 1),
        "soil_temperature_c": rounded(soil_temperature, 1),
        "temperature_850_c": rounded(temperature_850, 1),
        "temperature_700_c": rounded(temperature_700, 1),
        "temperature_500_c": rounded(temperature_500, 1),
        "temperature_10_c": rounded(temperature_10, 1),
        "theta_e_850_k": rounded(theta_e_850(temperature_850, humidity_850), 1),
        "humidity_pct": rounded(humidity, 0),
        "humidity_850_pct": rounded(humidity_850, 0),
        "humidity_700_pct": rounded(humidity_700, 0),
        "precipitation_mm": rounded(precipitation, 1),
        "precipitation_total_mm": rounded(rain_total, 1),
        "convective_rate_mmh": rounded(
            np.maximum(array_like(raw, "convective_rate_kgm2s", shape), 0.0) * 3600.0,
            2,
        ),
        "convective_precipitation_mm": rounded(convective_precipitation, 1),
        "precipitable_water_mm": rounded(array_like(raw, "precipitable_water_mm", shape), 1),
        "runoff_total_mm": rounded(runoff_total, 1),
        "cloud_cover_pct": rounded(cloud, 0),
        "cloud_low_pct": rounded(cloud_low, 0),
        "cloud_mid_pct": rounded(cloud_mid, 0),
        "cloud_high_pct": rounded(cloud_high, 0),
        "cloud_convective_pct": rounded(cloud_convective, 0),
        "cloud_composition_code": cloud_composition,
        "cloud_ceiling_m": rounded(array_like(raw, "cloud_ceiling_m", shape), 0),
        "precipitation_rate_mmh": rounded(precipitation_rate, 2),
        "wind_speed_kmh": rounded(wind_speed, 0),
        "wind_direction_deg": rounded(wind_direction, 0),
        "wind_gust_kmh": rounded(gust_speed, 0),
        "wind_u_kmh": rounded(u_wind * 3.6, 1),
        "wind_v_kmh": rounded(v_wind * 3.6, 1),
        "storm_motion_kmh": rounded(storm_motion_speed, 0),
        "storm_motion_u_kmh": rounded(storm_motion_u * 3.6, 1),
        "storm_motion_v_kmh": rounded(storm_motion_v * 3.6, 1),
        "ventilation_rate_m2s": rounded(array_like(raw, "ventilation_rate_m2s", shape), 0),
        "pressure_hpa": rounded(pressure, 0),
        "pressure_surface_hpa": rounded(surface_pressure, 0),
        "surface_pressure_hpa": rounded(surface_pressure, 0),
        "visibility_km": rounded(array_like(raw, "visibility_m", shape) / 1000.0, 1),
        "condition_code": condition,
        "cape_jkg": rounded(cape, 0),
        "sbcape_jkg": rounded(sbcape, 0),
        "mlcape_jkg": rounded(mlcape, 0),
        "mucape_jkg": rounded(mucape, 0),
        "sbcin_jkg": rounded(sbcin, 0),
        "mlcin_jkg": rounded(mlcin, 0),
        "mucin_jkg": rounded(mucin, 0),
        "surface_lifted_index_k": rounded(array_like(raw, "surface_lifted_index_k", shape), 1),
        "best_lifted_index_k": rounded(array_like(raw, "best_lifted_index_k", shape), 1),
        "srh_0_3_m2s2": rounded(array_like(raw, "srh_0_3_m2s2", shape), 0),
        "haines_index": rounded(array_like(raw, "haines_index", shape), 0),
        "cloud_work_jkg": rounded(array_like(raw, "cloud_work_jkg", shape), 0),
        "parcel_lift_pressure_hpa": rounded(
            array_like(raw, "parcel_lift_pressure_pa", shape) / 100.0, 0
        ),
        "reflectivity_dbz": rounded(reflectivity, 0),
        "reflectivity_1000_dbz": rounded(array_like(raw, "reflectivity_1000_dbz", shape), 0),
        "reflectivity_4000_dbz": rounded(array_like(raw, "reflectivity_4000_dbz", shape), 0),
        "geopotential_500_m": rounded(array_like(raw, "geopotential_500_m", shape), 0),
        "geopotential_850_m": rounded(array_like(raw, "geopotential_850_m", shape), 0),
        "freezing_level_m": rounded(array_like(raw, "freezing_level_m", shape), 0),
        "highest_freezing_level_m": rounded(array_like(raw, "highest_freezing_level_m", shape), 0),
        "tropopause_pressure_hpa": rounded(
            array_like(raw, "tropopause_pressure_pa", shape) / 100.0, 0
        ),
        "vertical_velocity_700_pas": rounded(array_like(raw, "vertical_velocity_700_pas", shape), 2),
        "absolute_vorticity_500_1e5s": rounded(
            array_like(raw, "absolute_vorticity_500_s", shape) * 100000.0, 1
        ),
        "absolute_vorticity_900_1e5s": rounded(
            array_like(raw, "absolute_vorticity_900_s", shape) * 100000.0, 1
        ),
        "pv_surface_height_m": rounded(array_like(raw, "pv_surface_height_m", shape), 0),
        "weather_code": condition,
        "graupel_mm": rounded(graupel, 2),
        "thunder_risk_code": thunder,
        "lcl_m": rounded(lcl, 0),
        "lightning_score": rounded(lightning, 0),
        "hail_risk_code": hail,
        "storm_type_code": storm_type,
        "snow_risk_code": snow_risk,
        "snowfall_mm": rounded(snow, 2),
        "snow_mm": rounded(snow, 2),
        "snow_fresh_cm": rounded(snow_fresh, 1),
        "snow_depth_cm": rounded(snow_depth, 1),
        "snow_water_equivalent_mm": rounded(snow_total, 1),
        "snow_stick_risk_code": snow_stick,
        "snow_phase_code": snow_phase,
        "snowfall_total_mm": rounded(snow_total, 1),
        "altitude_m": rounded(altitude, 0),
        "soil_moisture_liquid_pct": rounded(
            array_like(raw, "soil_moisture_liquid_fraction", shape) * 100.0, 1
        ),
        "soil_moisture_vol_pct": rounded(
            array_like(raw, "soil_moisture_vol_fraction", shape) * 100.0, 1
        ),
        "sensible_heat_flux_wm2": rounded(array_like(raw, "sensible_heat_flux_wm2", shape), 0),
        "latent_heat_flux_wm2": rounded(array_like(raw, "latent_heat_flux_wm2", shape), 0),
        "downward_shortwave_wm2": rounded(array_like(raw, "downward_shortwave_wm2", shape), 0),
        "downward_longwave_wm2": rounded(array_like(raw, "downward_longwave_wm2", shape), 0),
    }
    for level, components in wind_components.items():
        result[f"wind_speed_{level}_kmh"] = rounded(wind_speeds[level], 0)
        result[f"wind_u_{level}_kmh"] = rounded(components[0] * 3.6, 1)
        result[f"wind_v_{level}_kmh"] = rounded(components[1] * 3.6, 1)
    state = {
        "rain_total": rain_total,
        "snow_total": snow_total,
        "graupel_total": graupel_total,
        "runoff_total": runoff_total,
        "fresh_snow": estimated_snow_depth,
        "temperature_max_history": maximum_history,
        "temperature_min_history": minimum_history,
    }
    return result, state


def json_number(value: Any, integer: bool = False) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(round(number)) if integer else number


def compact_rows(
    transformed: dict[str, np.ndarray], point_ids: np.ndarray
) -> list[list[int | float | None]]:
    selected = {
        column: np.asarray(transformed[column])[point_ids] for column in VALUE_COLUMNS
    }
    return [
        [
            json_number(selected[column][position], column in INTEGER_COLUMNS)
            for column in VALUE_COLUMNS
        ]
        for position in range(len(point_ids))
    ]


def write_map_places(catalog: NationalCatalog, destination: Path) -> int:
    places = [
        [
            str(commune[1]),
            int(commune[3]),
            round(float(commune[4]), 5),
            round(float(commune[5]), 5),
            str(commune[0]),
            department.code,
        ]
        for department in catalog.departments.values()
        for commune in department.communes
        if int(commune[3]) > 0
    ]
    places.sort(key=lambda place: (-place[1], place[0]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema_version": 2,
                "columns": [
                    "name",
                    "population",
                    "latitude",
                    "longitude",
                    "code_insee",
                    "department",
                ],
                "count": len(places),
                "places": places,
            },
            handle,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        handle.write("\n")
    return len(places)


def write_departments(
    result_directory: Path,
    forecast_directory: Path,
    catalog: NationalCatalog,
    generated_at: str,
) -> tuple[dict[str, Any], int]:
    destination_directory = result_directory / "departements"
    destination_directory.mkdir(parents=True, exist_ok=True)
    department_index: dict[str, Any] = {}
    total_size = 0
    for code, department in catalog.departments.items():
        destination = destination_directory / f"{code}.json"
        with destination.open("w", encoding="utf-8") as output:
            output.write("{")
            output.write('"schema_version":4,"status":"ok","generated_at":')
            json.dump(generated_at, output)
            output.write(',"rain_radius_km":10')
            output.write(',"department":')
            json.dump(code, output)
            output.write(',"columns":')
            json.dump(
                {
                    "points": ["model_index", "latitude", "longitude", "altitude_m"],
                    "communes": [
                        "code_insee",
                        "name",
                        "postal_codes",
                        "population",
                        "latitude",
                        "longitude",
                        "point_id",
                    ],
                    "values": list(VALUE_COLUMNS),
                },
                output,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            output.write(',"points":')
            json.dump(department.points, output, ensure_ascii=False, separators=(",", ":"))
            output.write(',"communes":')
            json.dump(
                department.communes, output, ensure_ascii=False, separators=(",", ":")
            )
            output.write(',"forecast":[')
            first = True
            with (forecast_directory / f"{code}.ndjson").open(
                "r", encoding="utf-8"
            ) as lines:
                for line in lines:
                    if not line.strip():
                        continue
                    if not first:
                        output.write(",")
                    output.write(line.strip())
                    first = False
            output.write("]}\n")
        size = destination.stat().st_size
        total_size += size
        department_index[code] = {
            "file": f"departements/{code}.json",
            "communes": len(department.communes),
            "points": len(department.points),
            "size_bytes": size,
        }
    return department_index, total_size


def forecast_steps(forecast_hours: int) -> list[int]:
    """Échéances GFS à trois heures, de H+0 à H+360."""
    return list(range(0, forecast_hours + 1, 3))


def latest_gfs_run_hint(now: datetime | None = None) -> datetime:
    """Dernier cycle nominal ; sa disponibilité est vérifiée séparément."""
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    available = reference.astimezone(timezone.utc)
    cycle_hour = (available.hour // 6) * 6
    return available.replace(
        hour=cycle_hour, minute=0, second=0, microsecond=0
    )


def gfs_run_available(run_time: datetime, forecast_hours: int) -> bool:
    """Vérifie les index légers du début et de la fin du run demandé."""
    base = (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
            f"gfs.{run_time:%Y%m%d}/{run_time.hour:02d}/atmos")
    for lead in (0, forecast_hours):
        url = f"{base}/gfs.t{run_time.hour:02d}z.pgrb2.0p25.f{lead:03d}.idx"
        response = requests.get(url, timeout=(10, 30), headers={"User-Agent": USER_AGENT})
        if response.status_code in (403, 404):
            return False
        response.raise_for_status()
        if ":TMP:2 m above ground:" not in response.text:
            raise RuntimeError(f"Index NOAA inattendu : {url}")
    return True


def select_ready_run(metadata_url: str, forecast_hours: int, force: bool = False,
                     now: datetime | None = None) -> datetime | None:
    candidate = latest_gfs_run_hint(now)
    # Le cycle précédent peut être prêt alors que le cycle nominal démarre.
    for offset in range(3):
        run = candidate - timedelta(hours=6 * offset)
        if not force and already_published(metadata_url, run):
            LOGGER.info("Run %s déjà publié ; aucune reconstruction", iso_utc(run))
            return None
        if gfs_run_available(run, forecast_hours):
            return run
        LOGGER.info("Run %s incomplet ; nouvelle vérification au prochain passage", iso_utc(run))
    return None


def retrieve_gfs_step(run_time: datetime, lead: int, destination: Path) -> None:
    """Télécharge l'extraction Europe nécessaire, via le filtre NOMADS."""
    cycle = f"{run_time.hour:02d}"
    url = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
    parameters = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{lead:03d}",
        "dir": f"/gfs.{run_time:%Y%m%d}/{cycle}/atmos",
        "subregion": "",
        "leftlon": "-12",
        "rightlon": "18",
        "toplat": "57",
        "bottomlat": "38",
        "var_TMP": "on", "var_DPT": "on", "var_TMAX": "on", "var_TMIN": "on",
        "var_RH": "on", "var_UGRD": "on", "var_VGRD": "on", "var_GUST": "on",
        "var_PRMSL": "on", "var_PRES": "on", "var_HGT": "on",
        "var_APCP": "on", "var_PRATE": "on", "var_ACPCP": "on",
        "var_CPRAT": "on", "var_PWAT": "on", "var_WATR": "on",
        "var_WEASD": "on", "var_SNOD": "on", "var_CAPE": "on", "var_CIN": "on",
        "var_LFTX": "on", "var_4LFTX": "on", "var_HLCY": "on",
        "var_HINDEX": "on", "var_CWORK": "on", "var_PLPL": "on",
        "var_USTM": "on", "var_VSTM": "on", "var_VRATE": "on",
        "var_TCDC": "on", "var_LCDC": "on", "var_MCDC": "on", "var_HCDC": "on",
        "var_VIS": "on", "var_REFC": "on", "var_REFD": "on",
        "var_ABSV": "on", "var_VVEL": "on", "var_HPBL": "on",
        "var_TSOIL": "on", "var_SOILW": "on", "var_SOILL": "on",
        "var_SHTFL": "on", "var_LHTFL": "on", "var_DSWRF": "on", "var_DLWRF": "on",
        "lev_2_m_above_ground": "on", "lev_10_m_above_ground": "on",
        "lev_100_m_above_ground": "on", "lev_10_mb": "on", "lev_200_mb": "on",
        "lev_300_mb": "on", "lev_500_mb": "on", "lev_700_mb": "on",
        "lev_850_mb": "on", "lev_900_mb": "on", "lev_925_mb": "on",
        "lev_950_mb": "on", "lev_mean_sea_level": "on", "lev_surface": "on",
        "lev_entire_atmosphere": "on",
        "lev_entire_atmosphere_\\(considered_as_a_single_layer\\)": "on",
        "lev_low_cloud_layer": "on", "lev_middle_cloud_layer": "on",
        "lev_high_cloud_layer": "on", "lev_convective_cloud_layer": "on",
        "lev_cloud_ceiling": "on", "lev_planetary_boundary_layer": "on",
        "lev_1000_m_above_ground": "on", "lev_4000_m_above_ground": "on",
        "lev_0-0.1_m_below_ground": "on", "lev_3000-0_m_above_ground": "on",
        "lev_6000-0_m_above_ground": "on", "lev_90-0_mb_above_ground": "on",
        "lev_180-0_mb_above_ground": "on", "lev_255-0_mb_above_ground": "on",
        "lev_0C_isotherm": "on", "lev_highest_tropospheric_freezing_level": "on",
        "lev_tropopause": "on",
        "lev_PV=2e-06_\\(Km^2/kg/s\\)_surface": "on",
    }
    response = requests.get(
        url, params=parameters, stream=True, timeout=(20, 180),
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)


def build_product(
    catalog: NationalCatalog,
    forecast_hours: int,
    working_directory: Path,
    run_hint: datetime,
) -> Path:
    result_directory = working_directory / "result"
    forecast_directory = working_directory / "forecast-lines"
    downloads = working_directory / "downloads"
    result_directory.mkdir(parents=True)
    forecast_directory.mkdir(parents=True)
    downloads.mkdir(parents=True)

    line_handles = {
        code: (forecast_directory / f"{code}.ndjson").open("w", encoding="utf-8")
        for code in catalog.departments
    }
    grid = NationalGrid(catalog)
    map_sampler = MapSampler(MAP_WIDTH, MAP_HEIGHT)
    map_renderer = GFSMapRenderer(
        np.empty(0),
        np.empty(0),
        result_directory / "maps",
        width=MAP_WIDTH,
        height=MAP_HEIGHT,
        france_latitudes=catalog.point_latitudes,
        france_longitudes=catalog.point_longitudes,
        france_departments=catalog.point_departments,
        boundary_directory=(
            Path(__file__).resolve().parents[1] / "config" / "natural-earth"
        ),
        department_boundary_path=(
            Path(__file__).resolve().parents[1]
            / "config"
            / "france"
            / "departements.geojson"
        ),
        pregridded=True,
    )

    point_altitude: np.ndarray | None = None
    map_altitude: np.ndarray | None = None
    point_state: dict[str, Any] = {}
    map_state: dict[str, Any] = {}
    model_run = run_hint
    source_bytes = 0

    try:
        steps = forecast_steps(forecast_hours)
        for step_number, lead in enumerate(steps):
            current_paths: list[Path] = []
            try:
                destination = downloads / f"gfs-{lead:03d}h.grib2"
                LOGGER.info("Téléchargement GFS +%03d h", lead)
                retrieve_gfs_step(run_hint, lead, destination)
                source_bytes += destination.stat().st_size
                current_paths.append(destination)
                LOGGER.info(
                    "Décodage et cartes GFS %s/%s : +%03d h",
                    step_number + 1,
                    len(steps),
                    lead,
                )
                step = parse_grib_files(current_paths, grid, map_sampler if lead <= 240 else None, lead)
                model_run = model_run or step["run_time"]
                if lead == 0:
                    point_altitude = step["values"].get("surface_geopotential")
                    map_altitude = step["map_values"].get("surface_geopotential")
                    if point_altitude is None:
                        point_altitude = step["values"].get("surface_altitude_m")
                        map_altitude = step["map_values"].get("surface_altitude_m")
                    if point_altitude is None:
                        point_altitude = np.zeros(len(catalog.model_indexes))
                    if map_altitude is None:
                        map_altitude = np.zeros((MAP_HEIGHT, MAP_WIDTH))
                    for department in catalog.departments.values():
                        for position, global_id in enumerate(department.global_point_ids):
                            department.points[position].append(
                                json_number(point_altitude[int(global_id)], integer=True)
                            )
                assert point_altitude is not None and map_altitude is not None
                point_raw = step.pop("values")
                map_raw = step.pop("map_values")
                transformed, point_state = transform_step(
                    point_raw, point_altitude, point_state, lead
                )
                point_raw.clear()
                # Au-delà de J+10 : uniquement les tableaux, aucune carte supplémentaire.
                if lead <= 240:
                    map_transformed, map_state = transform_step(
                        map_raw, map_altitude, map_state, lead
                    )
                    map_raw.clear()
                    map_fields = {
                        key: values
                        for key, values in map_transformed.items()
                        if key in MAP_FIELDS
                    }
                    del map_transformed
                    map_renderer.render_step(
                        lead_hour=lead,
                        valid_time=step["valid_time"],
                        fields=map_fields,
                    )
                iso_time = iso_utc(step["valid_time"])
                for code, department in catalog.departments.items():
                    line = [
                        iso_time,
                        compact_rows(transformed, department.global_point_ids),
                    ]
                    json.dump(
                        line,
                        line_handles[code],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    line_handles[code].write("\n")
            finally:
                for path in current_paths:
                    path.unlink(missing_ok=True)
    finally:
        for handle in line_handles.values():
            handle.close()

    generated_at = iso_utc(datetime.now(timezone.utc))
    assert generated_at is not None
    run_time = iso_utc(model_run)
    places_path = result_directory / "maps" / "communes.json"
    places_count = write_map_places(catalog, places_path)
    map_manifest = map_renderer.write_manifest(
        generated_at=generated_at,
        run_time=run_time,
        places_path="maps/communes.json",
    )
    department_index, total_size = write_departments(
        result_directory,
        forecast_directory,
        catalog,
        generated_at,
    )

    model = {
        "name": "GFS déterministe",
        "provider": "NOAA / NCEP",
        "dataset": "NOAA Global Forecast System — GFS 0,25°",
        "domain": "Monde, extraction France et Europe de l'Ouest",
        "resolution_degrees": 0.25,
        "resolution_km": 28.0,
        "forecast_hours_requested": forecast_hours,
        "run_time": run_time,
        "pipeline_version": PIPELINE_VERSION,
        "catalog_version": catalog.version,
        "storm_diagnostics": True,
        "snow_diagnostics": True,
        "source_url": DATASET_PAGE,
        "source_size_bytes": source_bytes,
        "license": "Données publiques NOAA / NCEP",
    }
    index = {
        "schema_version": 4,
        "status": "ok",
        "generated_at": generated_at,
        "model": model,
        "coverage": {
            "label": "France métropolitaine et Corse",
            "communes": catalog.commune_count,
            "departments": len(catalog.departments),
        },
        "condition_codes": CONDITION_CODES,
        "diagnostics": {
            "direct": [
                "MUCAPE",
                "taux de précipitation instantané",
                "pluie cumulée",
                "neige cumulée",
                "pression de surface",
                "nébulosité totale",
                "pression au niveau de la mer",
            ],
            "derived": [
                "humidité relative à 2 m",
                "LCL",
                "risque orage",
                "phase et tenue de la neige",
            ],
        },
        "search": {
            "provider": "API Découpage administratif — République française",
            "endpoint": "https://geo.api.gouv.fr/communes",
        },
        "maps": {
            "status": "ok",
            "module_version": map_manifest["module_version"],
            "manifest": "maps/index.json",
            "layers": len(map_manifest["layers"]),
            "steps": len(map_manifest["steps"]),
            "places": places_count,
        },
        "departments": department_index,
        "total_department_bytes": total_size,
    }
    with (result_directory / "index.json").open("w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    LOGGER.info(
        "Produit GFS prêt : %.1f Mo de tableaux, %s couches, %s échéances",
        total_size / 1e6,
        len(map_manifest["layers"]),
        len(map_manifest["steps"]),
    )
    return result_directory


def safe_output_directory(path: Path) -> Path:
    resolved = path.resolve()
    forbidden = {Path("/").resolve(), Path.cwd().resolve(), Path.home().resolve()}
    if resolved in forbidden or len(resolved.parts) < 3:
        raise RuntimeError(f"Dossier de sortie dangereux : {resolved}")
    return resolved


def publish_result(source: Path, destination: Path) -> None:
    target = safe_output_directory(destination)
    temporary = target.with_name(target.name + ".new")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    if target.exists():
        shutil.rmtree(target)
    temporary.replace(target)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    if not 3 <= args.forecast_hours <= 360:
        raise ValueError("forecast-hours doit être compris entre 3 et 360")
    if args.forecast_hours % 3:
        raise ValueError("forecast-hours doit être un multiple de 3")
    catalog = load_catalog(Path(args.catalog))
    run_hint = select_ready_run(args.current_metadata_url, args.forecast_hours, args.force)
    if run_hint is None:
        LOGGER.info("Aucun nouveau run complet à publier")
        return 0
    LOGGER.info("Run GFS sélectionné : %s", iso_utc(run_hint))

    with tempfile.TemporaryDirectory(
        prefix="gfs-france-build-", ignore_cleanup_errors=True
    ) as temporary:
        result = build_product(
            catalog,
            args.forecast_hours,
            Path(temporary),
            run_hint,
        )
        publish_result(result, Path(args.output_dir))
    LOGGER.info("Fichiers nationaux prêts dans %s", args.output_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        LOGGER.exception("Échec de la mise à jour GFS France")
        raise SystemExit(1)
