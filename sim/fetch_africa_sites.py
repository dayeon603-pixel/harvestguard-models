"""Fetch NASA POWER climatology for African smallholder horticulture regions.

One pod specification cannot serve a continent. Africa spans equatorial highland
(Meru, 6.19 kWh/m2/day at 17.6 degC), humid lowland tropics (Ho, 4.91 at 26.7 degC),
Sahel (Kano, high irradiance and extreme heat), and Mediterranean-influenced north.
Supply and demand move independently across those zones, so the sizing engine has to be
parametric and the product line has to be a small set of SKUs that covers the space.

This module fetches and caches the climatology. It is separated from the model so the
network call happens once and every downstream run is reproducible offline.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CACHE_DIR: Final[Path] = Path(__file__).parent / "data" / "africa"
POWER_URL: Final[str] = (
    "https://power.larc.nasa.gov/api/temporal/climatology/point"
    "?parameters=ALLSKY_SFC_SW_DWN,T2M,T2M_MAX,RH2M&community=RE"
    "&longitude={lon}&latitude={lat}&format=JSON"
)
REQUEST_PAUSE_S: Final[float] = 0.4
MAX_RETRIES: Final[int] = 3


@dataclass(frozen=True, slots=True)
class SiteSpec:
    """A candidate deployment region."""

    key: str
    name: str
    country: str
    iso: str
    lat: float
    lon: float
    zone: str
    note: str
    harmattan: bool = False


# Regions chosen for smallholder horticulture density and cold-chain absence, spread
# across the continent's agro-ecological zones rather than by population alone.
SITES: Final[tuple[SiteSpec, ...]] = (
    # --- West Africa, humid coastal / forest ---
    SiteSpec("ho", "Ho, Volta", "Ghana", "GH", 6.601, 0.471, "humid lowland", "pilot site", True),
    SiteSpec("kumasi", "Kumasi", "Ghana", "GH", 6.700, -1.625, "humid forest", "Ashanti veg belt", True),
    SiteSpec("tamale", "Tamale", "Ghana", "GH", 9.404, -0.839, "guinea savanna", "northern tomato", True),
    SiteSpec("ibadan", "Ibadan", "Nigeria", "NG", 7.378, 3.947, "humid forest", "SW veg corridor", True),
    SiteSpec("owerri", "Owerri", "Nigeria", "NG", 5.483, 7.035, "humid forest", "ColdHubs base", True),
    SiteSpec("cotonou", "Cotonou", "Benin", "BJ", 6.370, 2.391, "humid coastal", "coastal market", True),
    SiteSpec("abidjan", "Abidjan", "Côte d'Ivoire", "CI", 5.345, -4.024, "humid coastal", "coastal market", True),
    SiteSpec("yaounde", "Yaoundé", "Cameroon", "CM", 3.848, 11.502, "humid forest", "central forest", False),
    # --- West Africa, Sahel / Sudan savanna ---
    SiteSpec("kano", "Kano", "Nigeria", "NG", 12.002, 8.592, "sahel", "dry-season irrigation", True),
    SiteSpec("ouaga", "Ouagadougou", "Burkina Faso", "BF", 12.371, -1.520, "sahel", "onion/tomato", True),
    SiteSpec("bamako", "Bamako", "Mali", "ML", 12.639, -8.003, "sahel", "Niger valley veg", True),
    SiteSpec("niamey", "Niamey", "Niger", "NE", 13.512, 2.112, "sahel", "onion belt", True),
    SiteSpec("dakar", "Dakar", "Senegal", "SN", 14.716, -17.467, "sahel coastal", "Niayes horticulture", True),
    SiteSpec("jos", "Jos Plateau", "Nigeria", "NG", 9.897, 8.858, "highland savanna", "cool-season veg", True),
    # --- East Africa highlands ---
    SiteSpec("meru", "Meru", "Kenya", "KE", 0.050, 37.650, "equatorial highland", "design baseline", False),
    SiteSpec("nakuru", "Nakuru", "Kenya", "KE", -0.303, 36.080, "equatorial highland", "Rift veg", False),
    SiteSpec("arusha", "Arusha", "Tanzania", "TZ", -3.387, 36.683, "equatorial highland", "horticulture hub", False),
    SiteSpec("mbeya", "Mbeya", "Tanzania", "TZ", -8.900, 33.456, "southern highland", "cool highland", False),
    SiteSpec("kampala", "Kampala", "Uganda", "UG", 0.348, 32.582, "equatorial lake", "lake basin", False),
    SiteSpec("kigali", "Kigali", "Rwanda", "RW", -1.944, 30.062, "equatorial highland", "terraced veg", False),
    SiteSpec("addis", "Addis Ababa", "Ethiopia", "ET", 9.005, 38.763, "high plateau", "2350 m plateau", False),
    SiteSpec("hawassa", "Hawassa", "Ethiopia", "ET", 7.062, 38.476, "rift highland", "Rift veg", False),
    SiteSpec("bahirdar", "Bahir Dar", "Ethiopia", "ET", 11.594, 37.391, "rift highland", "Tana basin", False),
    SiteSpec("hargeisa", "Hargeisa", "Somaliland", "SO", 9.560, 44.065, "arid highland", "arid highland", False),
    # --- Southern Africa ---
    SiteSpec("lusaka", "Lusaka", "Zambia", "ZM", -15.387, 28.323, "southern plateau", "plateau veg", False),
    SiteSpec("harare", "Harare", "Zimbabwe", "ZW", -17.825, 31.033, "southern plateau", "plateau veg", False),
    SiteSpec("lilongwe", "Lilongwe", "Malawi", "MW", -13.963, 33.787, "southern plateau", "smallholder veg", False),
    SiteSpec("nampula", "Nampula", "Mozambique", "MZ", -15.116, 39.266, "humid lowland", "northern corridor", False),
    SiteSpec("polokwane", "Polokwane", "South Africa", "ZA", -23.904, 29.469, "subtropical plateau", "Limpopo veg", False),
    SiteSpec("windhoek", "Windhoek", "Namibia", "NA", -22.560, 17.065, "arid plateau", "arid plateau", False),
    # --- Central + North ---
    SiteSpec("kinshasa", "Kinshasa", "DR Congo", "CD", -4.325, 15.322, "humid lowland", "peri-urban veg", False),
    SiteSpec("fayoum", "Fayoum", "Egypt", "EG", 29.309, 30.842, "hot desert", "Nile horticulture", False),
    SiteSpec("marrakech", "Marrakech", "Morocco", "MA", 31.630, -7.999, "semi-arid", "Haouz plain", False),
)


def fetch_site(site: SiteSpec, *, force: bool = False) -> dict:
    """Fetch one site's climatology, caching the raw response to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{site.key}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())

    url = POWER_URL.format(lat=site.lat, lon=site.lon)
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
            path.write_text(json.dumps(payload))
            return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("  %s attempt %d/%d failed: %s", site.key, attempt, MAX_RETRIES, exc)
            time.sleep(REQUEST_PAUSE_S * attempt * 3)
    raise RuntimeError(f"could not fetch {site.key}") from last_error


def main() -> None:
    logger.info("Fetching NASA POWER climatology for %d African sites", len(SITES))
    fetched = cached = 0
    for site in SITES:
        path = CACHE_DIR / f"{site.key}.json"
        was_cached = path.exists()
        data = fetch_site(site)
        ghi = data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]["ANN"]
        temp = data["properties"]["parameter"]["T2M"]["ANN"]
        elev = data["geometry"]["coordinates"][2]
        logger.info("  %-10s %-22s %-14s GHI %.2f  T %.1f degC  elev %4.0f m",
                    site.key, f"{site.name}, {site.country}", site.zone, ghi, temp, elev)
        if was_cached:
            cached += 1
        else:
            fetched += 1
            time.sleep(REQUEST_PAUSE_S)
    logger.info("done: %d fetched, %d from cache -> %s", fetched, cached, CACHE_DIR)


if __name__ == "__main__":
    main()
