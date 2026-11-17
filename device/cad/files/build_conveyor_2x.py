"""effente demo conveyor, 2x size: parametric Fusion build script (TT gear motor).

The same conveyor as build_conveyor.py at twice the size, still driven by the
yellow TT gear motor (run at up to 9 V through the TB6612). Bought parts keep
their real sizes: the TT motor, M3 bolts and nuts, zip ties, the steel probe
disc.

  Frame          400 x 144 x 78 mm, printed as two halves (about 200 x 190 mm,
                 one per printer) bolted together with 5 M3 x 20 bolts through
                 joint blocks at x = 200: 2 per side wall, 1 in the base.
                 U-slots the roller pins drop into, a pad for the motor
                 bracket, viewing windows, and a pocket for the steel probe
                 disc on wall B. No bearings: printed pins run in the slots
                 (a dab of grease helps).
  Drive roller   60 mm drum with pins at both ends, dropped into the slots like
                 the idle roller, and a 30-tooth gear printed onto its wall-A
                 end, outside the wall.
  Pinion         15 teeth, pushed onto the TT motor's output shaft (double-D
                 bore). 2:1 with the roller gear, so the belt gets the same pull
                 and speed as the 1x conveyor's direct drive.
  Motor bracket  A raised cradle beside the gear, so the TT shaft sits at the
                 roller's axis height, 33.75 mm along. Held by 2 zip ties and
                 bolted to the frame pad with 2 M3 bolts: the loose-mount fault.
  Imbalance      4 holes in the drive roller's wall-B end face for M3 screws.
  Belt           About 816 mm around and 80 mm wide: too big to print as one
                 TPU tube on a 250 x 210 bed. Duct tape folded sticky-side in,
                 joined into a loop, or a bought belt.
  Probe point    Pocket for a 20 mm steel disc on wall B's outside.

World axes: X along the belt, Y across it (wall A at y=0, wall B at y=144),
Z up from the table. All sizes in mm.

UNVERIFIED: the TT motor sizes are listing values (see build_conveyor.py),
and nobody has measured the TT motor's torque. The 2:1 gears give the belt
the same pull as the 1x conveyor, but the 2x belt and rollers add friction:
test the motor at 9 V before printing everything. Gear teeth are module 1.5
with 0.1 mm backlash per gear.

Run inside Fusion (Scripts and Add-Ins, or the Fusion MCP 'script' feature).
"""
# Fusion 360 build script: short geometry names (L, W, t, T) and many-argument
# helpers follow the CAD maths, so these style checks are off on purpose.
# pylint: disable=invalid-name,missing-function-docstring,missing-class-docstring,too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements,use-dict-literal,consider-using-f-string,disallowed-name,duplicate-code
import math

import adsk.core
import adsk.fusion

# ------------------------------------------------------------------ parameters
F = dict(
    length=400.0, width=144.0, wall=5.0, base_t=5.0, top=78.0,
    axis_z=48.0, drive_x=48.0, idle_x=360.0,
    split_x=200.0,                          # the two printed halves meet here
    # lightening windows in the base; an even count puts a bar on the split
    base_rim=12.0, base_bar=12.0, base_win_n=6,
    # viewing windows in both side walls: solid at the roller slots, the
    # joint (around x 190-210), the gear, and around the probe disc
    win_rail_bot=10.0, win_rail_top=12.0,
    win_a=((80.0, 176.0), (224.0, 330.0)),
    win_b=((80.0, 92.0), (156.0, 178.0), (224.0, 330.0)),
    pin_slot=8.4,                           # U-slots the 8 mm roller pins drop into
    probe_x=120.0, disc_d=20.1, disc_depth=2.0,
    pad_y=-42.0, pad_x=(62.0, 136.0),       # pad outside wall A for the motor bracket
    bolt_d=3.4, nut_af=5.6, nut_depth=2.5,
    # joint blocks at the split: inside both walls, and one on the base
    joint_len=20.0, joint_t=10.0, joint_zs=(22.0, 62.0),
    base_block=(24.0, 13.0), base_bolt_z=8.0,   # base block width (Y), height
    head_cbore=(6.0, 3.0),                  # M3 head counterbore: diameter, depth
)

