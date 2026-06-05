#!/usr/bin/env python3
"""
make_sculptmap.py — generate Second Life sculpt maps with no dependencies.

A sculpt map is a small RGB image where each pixel encodes a vertex position:
    R = X, G = Y, B = Z   (each channel 0..255 maps to the -1..+1 bounding box)

Second Life reads the image across (U, wraps around) and up (V, pole to pole)
and drapes a prim's surface over those positions, scaled to the prim's size.

Usage:
    python3 make_sculptmap.py [shape] [size] [outfile]
        shape   : sphere | torus | cylinder | plane   (default: sphere)
        size    : pixels per side, power of 2          (default: 64)
        outfile : output PNG path             (default: <shape>_sculptmap.png)
"""

import math
import struct
import sys
import zlib


# ---------------------------------------------------------------------------
# Minimal 24-bit RGB PNG writer (stdlib only)
# ---------------------------------------------------------------------------
def write_png(path, width, height, pixels):
    """pixels: flat bytes-like of length width*height*3 (R,G,B per pixel)."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    # Add the mandatory per-scanline filter byte (0 = "None") to each row.
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, truecolor
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Shape helpers
# ---------------------------------------------------------------------------
def _smoothstep(a, b, x):
    if a == b:
        return 0.0 if x < a else 1.0
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def _interp(points, t):
    """Piecewise-linear lookup over a list of (position, value) control points."""
    if t <= points[0][0]:
        return points[0][1]
    for (pa, va), (pb, vb) in zip(points, points[1:]):
        if t <= pb:
            return va + (vb - va) * (t - pa) / (pb - pa)
    return points[-1][1]


def _poly_r(theta, n, phi, apothem):
    """Radius to a regular n-gon boundary at angle theta (apothem = face dist)."""
    a = ((theta - phi) % (2.0 * math.pi / n)) - math.pi / n
    return apothem / math.cos(a)


def _ang_dist(theta, center):
    """Smallest absolute angular distance (radians) between theta and center."""
    d = (theta - center) % (2.0 * math.pi)
    return min(d, 2.0 * math.pi - d)


def _capped(v, cap):
    """Flat top+bottom caps over v in [0,1]. Returns (z, k): k folds the rim
    radius to 0 at the cap centres and is 1 along the side wall."""
    if v < cap:
        return -1.0, v / cap
    if v > 1.0 - cap:
        return 1.0, (1.0 - v) / cap
    return (v - cap) / (1.0 - 2.0 * cap) * 2.0 - 1.0, 1.0


def _convex_r(theta, verts):
    """Radius from the origin (which must be inside) to a convex polygon's edge
    along direction theta. Lets a cross-section be any convex outline."""
    dx, dy = math.cos(theta), math.sin(theta)
    best = None
    n = len(verts)
    for i in range(n):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        det = ex * dy - ey * dx
        if abs(det) < 1e-12:
            continue
        t = (ex * ay - ey * ax) / det        # distance along the ray
        s = (dx * ay - dy * ax) / det        # position along the edge [0,1]
        if t > 1e-9 and -1e-6 <= s <= 1.0 + 1e-6 and (best is None or t < best):
            best = t
    return best if best is not None else 0.0


def _pt_in_poly(x, y, verts):
    """Even-odd point-in-polygon test (works for non-convex outlines)."""
    inside = False
    n = len(verts)
    j = n - 1
    for i in range(n):
        xi, yi = verts[i]
        xj, yj = verts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _button(u, v, emboss):
    """A round control-panel button: flat bottom, short wall, and a top face
    with `emboss(x, y) -> 0..1` raised as relief (1 = full icon height)."""
    theta = u * 2.0 * math.pi
    c, s = math.cos(theta), math.sin(theta)
    R, cap_bot, wall_top, base_top, emboss_h = 0.92, 0.12, 0.32, 0.45, 0.55
    if v < cap_bot:                                   # flat bottom disk
        rr = R * (v / cap_bot)
        return (rr * c, rr * s, -1.0)
    if v < wall_top:                                  # side wall
        z = -1.0 + (v - cap_bot) / (wall_top - cap_bot) * (base_top + 1.0)
        return (R * c, R * s, z)
    frac = (1.0 - v) / (1.0 - wall_top)               # top face: rim -> centre
    rr = R * frac
    x, y = rr * c, rr * s
    return (x, y, base_top + emboss(x, y) * emboss_h)


def _rot_arrow(x, y, cw):
    """Emboss mask for a circular rotation arrow (clockwise if cw, else mirrored
    to counter-clockwise): an arc band plus an arrowhead at the leading end."""
    if not cw:
        x = -x                                        # mirror -> CCW
    rad = math.hypot(x, y)
    ang = math.atan2(y, x)
    if ang < 0.0:
        ang += 2.0 * math.pi
    ri, ro = 0.30, 0.52
    rc = 0.5 * (ri + ro)
    a0, a1 = math.radians(40.0), math.radians(310.0)
    in_arc = (ri <= rad <= ro) and (a0 <= ang <= a1)
    ex, ey = rc * math.cos(a0), rc * math.sin(a0)     # leading end of the arc
    tx, ty = math.sin(a0), -math.cos(a0)              # clockwise tangent there
    rx, ry = math.cos(a0), math.sin(a0)               # radial direction
    head = _pt_in_poly(x, y, [(ex + 0.26 * tx, ey + 0.26 * ty),
                              (ex + 0.17 * rx, ey + 0.17 * ry),
                              (ex - 0.17 * rx, ey - 0.17 * ry)])
    return 1.0 if (in_arc or head) else 0.0


def _fire_icon(x, y):
    """Emboss mask for a fire/target reticle: ring + crosshair arms + centre dot."""
    rad = math.hypot(x, y)
    ring = 0.45 <= rad <= 0.62
    arm = 0.07
    cross = (abs(x) <= arm or abs(y) <= arm) and rad <= 0.74
    dot = rad <= 0.12
    return 1.0 if (ring or cross or dot) else 0.0


# ---------------------------------------------------------------------------
# Shape surface functions: (u, v) in [0,1] -> (x, y, z) in roughly [-1, 1]
# ---------------------------------------------------------------------------
def sphere(u, v):
    theta = u * 2.0 * math.pi          # longitude, wraps around
    phi = v * math.pi                  # latitude, north pole -> south pole
    return (math.sin(phi) * math.cos(theta),
            math.sin(phi) * math.sin(theta),
            math.cos(phi))


def torus(u, v, ring=0.30):
    a = u * 2.0 * math.pi
    b = v * 2.0 * math.pi
    r = 1.0 - ring
    return ((r + ring * math.cos(b)) * math.cos(a),
            (r + ring * math.cos(b)) * math.sin(a),
            ring * math.sin(b))


def cylinder(u, v):
    a = u * 2.0 * math.pi
    return (math.cos(a), math.sin(a), 2.0 * v - 1.0)


def plane(u, v):
    return (2.0 * u - 1.0, 2.0 * v - 1.0, 0.0)


def foct(u, v):
    """Fully-Mirrored Octagon game piece: a capped octagonal prism.

    Cross-section is a regular octagon oriented so a flat face points at each of
    the 8 compass directions (N, NE, E, ...) — matching the game's 8 facings.
    The top and bottom rows fold inward to form flat octagonal caps, so the
    surface is closed under *Cylinder* stitching. Fills the [-1,1] box; scale
    Z in-world to taste.
    """
    cap = 0.20                                 # fraction of height for each cap
    n = 8
    phi = math.pi / n                          # 22.5 deg: face toward each dir
    apothem = 1.0                              # face distance -> touches +/-1

    theta = u * 2.0 * math.pi
    alpha = ((theta - phi) % (2.0 * math.pi / n)) - math.pi / n
    r_oct = apothem / math.cos(alpha)          # exact octagon boundary radius

    if v < cap:                                # bottom cap: flat disk at z=-1
        z, r = -1.0, r_oct * (v / cap)
    elif v > 1.0 - cap:                        # top cap: flat disk at z=+1
        z, r = 1.0, r_oct * ((1.0 - v) / cap)
    else:                                      # side wall: full radius
        z = (v - cap) / (1.0 - 2.0 * cap) * 2.0 - 1.0
        r = r_oct
    return (r * math.cos(theta), r * math.sin(theta), z)


def king(u, v):
    """King game piece: a surface-of-revolution body with a flat foot, an
    8-lobed crown (radius modulated by angle), and an orb finial.

    Demonstrates modulating radius by *both* height (the silhouette) and angle
    (the crown lobes). Flat foot + folded tip -> *Cylinder* stitching.
    """
    # Silhouette: (height v, radius). Linearly interpolated.
    prof = [(0.00, 0.00), (0.04, 0.85), (0.11, 0.80), (0.17, 0.34),
            (0.32, 0.40), (0.55, 0.50), (0.63, 0.48), (0.66, 0.74),
            (0.70, 0.58), (0.72, 0.56), (0.88, 0.56), (0.90, 0.30),
            (0.935, 0.36), (0.965, 0.16), (1.00, 0.00)]

    def silhouette(t):
        if t <= prof[0][0]:
            return prof[0][1]
        for (va, ra), (vb, rb) in zip(prof, prof[1:]):
            if t <= vb:
                return ra + (rb - ra) * (t - va) / (vb - va)
        return prof[-1][1]

    def smoothstep(a, b, x):
        t = max(0.0, min(1.0, (x - a) / (b - a)))
        return t * t * (3.0 - 2.0 * t)

    theta = u * 2.0 * math.pi
    scale = 1.15                               # fill more of the [-1,1] box

    # 8 crown lobes, faded in only across the crown band.
    band = smoothstep(0.68, 0.72, v) * (1.0 - smoothstep(0.88, 0.91, v))
    lobes = (math.cos(8.0 * theta) * 0.5 + 0.5) ** 0.7
    crown = 1.0 + 0.42 * band * lobes

    cap = 0.04                                 # flat underside of the foot
    if v < cap:
        z, r = -1.0, silhouette(cap) * (v / cap)
    else:
        z = -1.0 + 2.0 * (v - cap) / (1.0 - cap)
        r = silhouette(v)
    r *= scale * crown
    return (r * math.cos(theta), r * math.sin(theta), z)


def laser(u, v):
    """Laser: flat-capped round body with a barrel pointing at the facing dir."""
    theta = u * 2.0 * math.pi
    z, k = _capped(v, 0.10)
    r = 0.5 * k
    snout = math.exp(-(_ang_dist(theta, math.pi / 2.0) / 0.30) ** 2)
    band = _smoothstep(0.40, 0.46, v) * (1.0 - _smoothstep(0.60, 0.66, v))
    r += 0.45 * snout * band
    return (r * math.cos(theta), r * math.sin(theta), z)


def stunner(u, v):
    """Stunner: an octagonal piece whose 8 faces follow gun / vulnerable /
    shield = g-v-s-v-s-v-s-v clockwise from the front. The gun face (front) has
    a protruding barrel, the 3 shield faces are raised mirror plates, and the 4
    vulnerable faces are recessed so the mirrored faces read clearly."""
    theta = u * 2.0 * math.pi
    z, k = _capped(v, 0.16)
    r = _poly_r(theta, 8, math.pi / 8.0, 0.60)        # octagon, a face per dir
    wall = _smoothstep(0.16, 0.24, v) * (1.0 - _smoothstep(0.76, 0.84, v))
    # gun barrel on the front face (north, +Y)
    gun = math.exp(-(_ang_dist(theta, math.pi / 2.0) / 0.22) ** 2)
    gband = _smoothstep(0.40, 0.46, v) * (1.0 - _smoothstep(0.62, 0.68, v))
    r += 0.38 * gun * gband
    # raised shield plates on the other 3 cardinals (E, S, W)
    shield = 0.0
    for d in (0.0, math.pi, 1.5 * math.pi):
        shield = max(shield, math.exp(-(_ang_dist(theta, d) / 0.20) ** 2))
    r += 0.16 * shield * wall
    # recessed vulnerable faces on the 4 diagonals (NE, SE, SW, NW)
    vuln = 0.0
    for d in (0.25, 0.75, 1.25, 1.75):
        vuln = max(vuln, math.exp(-(_ang_dist(theta, d * math.pi) / 0.18) ** 2))
    r -= 0.10 * vuln * wall
    r *= k
    return (r * math.cos(theta), r * math.sin(theta), z)


def oneway(u, v):
    """One-Way Mirror: an arrow token. The arrow points in the direction a beam
    PASSES THROUGH (beams travelling with the arrow pass; against it reflect)."""
    theta = u * 2.0 * math.pi
    z, k = _capped(v, 0.10)
    arrow = [(0.0, 1.0), (-0.85, 0.10), (-0.30, -1.0),   # tip, L shoulder, L tail
             (0.30, -1.0), (0.85, 0.10)]                 # R tail, R shoulder
    r = _convex_r(theta, arrow) * k
    return (r * math.cos(theta), r * math.sin(theta), z)


def trimir(u, v):
    """Triangular Mirror: a right triangle. The reflective face is the hypotenuse
    (the long front face toward +Y); the right-angle vertex is at the back."""
    theta = u * 2.0 * math.pi
    z, k = _capped(v, 0.08)
    tri = [(-1.0, 0.55), (1.0, 0.55), (0.0, -0.45)]      # hypotenuse on top (+Y)
    r = _convex_r(theta, tri) * k
    return (r * math.cos(theta), r * math.sin(theta), z)


def bomb(u, v):
    """Bomb: a round body with 4 blast spikes on the orthogonal faces — the
    explosion sides in the default (orthogonal) config — and smooth diagonals,
    the vulnerable sides. Rotate the prim 45 deg in-world for the diagonal config
    so the spikes point at the diagonals instead."""
    prof = [(0.00, 0.00), (0.05, 0.42), (0.14, 0.54), (0.32, 0.58),
            (0.50, 0.58), (0.68, 0.55), (0.80, 0.42), (0.86, 0.14),
            (0.90, 0.13), (0.97, 0.12), (1.00, 0.00)]
    theta = u * 2.0 * math.pi
    cap = 0.05
    if v < cap:
        z, r = -1.0, _interp(prof, cap) * (v / cap)
    else:
        z, r = 2.0 * v - 1.0, _interp(prof, v)
    spike = 0.0
    for d in (0.0, 0.5, 1.0, 1.5):                       # N, E, S, W
        spike = max(spike, math.exp(-(_ang_dist(theta, d * math.pi) / 0.16) ** 2))
    sband = math.exp(-((v - 0.5) / 0.20) ** 2)           # concentrated at equator
    r += 0.40 * spike * sband
    return (r * math.cos(theta), r * math.sin(theta), z)


def hypergon(u, v):
    """Hypergon: a faceted octagonal bipyramid (gem) — points top and bottom."""
    theta = u * 2.0 * math.pi
    env = 1.0 - abs(2.0 * v - 1.0)       # 0 at poles, 1 at the equator
    r = _poly_r(theta, 8, math.pi / 8.0, 1.0) * env
    return (r * math.cos(theta), r * math.sin(theta), 2.0 * v - 1.0)


def splitter(u, v):
    """Beam Splitter: a square (diamond) prism, a vertical splitting edge to front."""
    theta = u * 2.0 * math.pi
    z, k = _capped(v, 0.08)
    r = _poly_r(theta, 4, math.pi / 2.0, 0.707) * k
    return (r * math.cos(theta), r * math.sin(theta), z)


def poct(u, v):
    """Partially-Mirrored Octagon: octagonal prism with a thicker 135 deg back shield."""
    theta = u * 2.0 * math.pi
    z, k = _capped(v, 0.18)
    r = _poly_r(theta, 8, math.pi / 8.0, 0.85)
    arc = _smoothstep(1.40, 1.18, _ang_dist(theta, 1.5 * math.pi))  # 3 back faces
    r *= (1.0 + 0.14 * arc) * k
    return (r * math.cos(theta), r * math.sin(theta), z)


def rotcw(u, v):
    """Control panel: clockwise rotation button (embossed circular arrow)."""
    return _button(u, v, lambda x, y: _rot_arrow(x, y, True))


def rotccw(u, v):
    """Control panel: counter-clockwise rotation button (embossed arrow)."""
    return _button(u, v, lambda x, y: _rot_arrow(x, y, False))


def fire(u, v):
    """Control panel: fire button (embossed target reticle)."""
    return _button(u, v, _fire_icon)


def vase(u, v):
    """A surface of revolution: spin a profile curve around the Z axis.

    The profile tapers to ~0 at v=0 and v=1, so it closes to a point at the
    base and rim under *Sphere* stitching. Edit profile() to design your own.
    """
    def profile(t):                                    # radius at height t
        body = math.sin(math.pi * t) ** 0.55           # rounded body
        neck = 1.0 - 0.55 * math.exp(-((t - 0.72) / 0.07) ** 2)  # pinch a neck
        lip = 1.0 + 0.35 * math.exp(-((t - 0.95) / 0.04) ** 2)   # flared rim
        foot = 1.0 + 0.20 * math.exp(-((t - 0.05) / 0.03) ** 2)  # base flare
        return body * neck * lip * foot

    theta = u * 2.0 * math.pi
    r = profile(v)
    return (r * math.cos(theta), r * math.sin(theta), 2.0 * v - 1.0)


# Each shape records its SL Stitching Type and how the U seam closes:
#   "dup"  - first and last columns coincide (seam meridian doubled)
#   "wrap" - columns span the full loop (col W-1 connects back to col 0);
#            needed when vertices must land exactly on features (e.g. octagon
#            corners) for crisp edges.
SHAPES = {
    "sphere":   {"fn": sphere,   "stitch": "Sphere",   "seam": "dup"},
    "torus":    {"fn": torus,    "stitch": "Torus",    "seam": "dup"},
    "cylinder": {"fn": cylinder, "stitch": "Cylinder", "seam": "dup"},
    "plane":    {"fn": plane,    "stitch": "Plane",    "seam": "dup"},
    "vase":     {"fn": vase,     "stitch": "Sphere",   "seam": "dup"},
    "foct":     {"fn": foct,     "stitch": "Cylinder", "seam": "wrap"},
    "king":     {"fn": king,     "stitch": "Cylinder", "seam": "wrap"},
    "laser":    {"fn": laser,    "stitch": "Cylinder", "seam": "wrap"},
    "stunner":  {"fn": stunner,  "stitch": "Cylinder", "seam": "wrap"},
    "oneway":   {"fn": oneway,   "stitch": "Cylinder", "seam": "wrap"},
    "trimir":   {"fn": trimir,   "stitch": "Cylinder", "seam": "wrap"},
    "bomb":     {"fn": bomb,     "stitch": "Cylinder", "seam": "wrap"},
    "hypergon": {"fn": hypergon, "stitch": "Sphere",   "seam": "wrap"},
    "splitter": {"fn": splitter, "stitch": "Cylinder", "seam": "wrap"},
    "poct":     {"fn": poct,     "stitch": "Cylinder", "seam": "wrap"},
    "rotcw":    {"fn": rotcw,    "stitch": "Cylinder", "seam": "wrap"},
    "rotccw":   {"fn": rotccw,   "stitch": "Cylinder", "seam": "wrap"},
    "fire":     {"fn": fire,     "stitch": "Cylinder", "seam": "wrap"},
}


def to_byte(value):
    """Map a coordinate in [-1, 1] to a 0..255 channel value."""
    return max(0, min(255, int(round((value + 1.0) * 0.5 * 255.0))))


def generate(shape_fn, size, seam="dup"):
    pixels = bytearray(size * size * 3)
    i = 0
    for row in range(size):
        # Row 0 is the top of the image; V should run bottom(0) -> top(1).
        v = 1.0 - (row / (size - 1))
        for col in range(size):
            # "wrap" lets the last column loop back to the first (col/size);
            # "dup" makes the first/last columns coincide (col/(size-1)).
            u = col / size if seam == "wrap" else col / (size - 1)
            x, y, z = shape_fn(u, v)
            pixels[i] = to_byte(x)
            pixels[i + 1] = to_byte(y)
            pixels[i + 2] = to_byte(z)
            i += 3
    return pixels


def main(argv):
    shape = argv[1] if len(argv) > 1 else "sphere"
    size = int(argv[2]) if len(argv) > 2 else 64
    outfile = argv[3] if len(argv) > 3 else f"{shape}_sculptmap.png"

    if shape not in SHAPES:
        sys.exit(f"Unknown shape '{shape}'. Choose from: {', '.join(SHAPES)}")
    if size & (size - 1) != 0:
        print(f"Warning: size {size} is not a power of two; SL prefers 8/16/32/64/128.")

    spec = SHAPES[shape]
    pixels = generate(spec["fn"], size, spec["seam"])
    write_png(outfile, size, size, pixels)
    print(f"Wrote {outfile}  ({shape}, {size}x{size})")
    print(f"In SL: set Stitching Type = {spec['stitch']}")


if __name__ == "__main__":
    main(sys.argv)
