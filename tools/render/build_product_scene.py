"""Blender product-render scene for IWR-AOP-MODULE.

Usage:
  Blender --background --factory-startup --python tools/render/build_product_scene.py -- \
      build/product-renders/IWR-AOP-MODULE.glb OUT_DIR [--views hero,top,sensor-detail,back] [--samples 256] [--res 2400x1600] \
      [--dof shallow|deep] [--mode stills|orbit]

--dof deep doubles every f-number (~2x in-focus depth) and writes *-deep-dof.png alongside
the shallow set instead of overwriting it.

--mode orbit renders the SHOTS camera moves instead of the stills: start/end keyframes
into OUT_DIR/keyframes (--key-res, default 1280x720; --key-samples, default 64) and
every frame into OUT_DIR/frames/<shot>/ at --res/--samples and --fps (default 24),
plus OUT_DIR/shots.txt for stitch_orbit.py.

Relative GLB paths resolve from the repository root; relative OUT_DIR paths resolve
under build/product-renders/. Textures are read from build/product-renders/aop-top/.
"""
import bpy, bmesh, sys, os, math, random
from mathutils import Vector, Matrix
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_ROOT = REPO_ROOT / "build" / "product-renders"

argv = sys.argv[sys.argv.index("--") + 1:]
GLB = str((REPO_ROOT / argv[0]).resolve())
OUT = str((RENDER_ROOT / argv[1]).resolve())
opts = dict(zip(argv[2::2], argv[3::2]))
VIEWS = opts.get("--views", "hero,top,sensor-detail,back").split(",")
SAMPLES = int(opts.get("--samples", "256"))
RES = tuple(int(v) for v in opts.get("--res", "2400x1600").split("x"))
DOF = opts.get("--dof", "shallow")
if DOF not in ("shallow", "deep"):
    raise SystemExit(f"--dof must be shallow or deep, got {DOF!r}")
FSTOP_MUL, SUFFIX = (1.0, "") if DOF == "shallow" else (2.0, "-deep-dof")
MODE = opts.get("--mode", "stills")
if MODE not in ("stills", "orbit"):
    raise SystemExit(f"--mode must be stills or orbit, got {MODE!r}")
SCALE = 10.0  # 1 Blender unit = 100 mm after scaling
PREFIX = "IWR-AOP-MODULE"
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
scene = bpy.context.scene
imported = list(bpy.data.objects)


def mesh_descendants(o):
    out = [o] if o.type == "MESH" else []
    for c in o.children:
        out += mesh_descendants(c)
    return out


def world_bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