ROLLER = dict(
    drum_d=60.0, flange_d=66.0, flange_t=3.0, hub_d=16.0, pin_d=8.0,
    belt_w=80.0, belt_t=1.0, drum_margin=4.0, end_clear=0.5,
    weight_holes=4, weight_r=20.0, weight_d=2.6, weight_depth=10.0,   # M3 self-tap
)

GEAR = dict(
    module=1.5, pressure=20.0, backlash=0.1,
    z_roller=30, z_pinion=15, face=6.0,
    gap=1.5,                                # gear face to wall A
    pinion_gap=1.5,                         # gearbox face to pinion
)

# yellow TT gear motor (1:48). UNVERIFIED except gb_len / total_len (yours).
TT = dict(
    gb_len=48.0, total_len=65.0, gb_t=18.8, gb_h=22.5,
    shaft_from_end=11.0, shaft_d=5.4, shaft_flats=3.7, shaft_len=8.5,
    clear=0.3,
)

BRACKET = dict(
    foot_t=3.0, back_t=3.0, stop_t=3.0, back_h=14.0,
    stub_hole=9.0, bolt_back=4.0, foot_back=8.0,
    bolt_dx=(4.0, 44.0),                    # fault bolts, from the gearbox end
    tie_w=4.0, tie_h=2.0, tie_xs=(30.0, 42.0),   # zip ties, clear of the pinion
)

# Slot caps: the roller pins sit in open-topped U-slots, and the gear mesh and
# belt can lift a roller out of them. Each cap straddles the top of the wall
# over a slot, a tongue fills the slot to just above the pin, and one M3 x 12
# bolt through the cap and the wall holds it. The bolt sits beside the slot,
# toward the frame's end, clear of the gear.
CAP = dict(
    slots=(('drive, wall A', 'drive_x', 'A'), ('drive, wall B', 'drive_x', 'B'),
           ('idle, wall A', 'idle_x', 'A'), ('idle, wall B', 'idle_x', 'B')),
    bolt_off=30.0, bolt_d=3.4, leg_w=12.0, leg_h=10.0, leg_t=1.5, top_t=3.0,
    clear=0.15, pin_gap=0.3,
)

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

    def poly(self, sk, pts):
        """Closed polyline through model points; returns its profile."""
        lines = sk.sketchCurves.sketchLines
        sp = [self._sp(sk, *q) for q in pts]
        first = prev = lines.addByTwoPoints(sp[0], sp[1])
        for q in sp[2:]:
            prev = lines.addByTwoPoints(prev.endSketchPoint, q)
        lines.addByTwoPoints(prev.endSketchPoint, first.startSketchPoint)
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

    def zbox(self, x0, y0, x1, y1, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        return self.extrude(self.rect(sk, (x0, y0, z0), (x1, y1, z0)), z1 - z0, op, body)

    def zcyl(self, cx, cy, d, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        return self.extrude(self.circle(sk, (cx, cy, z0), d), z1 - z0, op, body)

    def zhex(self, cx, cy, af, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        r = af / math.sqrt(3)
        pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)), z0)
               for a in range(0, 360, 60)]
        return self.extrude(self.poly(sk, pts), z1 - z0, op, body)

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

    def ypoly(self, pts_xz, y0, y1, op, body=None):
        ym = (y0 + y1) / 2
        sk = self.sketch('y', ym)
        return self.extrude(self.poly(sk, [(x, ym, z) for x, z in pts_xz]),
                            abs(y1 - y0), op, body, symmetric=True)

    # x prisms
    def xcyl(self, cy, cz, d, x0, x1, op, body=None):
        xm = (x0 + x1) / 2
        sk = self.sketch('x', xm)
        return self.extrude(self.circle(sk, (xm, cy, cz), d), abs(x1 - x0), op, body,
                            symmetric=True)

    def xhex(self, cy, cz, af, x0, x1, op, body=None):
        xm = (x0 + x1) / 2
        sk = self.sketch('x', xm)
        r = af / math.sqrt(3)
        pts = [(xm, cy + r * math.cos(math.radians(a)), cz + r * math.sin(math.radians(a)))
               for a in range(30, 390, 60)]
        return self.extrude(self.poly(sk, pts), abs(x1 - x0), op, body, symmetric=True)


