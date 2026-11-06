"""Single-radar triangle branch-and-bound over nonnegative, discrete demand.

The optimization score is sum(max(dBZ, 0)) for native HRRR cell centers
within range. It is a reflectivity-weighted coverage index, not radar power.
For fixed range this has the notebook's ranking without its arbitrary area
normalization. Distances use a local azimuthal equidistant CRS in meters.
"""
from __future__ import annotations
import heapq
import itertools
import time
import numpy as np
from scipy.spatial import cKDTree
from shapely import constrained_delaunay_triangles
from shapely.geometry import Point, shape, mapping
from shapely.ops import transform, unary_union
from pyproj import CRS, Transformer


def deployment_geometry(collection):
    features = (collection or {}).get('features', [])
    if not features:
        raise ValueError('Draw a deployment polygon using the map toolbar first.')
    if len(features) > 8:
        raise ValueError('Use at most eight deployment polygons.')
    polys = []
    for feature in features:
        geom = shape(feature.get('geometry') or {})
        if geom.geom_type not in ('Polygon', 'MultiPolygon') or geom.is_empty or not geom.is_valid:
            raise ValueError('Deployment areas must be valid polygons without crossing edges.')
        if sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
               for p in (geom.geoms if geom.geom_type == 'MultiPolygon' else [geom])) > 1000:
            raise ValueError('Simplify the polygon to fewer than 1,000 vertices.')
        polys.append(geom)
    geom = unary_union(polys)
    w, s, e, n = geom.bounds
    if not (-125 <= w < e <= -66 and 24 <= s < n <= 50):
        raise ValueError('Draw the deployment area within the contiguous United States.')
    if e-w > 10 or n-s > 8:
        raise ValueError('Use a regional deployment area, no wider than 10° × 8°.')
    return geom


def solve_triangles(polygon, xy, weights, radius_m, tolerance=.01, max_seconds=20, max_nodes=100000):
    """Valid upper bounds: an R disk centered anywhere in a triangle is
    contained in the (R + d) disk about its centroid, where d is the maximum
    vertex distance. Nonnegative weights make the expanded-disk sum a bound.
    No nodes are discarded on tolerance alone, so reported bounds remain valid.
    """
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError('The deployment polygon has no usable area.')
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    weights = np.asarray(weights, dtype=float)
    if len(weights) != len(xy) or not np.isfinite(xy).all() or not np.isfinite(weights).all() or (weights < 0).any():
        raise ValueError('Demand requires finite coordinates and nonnegative weights.')
    if radius_m <= 0 or not 0 <= tolerance <= .2 or max_seconds <= 0:
        raise ValueError('Invalid optimizer settings.')
    start = time.monotonic()
    tree = cKDTree(xy)
    calls = 0
    def score(center, radius):
        nonlocal calls
        calls += 1
        return float(weights[tree.query_ball_point(center, radius + 1e-7)].sum())
    best_xy = np.asarray(polygon.representative_point().coords[0])
    best = score(best_xy, radius_m)
    heap = []
    counter = itertools.count()
    def add(vertices):
        nonlocal best, best_xy
        center = vertices.mean(axis=0)
        value = score(center, radius_m)
        if value > best:
            best, best_xy = value, center
        d = np.linalg.norm(vertices-center, axis=1).max()
        upper = score(center, radius_m+d)
        if upper > best:
            heapq.heappush(heap, (-upper, next(counter), vertices))
    # Constrained triangulation preserves concavities, holes and disjoint areas.
    for triangle in constrained_delaunay_triangles(polygon).geoms:
        vertices = np.asarray(triangle.exterior.coords[:3])
        for vertex in vertices:
            value = score(vertex, radius_m)
            if value > best:
                best, best_xy = value, vertex
        add(vertices)
    iterations = 0
    termination = 'tolerance'
    while heap:
        upper = max(best, -heap[0][0])
        if upper <= best * (1+tolerance) + 1e-9:
            break
        if time.monotonic()-start >= max_seconds:
            termination = 'time_limit'
            break
        if iterations >= max_nodes:
            termination = 'iteration_limit'
            break
        _, _, v = heapq.heappop(heap)
        a, b, c = (v + np.roll(v, -1, axis=0)) / 2
        for child in (np.array([v[0], a, c]), np.array([v[1], b, a]),
                      np.array([v[2], c, b]), np.array([a, b, c])):
            add(child)
        iterations += 1
    upper = max(best, -heap[0][0] if heap else best)
    if not polygon.buffer(1e-5).covers(Point(best_xy)):
        raise RuntimeError('Solver produced a location outside the deployment area.')
    return {'xy': best_xy.tolist(), 'score': best, 'upper_bound': upper,
            'relative_gap': (upper-best)/best if best > 0 else (0 if upper == 0 else None),
            'termination': termination, 'iterations': iterations,
            'evaluations': calls, 'seconds': time.monotonic()-start}


