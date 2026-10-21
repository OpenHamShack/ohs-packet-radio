"""sgp4 2.27's pure-Python propagator (Brandon Rhodes, MIT licence: LICENSE
beside this file), vendored for Packet Radio's satellite passes.

Copied from the sdist (https://pypi.org/project/sgp4/2.27/) unchanged but
for one thing: `from sgp4.x import` became `from .x import`, so it imports
as part of this package and never meets an installed sgp4. Only the files
the Satrec path needs are here: alpha5, earth_gravity, ext, functions, io,
model, propagation. Not the C++ wrapper (no compiler in three build hosts'
worth of installers for a few hundred propagations a minute), not the
tests or OMM/exporter.

Vendored rather than a dependency: the installers carry exactly the
packages tools/ohslibs.py and the spec list, and a new wheel would mean a
new entry in three platforms' builds for ~2,500 lines of pure Python.
"""
from .model import Satrec, WGS72, WGS84  # noqa: F401

__version__ = "2.27"
