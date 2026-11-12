"""effente demo conveyor: parametric Fusion build script (TT gear motor).

A small, almost fully printed belt conveyor that gives the effente probe a
machine to diagnose, following conveyor-build-guide.md (3D-printed route)
but sized for the parts the team has: one yellow TT gear motor.

  Frame          One printed part: base, two side walls, a slider bed under
                 the top belt run, U-slots the roller pins drop into, a pad
                 for the motor bracket, and a pocket for the steel probe disc
                 on wall B. No bearings: the printed pins run in the slots
                 (a dab of grease or Vaseline helps).
  Drive roller   Pushed straight onto the TT motor's side output shaft
                 (double-D bore), its other end is a pin in wall B's slot.
  Idle roller    Pins in both walls.
  Motor bracket  A cradle the TT gearbox sits in, held by 2 zip ties. The
                 bracket is bolted to the frame's pad with 2 M3 bolts: these
                 are the loose-mount fault (guide step 10).
  Imbalance      4 holes in the drive roller's end face: screw an M3 screw
                 into one (or stick clay on the roller, as in the guide).
  Belt           Either a TPU tube printed in vase mode (separate component,
                 laid out beside the conveyor), or a strip of duct tape
                 folded sticky-side in, 40 mm wide, joined into a loop.
  Probe point    Pocket for a 20 mm steel disc / washer on wall B's outside,
                 next to the drive roller, on the frame (not the bracket).

World axes: X along the belt, Y across it (wall A at y=0, wall B at y=72),
Z up from the table. All sizes in mm.

UNVERIFIED: the TT motor sizes below are typical listing values (Addicore:
70 x 18.8 x 22.5 mm body) plus your 48 mm gearbox / 65 mm overall lengths.
The shaft position and shaft size are not measured: check them with
calipers and change TT before printing (see AGENTS.md).

Run inside Fusion (Scripts and Add-Ins, or the Fusion MCP 'script' feature).
"""
# Fusion 360 build script: short geometry names (L, W, T, H, B) and many-argument
# helpers follow the CAD maths, so these style checks are off on purpose.
# pylint: disable=invalid-name,missing-function-docstring,missing-class-docstring,too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements,use-dict-literal,consider-using-f-string,disallowed-name,duplicate-code
import math

import adsk.core
import adsk.fusion

# ------------------------------------------------------------------ parameters
F = dict(
    length=200.0, width=72.0, wall=4.0, base_t=4.0, top=39.0,
    axis_z=24.0, drive_x=24.0, idle_x=180.0,
    bed_t=3.0, bed_gap=1.0,                 # slider bed under the top belt run
    slider_bed=False,                       # off: it is a 64 mm span that needs supports
    # lightening windows in the base (saves print time; the walls stay solid)
    base_windows=True, base_rim=8.0, base_bar=8.0, base_win_n=4,
    # viewing windows in both side walls, so the rollers and belt show in the
    # demo. Solid rails top and bottom, posts between windows, and solid
    # panels at the roller slots and around the probe disc (it must be rigid).
    wall_windows=True, win_rail_bot=5.0, win_rail_top=6.0, win_post=8.0,
    win_a=((38.0, 80.0), (88.0, 126.0), (134.0, 168.0)),     # wall A, x ranges
    win_b=((38.0, 42.0), (82.0, 122.0), (130.0, 168.0)),     # wall B (probe panel 44-80)
    pin_slot=5.4,                           # U-slots the roller pins drop into
    hub_slot=11.0,                          # wall A slot the drive hub passes through
    probe_x=60.0, disc_d=20.1, disc_depth=2.0,
    # pad outside wall A that the motor bracket bolts to
    pad_y=-35.0, pad_x=(0.0, 70.0),
    bolt_d=3.4, nut_af=5.6, nut_depth=2.5,
)

