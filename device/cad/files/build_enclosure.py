"""effente enclosure: parametric Fusion build script.

Builds two components in a new Fusion design, following docs/mockup.svg and
docs/research/physical-design.md:

  Sensor Head    30 x 35 x 21.5 mm block. Magnet pocket flush in the base. The
                 MPU-9250/6500 breakout stands upright in a printed IMU cradle,
                 with its 10-pin header at the top and the jumper plugs lying
                 flat toward the cable slot in the +X end wall.
  Handheld Body  84 x 145 x 48 mm shell, split into a back shell and a
                 front lid. Power bank lies across the bottom, the UNO Q above
                 it (rotated: USB-C edge toward the bank), thermal camera and
                 mic in the top wall, 2.42" OLED in a Screen tray under the lid,
                 SCAN button in a snap sleeve, head dock, 4 M3 corner screws.

Every part keeps its soldered headers, and every header has room for female
or male jumper (Dupont) plugs plus a wire bend:
  * UNO Q: straight male jumpers in both long female headers (26.5 mm above
    the board, under the screen tray). JSPI / JCTL male pins stay clear.
  * OLED: 5-pin header on its +X short edge, plugs point back, beside the UNO Q.
  * MPU board: plugs lie flat inside the head and leave through its cable slot.
  * Thermal camera / mic: plugs point into the case, above the UNO Q.

Slot-in design: each part drops into its own mount and is held by a snap or
by the lid closing, with no glue. The OLED and the MPU board sit in small
adapters (Screen tray, IMU cradle); if a part measures differently, change its
numbers and reprint only that adapter.

Run it from Fusion (Utilities > Scripts and Add-Ins) or through the Fusion MCP
server. All dimensions are in mm. Values marked UNVERIFIED come from listings,
photos or typical parts, not from calipers on your parts: measure them before
printing.
"""
# Fusion 360 build script: short geometry names (L, W, T, H, B) and many-argument
# helpers follow the CAD maths, so these style checks are off on purpose.
# pylint: disable=invalid-name,missing-function-docstring,missing-class-docstring,too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements,use-dict-literal,consider-using-f-string,disallowed-name,duplicate-code
import adsk.core
import adsk.fusion

# Dupont jumper allowances (UNVERIFIED typical values for 2.54 mm jumpers)
DUPONT = dict(
    header_plastic=2.5,   # black spacer on a soldered pin header
    housing=14.0,         # length of a jumper's plastic housing
    bend=4.0,             # room for the wire to bend after the housing
)


def plug_depth():
    """From a board face to the end of the wire bend, through a pin header."""
    return DUPONT['header_plastic'] + DUPONT['housing'] + DUPONT['bend']


# ---------------------------------------------------------------- parameters
HEAD = dict(
    length=31.0, width=35.0, height=21.5,  # outer size (cavity 26 x 30)
    wall=2.5,
    floor=4.5,                             # solid floor (magnet pocket below it)
    corner_r=2.0,
    magnet_d=12.2, magnet_depth=3.0,       # pocket for a 12 x 3 mm disc magnet
    # MPU-9250/6500 breakout from your photo: 10-pin header along a long edge,
    # header plastic and long pins on the back. Size UNVERIFIED (listing
    # 26.16 x 15.57; your photo matches that shape): measure it.
    board=(26.2, 15.6), board_t=1.6,       # long edge (along Y), short edge (up)
    # the board stands upright in the cradle, chip side toward -X, header at
    # the TOP edge so the plugs point toward the cable slot in the +X wall
    chip_gap=3.0,                          # chip-side parts and header solder tails
    grip=1.0,                              # cradle lips cover the board edges
    base_h=1.0,                            # cradle floor bar under the board
    back_t=1.2,                            # cradle lips behind the board
    header_band=3.5,                       # top band left open for the header
    board_clear=0.15, frame_clear=0.15,
    cable_slot=(10.0, 6.0),                # width, depth from the top (+X wall)
    lid_t=1.5, lip_h=1.5, lip_w=1.2, lid_clear=0.15,
    relief_len=7.0, relief_w=14.0, relief_z0=1.0,
    tie_slot=(3.0, 2.0), tie_cz=11.5, tie_x=4.0,
)

