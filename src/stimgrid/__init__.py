"""stimgrid: fit stimulation pulses to what a stimulator can actually produce.

Rounds a planned pulse to a device's amplitude and timing steps, reports what is
really delivered, and finds the nearest exactly charge-balanced pulse.
For research use; check results on your own hardware.
"""

from .check import CheckError, CheckReport, check_file, check_text
from .devices import PRESETS, Device, cortical_labs_cl, intan_rhs2116, mcs_stg4000, mcs_stg5, preset, tdt_iz2
from .electrode import Density, Electrode, charge_density
from .formats import ExportError, export
from .fit import Delivered, FitReport, Issue, Pulse, RateFit, Rates, fit
from .loader import InputError, from_dict, load
from .protocol import Protocol, ProtocolReport, Step, fit_protocol, load_protocol, protocol_from_dict

__version__ = "0.3.0"
__all__ = [
    "PRESETS", "CheckError", "CheckReport", "Delivered", "Density", "Device", "Electrode", "ExportError",
    "FitReport", "InputError", "Issue", "Pulse", "RateFit", "Rates", "charge_density", "check_file", "check_text",
    "cortical_labs_cl", "export", "fit", "Protocol", "ProtocolReport", "Step", "fit_protocol", "load_protocol",
    "protocol_from_dict",
    "from_dict", "intan_rhs2116", "load", "mcs_stg4000", "mcs_stg5", "preset", "tdt_iz2", "__version__",
]
