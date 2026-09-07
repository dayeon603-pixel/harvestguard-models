"""Size HarvestGuard pods for Vietnamese smallholder horticulture regions.

Vietnam loses 20 to 30% of fruit and vegetable output between harvest and retail. The gap
is at the rural node: the Mekong Delta holds 180,000 t of cold storage against 2 Mt of
annual fruit production, and Central Highlands growers truck produce to other provinces to
be chilled.

The question this module answers is narrow and checkable: does a pod sized for African
conditions work in Vietnam, or does Vietnam need its own configuration? It reuses the
continental sizing engine unchanged, so the Vietnamese answer is produced by exactly the
method that produced the African one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

import fetch_africa_sites as fas
import africa_sizing_engine as eng
from fetch_africa_sites import SiteSpec

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Cache Vietnamese climatology separately from the African set.
VN_CACHE: Final[Path] = Path(__file__).parent / "data" / "vietnam"
fas.CACHE_DIR = VN_CACHE
eng.CACHE_DIR = VN_CACHE

# Regions chosen for smallholder horticulture density and cold-chain absence, spread across
# Vietnam's agro-ecological range rather than by population.
VN_SITES: Final[tuple[SiteSpec, ...]] = (
    SiteSpec("dalat", "Đà Lạt, Lâm Đồng", "Vietnam", "VN", 11.940, 108.458,
             "highland temperate", "national vegetable belt, ~1500 m"),
    SiteSpec("buonmathuot", "Buôn Ma Thuột, Đắk Lắk", "Vietnam", "VN", 12.680, 108.050,
             "highland tropical", "Central Highlands, ~500 m"),
    SiteSpec("moc_chau", "Mộc Châu, Sơn La", "Vietnam", "VN", 20.840, 104.633,
             "northern highland", "temperate veg and stone fruit"),
    SiteSpec("cantho", "Cần Thơ", "Vietnam", "VN", 10.030, 105.780,
             "humid delta", "Mekong Delta hub"),
    SiteSpec("mytho", "Mỹ Tho, Tiền Giang", "Vietnam", "VN", 10.360, 106.360,
             "humid delta", "Mekong fruit belt"),
    SiteSpec("phanthiet", "Phan Thiết, Bình Thuận", "Vietnam", "VN", 10.930, 108.100,
             "dry coastal", "dragon fruit, high irradiance"),
)

# The three African SKUs, from africa_sizing_engine's DP partition.
AFRICAN_SKUS: Final[tuple[tuple[str, float, float, int], ...]] = (
    ("HG-A", 200.0, 2.4, 451),
    ("HG-B", 300.0, 3.6, 524),
    ("HG-C", 440.0, 4.8, 603),
)


def covering_sku(array_w: float, battery_kwh: float) -> str | None:
    """Smallest African SKU that meets both the array and battery requirement."""
    for name, sku_w, sku_kwh, _ in AFRICAN_SKUS:
        if sku_w >= array_w and sku_kwh >= battery_kwh:
            return name
    return None


def main() -> None:
    logger.info("Fetching NASA POWER climatology for %d Vietnamese sites", len(VN_SITES))
    for site in VN_SITES:
        fas.fetch_site(site)
    logger.info("cached to %s\n", VN_CACHE)

    setpoint = eng.SETPOINT_WARM_C
    logger.info("=" * 104)
    logger.info("VIETNAM SIZING — solanaceous / mixed horticulture programme (%.0f degC)", setpoint)
    logger.info("=" * 104)
    logger.info("%-26s %-20s %5s %6s  %7s %8s %7s %7s  %s",
                "site", "zone", "GHI", "T mean", "min arr", "rec arr", "batt", "cost", "African SKU")

    sizings = []
    for site in VN_SITES:
        s = eng.size_site(site, setpoint)
        sizings.append(s)
        sku = covering_sku(s.array_w, s.battery_kwh)
        logger.info("%-26s %-20s %5.2f %5.1fC  %6.0fW %7.0fW %6.1fkWh $%6.0f  %s",
                    s.name, s.zone, s.annual_ghi, s.mean_temp_c,
                    s.min_array_w, s.array_w, s.battery_kwh, s.cost_usd,
                    sku if sku else "NONE — exceeds HG-C")

    arrays = [s.array_w for s in sizings]
    logger.info("-" * 104)
    logger.info("Vietnam array range: %.0f W to %.0f W (%.1fx). African range was 120 W to 440 W (3.7x).",
                min(arrays), max(arrays), max(arrays) / min(arrays))

    uncovered = [s for s in sizings if covering_sku(s.array_w, s.battery_kwh) is None]
    logger.info("Sites covered by an existing African SKU: %d of %d",
                len(sizings) - len(uncovered), len(sizings))
    if uncovered:
        logger.info("Not covered: %s", ", ".join(s.name for s in uncovered))

    # Cool programme. Da Lat is Vietnam's temperate vegetable belt, so the 6 degC hold is
    # not optional there: cabbage, lettuce and carrot do not belong in a 13 degC pod.
    logger.info("\n" + "=" * 104)
    logger.info("COOL PROGRAMME (%.0f degC) — temperate vegetables, the Da Lat and Moc Chau crop", eng.SETPOINT_COOL_C)
    logger.info("=" * 104)
    cool = []
    for site in VN_SITES:
        c = eng.size_site(site, eng.SETPOINT_COOL_C)
        cool.append(c)
        sku = covering_sku(c.array_w, c.battery_kwh)
        logger.info("%-26s %7.0fW %6.1fkWh $%6.0f  %s", c.name, c.array_w, c.battery_kwh,
                    c.cost_usd, sku if sku else "EXCEEDS HG-C — needs a fourth SKU")
    cool_uncovered = [c for c in cool if covering_sku(c.array_w, c.battery_kwh) is None]
    logger.info("-" * 104)
    logger.info("Cool-programme sites covered by an existing SKU: %d of %d. Range %.0f W to %.0f W "
                "against an HG-C ceiling of 440 W.", len(cool) - len(cool_uncovered), len(cool),
                min(c.array_w for c in cool), max(c.array_w for c in cool))

    out = Path(__file__).parent / "out" / "vietnam_sizing.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps([{
        "key": s.key, "name": s.name, "zone": s.zone, "lat": s.lat, "lon": s.lon,
        "annual_ghi": s.annual_ghi, "mean_temp_c": s.mean_temp_c, "worst_month": s.worst_month,
        "min_array_w": s.min_array_w, "array_w": s.array_w, "battery_kwh": s.battery_kwh,
        "autonomy_h": s.autonomy_h, "cost_usd": s.cost_usd,
        "african_sku": covering_sku(s.array_w, s.battery_kwh),
        "cool_array_w": eng.size_site(
            next(v for v in VN_SITES if v.key == s.key), eng.SETPOINT_COOL_C).array_w,
    } for s in sizings], indent=2, ensure_ascii=False))
    logger.info("\nwrote %s", out)


if __name__ == "__main__":
    main()
