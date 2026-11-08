#!/usr/bin/env python3
"""Generate app/web/static/us-basemap.json from us-atlas counties-10m.json.

Usage: build_us_basemap.py path/to/us-atlas/counties-10m.json

Source: npm us-atlas@3.0.1 (ISC licence, derived from US Census Bureau
cartographic boundary files). Output is two SVG path strings, state lines and
county lines, for the contiguous US in the Operator Center map's coordinates.
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app/web/static/us-basemap.json"


def project(lon, lat):
    return (lon + 125) / 59 * 720, (50 - lat) / 26 * 360


def arc_ids(node):
    """Yield every arc index in a nested TopoJSON arcs value."""
    if isinstance(node, int):
        yield node if node >= 0 else ~node
    else:
        for child in node:
            yield from arc_ids(child)


def decode(topology):
    sx, sy = topology["transform"]["scale"]
    tx, ty = topology["transform"]["translate"]
    arcs = []
    for arc in topology["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    return arcs


def in_conus(points):
    return all(-125 <= lon <= -66 and 24 <= lat <= 50 for lon, lat in points)


def to_path(arcs):
    parts = []
    for points in arcs:
        coords = []
        for lon, lat in points:
            xy = "%.2f %.2f" % project(lon, lat)
            if not coords or coords[-1] != xy:
                coords.append(xy)
        if len(coords) > 1:
            parts.append("M" + "L".join(coords))
    return "".join(parts)


def main():
    topology = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    arcs = decode(topology)
    objects = topology["objects"]
    state = {i for g in objects["states"]["geometries"] for i in arc_ids(g["arcs"])}
    county = {i for g in objects["counties"]["geometries"] for i in arc_ids(g["arcs"])} - state
    data = {
        "states": to_path(arcs[i] for i in sorted(state) if in_conus(arcs[i])),
        "counties": to_path(arcs[i] for i in sorted(county) if in_conus(arcs[i])),
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
