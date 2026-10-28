"""Charge density and the Shannon k value for an electrode.

Shannon (1992) fitted a line through tissue-damage data:

    k = log10(Q) + log10(Q / A)

with Q the charge per phase in µC and A the electrode's geometric area in cm².
Pulses with k above about 1.75 to 2 were linked to damage in those data. The data
came from macroelectrodes; for small microelectrodes the line is not a reliable
guide, so treat k as one input to your own judgement, not as a safety limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

DEFAULT_K_LIMIT = 1.75
UM2_TO_CM2 = 1e-8


@dataclass(frozen=True)
class Electrode:
    """An electrode's geometric area, given as area_um2 or as a disc diameter_um (one of them)."""

    area_um2: Optional[float] = None
    diameter_um: Optional[float] = None
    k_limit: float = DEFAULT_K_LIMIT

    def __post_init__(self) -> None:
        if (self.area_um2 is None) == (self.diameter_um is None):
            raise ValueError("give the electrode's area_um2 or its diameter_um (one of them)")
        for name in ("area_um2", "diameter_um"):
            v = getattr(self, name)
            if v is not None and not (_number(v) and v > 0):
                raise ValueError(f"{name} must be a positive number")
        if not _number(self.k_limit):
            raise ValueError("k_limit must be a number")

    @property
    def area(self) -> float:
        """Geometric area in µm²."""
        if self.area_um2 is not None:
            return float(self.area_um2)
        return math.pi * (self.diameter_um / 2) ** 2


@dataclass
class Density:
    """Charge density of a pulse's larger phase on an electrode."""

    area_um2: float
    charge_nc: float  # the larger phase
    density_uc_cm2: float
    shannon_k: float
    k_limit: float
    max_charge_nc: float  # largest charge per phase that stays at k_limit

    @property
    def within_limit(self) -> bool:
        return self.shannon_k <= self.k_limit + 1e-9


def charge_density(first_charge_nc: float, second_charge_nc: float, electrode: Electrode) -> Density:
    """Density and k for the larger of the two phases (pass 0 for a monophasic pulse's second phase)."""
    q_nc = max(abs(first_charge_nc), abs(second_charge_nc))
    area_cm2 = electrode.area * UM2_TO_CM2
    q_uc = q_nc * 1e-3
    density = q_uc / area_cm2
    k = math.log10(q_uc) + math.log10(density) if q_uc > 0 else -math.inf
    max_q_nc = math.sqrt(10 ** electrode.k_limit * area_cm2) * 1e3
    return Density(electrode.area, q_nc, density, k, float(electrode.k_limit), max_q_nc)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
