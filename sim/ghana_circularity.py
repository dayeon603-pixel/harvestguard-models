"""Material and emissions loop for one HarvestGuard pod in Ghana.

The CircularEconomy4Ghana priority areas reward material-loop closure, not efficiency
alone. The application asserts that near-spoilage produce is "routed to composting or
animal feed", which appears nowhere in the underlying engineering or cost documents. This
module replaces the assertion with a quantified stream so the claim is specified rather
than gestured at, and so the pod's circular contribution can be stated in tonnes.

It also checks the deck's two impact figures against each other. Slide 3 currently claims
~3.2 t CO2e avoided per pod per year alongside "1+ tonne of produce kept reaching market".
Those two numbers cannot both be right at any defensible emissions factor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CRATES_PER_POD: Final[int] = 40
CRATE_KG: Final[float] = 10.0
UTILISATION: Final[float] = 0.55
CRATES_LOADED_PER_DAY: Final[int] = 6
DAYS_PER_YEAR: Final[int] = 365

BASELINE_LOSS: Final[float] = 0.35    # addressable spoilage without cold holding
RESIDUAL_LOSS: Final[float] = 0.08    # with cold holding

# Emissions factor for fruit/vegetable waste decomposing anaerobically in an uncontrolled
# dump, expressed as kg CO2e per kg wet waste. IPCC-derived values for food waste to
# unmanaged disposal cluster in the 0.6-1.2 range; 0.9 is used as the central case.
EF_LOW: Final[float] = 0.6
EF_CENTRAL: Final[float] = 0.9
EF_HIGH: Final[float] = 1.2

COMPOST_YIELD_FRACTION: Final[float] = 0.30   # finished compost per kg wet feedstock
BSF_LARVAE_YIELD_FRACTION: Final[float] = 0.12  # larval biomass per kg wet substrate
COMPOST_PRICE_GHS_PER_KG: Final[float] = 1.20
GHS_PER_USD: Final[float] = 11.029
GLUT_PRICE_GHS_PER_KG: Final[float] = 8.0


@dataclass(frozen=True, slots=True)
class PodFlows:
    """Annual mass flows through one pod."""

    @property
    def throughput_kg(self) -> float:
        """Produce passing through the pod per year."""
        return CRATES_LOADED_PER_DAY * CRATE_KG * DAYS_PER_YEAR

    @property
    def saved_kg(self) -> float:
        """Food kept in the value chain that would otherwise have spoiled."""
        return self.throughput_kg * (BASELINE_LOSS - RESIDUAL_LOSS)

    @property
    def reject_kg(self) -> float:
        """Organic material still lost, available for valorisation at the pod."""
        return self.throughput_kg * RESIDUAL_LOSS

    @property
    def compost_kg(self) -> float:
        return self.reject_kg * COMPOST_YIELD_FRACTION

    @property
    def bsf_larvae_kg(self) -> float:
        return self.reject_kg * BSF_LARVAE_YIELD_FRACTION

    def co2e_avoided_t(self, ef: float) -> float:
        return self.saved_kg * ef / 1000.0

    @property
    def food_value_retained_ghs(self) -> float:
        return self.saved_kg * GLUT_PRICE_GHS_PER_KG


def main() -> None:
    f = PodFlows()
    logger.info("=" * 78)
    logger.info("CIRCULAR MATERIAL LOOP, ONE POD, ONE YEAR (Ghana, %.0f%% utilisation)",
                UTILISATION * 100)
    logger.info("=" * 78)
    logger.info("Throughput                    %8.0f kg", f.throughput_kg)
    logger.info("Food kept in the chain        %8.0f kg   (%.1f t, loss %.0f%% -> %.0f%%)",
                f.saved_kg, f.saved_kg / 1000, BASELINE_LOSS * 100, RESIDUAL_LOSS * 100)
    logger.info("Residual reject stream        %8.0f kg   (the valorisable fraction)", f.reject_kg)
    logger.info("  -> finished compost         %8.0f kg   at %.0f%% yield", f.compost_kg,
                COMPOST_YIELD_FRACTION * 100)
    logger.info("  -> or BSF larval biomass    %8.0f kg   at %.0f%% conversion", f.bsf_larvae_kg,
                BSF_LARVAE_YIELD_FRACTION * 100)
    logger.info("Food value retained           %8.0f GHS  ($%.0f)", f.food_value_retained_ghs,
                f.food_value_retained_ghs / GHS_PER_USD)

    logger.info("")
    logger.info("=" * 78)
    logger.info("EMISSIONS AVOIDED (sensitivity on the emissions factor)")
    logger.info("=" * 78)
    for label, ef in (("low", EF_LOW), ("central", EF_CENTRAL), ("high", EF_HIGH)):
        logger.info("EF %.1f kg CO2e/kg (%-7s) -> %5.2f t CO2e / pod / yr", ef, label,
                    f.co2e_avoided_t(ef))

    logger.info("")
    logger.info("=" * 78)
    logger.info("CONSISTENCY CHECK AGAINST THE DECK")
    logger.info("=" * 78)
    deck_co2e = 3.2
    deck_produce_t = 1.0
    implied_ef = deck_co2e / deck_produce_t
    logger.info("Deck slide 3 claims %.1f t CO2e avoided AND '%.0f+ tonne' of produce saved.",
                deck_co2e, deck_produce_t)
    logger.info("That pair implies an emissions factor of %.1f kg CO2e per kg of food waste,",
                implied_ef)
    logger.info("which is %.1fx the high end of the defensible range (%.1f). The two figures",
                implied_ef / EF_HIGH, EF_HIGH)
    logger.info("are not mutually consistent and a reviewer can show that in one line.")
    logger.info("")
    logger.info("Consistent replacement pair: %.1f t of produce kept in the chain and",
                f.saved_kg / 1000)
    logger.info("%.1f t CO2e avoided per pod per year (EF %.1f, range %.1f-%.1f t).",
                f.co2e_avoided_t(EF_CENTRAL), EF_CENTRAL,
                f.co2e_avoided_t(EF_LOW), f.co2e_avoided_t(EF_HIGH))

    logger.info("")
    logger.info("=" * 78)
    logger.info("WHAT THE VALORISATION CLAIM MAY AND MAY NOT SAY")
    logger.info("=" * 78)
    logger.info("May say: the pod is an aggregation point, so the %.0f kg/yr reject stream is",
                f.reject_kg)
    logger.info("  segregated at a single location instead of dispersed across a market floor,")
    logger.info("  which is the precondition for composting or BSF offtake at ~%.0f kg compost/yr.",
                f.compost_kg)
    logger.info("May NOT say: that composting or animal-feed offtake is operating. It is a")
    logger.info("  designed function of the pilot with no partner or cost line yet.")
    logger.info("At %.1f GHS/kg the compost stream is worth ~%.0f GHS/yr, which is a co-benefit",
                COMPOST_PRICE_GHS_PER_KG, f.compost_kg * COMPOST_PRICE_GHS_PER_KG)
    logger.info("  and not a revenue line. Do not put it in the business model.")


if __name__ == "__main__":
    main()