BODY = dict(
    width=84.0, height=145.0, depth=48.0,
    # corner_r 8 (was 12): the bank's corners reach into the bottom corners
    corner_r=8.0, edge_r=3.0, wall=2.5,
    split_z=38.0,                          # back shell below, front lid above
    screen=(62.0, 34.0), screen_cy=99.0,   # lid window over the OLED's active area
    # SCAN button: 12 x 12 mm tactile switch (from you) with a round cap.
    # UNVERIFIED: cap 12 mm across; sw_total = bottom of the switch body to the
    # top of the cap. Wires are soldered to its 4 legs.
    button_cy=64.0, sw_body=12.0, sw_body_h=3.5, sw_total=12.5, cap_d=12.0,
    cap_clear=0.4, cap_proud=1.5,
    sleeve_clear=0.15, sleeve_t=1.5, sw_finger_w=6.0, sw_finger_t=1.2, sw_lip=0.6,
    dock_cy=30.0, dock_depth=6.0,          # head dock pocket (head + 1 mm)
    disc_d=20.1, disc_depth=2.0, disc_skin=1.0,
    # M3 screw bosses: top corners, and beside the USB gap above the bank
    bosses=((6.5, 139.0), (77.5, 139.0), (6.5, 58.0), (77.5, 58.0)), boss_d=7.0,
    screw_pilot=2.5, screw_clear=3.4, cbore_d=6.0, cbore_depth=3.0,
    # UNO Q, rotated: short edges along X, USB-C edge toward the bank (-Y).
    # Size from the datasheet, 53.34 x 68.58 mm (you measured about 55 x 70;
    # the hooks leave room for that). Holes rotated from the datasheet.
    uno=(53.34, 68.58), uno_origin=(15.33, 65.2),
    uno_holes=((50.80, 13.97), (2.54, 15.24), (45.72, 66.04), (17.78, 66.04)),
    uno_usb_x=15.48,                       # USB-C centre along the -Y edge
    # standoffs lift the board over the JMEDIA / JMISC sockets on its underside.
    # UNVERIFIED: socket height not measured; 6 mm assumes they are under 5 mm.
    standoff_d=6.0, standoff_h=6.0, pcb_t=1.6,
    uno_pin_d=2.8, uno_pin_over=1.0,
    uno_header_h=8.5,                      # female headers (your 1 cm overall)
    # hooks on the two short edges, at a spot clear of USB-C / JSPI / Qwiic
    # (from your photo: UNVERIFIED), gap sized for a 70 mm long board
    hook_x=32.8, hook_w=4.0, hook_t=1.5, hook_gap=1.0, hook_lip=1.8,
    # power bank from your photos (3 x 1.9 x 0.5 in), lying across the bottom:
    # length along X. Take the key ring off.
    bank=(76.2, 48.3, 12.7), bank_clear=0.4,
    bank_rail=(2.0, 5.0),                  # stop rail above it: thickness, height
    bank_posts=((12.0, 13.0), (72.0, 13.0), (12.0, 46.0), (72.0, 46.0)), bank_post_d=6.0,
    bank_slot=(16.0, 8.0),                 # charge-port slot in both side walls
    # space between the bank and the UNO Q for the USB-C power plug (the
    # bank's own strap plug, or a short right-angle USB-C extension)
    usb_gap=14.0,
    cable_hole_d=6.0, cable_hole_z=20.0, cable_hole_x=13.5,
    # MLX90640-D55 thermal camera (Waveshare 28 x 16 mm PCB) in the top wall.
    # Raised so the plugs on its back pass over the UNO Q's JSPI pins.
    cam_pcb=(28.0, 16.0, 1.6), cam_cx=33.0, cam_cz=28.5, cam_hole_d=10.5,
    cam_clear=0.3, cam_rib=1.5, cam_rib_depth=4.0, cam_lip=1.0,
    # INMP441 round mic board (~14 mm, two rows of 3 pins), right of the camera.
    mic_d=15.0, mic_cx=57.0, mic_cz=25.0, mic_clear=0.3, mic_t=1.6, mic_gasket=1.0,
    mic_port_d=2.0, mic_row_dx=5.0,        # pin rows +-5 mm from centre (UNVERIFIED)
    mic_shelf=2.0,                         # shelf under the mic; the Qwiic cable passes below
    finger_w=12.0, finger_t=2.0,           # lid fingers over the camera / mic
    vent=(2.5, 16.0), vent_ys=(88.0, 96.0, 104.0, 112.0, 120.0), vent_cz=22.0,
)

# 2.42" SSD1309 I2C OLED (your photo: 4 corner holes, 5-pin header GND VCC SCL
# SDA RES on a short edge, pins out the back). Held in the Screen tray, which
# snaps into two hooks on the lid. The module slides in from the header end
# (+X), face down on the tray's front plate; lips hold the other three sides
# and two ribs on the lid stop it sliding out.
SCREEN = dict(
    tray=(76.0, 50.0), tray_h=8.0,         # fixed outside, centred on the window
    plate_t=1.2, lip=1.5, min_wall=1.0,
    # UNVERIFIED: 70 x 42 mm estimated from your photo's proportions; measure it.
    pcb=(70.0, 42.0), module_t=4.0,        # PCB; glass face to PCB back
    active=(55.01, 27.49),
    # active-area centre minus PCB centre, header end at +X. UNVERIFIED: from
    # your photo the glass sits about 2.35 mm off centre, away from the wide strip.
    active_off=(0.0, -2.35),
    header_inset=3.0,                      # header row centre from the +X edge
    header_len=12.7,                       # 5 pins
    clear=0.15,
    hook_w=8.0, hook_t=1.2, hook_gap=0.3, hook_lip=0.8,
    stop_w=4.0, stop_t=1.2,
)