def new_comp(root, name):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ.component


def first_body(feat, name):
    b = feat.bodies.item(0)
    b.name = name
    return b


# ---------------------------------------------------------------- derived
def gear_geo():
    g = GEAR
    m = g['module']
    rr, rp = m * g['z_roller'] / 2, m * g['z_pinion'] / 2
    y1 = -g['gap']                              # gear face nearest wall A
    y0 = y1 - g['face']
    return dict(rr=rr, rp=rp, centre=rr + rp, y0=y0, y1=y1,
                motor_x=F['drive_x'] + rr + rp, motor_z=F['axis_z'])


def gear_points(z, cx, cz, phase=0.0):
    """Involute spur gear outline (x, z points) round (cx, cz), a tooth at
    angle `phase` (radians, from +X). Teeth thinned by GEAR['backlash']."""
    g = GEAR
    m, a = g['module'], math.radians(g['pressure'])
    r = m * z / 2
    rb, ra, rf = r * math.cos(a), r + m, r - 1.25 * m
    psi = math.pi / (2 * z) - g['backlash'] / (2 * r)   # half tooth angle at the pitch circle

    def inv(t):
        return math.tan(t) - t

    def half(rad):
        rad = max(rad, rb)
        return psi + inv(a) - inv(math.acos(min(1.0, rb / rad)))
    radii = [max(rf, rb)] + [rb + (ra - rb) * k / 8 for k in range(1, 9)]
    pts = []
    for k in range(z):
        t0 = phase + 2 * math.pi * k / z
        flank = [(rad, t0 - half(rad)) for rad in radii]
        if rf < rb:
            flank = [(rf, t0 - half(rb))] + flank
        other = [(rad, 2 * t0 - th) for rad, th in reversed(flank)]
        for rad, th in flank + other:
            pts.append((cx + rad * math.cos(th), cz + rad * math.sin(th)))
    return pts


def roller_ys():
    w_in0, w_in1 = F['wall'], F['width'] - F['wall']
    mid = F['width'] / 2
    drum = ROLLER['belt_w'] + 2 * ROLLER['drum_margin']
    d0, d1 = mid - drum / 2, mid + drum / 2
    ft = ROLLER['flange_t']
    return dict(w_in0=w_in0, w_in1=w_in1, d0=d0, d1=d1, f0=d0 - ft, f1=d1 + ft,
                hub0=w_in0 + ROLLER['end_clear'], hub1=w_in1 - ROLLER['end_clear'])


def tt_geo():
    """TT motor outside wall A beside the roller gear: gearbox along X, output
    shaft along +Y through the pinion, motor can toward +X."""
    m, k, c, gg = TT, BRACKET, TT['clear'], gear_geo()
    mx, mz = gg['motor_x'], gg['motor_z']
    x_e = mx - m['shaft_from_end']
    y_face = gg['y0'] - GEAR['pinion_gap']
    y_back = y_face - m['gb_t']
    z0 = mz - m['gb_h'] / 2
    g = dict(mx=mx, mz=mz, x_e=x_e, x_gb1=x_e + m['gb_len'], x_can1=x_e + m['total_len'],
             y_face=y_face, y_back=y_back, z0=z0, z1=z0 + m['gb_h'])
    g['foot_z0'], g['foot_z1'] = F['base_t'], F['base_t'] + k['foot_t']
    g['bx0'] = x_e - c - k['stop_t']
    g['bx1'] = g['x_gb1']
    g['wy0'] = y_back - c - k['back_t']
    g['wy1'] = y_back - c
    g['fy0'] = g['wy0'] - k['foot_back']
    g['fy1'] = y_face - 0.5
    g['bolt_y'] = g['fy0'] + k['bolt_back']
    g['bolt_xs'] = [x_e + dx for dx in k['bolt_dx']]
    if (g['fy0'] < F['pad_y'] or g['bx0'] < F['pad_x'][0] or g['bx1'] > F['pad_x'][1]
            or g['x_can1'] > F['split_x'] - 5):
        raise ValueError('TT motor does not fit the bracket pad: change F or TT')
    return g


