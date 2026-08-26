from __future__ import annotations

import sys
import tempfile
import unittest
import re
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_gfs_france import (  # noqa: E402
    GFS_NI,
    MapSampler,
    NationalGrid,
    forecast_steps,
    grid_index,
    message_field,
    normalize_gfs_units,
    storm_diagnostics,
    transform_step,
)
from gfs_maps import GFSMapRenderer  # noqa: E402


class GFSGridTests(unittest.TestCase):
    def test_france_negative_longitude_wraps_on_global_grid(self) -> None:
        index, latitude, longitude = grid_index(48.8566, 2.3522)
        self.assertGreaterEqual(index, 0)
        self.assertAlmostEqual(latitude, 48.75, places=2)
        self.assertAlmostEqual(longitude, 2.25, places=2)

        _index, _latitude, longitude = grid_index(48.4, -4.5)
        self.assertAlmostEqual(longitude, 355.5, places=2)
        self.assertLess(_index % GFS_NI, GFS_NI)

    def test_deterministic_schedule_to_240_hours(self) -> None:
        steps = forecast_steps(240)
        self.assertEqual(steps[0], 0)
        self.assertEqual(steps[-1], 240)
        self.assertEqual(len(steps), 81)
        self.assertEqual(steps[48], 144)
        self.assertEqual(steps[49], 147)

    def test_map_sampling_covers_the_complete_domain(self) -> None:
        sampler = MapSampler(601, 180)
        self.assertEqual(sampler.target_latitudes.shape, (180,))
        self.assertEqual(sampler.target_longitudes.shape, (601,))

    def test_noaa_south_to_north_subset_grid_is_supported(self) -> None:
        catalog = SimpleNamespace(
            point_latitudes=np.array([41.5, 51.0]),
            point_longitudes=np.array([0.0, 359.75]),
            model_indexes=[0, 1],
        )
        metadata = {
            "Ni": 121,
            "Nj": 77,
            "latitudeOfFirstGridPointInDegrees": 38.0,
            "latitudeOfLastGridPointInDegrees": 57.0,
            "longitudeOfFirstGridPointInDegrees": 348.0,
            "iDirectionIncrementInDegrees": 0.25,
            "jDirectionIncrementInDegrees": 0.25,
        }

        with patch(
            "update_gfs_france.safe_get",
            side_effect=lambda _gid, key, default=None: metadata.get(key, default),
        ):
            grid = NationalGrid(catalog)
            grid.validate(1)

        self.assertEqual(grid.latitude_step, 0.25)
        self.assertEqual(len(grid.point_indexes), 2)
        self.assertTrue(all(0 <= value < 121 * 77 for value in grid.point_indexes))

    def test_surface_fields_are_transformed(self) -> None:
        shape = (2,)
        raw = {
            "temperature_k": np.array([273.15, 293.15]),
            "dewpoint_k": np.array([271.15, 288.15]),
            "wind_u_ms": np.array([3.0, 4.0]),
            "wind_v_ms": np.array([4.0, 3.0]),
            "gust_speed_ms": np.array([8.0, 10.0]),
            "surface_pressure_pa": np.array([101000.0, 100000.0]),
            "mean_sea_pressure_pa": np.array([102000.0, 101500.0]),
            "cloud_total_fraction": np.array([25.0, 80.0]),
            "precipitation_total_m": np.array([0.0, 2.5]),
        }
        result, _state = transform_step(raw, np.zeros(shape), {}, 0)
        np.testing.assert_allclose(result["temperature_c"], [0.0, 20.0])
        np.testing.assert_allclose(result["wind_speed_kmh"], [18.0, 18.0])
        np.testing.assert_allclose(result["pressure_hpa"], [1020.0, 1015.0])
        self.assertTrue(np.all(np.isfinite(result["humidity_pct"])))

    def test_dry_mucape_does_not_create_a_false_thunderstorm_risk(self) -> None:
        diagnostics = storm_diagnostics(
            cape=np.array([1374.0]),
            precipitation=np.array([0.0]),
            precipitation_rate=np.array([0.0]),
            humidity=np.array([75.0]),
            reflectivity=np.array([np.nan]),
            graupel=np.array([0.0]),
            gust_speed=np.array([0.0]),
            step_hours=3.0,
        )
        thunder, lightning, hail, convective_rain, storm_type = diagnostics
        self.assertEqual(int(thunder[0]), 0)
        self.assertEqual(float(lightning[0]), 0.0)
        self.assertEqual(int(hail[0]), 0)
        self.assertEqual(float(convective_rain[0]), 0.0)
        self.assertEqual(int(storm_type[0]), 0)

    def test_active_precipitation_and_mucape_raise_the_risk_progressively(self) -> None:
        thunder, lightning, _hail, _convective_rain, _storm_type = (
            storm_diagnostics(
                cape=np.array([150.0, 650.0, 1400.0, 2400.0]),
                precipitation=np.array([0.6, 3.0, 10.0, 30.0]),
                precipitation_rate=np.array([0.2, 1.2, 4.0, 10.0]),
                humidity=np.array([60.0, 65.0, 70.0, 75.0]),
                reflectivity=np.full(4, np.nan),
                graupel=np.zeros(4),
                gust_speed=np.array([20.0, 35.0, 65.0, 105.0]),
                step_hours=3.0,
            )
        )
        np.testing.assert_array_equal(thunder, [1, 2, 3, 4])
        self.assertTrue(np.all(np.diff(lightning) > 0))

    def test_only_surface_geopotential_is_used_as_altitude(self) -> None:
        metadata = {
            1: {"shortName": "z", "typeOfLevel": "surface"},
            2: {"shortName": "z", "typeOfLevel": "isobaricInhPa"},
        }

        def fake_get(gid: int, key: str, default=None):
            return metadata.get(gid, {}).get(key, default)

        with patch("update_gfs_france.safe_get", side_effect=fake_get):
            self.assertEqual(message_field(1), "surface_geopotential")
            self.assertIsNone(message_field(2))

    def test_gfs_grib_names_and_levels_are_selected_safely(self) -> None:
        metadata = {
            1: {"shortName": "t", "typeOfLevel": "surface", "level": 0},
            2: {"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2},
            3: {"shortName": "sdwe", "typeOfLevel": "surface", "level": 0},
            4: {"shortName": "sde", "typeOfLevel": "surface", "level": 0},
        }

        def fake_get(gid: int, key: str, default=None):
            return metadata.get(gid, {}).get(key, default)

        with patch("update_gfs_france.safe_get", side_effect=fake_get):
            self.assertIsNone(message_field(1))
            self.assertEqual(message_field(2), "temperature_k")
            self.assertEqual(message_field(3), "snow_total_m")
            self.assertEqual(message_field(4), "snow_depth_m")

    def test_noaa_units_are_not_multiplied_twice(self) -> None:
        point = np.array([12.5])
        mapped = np.array([[12.5]])
        rain, rain_map = normalize_gfs_units(
            "precipitation_total_m", point, mapped, "kg m**-2"
        )
        cloud, cloud_map = normalize_gfs_units(
            "cloud_total_fraction", np.array([78.0]), np.array([[78.0]]), "%"
        )
        depth, depth_map = normalize_gfs_units(
            "snow_depth_m", np.array([0.42]), np.array([[0.42]]), "m"
        )
        np.testing.assert_allclose(rain, [12.5])
        np.testing.assert_allclose(rain_map, [[12.5]])
        np.testing.assert_allclose(cloud, [78.0])
        np.testing.assert_allclose(cloud_map, [[78.0]])
        np.testing.assert_allclose(depth, [420.0])
        np.testing.assert_allclose(depth_map, [[420.0]])

    def test_wordpress_tabs_use_gfs_dataset_keys(self) -> None:
        script = (
            ROOT / "wordpress" / "gfs-noaa-france" / "assets" / "gfs-meteo.js"
        ).read_text(encoding="utf-8")
        self.assertIn("dataset.gfsTab", script)
        self.assertIn("dataset.gfsPanel", script)
        self.assertNotIn("dataset.cepTab", script)
        self.assertNotIn("dataset.cepPanel", script)

    def test_nomads_query_uses_existing_gfs_snow_field(self) -> None:
        source = (ROOT / "scripts" / "update_gfs_france.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"var_WEASD": "on"', source)
        self.assertNotIn('"var_ASNOW": "on"', source)

    def test_precise_department_boundaries_are_rendered(self) -> None:
        boundary_path = (
            ROOT
            / "config"
            / "france"
            / "departements.geojson"
        )
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "maps"
            GFSMapRenderer(
                np.empty(0),
                np.empty(0),
                output_directory,
                width=320,
                height=240,
                department_boundary_path=boundary_path,
                pregridded=True,
            )
            overlay = (output_directory / "frontieres.svg").read_text(
                encoding="utf-8",
            )

        self.assertIn('data-gfsm-quality="precise"', overlay)
        self.assertIn('data-gfsm-hide-deep="0"', overlay)
        self.assertGreater(overlay.count("M"), 100)
        department_path = re.search(
            r'<path d="([^"]+)"[^>]+data-gfsm-layer="departments"',
            overlay,
        )
        self.assertIsNotNone(department_path)
        self.assertRegex(department_path.group(1), r'\d+\.[1-9]')

    def test_wind_overlay_uses_pressure_isobars_and_screen_arrows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "maps"
            renderer = GFSMapRenderer(
                np.empty(0),
                np.empty(0),
                output_directory,
                width=224,
                height=168,
                pregridded=True,
            )
            x_axis = np.linspace(0.0, 1.0, 224)
            pressure = np.tile(1004.0 + x_axis * 16.0, (168, 1))
            wind_u = np.full((168, 224), 24.0)
            wind_v = np.full((168, 224), 8.0)
            destination = output_directory / "vectors" / "vent" / "000.svg"
            renderer._write_wind_overlay(
                wind_u, wind_v, destination, pressure
            )
            overlay = destination.read_text(encoding="utf-8")

        self.assertIn('data-gfsm-role="isobars"', overlay)
        self.assertIn('data-gfsm-interval="4"', overlay)
        self.assertIn('data-gfsm-role="isobar-labels"', overlay)
        self.assertIn('data-gfsm-labels="', overlay)
        self.assertIn('data-gfsm-role="wind-arrows"', overlay)
        self.assertIn('data-gfsm-points="', overlay)
        arrow_points = overlay.split('data-gfsm-points="', 1)[1].split('"', 1)[0]
        parsed_points = [
            tuple(float(value) for value in point.split(","))
            for point in arrow_points.split(";")
            if point
        ]
        self.assertGreater(len(parsed_points), 0)
        for x, y, _dx, _dy, _speed in parsed_points:
            self.assertGreaterEqual(
                renderer._isobar_clearance(pressure, int(x), int(y), 4.0),
                0.62,
            )

    def test_period_layers_reuse_numeric_maps_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory) / "maps"
            renderer = GFSMapRenderer(
                np.empty(0),
                np.empty(0),
                output_directory,
                width=64,
                height=48,
                pregridded=True,
            )
            shape = (48, 64)
            renderer.render_step(
                lead_hour=3,
                valid_time=datetime(2026, 8, 26, 3, tzinfo=timezone.utc),
                fields={
                    "precipitation_total_mm": np.full(shape, 7.5),
                    "wind_gust_kmh": np.full(shape, 62.0),
                    "wind_speed_kmh": np.full(shape, 25.0),
                    "wind_u_kmh": np.full(shape, 24.0),
                    "wind_v_kmh": np.full(shape, 7.0),
                    "pressure_hpa": np.tile(
                        np.linspace(1004.0, 1020.0, 64), (48, 1)
                    ),
                },
            )
            manifest = renderer.write_manifest(
                generated_at="2026-08-26T03:30:00Z",
                run_time="2026-08-26T00:00:00Z",
            )

        step = manifest["steps"][0]
        self.assertEqual(step["files"]["rafales_max"], step["files"]["rafales"])
        self.assertEqual(step["probes"]["rafales_max"], step["probes"]["rafales"])
        self.assertEqual(
            manifest["layers"]["pluie_cumul"]["range_mode"], "difference"
        )
        self.assertEqual(
            manifest["layers"]["rafales_max"]["range_mode"], "maximum"
        )
        self.assertEqual(
            manifest["layers"]["rafales_max"]["source_key"], "rafales"
        )


if __name__ == "__main__":
    unittest.main()
