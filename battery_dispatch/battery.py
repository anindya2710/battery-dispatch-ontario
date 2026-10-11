"""The battery, described by a handful of numbers.

Energy is in MWh, power in MW, time steps are one hour, so a power of 1 MW held
for one step moves 1 MWh.
"""
from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True)
class Battery:
    power_mw: float = 1.0            # maximum charge and discharge power at the grid connection
    energy_mwh: float = 4.0          # nameplate energy capacity (4-hour battery)
    round_trip_eff: float = 0.88     # energy out / energy in over a full cycle
    soc_min_frac: float = 0.10       # never go below 10% of nameplate
    soc_max_frac: float = 0.90       # never go above 90% of nameplate
    soc_start_frac: float = 0.10     # each day starts and ends here
    degradation_cost: float = 0.0    # $ per MWh of throughput (charged + discharged at the grid)

    @property
    def eta_c(self) -> float:
        """Charging efficiency. The round-trip loss is split evenly: sqrt(0.88) ~ 0.938."""
        return sqrt(self.round_trip_eff)

    @property
    def eta_d(self) -> float:
        """Discharging efficiency."""
        return sqrt(self.round_trip_eff)

    @property
    def s_min(self) -> float:
        return self.soc_min_frac * self.energy_mwh

    @property
    def s_max(self) -> float:
        return self.soc_max_frac * self.energy_mwh

    @property
    def s_start(self) -> float:
        return self.soc_start_frac * self.energy_mwh

    @property
    def usable_mwh(self) -> float:
        """Stored energy between the two state-of-charge limits."""
        return self.s_max - self.s_min
