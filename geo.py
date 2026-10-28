"""Small geometry helpers for build.py: NY State Plane feet -> lat/lon, and point-in-polygon. Standard library only.

NYC datasets give sign locations in NAD83 / New York Long Island (EPSG:2263, US survey feet), a Lambert
conformal conic projection. DSNY's collection areas are lat/lon polygons. The inverse projection below follows
the standard two-standard-parallel LCC formulas (Snyder, Map Projections: A Working Manual, eq. 15-1 to 15-11);
NAD83 vs WGS84 differs by about a meter here, which doesn't matter for picking a collection area.
"""
import math

# EPSG:2263 parameters
_A = 6378137.0                      # GRS80 semi-major axis (m)
_F = 1 / 298.257222101
_E = math.sqrt(_F * (2 - _F))
_LAT1, _LAT2 = math.radians(40 + 40 / 60), math.radians(41 + 2 / 60)
_LAT0, _LON0 = math.radians(40 + 10 / 60), math.radians(-74)
_X0 = 300000.0                      # false easting (m)
_FT = 1200 / 3937                   # US survey foot in meters


def _m(lat):
    return math.cos(lat) / math.sqrt(1 - (_E * math.sin(lat)) ** 2)


def _t(lat):
    s = _E * math.sin(lat)
    return math.tan(math.pi / 4 - lat / 2) / ((1 - s) / (1 + s)) ** (_E / 2)


_N = (math.log(_m(_LAT1)) - math.log(_m(_LAT2))) / (math.log(_t(_LAT1)) - math.log(_t(_LAT2)))
_FF = _m(_LAT1) / (_N * _t(_LAT1) ** _N)
_RHO0 = _A * _FF * _t(_LAT0) ** _N


def stateplane_to_lonlat(x_ft, y_ft):
    """(lon, lat) in degrees for an EPSG:2263 point given in US survey feet."""
    x, y = x_ft * _FT - _X0, y_ft * _FT
    rho = math.copysign(math.hypot(x, _RHO0 - y), _N)
    theta = math.atan2(x, _RHO0 - y)
    t = (rho / (_A * _FF)) ** (1 / _N)
    lat = math.pi / 2 - 2 * math.atan(t)
    for _ in range(10):  # iterate the conformal latitude to geodetic latitude
        s = _E * math.sin(lat)
        lat = math.pi / 2 - 2 * math.atan(t * ((1 - s) / (1 + s)) ** (_E / 2))
    return math.degrees(theta / _N + _LON0), math.degrees(lat)


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_multipolygon(lon, lat, coords):
    """True if the point is inside a GeoJSON MultiPolygon's coordinates (outer rings minus holes)."""
    for polygon in coords:
        if _in_ring(lon, lat, polygon[0]) and not any(_in_ring(lon, lat, hole) for hole in polygon[1:]):
            return True
    return False


class AreaIndex:
    """Find which feature's MultiPolygon contains a point, using bounding boxes to skip most of them."""

    def __init__(self, features):
        self.items = []
        for f in features:
            coords = f["geometry"]["coordinates"]
            xs = [p[0] for poly in coords for ring in poly for p in ring]
            ys = [p[1] for poly in coords for ring in poly for p in ring]
            self.items.append(((min(xs), min(ys), max(xs), max(ys)), coords, f["properties"]))

    def find(self, lon, lat):
        for (x0, y0, x1, y1), coords, props in self.items:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and in_multipolygon(lon, lat, coords):
                return props
        return None