# ---------------------------------------------------------------- frame
def build_frame(comp):
    """The whole frame as one body, then split at split_x into two halves."""
    b = Builder(comp)
    L, W, t, T, bt = F['length'], F['width'], F['wall'], F['top'], F['base_t']
    az, sx = F['axis_z'], F['split_x']
    frame = first_body(b.zbox(0, 0, L, W, 0, bt, 'new'), 'Frame')
    b.zbox(0, 0, L, t, bt, T, 'join', frame)                   # wall A
    b.zbox(0, W - t, L, W, bt, T, 'join', frame)               # wall B
    b.zbox(F['pad_x'][0], F['pad_y'], F['pad_x'][1], 0.5, 0, bt, 'join', frame)

    wz0, wz1 = bt + F['win_rail_bot'], T - F['win_rail_top']
    for (y0, y1), wins in (((0, t), F['win_a']), ((W - t, W), F['win_b'])):
        for xa, xb in wins:
            b.ybox(xa, xb, wz0, wz1, y0 - 0.5, y1 + 0.5, 'cut', frame)
    rim, bar, n = F['base_rim'], F['base_bar'], F['base_win_n']
    ww = (L - 2 * rim - (n - 1) * bar) / n
    for i in range(n):
        wx = rim + i * (ww + bar)
        b.zbox(wx, t + rim / 2, wx + ww, W - t - rim / 2, -0.5, bt + 0.5, 'cut', frame)

    # joint blocks across the split, bolted through along X
    jl, jt = F['joint_len'] / 2, F['joint_t']
    bw, bh = F['base_block']
    b.zbox(sx - jl, t - 0.01, sx + jl, t + jt, 0, T, 'join', frame)
    b.zbox(sx - jl, W - t - jt, sx + jl, W - t + 0.01, 0, T, 'join', frame)
    b.zbox(sx - jl, W / 2 - bw / 2, sx + jl, W / 2 + bw / 2, 0, bh, 'join', frame)
    bolts = [(t + jt / 2, z) for z in F['joint_zs']] + \
            [(W - t - jt / 2, z) for z in F['joint_zs']] + [(W / 2, F['base_bolt_z'])]
    cd, cdep = F['head_cbore']
    for by, bz in bolts:
        b.xcyl(by, bz, F['bolt_d'], sx - jl - 0.5, sx + jl + 0.5, 'cut', frame)
        b.xhex(by, bz, F['nut_af'], sx - jl - 0.5, sx - jl + F['nut_depth'], 'cut', frame)
        b.xcyl(by, bz, cd, sx + jl - cdep, sx + jl + 0.5, 'cut', frame)

    # U-slots: both rollers, both walls
    ps = F['pin_slot']
    for x in (F['drive_x'], F['idle_x']):
        for y0, y1 in ((0, t), (W - t, W)):
            b.ybox(x - ps / 2, x + ps / 2, az, T + 1, y0 - 0.5, y1 + 0.5, 'cut', frame)
            b.ycyl(x, az, ps, y0 - 0.5, y1 + 0.5, 'cut', frame)

    # fault bolts: holes through the pad, nut traps from underneath
    g = tt_geo()
    for bx in g['bolt_xs']:
        b.zcyl(bx, g['bolt_y'], F['bolt_d'], -0.5, bt + 0.5, 'cut', frame)
        b.zhex(bx, g['bolt_y'], F['nut_af'], -0.5, F['nut_depth'], 'cut', frame)

    # probe point: steel disc pocket on wall B's outer face
    b.ycyl(F['probe_x'], az, F['disc_d'], W - F['disc_depth'], W + 0.5, 'cut', frame)

    cut_cap_bolt_holes(b, frame)

    # split into the two printed halves
    spin = b.f.splitBodyFeatures.createInput(frame, b.plane('x', sx), True)
    b.f.splitBodyFeatures.add(spin)
    halves = {}
    for bd in comp.bRepBodies:
        if bd.boundingBox.maxPoint.x <= (sx + 0.01) * MM:
            bd.name = 'Frame half A (motor end)'
            halves['a'] = bd
        else:
            bd.name = 'Frame half B (idle end)'
            halves['b'] = bd
    return halves


