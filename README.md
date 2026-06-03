# sculptie-test

A tiny, **dependency-free** generator for Second Life **sculpt maps** (sculpties).

A sculpt map is a small RGB image where each pixel encodes a vertex position —
`R = X, G = Y, B = Z`, each channel mapping the `-1..+1` bounding box to `0..255`.
SL reads the image across (U, wraps around) and up (V) and drapes a prim's
surface over those positions, scaled to the prim's size. `make_sculptmap.py`
writes the PNG using only the Python standard library (`zlib`, `struct`) — no
Pillow, no numpy, no pip.

## Usage

```
python3 make_sculptmap.py [shape] [size] [outfile]
```

- `shape` — one of the shapes below (default: `sphere`)
- `size`  — pixels per side, a power of two (default: `64`)
- `outfile` — output PNG path (default: `<shape>_sculptmap.png`)

It prints the **Stitching Type** to select in-world for that shape.

```
python3 make_sculptmap.py king        # -> king_sculptmap.png  (Stitching: Cylinder)
python3 make_sculptmap.py sphere 64    # -> sphere_sculptmap.png (Stitching: Sphere)
```

## Shapes

| Shape | Stitching | Description |
|-------|-----------|-------------|
| `sphere`   | Sphere   | Basic sphere — the "hello world" sculptie. |
| `torus`    | Torus    | Ring/donut. |
| `cylinder` | Cylinder | Open-ended tube. |
| `plane`    | Plane    | Flat sheet. |
| `vase`     | Sphere   | Surface of revolution with a pinched neck and flared lip. |

### Laser-chess pieces

Game pieces for the laser-chess project; a copy of each map lives in
`../laser-chess/sculpties/`.

| Shape | Stitching | Piece |
|-------|-----------|-------|
| `king`     | Cylinder | King — flat foot, 8-lobed crown, orb finial. |
| `laser`    | Cylinder | Laser — round body with a directional barrel. |
| `stunner`  | Cylinder | Stunner — domed bell, front nub, rear shield ridge. |
| `oneway`   | Cylinder | One-Way Mirror — thin upright panel facing forward. |
| `trimir`   | Cylinder | Triangular Mirror — triangular prism, flat face forward. |
| `bomb`     | Cylinder | Bomb — round body, flat base, fuse stem. |
| `hypergon` | Sphere   | Hypergon — faceted octagonal bipyramid (gem). |
| `splitter` | Cylinder | Beam Splitter — square "diamond" prism, edge forward. |
| `poct`     | Cylinder | Partially-Mirrored Octagon — thicker 135° rear shield. |
| `foct`     | Cylinder | Fully-Mirrored Octagon — flat face toward each of the 8 facings. |

## Using a sculpt map in Second Life

1. **Upload** the PNG (`Ctrl+U`) — check **"Use lossless compression"**. SL
   re-compresses textures by default, and lossy artifacts warp a sculptie's
   surface. (~L$10.)
2. Rez a prim → **Build** → **Object** tab → Building Block Type = **Sculpted**.
3. Drag the uploaded map into the **Sculpt Texture** slot.
4. Set **Stitching Type** to the value the tool printed (must match the shape).
5. Resize the prim — the sculptie scales with it.

## Adding a shape

Each shape is one function mapping `(u, v)` in `[0,1]` to `(x, y, z)` in roughly
`[-1, 1]`, registered in the `SHAPES` table with its SL stitching type and a U-seam
mode (`"dup"` doubles the seam column; `"wrap"` loops the last column back to the
first, which is needed when vertices must land exactly on features — e.g. the
octagon's corners). Add a function, add a `SHAPES` entry, done.