# External power bank holder: a tray stuck to the outside of the right wall
# with command strips, for an Anker Prime 12,000 mAh (A1335). Size from Anker's
# spec page: 134.4 x 55 x 34.53 mm (not measured). The bank lies flat with its
# display (on the 134 x 55 face, near the USB-C end) facing up like the
# device's screen, and the tray's top is flush with the device's front face.
# The bank slides in from the top end (+Y) onto two corner stops at the bottom
# end, USB-C end down. A short, thin USB-C cable runs from the bank's port,
# under the tray floor, and through a window in the back plate into the
# existing side slot to the UNO Q.
BANK = dict(
    size=(134.4, 55.0, 34.53),             # length (Y), width (X), thickness (Z)
    clear=0.4, wall=2.0, floor_t=2.0,
    strip_gap=2.0,                         # command strip thickness (UNVERIFIED)
    y0=8.0,                                # USB-C end of the bank
    lip=3.0, lip_t=2.0,                    # lips over the long edges, flush with the front
    stop=6.0, stop_t=3.0,                  # corner stops at the USB-C end
    slot_win=((17.0, 37.0), (3.0, 15.0)),  # back plate window at the side slot: Y, Z
    vent_win=((86.0, 122.0), (13.0, 31.0)),  # back plate window over the vents: Y, Z
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

    # planes ---------------------------------------------------------------
    def plane(self, axis, offset):
        """Offset construction plane normal to world axis 'x', 'y' or 'z'."""
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

    # features -------------------------------------------------------------
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

    # z-axis prisms (most features); z1 may be below z0
    def zbox(self, x0, y0, x1, y1, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        return self.extrude(self.rect(sk, (x0, y0, z0), (x1, y1, z0)), z1 - z0, op, body)

    def zcyl(self, cx, cy, d, z0, z1, op, body=None):
        sk = self.sketch('z', z0)
        return self.extrude(self.circle(sk, (cx, cy, z0), d), z1 - z0, op, body)

    def fillet(self, edges, r):
        coll = adsk.core.ObjectCollection.create()
        for e in edges:
            coll.add(e)
        inp = self.f.filletFeatures.createInput()
        if hasattr(inp, 'edgeSetInputs'):
            inp.edgeSetInputs.addConstantRadiusEdgeSet(coll, v(r), True)
        else:
            inp.addConstantRadiusEdgeSet(coll, v(r), True)
        return self.f.filletFeatures.add(inp)


def vertical_edges(body, length_mm):
    out = []
    for e in body.edges:
        g = e.geometry
        if isinstance(g, adsk.core.Line3D):
            s, t = g.startPoint, g.endPoint
            if (abs(s.x - t.x) < 1e-6 and abs(s.y - t.y) < 1e-6
                    and abs(abs(s.z - t.z) - length_mm * MM) < 1e-6):
                out.append(e)
    return out


def z_face_edges(body, zs_mm):
    out = []
    for f in body.faces:
        g = f.geometry
        if isinstance(g, adsk.core.Plane) and abs(abs(g.normal.z) - 1) < 1e-6:
            if any(abs(g.origin.z - z * MM) < 1e-6 for z in zs_mm):
                out.extend(list(f.edges))
    return out




# ---------------------------------------------------------------- sensor head
def head_geo():
    H = HEAD
    L, W, T, w = H['length'], H['width'], H['height'], H['wall']
    iw, ih = L - 2 * w, W - 2 * w
    bl, bh = H['board']
    x0 = -iw / 2 + H['frame_clear']               # cradle front (-X)
    xb0 = x0 + H['chip_gap']                      # front of the board slot
    xf = xb0 + H['board_clear']                   # chip-side face of the board
    xbk = xf + H['board_t']                       # back face (header plastic side)
    xb1 = xb0 + H['board_t'] + 2 * H['board_clear']
    xc1 = xb1 + H['back_t']                       # cradle back
    zb0 = H['floor'] + H['base_h']                # board bottom edge
    zb1 = zb0 + bh                                # board top edge (header row)
    zc1 = T - H['lip_h'] - 0.2                    # cradle top, under the lid lip
    yc = ih / 2 - H['frame_clear']
    g = dict(L=L, W=W, T=T, w=w, iw=iw, ih=ih, bl=bl, bh=bh, x0=x0, xb0=xb0, xf=xf,
             xbk=xbk, xb1=xb1, xc1=xc1, zb0=zb0, zb1=zb1, zc1=zc1, yc=yc)
    # jumper housings on the header lie flat toward +X; the wires then bend
    # together into the cable slot
    g['plug_x1'] = xbk + DUPONT['header_plastic'] + DUPONT['housing']
    if g['plug_x1'] > iw / 2 - 2.0 or bl + 2 * H['board_clear'] > 2 * yc - 1.0 \
            or zb1 > T - 0.3 or xf - 2.5 < -iw / 2 + 0.2:
        raise ValueError('MPU board %s does not fit the head: make the head bigger'
                         % (H['board'],))
    return g


def build_head(comp):
    b, H, g = Builder(comp), HEAD, head_geo()
    L, W, T = g['L'], g['W'], g['T']
    b.zbox(-L / 2, -W / 2, L / 2, W / 2, 0, T, 'new')
    body = comp.bRepBodies.item(0)
    b.fillet(vertical_edges(body, T), H['corner_r'])
    body = comp.bRepBodies.item(0)
    b.zbox(-g['iw'] / 2, -g['ih'] / 2, g['iw'] / 2, g['ih'] / 2, T, H['floor'], 'cut', body)
    b.zcyl(0, 0, H['magnet_d'], 0, H['magnet_depth'], 'cut', body)
    sw, sd = H['cable_slot']
    b.zbox(g['iw'] / 2 - 0.5, -sw / 2, L / 2 + 0.5, sw / 2, T, T - sd, 'cut', body)
    comp.bRepBodies.item(0).name = 'Sensor head'
    add_head_extras(comp)


def add_head_extras(comp):
    """Strain relief, the head lid, and the IMU cradle."""
    b, H, g = Builder(comp), HEAD, head_geo()
    L, W, T = g['L'], g['W'], g['T']
    sw, sd = H['cable_slot']
    head = comp.bRepBodies.item(0)

    # strain relief: the cable rests on the block, a zip tie through the
    # tunnel clamps it
    b.zbox(L / 2 - 0.5, -H['relief_w'] / 2, L / 2 + H['relief_len'], H['relief_w'] / 2,
           H['relief_z0'], T - sd, 'join', head)
    tw, th = H['tie_slot']
    tx = L / 2 + H['tie_x']
    sk = b.sketch('y', 0)
    prof = b.rect(sk, (tx - tw / 2, 0, H['tie_cz'] - th / 2),
                  (tx + tw / 2, 0, H['tie_cz'] + th / 2))
    b.extrude(prof, H['relief_w'] + 2.0, 'cut', head, symmetric=True)

    # lid with a locating lip
    lid = b.zbox(-L / 2, -W / 2, L / 2, W / 2, T, T + H['lid_t'], 'new').bodies.item(0)
    lid.name = 'Head lid'
    b.fillet(vertical_edges(lid, H['lid_t']), H['corner_r'])
    lid = [bd for bd in comp.bRepBodies if bd.name == 'Head lid'][0]
    c, lw = H['lid_clear'], H['lip_w']
    ox, oy = g['iw'] / 2 - c, g['ih'] / 2 - c
    b.zbox(-ox, -oy, ox, oy, T, T - H['lip_h'], 'join', lid)
    b.zbox(-ox + lw, -oy + lw, ox - lw, oy - lw, T, T - H['lip_h'], 'cut', lid)
    b.zbox(ox - lw - 0.5, -sw / 2 - 0.3, ox + 0.5, sw / 2 + 0.3, T, T - H['lip_h'], 'cut', lid)
    # no lip along the -X wall: the header's solder tails come close to it
    b.zbox(-ox - 0.5, -oy + lw, -ox + lw + 0.5, oy - lw, T, T - H['lip_h'], 'cut', lid)
    # press rib: pushes the board's top edge down into the cradle
    yr = g['bl'] / 2 - H['grip']
    b.zbox(g['xf'], -yr, g['xbk'], yr, T + 0.5, g['zb1'] - 0.05, 'join', lid)

    # IMU cradle: the board slides down into grooves on both ends and a bar
    # at the bottom; open in front for the chip side, open at the top back
    # for the header
    yb = g['bl'] / 2 + H['board_clear']
    cr = b.zbox(g['x0'], -g['yc'], g['xc1'], g['yc'], H['floor'], g['zc1'], 'new').bodies.item(0)
    cr.name = 'IMU cradle'
    top = g['zc1'] + 1.0
    b.zbox(g['xb0'], -yb, g['xb1'], yb, g['zb0'], top, 'cut', cr)
    b.zbox(g['x0'] - 1.0, -yr, g['xb0'] + 0.1, yr, g['zb0'], top, 'cut', cr)
    b.zbox(g['xb1'] - 0.1, -yr, g['xc1'] + 1.0, yr, g['zb0'], top, 'cut', cr)
    b.zbox(g['xb1'] - 0.1, -g['yc'] - 1.0, g['xc1'] + 1.0, g['yc'] + 1.0,
           g['zb1'] - H['header_band'], top, 'cut', cr)


# ---------------------------------------------------------------- handheld body
def body_geo():
    B = BODY
    w = B['wall']
    ox, oy = B['uno_origin']
    btop = w + B['standoff_h'] + B['pcb_t']
    bl, bw, bt = B['bank']
    c = B['bank_clear']
    return dict(w=w, ox=ox, oy=oy, ul=B['uno'][1], uw=B['uno'][0], btop=btop,
                bx0=B['width'] / 2 - bl / 2, bx1=B['width'] / 2 + bl / 2,
                by0=w + c, by1=w + c + bw, bz1=w + bt,
                jump_top=btop + B['uno_header_h'] + DUPONT['housing'] + DUPONT['bend'],
                yin=B['height'] - w, zin=B['depth'] - w + 0.5)


def build_body(comp):
    b, B, bg = Builder(comp), BODY, body_geo()
    X, Y, Z, w = B['width'], B['height'], B['depth'], B['wall']

    b.zbox(0, 0, X, Y, 0, Z, 'new')
    b.fillet(vertical_edges(comp.bRepBodies.item(0), Z), B['corner_r'])
    b.fillet(z_face_edges(comp.bRepBodies.item(0), (0, Z)), B['edge_r'])
    coll = adsk.core.ObjectCollection.create()
    coll.add(comp.bRepBodies.item(0))
    sin = b.f.shellFeatures.createInput(coll, False)
    sin.insideThickness = v(w)
    b.f.shellFeatures.add(sin)
    body = comp.bRepBodies.item(0)

    # screw bosses: two in the top corners, two beside the gap above the bank
    # (the bank fills the bottom corners)
    for cx, cy in B['bosses']:
        b.zcyl(cx, cy, B['boss_d'], 1.0, Z - 1.0, 'join', body)
    for cx, cy in B['bosses']:
        b.zcyl(cx, cy, B['screw_pilot'], Z, w + 2.0, 'cut', body)
        b.zcyl(cx, cy, B['screw_clear'], Z, B['split_z'], 'cut', body)
        b.zcyl(cx, cy, B['cbore_d'], Z, Z - B['cbore_depth'], 'cut', body)

    # screen window and SCAN button hole
    sw, sh = B['screen']
    b.zbox(X / 2 - sw / 2, B['screen_cy'] - sh / 2, X / 2 + sw / 2, B['screen_cy'] + sh / 2,
           Z, Z - w - 0.5, 'cut', body)
    b.zcyl(X / 2, B['button_cy'], B['cap_d'] + 2 * B['cap_clear'], Z, Z - w - 0.5, 'cut', body)

    # head dock: cup behind the front wall, pocket, steel disc from inside
    dw, dh = HEAD['length'] + 1.0, HEAD['width'] + 1.0
    cy = B['dock_cy']
    b.zbox(X / 2 - dw / 2 - 3.5, cy - dh / 2 - 3.5, X / 2 + dw / 2 + 3.5, cy + dh / 2 + 3.5,
           Z - B['dock_depth'] - B['disc_skin'] - B['disc_depth'], Z - 1.0, 'join', body)
    b.zbox(X / 2 - dw / 2, cy - dh / 2, X / 2 + dw / 2, cy + dh / 2, Z, Z - B['dock_depth'],
           'cut', body)
    zf = Z - B['dock_depth']
    b.zcyl(X / 2, cy, B['disc_d'], zf - B['disc_skin'] - B['disc_depth'], zf - B['disc_skin'],
           'cut', body)
    H = HEAD
    nl, nw = H['relief_len'] + 0.5, H['relief_w'] + 0.6
    px = X / 2 + dw / 2
    b.zbox(px - 0.5, cy - nw / 2 - 3.5, px + nl + 3.5, cy + nw / 2 + 3.5,
           Z - B['dock_depth'] - 2.0, Z - 1.0, 'join', body)
    b.zbox(px - 0.5, cy - nw / 2, px + nl, cy + nw / 2, Z, Z - B['dock_depth'], 'cut', body)

    # UNO Q standoffs with locating pins
    ox, oy, btop = bg['ox'], bg['oy'], bg['btop']
    for hx, hy in B['uno_holes']:
        b.zcyl(ox + hx, oy + hy, B['standoff_d'], w - 0.5, w + B['standoff_h'], 'join', body)
        b.zcyl(ox + hx, oy + hy, B['uno_pin_d'], w + B['standoff_h'] - 0.5,
               btop + B['uno_pin_over'], 'join', body)
    # snap hooks on both short (Y) edges
    ft, lip, gap, hw = B['hook_t'], B['hook_lip'], B['hook_gap'], B['hook_w']
    hx = ox + B['hook_x']
    for ye, sgn in ((oy, -1), (oy + bg['ul'], 1)):
        f0, f1 = sorted((ye + sgn * gap, ye + sgn * (gap + ft)))
        b.zbox(hx - hw / 2, f0, hx + hw / 2, f1, w - 0.5, btop + 1.3, 'join', body)
        l0, l1 = sorted((ye - sgn * (lip - gap), ye + sgn * gap))
        b.zbox(hx - hw / 2, l0, hx + hw / 2, l1, btop + 0.1, btop + 1.3, 'join', body)

    # power bank: stop rail above it (the side and bottom walls do the rest)
    rt, rh = B['bank_rail']
    b.zbox(X / 2 - 20.0, bg['by1'] + 0.4, X / 2 + 20.0, bg['by1'] + 0.4 + rt, w - 0.5, w + rh,
           'join', body)
    # charge-port slots in both side walls, level with the bank's ends
    sl, sh2 = B['bank_slot']
    bcy, bcz = (bg['by0'] + bg['by1']) / 2, w + B['bank'][2] / 2
    for xw in (w / 2, X - w / 2):
        sk = b.sketch('x', xw)
        prof = b.rect(sk, (xw, bcy - sl / 2, bcz - sh2 / 2), (xw, bcy + sl / 2, bcz + sh2 / 2))
        b.extrude(prof, w + 2.0, 'cut', body, symmetric=True)

    # sensor-head cable hole in the top wall
    sk = b.sketch('y', Y - w / 2)
    b.extrude(b.circle(sk, (B['cable_hole_x'], Y - w / 2, B['cable_hole_z']), B['cable_hole_d']),
              w + 2.0, 'cut', body, symmetric=True)

    # thermal camera U-channel on the inside of the top wall
    cw, chh, ct = B['cam_pcb']
    cx, cz, c = B['cam_cx'], B['cam_cz'], B['cam_clear']
    rb, rd, clip = B['cam_rib'], B['cam_rib_depth'], B['cam_lip']
    yin = bg['yin']
    x0, x1 = cx - cw / 2 - c, cx + cw / 2 + c
    z0, z1 = cz - chh / 2 - c, cz + chh / 2 + c
    for xa, xb in ((x0 - rb, x0), (x1, x1 + rb)):
        b.zbox(xa, yin - rd, xb, yin + 0.5, w - 0.5, z1, 'join', body)
    b.zbox(x0 - rb, yin - rd, x1 + rb, yin + 0.5, w - 0.5, z0, 'join', body)
    yl = yin - ct - c
    for xa, xb in ((x0 - rb, x0 + clip), (x1 - clip, x1 + rb)):
        b.zbox(xa, yin - rd, xb, yl, w - 0.5, z1, 'join', body)
    sk = b.sketch('y', Y - w / 2)
    b.extrude(b.circle(sk, (cx, Y - w / 2, cz), B['cam_hole_d']), w + 2.0, 'cut', body,
              symmetric=True)

    # mic U-channel (same idea), sound hole through the wall
    mx, mz = B['mic_cx'], B['mic_cz']
    mh = B['mic_d'] / 2 + B['mic_clear']
    st = B['mic_t'] + B['mic_gasket'] + B['mic_clear']
    mx0, mx1, mz0 = mx - mh, mx + mh, mz - mh
    for xa, xb in ((mx0 - rb, mx0), (mx1, mx1 + rb)):
        b.zbox(xa, yin - rd, xb, yin + 0.5, w - 0.5, mz + mh, 'join', body)
    b.zbox(mx0 - rb, yin - rd, mx1 + rb, yin + 0.5, mz0 - B['mic_shelf'], mz0, 'join', body)
    for xa, xb in ((mx0 - rb, mx0 + clip), (mx1 - clip, mx1 + rb)):
        b.zbox(xa, yin - rd, xb, yin - st, w - 0.5, mz + mh, 'join', body)
    sk = b.sketch('y', Y - w / 2)
    b.extrude(b.circle(sk, (mx, Y - w / 2, mz), B['mic_port_d']), w + 2.0, 'cut', body,
              symmetric=True)

    # vent slots in both side walls beside the UNO Q
    vw, vh = B['vent']
    for xw in (w / 2, X - w / 2):
        for vy in B['vent_ys']:
            sk = b.sketch('x', xw)
            prof = b.rect(sk, (xw, vy - vw / 2, B['vent_cz'] - vh / 2),
                          (xw, vy + vw / 2, B['vent_cz'] + vh / 2))
            b.extrude(prof, w + 2.0, 'cut', body, symmetric=True)

    # split into back shell and front lid
    split_plane = b.plane('z', B['split_z'])
    spin = b.f.splitBodyFeatures.createInput(comp.bRepBodies.item(0), split_plane, True)
    b.f.splitBodyFeatures.add(spin)
    for bd in comp.bRepBodies:
        bb = bd.boundingBox
        bd.name = 'Front lid' if bb.minPoint.z > (B['split_z'] - 1) * MM else 'Back shell'

    # hold-downs on the lid: posts on the bank, fingers over camera and mic
    lid = [bd for bd in comp.bRepBodies if bd.name == 'Front lid'][0]
    zin = bg['zin']
    for px_, py_ in B['bank_posts']:
        b.zcyl(px_, py_, B['bank_post_d'], zin, bg['bz1'] + 0.3, 'join', lid)
    fw, ftk = B['finger_w'], B['finger_t']
    for fx, ztop_part in ((B['cam_cx'], B['cam_cz'] + B['cam_pcb'][1] / 2 + B['cam_clear']),
                          (B['mic_cx'], B['mic_cz'] + B['mic_d'] / 2 + B['mic_clear'])):
        b.zbox(fx - fw / 2, yin - ftk - 0.05, fx + fw / 2, yin - 0.05, ztop_part + 0.2,
               zin, 'join', lid)

    build_button_sleeve(b, lid)
    build_screen_mount(b, lid)


def button_geo():
    B = BODY
    zi = B['depth'] - B['wall']
    z_base = B['depth'] + B['cap_proud'] - B['sw_total']
    half = B['sw_body'] / 2 + B['sleeve_clear']
    return dict(zi=zi, z_base=z_base, half=half, cx=B['width'] / 2, cy=B['button_cy'])


def build_button_sleeve(b, lid):
    """Square sleeve under the lid for the 12 x 12 tactile switch: push the
    switch in from inside, cap first, until two fingers click behind it. The
    X-side walls stop 2 mm short of the base so the legs and wires pass."""
    B, g = BODY, button_geo()
    cx, cy, h, zi, zb = g['cx'], g['cy'], g['half'], g['zi'], g['z_base']
    t, ft, fw, lip = B['sleeve_t'], B['sw_finger_t'], B['sw_finger_w'], B['sw_lip']
    for xa, xb in ((cx - h - t, cx - h), (cx + h, cx + h + t)):
        b.zbox(xa, cy - h - ft, xb, cy + h + ft, zi + 0.5, zb + 2.0, 'join', lid)
    for sgn in (-1, 1):
        y_in = cy + sgn * h
        ya, yb = sorted((y_in, y_in + sgn * ft))
        b.zbox(cx - fw / 2, ya, cx + fw / 2, yb, zi + 0.5, zb - 1.0, 'join', lid)
        la, lb = sorted((y_in, y_in - sgn * lip))
        b.zbox(cx - fw / 2, la, cx + fw / 2, lb, zb - 0.05, zb - 1.0, 'join', lid)


def screen_geo():
    S, B = SCREEN, BODY
    zi = B['depth'] - B['wall']
    cx, cy = B['width'] / 2, B['screen_cy']
    tw, th = S['tray']
    pcx, pcy = cx - S['active_off'][0], cy - S['active_off'][1]
    pw, ph = S['pcb'][0] + 2 * S['clear'], S['pcb'][1] + 2 * S['clear']
    zp = zi - S['plate_t']                         # glass face of the module
    g = dict(zi=zi, cx=cx, cy=cy, x0=cx - tw / 2, x1=cx + tw / 2, y0=cy - th / 2,
             y1=cy + th / 2, zb=zi - S['tray_h'], pcx=pcx, pcy=pcy, pw=pw, ph=ph, zp=zp,
             zm=zp - S['module_t'] - 0.2,          # back of the pocket
             px0=pcx - pw / 2, px1=pcx + pw / 2, py0=pcy - ph / 2, py1=pcy + ph / 2)
    g['zback'] = zp - S['module_t']                # PCB back (header side)
    g['hdr_x'] = pcx + S['pcb'][0] / 2 - S['header_inset']
    xs = g['px1'] + 0.1
    g['stops'] = [(xs, min(ya, yb), xs + S['stop_t'], max(ya, yb))
                  for ya, yb in ((pcy + s * 8.0, pcy + s * (8.0 + S['stop_w'])) for s in (-1, 1))]
    return g


def build_screen_mount(b, lid):
    """Two snap hooks (Y ends) hold the Screen tray; two short ribs past the
    module's header end stop it sliding out of the tray."""
    S, g = SCREEN, screen_geo()
    ht, gap, hl, hw = S['hook_t'], S['hook_gap'], S['hook_lip'], S['hook_w']
    for ye, sgn in ((g['y0'], -1), (g['y1'], 1)):
        fa, fb = sorted((ye + sgn * gap, ye + sgn * (gap + ht)))
        b.zbox(g['cx'] - hw / 2, fa, g['cx'] + hw / 2, fb, g['zi'] + 0.5, g['zb'] - 1.0,
               'join', lid)
        la, lb = sorted((ye + sgn * gap, ye - sgn * hl))
        b.zbox(g['cx'] - hw / 2, la, g['cx'] + hw / 2, lb, g['zb'] - 0.1, g['zb'] - 1.0,
               'join', lid)
    for xa, ya, xb, yb in g['stops']:
        b.zbox(xa, ya, xb, yb, g['zi'] + 0.5, g['zb'], 'join', lid)


def build_screen_tray(comp):
    """Separate printed tray for the OLED (reprint it alone if the module's
    size differs). Open at the +X (header) end and at the back."""
    b, S, g = Builder(comp), SCREEN, screen_geo()
    mw = S['min_wall']
    if (g['px0'] < g['x0'] + mw or g['px1'] + 0.1 + S['stop_t'] > g['x1']
            or g['py0'] < g['y0'] + mw or g['py1'] > g['y1'] - mw or g['zm'] < g['zb'] + 1.0):
        raise ValueError('OLED module %s x %s does not fit the screen tray'
                         % (S['pcb'], S['module_t']))
    tray = b.zbox(g['x0'], g['y0'], g['x1'], g['y1'], g['zb'], g['zi'], 'new').bodies.item(0)
    tray.name = 'Screen tray'
    lip = S['lip']
    # pocket, open through the +X end
    b.zbox(g['px0'], g['py0'], g['x1'] + 1.0, g['py1'], g['zp'], g['zm'], 'cut', tray)
    # open back (module parts, header, wires), lips left on 3 sides
    b.zbox(g['px0'] + lip, g['py0'] + lip, g['x1'] + 1.0, g['py1'] - lip, g['zm'] + 0.5,
           g['zb'] - 1.0, 'cut', tray)
    # window for the active area
    aw, ah = S['active'][0] + 1.0, S['active'][1] + 1.0
    b.zbox(g['cx'] - aw / 2, g['cy'] - ah / 2, g['cx'] + aw / 2, g['cy'] + ah / 2,
           g['zi'] + 0.5, g['zp'] - 0.5, 'cut', tray)
    # slots in the front plate for the lid's two stop ribs
    for xa, ya, xb, yb in g['stops']:
        b.zbox(xa - 0.2, ya - 0.2, xb + 0.2, yb + 0.2, g['zi'] + 0.5, g['zp'] - 0.5, 'cut', tray)
    return tray


def bank_geo():
    k, B = BANK, BODY
    L, W, T = k['size']
    c, w = k['clear'], k['wall']
    xp0 = B['width'] + k['strip_gap']              # back plate, on the strips
    xp1 = xp0 + w
    xb0, xb1 = xp1 + c, xp1 + c + W                # bank, display up
    zt = B['depth']                                # flush with the front face
    zb1 = zt - k['lip_t']                          # bank top, just under the lips
    zb0 = zb1 - T
    yb0 = k['y0']
    return dict(xp0=xp0, xp1=xp1, xb0=xb0, xb1=xb1, xo=xb1 + c, xo2=xb1 + c + w,
                yb0=yb0, yb1=yb0 + L, zb0=zb0, zb1=zb1, zt=zt,
                zf1=zb0 - c, zf0=zb0 - c - k['floor_t'])


def build_bank_holder(comp):
    """Tray for the external power bank, display up, stuck on with command
    strips. Open at the top end so the bank slides in."""
    b, k, g = Builder(comp), BANK, bank_geo()
    c = k['clear']
    ys0, ytop = g['yb0'] - k['stop_t'], g['yb1'] + c
    # back plate on the strips, full height of the tray
    holder = b.zbox(g['xp0'], ys0, g['xp1'], ytop, g['zf0'], g['zt'], 'new').bodies.item(0)
    holder.name = 'Bank holder'
    b.zbox(g['xp0'], ys0, g['xo2'], ytop, g['zf0'], g['zf1'], 'join', holder)     # floor
    b.zbox(g['xo'], ys0, g['xo2'], ytop, g['zf0'], g['zt'], 'join', holder)       # outer wall
    # lips over the bank's long edges, top flush with the device's front face
    for xa, xb in ((g['xp1'], g['xp1'] + k['lip']), (g['xo'] - k['lip'], g['xo'])):
        b.zbox(xa, ys0, xb, ytop, g['zb1'] + c, g['zt'], 'join', holder)
    # corner stops at the USB-C end; the middle stays open for the plug
    for xa, xb in ((g['xp1'], g['xp1'] + k['stop']), (g['xo'] - k['stop'], g['xo'])):
        b.zbox(xa, ys0, xb, g['yb0'] - c, g['zf1'], g['zt'], 'join', holder)
    # windows in the back plate: the side slot (cable) and the vents
    for (wy0, wy1), (wz0, wz1) in (k['slot_win'], k['vent_win']):
        b.zbox(g['xp0'] - 0.5, wy0, g['xp1'] + 0.5, wy1, wz0, wz1, 'cut', holder)
    return holder


# ---------------------------------------------------------------- clash checks
def tbox(x0, y0, z0, x1, y1, z1):
    tm = adsk.fusion.TemporaryBRepManager.get()
    c = p((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    obb = adsk.core.OrientedBoundingBox3D.create(
        c, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
        abs(x1 - x0) * MM, abs(y1 - y0) * MM, abs(z1 - z0) * MM)
    return tm.createBox(obb)


def tcyl_z(cx, cy, d, z0, z1):
    tm = adsk.fusion.TemporaryBRepManager.get()
    return tm.createCylinderOrCone(p(cx, cy, z0), d * MM / 2, p(cx, cy, z1), d * MM / 2)


def overlap(a, bdy):
    tm = adsk.fusion.TemporaryBRepManager.get()
    ta, tb = tm.copy(a), tm.copy(bdy)
    tm.booleanOperation(ta, tb, adsk.fusion.BooleanTypes.IntersectionBooleanType)
    return ta.volume * 1000  # mm3


def dummies():
    """Stand-ins for the bought parts, their headers and the jumper plugs.
    Header positions on the UNO Q are read off your photo: APPROXIMATE."""
    B, S = BODY, SCREEN
    bg, sg, hg, kg = body_geo(), screen_geo(), head_geo(), button_geo()
    ox, oy, btop, w = bg['ox'], bg['oy'], bg['btop'], bg['w']
    ul, uw = bg['ul'], bg['uw']
    hp, hs = DUPONT['header_plastic'], DUPONT['housing']
    cw, chh, ct = B['cam_pcb']
    yin = bg['yin']
    cam_back = yin - ct - B['cam_clear']
    mic_back = yin - (B['mic_t'] + B['mic_gasket'] + B['mic_clear'])
    mrow = B['mic_row_dx']
    return dict(
        uno=tbox(ox, oy, btop - B['pcb_t'], ox + uw, oy + ul, btop),
        # the UNO Q's long female headers with straight male jumpers plugged in
        uno_hdr_left=tbox(ox + 0.3, oy + 20.0, btop, ox + 4.5, oy + ul, bg['jump_top']),
        uno_hdr_right=tbox(ox + uw - 4.5, oy + 30.0, btop, ox + uw - 0.3, oy + ul, bg['jump_top']),
        jspi=tbox(ox + 20.0, oy + ul - 5.0, btop, ox + 28.0, oy + ul - 1.0, btop + 8.5),
        jctl=tbox(ox + 4.0, oy + 12.0, btop, ox + 11.0, oy + 25.0, btop + 8.5),
        # Qwiic socket and plug (cable leaves past the board's top edge)
        qwiic=tbox(ox + 35.2, oy + ul - 5.0, btop, ox + 41.4, oy + ul + 4.5, btop + 4.5),
        # right-angle USB-C power plug in the gap below the UNO Q
        usb_plug=tbox(ox + B['uno_usb_x'] - 6.0, oy - B['usb_gap'] + 1.0, btop - 2.0,
                      ox + B['uno_usb_x'] + 6.0, oy, btop + 6.0),
        bank=tbox(bg['bx0'], bg['by0'], w, bg['bx1'], bg['by1'], bg['bz1']),
        oled=tbox(sg['pcx'] - S['pcb'][0] / 2, sg['pcy'] - S['pcb'][1] / 2, sg['zback'],
                  sg['pcx'] + S['pcb'][0] / 2, sg['pcy'] + S['pcb'][1] / 2, sg['zp']),
        oled_plugs=tbox(sg['hdr_x'] - 1.3, sg['pcy'] - S['header_len'] / 2,
                        sg['zback'] - hp - hs, sg['hdr_x'] + 1.3,
                        sg['pcy'] + S['header_len'] / 2, sg['zback']),
        cam=tbox(B['cam_cx'] - cw / 2, cam_back, B['cam_cz'] - chh / 2,
                 B['cam_cx'] + cw / 2, cam_back + ct, B['cam_cz'] + chh / 2),
        cam_plugs=tbox(B['cam_cx'] - 12.5, cam_back - plug_depth(), B['cam_cz'] - 7.7,
                       B['cam_cx'] + 12.5, cam_back, B['cam_cz'] + 7.7),
        mic_plugs_l=tbox(B['mic_cx'] - mrow - 1.3, mic_back - plug_depth(), B['mic_cz'] - 3.8,
                         B['mic_cx'] - mrow + 1.3, mic_back, B['mic_cz'] + 3.8),
        mic_plugs_r=tbox(B['mic_cx'] + mrow - 1.3, mic_back - plug_depth(), B['mic_cz'] - 3.8,
                         B['mic_cx'] + mrow + 1.3, mic_back, B['mic_cz'] + 3.8),
        switch=tbox(kg['cx'] - 6, kg['cy'] - 6, kg['z_base'], kg['cx'] + 6, kg['cy'] + 6,
                    kg['z_base'] + B['sw_body_h']),
        # legs + soldered wires leave from the two X sides of the switch
        switch_legs_l=tbox(kg['cx'] - 6.8, kg['cy'] - 5.0, kg['z_base'] - 6.0,
                           kg['cx'] - 5.0, kg['cy'] + 5.0, kg['z_base'] + 0.5),
        switch_legs_r=tbox(kg['cx'] + 5.0, kg['cy'] - 5.0, kg['z_base'] - 6.0,
                           kg['cx'] + 6.8, kg['cy'] + 5.0, kg['z_base'] + 0.5),
        cap=tcyl_z(kg['cx'], kg['cy'], B['cap_d'], kg['z_base'] + B['sw_body_h'],
                   B['depth'] + B['cap_proud']),
        imu=tbox(hg['xf'], -hg['bl'] / 2, hg['zb0'], hg['xbk'], hg['bl'] / 2, hg['zb1']),
        imu_tails=tbox(hg['xf'] - 2.5, -11.8, hg['zb1'] - 2.54, hg['xf'], 11.8, hg['zb1']),
        imu_plugs=tbox(hg['xbk'], -12.7, hg['zb1'] - 2.54, hg['plug_x1'], 12.7, hg['zb1']),
    )


def run_checks(head_comp, body_comp):
    d = dummies()
    hb = {bd.name: bd for bd in head_comp.bRepBodies}
    bb = {bd.name: bd for bd in body_comp.bRepBodies}
    shell, lid, tray = bb['Back shell'], bb['Front lid'], bb['Screen tray']
    head, hlid, cradle = hb['Sensor head'], hb['Head lid'], hb['IMU cradle']
    pairs = [('back shell', shell, 'front lid', lid), ('screen tray', tray, 'front lid', lid),
             ('screen tray', tray, 'back shell', shell)]
    printed = (('back shell', shell), ('front lid', lid), ('screen tray', tray))
    for name in ('oled', 'oled_plugs', 'cam_plugs', 'mic_plugs_l', 'mic_plugs_r', 'switch',
                 'switch_legs_l', 'switch_legs_r', 'cap', 'uno_hdr_left', 'uno_hdr_right',
                 'jspi', 'jctl',
                 'qwiic', 'usb_plug', 'cam'):
        for pn, pb in printed:
            pairs.append((name, d[name], pn, pb))
    pairs += [('bank', d['bank'], 'front lid', lid), ('bank', d['bank'], 'screen tray', tray)]
    for a in ('oled_plugs', 'cam_plugs', 'mic_plugs_l', 'mic_plugs_r', 'switch_legs_l',
              'switch_legs_r'):
        for c in ('uno', 'uno_hdr_left', 'uno_hdr_right', 'jspi', 'qwiic'):
            pairs.append((a, d[a], c, d[c]))
    pairs.append(('usb_plug', d['usb_plug'], 'bank', d['bank']))
    for name in ('imu', 'imu_tails', 'imu_plugs'):
        for pn, pb in (('sensor head', head), ('IMU cradle', cradle), ('head lid', hlid)):
            if not (name == 'imu' and pn == 'head lid'):
                pairs.append((name, d[name], pn, pb))
    pairs += [('IMU cradle', cradle, 'sensor head', head), ('IMU cradle', cradle, 'head lid', hlid)]
    if 'Bank holder' in bb:
        hold, g = bb['Bank holder'], bank_geo()
        ext_bank = tbox(g['xb0'], g['yb0'], g['zb0'], g['xb1'], g['yb1'], g['zb1'])
        # cable: from the bank's port end, under the tray floor, into the slot
        (sy0, sy1), _ = BANK['slot_win']
        under = tbox(BODY['width'] + 0.3, 0.0, 2.0, g['xb1'], sy1 - 1.0, g['zf0'] - 0.3)
        into = tbox(BODY['width'] + 0.3, sy0 + 1.0, 5.0, g['xp1'] + 0.4, sy1 - 1.0, 12.5)
        pairs += [('bank holder', hold, 'back shell', shell),
                  ('bank holder', hold, 'front lid', lid),
                  ('external bank', ext_bank, 'bank holder', hold),
                  ('external bank', ext_bank, 'back shell', shell),
                  ('cable under tray', under, 'bank holder', hold),
                  ('cable into slot', into, 'bank holder', hold),
                  ('cable into slot', into, 'back shell', shell)]
    bad = 0
    for an, a, bn, b2 in pairs:
        vol = overlap(a, b2)
        if vol > 0.001:
            bad += 1
            print('CLASH %s x %s: %.3f mm3' % (an, bn, vol))
    print('checked %d pairs, %d clashes' % (len(pairs), bad))
    print('expected contacts (mm3): UNO Q x back shell, pins in its holes %.3f; '
          'MPU board x head lid, press rib %.3f; bank x back shell, wall fillet %.3f'
          % (overlap(d['uno'], shell), overlap(d['imu'], hlid), overlap(d['bank'], shell)))


# ---------------------------------------------------------------- entry point
def run(_context: str):
    app = adsk.core.Application.get()
    app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = design.rootComponent

    head_occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    head_occ.component.name = 'Sensor Head'
    build_head(head_occ.component)

    body_occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    body_occ.component.name = 'Handheld Body'
    build_body(body_occ.component)
    build_screen_tray(body_occ.component)
    build_bank_holder(body_occ.component)

    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(4.0, -7.0, 0)
    if hasattr(body_occ, 'transform2'):
        body_occ.transform2 = m
    else:
        body_occ.transform = m
    if design.snapshots.hasPendingSnapshot:
        design.snapshots.add()

    app.activeViewport.fit()
    for occ in (head_occ, body_occ):
        for bd in occ.component.bRepBodies:
            bb = bd.boundingBox
            size = [round((getattr(bb.maxPoint, a) - getattr(bb.minPoint, a)) * 10, 2)
                    for a in 'xyz']
            print('%s / %s: %s mm, volume %.2f cm3' % (
                occ.component.name, bd.name, size, bd.volume))
    bad = [t.name for t in design.timeline if t.healthState != 0]
    print('timeline items with problems:', bad or 'none')
    run_checks(head_occ.component, body_occ.component)
