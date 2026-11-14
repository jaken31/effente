"""Run inside Blender by scan_convert.convert, never by itself:

    blender --background --factory-startup --python blender_usdz_to_glb.py -- scan.usdz model.glb

Opens the USDZ file and writes it out as a GLB (binary glTF, Y up, textures inside the file).
A line starting with "effente: " on stderr is the reason shown to the person when it fails.

Not yet run on the laptop this was written on (it had no Blender), and not on a real Object Capture file
(unverified). The plan's own check used Blender 5.2.2 and a USDZ made by Blender.
"""

import sys

import bpy


def fail(reason, code=3):
    print(f"effente: {reason}", file=sys.stderr, flush=True)
    sys.exit(code)


def last_line(error):
    lines = str(error).strip().splitlines()
    return lines[-1] if lines else "no reason given"


args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(args) != 2:
    fail("the converter was started without a scan file and a place to write the model", 2)
source, target = args

bpy.ops.wm.read_factory_settings(use_empty=True)  # an empty scene: no default cube, camera or light in the model
try:
    bpy.ops.wm.usd_import(filepath=source)
except Exception as e:  # Blender raises RuntimeError with its own text
    fail(f"Blender could not open the scan: {last_line(e)}")

if not any(o.type == "MESH" for o in bpy.data.objects):
    fail("the scan holds no 3D shape")

try:
    bpy.ops.export_scene.gltf(filepath=target, export_format="GLB")
except Exception as e:
    fail(f"Blender could not write the model: {last_line(e)}")