ROLLER = dict(
    drum_d=30.0, flange_d=34.0, flange_t=2.0, hub_d=10.0, pin_d=5.0,
    belt_w=40.0, belt_t=1.0, drum_margin=2.0, end_clear=0.5,
    weight_holes=4, weight_r=10.0, weight_d=2.6, weight_depth=8.0,   # M3 self-tap
)

# yellow TT gear motor (1:48). UNVERIFIED except gb_len / total_len (yours).
TT = dict(
    gb_len=48.0, total_len=65.0,            # yellow gearbox, gearbox + motor can
    gb_t=18.8, gb_h=22.5,                   # along the shaft, and height
    shaft_from_end=11.0,                    # gearbox end face to output shaft axis
    shaft_d=5.4, shaft_flats=3.7,           # double-D output shaft
    shaft_len=8.5,                          # sticks out of each side face
    face_gap=3.0,                           # gearbox face to wall A (zip tie room)
    clear=0.3,
)

BRACKET = dict(
    foot_t=3.0, back_t=3.0, stop_t=3.0, back_h=14.0,
    stub_hole=9.0,                          # clearance for the back shaft stub
    bolt_xs=(12.0, 52.0), bolt_back=4.0,     # fault bolts, behind the back wall
    foot_back=8.0,                          # foot reaches this far past the back wall
    tie_w=4.0, tie_h=2.0, tie_xs=(16.0, 36.0),   # zip-tie tunnels, from the gearbox end
)

# printed TPU belt: a thin tube printed in vase mode, a little short so it
# stretches tight over the rollers
BELT = dict(stretch=0.02, wall=1.0, layout_y=160.0)

MM = 0.1  # Fusion API works in cm


def v(mm):
    return adsk.core.ValueInput.createByReal(mm * MM)


def p(x, y, z=0.0):
    return adsk.core.Point3D.create(x * MM, y * MM, z * MM)