def optimize(forecast, geojson, radius_km, threshold_dbz, tolerance):
    polygon = deployment_geometry(geojson)
    radius_km, threshold_dbz, tolerance = map(float, (radius_km, threshold_dbz, tolerance))
    if not (5 <= radius_km <= 150 and 0 <= threshold_dbz <= 65 and .001 <= tolerance <= .1):
        raise ValueError('Range must be 5–150 km, threshold 0–65 dBZ and tolerance 0.1–10%.')
    from shapely.geometry import box
    w, s, e, n = forecast['bounds']
    if not box(w, s, e, n).covers(polygon):
        raise ValueError('The polygon extends outside the loaded forecast. Zoom out and reload HRRR.')
    center = polygon.centroid
    crs = CRS.from_proj4(f'+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m')
    forward = Transformer.from_crs(4326, crs, always_xy=True)
    backward = Transformer.from_crs(crs, 4326, always_xy=True)
    projected = transform(forward.transform, polygon)
    values = forecast['values']
    keep = np.isfinite(values) & (values > 0) & (values >= threshold_dbz)
    if not keep.any():
        raise ValueError('No positive reflectivity meets this threshold. Try a lower threshold or another forecast region.')
    lon, lat = forecast['lon'][keep], forecast['lat'][keep]
    x, y = forward.transform(lon, lat)
    xy, weights = np.column_stack((x, y)), values[keep].astype(float)
    total = float(weights.sum())
    # Bounding box prune removes only cells unreachable by any allowed location.
    west, south, east, north = projected.bounds
    radius = radius_km * 1000
    reachable = (x >= west-radius) & (x <= east+radius) & (y >= south-radius) & (y <= north+radius)
    if not reachable.any():
        raise ValueError('No demand is within radar range of this polygon. Move the polygon or increase range.')
    result = solve_triangles(projected, xy[reachable], weights[reachable], radius, tolerance)
    if result['score'] == 0:
        raise ValueError('No demand was covered. Change the polygon, range or threshold.')
    longitude, latitude = backward.transform(*result.pop('xy'))
    result.update(longitude=longitude, latitude=latitude, radius_km=radius_km,
                  threshold_dbz=threshold_dbz, tolerance=tolerance,
                  covered_demand_percent=100*result['score']/total,
                  demand_cells=int(keep.sum()), forecast=forecast['metadata'],
                  objective='Sum of positive HRRR composite reflectivity (dBZ) at covered cell centers',
                  distance_model='Local WGS84 azimuthal equidistant projection; planar distance',
                  deployment_polygon=mapping(polygon))
    # Render the exact planar circle used by the model, transformed for the map.
    cx, cy = forward.transform(longitude, latitude)
    result['coverage'] = mapping(transform(backward.transform, Point(cx, cy).buffer(radius, quad_segs=90)))
    result['edge_warning'] = not box(w, s, e, n).covers(shape(result['coverage']))
    return result