# ---------------------------------------------------------------- rollers
def build_roller(comp, drive):
    b = Builder(comp)
    ry = roller_ys()
    x, az, R = (F['drive_x'] if drive else F['idle_x']), F['axis_z'], ROLLER
    name = 'Drive roller (with gear)' if drive else 'Idle roller'
    body = first_body(b.ycyl(x, az, R['drum_d'], ry['d0'], ry['d1'], 'new'), name)
    b.ycyl(x, az, R['flange_d'], ry['f0'], ry['d0'], 'join', body)
    b.ycyl(x, az, R['flange_d'], ry['d1'], ry['f1'], 'join', body)
    b.ycyl(x, az, R['hub_d'], ry['f1'], ry['hub1'], 'join', body)
    b.ycyl(x, az, R['pin_d'], ry['hub1'], F['width'] - 0.5, 'join', body)
    b.ycyl(x, az, R['hub_d'], ry['hub0'], ry['f0'], 'join', body)
    if drive:
        gg = gear_geo()
        b.ycyl(x, az, R['pin_d'], gg['y1'] - 0.01, ry['hub0'], 'join', body)
        b.ypoly(gear_points(GEAR['z_roller'], x, az), gg['y0'], gg['y1'], 'join', body)
        for i in range(R['weight_holes']):
            a = 2 * math.pi * i / R['weight_holes']
            b.ycyl(x + R['weight_r'] * math.cos(a), az + R['weight_r'] * math.sin(a),
                   R['weight_d'], ry['f1'] - R['weight_depth'], ry['f1'] + 0.5, 'cut', body)
    else:
        b.ycyl(x, az, R['pin_d'], 0.5, ry['hub0'], 'join', body)
    return body


def build_pinion(comp):
    """15-tooth pinion with a double-D bore for the TT output shaft. With an
    odd tooth count, the gap at 180 degrees faces the roller gear's tooth at
    0 degrees, so the two mesh as built."""
    b, gg, m = Builder(comp), gear_geo(), TT
    mx, mz = gg['motor_x'], gg['motor_z']
    body = first_body(b.ypoly(gear_points(GEAR['z_pinion'], mx, mz), gg['y0'], gg['y1'], 'new'),
                      'Pinion')
    bd = m['shaft_d'] + 0.2
    b.ycyl(mx, mz, bd, gg['y0'] - 0.5, gg['y1'] + 0.5, 'cut', body)
    fl = (m['shaft_flats'] + 0.15) / 2            # fill back the two flats
    for z0, z1 in ((mz + fl, mz + bd / 2 + 0.3), (mz - bd / 2 - 0.3, mz - fl)):
        b.ybox(mx - bd / 2 - 0.3, mx + bd / 2 + 0.3, z0, z1, gg['y0'], gg['y1'], 'join', body)
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