# ---------------------------------------------------------------- materials
def principled(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    return m, m.node_tree.nodes["Principled BSDF"], m.node_tree


def add_bump(tree, bsdf, scale, strength, distance=0.0002):
    tc = tree.nodes.new("ShaderNodeTexCoord")
    nz = tree.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = scale
    nz.inputs["Detail"].default_value = 6.0
    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = distance
    tree.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    tree.links.new(nz.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return nz


def mat_enig():
    m, b, t = principled("ENIG")
    b.inputs["Base Color"].default_value = (0.93, 0.77, 0.47, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.26
    add_bump(t, b, 4000, 0.04)
    return m


def mat_gold():
    m, b, t = principled("GoldContact")
    b.inputs["Base Color"].default_value = (1.0, 0.76, 0.38, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.2
    return m


def mat_tin():
    m, b, t = principled("MatteTin")
    b.inputs["Base Color"].default_value = (0.78, 0.78, 0.76, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.34
    add_bump(t, b, 6000, 0.06)
    return m


def mat_solder():
    m, b, t = principled("SAC305")
    b.inputs["Base Color"].default_value = (0.70, 0.71, 0.71, 1)
    b.inputs["Metallic"].default_value = 1.0
    b.inputs["Roughness"].default_value = 0.3
    add_bump(t, b, 9000, 0.12)
    return m


def mat_mask():
    # JLC green LPI over ENIG copper / FR-4: glossy resin, slightly see-through
    m, b, t = principled("GreenLPI")
    b.inputs["Base Color"].default_value = (0.004, 0.075, 0.02, 1)
    b.inputs["Metallic"].default_value = 0.0
    b.inputs["Roughness"].default_value = 0.38
    # LPI resin has one glossy surface: the coat. A specular base layer under it adds a
    # 0.38-rough lobe that smears the rim light across the whole board at low angles.
    b.inputs["Specular IOR Level"].default_value = 0.0
    b.inputs["Coat Weight"].default_value = 0.8
    b.inputs["Coat Roughness"].default_value = 0.07
    b.inputs["Coat IOR"].default_value = 1.52
    b.inputs["Alpha"].default_value = 0.72
    add_bump(t, b, 2500, 0.025)
    return m


def mat_silk():
    m, b, t = principled("Silkscreen")
    b.inputs["Base Color"].default_value = (0.84, 0.85, 0.82, 1)
    b.inputs["Roughness"].default_value = 0.6
    add_bump(t, b, 5000, 0.08)
    return m


def mat_fr4():
    m, b, t = principled("FR4")
    b.inputs["Base Color"].default_value = (0.52, 0.42, 0.22, 1)
    b.inputs["Roughness"].default_value = 0.55
    b.inputs["Subsurface Weight"].default_value = 0.35
    b.inputs["Subsurface Radius"].default_value = (0.006, 0.004, 0.0015)
    b.inputs["Subsurface Scale"].default_value = 1.0
    add_bump(t, b, 1800, 0.08)
    return m


def mat_plastic(name, rgb, rough=0.55):
    m, b, t = principled(name)
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Metallic"].default_value = 0.0
    b.inputs["Roughness"].default_value = rough
    b.inputs["Specular IOR Level"].default_value = 0.45
    add_bump(t, b, 3000, 0.05)
    return m


def mat_ceramic(rgb):
    m, b, t = principled("MLCC")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = 0.62
    b.inputs["Specular IOR Level"].default_value = 0.4
    add_bump(t, b, 7000, 0.07)
    return m


def mat_aop_mask():
    m, b, t = principled("AOPSubstrateMask")
    b.inputs["Base Color"].default_value = (0.012, 0.06, 0.02, 1)
    b.inputs["Roughness"].default_value = 0.3
    b.inputs["Coat Weight"].default_value = 0.7
    b.inputs["Coat Roughness"].default_value = 0.06
    return m


def mat_laminate():
    m, b, t = principled("AOPLaminateEdge")
    b.inputs["Base Color"].default_value = (0.36, 0.27, 0.13, 1)
    b.inputs["Roughness"].default_value = 0.6
    return m


ENIG, GOLD, TIN, SOLDER = mat_enig(), mat_gold(), mat_tin(), mat_solder()
MASK, SILK, FR4 = mat_mask(), mat_silk(), mat_fr4()
AOP_MASK, LAMINATE = mat_aop_mask(), mat_laminate()

# Source colours (KiCad/vendor STEP) that the generic heuristic would misclassify.
OVERRIDES = {
    (0.888, 0.539, 0.027): lambda: mat_plastic("SwitchPlunger", (0.62, 0.42, 0.10), 0.45),
    (0.729, 0.523, 0.205): lambda: LAMINATE,
    (0.084, 0.205, 0.026): lambda: AOP_MASK,
    (0.119, 0.059, 0.038): lambda: mat_ceramic((0.16, 0.085, 0.05)),
}
# KiCad board-layer materials (non-unit roughness distinguishes them from STEP colours).
BOARD = {
    (0.7, 0.61, 0.0): ENIG,
    (0.5, 0.5, 0.5): ENIG,
    (1.0, 1.0, 1.0): SILK,
    (0.08, 0.2, 0.14): MASK,
    (0.42, 0.45, 0.29): FR4,
}


def near(a, b, tol=0.006):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def classify(src):
    b = src.node_tree.nodes.get("Principled BSDF")
    rgb = tuple(b.inputs["Base Color"].default_value[:3])
    is_board = b.inputs["Roughness"].default_value < 0.99 or b.inputs["Metallic"].default_value < 0.5
    if is_board:
        for k, v in BOARD.items():
            if near(rgb, k):
                return v
    for k, fn in OVERRIDES.items():
        if near(rgb, k):
            return fn()
    r, g, bl = rgb
    hi, lo = max(rgb), min(rgb)
    sat = 0 if hi == 0 else (hi - lo) / hi
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * bl
    if r > g > bl and sat > 0.45 and 0.55 <= g / r <= 0.9 and luma > 0.3:
        return GOLD
    if luma > 0.3 and sat < 0.35:
        return TIN
    if luma > 0.3:
        return TIN  # yellow-coded terminals on custom IC models
    return mat_plastic("Molded_" + src.name, tuple(max(c, 0.012) for c in rgb), 0.5)


remap = {}
for o in imported:
    if o.type != "MESH":
        continue
    for slot in o.material_slots:
        if slot.material is None:
            continue
        if slot.material.name not in remap:
            remap[slot.material.name] = classify(slot.material)
        slot.material = remap[slot.material.name]
for src, dst in sorted(remap.items()):
    print(f"MATMAP {src} -> {dst.name}")

# ---------------------------------------------------------------- AOP top surface
# U300 antenna patches, buried-copper tone and laser marking, from make_aop_texture.py.
# Maps use the TI photo frame (u right, v down in board top view, Pin A1 bottom-left),
# which is U300's placed orientation: u -> +X, v -> -Y in the GLB frame.
AOP_TEX = str(RENDER_ROOT / "aop-top")


def mat_aop_top(frame):
    m, b, t = principled("AOPTop")
    n, ln = t.nodes, t.links
    tc = n.new("ShaderNodeTexCoord")
    tc.object = frame
    mp = n.new("ShaderNodeMapping")
    mp.inputs["Location"].default_value = (0, 1, 0)
    mp.inputs["Scale"].default_value = (1, -1, 1)
    ln.new(tc.outputs["Object"], mp.inputs["Vector"])

    def img(name, srgb):
        tx = n.new("ShaderNodeTexImage")
        tx.image = bpy.data.images.load(os.path.join(AOP_TEX, f"{name}.png"), check_existing=True)
        tx.image.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
        tx.extension = "EXTEND"
        tx.interpolation = "Cubic"
        ln.new(mp.outputs["Vector"], tx.inputs["Vector"])
        return tx

    col, rough, height, mark = img("color", True), img("rough", False), img("height", False), img("mark", False)
    ln.new(col.outputs["Color"], b.inputs["Base Color"])
    ln.new(rough.outputs["Color"], b.inputs["Roughness"])
    b.inputs["Specular IOR Level"].default_value = 0.0  # coat-only reflection, as on the board mask
    coat = n.new("ShaderNodeMath")  # glossy LPI coat except where the laser ablated it
    coat.operation = "MULTIPLY_ADD"
    coat.inputs[1].default_value = -0.62
    coat.inputs[2].default_value = 0.7
    ln.new(mark.outputs["Color"], coat.inputs[0])
    ln.new(coat.outputs[0], b.inputs["Coat Weight"])
    b.inputs["Coat Roughness"].default_value = 0.07
    b.inputs["Coat IOR"].default_value = 1.52
    bump = n.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 0.0008  # ~15 um patch relief at scene scale
    ln.new(height.outputs["Color"], bump.inputs["Height"])
    tc2 = n.new("ShaderNodeTexCoord")
    nz = n.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 2500
    nz.inputs["Detail"].default_value = 6.0
    peel = n.new("ShaderNodeBump")
    peel.inputs["Strength"].default_value = 0.03
    ln.new(tc2.outputs["Object"], nz.inputs["Vector"])
    ln.new(nz.outputs["Fac"], peel.inputs["Height"])
    ln.new(bump.outputs["Normal"], peel.inputs["Normal"])
    ln.new(peel.outputs["Normal"], b.inputs["Normal"])
    ln.new(peel.outputs["Normal"], b.inputs["Coat Normal"])
    return m


smtop = bpy.data.objects.get("FCCSP_SMTOP")
if smtop is None:
    raise RuntimeError("U300 model lacks FCCSP_SMTOP; cannot apply AOP top surface")
slo, shi = world_bbox([smtop])
print("AOP_TOP_MM", tuple(round(v * 1000, 3) for v in (shi - slo)), "centre", tuple(round(v * 1000, 3) for v in (slo + shi) / 2))
aop_frame = bpy.data.objects.new("AOP_TexFrame", None)
scene.collection.objects.link(aop_frame)
aop_frame.matrix_world = Matrix.Translation((slo.x, shi.y, shi.z)) @ Matrix.Diagonal((shi.x - slo.x, -(shi.y - slo.y), 1.0, 1.0))
AOP_TOP = mat_aop_top(aop_frame)
for slot in smtop.material_slots:
    slot.link = "OBJECT"  # mesh data may be shared with the package's bottom mask layer
    slot.material = AOP_TOP

# ---------------------------------------------------------------- solder fillets
REF_RE = __import__("re").compile(r"^[A-Z]{1,3}\d+$")
refs = {o.name: o for o in imported if o.type == "EMPTY" and REF_RE.match(o.name)}
print("REFS", len(refs))
rng = random.Random(6843)
fillet_mesh_objs = []


def fillet(name, axis, c_u, c_v, end, L_half, W, h, ext, base, s):
    n = 6
    prof = [(0.0, 0.0), (0.0, h)]
    conc = rng.uniform(0.45, 0.65)
    for i in range(1, n):
        t = i / n
        a = ext * t
        bz = h * (1 - t) ** (1.0 / conc) * (1 - 0.15 * t)
        prof.append((a, max(bz, 0.0)))
    prof.append((ext, 0.0))
    bm = bmesh.new()
    sides = []
    for vv in (-W / 2, W / 2):
        ring = []
        for a, bz in prof:
            u = c_u + end * (L_half + a)
            x, y = (u, c_v + vv) if axis == 0 else (c_v + vv, u)
            ring.append(bm.verts.new((x, y, base + s * bz)))
        sides.append(ring)
    A, B = sides
    fa = bm.faces.new(A)
    fb = bm.faces.new(list(reversed(B)))
    fa.smooth = fb.smooth = False
    m = len(prof)
    for i in range(m):
        j = (i + 1) % m
        f = bm.faces.new((A[i], A[j], B[j], B[i]))
        f.smooth = 1 <= i < m - 2
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(SOLDER)
    scene.collection.objects.link(ob)
    fillet_mesh_objs.append(ob)


n_fillets = 0
for ref, emp in refs.items():
    pre = "".join(ch for ch in ref if ch.isalpha())
    if pre not in ("C", "R", "F", "FB", "L"):
        continue
    ms = mesh_descendants(emp)
    if not ms:
        continue
    lo, hi = world_bbox(ms)
    size = hi - lo
    Lmax = max(size.x, size.y)
    if Lmax > 0.0036:
        continue  # power inductors: bottom-terminated
    axis = 0 if size.x >= size.y else 1
    L, Wd, H = (size.x, size.y, size.z) if axis == 0 else (size.y, size.x, size.z)
    top = lo.z > 0.0008
    s = 1 if top else -1
    base = (lo.z - 0.00003) if top else (hi.z + 0.00003)
    c = (lo + hi) / 2
    c_u, c_v = (c.x, c.y) if axis == 0 else (c.y, c.x)
    for end in (-1, 1):
        h = min(0.55 * H, 0.0007) * rng.uniform(0.82, 1.08)
        ext = min(max(0.2 * L, 0.00014), 0.00045) * rng.uniform(0.88, 1.1)
        fillet(f"Fillet_{ref}_{'AB'[end > 0]}", axis, c_u, c_v, end, L / 2, Wd * rng.uniform(0.84, 0.92), h, ext, base, s)
        n_fillets += 1
print("FILLETS", n_fillets)

# ---------------------------------------------------------------- root + pose
root = bpy.data.objects.new("BoardRoot", None)
scene.collection.objects.link(root)
for o in list(bpy.data.objects):
    if o.parent is None and o is not root and o.type in ("MESH", "EMPTY"):
        o.parent = root
board_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
lo0, hi0 = world_bbox(board_meshes)
centre0 = (lo0 + hi0) / 2
print("BOARD_MM", tuple(round(v * 1000, 2) for v in (hi0 - lo0)))
aop = refs.get("U300")


def pose(flip):
    root.matrix_world = Matrix.Identity(4)
    rot = Matrix.Rotation(math.pi, 4, "Y") if flip else Matrix.Identity(4)
    root.matrix_world = Matrix.Scale(SCALE, 4) @ rot @ Matrix.Translation(-centre0)
    bpy.context.view_layer.update()
    lo, hi = world_bbox(board_meshes)
    root.matrix_world = Matrix.Translation((0, 0, -lo.z)) @ root.matrix_world
    bpy.context.view_layer.update()
    return world_bbox(board_meshes)


# ---------------------------------------------------------------- stage
def stage():
    bpy.ops.mesh.primitive_plane_add(size=1)
    floor = bpy.context.active_object
    floor.name = "Stage"
    me = floor.data
    # cyclorama: floor sweeping up into a back wall
    bm = bmesh.new()
    W, D, R, Hh, seg = 30.0, 14.0, 4.0, 14.0, 16
    prof = [(-D, 0.0), (2.0, 0.0)]
    for i in range(1, seg + 1):
        a = (math.pi / 2) * i / seg
        prof.append((2.0 + R * math.sin(a), R - R * math.cos(a)))
    prof.append((2.0 + R, R + Hh))
    rows = [[bm.verts.new((x, y, z)) for x in (-W / 2, W / 2)] for y, z in prof]
    for i in range(len(rows) - 1):
        f = bm.faces.new((rows[i][0], rows[i][1], rows[i + 1][1], rows[i + 1][0]))
        f.smooth = True
    bm.to_mesh(me)
    bm.free()
    m, b, t = principled("StageConcrete")
    tc = t.nodes.new("ShaderNodeTexCoord")
    nz = t.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 3.0
    nz.inputs["Detail"].default_value = 10.0
    nz.inputs["Roughness"].default_value = 0.6
    ramp = t.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.010, 0.0105, 0.012, 1)
    ramp.color_ramp.elements[1].color = (0.028, 0.028, 0.030, 1)
    t.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    t.links.new(nz.outputs["Fac"], ramp.inputs["Fac"])
    t.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    rr = t.nodes.new("ShaderNodeMapRange")
    rr.inputs["To Min"].default_value = 0.42
    rr.inputs["To Max"].default_value = 0.7
    t.links.new(nz.outputs["Fac"], rr.inputs["Value"])
    t.links.new(rr.outputs["Result"], b.inputs["Roughness"])
    nz2 = t.nodes.new("ShaderNodeTexNoise")
    nz2.inputs["Scale"].default_value = 160.0
    bump = t.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    t.links.new(tc.outputs["Object"], nz2.inputs["Vector"])
    t.links.new(nz2.outputs["Fac"], bump.inputs["Height"])
    t.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    floor.data.materials.append(m)
    return floor


stage_obj = stage()


def area(name, loc, target, size, power, color=(1, 1, 1), size_y=None, spread=55, glossy=True):
    d = bpy.data.lights.new(name, "AREA")
    d.shape = "RECTANGLE"
    d.size = size
    d.size_y = size_y or size
    d.energy = power
    d.color = color
    d.spread = math.radians(spread)
    o = bpy.data.objects.new(name, d)
    scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    o.visible_glossy = glossy
    return o


# Glossy LPI mirrors any light at the camera's reflection angle (same elevation,
# opposite azimuth). Cameras sit at 22-76 deg elevation: keep key/fill on the camera
# side. The rim sits low (~15 deg) directly behind the board, inside the mirror cone of
# the low orbit angles (az ~0, el 22-27); as a thin strip reflected only by the masks'
# clear coat it reads as a hard glint instead of a wash.
key = area("Key", (-1.4, -1.0, 2.6), (0, 0, 0), 1.2, 30, (1.0, 0.96, 0.9))
fill = area("Fill", (2.0, -1.6, 1.9), (0, 0, 0.1), 1.8, 8, (0.9, 0.95, 1.0))
rim = area("Rim", (0.3, 2.6, 0.7), (0, 0, 0.05), 2.4, 20, (0.7, 0.85, 1.0), size_y=0.05)
top_soft = area("TopSoft", (0, 0, 3.0), (0, 0, 0), 2.0, 10, glossy=False)

# world: studio HDRI for reflections only; camera sees near-black
world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
wt = world.node_tree
wt.nodes.clear()
env = wt.nodes.new("ShaderNodeTexEnvironment")
hdr = os.path.join(bpy.utils.system_resource("DATAFILES"), "studiolights", "world", "studio.exr")
env.image = bpy.data.images.load(hdr)
bg = wt.nodes.new("ShaderNodeBackground")
bg.inputs["Strength"].default_value = 0.12
dark = wt.nodes.new("ShaderNodeBackground")
dark.inputs["Color"].default_value = (0.004, 0.004, 0.005, 1)
lp = wt.nodes.new("ShaderNodeLightPath")
mix = wt.nodes.new("ShaderNodeMixShader")
out = wt.nodes.new("ShaderNodeOutputWorld")
wt.links.new(env.outputs["Color"], bg.inputs["Color"])
wt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
wt.links.new(bg.outputs["Background"], mix.inputs[1])
wt.links.new(dark.outputs["Background"], mix.inputs[2])
wt.links.new(mix.outputs["Shader"], out.inputs["Surface"])

# ---------------------------------------------------------------- render settings
scene.render.engine = "CYCLES"
cy = scene.cycles
prefs = bpy.context.preferences.addons["cycles"].preferences
try:
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    cy.device = "GPU"
except Exception as e:  # noqa: BLE001
    print("GPU unavailable:", e)
cy.samples = SAMPLES
cy.use_denoising = True
cy.denoiser = "OPENIMAGEDENOISE"
cy.max_bounces = 12
cy.transparent_max_bounces = 16
scene.render.resolution_x, scene.render.resolution_y = RES
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_depth = "16"
scene.view_settings.view_transform = "AgX"
try:
    scene.view_settings.look = "AgX - Medium High Contrast"
except TypeError:
    pass
scene.view_settings.exposure = 0.0

cam_data = bpy.data.cameras.new("Camera")
cam = bpy.data.objects.new("Camera", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam_data.clip_start = 0.005
cam_data.clip_end = 100


def aim(loc, target):
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()


def fit(target, az_deg, el_deg, lens, objs, margin=0.12, shift=(0, 0)):
    """Place camera on a sphere around target and back off until objs fit with margin."""
    from bpy_extras.object_utils import world_to_camera_view
    cam_data.lens = lens
    cam_data.shift_x, cam_data.shift_y = shift
    az, el = math.radians(az_deg), math.radians(el_deg)
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    dist = 1.0
    for _ in range(40):
        aim(Vector(target) + d * dist, target)
        uv = [world_to_camera_view(scene, cam, p) for p in pts]
        ext = max(max(abs(p.x - 0.5), abs(p.y - 0.5)) for p in uv) * 2
        if abs(ext - (1 - margin)) < 0.004:
            break
        dist *= ext / (1 - margin)
    return dist


def dof(focus_point, fstop):
    cam_data.dof.use_dof = True
    cam_data.dof.focus_distance = (cam.location - Vector(focus_point)).length
    # Scene is SCALE x real size; equivalent real-world DoF needs SCALE x the aperture diameter.
    cam_data.dof.aperture_fstop = fstop / SCALE


def obj_centre(objs):
    lo, hi = world_bbox(objs)
    return (lo + hi) / 2, lo, hi


def render(name):
    scene.render.filepath = os.path.join(OUT, f"{PREFIX}-{name}{SUFFIX}.png")
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)


aop_meshes = mesh_descendants(aop) if aop else []
HERO_AZ, HERO_EL = -28, 38  # hero still and the first frame of orbit shot 01


def render_stills():
    for view in VIEWS:
        if view in ("hero", "top", "sensor-detail"):
            lo, hi = pose(False)
        else:
            lo, hi = pose(True)
        c = (lo + hi) / 2
        if view == "hero":
            tgt = Vector((c.x, c.y, (lo.z + hi.z) * 0.45))
            fit(tgt, HERO_AZ, HERO_EL, 85, board_meshes, 0.14)
            dof(obj_centre(aop_meshes)[0], 11 * FSTOP_MUL)
        elif view == "top":
            tgt = Vector((c.x, c.y, hi.z * 0.7))
            fit(tgt, -6, 76, 70, board_meshes, 0.14)
            dof(tgt, 11 * FSTOP_MUL)
        elif view == "sensor-detail":
            ac, alo, ahi = obj_centre(aop_meshes)
            fit(ac, -18, 48, 100, aop_meshes, 0.66)
            dof(ac, 16 * FSTOP_MUL)
        elif view == "back":
            tgt = Vector((c.x, c.y, (lo.z + hi.z) * 0.45))
            fit(tgt, 30, 34, 85, board_meshes, 0.14)
            dof(tgt, 11 * FSTOP_MUL)
        render(view)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, f"{PREFIX}-product-renders{SUFFIX}.blend"))


# ---------------------------------------------------------------- orbit video
# Each shot moves the camera on a sphere around a target from (az, el, margin) to
# (az, el, margin); margin is the frame fraction left empty by fit() (negative = the
# fitted object overflows the frame). Shot 1 starts on the hero still, shot 2 on the
# sensor-detail still, and shot 3 ends on the back still.
# "frame" picks what fit() frames: the whole board or just U300.
SHOTS = [
    dict(name="01-opener", flip=False, target="hero", frame="board", lens=85, fstop=22, focus="aop",
         start=(HERO_AZ, HERO_EL, 0.14), end=(8, 34, 0.14), seconds=3.0, ease="in"),
    dict(name="02-radar", flip=False, target="aop", frame="aop", lens=100, fstop=16, focus="aop",
         start=(-18, 48, 0.66), end=(-48, 40, 0.60), seconds=3.0, ease="linear"),
    dict(name="03-back-crane", flip=True, target="hero", frame="board", lens=85, fstop=22, focus="target",
         start=(22, 62, 0.14), end=(30, 34, 0.14), seconds=2.5, ease="out"),
]
FPS = int(opts.get("--fps", "24"))
KEY_RES = tuple(int(v) for v in opts.get("--key-res", "1280x720").split("x"))
KEY_SAMPLES = int(opts.get("--key-samples", "64"))
SHOT_FILTER = set(opts["--shots"].split(",")) if "--shots" in opts else None
KEYS_ONLY = opts.get("--keys-only", "0") == "1"
EASE = {
    "linear": lambda t: t,
    "in": lambda t: t * t * (2 - t),          # gentle start, full speed at the cut
    "out": lambda t: 1 - (1 - t) ** 2 * (1 + t),  # full speed in, settles on the end frame
}


def sphere_dir(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))


def render_orbit():
    key_dir = os.path.join(OUT, "keyframes")
    os.makedirs(key_dir, exist_ok=True)
    scene.render.use_persistent_data = True  # keep BVH/textures between frames; only the camera moves
    scene.render.image_settings.color_depth = "8"
    focus = bpy.data.objects.new("FocusTarget", None)
    scene.collection.objects.link(focus)
    cam_data.dof.use_dof = True
    cam_data.dof.focus_object = focus
    manifest = []
    for shot in SHOTS:
        if SHOT_FILTER is not None and shot["name"] not in SHOT_FILTER:
            continue
        lo, hi = pose(shot["flip"])
        c = (lo + hi) / 2
        ac = obj_centre(aop_meshes)[0]
        tgt = {"hero": Vector((c.x, c.y, (lo.z + hi.z) * 0.45)),
               "aop": ac}[shot["target"]]
        objs = board_meshes if shot["frame"] == "board" else aop_meshes
        focus.location = ac if shot["focus"] == "aop" else tgt
        cam_data.dof.aperture_fstop = shot["fstop"] / SCALE
        (az0, el0, m0), (az1, el1, m1) = shot["start"], shot["end"]
        d0 = fit(tgt, az0, el0, shot["lens"], objs, m0)
        d1 = fit(tgt, az1, el1, shot["lens"], objs, m1)
        n = round(shot["seconds"] * FPS)
        ease = EASE[shot["ease"]]

        def place(t):
            k = ease(t)
            dist = d0 * (d1 / d0) ** k  # geometric: constant perceived zoom rate
            aim(tgt + sphere_dir(az0 + (az1 - az0) * k, el0 + (el1 - el0) * k) * dist, tgt)

        scene.render.resolution_x, scene.render.resolution_y = KEY_RES
        cy.samples = KEY_SAMPLES
        for tag, t in (("start", 0.0), ("end", 1.0)):
            place(t)
            scene.render.filepath = os.path.join(key_dir, f"{shot['name']}-{tag}.png")
            bpy.ops.render.render(write_still=True)
            print("WROTE", scene.render.filepath)

        if KEYS_ONLY:
            continue
        frame_dir = os.path.join(OUT, "frames", shot["name"])
        os.makedirs(frame_dir, exist_ok=True)
        scene.render.resolution_x, scene.render.resolution_y = RES
        cy.samples = SAMPLES
        for i in range(n):
            place(i / (n - 1))
            scene.render.filepath = os.path.join(frame_dir, f"{i + 1:04d}.png")
            bpy.ops.render.render(write_still=True)
        print("SHOT", shot["name"], n, "frames")
        manifest.append(f"{shot['name']} {n}")
    if not KEYS_ONLY:
        with open(os.path.join(OUT, "shots.txt"), "w") as f:
            f.write(f"fps {FPS}\n" + "\n".join(manifest) + "\n")


if MODE == "stills":
    render_stills()
else:
    render_orbit()