class Builder:
    def __init__(self, comp):
        self.c = comp
        self.f = comp.features

    def plane(self, axis, offset):
        base = {'x': self.c.yZConstructionPlane,
                'y': self.c.xZConstructionPlane,
                'z': self.c.xYConstructionPlane}[axis]
        if abs(offset) < 1e-9:
            return base
        idx = 'xyz'.index(axis)
        for sign in (1, -1):
            inp = self.c.constructionPlanes.createInput()
            inp.setByOffset(base, v(sign * offset))
            pl = self.c.constructionPlanes.add(inp)
            o = pl.geometry.origin
            if abs((o.x, o.y, o.z)[idx] - offset * MM) < 1e-6:
                return pl
            pl.deleteMe()
        raise RuntimeError('could not place plane %s=%s' % (axis, offset))

    def sketch(self, axis, offset):
        return self.c.sketches.add(self.plane(axis, offset))

    @staticmethod
    def _sp(sk, x, y, z):
        return sk.modelToSketchSpace(p(x, y, z))

    def rect(self, sk, a, b):
        sk.sketchCurves.sketchLines.addTwoPointRectangle(
            self._sp(sk, *a), self._sp(sk, *b))
        return sk.profiles.item(0)

    def circle(self, sk, c, d):
        sk.sketchCurves.sketchCircles.addByCenterRadius(self._sp(sk, *c), d * MM / 2)
        return sk.profiles.item(0)

    def extrude(self, prof, dist, op, body=None, symmetric=False):
        ops = {'new': adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
               'join': adsk.fusion.FeatureOperations.JoinFeatureOperation,
               'cut': adsk.fusion.FeatureOperations.CutFeatureOperation}
        inp = self.f.extrudeFeatures.createInput(prof, ops[op])
        if symmetric:
            inp.setSymmetricExtent(v(dist), True)
        else:
            inp.setDistanceExtent(False, v(dist))
        if body is not None and op != 'new':
            inp.participantBodies = [body]
        return self.f.extrudeFeatures.add(inp)

    # z prisms
    def zbox(self, x0, y0, x1, y1, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        return self.extrude(self.rect(sk, (x0, y0, z0), (x1, y1, z0)), z1 - z0, op, body)

    def zcyl(self, cx, cy, d, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        return self.extrude(self.circle(sk, (cx, cy, z0), d), z1 - z0, op, body)

    def zhex(self, cx, cy, af, z0, z1, op, body=None):
        """Hex prism along Z, across-flats af (nut traps)."""
        sk = self.sketch('z', z0)
        r = af / math.sqrt(3)
        pts = [self._sp(sk, cx + r * math.cos(math.radians(a)),
                        cy + r * math.sin(math.radians(a)), z0) for a in range(0, 360, 60)]
        lines = sk.sketchCurves.sketchLines
        for i in range(6):
            lines.addByTwoPoints(pts[i], pts[(i + 1) % 6])
        return self.extrude(sk.profiles.item(0), z1 - z0, op, body)

    # y prisms: symmetric about the mid plane, so extrude direction never matters
    def ybox(self, x0, x1, z0, z1, y0, y1, op, body=None):
        ym = (y0 + y1) / 2
        sk = self.sketch('y', ym)
        return self.extrude(self.rect(sk, (x0, ym, z0), (x1, ym, z1)),
                            abs(y1 - y0), op, body, symmetric=True)

    def ycyl(self, cx, cz, d, y0, y1, op, body=None):
        ym = (y0 + y1) / 2
        sk = self.sketch('y', ym)
        return self.extrude(self.circle(sk, (cx, ym, cz), d),
                            abs(y1 - y0), op, body, symmetric=True)

    def yhex(self, cx, cz, af, y0, y1, op, body=None):
        """Hex prism, across-flats af."""
        ym = (y0 + y1) / 2
        sk = self.sketch('y', ym)
        r = af / math.sqrt(3)
        pts = [self._sp(sk, cx + r * math.cos(math.radians(a)), ym,
                        cz + r * math.sin(math.radians(a))) for a in range(0, 360, 60)]
        lines = sk.sketchCurves.sketchLines
        for i in range(6):
            lines.addByTwoPoints(pts[i], pts[(i + 1) % 6])
        return self.extrude(sk.profiles.item(0), abs(y1 - y0), op, body, symmetric=True)

    # x prisms
    def xbox(self, y0, y1, z0, z1, x0, x1, op, body=None):
        xm = (x0 + x1) / 2
        sk = self.sketch('x', xm)
        return self.extrude(self.rect(sk, (xm, y0, z0), (xm, y1, z1)),
                            abs(x1 - x0), op, body, symmetric=True)


def new_comp(root, name):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ.component


def first_body(feat, name):
    b = feat.bodies.item(0)
    b.name = name
    return b



# ---------------------------------------------------------------- derived
def roller_ys():
    """Y extents shared by both rollers."""
    w_in0, w_in1 = F['wall'], F['width'] - F['wall']
    mid = F['width'] / 2
    drum = ROLLER['belt_w'] + 2 * ROLLER['drum_margin']
    d0, d1 = mid - drum / 2, mid + drum / 2
    ft = ROLLER['flange_t']
    return dict(w_in0=w_in0, w_in1=w_in1, d0=d0, d1=d1,
                f0=d0 - ft, f1=d1 + ft,
                hub0=w_in0 + ROLLER['end_clear'], hub1=w_in1 - ROLLER['end_clear'])


def tt_geo():
    """TT motor outside wall A: gearbox lying along X, output shaft along +Y
    on the drive-roller axis, motor can toward +X."""
    m, k, c = TT, BRACKET, TT['clear']
    x_e = F['drive_x'] - m['shaft_from_end']
    y_face = -m['face_gap']
    y_back = y_face - m['gb_t']
    z0 = F['axis_z'] - m['gb_h'] / 2
    g = dict(x_e=x_e, x_gb1=x_e + m['gb_len'], x_can1=x_e + m['total_len'],
             y_face=y_face, y_back=y_back, z0=z0, z1=z0 + m['gb_h'])
    g['foot_z0'], g['foot_z1'] = F['base_t'], F['base_t'] + k['foot_t']
    g['bx0'] = x_e - c - k['stop_t']                     # bracket x range
    g['bx1'] = g['x_gb1']
    g['wy0'] = y_back - c - k['back_t']                  # back wall
    g['wy1'] = y_back - c
    g['fy0'] = g['wy0'] - k['foot_back']                 # foot
    g['fy1'] = y_face - 0.5
    g['bolt_y'] = g['fy0'] + k['bolt_back']
    if g['fy0'] < F['pad_y'] or g['bx0'] < F['pad_x'][0] or g['z0'] - c < g['foot_z1'] + 2:
        raise ValueError('TT motor does not fit the bracket pad: change F or TT')
    return g


# ---------------------------------------------------------------- frame
def build_frame(comp):
    b = Builder(comp)
    L, W, t, T = F['length'], F['width'], F['wall'], F['top']
    az = F['axis_z']
    frame = first_body(b.zbox(0, 0, L, W, 0, F['base_t'], 'new'), 'Frame')
    b.zbox(0, 0, L, t, F['base_t'], T, 'join', frame)          # wall A
    b.zbox(0, W - t, L, W, F['base_t'], T, 'join', frame)      # wall B
    # pad outside wall A for the motor bracket
    b.zbox(F['pad_x'][0], F['pad_y'], F['pad_x'][1], 0.5, 0, F['base_t'], 'join', frame)

    # slider bed under the top belt run, between the two rollers
    r = ROLLER['flange_d'] / 2 + F['bed_gap']
    if F['slider_bed']:
        b.zbox(F['drive_x'] + r, t, F['idle_x'] - r, W - t, T - F['bed_t'], T, 'join', frame)
    if F['wall_windows']:
        wz0, wz1 = F['base_t'] + F['win_rail_bot'], T - F['win_rail_top']
        for (y0, y1), wins in (((0, t), F['win_a']), ((W - t, W), F['win_b'])):
            for xa, xb in wins:
                if xb - xa >= 6.0:
                    b.ybox(xa, xb, wz0, wz1, y0 - 0.5, y1 + 0.5, 'cut', frame)
    if F['base_windows']:
        rim, bar, n = F['base_rim'], F['base_bar'], F['base_win_n']
        x0, x1 = rim, L - rim
        ww = (x1 - x0 - (n - 1) * bar) / n
        for i in range(n):
            wx = x0 + i * (ww + bar)
            b.zbox(wx, t + rim / 2, wx + ww, W - t - rim / 2, -0.5, F['base_t'] + 0.5, 'cut', frame)

    # U-slots: drive pin in wall B, idle pins in both walls
    ps = F['pin_slot']
    for x, (y0, y1) in ((F['drive_x'], (W - t, W)),
                        (F['idle_x'], (0, t)), (F['idle_x'], (W - t, W))):
        b.ybox(x - ps / 2, x + ps / 2, az, T + 1, y0 - 0.5, y1 + 0.5, 'cut', frame)
        b.ycyl(x, az, ps, y0 - 0.5, y1 + 0.5, 'cut', frame)
    # wider U-slot in wall A for the drive roller's hub (the motor shaft
    # carries that end, the hub just passes through)
    hs = F['hub_slot']
    b.ybox(F['drive_x'] - hs / 2, F['drive_x'] + hs / 2, az, T + 1, -0.5, t + 0.5, 'cut', frame)
    b.ycyl(F['drive_x'], az, hs, -0.5, t + 0.5, 'cut', frame)

    # fault bolts: holes through the pad, nut traps from underneath
    g = tt_geo()
    for bx in BRACKET['bolt_xs']:
        b.zcyl(bx, g['bolt_y'], F['bolt_d'], -0.5, F['base_t'] + 0.5, 'cut', frame)
        b.zhex(bx, g['bolt_y'], F['nut_af'], -0.5, F['nut_depth'], 'cut', frame)

    # probe point: steel disc pocket on wall B's outer face
    b.ycyl(F['probe_x'], az, F['disc_d'], W - F['disc_depth'], W + 0.5, 'cut', frame)
    return frame


# ---------------------------------------------------------------- rollers
def build_roller(comp, drive):
    b = Builder(comp)
    ry = roller_ys()
    x, az = (F['drive_x'] if drive else F['idle_x']), F['axis_z']
    R = ROLLER
    name = 'Drive roller' if drive else 'Idle roller'
    body = first_body(b.ycyl(x, az, R['drum_d'], ry['d0'], ry['d1'], 'new'), name)
    b.ycyl(x, az, R['flange_d'], ry['f0'], ry['d0'], 'join', body)
    b.ycyl(x, az, R['flange_d'], ry['d1'], ry['f1'], 'join', body)
    # wall-B side: hub + pin (both rollers)
    b.ycyl(x, az, R['hub_d'], ry['f1'], ry['hub1'], 'join', body)
    b.ycyl(x, az, R['pin_d'], ry['hub1'], F['width'] - 0.5, 'join', body)
    if drive:
        # wall-A side: long hub through wall A onto the TT output shaft
        g, m = tt_geo(), TT
        y_end = g['y_face'] + 0.5
        b.ycyl(x, az, R['hub_d'], y_end, ry['f0'], 'join', body)
        bd = m['shaft_d'] + 0.2
        y_b1 = g['y_face'] + m['shaft_len'] + 0.5
        b.ycyl(x, az, bd, y_end - 0.5, y_b1, 'cut', body)
        fl = (m['shaft_flats'] + 0.15) / 2              # fill back the two flats
        for z0, z1 in ((az + fl, az + bd / 2 + 0.3), (az - bd / 2 - 0.3, az - fl)):
            b.ybox(x - bd / 2 - 0.3, x + bd / 2 + 0.3, z0, z1, y_end, y_b1, 'join', body)
        # imbalance: M3 screws self-tap into these holes in the wall-B end face
        for i in range(R['weight_holes']):
            a = 2 * math.pi * i / R['weight_holes']
            b.ycyl(x + R['weight_r'] * math.cos(a), az + R['weight_r'] * math.sin(a),
                   R['weight_d'], ry['f1'] - R['weight_depth'], ry['f1'] + 0.5, 'cut', body)
    else:
        b.ycyl(x, az, R['hub_d'], ry['hub0'], ry['f0'], 'join', body)
        b.ycyl(x, az, R['pin_d'], 0.5, ry['hub0'], 'join', body)
    return body


def belt_len():
    return (2 * (F['idle_x'] - F['drive_x'])
            + math.pi * (ROLLER['drum_d'] + ROLLER['belt_t']))


def build_belt(comp):
    """Reference only: the belt in place over the rollers."""
    b = Builder(comp)
    mid, w = F['width'] / 2, ROLLER['belt_w']
    y0, y1 = mid - w / 2, mid + w / 2
    r_in = ROLLER['drum_d'] / 2
    r_out = r_in + ROLLER['belt_t']
    x0, x1, az = F['drive_x'], F['idle_x'], F['axis_z']
    belt = first_body(b.ycyl(x0, az, 2 * r_out, y0, y1, 'new'), 'Belt (reference)')
    b.ycyl(x1, az, 2 * r_out, y0, y1, 'join', belt)
    b.ybox(x0, x1, az - r_out, az + r_out, y0, y1, 'join', belt)
    b.ycyl(x0, az, 2 * r_in, y0 - 1, y1 + 1, 'cut', belt)
    b.ycyl(x1, az, 2 * r_in, y0 - 1, y1 + 1, 'cut', belt)
    b.ybox(x0, x1, az - r_in, az + r_in, y0 - 1, y1 + 1, 'cut', belt)
    return belt


def build_belt_print(comp):
    """The belt as a TPU tube to print standing up (vase mode), laid out
    beside the conveyor. It is 2% short so it stretches tight."""
    b = Builder(comp)
    circ = belt_len() * (1 - BELT['stretch'])
    r = circ / (2 * math.pi)
    cx, cy = F['length'] / 2, BELT['layout_y'] + r
    tube = first_body(b.zcyl(cx, cy, 2 * r, 0, ROLLER['belt_w'], 'new'), 'Belt (TPU print)')
    b.zcyl(cx, cy, 2 * (r - BELT['wall']), -0.5, ROLLER['belt_w'] + 0.5, 'cut', tube)
    return tube


# ---------------------------------------------------------------- motor bracket
def build_bracket(comp):
    """Cradle for the TT gearbox: foot, saddle under the gearbox, end stop,
    back wall (with a hole for the back shaft stub), 2 zip-tie tunnels.
    The foot is bolted to the frame pad by the 2 fault bolts."""
    b, k, m, g = Builder(comp), BRACKET, TT, tt_geo()
    c = m['clear']
    body = first_body(b.zbox(g['bx0'], g['fy0'], g['bx1'], g['fy1'], g['foot_z0'], g['foot_z1'],
                             'new'), 'Motor bracket')
    # the foot also covers the bolt positions behind the back wall
    bxs = k['bolt_xs']
    b.zbox(min(bxs) - 5, g['fy0'], max(bxs) + 5, g['wy0'] + 0.01, g['foot_z0'], g['foot_z1'],
           'join', body)
    b.zbox(g['x_e'] - c, g['wy1'] - 0.01, g['bx1'], g['fy1'], g['foot_z1'] - 0.01, g['z0'] - c,
           'join', body)                                               # saddle
    b.zbox(g['bx0'], g['wy0'], g['x_e'] - c, g['fy1'], g['foot_z1'] - 0.01, g['z0'] + 10.0,
           'join', body)                                               # end stop
    b.zbox(g['bx0'], g['wy0'], g['bx1'], g['wy1'], g['foot_z1'] - 0.01, g['z0'] + k['back_h'],
           'join', body)                                               # back wall
    b.ycyl(F['drive_x'], F['axis_z'], k['stub_hole'], g['wy0'] - 0.5, g['wy1'] + 0.5, 'cut', body)
    for bx in bxs:
        b.zcyl(bx, g['bolt_y'], F['bolt_d'], g['foot_z0'] - 0.5, g['foot_z1'] + 0.5, 'cut', body)
    # zip-tie tunnels through the saddle, front to back
    tz0 = g['foot_z1'] + 0.8
    for tx in k['tie_xs']:
        x = g['x_e'] + tx
        b.ybox(x - k['tie_w'] / 2, x + k['tie_w'] / 2, tz0, tz0 + k['tie_h'],
               g['wy0'] - 0.5, g['fy1'] + 0.5, 'cut', body)
    return body


# ---------------------------------------------------------------- checks
def tbox(x0, y0, z0, x1, y1, z1):
    tm = adsk.fusion.TemporaryBRepManager.get()
    c = p((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    obb = adsk.core.OrientedBoundingBox3D.create(
        c, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
        abs(x1 - x0) * MM, abs(y1 - y0) * MM, abs(z1 - z0) * MM)
    return tm.createBox(obb)


def tcyl_y(cx, cz, d, y0, y1):
    tm = adsk.fusion.TemporaryBRepManager.get()
    return tm.createCylinderOrCone(p(cx, y0, cz), d * MM / 2, p(cx, y1, cz), d * MM / 2)


def overlap(a, bdy):
    tm = adsk.fusion.TemporaryBRepManager.get()
    ta, tb = tm.copy(a), tm.copy(bdy)
    tm.booleanOperation(ta, tb, adsk.fusion.BooleanTypes.IntersectionBooleanType)
    return ta.volume * 1000  # mm3


def dummies():
    g, m = tt_geo(), TT
    sd = m['shaft_d'] - 0.1
    return dict(
        gearbox=tbox(g['x_e'], g['y_back'], g['z0'], g['x_gb1'], g['y_face'], g['z1']),
        motor_can=tbox(g['x_gb1'], g['y_back'] + 1, g['z0'] + 1, g['x_can1'], g['y_face'] - 1,
                       g['z1'] - 1),
        shaft=tcyl_y(F['drive_x'], F['axis_z'], sd, g['y_face'], g['y_face'] + m['shaft_len']),
        back_stub=tcyl_y(F['drive_x'], F['axis_z'], sd, g['y_back'] - m['shaft_len'], g['y_back']),
        # bolt heads (M3, 5.5 x 3) on the bracket foot
        bolt_heads=[tbox(bx - 2.75, g['bolt_y'] - 2.75, g['foot_z1'], bx + 2.75,
                         g['bolt_y'] + 2.75, g['foot_z1'] + 3.0) for bx in BRACKET['bolt_xs']],
        # sensor head docked on the probe disc (31 x 35 x 23 mm, either way round)
        head=tbox(F['probe_x'] - 17.5, F['width'], F['axis_z'] - 17.5,
                  F['probe_x'] + 17.5, F['width'] + 23.0, F['axis_z'] + 17.5),
    )


def run_checks(bodies):
    d = dummies()
    fr, dr, ir = bodies['frame'], bodies['drive'], bodies['idle']
    br, belt = bodies['bracket'], bodies['belt']
    pairs = [
        ('frame x drive roller', fr, dr), ('frame x idle roller', fr, ir),
        ('frame x bracket', fr, br), ('bracket x drive roller', br, dr),
        ('frame x belt', fr, belt), ('belt x drive roller', belt, dr),
        ('belt x idle roller', belt, ir),
        ('gearbox x bracket', d['gearbox'], br), ('gearbox x frame', d['gearbox'], fr),
        ('gearbox x drive roller', d['gearbox'], dr), ('motor can x bracket', d['motor_can'], br),
        ('motor can x frame', d['motor_can'], fr),
        ('back shaft stub x bracket', d['back_stub'], br),
        ('shaft x frame', d['shaft'], fr),
        ('docked sensor head x frame', d['head'], fr),
        ('docked sensor head x bracket', d['head'], br),
    ]
    for i, h in enumerate(d['bolt_heads']):
        pairs += [('bolt head %d x bracket' % i, h, br),
                  ('bolt head %d x gearbox' % i, h, d['gearbox']),
                  ('bolt head %d x back stub' % i, h, d['back_stub'])]
    bad = 0
    for name, a, b2 in pairs:
        vol = overlap(a, b2)
        bad += vol > 0.001
        print('check %-34s %8.3f mm3' % (name, vol))
    print('%d pairs, %d clashes' % (len(pairs), bad))
    print('shaft x drive roller (should be > 0: round test shaft vs the D-flats) %.3f mm3'
          % overlap(d['shaft'], dr))


# ---------------------------------------------------------------- entry point
def run(_context: str):
    app = adsk.core.Application.get()
    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = design.rootComponent

    bodies = dict(
        frame=build_frame(new_comp(root, 'Frame')),
        drive=build_roller(new_comp(root, 'Drive roller'), True),
        idle=build_roller(new_comp(root, 'Idle roller'), False),
        belt=build_belt(new_comp(root, 'Belt (reference)')),
        belt_print=build_belt_print(new_comp(root, 'Belt (TPU print)')),
        bracket=build_bracket(new_comp(root, 'Motor bracket')),
    )
    app.activeViewport.fit()

    bad = [t.name for t in design.timeline if t.healthState != 0]
    print('timeline items with problems:', bad or 'none')
    for bd in bodies.values():
        bb = bd.boundingBox
        size = [round((getattr(bb.maxPoint, a) - getattr(bb.minPoint, a)) * 10, 1)
                for a in 'xyz']
        print('%s: %s mm, %.1f cm3' % (bd.name, size, bd.volume))
    print('belt loop %.1f mm; TPU tube %.1f mm across' % (
        belt_len(), belt_len() * (1 - BELT['stretch']) / math.pi))
    run_checks(bodies)