# ---------------------------------------------------------------- motor bracket
def build_bracket(comp):
    """Raised cradle for the TT gearbox: foot, saddle up to the gearbox, end
    stop, back wall with a hole for the back shaft stub, 2 zip-tie tunnels
    just under the gearbox. Bolted to the frame pad by the 2 fault bolts."""
    b, k, m, g = Builder(comp), BRACKET, TT, tt_geo()
    c = m['clear']
    body = first_body(b.zbox(g['bx0'], g['fy0'], g['bx1'], g['fy1'], g['foot_z0'], g['foot_z1'],
                             'new'), 'Motor bracket')
    b.zbox(g['x_e'] - c, g['wy1'] - 0.01, g['bx1'], g['fy1'], g['foot_z1'] - 0.01, g['z0'] - c,
           'join', body)                                               # saddle
    b.zbox(g['bx0'], g['wy0'], g['x_e'] - c, g['fy1'], g['foot_z1'] - 0.01, g['z0'] + 10.0,
           'join', body)                                               # end stop
    b.zbox(g['bx0'], g['wy0'], g['bx1'], g['wy1'], g['foot_z1'] - 0.01, g['z0'] + k['back_h'],
           'join', body)                                               # back wall
    b.ycyl(g['mx'], g['mz'], k['stub_hole'], g['wy0'] - 0.5, g['wy1'] + 0.5, 'cut', body)
    for bx in g['bolt_xs']:
        b.zcyl(bx, g['bolt_y'], F['bolt_d'], g['foot_z0'] - 0.5, g['foot_z1'] + 0.5, 'cut', body)
    tz1 = g['z0'] - c - 0.8
    for tx in k['tie_xs']:
        x = g['x_e'] + tx
        b.ybox(x - k['tie_w'] / 2, x + k['tie_w'] / 2, tz1 - k['tie_h'], tz1,
               g['wy0'] - 0.5, g['fy1'] + 0.5, 'cut', body)
    return body


# ---------------------------------------------------------------- slot caps
def cap_slots():
    """(name, slot x, wall 'A' or 'B', bolt x) for each capped slot."""
    k = CAP
    out = []
    for name, x, wall in k['slots']:
        sgn = -1 if F[x] < F['length'] / 2 else 1        # bolt toward the frame's end
        out.append((name, F[x], wall, F[x] + sgn * k['bolt_off']))
    return out


def cap_wall_y(wall):
    t, W = F['wall'], F['width']
    return (0.0, t) if wall == 'A' else (W - t, W)


def cut_cap_bolt_holes(b, frame):
    k = CAP
    for _name, _sx, wall, bx in cap_slots():
        wy0, wy1 = cap_wall_y(wall)
        b.ycyl(bx, F['top'] - k['leg_h'] / 2, k['bolt_d'], wy0 - 0.5, wy1 + 0.5, 'cut', frame)


def build_slot_caps(comp):
    b, k = Builder(comp), CAP
    T, ps, c, lt, lw, lh = F['top'], F['pin_slot'], k['clear'], k['leg_t'], k['leg_w'], k['leg_h']
    caps = []
    for name, sx, wall, bx in cap_slots():
        wy0, wy1 = cap_wall_y(wall)
        x0, x1 = min(sx - ps / 2 - 2.0, bx - lw / 2), max(sx + ps / 2 + 2.0, bx + lw / 2)
        ya, yb = wy0 - c - lt, wy1 + c + lt
        body = first_body(b.zbox(x0, ya, x1, yb, T, T + k['top_t'], 'new'),
                          'Slot cap (%s)' % name)
        for ly0, ly1 in ((ya, wy0 - c), (wy1 + c, yb)):          # legs either side of the wall
            b.zbox(bx - lw / 2, ly0, bx + lw / 2, ly1, T - lh, T + 0.01, 'join', body)
        tz0 = F['axis_z'] + ROLLER['pin_d'] / 2 + k['pin_gap']   # tongue stops just above the pin
        b.zbox(sx - ps / 2 + c, wy0, sx + ps / 2 - c, wy1, tz0, T + 0.01, 'join', body)
        b.ycyl(bx, T - lh / 2, k['bolt_d'], ya - 0.5, yb + 0.5, 'cut', body)
        caps.append(body)
    return caps


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
        shaft=tcyl_y(g['mx'], g['mz'], sd, g['y_face'], g['y_face'] + m['shaft_len']),
        back_stub=tcyl_y(g['mx'], g['mz'], sd, g['y_back'] - m['shaft_len'], g['y_back']),
        bolt_heads=[tbox(bx - 2.75, g['bolt_y'] - 2.75, g['foot_z1'], bx + 2.75,
                         g['bolt_y'] + 2.75, g['foot_z1'] + 3.0) for bx in g['bolt_xs']],
        # zip ties up the gearbox's front face (1 mm thick)
        ties=[tbox(g['x_e'] + tx - 2.0, g['y_face'], g['z0'], g['x_e'] + tx + 2.0,
                   g['y_face'] + 1.0, g['z1'] + 1.0) for tx in BRACKET['tie_xs']],
        # sensor head docked on the probe disc (29 x 33.5 x 23 mm, either way round)
        head=tbox(F['probe_x'] - 16.75, F['width'], F['axis_z'] - 16.75,
                  F['probe_x'] + 16.75, F['width'] + 23.0, F['axis_z'] + 16.75),
    )


def run_checks(bodies):
    d = dummies()
    fa, fb = bodies['frame']['a'], bodies['frame']['b']
    dr, ir, br, pn, belt = (bodies[k] for k in ('drive', 'idle', 'bracket', 'pinion', 'belt'))
    pairs = []
    for fn, fr in (('frame A', fa), ('frame B', fb)):
        pairs += [(fn + ' x drive roller', fr, dr), (fn + ' x idle roller', fr, ir),
                  (fn + ' x bracket', fr, br), (fn + ' x pinion', fr, pn),
                  (fn + ' x belt', fr, belt), ('gearbox x ' + fn, d['gearbox'], fr),
                  ('motor can x ' + fn, d['motor_can'], fr), ('shaft x ' + fn, d['shaft'], fr),
                  ('docked sensor head x ' + fn, d['head'], fr)]
    pairs += [('frame A x frame B', fa, fb),
              ('pinion x drive roller gear', pn, dr), ('pinion x bracket', pn, br),
              ('pinion x gearbox', pn, d['gearbox']),
              ('bracket x drive roller', br, dr), ('belt x drive roller', belt, dr),
              ('belt x idle roller', belt, ir),
              ('gearbox x bracket', d['gearbox'], br), ('gearbox x drive roller', d['gearbox'], dr),
              ('motor can x bracket', d['motor_can'], br),
              ('back shaft stub x bracket', d['back_stub'], br)]
    for i, h in enumerate(d['bolt_heads']):
        pairs += [('bolt head %d x bracket' % i, h, br),
                  ('bolt head %d x gearbox' % i, h, d['gearbox'])]
    for i, tie in enumerate(d['ties']):
        pairs += [('zip tie %d x pinion' % i, tie, pn), ('zip tie %d x roller gear' % i, tie, dr)]
    for cp in bodies['caps']:
        for nm, other in (('frame A', fa), ('frame B', fb), ('drive roller', dr), ('idle roller', ir),
                          ('belt', belt), ('bracket', br), ('pinion', pn), ('gearbox', d['gearbox']),
                          ('docked head', d['head'])):
            pairs.append(('%s x %s' % (cp.name, nm), cp, other))
    bad = 0
    for name, a, b2 in pairs:
        vol = overlap(a, b2)
        if vol > 0.001:
            bad += 1
            print('CLASH %s: %.3f mm3' % (name, vol))
    print('%d pairs, %d clashes' % (len(pairs), bad))
    print('shaft x pinion (should be > 0: round test shaft vs the D-flats) %.3f mm3'
          % overlap(d['shaft'], pn))


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
        pinion=build_pinion(new_comp(root, 'Pinion')),
        belt=build_belt(new_comp(root, 'Belt (reference)')),
        bracket=build_bracket(new_comp(root, 'Motor bracket')),
    )
    bodies['caps'] = build_slot_caps(new_comp(root, 'Slot caps'))
    app.activeViewport.fit()

    bad = [t.name for t in design.timeline if t.healthState != 0]
    print('timeline items with problems:', bad or 'none')
    flat = [bodies['frame']['a'], bodies['frame']['b']] + \
        [bodies[k] for k in ('drive', 'idle', 'pinion', 'belt', 'bracket')] + bodies['caps']
    for bd in flat:
        bb = bd.boundingBox
        size = [round((getattr(bb.maxPoint, a) - getattr(bb.minPoint, a)) * 10, 1)
                for a in 'xyz']
        print('%s: %s mm, %.1f cm3' % (bd.name, size, bd.volume))
    gg = gear_geo()
    print('belt loop %.1f mm; gear centres %.2f mm apart; motor shaft at x %.2f'
          % (belt_len(), gg['centre'], gg['motor_x']))
    run_checks(bodies)
